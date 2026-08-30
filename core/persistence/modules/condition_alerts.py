"""
Condition alert persistence (schema v13) — advanced market_condition alerts.

Two new tables, added by the v12→v13 migration:

    condition_alerts          definition rows (consumer-owned, one condition each)
    condition_runtime_state   minimal restart-safe evaluation state

The runtime state is deliberately minimal (B2 frozen decision):
    last_result    unknown | false | true          (LEVEL operators)
    crossing_side  unknown | below_or_equal | above (CROSSING operators)

``armed`` is NOT persisted: ONCE disables via ``enabled=0`` after a trigger;
REPEAT LEVEL re-arms via ``last_result``; CROSSING re-arms via the persisted
side. ``previous_value`` is NOT persisted for correctness — the side of the
threshold is sufficient because condition definitions are immutable in B2.

The atomic trigger transaction (``save_condition_trigger``) persists runtime
state + alert row + the canonical ``alert.triggered`` event + consumer
materialization in ONE ``BEGIN IMMEDIATE ... COMMIT``. A lost trigger is
forbidden: any failure rolls back everything.
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

CONDITION_VERSION = 1

VALID_OPERATORS = frozenset({
    "eq", "ne", "gt", "gte", "lt", "lte",
    "crosses_above", "crosses_below",
})
CROSSING_OPERATORS = frozenset({"crosses_above", "crosses_below"})
LEVEL_OPERATORS = VALID_OPERATORS - CROSSING_OPERATORS

VALID_TRIGGER_MODES = frozenset({"once", "repeat"})

# Runtime state values.
LAST_RESULT_UNKNOWN = "unknown"
LAST_RESULT_FALSE = "false"
LAST_RESULT_TRUE = "true"
CROSSING_UNKNOWN = "unknown"
CROSSING_BELOW_OR_EQUAL = "below_or_equal"
CROSSING_ABOVE = "above"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema (v13)
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
# Condition JSON validation (strict, deterministic)
# ---------------------------------------------------------------------------


def validate_condition_json(condition_json: Any) -> dict[str, Any]:
    """Validate a condition definition; returns the normalized condition dict.

    Rejects: ``condition_version != 1``, unknown metric/operator, non-numeric
    (or bool) threshold, missing canonical_id, and any AND/OR group payload
    (``logic`` / ``conditions`` / nested groups are not supported in B2).
    """
    if not isinstance(condition_json, dict):
        raise ConditionValidationError("condition must be a JSON object")
    if "logic" in condition_json or "conditions" in condition_json:
        raise ConditionValidationError(
            "AND/OR groups are not supported in B2 (single condition only)")
    version = condition_json.get("condition_version")
    if version != CONDITION_VERSION:
        raise ConditionValidationError(
            f"unsupported condition_version: {version!r}")
    condition_id = condition_json.get("condition_id")
    if not isinstance(condition_id, str) or not condition_id.strip():
        raise ConditionValidationError(
            "condition_id must be a non-empty string")
    metric = condition_json.get("metric")
    if metric not in METRIC_SET:
        raise ConditionValidationError(f"unknown metric: {metric!r}")
    operator = condition_json.get("operator")
    if operator not in VALID_OPERATORS:
        raise ConditionValidationError(f"unknown operator: {operator!r}")
    value = condition_json.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionValidationError(
            "value must be numeric (bool is not numeric)")
    instrument = condition_json.get("instrument")
    if not isinstance(instrument, dict):
        raise ConditionValidationError("instrument must be an object")
    canonical_id = instrument.get("canonical_id")
    if not isinstance(canonical_id, str) or not canonical_id.strip():
        raise ConditionValidationError(
            "instrument.canonical_id must be a non-empty string")
    return {
        "condition_version": CONDITION_VERSION,
        "condition_id": condition_id,
        "metric": metric,
        "operator": operator,
        "value": value,
        "instrument": {"canonical_id": canonical_id},
    }


# ---------------------------------------------------------------------------
# Definition CRUD (internal APIs — not public MCP tools in B2)
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
    condition = validate_condition_json(condition_json)
    alert_id = uuid.uuid4().hex
    now = _now()
    conn.execute(
        "INSERT INTO condition_alerts "
        "(alert_id, consumer_id, name, enabled, trigger_mode, condition_json, "
        " canonical_instrument_id, metadata_json, created_at, updated_at, "
        " last_triggered_at, trigger_count) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, NULL, 0)",
        (alert_id, consumer_id, name, trigger_mode,
         json.dumps(condition, ensure_ascii=False),
         condition["instrument"]["canonical_id"],
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
) -> dict[str, dict[str, str]]:
    """Return runtime state keyed by alert_id."""
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT alert_id, condition_id, last_result, crossing_side "
            "FROM condition_runtime_state").fetchall()
        return {
            r["alert_id"]: {
                "condition_id": r["condition_id"],
                "last_result": r["last_result"],
                "crossing_side": r["crossing_side"],
            }
            for r in rows
        }
    finally:
        conn.row_factory = None


def save_condition_runtime_state(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    condition_id: str,
    last_result: str,
    crossing_side: str,
    updated_at: str,
) -> None:
    """Upsert runtime state for one condition (standalone state write)."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO condition_runtime_state "
        "(condition_id, alert_id, last_result, crossing_side, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(condition_id) DO UPDATE SET "
        "alert_id=excluded.alert_id, "
        "last_result=excluded.last_result, "
        "crossing_side=excluded.crossing_side, "
        "updated_at=excluded.updated_at",
        (condition_id, alert_id, last_result, crossing_side, updated_at))
    conn.commit()


# ---------------------------------------------------------------------------
# Atomic trigger transaction (B2 §38/§46)
# ---------------------------------------------------------------------------


def save_condition_trigger(
    conn: sqlite3.Connection,
    *,
    alert_id: str,
    condition_id: str,
    consumer_id: str,
    event_id: str,
    event_type: str,
    source: str,
    timestamp: str,
    data: dict[str, Any],
    routing: dict[str, Any] | None,
    last_result: str,
    crossing_side: str,
    enabled: bool,
    trigger_count: int,
    last_triggered_at: str,
    materialize_fn,
) -> int:
    """Atomically persist a condition trigger in ONE transaction.

    Order inside the single ``BEGIN IMMEDIATE ... COMMIT``:
      1. condition_runtime_state update
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
    # 1. Runtime state.
    conn.execute(
        "INSERT INTO condition_runtime_state "
        "(condition_id, alert_id, last_result, crossing_side, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(condition_id) DO UPDATE SET "
        "alert_id=excluded.alert_id, "
        "last_result=excluded.last_result, "
        "crossing_side=excluded.crossing_side, "
        "updated_at=excluded.updated_at",
        (condition_id, alert_id, last_result, crossing_side,
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