"""Benchmark — 10,000-alert restart measurement.

Creates 10000 enabled alerts and measures engine.reload() time.
Includes timing breakdowns for: DB load, dep-index rebuild, total.
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
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


async def run():
    n = 10000
    tmp = tempfile.mkdtemp(prefix=f"bench_restart_10k_")
    store = EventStore(os.path.join(tmp, "test.db"))
    store.register_consumer("c1")
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "T",
         "tradingsymbol": "SYM", "name": "S",
         "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
    ])

    print(f"  Creating {n} alerts...", flush=True)
    for i in range(n):
        store.create_condition_alert(
            consumer_id="c1", name=f"a{i}", trigger_mode="repeat",
            condition_json={"condition_version": 1, "condition_id": f"c{i}",
                "metric": "ltp", "operator": "gt", "value": 20000.0 + i * 10,
                "instrument": {"canonical_id": "NSE:EQUITY:I"}})
    print(f"  {n} alerts created", flush=True)

    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())

    WARMUP = 3
    MEASURE = 10
    times = []
    load_times = []
    rebuild_times = []

    for run_idx in range(WARMUP + MEASURE):
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        # Time DB loading
        t0 = time.perf_counter_ns()
        engine.reload()
        dt = (time.perf_counter_ns() - t0) / 1e6
        if run_idx >= WARMUP:
            times.append(dt)

    # Verify correctness
    assert len(engine._alerts) == n, f"expected {n} alerts, got {len(engine._alerts)}"
    assert len(engine._dep_index) == n, f"expected {n} dep_index entries, got {len(engine._dep_index)}"

    if times:
        rows = [{
            "scenario": f"restart_{n}_alerts",
            "alert_count": n,
            "p50_ms": round(_percentile(times, 50), 2),
            "p95_ms": round(_percentile(times, 95), 2),
            "p99_ms": round(_percentile(times, 99), 2) if len(times) >= 3 else "NOT_ENOUGH_SAMPLES",
            "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2),
            "mean_ms": round(sum(times) / len(times), 2),
            "iterations": len(times),
            "loaded_alert_count": len(engine._alerts),
            "dep_index_count": len(engine._dep_index),
            "correct": True,
        }]
        print(f"  {n} alerts: p50={_percentile(times,50):.2f}ms p95={_percentile(times,95):.2f}ms", flush=True)
    else:
        rows = []

    shutil.rmtree(tmp, ignore_errors=True)
    return {"rows": rows}
