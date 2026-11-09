#!/usr/bin/env python3
"""Catalogue synchronisation safety (fail-safe, segment-scoped).

The catalogue is a derived cache: a failed, empty or incomplete master
must never destroy valid stored rows.

Covered here:
  * Upstox: download failure / parse failure / empty master / missing
    enabled segment  ->  the stored catalogue is untouched;
  * Upstox: a valid master replaces the catalogue;
  * FYERS: one failed master keeps its previous rows while the other
    masters are refreshed;
  * FYERS: all masters failing leaves the catalogue untouched;
  * FYERS: an empty master file keeps the previous rows;
  * the identity listener fires only after a catalogue actually changed;
  * the FYERS master coverage includes BSE_FO and excludes MCX.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest


def _upstox_master(*, with_equity=True, with_future=True, with_option=True,
                   with_index=True, with_bse=True) -> bytes:
    rows = []
    if with_equity:
        rows.append({"instrument_key": "NSE_EQ|INE002A01018",
                     "exchange": "NSE", "segment": "NSE_EQ",
                     "instrument_type": "EQ", "trading_symbol": "RELIANCE",
                     "name": "RELIANCE", "isin": "INE002A01018"})
    if with_future:
        rows.append({"instrument_key": "NSE_FO|42631", "exchange": "NSE",
                     "segment": "NSE_FO", "instrument_type": "FUT",
                     "trading_symbol": "RELIANCE25OCTFUT",
                     "underlying_symbol": "RELIANCE"})
    if with_option:
        rows.append({"instrument_key": "NSE_FO|42632", "exchange": "NSE",
                     "segment": "NSE_FO", "instrument_type": "CE",
                     "trading_symbol": "RELIANCE25OCT2500CE",
                     "underlying_symbol": "RELIANCE", "strike_price": 2500})
    if with_index:
        rows.append({"instrument_key": "NSE_INDEX|Nifty 50",
                     "exchange": "NSE", "segment": "NSE_INDEX",
                     "instrument_type": "INDEX",
                     "trading_symbol": "NIFTY 50", "name": "Nifty 50"})
    if with_bse:
        # The official master carries the BSE exchange too (BSE_INDEX /
        # BSE_FO) - a master without them is a partial download.
        rows.append({"instrument_key": "BSE_INDEX|SENSEX",
                     "exchange": "BSE", "segment": "BSE_INDEX",
                     "instrument_type": "INDEX",
                     "trading_symbol": "SENSEX", "name": "SENSEX"})
        rows.append({"instrument_key": "BSE_FO|801", "exchange": "BSE",
                     "segment": "BSE_FO", "instrument_type": "FUT",
                     "trading_symbol": "SENSEX25OCTFUT",
                     "underlying_symbol": "SENSEX"})
    return json.dumps(rows).encode("utf-8")


def _fyers_master(symbol: str, *, inst_type: int = 0, exchange: int = 10,
                  segment: int = 10, fytoken: str = "1010000000026",
                  ticker: str | None = None) -> bytes:
    row = {"fyToken": fytoken, "exchange": exchange, "segment": segment,
           "exInstType": inst_type, "symDetails": symbol,
           "symTicker": ticker or symbol, "minLotSize": 1,
           "tickSize": 0.05}
    return json.dumps({symbol: row}).encode("utf-8")


@pytest.fixture
def store():
    from core.persistence.store import EventStore
    with tempfile.TemporaryDirectory() as tmp:
        yield EventStore(str(Path(tmp) / "events.db"))


def _store():
    """A throwaway store (for tests that do not use the fixture)."""
    from core.persistence.store import EventStore
    tmp = tempfile.mkdtemp(prefix="cat-sync-")
    return EventStore(str(Path(tmp) / "events.db"))


@pytest.fixture
def catalog(store):
    from app.instruments import InstrumentCatalog
    return InstrumentCatalog(store)


def _count(store, provider=None) -> int:
    rows = store.list_all_instruments()
    if provider:
        rows = [r for r in rows if r.get("provider") == provider]
    return len(rows)


# -- Upstox ---------------------------------------------------------------

def test_upstox_download_failure_keeps_catalogue(catalog, store) -> None:
    def boom(url):
        raise OSError("network down")

    result = catalog.sync_upstox(fetch=boom)
    assert result["aborted"] is True
    assert result["ok"] is False
    assert "download failed" in result["reason"]
    assert _count(store) == 0


def test_upstox_parse_failure_keeps_catalogue(catalog, store) -> None:
    def bad(url):
        return b"{not json"

    result = catalog.sync_upstox(fetch=bad)
    assert result["aborted"] is True
    assert "parse failed" in result["reason"]
    assert _count(store) == 0


def test_upstox_empty_master_keeps_catalogue(catalog, store) -> None:
    catalog.sync_upstox(fetch=lambda url: _upstox_master())
    before = _count(store)
    assert before > 0

    result = catalog.sync_upstox(fetch=lambda url: json.dumps([]).encode())
    assert result["aborted"] is True
    assert "zero records" in result["reason"]
    assert _count(store) == before


def test_upstox_incomplete_master_keeps_catalogue(catalog, store) -> None:
    catalog.sync_upstox(fetch=lambda url: _upstox_master())
    before = _count(store)

    # A master that lost a whole segment is a partial download.
    result = catalog.sync_upstox(
        fetch=lambda url: _upstox_master(with_future=False,
                                         with_option=False))
    assert result["aborted"] is True
    assert "missing enabled segment" in result["reason"]
    assert _count(store) == before


def test_upstox_successful_sync_replaces_catalogue(catalog, store) -> None:
    result = catalog.sync_upstox(fetch=lambda url: _upstox_master())
    assert result["aborted"] is False
    assert result["records"] > 0
    assert _count(store) == result["records"]


# -- FYERS ----------------------------------------------------------------

def test_fyers_failed_master_keeps_its_previous_rows(catalog, store) -> None:
    from app.instruments import FYERS_SEGMENT_URLS

    def fetch_ok(url):
        if url == FYERS_SEGMENT_URLS["NSE_CM"]:
            return _fyers_master("NSE:SBIN-EQ", fytoken="1010000000026")
        if url == FYERS_SEGMENT_URLS["NSE_FO"]:
            return _fyers_master("NSE:RELIANCE25OCTFUT", inst_type=13,
                                  segment=11, fytoken="101126000000001",
                                  ticker="RELIANCE25OCTFUT")
        if url == FYERS_SEGMENT_URLS["BSE_CM"]:
            return _fyers_master("BSE:SENSEX-INDEX", inst_type=10,
                                  exchange=12, fytoken="12100000001")
        return _fyers_master("BSE:SENSEX25OCTFUT", inst_type=11, exchange=12,
                              segment=11, fytoken="121000000099",
                              ticker="SENSEX25OCTFUT")

    # First sync: every master succeeds.
    first = catalog.sync_fyers(fetch=fetch_ok)
    assert first["aborted"] is False
    nse_fo_rows = len([
        r for r in store.list_all_instruments()
        if r.get("provider") == "fyers" and r.get("segment") == "NSE_FO"])
    assert nse_fo_rows > 0

    # Second sync: the NSE_FO master fails -> its rows must survive.
    def fetch_fail(url):
        if url == FYERS_SEGMENT_URLS["NSE_FO"]:
            raise OSError("segment down")
        return fetch_ok(url)

    second = catalog.sync_fyers(fetch=fetch_fail)
    assert second["ok"] is True          # other masters still refreshed
    assert second["masters"]["NSE_FO"] == "retained"
    kept = [
        r for r in store.list_all_instruments()
        if r.get("provider") == "fyers" and r.get("segment") == "NSE_FO"]
    assert len(kept) == nse_fo_rows, "failed master must not delete rows"


def test_fyers_all_masters_failing_keeps_catalogue(catalog, store) -> None:
    def boom(url):
        raise OSError("network down")

    result = catalog.sync_fyers(fetch=boom)
    assert result["aborted"] is True
    assert result["ok"] is False
    assert _count(store) == 0


def test_fyers_empty_master_keeps_previous_rows(catalog, store) -> None:
    from app.instruments import FYERS_SEGMENT_URLS

    def fetch(url):
        if url == FYERS_SEGMENT_URLS["NSE_CM"]:
            return json.dumps({}).encode()
        if url == FYERS_SEGMENT_URLS["BSE_CM"]:
            return _fyers_master("BSE:SENSEX-INDEX", inst_type=10, exchange=12,
                                  fytoken="12100000001")
        if url == FYERS_SEGMENT_URLS["BSE_FO"]:
            return _fyers_master("BSE:SENSEX25OCTFUT", inst_type=11,
                                  exchange=12, segment=11,
                                  fytoken="121000000099",
                                  ticker="SENSEX25OCTFUT")
        return _fyers_master("NSE:SBIN-EQ", fytoken="1010000000026")

    catalog.sync_fyers(fetch=fetch)
    before = [
        r for r in store.list_all_instruments()
        if r.get("provider") == "fyers" and r.get("segment") == "NSE_EQ"]

    def fetch_empty(url):
        if url == FYERS_SEGMENT_URLS["NSE_CM"]:
            return json.dumps({}).encode()
        return fetch(url)

    catalog.sync_fyers(fetch=fetch_empty)
    after = [
        r for r in store.list_all_instruments()
        if r.get("provider") == "fyers" and r.get("segment") == "NSE_EQ"]
    assert len(after) == len(before) > 0


def test_fyers_master_coverage_includes_bse_fo_excludes_mcx() -> None:
    from app.instruments import FYERS_SEGMENT_URLS
    assert "BSE_FO" in FYERS_SEGMENT_URLS
    assert "MCX_COM" not in FYERS_SEGMENT_URLS
    assert "NSE_CM" in FYERS_SEGMENT_URLS
    assert "NSE_FO" in FYERS_SEGMENT_URLS


# -- identity refresh -----------------------------------------------------

def test_sync_listener_fires_only_after_a_real_change(catalog) -> None:
    seen = []
    catalog.add_sync_listener(seen.append)

    catalog.sync_upstox(fetch=lambda url: _upstox_master())
    assert len(seen) == 1

    # An aborted sync must not notify (the catalogue did not change).
    catalog.sync_upstox(fetch=lambda url: json.dumps([]).encode())
    assert len(seen) == 1


def test_a_failing_segment_write_rolls_back_and_keeps_the_rows() -> None:
    """A write that fails mid-transaction must not remove valid rows."""
    from core.persistence.modules import products

    store = _store()
    from app.instruments import InstrumentCatalog
    catalog = InstrumentCatalog(store)

    # A valid first sync.
    catalog.sync_fyers(fetch=lambda url: json.dumps({
        "NSE:SBIN-EQ": {"fyToken": "1010000000026", "exchange": 10,
                        "segment": 10, "exInstType": 0,
                        "symDetails": "SBIN", "symTicker": "NSE:SBIN-EQ",
                        "minLotSize": 1, "tickSize": 0.05},
    }).encode())
    before = len(store.list_all_instruments())
    assert before == 1

    # A write that violates the primary key must roll back completely.
    duplicate = {"instrument_token": "1010000000026", "exchange": "NSE",
                 "segment": "NSE_EQ", "tradingsymbol": "NSE:SBIN-EQ",
                 "instrument_type": "EQUITY", "provider_symbol": "NSE:SBIN-EQ"}
    try:
        products.replace_provider_instruments_segmented_at(
            store.db_path, "fyers", [duplicate, duplicate], ["NSE_EQ"])
    except Exception:
        pass
    else:
        raise AssertionError("the duplicate insert must fail")

    rows = store.list_all_instruments()
    assert len(rows) == before, "the rollback must keep the previous rows"
    # the canonicaliser strips the EXCH: namespace for cash rows
    assert rows[0]["tradingsymbol"] == "SBIN-EQ"


def test_the_segmented_write_is_segment_scoped() -> None:
    """Only the refreshed segments are rewritten; the others survive."""
    from core.persistence.modules import products

    store = _store()
    rows = [
        {"instrument_token": "1", "exchange": "NSE", "segment": "NSE_EQ",
         "tradingsymbol": "NSE:SBIN-EQ", "instrument_type": "EQUITY",
         "provider_symbol": "NSE:SBIN-EQ"},
        {"instrument_token": "2", "exchange": "NSE", "segment": "NSE_INDEX",
         "tradingsymbol": "NSE:NIFTY50-INDEX", "instrument_type": "INDEX",
         "provider_symbol": "NSE:NIFTY50-INDEX"},
    ]
    products.replace_provider_instruments_segmented_at(
        store.db_path, "fyers", rows, ["NSE_EQ", "NSE_INDEX"])
    assert len(store.list_all_instruments()) == 2

    # Replace only NSE_EQ; the index row must survive untouched.
    products.replace_provider_instruments_segmented_at(
        store.db_path, "fyers",
        [rows[0] | {"tradingsymbol": "NSE:SBIN-EQ", "name": "refreshed"}],
        ["NSE_EQ"])
    kept = {r["tradingsymbol"]: r for r in store.list_all_instruments()}
    assert set(kept) == {"NSE:SBIN-EQ", "NSE:NIFTY50-INDEX"}
    assert kept["NSE:SBIN-EQ"]["name"] == "refreshed"


# -- predicate-scoped write (Sensex-only BSE_FO ingestion) ------------------

def _bse_row(token: str, symbol: str, underlying: str, **kw) -> dict:
    row = {
        "instrument_token": token, "exchange": "BSE", "segment": "BSE_FO",
        "tradingsymbol": symbol, "instrument_type": "FUTURE",
        "provider_symbol": f"BSE:{symbol}", "underlying": underlying,
        "expiry": kw.pop("expiry", "2026-10-29"),
    }
    row.update(kw)
    return row


def test_the_scoped_write_preserves_rows_outside_its_scope() -> None:
    """A Sensex refresh must not delete the provider's BANKEX/stock rows."""
    from core.persistence.modules import products

    store = _store()
    products.replace_provider_instruments_segmented_at(
        store.db_path, "fyers",
        [_bse_row("1", "SENSEX26OCTFUT", "SENSEX"),
         _bse_row("2", "BANKEX26OCTFUT", "BANKEX")],
        ["BSE_FO"])

    products.replace_provider_instruments_scoped_at(
        store.db_path, "fyers",
        [_bse_row("3", "SENSEX26NOVFUT", "SENSEX")],
        segment="BSE_FO", scope_column="underlying",
        scope_values=("SENSEX",))

    rows = {r["instrument_token"]: r for r in store.list_all_instruments()}
    assert set(rows) == {"2", "3"}, "BANKEX survives; old Sensex replaced"
    assert rows["2"]["underlying"] == "BANKEX"


