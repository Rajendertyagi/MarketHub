#!/usr/bin/env python3
"""F&O read-path regression tests (performance defects).

Guards the fixes for the confirmed F&O performance defects:

  * hot catalog lookups must use the existing ``idx_instr_lookup`` index
    (the ``exchange`` constraint is the leading column) — no full scans;
  * the workspace/resolution paths must consume the canonical bulk/derived
    F&O universe instead of issuing per-underlying / per-expiry searches;
  * the canonical derived reads are cached and single-flighted, and are
    invalidated from the catalog-write (epoch) boundary.
"""

from __future__ import annotations

import collections
import os
import sqlite3
import sys
import tempfile
import threading
import time

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.instruments import InstrumentCatalog, upstox_master_records
from app.subscriptions.service import SubscriptionService
from core.persistence.modules import products as _products
from core.persistence.store import EventStore
from market import derivatives_universe as du

# Sanitized Upstox master rows: one NSE equity + one stock future + CE/PE for
# the same underlying/expiry (enough to exercise the whole workspace path).
_ROWS = [
    {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE",
     "segment": "NSE_EQ", "instrument_type": "EQ",
     "trading_symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES LTD",
     "underlying_symbol": None, "expiry": None, "strike_price": None,
     "lot_size": 1, "tick_size": 0.05},
    {"instrument_key": "NSE_FO|F1", "exchange": "NSE", "segment": "NSE_FO",
     "instrument_type": "FUT", "trading_symbol": "RELIANCE FUT 28 JAN 27",
     "name": "RELIANCE", "underlying_symbol": "RELIANCE",
     "expiry": 1832514599000, "strike_price": None,
     "lot_size": 500, "tick_size": 10.0},
    {"instrument_key": "NSE_FO|C1", "exchange": "NSE", "segment": "NSE_FO",
     "instrument_type": "CE", "trading_symbol": "RELIANCE 1400 CE 28 JAN 27",
     "name": "RELIANCE", "underlying_symbol": "RELIANCE",
     "expiry": 1832514599000, "strike_price": 1400.0,
     "lot_size": 500, "tick_size": 0.05},
    {"instrument_key": "NSE_FO|P1", "exchange": "NSE", "segment": "NSE_FO",
     "instrument_type": "PE", "trading_symbol": "RELIANCE 1400 PE 28 JAN 27",
     "name": "RELIANCE", "underlying_symbol": "RELIANCE",
     "expiry": 1832514599000, "strike_price": 1400.0,
     "lot_size": 500, "tick_size": 0.05},
]


def _real_catalog():
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    store.replace_provider_instruments("upstox", upstox_master_records(_ROWS))
    return InstrumentCatalog(store), store, tmp


class _FakeCatalog:
    """Minimal catalog double with call counting + optional build delay."""

    def __init__(self, futures=(), *, epoch=None, expiries=(),
                 options=(), delay=0.0):
        self._futures = list(futures)
        self._epoch = epoch
        self._expiries = list(expiries)
        self._options = list(options)
        self._delay = delay
        self.calls = collections.Counter()

    def catalog_epoch(self):
        return self._epoch

    def list_future_contracts(self, **kw):
        self.calls["list_future_contracts"] += 1
        if self._delay:
            time.sleep(self._delay)
        rows = list(self._futures)
        if kw.get("underlying"):
            rows = [r for r in rows if r.get("underlying") == kw["underlying"]]
        return rows

    def derivative_expiries(self, underlying, instrument_type, exchange=None):
        self.calls["derivative_expiries"] += 1
        return list(self._expiries)

    def option_strikes(self, underlying, expiry, exchange=None):
        self.calls["option_strikes"] += 1
        return list(self._options)

    def search(self, **kw):
        self.calls["search"] += 1
        return []


def _futures_rows():
    return [{"underlying": "RELIANCE", "expiry": "2027-01-28",
             "instrument_type": "FUTURE", "provider": "upstox",
             "provider_symbol": "NSE_FO|F1", "instrument_token": "NSE_FO|F1",
             "tradingsymbol": "RELIANCE FUT", "lot_size": 500,
             "exchange": "NSE"}]


# -- canonical bulk futures index --------------------------------------------

