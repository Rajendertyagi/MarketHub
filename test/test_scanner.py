"""Unit tests for the generic scanner engine (Phase C / D)."""

import sys
import types
from datetime import datetime, timezone

import pytest

import market.scanner as scanner_mod
from market.scanner import ScannerEngine, _status_of
from market.models import Quote, OptionGreeks


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
        "gainers", "losers", "volume", "advances", "declines",
        "futures_oi", "futures_oi_change", "futures_oi_change_pct",
        "option_iv"}
    assert all(s["instrument_class"] == "equity"
               for s in scanners if s["name"] in
               {"gainers", "losers", "volume", "advances", "declines"})
    assert all(s["contract_kind"] in ("future", "option")
               for s in scanners if s["name"].startswith(("futures", "option")))


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

# ---------------------------------------------------------------------------
# Derivative scanners (Futures OI / Option IV) - reuse the SAME engine.
# ---------------------------------------------------------------------------

class FakeDerivCatalog:
    EXPIRIES = ["2026-10-30", "2026-11-27"]

    def search(self, underlying=None, instrument_type=None, **kw):
        if instrument_type == "FUTURE":
            syms = [underlying] if underlying else \
                ["RELIANCE", "INFY", "TCS", "HDFC", "WIPRO"]
            return [{
                "provider": "upstox",
                "instrument_token": f"NFO_FUT|{s}",
                "exchange": "NFO",
                "tradingsymbol": f"{s}26OCTFUT",
                "underlying": s,
                "expiry": "2026-10-30", "strike": None, "option_type": None,
            } for s in syms]
        return []

    def derivative_expiries(self, underlying, instrument_type):
        return list(self.EXPIRIES)

    def option_strikes(self, underlying, expiry):
        rows = []
        for s in [100, 110, 120, 130, 140]:
            rows.append({
                "provider": "upstox",
                "instrument_token": f"NFO_CE|{underlying}|{s}",
                "exchange": "NFO",
                "tradingsymbol": f"{underlying}26OCT{s}CE",
                "expiry": expiry, "strike": s, "option_type": "CE"})
            rows.append({
                "provider": "upstox",
                "instrument_token": f"NFO_PE|{underlying}|{s}",
                "exchange": "NFO",
                "tradingsymbol": f"{underlying}26OCT{s}PE",
                "expiry": expiry, "strike": s, "option_type": "PE"})
        return rows


def _fut_reader(quotes):
    store = {q.instrument_token: q for q in quotes}
    return lambda e, t: store.get(t)


def _fut_quote(sym, oi, ltp=100.0, chg=0.0, oic=None, oicp=None):
    return Quote(
        instrument_token=f"NFO_FUT|{sym}", exchange="NFO",
        tradingsymbol=f"{sym}26OCTFUT",
        received_ts=datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc),
        ltp=ltp, change=chg, change_percent=chg,
        open_interest=oi, oi_change=oic, oi_change_percent=oicp)


def _opt_quote(sym, strike, ot, iv, ltp=10.0):
    return Quote(
        instrument_token=f"NFO_{ot}|{sym}|{strike}", exchange="NFO",
        tradingsymbol=f"{sym}26OCT{strike}{ot}",
        received_ts=datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc),
        ltp=ltp, change=0.0, change_percent=0.0,
        open_interest=500, greeks=OptionGreeks(iv=iv))


def test_futures_oi_uses_future_instruments():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_fut_quote("RELIANCE", 1000), _fut_quote("INFY", 3000),
              _fut_quote("TCS", 2000)]
    res = eng.scan("futures_oi", "NIFTY50", _fut_reader(quotes), limit=10)
    assert res.eligible == 5 and res.quoted == 3
    assert [r.symbol for r in res.rows] == ["INFY", "TCS", "RELIANCE"]
    assert all(r.contract and r.contract.endswith("FUT") for r in res.rows)
    assert all(r.expiry == "2026-10-30" for r in res.rows)


def test_futures_oi_change_pct_ranking():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_fut_quote("RELIANCE", 1000, oicp=5.0),
              _fut_quote("INFY", 1000, oicp=-2.0)]
    res = eng.scan("futures_oi_change_pct", "NIFTY50", _fut_reader(quotes),
                   limit=10)
    assert [r.symbol for r in res.rows] == ["RELIANCE", "INFY"]


def test_futures_oi_missing_oi_excluded():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_fut_quote("RELIANCE", 1000), _fut_quote("INFY", None)]
    res = eng.scan("futures_oi", "NIFTY50", _fut_reader(quotes), limit=10)
    assert res.quoted == 2
    assert [r.symbol for r in res.rows] == ["RELIANCE"]


