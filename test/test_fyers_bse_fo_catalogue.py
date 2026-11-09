#!/usr/bin/env python3
"""FYERS BSE F&O catalogue: Sensex-only selection + safe selective write.

The BSE F&O master carries the whole BSE derivatives universe (SENSEX,
BANKEX, SENSEX50, individual BSE stocks). MarketHub's scope is the SENSEX
leg only, and the existing catalogue already holds unrelated BSE_FO rows
that must survive the refresh.

Every test runs against an ISOLATED database with deterministic master
fixtures - never against the live catalogue:

  * the master parses into canonical ``BSE_FO`` rows;
  * only authoritative-Sensex, ACTIVE contracts survive the filter;
  * the provider's unrelated BSE_FO rows are PRESERVED (predicate-scoped
    write, never a segment-wide delete);
  * a failed / empty / malformed / zero-Sensex master retains the previous
    catalogue;
  * the ``masters`` selector refreshes ONE master file without touching the
    NSE/BSE cash masters.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

from app.instruments import (
    FYERS_SEGMENT_URLS,
    SENSEX_UNDERLYINGS,
    InstrumentCatalog,
    _filter_sensex_bse_fo,
    fyers_master_records,
)


def _epoch(day: str) -> str:
    """An ISO date -> the master's epoch-seconds string (expiry instant)."""
    dt = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp()))


_NEAR = "2026-10-29"
_NEXT = "2026-11-26"


def _row(symbol: str, *, underlying: str, inst_type: int,
         fytoken: str, expiry: str | None = None, opt_type: str = "",
         strike: float = -1.0, trade_status: int = 1) -> dict:
    return {
        "symbol": symbol, "underlying": underlying, "inst_type": inst_type,
        "fytoken": fytoken, "expiry": expiry, "opt_type": opt_type,
        "strike": strike, "trade_status": trade_status,
    }


def _master(*rows: dict) -> bytes:
    """Build a FYERS BSE F&O master (exchange 12, segment 11)."""
    master: dict[str, dict] = {}
    for r in rows:
        symbol = r["symbol"]
        master[symbol] = {
            "fyToken": r["fytoken"],
            "exToken": len(master) + 1,
            "exSymbol": r["underlying"],
            "exSymName": symbol.split(":")[-1],
            "symTicker": symbol,
            "exchange": 12,
            "segment": 11,
            "exInstType": r["inst_type"],
            "tradeStatus": r["trade_status"],
            "underSym": r["underlying"],
            "underFyTok": "121000000012",
            "expiryDate": _epoch(r["expiry"]) if r["expiry"] else "",
            "optType": r["opt_type"],
            "strikePrice": r["strike"],
            "minLotSize": 20,
            "tickSize": 0.05,
            "isin": "",
            "symDetails": symbol,
            "lastUpdate": "2026-10-21",
            "currencyCode": "INR",
        }
    return json.dumps(master).encode("utf-8")


def _sensex_rows() -> list[dict]:
    """The in-scope set: futures + CE/PE options across two expiries."""
    rows = [
        _row("BSE:SENSEX26OCTFUT", underlying="SENSEX", inst_type=11,
             fytoken="sx-fut-1", expiry=_NEAR),
        _row("BSE:SENSEX26NOVFUT", underlying="SENSEX", inst_type=11,
             fytoken="sx-fut-2", expiry=_NEXT),
    ]
    for expiry in (_NEAR, _NEXT):
        for strike in (80000.0, 80500.0):
            for leg in ("CE", "PE"):
                suffix = "26O" if expiry == _NEAR else "26N"
                rows.append(_row(
                    f"BSE:SENSEX{suffix}{int(strike)}{leg}",
                    underlying="SENSEX", inst_type=14,
                    fytoken=f"sx-{expiry}-{int(strike)}-{leg}",
                    expiry=expiry, opt_type=leg, strike=strike))
    return rows


def _excluded_rows() -> list[dict]:
    """Out-of-scope BSE derivatives the master publishes."""
    return [
        _row("BSE:BANKEX26OCTFUT", underlying="BANKEX", inst_type=11,
             fytoken="bk-fut", expiry=_NEAR),
        _row("BSE:BANKEX26OCT80000CE", underlying="BANKEX", inst_type=14,
             fytoken="bk-ce", expiry=_NEAR, opt_type="CE",
             strike=80000.0),
        _row("BSE:SENSEX50 26OCTFUT", underlying="SENSEX50", inst_type=11,
             fytoken="sx50-fut", expiry=_NEAR),
        _row("BSE:RELIANCE26OCTFUT", underlying="RELIANCE", inst_type=15,
             fytoken="stock-fut", expiry=_NEAR),
    ]


@pytest.fixture
def catalog(store):
    return InstrumentCatalog(store)


@pytest.fixture
def store():
    from core.persistence.store import EventStore
    with tempfile.TemporaryDirectory() as tmp:
        yield EventStore(str(Path(tmp) / "events.db"))


def _bse_fo(store) -> list[dict]:
    return [r for r in store.list_all_instruments()
            if r.get("provider") == "fyers" and r.get("segment") == "BSE_FO"]


