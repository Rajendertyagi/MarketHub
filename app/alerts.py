"""Market alert engine: canonical quote callbacks -> trigger state.

Consumes MarketService quote updates (via the composition-root hook —
never polls REST). Rules are simple threshold comparisons over canonical
fields. State machine per alert:

    inactive --condition met--> triggered  (notification recorded once)
    triggered --operator re-arm--> inactive (manual, via API)

Each trigger records alert state + trigger history AND publishes exactly
one canonical durable ``alert.triggered`` event through the shared event
pipeline (broadcast, routing=None). No trading execution. No external
notification channels.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from core import events
from core.alert_events import ALERT_ENGINE_SOURCE, build_alert_triggered_data

logger = logging.getLogger("event_server")

_FIELD_ATTRS = {
    "ltp": "ltp",
    "change_percent": "change_percent",
    "volume": "volume",
    "oi_change_percent": "oi_change_percent",
}

# Crossing operators track the previous canonical value per instrument so
# an alert fires ONCE at the moment of crossing, not on every tick beyond.
_CROSSING_OPERATORS = frozenset({"crosses_above", "crosses_below"})
_ALL_OPERATORS = frozenset({"gt", "lt"}) | _CROSSING_OPERATORS

#: ABSOLUTE freshness ceiling for alert evaluation (the same default
#: window the canonical quote service uses). A quote older than this - or
#: one whose age cannot be established - must NEVER fire an alert.
_DEFAULT_MAX_QUOTE_AGE_SECONDS = 120.0


class AlertEngine:
    """Evaluates enabled alerts against live canonical quotes."""

    def __init__(self, store: Any, bus: Any = None) -> None:
        self._store = store
        self._bus = bus   # MCP subscription bus (optional; resource notify)
        self._lock = threading.Lock()
        self._rules: dict[int, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._last_values: dict[tuple[str, str], float] = {}
        # Per-alert async serialization for trigger decisions/commitment.
        # Independent alerts evaluate concurrently; the same alert never
        # commits more than once for a single activation.
        self._alert_locks: dict[int, asyncio.Lock] = {}
        self._alert_locks_guard = threading.Lock()
        # The ABSOLUTE freshness ceiling for alert evaluation (the same
        # window the canonical quote service uses by default).
        self._max_quote_age_seconds: float = _DEFAULT_MAX_QUOTE_AGE_SECONDS
        # How many evaluations were refused because the quote was stale or
        # undated (surfaced for diagnostics).
        self._stale_evaluations = 0
        self.reload()

    @staticmethod
    def _quote_age(quote: Any) -> float | None:
        """The age of a quote in seconds (None when unknown)."""
        received = getattr(quote, "received_ts", None)
        if received is None:
            return None
        try:
            now = datetime.now(timezone.utc)
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            return (now - received).total_seconds()
        except Exception:
            return None

    @staticmethod
    def _provider_for(instrument_token: str | None) -> str | None:
        """The provider that published a storage key.

        The provider REGISTRY is the authority (the catalogue first, the
        adapter-declared key style as the fallback) - the durable trigger
        record never leaves the provider blank.
        """
        if not instrument_token:
            return None
        try:
            from providers.registry import get_registry
            provider = get_registry().provider_for_key(str(instrument_token))
        except Exception:
            provider = None
        return str(provider) if provider else None

    def _get_alert_lock(self, alert_id: int) -> asyncio.Lock:
        with self._alert_locks_guard:
            lock = self._alert_locks.get(alert_id)
            if lock is None:
                lock = asyncio.Lock()
                self._alert_locks[alert_id] = lock
            return lock

    def _cleanup_alert_locks(self, active_ids: set[int] | None = None) -> None:
        """Drop idle per-alert locks for alerts no longer active.

        Safe to call without holding alert locks: only idle, unreferenced
        locks are removed, so lock lifecycle remains bounded as alerts are
        deleted/disabled/reloaded.
        """
        with self._alert_locks_guard:
            for alert_id, lock in list(self._alert_locks.items()):
                if active_ids is not None and alert_id in active_ids:
                    continue
                if not lock.locked():
                    self._alert_locks.pop(alert_id, None)

    def reload(self) -> None:
        """Re-read enabled alert rules from persistence."""
        try:
            rules = self._store.load_enabled_alerts()
        except Exception as exc:
            logger.warning("alert reload failed: %s", type(exc).__name__)
            return
        with self._lock:
            self._rules = {r["id"]: r for r in rules}
            active_ids = set(self._rules)
        self._cleanup_alert_locks(active_ids)

    async def evaluate(self, quote: Any) -> list[dict[str, Any]]:
        """Check one canonical Quote against all matching active rules.

        Returns newly-fired notifications (already persisted as
        'triggered'); repeated ticks never re-notify until re-arm.

        A quote whose age cannot be established, or whose age exceeds the
        freshness window, NEVER fires: stale or undated data must never
        trigger an alert.
        """
        fired: list[dict[str, Any]] = []
        age = self._quote_age(quote)
        if age is None or age > self._max_quote_age_seconds:
            with self._lock:
                self._stale_evaluations += 1
            return fired
        with self._lock:
            rules = list(self._rules.values())
        for rule in rules:
            if rule["exchange"] != quote.exchange or \
                    rule["instrument_token"] != quote.instrument_token:
                continue
            if rule["state"] != "inactive":
                continue  # already triggered; manual re-arm required
            attr = _FIELD_ATTRS.get(rule["field"], None)
            if attr is None:
                continue
            value = getattr(quote, attr, None)
            if value is None:
                continue
            op = rule["operator"]
            key = (quote.exchange, quote.instrument_token)
            with self._lock:
                prev = self._last_values.get(key)
                self._last_values[key] = value
            if op == "crosses_above":
                hit = prev is not None and prev <= rule["threshold"] < value
            elif op == "crosses_below":
                hit = prev is not None and prev >= rule["threshold"] > value
            elif op == "gt":
                hit = value > rule["threshold"]
            else:
                hit = value < rule["threshold"]
            if not hit:
                continue

            alert_id = rule["id"]
            alert_lock = self._get_alert_lock(alert_id)
            async with alert_lock:
                # Fresh-state revalidation under per-alert serialization.
                with self._lock:
                    current = self._rules.get(alert_id)
                if current is None or current.get("state") != "inactive" or not current.get("enabled", 1):
                    continue

                triggered_at = datetime.now(timezone.utc).isoformat()
                _provider = getattr(quote, "provider", None)
                if _provider is None:
                    _provider = getattr(quote, "source", None)
                if _provider is None:
                    _provider = self._provider_for(quote.instrument_token)

                # Durable trigger commitment: state + history in one DB
                # transaction, committed only if still inactive/enabled.
                # This is the at-most-once trigger commitment boundary.
                try:
                    commit_fn = getattr(self._store, "commit_market_alert_trigger", None)
                    if callable(commit_fn):
                        committed = commit_fn(
                            alert_id=alert_id,
                            exchange=current.get("exchange"),
                            instrument_token=current.get("instrument_token"),
                            tradingsymbol=current.get("tradingsymbol"),
                            field=current.get("field"),
                            operator=current.get("operator"),
                            threshold=current.get("threshold"),
                            observed_value=value,
                            provider=_provider,
                            triggered_at=triggered_at,
                        )
                    else:
                        # Backward-compatible fallback for stores without the
                        # atomic commit API (e.g. lightweight test doubles).
                        self._store.record_trigger(alert_id)
                        try:
                            self._store.record_alert_trigger_history(
                                alert_id=alert_id,
                                exchange=current.get("exchange"),
                                instrument_token=current.get("instrument_token"),
                                tradingsymbol=current.get("tradingsymbol"),
                                field=current.get("field"),
                                operator=current.get("operator"),
                                threshold=current.get("threshold"),
                                observed_value=value,
                                provider=_provider,
                                triggered_at=triggered_at,
                            )
                        except Exception as exc:
                            logger.warning("alert trigger history persist failed: %s",
                                           type(exc).__name__)
                        committed = True
                except Exception as exc:
                    logger.warning("alert trigger persist failed: %s",
                                   type(exc).__name__)
                    continue
                if not committed:
                    # Lost the race or alert no longer triggerable.
                    with self._lock:
                        stale = self._rules.get(alert_id)
                        if stale is not None and stale.get("state") == "inactive":
                            # Reflect durable state conservatively.
                            stale["state"] = "triggered"
                    continue

                # Canonical durable event publication — exactly one per
                # committed trigger. Failure here must not roll back the
                # durable trigger commitment (at-most-once commitment).
                try:
                    await events.publish_event(
                        event_type="alert.triggered",
                        source=ALERT_ENGINE_SOURCE,
                        data=build_alert_triggered_data(
                            alert_family="market",
                            alert_id=alert_id,
                            consumer_id=None,
                            condition={
                                "field": current.get("field"),
                                "operator": current.get("operator"),
                                "threshold": current.get("threshold"),
                            },
                            observed={"value": value},
                            instrument={
                                "exchange": current.get("exchange"),
                                "instrument_token": current.get("instrument_token"),
                                "tradingsymbol": current.get("tradingsymbol"),
                            },
                            one_shot=False,
                            metadata={},
                        ),
                        persistent=True,
                        routing=None,
                        store=self._store,
                        bus=self._bus,
                    )
                except Exception as exc:
                    logger.warning("alert trigger event publish failed: %s",
                                   type(exc).__name__)

                with self._lock:
                    if alert_id in self._rules:
                        self._rules[alert_id]["state"] = "triggered"
                notification = {
                    "alert_id": alert_id,
                    "tradingsymbol": current.get("tradingsymbol"),
                    "field": current.get("field"),
                    "operator": current.get("operator"),
                    "threshold": current.get("threshold"),
                    "value": value,
                    "ts": time.time(),
                }
                fired.append(notification)
                self.add_notification(notification)
        return fired

    def add_notification(self, n: dict[str, Any]) -> None:
        with self._lock:
            self._notifications.insert(0, n)
            del self._notifications[50:]  # bounded memory

    def recent_notifications(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._notifications[:limit])

    def clear_notification(self, alert_id: int) -> None:
        with self._lock:
            self._notifications = [n for n in self._notifications
                                   if n.get("alert_id") != alert_id]


