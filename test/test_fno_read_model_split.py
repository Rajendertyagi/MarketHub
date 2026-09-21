#!/usr/bin/env python3
"""F&O read-model split + cache tests (Issue 2).

``fno_universe`` aggregates contract counts; callers that only need universe
membership use the cheaper enumeration model. Both must return the SAME
membership, and the enumeration must be cached at the canonical read-model
boundary (keyed by provider + day + catalog epoch).
"""

from __future__ import annotations

import collections
import json
import os
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.instruments import (  # noqa: E402
    InstrumentCatalog,
    fyers_master_records,
    upstox_master_records,
)
from core.persistence.store import EventStore  # noqa: E402
from market import derivatives_universe as du  # noqa: E402

_FYERS_EXPIRY = "1790706600"


def _upstox_rows() -> list[dict]:
    return [
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE",
         "segment": "NSE_EQ", "instrument_type": "EQ",
         "trading_symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES LTD",
         "underlying_symbol": None, "expiry": None, "strike_price": None,
         "lot_size": 1, "tick_size": 0.05},
        {"instrument_key": "NSE_FO|F1", "exchange": "NSE", "segment": "NSE_FO",
         "instrument_type": "FUT", "trading_symbol": "RELIANCE FUT 29 SEP 26",
         "name": "RELIANCE", "underlying_symbol": "RELIANCE",
         "expiry": 1790706599000, "strike_price": None, "lot_size": 500},
        {"instrument_key": "NSE_FO|C1", "exchange": "NSE", "segment": "NSE_FO",
         "instrument_type": "CE", "trading_symbol": "RELIANCE 1400 CE 29 SEP 26",
         "name": "RELIANCE", "underlying_symbol": "RELIANCE",
         "expiry": 1790706599000, "strike_price": 1400.0, "lot_size": 500},
    ]


def _fyers_master() -> bytes:
    obj = {
        "NSE:BAJAJ-AUTO-EQ": {
            "fyToken": "101", "exchange": 10, "segment": 10, "exInstType": 0,
            "symTicker": "NSE:BAJAJ-AUTO-EQ", "underSym": "BAJAJ-AUTO",
            "symDetails": "BAJAJ AUTO LIMITED", "minLotSize": 1},
        "NSE:BAJAJ-AUTO26SEPFUT": {
            "fyToken": "103", "exchange": 10, "segment": 11, "exInstType": 13,
            "symTicker": "NSE:BAJAJ-AUTO26SEPFUT", "underSym": "BAJAJ-AUTO",
            "expiryDate": _FYERS_EXPIRY, "minLotSize": 75},
        "NSE:BAJAJ-AUTO26SEP1400CE": {
            "fyToken": "104", "exchange": 10, "segment": 11, "exInstType": 15,
            "symTicker": "NSE:BAJAJ-AUTO26SEP1400CE", "underSym": "BAJAJ-AUTO",
            "expiryDate": _FYERS_EXPIRY, "strikePrice": 1400.0, "optType": "CE",
            "minLotSize": 75},
    }
    return json.dumps(obj).encode()


def _catalog(tmp: str, *, upstox=True, fyers=True):
    store = EventStore(os.path.join(tmp, "t.db"))
    if upstox:
        store.replace_provider_instruments("upstox",
                                           upstox_master_records(_upstox_rows()))
    if fyers:
        store.replace_provider_instruments("fyers",
                                           fyers_master_records(_fyers_master()))
    return InstrumentCatalog(store)


# -- membership parity -------------------------------------------------------

def test_enumeration_matches_count_model_membership() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        for prov in ("upstox", "fyers"):
            enum = catalog.fno_underlying_rows(provider=prov,
                                               today="2026-09-01", limit=2000)
            counted = catalog.fno_universe(provider=prov, today="2026-09-01",
                                           limit=2000)
            assert [r["symbol"] for r in enum] == [r["symbol"] for r in counted]


