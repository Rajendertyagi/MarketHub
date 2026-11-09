#!/usr/bin/env python3
"""Canonical quote lookup parity (one identity, every consumer).

Proves:

  * a quote is found by ANY known identifier - the canonical id, the
    provider storage key, the market identity or the ISIN;
  * a quote served by ONE provider is found by a consumer that holds
    ANOTHER provider's identifier (the consumer never assumes which
    provider served the instrument);
  * the single read and the bulk read agree (same price, same provider,
    same freshness) and the instrument appears ONCE in the bulk read;
  * the payload carries the provider-neutral canonical id; the provider
    native key stays a storage key.

No network, no broker, no live database: isolated doubles only.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from market.models import Quote


def _quote(token: str, ltp: float, *, exchange: str = "NSE",
           tradingsymbol: str = "NIFTY", age_seconds: float = 0.0,
           close: float | None = None,
           canonical_id: str | None = None) -> Quote:
    return Quote(
        instrument_token=token,
        exchange=exchange,
        tradingsymbol=tradingsymbol,
        received_ts=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        ltp=ltp,
        close=close,
        canonical_id=canonical_id,
    )


class _Store:
    """A quote store double keyed by (exchange, storage key)."""

    def __init__(self, quotes):
        self._quotes = list(quotes)

    async def quotes(self):
        return tuple(self._quotes)

    def get_quote_now(self, exchange, instrument_token):
        for q in self._quotes:
            if (q.exchange == exchange
                    and q.instrument_token == instrument_token):
                return q
        return None


class _Catalog:
    """A catalogue double: the Upstox and the Fyers row of one index."""

    def __init__(self, rows):
        self._rows = list(rows)

    def _row(self, key):
        for row in self._rows:
            if key in (row.get("instrument_token"),
                       row.get("provider_symbol"),
                       row.get("isin")):
                return row
        return None

    def get_by_key(self, key):
        return self._row(key)

    def get(self, provider, token):
        row = self._row(token)
        if row and row.get("provider") == provider:
            return row
        return None

    def search(self, **kw):
        isin = kw.get("isin")
        if not isin:
            return []
        return [r for r in self._rows if r.get("isin") == isin]

    def find_canonical_rows(self, canonical_id):
        from app.canonical_identity import canonical_id_for_row
        out = []
        for row in self._rows:
            try:
                if canonical_id_for_row(row) == canonical_id:
                    out.append(row)
            except Exception:
                continue
        return out


def _index_rows():
    return [
        {"provider": "upstox", "instrument_token": "NSE_INDEX|Nifty 50",
         "provider_symbol": "NSE_INDEX|Nifty 50", "tradingsymbol": "NIFTY",
         "exchange": "NSE", "instrument_type": "INDEX", "underlying": None},
        {"provider": "fyers", "instrument_token": "101NIFTY",
         "provider_symbol": "NSE:NIFTY50-INDEX",
         "tradingsymbol": "NSE:NIFTY50-INDEX", "exchange": "NSE",
         "instrument_type": "INDEX", "underlying": "NIFTY"},
    ]


def _equity_rows():
    return [
        {"provider": "upstox", "instrument_token": "NSE_EQ|INE040A01034",
         "provider_symbol": "NSE_EQ|INE040A01034",
         "tradingsymbol": "HDFCBANK-EQ", "exchange": "NSE",
         "instrument_type": "EQ", "isin": "INE040A01034"},
    ]


def _service(quotes, rows):
    from app.quote_service import QuoteService
    return QuoteService(_Store(quotes), catalog=_Catalog(rows))


def test_a_quote_is_found_by_the_canonical_id() -> None:
    svc = _service([_quote("NSE_INDEX|Nifty 50", 24500.0)], _index_rows())
    payload = asyncio.run(svc.quote("NSE:INDEX:NIFTY"))
    assert payload is not None
    assert payload["ltp"] == 24500.0
    assert payload["canonical_id"] == "NSE:INDEX:NIFTY"
    assert payload["storage_key"] == "NSE_INDEX|Nifty 50"


def test_a_quote_is_found_by_the_isin() -> None:
    """The ISIN is the cross-provider identity of a cash instrument."""
    svc = _service(
        [_quote("NSE_EQ|INE040A01034", 1900.0, tradingsymbol="HDFCBANK-EQ")],
        _equity_rows())
    payload = asyncio.run(svc.quote("INE040A01034"))
    assert payload is not None, "the ISIN must resolve to the quote"
    assert payload["ltp"] == 1900.0
    assert payload["canonical_id"] == "NSE:EQUITY:INE040A01034"


def test_a_quote_served_by_another_provider_is_still_found() -> None:
    """The consumer holds Upstox's key; the quote came from Fyers."""
    svc = _service(
        [_quote("NSE:NIFTY50-INDEX", 24510.0, tradingsymbol="NSE:NIFTY50-INDEX")],
        _index_rows())
    payload = asyncio.run(svc.quote("NSE_INDEX|Nifty 50"))
    assert payload is not None, (
        "the instrument must be found regardless of the serving provider")
    assert payload["ltp"] == 24510.0
    assert payload["canonical_id"] == "NSE:INDEX:NIFTY"


def test_single_and_bulk_reads_agree() -> None:
    """One instrument, one answer - whatever the reference is."""
    svc = _service([
        _quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=5),
        _quote("NSE:NIFTY50-INDEX", 24510.0, age_seconds=1),
    ], _index_rows())
    single = asyncio.run(svc.quote("NSE:INDEX:NIFTY"))
    bulk = asyncio.run(svc.quotes())
    assert single is not None
    assert len(bulk) == 1, "one instrument, one bulk entry"
    assert bulk[0]["ltp"] == single["ltp"]
    assert bulk[0]["provider"] == single["provider"]
    assert bulk[0]["stale"] == single["stale"]
    assert bulk[0]["canonical_id"] == single["canonical_id"]


def test_breadth_finds_a_quote_stored_under_another_providers_key() -> None:
    """The breadth consumer must not report a quoted instrument unavailable."""
    from market.breadth import compute_breadth

    svc = _service(
        [_quote("NSE:NIFTY50-INDEX", 24510.0, close=24400.0,
                tradingsymbol="NSE:NIFTY50-INDEX")],
        _index_rows())

    class _Member:
        exchange = "NSE"
        instrument_token = "NSE_INDEX|Nifty 50"
        symbol = "NIFTY"
        name = "Nifty 50"

    def _reader(exchange, token):
        return svc.quote_now(token, exchange)

    snap = compute_breadth("NIFTY50", [_Member()], _reader)
    assert snap.quoted == 1, (
        "a quote that exists must be found by the breadth consumer")
    assert snap.unavailable == 0


def test_a_stale_quote_is_not_live() -> None:
    svc = _service([_quote("NSE_INDEX|Nifty 50", 24500.0, age_seconds=3600)],
                   _index_rows())
    payload = asyncio.run(svc.quote("NSE:INDEX:NIFTY"))
    assert payload is not None
    assert payload["stale"] is True
    assert payload["live"] is False
    assert payload["as_of"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
