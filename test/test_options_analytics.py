#!/usr/bin/env python3
"""Derived options-analytics tests (OA1-OA21).

Validates the 17 derived-analytics tools' pure layer (market.analytics) over a
synthetic OptionChainSnapshot, plus the MCP tool registration. Mirrors the
output keys TBMCP exposed, so the analytics behave identically whichever broker
served the chain.

NO LIVE BROKER. Hand-computed expected values for a 5-strike NIFTY-like chain.
"""

from __future__ import annotations

import asyncio
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

from market.models import (  # noqa: E402
    OptionChainSnapshot,
    OptionContractData,
    OptionStrikeRow,
)
from market.analytics.option_chain import (  # noqa: E402
    analyze_option_chain,
    classify_buildup,
    compute_atm,
    compute_futures_basis,
    compute_gex,
    compute_iv_skew,
    compute_max_pain,
    compute_oi_buildup,
    compute_pcr,
    compute_straddle,
    compute_support_resistance,
    compute_top_oi_strikes,
)
from market.analytics.strategies import (  # noqa: E402
    price_bear_put_spread,
    price_bull_call_spread,
    price_iron_condor,
    price_long_butterfly,
    price_long_strangle,
    price_long_straddle,
    price_strategy,
)
from mcp_server.contract import (  # noqa: E402
    TOOL_ANALYZE_OPTION_CHAIN,
    TOOL_COMPUTE_ATM,
    TOOL_COMPUTE_FUTURES_BASIS,
    TOOL_COMPUTE_GEX,
    TOOL_COMPUTE_IV_SKEW,
    TOOL_COMPUTE_MAX_PAIN,
    TOOL_COMPUTE_OI_BUILDUP,
    TOOL_COMPUTE_PCR,
    TOOL_COMPUTE_STRADDLE,
    TOOL_COMPUTE_SUPPORT_RESISTANCE,
    TOOL_COMPUTE_TOP_OI_STRIKES,
    TOOL_PRICE_BEAR_PUT_SPREAD,
    TOOL_PRICE_BULL_CALL_SPREAD,
    TOOL_PRICE_IRON_CONDOR,
    TOOL_PRICE_LONG_BUTTERFLY,
    TOOL_PRICE_LONG_STRADDLE,
    TOOL_PRICE_LONG_STRANGLE,
)


def _leg(oi, ltp, iv, gamma, oi_change, close):
    return OptionContractData(
        ltp=float(ltp), oi=float(oi), previous_oi=float(oi) - float(oi_change),
        oi_change=float(oi_change), close=float(close),
        iv=float(iv), gamma=float(gamma),
    )


def _build_snapshot():
    """5-strike NIFTY-like chain, spot 25000, ATM 25000. Hand-computed below."""
    rows = [
        OptionStrikeRow(strike=24800, atm=False,
                        call=_leg(1000, 250, 0.20, 0.010, 200, 240),
                        put=_leg(500, 30, 0.22, 0.008, -100, 31)),
        OptionStrikeRow(strike=24900, atm=False,
                        call=_leg(1500, 160, 0.19, 0.012, 300, 150),
                        put=_leg(800, 70, 0.21, 0.009, 50, 70)),
        OptionStrikeRow(strike=25000, atm=True,
                        call=_leg(2000, 90, 0.18, 0.015, -200, 95),
                        put=_leg(2000, 95, 0.18, 0.015, 100, 95)),
        OptionStrikeRow(strike=25100, atm=False,
                        call=_leg(1200, 40, 0.20, 0.010, 100, 40),
                        put=_leg(1500, 160, 0.19, 0.012, -50, 165)),
        OptionStrikeRow(strike=25200, atm=False,
                        call=_leg(600, 15, 0.22, 0.006, 0, 15),
                        put=_leg(1800, 250, 0.20, 0.010, 400, 240)),
    ]
    return OptionChainSnapshot(
        instrument_token="NSE_INDEX|NIFTY",
        exchange="NSE",
        tradingsymbol="NIFTY",
        expiry="2026-09-25",
        spot_price=25000.0,
        atm_strike=25000.0,
        strikes=tuple(rows),
    )


# ─── OA1-OA9: compute_* functions ────────────────────────────────────────────

def test_oa1_pcr(runner: R) -> None:
    r = compute_pcr(_build_snapshot())
    runner.assert_eq("OA1-pcr", r["pcr"], 1.0476)
    runner.assert_eq("OA1-total_call_oi", r["total_call_oi"], 6300)
    runner.assert_eq("OA1-total_put_oi", r["total_put_oi"], 6600)
    runner.assert_eq("OA1-interp", r["interpretation"], "Balanced sentiment")


def test_oa2_max_pain(runner: R) -> None:
    r = compute_max_pain(_build_snapshot())
    runner.assert_eq("OA2-max_pain", r["max_pain"], 25000)
    runner.assert_eq("OA2-underlying", r["underlying_value"], 25000.0)


def test_oa3_atm(runner: R) -> None:
    r = compute_atm(_build_snapshot())
    runner.assert_eq("OA3-atm_strike", r["atm_strike"], 25000)
    runner.assert_eq("OA3-underlying", r["underlying_value"], 25000.0)


