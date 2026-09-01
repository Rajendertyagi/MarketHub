"""Benchmark Part 15 — ACK scale.

Sequential ACKs: 1, 100, 1000, 10000.
Reports per-ACK latency.
"""
from __future__ import annotations
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
from core import events
from core.alert_events import build_alert_triggered_data


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
    event_counts = [1, 100, 1000, 10000]

    for n in event_counts:
        tmp = tempfile.mkdtemp(prefix=f"bench_ack_{n}_")
        store = EventStore(os.path.join(tmp, "test.db"))
        store.register_consumer("c1")

        # Create events
        for i in range(n):
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
                event_type="alert.triggered", source="event_server",
                data=data, persistent=True,
                routing={"targets": ["c1"]},
                store=store, bus=None,
            )

        # Get all event IDs
        conn = store._open(store._db_path)
        from core.persistence.modules.events import _row_to_event
        all_events = conn.execute(
            "SELECT sequence, id FROM persistent_events ORDER BY sequence").fetchall()
        conn.close()

        event_ids = [e[1] for e in all_events]

        # Sequential ACKs
        times = []
        for eid in event_ids:
            t0 = time.perf_counter_ns()
            store.acknowledge_event("c1", eid)
            dt = (time.perf_counter_ns() - t0) / 1e6
            times.append(dt)

        total_ms = sum(times)
        rows.append({
            "scenario": f"sequential_ack_{n}_events",
            "event_count": n,
            "total_ms": round(total_ms, 2),
            "per_ack_p50_ms": round(_percentile(times, 50), 4),
            "per_ack_p95_ms": round(_percentile(times, 95), 4),
            "per_ack_p99_ms": round(_percentile(times, 99), 4),
            "min_ms": round(min(times), 4),
            "max_ms": round(max(times), 4),
        })
        print(f"  {n} ACKs: total={total_ms:.1f}ms p50={_percentile(times,50):.3f}ms")
        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