def test_futures_index_built_once_and_cached() -> None:
    du.clear_universe_cache()
    cat = _FakeCatalog(_futures_rows(), epoch=1)
    a = du.futures_index(cat, exchange="NSE")
    b = du.futures_index(cat, exchange="NSE")
    assert cat.calls["list_future_contracts"] == 1  # ONE catalog read
    assert a is b
    assert a[("RELIANCE", "2027-01-28")][0]["provider"] == "upstox"


def test_futures_index_coalesces_concurrent_reads() -> None:
    """N simultaneous identical reads must produce exactly ONE build."""
    du.clear_universe_cache()
    cat = _FakeCatalog(_futures_rows(), epoch=7, delay=0.20)
    results: list[object] = []
    start = threading.Barrier(8)

    def worker():
        start.wait()
        results.append(du.futures_index(cat, exchange="NSE"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    assert cat.calls["list_future_contracts"] == 1, cat.calls
    assert all(r is results[0] for r in results)


def test_futures_index_invalidates_on_catalog_epoch() -> None:
    du.clear_universe_cache()
    cat = _FakeCatalog(_futures_rows(), epoch=1)
    du.futures_index(cat, exchange="NSE")
    du.futures_index(cat, exchange="NSE")
    assert cat.calls["list_future_contracts"] == 1
    # Catalog replaced -> new epoch -> rebuild.
    cat._epoch = 2
    du.futures_index(cat, exchange="NSE")
    assert cat.calls["list_future_contracts"] == 2


def test_option_expiries_memoized_per_epoch() -> None:
    du.clear_universe_cache()
    cat = _FakeCatalog(epoch=5, expiries=["2027-01-28"])
    for _ in range(4):
        assert du.expiries_for(cat, "RELIANCE", "OPTION") == ["2027-01-28"]
    assert cat.calls["derivative_expiries"] == 1


def test_clear_universe_cache_drops_entries() -> None:
    du.clear_universe_cache()
    cat = _FakeCatalog(_futures_rows(), epoch=1)
    du.futures_index(cat, exchange="NSE")
    du.clear_universe_cache()
    du.futures_index(cat, exchange="NSE")
    assert cat.calls["list_future_contracts"] == 2


def test_uncached_without_catalog_epoch() -> None:
    """A catalog double with no epoch is never cached (correctness first)."""
    du.clear_universe_cache()
    cat = _FakeCatalog(_futures_rows(), epoch=None)
    du.futures_index(cat, exchange="NSE")
    du.futures_index(cat, exchange="NSE")
    assert cat.calls["list_future_contracts"] == 2


# -- workspace resolution consumes the canonical read model -------------------

class _Recording:
    """Catalog proxy that records every call + kwargs (real behavior)."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[tuple[str, dict]] = []

    def catalog_epoch(self):
        return self._inner.catalog_epoch()

    def search(self, **kw):
        self.calls.append(("search", dict(kw)))
        return self._inner.search(**kw)

    def derivative_expiries(self, underlying, instrument_type, exchange=None):
        self.calls.append(("derivative_expiries", {
            "underlying": underlying, "instrument_type": instrument_type,
            "exchange": exchange}))
        return self._inner.derivative_expiries(
            underlying, instrument_type, exchange=exchange)

    def option_strikes(self, underlying, expiry, exchange=None):
        self.calls.append(("option_strikes", {
            "underlying": underlying, "expiry": expiry,
            "exchange": exchange}))
        return self._inner.option_strikes(
            underlying, expiry, exchange=exchange)

    def list_future_contracts(self, **kw):
        self.calls.append(("list_future_contracts", dict(kw)))
        return self._inner.list_future_contracts(**kw)


def _workspace(recording, store, symbol="RELIANCE"):
    svc = SubscriptionService(store, recording)
    import app.subscriptions.service as _svc_mod
    original = _svc_mod._today_iso
    _svc_mod._today_iso = lambda: "2026-09-01"  # deterministic clock
    try:
        return svc.workspace_contracts(symbol)
    finally:
        _svc_mod._today_iso = original


def test_workspace_uses_bulk_futures_not_per_expiry_search() -> None:
    du.clear_universe_cache()
    catalog, store, _tmp = _real_catalog()
    rec = _Recording(catalog)
    out = _workspace(rec, store)

    assert out["futures"], "workspace must still resolve futures"
    # Futures come from the canonical bulk read...
    assert any(name == "list_future_contracts" for name, _ in rec.calls)
    # ...and NEVER from a per-expiry/per-underlying futures search.
    assert not [kw for name, kw in rec.calls
                if name == "search" and kw.get("instrument_type") == "FUTURE"]
    # Exactly one search: the equity identity lookup.
    assert [kw for name, kw in rec.calls if name == "search"] == [
        {"q": "RELIANCE", "provider": "upstox",
         "instrument_type": "EQUITY", "exchange": "NSE", "limit": 5}]


def test_workspace_hot_lookups_pass_exchange() -> None:
    du.clear_universe_cache()
    catalog, store, _tmp = _real_catalog()
    rec = _Recording(catalog)
    _workspace(rec, store)

    for name, kw in rec.calls:
        if name in ("search", "derivative_expiries", "option_strikes"):
            assert kw.get("exchange") == "NSE", (name, kw)
    # The bulk futures read is exchange-scoped too.
    fut = [kw for name, kw in rec.calls if name == "list_future_contracts"]
    assert fut and all(kw.get("exchange") == "NSE" for kw in fut)


def test_workspace_avoids_duplicate_expiry_reads() -> None:
    """One option-expiry catalog read serves the whole request (memoized)."""
    du.clear_universe_cache()
    catalog, store, _tmp = _real_catalog()
    rec = _Recording(catalog)
    _workspace(rec, store)
    # The workspace asks for option expiries several times; only the first ask
    # reaches the catalog (pre-fix it was one catalog read per ask).
    n = len([1 for name, _ in rec.calls if name == "derivative_expiries"])
    assert n == 1, f"option expiries must be memoized, got {n} catalog reads"
    # A second identical request is served entirely from the cache.
    before = len(rec.calls)
    _workspace(rec, store)
    again = len([1 for name, _ in rec.calls[before:]
                 if name == "derivative_expiries"])
    assert again == 0, "second request must not re-read option expiries"


# -- the generated SQL actually uses idx_instr_lookup -------------------------

class _TracingConn:
    """sqlite3 connection proxy that captures (sql, args) for SELECTs."""

    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "queries", [])

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def execute(self, sql, args=()):
        conn = object.__getattribute__(self, "_conn")
        if sql.lstrip().lower().startswith("select"):
            object.__getattribute__(self, "queries").append((sql, args))
        return conn.execute(sql, args)


def _plan_for(fn, db):
    conn = sqlite3.connect(db)
    traced = _TracingConn(conn)
    try:
        fn(traced)
    finally:
        pass
    sql, args = traced.queries[-1]
    plan = [r[-1] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, args)]
    conn.close()
    return plan


def test_hot_lookups_use_idx_instr_fo() -> None:
    """The F&O access patterns are served by the purpose-built covering index.

    ``idx_instr_fo(exchange, instrument_type, underlying, expiry)`` is the
    intentionally designed index for these call shapes: ``underlying`` precedes
    ``expiry`` (unlike the older ``idx_instr_lookup``), which is what lets
    ``derivative_expiries`` seek instead of scanning the whole option range.
    """
    catalog, _store, _tmp = _real_catalog()
    db = catalog._store._db_path

    cases = {
        "derivative_expiries": lambda c: _products.derivative_expiries(
            c, underlying="RELIANCE", instrument_type="FUTURE",
            exchange="NSE"),
        "option_strikes": lambda c: _products.option_strikes(
            c, underlying="RELIANCE", expiry="2027-01-28", exchange="NSE"),
        "list_future_contracts": lambda c: _products.list_future_contracts(
            c, exchange="NSE", limit=20000),
        "search_instruments": lambda c: _products.search_instruments(
            c, q="RELIANCE", provider="upstox", instrument_type="EQUITY",
            exchange="NSE", limit=5),
    }
    for label, fn in cases.items():
        plan = _plan_for(fn, db)
        joined = " | ".join(plan)
        assert "idx_instr_fo" in joined, f"{label}: {joined}"
        # A full scan of the catalog is exactly the regression we removed.
        assert "SCAN instruments" not in joined, f"{label}: {joined}"


def test_equity_universe_uses_idx_instr_seg() -> None:
    """The NSE_EQ universe read is served by its own covering index."""
    catalog, _store, _tmp = _real_catalog()
    db = catalog._store._db_path
    plan = _plan_for(
        lambda c: _products.equity_universe(c, limit=20000), db)
    joined = " | ".join(plan)
    assert "idx_instr_seg" in joined, joined
    assert "SCAN instruments" not in joined, joined


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
