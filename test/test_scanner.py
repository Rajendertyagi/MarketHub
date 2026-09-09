"""Unit tests for the generic scanner engine (Phase C / D)."""

import sys
import types
from datetime import datetime, timezone

import pytest

import market.scanner as scanner_mod
from market.scanner import ScannerEngine, _status_of
from market.models import Quote


class FakeCatalog:
    def __init__(self, symbols):
        self.symbols = symbols

    def is_index(self, idx):
        return idx == "NIFTY50"

    def list_index_constituents(self, idx):
        return list(self.symbols)

    def get_instrument(self, exchange, symbol):
        return types.SimpleNamespace(
            exchange=exchange, tradingsymbol=symbol,
            instrument_token=f"{exchange}|INE{abs(hash(symbol)) % 10**8:08d}",
            name=symbol, instrument_type="EQ",
        )

    def is_fno_symbol(self, s):
        return False

    def is_equity(self, s):
        return True

    def get_equity_symbols(self):
        return list(self.symbols)


def _quote(symbol, ltp, change_pct, volume=1000, ts=None):
    ts = ts or datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
    return Quote(
        instrument_token=f"NSE_EQ|INE{abs(hash(symbol)) % 10**8:08d}",
        exchange="NSE_EQ", tradingsymbol=symbol, received_ts=ts,
        ltp=ltp, change=round(change_pct, 2), change_percent=change_pct,
        volume=volume,
    )


SYMS = ["RELIANCE", "INFY", "TCS", "HDFC", "WIPRO"]
CATALOG = FakeCatalog(SYMS)


def _fake_members():
    return [types.SimpleNamespace(
        exchange="NSE_EQ", symbol=s,
        instrument_token=f"NSE_EQ|INE{abs(hash(s)) % 10**8:08d}", name=s,
    ) for s in SYMS]


@pytest.fixture(autouse=True)
def _patch_resolve(monkeypatch):
    monkeypatch.setattr(scanner_mod, "resolve_universe",
                        lambda universe, catalog: _fake_members())


def _reader_factory(quotes):
    store = {q.instrument_token: q for q in quotes}

    def reader(exchange, token):
        return store.get(token)
    return reader


def test_status_of():
    assert _status_of(None) == "unavailable"
    assert _status_of(1.0) == "advance"
    assert _status_of(-1.0) == "decline"
    assert _status_of(0.0) == "unchanged"


def test_list_scanners_all_equity():
    eng = ScannerEngine(CATALOG)
    scanners = eng.list_scanners()
    assert {s["name"] for s in scanners} == {
        "gainers", "losers", "volume", "advances", "declines"}
    assert all(s["instrument_class"] == "equity" for s in scanners)


def test_gainers_ranking():
    quotes = [
        _quote("RELIANCE", 2500, 3.0),
        _quote("INFY", 1800, -1.5),
        _quote("TCS", 4000, 5.0),
        _quote("HDFC", 1500, 0.0),
        _quote("WIPRO", 500, -2.0),
    ]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("gainers", "NIFTY50", _reader_factory(quotes), limit=25)
    assert res.eligible == 5 and res.quoted == 5 and res.matched == 5
    assert [r.symbol for r in res.rows] == ["TCS", "RELIANCE", "HDFC", "INFY", "WIPRO"]
    assert res.rows[0].change_percent == 5.0


def test_losers_ranking():
    quotes = [
        _quote("RELIANCE", 2500, 3.0),
        _quote("INFY", 1800, -1.5),
        _quote("TCS", 4000, 5.0),
        _quote("HDFC", 1500, 0.0),
        _quote("WIPRO", 500, -2.0),
    ]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("losers", "NIFTY50", _reader_factory(quotes), limit=25)
    assert [r.symbol for r in res.rows] == ["WIPRO", "INFY", "HDFC", "RELIANCE", "TCS"]


def test_volume_ranking():
    quotes = [
        _quote("RELIANCE", 2500, 0.0, volume=5000),
        _quote("INFY", 1800, 0.0, volume=9000),
        _quote("TCS", 4000, 0.0, volume=1000),
        _quote("HDFC", 1500, 0.0, volume=3000),
        _quote("WIPRO", 500, 0.0, volume=7000),
    ]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("volume", "NIFTY50", _reader_factory(quotes), limit=25)
    assert [r.symbol for r in res.rows] == ["INFY", "WIPRO", "RELIANCE", "HDFC", "TCS"]


def test_advances_filter_excludes_decliners():
    quotes = [
        _quote("RELIANCE", 2500, 3.0),
        _quote("INFY", 1800, -1.5),
        _quote("TCS", 4000, 5.0),
        _quote("HDFC", 1500, 0.0),
        _quote("WIPRO", 500, -2.0),
    ]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("advances", "NIFTY50", _reader_factory(quotes), limit=25)
    assert [r.symbol for r in res.rows] == ["TCS", "RELIANCE"]


def test_declines_filter_excludes_advancers():
    quotes = [
        _quote("RELIANCE", 2500, 3.0),
        _quote("INFY", 1800, -1.5),
        _quote("TCS", 4000, 5.0),
        _quote("HDFC", 1500, 0.0),
        _quote("WIPRO", 500, -2.0),
    ]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("declines", "NIFTY50", _reader_factory(quotes), limit=25)
    assert [r.symbol for r in res.rows] == ["WIPRO", "INFY"]


def test_unavailable_quote_excluded_from_match():
    # Only RELIANCE has a quote; the rest are unavailable.
    quotes = [_quote("RELIANCE", 2500, 3.0)]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("gainers", "NIFTY50", _reader_factory(quotes), limit=25)
    assert res.eligible == 5 and res.quoted == 1 and res.matched == 1
    assert res.rows[0].symbol == "RELIANCE"


def test_limit_caps_rows():
    quotes = [_quote(s, 100, i * 0.5) for i, s in enumerate(SYMS)]
    eng = ScannerEngine(CATALOG)
    res = eng.scan("gainers", "NIFTY50", _reader_factory(quotes), limit=2)
    assert res.matched == 5
    assert len(res.rows) == 2


def test_unknown_scanner_raises():
    eng = ScannerEngine(CATALOG)
    with pytest.raises(ValueError):
        eng.scan("nope", "NIFTY50", _reader_factory([]))


def test_unknown_universe_raises(monkeypatch):
    from market.market_universe import resolve_universe as real_resolve
    monkeypatch.setattr(scanner_mod, "resolve_universe", real_resolve)
    eng = ScannerEngine(CATALOG)
    with pytest.raises(ValueError):
        eng.scan("gainers", "NOTREAL", _reader_factory([]))