def test_oa4_iv_skew(runner: R) -> None:
    r = compute_iv_skew(_build_snapshot())
    runner.assert_eq("OA4-otm_call_avg_iv", r["otm_call_avg_iv"], 0.21)
    runner.assert_eq("OA4-otm_put_avg_iv", r["otm_put_avg_iv"], 0.215)
    runner.assert_eq("OA4-skew", r["skew"], 0.005)


def test_oa5_oi_buildup(runner: R) -> None:
    r = compute_oi_buildup(_build_snapshot())
    # Hand count: 3 Long Buildup, 3 Long Unwinding, 4 Neutral = 10 legs.
    runner.assert_eq("OA5-total_legs", r["total_legs"], 10)
    runner.assert_eq("OA5-long_buildup", r["buildup_counts"].get("Long Buildup"), 3)
    runner.assert_eq("OA5-long_unwinding", r["buildup_counts"].get("Long Unwinding"), 3)
    runner.assert_eq("OA5-neutral", r["buildup_counts"].get("Neutral"), 4)


def test_oa6_support_resistance(runner: R) -> None:
    r = compute_support_resistance(_build_snapshot())
    runner.assert_eq("OA6-support", r["support"], 25000)
    runner.assert_eq("OA6-support_oi", r["support_oi"], 2000)
    runner.assert_eq("OA6-resistance", r["resistance"], 25000)
    runner.assert_eq("OA6-resistance_oi", r["resistance_oi"], 2000)


def test_oa7_straddle(runner: R) -> None:
    r = compute_straddle(_build_snapshot())
    runner.assert_eq("OA7-atm", r["atm_strike"], 25000)
    runner.assert_eq("OA7-cost", r["straddle_cost"], 185.0)
    runner.assert_eq("OA7-upper_be", r["upper_breakeven"], 25185.0)
    runner.assert_eq("OA7-lower_be", r["lower_breakeven"], 24815.0)


def test_oa8_gex(runner: R) -> None:
    r = compute_gex(_build_snapshot())
    runner.assert_eq("OA8-call_gex", r["call_gamma_exposure"], 73.6)
    runner.assert_eq("OA8-put_gex", r["put_gamma_exposure"], 77.2)
    runner.assert_eq("OA8-net_gex", r["net_gex"], -3.6)
    runner.assert_eq("OA8-interp", r["interpretation"],
                     "negative (dealers short gamma, amplifying)")


def test_oa9_top_oi(runner: R) -> None:
    r = compute_top_oi_strikes(_build_snapshot(), n=2)
    runner.assert_eq("OA9-top_call", r["top_call_oi"],
                     [{"strike": 25000, "oi": 2000}, {"strike": 24900, "oi": 1500}])
    runner.assert_eq("OA9-top_put", r["top_put_oi"],
                     [{"strike": 25000, "oi": 2000}, {"strike": 25200, "oi": 1800}])


# ─── OA10: futures basis ──────────────────────────────────────────────────────

def test_oa10_futures_basis(runner: R) -> None:
    legs = [
        {"expiry": "2026-09-25", "last_price": 25100.0},
        {"expiry": "2026-10-30", "last_price": 25250.0},
    ]
    r = compute_futures_basis(legs, 25000.0)
    runner.assert_eq("OA10-spot", r["spot"], 25000.0)
    runner.assert_eq("OA10-n-contracts", len(r["contracts"]), 2)
    runner.assert_eq("OA10-c1-basis", r["contracts"][0]["basis"], 100.0)
    runner.assert_eq("OA10-c1-pct", r["contracts"][0]["basis_pct"], 0.4)
    runner.assert_eq("OA10-c2-basis", r["contracts"][1]["basis"], 250.0)
    runner.assert_eq("OA10-c2-pct", r["contracts"][1]["basis_pct"], 1.0)


# ─── OA11-OA16: strategy pricers ─────────────────────────────────────────────

def test_oa11_long_straddle(runner: R) -> None:
    r = price_long_straddle(_build_snapshot())
    runner.assert_eq("OA11-net_debit", r["net_debit"], -185.0)
    runner.assert_eq("OA11-max_loss", r["max_loss"], -185.0)
    runner.assert_eq("OA11-n-breakevens", len(r["breakevens"]), 2)
    # Breakevens ~25000 +/- 185 (numeric search tolerance).
    runner.assert_le("OA11-be-low", abs(r["breakevens"][0] - 24815.0), 250.0)
    runner.assert_le("OA11-be-high", abs(r["breakevens"][1] - 25185.0), 250.0)


def test_oa12_long_strangle(runner: R) -> None:
    r = price_long_strangle(_build_snapshot(), call_strike=25100, put_strike=24900)
    # call 25100 ltp=40, put 24900 ltp=70 -> net debit = -(40+70) = -110
    runner.assert_eq("OA12-net_debit", r["net_debit"], -110.0)
    runner.assert_eq("OA12-max_loss", r["max_loss"], -110.0)


