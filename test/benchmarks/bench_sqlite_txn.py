"""Benchmark Part 4 — SQLite transaction measurements.

Measures actual transaction latency for:
  - non-trigger state save
  - trigger transaction
  - multi-leaf v2 trigger

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
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
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


def _make_tracked_engine(tmp_path):
    store = EventStore(tmp_path)
    store.register_consumer("c1")
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "T",
         "tradingsymbol": "SYM", "name": "S",
         "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
    ])
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())

    write_counts = [0]
    orig_open = store._open

    def tracked_open(db_path):
        conn = orig_open(db_path)
        def trace_callback(sql):
            if sql:
                sql_upper = sql.strip().upper()
                if sql_upper.startswith(("INSERT", "UPDATE", "DELETE", "UPSERT")):
                    write_counts[0] += 1
        conn.set_trace_callback(trace_callback)
        return conn

    store._open = tracked_open
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    return store, engine, write_counts


async def run():
    rows = []
    WARMUP = 10
    MEASURE = 50

    def build_engine(tmp_prefix):
        tmp = tempfile.mkdtemp(prefix=tmp_prefix)
        store, engine, write_counts = _make_tracked_engine(
            os.path.join(tmp, "test.db"))
        return store, engine, write_counts, tmp

    # --- A: non-trigger state save (FALSE -> FALSE) ---
    store, engine, wc, tmp = build_engine("bench_sqlite_a_")
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version": 1, "condition_id": "c1",
            "metric": "ltp", "operator": "gt", "value": 25000.0,
            "instrument": {"canonical_id": "NSE:EQUITY:I"}})
    engine.reload()
    q = _FakeQuote(24000.0)
    for _ in range(WARMUP):
        await engine.evaluate(q)
    times = []
    call_counts = []
    for _ in range(MEASURE):
        wc[0] = 0
        t0 = time.perf_counter_ns()
        await engine.evaluate(q)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)
        call_counts.append(wc[0])
    rows.append({
        "scenario": "A_non_trigger_state_save",
        "sql_writes_p50": int(_percentile(call_counts, 50)),
        "sql_writes_p95": int(_percentile(call_counts, 95)),
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- B: trigger transaction (FALSE -> TRUE) ---
    store, engine, wc, tmp = build_engine("bench_sqlite_b_")
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version": 1, "condition_id": "c1",
            "metric": "ltp", "operator": "gt", "value": 25000.0,
            "instrument": {"canonical_id": "NSE:EQUITY:I"}})
    engine.reload()
    q_above = _FakeQuote(26000.0)
    q_below = _FakeQuote(24000.0)
    await engine.evaluate(q_below)  # FALSE
    for _ in range(WARMUP):
        await engine.evaluate(q_above)  # TRUE (fires)
        await engine.evaluate(q_below)  # FALSE (re-arm)
    times = []
    call_counts = []
    for _ in range(MEASURE):
        wc[0] = 0
        t0 = time.perf_counter_ns()
        fired = await engine.evaluate(q_above)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)
        call_counts.append(wc[0])
        assert len(fired) == 1, "should fire"
        await engine.evaluate(q_below)  # re-arm
    rows.append({
        "scenario": "B_trigger_transaction",
        "sql_writes_p50": int(_percentile(call_counts, 50)),
        "sql_writes_p95": int(_percentile(call_counts, 95)),
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    # --- C: multi-leaf v2 trigger ---
    store, engine, wc, tmp = build_engine("bench_sqlite_c_")
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version": 2, "logic": "all",
            "conditions": [
                {"condition_version": 1, "condition_id": "ca",
                 "metric": "ltp", "operator": "gt", "value": 25000.0,
                 "instrument": {"canonical_id": "NSE:EQUITY:I"}},
                {"condition_version": 1, "condition_id": "cb",
                 "metric": "volume", "operator": "gt", "value": 1000.0,
                 "instrument": {"canonical_id": "NSE:EQUITY:I"}},
            ]})
    engine.reload()
    q_above = _FakeQuote(26000.0)
    q_below = _FakeQuote(24000.0)
    await engine.evaluate(q_below)
    for _ in range(WARMUP):
        await engine.evaluate(q_above)
        await engine.evaluate(q_below)
    times = []
    call_counts = []
    for _ in range(MEASURE):
        wc[0] = 0
        t0 = time.perf_counter_ns()
        fired = await engine.evaluate(q_above)
        dt = (time.perf_counter_ns() - t0) / 1e6
        times.append(dt)
        call_counts.append(wc[0])
        assert len(fired) == 1
        await engine.evaluate(q_below)
    rows.append({
        "scenario": "C_multi_leaf_v2_trigger",
        "sql_writes_p50": int(_percentile(call_counts, 50)),
        "sql_writes_p95": int(_percentile(call_counts, 95)),
        "p50_ms": round(_percentile(times, 50), 4),
        "p95_ms": round(_percentile(times, 95), 4),
        "p99_ms": round(_percentile(times, 99), 4),
        "iterations": MEASURE,
    })
    shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
