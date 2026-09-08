#!/usr/bin/env python3
"""Phase A — analytics cash-equity coverage owner tests.

Verifies the NEW analytics-universe subscription owner:
  * it unions into the resolved desired set (no redesign of reconciliation)
  * reconcile() subscribes its cash-equity keys through the EXISTING
    feed.add_instruments(metadata=...) path (preserves the 1cf52fd
    unknown_instrument metadata fix)
  * clearing/leaving analytics removes ONLY analytics keys; baseline indices,
    persistent stocks and F&O active-view keys survive
  * switching universe replaces (not accumulates) analytics keys
  * the route resolves a universe to canonical NSE_EQ keys and applies them

NO LIVE BROKER. Lightweight fakes only.
"""

from __future__ import annotations

import asyncio
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.subscriptions.service import SubscriptionService


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeStore:
    def __init__(self):
        self._subs = {}
        self._rules = []
        self._state = {}

    def get_md_subscription(self, category, key):
        return self._subs.get((category, key))

    def upsert_md_subscription(self, category, key, label, enabled=True):
        row = {"category": category, "key": key, "label": label, "enabled": enabled}
        self._subs[(category, key)] = row
        return row

    def list_md_subscriptions(self):
        return list(self._subs.values())

    def delete_md_subscription(self, category, key):
        return self._subs.pop((category, key), None) is not None

    def list_md_derivative_rules(self):
        return list(self._rules)

    def upsert_md_derivative_rule(self, underlying, **kw):
        row = {"underlying": underlying, **kw}
        for i, r in enumerate(self._rules):
            if r["underlying"] == underlying:
                self._rules[i] = row
                return row
        self._rules.append(row)
        return row

    def delete_md_derivative_rule(self, underlying):
        before = len(self._rules)
        self._rules = [r for r in self._rules if r["underlying"] != underlying]
        return len(self._rules) != before

    def get_source_state(self, ns, marker):
        return self._state.get((ns, marker))

    def set_source_state(self, ns, marker, val):
        self._state[(ns, marker)] = val


class _FakeCatalog:
    """Resolves any symbol to an ISIN-backed NSE_EQ key (canonical identity)."""

    def search(self, q="", instrument_type=None, exchange=None, limit=1, **_):
        return [{
            "instrument_token": f"NSE_EQ|INE_{q}",
            "tradingsymbol": q,
            "name": q,
            "exchange": "NSE",
        }]

    def get(self, provider, key):
        return {"tradingsymbol": key.split("|")[-1]}

    def fno_universe(self, provider="upstox", today=None, limit=2000, **_):
        return [{"symbol": "RELIANCE", "name": "Reliance",
                 "equity_key": "NSE_EQ|INE_RELIANCE"}]

    def equity_universe(self, provider="upstox", limit=5000, **_):
        return [{"tradingsymbol": "RELIANCE", "name": "Reliance",
                 "exchange": "NSE", "instrument_token": "NSE_EQ|INE_RELIANCE"}]


class _FakeFeed:
    def __init__(self):
        self._instrument_keys = set()
        self.add_calls = []
        self.remove_calls = []
        self.metadata_seen = {}

    async def add_instruments(self, keys, metadata=None):
        new = [k for k in keys if k not in self._instrument_keys]
        self._instrument_keys.update(new)
        if metadata:
            self.metadata_seen.update(metadata)
        self.add_calls.append((list(new), metadata))
        return len(new)

    async def remove_instruments(self, keys):
        gone = [k for k in keys if k in self._instrument_keys]
        self._instrument_keys.difference_update(gone)
        self.remove_calls.append(list(gone))
        return len(gone)


def _mk_service():
    svc = SubscriptionService(_FakeStore(), _FakeCatalog())
    svc.ensure_defaults()
    return svc


def _feed_provider(feed):
    return lambda p: feed if p == "upstox" else None


# ── service-level tests ────────────────────────────────────────────────────────

def test_analytics_unions_into_resolve():
    svc = _mk_service()
    keys = {"upstox": ["NSE_EQ|INE_A", "NSE_EQ|INE_B", "NSE_EQ|INE_C"]}
    svc.set_analytics_universe(keys)
    res = svc.resolve()
    analytics = [c for c in res["contracts"] if c["kind"] == "analytics"]
    assert len(analytics) == 3, analytics
    # baseline indices still present
    idx = [c for c in res["contracts"] if c["kind"] == "index"]
    assert len(idx) == 8
    # no duplicate keys
    upstox = res["by_provider"]["upstox"]
    assert len(upstox) == len(set(upstox))


def test_analytics_reconcile_subscribes_with_metadata():
    svc = _mk_service()
    feed = _FakeFeed()
    svc.set_analytics_universe({"upstox": ["NSE_EQ|INE_A", "NSE_EQ|INE_B"]})
    out = asyncio.run(svc.reconcile(_feed_provider(feed)))
    assert out["apply"]["results"]["upstox"]["applied"] is True
    assert "NSE_EQ|INE_A" in feed._instrument_keys
    assert "NSE_EQ|INE_B" in feed._instrument_keys
    # metadata path reused (1cf52fd fix preserved)
    assert feed.metadata_seen.get("NSE_EQ|INE_A") == ("NSE", "INE_A")


