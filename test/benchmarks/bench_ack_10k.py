"""Benchmark — 10,000-event ACK measurement.

Creates 10000 pending events and ACKs them sequentially.
Reports total elapsed, throughput, per-ACK latency stats.
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
from core.persistence.modules.replay import get_consumer_inbox_status


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
    n = 10000
    tmp = tempfile.mkdtemp(prefix=f"bench_ack_10k_")
    store = EventStore(os.path.join(tmp, "test.db"))
    store.register_consumer("c1")

    print(f"  Creating {n} events...", flush=True)
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
    print(f"  {n} events created", flush=True)

    # Get all event IDs
    conn = store._open(store._db_path)
    from core.persistence.modules.events import row_to_event
    all_events = conn.execute(
        "SELECT sequence, id FROM persistent_events ORDER BY sequence").fetchall()
    conn.close()
    event_ids = [e[1] for e in all_events]
    assert len(event_ids) == n, f"expected {n} events, got {len(event_ids)}"

    # Sequential ACKs
    times = []
    for eid in event_ids:
        t0 = time.perf_counter_ns()
        store.acknowledge_event("c1", eid)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)

    total_ms = sum(times)
    throughput = n / (total_ms / 1000.0) if total_ms > 0 else 0

    rows.append({
        "scenario": f"sequential_ack_{n}_events",
        "event_count": n,
        "total_ms": round(total_ms, 2),
        "throughput_ack_per_second": round(throughput, 1),
        "per_ack_p50_ms": round(_percentile(times, 50), 4),
        "per_ack_p95_ms": round(_percentile(times, 95), 4),
        "per_ack_p99_ms": round(_percentile(times, 99), 4),
        "min_ms": round(min(times), 4),
        "max_ms": round(max(times), 4),
        "mean_ms": round(sum(times)/len(times), 4),
    })
    print(f"  {n} ACKs: total={total_ms:.1f}ms p50={_percentile(times,50):.3f}ms", flush=True)

    # Verify correctness
    conn2 = store._open(store._db_path)
    status = get_consumer_inbox_status(conn2, "c1")
    conn2.close()
    rows.append({
        "scenario": f"ack_correctness_{n}",
        "pending_after_ack": status["pending_count"],
        "expected_pending": 0,
        "correct": status["pending_count"] == 0,
    })

    # Also run smaller cases for scale comparison if not already present
    shutil.rmtree(tmp, ignore_errors=True)

    # Re-run with 100 and 1000 for comparison
    for n_small in [100, 1000]:
        tmp2 = tempfile.mkdtemp(prefix=f"bench_ack_{n_small}_")
        store2 = EventStore(os.path.join(tmp2, "test.db"))
        store2.register_consumer("c1")
        for i in range(n_small):
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
                store=store2, bus=None,
            )
        conn3 = store2._open(store2._db_path)
        all_ev = conn3.execute(
            "SELECT sequence, id FROM persistent_events ORDER BY sequence").fetchall()
        conn3.close()
        eids = [e[1] for e in all_ev]
        t2 = []
        for eid in eids:
            t0 = time.perf_counter_ns()
            store2.acknowledge_event("c1", eid)
            dt = (time.perf_counter_ns() - t0) / 1e6
            t2.append(dt)
        rows.append({
            "scenario": f"sequential_ack_{n_small}_events",
            "event_count": n_small,
            "total_ms": round(sum(t2), 2),
            "per_ack_p50_ms": round(_percentile(t2, 50), 4),
            "per_ack_p95_ms": round(_percentile(t2, 95), 4),
            "per_ack_p99_ms": round(_percentile(t2, 99), 4),
        })
        print(f"  {n_small} ACKs: p50={_percentile(t2,50):.3f}ms", flush=True)
        shutil.rmtree(tmp2, ignore_errors=True)

    return {"rows": rows}
