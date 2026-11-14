"""Shared Futures Board projection (canonical, broker-neutral).

ONE unfiltered snapshot per expiry over routed canonical contracts. The board
requests **canonical market data only** — it never constructs provider keys,
selects brokers, or references Upstox/Fyers. Provider routing stays below the
board (the caller passes routed contracts as :class:`BoardInput`).

Per tile: identity, spot, futures LTP / price %, OI, OI change / OI%,
buildup, basis, coverage state, OI basis (provider/persisted/unavailable),
previous-session reference, received timestamp.

Filtering/sorting is a projection concern (:func:`filter_board`) over the one
snapshot — never a separate fetch.

No ``app``/``brokers`` imports (canonical market boundary).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from market.analytics.option_chain import classify_buildup
from market.breadth import effective_change
from market.coverage_states import (
    LIVE,
    NO_DATA,
    ROUTING_STATES,
    STALE,
    UNSUBSCRIBED,
)
from market.oi import BASIS_UNAVAILABLE, oi_change_and_pct, quote_field

logger = logging.getLogger(__name__)

FUTURE = "FUTURE"
SPOT = "SPOT"

STALE_MINUTES = 5.0
CACHE_TTL_SECONDS = 20.0

# Public buildup filter value -> authoritative label (None = no filter).
BUILDUP_FILTERS: dict[str, str | None] = {
    "all": None,
    "long": "Long Buildup",
    "short": "Short Buildup",
    "shortcovering": "Short Covering",
    "longunwinding": "Long Unwinding",
}

QuoteReader = Callable[[str, str], Any]
PreviousOiReader = Callable[[str, str, str, str], float | None]


@dataclass(frozen=True)
class BoardInput:
    """One routed canonical contract handed to the board.

    ``provider``/``provider_token``/``coverage_state`` come from routing; the
    board only reads canonical quotes through ``quote_reader``.
    """

    underlying: str
    kind: str
    exchange: str
    expiry: str
    instrument_type: str
    provider: str | None = None
    provider_token: str | None = None
    coverage_state: str = UNSUBSCRIBED


@dataclass
class FuturesTile:
    symbol: str
    kind: str
    exchange: str
    underlying: str
    expiry: str
    provider: str | None = None
    instrument_key: str | None = None
    futures_ltp: float | None = None
    price_pct: float | None = None
    oi: float | int | None = None
    previous_oi: float | None = None
    oi_change: float | None = None
    oi_pct: float | None = None
    buildup: str | None = None
    spot_ltp: float | None = None
    basis: float | None = None
    coverage: str = UNSUBSCRIBED
    oi_basis: str = BASIS_UNAVAILABLE
    received_ts: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "kind": self.kind,
            "exchange": self.exchange, "underlying": self.underlying,
            "expiry": self.expiry, "provider": self.provider,
            "instrument_key": self.instrument_key,
            "futures_ltp": self.futures_ltp, "price_pct": self.price_pct,
            "oi": self.oi, "previous_oi": self.previous_oi,
            "oi_change": self.oi_change, "oi_pct": self.oi_pct,
            "buildup": self.buildup, "spot_ltp": self.spot_ltp,
            "basis": self.basis, "coverage": self.coverage,
            "oi_basis": self.oi_basis, "received_ts": self.received_ts,
        }


@dataclass
class FuturesBoardSnapshot:
    expiry_requested: str
    expiry_resolved: str | None
    expiries: list[str] = field(default_factory=list)
    eligible: int = 0
    quoted: int = 0
    unavailable: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)
    as_of: str | None = None
    data_status: str = "live"
    items: list[FuturesTile] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "expiry_requested": self.expiry_requested,
            "expiry_resolved": self.expiry_resolved,
            "expiries": list(self.expiries),
            "eligible": self.eligible, "quoted": self.quoted,
            "unavailable": self.unavailable,
            "class_counts": dict(self.class_counts),
            "as_of": self.as_of, "data_status": self.data_status,
            "items": [t.to_dict() for t in self.items],
        }


# -- helpers -----------------------------------------------------------------

def _to_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_iso(value: Any) -> str | None:
    dt = _to_dt(value)
    return dt.isoformat() if dt is not None else (
        str(value) if value is not None else None)


def _build_tile(c: BoardInput, spot: BoardInput | None, *,
                quote_reader: QuoteReader,
                previous_oi_reader: PreviousOiReader | None,
                now: datetime, stale_minutes: float) -> FuturesTile:
    tile = FuturesTile(
        symbol=c.underlying, kind=c.kind, exchange=c.exchange,
        underlying=c.underlying, expiry=c.expiry, provider=c.provider,
        instrument_key=c.provider_token, coverage=c.coverage_state)

    quote = None
    if c.provider_token:
        try:
            quote = quote_reader(c.exchange, c.provider_token)
        except Exception:
            quote = None

    if c.coverage_state in ROUTING_STATES:
        pass  # routing-level state carried through unchanged
    elif quote is None:
        tile.coverage = NO_DATA if c.provider_token else UNSUBSCRIBED
    else:
        tile.received_ts = _to_iso(quote_field(quote, "received_ts"))
        recv = _to_dt(quote_field(quote, "received_ts"))
        tile.coverage = (
            STALE if recv is not None
            and (now - recv).total_seconds() / 60.0 > stale_minutes else LIVE)

    if quote is not None:
        _ch, pct, has = effective_change(quote)
        ltp = quote_field(quote, "ltp")
        tile.futures_ltp = ltp
        tile.price_pct = pct if has else None

        persisted = None
        if quote_field(quote, "previous_oi") is None \
                and previous_oi_reader is not None:
            try:
                persisted = previous_oi_reader(
                    c.exchange, c.underlying, c.expiry, FUTURE)
            except Exception:
                persisted = None
        oi, prev, change, oi_pct, oi_basis = oi_change_and_pct(
            quote, persisted_previous_oi=persisted)
        tile.oi, tile.previous_oi = oi, prev
        tile.oi_change, tile.oi_pct, tile.oi_basis = change, oi_pct, oi_basis

        price_delta = quote_field(quote, "change")
        if price_delta is None:
            close = quote_field(quote, "close")
            if ltp is not None and close not in (None, 0):
                price_delta = ltp - close
        if change is not None and price_delta is not None:
            tile.buildup = classify_buildup(change, price_delta)

    if spot is not None and spot.provider_token:
        try:
            sq = quote_reader(spot.exchange, spot.provider_token)
        except Exception:
            sq = None
        if sq is not None:
            tile.spot_ltp = quote_field(sq, "ltp")
    if tile.futures_ltp is not None and tile.spot_ltp is not None:
        tile.basis = tile.futures_ltp - tile.spot_ltp
    return tile


# -- projection --------------------------------------------------------------

def compute_futures_board(
    inputs: Sequence[BoardInput], *, expiry: str,
    quote_reader: QuoteReader,
    previous_oi_reader: PreviousOiReader | None = None,
    stale_minutes: float = STALE_MINUTES,
    expiries: Sequence[str] | None = None,
) -> FuturesBoardSnapshot:
    """One UNFILTERED snapshot for the given expiry (all routed futures)."""
    now = datetime.now(timezone.utc)
    spots: dict[str, BoardInput] = {}
    futures: list[BoardInput] = []
    for c in inputs:
        if c.instrument_type == SPOT:
            spots.setdefault(c.underlying, c)
        elif c.instrument_type == FUTURE:
            futures.append(c)

    tiles: list[FuturesTile] = []
    counts: dict[str, int] = {}
    quoted = unavailable = 0
    newest: datetime | None = None
    for c in futures:
        tile = _build_tile(
            c, spots.get(c.underlying), quote_reader=quote_reader,
            previous_oi_reader=previous_oi_reader, now=now,
            stale_minutes=stale_minutes)
        tiles.append(tile)
        if tile.coverage in (LIVE, STALE):
            quoted += 1
            if tile.buildup:
                counts[tile.buildup] = counts.get(tile.buildup, 0) + 1
        else:
            unavailable += 1
        recv = _to_dt(tile.received_ts)
        if recv is not None and (newest is None or recv > newest):
            newest = recv

    data_status = "live"
    if newest is not None and \
            (now - newest).total_seconds() / 60.0 > stale_minutes:
        data_status = "stale"

    return FuturesBoardSnapshot(
        expiry_requested=expiry, expiry_resolved=expiry,
        expiries=list(expiries or []), eligible=len(futures), quoted=quoted,
        unavailable=unavailable, class_counts=counts, as_of=now.isoformat(),
        data_status=data_status, items=tiles)


def filter_board(
    snapshot: FuturesBoardSnapshot, *, buildup: str | None = None,
    symbols: Sequence[str] | None = None,
) -> FuturesBoardSnapshot:
    """Pure projection filter over ONE snapshot (no re-fetch)."""
    wanted_label = BUILDUP_FILTERS.get(buildup) if buildup else None
    wanted_symbols = ({str(s).upper() for s in symbols} if symbols else None)
    items = [
        t for t in snapshot.items
        if (wanted_label is None or t.buildup == wanted_label)
        and (wanted_symbols is None or t.underlying in wanted_symbols)
    ]
    return FuturesBoardSnapshot(
        expiry_requested=snapshot.expiry_requested,
        expiry_resolved=snapshot.expiry_resolved,
        expiries=list(snapshot.expiries), eligible=snapshot.eligible,
        quoted=sum(1 for t in items if t.coverage in (LIVE, STALE)),
        unavailable=sum(1 for t in items
                        if t.coverage not in (LIVE, STALE)),
        class_counts=dict(snapshot.class_counts), as_of=snapshot.as_of,
        data_status=snapshot.data_status, items=items)


# -- TTL cache + single-flight -----------------------------------------------

_cache: dict[Any, tuple[float, FuturesBoardSnapshot]] = {}
_cache_lock = threading.Lock()
_build_locks: dict[Any, dict[str, Any]] = {}
_build_locks_guard = threading.Lock()


def _signature(inputs: Sequence[BoardInput], expiry: str) -> tuple[Any, ...]:
    return (str(expiry), tuple(sorted(
        (c.underlying, c.instrument_type, c.provider or "",
         c.provider_token or "") for c in inputs)))


def _acquire_build_lock(key: Any) -> tuple[threading.Lock, Callable[[], None]]:
    """Return (lock, release) for a shared single-flight build lock.

    Ref-counted so unused lock entries are removed when no caller is using or
    waiting on the key, without replacing an in-flight lock.
    """
    with _build_locks_guard:
        entry = _build_locks.get(key)
        if entry is None:
            entry = {"lock": threading.Lock(), "refs": 0}
            _build_locks[key] = entry
        entry["refs"] += 1
        lock = entry["lock"]

    def _release() -> None:
        with _build_locks_guard:
            cur = _build_locks.get(key)
            if cur is not entry:
                return
            cur["refs"] -= 1
            if cur["refs"] <= 0:
                _build_locks.pop(key, None)

    return lock, _release


def _prune_cache_locked(now: float, ttl: float) -> None:
    expired = [k for k, (ts, _) in _cache.items() if now - ts >= ttl]
    for k in expired:
        _cache.pop(k, None)


def get_futures_board(
    inputs: Sequence[BoardInput], *, expiry: str,
    quote_reader: QuoteReader,
    previous_oi_reader: PreviousOiReader | None = None,
    stale_minutes: float = STALE_MINUTES,
    ttl: float = CACHE_TTL_SECONDS,
    expiries: Sequence[str] | None = None,
) -> FuturesBoardSnapshot:
    """TTL-cached board with single-flight protection (one build per key)."""
    key = _signature(inputs, expiry)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        _prune_cache_locked(now, ttl)
    lock, release = _acquire_build_lock(key)
    try:
        with lock:
            now = time.monotonic()
            with _cache_lock:
                hit = _cache.get(key)
                if hit is not None and now - hit[0] < ttl:
                    return hit[1]
            snap = compute_futures_board(
                inputs, expiry=expiry, quote_reader=quote_reader,
                previous_oi_reader=previous_oi_reader,
                stale_minutes=stale_minutes, expiries=expiries)
            with _cache_lock:
                _cache[key] = (time.monotonic(), snap)
                _prune_cache_locked(time.monotonic(), ttl)
            return snap
    finally:
        release()


def clear_board_cache() -> None:
    """Drop cached snapshots (diagnostics / tests)."""
    with _cache_lock:
        _cache.clear()
