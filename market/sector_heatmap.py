"""Canonical Sector Heatmap aggregation.

Reuses the EXACT same foundation as Market Breadth:
  * ``market_universe.resolve_universe`` for membership
  * the same ``quote_reader`` (MarketService.get_quote_now)
  * the single canonical ``sector_for_symbol`` classifier

Because membership, quotes and the classifier are shared, sector totals
reconcile with breadth totals for the same universe by construction (acceptance
§16/§24). The "Unclassified" bucket is itself a sector so the sums close exactly;
its size is also reported separately as ``unclassified``.

Weighting (§13)
---------------
Sector performance is EQUAL-WEIGHTED: the average constituent change % is the
simple mean of per-stock change % across quoted members. No market-cap / index
weighting is fabricated. ``weighting`` is always reported as ``"equal"``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Callable

from .breadth import effective_change
from .sector_classification import UNCLASSIFIED, sector_for_symbol

QuoteReader = Callable[[str, str], Any]


@dataclass
class SectorRow:
    sector: str
    constituent_count: int
    quoted: int
    unavailable: int
    advances: int
    declines: int
    unchanged: int
    average_change_percent: float | None
    median_change_percent: float | None
    net_advances: int
    top_gainer: dict[str, Any] | None
    top_loser: dict[str, Any] | None
    members: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sector": self.sector,
            "constituent_count": self.constituent_count,
            "quoted": self.quoted,
            "unavailable": self.unavailable,
            "advances": self.advances,
            "declines": self.declines,
            "unchanged": self.unchanged,
            "average_change_percent": self.average_change_percent,
            "median_change_percent": self.median_change_percent,
            "net_advances": self.net_advances,
            "top_gainer": self.top_gainer,
            "top_loser": self.top_loser,
            "weighting": "equal",
            "members": self.members,
        }


@dataclass
class SectorHeatmapSnapshot:
    universe: str
    sector_count: int
    classified_count: int
    unclassified_count: int
    eligible: int
    quoted: int
    unavailable: int
    advances: int
    declines: int
    unchanged: int
    as_of: str | None
    weighting: str
    sectors: list[SectorRow] = field(default_factory=list)
    # Reconciliation (breadth <-> sector) — must hold exactly.
    reconciliation: dict[str, Any] = field(default_factory=dict)
    stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe": self.universe,
            "sector_count": self.sector_count,
            "classified_count": self.classified_count,
            "unclassified_count": self.unclassified_count,
            "eligible": self.eligible,
            "quoted": self.quoted,
            "unavailable": self.unavailable,
            "advances": self.advances,
            "declines": self.declines,
            "unchanged": self.unchanged,
            "as_of": self.as_of,
            "weighting": self.weighting,
            "stale": self.stale,
            "sectors": [s.to_dict() for s in self.sectors],
            "reconciliation": self.reconciliation,
        }


def _round(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(v, n)


def compute_sector_heatmap(
    universe: str,
    members: list[Any],
    quote_reader: QuoteReader,
    *,
    as_of: str | None = None,
    stale_minutes: int = 5,
    include_members: bool = True,
) -> SectorHeatmapSnapshot:
    """Aggregate members into per-sector metrics using the canonical classifier."""
    from datetime import datetime, timezone

    groups: dict[str, list[Any]] = {}
    for m in members:
        sec = sector_for_symbol(m.symbol)
        groups.setdefault(sec, []).append(m)

    sector_rows: list[SectorRow] = []
    total_adv = total_dec = total_unch = total_quoted = total_unavail = 0
    total_eligible = len(members)
    classified = 0
    unclassified = 0
    newest_ts = None

    for sector, group in groups.items():
        if sector == UNCLASSIFIED:
            unclassified += len(group)
        else:
            classified += len(group)

        advances = declines = unchanged = unavailable = 0
        pcts: list[float] = []
        gainer = None
        loser = None
        member_dicts: list[dict[str, Any]] = []

        for m in group:
            token = m.instrument_token
            quote = quote_reader(m.exchange, token) if token else None
            ch, pct, has = effective_change(quote)
            if not has:
                status = "unavailable"
                unavailable += 1
            elif (ch or 0) > 0:
                status = "advance"
                advances += 1
            elif (ch or 0) < 0:
                status = "decline"
                declines += 1
            else:
                status = "unchanged"
                unchanged += 1

            if has and pct is not None:
                pcts.append(pct)
                md = {
                    "symbol": m.symbol,
                    "name": m.name,
                    "ltp": _f(quote, "ltp"),
                    "change": ch,
                    "change_percent": pct,
                    "status": status,
                }
                member_dicts.append(md)
                if gainer is None or pct > gainer["change_percent"]:
                    gainer = md
                if loser is None or pct < loser["change_percent"]:
                    loser = md

            received = _f(quote, "received_ts")
            if received is not None and isinstance(received, datetime):
                if newest_ts is None or received > newest_ts:
                    newest_ts = received

        quoted = advances + declines + unchanged
        avg = _round(sum(pcts) / len(pcts), 2) if pcts else None
        med = _round(median(pcts), 2) if pcts else None

        total_adv += advances
        total_dec += declines
        total_unch += unchanged
        total_quoted += quoted
        total_unavail += unavailable

        sector_rows.append(SectorRow(
            sector=sector,
            constituent_count=len(group),
            quoted=quoted,
            unavailable=unavailable,
            advances=advances,
            declines=declines,
            unchanged=unchanged,
            average_change_percent=avg,
            median_change_percent=med,
            net_advances=advances - declines,
            top_gainer=gainer,
            top_loser=loser,
            members=member_dicts if include_members else [],
        ))

    # Stable, readable order: real sectors alphabetically, Unclassified last.
    sector_rows.sort(key=lambda s: (s.sector == UNCLASSIFIED, s.sector))

    as_of_iso = as_of
    stale = False
    if newest_ts is not None:
        as_of_iso = newest_ts.isoformat()
        age = (datetime.now(timezone.utc) - newest_ts).total_seconds() / 60.0
        stale = age > stale_minutes
    if as_of_iso is None:
        as_of_iso = datetime.now(timezone.utc).isoformat()

    reconciliation = {
        "sector_advances_sum": sum(s.advances for s in sector_rows),
        "breadth_advances": total_adv,
        "sector_declines_sum": sum(s.declines for s in sector_rows),
        "breadth_declines": total_dec,
        "sector_quoted_sum": sum(s.quoted for s in sector_rows),
        "breadth_quoted": total_quoted,
        "advances_match": sum(s.advances for s in sector_rows) == total_adv,
        "declines_match": sum(s.declines for s in sector_rows) == total_dec,
        "quoted_match": sum(s.quoted for s in sector_rows) == total_quoted,
    }

    return SectorHeatmapSnapshot(
        universe=universe,
        sector_count=len(sector_rows),
        classified_count=classified,
        unclassified_count=unclassified,
        eligible=total_eligible,
        quoted=total_quoted,
        unavailable=total_unavail,
        advances=total_adv,
        declines=total_dec,
        unchanged=total_unch,
        as_of=as_of_iso,
        weighting="equal",
        sectors=sector_rows,
        reconciliation=reconciliation,
        stale=stale,
    )


def _f(quote: Any, name: str, default: Any = None) -> Any:
    if quote is None:
        return default
    if isinstance(quote, dict):
        return quote.get(name, default)
    return getattr(quote, name, default)
