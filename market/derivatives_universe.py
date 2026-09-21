"""Dynamic F&O derivatives universe — catalog-driven, broker-neutral.

The set of underlyings that have listed F&O contracts is DERIVED, never
hardcoded:

    canonical six F&O indices (market.index_registry)
        +
    stock underlyings discovered from the instruments catalog
        (``catalog.fno_universe`` — live, non-expired F&O membership)

Expiries and contracts are always catalog-derived. No broker is referenced,
no provider key is hardcoded, and this module imports neither ``app`` nor
``brokers`` (architecture boundary: canonical market layer).

Scope: NSE equity derivatives (indices + stock F&O). Provider-specific feed
keys are resolved by the app/routing layer from the catalog, never here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from market.index_registry import CANONICAL_INDICES

KIND_INDEX = "index"
KIND_EQUITY = "equity"

FUTURE = "FUTURE"
OPTION = "OPTION"

NSE = "NSE"


# ---------------------------------------------------------------------------
# Canonical derived F&O read model (cache + single-flight)
# ---------------------------------------------------------------------------
#
# Catalog-derived F&O reads are produced ONCE and reused by every consumer
# (workspace, coverage, futures board/heatmap) instead of being rebuilt per
# request through per-underlying / per-expiry searches:
#
#   * the bulk futures universe — ONE ``list_future_contracts`` read, indexed
#     by (underlying, expiry);
#   * listed expiries per (underlying, class).
#
# The cache is keyed by the CATALOG EPOCH, so it is invalidated from the
# catalog-write boundary (provider sync) rather than by an arbitrary request
# TTL; the TTL is only a safety net. Concurrent identical reads coalesce on a
# per-key lock — the same single-flight pattern used by market.futures_board.
#
# When no epoch is available (e.g. a lightweight catalog double) reads are not
# cached — correctness over caching.

_UNIVERSE_TTL_SECONDS = 300.0

_cache: dict[Any, tuple[float, Any]] = {}
_cache_locks: dict[Any, threading.Lock] = {}
_cache_guard = threading.Lock()


def _catalog_version(catalog: Any) -> Any:
    """Catalog epoch when the catalog exposes one, else ``None``."""
    fn = getattr(catalog, "catalog_epoch", None)
    try:
        return fn() if callable(fn) else None
    except Exception:  # noqa: BLE001 — a broken epoch must never break reads
        return None


def _lock_for(key: Any) -> threading.Lock:
    with _cache_guard:
        lock = _cache_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _cache_locks[key] = lock
        return lock


def _cached(key: Any, version: Any, build: Any,
            ttl: float = _UNIVERSE_TTL_SECONDS) -> Any:
    """TTL cache with single-flight. Uncached when ``version`` is None."""
    if version is None:
        return build()
    now = time.monotonic()
    with _cache_guard:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    lock = _lock_for(key)
    with lock:
        with _cache_guard:
            hit = _cache.get(key)
            if hit is not None and time.monotonic() - hit[0] < ttl:
                return hit[1]
        value = build()
        with _cache_guard:
            _cache[key] = (time.monotonic(), value)
        return value


def _build_futures_index(catalog: Any, *,
                         exchange: str) -> dict[tuple[str, str], list[dict]]:
    rows = catalog.list_future_contracts(
        exchange=exchange, limit=20000) or []
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        und = str(row.get("underlying") or "").strip().upper()
        exp = str(row.get("expiry") or "")
        if not und or not exp:
            continue
        index.setdefault((und, exp), []).append(row)
    return index


def futures_index(catalog: Any, *, exchange: str = NSE,
                  version: Any = None,
                  ttl: float = _UNIVERSE_TTL_SECONDS,
                  ) -> dict[tuple[str, str], list[dict]]:
    """Bulk futures index ``(underlying, expiry) -> [catalog rows]``.

    Built from ONE ``list_future_contracts`` read and reused by all F&O
    consumers. Cached + single-flighted when a catalog epoch is available.
    """
    if version is None:
        version = _catalog_version(catalog)
    return _cached(
        ("futures", exchange, version), version,
        lambda: _build_futures_index(catalog, exchange=exchange), ttl)


def _option_expiries(catalog: Any, underlying: str, instrument_type: str,
                     exchange: str) -> list[str]:
    return list(catalog.derivative_expiries(
        underlying, instrument_type, exchange=exchange) or [])


def clear_universe_cache() -> None:
    """Drop cached derived reads (catalog sync / diagnostics / tests)."""
    with _cache_guard:
        _cache.clear()


@dataclass(frozen=True)
class Underlying:
    """One canonical F&O underlying (no provider-specific fields)."""

    symbol: str          # canonical underlying key, e.g. "NIFTY", "RELIANCE"
    kind: str            # KIND_INDEX | KIND_EQUITY
    exchange: str        # canonical base exchange (NSE)


@dataclass(frozen=True)
class Contract:
    """One catalog-derived F&O contract.

    ``provider_token`` is the catalog's provider instrument token (data, not
    an assumption) — callers route by ``provider``.
    """

    underlying: str
    kind: str
    exchange: str
    expiry: str
    instrument_type: str          # FUTURE | OPTION
    provider: str | None
    provider_token: str | None
    tradingsymbol: str | None = None
    lot_size: int | None = None
    strike: float | None = None
    option_type: str | None = None


def index_underlyings() -> list[Underlying]:
    """The canonical six F&O index underlyings (from the neutral registry)."""
    return [
        Underlying(symbol=e.label, kind=KIND_INDEX, exchange=e.exchange)
        for e in CANONICAL_INDICES if e.is_fno
    ]


def _read_fno_underlying_rows(catalog: Any, *, provider: str, today: str,
                              limit: int) -> list[dict[str, Any]]:
    """Read the enumeration model, falling back to the count model.

    Catalogs that predate the split (test doubles, injected fakes) implement
    only ``fno_universe``; both return the same membership.
    """
    fn = getattr(catalog, "fno_underlying_rows", None)
    if callable(fn):
        return list(fn(provider=provider, today=today, limit=limit) or [])
    return list(catalog.fno_universe(provider=provider, today=today,
                                     limit=limit) or [])


def fno_underlying_rows(catalog: Any, *, provider: str,
                        today: str | None = None,
                        limit: int = 2000) -> list[dict[str, Any]]:
    """Canonical, cached ENUMERATION of F&O stock underlyings (no counts).

    Callers that only need universe membership use this instead of
    ``fno_universe``, which additionally aggregates contract counts. Memoized
    per (provider, day, catalog epoch) at the canonical read-model boundary.
    """
    day = today or date.today().isoformat()
    version = _catalog_version(catalog)
    return _cached(
        ("fno_underlying_rows", provider, day, int(limit), version), version,
        lambda: _read_fno_underlying_rows(catalog, provider=provider,
                                          today=day, limit=limit))


def stock_underlyings(catalog: Any, *, provider: str,
                      today: str | None = None,
                      limit: int = 2000) -> list[Underlying]:
    """Stock F&O underlyings from the canonical enumeration read model.

    ``provider`` is REQUIRED — the caller decides which provider's catalog
    rows to enumerate; there is no hardcoded default provider.
    """
    rows = fno_underlying_rows(catalog, provider=provider, today=today,
                               limit=limit)
    out: list[Underlying] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        out.append(Underlying(symbol=symbol, kind=KIND_EQUITY, exchange=NSE))
    return out


def fno_underlyings(catalog: Any, *, provider: str,
                    today: str | None = None,
                    limit: int = 2000) -> list[Underlying]:
    """All F&O underlyings: six canonical indices + catalog stock underlyings."""
    indices = index_underlyings()
    seen = {u.symbol for u in indices}
    stocks = [u for u in stock_underlyings(
        catalog, provider=provider, today=today, limit=limit)
        if u.symbol not in seen]
    return indices + stocks


def expiries_for(catalog: Any, underlying: str, instrument_type: str,
                 *, exchange: str = NSE) -> list[str]:
    """Listed expiries for an underlying + class (catalog-derived).

    Futures expiries come from the canonical bulk futures index (no
    per-underlying query). Option expiries use the catalog's indexed lookup
    with the canonical exchange so ``idx_instr_lookup`` is used instead of a
    full catalog scan.
    """
    symbol = underlying.strip().upper()
    if instrument_type == FUTURE:
        idx = futures_index(catalog, exchange=exchange)
        return sorted({exp for (und, exp) in idx if und == symbol})
    # Option expiries: index-backed lookup, memoized per catalog epoch so the
    # several identical reads inside one workspace request coalesce.
    version = _catalog_version(catalog)
    return _cached(
        ("expiries", exchange, version, symbol, instrument_type), version,
        lambda: _option_expiries(catalog, underlying, instrument_type,
                                 exchange))


def _contract(row: dict[str, Any], *, underlying: str, kind: str,
              instrument_type: str) -> Contract:
    return Contract(
        underlying=underlying,
        kind=kind,
        exchange=str(row.get("exchange") or NSE),
        expiry=str(row.get("expiry") or ""),
        instrument_type=instrument_type,
        provider=row.get("provider"),
        provider_token=(row.get("provider_symbol")
                        or row.get("instrument_token")),
        tradingsymbol=row.get("tradingsymbol"),
        lot_size=row.get("lot_size"),
        strike=row.get("strike"),
        option_type=row.get("option_type"),
    )


def futures_for(catalog: Any, underlying: str, expiry: str,
                kind: str = KIND_EQUITY, *, exchange: str = NSE,
                ) -> list[Contract]:
    """Futures contracts for one underlying + expiry (catalog-derived).

    Served from the canonical bulk futures index — no per-expiry search.
    """
    rows = futures_index(catalog, exchange=exchange).get(
        (underlying.strip().upper(), expiry), [])
    return [_contract(r, underlying=underlying, kind=kind,
                      instrument_type=FUTURE) for r in rows]


def options_for(catalog: Any, underlying: str, expiry: str,
                kind: str = KIND_EQUITY, *, exchange: str = NSE,
                ) -> list[Contract]:
    """Option contracts (CE/PE) for one underlying + expiry (catalog-derived)."""
    rows = catalog.option_strikes(underlying, expiry, exchange=exchange) or []
    return [_contract(r, underlying=underlying, kind=kind,
                      instrument_type=OPTION) for r in rows]