def test_analytics_clear_keeps_baseline():
    svc = _mk_service()
    feed = _FakeFeed()
    svc.set_analytics_universe({"upstox": ["NSE_EQ|INE_A", "NSE_EQ|INE_B"]})
    asyncio.run(svc.reconcile(_feed_provider(feed)))
    assert "NSE_EQ|INE_A" in feed._instrument_keys
    # baseline index keys already subscribed
    baseline = list(feed._instrument_keys)
    svc.clear_analytics_universe()
    asyncio.run(svc.reconcile(_feed_provider(feed)))
    # analytics keys removed
    assert "NSE_EQ|INE_A" not in feed._instrument_keys
    # baseline keys survive
    for k in baseline:
        if k.startswith("NSE_INDEX"):
            assert k in feed._instrument_keys


def test_analytics_switch_replaces_not_accumulates():
    svc = _mk_service()
    feed = _FakeFeed()
    svc.set_analytics_universe({"upstox": ["NSE_EQ|INE_A"]})
    asyncio.run(svc.reconcile(_feed_provider(feed)))
    svc.set_analytics_universe({"upstox": ["NSE_EQ|INE_B"]})
    asyncio.run(svc.reconcile(_feed_provider(feed)))
    assert "NSE_EQ|INE_A" not in feed._instrument_keys
    assert "NSE_EQ|INE_B" in feed._instrument_keys


def test_analytics_coexists_with_active_view():
    svc = _mk_service()
    feed = _FakeFeed()
    svc.set_active_view({"upstox": ["NSE_FO|X"]})
    svc.set_analytics_universe({"upstox": ["NSE_EQ|INE_A"]})
    res = svc.resolve()
    kinds = [c["kind"] for c in res["contracts"]]
    assert "active_view" in kinds and "analytics" in kinds
    asyncio.run(svc.reconcile(_feed_provider(feed)))
    assert "NSE_FO|X" in feed._instrument_keys
    assert "NSE_EQ|INE_A" in feed._instrument_keys
    # removing analytics must not remove active-view key
    svc.clear_analytics_universe()
    asyncio.run(svc.reconcile(_feed_provider(feed)))
    assert "NSE_FO|X" in feed._instrument_keys


def test_analytics_status_exposes_owner():
    svc = _mk_service()
    svc.set_analytics_universe({"upstox": ["NSE_EQ|INE_A"]})
    assert svc.status()["analytics"] == {"upstox": 1}
    svc.clear_analytics_universe()
    assert svc.status()["analytics"] == {}


# ── route-level integration ─────────────────────────────────────────────────────

class _FakeMarketService:
    def get_quote_now(self, exchange, token):
        return None


def _build_client():
    from api.routes import build_market_routes
    svc = _mk_service()
    feed = _FakeFeed()
    routes = build_market_routes(
        None, market_service=_FakeMarketService(), index_catalog=_FakeCatalog(),
        subscriptions=svc, feed_provider=_feed_provider(feed),
    )
    app = Starlette(routes=routes)
    return TestClient(app), svc, feed


def test_route_coverage_post_resolves_nifty50():
    client, svc, feed = _build_client()
    resp = client.post("/api/market/analytics/coverage",
                       json={"universe": "NIFTY50"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["universe"] == "NIFTY50"
    assert body["eligible"] == 50, body
    # all 50 resolved to canonical NSE_EQ keys and subscribed
    assert body["resolved"] == 50, body
    assert len(feed._instrument_keys) >= 50
    # the service owns exactly the NIFTY50 analytics keys
    assert svc.analytics_universe_keys()["upstox"][0].startswith("NSE_EQ|INE_")


def test_route_coverage_get_reports_state():
    client, svc, feed = _build_client()
    client.post("/api/market/analytics/coverage", json={"universe": "NIFTY50"})
    resp = client.get("/api/market/analytics/coverage?universe=NIFTY50")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["eligible"] == 50
    assert body["resolved"] == 50
    assert body["desired"] == 50


def test_route_coverage_delete_clears():
    client, svc, feed = _build_client()
    client.post("/api/market/analytics/coverage", json={"universe": "NIFTY50"})
    assert svc.analytics_universe_keys()
    resp = client.delete("/api/market/analytics/coverage")
    assert resp.status_code == 200, resp.text
    assert resp.json()["cleared"] is True
    assert svc.analytics_universe_keys() == {}


def test_route_coverage_rejects_bad_universe():
    client, svc, feed = _build_client()
    resp = client.post("/api/market/analytics/coverage",
                       json={"universe": "NOTREAL"})
    assert resp.status_code == 400, resp.text
