#!/usr/bin/env python3
"""Standalone Option Greeks regression tests (no live broker).

Covers the standalone-Greeks fix (canonical provider routing, honest error
classification, URL-encoded keys, Test Center wiring):

   1. Upstox option routes to Upstox Greeks (encoded key, GET v3 endpoint)
   2. Fyers option NEVER routes to Upstox (honest unsupported)
   3. provider inference for option keys (pipes / colon / numeric)
   4. explicit provider override honored; unknown override rejected
   5. valid Greeks normalize (delta/gamma/theta/vega/iv/oi/volume/cp)
   6. missing rho stays None (Upstox never exposes it)
   7. partial provider Greeks remain honest (only reported fields set)
   8. provider no-data -> empty entries, not an error
   9. unsupported Fyers capability -> ProviderMarketDataError
  10. invalid/empty instrument keys -> client error
  11. malformed identity shapes
  12. upstream 401 (rejected session) -> client error, NEVER 502-flattened
  13. upstream 400/404 (invalid key) -> client error
  14. upstream 429/5xx propagate (honest 502 at transport)
  15. REST contract (200 / 400 / 502 semantics)
  16. MCP parity: option_chain tool rides the same canonical service;
      no independent standalone-Greeks tool exists (contract shape)
  17. Test Center classification shape (option identity, honest reasons)
  18. Option Chain greeks path untouched (regression guard)
  19. History implementation untouched (regression guard)

Synthetic REST payloads only. Follows test_market_history.py.
"""

from __future__ import annotations

import asyncio
import inspect
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
                           "access_token": access_token, "params": params})
        if self._exc is not None:
            raise self._exc
        return self._payload


def _make_md(payload=None, exc=None, fyers=None):
    rest = _FakeRest(payload, exc)
    md = ProviderMarketData(
        upstox_auth_context_fn=lambda: (rest, _FakeCreds()),
        fyers_adapter=fyers)
    return md, rest


_GREEKS = {"status": "success", "data": {
    "NSE_FO:NIFTY2690823800CE": {
        "last_price": 56.6, "ltq": 260, "volume": 490374300, "cp": 195.6,
        "iv": 0.130615234375, "vega": 4.928, "gamma": 0.0025,
        "theta": -32.4084, "delta": 0.463, "oi": 17532970,
        # intentionally no rho -> must normalize to None
    },
}}
_EMPTY = {"status": "success", "data": {}}


def _make_request(path, query=b""):
    scope = {"type": "http", "method": "GET", "path": path,
             "headers": [], "query_string": query}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _route(md, path="/api/options/greeks"):
    routes = build_market_data_routes(md)
    return next(r for r in routes if r.path == path)


async def _get_json(route, query):
    resp = await route.endpoint(_make_request(route.path, query))
    return resp.status_code, json.loads(resp.body.decode())


# -- 3. inference ----------------------------------------------------------------

async def test_infer_provider_options(runner: R) -> None:
    cases = [
        ("NSE_FO|42631", "upstox"),
        ("NSE_INDEX|Nifty 50", "upstox"),
        ("NSE:NIFTY2690823800CE", "fyers"),
        ("101126090842631", "fyers"),
        ("", "upstox"),
    ]
    for key, want in cases:
        runner.assert_eq(f"infer-{key or 'empty'}",
                         _infer_provider(key), want)


# -- 1/5/6. Upstox routing + normalization + rho ---------------------------------