def _seed(store, *rows: dict) -> None:
    from core.persistence.modules.products import (
        replace_provider_instruments_segmented_at,
    )
    replace_provider_instruments_segmented_at(
        store.db_path, "fyers", list(rows), ["BSE_FO"])


def _old_row(token: str, symbol: str, underlying: str) -> dict:
    return {
        "instrument_token": token, "exchange": "BSE",
        "tradingsymbol": symbol, "instrument_type": "FUTURE",
        "segment": "BSE_FO", "underlying": underlying,
        "option_type": None, "provider_symbol": f"BSE:{symbol}",
    }


def _fetch_master(payload: bytes, *, seen: list | None = None):
    def fetch(url):
        if seen is not None:
            seen.append(url.rsplit("/", 1)[-1])
        if url == FYERS_SEGMENT_URLS["BSE_FO"]:
            return payload
        raise OSError("not requested")
    return fetch


# -- parsing + selection ---------------------------------------------------

def test_master_parses_to_bse_fo_and_selects_sensex_only() -> None:
    records = fyers_master_records(
        _master(*_sensex_rows(), *_excluded_rows()))
    assert len(records) == 14

    kept, counts, filtered = _filter_sensex_bse_fo(records, {"BSE_FO"})
    assert counts == {"BSE_FO": 10}
    assert filtered == 4
    assert {r["underlying"] for r in kept} == SENSEX_UNDERLYINGS
    # Futures and options are classified from the provider's own metadata.
    futures = [r for r in kept if r["instrument_type"] == "FUTURE"]
    calls = [r for r in kept if r["option_type"] == "CE"]
    puts = [r for r in kept if r["option_type"] == "PE"]
    assert len(futures) == 2 and len(calls) == 4 and len(puts) == 4
    assert {r["expiry"] for r in kept} == {_NEAR, _NEXT}
    assert {r["strike"] for r in calls} == {80000.0, 80500.0}
    # Provider identity is preserved exactly.
    assert all(r["instrument_token"] for r in kept)
    assert {r["provider_symbol"] for r in futures} == {
        "BSE:SENSEX26OCTFUT", "BSE:SENSEX26NOVFUT"}


def test_sync_writes_only_sensex_and_preserves_unrelated_rows(
        catalog, store) -> None:
    _seed(store, _old_row("old-bankex", "BANKEX26OCTFUT", "BANKEX"),
          _old_row("old-stock", "TCS26OCTFUT", "TCS"))

    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*_sensex_rows(), *_excluded_rows())),
        masters=["BSE_FO"], segments={"BSE_FO"})

    assert result["ok"] is True, result.get("reason")
    rows = _bse_fo(store)
    by_symbol = {r["provider_symbol"]: r for r in rows}
    # The pre-existing unrelated rows SURVIVE the Sensex-only refresh.
    assert {"BSE:BANKEX26OCTFUT", "BSE:TCS26OCTFUT"} <= set(by_symbol)
    # Only the in-scope contracts were written.
    assert len(rows) == 12
    assert by_symbol["BSE:BANKEX26OCTFUT"]["instrument_token"] == "old-bankex"
    assert by_symbol["BSE:TCS26OCTFUT"]["instrument_token"] == "old-stock"
    assert not any(r["underlying"] in ("BANKEX", "SENSEX50", "RELIANCE")
                   and r["instrument_token"] not in ("old-bankex", "old-stock")
                   for r in rows)
    assert result["records"] == 10
    assert result["details"]["BSE_FO"]["persisted"] == 10
    assert result["scope_audit"]["in_scope"]["instruments"] == 10
    assert set(result["scope_audit"]["out_of_scope"]["underlyings"]) == {
        "BANKEX", "TCS"}


def test_sync_reports_selection_evidence(catalog, store) -> None:
    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*_sensex_rows(), *_excluded_rows())),
        masters=["BSE_FO"], segments={"BSE_FO"})

    detail = result["details"]["BSE_FO"]
    assert detail["source_records"] == 14
    assert detail["bse_fo_records"] == 14
    assert detail["matched_sensex"] == 10
    assert detail["active"] == 10 and detail["inactive"] == 0
    assert (detail["futures"], detail["options"]) == (2, 8)
    assert (detail["calls"], detail["puts"]) == (4, 4)
    assert detail["expiries"] == [_NEAR, _NEXT]
    assert detail["excluded_bse_fo"] == 4
    assert detail["excluded_by_underlying"] == {
        "BANKEX": 2, "RELIANCE": 1, "SENSEX50": 1}
    assert detail["persisted"] == 10
    # The whole master was NOT imported.
    assert result["kept"] == 10


def test_inactive_contracts_are_not_persisted(catalog, store) -> None:
    inactive = _row("BSE:SENSEX26OCT81000CE", underlying="SENSEX",
                    inst_type=14, fytoken="sx-inactive",
                    expiry=_NEAR, opt_type="CE", strike=81000.0,
                    trade_status=0)
    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*_sensex_rows(), inactive)),
        masters=["BSE_FO"], segments={"BSE_FO"})

    assert result["ok"] is True
    assert result["details"]["BSE_FO"]["inactive"] == 1
    assert result["details"]["BSE_FO"]["persisted"] == 10
    assert not any(r["tradingsymbol"].endswith("81000CE")
                   for r in _bse_fo(store))


