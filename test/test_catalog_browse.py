"""Phase 1 acceptance tests — catalog browse + navigation pool read models.

Synthetic catalogs only (no network, no live broker). Covers:
  * provider dedupe parity with the Python canonical derivation
  * keyset pagination reaches the full set without duplicates or gaps
  * expired-contract and untyped-row exclusion
  * cursor validation (shape, filter fingerprint)
  * pool membership, ordering, and global ticker uniqueness
  * HTTP route validation (400s)
"""
import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

_TODAY = "2026-11-08"


def _cash_rows():
    """Cash rows with deliberate tricky identities."""
    rows = [
        # Same ISIN, two providers, different symbol spellings -> ONE row.
        dict(provider="fyers", instrument_token="F1", exchange="NSE",
             tradingsymbol="NSE:RELIANCE-EQ", name="Reliance",
             instrument_type="EQUITY", segment="NSE_EQ",
             underlying="RELIANCE", isin="INE002A01018"),
        dict(provider="upstox", instrument_token="U1", exchange="NSE",
             tradingsymbol="RELIANCE", name="Reliance Industries",
             instrument_type="EQUITY", segment="NSE_EQ",
             underlying="RELIANCE", isin="INE002A01018"),
        # No ISIN: symbol normalization must merge M&M variants. The
        # canonical derivation normalizes tradingsymbol (not underlying),
        # so both rows carry the same bare symbol.
        dict(provider="fyers", instrument_token="F2", exchange="NSE",
             tradingsymbol="NSE:M&M-EQ", name="Mahindra",
             instrument_type="EQUITY", segment="NSE_EQ",
             underlying="M&M", isin="INE0000000M1"),
        dict(provider="upstox", instrument_token="U2", exchange="NSE",
             tradingsymbol="M&M", name="Mahindra & Mahindra",
             instrument_type="EQUITY", segment="NSE_EQ",
             underlying="M&M", isin="INE0000000M1"),
        # Index alias forms across providers -> ONE row.
        dict(provider="fyers", instrument_token="F3", exchange="NSE",
             tradingsymbol="NSE:NIFTY50-INDEX", name="Nifty 50",
             instrument_type="INDEX", segment="NSE_INDEX",
             underlying="NIFTY"),
        dict(provider="upstox", instrument_token="U3", exchange="NSE",
             tradingsymbol="Nifty 50", name="Nifty 50",
             instrument_type="INDEX", segment="NSE_INDEX"),
        # Cross-exchange same ticker -> TWO rows (exchange is in the key).
        dict(provider="fyers", instrument_token="F4", exchange="NSE",
             tradingsymbol="NSE:ENERGY-EQ", name="Energy NSE",
             instrument_type="EQUITY", segment="NSE_EQ", isin="INE0000000A1"),
        dict(provider="fyers", instrument_token="F5", exchange="BSE",
             tradingsymbol="BSE:ENERGY-EQ", name="Energy BSE",
             instrument_type="EQUITY", segment="BSE_EQ", isin="INE0000000A2"),
        # ETF.
        dict(provider="upstox", instrument_token="U6", exchange="NSE",
             tradingsymbol="NIFTYBEES", name="Nifty ETF",
             instrument_type="ETF", segment="NSE_EQ", isin="INF204K01A10"),
        # Untyped row: never browsable.
        dict(provider="upstox", instrument_token="U7", exchange="NSE",
             tradingsymbol="GHOST", name="Untyped",
             instrument_type=None, segment="NSE_EQ"),
    ]
    return rows


def _derivative_rows():
    rows = []
    for exp in ("2026-10-01", "2026-12-29"):
        # Same logical future on both providers -> ONE live row.
        rows.append(dict(provider="fyers", instrument_token=f"FF{exp}",
                         exchange="NSE", tradingsymbol=f"NSE:NIFTY{exp}FUT",
                         name="NIFTY FUT", instrument_type="FUTURE",
                         segment="NSE_FO", underlying="NIFTY", expiry=exp))
        rows.append(dict(provider="upstox", instrument_token=f"UF{exp}",
                         exchange="NSE", tradingsymbol=f"NIFTY {exp} FUT",
                         name="NIFTY FUT", instrument_type="FUTURE",
                         segment="NSE_FO", underlying="NIFTY", expiry=exp))
        for strike in (25000, 25050.5):
            for ot in ("CE", "PE"):
                rows.append(dict(
                    provider="fyers",
                    instrument_token=f"FO{exp}{strike}{ot}",
                    exchange="NSE",
                    tradingsymbol=f"NSE:NIFTY{exp}{strike}{ot}",
                    name="NIFTY OPT", instrument_type="OPTION",
                    segment="NSE_FO", underlying="NIFTY", expiry=exp,
                    strike=float(strike), option_type=ot))
    return rows


