"""
Condition alert persistence (schema v13) — advanced market_condition alerts.

Tables:
    condition_alerts          definition rows (consumer-owned)
    condition_runtime_state   per-node restart-safe evaluation state

Runtime state is keyed by condition_id:
    - For v1 (single leaf): one row keyed by the leaf's condition_id
    - For v2 (group): one row per leaf + one synthetic "root-{alert_id}" row

The atomic trigger transaction (``save_condition_trigger``) persists runtime
state + alert row + the canonical ``alert.triggered`` event + consumer
materialization in ONE ``BEGIN IMMEDIATE ... COMMIT``. A lost trigger is
forbidden.

B4 adds condition_version=2 support with structured ALL/ANY groups.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from core.errors import ConditionValidationError, ConsumerNotFoundError
from market.condition_metrics import METRIC_SET

logger = logging.getLogger(__name__)

CONDITION_VERSION_V1 = 1
CONDITION_VERSION_V2 = 2

# Max nesting depth and max leaf count for v2 groups.
MAX_CONDITION_DEPTH = 8
MAX_CONDITION_LEAVES = 64

VALID_OPERATORS = frozenset({
    "eq", "ne", "gt", "gte", "lt", "lte",
    "crosses_above", "crosses_below",
})
CROSSING_OPERATORS = frozenset({"crosses_above", "crosses_below"})
LEVEL_OPERATORS = VALID_OPERATORS - CROSSING_OPERATORS

VALID_TRIGGER_MODES = frozenset({"once", "repeat"})
VALID_LOGIC_OPERATORS = frozenset({"all", "any"})

# Runtime state values.
LAST_RESULT_UNKNOWN = "unknown"
LAST_RESULT_FALSE = "false"
LAST_RESULT_TRUE = "true"
CROSSING_UNKNOWN = "unknown"
CROSSING_BELOW_OR_EQUAL = "below_or_equal"
CROSSING_ABOVE = "above"

# Synthetic condition_id prefix for root group state.
ROOT_CONDITION_ID_PREFIX = "root-"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema (v13) — unchanged, v2 reuses existing tables
# ---------------------------------------------------------------------------


def create_condition_alert_tables(conn: sqlite3.Connection) -> None:
    """Create the condition_alerts + condition_runtime_state tables (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS condition_alerts (
            alert_id                TEXT PRIMARY KEY,
            consumer_id             TEXT NOT NULL,
            name                    TEXT,
            enabled                 INTEGER NOT NULL DEFAULT 1,
            trigger_mode            TEXT NOT NULL,
            condition_json          TEXT NOT NULL,
            canonical_instrument_id TEXT NOT NULL,
            metadata_json           TEXT,
            created_at              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            last_triggered_at       TEXT,
            trigger_count           INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (consumer_id) REFERENCES consumers(consumer_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_condition_alerts_consumer_enabled
        ON condition_alerts(consumer_id, enabled)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_condition_alerts_canonical_enabled
        ON condition_alerts(canonical_instrument_id, enabled)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS condition_runtime_state (
            condition_id  TEXT PRIMARY KEY,
            alert_id      TEXT NOT NULL,
            last_result   TEXT NOT NULL DEFAULT 'unknown',
            crossing_side TEXT NOT NULL DEFAULT 'unknown',
            updated_at    TEXT NOT NULL,
            FOREIGN KEY (alert_id) REFERENCES condition_alerts(alert_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_condition_runtime_alert
        ON condition_runtime_state(alert_id)
    """)


def migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """Add condition_alerts + condition_runtime_state tables."""
    create_condition_alert_tables(conn)
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    logger.info("migrated v12→v13: added condition_alerts/condition_runtime_state")


# ---------------------------------------------------------------------------
# Condition tree validation (v1 leaf + v2 groups)
# ---------------------------------------------------------------------------


def _validate_leaf(
    node: Any, *, depth: int, leaf_count: list[int],
    expected_canonical_id: str | None,
) -> dict[str, Any]:
    """Validate a single leaf node. Returns normalized leaf dict."""
    if not isinstance(node, dict):
        raise ConditionValidationError("leaf must be a JSON object")
    if "logic" in node or "conditions" in node:
        raise ConditionValidationError(
            "leaf must not contain logic/conditions (use a group node)")
    version = node.get("condition_version")
    if version is not None and version != CONDITION_VERSION_V1:
        raise ConditionValidationError(
            f"leaf condition_version must be 1 (got {version!r})")
    condition_id = node.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id.strip():
        raise ConditionValidationError(
            "condition_id must be a non-empty string")
    metric = node.get("metric")
    if metric not in METRIC_SET:
        raise ConditionValidationError(f"unknown metric: {metric!r}")
    operator = node.get("operator")
    if operator not in VALID_OPERATORS:
        raise ConditionValidationError(f"unknown operator: {operator!r}")
    value = node.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionValidationError(
            "value must be numeric (bool is not numeric)")
    instrument = node.get("instrument")
    if not isinstance(instrument, dict):
        raise ConditionValidationError("instrument must be an object")
    canonical_id = instrument.get("canonical_id")
    if not isinstance(canonical_id, str) or not canonical_id.strip():
        raise ConditionValidationError(
            "instrument.canonical_id must be a non-empty string")
    if expected_canonical_id is not None and canonical_id != expected_canonical_id:
        raise ConditionValidationError(
            f"same-instrument required: expected {expected_canonical_id!r}, "
            f"got {canonical_id!r}")
    leaf_count[0] += 1
    if leaf_count[0] > MAX_CONDITION_LEAVES:
        raise ConditionValidationError(
            f"too many leaves (max {MAX_CONDITION_LEAVES})")
    return {
        "condition_version": CONDITION_VERSION_V1,
        "condition_id": condition_id,
        "metric": metric,
        "operator": operator,
        "value": value,
        "instrument": {"canonical_id": canonical_id},
    }


def _get_canonical_id(node: dict[str, Any]) -> str | None:
    """Extract canonical_id from a normalized condition node (leaf or group)."""
    if node.get("condition_version") == CONDITION_VERSION_V1:
        return node.get("instrument", {}).get("canonical_id")
    # Group: recurse into first child.
    children = node.get("conditions", [])
    if children:
        return _get_canonical_id(children[0])
    return None


def validate_condition_tree(
    condition_json: Any,
    *,
    depth: int = 0,
    leaf_count: list[int] | None = None,
    expected_canonical_id: str | None = None,
) -> dict[str, Any]:
    """Validate a condition definition (v1 leaf or v2 group tree).

    Returns the normalized tree dict. Raises ConditionValidationError on
    any violation.
    """
    if leaf_count is None:
        leaf_count = [0]
    if not isinstance(condition_json, dict):
        raise ConditionValidationError("condition must be a JSON object")

    version = condition_json.get("condition_version")
    if version is None:
        # Implicit v1 leaf inside a v2 group.
        version = CONDITION_VERSION_V1
    if version == CONDITION_VERSION_V1:
        # v1 leaf — delegate to legacy validator (without group rejection)
        return _validate_leaf(
            condition_json, depth=depth, leaf_count=leaf_count,
            expected_canonical_id=expected_canonical_id)
    elif version == CONDITION_VERSION_V2:
        if depth >= MAX_CONDITION_DEPTH:
            raise ConditionValidationError(
                f"max condition depth ({MAX_CONDITION_DEPTH}) exceeded")
        logic = condition_json.get("logic")
        if logic not in VALID_LOGIC_OPERATORS:
            raise ConditionValidationError(
                f"invalid logic: {logic!r} (expected 'all' or 'any')")
        conditions = condition_json.get("conditions")
        if not isinstance(conditions, list) or len(conditions) == 0:
            raise ConditionValidationError(
                "conditions must be a non-empty array")
        if len(conditions) > MAX_CONDITION_LEAVES:
            raise ConditionValidationError(
                f"too many children ({len(conditions)}), max {MAX_CONDITION_LEAVES}")
        normalized_conditions = []
        expected_canonical_id = None
        for child in conditions:
            nv = validate_condition_tree(
                child, depth=depth + 1, leaf_count=leaf_count,
                expected_canonical_id=expected_canonical_id)
            # Derive expected_canonical_id from the first child.
            if expected_canonical_id is None:
                expected_canonical_id = _get_canonical_id(nv)
            # Enforce same-instrument across siblings.
            child_canonical = _get_canonical_id(nv)
            if child_canonical != expected_canonical_id:
                raise ConditionValidationError(
                    f"same-instrument required within group: "
                    f"{expected_canonical_id!r} != {child_canonical!r}")
            normalized_conditions.append(nv)
        return {
            "condition_version": CONDITION_VERSION_V2,
            "logic": logic,
            "conditions": normalized_conditions,
        }
    else:
        raise ConditionValidationError(
            f"unsupported condition_version: {version!r}")


# ---------------------------------------------------------------------------
# Definition CRUD (internal APIs)
# ---------------------------------------------------------------------------


def create_condition_alert(
    conn: sqlite3.Connection,
    *,
    consumer_id: str,
    name: str | None,
    trigger_mode: str,
    condition_json: dict[str, Any],
    metadata: dict[str, Any] | None,
) -> str:
    """Create a condition alert; returns the new alert_id (UUID hex)."""
    exist = conn.execute(
        "SELECT 1 FROM consumers WHERE consumer_id = ?", (consumer_id,)
    ).fetchone()
    if not exist:
        raise ConsumerNotFoundError(consumer_id)
    if trigger_mode not in VALID_TRIGGER_MODES:
        raise ConditionValidationError(
            f"invalid trigger_mode: {trigger_mode!r}")
    condition = validate_condition_tree(condition_json)
    alert_id = uuid.uuid4().hex
    now = _now()
    # Extract canonical_instrument_id from the tree.
    canonical_id = _get_canonical_id(condition)
    if canonical_id is None:
        raise ConditionValidationError(
            "invalid market_condition: no instrument found in condition tree")
    conn.execute(
        "INSERT INTO condition_alerts "
        "(alert_id, consumer_id, name, enabled, trigger_mode, condition_json, "
        " canonical_instrument_id, metadata_json, created_at, updated_at, "
        " last_triggered_at, trigger_count) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, NULL, 0)",
        (alert_id, consumer_id, name, trigger_mode,
         json.dumps(condition, ensure_ascii=False),
         canonical_id,
         json.dumps(metadata or {}, ensure_ascii=False),
         now, now),
    )
    conn.commit()
    return alert_id


def _row_to_alert(row: sqlite3.Row) -> dict[str, Any]:
    alert = dict(row)
    alert["enabled"] = bool(alert["enabled"])
    alert["trigger_count"] = int(alert["trigger_count"] or 0)
    try:
        alert["condition"] = json.loads(alert["condition_json"])
    except (TypeError, ValueError):
        alert["condition"] = None
    try:
        alert["metadata"] = json.loads(alert["metadata_json"] or "{}")
    except (TypeError, ValueError):
        alert["metadata"] = {}
    return alert


def list_condition_alerts(
    conn: sqlite3.Connection, consumer_id: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM condition_alerts"
    args: list[Any] = []
    if consumer_id is not None:
        sql += " WHERE consumer_id = ?"
        args.append(consumer_id)
    sql += " ORDER BY created_at"
    conn.row_factory = sqlite3.Row
    try:
        return [_row_to_alert(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def get_condition_alert(
    conn: sqlite3.Connection, alert_id: str
) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM condition_alerts WHERE alert_id = ?",
            (alert_id,)).fetchone()
        return _row_to_alert(row) if row else None
    finally:
        conn.row_factory = None


def set_condition_alert_enabled(
    conn: sqlite3.Connection, alert_id: str, enabled: bool
) -> None:
    cur = conn.execute(
        "UPDATE condition_alerts SET enabled = ?, updated_at = ? "
        "WHERE alert_id = ?",
        (1 if enabled else 0, _now(), alert_id))
    conn.commit()
    if cur.rowcount == 0:
        from core.errors import AlertNotFoundError
        raise AlertNotFoundError(alert_id)


def delete_condition_alert(conn: sqlite3.Connection, alert_id: str) -> None:
    conn.execute("DELETE FROM condition_runtime_state WHERE alert_id = ?",
                 (alert_id,))
    cur = conn.execute(
        "DELETE FROM condition_alerts WHERE alert_id = ?", (alert_id,))
    conn.commit()
    if cur.rowcount == 0:
        from core.errors import AlertNotFoundError
        raise AlertNotFoundError(alert_id)


def load_enabled_condition_alerts(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [_row_to_alert(r) for r in conn.execute(
            "SELECT * FROM condition_alerts WHERE enabled = 1 "
            "ORDER BY created_at")]
    finally:
        conn.row_factory = None


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------


def load_condition_runtime_state(
    conn: sqlite3.Connection,
) -> dict[str, dict[str, dict[str, str]]]:
    """Return runtime state keyed by alert_id → condition_id → state.

    Each alert maps to a dict of {condition_id: {last_result, crossing_side}}.
    The root state uses condition_id ``root-{alert_id}``.
    """
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT alert_id, condition_id, last_result, crossing_side "
            "FROM condition_runtime_state").fetchall()
        result: dict[str, dict[str, dict[str, str]]] = {}
        for r in rows:
            aid = r["alert_id"]
            if aid not in result:
                result[aid] = {}
            result[aid][r["condition_id"]] = {
                "last_result": r["last_result"],
                "crossing_side": r["crossing_side"],
            }
        return result
    finally:
        conn.row_factory = None


def save_condition_runtime_states(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    states: dict[str, dict[str, str]],
    updated_at: str,
) -> None:
    """Batch upsert runtime state for multiple condition_ids of one alert."""
    if not states:
        return
    conn.execute("BEGIN IMMEDIATE")
    for condition_id, state in states.items():
        conn.execute(
            "INSERT INTO condition_runtime_state "
            "(condition_id, alert_id, last_result, crossing_side, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(condition_id) DO UPDATE SET "
            "alert_id=excluded.alert_id, "
            "last_result=excluded.last_result, "
            "crossing_side=excluded.crossing_side, "
            "updated_at=excluded.updated_at",
            (condition_id, alert_id,
             state["last_result"], state["crossing_side"], updated_at))
    conn.commit()


def save_condition_runtime_state(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    condition_id: str,
    last_result: str,
    crossing_side: str,
    updated_at: str,
) -> None:
    """Upsert runtime state for one condition (backward-compat wrapper)."""
    save_condition_runtime_states(
        conn, alert_id=alert_id,
        states={condition_id: {"last_result": last_result,
                               "crossing_side": crossing_side}},
        updated_at=updated_at,
    )


# ---------------------------------------------------------------------------
# Atomic trigger transaction (B2 §38/§46, extended for v2 groups)
# ---------------------------------------------------------------------------


def save_condition_trigger(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    consumer_id: str,
    event_id: str,
    event_type: str,
    source: str,
    timestamp: str,
    data: dict[str, Any],
    routing: dict[str, Any] | None,
    enabled: bool,
    trigger_count: int,
    last_triggered_at: str,
    state_updates: dict[str, dict[str, str]],
    materialize_fn,
) -> int:
    """Atomically persist a condition trigger in ONE transaction.

    Order inside the single ``BEGIN IMMEDIATE ... COMMIT``:
      1. condition_runtime_state updates (batch: leaves + root)
      2. condition_alerts update (enabled if once, trigger_count,
         last_triggered_at, updated_at)
      3. persistent ``alert.triggered`` INSERT
      4. consumer materialization (routing targets)

    Any failure rolls back everything — a lost trigger is forbidden.
    Returns the assigned persistent-event sequence number.
    """
    data_json = json.dumps(data, ensure_ascii=False, allow_nan=False)
    routing_json = (
        json.dumps(routing, ensure_ascii=False, allow_nan=False)
        if routing is not None else None
    )

    conn.execute("BEGIN IMMEDIATE")
    # 1. Runtime state (batch upsert).
    for condition_id, state in state_updates.items():
        conn.execute(
            "INSERT INTO condition_runtime_state "
            "(condition_id, alert_id, last_result, crossing_side, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(condition_id) DO UPDATE SET "
            "alert_id=excluded.alert_id, "
            "last_result=excluded.last_result, "
            "crossing_side=excluded.crossing_side, "
            "updated_at=excluded.updated_at",
            (condition_id, alert_id,
             state["last_result"], state["crossing_side"],
             last_triggered_at))
    # 2. Alert row.
    conn.execute(
        "UPDATE condition_alerts SET "
        "enabled = ?, trigger_count = ?, last_triggered_at = ?, "
        "updated_at = ? WHERE alert_id = ?",
        (1 if enabled else 0, trigger_count, last_triggered_at,
         last_triggered_at, alert_id))
    # 3. Persistent event.
    conn.execute(
        "INSERT INTO persistent_events "
        "(id, type, source, timestamp, data, routing, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, event_type, source, timestamp, data_json, routing_json,
         timestamp))
    seq = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    # 4. Consumer materialization.
    materialize_fn(conn, event_id, seq, routing)
    conn.commit()
    return seq
