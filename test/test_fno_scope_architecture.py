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
        # Supported discovery excludes out-of-scope BSE_FO but keeps SENSEX.
        bse = store.search_instruments(provider="upstox", exchange="BSE")
        underlyings = {r.get("underlying") for r in bse if r.get("segment") == "BSE_FO"}
        assert "SENSEX" in underlyings
        assert "BANKEX" not in underlyings
        # Pre-existing out-of-scope row preserved in storage (diagnostic/raw),
        # but not exposed as a supported product.
        assert store.get_instrument("upstox", "BSE_FO|OLD-BANKEX") is not None
        assert all(r.get("instrument_token") != "BSE_FO|OLD-BANKEX" for r in bse)
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


def test_upstox_sync_atomic_rollback_across_scoped_and_other_segments():
    tmp, store, catalog = _catalog()
    try:
        store.replace_provider_instruments("upstox", [
            {
                "provider": "upstox", "instrument_token": "BSE_FO|OLD-BANKEX",
                "provider_symbol": "BSE_FO|OLD-BANKEX", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "BANKEX OLD FUT",
                "name": "BANKEX", "instrument_type": "FUTURE",
                "underlying": "BANKEX", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|OLD-SENSEX",
                "provider_symbol": "BSE_FO|OLD-SENSEX", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX OLD FUT",
                "name": "SENSEX", "instrument_type": "FUTURE",
                "underlying": "SENSEX", "expiry": "2026-11-24",
            },
            {
                "provider": "upstox", "instrument_token": "NSE_EQ|OLD",
                "provider_symbol": "NSE_EQ|OLD", "exchange": "NSE",
                "segment": "NSE_EQ", "tradingsymbol": "OLD",
                "name": "OLD", "instrument_type": "EQUITY",
                "underlying": "OLD",
            },
        ])
        before = {
            r["instrument_token"]: r
            for r in store.search_instruments(provider="upstox", limit=100)
        }
        assert store.get_instrument("upstox", "BSE_FO|OLD-BANKEX") is not None

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
            {"instrument_key": "NSE_EQ|NEW", "exchange": "NSE", "segment": "NSE_EQ",
             "instrument_type": "EQ", "trading_symbol": "NEW", "name": "NEW",
             "underlying_symbol": "NEW", "expiry": None, "strike_price": None,
             "lot_size": 1, "tick_size": 0.05, "isin": "INE999"},
        ]

        import core.persistence.modules.products as products

        orig = products._apply_segmented_replace
        calls = {"n": 0}

        def boom(*args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("boom-after-scoped")

        products._apply_segmented_replace = boom  # type: ignore[assignment]
        try:
            result = catalog.sync_upstox(
                fetch=lambda _url: __import__("json").dumps(rows).encode("utf-8"),
                segments={"BSE_FO", "NSE_EQ"},
            )
        finally:
            products._apply_segmented_replace = orig  # type: ignore[assignment]

        assert result["ok"] is False
        assert result.get("aborted") is True
        assert calls["n"] >= 1
        after = {
            r["instrument_token"]: r
            for r in store.search_instruments(provider="upstox", limit=100)
        }
        assert set(after.keys()) == set(before.keys())
        assert store.get_instrument("upstox", "BSE_FO|OLD-BANKEX") is not None
        assert store.get_instrument("upstox", "BSE_FO|OLD-SENSEX") is not None
        assert store.get_instrument("upstox", "NSE_EQ|OLD") is not None
        assert store.get_instrument("upstox", "BSE_FO|S1") is None
        assert store.get_instrument("upstox", "NSE_EQ|NEW") is None
    finally:
        tmp.cleanup()


def test_out_of_scope_bse_derivatives_excluded_from_supported_discovery():
    tmp, store, catalog = _catalog()
    try:
        store.replace_provider_instruments("upstox", [
            {
                "provider": "upstox", "instrument_token": "BSE_FO|S-FUT",
                "provider_symbol": "BSE_FO|S-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX FUT 29 DEC 26",
                "name": "SENSEX", "instrument_type": "FUTURE",
                "underlying": "SENSEX", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|S-CE",
                "provider_symbol": "BSE_FO|S-CE", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX 80000 CE 29 DEC 26",
                "name": "SENSEX", "instrument_type": "OPTION",
                "underlying": "SENSEX", "expiry": "2026-12-29",
                "strike": 80000.0, "option_type": "CE",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|S-PE",
                "provider_symbol": "BSE_FO|S-PE", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX 80000 PE 29 DEC 26",
                "name": "SENSEX", "instrument_type": "OPTION",
                "underlying": "SENSEX", "expiry": "2026-12-29",
                "strike": 80000.0, "option_type": "PE",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|BANKEX-FUT",
                "provider_symbol": "BSE_FO|BANKEX-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "BANKEX FUT 29 DEC 26",
                "name": "BANKEX", "instrument_type": "FUTURE",
                "underlying": "BANKEX", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|SENSEX50-FUT",
                "provider_symbol": "BSE_FO|SENSEX50-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX50 FUT 29 DEC 26",
                "name": "SENSEX50", "instrument_type": "FUTURE",
                "underlying": "SENSEX50", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|STOCK-FUT",
                "provider_symbol": "BSE_FO|STOCK-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "RELIANCE FUT 29 DEC 26",
                "name": "RELIANCE", "instrument_type": "FUTURE",
                "underlying": "RELIANCE", "expiry": "2026-12-29",
            },
        ])

        assert store.get_instrument("upstox", "BSE_FO|BANKEX-FUT") is not None
        assert store.get_instrument("upstox", "BSE_FO|SENSEX50-FUT") is not None
        assert store.get_instrument("upstox", "BSE_FO|STOCK-FUT") is not None

        search = store.search_instruments(provider="upstox", exchange="BSE", limit=100)
        tokens = {r["instrument_token"] for r in search}
        assert "BSE_FO|S-FUT" in tokens
        assert "BSE_FO|S-CE" in tokens
        assert "BSE_FO|S-PE" in tokens
        assert "BSE_FO|BANKEX-FUT" not in tokens
        assert "BSE_FO|SENSEX50-FUT" not in tokens
        assert "BSE_FO|STOCK-FUT" not in tokens

        assert store.derivative_expiries("SENSEX", "FUTURE", exchange="BSE") == ["2026-12-29"]
        assert store.derivative_expiries("SENSEX", "OPTION", exchange="BSE") == ["2026-12-29"]
        assert store.derivative_expiries("BANKEX", "FUTURE", exchange="BSE") == []
        assert store.derivative_expiries("SENSEX50", "FUTURE", exchange="BSE") == []

        strikes = store.option_strikes("SENSEX", "2026-12-29", exchange="BSE")
        assert {r["instrument_token"] for r in strikes} == {"BSE_FO|S-CE", "BSE_FO|S-PE"}
        assert store.option_strikes("BANKEX", "2026-12-29", exchange="BSE") == []

        futs = store.list_future_contracts(exchange="BSE")
        assert {r["instrument_token"] for r in futs} == {"BSE_FO|S-FUT"}
        futs_sensex = store.list_future_contracts(underlying="SENSEX", exchange="BSE")
        assert len(futs_sensex) == 1
        assert store.list_future_contracts(underlying="BANKEX", exchange="BSE") == []
    finally:
        tmp.cleanup()


def test_option_underlyings_and_expiries_exclude_out_of_scope_bse_derivatives():
    tmp, store, catalog = _catalog()
    try:
        store.replace_provider_instruments("upstox", [
            # Supported NSE F&O product
            {
                "provider": "upstox", "instrument_token": "NSE_FO|NIFTY-FUT",
                "provider_symbol": "NSE_FO|NIFTY-FUT", "exchange": "NSE",
                "segment": "NSE_FO", "tradingsymbol": "NIFTY FUT 29 DEC 26",
                "name": "NIFTY", "instrument_type": "FUTURE",
                "underlying": "NIFTY", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "NSE_FO|NIFTY-CE",
                "provider_symbol": "NSE_FO|NIFTY-CE", "exchange": "NSE",
                "segment": "NSE_FO", "tradingsymbol": "NIFTY 24000 CE 29 DEC 26",
                "name": "NIFTY", "instrument_type": "OPTION",
                "underlying": "NIFTY", "expiry": "2026-12-29",
                "strike": 24000.0, "option_type": "CE",
            },
            # Supported SENSEX BSE_FO
            {
                "provider": "upstox", "instrument_token": "BSE_FO|S-FUT",
                "provider_symbol": "BSE_FO|S-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX FUT 29 DEC 26",
                "name": "SENSEX", "instrument_type": "FUTURE",
                "underlying": "SENSEX", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|S-CE",
                "provider_symbol": "BSE_FO|S-CE", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX 80000 CE 29 DEC 26",
                "name": "SENSEX", "instrument_type": "OPTION",
                "underlying": "SENSEX", "expiry": "2026-12-29",
                "strike": 80000.0, "option_type": "CE",
            },
            # Out-of-scope BSE derivatives (stored for diagnostics/preservation)
            {
                "provider": "upstox", "instrument_token": "BSE_FO|BANKEX-FUT",
                "provider_symbol": "BSE_FO|BANKEX-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "BANKEX FUT 29 DEC 26",
                "name": "BANKEX", "instrument_type": "FUTURE",
                "underlying": "BANKEX", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|BANKEX-CE",
                "provider_symbol": "BSE_FO|BANKEX-CE", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "BANKEX 50000 CE 29 DEC 26",
                "name": "BANKEX", "instrument_type": "OPTION",
                "underlying": "BANKEX", "expiry": "2026-12-29",
                "strike": 50000.0, "option_type": "CE",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|SENSEX50-FUT",
                "provider_symbol": "BSE_FO|SENSEX50-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "SENSEX50 FUT 29 DEC 26",
                "name": "SENSEX50", "instrument_type": "FUTURE",
                "underlying": "SENSEX50", "expiry": "2026-12-29",
            },
            {
                "provider": "upstox", "instrument_token": "BSE_FO|RELIANCE-FUT",
                "provider_symbol": "BSE_FO|RELIANCE-FUT", "exchange": "BSE",
                "segment": "BSE_FO", "tradingsymbol": "RELIANCE FUT 29 DEC 26",
                "name": "RELIANCE", "instrument_type": "FUTURE",
                "underlying": "RELIANCE", "expiry": "2026-12-29",
            },
        ])

        # Raw diagnostic lookup remains unchanged.
        for token in ("BSE_FO|BANKEX-FUT", "BSE_FO|BANKEX-CE",
                      "BSE_FO|SENSEX50-FUT", "BSE_FO|RELIANCE-FUT"):
            assert store.get_instrument("upstox", token) is not None

        underlyings = {r["underlying"] for r in store.option_underlyings(limit=100)}
        assert "NIFTY" in underlyings
        assert "SENSEX" in underlyings
        assert "BANKEX" not in underlyings
        assert "SENSEX50" not in underlyings
        assert "RELIANCE" not in underlyings

        assert store.option_expiries("NIFTY") == ["2026-12-29"]
        assert store.option_expiries("SENSEX") == ["2026-12-29"]
        assert store.option_expiries("BANKEX") == []
        assert store.option_expiries("SENSEX50") == []
        assert store.option_expiries("RELIANCE") == []
    finally:
        tmp.cleanup()
