#!/usr/bin/env python3
"""``migrate_from_config`` Fyers index categorization tests (S13).

The one-time config import must recognize BOTH providers' index keys
(Upstox ``NSE_INDEX|Nifty 50`` and Fyers ``NSE:NIFTY50-INDEX``) and
categorize them as INDICES keyed by the canonical label. A Fyers index key
mis-categorized as a stock is the defect this stage fixes.

Existing migration semantics preserved: one-time (marker), idempotent,
DB wins, later deletions are never resurrected.
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


def _make_svc():
    db = os.path.join(tempfile.gettempdir(), f"mig_{os.getpid()}.db")
    if os.path.exists(db):
        os.unlink(db)
    store = EventStore(db)
    return SubscriptionService(store, _Catalog()), store, db


def test_fyers_index_key_is_categorized_as_index() -> None:
    svc, store, db = _make_svc()
    result = svc.migrate_from_config({
        "fyers": {"type": "fyers_feed", "instruments": [
            {"key": "NSE:NIFTY50-INDEX", "exchange": "NSE",
             "tradingsymbol": "NIFTY 50"},
        ]},
    })
    assert result["ran"] is True
    assert result["imported"] == 1
    rows = store.list_md_subscriptions()
    index_rows = [r for r in rows if r["category"] == "index"]
    stock_rows = [r for r in rows if r["category"] == "stock"]
    assert len(index_rows) == 1
    assert index_rows[0]["key"] == "NIFTY"
    assert index_rows[0]["label"] == "NIFTY"
    assert stock_rows == []


def test_upstox_index_key_still_categorized_as_index() -> None:
    svc, store, db = _make_svc()
    svc.migrate_from_config({
        "upstox": {"type": "upstox_feed", "instruments": [
            {"key": "NSE_INDEX|Nifty 50", "exchange": "NSE",
             "tradingsymbol": "Nifty 50"},
        ]},
    })
    rows = store.list_md_subscriptions()
    assert [r["key"] for r in rows if r["category"] == "index"] == ["NIFTY"]
    assert [r for r in rows if r["category"] == "stock"] == []


def test_non_index_config_instrument_remains_stock() -> None:
    svc, store, db = _make_svc()
    svc.migrate_from_config({
        "upstox": {"type": "upstox_feed", "instruments": [
            {"key": "NSE_EQ|INE002A01018", "exchange": "NSE",
             "tradingsymbol": "RELIANCE"},
        ]},
    })
    rows = store.list_md_subscriptions()
    assert [r for r in rows if r["category"] == "index"] == []
    assert [r["key"] for r in rows if r["category"] == "stock"] == [
        "NSE_EQ|INE002A01018"]


def test_migration_is_one_time_and_idempotent() -> None:
    svc, store, db = _make_svc()
    cfg = {
        "fyers": {"type": "fyers_feed", "instruments": [
            {"key": "NSE:NIFTY50-INDEX", "exchange": "NSE",
             "tradingsymbol": "NIFTY 50"},
        ]},
    }
    first = svc.migrate_from_config(cfg)
    second = svc.migrate_from_config(cfg)
    assert first["ran"] is True
    assert first["imported"] == 1
    assert second["ran"] is False
    assert second["imported"] == 0
    rows = store.list_md_subscriptions()
    assert len([r for r in rows if r["category"] == "index"]) == 1


def test_existing_db_row_is_skipped_not_duplicated() -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    result = svc.migrate_from_config({
        "fyers": {"type": "fyers_feed", "instruments": [
            {"key": "NSE:NIFTY50-INDEX", "exchange": "NSE",
             "tradingsymbol": "NIFTY 50"},
        ]},
    })
    assert result["skipped"] == 1
    assert result["imported"] == 0
    rows = store.list_md_subscriptions()
    assert len([r for r in rows if r["key"] == "NIFTY"]) == 1


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
