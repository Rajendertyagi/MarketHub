#!/usr/bin/env python3
"""WebUI-controlled instrument catalog segment tests.

Synthetic masters only (no network). Covers:

  PREFERENCES
   1. no preference -> minimal four (NSE_EQ/NSE_FO/NSE_INDEX/BSE_INDEX)
   2. saved round-trip + idempotent save
   3. saved preference survives restart (fresh store over same DB)
   4. invalid segment rejected honestly (API + service)

  UPSTOX FILTER (parse -> segment filter -> transactional replace)
   5. defaults keep NSE_EQ/NSE_FO/NSE_INDEX/BSE_INDEX
   6. defaults filter NSE_COM/MCX_FO/BSE_EQ/BSE_FO/BCD_FO/NCD_FO
   7. enabling a segment brings it in on re-sync
   8. disabling removes it on re-sync
   9. counts (parsed/kept/filtered/segments) correct
  10. repeated sync idempotent
  11. failed sync preserves previous catalog (download error)

  FYERS (derived per-row segment — the key regression)
  12. NSE_EQ OFF + NSE_INDEX ON removes equities but KEEPS NIFTY index rows
  13. provider identity preserved

  API
  14. GET /api/instruments/segments shape
  15. PUT round-trip + invalid
  16. sync honors preference

  COLLISIONS
  17. ambiguous identity remains ambiguous (resolver semantics unchanged)
  18. register_catalog_rows summary bounded (no per-row warnings)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

from core.persistence.store import EventStore  # noqa: E402
from app.instruments import (  # noqa: E402
    DEFAULT_SEGMENTS,
    InstrumentCatalog,
    InstrumentSyncError,
)
from api.product_routes import build_instrument_routes  # noqa: E402
from starlette.requests import Request  # noqa: E402


def _u(key, seg, exch, itype, sym, **kw):
    base = {"instrument_key": key, "exchange": exch, "segment": seg,
            "instrument_type": itype, "trading_symbol": sym,
            "underlying_symbol": kw.get("und", sym.split()[0]),
            "expiry": kw.get("expiry"), "strike_price": kw.get("strike"),
            "lot_size": 1, "tick_size": 0.05}
    if kw.get("isin"):
        base["isin"] = kw["isin"]
    return base


def _master_rows():
    ms = 86400000
    return [
        # NSE equity
        _u("NSE_EQ|INE002A01018", "NSE_EQ", "NSE", "EQ", "RELIANCE",
           isin="INE002A01018"),
        # NSE F&O
        _u("NSE_FO|90001", "NSE_FO", "NSE", "FUT", "RELIANCE FUT",
           expiry=1790706599000),
        _u("NSE_FO|90010", "NSE_FO", "NSE", "CE", "RELIANCE 1500 CE",
           expiry=1790706599000, strike=1500.0),
        # NSE index
        _u("NSE_INDEX|Nifty 50", "NSE_INDEX", "NSE", "INDEX", "Nifty 50"),
        # NSE commodities (must be filtered by default)
        _u("NSE_COM|144545", "NSE_COM", "NSE", "CE", "GOLD 147000 CE",
           expiry=1790706599000, strike=147000.0),
        # MCX (filtered by default)
        _u("MCX_FO|569467", "MCX_FO", "MCX", "CE", "GOLD MCX CE",
           expiry=1790706599000, strike=147000.0),
        # BSE equity + FO (filtered by default)
        _u("BSE_EQ|INE002A01018", "BSE_EQ", "BSE", "EQ", "RELIANCE",
           isin="INE002A01018"),
        _u("BSE_FO|1", "BSE_FO", "BSE", "FUT", "RELIANCE BSE FUT",
           expiry=1790706599000),
        # BSE index (kept by default — SENSEX/BANKEX live here)
        _u("BSE_INDEX|SENSEX", "BSE_INDEX", "BSE", "INDEX", "SENSEX"),
        # BCD/NCD (filtered by default)
        _u("BCD_FO|1", "BCD_FO", "BCD", "FUT", "BCD THING",
           expiry=1790706599000),
        _u("NCD_FO|1", "NCD_FO", "NCD", "FUT", "NCD THING",
           expiry=1790706599000),
    ]


def _fyers_master():
    """RAW Fyers master (EXCH:SYMBOL-keyed object) — real parser input."""
    return {
        "NSE:SBIN-EQ": {
            "fyToken": "101", "exchange": 10, "segment": 10,
            "exInstType": 0, "symTicker": "NSE:SBIN-EQ",
            "symDetails": "State Bank", "tickSize": 0.05,
            "minLotSize": 1, "isin": "INE062A01020", "underSym": "SBIN"},
        "NSE:NIFTY50-INDEX": {
            "fyToken": "102", "exchange": 10, "segment": 10,
            "exInstType": 10, "symTicker": "NSE:NIFTY50-INDEX",
            "symDetails": "NIFTY 50", "tickSize": 0.05, "minLotSize": 1,
            "underSym": "NIFTY"},
        "NSE:NIFTY2690823800CE": {
            "fyToken": "103", "exchange": 10, "segment": 11,
            "exInstType": 14, "symTicker": "NSE:NIFTY2690823800CE",
            "symDetails": "NIFTY", "tickSize": 0.05, "minLotSize": 65,
            "optType": "CE", "strikePrice": 23800.0,
            "expiryDate": "1788892199", "underSym": "NIFTY"},
    }


class _Env:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "t.db")
        self.store = EventStore(self.path)
        self.catalog = InstrumentCatalog(self.store)

    def reopen(self):
        """Simulate restart: fresh store over the same file."""
        self.store = EventStore(self.path)
        self.catalog = InstrumentCatalog(self.store)
        return self.catalog


# -- preferences ---------------------------------------------------------------

def test_preferences(runner: R) -> None:
    env = _Env()
    # 1. default minimal four
    enabled = env.catalog.get_enabled_segments()
    runner.assert_eq("pref-defaults", enabled, set(DEFAULT_SEGMENTS))
    runner.assert_eq("pref-default-set", enabled,
                     {"NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX"})
    # 2. round-trip + idempotent
    saved = env.catalog.set_enabled_segments(
        ["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX", "MCX_FO"])
    runner.assert_eq("pref-saved", saved,
                     {"NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX", "MCX_FO"})
    runner.assert_eq("pref-idempotent",
                     env.catalog.set_enabled_segments(
                         ["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX",
                          "MCX_FO"]),
                     {"NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX", "MCX_FO"})
    # 3. survives restart
    cat = env.reopen()
    runner.assert_eq("pref-restart", cat.get_enabled_segments(),
                     {"NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX", "MCX_FO"})
    # existing preference not overwritten by defaults logic
    runner.assert_eq("pref-not-overwritten",
                     cat.get_enabled_segments() != set(DEFAULT_SEGMENTS),
                     True)
    # invalid: empty list rejected
    try:
        env.catalog.set_enabled_segments([])
        ok = False
    except ValueError:
        ok = True
    runner.assert_true("pref-empty-400", ok)


# -- upstox filter ---------------------------------------------------------------

def test_upstox_filter(runner: R) -> None:
    env = _Env()
    cat = env.catalog
    # sync with defaults via the real sync path (fetch faked)
    result = cat.sync_upstox(fetch=lambda url: json.dumps(
        _master_rows()).encode())
    runner.assert_eq("ux-parsed", result["parsed"], 11)
    runner.assert_eq("ux-kept", result["kept"], 5)
    runner.assert_eq("ux-filtered", result["filtered"], 6)
    segs = result["segments"]
    runner.assert_eq("ux-nse-eq", segs.get("NSE_EQ"), 1)
    runner.assert_eq("ux-nse-fo", segs.get("NSE_FO"), 2)
    runner.assert_eq("ux-nse-index", segs.get("NSE_INDEX"), 1)
    runner.assert_eq("ux-bse-index", segs.get("BSE_INDEX"), 1)
    for absent in ("NSE_COM", "MCX_FO", "BSE_EQ", "BSE_FO", "BCD_FO",
                   "NCD_FO"):
        runner.assert_eq(f"ux-absent-{absent}", segs.get(absent), None)
    # catalog state
    counts = cat.segment_counts()
    runner.assert_eq("cat-nse-com-gone", counts.get("NSE_COM"), None)
    runner.assert_eq("cat-mcx-gone", counts.get("MCX_FO"), None)
    rows = cat.search(q="GOLD", limit=10)
    runner.assert_eq("cat-gold-gone", len(rows), 0)
    nifty = cat.search(q="Nifty 50", limit=5)
    runner.assert_eq("cat-nifty-kept", len(nifty), 1)
    # 7. enable MCX_FO -> appears on re-sync
    cat.set_enabled_segments(["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX",
                              "MCX_FO"])
    r2 = cat.sync_upstox(fetch=lambda url: json.dumps(
        _master_rows()).encode())
    runner.assert_eq("ux-enable-mcx", r2["segments"].get("MCX_FO"), 1)
    # 8. disable again -> removed on re-sync
    cat.set_enabled_segments(["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX"])
    r3 = cat.sync_upstox(fetch=lambda url: json.dumps(
        _master_rows()).encode())
    runner.assert_eq("ux-disable-mcx", r3["segments"].get("MCX_FO"), None)
    # 10. idempotent repeat
    r4 = cat.sync_upstox(fetch=lambda url: json.dumps(
        _master_rows()).encode())
    runner.assert_eq("ux-repeat", (r4["kept"], r4["filtered"]),
                     (r3["kept"], r3["filtered"]))
    # 11. failed sync preserves previous catalog
    def _boom(url):
        raise InstrumentSyncError("download failed")
    try:
        cat.sync_upstox(fetch=_boom)
        ok = False
    except InstrumentSyncError:
        ok = True
    runner.assert_true("ux-fail-raised", ok)
    counts_after = cat.segment_counts()
    runner.assert_eq("ux-fail-preserved", counts_after.get("NSE_FO"),
                     r4["segments"].get("NSE_FO"))


def test_fyers_derived_filter(runner: R) -> None:
    """KEY REGRESSION: NSE_EQ OFF + NSE_INDEX ON keeps NIFTY, drops equities."""
    env = _Env()
    cat = env.catalog
    # Fyers rows derive their segment; feed through the sync path with
    # defaults (NSE_EQ off? no — defaults KEEP NSE_EQ; disable it here).
    cat.set_enabled_segments(["NSE_FO", "NSE_INDEX", "BSE_INDEX"])
    # One master per URL: NSE_CM carries the index + equity rows; the
    # other segment masters contribute nothing here.
    payloads = {
        "NSE_CM_sym_master.json": _fyers_master(),
        "NSE_FO_sym_master.json": {},
        "BSE_CM_sym_master.json": {},
        "MCX_COM_sym_master.json": {},
    }
    result = cat.sync_fyers(fetch=lambda url: json.dumps(
        payloads[url.rsplit("/", 1)[-1]]).encode())
    runner.assert_eq("fy-parsed", result["parsed"], 3)
    runner.assert_eq("fy-kept", result["kept"], 2)
    runner.assert_eq("fy-filtered", result["filtered"], 1)
    segs = result["segments"]
    runner.assert_eq("fy-index-kept", segs.get("NSE_INDEX"), 1)
    runner.assert_eq("fy-eq-dropped", segs.get("NSE_EQ"), None)
    rows = cat.search(provider="fyers", limit=10)
    syms = {r["tradingsymbol"] for r in rows}
    runner.assert_true("fy-nifty-kept", "NSE:NIFTY50-INDEX" in syms)
    runner.assert_true("fy-niftyce-kept", "NSE:NIFTY2690823800CE" in syms)
    runner.assert_true("fy-sbin-dropped", "NSE:SBIN-EQ" not in syms)
    runner.assert_eq("fy-provider-preserved",
                     all(r["provider"] == "fyers" for r in rows), True)


# -- API -------------------------------------------------------------------------

def _req(method, path, query=b"", body=None):
    scope = {"type": "http", "method": method, "path": path,
             "headers": [(b"content-type", b"application/json")],
             "query_string": query}

    async def receive():
        return {"type": "http.request",
                "body": json.dumps(body).encode() if body is not None
                else b"", "more_body": False}

    return Request(scope, receive)


def test_api(runner: R) -> None:
    env = _Env()
    routes = {}
    for r in build_instrument_routes(env.catalog, store=env.store):
        for m in r.methods:
            if m != "HEAD":
                routes[(r.path, m)] = r
    # 14. GET shape
    route = routes[("/api/instruments/segments", "GET")]

    async def call(r, req):
        resp = await r.endpoint(req)
        return resp.status_code, json.loads(resp.body.decode())

    code, data = asyncio.run(call(route, _req("GET", "/x")))
    runner.assert_eq("api-get-200", code, 200)
    runner.assert_eq("api-defaults", data["default"],
                     sorted(DEFAULT_SEGMENTS))
    seg_map = {s["segment"]: s for s in data["segments"]}
    runner.assert_eq("api-nse-eq-on", seg_map["NSE_EQ"]["enabled"], True)
    runner.assert_eq("api-nse-com-off", seg_map["NSE_COM"]["enabled"], False)
    runner.assert_true("api-has-rows", seg_map["NSE_EQ"]["catalog_rows"] >= 0)
    # 15. PUT round-trip + invalid
    put = routes[("/api/instruments/segments", "PUT")]
    code, data = asyncio.run(call(put, _req("PUT", "/x", body={
        "segments": ["NSE_EQ", "NSE_FO", "NSE_INDEX", "BSE_INDEX",
                     "NSE_COM"]})))
    runner.assert_eq("api-put-200", code, 200)
    code, data = asyncio.run(call(put, _req("PUT", "/x", body={
        "segments": ["NSE_EQ", "BOGUS_SEGMENT"]})))
    runner.assert_eq("api-put-invalid-400", code, 400)
    code, data = asyncio.run(call(put, _req("PUT", "/x", body={})))
    runner.assert_eq("api-put-missing-400", code, 400)
    # 16. sync honors preference — the saved preference enabled NSE_COM,
    # so it MUST appear in the catalog after sync (fetch mocked to the
    # synthetic master; the PUT above set the preference).
    import app.instruments as inst_mod
    old_fetch = inst_mod._fetch
    inst_mod._fetch = lambda url: json.dumps(_master_rows()).encode()
    try:
        code, data = asyncio.run(call(
            routes[("/api/instruments/sync", "POST")],
            _req("POST", "/x", body={"provider": "upstox"})))
        runner.assert_eq("api-sync-200", code, 200)
        runner.assert_eq("api-sync-honors",
                         data["segments"].get("NSE_COM"), 1)
        runner.assert_eq("api-sync-kept-count",
                         data["kept"] + data["filtered"], data["parsed"])
    finally:
        inst_mod._fetch = old_fetch


# -- collisions ---------------------------------------------------------------------

def test_collision_logging(runner: R) -> None:
    import logging
    from app.market_identity import MarketInstrumentIdentityResolver
    records = []
    handler = logging.Handler()
    handler.emit = lambda rec: records.append(rec)
    logger = logging.getLogger("app.market_identity")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        res = MarketInstrumentIdentityResolver()
        row1 = {"exchange": "MCX", "instrument_type": "OPTION",
                "option_type": "CE", "underlying": "GOLD",
                "expiry": "2027-02-26", "strike": 147000,
                "instrument_token": "MCX_FO|1", "tradingsymbol":
                "GOLD 147000 CE"}
        row2 = dict(row1, instrument_token="NSE_COM|2", exchange="NSE")
        r1 = res.register_catalog_row(row1)
        r2 = res.register_catalog_row(row2)
        runner.assert_eq("col-first-registers", r1["registered"] >= 1, True)
        runner.assert_eq("col-second-rejected", len(r2["rejected"]), 1)
        # ambiguity preserved: bare name still resolves to FIRST canonical
        got = res.resolve("GOLD 147000 CE")
        runner.assert_eq("col-first-wins", got,
                         res.canonical_id_for_row(row1))
        # batch summary path: one INFO line, no WARNING spam
        records.clear()
        res2 = MarketInstrumentIdentityResolver()
        rows = [row1, row2, dict(row1, instrument_token="MCX_FO|3"),
                dict(row2, instrument_token="NSE_COM|4", exchange="NSE")]
        total = res2.register_catalog_rows(rows)
        runner.assert_eq("col-summary-rejected", len(total["rejected"]), 2)
        warns = [r for r in records if r.levelno == logging.WARNING
                 and "collision" in r.getMessage()]
        runner.assert_eq("col-no-warning-spam", len(warns), 0)
        infos = [r for r in records if r.levelno == logging.INFO
                 and "identity collisions" in r.getMessage()]
        runner.assert_eq("col-one-summary", len(infos), 1)
        runner.assert_true("col-bounded-examples",
                           "GOLD 147000 CE" in infos[0].getMessage())
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def main() -> bool:
    runner = R()
    test_preferences(runner)
    test_upstox_filter(runner)
    test_fyers_derived_filter(runner)
    test_api(runner)
    test_collision_logging(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = main()
    sys.exit(0 if _ok else 1)