def _mk_store(extra=()):
    from core.persistence.store import EventStore
    store = EventStore(os.path.join(tempfile.mkdtemp(), "browse.db"))
    rows = _cash_rows() + _derivative_rows() + list(extra)
    for provider in ("fyers", "upstox"):
        store.replace_provider_instruments(
            provider, [r for r in rows if r["provider"] == provider])
    return store


def _mk_intel(store):
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel
    return MarketIntel(InstrumentCatalog(store))


def _browse(store, **kw):
    from app.canonical_identity import _SYMBOL_ALIASES
    from core.persistence.modules import products as P
    conn = store._open(store._db_path)
    try:
        kw.setdefault("today", _TODAY)
        kw.setdefault("symbol_aliases", _SYMBOL_ALIASES)
        kw.setdefault("provider_order", ("upstox", "fyers"))
        return P.browse_instruments(conn, **kw)
    finally:
        conn.close()


def test_cb1_dedupe_parity(runner: R) -> None:
    """SQL dedupe keeps exactly the rows the Python canonical derivation
    keeps, with the preferred provider's row winning."""
    from app.canonical_identity import canonical_id_for_row
    store = _mk_store()
    result = _browse(store, types=["INDEX", "EQUITY", "ETF"], limit=200)
    rows = result["rows"]
    cids = [canonical_id_for_row(r) for r in rows]
    runner.assert_eq("CB1-no-duplicate-cids", len(cids), len(set(cids)))
    # 11 raw cash rows (2 untyped/duplicate merges + 1 untyped excluded):
    # RELIANCE, M&M, NIFTY, ENERGY-NSE, ENERGY-BSE, NIFTYBEES = 6 logical.
    runner.assert_eq("CB1-logical-count", len(rows), 6)
    runner.assert_eq("CB1-total", result["total"], 6)
    by_cid = {canonical_id_for_row(r): r for r in rows}
    runner.assert_eq("CB1-preferred-provider",
                     by_cid["NSE:EQUITY:INE002A01018"]["provider"], "upstox")
    runner.assert_eq("CB1-index-merged",
                     by_cid["NSE:INDEX:NIFTY"]["provider"], "upstox")
    runner.assert_true("CB1-untyped-excluded",
                       all(r["tradingsymbol"] != "GHOST" for r in rows))
    runner.assert_true("CB1-cross-exchange-kept",
                       "NSE:EQUITY:INE0000000A1" in by_cid
                       and "BSE:EQUITY:INE0000000A2" in by_cid)


def test_cb2_pagination(runner: R) -> None:
    """Keyset pagination reaches every logical row exactly once."""
    extra = [dict(provider="fyers", instrument_token=f"X{i:04d}",
                  exchange="NSE", tradingsymbol=f"ANCHOR{i:04d}",
                  name=f"Anchor {i}", instrument_type="EQUITY",
                  segment="NSE_EQ", isin=f"INX{i:04d}AA0")
             for i in range(257)]
    store = _mk_store(extra)
    first = _browse(store, types=["EQUITY"], limit=50)
    runner.assert_eq("CB2-total", first["total"], 257 + 4)  # +RELIANCE,M&M,ENERGYx2
    seen = set()
    page = first
    pages = 0
    while True:
        pages += 1
        for r in page["rows"]:
            key = (r["tradingsymbol"], r["provider"], r["instrument_token"])
            runner.assert_true(f"CB2-unique-{key}", key not in seen)
            seen.add(key)
        if page["next_after"] is None:
            break
        page = _browse(store, types=["EQUITY"], limit=50,
                       after=page["next_after"])
        runner.assert_true("CB2-later-total-none", page["total"] is None)
    runner.assert_eq("CB2-walked-all", len(seen), first["total"])
    runner.assert_eq("CB2-page-count", pages, 6)  # 261 rows / 50


def test_cb3_expired_and_scope(runner: R) -> None:
    store = _mk_store()
    fut = _browse(store, types=["FUTURE"], limit=200)
    runner.assert_eq("CB3-live-futures-only", len(fut["rows"]), 1)
    runner.assert_eq("CB3-future-expiry", fut["rows"][0]["expiry"],
                     "2026-12-29")
    fut_all = _browse(store, types=["FUTURE"], include_expired=True,
                      limit=200)
    runner.assert_eq("CB3-include-expired", len(fut_all["rows"]), 2)
    opt = _browse(store, types=["OPTION"], underlying="NIFTY", limit=200)
    # live expiry only: 2 strikes x CE/PE = 4 logical contracts
    runner.assert_eq("CB3-live-options", len(opt["rows"]), 4)
    runner.assert_true("CB3-strike-normalized",
                     any(r["strike"] == 25050.5 for r in opt["rows"]))
    scoped = _browse(store, types=["OPTION"], underlying="NIFTY",
                     expiry="2026-12-29", option_type="CE", strike=25000,
                     limit=50)
    runner.assert_eq("CB3-scoped-one", len(scoped["rows"]), 1)


