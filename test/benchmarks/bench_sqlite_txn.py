"""Benchmark Part 4 — SQLite transaction measurements.

Measures actual transaction latency for:
  - non-trigger state save
  - trigger transaction
  - once trigger
  - nested/multi-leaf trigger

Counts actual SQL writes via trace callback.
"""
from __future__ import annotations
import asyncio
import os
import sqlite3
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
    def __init__(self, ltp, token="T"):
        self.ltp = ltp
        self.volume = ltp * 100
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = "SYM"
        self.provider = "upstox"


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
    WARMUP = 10
    MEASURE = 50

    # Helper to count SQL statements via trace
    def make_tracked_store(tmp_path):
        store = EventStore(tmp_path)
        store.register_consumer("c1")
        store.replace_provider_instruments("upstox", [
            {"exchange": "NSE", "instrument_token": "T",
             "tradingsymbol": "SYM", "name": "S",
             "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
        ])
        # Track SQL calls
        store._trace_calls = []
        orig_conn = store._open
        def tracked_open(path):
            conn = orig_conn(path)
            conn.set_trace_callback(lambda evt: store._trace_calls.append(evt))
            return conn
        store._open = tracked_open
        return store

    # --- A: non-trigger state save ---
    tmp = tempfile.mkdtemp(prefix="bench_sqlite_")
    store = make_tracked_store(os.path.join(tmp, "test.db"))
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version":1,"condition_id":"c1",
            "metric":"ltp","operator":"gt","value":25000.0,
            "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine.reload()
    q = _FakeQuote(24000.0)  # below threshold
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    call_counts = []
    for _ in range(MEASURE):
        store._trace_calls = []
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)
        call_counts.append(len(store._trace_calls))
    rows.append({
        "scenario": "A_non_trigger_state_save",
        "sql_calls_p50": int(_percentile(call_counts, 50)),
        "sql_calls_p95": int(_percentile(call_counts, 95)),
        "sql_calls_p99": int(_percentile(call_counts, 99)),
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- B: trigger transaction ---
    tmp = tempfile.mkdtemp(prefix="bench_sqlite_")
    store = make_tracked_store(os.path.join(tmp, "test.db"))
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version":1,"condition_id":"c1",
            "metric":"ltp","operator":"gt","value":25000.0,
            "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine.reload()
    q = _FakeQuote(26000.0)  # above threshold
    # First eval to establish TRUE state
    await engine.evaluate(q)
    for _ in range(WARMUP):
        await engine.evaluate(q)  # TRUE→TRUE, no fire
    # Now go below then above to get a fire
    q_below = _FakeQuote(24000.0)
    await engine.evaluate(q_below)  # TRUE→FALSE, re-arm
    times = []
    call_counts = []
    for _ in range(MEASURE):
        store._trace_calls = []
        t0 = time.perf_counter_ns()
        fired = await engine.evaluate(q)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)
        call_counts.append(len(store._trace_calls))
        assert len(fired) == 1, "should fire"
    rows.append({
        "scenario": "B_trigger_transaction",
        "sql_calls_p50": int(_percentile(call_counts, 50)),
        "sql_calls_p95": int(_percentile(call_counts, 95)),
        "sql_calls_p99": int(_percentile(call_counts, 99)),
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- C: multi-leaf v2 trigger ---
    tmp = tempfile.mkdtemp(prefix="bench_sqlite_")
    store = make_tracked_store(os.path.join(tmp, "test.db"))
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version":2,"logic":"all",
            "conditions":[
                {"condition_version":1,"condition_id":"ca",
                 "metric":"ltp","operator":"gt","value":25000.0,
                 "instrument":{"canonical_id":"NSE:EQUITY:I"}},
                {"condition_version":1,"condition_id":"cb",
                 "metric":"volume","operator":"gt","value":1000.0,
                 "instrument":{"canonical_id":"NSE:EQUITY:I"}},
            ]})
    engine.reload()
    q = _FakeQuote(26000.0)
    await engine.evaluate(q)
    for _ in range(WARMUP):
        await engine.evaluate(q)
    q_below = _FakeQuote(24000.0)
    await engine.evaluate(q_below)
    times = []
    call_counts = []
    for _ in range(MEASURE):
        store._trace_calls = []
        t0 = time.perf_counter_ns()
        fired = await engine.evaluate(q)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)
        call_counts.append(len(store._trace_calls))
        assert len(fired) == 1
    rows.append({
        "scenario": "C_multi_leaf_v2_trigger",
        "sql_calls_p50": int(_percentile(call_counts, 50)),
        "sql_calls_p95": int(_percentile(call_counts, 95)),
        "sql_calls_p99": int(_percentile(call_counts, 99)),
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
