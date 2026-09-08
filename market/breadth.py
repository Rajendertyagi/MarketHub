"""Canonical Market Breadth aggregation.

Pure over:
  * a list of universe ``Member`` (from ``market_universe.resolve_universe``)
  * a quote reader: ``callable(exchange, instrument_token) -> quote | None``
    — in production this is ``MarketService.get_quote_now``.

No network, no provider code, no UI. Deterministic and unit-testable, so the
WebUI and REST layer only serialize the result (aggregation logic lives here,
never in a route handler).

Movement rule (explicit, per mission §4)
----------------------------------------
    change > 0  -> advance
    change < 0  -> decline
    change == 0 -> unchanged
    no valid quote -> unavailable

``unavailable`` is NEVER counted as unchanged.

Quote freshness
---------------
The reader returns canonical quotes; ``received_ts`` is preserved so callers can
label stale (last-session) vs live. Breadth itself is computed on whatever
canonical state exists (works when the market is closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .sector_classification import UNCLASSIFIED, sector_for_symbol

# Reader signature: (exchange, instrument_token) -> quote-like | None
QuoteReader = Callable[[str, str], Any]

_STATUS_ADVANCE = "advance"
_STATUS_DECLINE = "decline"
_STATUS_UNCHANGED = "unchanged"
_STATUS_UNAVAILABLE = "unavailable"


def _field(quote: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Quote (frozen dataclass) or a plain dict."""
    if quote is None:
        return default
    if isinstance(quote, dict):
        return quote.get(name, default)
    return getattr(quote, name, default)


def effective_change(quote: Any) -> tuple[float | None, float | None, bool]:
    """Return ``(change, change_percent, has_quote)`` from a canonical quote.

    Prefers the provider-reported ``change``/``change_percent``. If ``change``
    is missing but both ``ltp`` and ``close`` (previous close) are present, the
    change is derived so snapshot-only data (last-session close + last price)
    still yields a movement classification. ``has_quote`` is True only when a
    usable movement value exists.
    """
    if quote is None:
        return None, None, False
    ch = _field(quote, "change")
    pct = _field(quote, "change_percent")
    ltp = _field(quote, "ltp")
    close = _field(quote, "close")
    if ch is None and ltp is not None and close not in (None, 0):
        ch = ltp - close
        if pct is None and close:
            pct = (ch / close) * 100.0
    has = ch is not None
    return ch, pct, has


def _classify(ch: float | None, has: bool) -> str:
    if not has or ch is None:
        return _STATUS_UNAVAILABLE
    if ch > 0:
        return _STATUS_ADVANCE
    if ch < 0:
        return _STATUS_DECLINE
    return _STATUS_UNCHANGED


@dataclass
class BreadthRow:
    """One constituent's breadth contribution."""

    symbol: str
    name: str | None
    exchange: str
    instrument_token: str | None
    ltp: float | None
    change: float | None
    change_percent: float | None
    status: str
    sector: str
    received_ts: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "exchange": self.exchange,
            "instrument_token": self.instrument_token,
            "ltp": self.ltp,
            "change": self.change,
            "change_percent": self.change_percent,
            "status": self.status,
            "sector": self.sector,
            "received_ts": self.received_ts,
        }