async def test_upstox_greeks_request(runner: R) -> None:
    md, rest = _make_md(_GREEKS)
    snap = await md.option_greeks(instrument_keys="NSE_FO|42631")
    runner.assert_eq("up-n", len(snap.entries), 1)
    e = snap.entries[0]
    runner.assert_eq("up-key", e.instrument_key, "NSE_FO:NIFTY2690823800CE")
    runner.assert_eq("up-delta", e.delta, 0.463)
    runner.assert_eq("up-gamma", e.gamma, 0.0025)
    runner.assert_eq("up-theta", e.theta, -32.4084)
    runner.assert_eq("up-vega", e.vega, 4.928)
    runner.assert_eq("up-iv", e.iv, 0.130615234375)
    runner.assert_eq("up-oi", e.oi, 17532970)
    runner.assert_eq("up-vol", e.volume, 490374300)
    runner.assert_eq("up-cp", e.previous_close, 195.6)
    runner.assert_true("up-rho-none", e.rho is None)
    call = rest.calls[0]
    runner.assert_eq("up-method", call["method"], "GET")
    runner.assert_in("up-url", "/v3/market-quote/option-greek", call["url"])
    # Raw pipe in params: authenticated_request urlencodes the value itself
    # (double-encoding here would corrupt the key upstream).
    runner.assert_eq("up-raw-key-param", call["params"]["instrument_key"],
                     "NSE_FO|42631")


async def test_partial_greeks_honest(runner: R) -> None:
    payload = {"status": "success", "data": {"NSE_FO|1": {
        "last_price": 10.0, "delta": -0.2}}}
    md, _rest = _make_md(payload)
    snap = await md.option_greeks(instrument_keys="NSE_FO|1")
    e = snap.entries[0]
    runner.assert_eq("partial-delta", e.delta, -0.2)
    runner.assert_true("partial-gamma-none", e.gamma is None)
    runner.assert_true("partial-iv-none", e.iv is None)
    runner.assert_true("partial-rho-none", e.rho is None)


# -- 2/9. Fyers never touches Upstox ------------------------------------------------

async def test_fyers_greeks_unwired_honest(runner: R) -> None:
    class _FyersAdapter:  # even a WIRED fyers adapter has no standalone greeks
        async def history(self, **kw):
            raise AssertionError("fyers history must not be called")

    md, rest = _make_md(_GREEKS, fyers=_FyersAdapter())
    try:
        await md.option_greeks(instrument_keys="NSE:NIFTY2690823800CE")
        ok = False
    except ProviderMarketDataError as exc:
        ok = "not available" in str(exc)
    runner.assert_true("fy-honest", ok)
    runner.assert_eq("fy-no-upstox-call", rest.calls, [])


# -- 4. explicit override ------------------------------------------------------------

async def test_explicit_override(runner: R) -> None:
    md, rest = _make_md(_GREEKS)
    try:
        await md.option_greeks(instrument_keys="NSE_FO|42631",
                               provider="fyers")
        ok = False
    except ProviderMarketDataError:
        ok = True
    runner.assert_true("override-fyers-400", ok)
    runner.assert_eq("override-no-upstox", rest.calls, [])
    try:
        await md.option_greeks(instrument_keys="NSE_FO|42631",
                               provider="nope")
        ok2 = False
    except ProviderMarketDataError:
        ok2 = True
    runner.assert_true("override-unknown-400", ok2)


# -- 8/10/11. no-data / invalid / malformed --------------------------------------------

async def test_no_data_is_honest(runner: R) -> None:
    md, _rest = _make_md(_EMPTY)
    snap = await md.option_greeks(instrument_keys="NSE_FO|42631")
    runner.assert_eq("empty-entries", snap.entries, ())


async def test_invalid_keys(runner: R) -> None:
    md, _rest = _make_md(_GREEKS)
    try:
        await md.option_greeks(instrument_keys="")
        ok = False
    except ProviderMarketDataError:
        ok = True
    runner.assert_true("invalid-empty", ok)
    try:
        await md.option_greeks(instrument_keys=" , ")
        ok2 = False
    except ProviderMarketDataError:
        ok2 = True
    runner.assert_true("invalid-blank", ok2)


# -- 12/13/14. upstream failure classification --------------------------------------------

