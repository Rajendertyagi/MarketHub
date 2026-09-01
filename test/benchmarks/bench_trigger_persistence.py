"""Benchmark Part 3 — No-trigger vs trigger cost.

Scenarios:
  A: evaluation where state does not change (TRUE→TRUE)
  B: state transition but no trigger (TRUE→FALSE re-arm)
  C: root trigger (UNKNOWN→TRUE or FALSE→TRUE)
  D: once-mode trigger + disable
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


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


async def run():
    WARMUP = 10
    MEASURE = 50
    rows = []

    # --- A: TRUE→TRUE (no change) ---
    store, engine, tmp = _mk_engine(1)
    try:
        aid = _create_alert(store, "gt", 25000.0)
        engine.reload()
        q_fire = _FakeQuote(26000.0)
        q_nochange = _FakeQuote(26000.0)
        await engine.evaluate(q_fire)  # UNKNOWN→TRUE, fires once
        # Now TRUE→TRUE
        times = []
        for _ in range(WARMUP):
            await engine.evaluate(q_nochange)
        for _ in range(MEASURE):
            t0 = time.perf_counter_ns()
            fired = await engine.evaluate(q_nochange)
            times.append((time.perf_counter_ns() - t0) / 1e6)
            assert len(fired) == 0, "should not fire on TRUE→TRUE"
        rows.append({
            "scenario": "A_true_to_true_no_change",
            "writes": 0,
            "p50_ms": round(_percentile(times, 50), 4),
            "p95_ms": round(_percentile(times, 95), 4),
            "p99_ms": round(_percentile(times, 99), 4),
            "iterations": MEASURE,
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- B: TRUE→FALSE (re-arm, no fire) ---
    store, engine, tmp = _mk_engine(1)
    try:
        aid = _create_alert(store, "gt", 25000.0)
        engine.reload()
        q_above = _FakeQuote(26000.0)
        q_below = _FakeQuote(24000.0)
        await engine.evaluate(q_above)  # fires
        times = []
        for _ in range(WARMUP):
            await engine.evaluate(q_below)
        for _ in range(MEASURE):
            t0 = time.perf_counter_ns()
            fired = await engine.evaluate(q_below)
            times.append((time.perf_counter_ns() - t0) / 1e6)
            assert len(fired) == 0, "should not fire on TRUE→FALSE"
        rows.append({
            "scenario": "B_true_to_false_rearm",
            "writes": 1,  # state save
            "p50_ms": round(_percentile(times, 50), 4),
            "p95_ms": round(_percentile(times, 95), 4),
            "p99_ms": round(_percentile(times, 99), 4),
            "iterations": MEASURE,
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- C: root trigger (FALSE→TRUE) ---
    store, engine, tmp = _mk_engine(1)
    try:
        aid = _create_alert(store, "gt", 25000.0)
        engine.reload()
        q_below = _FakeQuote(24000.0)
        q_above = _FakeQuote(26000.0)
        await engine.evaluate(q_below)  # goes FALSE
        times = []
        for _ in range(WARMUP):
            pass  # need to get to FALSE state
        # Fire
        t0 = time.perf_counter_ns()
        fired = await engine.evaluate(q_above)
        trigger_time = (time.perf_counter_ns() - t0) / 1e6
        assert len(fired) == 1, "should fire on FALSE→TRUE"
        # Measure subsequent TRUE→TRUE (no fire)
        times = [trigger_time]
        for _ in range(MEASURE - 1):
            t0 = time.perf_counter_ns()
            fired = await engine.evaluate(q_above)
            times.append((time.perf_counter_ns() - t0) / 1e6)
            assert len(fired) == 0
        rows.append({
            "scenario": "C_root_trigger",
            "writes": 4,  # runtime state + alert row + event + materialization
            "p50_ms": round(_percentile(times, 50), 4),
            "p95_ms": round(_percentile(times, 95), 4),
            "p99_ms": round(_percentile(times, 99), 4),
            "iterations": MEASURE,
            "note": "first sample is the trigger; rest are TRUE->TRUE no-fire"
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- D: once-mode trigger + disable ---
    store, engine, tmp = _mk_engine(1)
    try:
        aid = _create_alert(store, "gt", 25000.0, trigger_mode="once")
        engine.reload()
        q_above = _FakeQuote(26000.0)
        t0 = time.perf_counter_ns()
        fired = await engine.evaluate(q_above)
        dt = (time.perf_counter_ns() - t0) / 1e6
        assert len(fired) == 1
        # Verify disabled
        a = store.get_condition_alert(aid)
        assert a["enabled"] == False
        # Next evaluate should not match dep_index
        t0 = time.perf_counter_ns()
        fired2 = await engine.evaluate(q_above)
        dt2 = (time.perf_counter_ns() - t0) / 1e6
        assert len(fired2) == 0
        rows.append({
            "scenario": "D_once_trigger_disable",
            "writes": 4,
            "trigger_p50_ms": round(dt, 4),
            "post_disable_p50_ms": round(dt2, 4),
            "note": "once-mode: alert removed from dep_index after fire"
        })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}


def _mk_engine(n_alerts=1):
    tmp = tempfile.mkdtemp(prefix="bench_trigger_")
    store = EventStore(os.path.join(tmp, "test.db"))
    store.register_consumer("c1")
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "T",
         "tradingsymbol": "SYM", "name": "S",
         "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
    ])
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
    return store, engine, tmp


def _create_alert(store, operator, threshold, trigger_mode="repeat"):
    return store.create_condition_alert(
        consumer_id="c1", name="bench", trigger_mode=trigger_mode,
        condition_json={
            "condition_version": 1, "condition_id": "c1",
            "metric": "ltp", "operator": operator, "value": threshold,
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
        })
