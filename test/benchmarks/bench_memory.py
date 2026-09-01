"""Benchmark Part 11 — Memory measurement.

Uses tracemalloc to measure incremental memory for:
  baseline, 1000, 5000, 10000 alerts
Then delete/disable and measure retained memory.
"""
from __future__ import annotations
import os
import sys
import tempfile
import shutil
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tracemalloc

from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


def _get_mem_mb():
    """Return (rss_mb, tracemalloc_mb) or (None, None) if tracemalloc not active."""
    import os as _os
    rss = None
    try:
        with open(f"/proc/{_os.getpid()}/statm") as f:
            pages = int(f.read().split()[0])
            rss = pages * _os.sysconf("SC_PAGE_SIZE") / 1024 / 1024
    except Exception:
        pass
    tm = None
    if tracemalloc.is_tracing():
        tm = tracemalloc.get_traced_memory()[0] / 1024 / 1024
    return rss, tm


async def run():
    rows = []
    tracemalloc.start()
    baseline_rss, baseline_tm = _get_mem_mb()

    counts = [100, 1000, 5000]
    store_paths = []
    engines = []

    for n in counts:
        tmp = tempfile.mkdtemp(prefix=f"bench_mem_{n}_")
        store_paths.append(tmp)
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
                condition_json={"condition_version":1,"condition_id":f"c{i}",
                    "metric":"ltp","operator":"gt","value":20000.0+i*10,
                    "instrument":{"canonical_id":"NSE:EQUITY:I"}})
        engine.reload()
        engines.append(engine)
        # Fire a few to populate transient state
        from test.benchmarks.bench_quote_eval import _FakeQuote
        q = _FakeQuote(30000.0)
        await engine.evaluate(q)
        rss, tm = _get_mem_mb()
        rows.append({
            "scenario": f"create_{n}_alerts",
            "alert_count": n,
            "dep_index_size": len(engine._dep_index),
            "alert_deps_size": len(engine._alert_deps),
            "alerts_dict_size": len(engine._alerts),
            "state_dict_size": len(engine._state),
            "dep_last_values_size": len(engine._dep_last_values),
            "analytics_seen_size": len(engine._analytics_seen),
            "analytics_snapshot_size": len(engine._analytics_last_snapshot),
            "alert_locks_size": len(engine._alert_locks),
            "rss_mb": round(rss, 2) if rss else None,
            "tracemalloc_mb": round(tm, 2) if tm else None,
        })
        print(f"  {n} alerts: dep_index={len(engine._dep_index)} locks={len(engine._alert_locks)} dep_last={len(engine._dep_last_values)}")

    # Test delete cleanup
    for idx, (engine, n) in enumerate(zip(engines, counts)):
        # Delete half the alerts
        store = engine._store
        alerts = store.list_condition_alerts("c1")
        to_delete = alerts[:n//2]
        for a in to_delete:
            store.delete_condition_alert(a["alert_id"])
        engine.reload()
        rss, tm = _get_mem_mb()
        rows.append({
            "scenario": f"delete_half_{n}_alerts",
            "remaining_alerts": len(engine._alerts),
            "dep_index_size": len(engine._dep_index),
            "alert_deps_size": len(engine._alert_deps),
            "dep_last_values_size": len(engine._dep_last_values),
            "analytics_seen_size": len(engine._analytics_seen),
            "analytics_snapshot_size": len(engine._analytics_last_snapshot),
            "alert_locks_size": len(engine._alert_locks),
            "rss_mb": round(rss, 2) if rss else None,
            "tracemalloc_mb": round(tm, 2) if tm else None,
        })
        print(f"  after delete half {n}: locks={len(engine._alert_locks)} dep_last={len(engine._dep_last_values)}")

    # Test disable cleanup
    for idx, (engine, n) in enumerate(engines):
        store = engine._store
        alerts = store.list_condition_alerts("c1")
        to_disable = alerts[:n//4]
        for a in to_disable:
            store.set_condition_alert_enabled(a["alert_id"], False)
        engine.reload()
        rss, tm = _get_mem_mb()
        rows.append({
            "scenario": f"disable_quarter_{n}_alerts",
            "remaining_alerts": len(engine._alerts),
            "dep_index_size": len(engine._dep_index),
            "alert_locks_size": len(engine._alert_locks),
            "rss_mb": round(rss, 2) if rss else None,
            "tracemalloc_mb": round(tm, 2) if tm else None,
        })
        print(f"  after disable quarter {n}: locks={len(engine._alert_locks)}")

    tracemalloc.stop()

    # Cleanup
    for tmp in store_paths:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"rows": rows}
