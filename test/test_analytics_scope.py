#!/usr/bin/env python3
"""Analytics scope + labelling tests (S6).

Locked contract (three tiers):
  * ``window``               — locally computed over the returned strike rows
  * ``aggregate-unverified`` — provider-reported aggregate OI (scope UNVERIFIED)
  * ``whole-expiry``         — full-chain source (Upstox only)

Fyers max-pain stays bounded (window-bounded, never whole-expiry).
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def _fyers_snapshot():
    from market.normalize.fyers import option_chain_from_rest
    return option_chain_from_rest(
        {"s": "ok", "data": {"optionsChain": [
            {"strike_price": 24000, "option_type": "", "ltp": 24010.0},
            {"strike_price": 24000, "option_type": "CE", "ltp": 100.0,
             "oi": 1000.0},
            {"strike_price": 24000, "option_type": "PE", "ltp": 50.0,
             "oi": 2000.0},
        ]}},
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE",
        tradingsymbol="NIFTY", expiry="2026-10-23")


def _upstox_snapshot():
    from market.normalize.upstox import option_chain_from_rest
    return option_chain_from_rest(
        {"data": [{"strike_price": 24000.0, "atm": False,
                   "call_options": {"market_data": {"ltp": 100.0, "oi": 1000.0},
                                    "option_greeks": {}},
                   "put_options": {"market_data": {"ltp": 50.0, "oi": 2000.0},
                                   "option_greeks": {}}}]},
        instrument_token="NSE_INDEX|NIFTY", exchange="NSE",
        tradingsymbol="NIFTY", expiry="2026-10-23")


def test_compute_pcr_scope_is_window_for_fyers() -> None:
    from market.analytics.option_chain import compute_pcr
    r = compute_pcr(_fyers_snapshot())
    assert r["scope"] == "window"
    assert r["pcr"] == 2.0


def test_compute_pcr_scope_is_whole_expiry_for_upstox() -> None:
    from market.analytics.option_chain import compute_pcr
    r = compute_pcr(_upstox_snapshot())
    assert r["scope"] == "whole-expiry"
    assert r["pcr"] == 2.0


def test_compute_pcr_volume_scope_is_window_for_fyers() -> None:
    from market.analytics.option_chain import compute_pcr_volume
    r = compute_pcr_volume(_fyers_snapshot())
    assert r["scope"] == "window"


def test_compute_max_pain_is_bounded_for_fyers() -> None:
    from market.analytics.option_chain import compute_max_pain
    r = compute_max_pain(_fyers_snapshot())
    assert r["scope"] == "window"
    assert r["bounded"] is True


def test_compute_max_pain_is_unbounded_for_upstox() -> None:
    from market.analytics.option_chain import compute_max_pain
    r = compute_max_pain(_upstox_snapshot())
    assert r["scope"] == "whole-expiry"
    assert r["bounded"] is False


def test_compute_pcr_aggregate_is_unverified() -> None:
    from market.analytics.option_chain import compute_pcr_aggregate
    r = compute_pcr_aggregate(call_oi=1000.0, put_oi=2000.0)
    assert r["scope"] == "aggregate-unverified"
    assert r["pcr"] == 2.0
    assert r["total_call_oi"] == 1000.0
    assert r["total_put_oi"] == 2000.0


def test_compute_pcr_aggregate_can_claim_whole_expiry_when_evidenced() -> None:
    """The scope is caller-supplied: whole-expiry only with evidence."""
    from market.analytics.option_chain import compute_pcr_aggregate
    r = compute_pcr_aggregate(call_oi=1000.0, put_oi=2000.0,
                              scope="whole-expiry")
    assert r["scope"] == "whole-expiry"
    assert r["pcr"] == 2.0


def test_compute_pcr_aggregate_rejects_an_unknown_scope() -> None:
    from market.analytics.option_chain import compute_pcr_aggregate
    try:
        compute_pcr_aggregate(call_oi=1.0, put_oi=2.0, scope="window")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown aggregate scope must be rejected")


def test_fyers_snapshot_declares_evidence_backed_aggregate_scope() -> None:
    """Fyers' aggregate is EVIDENCED whole-expiry (live U3 capture)."""
    from market.normalize.fyers import option_chain_from_rest
    snap = option_chain_from_rest(
        {"s": "ok", "data": {
            "optionsChain": [
                {"strike_price": 24000, "option_type": "CE", "ltp": 100.0,
                 "oi": 1000.0},
                {"strike_price": 24000, "option_type": "PE", "ltp": 50.0,
                 "oi": 2000.0},
            ],
            "callOi": 145731720.0, "putOi": 127688035.0,
        }},
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE",
        tradingsymbol="NIFTY", expiry="2026-10-27")
    # The returned strike rows are still window-scoped.
    assert snap.scope == "window"
    # The provider aggregate is whole-expiry (evidence-backed).
    assert snap.aggregate_scope == "whole-expiry"
    assert snap.call_oi == 145731720.0
    assert snap.put_oi == 127688035.0


def test_compute_pcr_aggregate_zero_call_is_none() -> None:
    from market.analytics.option_chain import compute_pcr_aggregate
    r = compute_pcr_aggregate(call_oi=0.0, put_oi=2000.0)
    assert r["pcr"] is None
    assert r["scope"] == "aggregate-unverified"


def test_analytics_snapshot_carries_scope() -> None:
    from market.models import OptionChainAnalyticsSnapshot
    snap = OptionChainAnalyticsSnapshot(
        chain_key="analytics:X:2026-10-23", canonical_underlying_id="X",
        exchange="NSE", tradingsymbol="NIFTY", expiry="2026-10-23",
        pcr_oi=2.0, scope="window")
    assert snap.scope == "window"


def test_analytics_snapshot_scope_defaults_to_window() -> None:
    from market.models import OptionChainAnalyticsSnapshot
    snap = OptionChainAnalyticsSnapshot(
        chain_key="analytics:X:2026-10-23", canonical_underlying_id="X",
        exchange="NSE", tradingsymbol="NIFTY", expiry="2026-10-23")
    assert snap.scope == "window"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
