#!/usr/bin/env python3
"""Fyers option-chain greeks + OI analytics tests (S3).

Verifies:
  * the REST adapter requests ``greeks=1`` so the response carries greeks
  * ``oichp`` maps to ``oi_change_percent``
  * nested greeks (delta/gamma/theta/vega/iv) populate, with IV converted
    to the canonical decimal FRACTION (percent -> fraction)
  * aggregate ``callOi``/``putOi`` are exposed as provider-reported
    aggregates whose scope remains UNVERIFIED (never labelled whole-expiry)
  * absent fields stay None (no fabrication)
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from brokers.fyers.rest import HttpResponse


def _transport_capture(captured, body=b'{"s":"ok","data":{"optionsChain":[]}}'):
    def transport(method, url, headers, body_bytes, timeout):
        captured["url"] = url
        return HttpResponse(status=200, headers={}, body=body)
    return transport


def test_option_chain_request_sends_greeks() -> None:
    from brokers.fyers.rest import FyersRest
    captured: dict = {}
    rest = FyersRest(sync_transport=_transport_capture(captured))
    asyncio.run(rest.option_chain("TOKEN", "NSE:NIFTY50-INDEX"))
    assert "greeks=1" in captured["url"]


def _leg(strike, opt_type, **kw):
    leg = {"strike_price": strike, "option_type": opt_type, "ltp": 100.0,
           "oi": 1000.0, "prev_oi": 900.0, "oich": 100.0, "oichp": 10.0}
    leg.update(kw)
    return leg


def _payload(legs, **data_extra):
    data = {"optionsChain": legs}
    data.update(data_extra)
    return {"s": "ok", "data": data}


def test_leg_maps_oichp_to_oi_change_percent() -> None:
    from market.normalize.fyers import option_chain_from_rest
    snap = option_chain_from_rest(
        _payload([_leg(24000, "CE")]),
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", expiry="2026-10-23")
    call = snap.strikes[0].call
    assert call is not None
    assert call.oi_change_percent == 10.0
    assert call.oi_change == 100.0


def test_leg_greeks_iv_converted_to_fraction() -> None:
    from market.normalize.fyers import option_chain_from_rest
    leg = _leg(24000, "CE", greeks={"delta": 0.5, "gamma": 0.01,
                                    "theta": -1.2, "vega": 3.4, "iv": 14.2})
    snap = option_chain_from_rest(
        _payload([leg]),
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", expiry="2026-10-23")
    call = snap.strikes[0].call
    assert call is not None
    assert call.delta == 0.5
    assert call.gamma == 0.01
    assert call.theta == -1.2
    assert call.vega == 3.4
    # IV is a decimal FRACTION at the canonical boundary (14.2% -> 0.142).
    assert call.iv == 0.142


def test_aggregate_call_oi_put_oi_exposed_as_unverified() -> None:
    from market.normalize.fyers import option_chain_from_rest
    snap = option_chain_from_rest(
        _payload([_leg(24000, "CE"), _leg(24000, "PE")],
                 callOi=123456.0, putOi=234567.0),
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", expiry="2026-10-23")
    # Provider-reported aggregates are exposed verbatim; their scope is
    # UNVERIFIED and must never be labelled whole-expiry.
    assert snap.call_oi == 123456.0
    assert snap.put_oi == 234567.0


def test_aggregate_absent_is_none() -> None:
    from market.normalize.fyers import option_chain_from_rest
    snap = option_chain_from_rest(
        _payload([_leg(24000, "CE")]),
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", expiry="2026-10-23")
    assert snap.call_oi is None
    assert snap.put_oi is None


def test_leg_missing_oichp_is_none() -> None:
    from market.normalize.fyers import option_chain_from_rest
    leg = _leg(24000, "CE")
    leg.pop("oichp")
    snap = option_chain_from_rest(
        _payload([leg]),
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", expiry="2026-10-23")
    call = snap.strikes[0].call
    assert call is not None
    assert call.oi_change_percent is None


def test_leg_missing_greeks_keeps_iv_none() -> None:
    from market.normalize.fyers import option_chain_from_rest
    leg = _leg(24000, "CE")
    leg.pop("greeks", None)
    snap = option_chain_from_rest(
        _payload([leg]),
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", expiry="2026-10-23")
    call = snap.strikes[0].call
    assert call is not None
    assert call.iv is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
