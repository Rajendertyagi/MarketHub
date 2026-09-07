#!/usr/bin/env python3
"""Market History routing + contract tests (no live broker).

Covers the History fix (canonical provider routing, Upstox interval
mapping, honest response semantics):

  1. Upstox-backed History routes to Upstox (correct interval token + URL)
  2. Fyers-backed History never routes to Upstox (honest unsupported)
  3. provider inference from canonical identity
  4. explicit provider override honored; unknown override rejected
  5. malformed/unknown identity
  6. valid no-data result (honest empty, not an error)
  7. upstream/provider failure propagation
  8. unsupported capability classification
  9. interval/timeframe mapping table
  10. timezone/range conversion + validation
  11. REST history contract
  12. MCP history contract + REST parity
  13. Test Center classification shape
  (14. auth-freeze regression is covered by the boundary suites.)

Synthetic REST payloads only. Follows test_margin_shareholdings_greeks.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

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
    upstox_interval_token,
)
from api.product_routes import build_market_data_routes  # noqa: E402
from starlette.requests import Request  # noqa: E402


class _FakeCreds:
    access_token = "test-token"


class _FakeRest:
    def __init__(self, payload=None, exc=None):
        self._payload = payload
        self._exc = exc
        self.calls = []

    async def authenticated_request(self, *, method, url, access_token,
                                    json_body=None, params=None):
        self.calls.append({"method": method, "url": url,
                           "access_token": access_token})
        if self._exc is not None:
            raise self._exc
        return self._payload


def _make_md(payload=None, exc=None, fyers=None):
    rest = _FakeRest(payload, exc)
    md = ProviderMarketData(
        upstox_auth_context_fn=lambda: (rest, _FakeCreds()),
        fyers_adapter=fyers)
    return md, rest


_CANDLES = {"status": "ok", "data": {"candles": [
    ["2026-07-31T00:00:00+05:30", 100, 105, 99, 104, 800000],
    ["2026-07-30T00:00:00+05:30", 99, 102, 98, 100, 700000],
]}}
_EMPTY = {"status": "ok", "data": {"candles": []}}


def _make_request(path, query=b""):
    scope = {"type": "http", "method": "GET", "path": path,
             "headers": [], "query_string": query}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _route(md, path="/api/market/history"):
    routes = build_market_data_routes(md)
    return next(r for r in routes if r.path == path)


async def _get_json(route, query):
    resp = await route.endpoint(_make_request(route.path, query))
    return resp.status_code, json.loads(resp.body.decode())


# -- 3. inference ---------------------------------------------------------------

async def test_infer_provider(runner: R) -> None:
    cases = [
        ("NSE_EQ|RELIANCE", "upstox"),
        ("NSE_INDEX|Nifty 50", "upstox"),
        ("NSE:NIFTY50-INDEX", "fyers"),
        ("NSE:RELIANCE-EQ", "fyers"),
        ("1210000000538593", "fyers"),
        ("RELIANCE", "upstox"),
        ("", "upstox"),
    ]
    for key, want in cases:
        runner.assert_eq(f"infer-{key or 'empty'}",
                         _infer_provider(key), want)


# -- 9. interval mapping ------------------------------------------------------------

async def test_interval_mapping(runner: R) -> None:
    good = {
        (("minutes", 1), ("intraday", "1minute")),
        (("minutes", 30), ("intraday", "30minute")),
        (("days", 1), ("history", "day")),
        (("weeks", 1), ("history", "week")),
        (("months", 1), ("history", "month")),
    }
    for (unit, interval), want in good:
        runner.assert_eq(f"map-{unit}-{interval}",
                         upstox_interval_token(unit, interval), want)
    for unit, interval in [("hours", 1), ("days", 5), ("minutes", 5),
                           ("minutes", 60), ("days", 0), ("nanoseconds", 1),
                           ("days", "abc")]:
        runner.assert_eq(f"unmapped-{unit}-{interval}",
                         upstox_interval_token(unit, interval), None)


# -- 1. Upstox routing ---------------------------------------------------------------

async def test_upstox_daily_url(runner: R) -> None:
    md, rest = _make_md(_CANDLES)
    candles = await md.history(
        instrument_key="NSE_INDEX|Nifty 50", unit="days", interval=1,
        from_date="2026-07-01", to_date="2026-07-31")
    runner.assert_eq("up-n", len(candles), 2)
    url = rest.calls[0]["url"]
    runner.assert_in("up-day-token", "/day/2026-07-31/2026-07-01", url)
    runner.assert_in("up-encoded-key", "NSE_INDEX%7CNifty%2050", url)
    runner.assert_not_in("up-no-raw-pipe", "NSE_INDEX|", url)
    runner.assert_not_in("up-no-days-slash", "/days/", url)


async def test_upstox_intraday_url(runner: R) -> None:
    md, rest = _make_md(_CANDLES)
    await md.history(instrument_key="NSE_EQ|INE002A01018", unit="minutes",
                     interval=30, from_date="2026-07-31",
                     to_date="2026-07-31")
    url = rest.calls[0]["url"]
    runner.assert_in("up-intraday", "/intraday/", url)
    runner.assert_in("up-30m", "/30minute", url)


# -- 2. Fyers never touches Upstox ------------------------------------------------------

async def test_fyers_unwired_is_honest(runner: R) -> None:
    md, rest = _make_md(_CANDLES)
    try:
        await md.history(instrument_key="NSE:RELIANCE-EQ", unit="days",
                         interval=1, from_date="2026-07-01",
                         to_date="2026-07-31")
        ok = False
    except ProviderMarketDataError as exc:
        ok = "fyers" in str(exc).lower()
    runner.assert_true("fy-unwired-400", ok)
    runner.assert_eq("fy-no-upstox-call", rest.calls, [])


async def test_fyers_wired_never_calls_upstox(runner: R) -> None:
    from market.normalize.fyers import fyers_resolution

    class _FyersAdapter:
        def __init__(self):
            self.calls = []

        async def history(self, *, instrument_key, resolution,
                          from_date, to_date):
            self.calls.append((instrument_key, resolution))
            return []

    md, rest = _make_md(_CANDLES, fyers=_FyersAdapter())
    out = await md.history(instrument_key="NSE:RELIANCE-EQ", unit="days",
                           interval=1, from_date="2026-07-01",
                           to_date="2026-07-31")
    runner.assert_eq("fy-wired-empty", out, [])
    runner.assert_eq("fy-wired-no-upstox", rest.calls, [])
    runner.assert_eq("fy-resolution", fyers_resolution("days", 1) is not None,
                     True)


# -- 4. explicit override -----------------------------------------------------------------

async def test_explicit_override(runner: R) -> None:
    md, rest = _make_md(_CANDLES)
    # Upstox-style key forced through fyers -> honest unsupported (no adapter).
    try:
        await md.history(instrument_key="NSE_EQ|X", unit="days", interval=1,
                         from_date="2026-07-01", to_date="2026-07-31",
                         provider="fyers")
        ok = False
    except ProviderMarketDataError:
        ok = True
    runner.assert_true("override-fyers-400", ok)
    runner.assert_eq("override-no-upstox", rest.calls, [])
    # Unknown provider is rejected loudly, never silently routed.
    try:
        await md.history(instrument_key="NSE_EQ|X", unit="days", interval=1,
                         from_date="2026-07-01", to_date="2026-07-31",
                         provider="nope")
        ok2 = False
    except ProviderMarketDataError:
        ok2 = True
    runner.assert_true("override-unknown-400", ok2)


# -- 5/8/10. malformed, unsupported, dates ---------------------------------------------------

async def test_malformed_and_unsupported(runner: R) -> None:
    md, _rest = _make_md(_CANDLES)
    bad = [
        dict(instrument_key="K", unit="nanoseconds", interval=1,
             from_date="2026-01-01", to_date="2026-01-02"),
        dict(instrument_key="K", unit="days", interval="abc",
             from_date="2026-01-01", to_date="2026-01-02"),
        dict(instrument_key="K", unit="days", interval=1,
             from_date="junk", to_date="2026-01-02"),
        dict(instrument_key="K", unit="hours", interval=1,
             from_date="2026-01-01", to_date="2026-01-02"),
        dict(instrument_key="K", unit="days", interval=5,
             from_date="2026-01-01", to_date="2026-01-02"),
        dict(instrument_key="K", unit="minutes", interval=5,
             from_date="2026-01-01", to_date="2026-01-02"),
    ]
    for i, kw in enumerate(bad):
        try:
            await md.history(**kw)
            ok = False
        except ProviderMarketDataError:
            ok = True
        runner.assert_true(f"bad-{i}", ok)


async def test_candle_tz_aware(runner: R) -> None:
    md, _rest = _make_md(_CANDLES)
    candles = await md.history(
        instrument_key="NSE_EQ|X", unit="days", interval=1,
        from_date="2026-07-01", to_date="2026-07-31")
    for c in candles:
        runner.assert_true("tz-aware",
                           c.timestamp.tzinfo is not None)


# -- 6/7. no-data + upstream failure ------------------------------------------------------------

async def test_empty_is_honest(runner: R) -> None:
    md, _rest = _make_md(_EMPTY)
    out = await md.history(instrument_key="NSE_EQ|X", unit="days",
                           interval=1, from_date="2026-07-01",
                           to_date="2026-07-31")
    runner.assert_eq("empty-list", out, [])


async def test_upstream_404_is_client_error(runner: R) -> None:
    from brokers.upstox.errors import UpstoxRestError
    err = UpstoxRestError("upstox api call failed: HTTP 404 [UDAPI100060]",
                          status_code=404, upstox_codes=["UDAPI100060"],
                          retryable=False)
    md, _rest = _make_md(exc=err)
    try:
        await md.history(instrument_key="NSE_EQ|BOGUS", unit="days",
                         interval=1, from_date="2026-07-01",
                         to_date="2026-07-31")
        ok = False
    except ProviderMarketDataError as exc:
        ok = "invalid key" in str(exc).lower() or "no data" in str(exc).lower()
    runner.assert_true("upstream-404-client", ok)


async def test_upstream_retryable_propagates(runner: R) -> None:
    from brokers.upstox.errors import UpstoxRestError
    err = UpstoxRestError("upstox api call failed: HTTP 503",
                          status_code=503, retryable=True)
    md, _rest = _make_md(exc=err)
    try:
        await md.history(instrument_key="NSE_EQ|X", unit="days", interval=1,
                         from_date="2026-07-01", to_date="2026-07-31")
        ok = False
    except ProviderMarketDataError:
        ok = False  # must NOT be flattened into a client error
    except UpstoxRestError:
        ok = True
    runner.assert_true("upstream-503-propagates", ok)


# -- 11. REST contract -------------------------------------------------------------------------------

async def test_rest_contract(runner: R) -> None:
    md, rest = _make_md(_CANDLES)
    route = _route(md)
    code, data = await _get_json(
        route, b"instrument_key=NSE_INDEX%7CNifty%2050&unit=days&interval=1"
               b"&from=2026-07-01&to=2026-07-31")
    runner.assert_eq("rest-200", code, 200)
    runner.assert_eq("rest-n", len(data["candles"]), 2)
    runner.assert_true("rest-close", data["candles"][0]["close"] == 104.0)
    # Provider override is forwarded (not silently dropped).
    code, _d = await _get_json(
        route, b"instrument_key=NSE_EQ%7CX&unit=days&interval=1"
               b"&from=2026-07-01&to=2026-07-31&provider=bogus")
    runner.assert_eq("rest-bad-provider-400", code, 400)
    # Missing key -> 400, not 502.
    code, _d = await _get_json(route, b"unit=days")
    runner.assert_eq("rest-missing-400", code, 400)
    # Upstream 404 surfaces as 400 (invalid instrument), not 502.
    from brokers.upstox.errors import UpstoxRestError
    md2, _r2 = _make_md(exc=UpstoxRestError(
        "upstox api call failed: HTTP 404 [UDAPI100060]",
        status_code=404, upstox_codes=["UDAPI100060"], retryable=False))
    code, data = await _get_json(
        _route(md2), b"instrument_key=NSE_EQ%7CBOGUS&unit=days&interval=1"
                     b"&from=2026-07-01&to=2026-07-31")
    runner.assert_eq("rest-404-mapped-400", code, 400)
    # Retryable upstream failure stays an honest 502.
    md3, _r3 = _make_md(exc=UpstoxRestError(
        "upstox api call failed: HTTP 503", status_code=503, retryable=True))
    code, _d = await _get_json(
        _route(md3), b"instrument_key=NSE_EQ%7CX&unit=days&interval=1"
                     b"&from=2026-07-01&to=2026-07-31")
    runner.assert_eq("rest-503-stays-502", code, 502)


# -- 12. MCP parity --------------------------------------------------------------------------------------

def _mcp_history_fn(md):
    import types

    class _FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, name=None, description=None):
            def deco(fn):
                self.tools[name] = fn
                return fn
            return deco

    services = types.SimpleNamespace(
        provider_market_data=md, identity_resolver=None,
        instrument_catalog=None, market_service=None, store=None)
    fake = _FakeMCP()
    import mcp_server.tools.market as _mt
    _mt.register_market_tools(fake, services)
    return fake.tools["market_history"]


async def test_mcp_parity(runner: R) -> None:
    md, _rest = _make_md(_CANDLES)
    fn = _mcp_history_fn(md)
    out = await fn(instrument_ref="NSE_EQ|INE002A01018", unit="days",
                   interval=1, from_date="2026-07-01", to_date="2026-07-31")
    runner.assert_eq("mcp-ok", out.get("status"), "ok")
    runner.assert_eq("mcp-n", len(out.get("candles", [])), 2)
    runner.assert_eq("mcp-same-count", len(out["candles"]), 2)
    # Same canonical error classification as REST for Fyers keys.
    out2 = await fn(instrument_ref="NSE:RELIANCE-EQ", unit="days",
                    interval=1, from_date="2026-07-01", to_date="2026-07-31")
    runner.assert_true("mcp-fyers-error", "error" in out2)
    # Unresolvable identity stays an explicit error, not routed anywhere.
    out3 = await fn(instrument_ref="", unit="days",
                    interval=1, from_date="2026-07-01", to_date="2026-07-31")
    runner.assert_true("mcp-unresolvable", "error" in out3)


# -- 13. Test Center classification ----------------------------------------------------------------------------

async def test_testcenter_shape(runner: R) -> None:
    import inspect
    from app import diagnostics as _dg
    src = inspect.getsource(_dg.DiagnosticsRunner._check_history)
    runner.assert_in("tc-pass", "history_available", src)
    runner.assert_in("tc-partial", "no_candles", src)
    runner.assert_in("tc-fail", "fetch_error", src)
    runner.assert_in("tc-nifty", "NSE_INDEX", src)


# -- main ---------------------------------------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_infer_provider(runner)
    await test_interval_mapping(runner)
    await test_upstox_daily_url(runner)
    await test_upstox_intraday_url(runner)
    await test_fyers_unwired_is_honest(runner)
    await test_fyers_wired_never_calls_upstox(runner)
    await test_explicit_override(runner)
    await test_malformed_and_unsupported(runner)
    await test_candle_tz_aware(runner)
    await test_empty_is_honest(runner)
    await test_upstream_404_is_client_error(runner)
    await test_upstream_retryable_propagates(runner)
    await test_rest_contract(runner)
    await test_mcp_parity(runner)
    await test_testcenter_shape(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = asyncio.run(main())
    sys.exit(0 if _ok else 1)
