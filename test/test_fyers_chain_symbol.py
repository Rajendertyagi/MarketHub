#!/usr/bin/env python3
"""Fyers option-chain symbol translation (S: provider-correct request).

LIVE-VERIFIED REQUEST CONTRACT (2026-10-20, read-only authenticated probe
against ``GET /data/options-chain-v3``; see
``test/test_fyers_chain_request_form.py``):

  * ``NSE:ADANIGREEN-EQ`` (underlying equity symbol) -> ``s=ok, code=200``
  * ``NSE:NIFTY50-INDEX`` (underlying index symbol)   -> ``s=ok, code=200``
  * ``NSE:ADANIGREEN26OCT1000CE`` (contract symbol)   -> ``s=ok, code=200``
    (Fyers resolves it back to the same underlying chain)
  * ``10100000003563`` (numeric fyToken)              -> ``s=error, code=-300``
    "Please provide a valid symbol"

The endpoint is therefore addressed by the UNDERLYING's provider symbol —
the documented request attribute. The previous implementation sent a
CONTRACT symbol (a guess) or, before that, the numeric fyToken (which the
provider rejects, leaving the workspace chain snapshot always empty).

The symbol is resolved from the instrument catalogue's CASH/INDEX row for
the underlying — so short symbols (IOC, LT) resolve correctly, and no
string-shape guess is involved.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class _FakeRest:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def option_chain(self, token, symbol, strikecount=50, timestamp=None,
                           expiry=""):
        self.calls.append({"symbol": symbol, "expiry": expiry,
                            "timestamp": timestamp})
        return {"symbol": symbol}


class _Catalog:
    """Catalogue stub exposing the exact-match helpers."""

    def __init__(self, rows):
        self._rows = rows
        self.queries: list[dict] = []

    def search(self, **kw):
        self.queries.append(kw)
        if kw.get("instrument_type") != "OPTION":
            return []
        return [dict(r) for r in self._rows
                if (not kw.get("expiry")
                    or r.get("expiry") == kw.get("expiry"))]

    def find_equity(self, symbol, *, provider=None, exchange=None):
        for r in self._rows:
            if (r.get("tradingsymbol") or "").upper() == symbol.upper():
                return dict(r)
        return None

    def find_index(self, symbol, *, provider=None, exchange=None):
        for r in self._rows:
            if (r.get("underlying") or "").upper() == symbol.upper():
                return dict(r)
        return None


def _adapter(catalog):
    from brokers.fyers.rest import FyersMarketDataAdapter
    rest = _FakeRest()
    ad = FyersMarketDataAdapter(rest, lambda: "TOKEN")
    ad._catalog = catalog          # injected catalogue for symbol resolution
    return ad, rest


def test_equity_key_is_translated_to_the_underlying_symbol() -> None:
    catalog = _Catalog([{
        "provider_symbol": "NSE:ADANIGREEN-EQ",
        "instrument_token": "10100000003563",
        "tradingsymbol": "ADANIGREEN", "underlying": "ADANIGREEN",
        "exchange": "NSE",
    }])
    ad, rest = _adapter(catalog)
    asyncio.run(ad.option_chain(
        instrument_key="10100000003563", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls, "no chain call recorded"
    # The UNDERLYING symbol — the live-verified request form.
    assert rest.calls[0]["symbol"] == "NSE:ADANIGREEN-EQ"
    # A fyToken is never sent (the provider rejects it with -300).
    assert not rest.calls[0]["symbol"].isdigit()


def test_index_key_is_translated_to_the_underlying_symbol() -> None:
    catalog = _Catalog([{
        "provider_symbol": "NSE:NIFTY50-INDEX",
        "instrument_token": "101000000026000",
        "tradingsymbol": "NSE:NIFTY50-INDEX", "underlying": "NIFTY",
        "exchange": "NSE",
    }])
    ad, rest = _adapter(catalog)
    asyncio.run(ad.option_chain(
        instrument_key="101000000026000", exchange="NSE",
        tradingsymbol="NSE:NIFTY50-INDEX", expiry="2026-10-27"))
    assert rest.calls[0]["symbol"] == "NSE:NIFTY50-INDEX"


def test_symbol_style_key_is_passed_through_unchanged() -> None:
    catalog = _Catalog([{
        "provider_symbol": "NSE:ADANIGREEN-EQ",
        "instrument_token": "10100000003563",
    }])
    ad, rest = _adapter(catalog)
    asyncio.run(ad.option_chain(
        instrument_key="NSE:ADANIGREEN-EQ", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls[0]["symbol"] == "NSE:ADANIGREEN-EQ"


def test_unresolvable_key_is_passed_through_for_the_provider_to_reject() -> None:
    """No catalogue row -> the original key is sent (the provider decides)."""
    catalog = _Catalog([])
    ad, rest = _adapter(catalog)
    asyncio.run(ad.option_chain(
        instrument_key="10100000003563", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls[0]["symbol"] == "10100000003563"


def test_expiry_is_still_sent_as_a_timestamp() -> None:
    catalog = _Catalog([])
    ad, rest = _adapter(catalog)
    asyncio.run(ad.option_chain(
        instrument_key="10100000003563", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls[0]["timestamp"] is not None
    assert rest.calls[0]["expiry"] == "2026-10-27"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