@dataclass
class BreadthSnapshot:
    """Aggregated breadth result for one universe."""

    universe: str
    eligible: int
    quoted: int
    unavailable: int
    advances: int
    declines: int
    unchanged: int
    advance_percent: float
    decline_percent: float
    ad_ratio: float | None
    net_advances: int
    as_of: str | None
    rows: list[BreadthRow] = field(default_factory=list)
    unclassified: int = 0
    # Optional extended metrics (populated only when quote data supports them).
    volume_advancing: int | None = None
    volume_declining: int | None = None
    intraday_highs: int | None = None
    intraday_lows: int | None = None
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "eligible": self.eligible,
            "quoted": self.quoted,
            "unavailable": self.unavailable,
            "advances": self.advances,
            "declines": self.declines,
            "unchanged": self.unchanged,
            "advance_percent": self.advance_percent,
            "decline_percent": self.decline_percent,
            "ad_ratio": self.ad_ratio,
            "net_advances": self.net_advances,
            "as_of": self.as_of,
            "unclassified": self.unclassified,
            "stale": self.stale,
            "volume_advancing": self.volume_advancing,
            "volume_declining": self.volume_declining,
            "intraday_highs": self.intraday_highs,
            "intraday_lows": self.intraday_lows,
            "weighting": "equal",
            "rows": [r.to_dict() for r in self.rows],
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_breadth(
    universe: str,
    members: list[Any],
    quote_reader: QuoteReader,
    *,
    as_of: str | None = None,
    stale_minutes: int = 5,
) -> BreadthSnapshot:
    """Compute breadth for ``members`` using ``quote_reader``.

    ``members`` are ``market_universe.Member`` instances. The reader is invoked
    with ``(exchange, instrument_token)``; a ``None`` token yields
    ``unavailable`` (the member is still counted as eligible).
    """
    rows: list[BreadthRow] = []
    advances = declines = unchanged = unavailable = 0
    unclassified = 0
    vol_adv = vol_dec = 0
    highs = lows = 0
    any_live = False
    newest_ts: datetime | None = None

    for m in members:
        token = m.instrument_token
        quote = quote_reader(m.exchange, token) if token else None
        ch, pct, has = effective_change(quote)
        status = _classify(ch, has)
        sector = sector_for_symbol(m.symbol)
        if sector == UNCLASSIFIED:
            unclassified += 1

        received = _field(quote, "received_ts")
        received_iso: str | None = None
        if received is not None:
            if isinstance(received, datetime):
                received_iso = received.isoformat()
                if newest_ts is None or received > newest_ts:
                    newest_ts = received
            else:
                received_iso = str(received)

        ltp = _field(quote, "ltp")
        volume = _field(quote, "volume")

        if status == _STATUS_ADVANCE:
            advances += 1
            if isinstance(volume, (int, float)):
                vol_adv += int(volume)
            high = _field(quote, "high")
            close = _field(quote, "close")
            if isinstance(high, (int, float)) and isinstance(close, (int, float)) \
                    and close and high > close:
                highs += 1
        elif status == _STATUS_DECLINE:
            declines += 1
            if isinstance(volume, (int, float)):
                vol_dec += int(volume)
            low = _field(quote, "low")
            close = _field(quote, "close")
            if isinstance(low, (int, float)) and isinstance(close, (int, float)) \
                    and close and low < close:
                lows += 1
        elif status == _STATUS_UNCHANGED:
            unchanged += 1
        else:
            unavailable += 1

        rows.append(BreadthRow(
            symbol=m.symbol, name=m.name, exchange=m.exchange,
            instrument_token=token, ltp=ltp, change=ch, change_percent=pct,
            status=status, sector=sector, received_ts=received_iso,
        ))

    quoted = advances + declines + unchanged
    eligible = len(members)
    advance_percent = (advances / quoted * 100.0) if quoted else 0.0
    decline_percent = (declines / quoted * 100.0) if quoted else 0.0
    ad_ratio = (advances / declines) if declines else None
    net_advances = advances - declines

    as_of_iso = as_of or (newest_ts.isoformat() if newest_ts is not None else _now_iso())
    stale = False
    if newest_ts is not None:
        age = (datetime.now(timezone.utc) - newest_ts).total_seconds() / 60.0
        stale = age > stale_minutes

    return BreadthSnapshot(
        universe=universe,
        eligible=eligible,
        quoted=quoted,
        unavailable=unavailable,
        advances=advances,
        declines=declines,
        unchanged=unchanged,
        advance_percent=round(advance_percent, 2),
        decline_percent=round(decline_percent, 2),
        ad_ratio=round(ad_ratio, 4) if ad_ratio is not None else None,
        net_advances=net_advances,
        as_of=as_of_iso,
        rows=rows,
        unclassified=unclassified,
        volume_advancing=vol_adv or None,
        volume_declining=vol_dec or None,
        intraday_highs=highs or None,
        intraday_lows=lows or None,
        stale=stale,
    )
