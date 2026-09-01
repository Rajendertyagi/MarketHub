"""Benchmark Part 13 — Restart scale.

Measure reload() time with 100, 1000, 5000, 10000 alerts.
Includes: DB load, dep-index rebuild, analytics chain reconstruction.
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
    rows = []
    WARMUP = 3
    MEASURE = 10
    alert_counts = [100, 1000, 5000, 10000]

    for n in alert_counts:
        tmp = tempfile.mkdtemp(prefix=f"bench_restart_{n}_")
        store = EventStore(os.path.join(tmp, "test.db"))
        store.register_consumer("c1")
        store.replace_provider_instruments("upstox", [
            {"exchange": "NSE", "instrument_token": "T",
             "tradingsymbol": "SYM", "name": "S",
             "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
        ])
        # Create alerts
        for i in range(n):
            store.create_condition_alert(
                consumer_id="c1", name=f"a{i}", trigger_mode="repeat",
                condition_json={"condition_version":1,"condition_id":f"c{i}",
                    "metric":"ltp","operator":"gt","value":20000.0+i*10,
                    "instrument":{"canonical_id":"NSE:EQUITY:I"}})
        # Populate some runtime state
        resolver = MarketInstrumentIdentityResolver()
        resolver.register_catalog_rows(store.list_all_instruments())

        times = []
        for run_idx in range(WARMUP + MEASURE):
            engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
            t0 = time.perf_counter_ns()
            engine.reload()
            dt = (time.perf_counter_ns() - t0) / 1e6  # ms
            if run_idx >= WARMUP:
                times.append(dt)

        if times:
            rows.append({
                "scenario": f"restart_{n}_alerts",
                "alert_count": n,
                "p50_ms": round(_percentile(times, 50), 2),
                "p95_ms": round(_percentile(times, 95), 2),
                "p99_ms": round(_percentile(times, 99), 2),
                "min_ms": round(min(times), 2),
                "max_ms": round(max(times), 2),
                "mean_ms": round(sum(times)/len(times), 2),
                "iterations": len(times),
            })
            print(f"  {n} alerts: p50={_percentile(times,50):.2f}ms p95={_percentile(times,95):.2f}ms")
        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
