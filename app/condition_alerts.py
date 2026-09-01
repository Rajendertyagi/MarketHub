"""Advanced market_condition alert engine (B2+B4).

B2: single quote-backed leaf conditions (condition_version=1).
B4: structured ALL/ANY condition groups (condition_version=2).

Consumes MarketService quote updates via the composition-root hook.
Each enabled condition alert owns ONE condition tree over a canonical
instrument identity; the engine evaluates it against every live canonical
Quote that resolves to that identity.

State machine (frozen):

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
        crossing RESULT is ephemeral: TRUE only on the crossing tick,
        then FALSE on subsequent same-side ticks. SIDE is persisted.

    GROUP (ALL/ANY, v2):
        ALL: TRUE iff all children TRUE; FALSE iff any child FALSE; else UNKNOWN
        ANY: TRUE iff any child TRUE; FALSE iff all children FALSE; else UNKNOWN

Restart safety: leaf last_result/crossing_side and root last_result are
persisted in ``condition_runtime_state``. ``armed`` and ``previous_value``
are NOT persisted.

Trigger atomicity: a trigger persists runtime state (batch) + alert row
+ the canonical ``alert.triggered`` event + consumer materialization in
ONE SQLite transaction (``EventStore.save_condition_trigger``), then wakes
the live pipeline via ``events.finalize_persisted_event`` WITHOUT
re-inserting the event. A lost trigger is forbidden.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from core import events
from core.alert_events import ALERT_ENGINE_SOURCE, build_alert_triggered_data
from core.persistence.modules.condition_alerts import (
    CONDITION_VERSION_V1,
    CONDITION_VERSION_V2,
    CROSSING_ABOVE,
    CROSSING_BELOW_OR_EQUAL,
    CROSSING_OPERATORS,
    CROSSING_UNKNOWN,
    LAST_RESULT_FALSE,
    LAST_RESULT_TRUE,
    LAST_RESULT_UNKNOWN,
    LEVEL_OPERATORS,
    ROOT_CONDITION_ID_PREFIX,
    validate_condition_tree,
)
from market.condition_metrics import extract_metric, extract_analytics_metric, METRIC_SOURCE

logger = logging.getLogger("event_server")

# Canonical alert family for the market_condition engine.
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


# ---------------------------------------------------------------------------
# Kleene three-valued logic for groups
# ---------------------------------------------------------------------------

def _all_result(children: list[str]) -> str:
    """ALL (AND): TRUE only if every child is TRUE."""
    if LAST_RESULT_FALSE in children:
        return LAST_RESULT_FALSE
    if all(r == LAST_RESULT_TRUE for r in children):
        return LAST_RESULT_TRUE
    return LAST_RESULT_UNKNOWN


def _any_result(children: list[str]) -> str:
    """ANY (OR): TRUE if at least one child is TRUE."""
    if LAST_RESULT_TRUE in children:
        return LAST_RESULT_TRUE
    if all(r == LAST_RESULT_FALSE for r in children):
        return LAST_RESULT_FALSE
    return LAST_RESULT_UNKNOWN


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ConditionAlertEngine:
    """Evaluates enabled condition alerts against live canonical quotes.

    Instrument-indexed: alerts are keyed by canonical instrument id, so a
    quote only ever touches the alerts registered for its resolved identity
    (NO global scan). Per-alert ``asyncio.Lock`` serializes evaluation for
    a single alert across concurrent quote callbacks.

    State model per alert:
        _state[alert_id] = {
            "leaves": {condition_id: {"last_result": ..., "crossing_side": ...}},
            "root":   {"last_result": ..., "crossing_side": "unknown"},
        }
    """

    def __init__(self, store: Any, resolver: Any, bus: Any = None,
                 analytics_service: Any = None) -> None:
        self._store = store
        self._resolver = resolver
        self._bus = bus
        self._analytics = analytics_service
        self._lock = threading.Lock()
        self._alerts: dict[str, dict[str, Any]] = {}
        # dependency_key → set of alert_ids (B7: multi-target routing)
        self._dep_index: dict[str, set[str]] = {}
        # alert_id → set of dependency_keys
        self._alert_deps: dict[str, set[str]] = {}
        # alert_id → {"leaves": {cond_id: state}, "root": state}
        self._state: dict[str, dict[str, Any]] = {}
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
            raw_state = self._store.load_condition_runtime_state()
        except Exception as exc:
            logger.warning("condition alert reload failed: %s",
                           type(exc).__name__)
            return
        with self._lock:
            self._alerts = {}
            self._dep_index = {}
            self._alert_deps = {}
            self._state = {}
            for alert in alerts:
                try:
                    condition = validate_condition_tree(
                        alert.get("condition"))
                except Exception as exc:
                    logger.warning(
                        "condition alert %s skipped (malformed): %s",
                        alert.get("alert_id"), type(exc).__name__)
                    continue
                alert["_condition"] = condition
                alert_id = alert["alert_id"]
                self._alerts[alert_id] = alert
                # B7: Build multi-target dependency index.
                dep_keys = self._get_dependency_keys(condition)
                self._alert_deps[alert_id] = dep_keys
                for dk in dep_keys:
                    self._dep_index.setdefault(dk, set()).add(alert_id)
                # Build per-alert state from DB rows.
                alert_rows = raw_state.get(alert_id, {})
                leaves: dict[str, dict[str, str]] = {}
                root_state: dict[str, str] = {
                    "last_result": LAST_RESULT_UNKNOWN,
                    "crossing_side": CROSSING_UNKNOWN,
                }
                for cid, st in alert_rows.items():
                    if cid.startswith(ROOT_CONDITION_ID_PREFIX):
                        root_state = st
                    else:
                        leaves[cid] = st
                # For v1: ensure the single leaf has an entry.
                if condition.get("condition_version") == CONDITION_VERSION_V1:
                    cid = condition["condition_id"]
                    if cid not in leaves:
                        leaves[cid] = {
                            "last_result": LAST_RESULT_UNKNOWN,
                            "crossing_side": CROSSING_UNKNOWN,
                        }
                    if not any(cid.startswith(ROOT_CONDITION_ID_PREFIX)
                               for cid in alert_rows):
                        root_state = dict(leaves[cid])
                self._state[alert_id] = {
                    "leaves": leaves,
                    "root": root_state,
                }
            self._last_values = {}
            # B7: per-(alert_id, condition_id) last-known values for cross-instrument
            self._dep_last_values: dict[tuple[str, str], float] = {}
            # B7: track which analytics leaves have seen a non-None value.
            # When an analytics snapshot disappears (None) after having a value,
            # the leaf must go UNKNOWN, not preserve its old state.
            self._analytics_seen: set[tuple[str, str]] = set()

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _get_dependency_keys(condition: dict[str, Any]) -> set[str]:
        """Return all dependency keys for a condition tree (B7 multi-target).

        Quote deps: "quote:<canonical_id>"
        Analytics deps: "analytics:<canonical_id>:<expiry>"
        """
        keys: set[str] = set()
        version = condition.get("condition_version")
        if version == CONDITION_VERSION_V1:
            dep = condition.get("_dependency_key")
            if dep:
                keys.add(dep)
            elif "expiry" in condition.get("instrument", {}):
                cid = condition.get("instrument", {}).get("canonical_id", "")
                keys.add(f"analytics:{cid}:{condition['instrument']['expiry']}")
            else:
                cid = condition.get("instrument", {}).get("canonical_id", "")
                if cid:
                    keys.add(f"quote:{cid}")
        elif version == CONDITION_VERSION_V2:
            for child in condition.get("conditions", []):
                keys |= ConditionAlertEngine._get_dependency_keys(child)
        return keys

    @staticmethod
    def _first_canonical_id(condition: dict[str, Any]) -> str:
        """Return the canonical_id of the first leaf in the tree."""
        version = condition.get("condition_version")
        if version == CONDITION_VERSION_V1:
            return condition.get("instrument", {}).get("canonical_id", "")
        children = condition.get("conditions", [])
        if children:
            return ConditionAlertEngine._first_canonical_id(children[0])
        return ""

    def _get_alert_and_state(
        self, alert_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert is None:
                return None
            state = dict(self._state[alert_id])
            state["leaves"] = dict(state["leaves"])
        return alert, state

    # ── Evaluation ──────────────────────────────────────────────────────────

    async def evaluate(self, quote: Any) -> list[dict[str, Any]]:
        """Check one canonical Quote against its resolved condition alerts.

        Returns newly-fired trigger records (already persisted atomically).
        B7: uses dependency index — only evaluates alerts that depend on
        this quote's canonical identity.
        """
        canonical_id = self._resolver.resolve_quote(quote)
        if canonical_id is None:
            return []
        dep_key = f"quote:{canonical_id}"
        with self._lock:
            alert_ids = list(self._dep_index.get(dep_key, ()))
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
        obj = self._get_alert_and_state(alert_id)
        if obj is None:
            return None
        alert, state = obj
        condition = alert["_condition"]
        version = condition.get("condition_version", CONDITION_VERSION_V1)

        if version == CONDITION_VERSION_V1:
            return await self._evaluate_leaf_v1(alert, state, quote)
        else:
            return await self._evaluate_group(alert, state, quote)

    async def _evaluate_leaf_v1(
        self,
        alert: dict[str, Any],
        state: dict[str, Any],
        quote: Any,
    ) -> dict[str, Any] | None:
        """Evaluate a v1 single-leaf condition alert."""
        condition = alert["_condition"]
        metric = condition["metric"]
        operator = condition["operator"]
        threshold = condition["value"]
        condition_id = condition["condition_id"]

        # Analytics metrics are evaluated against the cached snapshot,
        # not per-quote. Quote is ignored for analytics-backed leaves.
        if METRIC_SOURCE.get(metric) == "analytics" and self._analytics is not None:
            value = self._extract_analytics_value(alert, condition, metric)
        else:
            value = extract_metric(quote, metric)
        leaf_state = state["leaves"].get(
            condition_id,
            {"last_result": LAST_RESULT_UNKNOWN,
             "crossing_side": CROSSING_UNKNOWN})
        previous_value = self._last_values.get(alert["alert_id"])

        new_state = await self._evaluate_leaf_node(
            operator, threshold, value, leaf_state,
            analytics_seen=(metric in ("pcr_oi", "pcr_volume", "max_pain", "iv_skew")
                            and (alert["alert_id"], condition_id)
                            in self._analytics_seen),
            prev_value=self._last_values.get(alert["alert_id"]))
        changed = (new_state["last_result"] != leaf_state["last_result"]
                   or new_state["crossing_side"] != leaf_state["crossing_side"])

        # For v1, root state mirrors leaf state.
        root_state = dict(new_state)
        all_states = {condition_id: new_state,
                      ROOT_CONDITION_ID_PREFIX + alert["alert_id"]: root_state}

        fire = self._check_root_fire(state["root"], root_state)
        if fire:
            result = await self._trigger(
                alert, quote, value, previous_value,
                all_states, root_state)
            if result is not None:
                with self._lock:
                    self._state[alert["alert_id"]]["leaves"][condition_id] = new_state
                    self._state[alert["alert_id"]]["root"] = root_state
                    if value is not None:
                        self._last_values[alert["alert_id"]] = value
            return result
        if changed:
            if await self._save_states(alert, all_states):
                with self._lock:
                    self._state[alert["alert_id"]]["leaves"][condition_id] = new_state
                    self._state[alert["alert_id"]]["root"] = root_state
                    if value is not None:
                        self._last_values[alert["alert_id"]] = value
        return None

    async def _evaluate_group(
        self,
        alert: dict[str, Any],
        state: dict[str, Any],
        quote: Any,
    ) -> dict[str, Any] | None:
        """Evaluate a v2 grouped condition alert (may contain nested groups)."""
        condition = alert["_condition"]
        root_condition_id = ROOT_CONDITION_ID_PREFIX + alert["alert_id"]
        previous_value = self._last_values.get(alert["alert_id"])

        # Evaluate all children (leaves and nested groups), collecting results.
        child_results: list[str] = []
        leaf_updates: dict[str, dict[str, str]] = {}

        for child in condition["conditions"]:
            cid = self._resolve_child_id(child)
            if child.get("condition_version") == CONDITION_VERSION_V2:
                # Nested group: evaluate recursively.
                child_result = await self._evaluate_subgroup(
                    child, state, quote, alert=alert)
                child_results.append(child_result)
                # Persist subgroup state under synthetic ID.
                sg_state = self._get_subgroup_state(state, cid)
                # No direct state updates for subgroups — they manage their
                # own internal state via recursive calls.
            else:
                # Leaf node.
                metric = child["metric"]
                operator = child["operator"]
                threshold = child["value"]
                leaf_dep = child.get("_dependency_key")
                quote_dep = f"quote:{self._resolver.resolve_quote(quote) or ''}"
                if (METRIC_SOURCE.get(metric) == "analytics"
                        and self._analytics is not None):
                    value = self._extract_analytics_value(alert, child, metric)
                    if value is not None:
                        self._analytics_seen.add((alert["alert_id"], cid))
                        # Track last analytics value so crossing leaves can
                        # distinguish "same stale value" from "new tick".
                        self._dep_last_values[(alert["alert_id"], cid)] = value
                else:
                    # B7: only use quote value if it matches this leaf's dep.
                    if leaf_dep and leaf_dep == quote_dep:
                        value = extract_metric(quote, metric)
                        # Store last-known for this specific leaf.
                        self._dep_last_values[(alert["alert_id"], cid)] = value
                    else:
                        # Use stored last-known value (may be None → UNKNOWN).
                        value = self._dep_last_values.get(
                            (alert["alert_id"], cid))
                prev_leaf_state = state["leaves"].get(
                    cid,
                    {"last_result": LAST_RESULT_UNKNOWN,
                     "crossing_side": CROSSING_UNKNOWN})
                is_new_tick = (leaf_dep is not None
                               and leaf_dep == quote_dep)
                new_leaf_state = await self._evaluate_leaf_node(
                    operator, threshold, value, prev_leaf_state,
                    analytics_seen=(METRIC_SOURCE.get(metric) == "analytics"
                                    and (alert["alert_id"], cid)
                                    in self._analytics_seen),
                    prev_value=self._dep_last_values.get(
                        (alert["alert_id"], cid)),
                    is_new_tick=is_new_tick)
                changed = (new_leaf_state["last_result"] != prev_leaf_state["last_result"]
                           or new_leaf_state["crossing_side"] != prev_leaf_state["crossing_side"])
                if changed:
                    leaf_updates[cid] = new_leaf_state
                child_results.append(new_leaf_state["last_result"])

        # Compute root result from child results using Kleene logic.
        logic = condition["logic"]
        if logic == "all":
            new_root_result = _all_result(child_results)
        else:
            new_root_result = _any_result(child_results)

        prev_root = state["root"]
        new_root_state = {
            "last_result": new_root_result,
            "crossing_side": CROSSING_UNKNOWN,
        }

        # Collect all state to persist (changed leaves + root).
        all_states: dict[str, dict[str, str]] = dict(state["leaves"])
        all_states.update(leaf_updates)
        all_states[root_condition_id] = new_root_state

        fire = self._check_root_fire(prev_root, new_root_state)
        has_changes = bool(leaf_updates) or (
            new_root_result != prev_root["last_result"])

        if fire:
            result = await self._trigger(
                alert, quote, None, previous_value,
                all_states, new_root_state)
            if result is not None:
                with self._lock:
                    self._state[alert["alert_id"]]["leaves"] = all_states
                    del self._state[alert["alert_id"]]["leaves"][root_condition_id]
                    self._state[alert["alert_id"]]["root"] = new_root_state
            return result
        if has_changes:
            if await self._save_states(alert, all_states):
                with self._lock:
                    self._state[alert["alert_id"]]["leaves"] = all_states
                    del self._state[alert["alert_id"]]["leaves"][root_condition_id]
                    self._state[alert["alert_id"]]["root"] = new_root_state
        return None

    @staticmethod
    def _resolve_child_id(child: dict[str, Any]) -> str:
        """Return the condition_id for a leaf, or a synthetic ID for a group."""
        if child.get("condition_version") == CONDITION_VERSION_V2:
            # Synthetic ID for nested groups based on their structure.
            key = hashlib.sha256(
                str(child).encode()).hexdigest()[:16]
            return f"group-{key}"
        return child["condition_id"]

    def _get_subgroup_state(
        self, state: dict[str, Any], cid: str
    ) -> dict[str, str]:
        """Get or initialize subgroup state from the leaves dict."""
        leaves = state["leaves"]
        if cid not in leaves:
            leaves[cid] = {
                "last_result": LAST_RESULT_UNKNOWN,
                "crossing_side": CROSSING_UNKNOWN,
            }
        return leaves[cid]

    def _extract_analytics_value(
        self, alert: dict[str, Any], leaf: dict[str, Any], metric: str
    ) -> float | None:
        """Extract an analytics metric value from the cached snapshot.

        Returns None (UNKNOWN) when the snapshot is missing or stale.
        """
        if self._analytics is None:
            return None
        # Determine chain key from the specific leaf.
        chain_key = self._chain_key_from_leaf(leaf)
        if chain_key is None:
            return None
        snap = self._analytics.get_snapshot(chain_key)
        if snap is None:
            return None
        try:
            return extract_analytics_metric(snap, metric)
        except KeyError:
            return None

    @staticmethod
    def _chain_key_from_leaf(leaf: dict[str, Any]) -> str | None:
        """Extract chain key from a single leaf condition."""
        dep = leaf.get("_dependency_key")
        if dep:
            return dep
        inst = leaf.get("instrument", {})
        cid = inst.get("canonical_id")
        expiry = inst.get("expiry")
        if cid and expiry:
            return f"analytics:{cid}:{expiry}"
        return None

    async def _evaluate_subgroup(
        self,
        child: dict[str, Any],
        state: dict[str, Any],
        quote: Any,
        alert: dict[str, Any] | None = None,
    ) -> str:
        """Recursively evaluate a nested group, returning its root result."""
        logic = child["logic"]
        results: list[str] = []
        for sub_child in child["conditions"]:
            sub_cid = self._resolve_child_id(sub_child)
            if sub_child.get("condition_version") == CONDITION_VERSION_V2:
                # Deeper nested group.
                result = await self._evaluate_subgroup(
                    sub_child, state, quote, alert=alert)
                results.append(result)
            else:
                # Leaf.
                metric = sub_child["metric"]
                operator = sub_child["operator"]
                threshold = sub_child["value"]
                sub_leaf_dep = sub_child.get("_dependency_key")
                sub_quote_dep = f"quote:{self._resolver.resolve_quote(quote) or ''}"
                if (METRIC_SOURCE.get(metric) == "analytics"
                        and self._analytics is not None
                        and alert is not None):
                    value = self._extract_analytics_value(alert, sub_child, metric)
                    if value is not None:
                        self._analytics_seen.add((alert["alert_id"], sub_cid))
                else:
                    # B7: only use quote value if it matches this leaf's dep.
                    if sub_leaf_dep and sub_leaf_dep == sub_quote_dep:
                        value = extract_metric(quote, metric)
                        self._dep_last_values[(alert["alert_id"], sub_cid)] = value
                    else:
                        value = self._dep_last_values.get(
                            (alert["alert_id"], sub_cid))
                prev = self._get_subgroup_state(state, sub_cid)
                sub_is_new_tick = (sub_leaf_dep is not None
                                   and sub_leaf_dep == sub_quote_dep)
                new_state = await self._evaluate_leaf_node(
                    operator, threshold, value, prev,
                    analytics_seen=(METRIC_SOURCE.get(metric) == "analytics"
                                    and (alert["alert_id"], sub_cid)
                                    in self._analytics_seen),
                    prev_value=self._dep_last_values.get(
                        (alert["alert_id"], sub_cid)),
                    is_new_tick=sub_is_new_tick)
                changed = (new_state["last_result"] != prev["last_result"]
                           or new_state["crossing_side"] != prev["crossing_side"])
                if changed:
                    state["leaves"][sub_cid] = new_state
                results.append(new_state["last_result"])

        if logic == "all":
            return _all_result(results)
        else:
            return _any_result(results)

    @staticmethod
    async def _evaluate_leaf_node(
        operator: str, threshold: float,
        value: float | None,
        prev_state: dict[str, str],
        analytics_seen: bool = False,
        prev_value: float | None = None,
        is_new_tick: bool = True,
    ) -> dict[str, str]:
        """Evaluate a single leaf node, returning new state.

        is_new_tick must be True when the current evaluation is driven by a
        quote/analytics update relevant to this leaf (i.e. the leaf's dep
        matches the incoming data source).  False means this is a stale
        re-evaluation caused by a *different* leaf's dep matching the
        incoming tick — in that case crossing leaves must reset to avoid
        phantom fires across instruments.
        """
        if operator in CROSSING_OPERATORS:
            new_side = prev_state["crossing_side"]
            if value is not None:
                new_side = (CROSSING_ABOVE if value > threshold
                            else CROSSING_BELOW_OR_EQUAL)
            # Crossing result is TRUE only on the crossing observation.
            fire = False
            if value is not None:
                prev_side = prev_state["crossing_side"]
                if operator == "crosses_above":
                    fire = (prev_side == CROSSING_BELOW_OR_EQUAL
                            and new_side == CROSSING_ABOVE)
                else:
                    fire = (prev_side == CROSSING_ABOVE
                            and new_side == CROSSING_BELOW_OR_EQUAL)
            if fire:
                leaf_result = LAST_RESULT_TRUE
            elif prev_value is not None and value == prev_value:
                # Same stale value — preserve state for re-arm support.
                leaf_result = prev_state["last_result"]
            else:
                # New value or first evaluation — preserve state.
                leaf_result = prev_state["last_result"]
            return {"last_result": leaf_result, "crossing_side": new_side}
        else:
            new_result = prev_state["last_result"]
            if value is not None:
                new_result = _compare(operator, value, threshold)
            elif analytics_seen:
                # Analytics snapshot disappeared after having a value → UNKNOWN.
                new_result = LAST_RESULT_UNKNOWN
            return {"last_result": new_result,
                    "crossing_side": prev_state["crossing_side"]}

    @staticmethod
    def _check_root_fire(
        prev_root: dict[str, str], new_root: dict[str, str]
    ) -> bool:
        """Check if root transition qualifies for a trigger."""
        prev = prev_root["last_result"]
        new = new_root["last_result"]
        if prev == LAST_RESULT_UNKNOWN and new == LAST_RESULT_TRUE:
            return True
        if prev == LAST_RESULT_FALSE and new == LAST_RESULT_TRUE:
            return True
        return False

    # ── Trigger ─────────────────────────────────────────────────────────────

    async def _trigger(
        self,
        alert: dict[str, Any],
        quote: Any,
        value: float | None,
        previous_value: float | None,
        state_updates: dict[str, dict[str, str]],
        root_state: dict[str, str],
    ) -> dict[str, Any] | None:
        alert_id = alert["alert_id"]
        consumer_id = alert["consumer_id"]
        condition = alert["_condition"]
        trigger_mode = alert["trigger_mode"]
        one_shot = trigger_mode == "once"
        enabled = not one_shot

        trigger_count = int(alert.get("trigger_count") or 0) + 1
        last_triggered_at = datetime.now(timezone.utc).isoformat()

        # Build observed payload.
        observed: list[dict[str, Any]] = []
        if condition.get("condition_version") == CONDITION_VERSION_V1:
            observed.append({
                "condition_id": condition["condition_id"],
                "metric": condition["metric"],
                "operator": condition["operator"],
                "expected": condition["value"],
                "value": value,
                "previous_value": previous_value,
            })
        else:
            for child in condition["conditions"]:
                if child.get("condition_version") == CONDITION_VERSION_V2:
                    # Nested group — include its structure in observed.
                    observed.append({
                        "condition_id": self._resolve_child_id(child),
                        "metric": None,
                        "operator": None,
                        "expected": None,
                        "value": None,
                        "previous_value": None,
                        "group_logic": child.get("logic"),
                        "group_conditions_count": len(child.get("conditions", [])),
                    })
                else:
                    # B7: analytics metrics cannot be extracted from quote.
                    leaf_metric = child["metric"]
                    leaf_dep = child.get("_dependency_key", "")
                    is_analytics = METRIC_SOURCE.get(leaf_metric) == "analytics"
                    if is_analytics and self._analytics is not None:
                        leaf_value = self._extract_analytics_value(
                            alert, child, leaf_metric)
                    elif leaf_dep and leaf_dep.startswith("quote:"):
                        leaf_value = extract_metric(quote, leaf_metric)
                    else:
                        leaf_value = None
                    observed.append({
                        "condition_id": child["condition_id"],
                        "metric": leaf_metric,
                        "operator": child["operator"],
                        "expected": child["value"],
                        "value": leaf_value,
                        "previous_value": None,
                    })

        data = build_alert_triggered_data(
            alert_family=CONDITION_ALERT_FAMILY,
            alert_id=alert_id,
            consumer_id=consumer_id,
            condition={
                "condition_version": condition.get("condition_version", 1),
                "logic": condition.get("logic"),
                "conditions": condition.get("conditions", [condition]),
            },
            observed={
                "root_result": root_state["last_result"],
                "leaves": observed,
            },
            instrument=self._instrument_payload(alert, quote),
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

        try:
            sequence = await asyncio.to_thread(
                self._store.save_condition_trigger,
                alert_id=alert_id,
                consumer_id=consumer_id,
                event_id=event_id,
                event_type=event["type"],
                source=event["source"],
                timestamp=timestamp,
                data=data,
                routing=routing,
                enabled=enabled,
                trigger_count=trigger_count,
                last_triggered_at=last_triggered_at,
                state_updates=state_updates,
            )
        except Exception as exc:
            logger.warning("condition trigger persist failed: %s",
                           type(exc).__name__)
            return None
        event["sequence"] = sequence

        with self._lock:
            alert["trigger_count"] = trigger_count
            alert["last_triggered_at"] = last_triggered_at
            if one_shot:
                alert["enabled"] = False
                # B7: remove from all dependency sets.
                for dk in self._alert_deps.get(alert_id, ()):
                    self._dep_index.get(dk, set()).discard(alert_id)
                self._alert_deps.pop(alert_id, None)

        try:
            await events.finalize_persisted_event(event, self._store, self._bus)
        except Exception as exc:
            logger.warning("condition trigger finalize failed: %s",
                           type(exc).__name__)

        notification = {
            "alert_id": alert_id,
            "consumer_id": consumer_id,
            "trigger_mode": trigger_mode,
            "event_id": event_id,
            "sequence": sequence,
            "ts": time.time(),
            "root_result": root_state["last_result"],
        }
        # Enrich with per-leaf diagnostics for backward compat.
        if condition.get("condition_version") == CONDITION_VERSION_V1:
            notification.update({
                "condition_id": condition["condition_id"],
                "metric": condition["metric"],
                "operator": condition["operator"],
                "threshold": condition["value"],
                "value": value,
                "previous_value": previous_value,
            })
        self.add_notification(notification)
        return notification

    async def _save_states(
        self,
        alert: dict[str, Any],
        states: dict[str, dict[str, str]],
    ) -> bool:
        """Persist state changes (non-trigger). Returns True on success."""
        try:
            await asyncio.to_thread(
                self._store.save_condition_runtime_states,
                alert_id=alert["alert_id"],
                states=states,
            )
            return True
        except Exception as exc:
            logger.warning("condition state persist failed: %s",
                           type(exc).__name__)
            return False

    def _instrument_payload(
        self, alert: dict[str, Any], quote: Any
    ) -> dict[str, Any]:
        condition = alert["_condition"]
        # Extract primary canonical_id for context lookup (first leaf).
        cid = self._first_canonical_id(condition)
        context = self._resolver.context_for(cid or "")
        payload: dict[str, Any] = {
            "canonical_id": cid,
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
            del self._notifications[50:]

    def recent_notifications(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._notifications[:limit])

    def clear_notification(self, alert_id: str) -> None:
        with self._lock:
            self._notifications = [n for n in self._notifications
                                   if n.get("alert_id") != alert_id]
