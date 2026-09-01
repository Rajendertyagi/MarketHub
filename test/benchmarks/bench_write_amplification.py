"""Benchmark Part 5 — State write amplification.

1000 repeated evaluations where state is unchanged.
Count exact SQL writes.
Then alternate TRUE/FALSE and measure writes.
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
    def __init__(self, ltp, token="T"):
        self.ltp = ltp
        self.volume = ltp * 100
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = "SYM"
        self.provider = "upstox"


async def run():
    rows = []

    # --- Unchanged state ---
    tmp = tempfile.mkdtemp(prefix="bench_writeamp_")
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
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version":1,"condition_id":"c1",
            "metric":"ltp","operator":"gt","value":25000.0,
            "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine.reload()

    # Fire once to establish TRUE state
    q_above = _FakeQuote(26000.0)
    await engine.evaluate(q_above)

    # 1000 repeated evaluations, state unchanged (TRUE→TRUE)
    sql_counts = []
    for _ in range(1000):
        store._trace_calls = []
        orig_open = store._open
        def tracked_open(path):
            conn = orig_open(path)
            calls = []
            orig_exec = conn.execute
            def tracked_execute(sql, params=()):
                calls.append(sql)
                return orig_exec(sql, params)
            conn.execute = tracked_execute
            return conn
        store._open = tracked_open
        await engine.evaluate(q_above)
        # Count INSERT/UPDATE/DELETE
        write_count = sum(1 for s in store._trace_calls if s and any(s.upper().startswith(p) for p in ("INSERT","UPDATE","DELETE")))
        sql_counts.append(write_count)
        store._open = orig_open

    rows.append({
        "scenario": "unchanged_state_1000_evals",
        "evaluations": 1000,
        "total_sql_writes": sum(sql_counts),
        "avg_writes_per_eval": round(sum(sql_counts) / len(sql_counts), 2),
        "min_writes": min(sql_counts),
        "max_writes": max(sql_counts),
    })

    # --- Alternating TRUE/FALSE ---
    store2 = EventStore(os.path.join(tmp, "test2.db"))
    store2.register_consumer("c1")
    store2.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "T",
         "tradingsymbol": "SYM", "name": "S",
         "instrument_type": "EQ", "segment": "NSE", "isin": "I"}
    ])
    resolver2 = MarketInstrumentIdentityResolver()
    resolver2.register_catalog_rows(store2.list_all_instruments())
    engine2 = ConditionAlertEngine(store2, resolver=resolver2, bus=None)
    aid2 = store2.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version":1,"condition_id":"c1",
            "metric":"ltp","operator":"gt","value":25000.0,
            "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine2.reload()

    q_above2 = _FakeQuote(26000.0)
    q_below2 = _FakeQuote(24000.0)
    sql_counts_alt = []
    for i in range(200):
        store2._trace_calls = []
        orig_open2 = store2._open
        def tracked_open2(path):
            conn = orig_open2(path)
            orig_exec = conn.execute
            def tracked_execute(sql, params=()):
                calls2.append(sql)
                return orig_exec(sql, params)
            calls2 = []
            conn.execute = tracked_execute
            return conn
        store2._open = tracked_open2
        q = q_above2 if i % 2 == 0 else q_below2
        await engine2.evaluate(q)
        write_count = sum(1 for s in calls2 if s and any(s.upper().startswith(p) for p in ("INSERT","UPDATE","DELETE")))
        sql_counts_alt.append(write_count)
        store2._open = orig_open2

    rows.append({
        "scenario": "alternating_true_false_200_evals",
        "evaluations": 200,
        "total_sql_writes": sum(sql_counts_alt),
        "avg_writes_per_eval": round(sum(sql_counts_alt) / len(sql_counts_alt), 2),
        "min_writes": min(sql_counts_alt),
        "max_writes": max(sql_counts_alt),
    })

    shutil.rmtree(tmp, ignore_errors=True)
    return {"rows": rows}