def test_futures_stale_unavailable():
    eng = ScannerEngine(FakeDerivCatalog())
    res = eng.scan("futures_oi", "NIFTY50", _fut_reader([]), limit=10)
    assert res.eligible == 5 and res.quoted == 0 and res.matched == 0


def test_option_iv_uses_option_instruments():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_opt_quote("RELIANCE", 120, "CE", 0.20),
              _opt_quote("INFY", 120, "CE", 0.35),
              _opt_quote("TCS", 120, "CE", 0.10)]
    res = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10,
                   atm_range=2)
    assert res.quoted == 3
    assert [r.symbol for r in res.rows] == ["INFY", "RELIANCE", "TCS"]
    assert all(r.option_type == "CE" for r in res.rows)
    assert all(r.iv is not None for r in res.rows)


def test_option_iv_zero_iv_valid():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_opt_quote("RELIANCE", 120, "CE", 0.0),
              _opt_quote("INFY", 120, "CE", 0.30)]
    res = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10)
    assert [r.symbol for r in res.rows] == ["INFY", "RELIANCE"]
    assert res.rows[-1].iv == 0.0


def test_option_iv_gt_100pct_valid_canonical_fraction():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_opt_quote("RELIANCE", 120, "CE", 1.5),
              _opt_quote("INFY", 120, "CE", 0.30)]
    res = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10)
    assert res.rows[0].iv == 1.5


def test_option_iv_missing_iv_excluded():
    eng = ScannerEngine(FakeDerivCatalog())
    q = Quote(instrument_token="NFO_CE|RELIANCE|120", exchange="NFO",
              tradingsymbol="RELIANCE26OCT120CE",
              received_ts=datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc),
              ltp=10, greeks=OptionGreeks(iv=None))
    res = eng.scan("option_iv", "NIFTY50", _fut_reader([q, _opt_quote("INFY", 120, "CE", 0.30)]),
                   limit=10)
    assert [r.symbol for r in res.rows] == ["INFY"]


def test_option_iv_atm_range():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_opt_quote("RELIANCE", 100, "CE", 0.10),
              _opt_quote("RELIANCE", 140, "CE", 0.40)]
    none = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10,
                    atm_range=0)
    assert none.quoted == 0  # only ATM strike (120) selected, no quote there
    both = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10,
                    atm_range=2)
    assert both.quoted == 2


def test_option_iv_ce_pe_filter():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_opt_quote("RELIANCE", 120, "CE", 0.20),
              _opt_quote("RELIANCE", 120, "PE", 0.50)]
    res = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10,
                   option_type="PE")
    assert res.quoted == 1 and res.rows[0].option_type == "PE"


def test_option_iv_expiry_selection():
    eng = ScannerEngine(FakeDerivCatalog())
    none = eng.scan("option_iv", "NIFTY50", _fut_reader([]), limit=10,
                     expiry="2030-01-01")
    assert none.quoted == 0
    res = eng.scan("option_iv", "NIFTY50",
                   _fut_reader([_opt_quote("RELIANCE", 120, "CE", 0.20)]),
                   limit=10, expiry="2026-10-30")
    assert res.quoted == 1 and res.rows[0].expiry == "2026-10-30"


def test_no_cross_instrument_substitution():
    eng = ScannerEngine(FakeDerivCatalog())
    res = eng.scan("futures_oi", "NIFTY50",
                   _fut_reader([_fut_quote("RELIANCE", 1000)]), limit=10)
    assert all(r.option_type == "FUT" and r.contract.endswith("FUT")
               for r in res.rows)
    res2 = eng.scan("option_iv", "NIFTY50",
                    _fut_reader([_opt_quote("RELIANCE", 120, "CE", 0.2)]),
                    limit=10)
    assert all(r.contract.endswith(("CE", "PE")) for r in res2.rows)


def test_invalid_option_type_defaults_to_both():
    eng = ScannerEngine(FakeDerivCatalog())
    quotes = [_opt_quote("RELIANCE", 120, "CE", 0.2),
              _opt_quote("RELIANCE", 120, "PE", 0.3)]
    res = eng.scan("option_iv", "NIFTY50", _fut_reader(quotes), limit=10,
                   option_type="BOGUS")
    assert res.quoted == 2


def test_equity_scanners_preserved():
    eng = ScannerEngine(CATALOG)
    quotes = [_quote("RELIANCE", 2500, 3.0), _quote("INFY", 1800, -1.5),
              _quote("TCS", 4000, 5.0), _quote("HDFC", 1500, 0.0),
              _quote("WIPRO", 500, -2.0)]
    res = eng.scan("gainers", "NIFTY50", _reader_factory(quotes), limit=25)
    assert [r.symbol for r in res.rows] == ["TCS", "RELIANCE", "HDFC", "INFY", "WIPRO"]
