#!/usr/bin/env python3
"""Focused tests for approved F&O scope and provider-neutral architecture."""

from __future__ import annotations

import os
import sys
import tempfile
import threading

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.instruments import InstrumentCatalog, upstox_master_records
from core.persistence.store import EventStore
from market.derivatives_universe import clear_universe_cache, futures_index
from market.market_universe import resolve_universe

TODAY = "2026-10-16"


def _upstox_eq(key, sym, name):
    return {"instrument_key": key, "exchange": "NSE", "segment": "NSE_EQ",
            "instrument_type": "EQ", "trading_symbol": sym, "name": name,
            "underlying_symbol": sym, "expiry": None, "strike_price": None,
            "lot_size": 1, "tick_size": 0.05, "isin": key.split("|")[-1]}


def _upstox_fo(key, itype, und, expiry_ms, strike=None):
    return {"instrument_key": key, "exchange": "NSE", "segment": "NSE_FO",
            "instrument_type": itype, "trading_symbol": f"{und}-{itype}-{key}",
            "name": und, "underlying_symbol": und, "expiry": expiry_ms,
            "strike_price": strike, "lot_size": 1, "tick_size": 0.05}


def _fyers_eq(token, sym, name):
    return {
        "provider": "fyers", "instrument_token": token,
        "provider_symbol": f"NSE:{sym}-EQ", "exchange": "NSE",
        "segment": "NSE_EQ", "tradingsymbol": sym, "name": name,
        "instrument_type": "EQUITY", "underlying": sym,
    }


def _fyers_fo(token, itype, und, expiry, strike=None, opt=None):
    return {
        "provider": "fyers", "instrument_token": token,
        "provider_symbol": f"NSE:{und}{expiry}{itype}{token}",
        "exchange": "NSE", "segment": "NSE_FO",
        "tradingsymbol": f"{und}{expiry}{itype}{token}", "name": und,
        "instrument_type": itype, "underlying": und, "expiry": expiry,
        "strike": strike, "option_type": opt,
    }


def _catalog():
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    return tmp, store, InstrumentCatalog(store)


def test_provider_neutral_fno_membership_union_and_dedupe():
    tmp, store, catalog = _catalog()
    try:
        ms = 86400000
        base = 1796774399000  # 2026-12-08
        up_rows = upstox_master_records([
            _upstox_eq("NSE_EQ|INE001", "UPONLY", "Upstox Only"),
            _upstox_fo("NSE_FO|U1", "FUT", "UPONLY", base + 21 * ms),
            _upstox_eq("NSE_EQ|INE002", "BOTH", "Both Co"),
            _upstox_fo("NSE_FO|U2", "FUT", "BOTH", base + 21 * ms),
        ])
        store.replace_provider_instruments("upstox", up_rows)
        fy_rows = [
            _fyers_eq("FY-EQ-1", "FYONLY", "Fyers Only"),
            _fyers_fo("FY-F1", "FUTURE", "FYONLY", "2026-12-29"),
            _fyers_eq("FY-EQ-2", "BOTH", "Both Co FY"),
            _fyers_fo("FY-F2", "FUTURE", "BOTH", "2026-12-29"),
        ]
        store.replace_provider_instruments("fyers", fy_rows)

        uni = catalog.fno_universe(provider=None, today=TODAY)
        symbols = [r["symbol"] for r in uni]
        assert "UPONLY" in symbols
        assert "FYONLY" in symbols
        assert symbols.count("BOTH") == 1

        members = resolve_universe("FNO", catalog)
        msymbols = [m.symbol for m in members]
        assert "UPONLY" in msymbols
        assert "FYONLY" in msymbols
        assert msymbols.count("BOTH") == 1
        both = next(m for m in members if m.symbol == "BOTH")
        # Prefer Upstox representative equity key when available.
        assert both.instrument_token == "NSE_EQ|INE002"
    finally:
        tmp.cleanup()


