"""Market analytics service — cached option-chain analytics for B6.

Provides a memory-only cache of derived option-chain analytics
(``OptionChainAnalyticsSnapshot``) keyed by provider-neutral chain
identity. The service owns:

  * active chain registry  — which chains need refreshing
  * latest snapshot cache  — one ``OptionChainAnalyticsSnapshot`` per chain
  * per-chain refresh lock — ensures one REST call per chain at a time
  * background scheduler  — periodic refresh of all active chains
                           (concurrent with configurable bound)

It calls ``MarketService.option_chain(...)`` internally; broker
implementations are never imported directly.

Analytics conditions evaluate against the latest snapshot. A snapshot
older than ``stale_after_seconds`` is treated as UNKNOWN — it cannot
cause a trigger.

Restart semantics:
  * Cache is empty on startup
  * Active chains are reconstructed from enabled analytics alerts
  * First fresh snapshot must arrive before evaluation resumes
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from market.analytics.option_chain import (
    compute_iv_skew,
    compute_max_pain,
    compute_pcr,
    compute_pcr_volume,
)
from market.models import OptionChainAnalyticsSnapshot, OptionChainSnapshot

logger = logging.getLogger(__name__)

# Default refresh cadence.
DEFAULT_REFRESH_INTERVAL_SECONDS = 60.0
MIN_REFRESH_INTERVAL_SECONDS = 30.0
# The freshness window of the analytics snapshot. It follows the SHARED
# canonical quote service's default window, so an analytics verdict and a
# quote payload never disagree about whether a value is fresh.
DEFAULT_STALE_AFTER_SECONDS = 120.0
DEFAULT_MAX_CONCURRENT_REFRESHES = 4

# Retry backoff constants.
INITIAL_RETRY_DELAY = 5.0
MAX_RETRY_DELAY = 60.0

# The chain-key prefix used by the condition-alert engine.
ANALYTICS_KEY_PREFIX = "analytics:"


def parse_chain_key(chain_key: str) -> tuple[str, str]:
    """Split a chain key into ``(canonical_id, expiry)``.

    The chain key is ``"analytics:<canonical_id>:<expiry>"``. A canonical
    id itself contains colons (e.g.
    ``NSE:OPTION:ADANIGREEN:2026-10-27:1300:CE``), so the two fields are
    separated on their LAST delimiter — splitting on the FIRST delimiter
    truncated the canonical id and corrupted the expiry.

    Returns ``("", "")`` for an unparseable key.
    """
    key = (chain_key or "").strip()
    if key.startswith(ANALYTICS_KEY_PREFIX):
        key = key[len(ANALYTICS_KEY_PREFIX):]
    if ":" not in key:
        return "", ""
    canonical_id, _, expiry = key.rpartition(":")
    if not canonical_id or not expiry:
        return "", ""
    return canonical_id, expiry


class MarketAnalyticsService:
    """Cached option-chain analytics service for B6 analytics alerts."""

    def __init__(
        self,
        market_service: Any,
        instrument_catalog: Any = None,
        chain_service: Any = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        stale_after: float = DEFAULT_STALE_AFTER_SECONDS,
        max_concurrent_refreshes: int = DEFAULT_MAX_CONCURRENT_REFRESHES,
    ) -> None:
        self._market_service = market_service
        self._instrument_catalog = instrument_catalog
        # The service that FETCHES a chain snapshot. It is NOT the quote
        # store (MarketService has no option_chain method); it is the
        # provider-backed market-data service.
        self._chain_service = chain_service or market_service
        self._refresh_interval = max(
            MIN_REFRESH_INTERVAL_SECONDS, refresh_interval)
        self._stale_after = stale_after
        self._max_concurrent_refreshes = max(1, max_concurrent_refreshes)

        # chain_key -> OptionChainAnalyticsSnapshot
        self._cache: dict[str, OptionChainAnalyticsSnapshot] = {}
        # chain_key -> set of alert_ids that depend on it
        self._dependents: dict[str, set[str]] = {}
        # Per-chain refresh lock (created on demand; cleaned when chain idle)
        self._locks: dict[str, asyncio.Lock] = {}
        # Track last refresh time per chain (for scheduler/diagnostics)
        self._last_refresh: dict[str, float] = {}
        # Failures per chain (for staleness/diagnostics)
        self._failure_count: dict[str, int] = {}
        # Registration generation per chain: incremented on activation and on
        # deactivation so in-flight refresh commits for stale generations are
        # rejected. Kept only while chain is active or has in-flight work.
        self._generation: dict[str, int] = {}
        # Number of in-flight refresh operations per chain (scheduler/trigger).
        self._inflight: dict[str, int] = {}
        # Scheduler-owned in-flight refresh tasks (for deterministic stop).
        self._scheduler_refresh_tasks: set[asyncio.Task[Any]] = set()

        self._scheduler_task: asyncio.Task | None = None
        self._running = False

    # -- Properties -----------------------------------------------------------

    @property
    def stale_after_seconds(self) -> float:
        return self._stale_after

    # -- Chain service --------------------------------------------------------

    def set_chain_service(self, chain_service: Any) -> None:
        """Wire the service that FETCHES chain snapshots.

        The quote store (``MarketService``) exposes no ``option_chain``
        method, so it can never be the chain source; the provider-backed
        market-data service is.
        """
        if chain_service is not None:
            self._chain_service = chain_service

    # -- Chain registry -------------------------------------------------------

    def _is_chain_active(self, chain_key: str) -> bool:
        return bool(self._dependents.get(chain_key))

    def _get_lock(self, chain_key: str) -> asyncio.Lock:
        lock = self._locks.get(chain_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chain_key] = lock
        return lock

    def _maybe_cleanup_chain_state(self, chain_key: str) -> None:
        """Drop per-chain bookkeeping when fully idle.

        Safe to call only when there are no dependents and no in-flight
        refresh operations for the chain. Keeps lock/map sizes bounded by
        active and genuinely in-flight work.
        """
        if self._is_chain_active(chain_key):
            return
        if self._inflight.get(chain_key, 0) > 0:
            return
        self._dependents.pop(chain_key, None)
        self._cache.pop(chain_key, None)
        self._last_refresh.pop(chain_key, None)
        self._failure_count.pop(chain_key, None)
        self._generation.pop(chain_key, None)
        self._inflight.pop(chain_key, None)
        self._locks.pop(chain_key, None)

    def register_chain(self, chain_key: str, alert_id: str) -> None:
        """Register a chain key as needed (increments ref count)."""
        dependents = self._dependents.get(chain_key)
        if dependents is None:
            dependents = set()
            self._dependents[chain_key] = dependents
            # New activation epoch (including re-register after full removal).
            self._generation[chain_key] = self._generation.get(chain_key, 0) + 1
        dependents.add(alert_id)
        logger.debug("registered chain %s for alert %s", chain_key, alert_id)

    def unregister_chain(self, chain_key: str, alert_id: str) -> None:
        """Unregister an alert's dependency; remove chain if no dependents."""
        dependents = self._dependents.get(chain_key)
        if dependents is None:
            return
        dependents.discard(alert_id)
        if not dependents:
            # Invalidate any in-flight refresh for the deactivated epoch and
            # drop cached state immediately. Lock/bookkeeping cleanup is
            # deferred until no in-flight refresh can still reference it.
            self._generation[chain_key] = self._generation.get(chain_key, 0) + 1
            self._dependents.pop(chain_key, None)
            self._cache.pop(chain_key, None)
            self._last_refresh.pop(chain_key, None)
            self._failure_count.pop(chain_key, None)
            self._maybe_cleanup_chain_state(chain_key)
            logger.debug("unregistered chain %s (no dependents)", chain_key)

    def get_active_chains(self) -> set[str]:
        """Return the set of currently active chain keys."""
        return set(self._dependents.keys())

    def has_chain(self, chain_key: str) -> bool:
        return chain_key in self._dependents

    # -- Snapshot access ------------------------------------------------------

    def get_snapshot(self, chain_key: str) -> OptionChainAnalyticsSnapshot | None:
        """Return the latest snapshot, or None if unavailable/stale."""
        snap = self._cache.get(chain_key)
        if snap is None:
            return None
        if snap.is_stale:
            return None
        return snap

    def get_snapshot_raw(self, chain_key: str) -> OptionChainAnalyticsSnapshot | None:
        """Return the latest snapshot regardless of staleness (for diagnostics)."""
        return self._cache.get(chain_key)

    # -- Startup / lifecycle --------------------------------------------------

    async def start(self, bg_manager: Any) -> None:
        """Start the background refresh scheduler."""
        if self._running:
            return
        self._running = True
        await bg_manager.start("analytics_scheduler", self._scheduler_loop())
        logger.info(
            "analytics service started (interval=%ds, stale=%ds)",
            int(self._refresh_interval), int(self._stale_after))

    async def stop(self, bg_manager: Any) -> None:
        """Stop the background refresh scheduler.

        Shutdown contract: after this returns, no scheduler-owned refresh work
        continues mutating analytics state. Active chain registrations are
        preserved for restart semantics.
        """
        self._running = False
        if bg_manager is not None:
            # Await scheduler termination so it cannot spawn new refresh work.
            await bg_manager.cancel_and_wait("analytics_scheduler")
        # Cancel and await any scheduler-owned in-flight refresh tasks.
        tasks = list(self._scheduler_refresh_tasks)
        self._scheduler_refresh_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Defensive: ensure no stale scheduler-owned bookkeeping remains.
        self._scheduler_refresh_tasks.clear()

    async def _scheduler_loop(self) -> None:
        """Periodically refresh all active chains."""
        try:
            while self._running:
                try:
                    await self._refresh_all_active()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("analytics scheduler tick failed: %s", exc)
                try:
                    await asyncio.wait_for(
                        asyncio.sleep(self._refresh_interval),
                        timeout=self._refresh_interval + 5.0)
                except asyncio.TimeoutError:
                    pass
                except asyncio.CancelledError:
                    raise
        except asyncio.CancelledError:
            # Propagate so cancel/cancel_and_wait observe cancellation.
            raise

    async def _refresh_all_active(self) -> None:
        """Refresh every active chain concurrently with bounded concurrency.

        Per-chain locking in _refresh_one preserves same-chain dedup.
        One slow/failing chain does not block unrelated chains.
        """
        keys = list(self._dependents.keys())
        if not keys:
            return
        sem = asyncio.Semaphore(self._max_concurrent_refreshes)

        async def _gated(key: str) -> None:
            async with sem:
                await self._refresh_one(key, scheduler_owned=True)

        tasks = [asyncio.create_task(_gated(k)) for k in keys]
        for task in tasks:
            self._scheduler_refresh_tasks.add(task)
            task.add_done_callback(self._scheduler_refresh_tasks.discard)
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            # Stop/cancellation: ensure spawned refresh tasks do not continue
            # mutating state after scheduler-owned work is cancelled.
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _refresh_one(self, chain_key: str, *, scheduler_owned: bool = False) -> None:
        """Refresh a single chain with per-chain locking and generation guards."""
        generation = self._generation.get(chain_key, 0)
        if not self._is_chain_active(chain_key):
            return
        lock = self._get_lock(chain_key)
        self._inflight[chain_key] = self._inflight.get(chain_key, 0) + 1
        try:
            async with lock:
                # Re-check after waiting for lock: chain may have been
                # unregistered/re-registered while we waited.
                if (not self._is_chain_active(chain_key)
                        or self._generation.get(chain_key) != generation):
                    return
                try:
                    analytics = await self._compute_refresh(chain_key)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Record failure only for the current active generation.
                    if (self._is_chain_active(chain_key)
                            and self._generation.get(chain_key) == generation):
                        self._failure_count[chain_key] = (
                            self._failure_count.get(chain_key, 0) + 1)
                    logger.warning(
                        "analytics refresh failed for %s: %s",
                        chain_key, type(exc).__name__)
                    return
                # Commit only if the registration generation is still current.
                if (analytics is not None
                        and self._is_chain_active(chain_key)
                        and self._generation.get(chain_key) == generation):
                    self._cache[chain_key] = analytics
                    self._last_refresh[chain_key] = time.monotonic()
                    self._failure_count[chain_key] = 0
                    logger.debug("refreshed chain %s (pcr_oi=%s, max_pain=%s)",
                                 chain_key, analytics.pcr_oi, analytics.max_pain)
        finally:
            remaining = self._inflight.get(chain_key, 0) - 1
            if remaining > 0:
                self._inflight[chain_key] = remaining
            else:
                self._inflight.pop(chain_key, None)
            self._maybe_cleanup_chain_state(chain_key)

    def _resolve_underlying_row(self, exchange: str,
                                underlying: str) -> dict[str, Any] | None:
        """The catalogue row for a canonical id's underlying.

        Prefer exact-symbol lookups when available; fall back to catalogue
        search for compatibility with existing callers/tests.
        """
        catalog = self._instrument_catalog
        if catalog is None:
            return None
        want = (underlying or "").strip().upper()
        if not want:
            return None
        for instrument_type in ("INDEX", "EQUITY"):
            try:
                if instrument_type == "INDEX":
                    row = catalog.find_index(want, exchange=exchange)
                else:
                    row = catalog.find_equity(want, exchange=exchange)
            except Exception:
                row = None
            if isinstance(row, dict) and row:
                return row
        # Compatibility fallback: general search (older behavior / test doubles).
        try:
            results = catalog.search(exchange=exchange, q=underlying, limit=5)
        except Exception:
            results = None
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                return first
        return None

    async def _compute_refresh(self, chain_key: str) -> OptionChainAnalyticsSnapshot | None:
        """Fetch chain from the chain service and compute analytics.

        Returns a snapshot without mutating service state; caller commits only
        for the current active generation.
        """
        canonical_id, expiry = parse_chain_key(chain_key)
        if not canonical_id or not expiry:
            logger.error("invalid chain_key format: %r", chain_key)
            return None
        # Extract exchange and underlying from canonical_id.
        # Format: "EXCHANGE:TYPE:SYMBOL" or "EXCHANGE:TYPE:SYMBOL:EXPIRY:STRIKE:OPT"
        id_parts = canonical_id.split(":")
        exchange = id_parts[0] if id_parts else ""
        underlying = id_parts[-1] if id_parts else ""

        # Look up the provider instrument key from the catalogue.
        target = None
        if self._instrument_catalog is not None:
            target = self._resolve_underlying_row(exchange, underlying)
        if target is None:
            logger.warning("no catalog match for chain %s", chain_key)
            return None

        instrument_key = target.get("instrument_token", "")
        tradingsymbol = target.get("tradingsymbol", "")

        try:
            snapshot = await self._chain_service.option_chain(
                instrument_key=instrument_key,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
                expiry=expiry,
            )
        except Exception as exc:
            logger.warning("option_chain fetch failed for %s: %s", chain_key, exc)
            raise

        if not isinstance(snapshot, OptionChainSnapshot):
            logger.warning("option_chain returned unexpected type for %s", chain_key)
            return None

        # Compute analytics from the snapshot.
        pcr_result = compute_pcr(snapshot)
        pcr_vol_result = compute_pcr_volume(snapshot)
        mp_result = compute_max_pain(snapshot)
        iv_result = compute_iv_skew(snapshot)

        # The provider-reported aggregate OI (whole-expiry when the
        # provider's aggregate scope is proven) is a DIFFERENT scope from
        # the returned legs — it is carried alongside, never mixed into
        # the window-scoped values.
        aggregate_scope = getattr(snapshot, "aggregate_scope",
                                  "aggregate-unverified")
        aggregate_call_oi = getattr(snapshot, "call_oi", None)
        aggregate_put_oi = getattr(snapshot, "put_oi", None)

        now = datetime.now(timezone.utc)
        return OptionChainAnalyticsSnapshot(
            chain_key=chain_key,
            canonical_underlying_id=canonical_id,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            expiry=expiry,
            spot_price=snapshot.spot_price,
            pcr_oi=pcr_result.get("pcr"),
            pcr_volume=pcr_vol_result.get("pcr_volume"),
            max_pain=mp_result.get("max_pain"),
            iv_skew=iv_result.get("skew"),
            scope=snapshot.scope,
            provider=getattr(snapshot, "provider", None),
            aggregate_scope=aggregate_scope,
            aggregate_call_oi=aggregate_call_oi,
            aggregate_put_oi=aggregate_put_oi,
            contracts_considered=len(getattr(snapshot, "strikes", ()) or ()),
            received_ts=now,
            calculated_at=now,
            stale_after_seconds=self._stale_after,
        )

    async def _do_refresh(self, chain_key: str) -> None:
        """Compatibility wrapper: compute and commit for current generation.

        Kept for internal/external callers that may invoke it directly; commits
        are guarded by the current active generation.
        """
        generation = self._generation.get(chain_key, 0)
        if not self._is_chain_active(chain_key):
            return
        analytics = await self._compute_refresh(chain_key)
        if (analytics is not None
                and self._is_chain_active(chain_key)
                and self._generation.get(chain_key) == generation):
            self._cache[chain_key] = analytics
            self._last_refresh[chain_key] = time.monotonic()
            self._failure_count[chain_key] = 0
            logger.debug("refreshed chain %s (pcr_oi=%s, max_pain=%s)",
                         chain_key, analytics.pcr_oi, analytics.max_pain)

    def reconstruct_from_alerts(self, load_enabled: Any) -> int:
        """Rebuild active chain registry from persisted enabled alerts.

        Called at startup after loading enabled condition alerts.
        Returns the number of chains registered.
        """
        try:
            alerts = load_enabled()
        except Exception as exc:
            logger.warning("analytics restart: load enabled failed: %s", exc)
            return 0

        count = 0
        for alert in alerts:
            try:
                condition = alert.get("condition")
                if not isinstance(condition, dict):
                    continue
                analytics_keys = self._extract_analytics_keys(condition)
                for key in analytics_keys:
                    self.register_chain(key, alert["alert_id"])
                    count += 1
            except Exception as exc:
                logger.warning("analytics restart: skipped alert %s: %s",
                               alert.get("alert_id"), exc)
        logger.info("analytics restart: registered %d chain(s)", count)
        return count

    def _extract_analytics_keys(self, condition: dict[str, Any]) -> list[str]:
        """Extract chain keys from a condition tree (recursive)."""
        keys: list[str] = []
        version = condition.get("condition_version")
        if version == 1:
            metric = condition.get("metric", "")
            if metric in ("pcr_oi", "pcr_volume", "max_pain", "iv_skew"):
                # Use _dependency_key if available (expiry stripped from
                # instrument during validation; preserved in dep key).
                dep = condition.get("_dependency_key")
                if dep and dep.startswith("analytics:"):
                    keys.append(dep)
                else:
                    instrument = condition.get("instrument", {})
                    canonical_id = instrument.get("canonical_id", "")
                    expiry = instrument.get("expiry", "")
                    if canonical_id and expiry:
                        keys.append(f"{canonical_id}:{expiry}")
        elif version == 2:
            for child in condition.get("conditions", []):
                keys.extend(self._extract_analytics_keys(child))
        return keys

    async def trigger_refresh(self, chain_key: str) -> None:
        """Force an immediate refresh of one chain (non-scheduler)."""
        if chain_key in self._dependents:
            await self._refresh_one(chain_key)

    def get_stats(self) -> dict[str, Any]:
        """Return diagnostic stats for observability."""
        return {
            "active_chains": len(self._dependents),
            "cached_chains": len(self._cache),
            "stale_after_seconds": self._stale_after,
            "refresh_interval_seconds": self._refresh_interval,
            "chains": {
                k: {
                    "has_snapshot": k in self._cache,
                    "age_seconds": (
                        self._cache[k].age_seconds
                        if k in self._cache and self._cache[k].age_seconds is not None
                        else None
                    ),
                    "is_stale": (
                        self._cache[k].is_stale if k in self._cache else False
                    ),
                    "pcr_oi": (
                        self._cache[k].pcr_oi if k in self._cache else None
                    ),
                    "max_pain": (
                        self._cache[k].max_pain if k in self._cache else None
                    ),
                    "scope": (
                        self._cache[k].scope if k in self._cache else None
                    ),
                }
                for k in list(self._dependents.keys())[:20]
            },
        }
