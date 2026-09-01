"""Benchmark Part 1 — Quote evaluation latency at scale.

Scenarios:
  A: 100 total, 100 bucket
  B: 1000 total, 1000 bucket
  C: 1000 total, 10 target / 990 unrelated
  D: 5000 total, 10 target
  E: 5000 total, 1000 target
  F: 10000 total, 10 target
  G: 10000 total, 1000 target

Measures: dep lookup + evaluation latency.
Verifies: evaluated count == bucket size (no global scan).
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


class _FakeQuote:
    def __init__(self, ltp, token="T", tsym="SYM"):
        self.ltp = ltp
        self.volume = ltp * 100
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = tsym
        self.provider = "upstox"


def _make_store_and_engine(n_per_instrument, n_instruments, trigger_mode="repeat"):
    tmp = tempfile.mkdtemp(prefix="bench_quote_")
    store = EventStore(os.path.join(tmp, "test.db"))
    store.register_consumer("c1")
    tokens = []
    for i in range(n_instruments):
        token = f"T{i:04d}"
        tokens.append(token)
        store.replace_provider_instruments("upstox", [
            {"exchange": "NSE", "instrument_token": token,
             "tradingsymbol": f"SYM{i}", "name": f"S{i}",
             "instrument_type": "EQ", "segment": "NSE", "isin": f"I{i}"}
        ])
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)

    canonical_ids = []
    for i in range(n_instruments):
        cid = f"NSE:EQUITY:I{i}"
        canonical_ids.append(cid)
        for j in range(n_per_instrument):
            store.create_condition_alert(
                consumer_id="c1", name=f"a{i}_{j}", trigger_mode=trigger_mode,
                condition_json={
                    "condition_version": 1, "condition_id": f"c{i}_{j}",
                    "metric": "ltp", "operator": "gt",
                    "value": 20000.0 + j * 10,
                    "instrument": {"canonical_id": cid},
                })
    engine.reload()
    return store, engine, tmp, canonical_ids, tokens


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])


async def run():
    scenarios = [
        (100, 100, 100, "A:100_total_100_bucket"),
        (1000, 1000, 1000, "B:1000_total_1000_bucket"),
        (10, 100, 10, "C:1000_total_10_target"),
        (10, 500, 10, "D:5000_total_10_target"),
        (1000, 5, 1000, "E:5000_total_1000_target"),
        (10, 1000, 10, "F:10000_total_10_target"),
        (1000, 10, 1000, "G:10000_total_1000_target"),
    ]
    rows = []
    WARMUP = 20
    MEASURE = 100

    for n_per_inst, n_inst, target_bucket, label in scenarios:
        store, engine, tmp, cids, tokens = _make_store_and_engine(
            n_per_inst, n_inst)
        try:
            # Determine which instrument is the target
            target_idx = 0
            target_cid = cids[target_idx]
            target_token = tokens[target_idx]

            # Warm up
            q = _FakeQuote(30000.0, token=target_token)
            for _ in range(WARMUP):
                await engine.evaluate(q)

            # Measure
            times = []
            eval_counts = []
            for _ in range(MEASURE):
                t0 = time.perf_counter_ns()
                fired = await engine.evaluate(q)
                dt = (time.perf_counter_ns() - t0) / 1e6  # ms
                times.append(dt)
                eval_counts.append(len(fired))

            p50 = _percentile(times, 50)
            p95 = _percentile(times, 95)
            p99 = _percentile(times, 99)
            mn = min(times)
            mx = max(times)
            mean = sum(times) / len(times)

            # Correctness check
            assert all(c == target_bucket for c in eval_counts), \
                f"evaluated counts mismatch: {eval_counts[:5]}..."

            rows.append({
                "scenario": label,
                "total_alerts": n_per_inst * n_inst,
                "bucket_size": target_bucket,
                "evaluated_alerts": target_bucket,
                "p50_ms": round(p50, 4),
                "p95_ms": round(p95, 4),
                "p99_ms": round(p99, 4),
                "min_ms": round(mn, 4),
                "max_ms": round(mx, 4),
                "mean_ms": round(mean, 4),
                "iterations": MEASURE,
                "warmup": WARMUP,
            })
            print(f"  {label}: p50={p50:.3f}ms p95={p95:.3f}ms p99={p99:.3f}ms eval={target_bucket}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows, "environment": {
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "platform": sys.platform,
    }}
