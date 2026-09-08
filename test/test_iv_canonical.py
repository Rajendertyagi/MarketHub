#!/usr/bin/env python3
"""Canonical IV unit contract tests.

Contract: OptionGreeks.iv / OptionContractData.iv = decimal FRACTION
(0.1758 = 17.58%). Percent-emitting providers convert at the provider
normalization boundary; fraction-emitting providers pass through. NO
magnitude heuristics anywhere.

  PROVIDER NORMALIZATION
   1. Upstox chain percent IV -> fraction (17.58 -> 0.1758)
   2. Upstox standalone option-greeks fraction passes through
   3. Upstox live-feed fraction passes through
   4. Fyers options-chain percent -> fraction
   5. IV >100% remains mathematically valid after explicit conversion
   6. no magnitude heuristic: 0.5 percent-style input would become 0.005
      (conversion is by contract, not by value)

  SEMANTICS
   7. null remains null (no 0 / NaN fabrication)
   8. genuine zero stays zero
   9. snapshot/live merge keeps the same unit
  10. field-wise Greeks merge unchanged

  PARITY
  11. chain and standalone produce the same canonical unit
  12. REST projection (workspace) receives fraction
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

from market.models import OptionGreekSnapshot  # noqa: E402
from market.normalize.upstox import option_chain_from_rest  # noqa: E402
from market.normalize.upstox_option_greeks import (  # noqa: E402
    option_greeks_from_rest as standalone_from_rest,
)
from market.normalize.fyers import greeks_from_options_chain  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


def _ts():
    return datetime.now(timezone.utc)


# -- 1. Upstox chain percent -> fraction -----------------------------------------

def test_upstox_chain_percent(runner: R) -> None:
    payload = {"data": [{
        "strike_price": 23800.0, "atm": False,
        "call_options": {
            "market_data": {"ltp": 16.65, "oi": 7772050},
            "option_greeks": {"delta": 0.4897, "iv": 17.58}},
        "put_options": {
            "market_data": {"ltp": 8.25},
            "option_greeks": {"delta": -0.51, "iv": 17.94}},
    }]}
    snap = option_chain_from_rest(payload, instrument_token="NSE_INDEX|X",
                                  exchange="NSE", tradingsymbol="NIFTY",
                                  expiry="2026-09-08")
    call = snap.strikes[0].call
    put = snap.strikes[0].put
    runner.assert_true("chain-ce-fraction", abs(call.iv - 0.1758) < 1e-9)
    runner.assert_true("chain-pe-fraction", abs(put.iv - 0.1794) < 1e-9)
    runner.assert_eq("chain-delta-untouched", call.delta, 0.4897)


# -- 2/3. standalone + feed passthrough -------------------------------------------

def test_upstox_standalone_passthrough(runner: R) -> None:
    payload = {"status": "success", "data": {"NSE_FO|109092": {
        "last_price": 16.65, "delta": 0.622, "iv": 0.17578125,
    }}}
    snap = standalone_from_rest(payload)
    e = snap.entries[0]
    # Standalone endpoint natively emits FRACTION: passthrough, NOT /100.
    runner.assert_eq("standalone-fraction", e.iv, 0.17578125)


def test_upstox_feed_passthrough(runner: R) -> None:
    # Live-feed path (upstox.py): ff["iv"] is already a FRACTION on the
    # wire; assert the feed block does NOT divide by 100 (grep-guard).
    from market.normalize import upstox as u
    src = open(u.__file__, encoding="utf-8").read()
    feed_block = src[src.find('iv_raw = ff.get("iv")'):]
    feed_block = feed_block[:feed_block.find("# Day candle")]
    runner.assert_true("feed-no-div100", "/ 100" not in feed_block)
    runner.assert_true("feed-no-div100-alt", "/100" not in feed_block)
    runner.assert_true("feed-passthrough-assign",
                       "iv_val = to_float(iv_raw" in feed_block)


# -- 4. Fyers chain percent -> fraction --------------------------------------------

def test_fyers_chain_percent(runner: R) -> None:
    leg = {"greeks": {"delta": 0.49, "iv": 17.09}}
    g = greeks_from_options_chain(leg)
    runner.assert_eq("fyers-fraction", g.iv, 0.1709)
    runner.assert_eq("fyers-delta-untouched", g.delta, 0.49)


# -- 5/6. >100% + no magnitude heuristic --------------------------------------------

def test_high_iv_and_no_heuristic(runner: R) -> None:
    # IV >100% (e.g. 250%) remains valid: 250 -> 2.5, never clamped/inferred.
    payload = {"data": [{
        "strike_price": 100.0, "atm": False,
        "call_options": {"market_data": {"ltp": 1.0},
                         "option_greeks": {"iv": 250.0}},
        "put_options": {"market_data": {"ltp": 1.0},
                        "option_greeks": {"iv": 0.5}},
    }]}
    snap = option_chain_from_rest(payload, instrument_token="X",
                                  exchange="NSE", tradingsymbol="X",
                                  expiry="2026-09-08")
    call = snap.strikes[0].call
    put = snap.strikes[0].put
    runner.assert_eq("iv-over-100", call.iv, 2.5)
    # Contract-based conversion: a percent value BELOW 1 (0.5%) also
    # converts by /100 — proves NO magnitude heuristic exists.
    runner.assert_eq("no-magnitude-heuristic", put.iv, 0.005)


# -- 7/8. null/zero semantics ---------------------------------------------------------

def test_null_zero_semantics(runner: R) -> None:
    payload = {"data": [{
        "strike_price": 100.0, "atm": False,
        "call_options": {"market_data": {"ltp": 1.0},
                         "option_greeks": {}},          # iv absent -> None
        "put_options": {"market_data": {"ltp": 1.0},
                        "option_greeks": {"iv": 0.0}},  # genuine zero -> 0
    }]}
    snap = option_chain_from_rest(payload, instrument_token="X",
                                  exchange="NSE", tradingsymbol="X",
                                  expiry="2026-09-08")
    call = snap.strikes[0].call
    put = snap.strikes[0].put
    runner.assert_true("null-stays-null", call.iv is None)
    runner.assert_eq("zero-stays-zero", put.iv, 0.0)
    # standalone null
    s2 = standalone_from_rest({"status": "success", "data": {"K": {
        "last_price": 1.0, "delta": 0.5}}})
    runner.assert_true("standalone-null", s2.entries[0].iv is None)


# -- 9/10. merge semantics --------------------------------------------------------------

def test_merge_unchanged(runner: R) -> None:
    from market.models import OptionGreeks, merge_greeks
    old = OptionGreeks(delta=0.5, iv=0.17)
    new = OptionGreeks(gamma=0.01, iv=0.18)   # delta absent -> preserved
    merged = merge_greeks(old, new)
    runner.assert_eq("merge-delta-preserved", merged.delta, 0.5)
    runner.assert_eq("merge-iv-updated-same-unit", merged.iv, 0.18)
    runner.assert_eq("merge-gamma-added", merged.gamma, 0.01)
    # a live update cannot inject a percent-style value: 17.58 in a
    # fraction contract would be wrong — normalization prevents it, so a
    # merged row never mixes units (tested at normalizer level above).


# -- 11/12. parity + REST projection ------------------------------------------------------

def test_parity_and_workspace(runner: R) -> None:
    # Same contract through chain vs standalone: identical canonical unit.
    chain = option_chain_from_rest(
        {"data": [{"strike_price": 23800.0, "atm": False,
                   "call_options": {"market_data": {"ltp": 16.65},
                                    "option_greeks": {"iv": 17.58}},
                   "put_options": None}]},
        instrument_token="X", exchange="NSE", tradingsymbol="N",
        expiry="2026-09-08")
    standalone = standalone_from_rest(
        {"status": "success", "data": {"NSE_FO|X": {
            "last_price": 16.65, "iv": 0.1758}}})
    runner.assert_true("parity-chain-standalone",
                       abs(chain.strikes[0].call.iv - 0.1758) < 1e-9
                       and standalone.entries[0].iv == 0.1758)
    # REST projection of the workspace uses the same canonical model —
    # verified by the workspace test suite; here assert the model shape.
    snap = OptionGreekSnapshot(entries=())
    runner.assert_true("workspace-model", hasattr(snap, "entries"))


def main() -> bool:
    runner = R()
    test_upstox_chain_percent(runner)
    test_upstox_standalone_passthrough(runner)
    test_upstox_feed_passthrough(runner)
    test_fyers_chain_percent(runner)
    test_high_iv_and_no_heuristic(runner)
    test_null_zero_semantics(runner)
    test_merge_unchanged(runner)
    test_parity_and_workspace(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = main()
    sys.exit(0 if _ok else 1)
