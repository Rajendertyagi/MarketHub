"""Benchmark Part 18 — Concurrency measurements.

Concurrent quote updates to same alert and different alerts.
Verifies: one logical transition → one durable trigger.
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

    # --- Same alert, concurrent quotes ---
    tmp = tempfile.mkdtemp(prefix="bench_concur_")
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

    # Fire once to establish state
    await engine.evaluate(_FakeQuote(24000.0))  # FALSE
    await engine.evaluate(_FakeQuote(26000.0))  # TRUE, fires once

    # Now send 100 concurrent identical quotes
    q = _FakeQuote(26000.0)
    t0 = time.perf_counter_ns()
    results = await asyncio.gather(*[engine.evaluate(q) for _ in range(100)])
    total_time = (time.perf_counter_ns() - t0) / 1e6
    total_fired = sum(len(r) for r in results)
    a = store.get_condition_alert(aid)

    rows.append({
        "scenario": "same_alert_100_concurrent",
        "concurrent_tasks": 100,
        "total_fired": total_fired,
        "expected_fired": 0,  # TRUE→TRUE, no fire
        "correctness_ok": total_fired == 0,
        "total_time_ms": round(total_time, 2),
        "trigger_count": a["trigger_count"],
        "expected_trigger_count": 1,
    })
    print(f"  same alert 100 concurrent: fired={total_fired} triggers={a['trigger_count']}")
    shutil.rmtree(tmp, ignore_errors=True)

    # --- Different alerts, same quote ---
    tmp = tempfile.mkdtemp(prefix="bench_concur_")
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

    # Create 50 alerts on same instrument, all will fire
    for i in range(50):
        store.create_condition_alert(
            consumer_id="c1", name=f"a{i}", trigger_mode="repeat",
            condition_json={"condition_version":1,"condition_id":f"c{i}",
                "metric":"ltp","operator":"gt","value":20000.0+i*100,
                "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine.reload()

    q = _FakeQuote(26000.0)
    t0 = time.perf_counter_ns()
    results = await asyncio.gather(*[engine.evaluate(q) for _ in range(10)])
    total_time = (time.perf_counter_ns() - t0) / 1e6
    total_fired = sum(len(r) for r in results)

    rows.append({
        "scenario": "50_alerts_10_concurrent_samesimulate",
        "alerts": 50,
        "concurrent_tasks": 10,
        "total_fired": total_fired,
        "total_time_ms": round(total_time, 2),
    })
    print(f"  50 alerts 10 concurrent: fired={total_fired} time={total_time:.1f}ms")
    shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
