#!/usr/bin/env python3
"""Upstox V3 REST previous-OI mapping tests (S4).

Contract: Upstox V3 ``/v3/market-quote/quotes`` returns ``previous_oi``
for F&O (REST-only). The WebSocket ``full`` mode does NOT carry it on the
wire. The canonical ``Quote.previous_oi`` is populated from REST; OI change
and OI% are derived by :mod:`market.oi` with the approved precedence
(provider -> persisted -> unavailable).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def _entry(**kw):
    entry = {
        "instrument_token": "NSE_FO|12345",
        "symbol": "NIFTY25OCT24500CE",
        "last_price": 100.0,
        "oi": 1500.0,
        "volume": 1000.0,
    }
    entry.update(kw)
    return entry


def _ts():
    return datetime.now(timezone.utc)


def test_rest_quote_maps_previous_oi() -> None:
    from market.normalize.upstox import quote_from_rest
    q = quote_from_rest(_entry(previous_oi=1200.0), received_ts=_ts())
    assert q.previous_oi == 1200.0
    assert q.open_interest == 1500.0


def test_rest_quote_previous_oi_absent_is_none() -> None:
    from market.normalize.upstox import quote_from_rest
    q = quote_from_rest(_entry(), received_ts=_ts())
    assert q.previous_oi is None


def test_oi_change_derived_from_previous_oi() -> None:
    from market.normalize.upstox import quote_from_rest
    from market.oi import oi_change_and_pct
    q = quote_from_rest(_entry(previous_oi=1200.0), received_ts=_ts())
    oi, prev, change, pct, basis = oi_change_and_pct(q)
    assert oi == 1500.0
    assert prev == 1200.0
    assert change == 300.0
    assert abs(pct - 25.0) < 1e-9
    assert basis == "provider"


def test_provider_previous_oi_wins_over_persisted() -> None:
    from market.normalize.upstox import quote_from_rest
    from market.oi import oi_change_and_pct
    q = quote_from_rest(_entry(previous_oi=1200.0), received_ts=_ts())
    _, prev, _, _, basis = oi_change_and_pct(q, persisted_previous_oi=999.0)
    assert prev == 1200.0
    assert basis == "provider"


def test_persisted_previous_oi_fallback() -> None:
    from market.normalize.upstox import quote_from_rest
    from market.oi import oi_change_and_pct
    q = quote_from_rest(_entry(), received_ts=_ts())
    _, prev, change, _, basis = oi_change_and_pct(q, persisted_previous_oi=1000.0)
    assert prev == 1000.0
    assert change == 500.0
    assert basis == "persisted"


def test_ws_full_mode_has_no_previous_oi() -> None:
    """The WS ``full`` field map must not carry previous_oi (REST-only)."""
    from market.normalize import upstox as u
    src = open(u.__file__, encoding="utf-8").read()
    start = src.find("def quote_fields_from_ws_full")
    end = src.find("def quote_fields_from_ws_first_level_with_greeks")
    block = src[start:end]
    assert "previous_oi" not in block


def test_option_chain_prev_oi_still_maps() -> None:
    """Regression: the option-chain ``prev_oi`` mapping is unchanged."""
    from market.normalize.upstox import option_chain_from_rest
    payload = {"data": [{
        "strike_price": 24500.0, "atm": False,
        "call_options": {
            "market_data": {"ltp": 100.0, "oi": 1500.0},
            "option_greeks": {}},
        "put_options": None}]}
    snap = option_chain_from_rest(
        payload, instrument_token="NSE_FO|X", exchange="NSE",
        tradingsymbol="NIFTY", expiry="2026-10-23")
    assert snap.strikes[0].call is not None
    assert snap.strikes[0].call.oi == 1500.0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
