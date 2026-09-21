#!/usr/bin/env python3
"""Canonical instrument catalog tests (Issue 1).

The catalog is the canonical boundary: provider-native values are normalized
ONCE at ingestion so no read model needs provider-specific SQL or branching.

Covers:
  * segment canonicalization for every provider (Fyers raw 10/11 -> NSE_*)
  * canonical cash symbols (Fyers ``NSE:X-EQ`` -> ``X``)
  * canonical ``underlying`` on cash rows (both providers)
  * ``provider_symbol`` preserved provider-native
  * INDEX tradingsymbol preserved (provider-identity lookup)
  * Fyers equity <-> future linkage through the canonical ``underlying``
  * v21 -> v22 migration idempotency
"""

from __future__ import annotations

import json
import os
import sqlite3
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
from core.persistence.modules import products as P  # noqa: E402
from core.persistence.store import EventStore  # noqa: E402

_FYERS_EXPIRY = "1790706600"  # epoch seconds -> 2026-09-29


def _upstox_rows() -> list[dict]:
    return [
        {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE",
         "segment": "NSE_EQ", "instrument_type": "EQ",
         "trading_symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES LTD",
         "underlying_symbol": None, "expiry": None, "strike_price": None,
         "lot_size": 1, "tick_size": 0.05},
        {"instrument_key": "NSE_INDEX|Nifty 50", "exchange": "NSE",
         "segment": "NSE_INDEX", "instrument_type": "INDEX",
         "trading_symbol": "Nifty 50", "name": "Nifty 50",
         "underlying_symbol": None, "expiry": None, "strike_price": None,
         "lot_size": 1, "tick_size": 0.05},
        {"instrument_key": "NSE_FO|125841", "exchange": "NSE",
         "segment": "NSE_FO", "instrument_type": "FUT",
         "trading_symbol": "RELIANCE FUT 29 SEP 26", "name": "RELIANCE",
         "underlying_symbol": "RELIANCE", "expiry": 1790706599000,
         "strike_price": None, "lot_size": 500, "tick_size": 10.0},
        {"instrument_key": "NSE_FO|42631", "exchange": "NSE",
         "segment": "NSE_FO", "instrument_type": "CE",
         "trading_symbol": "RELIANCE 1400 CE 29 SEP 26", "name": "RELIANCE",
         "underlying_symbol": "RELIANCE", "expiry": 1790706599000,
         "strike_price": 1400.0, "lot_size": 500, "tick_size": 0.05},
    ]