def test_upstox_bse_fo_sync_is_sensex_only():
    tmp, store, catalog = _catalog()
    try:
        # Seed an out-of-scope BSE_FO row to ensure scoped replacement preserves it.
        store.replace_provider_instruments("upstox", [{
            "provider": "upstox", "instrument_token": "BSE_FO|OLD-BANKEX",
            "provider_symbol": "BSE_FO|OLD-BANKEX", "exchange": "BSE",
            "segment": "BSE_FO", "tradingsymbol": "BANKEX OLD FUT",
            "name": "BANKEX", "instrument_type": "FUTURE",
            "underlying": "BANKEX", "expiry": "2026-12-29",
        }])

        ms = 86400000
        base = 1796774399000  # 2026-12-08
        rows = [
            {"instrument_key": "BSE_FO|S1", "exchange": "BSE", "segment": "BSE_FO",
             "instrument_type": "FUT", "trading_symbol": "SENSEX FUT 29 DEC 26",
             "name": "SENSEX", "underlying_symbol": "SENSEX", "expiry": base + 21 * ms,
             "strike_price": None, "lot_size": 20, "tick_size": 1.0},
            {"instrument_key": "BSE_FO|S2", "exchange": "BSE", "segment": "BSE_FO",
             "instrument_type": "CE", "trading_symbol": "SENSEX 80000 CE 29 DEC 26",
             "name": "SENSEX", "underlying_symbol": "SENSEX", "expiry": base + 21 * ms,
             "strike_price": 80000.0, "lot_size": 20, "tick_size": 1.0},
            {"instrument_key": "BSE_FO|S3", "exchange": "BSE", "segment": "BSE_FO",
             "instrument_type": "PE", "trading_symbol": "SENSEX 80000 PE 29 DEC 26",
             "name": "SENSEX", "underlying_symbol": "SENSEX", "expiry": base + 21 * ms,
             "strike_price": 80000.0, "lot_size": 20, "tick_size": 1.0},
            {"instrument_key": "BSE_FO|B1", "exchange": "BSE", "segment": "BSE_FO",
             "instrument_type": "FUT", "trading_symbol": "BANKEX FUT 29 DEC 26",
             "name": "BANKEX", "underlying_symbol": "BANKEX", "expiry": base + 21 * ms,
             "strike_price": None, "lot_size": 15, "tick_size": 1.0},
        ]
        # sync_upstox expects raw bytes/list parseable by upstox_master_records; pass list via fetch shim
        result = catalog.sync_upstox(
            fetch=lambda _url: __import__("json").dumps(rows).encode("utf-8"),
            segments={"BSE_FO"},
        )
        assert result["ok"] is True
        bse = store.search_instruments(provider="upstox", exchange="BSE")
        underlyings = {r.get("underlying") for r in bse if r.get("segment") == "BSE_FO"}
        assert "SENSEX" in underlyings
        assert "BANKEX" not in {r.get("underlying") for r in bse
                                if r.get("segment") == "BSE_FO"
                                and r.get("instrument_token") != "BSE_FO|OLD-BANKEX"}
        # Pre-existing out-of-scope row preserved by scoped Sensex replacement.
        assert any(r.get("instrument_token") == "BSE_FO|OLD-BANKEX" for r in bse)
    finally:
        tmp.cleanup()


def test_futures_index_complete_no_truncation_and_cached():
    tmp, store, catalog = _catalog()
    try:
        clear_universe_cache()
        rows = []
        for i in range(20050):
            rows.append({
                "provider": "upstox", "instrument_token": f"NSE_FO|F{i}",
                "provider_symbol": f"NSE_FO|F{i}", "exchange": "NSE",
                "segment": "NSE_FO", "tradingsymbol": f"SYM{i} FUT",
                "name": f"SYM{i}", "instrument_type": "FUTURE",
                "underlying": f"SYM{i % 250}", "expiry": "2026-12-29",
            })
        store.replace_provider_instruments("upstox", rows)

        calls = {"n": 0}
        orig = catalog.list_future_contracts

        def counted(**kw):
            calls["n"] += 1
            return orig(**kw)

        catalog.list_future_contracts = counted  # type: ignore[assignment]
        idx = futures_index(catalog, exchange="NSE")
        total = sum(len(v) for v in idx.values())
        assert total == 20050
        assert calls["n"] == 1

        # Single-flight: concurrent callers should not trigger redundant builds.
        calls["n"] = 0
        clear_universe_cache()
        results = []
        barrier = threading.Barrier(5)

        def worker():
            barrier.wait()
            results.append(sum(len(v) for v in futures_index(catalog, exchange="NSE").values()))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r == 20050 for r in results)
        assert calls["n"] == 1
    finally:
        tmp.cleanup()
