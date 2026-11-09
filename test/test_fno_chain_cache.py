#!/usr/bin/env python3
"""F&O workspace: the provider chain snapshot is fetched once per window.

The workspace view is POLLED every few seconds by the WebUI. The provider
chain snapshot is a last-session REST snapshot (OI/IV/greeks) that changes
slowly, so the route fetches it once per bounded window instead of once per
poll - a provider that cannot serve the request must not be hammered (and
logged) on every poll.

Proves:
  * a repeated poll does NOT hit the provider again;
  * the failure is reported ONCE per window (at WARNING) and the payload
    carries the reason on every response (nothing is hidden);
  * a snapshot that IS available is reused for the polls in the window.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest


class _Subscriptions:
    def __init__(self):
        self.workspace_calls = 0

    def workspace_contracts(self, symbol, **kw):
        """Synchronous (the route runs it in a worker thread)."""
        self.workspace_calls += 1
        return {
            "symbol": symbol,
            "equity_key": "NSE_EQ|INE002A01010",
            "futures": [],
            "options": [],
            "selected_expiry": "2026-10-27",
            "notes": [],
        }


class _FailingProvider:
    def __init__(self):
        self.calls = 0

    async def option_chain(self, **kw):
        self.calls += 1
        from app.market_data import ProviderMarketDataError
        raise ProviderMarketDataError("provider session unavailable")


class _WorkingProvider:
    def __init__(self):
        self.calls = 0

    async def option_chain(self, **kw):
        self.calls += 1
        return None            # no strikes: the workspace stays unchanged


class _Req:
    def __init__(self, path_params=None, query_params=None):
        self.path_params = path_params or {}
        self.query_params = query_params or {}

    def json(self):
        return {}


def _routes(provider_md, subscriptions):
    from api.fno_routes import build_fno_routes

    return build_fno_routes(
        catalog=None, subscriptions=subscriptions, market_service=None,
        feed_provider=None, provider_md=provider_md, quote_service=None)


def _stock_route(routes):
    for route in routes:
        if route.path == "/api/market/fno/stock/{symbol}":
            return route.endpoint
    raise AssertionError("the F&O stock route is missing")


def _call(routes, symbol="HDFCBANK"):
    endpoint = _stock_route(routes)
    req = _Req(path_params={"symbol": symbol},
               query_params={"window": "10"})
    resp = asyncio.run(endpoint(req))
    return json.loads(resp.body)


def test_a_repeated_poll_does_not_hit_the_provider_again() -> None:
    provider = _FailingProvider()
    subs = _Subscriptions()
    routes = _routes(provider, subs)

    first = _call(routes)
    second = _call(routes)

    assert provider.calls == 1, (
        "the provider chain snapshot must be fetched once per window, not "
        "once per poll")
    # The reason travels with EVERY response (nothing is hidden).
    notes = " ".join(second.get("notes") or [])
    assert "unavailable" in notes, (
        "the payload must carry the reason on every response")
    assert first.get("status") == "ok"


def test_the_provider_failure_is_logged_once_per_window(caplog) -> None:
    provider = _FailingProvider()
    subs = _Subscriptions()
    routes = _routes(provider, subs)

    with caplog.at_level(logging.DEBUG, logger="api.fno_routes"):
        for _ in range(5):
            _call(routes)

    warnings = [r for r in caplog.records
                if r.levelno >= logging.WARNING
                and "chain snapshot unavailable" in r.getMessage()]
    assert len(warnings) == 1, (
        "the failure is reported ONCE per window at WARNING, not once per "
        "poll")
    repeats = [r for r in caplog.records
               if r.levelno == logging.DEBUG
               and "still unavailable" in r.getMessage()]
    assert repeats, "the repeats are counted, not hidden"
    assert provider.calls == 1


def test_an_available_snapshot_is_reused_for_the_polls_in_the_window(
        ) -> None:
    provider = _WorkingProvider()
    subs = _Subscriptions()
    routes = _routes(provider, subs)

    _call(routes)
    _call(routes)
    _call(routes)

    assert provider.calls == 1, (
        "a poll must reuse the snapshot of the window")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
