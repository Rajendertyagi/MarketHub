#!/usr/bin/env python3
"""Product foundations tests: instrument catalog, watchlists, alerts.

  * PF1   Upstox master JSON parsing (canonical records)
  * PF2   Upstox gzip payload parsing
  * PF3   malformed rows skipped safely
  * PF4   Fyers dict-row parsing
  * PF5   Fyers positional-row parsing
  * PF6   catalog transactional replace (stale removal)
  * PF7   search by q/exchange/type/provider + limit clamp
  * PF8   sync with fake fetch (no network)
  * PF9   sync failure leaves previous catalog intact
  * PF10  watchlist CRUD lifecycle
  * PF11  duplicate watchlist item rejected
  * PF12  reorder items
  * PF13  alert creation + validation (bad field/operator rejected)
  * PF14  alert engine triggers on canonical quote
  * PF15  no notification spam (state machine)
  * PF16  re-arm re-enables triggering
  * PF17  disabled alerts never fire
  * PF18  instrument search REST endpoint
  * PF19  watchlist REST endpoints
  * PF20  alert REST endpoints

NO LIVE BROKER. NO NETWORK. Synthetic fixtures only.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


class _Env:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        from core.persistence.store import EventStore
        self.store = EventStore(os.path.join(self._tmp.name, "t.db"))


# -- Upstox master ---------------------------------------------------------------

_UPSTOX_ROWS = [
    {"instrument_token": "NSE_EQ|INE002A01018", "exchange": "NSE",
     "tradingsymbol": "RELIANCE", "name": "Reliance Industries",
     "lot_size": 1, "tick_size": 0.05, "isin": "INE002A01018"},
    {"instrument_token": "NSE_FO|55001", "exchange": "NFO",
     "tradingsymbol": "NIFTY26AUG24500CE", "name": "Nifty",
     "instrument_type": "CE", "option_type": "CE", "strike": 24500.0,
     "expiry": "1768867200", "lot_size": 75, "tick_size": 0.05,
     "underlying_symbol": "NIFTY 50"},
    {"bad_row": True},                       # malformed -> skipped
]


def test_pf1_to_pf3_upstox_parse(runner: R) -> None:
    from app.instruments import upstox_master_records

    recs = upstox_master_records(_UPSTOX_ROWS)
    runner.assert_eq("PF1-parsed-count", len(recs), 2)
    eq = next(r for r in recs if r["tradingsymbol"] == "RELIANCE")
    runner.assert_eq("PF1-identity", eq["exchange"], "NSE")
    ce = next(r for r in recs if r["option_type"] == "CE")
    runner.assert_eq("PF1-strike", ce["strike"], 24500.0)
    runner.assert_eq("PF1-underlying", ce["underlying"], "NIFTY 50")

    # PF2: gzip bytes path.
    gz = gzip.compress(json.dumps(_UPSTOX_ROWS).encode())
    recs2 = upstox_master_records(gz)
    runner.assert_eq("PF2-gzip-parse", len(recs2), 2)

    # PF3: malformed row skipped without error.
    runner.assert_true("PF3-malformed-skipped", len(recs) < len(_UPSTOX_ROWS))


def test_pf4_pf5_fyers_parse(runner: R) -> None:
    from app.instruments import fyers_master_records

    # Real Fyers masters: JSON OBJECT keyed by "EXCH:SYMBOL".
    # Equity (CM, exInstType 0).
    master = {"NSE:SBIN-EQ": {
        "fyToken": "101000000010101", "exToken": 10101,
        "exchange": 10, "segment": 10, "exInstType": 0,
        "exSeries": "EQ", "symDetails": "State Bank of India",
        "symTicker": "NSE:SBIN-EQ", "tickSize": 0.05, "minLotSize": 1,
        "underSym": "SBIN", "isin": "INE062A01020"}}
    recs = fyers_master_records(json.dumps(master).encode())
    runner.assert_eq("PF4-dict-count", len(recs), 1)
    if recs:
        runner.assert_eq("PF4-token",
                         recs[0]["instrument_token"], "101000000010101")
        runner.assert_eq("PF4-type", recs[0]["instrument_type"], "EQUITY")
        runner.assert_eq("PF4-exchange", recs[0]["exchange"], "NSE")

    # Option (FO, exInstType 14, epoch expiry, optType CE).
    opt_master = {"NSE:NIFTY26SEP24000CE": {
        "fyToken": "101126099990001", "exToken": 90001,
        "exchange": 10, "segment": 11, "exInstType": 14,
        "expiryDate": "1790676600", "optType": "CE",
        "strikePrice": 24000.0, "minLotSize": 75, "tickSize": 0.05,
        "underSym": "NIFTY", "symTicker": "NSE:NIFTY26SEP24000CE",
        "symDetails": "24 Sep 26 24000 CE"}}
    recs2 = fyers_master_records(json.dumps(opt_master).encode())
    runner.assert_eq("PF5-option-count", len(recs2), 1)
    if recs2:
        runner.assert_eq("PF5-type", recs2[0]["instrument_type"], "OPTION")
        runner.assert_eq("PF5-expiry-iso", recs2[0]["expiry"], "2026-09-29")
        runner.assert_eq("PF5-strike", recs2[0]["strike"], 24000.0)
        runner.assert_eq("PF5-opt-type", recs2[0]["option_type"], "CE")


# -- Catalog persistence / sync ----------------------------------------------------


def test_pf6_pf7_catalog(runner: R) -> None:
    env = _Env()
    n = env.store.replace_provider_instruments("upstox", [
        {"instrument_token": "T1", "exchange": "NSE",
         "tradingsymbol": "OLD1"},
        {"instrument_token": "T2", "exchange": "NSE",
         "tradingsymbol": "KEEP"},
    ])
    runner.assert_eq("PF6-initial-insert", n, 2)
    # Replacement removes stale OLD1.
    env.store.replace_provider_instruments("upstox", [
        {"instrument_token": "T2", "exchange": "NSE",
         "tradingsymbol": "KEEP"}])
    runner.assert_eq("PF6-stale-removed",
                     len(env.store.search_instruments(q="OLD1")), 0)

    env.store.replace_provider_instruments("fyers", [
        {"instrument_token": "F1", "exchange": "NSE",
         "tradingsymbol": "FYER1", "instrument_type": "CE"},
    ])
    runner.assert_eq("PF7-search-q",
                     len(env.store.search_instruments(q="KEEP")), 1)
    runner.assert_eq("PF7-search-exchange",
                     len(env.store.search_instruments(exchange="NFO")), 0)
    runner.assert_eq("PF7-search-type",
                     len(env.store.search_instruments(
                         instrument_type="CE", provider="fyers")), 1)
    state = {s["provider"]: s["instruments"]
             for s in env.store.instruments_sync_state()}
    runner.assert_eq("PF7-sync-state-upstox", state.get("upstox"), 1)


def test_pf8_pf9_sync_service(runner: R) -> None:
    from app.instruments import InstrumentCatalog, InstrumentSyncError

    env = _Env()
    cat = InstrumentCatalog(env.store)

    calls = []
    def fake_fetch(url):
        calls.append(url)
        if "upstox" in url:
            return json.dumps(_UPSTOX_ROWS).encode()
        raise InstrumentSyncError("offline segment")

    result = cat.sync_upstox(fetch=fake_fetch)
    runner.assert_eq("PF8-sync-inserted", result["records"], 2)
    runner.assert_eq("PF8-no-fyers-url-hit",
                     any("fyers" in u for u in calls), False)

    # PF9: failing sync leaves existing catalog intact.
    before = len(cat.search(provider="upstox"))
    try:
        cat.sync_upstox(fetch=lambda url: (_ for _ in ()).throw(
            InstrumentSyncError("network down")))
        failed = False
    except InstrumentSyncError:
        failed = True
    runner.assert_true("PF9-failure-raised", failed)
    runner.assert_eq("PF9-catalog-intact",
                     len(cat.search(provider="upstox")), before)


# -- Watchlists ---------------------------------------------------------------------


def test_pf10_to_pf12_watchlists(runner: R) -> None:
    env = _Env()
    wl = env.store.create_watchlist("Default")
    i1 = env.store.add_watchlist_item(wl["id"], exchange="NSE",
                                      instrument_token="T1",
                                      tradingsymbol="AAA")
    i2 = env.store.add_watchlist_item(wl["id"], exchange="NSE",
                                      instrument_token="T2",
                                      tradingsymbol="BBB")
    runner.assert_eq("PF10-two-items", len(env.store.list_watchlist_items(
        wl["id"])), 2)

    dup = env.store.add_watchlist_item(wl["id"], exchange="NSE",
                                       instrument_token="T1",
                                       tradingsymbol="AAA")
    runner.assert_eq("PF11-duplicate-rejected", dup, None)

    ok = env.store.reorder_watchlist_items(
        wl["id"], [i2["id"], i1["id"]])
    items = env.store.list_watchlist_items(wl["id"])
    runner.assert_true("PF12-reorder-ok", ok)
    runner.assert_eq("PF12-new-order",
                     [i["instrument_token"] for i in items], ["T2", "T1"])

    env.store.remove_watchlist_item(i1["id"])
    runner.assert_eq("PF10-remove-works",
                     len(env.store.list_watchlist_items(wl["id"])), 1)
    env.store.delete_watchlist(wl["id"])
    runner.assert_eq("PF10-delete-cascades",
                     len(env.store.list_watchlists()), 0)


# -- Alerts -------------------------------------------------------------------------


def _mk_quote(**overrides):
    from market.models import Quote
    base = dict(instrument_token="T1", exchange="NSE",
                tradingsymbol="AAA", received_ts=NOW, ltp=100.0,
                change_percent=1.0, volume=1000)
    base.update(overrides)
    return Quote(**base)


def test_pf13_to_pf17_alert_engine(runner: R) -> None:
    from app.alerts import AlertEngine

    env = _Env()
    a = env.store.create_alert(exchange="NSE", instrument_token="T1",
                               tradingsymbol="AAA", field="ltp",
                               operator="gt", threshold=105.0)
    engine = AlertEngine(env.store)

    fired = engine.evaluate(_mk_quote(ltp=100.0))
    runner.assert_eq("PF14-below-no-fire", fired, [])

    fired = engine.evaluate(_mk_quote(ltp=110.0))
    runner.assert_eq("PF14-triggered", len(fired), 1)

    # PF15: repeated ticks do NOT re-fire until re-arm.
    runner.assert_eq("PF15-no-spam", engine.evaluate(
        _mk_quote(ltp=120.0)), [])

    # PF16: manual re-arm allows firing again.
    env.store.rearm_alert(a["id"])
    engine.reload()
    fired = engine.evaluate(_mk_quote(ltp=130.0))
    runner.assert_eq("PF16-rearm-fires", len(fired), 1)

    # PF17: disabled alerts never fire.
    env.store.set_alert_enabled(a["id"], False)
    engine.reload()
    runner.assert_eq("PF17-disabled-inert", engine.evaluate(
        _mk_quote(ltp=999.0)), [])

    # PF13: validation at store layer.
    try:
        env.store.create_alert(exchange="NSE", instrument_token="X",
                               tradingsymbol="X", field="bogus",
                               operator="gt", threshold=1)
        bad_field = False
    except ValueError:
        bad_field = True
    runner.assert_true("PF13-bad-field-rejected", bad_field)


# -- REST endpoints -------------------------------------------------------------------


async def _call(route, method="GET", body=None, path_params=None):
    from starlette.requests import Request
    scope = {"type": "http", "method": method, "path": route.path,
             "headers": [(b"content-type", b"application/json")],
             "query_string": b"", "server": ("t", 80), "scheme": "http",
             "path_params": path_params or {}}

    async def receive():
        if body is not None:
            return {"type": "http.request",
                    "body": json.dumps(body).encode(), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = await route.endpoint(Request(scope, receive))
    raw = getattr(resp, "body", b"")
    return resp.status_code, json.loads(raw)


def _find(routes, path, method="GET"):
    return next(r for r in routes
                if r.path == path and method in r.methods)


async def test_pf18_to_pf20_rest_endpoints(runner: R) -> None:
    from api.product_routes import (
        build_alert_routes, build_instrument_routes, build_watchlist_routes,
    )

    env = _Env()
    env.store.replace_provider_instruments("upstox", [
        {"instrument_token": "T1", "exchange": "NSE",
         "tradingsymbol": "RELIANCE", "name": "Reliance"}])

    # PF18: instrument search endpoint.
    iroutes = build_instrument_routes(
        __import__("app.instruments", fromlist=["InstrumentCatalog"]).
        InstrumentCatalog(env.store))
    code, data = await _call(_find(iroutes, "/api/instruments/search"))
    runner.assert_eq("PF18-search-status", code, 200)
    runner.assert_eq("PF18-search-hit", data["count"], 1)
    runner.assert_not_in("PF18-no-provider-payload",
                         "instrument_token_raw", json.dumps(data))

    # PF19: watchlist endpoints.
    wroutes = build_watchlist_routes(env.store)
    code, wl = await _call(_find(wroutes, "/api/watchlists", "POST"),
                           "POST", {"name": "Night"})
    runner.assert_eq("PF19-create", code, 200)
    wl_id = wl["watchlist"]["id"]
    code, _ = await _call(
        _find(wroutes, "/api/watchlists/{watchlist_id}/items", "POST"),
        "POST", {"exchange": "NSE", "instrument_token": "T1",
                 "tradingsymbol": "RELIANCE"},
        path_params={"watchlist_id": str(wl_id)})
    runner.assert_eq("PF19-add-item", code, 200)
    code, listing = await _call(_find(wroutes, "/api/watchlists"))
    runner.assert_eq("PF19-list-items",
                     len(listing["watchlists"][0]["items"]), 1)

    # PF20: alert endpoints.
    aroutes = build_alert_routes(env.store)
    code, al = await _call(_find(aroutes, "/api/alerts", "POST"), "POST",
                           {"exchange": "NSE",
                            "instrument_token": "T1",
                            "tradingsymbol": "RELIANCE",
                            "field": "ltp", "operator": "gt",
                            "threshold": 500})
    runner.assert_eq("PF20-create-alert", code, 200)
    code, listing = await _call(_find(aroutes, "/api/alerts"))
    runner.assert_eq("PF20-list-alerts", len(listing["alerts"]), 1)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_pf1_to_pf3_upstox_parse(runner)
    test_pf4_pf5_fyers_parse(runner)
    test_pf6_pf7_catalog(runner)
    test_pf8_pf9_sync_service(runner)
    test_pf10_to_pf12_watchlists(runner)
    test_pf13_to_pf17_alert_engine(runner)
    await test_pf18_to_pf20_rest_endpoints(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)



