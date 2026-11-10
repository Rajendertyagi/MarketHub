#!/usr/bin/env python3
"""Dynamic F&O universe + neutral index registry tests (Phase 3).

Verifies:
  * the neutral registry (market.index_registry) is the single source of the
    canonical six F&O indices; app.market_indices re-exports it (same object)
  * the broader indices (INDIA VIX/SENSEX/BANKEX) are NOT F&O underlyings
  * fno_underlyings = six indices + catalog stock underlyings (deduped)
  * expiries/futures/options are catalog-derived
  * the bulk future-contract query works in one provider-neutral pass
  * market/ imports neither app nor brokers (broker-neutral boundary)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.instruments import (
    InstrumentCatalog,
    upstox_master_records,
)
from core.persistence.store import EventStore

# Sanitized Upstox master rows: one NSE equity, one stock future, one index
# future, one index option. Expiries are epoch-ms (end of day).
_ROWS = [
    {"instrument_key": "NSE_EQ|INE002A01018", "exchange": "NSE",
     "segment": "NSE_EQ", "instrument_type": "EQ",
     "trading_symbol": "RELIANCE", "name": "RELIANCE INDUSTRIES LTD",
     "underlying_symbol": None, "expiry": None, "strike_price": None,
     "lot_size": 1, "tick_size": 0.05},
    {"instrument_key": "NSE_FO|125841", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "FUT",
     "trading_symbol": "RELIANCE FUT 29 SEP 26", "name": "RELIANCE",
     "underlying_symbol": "RELIANCE", "expiry": 1798588799000,
     "strike_price": None, "lot_size": 500, "tick_size": 10.0},
    {"instrument_key": "NSE_FO|68407", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "FUT",
     "trading_symbol": "NIFTY FUT 29 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1798588799000,
     "strike_price": None, "lot_size": 65, "tick_size": 10.0},
    {"instrument_key": "NSE_FO|42631", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "CE",
     "trading_symbol": "NIFTY 23800 CE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1796774399000,
     "strike_price": 23800.0, "lot_size": 65, "tick_size": 0.05},
]


def _catalog():
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    store.replace_provider_instruments("upstox", upstox_master_records(_ROWS))
    return InstrumentCatalog(store), store, tmp


# -- neutral registry --------------------------------------------------------

def test_registry_is_single_source() -> None:
    import app.market_indices as mi
    from market import index_registry as reg
    assert mi.FNO_INDEX_LABELS is reg.FNO_INDEX_LABELS
    assert reg.FNO_INDEX_LABELS == (
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
        "SENSEX",
    )
    # MAJOR_INDICES (provider map) must cover the canonical index labels.
    assert [e["label"] for e in mi.MAJOR_INDICES] == list(reg.INDEX_LABELS)


def test_broader_indices_are_not_fno() -> None:
    from market.index_registry import is_fno_index
    for label in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                  "NIFTYNXT50", "SENSEX"):
        assert is_fno_index(label) is True
    for label in ("INDIA VIX", "NIFTYFPI", "BANKEX"):
        assert is_fno_index(label) is False


# -- dynamic universe --------------------------------------------------------

def test_index_underlyings_are_the_canonical_fno_set() -> None:
    from market.derivatives_universe import KIND_INDEX, index_underlyings
    us = index_underlyings()
    assert [u.symbol for u in us] == [
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
        "SENSEX",
    ]
    assert all(u.kind == KIND_INDEX for u in us)


def test_fno_underlyings_index_plus_catalog_stocks() -> None:
    from market.derivatives_universe import (
        KIND_EQUITY,
        KIND_INDEX,
        fno_underlyings,
    )
    catalog, _store, _tmp = _catalog()
    us = fno_underlyings(catalog, provider="upstox")
    symbols = [u.symbol for u in us]
    # supported F&O indices first, then the catalog stock underlying
    assert symbols[:6] == [
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
        "SENSEX",
    ]
    assert "RELIANCE" in symbols
    # NIFTY appears once (index), not duplicated by a stock row
    assert symbols.count("NIFTY") == 1
    kinds = {u.symbol: u.kind for u in us}
    assert kinds["NIFTY"] == KIND_INDEX
    assert kinds["RELIANCE"] == KIND_EQUITY


def test_expiries_futures_options_are_catalog_derived() -> None:
    from market.derivatives_universe import (
        expiries_for,
        futures_for,
        options_for,
    )
    catalog, _store, _tmp = _catalog()
    assert expiries_for(catalog, "NIFTY", "FUTURE") == ["2026-12-29"]
    futures = futures_for(catalog, "NIFTY", "2026-12-29")
    assert len(futures) == 1
    assert futures[0].instrument_type == "FUTURE"
    assert futures[0].provider == "upstox"
    options = options_for(catalog, "NIFTY", "2026-12-08")
    assert len(options) == 1
    assert options[0].instrument_type == "OPTION"
    assert options[0].option_type == "CE"
    assert options[0].strike == 23800.0


def test_bulk_future_contracts_one_pass() -> None:
    catalog, _store, _tmp = _catalog()
    rows = catalog.list_future_contracts(provider="upstox")
    assert len(rows) == 2
    assert {r["underlying"] for r in rows} == {"NIFTY", "RELIANCE"}
    assert all(r["instrument_type"] == "FUTURE" for r in rows)
    # Optional filters do not require per-underlying queries.
    only_nifty = catalog.list_future_contracts(
        provider="upstox", underlying="NIFTY")
    assert [r["underlying"] for r in only_nifty] == ["NIFTY"]
    by_expiry = catalog.list_future_contracts(
        provider="upstox", expiries=["2026-12-29"])
    assert len(by_expiry) == 2


def test_market_layer_is_broker_and_app_free() -> None:
    """market/index_registry + derivatives_universe import no app/brokers."""
    code = (
        "import sys\n"
        "import market.index_registry\n"
        "import market.derivatives_universe\n"
        "bad = [m for m in sys.modules "
        "if m.startswith('app') or m.startswith('brokers')]\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=_PROJECT_DIR,
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