def test_the_scoped_write_rejects_an_empty_scope() -> None:
    """An empty scope would degenerate into a segment-wide delete."""
    from core.persistence.modules import products

    store = _store()
    products.replace_provider_instruments_segmented_at(
        store.db_path, "fyers",
        [_bse_row("1", "BANKEX26OCTFUT", "BANKEX")], ["BSE_FO"])
    before = store.list_all_instruments()

    with pytest.raises(ValueError):
        products.replace_provider_instruments_scoped_at(
            store.db_path, "fyers",
            [_bse_row("2", "SENSEX26OCTFUT", "SENSEX")],
            segment="BSE_FO", scope_column="underlying", scope_values=())
    assert store.list_all_instruments() == before


def test_the_scoped_write_rejects_a_token_owned_outside_the_scope() -> None:
    """Re-using a token that belongs to another underlying is a conflict."""
    from core.persistence.modules import products

    store = _store()
    products.replace_provider_instruments_segmented_at(
        store.db_path, "fyers",
        [_bse_row("1", "BANKEX26OCTFUT", "BANKEX")], ["BSE_FO"])
    before = store.list_all_instruments()

    with pytest.raises(products.ScopedReplaceConflict):
        products.replace_provider_instruments_scoped_at(
            store.db_path, "fyers",
            [_bse_row("1", "SENSEX26OCTFUT", "SENSEX")],
            segment="BSE_FO", scope_column="underlying",
            scope_values=("SENSEX",))
    assert store.list_all_instruments() == before, (
        "a rejected write must leave the catalogue untouched")


