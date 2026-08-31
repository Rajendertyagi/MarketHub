#!/usr/bin/env python3
"""B2 condition metric registry tests.

Covers the 27 Quote-backed metrics a ``market_condition`` alert may reference:

  * CM1  metric registry is exactly the 27 frozen metrics (no drift)
  * CM2  direct scalar extraction matrix (all 20 direct fields)
  * CM3  greeks.* extraction (6 metrics) — present and absent snapshots
  * CM4  spread derivation — ask-bid, UNKNOWN unless both sides present
  * CM5  zero is a valid value, never treated as missing
  * CM6  None (UNKNOWN) is never treated as zero
  * CM7  unknown metric -> KeyError

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

from market.condition_metrics import (
    METRIC_NAMES,
    METRIC_SET,
    extract_metric,
    metric_value,
)
from market.models import OptionGreeks, Quote

_EXPECTED_27 = (
    "ltp", "open", "high", "low", "close",
    "change", "change_percent", "avg_trade_price", "last_traded_qty",
    "volume", "total_buy_qty", "total_sell_qty", "open_interest",
    "previous_oi", "oi_change", "oi_change_percent",
    "best_bid", "best_ask", "spread",
    "upper_circuit", "lower_circuit",
    "greeks.delta", "greeks.gamma", "greeks.theta", "greeks.vega",
    "greeks.rho", "greeks.iv",
)

# B6B analytics metrics.
_EXPECTED_B6B = (
    "pcr_oi", "pcr_volume", "max_pain", "iv_skew",
)

_EXPECTED_31 = _EXPECTED_27 + _EXPECTED_B6B


def _mk_quote(**kw) -> Quote:
    base = dict(
        instrument_token="T1", exchange="NSE", tradingsymbol="AAA",
        received_ts=datetime.now(timezone.utc),
    )
    base.update(kw)
    return Quote(**base)


def _full_quote() -> Quote:
    return _mk_quote(
        ltp=100.5, open=99.0, high=102.0, low=98.5, close=99.5,
        change=1.0, change_percent=1.01, avg_trade_price=100.2,
        last_traded_qty=25, volume=100000, total_buy_qty=5000,
        total_sell_qty=4500, open_interest=12345, previous_oi=12000,
        oi_change=345, oi_change_percent=2.875, best_bid=100.4,
        best_ask=100.6, upper_circuit=110.0, lower_circuit=90.0,
        greeks=OptionGreeks(delta=0.55, gamma=0.002, theta=-0.12,
                            vega=0.35, rho=0.05, iv=18.5),
    )


def test_cm1_registry_exact(runner: R) -> None:
    runner.assert_eq("CM1-count", len(METRIC_NAMES), 27)
    runner.assert_eq("CM1-names", METRIC_NAMES, _EXPECTED_27)
    runner.assert_eq("CM1-set", METRIC_SET, frozenset(_EXPECTED_31))
    runner.assert_true("CM1-no-dupes", len(set(METRIC_NAMES)) == 27,
                       "duplicate metric names")


def test_cm2_direct_scalars(runner: R) -> None:
    q = _full_quote()
    expected = {
        "ltp": 100.5, "open": 99.0, "high": 102.0, "low": 98.5,
        "close": 99.5, "change": 1.0, "change_percent": 1.01,
        "avg_trade_price": 100.2, "last_traded_qty": 25, "volume": 100000,
        "total_buy_qty": 5000, "total_sell_qty": 4500,
        "open_interest": 12345, "previous_oi": 12000, "oi_change": 345,
        "oi_change_percent": 2.875, "best_bid": 100.4, "best_ask": 100.6,
        "upper_circuit": 110.0, "lower_circuit": 90.0,
    }
    for metric, want in expected.items():
        got = extract_metric(q, metric)
        runner.assert_eq(f"CM2-{metric}", got, want)


def test_cm3_greeks(runner: R) -> None:
    q = _full_quote()
    runner.assert_eq("CM3-delta", extract_metric(q, "greeks.delta"), 0.55)
    runner.assert_eq("CM3-gamma", extract_metric(q, "greeks.gamma"), 0.002)
    runner.assert_eq("CM3-theta", extract_metric(q, "greeks.theta"), -0.12)
    runner.assert_eq("CM3-vega", extract_metric(q, "greeks.vega"), 0.35)
    runner.assert_eq("CM3-rho", extract_metric(q, "greeks.rho"), 0.05)
    runner.assert_eq("CM3-iv", extract_metric(q, "greeks.iv"), 18.5)
    # No greeks snapshot -> every greeks.* metric is UNKNOWN (None).
    bare = _mk_quote(ltp=100.5)
    for metric in ("greeks.delta", "greeks.gamma", "greeks.theta",
                   "greeks.vega", "greeks.rho", "greeks.iv"):
        runner.assert_eq(f"CM3-absent-{metric}",
                         extract_metric(bare, metric), None)


def test_cm4_spread(runner: R) -> None:
    q = _full_quote()
    got = extract_metric(q, "spread")
    runner.assert_true("CM4-spread", got is not None
                       and abs(got - 0.2) < 1e-9,
                       f"spread expected ~0.2, got {got!r}")
    # Missing bid -> UNKNOWN.
    runner.assert_eq("CM4-no-bid",
                     extract_metric(_mk_quote(best_ask=100.6), "spread"), None)
    # Missing ask -> UNKNOWN.
    runner.assert_eq("CM4-no-ask",
                     extract_metric(_mk_quote(best_bid=100.4), "spread"), None)
    # Neither -> UNKNOWN.
    runner.assert_eq("CM4-neither",
                     extract_metric(_mk_quote(), "spread"), None)


def test_cm5_zero_is_valid(runner: R) -> None:
    q = _mk_quote(ltp=0.0, change=0.0, volume=0, open_interest=0,
                  best_bid=0.0, best_ask=0.0)
    runner.assert_eq("CM5-ltp-zero", extract_metric(q, "ltp"), 0.0)
    runner.assert_eq("CM5-change-zero", extract_metric(q, "change"), 0.0)
    runner.assert_eq("CM5-volume-zero", extract_metric(q, "volume"), 0)
    runner.assert_eq("CM5-oi-zero", extract_metric(q, "open_interest"), 0)
    runner.assert_eq("CM5-spread-zero", extract_metric(q, "spread"), 0.0)


def test_cm6_none_is_unknown(runner: R) -> None:
    q = _mk_quote()  # no ltp, no volume, no greeks
    runner.assert_eq("CM6-ltp", extract_metric(q, "ltp"), None)
    runner.assert_eq("CM6-volume", extract_metric(q, "volume"), None)
    runner.assert_eq("CM6-greeks", extract_metric(q, "greeks.iv"), None)
    # metric_value alias agrees.
    runner.assert_eq("CM6-alias", metric_value(q, "ltp"), None)


def test_cm7_unknown_metric(runner: R) -> None:
    q = _full_quote()
    try:
        extract_metric(q, "not_a_metric")
        runner.fail("CM7-unknown", "expected KeyError")
    except KeyError:
        runner.ok("CM7-unknown")
    try:
        extract_metric(q, "greeks.pop")
        runner.fail("CM7-greeks-pop", "expected KeyError")
    except KeyError:
        runner.ok("CM7-greeks-pop")


async def main() -> bool:
    runner = R()
    test_cm1_registry_exact(runner)
    test_cm2_direct_scalars(runner)
    test_cm3_greeks(runner)
    test_cm4_spread(runner)
    test_cm5_zero_is_valid(runner)
    test_cm6_none_is_unknown(runner)
    test_cm7_unknown_metric(runner)
    return runner.summary()


if __name__ == "__main__":
    import asyncio
    success = asyncio.run(main())
    sys.exit(0 if success else 1)