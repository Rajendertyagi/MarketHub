"""Benchmark Part 16 — EXPLAIN QUERY PLAN for replay/status queries.
"""
from __future__ import annotations
import os
import sqlite3
import sys
import tempfile
import shutil

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.store import EventStore
from core import events
from core.alert_events import build_alert_triggered_data


async def run():
    rows = []
    n_events = 1000

    tmp = tempfile.mkdtemp(prefix="bench_explain_")
    store = EventStore(os.path.join(tmp, "test.db"))
    store.register_consumer("c1")

    # Create events
    for i in range(n_events):
        data = build_alert_triggered_data(
            alert_family="market_condition", alert_id=f"alert-{i}",
            consumer_id="c1",
            condition={"condition_version": 1, "logic": None,
                       "conditions": [{"condition_version": 1, "condition_id": "c1",
                                      "metric": "ltp", "operator": "gt",
                                      "value": 25000.0,
                                      "instrument": {"canonical_id": "NSE:EQUITY:I"}}]},
            observed={"root_result": "true", "leaves": []},
            instrument={"canonical_id": "NSE:EQUITY:I"}, one_shot=False,
        )
        await events.publish_event(
            event_type="alert.triggered", source="event_server",
            data=data, persistent=True, routing={"targets": ["c1"]},
            store=store, bus=None,
        )

    conn = store._open(store._db_path)
    conn.row_factory = sqlite3.Row

    queries = [
        ("pending_list", """
            SELECT pe.sequence, pe.id, pe.type, pe.source, pe.timestamp,
                   pe.data, pe.routing, pe.created_at
            FROM persistent_events pe
            JOIN consumer_event_state ces ON ces.event_id = pe.id
            WHERE ces.consumer_id = ? AND pe.sequence > ? AND ces.acknowledged_at IS NULL
            ORDER BY pe.sequence ASC LIMIT ?
        """),
        ("inbox_status", """
            SELECT COUNT(*) AS pending_count, MAX(pe.sequence) AS latest_sequence
            FROM consumer_event_state ces
            JOIN persistent_events pe ON ces.event_id = pe.id
            WHERE ces.consumer_id = ? AND pe.sequence > ? AND ces.acknowledged_at IS NULL
        """),
        ("advance_checkpoint_find_next", """
            SELECT MIN(pe.sequence)
            FROM persistent_events pe
            JOIN consumer_event_state ces ON ces.event_id = pe.id
            WHERE ces.consumer_id = ? AND pe.sequence > ? AND ces.acknowledged_at IS NULL
            ORDER BY pe.sequence ASC LIMIT 1
        """),
        ("ack_lookup", """
            SELECT 1 FROM consumer_event_state WHERE consumer_id = ? AND event_id = ?
        """),
    ]

    for qname, sql in queries:
        plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", ("c1", 0, 100)).fetchall()
        plan_str = "; ".join(str(r["detail"]) for r in plan_rows)
        rows.append({
            "scenario": qname,
            "sql": sql.strip()[:120],
            "query_plan": plan_str,
            "uses_index": "SCAN" not in plan_str or "INDEX" in plan_str,
        })
        print(f"  {qname}: {plan_str}")

    conn.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return {"rows": rows}