def test_cb4_intel_validation(runner: R) -> None:
    store = _mk_store()
    intel = _mk_intel(store)
    for label, kw in (
            ("CB4-option-needs-underlying", {"types": ["OPTION"]}),
            ("CB4-bad-type", {"types": ["BOGUS"]}),
            ("CB4-bad-option-type", {"types": ["OPTION"],
                                     "underlying": "NIFTY",
                                     "option_type": "XX"}),
            ("CB4-bad-cursor", {"cursor": "!!!invalid!!!"})):
        try:
            intel.browse(**kw)
            runner.fail(label, "expected ValueError")
        except ValueError:
            runner.ok(label)
    page = intel.browse(types=["EQUITY"], limit=2)
    runner.assert_true("CB4-cursor-issued", bool(page["next_cursor"]))
    try:
        intel.browse(types=["ETF"], cursor=page["next_cursor"])
        runner.fail("CB4-cursor-fingerprint", "expected ValueError")
    except ValueError:
        runner.ok("CB4-cursor-fingerprint")
    page2 = intel.browse(types=["EQUITY"], limit=2,
                         cursor=page["next_cursor"])
    runner.assert_true("CB4-cursor-continues", page2["count"] > 0)
    syms1 = {r["symbol"] for r in page["results"]}
    syms2 = {r["symbol"] for r in page2["results"]}
    runner.assert_true("CB4-no-page-overlap", not (syms1 & syms2))


def test_cb5_pool(runner: R) -> None:
    # A live RELIANCE future so its cash row qualifies for the pool.
    live = [dict(provider="upstox", instrument_token="ULIVE",
                 exchange="NSE", tradingsymbol="RELIANCE LIVE FUT",
                 name="RELIANCE FUT", instrument_type="FUTURE",
                 segment="NSE_FO", underlying="RELIANCE",
                 expiry="2026-12-29")]
    store = _mk_store(live)
    intel = _mk_intel(store)
    pool = intel.pool()
    tickers = [s["ticker"] for s in pool["symbols"]]
    runner.assert_eq("CB5-tickers-unique", len(tickers), len(set(tickers)))
    runner.assert_true("CB5-index-first",
                     pool["symbols"][0]["type"] == "index")
    runner.assert_eq("CB5-nifty-leads", pool["symbols"][0]["ticker"],
                     "NIFTY 50")
    types = {s["type"] for s in pool["symbols"]}
    runner.assert_true("CB5-types-mapped", types <= {"index", "stock", "etf"})
    runner.assert_true("CB5-etf-present", "NIFTYBEES" in tickers)
    # RELIANCE has a live future -> its cash row is in the pool.
    runner.assert_true("CB5-fno-underlying-present", "RELIANCE" in tickers)
    runner.assert_not_in("CB5-untyped-out", "GHOST", tickers)


def test_cb6_routes(runner: R) -> None:
    import asyncio
    import json

    from api.product_routes import build_intel_routes

    store = _mk_store()
    intel = _mk_intel(store)
    routes = build_intel_routes(intel, None)
    browse = next(r for r in routes if r.path == "/api/instruments/browse")
    pool = next(r for r in routes if r.path == "/api/instruments/pool")

    class _Req:
        def __init__(self, params):
            self.query_params = params

    def call(route, params):
        resp = asyncio.run(route.endpoint(_Req(params)))
        return resp.status_code, json.loads(resp.body)

    status, body = call(browse, {"limit": "5"})
    runner.assert_eq("CB6-browse-200", status, 200)
    runner.assert_true("CB6-browse-results", body["count"] > 0)
    status, _ = call(browse, {"types": "OPTION"})
    runner.assert_eq("CB6-option-400", status, 400)
    status, _ = call(browse, {"limit": "abc"})
    runner.assert_eq("CB6-limit-400", status, 400)
    status, _ = call(browse, {"strike": "abc"})
    runner.assert_eq("CB6-strike-400", status, 400)
    status, _ = call(browse, {"cursor": "!!!"})
    runner.assert_eq("CB6-cursor-400", status, 400)
    status, body = call(pool, {})
    runner.assert_eq("CB6-pool-200", status, 200)
    runner.assert_true("CB6-pool-symbols", body["count"] > 0)


if __name__ == "__main__":
    runner = R()
    test_cb1_dedupe_parity(runner)
    test_cb2_pagination(runner)
    test_cb3_expired_and_scope(runner)
    test_cb4_intel_validation(runner)
    test_cb5_pool(runner)
    test_cb6_routes(runner)
    sys.exit(0 if runner.summary() else 1)