def test_second_sync_is_idempotent(catalog, store) -> None:
    payload = _master(*_sensex_rows(), *_excluded_rows())
    catalog.sync_fyers(fetch=_fetch_master(payload), masters=["BSE_FO"],
                       segments={"BSE_FO"})
    first = sorted((r["instrument_token"], r["tradingsymbol"])
                   for r in _bse_fo(store))

    result = catalog.sync_fyers(fetch=_fetch_master(payload),
                                masters=["BSE_FO"], segments={"BSE_FO"})
    second = sorted((r["instrument_token"], r["tradingsymbol"])
                    for r in _bse_fo(store))
    assert result["ok"] is True
    assert second == first


# -- fail-safe retention ---------------------------------------------------

def test_failed_download_keeps_the_previous_rows(catalog, store) -> None:
    catalog.sync_fyers(fetch=_fetch_master(_master(*_sensex_rows())),
                       masters=["BSE_FO"], segments={"BSE_FO"})
    before = sorted(r["instrument_token"] for r in _bse_fo(store))
    assert before

    def boom(url):
        raise OSError("network down")

    result = catalog.sync_fyers(fetch=boom, masters=["BSE_FO"],
                                segments={"BSE_FO"})
    assert result["aborted"] is True
    assert result["masters"]["BSE_FO"] == "failed"
    assert sorted(r["instrument_token"] for r in _bse_fo(store)) == before


def test_zero_sensex_master_keeps_the_previous_rows(catalog, store) -> None:
    catalog.sync_fyers(fetch=_fetch_master(_master(*_sensex_rows())),
                       masters=["BSE_FO"], segments={"BSE_FO"})
    before = sorted(r["instrument_token"] for r in _bse_fo(store))

    # A master that lost the whole Sensex universe is a partial download.
    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*_excluded_rows())),
        masters=["BSE_FO"], segments={"BSE_FO"})
    assert result["aborted"] is True
    assert any("no active SENSEX contracts" in f
               for f in result["failures"]), result["failures"]
    assert sorted(r["instrument_token"] for r in _bse_fo(store)) == before


def test_malformed_sensex_row_keeps_the_previous_rows(catalog, store) -> None:
    catalog.sync_fyers(fetch=_fetch_master(_master(*_sensex_rows())),
                       masters=["BSE_FO"], segments={"BSE_FO"})
    before = sorted(r["instrument_token"] for r in _bse_fo(store))

    broken = _row("BSE:SENSEX26OCT82000CE", underlying="SENSEX",
                  inst_type=14, fytoken="sx-broken", expiry=_NEAR,
                  opt_type="CE", strike=0.0)
    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*_sensex_rows(), broken)),
        masters=["BSE_FO"], segments={"BSE_FO"})
    assert result["aborted"] is True
    assert any("strikePrice invalid" in f for f in result["failures"])
    assert sorted(r["instrument_token"] for r in _bse_fo(store)) == before


def test_duplicate_provider_tokens_keep_the_previous_rows(catalog, store
                                                          ) -> None:
    catalog.sync_fyers(fetch=_fetch_master(_master(*_sensex_rows())),
                       masters=["BSE_FO"], segments={"BSE_FO"})
    before = sorted(r["instrument_token"] for r in _bse_fo(store))

    rows = _sensex_rows()
    rows[0] = dict(rows[0], fytoken=rows[1]["fytoken"])
    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*rows)), masters=["BSE_FO"],
        segments={"BSE_FO"})
    assert result["aborted"] is True
    assert any("duplicate SENSEX provider tokens" in f
               for f in result["failures"]), result["failures"]
    assert sorted(r["instrument_token"] for r in _bse_fo(store)) == before


# -- master selector -------------------------------------------------------

def test_masters_selector_fetches_only_the_requested_master(catalog) -> None:
    seen: list[str] = []
    result = catalog.sync_fyers(
        fetch=_fetch_master(_master(*_sensex_rows()), seen=seen),
        masters=["BSE_FO"], segments={"BSE_FO"})
    assert result["ok"] is True
    assert seen == ["BSE_FO_sym_master.json"]


def test_unknown_or_disabled_master_is_rejected(catalog) -> None:
    with pytest.raises(ValueError):
        catalog.sync_fyers(fetch=_fetch_master(b"{}"),
                           masters=["NOT_A_MASTER"], segments={"BSE_FO"})
    with pytest.raises(ValueError):
        catalog.sync_fyers(fetch=_fetch_master(b"{}"),
                           masters=["BSE_FO"], segments={"NSE_FO"})
    with pytest.raises(ValueError):
        catalog.sync_fyers(fetch=_fetch_master(b"{}"), masters=[],
                           segments={"BSE_FO"})


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
