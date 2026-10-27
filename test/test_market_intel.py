"""Market-intelligence acceptance tests (MI) — Packages 1/4/5/6/7/8/9/13/14.

Uses a synthetic instrument catalog (no network, no live broker):
  * unified search: plain / type-word / option-descriptor queries
  * futures + option expiry discovery by underlying
  * deterministic ATM, strike windows, CE/PE pairing
  * window-scoped analytics incl. PCR
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


def _seed(store):
    """Synthetic catalog: NIFTY index + equity RELIANCE + derivatives."""
    rows = []
    rows.append(dict(provider="fyers", instrument_token="100IDX",
                     exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
                     name="Nifty 50", instrument_type="INDEX",
                     segment="10", underlying="NIFTY", lot_size=1))
    rows.append(dict(provider="fyers", instrument_token="200EQ",
                     exchange="NSE", tradingsymbol="NSE:RELIANCE-EQ",
                     name="Reliance Industries", instrument_type="EQUITY",
                     segment="10", underlying="RELIANCE", lot_size=1,
                     isin="INE002A01018"))
    # Same real instrument, second provider (cash duplicate for dedupe) and
    # two near-miss symbols for the substring/ranking checks.
    rows.append(dict(provider="upstox", instrument_token="NSE_EQ|INE002A01018",
                     exchange="NSE", tradingsymbol="RELIANCE",
                     name="Reliance Industries Ltd", instrument_type="EQUITY",
                     segment="NSE_EQ", underlying="RELIANCE", lot_size=1,
                     isin="INE002A01018"))
    rows.append(dict(provider="fyers", instrument_token="201EQ",
                     exchange="NSE", tradingsymbol="NSE:RELIABLE-EQ",
                     name="Reliable Data Service", instrument_type="EQUITY",
                     segment="10", underlying="RELIABLE", lot_size=1,
                     isin="INE000000002"))
    rows.append(dict(provider="fyers", instrument_token="202EQ",
                     exchange="BSE", tradingsymbol="BSE:RELINFRA-EQ",
                     name="Reliance Infrastructure", instrument_type="EQUITY",
                     segment="10", underlying="RELINFRA", lot_size=1,
                     isin="INE000000003"))
    # Untyped Upstox index row: the picker must exclude it, not classify it.
    rows.append(dict(provider="upstox", instrument_token="NSE_INDEX|Nifty 50",
                     exchange="NSE", tradingsymbol="NIFTY",
                     name="Nifty 50", instrument_type=None,
                     segment="NSE_INDEX"))
    for i, exp in enumerate(("2026-09-29", "2026-10-30")):
        rows.append(dict(provider="fyers",
                         instrument_token=f"300FUT{i}",
                         exchange="NSE",
                         tradingsymbol=f"NSE:NIFTY{29+i}FUT",
                         name="NIFTY FUT", instrument_type="FUTURE",
                         segment="11", underlying="NIFTY", expiry=exp,
                         lot_size=75))
        rows.append(dict(provider="fyers",
                         instrument_token=f"350FUT{i}",
                         exchange="NSE",
                         tradingsymbol=f"NSE:RELIANCE{29+i}FUT",
                         name="RELIANCE FUT", instrument_type="FUTURE",
                         segment="11", underlying="RELIANCE", expiry=exp,
                         lot_size=250))
    strikes = [23750, 23800, 23850, 23900, 23950, 24000, 24050, 24100,
               24150, 24200, 24250]
    for exp_i, exp in enumerate(("2026-09-01", "2026-09-08")):
        for s in strikes:
            for ot in ("CE", "PE"):
                rows.append(dict(
                    provider="fyers",
                    instrument_token=f"400{exp_i}{s}{ot}",
                    exchange="NSE",
                    tradingsymbol=f"NSE:NIFTY{exp_i}{s}{ot}",
                    name="NIFTY OPT", instrument_type="OPTION",
                    segment="11", underlying="NIFTY", expiry=exp,
                    strike=float(s), option_type=ot, lot_size=75))
    upstox = [r for r in rows if r["provider"] == "upstox"]
    fyers = [r for r in rows if r["provider"] != "upstox"]
    store.replace_provider_instruments("fyers", fyers)
    store.replace_provider_instruments("upstox", upstox)
    return len(rows)


def _mk_intel(quotes=None):
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel
    from core.persistence.store import EventStore

    store = EventStore(os.path.join(tempfile.mkdtemp(), "e.db"))
    n = _seed(store)
    catalog = InstrumentCatalog(store)

    async def spot(exchange, token):
        return (quotes or {}).get(f"{exchange}:{token}")

    return MarketIntel(catalog, spot_provider=None), n


def test_mi1_search(runner: R) -> None:
    intel, _n = _mk_intel()

    r = intel.search("nifty")
    runner.assert_true("MI1-index-first",
                       r["results"][0]["symbol"] == "NSE:NIFTY50-INDEX")
    runner.assert_eq("MI1-type", r["results"][0]["type"], "INDEX")

    r = intel.search("reliance")
    runner.assert_true("MI1-equity-found",
                       any(x["symbol"] in ("NSE:RELIANCE-EQ", "RELIANCE")
                           for x in r["results"]))

    r = intel.search("reliance future")
    runner.assert_eq("MI1-fut-count", r["count"], 2)
    runner.assert_true("MI1-fut-underlying",
                       all(x["underlying"] == "RELIANCE"
                           for x in r["results"]))

    r = intel.search("nifty 24000 ce")
    runner.assert_eq("MI1-opt-count", r["count"], 2)   # two expiries
    runner.assert_true("MI1-opt-type",
                       all(x["option_type"] == "CE" for x in r["results"]))
    runner.assert_true("MI1-opt-strike",
                       all(x["strike"] == 24000.0 for x in r["results"]))


def test_mi2_derivatives_discovery(runner: R) -> None:
    intel, _n = _mk_intel()

    f = intel.futures_contracts("NIFTY")
    runner.assert_eq("MI2-fut-expiries", len(f["expiries"]), 2)
    runner.assert_eq("MI2-fut-contracts", len(f["contracts"]), 2)
    runner.assert_eq("MI2-fut-lot", f["contracts"][0]["lot_size"], 75)
    runner.assert_eq("MI2-underlying-resolved",
                     f["underlying_instrument"]["symbol"],
                     "NSE:NIFTY50-INDEX")

    e = intel.option_expiries("NIFTY")
    runner.assert_eq("MI2-opt-expiries", e["expiries"],
                     ["2026-09-01", "2026-09-08"])

    bad = intel.futures_contracts("NOPE")
    runner.assert_in("MI2-unknown-underlying", "error", str(bad))


def test_mi3_chain_atm_window(runner: R) -> None:
    intel, _n = _mk_intel()

    c = intel.option_chain("NIFTY", spot=23960, window=2)
    runner.assert_eq("MI3-atm", c["atm_strike"], 23950.0)
    runner.assert_eq("MI3-spot-basis", c["spot_basis"], "explicit")
    # ATM ±2 -> strikes 23850..24050 = 5 rows
    runner.assert_eq("MI3-window-rows", c["strikes_loaded"], 5)
    runner.assert_eq("MI3-total-listed", c["strikes_total_listed"], 11)
    atm_row = next(r for r in c["rows"] if r["atm"])
    runner.assert_eq("MI3-atm-row-strike", atm_row["strike"], 23950.0)
    runner.assert_true("MI3-ce-present", atm_row["call"] is not None)
    runner.assert_true("MI3-pe-present", atm_row["put"] is not None)
    runner.assert_eq("MI3-ce-id", atm_row["call"]["instrument_key"],
                     "NSE:400023950CE")
    # nearest-expiry default
    runner.assert_eq("MI3-default-expiry", c["expiry"], "2026-09-01")

    # second expiry via explicit param
    c2 = intel.option_chain("NIFTY", expiry="2026-09-08", spot=23980,
                            window=1)
    runner.assert_eq("MI3-second-expiry", c2["expiry"], "2026-09-08")

    # empty window bounds
    c3 = intel.option_chain("NIFTY", spot=23980, window=100)
    runner.assert_eq("MI3-all-strikes", c3["strikes_loaded"], 11)


def test_mi4_chain_spot_fallback_and_errors(runner: R) -> None:
    intel, _n = _mk_intel()

    c = intel.option_chain("NIFTY")     # no spot anywhere
    runner.assert_eq("MI4-fallback-basis", c["spot_basis"],
                     "fallback_mid_strike")
    runner.assert_true("MI4-fallback-atm-listed",
                       any(r["strike"] == c["atm_strike"]
                           for r in c["rows"]))

    bad = intel.option_chain("NIFTY", expiry="2030-01-01")
    runner.assert_in("MI4-unlisted-expiry", "not listed", str(bad))

    missing = intel.option_chain("NOSUCH")
    runner.assert_in("MI4-unknown", "unknown underlying", str(missing))


def test_mi5_snapshot_freshness(runner: R) -> None:
    """Snapshot includes live quote + explicit freshness; none fabricated."""
    from core.persistence.store import EventStore
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel

    store = EventStore(os.path.join(tempfile.mkdtemp(), "e.db"))
    _seed(store)
    catalog = InstrumentCatalog(store)

    class _Q:
        pass

    from market.models import Quote
    from datetime import datetime, timezone
    live_quote = Quote(instrument_token="100IDX", exchange="NSE",
                       tradingsymbol="NSE:NIFTY50-INDEX",
                       received_ts=datetime.now(timezone.utc),
                       ltp=123.5)

    def spot(exchange, token):
        if token == "100IDX":
            return live_quote
        return None

    intel = MarketIntel(catalog, spot_provider=spot)

    import asyncio
    snap = asyncio.run(intel.snapshot("nifty"))
    runner.assert_eq("MI5-identity",
                     snap["instrument"]["symbol"], "NSE:NIFTY50-INDEX")
    runner.assert_eq("MI5-live-ltp", snap["quote"]["ltp"], 123.5)
    runner.assert_false("MI5-not-stale", snap["freshness"]["stale"])

    snap2 = asyncio.run(intel.snapshot("reliance"))
    runner.assert_true("MI5-no-quote-honest",
                       snap2.get("quote") is None)
    runner.assert_true("MI5-stale-flagged", snap2["freshness"]["stale"])


def test_mi6_ranked_cash_scope(runner: R) -> None:
    """Phase 0: the generic fallback is ONE ranked cash-class pass."""
    intel, _n = _mk_intel()

    r = intel.search("rel", limit=20)
    runner.assert_true("MI6-has-results", r["count"] > 0)
    runner.assert_true(
        "MI6-cash-only",
        all(x["type"] in ("INDEX", "EQUITY", "ETF") for x in r["results"]))
    runner.assert_false(
        "MI6-no-derivative-noise",
        any(x["type"] in ("FUTURE", "OPTION") for x in r["results"]))

    symbols = [x["symbol"] for x in r["results"]]
    runner.assert_true("MI6-nse-before-bse",
                       symbols.index("BSE:RELINFRA-EQ")
                       > symbols.index("NSE:RELIABLE-EQ"))

    typed = intel.search("nifty", types=["OPTION"], limit=5)
    runner.assert_true("MI6-explicit-type-kept",
                       typed["count"] > 0 and
                       all(x["type"] == "OPTION" for x in typed["results"]))


def test_mi7_canonical_dedupe(runner: R) -> None:
    """Phase 0: one row per canonical instrument, winning provider kept."""
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel
    from core.persistence.store import EventStore

    store = EventStore(os.path.join(tempfile.mkdtemp(), "dedupe.db"))
    _seed(store)
    catalog = InstrumentCatalog(store)
    intel = MarketIntel(catalog)

    r = intel.search("reliance", limit=20)
    cash = [x for x in r["results"]
            if x["symbol"] in ("NSE:RELIANCE-EQ", "RELIANCE")]
    runner.assert_eq("MI7-one-row", len(cash), 1)
    runner.assert_in("MI7-provider-attribution", cash[0]["provider"],
                     ("fyers", "upstox"))
    runner.assert_true("MI7-provider-token", bool(cash[0]["provider_token"]))

    # The unranked read model is unchanged for every legacy caller.
    legacy = catalog.search(q="RELIANCE", limit=20)
    providers = {x["provider"] for x in legacy
                 if x["isin"] == "INE002A01018"}
    runner.assert_eq("MI7-legacy-both-providers", len(providers), 2)


def test_mi8_exact_mode(runner: R) -> None:
    """Phase 0: exact resolution never guesses (no results[0] fallback)."""
    intel, _n = _mk_intel()

    exact = intel.search_exact("RELIANCE")
    runner.assert_true("MI8-has-result", exact["count"] > 0)
    runner.assert_eq("MI8-cash-first",
                     exact["results"][0]["type"], "EQUITY")
    runner.assert_eq("MI8-one-equity",
                     len([x for x in exact["results"]
                          if x["type"] == "EQUITY"]), 1)

    runner.assert_eq("MI8-no-substring-guess",
                     intel.search_exact("REL")["count"], 0)

    nifty = intel.search_exact("NIFTY")
    runner.assert_true("MI8-index-result", nifty["count"] > 0)
    runner.assert_eq("MI8-index-first",
                     nifty["results"][0]["type"], "INDEX")
    runner.assert_eq("MI8-index-symbol",
                     nifty["results"][0]["symbol"], "NSE:NIFTY50-INDEX")
    # The untyped Upstox NIFTY row is excluded, not classified.
    runner.assert_true(
        "MI8-only-canonical-types",
        all(x["type"] in ("INDEX", "EQUITY", "ETF", "FUTURE", "OPTION")
            for x in nifty["results"]))


def test_mi9_limit_caps(runner: R) -> None:
    """Ranked queries cap at 50; the legacy read model keeps its cap of 100."""
    from app.instruments import InstrumentCatalog
    from core.persistence.store import EventStore

    rows = [dict(provider="fyers", instrument_token=f"A{i}",
                 exchange="NSE", tradingsymbol=f"ANCHOR{i:03d}",
                 name=f"Anchor {i}", instrument_type="EQUITY",
                 segment="10") for i in range(120)]
    store = EventStore(os.path.join(tempfile.mkdtemp(), "caps.db"))
    store.replace_provider_instruments("fyers", rows)
    catalog = InstrumentCatalog(store)

    runner.assert_eq("MI9-ranked-cap",
                     len(catalog.search(q="ANCHOR", rank="anchor",
                                        limit=1000)), 50)
    runner.assert_eq("MI9-legacy-cap",
                     len(catalog.search(q="ANCHOR", limit=1000)), 100)


def test_mi10_route_exact_and_ranked(runner: R) -> None:
    """/api/market/search: exact=1 resolves exactly; ranked stays cash-first."""
    import asyncio
    import json

    from api.product_routes import build_intel_routes

    intel, _n = _mk_intel()
    routes = build_intel_routes(intel, None)
    target = next(r for r in routes if r.path == "/api/market/search")

    class _Req:
        def __init__(self, params):
            self.query_params = params

    def call(params):
        resp = asyncio.run(target.endpoint(_Req(params)))
        return json.loads(resp.body)

    runner.assert_eq("MI10-exact-no-substring",
                     call({"q": "REL", "exact": "1"})["count"], 0)

    exact = call({"q": "RELIANCE", "exact": "1"})
    runner.assert_eq("MI10-exact-first-type",
                     exact["results"][0]["type"], "EQUITY")
    runner.assert_eq("MI10-exact-one-equity",
                     len([x for x in exact["results"]
                          if x["type"] == "EQUITY"]), 1)

    ranked = call({"q": "REL", "limit": "5"})
    runner.assert_true("MI10-ranked-cash-only",
                       all(x["type"] in ("INDEX", "EQUITY", "ETF")
                           for x in ranked["results"]))
    runner.assert_true("MI10-ranked-has-results", ranked["count"] > 0)


if __name__ == "__main__":
    runner = R()
    test_mi1_search(runner)
    test_mi2_derivatives_discovery(runner)
    test_mi3_chain_atm_window(runner)
    test_mi4_chain_spot_fallback_and_errors(runner)
    test_mi5_snapshot_freshness(runner)
    test_mi6_ranked_cash_scope(runner)
    test_mi7_canonical_dedupe(runner)
    test_mi8_exact_mode(runner)
    test_mi9_limit_caps(runner)
    test_mi10_route_exact_and_ranked(runner)
    sys.exit(0 if runner.summary() else 1)
