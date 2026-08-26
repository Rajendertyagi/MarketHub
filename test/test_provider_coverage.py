#!/usr/bin/env python3
"""Full provider data coverage tests (PC1-PC20).

Covers the extended canonical vocabulary across both providers:
  * PC1   Upstox WS full feed: option greeks extracted (P-ZERO applied)
  * PC2   Upstox WS full feed: IV extracted, merged with greeks
  * PC3   Upstox WS full feed: day candle fills open/high/low
  * PC4   Upstox P-ZERO: absent/zero greeks and iv produce no greeks field
  * PC5   Fyers REST quotes: circuits via depth supplemental
  * PC6   Fyers REST depth: upper/lower circuit + oi_change_percent + ltq
  * PC7   Fyers REST depth: last_trade_time from ltt
  * PC8   Fyers WS symbol update: circuits mapped
  * PC9   Fyers WS dp message: 5-level depth with order counts
  * PC10  Fyers WS dp: zero-price levels dropped, zero qty kept
  * PC11  Fyers options-chain greeks normalized (rho stays None)
  * PC12  Fyers options-chain: empty/missing greeks -> None
  * PC13  Canonical Quote: new fields construct + validate
  * PC14  OptionGreeks immutability
  * PC15  Serialization: greeks + new fields serialize JSON-safe
  * PC16  MarketService: greeks patch merges (replace-on-report)
  * PC17  MarketService: circuit fields merge with presence semantics
  * PC18  CROSS-PROVIDER: same LTP from Upstox/Fyers -> identical canonical
  * PC19  Existing behavior preserved: old field set still normalizes
  * PC20  Unknown patch fields still rejected

NO LIVE BROKER. Synthetic payloads based on official documentation only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
KEY = "NSE_EQ|INE002A01018"
TS = "RELIANCE"


def _ts():
    return datetime.now(UTC)


# -- Upstox --------------------------------------------------------------------


def _upstox_full_feed() -> dict:
    """Synthetic full-mode tick per official MarketDataFeed.proto semantics."""
    return {
        "ltpc": {"ltp": 100.5, "ltq": 25, "ltt": 1756000000000, "cp": 99.0},
        "atp": 100.25,
        "vtt": 500000,
        "oi": 1250000,
        "iv": 18.5,
        "tbq": 450000,
        "tsq": 380000,
        "optionGreeks": {
            "delta": 0.52, "theta": -6.25, "gamma": 0.001,
            "vega": 11.4,
        },
        "marketLevel": {"bidAskQuote": [
            {"bidQ": 500, "bidP": 100.45, "askQ": 300, "askP": 100.55},
            {"bidQ": 700, "bidP": 100.40, "askQ": 900, "askP": 100.60},
        ]},
        "marketOHLC": {"ohlc": [
            {"interval": "1m", "open": 100.0, "high": 100.6,
             "low": 99.9, "close": 100.5, "vol": 12000},
            {"interval": "1d", "open": 99.2, "high": 101.0,
             "low": 98.7, "close": 99.0, "vol": 480000},
        ]},
    }


def test_pc1_to_pc4_upstox(runner: R) -> None:
    from market.normalize.upstox import quote_fields_from_ws_full

    ff = _upstox_full_feed()
    fields = quote_fields_from_ws_full(ff, instrument_key=KEY,
                                       received_ts=NOW, tradingsymbol=TS)

    # PC1: greeks extracted; wire-absent rho stays None.
    g = fields.get("greeks")
    runner.assert_true("PC1-greeks-extracted", g is not None)
    if g is not None:
        runner.assert_eq("PC1-delta", g.delta, 0.52)
        runner.assert_eq("PC1-theta", g.theta, -6.25)
        runner.assert_eq("PC1-gamma", g.gamma, 0.001)
        runner.assert_eq("PC1-vega", g.vega, 11.4)
        runner.assert_eq("PC1-rho-not-reported", g.rho, None)

    # PC2: iv merged into the same greeks object.
    runner.assert_eq("PC2-iv-merged", getattr(g, "iv", None), 18.5)

    # PC3: day candle fills open/high/low (NOT the 1m candle).
    runner.assert_eq("PC3-open-from-1d", fields.get("open"), 99.2)
    runner.assert_eq("PC3-high-from-1d", fields.get("high"), 101.0)
    runner.assert_eq("PC3-low-from-1d", fields.get("low"), 98.7)

    # PC4: no greeks/iv in payload -> no greeks key at all.
    bare = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 50.0}}, instrument_key=KEY, received_ts=NOW)
    runner.assert_false("PC4-no-greeks-key", "greeks" in bare)
    zeroed = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 50.0}, "iv": 0, "optionGreeks": {"delta": 0}},
        instrument_key=KEY, received_ts=NOW)
    runner.assert_false("PC4-pzero-zero-dropped", "greeks" in zeroed)


# -- Fyers ---------------------------------------------------------------------


def test_pc5_to_pc8_fyers_quotes_depth(runner: R) -> None:
    from market.normalize.fyers import (
        depth_from_rest, quote_fields_from_symbol_update,
    )

    # PC6/PC7: REST depth supplemental carries circuits/OI%/ltq/ltt.
    payload = {
        "bids": [{"price": 99.5, "volume": 100, "ord": 3}],
        "ask": [{"price": 100.5, "volume": 200, "ord": 4}],
        "totalbuyqty": 150000, "totalsellqty": 175000,
        "ltp": 100.0, "v": 900000, "atp": 99.8,
        "oi": 2000000.0, "oipercent": 4.25,
        "upper_ckt": 110.0, "lower_ckt": 90.0,
        "ltq": 50, "ltt": 1756000100,
    }
    _depth, supp = depth_from_rest(payload, symbol="NSE:RELIANCE-EQ",
                                   received_ts=NOW)
    runner.assert_eq("PC6-upper-circuit", supp.get("upper_circuit"), 110.0)
    runner.assert_eq("PC6-lower-circuit", supp.get("lower_circuit"), 90.0)
    runner.assert_eq("PC6-oi-change-pct", supp.get("oi_change_percent"), 4.25)
    runner.assert_eq("PC6-ltq", supp.get("last_traded_qty"), 50)
    ltt = supp.get("last_trade_time")
    runner.assert_true("PC7-last-trade-time-aware",
                       ltt is not None and ltt.tzinfo is not None)

    # PC8: WS symbol update maps circuits.
    fields = quote_fields_from_symbol_update(
        {"symbol": "NSE:SBIN-EQ", "ltp": 810.5,
         "upper_ckt": 891.5, "lower_ckt": 729.5},
        received_ts=NOW)
    runner.assert_eq("PC8-ws-upper", fields.get("upper_circuit"), 891.5)
    runner.assert_eq("PC8-ws-lower", fields.get("lower_circuit"), 729.5)


def test_pc9_to_pc10_fyers_ws_depth(runner: R) -> None:
    from market.normalize.fyers import depth_from_ws_depth

    msg = {"symbol": "NSE:SBIN-EQ"}
    for i in range(1, 6):
        msg[f"bid_price{i}"] = 810.0 - (i - 1) * 0.05
        msg[f"bid_size{i}"] = 100 * i
        msg[f"bid_order{i}"] = 10 + i
        msg[f"ask_price{i}"] = 810.1 + (i - 1) * 0.05
        msg[f"ask_size{i}"] = 120 * i
        msg[f"ask_order{i}"] = 20 + i
    msg["ask_price5"] = 0          # placeholder row -> dropped
    msg["ask_size5"] = 999

    depth, _supp = depth_from_ws_depth(msg, received_ts=NOW)
    runner.assert_eq("PC9-five-bid-levels", len(depth.bids), 5)
    runner.assert_eq("PC9-four-ask-levels", len(depth.asks), 4)
    top = depth.bids[0]
    runner.assert_eq("PC9-bid-price", top.price, 810.0)
    runner.assert_eq("PC9-bid-orders", top.orders, 11)
    runner.assert_eq("PC10-zero-price-dropped",
                     [lv.price for lv in depth.asks][-1], 810.25)


def test_pc11_to_pc12_fyers_greeks(runner: R) -> None:
    from market.normalize.fyers import greeks_from_options_chain

    leg = {"symbol": "NSE:NIFTY26AUG24500CE",
           "greeks": {"delta": 0.41, "gamma": 0.0009,
                      "theta": -4.2, "vega": 9.8, "iv": 14.75}}
    g = greeks_from_options_chain(leg)
    runner.assert_true("PC11-greeks-built", g is not None)
    if g is not None:
        runner.assert_eq("PC11-delta", g.delta, 0.41)
        runner.assert_eq("PC11-iv", g.iv, 14.75)
        runner.assert_eq("PC11-rho-none", g.rho, None)

    runner.assert_eq("PC12-empty-greeks",
                     greeks_from_options_chain({"greeks": {}}), None)
    runner.assert_eq("PC12-missing-greeks",
                     greeks_from_options_chain({"symbol": "X"}), None)


# -- Canonical / service ---------------------------------------------------------


def test_pc13_to_pc15_models_serialization(runner: R) -> None:
    from market.models import OptionGreeks, Quote
    from market import serialization

    g = OptionGreeks(delta=0.5, iv=15.0)
    q = Quote(
        instrument_token=KEY, exchange="NSE", tradingsymbol=TS,
        received_ts=NOW, ltp=100.0,
        upper_circuit=110.0, lower_circuit=90.0,
        oi_change=25000.0, oi_change_percent=2.0,
        last_trade_time=NOW, greeks=g,
    )
    d = serialization.quote_to_dict(q)
    runner.assert_eq("PC15-upper-roundtrip", d["upper_circuit"], 110.0)
    runner.assert_eq("PC15-oi-change", d["oi_change"], 25000.0)
    runner.assert_eq("PC15-greeks-nested", d["greeks"]["delta"], 0.5)
    json.dumps(d)  # must be JSON-safe
    runner.assert_true("PC15-json-safe", True)

    # PC13: naive last_trade_time rejected.
    try:
        Quote(instrument_token=KEY, exchange="NSE", tradingsymbol=TS,
              received_ts=NOW, last_trade_time=datetime(2026, 1, 1))
        ok = False
    except ValueError:
        ok = True
    runner.assert_true("PC13-naive-rejected", ok)

    # PC14: OptionGreeks immutable.
    try:
        g.delta = 0.9  # type: ignore[misc]
        frozen = False
    except Exception:
        frozen = True
    runner.assert_true("PC14-immutable", frozen)


async def test_pc16_to_pc17_service_merge(runner: R) -> None:
    from market.service import MarketService, QuotePatch

    async def run():
        svc = MarketService()
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
            received_ts=NOW,
            reported_fields={"ltp": 100.0, "greeks": None},
        ))
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token=KEY, received_ts=NOW,
            reported_fields={"greeks": {"delta": 0.5, "iv": 15.0},
                             "upper_circuit": 110.0},
        ))
        return svc

    async def run_clear():
        svc = MarketService()
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
            received_ts=NOW,
            reported_fields={"upper_circuit": 110.0},
        ))
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token=KEY, received_ts=NOW,
            reported_fields={"upper_circuit": None},   # explicit clear
        ))
        return svc

    svc = await run()
    q = await svc.get_quote("NSE", KEY)
    runner.assert_true("PC16-greeks-merged", q is not None)
    if q is not None:
        runner.assert_eq("PC16-greeks-delta",
                         q.greeks.delta if q.greeks else None, 0.5)
        runner.assert_eq("PC16-circuit-set", q.upper_circuit, 110.0)

    svc2 = await run_clear()
    q2 = await svc2.get_quote("NSE", KEY)
    runner.assert_true("PC17-explicit-null-clears",
                       q2 is not None and q2.upper_circuit is None)


def test_pc18_cross_provider_ltp(runner: R) -> None:
    """Same semantic LTP from both providers -> identical canonical value."""
    from market.normalize.fyers import quote_from_quotes_rest
    from market.normalize.upstox import quote_fields_from_ws_full

    fyers_q = quote_from_quotes_rest(
        {"lp": 123.45, "prev_close_price": 120.0},
        symbol="NSE:RELIANCE-EQ", received_ts=NOW)
    upstox_f = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 123.45, "cp": 120.0}},
        instrument_key=KEY, received_ts=NOW, tradingsymbol=TS)

    runner.assert_eq("PC18-fyers-ltp", fyers_q.ltp, 123.45)
    runner.assert_eq("PC18-upstox-ltp", upstox_f.get("ltp"), 123.45)
    runner.assert_eq("PC18-close-parity", fyers_q.close, upstox_f.get("close"))


def test_pc19_pc20_regression_guards(runner: R) -> None:
    from market.normalize.common import NormalizationError
    from market.normalize.upstox import quote_fields_from_ws_full
    from market.service import QuotePatch

    # PC19: legacy minimal payload still normalizes identically.
    fields = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 42.0, "cp": 40.0}},
        instrument_key=KEY, received_ts=NOW)
    runner.assert_eq("PC19-legacy-ltp", fields.get("ltp"), 42.0)
    runner.assert_false("PC19-no-new-keys-leak",
                        {"greeks"} & set(fields))

    # PC20: unknown patch fields still rejected.
    try:
        QuotePatch(exchange="NSE", instrument_token=KEY, received_ts=NOW,
                   reported_fields={"not_a_field": 1})
        rejected = False
    except Exception:
        rejected = True
    runner.assert_true("PC20-unknown-rejected", rejected)


# -- Semantic audit additions (SA1-SA4) ---------------------------------------------


def test_sa1_wire_forced_zero_greek(runner: R) -> None:
    """SA1: exact-0.0 greek dropped (wire-forced); near-zero preserved."""
    from market.normalize.upstox import quote_fields_from_ws_full

    fields = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 50.0},
         "optionGreeks": {"delta": 0.005, "gamma": 0.0001}},
        instrument_key=KEY, received_ts=NOW)
    g = fields.get("greeks")
    runner.assert_true("SA1-near-zero-kept",
                       g is not None and g.delta == 0.005)

    fields2 = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 50.0}, "optionGreeks": {"delta": 0}},
        instrument_key=KEY, received_ts=NOW)
    runner.assert_false("SA1-exact-zero-dropped", "greeks" in fields2)


async def test_sa2_partial_greeks_merge(runner: R) -> None:
    """SA2: partial Greeks snapshot must not discard prior field values."""
    from market.models import OptionGreeks
    from market.service import MarketService, QuotePatch

    svc = MarketService()
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
        received_ts=NOW,
        reported_fields={"greeks": OptionGreeks(delta=0.52, vega=11.4)},
    ))
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token=KEY, received_ts=NOW,
        reported_fields={"greeks": OptionGreeks(theta=-6.25)},
    ))
    q = await svc.get_quote("NSE", KEY)
    runner.assert_true("SA2-greeks-present",
                       q is not None and q.greeks is not None)
    if q and q.greeks:
        runner.assert_eq("SA2-delta-preserved", q.greeks.delta, 0.52)
        runner.assert_eq("SA2-vega-preserved", q.greeks.vega, 11.4)
        runner.assert_eq("SA2-theta-updated", q.greeks.theta, -6.25)


async def test_sa3_greeks_whole_clear(runner: R) -> None:
    """SA3: patch reporting greeks=None still clears the whole object."""
    from market.models import OptionGreeks
    from market.service import MarketService, QuotePatch

    svc = MarketService()
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
        received_ts=NOW,
        reported_fields={"greeks": OptionGreeks(delta=0.5)},
    ))
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token=KEY, received_ts=NOW,
        reported_fields={"greeks": None},
    ))
    q = await svc.get_quote("NSE", KEY)
    runner.assert_true("SA3-whole-clear", q is not None and q.greeks is None)


def test_sa4_pdoi_semantics(runner: R) -> None:
    """SA4: pdoi = previous-day OI (NOT change); oi_change derived only
    when both inputs present and provider gave no explicit value."""
    from market.normalize.fyers import depth_from_rest

    payload = {
        "bids": [{"price": 100.0, "volume": 10}],
        "ask": [{"price": 101.0, "volume": 10}],
        "oi": 2000000.0, "pdoi": 1900000.0,
    }
    _d, supp = depth_from_rest(payload, symbol="NSE:NIFTY26AUG24500CE",
                               received_ts=NOW)
    runner.assert_eq("SA4-previous-oi-mapped",
                     supp.get("previous_oi"), 1900000.0)
    runner.assert_eq("SA4-change-derived",
                     supp.get("oi_change"), 100000.0)

    # oipercent is a DIFFERENT canonical field (oi_change_percent) and
    # never triggers/blocks the oi_change derivation.
    payload_pct = dict(payload, oipercent=5.26)
    _dp, suppp = depth_from_rest(payload_pct, symbol="NSE:X", received_ts=NOW)
    runner.assert_eq("SA4-percent-mapped",
                     suppp.get("oi_change_percent"), 5.26)
    runner.assert_eq("SA4-change-still-derived",
                     suppp.get("oi_change"), 100000.0)

    # Only one input present -> no derivation.
    payload3 = {"bids": [], "ask": [], "oi": 2000000.0}
    _d3, supp3 = depth_from_rest(payload3, symbol="NSE:X", received_ts=NOW)
    runner.assert_false("SA4-no-single-input-derive",
                        "oi_change" in supp3)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_pc1_to_pc4_upstox(runner)
    test_pc5_to_pc8_fyers_quotes_depth(runner)
    test_pc9_to_pc10_fyers_ws_depth(runner)
    test_pc11_to_pc12_fyers_greeks(runner)
    test_pc13_to_pc15_models_serialization(runner)
    await test_pc16_to_pc17_service_merge(runner)
    test_pc18_cross_provider_ltp(runner)
    test_pc19_pc20_regression_guards(runner)
    test_sa1_wire_forced_zero_greek(runner)
    await test_sa2_partial_greeks_merge(runner)
    await test_sa3_greeks_whole_clear(runner)
    test_sa4_pdoi_semantics(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)


