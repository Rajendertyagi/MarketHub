"""Benchmark Part 12 — Transient state cleanup on delete/disable.

Directly inspects sizes of internal dicts after operations.
"""
from __future__ import annotations
import os
import sys
import tempfile
import shutil

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


class _FakeQuote:
    def __init__(self, ltp):
        self.ltp = ltp
        self.volume = ltp * 100
        self.exchange = "NSE"
        self.instrument_token = "T"
        self.tradingsymbol = "SYM"
        self.provider = "upstox"


async def run():
    rows = []
    n = 5000

    tmp = tempfile.mkdtemp(prefix="bench_cleanup_")
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

    # Create alerts
    for i in range(n):
        store.create_condition_alert(
            consumer_id="c1", name=f"a{i}", trigger_mode="repeat",
            condition_json={"condition_version":1,"condition_id":f"c{i}",
                "metric":"ltp","operator":"gt","value":20000.0+i*10,
                "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine.reload()

    # Populate transient state via evaluation
    q = _FakeQuote(30000.0)
    for _ in range(10):
        await engine.evaluate(q)

    rows.append({
        "scenario": "before_any_cleanup",
        "n_created": n,
        "_alerts": len(engine._alerts),
        "_dep_index_entries": sum(len(v) for v in engine._dep_index.values()),
        "_dep_index_keys": len(engine._dep_index),
        "_alert_deps": len(engine._alert_deps),
        "_alert_locks": len(engine._alert_locks),
        "_dep_last_values": len(engine._dep_last_values),
        "_analytics_seen": len(engine._analytics_seen),
        "_analytics_last_snapshot": len(engine._analytics_last_snapshot),
        "_state_entries": len(engine._state),
    })

    # DELETE all alerts
    alerts = store.list_condition_alerts("c1")
    for a in alerts:
        store.delete_condition_alert(a["alert_id"])
    engine.reload()

    rows.append({
        "scenario": "after_delete_all_reload",
        "_alerts": len(engine._alerts),
        "_dep_index_entries": sum(len(v) for v in engine._dep_index.values()),
        "_dep_index_keys": len(engine._dep_index),
        "_alert_deps": len(engine._alert_deps),
        "_alert_locks": len(engine._alert_locks),
        "_dep_last_values": len(engine._dep_last_values),
        "_analytics_seen": len(engine._analytics_seen),
        "_analytics_last_snapshot": len(engine._analytics_last_snapshot),
        "_state_entries": len(engine._state),
    })

    # Test: create then DISABLE (not delete)
    for i in range(100):
        store.create_condition_alert(
            consumer_id="c1", name=f"b{i}", trigger_mode="repeat",
            condition_json={"condition_version":1,"condition_id":f"cb{i}",
                "metric":"ltp","operator":"gt","value":20000.0+i*10,
                "instrument":{"canonical_id":"NSE:EQUITY:I"}})
    engine.reload()
    alerts = store.list_condition_alerts("c1")
    for a in alerts:
        store.set_condition_alert_enabled(a["alert_id"], False)
    engine.reload()

    rows.append({
        "scenario": "after_disable_all_reload",
        "_alerts": len(engine._alerts),
        "_dep_index_entries": sum(len(v) for v in engine._dep_index.values()),
        "_dep_index_keys": len(engine._dep_index),
        "_alert_deps": len(engine._alert_deps),
        "_alert_locks": len(engine._alert_locks),
        "_dep_last_values": len(engine._dep_last_values),
        "_analytics_seen": len(engine._analytics_seen),
        "_analytics_last_snapshot": len(engine._analytics_last_snapshot),
        "_state_entries": len(engine._state),
    })

    shutil.rmtree(tmp, ignore_errors=True)
    return {"rows": rows}
