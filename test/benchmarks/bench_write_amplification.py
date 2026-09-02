"""Benchmark Part 5 — State write amplification.

1000 repeated evaluations where state is unchanged.
Count exact SQL writes.
Then alternate TRUE/FALSE and measure writes.
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

    # --- Unchanged state: 1000 repeated TRUE->TRUE evaluations ---
    tmp = tempfile.mkdtemp(prefix="bench_writeamp_")
    store, engine, write_counts = _make_tracked_engine(os.path.join(tmp, "test.db"))
    aid = store.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version": 1, "condition_id": "c1",
            "metric": "ltp", "operator": "gt", "value": 25000.0,
            "instrument": {"canonical_id": "NSE:EQUITY:I"}})
    engine.reload()

    q_above = _FakeQuote(26000.0)
    await engine.evaluate(q_above)  # UNKNOWN->TRUE, fires

    eval_counts = []
    for _ in range(1000):
        write_counts[0] = 0
        await engine.evaluate(q_above)  # TRUE->TRUE, no fire
        eval_counts.append(write_counts[0])

    total_writes = sum(eval_counts)
    rows.append({
        "scenario": "unchanged_state_1000_evals",
        "evaluations": 1000,
        "total_sql_writes": total_writes,
        "avg_writes_per_eval": round(total_writes / 1000, 4),
        "min_writes": min(eval_counts),
        "max_writes": max(eval_counts),
    })

    # --- Alternating TRUE/FALSE: 200 evaluations ---
    tmp2 = tempfile.mkdtemp(prefix="bench_writeamp_alt_")
    store2, engine2, write_counts2 = _make_tracked_engine(os.path.join(tmp2, "test.db"))
    aid2 = store2.create_condition_alert(
        consumer_id="c1", name="s", trigger_mode="repeat",
        condition_json={"condition_version": 1, "condition_id": "c1",
            "metric": "ltp", "operator": "gt", "value": 25000.0,
            "instrument": {"canonical_id": "NSE:EQUITY:I"}})
    engine2.reload()

    q_above2 = _FakeQuote(26000.0)
    q_below2 = _FakeQuote(24000.0)
    alt_counts = []
    for i in range(200):
        write_counts2[0] = 0
        q = q_above2 if i % 2 == 0 else q_below2
        await engine2.evaluate(q)
        alt_counts.append(write_counts2[0])

    total_alt = sum(alt_counts)
    rows.append({
        "scenario": "alternating_true_false_200_evals",
        "evaluations": 200,
        "total_sql_writes": total_alt,
        "avg_writes_per_eval": round(total_alt / 200, 4),
        "min_writes": min(alt_counts),
        "max_writes": max(alt_counts),
    })

    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(tmp2, ignore_errors=True)
    return {"rows": rows}
