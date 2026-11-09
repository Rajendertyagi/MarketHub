"""Shared fakes for Market Breadth / Sector Heatmap tests.

Keeps the canonical aggregation engine deterministic and independent of any
live feed, broker, or populated database. The fakes mirror the real contracts:

* ``FakeQuoteReader`` behaves like ``MarketService.get_quote_now``:
  ``(exchange, instrument_token) -> Quote | None``.
* ``FakeCatalog`` behaves like ``InstrumentCatalog`` for the two methods the
  universe resolver uses (``fno_universe``, ``equity_universe``, ``search``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from market.market_universe import Member
from market.models import Quote


def make_quote(
    token: str,
    *,
    ltp: float | None = None,
    close: float | None = None,
    change: float | None = None,
    change_percent: float | None = None,
    volume: int | None = None,
    high: float | None = None,
    low: float | None = None,
    received_ts: datetime | None = None,
) -> Quote:
    """Build a canonical Quote, deriving change from ltp/close when omitted."""
    if received_ts is None:
        received_ts = datetime(2026, 1, 1, 9, 30, tzinfo=timezone.utc)
    if change is None and ltp is not None and close is not None and close != 0:
        change = ltp - close
        if change_percent is None:
            change_percent = (change / close) * 100.0
    return Quote(
        instrument_token=token,
        exchange="NSE",
        tradingsymbol=token.split("|")[-1] if token else "",
        received_ts=received_ts,
        ltp=ltp,
        close=close,
        change=change,
        change_percent=change_percent,
        volume=volume,
        high=high,
        low=low,
    )


class FakeQuoteReader:
    """Maps instrument_token -> Quote; returns None for unknown tokens."""

    def __init__(self, quotes: dict[str, Quote]) -> None:
        self._quotes = quotes

    def get_quote_now(self, exchange: str, token: str) -> Quote | None:
        return self._quotes.get(token)


class FakeCatalog:
    """Minimal InstrumentCatalog stand-in for universe resolution.

    ``find_equity`` mirrors the real EXACT-symbol read model: the general
    ``search`` is a substring LIKE, so a short symbol ("LT") can match an
    unrelated equity ("20 MICRONS LTD"). The exact read model only
    accepts a row whose ``tradingsymbol``/``underlying`` IS the symbol.
    """

    def __init__(
        self,
        fno: list[dict[str, Any]] | None = None,
        equities: list[dict[str, Any]] | None = None,
        search_map: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._fno = fno or []
        self._equities = equities or []
        self._search = search_map or {}

    def fno_universe(self, provider, today, limit=2000):
        return self._fno

    def equity_universe(self, provider=None, limit=5000):
        return self._equities

    def search(self, **kw):
        q = (kw.get("q") or "").upper()
        return self._search.get(q, [])

    def find_equity(self, symbol, *, provider=None, exchange=None):
        want = (symbol or "").strip().upper()
        rows = self._search.get(want, [])
        exact = [r for r in rows
                 if str(r.get("tradingsymbol") or "").upper() == want
                 or str(r.get("underlying") or "").upper() == want]
        for row in (exact or rows):
            if provider and row.get("provider") \
                    and str(row["provider"]) != provider:
                continue
            if exchange and row.get("exchange") \
                    and str(row["exchange"]) != exchange:
                continue
            return row
        return None

    def quote_key(self, row):
        """The key under which this row's quote state is stored."""
        return row.get("provider_symbol") or row.get("instrument_token")


def members_from_symbols(symbols: list[str], tokens: dict[str, str | None] | None = None):
    """Build Member list for unit tests (tokens default to NSE_EQ|<SYM>)."""
    out = []
    for sym in symbols:
        tok = (tokens or {}).get(sym, f"NSE_EQ|{sym}")
        out.append(Member(sym, sym.title(), "NSE", tok))
    return out
