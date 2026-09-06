#!/usr/bin/env python3
"""Phase 4C — Provider routing for history / standalone greeks.

ProviderMarketData must route to the correct broker based on the instrument
key's format (Upstox pipes vs Fyers EXCH:SYMBOL / numeric), without every
caller passing an explicit provider= argument. Fyers greeks are not
implemented; a Fyers key must route there honestly (clear error) rather than
being silently force-fed to Upstox.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import patch

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

from app.market_data import (  # noqa: E402
    ProviderMarketData,
    ProviderMarketDataError,
    _infer_provider,
)
from api.product_routes import build_market_data_routes  # noqa: E402
from starlette.requests import Request  # noqa: E402


class _FakeCreds:
    access_token = "test-token"


class _FakeRest:
    def __init__(self):
        self.calls = []

    async def authenticated_request(self, *, method, url, access_token,
                                   json_body=None, params=None):
        self.calls.append({
            "method": method, "url": url, "access_token": access_token,
            "json_body": json_body, "params": params,
        })
        return {"status": "success", "data": {}}


class _FakeFyers:
    def __init__(self):
        self.history_calls = []

    async def history(self, *, instrument_key, resolution, from_date, to_date):
        self.history_calls.append(
            (instrument_key, resolution, from_date, to_date))
        return [("CANDLE", instrument_key)]


def _make_md(fyers=None):
    rest = _FakeRest()
    md = ProviderMarketData(
        upstox_auth_context_fn=lambda: (rest, _FakeCreds()),
        fyers_adapter=fyers)
    return md, rest


def _make_request(method, path, *, query_string=b""):
    scope = {
        "type": "http", "method": method, "path": path,
        "headers": [], "query_string": query_string,
    }

    def _receive(raw):
        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}
        return receive

    return Request(scope, receive=_receive(b""))


def _response_json(response):
    return json.loads(response.body.decode())


async def test_infer_provider(runner: R) -> None:
    cases = [
        ("NSE_INDEX|Nifty 50", "upstox"),
        ("NSE_EQ|RELIANCE", "upstox"),
        ("NSE:NIFTY50-INDEX", "fyers"),
        ("BSE:RELIANCE", "fyers"),
        ("101000000026000", "fyers"),
        ("K", "upstox"),
        ("", "upstox"),
    ]
    for key, expected in cases:
        runner.assert_eq(f"infer:{key!r}", _infer_provider(key), expected)


async def test_history_fyers_routing(runner: R) -> None:
    fyers = _FakeFyers()
    md, _ = _make_md(fyers)
    candles = await md.history(
        instrument_key="NSE:NIFTY50-INDEX", unit="days", interval=1,
        from_date="2026-01-01", to_date="2026-01-02")
    runner.assert_eq("fyers-called", len(fyers.history_calls), 1)
    runner.assert_eq("fyers-key", fyers.history_calls[0][0], "NSE:NIFTY50-INDEX")
    runner.assert_eq("fyers-resolution", fyers.history_calls[0][1], "1D")
    runner.assert_eq("returns", candles, [("CANDLE", "NSE:NIFTY50-INDEX")])


async def test_history_upstox_routing(runner: R) -> None:
    fyers = _FakeFyers()
    md, rest = _make_md(fyers)
    with patch("market.normalize.upstox.candles_from_rest",
               return_value=[("C", "NSE_INDEX|Nifty 50")]):
        candles = await md.history(
            instrument_key="NSE_INDEX|Nifty 50", unit="days", interval=1,
            from_date="2026-01-01", to_date="2026-01-02")
    runner.assert_eq("upstox-rest-called", len(rest.calls), 1)
    runner.assert_eq("fyers-not-called", len(fyers.history_calls), 0)
    runner.assert_eq("upstox-returns", candles, [("C", "NSE_INDEX|Nifty 50")])


async def test_greeks_fyers_honest_error(runner: R) -> None:
    md, _ = _make_md(_FakeFyers())
    raised = False
    try:
        await md.option_greeks(instrument_keys="NSE:NIFTY50-INDEX")
    except ProviderMarketDataError as exc:
        raised = True
        runner.assert_in("greeks-err", "not available", str(exc))
    runner.assert_true("greeks-raised", raised)


async def test_history_route_fyers(runner: R) -> None:
    fyers = _FakeFyers()
    md, _ = _make_md(fyers)
    routes = build_market_data_routes(md)
    route = next(r for r in routes if r.path == "/api/market/history")
    request = _make_request(
        "GET", "/api/market/history",
        query_string=b"instrument_key=NSE%3ANIFTY50-INDEX&unit=days&interval=1"
                     b"&from=2026-01-01&to=2026-01-02")
    data = _response_json(await route.endpoint(request))
    runner.assert_in("route-has-candles", "candles", data)
    runner.assert_eq("route-fyers-called", len(fyers.history_calls), 1)
    runner.assert_eq("route-fyers-key", fyers.history_calls[0][0],
                     "NSE:NIFTY50-INDEX")


async def main() -> bool:
    runner = R()
    await test_infer_provider(runner)
    await test_history_fyers_routing(runner)
    await test_history_upstox_routing(runner)
    await test_greeks_fyers_honest_error(runner)
    await test_history_route_fyers(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