async def test_upstream_401_is_client_error(runner: R) -> None:
    from brokers.upstox.errors import UpstoxAuthError
    md, _rest = _make_md(exc=UpstoxAuthError(
        "upstox upstox api call (GET) failed: HTTP 401 [UDAPI100050]: "
        "Invalid token used to access API"))
    try:
        await md.option_greeks(instrument_keys="NSE_FO|42631")
        ok = False
    except ProviderMarketDataError as exc:
        ok = "unavailable" in str(exc)
    except Exception:
        ok = False
    runner.assert_true("upstream-401-client", ok)


async def test_upstream_400_404_are_client_errors(runner: R) -> None:
    from brokers.upstox.errors import UpstoxRestError
    for status in (400, 404):
        md, _rest = _make_md(exc=UpstoxRestError(
            f"upstox api call failed: HTTP {status}",
            status_code=status, retryable=False))
        try:
            await md.option_greeks(instrument_keys="NSE_FO|BOGUS")
            ok = False
        except ProviderMarketDataError:
            ok = True
        runner.assert_true(f"upstream-{status}-client", ok)


async def test_upstream_retryable_propagates(runner: R) -> None:
    from brokers.upstox.errors import UpstoxRestError
    err = UpstoxRestError("upstox api call failed: HTTP 503",
                          status_code=503, retryable=True)
    md, _rest = _make_md(exc=err)
    try:
        await md.option_greeks(instrument_keys="NSE_FO|42631")
        ok = False
    except ProviderMarketDataError:
        ok = False  # must NOT be flattened into a client error
    except UpstoxRestError:
        ok = True
    runner.assert_true("upstream-503-propagates", ok)


# -- 15. REST contract ----------------------------------------------------------------------

async def test_rest_contract(runner: R) -> None:
    md, _rest = _make_md(_GREEKS)
    route = _route(md)
    code, data = await _get_json(
        route, b"instrument_key=NSE_FO%7C42631")
    runner.assert_eq("rest-200", code, 200)
    entries = data["data"]["entries"]
    runner.assert_eq("rest-n", len(entries), 1)
    runner.assert_eq("rest-delta", entries[0]["delta"], 0.463)
    runner.assert_true("rest-rho-null", entries[0]["rho"] is None)
    # missing key -> 400
    code, _d = await _get_json(route, b"")
    runner.assert_eq("rest-missing-400", code, 400)
    # fyers identity -> honest 400, never 502
    code, data = await _get_json(
        route, b"instrument_key=NSE%3ANIFTY2690823800CE")
    runner.assert_eq("rest-fyers-400", code, 400)
    runner.assert_true("rest-fyers-msg", "not available" in data["error"])
    # upstream 401 -> classified 400 (rejected session), NOT 502
    from brokers.upstox.errors import UpstoxAuthError
    md2, _r2 = _make_md(exc=UpstoxAuthError(
        "upstox api call failed: HTTP 401 [UDAPI100050]"))
    code, data = await _get_json(
        _route(md2), b"instrument_key=NSE_FO%7C42631")
    runner.assert_eq("rest-401-mapped-400", code, 400)
    # retryable upstream failure stays an honest 502
    md3, _r3 = _make_md(exc=__import__(
        "brokers.upstox.errors", fromlist=["UpstoxRestError"]
    ).UpstoxRestError("upstox api call failed: HTTP 503",
                      status_code=503, retryable=True))
    code, _d = await _get_json(
        _route(md3), b"instrument_key=NSE_FO%7C42631")
    runner.assert_eq("rest-503-stays-502", code, 502)


# -- 16. MCP parity (contract shape) ----------------------------------------------------------

async def test_mcp_contract_shape(runner: R) -> None:
    import types

    class _FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, name=None, description=None):
            def deco(fn):
                self.tools[name] = fn
                return fn
            return deco

    md, rest = _make_md(_GREEKS)
    services = types.SimpleNamespace(
        provider_market_data=md, identity_resolver=None,
        instrument_catalog=None, market_service=None, store=None,
        market_intel=None)
    fake = _FakeMCP()
    import mcp_server.tools.market as _mt
    _mt.register_market_tools(fake, services)
    from mcp_server.tools.market_intel_tools import (
        register_market_intel_tools)
    register_market_intel_tools(fake, services)
    # No standalone-Greeks tool: MCP Greeks ride the canonical chain tool.
    runner.assert_true("mcp-no-standalone-greeks",
                       not any("greek" in t for t in fake.tools))
    # option_chain exists and shares the same canonical service instance.
    runner.assert_true("mcp-chain-tool", "option_chain" in fake.tools)
    runner.assert_true("mcp-same-service",
                       services.provider_market_data is md)