def _sensex_master() -> bytes:
    """A minimal valid FYERS BSE_FO master (future + CE + PE)."""
    from datetime import datetime, timezone

    def epoch(day: str) -> str:
        return str(int(datetime.fromisoformat(day)
                       .replace(tzinfo=timezone.utc).timestamp()))

    def row(token: str, symbol: str, inst_type: int, *, expiry: str,
            opt: str = "", strike: float = -1.0) -> dict:
        return {
            "fyToken": token, "exchange": 12, "segment": 11,
            "exInstType": inst_type, "tradeStatus": 1, "underSym": "SENSEX",
            "symTicker": f"BSE:{symbol}", "symDetails": symbol,
            "expiryDate": epoch(expiry), "optType": opt,
            "strikePrice": strike, "minLotSize": 20, "tickSize": 0.05,
        }

    master = {
        "BSE:SENSEX26OCTFUT": row("sx-fut", "SENSEX26OCTFUT", 11,
                                  expiry="2026-10-29"),
        "BSE:SENSEX26O80000CE": row("sx-ce", "SENSEX26O80000CE", 14,
                                    expiry="2026-10-29", opt="CE",
                                    strike=80000.0),
        "BSE:SENSEX26O80000PE": row("sx-pe", "SENSEX26O80000PE", 14,
                                    expiry="2026-10-29", opt="PE",
                                    strike=80000.0),
        "BSE:BANKEX26OCTFUT": {
            **row("bk-fut", "BANKEX26OCTFUT", 11, expiry="2026-10-29"),
            "underSym": "BANKEX", "symTicker": "BSE:BANKEX26OCTFUT",
        },
    }
    return json.dumps(master).encode("utf-8")


