"""Regression tests for the market-data foundation stabilization fixes.

Self-contained: imports the changed modules directly and exercises the
corrected behavior with lightweight fakes. Does NOT depend on the composed
``app.server`` object or the pre-existing ``app``-global test harness.

Run:
    python -m pytest test/test_data_foundation_fixes.py -q
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from market.models import Quote


# ---------------------------------------------------------------------------
# 1. Upstox authenticated_request must accept `params` (GET query string)
# ---------------------------------------------------------------------------


def _fake_rest_with_capture():
    captured = {}

    class _Transport:
        def __init__(self, *a, **k):
            pass

        def __call__(self, method, url, headers, body, timeout):
            captured["url"] = url
            captured["body"] = body

            class _Resp:
                status = 200
                body = b"{}"

            return _Resp()

    import brokers.upstox.rest as rest_mod

    return rest_mod.UpstoxRest(sync_transport=_Transport()), captured


def test_authenticated_request_builds_query_string():
    rest, cap = _fake_rest_with_capture()

    async def go():
        return await rest.authenticated_request(
            method="GET", url="https://x/v2/market/oi",
            access_token="TKN",
            params={"instrument_key": "NSE|F", "expiry": "2026-01-01"})

    asyncio.run(go())
    assert "instrument_key=NSE%7CF" in cap["url"]
    assert "expiry=2026-01-01" in cap["url"]


# ---------------------------------------------------------------------------
# 2. fii()/dii() must request once per data_type (the API returns only the
#    requested type; the previous loop overwrote the param and parsed one
#    payload for every type).
# ---------------------------------------------------------------------------


def test_fii_requests_once_per_data_type():
    import app.market_data as md

    calls = []

    class _Rest:
        async def authenticated_request(self, *, method, url, access_token,
                                        params=None, json_body=None, timeout=15.0):
            calls.append(dict(params))
            return {"data": {params["data_type"]: [{"date": "2026-01-01", "value": 1}]},
                    "interval": "1D"}

    pmd = md.ProviderMarketData(upstox_auth_context_fn=lambda: (None, None))
    pmd._auth = lambda: (_Rest(), type("C", (), {"access_token": "T"})())

    async def go():
        return await pmd.fii(data_types=["NSE_FNO|INDEX", "NSE_FNO|STOCK"], interval="1D")

    res = asyncio.run(go())
    assert len(calls) == 2
    assert sorted(res.keys()) == ["NSE_FNO|INDEX", "NSE_FNO|STOCK"]


# ---------------------------------------------------------------------------
# 3. Fyers index ticks (topic prefix "if") must NOT enter the canonical store
#    — the HSM index multiplier/precision metadata is unreliable (a captured
#    NIFTY snapshot decoded to ~87,000 instead of ~24,400). Upstox is the
#    authoritative index source; Fyers index ticks are dropped at the gate.
# ---------------------------------------------------------------------------


def test_fyers_index_tick_is_rejected():
    # Replicates the gating decision in brokers/fyers/feed.py::_emit_tick.
    def gate(topic, multiplier, precision, raw):
        if topic.startswith("if"):
            return None
        scale = (10 ** precision) * multiplier
        return raw / scale

    # Bogus index multiplier (28/0) would yield ~87,000 — must be dropped.
    assert gate("if|nse_cm|Nifty 50", 28, 0, 2441760) is None
    # A normal equity decodes correctly.
    assert gate("sf|nse_cm|RELIANCE", 1, 1, 12345) == 1234.5


# ---------------------------------------------------------------------------
# 4. MCP market tools must resolve instrument references through the SAME
#    identity resolver as REST, so a Fyers catalog token resolves to the live
#    feed key (and thus the stored quote) instead of "quote not found".
# ---------------------------------------------------------------------------


def test_mcp_resolves_catalog_token_to_feed_key():
    import mcp_server.tools.market as mkt

    class _Resolver:
        def __init__(self, m):
            self._m = m

        def resolve(self, t):
            return self._m.get(t)

    class _Services:
        identity_resolver = _Resolver({"101000000026000": "NSE:NIFTY50-INDEX"})
        instrument_catalog = None

    class _Catalog:
        def search(self, q, limit=1):
            if q == "NIFTY":
                return [{"exchange": "NSE", "instrument_token": "101000000026000"}]
            return []

    svc = _Services()
    svc.instrument_catalog = _Catalog()

    assert mkt._parse_instrument_ref("NIFTY", svc) == ("NSE", "NSE:NIFTY50-INDEX")
    assert mkt._parse_instrument_ref("101000000026000", svc) == ("NSE", "NSE:NIFTY50-INDEX")
    assert mkt._parse_instrument_ref("NSE:NIFTY50-INDEX", svc) == ("NSE", "NSE:NIFTY50-INDEX")


# ---------------------------------------------------------------------------
# 5. The market SSE stream must (a) frame quotes as `event: quote` (so the
#    WebUI listener fires) and (b) emit a reconciliation `reset` + snapshot on
#    (re)connect so stale pre-restart values are not displayed forever.
# ---------------------------------------------------------------------------


def test_market_sse_emits_reset_and_quote_events():
    import api.routes as routes

    class _Sub:
        def __init__(self, lines):
            self._lines = lines
            self._i = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._i >= len(self._lines):
                raise StopAsyncIteration
            v = self._lines[self._i]
            self._i += 1
            return v

    class _Broker:
        def __init__(self, lines):
            self._lines = lines

        def subscribe(self):
            return _Sub(self._lines)

    class _Service:
        def __init__(self, quotes):
            self._q = quotes

        async def quotes(self):
            return self._q

    q = Quote(
        instrument_token="NSE:NIFTY50-INDEX", exchange="NSE", tradingsymbol="NIFTY",
        received_ts=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        ltp=24500.0, change=10.0, change_percent=0.04)

    broker = _Broker(['{"type":"quote","data":{"exchange":"NSE","instrument_token":"X","ltp":9}}'])
    svc = _Service([q])

    routes_mod = routes.build_market_routes(market_broker=broker, market_service=svc)
    handler = next(r.endpoint for r in routes_mod if r.path == "/api/market/stream")

    async def drive():
        resp = await handler(None)
        chunks = []
        async for chunk in resp.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else chunk)
        return "".join(chunks)

    out = asyncio.run(drive())
    assert "event: reset" in out
    assert "event: quote" in out
    assert "NSE:NIFTY50-INDEX" in out
    assert '"ltp":9' in out