def test_oa13_bull_call_spread(runner: R) -> None:
    r = price_bull_call_spread(_build_snapshot(), lower_strike=24900, higher_strike=25000)
    # buy 24900 call (160) sell 25000 call (90) -> net = -(160) + 90 = -70
    runner.assert_eq("OA13-net_debit", r["net_debit"], -70.0)
    runner.assert_eq("OA13-legs", len(r["legs"]), 2)


def test_oa14_bear_put_spread(runner: R) -> None:
    r = price_bear_put_spread(_build_snapshot(), higher_strike=25000, lower_strike=24900)
    # buy 25000 put (95) sell 24900 put (70) -> net = -(95) + 70 = -25
    runner.assert_eq("OA14-net_debit", r["net_debit"], -25.0)
    runner.assert_eq("OA14-legs", len(r["legs"]), 2)


def test_oa15_iron_condor(runner: R) -> None:
    r = price_iron_condor(_build_snapshot(), 24900, 24800, 25100, 25200)
    # sell 24900 put (70) buy 24800 put (30) buy 25100 call (40) sell 25200 call (15)
    # net = -(-70) + (-30) + (-40) + -(-15) = 70 -30 -40 +15 = 15
    runner.assert_eq("OA15-net_debit", r["net_debit"], 15.0)
    runner.assert_eq("OA15-legs", len(r["legs"]), 4)


def test_oa16_long_butterfly(runner: R) -> None:
    r = price_long_butterfly(_build_snapshot(), 24900, 25000, 25100)
    # buy 24900 call (160) sell 2x 25000 call (90*2) buy 25100 call (40)
    # net = -(160) + 2*90 + -(40) = -160 + 180 - 40 = -20
    runner.assert_eq("OA16-net_debit", r["net_debit"], -20.0)
    runner.assert_eq("OA16-legs", len(r["legs"]), 3)


# ─── OA17: composite analyze ─────────────────────────────────────────────────

def test_oa17_analyze(runner: R) -> None:
    r = analyze_option_chain(_build_snapshot())
    runner.assert_eq("OA17-source", r["source"], "derived")
    for key in ("pcr", "max_pain", "atm", "support_resistance",
                "oi_buildup", "iv_skew", "gex"):
        runner.assert_true(f"OA17-has-{key}", key in r)
    runner.assert_eq("OA17-pcr-eq", r["pcr"]["pcr"], 1.0476)


# ─── OA18-OA21: MCP contract wiring ──────────────────────────────────────────

def test_oa18_contract_constants(runner: R) -> None:
    names = {
        TOOL_COMPUTE_PCR, TOOL_COMPUTE_MAX_PAIN, TOOL_COMPUTE_TOP_OI_STRIKES,
        TOOL_COMPUTE_ATM, TOOL_COMPUTE_IV_SKEW, TOOL_COMPUTE_OI_BUILDUP,
        TOOL_COMPUTE_SUPPORT_RESISTANCE, TOOL_COMPUTE_STRADDLE, TOOL_COMPUTE_GEX,
        TOOL_COMPUTE_FUTURES_BASIS, TOOL_PRICE_LONG_STRADDLE, TOOL_PRICE_LONG_STRANGLE,
        TOOL_PRICE_BULL_CALL_SPREAD, TOOL_PRICE_BEAR_PUT_SPREAD, TOOL_PRICE_IRON_CONDOR,
        TOOL_PRICE_LONG_BUTTERFLY, TOOL_ANALYZE_OPTION_CHAIN,
    }
    runner.assert_eq("OA18-total-tools", len(names), 17)
    # Distinct, lower-case tool names (MCP public contract).
    runner.assert_eq("OA18-distinct", len({n.lower() for n in names}), 17)


def test_oa19_tools_module_importable(runner: R) -> None:
    from mcp_server.tools.options_analytics_tools import register_options_analytics_tools
    runner.assert_true("OA19-import", callable(register_options_analytics_tools))


def test_oa20_registered_in_server(runner: R) -> None:
    import app.server as srv
    runner.assert_in("OA20-import", "register_options_analytics_tools",
                     open(srv.__file__, encoding="utf-8").read())


def test_oa21_registered_in_tools_init(runner: R) -> None:
    import mcp_server.tools as tmod
    runner.assert_true(
        "OA21-init-export",
        hasattr(tmod, "register_options_analytics_tools"))


async def main() -> bool:
    runner = R()
    test_oa1_pcr(runner)
    test_oa2_max_pain(runner)
    test_oa3_atm(runner)
    test_oa4_iv_skew(runner)
    test_oa5_oi_buildup(runner)
    test_oa6_support_resistance(runner)
    test_oa7_straddle(runner)
    test_oa8_gex(runner)
    test_oa9_top_oi(runner)
    test_oa10_futures_basis(runner)
    test_oa11_long_straddle(runner)
    test_oa12_long_strangle(runner)
    test_oa13_bull_call_spread(runner)
    test_oa14_bear_put_spread(runner)
    test_oa15_iron_condor(runner)
    test_oa16_long_butterfly(runner)
    test_oa17_analyze(runner)
    test_oa18_contract_constants(runner)
    test_oa19_tools_module_importable(runner)
    test_oa20_registered_in_server(runner)
    test_oa21_registered_in_tools_init(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