def _fyers_master() -> bytes:
    obj = {
        "NSE:BAJAJ-AUTO-EQ": {
            "fyToken": "101", "exchange": 10, "segment": 10, "exInstType": 0,
            "symTicker": "NSE:BAJAJ-AUTO-EQ", "underSym": "BAJAJ-AUTO",
            "symDetails": "BAJAJ AUTO LIMITED", "minLotSize": 1},
        "NSE:NIFTY50-INDEX": {
            "fyToken": "102", "exchange": 10, "segment": 10, "exInstType": 10,
            "symTicker": "NSE:NIFTY50-INDEX", "underSym": "NIFTY",
            "symDetails": "NIFTY 50"},
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


def _store(tmp: str) -> EventStore:
    return EventStore(os.path.join(tmp, "t.db"))


def _rows(store: EventStore, provider: str, **where) -> list[dict]:
    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM instruments WHERE provider = ?"
        args: list = [provider]
        for col, val in where.items():
            sql += f" AND {col} = ?"
            args.append(val)
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


# -- segment -----------------------------------------------------------------

def test_upstox_segment_is_canonical() -> None:
    recs = upstox_master_records(_upstox_rows())
    by_sym = {r["tradingsymbol"]: r for r in recs}
    assert by_sym["RELIANCE"]["segment"] == "NSE_EQ"
    assert by_sym["Nifty 50"]["segment"] == "NSE_INDEX"
    assert by_sym["RELIANCE FUT 29 SEP 26"]["segment"] == "NSE_FO"
    assert by_sym["RELIANCE 1400 CE 29 SEP 26"]["segment"] == "NSE_FO"


def test_fyers_raw_segment_becomes_canonical() -> None:
    recs = fyers_master_records(_fyers_master())
    by_sym = {r["tradingsymbol"]: r for r in recs}
    assert by_sym["BAJAJ-AUTO"]["segment"] == "NSE_EQ"
    assert by_sym["NSE:NIFTY50-INDEX"]["segment"] == "NSE_INDEX"
    assert by_sym["BAJAJ-AUTO26SEPFUT"]["segment"] == "NSE_FO"
    assert by_sym["BAJAJ-AUTO26SEP1400CE"]["segment"] == "NSE_FO"
    # never the raw master code
    assert all(r["segment"] not in ("10", "11") for r in recs)


# -- canonical symbol --------------------------------------------------------

def test_fyers_cash_symbol_is_canonical() -> None:
    recs = fyers_master_records(_fyers_master())
    eq = next(r for r in recs if r["instrument_type"] == "EQUITY")
    assert eq["tradingsymbol"] == "BAJAJ-AUTO"
    assert eq["underlying"] == "BAJAJ-AUTO"


def test_upstox_cash_symbol_unchanged_and_underlying_populated() -> None:
    recs = upstox_master_records(_upstox_rows())
    eq = next(r for r in recs if r["instrument_type"] == "EQUITY")
    assert eq["tradingsymbol"] == "RELIANCE"   # already canonical
    assert eq["underlying"] == "RELIANCE"      # newly populated


def test_provider_symbol_is_never_rewritten() -> None:
    up = upstox_master_records(_upstox_rows())
    assert {r["provider_symbol"] for r in up} == {
        "NSE_EQ|INE002A01018", "NSE_INDEX|Nifty 50",
        "NSE_FO|125841", "NSE_FO|42631",
    }
    fy = fyers_master_records(_fyers_master())
    assert {r["provider_symbol"] for r in fy} == {
        "NSE:BAJAJ-AUTO-EQ", "NSE:NIFTY50-INDEX",
        "NSE:BAJAJ-AUTO26SEPFUT", "NSE:BAJAJ-AUTO26SEP1400CE",
    }


def test_index_tradingsymbol_preserved_native() -> None:
    """Index spots are resolved by an EXACT provider-symbol lookup."""
    recs = fyers_master_records(_fyers_master())
    idx = next(r for r in recs if r["instrument_type"] == "INDEX")
    assert idx["tradingsymbol"] == "NSE:NIFTY50-INDEX"
    assert idx["segment"] == "NSE_INDEX"


def test_derivative_underlying_is_canonical() -> None:
    recs = fyers_master_records(_fyers_master())
    fut = next(r for r in recs if r["instrument_type"] == "FUTURE")
    opt = next(r for r in recs if r["instrument_type"] == "OPTION")
    assert fut["underlying"] == "BAJAJ-AUTO"
    assert opt["underlying"] == "BAJAJ-AUTO"
    assert fut["tradingsymbol"] == "BAJAJ-AUTO26SEPFUT"  # namespace stripped


# -- F&O linkage -------------------------------------------------------------

def test_fyers_fno_universe_linkage() -> None:
    """A Fyers cash row must join its own futures through the canonical link."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        store.replace_provider_instruments("fyers",
                                           fyers_master_records(_fyers_master()))
        catalog = InstrumentCatalog(store)
        rows = catalog.fno_universe(provider="fyers", today="2026-09-01",
                                    limit=100)
        assert [r["symbol"] for r in rows] == ["BAJAJ-AUTO"]


def test_upstox_fno_universe_still_works() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        store.replace_provider_instruments(
            "upstox", upstox_master_records(_upstox_rows()))
        catalog = InstrumentCatalog(store)
        rows = catalog.fno_universe(provider="upstox", today="2026-09-01",
                                    limit=100)
        assert [r["symbol"] for r in rows] == ["RELIANCE"]


# -- migration ---------------------------------------------------------------

def _native_rows() -> list[dict]:
    """Raw (pre-canonicalization) rows, as an older catalog would hold them."""
    return [
        {"provider": "fyers", "instrument_token": "101", "exchange": "NSE",
         "tradingsymbol": "NSE:BAJAJ-AUTO-EQ", "name": "BAJAJ AUTO",
         "instrument_type": "EQUITY", "segment": "10", "expiry": None,
         "strike": None, "option_type": None, "lot_size": 1, "tick_size": None,
         "isin": None, "underlying": "BAJAJ-AUTO",
         "provider_symbol": "NSE:BAJAJ-AUTO-EQ"},
        {"provider": "fyers", "instrument_token": "103", "exchange": "NSE",
         "tradingsymbol": "NSE:BAJAJ-AUTO26SEPFUT", "name": "FUT",
         "instrument_type": "FUTURE", "segment": "11", "expiry": "2026-09-29",
         "strike": None, "option_type": None, "lot_size": 75, "tick_size": None,
         "isin": None, "underlying": "BAJAJ-AUTO",
         "provider_symbol": "NSE:BAJAJ-AUTO26SEPFUT"},
        {"provider": "upstox", "instrument_token": "NSE_EQ|X", "exchange": "NSE",
         "tradingsymbol": "RELIANCE", "name": "RELIANCE",
         "instrument_type": "EQUITY", "segment": "NSE_EQ", "expiry": None,
         "strike": None, "option_type": None, "lot_size": 1, "tick_size": None,
         "isin": None, "underlying": None, "provider_symbol": "NSE_EQ|X"},
    ]


def test_v22_migration_canonicalizes_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        # Insert provider-native rows directly (bypassing ingestion).
        store.replace_provider_instruments("fyers", _native_rows())

        con = sqlite3.connect(store.db_path)
        try:
            P.migrate_v21_to_v22(con)
            after1 = {
                r[0]: tuple(r[1:]) for r in con.execute(
                    "SELECT instrument_token, segment, tradingsymbol, "
                    "underlying, provider_symbol FROM instruments")
            }
            # canonicalized
            assert after1["101"][0] == "NSE_EQ"
            assert after1["101"][1] == "BAJAJ-AUTO"
            assert after1["101"][2] == "BAJAJ-AUTO"
            assert after1["103"][0] == "NSE_FO"
            assert after1["103"][1] == "BAJAJ-AUTO26SEPFUT"
            assert after1["NSE_EQ|X"][2] == "RELIANCE"   # underlying populated
            # provider identity preserved
            assert after1["101"][3] == "NSE:BAJAJ-AUTO-EQ"
            assert after1["103"][3] == "NSE:BAJAJ-AUTO26SEPFUT"

            # idempotent: second run changes nothing
            P.migrate_v21_to_v22(con)
            after2 = {
                r[0]: tuple(r[1:]) for r in con.execute(
                    "SELECT instrument_token, segment, tradingsymbol, "
                    "underlying, provider_symbol FROM instruments")
            }
            assert after2 == after1
            assert con.execute("PRAGMA user_version").fetchone()[0] == 22
        finally:
            con.close()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
