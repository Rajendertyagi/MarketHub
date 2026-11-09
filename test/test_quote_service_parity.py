#!/usr/bin/env python3
"""Unified canonical quote service (freshness, provenance, parity).

Proves:
  * the provider of a quote is resolved from the CATALOGUE, not from the
    shape of the storage key;
  * a fresh quote is selected per canonical instrument (most recent wins;
    provider preference is a tie-break only);
  * a stale-only instrument KEEPS its last known value, marked stale with
    its ``as_of`` timestamp and source - never dropped, never "live";
  * REST and MCP read through the SAME service and return the SAME
    payload shape.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from market.models import Quote


def _quote(token: str, ltp: float, *, age_seconds: float = 0.0,
           canonical_id: str = "NSE:INDEX:NIFTY") -> Quote:
    return Quote(
        instrument_token=token,
        exchange="NSE",
        tradingsymbol="NIFTY",
        received_ts=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        ltp=ltp,
        canonical_id=canonical_id,
    )


class _Service:
    """A minimal quote store double."""

    def __init__(self, quotes):
        self._quotes = list(quotes)

    async def quotes(self):
        return tuple(self._quotes)


class _Catalog:
    """A catalogue double that owns the provider of each key."""

    def __init__(self, mapping):
        self._mapping = dict(mapping)

    def get_by_key(self, key):
        provider = self._mapping.get(key)
        if not provider:
            return None
        return {"provider": provider, "tradingsymbol": key}

    def search(self, **kw):
        return []


def _service(quotes, mapping=None):
    from app.quote_service import QuoteService
    return QuoteService(_Service(quotes), catalog=_Catalog(mapping or {}))


def test_provider_comes_from_the_catalogue_not_the_key_shape() -> None:
    """A pipe-shaped key published by 'fyers' must be reported as fyers."""
    svc = _service([_quote("NSE_EQ|INE002A01018", 100.0,
                           canonical_id="NSE:EQUITY:INE002A01018")],
                   {"NSE_EQ|INE002A01018": "fyers"})
    payload = asyncio.run(svc.quote("NSE:EQUITY:INE002A01018"))
    assert payload is not None
    assert payload["provider"] == "fyers"
    assert payload["source"] == "fyers"


def test_fresh_quote_is_selected_and_marked_live() -> None:
    svc = _service([_quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=1)])
    payload = asyncio.run(svc.quote("NSE:INDEX:NIFTY"))
    assert payload["live"] is True
    assert payload["stale"] is False
    assert payload["state"] == "live"
    assert payload["as_of"]
    assert payload["age_seconds"] < 5


def test_stale_only_instrument_stays_visible_but_is_not_live() -> None:
    """A stale value is kept (with as_of/source) - never dropped."""
    svc = _service([_quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=3600)])
    payload = asyncio.run(svc.quote("NSE:INDEX:NIFTY"))
    assert payload is not None, "a stale value must stay visible"
    assert payload["stale"] is True
    assert payload["live"] is False
    assert payload["state"] == "stale"
    assert payload["as_of"]
    assert payload["provider"]


def test_the_freshest_fresh_candidate_wins() -> None:
    svc = _service([
        _quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=30),
        _quote("NSE:NIFTY50-INDEX", 24510.0, age_seconds=2),
    ])
    payload = asyncio.run(svc.quote("NSE:INDEX:NIFTY"))
    assert payload["ltp"] == 24510.0
    assert payload["candidate_count"] == 2


def test_bulk_read_keeps_stale_instruments() -> None:
    svc = _service([
        _quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=1,
               canonical_id="NSE:INDEX:NIFTY"),
        _quote("NSE_INDEX|Nifty Bank", 52000.0, age_seconds=7200,
               canonical_id="NSE:INDEX:BANKNIFTY"),
    ])
    payloads = asyncio.run(svc.quotes())
    states = {p["canonical_id"]: p["state"] for p in payloads}
    assert states["NSE:INDEX:NIFTY"] == "live"
    assert states["NSE:INDEX:BANKNIFTY"] == "stale"


def test_rest_and_mcp_use_the_same_service() -> None:
    """The REST route and the MCP tool read through the SAME service."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from api.routes import build_market_routes

    quote = _quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=1)
    svc = _service([quote], {"NSE_INDEX|Nifty 50": "upstox"})

    app = Starlette(routes=build_market_routes(
        market_broker=None, market_service=svc._market_service,
        quote_service=svc))
    body = TestClient(app).get("/api/market/quotes").json()
    rest_quote = body["quotes"][0]
    assert rest_quote["provider"] == "upstox"
    assert rest_quote["live"] is True
    assert "as_of" in rest_quote and "stale" in rest_quote

    # The MCP tool reads the same service -> the same payload.
    from mcp_server.tools.market import register_market_tools

    class _MCP:
        def __init__(self):
            self.tools = {}

        def tool(self, name=None, description=None):
            def deco(fn):
                self.tools[name] = fn
                return fn
            return deco

    class _Services:
        market_service = svc._market_service
        quote_service = svc
        condition_identity_resolver = None
        identity_resolver = None

    mcp = _MCP()
    register_market_tools(mcp, _Services())
    result = asyncio.run(mcp.tools["market_quote"]("NSE_INDEX|Nifty 50"))
    assert result["status"] == "ok"
    assert result["quote"]["provider"] == rest_quote["provider"]
    assert result["quote"]["stale"] == rest_quote["stale"]
    assert result["quote"]["live"] == rest_quote["live"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
