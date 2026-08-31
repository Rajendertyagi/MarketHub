"""Market analytics service — cached option-chain analytics for B6.

Provides a memory-only cache of derived option-chain analytics
(``OptionChainAnalyticsSnapshot``) keyed by provider-neutral chain
identity. The service owns:

  * active chain registry  — which chains need refreshing
  * latest snapshot cache  — one ``OptionChainAnalyticsSnapshot`` per chain
  * per-chain refresh lock — ensures one REST call per chain at a time
  * background scheduler  — periodic refresh of all active chains

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
DEFAULT_STALE_AFTER_SECONDS = 300.0

# Retry backoff constants.
INITIAL_RETRY_DELAY = 5.0
MAX_RETRY_DELAY = 60.0


class MarketAnalyticsService:
    """Cached option-chain analytics service for B6 analytics alerts."""

    def __init__(
        self,
        market_service: Any,
        instrument_catalog: Any = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        stale_after: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self._market_service = market_service
        self._instrument_catalog = instrument_catalog
        self._refresh_interval = max(
            MIN_REFRESH_INTERVAL_SECONDS, refresh_interval)
        self._stale_after = stale_after

        # chain_key → OptionChainAnalyticsSnapshot
        self._cache: dict[str, OptionChainAnalyticsSnapshot] = {}
        # chain_key → set of alert_ids that depend on it
        self._dependents: dict[str, set[str]] = {}
        # Per-chain refresh lock
        self._locks: dict[str, asyncio.Lock] = {}
        # Track last refresh time per chain (for scheduler)
        self._last_refresh: dict[str, float] = {}
        # Failures per chain (for staleness)
        self._failure_count: dict[str, int] = {}

        self._scheduler_task: asyncio.Task | None = None
        self._running = False

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def stale_after_seconds(self) -> float:
        return self._stale_after

    # ── Chain registry ──────────────────────────────────────────────────────

    def register_chain(self, chain_key: str, alert_id: str) -> None:
        """Register a chain key as needed (increments ref count)."""
        self._dependents.setdefault(chain_key, set()).add(alert_id)
        logger.debug("registered chain %s for alert %s", chain_key, alert_id)

    def unregister_chain(self, chain_key: str, alert_id: str) -> None:
        """Unregister an alert's dependency; remove chain if no dependents."""
        dependents = self._dependents.get(chain_key)
        if dependents is None:
            return
        dependents.discard(alert_id)
        if not dependents:
            del self._dependents[chain_key]
            # Drop cache entry — next registration will fetch fresh.
            self._cache.pop(chain_key, None)
            self._last_refresh.pop(chain_key, None)
            self._failure_count.pop(chain_key, None)
            logger.debug("unregistered chain %s (no dependents)", chain_key)

    def get_active_chains(self) -> set[str]:
        """Return the set of currently active chain keys."""
        return set(self._dependents.keys())

    def has_chain(self, chain_key: str) -> bool:
        return chain_key in self._dependents

    # ── Snapshot access ─────────────────────────────────────────────────────

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

    # ── Startup / lifecycle ─────────────────────────────────────────────────

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
        """Stop the background refresh scheduler."""
        self._running = False
        if bg_manager is not None:
            await bg_manager.cancel("analytics_scheduler")

    async def _scheduler_loop(self) -> None:
        """Periodically refresh all active chains."""
        while self._running:
            try:
                await self._refresh_all_active()
            except Exception as exc:
                logger.warning("analytics scheduler tick failed: %s", exc)
            try:
                await asyncio.wait_for(
                    asyncio.sleep(self._refresh_interval),
                    timeout=self._refresh_interval + 5.0)
            except asyncio.TimeoutError:
                pass

    async def _refresh_all_active(self) -> None:
        """Refresh every active chain, one at a time (bounded concurrency)."""
        keys = list(self._dependents.keys())
        for key in keys:
            if not self._running:
                return
            await self._refresh_one(key)

    async def _refresh_one(self, chain_key: str) -> None:
        """Refresh a single chain with per-chain locking."""
        lock = self._locks.setdefault(chain_key, asyncio.Lock())
        async with lock:
            try:
                await self._do_refresh(chain_key)
            except Exception as exc:
                self._failure_count[chain_key] = (
                    self._failure_count.get(chain_key, 0) + 1)
                logger.warning(
                    "analytics refresh failed for %s: %s",
                    chain_key, type(exc).__name__)

    async def _do_refresh(self, chain_key: str) -> None:
        """Fetch chain from MarketService and compute analytics."""
        # Parse chain_key: "canonical_id:expiry"
        parts = chain_key.split(":", 1)
        if len(parts) != 2:
            logger.error("invalid chain_key format: %r", chain_key)
            return
        canonical_id, expiry = parts
        # Extract exchange and underlying from canonical_id.
        # Format: "EXCHANGE:TYPE:SYMBOL" or "EXCHANGE:TYPE:SYMBOL:EXPIRY:STRIKE:OPT"
        id_parts = canonical_id.split(":")
        exchange = id_parts[0] if id_parts else ""
        underlying = id_parts[-1] if id_parts else ""

        # Look up the provider instrument key from catalog.
        # We need the instrument_token for the MarketService call.
        # For now, use the canonical_id's exchange + underlying to search.
        rows = []
        if self._instrument_catalog is not None:
            rows = self._instrument_catalog.search(
                exchange=exchange, q=underlying, limit=5)
        if not rows:
            logger.warning("no catalog match for chain %s", chain_key)
            return

        # Pick the best match (exact symbol match preferred).
        target = None
        for row in rows:
            if (row.get("tradingsymbol") or "").upper() == underlying.upper():
                target = row
                break
        if target is None:
            target = rows[0]

        instrument_key = target.get("instrument_token", "")
        tradingsymbol = target.get("tradingsymbol", "")

        try:
            snapshot = await self._market_service.option_chain(
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
            return

        # Compute analytics from the snapshot.
        pcr_result = compute_pcr(snapshot)
        pcr_vol_result = compute_pcr_volume(snapshot)
        mp_result = compute_max_pain(snapshot)
        iv_result = compute_iv_skew(snapshot)

        now = datetime.now(timezone.utc)
        analytics = OptionChainAnalyticsSnapshot(
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
            received_ts=now,
            calculated_at=now,
            stale_after_seconds=self._stale_after,
        )

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
                }
                for k in list(self._dependents.keys())[:20]
            },
        }
