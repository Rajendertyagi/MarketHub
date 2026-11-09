#!/usr/bin/env python3
"""F&O coverage + routing + budgets + derivatives-owner tests (Phase 4)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.fno_coverage import (
    CAPACITY_LIMITED,
    UNCOVERED,
    UNSUBSCRIBED,
    FnoCoverageService,
)
from app.subscriptions.service import SubscriptionService
from core.persistence.store import EventStore
from providers.capabilities import (
    CAP_LTP,
    CAP_OI,
    CAP_PREVIOUS_OI,
    STYLE_COLON,
    STYLE_NUMERIC,
    STYLE_PIPE,
    ProviderCapabilities,
)
from providers.registry import ProviderRegistry

_INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
            "NIFTYFPI", "SENSEX", "BANKEX")


def _registry(up=2000, fy=5000):
    upc = ProviderCapabilities(
        provider="upstox", label="Upstox",
        capabilities=frozenset({CAP_LTP, CAP_OI}),
        modes={"full": up}, default_mode="full", key_styles=(STYLE_PIPE,))
    fyc = ProviderCapabilities(
        provider="fyers", label="Fyers",
        capabilities=frozenset({CAP_LTP, CAP_OI, CAP_PREVIOUS_OI}),
        modes={"full": fy}, default_mode="full",
        key_styles=(STYLE_COLON, STYLE_NUMERIC))
    return ProviderRegistry([upc, fyc])


class _Cat:
    """Synthetic catalog: index spots + one stock, futures for a few names."""

    def __init__(self, *, future_underlyings=("NIFTY", "RELIANCE"),
                 upstox_only=(), no_token=(), unknown_provider=()):
        self._future_underlyings = list(future_underlyings)
        self._upstox_only = set(upstox_only)
        self._no_token = set(no_token)
        self._unknown = set(unknown_provider)

    def fno_universe(self, *, provider, today, limit):
        return [{"symbol": "RELIANCE"}]

    def equity_universe(self, *, provider=None, limit=5000):
        # Bulk equity read model used for stock spot-key resolution.
        p = provider or "upstox"
        return [{"tradingsymbol": "RELIANCE", "provider": p,
                 "provider_symbol": f"EQ-{p}", "instrument_token": f"EQ-{p}"}]

    def derivative_expiries(self, underlying, instrument_type):
        return ["2026-10-27", "2026-10-27"]

    def list_future_contracts(self, *, expiries=None, **kw):
        rows = []
        for u in self._future_underlyings:
            if u in self._unknown:
                rows.append({"exchange": "NSE", "underlying": u,
                             "expiry": "2026-10-27",
                             "instrument_type": "FUTURE",
                             "provider": "unknown",
                             "provider_symbol": "X", "instrument_token": "X",
                             "tradingsymbol": f"{u}FUT", "lot_size": 1})
                continue
            if u not in self._no_token:
                rows.append({"exchange": "NSE", "underlying": u,
                             "expiry": "2026-10-27", "instrument_type": "FUTURE",
                             "provider": "upstox",
                             "provider_symbol": f"NSE_FO|U-{u}",
                             "instrument_token": f"NSE_FO|U-{u}",
                             "tradingsymbol": f"{u}FUT", "lot_size": 1})
            if u not in self._upstox_only and u not in self._no_token:
                rows.append({"exchange": "NSE", "underlying": u,
                             "expiry": "2026-10-27", "instrument_type": "FUTURE",
                             "provider": "fyers",
                             "provider_symbol": f"NSE:{u}FUT",
                             "instrument_token": f"F-{u}",
                             "tradingsymbol": f"{u}FUT", "lot_size": 1})
        if expiries:
            rows = [r for r in rows if r["expiry"] in expiries]
        return rows

    def search(self, **kw):
        if kw.get("instrument_type") != "EQUITY":
            return []
        return [
            {"tradingsymbol": "RELIANCE", "provider": "upstox",
             "provider_symbol": "NSE_EQ|INE002A01018",
             "instrument_token": "NSE_EQ|INE002A01018"},
            {"tradingsymbol": "RELIANCE", "provider": "fyers",
             "provider_symbol": "NSE:RELIANCE-EQ", "instrument_token": "E-REL"},
        ]


def _svc(cat=None, reg=None, **kw):
    return FnoCoverageService(cat or _Cat(), registry=reg or _registry(), **kw)


# -- coverage / routing ------------------------------------------------------

def test_universe_is_six_indices_plus_catalog_stocks() -> None:
    plan = _svc().plan()
    unders = {c.underlying for c in plan.contracts}
    assert set(_INDICES) <= unders
    assert "RELIANCE" in unders
    # index spots present, no hardcoded stock list
    index_spots = [c for c in plan.contracts
                   if c.kind == "index" and c.instrument_type == "SPOT"]
    assert len(index_spots) == 8


def test_default_routing_prefers_first_provider() -> None:
    plan = _svc().plan()
    fut = next(c for c in plan.contracts
               if c.underlying == "NIFTY" and c.instrument_type == "FUTURE")
    assert fut.provider == "upstox"
    assert plan.by_provider.get("upstox", 0) >= 1


def test_preferred_provider_routes_contracts() -> None:
    plan = _svc().plan(config={"preferred_provider": "fyers"})
    fut = next(c for c in plan.contracts
               if c.underlying == "NIFTY" and c.instrument_type == "FUTURE")
    assert fut.provider == "fyers"
    assert fut.provider_token == "NSE:NIFTYFUT"


def test_capability_filtering_selects_capable_provider() -> None:
    # Only Fyers declares previous_oi -> all routed to fyers.
    plan = _svc().plan(required_caps=frozenset({CAP_LTP, CAP_PREVIOUS_OI}))
    routed = [c for c in plan.contracts if c.provider is not None]
    assert routed and all(c.provider == "fyers" for c in routed)


def test_availability_filtering() -> None:
    # Upstox auth_required -> futures fall to fyers.
    statuses = {"upstox": "auth_required", "fyers": "streaming"}
    plan = _svc(status_provider=lambda p: statuses.get(p)).plan()
    fut = next(c for c in plan.contracts
               if c.underlying == "NIFTY" and c.instrument_type == "FUTURE")
    assert fut.provider == "fyers"


def test_capacity_limited_when_budget_exhausted() -> None:
    # The budget IS the provider's verified limit (there is no reserve
    # any more): with a limit of 2 per provider and a larger demand, the
    # overflow must be REPORTED, never silently dropped.
    plan = _svc(reg=_registry(up=2, fy=2)).plan()
    limited = [c for c in plan.contracts if c.state == CAPACITY_LIMITED]
    assert limited, plan.states


def test_uncovered_when_no_provider_has_token() -> None:
    cat = _Cat(future_underlyings=("NIFTY",), unknown_provider=("NIFTY",))
    plan = _svc(cat=cat).plan()
    fut = next(c for c in plan.contracts
               if c.underlying == "NIFTY" and c.instrument_type == "FUTURE")
    assert fut.state == UNCOVERED
    assert fut.provider is None


def test_provider_allocation_counts() -> None:
    plan = _svc().plan()
    assert plan.by_provider
    assert sum(plan.by_provider.values()) == plan.routed
    assert plan.states.get(UNSUBSCRIBED, 0) == plan.routed


# -- derivatives-universe owner ----------------------------------------------

_DB_SEQ = [0]


def _sub_service(registry=None):
    _DB_SEQ[0] += 1
    db = os.path.join(tempfile.gettempdir(),
                      f"fnocov_{os.getpid()}_{_DB_SEQ[0]}.db")
    if os.path.exists(db):
        os.unlink(db)
    store = EventStore(db)
    svc = SubscriptionService(store, catalog=None, registry=registry)
    return svc, store, db


def test_derivatives_owner_is_additive() -> None:
    svc, store, db = _sub_service()
    svc.ensure_defaults()
    base = svc.resolve()["by_provider"]["upstox"]
    svc.set_derivatives_universe({"upstox": ["NSE_FO|999"]})
    r = svc.resolve()
    assert "NSE_FO|999" in r["by_provider"]["upstox"]
    assert all(k in r["by_provider"]["upstox"] for k in base)
    # release only the derivatives owner; base survives
    svc.clear_derivatives_universe()
    r2 = svc.resolve()
    assert "NSE_FO|999" not in r2["by_provider"]["upstox"]
    assert all(k in r2["by_provider"]["upstox"] for k in base)
    os.unlink(db)


def test_derivatives_owner_replace_and_coexist() -> None:
    svc, store, db = _sub_service()
    svc.set_active_view({"upstox": ["NSE_FO|AV"]})
    svc.set_analytics_universe({"upstox": ["NSE_EQ|AN"]})
    svc.set_derivatives_universe({"upstox": ["NSE_FO|D1"]})
    r = svc.resolve()["by_provider"]["upstox"]
    assert {"NSE_FO|AV", "NSE_EQ|AN", "NSE_FO|D1"} <= set(r)
    # replace derivatives only
    svc.set_derivatives_universe({"upstox": ["NSE_FO|D2"]})
    r2 = svc.resolve()["by_provider"]["upstox"]
    assert "NSE_FO|D1" not in r2 and "NSE_FO|D2" in r2
    assert "NSE_FO|AV" in r2 and "NSE_EQ|AN" in r2
    os.unlink(db)


# -- coverage API ------------------------------------------------------------

def _routes(svc, coverage, feed=None):
    from api.fno_coverage_routes import build_fno_coverage_routes

    class _CatAPI:
        def sync_state(self):
            return [{"provider": "upstox", "instruments": 10}]

        def segment_counts(self):
            return {"NSE_FO": 5}

    routes = build_fno_coverage_routes(
        _CatAPI(), svc, coverage, feed_provider=lambda n: feed)
    out = {}
    for r in routes:
        for m in (r.methods or ()):
            out[(r.path, m)] = r
    return out


async def _json_of(route, request):
    resp = await route.endpoint(request)
    import json
    return resp.status_code, json.loads(resp.body)


def _req(method, path, body=b""):
    from starlette.requests import Request

    scope = {"type": "http", "method": method, "path": path,
             "headers": [(b"content-type", b"application/json")],
             "query_string": b""}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def test_api_catalog_coverage_preview() -> None:
    svc, store, db = _sub_service()
    coverage = _svc()
    routes = _routes(svc, coverage)

    async def run():
        c = await _json_of(routes[("/api/fno/catalog", "GET")],
                           _req("GET", "/api/fno/catalog"))
        assert c[0] == 200 and "fno" in c[1]
        cov = await _json_of(routes[("/api/fno/coverage", "GET")],
                             _req("GET", "/api/fno/coverage"))
        assert cov[0] == 200 and cov[1]["desired"] > 0
        prev = await _json_of(routes[("/api/fno/preview", "GET")],
                              _req("GET", "/api/fno/preview"))
        assert prev[0] == 200 and prev[1]["preview"] is True
        # preview must not change subscriptions
        assert svc.derivatives_universe_keys() == {}

    asyncio.run(run())
    os.unlink(db)


def test_api_apply_and_release() -> None:
    import json
    svc, store, db = _sub_service()
    coverage = _svc()

    class _Feed:
        def __init__(self):
            self._instrument_keys = ()
            self.added = []

        async def add_instruments(self, keys, metadata=None):
            self.added += list(keys)
            self._instrument_keys = tuple(sorted(
                set(self._instrument_keys) | set(keys)))
            return len(keys)

        async def remove_instruments(self, keys):
            self._instrument_keys = tuple(
                k for k in self._instrument_keys if k not in set(keys))
            return len(keys)

    feed = _Feed()
    routes = _routes(svc, coverage, feed=feed)
    apply_route = routes[("/api/fno/apply", "POST")]
    release_route = routes[("/api/fno/apply", "DELETE")]

    async def run():
        code, data = await _json_of(
            apply_route, _req("POST", "/api/fno/apply",
                              json.dumps({}).encode()))
        assert code == 200, data
        assert data["routed"] > 0
        assert svc.derivatives_universe_keys()  # owner set
        assert len(feed._instrument_keys) > 0    # feed reconciled
        code2, data2 = await _json_of(
            release_route, _req("DELETE", "/api/fno/apply"))
        assert code2 == 200, data2
        assert svc.derivatives_universe_keys() == {}

    asyncio.run(run())
    os.unlink(db)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
