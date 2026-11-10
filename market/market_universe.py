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

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from market.derivatives_universe import fno_underlying_rows

from . import index_constituents as _idx

UNIVERSE_NAMES: tuple[str, ...] = ("FNO", "NSE_EQ", "NIFTY50", "NIFTYNXT50", "BANKNIFTY")

# Index membership is a curated constant, so its EXPENSIVE part is only the
# per-symbol catalogue resolution (one exact lookup per constituent). The
# resolved members are cached per universe as a pure function of
# (universe, catalogue epoch); an epoch change makes the old entry
# unreachable and the TTL bounds staleness for anything the epoch cannot
# observe (e.g. provider enablement).
_INDEX_CACHE_TTL_S = 300.0
_INDEX_CACHE: dict[str, tuple[int | None, float, list[Member]]] = {}
_INDEX_CACHE_LOCK = threading.Lock()


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
        return _resolve_index_cached(key, catalog)
    raise ValueError(f"unknown universe: {name}")


def _catalog_epoch(catalog: Any) -> int | None:
    """The catalogue revision, or None when the catalogue cannot report one."""
    try:
        return int(catalog.catalog_epoch())
    except Exception:
        return None


def _resolve_index_cached(key: str, catalog: Any) -> list[Member]:
    """Index members, cached by catalogue epoch.

    Concurrent readers may compute the same list twice; that is harmless
    because the value is a pure function of (universe, epoch) and the cache
    slot is replaced atomically with a fully-built list. Callers receive a
    copy, so a consumer mutating its list cannot corrupt the cached one.
    """
    epoch = _catalog_epoch(catalog)
    if epoch is None:
        # Without an epoch the cache could serve members resolved against a
        # catalogue that has since been replaced; resolve fresh instead.
        return _resolve_index(key, catalog)
    now = time.monotonic()
    with _INDEX_CACHE_LOCK:
        hit = _INDEX_CACHE.get(key)
        if hit is not None and hit[0] == epoch \
                and now - hit[1] < _INDEX_CACHE_TTL_S:
            return list(hit[2])
    members = _resolve_index(key, catalog)
    with _INDEX_CACHE_LOCK:
        _INDEX_CACHE[key] = (epoch, now, members)
    return list(members)


def _resolve_fno(catalog: Any) -> list[Member]:
    today = date.today().isoformat()
    # Enumeration only — provider-neutral membership.
    rows = fno_underlying_rows(catalog, provider=None, today=today,
                               limit=5000)
    members: list[Member] = []
    for r in rows or []:
        token = r.get("equity_key")
        if not token:
            continue
        members.append(Member(r.get("symbol"), r.get("name"), "NSE", token))
    return members


def _resolve_nse_eq(catalog: Any) -> list[Member]:
    # Prefer Upstox equity rows for stable storage keys when available.
    rows = catalog.equity_universe(provider="upstox", limit=5000)
    if not rows:
        rows = catalog.equity_universe(provider=None, limit=5000)
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
            row = _exact_equity_row(catalog, sym)
            if row:
                token = _quote_key(catalog, row)
                name = row.get("name")
        except Exception:
            # Catalog lookup failure must not abort the whole universe; the
            # member is still eligible, just unresolved -> unavailable quote.
            pass
        members.append(Member(sym, name, "NSE", token))
    return members


def _exact_equity_row(catalog: Any, symbol: str) -> dict[str, Any] | None:
    """The EXACT catalogue row for one index constituent.

    The general catalogue search is a substring LIKE with a LIMIT, so a
    short symbol ("LT") matches an unrelated equity ("20 MICRONS LTD")
    and a long one ("RELIANCE") matches an invitation/bearer variant
    ("RCOM"). The EXACT-symbol read model is used instead.

    The primary streaming provider's row is preferred, so the member's
    quote key is the one the live feed actually writes under; the quote
    reader still finds the instrument under ANY provider's key.
    """
    finder = getattr(catalog, "find_equity", None)
    if finder is None:
        return None
    for provider in ("upstox", None):
        try:
            row = finder(symbol, provider=provider, exchange="NSE")
        except TypeError:
            try:
                row = finder(symbol)
            except Exception:
                row = None
        except Exception:
            row = None
        if row:
            return row
    return None


def _quote_key(catalog: Any, row: dict[str, Any]) -> str | None:
    """The key under which this row's QUOTE STATE is stored.

    The catalogue owns the provider-native identifier roles, so the key is
    asked of the catalogue - never derived from a key's string shape.
    """
    key_of = getattr(catalog, "quote_key", None)
    if key_of is not None:
        try:
            return key_of(row)
        except Exception:
            return None
    return row.get("provider_symbol") or row.get("instrument_token")
