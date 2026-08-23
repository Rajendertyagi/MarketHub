"""
Consumer-safe retention pruning for persistent_events.

Principle:
    retention limits define what MAY be pruned;
    consumer replay safety defines what is SAFE to prune.

An event is REQUIRED by a consumer — and therefore preserved — when a
consumer_event_state row exists for it with ``acknowledged_at IS NULL``
and the event's sequence is above that consumer's replay floor. The floor
is the consumer's checkpoint; a MISSING checkpoint row means floor 0,
which matches ``replay_events``' fallback semantics exactly (a consumer
without a checkpoint replays from sequence 1 upward).

Dependent consumer_event_state rows are removed in the SAME transaction
and BEFORE the persistent_events rows, keeping foreign keys satisfied
(PRAGMA foreign_keys = ON). No schema, contract, or replay-semantics
changes.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any


def prune_persistent_events(
    conn: sqlite3.Connection,
    max_age_days: float,
    max_rows: int,
) -> dict[str, Any]:
    """Delete persistent events eligible by age/rows AND safe for all consumers.

    Args:
        conn: Open SQLite connection (this function manages the transaction).
        max_age_days: Delete events whose created_at is older than this many
            days. 0 disables the age criterion.
        max_rows: Keep only the newest N events (by sequence). 0 disables the
            row-count criterion.

    Returns:
        {"events_deleted": int, "state_deleted": int}
    """
    if max_age_days <= 0 and max_rows <= 0:
        return {"events_deleted": 0, "state_deleted": 0}

    criteria: list[str] = []
    params: list[Any] = []
    if max_age_days > 0:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max_age_days)
        ).isoformat()
        criteria.append("pe.created_at < ?")
        params.append(cutoff)
    if max_rows > 0:
        # Everything outside the newest max_rows sequences MAY be pruned.
        criteria.append(
            "pe.sequence NOT IN ("
            "  SELECT pe2.sequence FROM persistent_events AS pe2"
            "  ORDER BY pe2.sequence DESC LIMIT ?"
            ")"
        )
        params.append(int(max_rows))

    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            f"""
            SELECT pe.id
            FROM persistent_events AS pe
            WHERE ({" OR ".join(criteria)})
              AND NOT EXISTS (
                  SELECT 1
                  FROM consumer_event_state AS ces
                  LEFT JOIN consumer_checkpoints AS cp
                         ON cp.consumer_id = ces.consumer_id
                  WHERE ces.event_id = pe.id
                    AND ces.acknowledged_at IS NULL
                    AND pe.sequence > COALESCE(cp.last_sequence, 0)
              )
            """,
            params,
        ).fetchall()
        target_ids = [r[0] for r in rows]
        if not target_ids:
            conn.commit()
            return {"events_deleted": 0, "state_deleted": 0}

        id_params = [(i,) for i in target_ids]
        state_cur = conn.executemany(
            "DELETE FROM consumer_event_state WHERE event_id = ?", id_params
        )
        state_deleted = state_cur.rowcount
        conn.executemany(
            "DELETE FROM persistent_events WHERE id = ?", id_params
        )
        conn.commit()
        return {
            "events_deleted": len(target_ids),
            "state_deleted": state_deleted,
        }
    except Exception:
        conn.rollback()
        raise
