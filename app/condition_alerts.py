"""Advanced market_condition alert engine (B2) — single quote-backed conditions.

Consumes MarketService quote updates via the composition-root hook (never
polls REST). Each enabled condition alert owns exactly ONE condition over a
canonical instrument identity; the engine evaluates it against every live
canonical Quote that resolves to that identity.

State machine (frozen in B2):

    LEVEL (eq/ne/gt/gte/lt/lte):
        UNKNOWN -> TRUE   fires (first observation)
        UNKNOWN -> FALSE  persists baseline, no fire
        FALSE  -> TRUE    FIRES
        TRUE   -> TRUE    no fire
        TRUE   -> FALSE   re-arms, no fire
        FALSE  -> FALSE   no fire
        TRUE   -> UNKNOWN does NOT re-arm
        FALSE  -> UNKNOWN retains FALSE

    CROSSING (crosses_above/crosses_below):
        side = above if value > threshold else below_or_equal
        first observation establishes the side, never fires
        crosses_above fires below_or_equal -> above
        crosses_below fires above -> below_or_equal
        repeat fires per crossing; once disables after the first trigger

Restart safety: only ``last_result`` / ``crossing_side`` are persisted (in
``condition_runtime_state``). ``armed`` and ``previous_value`` are NOT
persisted — the side of the threshold is sufficient because condition
definitions are immutable in B2. ``previous_value`` is an in-memory
diagnostic only.

Trigger atomicity (B2 §38/§46): a trigger persists runtime state + alert row
+ the canonical ``alert.triggered`` event + consumer materialization in ONE
SQLite transaction (``EventStore.save_condition_trigger``), then wakes the
live pipeline via ``events.finalize_persisted_event`` WITHOUT re-inserting
the event. A lost trigger is forbidden.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from core import events
from core.alert_events import ALERT_ENGINE_SOURCE, build_alert_triggered_data
from core.persistence.modules.condition_alerts import (
    CROSSING_ABOVE,
    CROSSING_BELOW_OR_EQUAL,
    CROSSING_UNKNOWN,
    LAST_RESULT_FALSE,
    LAST_RESULT_TRUE,
    LAST_RESULT_UNKNOWN,
    CROSSING_OPERATORS,
    LEVEL_OPERATORS,
    validate_condition_json,
)
from market.condition_metrics import extract_metric

logger = logging.getLogger("event_server")

# Canonical alert family for the market_condition engine (B2).
CONDITION_ALERT_FAMILY = "market_condition"


def _compare(operator: str, value: float, threshold: float) -> str:
    """LEVEL comparison -> 'true' | 'false' (never 'unknown')."""
    if operator == "eq":
        return LAST_RESULT_TRUE if value == threshold else LAST_RESULT_FALSE
    if operator == "ne":
        return LAST_RESULT_TRUE if value != threshold else LAST_RESULT_FALSE
    if operator == "gt":
        return LAST_RESULT_TRUE if value > threshold else LAST_RESULT_FALSE
    if operator == "gte":
        return LAST_RESULT_TRUE if value >= threshold else LAST_RESULT_FALSE
    if operator == "lt":
        return LAST_RESULT_TRUE if value < threshold else LAST_RESULT_FALSE
    if operator == "lte":
        return LAST_RESULT_TRUE if value <= threshold else LAST_RESULT_FALSE
    raise ValueError(f"unknown operator: {operator!r}")


class ConditionAlertEngine:
    """Evaluates enabled condition alerts against live canonical quotes.

    Instrument-indexed: alerts are keyed by canonical instrument id, so a
    quote only ever touches the alerts registered for its resolved identity
    (NO global scan). Per-alert ``asyncio.Lock`` serializes evaluation for a
    single alert across concurrent quote callbacks without blocking the event
    loop across ``await asyncio.to_thread(...)`` persistence calls.
    """

    def __init__(self, store: Any, resolver: Any, bus: Any = None) -> None:
        self._store = store
        self._resolver = resolver
        self._bus = bus  # MCP subscription bus (optional; resource notify)
        self._lock = threading.Lock()
        self._alerts: dict[str, dict[str, Any]] = {}
        self._index: dict[str, set[str]] = {}
        self._state: dict[str, dict[str, str]] = {}
        self._last_values: dict[str, float] = {}
        self._alert_locks: dict[str, asyncio.Lock] = {}
        self._notifications: list[dict[str, Any]] = []
        self.reload()

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def reload(self) -> None:
        """Re-read enabled condition alerts + runtime state from persistence.

        Malformed rows are logged and skipped — a bad row must never crash
        the engine or block evaluation of healthy alerts.
        """
        try:
            alerts = self._store.load_enabled_condition_alerts()
            state = self._store.load_condition_runtime_state()
        except Exception as exc:
            logger.warning("condition alert reload failed: %s",
                           type(exc).__name__)
            return
        with self._lock:
            self._alerts = {}
            self._index = {}
            for alert in alerts:
                try:
                    condition = validate_condition_json(alert.get("condition"))
                except Exception as exc:
                    logger.warning(
                        "condition alert %s skipped (malformed): %s",
                        alert.get("alert_id"), type(exc).__name__)
                    continue
                alert["_condition"] = condition
                self._alerts[alert["alert_id"]] = alert
                canonical_id = condition["instrument"]["canonical_id"]
                self._index.setdefault(canonical_id, set()).add(
                    alert["alert_id"])
            self._state = {
                alert_id: st for alert_id, st in state.items()
                if alert_id in self._alerts
            }
            self._last_values = {}

    # ── Evaluation ──────────────────────────────────────────────────────────

    async def evaluate(self, quote: Any) -> list[dict[str, Any]]:
        """Check one canonical Quote against its resolved condition alerts.

        Returns newly-fired trigger records (already persisted atomically).
        """
        canonical_id = self._resolver.resolve_quote(quote)
        if canonical_id is None:
            return []
        with self._lock:
            alert_ids = list(self._index.get(canonical_id, ()))
        fired: list[dict[str, Any]] = []
        for alert_id in alert_ids:
            lock = self._alert_locks.setdefault(alert_id, asyncio.Lock())
            async with lock:
                result = await self._evaluate_one(alert_id, quote)
                if result is not None:
                    fired.append(result)
        return fired

    async def _evaluate_one(
        self, alert_id: str, quote: Any
    ) -> dict[str, Any] | None:
        with self._lock:
            alert = self._alerts.get(alert_id)
        if alert is None:
            return None
        condition = alert["_condition"]
        metric = condition["metric"]
        operator = condition["operator"]
        threshold = condition["value"]

        value = extract_metric(quote, metric)
        with self._lock:
            state = dict(self._state.get(
                alert_id,
                {"last_result": LAST_RESULT_UNKNOWN,
                 "crossing_side": CROSSING_UNKNOWN}))
            previous_value = self._last_values.get(alert_id)

        if operator in CROSSING_OPERATORS:
            new_side = state["crossing_side"]
            fire = False
            if value is not None:
                new_side = (
                    CROSSING_ABOVE if value > threshold
                    else CROSSING_BELOW_OR_EQUAL)
                prev_side = state["crossing_side"]
                if operator == "crosses_above":
                    fire = (prev_side == CROSSING_BELOW_OR_EQUAL
                            and new_side == CROSSING_ABOVE)
                else:
                    fire = (prev_side == CROSSING_ABOVE
                            and new_side == CROSSING_BELOW_OR_EQUAL)
            new_state = {
                "last_result": state["last_result"],
                "crossing_side": new_side,
            }
            changed = new_side != state["crossing_side"]
        else:
            new_result = state["last_result"]
            fire = False
            if value is not None:
                new_result = _compare(operator, value, threshold)
                prev_result = state["last_result"]
                if prev_result == LAST_RESULT_UNKNOWN:
                    fire = new_result == LAST_RESULT_TRUE
                elif prev_result == LAST_RESULT_FALSE \
                        and new_result == LAST_RESULT_TRUE:
                    fire = True
            new_state = {
                "last_result": new_result,
                "crossing_side": state["crossing_side"],
            }
            changed = new_result != state["last_result"]

        # Persist the transition FIRST, then advance in-memory state. A
        # failed trigger persistence must NOT consume the trigger in memory
        # (a lost trigger is forbidden): the alert stays armed and the next
        # qualifying quote fires.
        if fire:
            result = await self._trigger(
                alert, condition, quote, value, previous_value, new_state)
            if result is not None:
                with self._lock:
                    self._state[alert_id] = new_state
                    if value is not None:
                        self._last_values[alert_id] = value
            return result
        if changed:
            if await self._save_state(alert, condition, new_state):
                with self._lock:
                    self._state[alert_id] = new_state
                    if value is not None:
                        self._last_values[alert_id] = value
        return None

    # ── Trigger ─────────────────────────────────────────────────────────────

    async def _trigger(
        self,
        alert: dict[str, Any],
        condition: dict[str, Any],
        quote: Any,
        value: float,
        previous_value: float | None,
        new_state: dict[str, str],
    ) -> dict[str, Any] | None:
        alert_id = alert["alert_id"]
        consumer_id = alert["consumer_id"]
        condition_id = condition["condition_id"]
        trigger_mode = alert["trigger_mode"]
        one_shot = trigger_mode == "once"
        enabled = not one_shot

        trigger_count = int(alert.get("trigger_count") or 0) + 1
        last_triggered_at = datetime.now(timezone.utc).isoformat()

        data = build_alert_triggered_data(
            alert_family=CONDITION_ALERT_FAMILY,
            alert_id=alert_id,
            consumer_id=consumer_id,
            condition={
                "metric": condition["metric"],
                "operator": condition["operator"],
                "value": condition["value"],
            },
            observed={
                "metric": condition["metric"],
                "operator": condition["operator"],
                "expected": condition["value"],
                "value": value,
                "previous_value": previous_value,
                "condition_id": condition_id,
            },
            instrument=self._instrument_payload(quote, condition),
            one_shot=one_shot,
            metadata={"trigger_mode": trigger_mode},
        )

        event_id = uuid.uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat()
        routing = {"targets": [consumer_id]}
        event = {
            "id": event_id,
            "type": "alert.triggered",
            "source": ALERT_ENGINE_SOURCE,
            "timestamp": timestamp,
            "data": data,
            "persistent": True,
            "routing": routing,
        }

        # Atomic persistence: runtime state + alert row + event + consumer
        # materialization in ONE transaction. A lost trigger is forbidden.
        try:
            sequence = await asyncio.to_thread(
                self._store.save_condition_trigger,
                alert_id=alert_id,
                condition_id=condition_id,
                consumer_id=consumer_id,
                event_id=event_id,
                event_type=event["type"],
                source=event["source"],
                timestamp=timestamp,
                data=data,
                routing=routing,
                last_result=new_state["last_result"],
                crossing_side=new_state["crossing_side"],
                enabled=enabled,
                trigger_count=trigger_count,
                last_triggered_at=last_triggered_at,
            )
        except Exception as exc:
            logger.warning("condition trigger persist failed: %s",
                           type(exc).__name__)
            return None
        event["sequence"] = sequence

        # Update in-memory alert row (once disables + leaves the index).
        with self._lock:
            alert["trigger_count"] = trigger_count
            alert["last_triggered_at"] = last_triggered_at
            if one_shot:
                alert["enabled"] = False
                self._index.get(
                    condition["instrument"]["canonical_id"], set()
                ).discard(alert_id)

        # Live wake-up WITHOUT re-inserting the event (already persisted).
        try:
            await events.finalize_persisted_event(event, self._store, self._bus)
        except Exception as exc:
            logger.warning("condition trigger finalize failed: %s",
                           type(exc).__name__)

        notification = {
            "alert_id": alert_id,
            "condition_id": condition_id,
            "consumer_id": consumer_id,
            "metric": condition["metric"],
            "operator": condition["operator"],
            "threshold": condition["value"],
            "value": value,
            "previous_value": previous_value,
            "trigger_mode": trigger_mode,
            "event_id": event_id,
            "sequence": sequence,
            "ts": time.time(),
        }
        self.add_notification(notification)
        return notification

    async def _save_state(
        self,
        alert: dict[str, Any],
        condition: dict[str, Any],
        new_state: dict[str, str],
    ) -> bool:
        """Persist a non-trigger state transition (standalone write).

        Returns True only when the write committed; on failure the in-memory
        state is left unchanged so memory stays consistent with the DB.
        """
        try:
            await asyncio.to_thread(
                self._store.save_condition_runtime_state,
                alert_id=alert["alert_id"],
                condition_id=condition["condition_id"],
                last_result=new_state["last_result"],
                crossing_side=new_state["crossing_side"],
            )
            return True
        except Exception as exc:
            logger.warning("condition state persist failed: %s",
                           type(exc).__name__)
            return False

    def _instrument_payload(
        self, quote: Any, condition: dict[str, Any]
    ) -> dict[str, Any]:
        canonical_id = condition["instrument"]["canonical_id"]
        context = self._resolver.context_for(canonical_id)
        payload: dict[str, Any] = {
            "canonical_id": canonical_id,
            "exchange": quote.exchange,
            "instrument_type": context.get("instrument_type"),
            "tradingsymbol": quote.tradingsymbol,
            "instrument_token": quote.instrument_token,
        }
        for key in ("name", "underlying", "expiry", "strike", "option_type"):
            if context.get(key) is not None:
                payload[key] = context[key]
        return payload

    # ── Diagnostics ─────────────────────────────────────────────────────────

    def add_notification(self, n: dict[str, Any]) -> None:
        with self._lock:
            self._notifications.insert(0, n)
            del self._notifications[50:]  # bounded memory

    def recent_notifications(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._notifications[:limit])

    def clear_notification(self, alert_id: str) -> None:
        with self._lock:
            self._notifications = [n for n in self._notifications
                                   if n.get("alert_id") != alert_id]