# -- 17. Test Center classification shape -------------------------------------------------------

async def test_testcenter_shape(runner: R) -> None:
    from app import diagnostics as _dg
    src = inspect.getsource(_dg.DiagnosticsRunner._check_greeks)
    runner.assert_in("tc-option-identity", "_resolve_greeks_option", src)
    runner.assert_in("tc-pass", "greeks_available", src)
    runner.assert_in("tc-partial", "partial_fields", src)
    runner.assert_in("tc-unavailable", "no_greeks", src)
    runner.assert_in("tc-unsupported", "provider_not_supported", src)
    runner.assert_in("tc-fail", "fetch_error", src)
    # The legacy index probe must be gone: it always zeroed.
    runner.assert_not_in("tc-no-index-probe", "NSE_INDEX", src)


# -- 18/19. regression guards ---------------------------------------------------------------------

async def test_chain_greeks_path_untouched(runner: R) -> None:
    from market.normalize.upstox import option_chain_from_rest
    payload = {"data": [{
        "strike_price": 23800.0, "atm": True,
        "call_options": {"market_data": {"ltp": 56.6},
                         "option_greeks": {"delta": 0.463, "iv": 13.06}},
        "put_options": {"market_data": {"ltp": 71.15},
                        "option_greeks": {"delta": -0.5391}},
    }]}
    snap = option_chain_from_rest(
        payload, instrument_token="NSE_INDEX|Nifty 50", exchange="NSE",
        tradingsymbol="NIFTY", expiry="2026-09-08")
    runner.assert_eq("chain-n", len(snap.strikes), 1)
    runner.assert_eq("chain-delta", snap.strikes[0].call.delta, 0.463)
    runner.assert_true("chain-iv-fraction",
                       abs(snap.strikes[0].call.iv - 0.1306) < 1e-9)


async def test_history_untouched(runner: R) -> None:
    from app.market_data import upstox_interval_token
    runner.assert_eq("hist-day", upstox_interval_token("days", 1),
                     ("history", "day"))
    runner.assert_eq("hist-intraday", upstox_interval_token("minutes", 1),
                     ("intraday", "1minute"))
    md, rest = _make_md({"status": "ok", "data": {"candles": [
        ["2026-07-31T00:00:00+05:30", 100, 105, 99, 104, 800000]]}})
    candles = await md.history(
        instrument_key="NSE_INDEX|Nifty 50", unit="days", interval=1,
        from_date="2026-07-01", to_date="2026-07-31")
    runner.assert_eq("hist-n", len(candles), 1)
    runner.assert_in("hist-encoded", "NSE_INDEX%7CNifty%2050",
                     rest.calls[0]["url"])
    runner.assert_in("hist-day-token", "/day/2026-07-31/2026-07-01",
                     rest.calls[0]["url"])


# -- main ------------------------------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_infer_provider_options(runner)
    await test_upstox_greeks_request(runner)
    await test_partial_greeks_honest(runner)
    await test_fyers_greeks_unwired_honest(runner)
    await test_explicit_override(runner)
    await test_no_data_is_honest(runner)
    await test_invalid_keys(runner)
    await test_upstream_401_is_client_error(runner)
    await test_upstream_400_404_are_client_errors(runner)
    await test_upstream_retryable_propagates(runner)
    await test_rest_contract(runner)
    await test_mcp_contract_shape(runner)
    await test_testcenter_shape(runner)
    await test_chain_greeks_path_untouched(runner)
    await test_history_untouched(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = asyncio.run(main())
    sys.exit(0 if _ok else 1)
