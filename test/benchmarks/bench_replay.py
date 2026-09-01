"""Benchmark Part 14 — Replay scale.

Create deterministic events, measure:
  pending list page fetch
  inbox/status query
  checkpoint behavior
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
import shutil
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.store import EventStore
from core.persistence.modules.replay import replay_events, get_consumer_inbox_status, advance_checkpoint


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


async def run():
    rows = []
    event_counts = [100, 1000, 10000]
    page_size = 100

    for n_events in event_counts:
        tmp = tempfile.mkdtemp(prefix=f"bench_replay_{n_events}_")
        store = EventStore(os.path.join(tmp, "test.db"))
        store.register_consumer("c1")

        # Create events
        from datetime import datetime, timezone
        from core import events
        from core.alert_events import build_alert_triggered_data

        for i in range(n_events):
            data = build_alert_triggered_data(
                alert_family="market_condition",
                alert_id=f"alert-{i}",
                consumer_id="c1",
                condition={"condition_version": 1, "logic": None,
                           "conditions": [{"condition_version": 1, "condition_id": "c1",
                                          "metric": "ltp", "operator": "gt",
                                          "value": 25000.0, "instrument": {"canonical_id": "NSE:EQUITY:I"}}]},
                observed={"root_result": "true", "leaves": []},
                instrument={"canonical_id": "NSE:EQUITY:I"},
                one_shot=False,
            )
            await events.publish_event(
                event_type="alert.triggered",
                source="event_server",
                data=data,
                persistent=True,
                routing={"targets": ["c1"]},
                store=store, bus=None,
            )

        # Measure pending list
        conn = store._open(store._db_path)
        try:
            status = get_consumer_inbox_status(conn, "c1")
            rows.append({
                "scenario": f"pending_status_{n_events}",
                "pending_events": n_events,
                "pending_count": status["pending_count"],
                "latest_sequence": status["latest_sequence"],
            })
        finally:
            conn.close()

        # Measure replay page fetch
        def row_to_event(r):
            return {
                "sequence": r["sequence"], "id": r["id"],
                "type": r["type"], "source": r["source"],
                "timestamp": r["timestamp"], "data": r["data"],
                "routing": r["routing"],
            }

        times = []
        for _ in range(10):
            t0 = time.perf_counter_ns()
            result = replay_events(conn, "c1", limit=page_size,
                                   max_replay_limit=1000, row_to_event=row_to_event)
            dt = (time.perf_counter_ns() - t0) / 1e6
            times.append(dt)

        rows.append({
            "scenario": f"replay_page_{n_events}_events_page{page_size}",
            "total_events": n_events,
            "page_size": page_size,
            "returned": result["returned"],
            "p50_ms": round(_percentile(times, 50), 4),
            "p95_ms": round(_percentile(times, 95), 4),
            "p99_ms": round(_percentile(times, 99), 4),
            "iterations": 10,
        })
        print(f"  {n_events} events: replay p50={_percentile(times,50):.3f}ms")

        # Measure ACK
        ack_times = []
        events_list = result["events"]
        for evt in events_list[:min(100, len(events_list))]:
            t0 = time.perf_counter_ns()
            store.acknowledge_event("c1", evt["id"])
            dt = (time.perf_counter_ns() - t0) / 1e6
            ack_times.append(dt)

        if ack_times:
            rows.append({
                "scenario": f"ack_{n_events}_events_sample100",
                "total_events": n_events,
                "acked_sample": len(ack_times),
                "p50_ms": round(_percentile(ack_times, 50), 4),
                "p95_ms": round(_percentile(ack_times, 95), 4),
                "p99_ms": round(_percentile(ack_times, 99), 4),
            })

        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