def test_a_bse_fo_only_sync_leaves_other_segments_untouched(catalog,
                                                            store) -> None:
    """Refreshing the SENSEX leg never rewrites NSE or BSE index rows."""
    from app.instruments import FYERS_SEGMENT_URLS
    from core.persistence.modules import products

    products.replace_provider_instruments_segmented_at(
        store.db_path, "fyers",
        [{"instrument_token": "eq-1", "exchange": "NSE",
          "segment": "NSE_EQ", "tradingsymbol": "SBIN",
          "instrument_type": "EQUITY", "provider_symbol": "NSE:SBIN-EQ"},
         {"instrument_token": "idx-1", "exchange": "NSE",
          "segment": "NSE_INDEX", "tradingsymbol": "NSE:NIFTY50-INDEX",
          "instrument_type": "INDEX", "underlying": "NIFTY",
          "provider_symbol": "NSE:NIFTY50-INDEX"},
         {"instrument_token": "bse-idx", "exchange": "BSE",
          "segment": "BSE_INDEX", "tradingsymbol": "BSE:SENSEX-INDEX",
          "instrument_type": "INDEX", "underlying": "SENSEX",
          "provider_symbol": "BSE:SENSEX-INDEX"},
         _bse_row("old-bankex", "BANKEX26OCTFUT", "BANKEX")],
        ["NSE_EQ", "NSE_INDEX", "BSE_INDEX", "BSE_FO"])
    before = {r["instrument_token"]: dict(r)
              for r in store.list_all_instruments()
              if r["segment"] in ("NSE_EQ", "NSE_INDEX", "BSE_INDEX")}

    def fetch(url):
        if url == FYERS_SEGMENT_URLS["BSE_FO"]:
            return _sensex_master()
        raise OSError("not requested")

    result = catalog.sync_fyers(
        fetch=fetch, masters=["BSE_FO"],
        segments={"NSE_EQ", "NSE_INDEX", "BSE_INDEX", "BSE_FO"})
    assert result["ok"] is True

    after = {r["instrument_token"]: dict(r)
             for r in store.list_all_instruments()
             if r["segment"] in ("NSE_EQ", "NSE_INDEX", "BSE_INDEX")}
    assert after == before
    assert any(r["instrument_token"] == "old-bankex"
               for r in store.list_all_instruments()), (
        "unrelated BSE_FO rows are preserved")


