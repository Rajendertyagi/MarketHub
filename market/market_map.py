"""Canonical Market Map projection (stock-level, sector-grouped).

Reuses the EXACT same foundation as Market Breadth and Sector Heatmap:
  * market_universe.resolve_universe   -> single universe owner
  * market.sector_classification       -> single sector owner (sector_for_symbol)
  * market.breadth.effective_change    -> identical movement/color semantics
  * the canonical quote reader          -> MarketService.get_quote_now

Because membership, the sector classifier and the quote reader are shared,
Market Map reconciles with Breadth (eligible/quoted/unavailable/advances/
declines/unchanged) and with Sector Heatmap (sector membership + per-symbol
change %) by construction. No new quote store, no new sector taxonomy, no
independent aggregation engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .breadth import compute_breadth, effective_change
from .sector_classification import UNCLASSIFIED, sector_for_symbol

QuoteReader = Callable[[str, str], Any]


def _field(quote: Any, name: str, default: Any = None) -> Any:
    """Read a field from a Quote (frozen dataclass) or a plain dict."""
    if quote is None:
        return default
    if isinstance(quote, dict):
        return quote.get(name, default)
    return getattr(quote, name, default)


@dataclass
class MapStock:
    """One stock tile in the Market Map."""
    symbol: str
    exchange: str
    instrument_token: str | None
    sector: str
    ltp: float | None
    change: float | None
    change_percent: float | None
    volume: int | None
    status: str
    fno: bool = False
    received_ts: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "instrument_token": self.instrument_token,
            "sector": self.sector,
            "ltp": self.ltp,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume": self.volume,
            "status": self.status,
            "fno": self.fno,
            "received_ts": self.received_ts,
        }


@dataclass
class SectorGroup:
    """Stocks belonging to one canonical sector."""
    sector: str
    stocks: list[MapStock] = field(default_factory=list)
    advances: int = 0
    declines: int = 0
    unchanged: int = 0
    unavailable: int = 0
    quoted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "stocks": [s.to_dict() for s in self.stocks],
            "advances": self.advances,
            "declines": self.declines,
            "unchanged": self.unchanged,
            "unavailable": self.unavailable,
            "quoted": self.quoted,
        }


@dataclass
class MarketMapSnapshot:
    """Whole-market map for one universe."""
    universe: str
    eligible: int
    quoted: int
    unavailable: int
    advances: int
    declines: int
    unchanged: int
    unclassified: int
    as_of: str | None
    stale: bool
    sectors: list[SectorGroup] = field(default_factory=list)
    reconciliation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "eligible": self.eligible,
            "quoted": self.quoted,
            "unavailable": self.unavailable,
            "advances": self.advances,
            "declines": self.declines,
            "unchanged": self.unchanged,
            "unclassified": self.unclassified,
            "as_of": self.as_of,
            "stale": self.stale,
            "sectors": [s.to_dict() for s in self.sectors],
            "reconciliation": self.reconciliation,
        }


def compute_market_map(
    universe: str,
    members: list[Any],
    quote_reader: QuoteReader,
    *,
    fno_symbols: set[str] | None = None,
    as_of: str | None = None,
    stale_minutes: int = 5,
) -> MarketMapSnapshot:
    """Build a sector-grouped stock map from shared universe + quotes.

    ``members`` are ``market_universe.Member`` instances; ``quote_reader`` is
    invoked with ``(exchange, instrument_token)`` (canonical MarketService).
    ``fno_symbols`` is an optional uppercased symbol set marking F&O underlyings
    so the WebUI can honestly open the existing F&O workspace (or decline for
    non-derivative stocks). The set is computed once by the caller via the
    catalog's ``fno_universe`` — never per-symbol.
    """
    fno_set = {str(s).upper() for s in (fno_symbols or set())}

    groups: dict[str, list[Any]] = {}
    for m in members:
        sec = sector_for_symbol(m.symbol)
        groups.setdefault(sec, []).append(m)

    sector_groups: list[SectorGroup] = []
    total_adv = total_dec = total_unch = total_quoted = total_unavail = 0
    total_eligible = len(members)
    unclassified = 0
    newest_ts: datetime | None = None

    for sector, group in groups.items():
        if sector == UNCLASSIFIED:
            unclassified += len(group)
        sg = SectorGroup(sector=sector)
        for m in group:
            token = m.instrument_token
            quote = quote_reader(m.exchange, token) if token else None
            ch, pct, has = effective_change(quote)
            if not has:
                status = "unavailable"
                sg.unavailable += 1
            elif (ch or 0) > 0:
                status = "advance"
                sg.advances += 1
            elif (ch or 0) < 0:
                status = "decline"
                sg.declines += 1
            else:
                status = "unchanged"
                sg.unchanged += 1

            ltp = _field(quote, "ltp")
            volume = _field(quote, "volume")
            received = _field(quote, "received_ts")
            received_iso: str | None = None
            if received is not None:
                if isinstance(received, datetime):
                    received_iso = received.isoformat()
                    if newest_ts is None or received > newest_ts:
                        newest_ts = received
                else:
                    received_iso = str(received)

            sg.stocks.append(MapStock(
                symbol=m.symbol,
                exchange=m.exchange,
                instrument_token=token,
                sector=sector,
                ltp=ltp,
                change=ch,
                change_percent=pct,
                volume=volume,
                status=status,
                fno=m.symbol.upper() in fno_set,
                received_ts=received_iso,
            ))
        sg.quoted = sg.advances + sg.declines + sg.unchanged
        total_adv += sg.advances
        total_dec += sg.declines
        total_unch += sg.unchanged
        total_unavail += sg.unavailable
        total_quoted += sg.quoted
        sector_groups.append(sg)

    # Stable, readable order: real sectors alphabetically, Unclassified last.
    sector_groups.sort(key=lambda s: (s.sector == UNCLASSIFIED, s.sector))

    as_of_iso = as_of
    stale = False
    if newest_ts is not None:
        as_of_iso = newest_ts.isoformat()
        age = (datetime.now(timezone.utc) - newest_ts).total_seconds() / 60.0
        stale = age > stale_minutes
    if as_of_iso is None:
        as_of_iso = datetime.now(timezone.utc).isoformat()

    # Cross-feature reconciliation with Market Breadth. Because membership, the
    # sector classifier and the quote reader are shared, these MUST all match.
    b = compute_breadth(
        universe, members, quote_reader, stale_minutes=stale_minutes)
    reconciliation = {
        "sector_advances_sum": sum(s.advances for s in sector_groups),
        "breadth_advances": total_adv,
        "sector_declines_sum": sum(s.declines for s in sector_groups),
        "breadth_declines": total_dec,
        "sector_quoted_sum": sum(s.quoted for s in sector_groups),
        "breadth_quoted": total_quoted,
        "advances_match": sum(s.advances for s in sector_groups) == total_adv,
        "declines_match": sum(s.declines for s in sector_groups) == total_dec,
        "quoted_match": sum(s.quoted for s in sector_groups) == total_quoted,
        # Cross-feature (Market Map totals == Breadth totals).
        "breadth_eligible": b.eligible,
        "breadth_unavailable": b.unavailable,
        "breadth_unchanged": b.unchanged,
        "eligible_match": total_eligible == b.eligible,
        "quoted_cross_match": total_quoted == b.quoted,
        "unavailable_cross_match": total_unavail == b.unavailable,
        "advances_cross_match": total_adv == b.advances,
        "declines_cross_match": total_dec == b.declines,
        "unchanged_cross_match": total_unch == b.unchanged,
    }

    return MarketMapSnapshot(
        universe=universe,
        eligible=total_eligible,
        quoted=total_quoted,
        unavailable=total_unavail,
        advances=total_adv,
        declines=total_dec,
        unchanged=total_unch,
        unclassified=unclassified,
        as_of=as_of_iso,
        stale=stale,
        sectors=sector_groups,
        reconciliation=reconciliation,
    )
