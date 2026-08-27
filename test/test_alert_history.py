#!/usr/bin/env python3
"""Alert trigger history regression tests (AH1-AH9).

Covers the SQLite-backed durable alert trigger history (P2):
  * AH1  fresh DB (v12) has the table; a v11 DB migrates to v12
  * AH2  record + list returns correct fields
  * AH3  newest-first ordering + limit/offset pagination
  * AH4  alert_id + provider filters
  * AH5  clear one alert vs clear all
  * AH6  restart (reopen) preserves history
  * AH7  backup_to automatically includes history
  * AH8  AlertEngine.evaluate records a history row
  * AH9  API: bounded/validated query params (400 on invalid)

NO LIVE BROKER. Run: python test/test_alert_history.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402


def _tmp_db():
    d = tempfile.mkdtemp()
    return os.path.join(d, "alerts.db")


async def test_ah1_migration(runner: R) -> None:
    """Fresh v12 DB has the table; a v11 DB migrates to v12."""
    from core.persistence.store import EventStore

    # Fresh DB at current schema version must expose the table.
    p = _tmp_db()
    s = EventStore(p)
    runner.assert_eq("AH1-fresh-table-empty", s.list_alert_trigger_history(), [])
    del s

    # A pre-existing v11 DB must migrate forward to v12 and gain the table.
    p2 = _tmp_db()
    conn = sqlite3.connect(p2)
    from core.persistence.modules.products import create_product_tables
    create_product_tables(conn)
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()
    s2 = EventStore(p2)
    runner.assert_eq("AH1-migrate-table-empty", s2.list_alert_trigger_history(), [])
    del s2


async def test_ah2_record_list_fields(runner: R) -> None:
    from core.persistence.store import EventStore

    s = EventStore(_tmp_db())
    s.record_alert_trigger_history(
        alert_id=7, exchange="NSE", instrument_token="INE009",
        tradingsymbol="TEST9", field="ltp", operator="gt",
        threshold=10.0, observed_value=12.5, provider="upstox")
    rows = s.list_alert_trigger_history()
    runner.assert_eq("AH2-one-row", len(rows), 1)
    r = rows[0]
    runner.assert_eq("AH2-tradingsymbol", r["tradingsymbol"], "TEST9")
    runner.assert_eq("AH2-observed", r["observed_value"], 12.5)
    runner.assert_eq("AH2-provider", r["provider"], "upstox")
    runner.assert_eq("AH2-alert-id", r["alert_id"], 7)
    runner.assert_true("AH2-triggered_at", bool(r["triggered_at"]))


async def test_ah3_ordering_pagination(runner: R) -> None:
    from core.persistence.store import EventStore

    s = EventStore(_tmp_db())
    for i in range(5):
        s.record_alert_trigger_history(
            alert_id=i, exchange="NSE", instrument_token=f"T{i}",
            tradingsymbol=f"S{i}", field="ltp", operator="gt",
            threshold=1.0, observed_value=float(i), provider="upstox")
    all_rows = s.list_alert_trigger_history()
    runner.assert_eq("AH3-count", len(all_rows), 5)
    # newest first: highest alert_id first
    runner.assert_eq("AH3-newest-first", all_rows[0]["alert_id"], 4)
    runner.assert_eq("AH3-last", all_rows[-1]["alert_id"], 0)
    # pagination
    page = s.list_alert_trigger_history(limit=2, offset=1)
    runner.assert_eq("AH3-page-len", len(page), 2)
    runner.assert_eq("AH3-page-offset", page[0]["alert_id"], 3)
    # limit clamping (store-level defensive)
    big = s.list_alert_trigger_history(limit=1000)
    runner.assert_eq("AH3-clamp", len(big), 5)


async def test_ah4_filters(runner: R) -> None:
    from core.persistence.store import EventStore

    s = EventStore(_tmp_db())
    s.record_alert_trigger_history(1, "NSE", "T1", "S1", "ltp", "gt",
                                   1.0, 2.0, "upstox")
    s.record_alert_trigger_history(1, "NSE", "T1", "S1", "ltp", "gt",
                                   1.0, 3.0, "upstox")
    s.record_alert_trigger_history(2, "BSE", "T2", "S2", "ltp", "lt",
                                   1.0, 0.5, "fyers")
    runner.assert_eq("AH4-by-alert", len(s.list_alert_trigger_history(alert_id=1)), 2)
    runner.assert_eq("AH4-by-provider", len(s.list_alert_trigger_history(provider="fyers")), 1)
    runner.assert_eq("AH4-combined", len(s.list_alert_trigger_history(alert_id=1, provider="upstox")), 2)
    runner.assert_eq("AH4-none", len(s.list_alert_trigger_history(alert_id=99)), 0)


async def test_ah5_clear(runner: R) -> None:
    from core.persistence.store import EventStore

    s = EventStore(_tmp_db())
    s.record_alert_trigger_history(1, "NSE", "T1", "S1", "ltp", "gt", 1.0, 2.0, "upstox")
    s.record_alert_trigger_history(2, "BSE", "T2", "S2", "ltp", "lt", 1.0, 0.5, "fyers")
    deleted = s.clear_alert_trigger_history(alert_id=1)
    runner.assert_eq("AH5-clear-one", deleted, 1)
    runner.assert_eq("AH5-remaining", s.count_alert_trigger_history(), 1)
    deleted_all = s.clear_alert_trigger_history()
    runner.assert_eq("AH5-clear-all", deleted_all, 1)
    runner.assert_eq("AH5-empty", s.count_alert_trigger_history(), 0)


async def test_ah6_restart_preserves(runner: R) -> None:
    from core.persistence.store import EventStore

    p = _tmp_db()
    s = EventStore(p)
    s.record_alert_trigger_history(1, "NSE", "T1", "S1", "ltp", "gt", 1.0, 2.0, "upstox")
    del s
    # Simulate a process restart: new EventStore on the SAME path.
    s2 = EventStore(p)
    runner.assert_eq("AH6-preserved", s2.count_alert_trigger_history(), 1)
    runner.assert_eq("AH6-value", s2.list_alert_trigger_history()[0]["observed_value"], 2.0)


async def test_ah7_backup_includes(runner: R) -> None:
    from core.persistence.store import EventStore

    p = _tmp_db()
    s = EventStore(p)
    s.record_alert_trigger_history(1, "NSE", "T1", "S1", "ltp", "gt", 1.0, 2.0, "upstox")
    backup = p + ".bak"
    s.backup_to(backup)
    s2 = EventStore(backup)
    runner.assert_eq("AH7-backup-has-history", s2.count_alert_trigger_history(), 1)


async def test_ah8_engine_integration(runner: R) -> None:
    """AlertEngine.evaluate must record a history row on trigger."""
    from core.persistence.store import EventStore
    from app.alerts import AlertEngine

    p = _tmp_db()
    s = EventStore(p)
    s.create_market_alert(exchange="NSE", instrument_token="INE009",
                   tradingsymbol="TEST9", field="ltp", operator="gt",
                   threshold=10.0)

    class _Quote:
        exchange = "NSE"
        instrument_token = "INE009"
        ltp = 15.0
        provider = "upstox"

    engine = AlertEngine(s)
    fired = engine.evaluate(_Quote())
    runner.assert_eq("AH8-fired", len(fired), 1)
    rows = s.list_alert_trigger_history()
    runner.assert_eq("AH8-history-recorded", len(rows), 1)
    runner.assert_eq("AH8-observed", rows[0]["observed_value"], 15.0)
    runner.assert_eq("AH8-tradingsymbol", rows[0]["tradingsymbol"], "TEST9")
    runner.assert_eq("AH8-provider", rows[0]["provider"], "upstox")
    # Re-evaluate same quote: already triggered -> no duplicate history.
    engine.evaluate(_Quote())
    runner.assert_eq("AH8-no-dup", s.count_alert_trigger_history(), 1)


async def test_ah9_api_validation(runner: R) -> None:
    """Bounded/validated query params; 400 on invalid limit/offset."""
    from core.persistence.store import EventStore
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from api.product_routes import build_alert_history_routes

    p = _tmp_db()
    s = EventStore(p)
    s.record_alert_trigger_history(1, "NSE", "T1", "S1", "ltp", "gt", 1.0, 2.0, "upstox")

    app = Starlette(routes=build_alert_history_routes(s))
    client = TestClient(app)

    ok = client.get("/api/alerts/history?limit=10&offset=0")
    runner.assert_eq("AH9-ok-status", ok.status_code, 200)
    body = ok.json()
    runner.assert_eq("AH9-ok-total", body["total"], 1)
    runner.assert_eq("AH9-ok-limit", body["limit"], 10)

    bad_limit = client.get("/api/alerts/history?limit=99999")
    runner.assert_eq("AH9-bad-limit-400", bad_limit.status_code, 400)

    bad_offset = client.get("/api/alerts/history?offset=-3")
    runner.assert_eq("AH9-bad-offset-400", bad_offset.status_code, 400)

    bad_alert = client.get("/api/alerts/history?alert_id=abc")
    runner.assert_eq("AH9-bad-alert-400", bad_alert.status_code, 400)

    cleared = client.delete("/api/alerts/history")
    runner.assert_eq("AH9-clear-status", cleared.status_code, 200)
    runner.assert_eq("AH9-clear-deleted", cleared.json()["deleted"], 1)


async def main() -> bool:
    runner = R()
    await test_ah1_migration(runner)
    await test_ah2_record_list_fields(runner)
    await test_ah3_ordering_pagination(runner)
    await test_ah4_filters(runner)
    await test_ah5_clear(runner)
    await test_ah6_restart_preserves(runner)
    await test_ah7_backup_includes(runner)
    await test_ah8_engine_integration(runner)
    await test_ah9_api_validation(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
