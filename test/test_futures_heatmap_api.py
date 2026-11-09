#!/usr/bin/env python3
"""Futures heatmap API tests (Phase 7) — thin transport over the board."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.fno_coverage import RoutedContract, RoutingPlan
from market.coverage_states import (
    CAPACITY_LIMITED,
    LIVE,
    PROVIDER_UNAVAILABLE,
    UNCOVERED,
    UNSUBSCRIBED,
)

NOW = datetime.now(timezone.utc).isoformat()


def _plan(*, resolved="2026-09-29",
          expiries=("2026-09-29", "2026-10-27"), extra=()):
    contracts = [
        RoutedContract(underlying="NIFTY", kind="index", exchange="NSE",
                       expiry=resolved, instrument_type="FUTURE",
                       provider="upstox", provider_token="FUT|1",
                       state=UNSUBSCRIBED),
        RoutedContract(underlying="NIFTY", kind="index", exchange="NSE",
                       expiry="", instrument_type="SPOT", provider="upstox",
                       provider_token="SPOT|1", state=UNSUBSCRIBED),
        RoutedContract(underlying="RELIANCE", kind="equity", exchange="NSE",
                       expiry=resolved, instrument_type="FUTURE",
                       provider="upstox", provider_token="FUT|2",
                       state=UNSUBSCRIBED),
        RoutedContract(underlying="RELIANCE", kind="equity", exchange="NSE",
                       expiry="", instrument_type="SPOT", provider="upstox",
                       provider_token="SPOT|2", state=UNSUBSCRIBED),
        *extra,
    ]
    p = RoutingPlan(enabled=True, expiry_requested="latest",
                    expiry_resolved=resolved, expiries=list(expiries))
    p.contracts = contracts
    p.desired = len(contracts)
    p.routed = sum(1 for c in contracts if c.provider)
    p.by_provider = {"upstox": p.routed}
    p.states = {UNSUBSCRIBED: p.routed}
    p.capacity = {"upstox": {"budget": 1800, "used": p.routed,
                             "available": 1800 - p.routed}}
    p.providers = [{"provider": "upstox", "label": "Upstox", "enabled": True,
                    "status": "streaming", "available": True,
                    "capabilities": ["ltp", "oi"], "budget": 1800}]
    p.uncovered = [c for c in contracts
                   if c.state in (UNCOVERED, CAPACITY_LIMITED,
                                  PROVIDER_UNAVAILABLE)]
    p.errors = []
    return p


class _FakeCoverage:
    def __init__(self, plan):
        self._plan = plan

    def plan(self, *, expiry=None, **kw):
        if expiry and expiry not in self._plan.expiries:
            return _plan(resolved=None)
        if expiry:
            return _plan(resolved=expiry, expiries=self._plan.expiries)
        return self._plan


class _MS:
    def __init__(self, quotes):
        self._q = quotes

    def get_quote_now(self, exchange, token):
        return self._q.get(token)


class _Store:
    def get_previous_oi(self, **kw):
        return None


_QUOTES = {
    "FUT|1": {"ltp": 23400, "close": 23300, "change": 100,
              "change_percent": 0.43, "open_interest": 1000,
              "previous_oi": 900, "received_ts": NOW},
    "SPOT|1": {"ltp": 23350, "received_ts": NOW},
    # oi up, price down -> Short Buildup
    "FUT|2": {"ltp": 1500, "change": -10, "open_interest": 500,
              "previous_oi": 400, "received_ts": NOW},
    "SPOT|2": {"ltp": 1495, "received_ts": NOW},
}


def _routes(coverage, quotes=None):
    from api.futures_heatmap import build_futures_heatmap_routes
    return build_futures_heatmap_routes(coverage, _MS(quotes or _QUOTES),
                                        _Store())


def _req(path, query=b""):
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": path,
             "headers": [(b"content-type", b"application/json")],
             "query_string": query}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


async def _get(route, path, query=b""):
    resp = await route.endpoint(_req(path, query))
    return resp.status_code, json.loads(resp.body)


def _route(routes):
    return next(r for r in routes if r.path == "/api/futures/heatmap")


# -- tests -------------------------------------------------------------------

def test_heatmap_ok_and_compat_fields() -> None:
    routes = _routes(_FakeCoverage(_plan()))
    code, body = asyncio.run(_get(_route(routes), "/api/futures/heatmap"))
    assert code == 200, body
    assert body["expiry_resolved"] == "2026-09-29"
    assert body["buildup"] == "all"          # compat field
    assert body["eligible"] == 2 and len(body["items"]) == 2
    for item in body["items"]:
        assert "status" in item              # compat per-tile field
        assert "coverage" in item and "provider" in item
    assert body["providers"][0]["provider"] == "upstox"
    assert body["coverage"]["routed"] >= 1
    # unfiltered snapshot: both futures present
    assert {t["underlying"] for t in body["items"]} == {"NIFTY", "RELIANCE"}
    # basis computed from spot
    nifty = next(t for t in body["items"] if t["underlying"] == "NIFTY")
    assert nifty["basis"] == 23400 - 23350
    assert nifty["coverage"] == LIVE


def test_expiry_selection_and_unknown() -> None:
    routes = _routes(_FakeCoverage(_plan()))
    route = _route(routes)
    ok, body = asyncio.run(_get(route, "/api/futures/heatmap",
                                b"expiry=2026-10-27"))
    assert ok == 200 and body["expiry_resolved"] == "2026-10-27"
    bad, body2 = asyncio.run(_get(route, "/api/futures/heatmap",
                                  b"expiry=2099-01-01"))
    assert bad == 404 and "unknown expiry" in body2["error"]


def test_filter_projection_and_bad_param() -> None:
    routes = _routes(_FakeCoverage(_plan()))
    route = _route(routes)
    code, body = asyncio.run(_get(route, "/api/futures/heatmap", b"buildup=short"))
    assert code == 200
    assert [t["underlying"] for t in body["items"]] == ["RELIANCE"]
    assert body["buildup"] == "short"
    bad, body2 = asyncio.run(_get(route, "/api/futures/heatmap",
                                  b"buildup=bogus"))
    assert bad == 400 and "unknown buildup" in body2["error"]


def test_coverage_states_surfaced_honestly() -> None:
    extra = [
        RoutedContract(underlying="UNCOV", kind="equity", exchange="NSE",
                       expiry="2026-09-29", instrument_type="FUTURE",
                       provider=None, provider_token=None, state=UNCOVERED),
        RoutedContract(underlying="CAP", kind="equity", exchange="NSE",
                       expiry="2026-09-29", instrument_type="FUTURE",
                       provider="upstox", provider_token="FUT|CAP",
                       state=CAPACITY_LIMITED),
        RoutedContract(underlying="UNAV", kind="equity", exchange="NSE",
                       expiry="2026-09-29", instrument_type="FUTURE",
                       provider="upstox", provider_token="FUT|UNAV",
                       state=PROVIDER_UNAVAILABLE),
    ]
    routes = _routes(_FakeCoverage(_plan(extra=extra)))
    code, body = asyncio.run(_get(_route(routes), "/api/futures/heatmap"))
    assert code == 200
    by = {t["underlying"]: t["coverage"] for t in body["items"]}
    assert by["UNCOV"] == UNCOVERED
    assert by["CAP"] == CAPACITY_LIMITED
    assert by["UNAV"] == PROVIDER_UNAVAILABLE
    assert body["coverage"]["uncovered"] >= 1


def test_empty_board_is_valid() -> None:
    p = _plan(extra=[])
    p.contracts = [c for c in p.contracts
                   if c.instrument_type == "SPOT"]  # spots only, no futures
    routes = _routes(_FakeCoverage(p))
    code, body = asyncio.run(_get(_route(routes), "/api/futures/heatmap"))
    assert code == 200 and body["eligible"] == 0 and body["items"] == []


def test_missing_service_returns_503() -> None:
    routes = _routes(None)
    code, body = asyncio.run(_get(_route(routes), "/api/futures/heatmap"))
    assert code == 503


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
