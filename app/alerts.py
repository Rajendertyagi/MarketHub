"""Market alert engine: canonical quote callbacks -> trigger state.

Consumes MarketService quote updates (via the composition-root hook —
never polls REST). Rules are simple threshold comparisons over canonical
fields. State machine per alert:

    inactive --condition met--> triggered  (notification recorded once)
    triggered --operator re-arm--> inactive (manual, via API)

No trading execution. No external notification channels.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

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


class AlertEngine:
    """Evaluates enabled alerts against live canonical quotes."""

    def __init__(self, store: Any, on_trigger: Any = None) -> None:
        self._store = store
        self._on_trigger = on_trigger   # push hook (generic EventBroker)
        self._lock = threading.Lock()
        self._rules: dict[int, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._last_values: dict[tuple[str, str], float] = {}
        self.reload()

    def reload(self) -> None:
        """Re-read enabled alert rules from persistence."""
        try:
            rules = self._store.load_enabled_alerts()
        except Exception as exc:
            logger.warning("alert reload failed: %s", type(exc).__name__)
            return
        with self._lock:
            self._rules = {r["id"]: r for r in rules}

    def evaluate(self, quote: Any) -> list[dict[str, Any]]:
        """Check one canonical Quote against all matching active rules.

        Returns newly-fired notifications (already persisted as
        'triggered'); repeated ticks never re-notify until re-arm.
        """
        fired: list[dict[str, Any]] = []
        with self._lock:
            rules = list(self._rules.values())
        for rule in rules:
            if rule["exchange"] != quote.exchange or \
                    rule["instrument_token"] != quote.instrument_token:
                continue
            if rule["state"] != "inactive":
                continue  # already triggered; manual re-arm required
            value = getattr(quote, _FIELD_ATTRS.get(rule["field"], ""),
                            None)
            if value is None:
                continue
            hit = (value > rule["threshold"] if rule["operator"] == "gt"
                   else value < rule["threshold"])
            if not hit:
                continue
            try:
                self._store.record_trigger(rule["id"])
            except Exception as exc:
                logger.warning("alert trigger persist failed: %s",
                               type(exc).__name__)
                continue
            with self._lock:
                if rule["id"] in self._rules:
                    self._rules[rule["id"]]["state"] = "triggered"
            notification = {
                "alert_id": rule["id"],
                "tradingsymbol": rule["tradingsymbol"],
                "field": rule["field"],
                "operator": rule["operator"],
                "threshold": rule["threshold"],
                "value": value,
                "ts": time.time(),
            }
            fired.append(notification)
            self.add_notification(notification)
            if self._on_trigger is not None:
                try:
                    self._on_trigger(notification)
                except Exception as exc:
                    logger.warning("alert push failed: %s",
                                   type(exc).__name__)
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


