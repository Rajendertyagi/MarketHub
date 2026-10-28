#!/usr/bin/env python3
"""`/api/market/bars` transport tests — thin over BarService."""

from __future__ import annotations

import asyncio
import json
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.market_bars import BarsUnavailable

KEY = "NSE_EQ|INE002A01018"
ROUTES = "/api/market/bars"


class _Service:
    def __init__(self, *, error=None) -> None:
        self.calls: list[dict] = []
        self._error = error

    async def get_bars(self, **kw):
        self.calls.append(kw)
        if self._error is not None:
            raise self._error
        return {
            "provider": kw.get("provider") or "upstox",
            "instrument_key": kw["instrument_key"],
            "timeframe": 5,
            "bars": [{"ts": 1_793_072_700_000, "open": 100.0, "high": 101.0,
                      "low": 99.0, "close": 100.5, "volume": 500.0,
                      "oi": None, "source": "rest"}],
        }


def _route(service):
    from api.market_bars_routes import build_market_bars_routes
    routes = build_market_bars_routes(service)
    return next(r for r in routes if r.path == ROUTES)


def _req(query: bytes):
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": ROUTES,
             "headers": [(b"content-type", b"application/json")],
             "query_string": query}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


async def _get(service, query: bytes):
    resp = await _route(service).endpoint(_req(query))
    return resp.status_code, json.loads(resp.body)


def test_bars_ok_passes_parameters_through() -> None:
    svc = _Service()
    code, body = asyncio.run(_get(
        svc,
        b"instrument_key=NSE_EQ%7CINE002A01018&timeframe=5"
        b"&from=1000&to=2000&limit=50&provider=upstox"))
    assert code == 200, body
    assert body["instrument_key"] == KEY
    assert body["bars"][0]["close"] == 100.5
    (call,) = svc.calls
    assert call["timeframe"] == "5"
    assert call["from_ts"] == 1000 and call["to_ts"] == 2000
    assert call["limit"] == 50
    assert call["provider"] == "upstox"


def test_missing_instrument_key_is_a_client_error() -> None:
    svc = _Service()
    code, body = asyncio.run(_get(svc, b"timeframe=5"))
    assert code == 400 and "instrument_key" in body["error"]
    assert svc.calls == []


def test_non_integer_range_is_a_client_error() -> None:
    svc = _Service()
    for query in (b"instrument_key=K&from=abc",
                  b"instrument_key=K&to=1.5",
                  b"instrument_key=K&limit=all"):
        code, body = asyncio.run(_get(svc, query))
        assert code == 400 and "integer" in body["error"]
    assert svc.calls == []


def test_unsupported_timeframe_is_a_client_error() -> None:
    svc = _Service(error=BarsUnavailable("unsupported timeframe: '3m'"))
    code, body = asyncio.run(_get(svc, b"instrument_key=K&timeframe=3m"))
    assert code == 400 and "unsupported timeframe" in body["error"]


def test_provider_failure_is_an_upstream_error() -> None:
    svc = _Service(error=RuntimeError("boom"))
    code, body = asyncio.run(_get(svc, b"instrument_key=K&timeframe=5"))
    assert code == 502 and "unavailable" in body["error"]


def test_missing_service_returns_503() -> None:
    code, body = asyncio.run(_get(None, b"instrument_key=K&timeframe=5"))
    assert code == 503


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