def test_the_sensex_policy_is_provider_neutral() -> None:
    """The SAME metadata rule selects SENSEX from Upstox-shaped rows.

    Upstox publishes ``underlying_symbol`` and the canonical segment; the
    policy reads that authoritative field (never the symbol shape), so the
    provider-neutral selection is identical to the FYERS one. (The Upstox
    write path is deliberately NOT filtered in this change: pruning its
    existing BANKEX/SENSEX50 rows is a separate, destructive decision.)
    """
    from app.instruments import _filter_sensex_bse_fo, upstox_master_records

    payload = json.dumps([
        {"instrument_key": "BSE_FO|1", "exchange": "BSE",
         "segment": "BSE_FO", "instrument_type": "FUT",
         "trading_symbol": "SENSEX FUT", "underlying_symbol": "SENSEX"},
        {"instrument_key": "BSE_FO|2", "exchange": "BSE",
         "segment": "BSE_FO", "instrument_type": "CE",
         "trading_symbol": "SENSEX 80000 CE",
         "underlying_symbol": "SENSEX", "strike_price": 80000},
        {"instrument_key": "BSE_FO|3", "exchange": "BSE",
         "segment": "BSE_FO", "instrument_type": "FUT",
         "trading_symbol": "BANKEX FUT", "underlying_symbol": "BANKEX"},
        {"instrument_key": "BSE_FO|4", "exchange": "BSE",
         "segment": "BSE_FO", "instrument_type": "FUT",
         "trading_symbol": "SENSEX50 FUT", "underlying_symbol": "SENSEX50"},
        {"instrument_key": "BSE_FO|5", "exchange": "BSE",
         "segment": "BSE_FO", "instrument_type": "FUT",
         "trading_symbol": "TCS FUT", "underlying_symbol": "TCS"},
    ]).encode()

    records = upstox_master_records(payload)
    kept, counts, filtered = _filter_sensex_bse_fo(records, {"BSE_FO"})
    assert {r["underlying"] for r in kept} == {"SENSEX"}
    assert counts == {"BSE_FO": 2}
    assert filtered == 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
