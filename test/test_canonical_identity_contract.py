#!/usr/bin/env python3
"""Additive canonical identity contract tests (S8a).

``preferences()`` adds a ``canonical_id`` field while RETAINING the native
``key`` (the backend mutation contract). The canonical id is cross-provider
identity; the native key stays the mutation key.
"""

from __future__ import annotations

import os
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.subscriptions import SubscriptionService
from core.persistence.store import EventStore


class _Catalog:
    def derivative_expiries(self, *a, **k):
        return []

    def search(self, **kw):
        return []

    def option_strikes(self, *a, **k):
        return []


class _Resolver:
    def resolve(self, key):
        if key == "NSE_EQ|INE002A01018":
            return "NSE:EQUITY:INE002A01018"
        return None


def _make_svc(resolver=None):
    db = os.path.join(tempfile.gettempdir(), f"cid_{os.getpid()}.db")
    if os.path.exists(db):
        os.unlink(db)
    store = EventStore(db)
    return SubscriptionService(store, _Catalog(), identity_resolver=resolver)


def test_indices_carry_canonical_id() -> None:
    svc = _make_svc()
    svc.ensure_defaults()
    prefs = svc.preferences()
    nifty = next(i for i in prefs["indices"] if i["label"] == "NIFTY")
    assert nifty["canonical_id"] == "NSE:INDEX:NIFTY"
    # native key retained as provenance
    assert nifty["key"] == "NSE_INDEX|Nifty 50"


def test_sensex_canonical_id_uses_bse() -> None:
    svc = _make_svc()
    svc.ensure_defaults()
    prefs = svc.preferences()
    sensex = next(i for i in prefs["indices"] if i["label"] == "SENSEX")
    assert sensex["canonical_id"] == "BSE:INDEX:SENSEX"


def test_stock_canonical_id_resolved() -> None:
    svc = _make_svc(resolver=_Resolver())
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")
    stocks = svc.preferences()["stocks"]
    assert stocks[0]["canonical_id"] == "NSE:EQUITY:INE002A01018"
    # native mutation key retained
    assert stocks[0]["key"] == "NSE_EQ|INE002A01018"


def test_stock_canonical_id_none_without_resolver() -> None:
    svc = _make_svc()
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")
    stocks = svc.preferences()["stocks"]
    assert stocks[0]["canonical_id"] is None


def test_mutation_key_unchanged_by_canonical_id() -> None:
    svc = _make_svc(resolver=_Resolver())
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")
    svc.set_stock_enabled("NSE_EQ|INE002A01018", False)
    stocks = svc.preferences()["stocks"]
    assert stocks[0]["enabled"] is False
    assert stocks[0]["key"] == "NSE_EQ|INE002A01018"
    assert stocks[0]["canonical_id"] == "NSE:EQUITY:INE002A01018"


def test_index_mutation_still_uses_label() -> None:
    svc = _make_svc()
    svc.ensure_defaults()
    svc.set_index("NIFTY", False)
    nifty = next(i for i in svc.preferences()["indices"]
                 if i["label"] == "NIFTY")
    assert nifty["enabled"] is False
    assert nifty["canonical_id"] == "NSE:INDEX:NIFTY"


def test_watchlist_items_carry_canonical_id() -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from api.product_routes import build_watchlist_routes
    from core.persistence.store import EventStore

    db = os.path.join(tempfile.gettempdir(), f"wl_{os.getpid()}.db")
    if os.path.exists(db):
        os.unlink(db)
    store = EventStore(db)
    wl = store.create_watchlist("Watch")
    store.add_watchlist_item(
        wl["id"], exchange="NSE", instrument_token="NSE_EQ|INE002A01018",
        tradingsymbol="RELIANCE")
    routes = build_watchlist_routes(store, identity_resolver=_Resolver())
    client = TestClient(Starlette(routes=routes))
    resp = client.get("/api/watchlists")
    assert resp.status_code == 200
    items = resp.json()["watchlists"][0]["items"]
    assert items[0]["canonical_id"] == "NSE:EQUITY:INE002A01018"
    # native identity retained for depth/nav
    assert items[0]["instrument_token"] == "NSE_EQ|INE002A01018"


def test_watchlist_items_canonical_id_none_without_resolver() -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from api.product_routes import build_watchlist_routes
    from core.persistence.store import EventStore

    db = os.path.join(tempfile.gettempdir(), f"wl2_{os.getpid()}.db")
    if os.path.exists(db):
        os.unlink(db)
    store = EventStore(db)
    wl = store.create_watchlist("Watch")
    store.add_watchlist_item(
        wl["id"], exchange="NSE", instrument_token="NSE_EQ|INE002A01018",
        tradingsymbol="RELIANCE")
    routes = build_watchlist_routes(store)
    client = TestClient(Starlette(routes=routes))
    resp = client.get("/api/watchlists")
    assert resp.status_code == 200
    items = resp.json()["watchlists"][0]["items"]
    assert items[0]["canonical_id"] is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
