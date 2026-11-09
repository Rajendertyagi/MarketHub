#!/usr/bin/env python3
"""Provider routing for the option-chain path (S: broker-neutral routing).

Defect: ``ProviderMarketData.option_chain`` hard-defaulted
``provider="upstox"`` and never inferred the provider from the instrument
key, so a Fyers key (``NSE:...``) was served by the Upstox adapter. With
Upstox logged out the call failed with "upstox feed is not authenticated"
even though Fyers was authenticated and able to serve the same chain.

The fix mirrors the existing ``history()`` behaviour: infer the provider
from the instrument key when the caller does not name one, so the routing
follows the instrument rather than a hardcoded provider.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class _FakeFyers:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def option_chain(self, *, instrument_key, exchange, tradingsymbol,
                           expiry, **kw):
        self.calls.append({"instrument_key": instrument_key,
                            "expiry": expiry})
        return {"fyers": True, "instrument_key": instrument_key}


class _FakeUpstox:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def option_chain(self, *, instrument_key, exchange, tradingsymbol,
                           expiry, **kw):
        self.calls.append({"instrument_key": instrument_key})
        return {"upstox": True}


class _FakeRest:
    async def authenticated_request(self, *, method, url, access_token, **kw):
        # Minimal valid Upstox option-chain payload (empty strike list).
        return {"s": "ok", "code": 200, "data": []}


class _FakeCreds:
    access_token = "TOKEN"

def _md(fyers=None, upstox_auth=None):
    from app.market_data import ProviderMarketData
    return ProviderMarketData(lambda: upstox_auth, fyers_adapter=fyers)


def test_fyers_key_routes_to_fyers_without_an_explicit_provider() -> None:
    fyers = _FakeFyers()
    md = _md(fyers=fyers, upstox_auth=None)
    res = asyncio.run(md.option_chain(
        instrument_key="NSE:ADANIGREEN", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert res["fyers"] is True
    assert len(fyers.calls) == 1


def test_upstox_key_routes_to_upstox() -> None:
    md = _md(upstox_auth=(_FakeRest(), _FakeCreds()))
    res = asyncio.run(md.option_chain(
        instrument_key="NSE_EQ|INE364U01010", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    # The Upstox adapter produced the canonical snapshot (not the Fyers one).
    assert getattr(res, "instrument_token", None) == "NSE_EQ|INE364U01010"


def test_explicit_provider_still_wins() -> None:
    """An explicitly named provider is honoured (no silent re-routing)."""
    fyers = _FakeFyers()
    md = _md(fyers=fyers, upstox_auth=(_FakeRest(), _FakeCreds()))
    res = asyncio.run(md.option_chain(
        instrument_key="NSE:ADANIGREEN", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27", provider="fyers"))
    assert res["fyers"] is True
    assert len(fyers.calls) == 1


def test_infer_provider_matches_the_existing_convention() -> None:
    from app.market_data import _infer_provider
    assert _infer_provider("NSE:ADANIGREEN") == "fyers"
    assert _infer_provider("NSE_EQ|INE364U01010") == "upstox"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
