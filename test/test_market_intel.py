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
                     segment="10", underlying="RELIANCE", lot_size=1))
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
    store.replace_provider_instruments("fyers", rows)
    return len(rows)


def _mk_intel(quotes=None):
    from core.persistence.store import EventStore
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel

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
                       any(x["symbol"] == "NSE:RELIANCE-EQ"
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

    async def spot(exchange, token):
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


if __name__ == "__main__":
    runner = R()
    test_mi1_search(runner)
    test_mi2_derivatives_discovery(runner)
    test_mi3_chain_atm_window(runner)
    test_mi4_chain_spot_fallback_and_errors(runner)
    test_mi5_snapshot_freshness(runner)
    sys.exit(0 if runner.summary() else 1)
