"""Benchmark Part 11 — Memory measurement.

Uses tracemalloc to measure incremental memory for:
  baseline, 100, 1000, 5000 alerts.
Then delete/disable and measure retained memory.
"""
from __future__ import annotations
import os
import sys
import tempfile
import shutil
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracemalloc

from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


def _get_traced_mb():
    if tracemalloc.is_tracing():
        return round(tracemalloc.get_traced_memory()[0] / 1024 / 1024, 4)
    return None


def _get_current_mb():
    if tracemalloc.is_tracing():
        return round(tracemalloc.get_traced_memory()[1] / 1024 / 1024, 4)
    return None


async def run():
    rows = []
    tracemalloc.start()
    baseline = _get_traced_mb()

    counts = [100, 1000, 5000]
    engines = []
    tmps = []

    for n in counts:
        tmp = tempfile.mkdtemp(prefix=f"bench_mem_{n}_")
        tmps.append(tmp)
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
        for i in range(n):
            store.create_condition_alert(
                consumer_id="c1", name=f"a{i}", trigger_mode="repeat",
                condition_json={"condition_version": 1, "condition_id": f"c{i}",
                    "metric": "ltp", "operator": "gt", "value": 20000.0 + i * 10,
                    "instrument": {"canonical_id": "NSE:EQUITY:I"}})
        engine.reload()
        engines.append(engine)

        from bench_quote_eval import _FakeQuote
        q = _FakeQuote(30000.0)
        await engine.evaluate(q)

        rows.append({
            "scenario": f"create_{n}_alerts",
            "alert_count": n,
            "dep_index_entries": sum(len(v) for v in engine._dep_index.values()),
            "dep_index_keys": len(engine._dep_index),
            "alerts_dict_size": len(engine._alerts),
            "alert_deps_size": len(engine._alert_deps),
            "alert_locks_size": len(engine._alert_locks),
            "dep_last_values_size": len(engine._dep_last_values),
            "state_entries": len(engine._state),
            "tracemalloc_mb": _get_traced_mb(),
            "tracemalloc_peak_mb": _get_current_mb(),
            "baseline_mb": baseline,
            "allocated_delta_mb": round((_get_traced_mb() or 0) - (baseline or 0), 4),
        })
        print(f"  {n} alerts: locks={len(engine._alert_locks)} dep_last={len(engine._dep_last_values)}")

    for idx, (engine, n) in enumerate(zip(engines, counts)):
        store = engine._store
        alerts = store.list_condition_alerts("c1")
        to_delete = alerts[:n // 2]
        for a in to_delete:
            store.delete_condition_alert(a["alert_id"])
        engine.reload()
        rows.append({
            "scenario": f"delete_half_{n}_alerts",
            "remaining_alerts": len(engine._alerts),
            "dep_index_entries": sum(len(v) for v in engine._dep_index.values()),
            "dep_index_keys": len(engine._dep_index),
            "alert_deps_size": len(engine._alert_deps),
            "alert_locks_size": len(engine._alert_locks),
            "dep_last_values_size": len(engine._dep_last_values),
            "state_entries": len(engine._state),
            "tracemalloc_mb": _get_traced_mb(),
            "tracemalloc_peak_mb": _get_current_mb(),
        })
        print(f"  delete half {n}: locks={len(engine._alert_locks)}")

    for idx, (engine, n) in enumerate(zip(engines, counts)):
        store = engine._store
        alerts = store.list_condition_alerts("c1")
        to_disable = alerts[:max(1, len(alerts) // 4)]
        for a in to_disable:
            store.set_condition_alert_enabled(a["alert_id"], False)
        engine.reload()
        rows.append({
            "scenario": f"disable_quarter_{n}_alerts",
            "remaining_alerts": len(engine._alerts),
            "dep_index_entries": sum(len(v) for v in engine._dep_index.values()),
            "dep_index_keys": len(engine._dep_index),
            "alert_locks_size": len(engine._alert_locks),
            "dep_last_values_size": len(engine._dep_last_values),
            "tracemalloc_mb": _get_traced_mb(),
            "tracemalloc_peak_mb": _get_current_mb(),
        })
        print(f"  disable quarter {n}: locks={len(engine._alert_locks)}")

    tracemalloc.stop()
    for tmp in tmps:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
