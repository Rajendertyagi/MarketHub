"""Benchmark Part 2 — Condition tree complexity latency.

Measures:
  v1 single level leaf
  v2 ALL with 2 leaves
  v2 ANY with 2 leaves
  nested v2 group (ALL of ANY)
  mixed quote+analytics group (deterministic cached snapshot)

Bucket size controlled at 10 alerts per type.
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
import shutil
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
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


class _FakeAnalytics:
    """Deterministic analytics service returning fixed snapshots."""
    def __init__(self):
        self._cache = {}

    def get_snapshot(self, chain_key):
        return self._cache.get(chain_key)

    def set_snapshot(self, chain_key, value):
        class Snap:
            pass
        s = Snap()
        s.pcr_oi = value
        s.is_stale = False
        s.age_seconds = 0
        self._cache[chain_key] = s


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


def _make_base():
    tmp = tempfile.mkdtemp(prefix="bench_complex_")
    store = EventStore(os.path.join(tmp, "test.db"))
    store.register_consumer("c1")
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "T",
         "tradingsymbol": "SYM", "name": "S",
         "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
    ])
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    return store, resolver, tmp


async def run():
    WARMUP = 20
    MEASURE = 100
    rows = []
    BUCKET = 10

    # --- v1 single leaf ---
    store, resolver, tmp = _make_base()
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    for i in range(BUCKET):
        store.create_condition_alert(consumer_id="c1", name=f"v1_{i}",
            trigger_mode="repeat",
            condition_json={"condition_version": 1, "condition_id": f"c{i}",
                "metric": "ltp", "operator": "gt", "value": 25000.0 + i,
                "instrument": {"canonical_id": "NSE:EQUITY:I"}})
    engine.reload()
    q = _FakeQuote(26000.0)
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    for _ in range(MEASURE):
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        times.append((time.perf_counter_ns() - t0) / 1e6)
    rows.append({
        "scenario": "v1_single_leaf",
        "bucket_size": BUCKET,
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- v2 ALL 2 leaves ---
    store, resolver, tmp = _make_base()
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    for i in range(BUCKET):
        store.create_condition_alert(consumer_id="c1", name=f"all2_{i}",
            trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "all",
                "conditions": [
                    {"condition_version": 1, "condition_id": f"c{i}a",
                     "metric": "ltp", "operator": "gt", "value": 25000.0 + i,
                     "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                    {"condition_version": 1, "condition_id": f"c{i}b",
                     "metric": "volume", "operator": "gt", "value": 1000.0,
                     "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                ]})
    engine.reload()
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    for _ in range(MEASURE):
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        times.append((time.perf_counter_ns() - t0) / 1e6)
    rows.append({
        "scenario": "v2_all_2leaves",
        "bucket_size": BUCKET,
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- v2 ANY 2 leaves ---
    store, resolver, tmp = _make_base()
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    for i in range(BUCKET):
        store.create_condition_alert(consumer_id="c1", name=f"any2_{i}",
            trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "any",
                "conditions": [
                    {"condition_version": 1, "condition_id": f"c{i}a",
                     "metric": "ltp", "operator": "gt", "value": 25000.0 + i,
                     "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                    {"condition_version": 1, "condition_id": f"c{i}b",
                     "metric": "volume", "operator": "gt", "value": 1000.0,
                     "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                ]})
    engine.reload()
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    for _ in range(MEASURE):
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        times.append((time.perf_counter_ns() - t0) / 1e6)
    rows.append({
        "scenario": "v2_any_2leaves",
        "bucket_size": BUCKET,
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- nested v2 (ALL of ANY) ---
    store, resolver, tmp = _make_base()
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    for i in range(BUCKET):
        store.create_condition_alert(consumer_id="c1", name=f"nest_{i}",
            trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "all",
                "conditions": [
                    {"condition_version": 2, "logic": "any",
                     "conditions": [
                         {"condition_version": 1, "condition_id": f"c{i}a",
                          "metric": "ltp", "operator": "gt", "value": 25000.0 + i,
                          "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                         {"condition_version": 1, "condition_id": f"c{i}b",
                          "metric": "volume", "operator": "gt", "value": 1000.0,
                          "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                     ]},
                    {"condition_version": 1, "condition_id": f"c{i}c",
                     "metric": "ltp", "operator": "lt", "value": 30000.0,
                     "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                ]})
    engine.reload()
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    for _ in range(MEASURE):
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        times.append((time.perf_counter_ns() - t0) / 1e6)
    rows.append({
        "scenario": "v2_nested_all_of_any",
        "bucket_size": BUCKET,
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- mixed quote+analytics ---
    store, resolver, tmp = _make_base()
    from app.market_analytics import MarketAnalyticsService
    mock_ms = type('M', (), {'option_chain': asyncio.coroutine(lambda **kw: None)})()
    analytics = MarketAnalyticsService(mock_ms)
    analytics.set_snapshot("analytics:NSE:EQUITY:I:2026-09-25", 1.1)
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None,
                                  analytics_service=analytics)
    for i in range(BUCKET):
        store.create_condition_alert(consumer_id="c1", name=f"mix_{i}",
            trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "all",
                "conditions": [
                    {"condition_version": 1, "condition_id": f"c{i}a",
                     "metric": "ltp", "operator": "gt", "value": 25000.0 + i,
                     "instrument": {"canonical_id": "NSE:EQUITY:I",
                                    "expiry": "2026-09-25",
                                    "_dependency_key": "analytics:NSE:EQUITY:I:2026-09-25"}},
                    {"condition_version": 1, "condition_id": f"c{i}b",
                     "metric": "ltp", "operator": "lt", "value": 30000.0,
                     "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                ]})
    engine.reload()
    # Need to also register the analytics chain
    analytics.register_chain("analytics:NSE:EQUITY:I:2026-09-25", "dummy")
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    for _ in range(MEASURE):
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        times.append((time.perf_counter_ns() - t0) / 1e6)
    rows.append({
        "scenario": "mixed_quote_analytics",
        "bucket_size": BUCKET,
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