def test_enumeration_returns_no_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        rows = catalog.fno_underlying_rows(provider="fyers",
                                           today="2026-09-01", limit=2000)
        assert rows
        for row in rows:
            assert "symbol" in row and "equity_key" in row
            for absent in ("futures_count", "options_count", "future_expiries",
                           "option_expiries"):
                assert absent not in row


def test_counts_model_still_returns_counts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        rows = catalog.fno_universe(provider="upstox", today="2026-09-01",
                                    limit=2000)
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "RELIANCE"
        assert row["futures_count"] == 1
        assert row["options_count"] == 1
        assert row["future_expiries"] == 1
        assert row["option_expiries"] == 1


def test_fyers_enumeration_is_non_zero() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp, upstox=False)
        rows = catalog.fno_underlying_rows(provider="fyers",
                                           today="2026-09-01", limit=2000)
        assert [r["symbol"] for r in rows] == ["BAJAJ-AUTO"]


def test_provider_isolation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog = _catalog(tmp)
        up = catalog.fno_underlying_rows(provider="upstox", today="2026-09-01",
                                         limit=2000)
        fy = catalog.fno_underlying_rows(provider="fyers", today="2026-09-01",
                                         limit=2000)
        assert [r["symbol"] for r in up] == ["RELIANCE"]
        assert [r["symbol"] for r in fy] == ["BAJAJ-AUTO"]


# -- cache -------------------------------------------------------------------

class _CountingCatalog:
    """Counts enumeration reads; returns data that can be swapped."""

    def __init__(self, symbols, epoch=1):
        self.symbols = list(symbols)
        self.epoch = epoch
        self.calls = collections.Counter()

    def catalog_epoch(self):
        return self.epoch

    def fno_underlying_rows(self, *, provider, today, limit):
        self.calls[provider] += 1
        return [{"symbol": s, "name": s, "equity_key": "K-" + s}
                for s in self.symbols]


def test_enumeration_is_cached_per_provider_and_epoch() -> None:
    du.clear_universe_cache()
    cat = _CountingCatalog(["RELIANCE"])

    a = du.fno_underlying_rows(cat, provider="upstox", today="2026-09-01")
    b = du.fno_underlying_rows(cat, provider="upstox", today="2026-09-01")
    assert cat.calls["upstox"] == 1          # cached
    assert a is b

    du.fno_underlying_rows(cat, provider="fyers", today="2026-09-01")
    assert cat.calls["fyers"] == 1           # separate cache entry
    assert cat.calls["upstox"] == 1

    # new catalog epoch -> fresh snapshot
    cat.epoch = 2
    cat.symbols = ["RELIANCE", "INFY"]
    rows = du.fno_underlying_rows(cat, provider="upstox", today="2026-09-01")
    assert cat.calls["upstox"] == 2
    assert [r["symbol"] for r in rows] == ["RELIANCE", "INFY"]


def test_enumeration_cache_not_stale_after_catalog_change() -> None:
    du.clear_universe_cache()
    cat = _CountingCatalog(["RELIANCE"], epoch=10)
    first = du.fno_underlying_rows(cat, provider="upstox", today="2026-09-01")
    assert [r["symbol"] for r in first] == ["RELIANCE"]

    # catalog replaced (new epoch) and membership changed
    cat.epoch = 11
    cat.symbols = ["INFY"]
    second = du.fno_underlying_rows(cat, provider="upstox", today="2026-09-01")
    assert [r["symbol"] for r in second] == ["INFY"]


def test_stock_underlyings_uses_the_cached_enumeration() -> None:
    du.clear_universe_cache()
    cat = _CountingCatalog(["RELIANCE", "INFY"])
    us = du.stock_underlyings(cat, provider="upstox", today="2026-09-01")
    assert [u.symbol for u in us] == ["RELIANCE", "INFY"]
    du.stock_underlyings(cat, provider="upstox", today="2026-09-01")
    assert cat.calls["upstox"] == 1          # one catalog read, reused


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
