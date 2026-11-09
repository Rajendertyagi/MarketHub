#!/usr/bin/env python3
"""Index-constituent identity resolution (the breadth coverage defect).

The universe resolver used the catalogue's general SEARCH - a substring
``LIKE`` with a ``LIMIT`` - so a short constituent symbol matched an
unrelated equity and a long one matched an invitation/bearer variant::

    LT        -> "20 MICRONS LTD"   (the name contains "LT")
    MARUTI    -> "JAY BHARAT MARUTI LTD"
    POWERGRID -> "PGINVIT"          (POWERGRID INFRA. INVITS)
    RELIANCE  -> "RCOM"             (RELIANCE COMMUNICATIONS LTD)

Those four NIFTY 50 constituents were therefore resolved to the WRONG
instrument and reported as "unavailable" (breadth read
``46 quoted / 4 unavailable``) even though a quote existed for them.

This suite proves:
  * the resolver uses the EXACT-symbol read model;
  * the four previously-broken constituents resolve to the right
    instrument and to a usable quote key;
  * a constituent that is genuinely absent from the catalogue stays
    unresolved (a real coverage gap is never concealed).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.instruments import (
    InstrumentCatalog,
    fyers_master_records,
    upstox_master_records,
)
from core.persistence.store import EventStore
from market.market_universe import resolve_universe

#: The four constituents that the substring search got wrong, with the
#: instrument each of them MUST resolve to.
_TARGETS = {
    "LT": ("NSE_EQ|INE018A01030", "NSE:LT-EQ"),
    "MARUTI": ("NSE_EQ|INE585B01010", "NSE:MARUTI-EQ"),
    "POWERGRID": ("NSE_EQ|INE752E01010", "NSE:POWERGRID-EQ"),
    "RELIANCE": ("NSE_EQ|INE002A01018", "NSE:RELIANCE-EQ"),
}

#: Rows that the substring search used to pick instead (alphabetically
#: earlier, or matching on the name only).
_DECOYS = [
    ("NSE_EQ|INE144J01027", "20MICRONS", "20 MICRONS LTD"),
    ("NSE_EQ|INE571B01036", "JAYBARMARU", "JAY BHARAT MARUTI LTD"),
    ("NSE_EQ|INE501A01020", "PGINVIT", "POWERGRID INFRA. INVITS"),
    ("NSE_EQ|INE002Y01018", "RCOM", "RELIANCE COMMUNICATIONS LTD"),
]


def _upstox_rows() -> list[dict]:
    rows: list[dict] = []
    for symbol, (key, _fyers) in _TARGETS.items():
        rows.append({
            "instrument_key": key, "exchange": "NSE", "segment": "NSE_EQ",
            "instrument_type": "EQ", "trading_symbol": symbol,
            "name": f"{symbol} LIMITED", "underlying_symbol": None,
            "expiry": None, "strike_price": None, "lot_size": 1,
            "tick_size": 0.05,
        })
    for key, symbol, name in _DECOYS:
        rows.append({
            "instrument_key": key, "exchange": "NSE", "segment": "NSE_EQ",
            "instrument_type": "EQ", "trading_symbol": symbol,
            "name": name, "underlying_symbol": None, "expiry": None,
            "strike_price": None, "lot_size": 1, "tick_size": 0.05,
        })
    return rows


def _fyers_master() -> bytes:
    obj: dict[str, dict] = {}
    for n, (symbol, (_upstox_key, fyers_symbol)) in enumerate(
            _TARGETS.items(), start=1):
        obj[fyers_symbol] = {
            "fyToken": f"10100000000{n:02d}", "exchange": 10,
            "segment": 10, "exInstType": 0, "symTicker": fyers_symbol,
            "underSym": symbol, "symDetails": f"{symbol} LIMITED",
            "minLotSize": 1,
        }
    return json.dumps(obj).encode()


def _catalog(tmp: str) -> InstrumentCatalog:
    store = EventStore(os.path.join(tmp, "catalog.db"))
    store.replace_provider_instruments(
        "upstox", upstox_master_records(_upstox_rows()))
    store.replace_provider_instruments(
        "fyers", fyers_master_records(_fyers_master()))
    return InstrumentCatalog(store)


def test_the_four_broken_constituents_resolve_to_the_right_instrument(
        ) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        members = {m.symbol: m for m in resolve_universe("NIFTY50", catalog)}

        for symbol, (upstox_key, _fyers_symbol) in _TARGETS.items():
            member = members[symbol]
            assert member.instrument_token, (
                f"{symbol} must resolve to a quote key")
            assert member.instrument_token == upstox_key, (
                f"{symbol} resolved to the WRONG instrument "
                f"({member.instrument_token})")


def test_the_decoys_are_never_resolved_as_a_constituent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        members = {m.symbol: m for m in resolve_universe("NIFTY50", catalog)}
        decoy_keys = {key for key, _sym, _name in _DECOYS}
        for symbol, member in members.items():
            if member.instrument_token in decoy_keys:
                raise AssertionError(
                    f"{symbol} resolved to a decoy row "
                    f"({member.instrument_token})")


def test_a_genuinely_missing_constituent_stays_unresolved() -> None:
    """A real coverage gap is reported, never concealed."""
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        members = {m.symbol: m for m in resolve_universe("NIFTY50", catalog)}
        # TCS is not in the fixture -> it stays unresolved.
        assert members["TCS"].instrument_token is None
        assert members["TCS"].name is None


def test_the_quote_key_is_the_provider_feed_key() -> None:
    """The member's key is the key the quote state is written under."""
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        members = {m.symbol: m for m in resolve_universe("NIFTY50", catalog)}
        for symbol, (upstox_key, _fyers_symbol) in _TARGETS.items():
            assert members[symbol].instrument_token == upstox_key
        # The FYERS row of the same instrument is reachable through the
        # catalogue's own quote key (the identity is the instrument, not
        # the provider's key).
        row = catalog.find_equity("LT", provider="fyers", exchange="NSE")
        assert row is not None
        assert catalog.quote_key(row) == "NSE:LT-EQ"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
