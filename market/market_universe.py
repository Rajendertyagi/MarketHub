"""Universe membership resolution shared by Breadth and Sector Heatmap.

This module is the SINGLE OWNER of "which instruments belong to a universe".
Both features call :func:`resolve_universe`, so they can never disagree about
membership (acceptance: breadth totals must reconcile with sector totals for the
same universe).

Universes
---------
* ``FNO``       — equity underlyings with live (non-expired) F&O contracts.
                  Derived from the catalog (``InstrumentCatalog.fno_universe``).
                  Authoritative and batch-cheap (one grouped query).
* ``NSE_EQ``    — all supported NSE equities (catalog-derived).
* ``NIFTY50`` / ``NIFTYNXT50`` / ``BANKNIFTY`` — curated index-constituent
  reference (``market.index_constituents``). The catalog does NOT carry index
  membership, so this curated taxonomy is the only structured source.

Every member carries the canonical MarketService storage key
(``instrument_token``) when the catalog can resolve it; if it cannot (e.g. an
index constituent absent from the synced master), ``instrument_token`` is
``None`` and the quote reader will report it as ``unavailable`` — never silently
dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from market.derivatives_universe import fno_underlying_rows

from . import index_constituents as _idx

UNIVERSE_NAMES: tuple[str, ...] = ("FNO", "NSE_EQ", "NIFTY50", "NIFTYNXT50", "BANKNIFTY")


@dataclass
class Member:
    """One instrument belonging to a universe."""

    symbol: str
    name: str | None
    exchange: str
    instrument_token: str | None


def resolve_universe(name: str, catalog: Any) -> list[Member]:
    """Resolve the member list for a universe name.

    Raises ``ValueError`` for an unknown universe (callers surface as 400).
    """
    key = (name or "").upper()
    if key == "FNO":
        return _resolve_fno(catalog)
    if key == "NSE_EQ":
        return _resolve_nse_eq(catalog)
    if key in _idx.INDEX_MEMBERS:
        return _resolve_index(key, catalog)
    raise ValueError(f"unknown universe: {name}")


def _resolve_fno(catalog: Any) -> list[Member]:
    today = date.today().isoformat()
    # Enumeration only — no contract counts needed here.
    rows = fno_underlying_rows(catalog, provider="upstox", today=today,
                               limit=2000)
    members: list[Member] = []
    for r in rows or []:
        token = r.get("equity_key")
        if not token:
            continue
        members.append(Member(r.get("symbol"), r.get("name"), "NSE", token))
    return members


def _resolve_nse_eq(catalog: Any) -> list[Member]:
    rows = catalog.equity_universe(provider="upstox", limit=5000)
    members: list[Member] = []
    for r in rows or []:
        token = r.get("instrument_token")
        if not token:
            continue
        members.append(
            Member(
                r.get("tradingsymbol"),
                r.get("name"),
                r.get("exchange") or "NSE",
                token,
            )
        )
    return members


def _resolve_index(key: str, catalog: Any) -> list[Member]:
    symbols = _idx.INDEX_MEMBERS[key]
    members: list[Member] = []
    for sym in symbols:
        token: str | None = None
        name: str | None = None
        try:
            rows = catalog.search(
                q=sym, instrument_type="EQUITY", exchange="NSE", limit=1
            )
            if rows:
                row = rows[0]
                token = row.get("instrument_token")
                name = row.get("name")
        except Exception:
            # Catalog lookup failure must not abort the whole universe; the
            # member is still eligible, just unresolved -> unavailable quote.
            pass
        members.append(Member(sym, name, "NSE", token))
    return members
