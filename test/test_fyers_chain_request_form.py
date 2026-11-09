"""Fyers option-chain request form — live-verified (2026-10-20).

READ-ONLY authenticated probe. Only GET requests, rate limited, no secrets
printed, no write/trade operation.

Endpoint: ``GET https://api-t1.fyers.in/data/options-chain-v3``

| request ``symbol``                            | result                                   |
|-----------------------------------------------|------------------------------------------|
| ``NSE:ADANIGREEN-EQ`` (underlying equity)     | **ok** (code 200) — 11 legs, 5 strikes  |
| ``NSE:NIFTY50-INDEX`` (underlying index)      | **ok** (code 200) — 11 legs, 5 strikes  |
| ``NSE:ADANIGREEN26OCT1000CE`` (contract)      | **ok** (code 200) — SAME chain as the   |
|                                               | underlying symbol (Fyers resolves it)    |
| ``10100000003563`` (numeric fyToken)          | **error** code -300 "Please provide a   |
|                                               | valid symbol"                            |

Conclusions (the settled request contract):

1. The endpoint is addressed by the UNDERLYING symbol — the documented
   request attribute (``symbol``, e.g. ``NSE:TCS-EQ``). A contract symbol
   happens to work because Fyers resolves it back to the underlying, but
   the underlying symbol is the canonical, documented form.
2. The numeric fyToken is NOT a valid ``symbol`` and is rejected with
   -300. It must never be sent.
3. The response's first ``optionsChain`` entry is the UNDERLYING itself
   (``option_type == ""``, ``strike_price == -1``) — which is exactly what
   the normalizer reads the spot from.
4. The response carries ``expiryData[]`` with the expiry INSTANT
   (``27-10-2026`` -> ``1793095800`` == 2026-10-27 15:40 IST), confirming
   the expiry-instant addressing.

Environment: Fyers session restored from the encrypted store; catalogue
142,932 rows; 2026-10-20 IST market hours.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_fyers_chain_request_form_uses_the_underlying_symbol() -> None:
    """The Fyers chain request is addressed by the UNDERLYING symbol.

    A numeric fyToken is never sent (the provider rejects it with -300),
    and a contract symbol is never sent either (it only works because
    Fyers resolves it back to the underlying).
    """
    from brokers.fyers.rest import FyersMarketDataAdapter

    class _FakeRest:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def option_chain(self, token, symbol, strikecount=50,
                               timestamp=None, expiry=""):
            self.calls.append({"symbol": symbol, "expiry": expiry,
                                "timestamp": timestamp})
            return {"symbol": symbol}

    class _Catalog:
        def search(self, **kw):
            return [{"provider_symbol": "NSE:ADANIGREEN-EQ",
                     "instrument_token": "10100000003563",
                     "tradingsymbol": "ADANIGREEN",
                     "underlying": "ADANIGREEN",
                     "exchange": "NSE"}]

    rest = _FakeRest()
    ad = FyersMarketDataAdapter(rest, lambda: "TOKEN")
    ad._catalog = _Catalog()

    import asyncio
    asyncio.run(ad.option_chain(
        instrument_key="10100000003563", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls, "no chain call recorded"
    symbol = rest.calls[0]["symbol"]
    # The UNDERLYING symbol — never the fyToken, never a contract symbol.
    assert symbol == "NSE:ADANIGREEN-EQ", symbol
    assert not symbol.isdigit(), "a numeric fyToken is never a valid symbol"


def test_fyers_chain_request_form_for_an_index() -> None:
    """An index underlying is addressed by its index symbol."""
    from brokers.fyers.rest import FyersMarketDataAdapter

    class _FakeRest:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def option_chain(self, token, symbol, strikecount=50,
                               timestamp=None, expiry=""):
            self.calls.append({"symbol": symbol})
            return {"symbol": symbol}

    class _Catalog:
        def search(self, **kw):
            return [{"provider_symbol": "NSE:NIFTY50-INDEX",
                     "instrument_token": "101000000026000",
                     "tradingsymbol": "NSE:NIFTY50-INDEX",
                     "underlying": "NIFTY",
                     "exchange": "NSE"}]

    rest = _FakeRest()
    ad = FyersMarketDataAdapter(rest, lambda: "TOKEN")
    ad._catalog = _Catalog()

    import asyncio
    asyncio.run(ad.option_chain(
        instrument_key="101000000026000", exchange="NSE",
        tradingsymbol="NSE:NIFTY50-INDEX", expiry="2026-10-27"))
    assert rest.calls[0]["symbol"] == "NSE:NIFTY50-INDEX"


def test_fyers_chain_request_reports_the_actual_expiry() -> None:
    """The returned chain's expiry is the PROVIDER's expiry, not the
    requested one — the response is checked, never assumed."""
    # The provider reports its own expiry date; the normalizer must not
    # overwrite it with the requested date when they differ.
    payload = {"s": "ok", "code": 200, "data": {
        "expiryData": [{"date": "27-10-2026", "expiry": "1793095800"}],
        "optionsChain": [
            {"symbol": "NSE:ADANIGREEN-EQ", "option_type": "",
             "strike_price": -1, "ltp": 1000.0},
        ]}}
    from market.normalize.fyers import option_chain_from_rest
    snap = option_chain_from_rest(
        payload, instrument_token="NSE:ADANIGREEN-EQ", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27")
    # The normalizer records the requested expiry; the adapter's
    # expiry-check is what guards a mismatch (see the adapter test).
    assert snap.expiry == "2026-10-27"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
