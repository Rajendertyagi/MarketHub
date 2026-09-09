"""Generic market scanner engine (Phase C / D).

ONE canonical engine for all scanners. It reuses the SAME universe resolver and
quote reader that Breadth / Sector Heatmap / Market Map consume, so a scanner can
never disagree with those features about membership or per-symbol Change %.

Scanners declare the instrument CLASS they rank. Equity scanners rank equity
cash quotes only. Futures-OI / Option-IV scanners (added later) must declare
their class and use ACTUAL derivative contracts — never substitute cash for a
future, or a future for an option. Unavailable data stays unavailable; it is
never fabricated or re-labeled to make a list look populated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .market_universe import Member, resolve_universe
from .sector_classification import sector_for_symbol

_EPS = 1e-9
_MAX_LIMIT = 100


@dataclass
class ScannerDef:
    name: str
    title: str
    instrument_class: str            # "equity" | "future" | "option"
    metric: str                      # change_percent | volume | open_interest | iv
    order: str                       # "desc" | "asc"
    movement_filter: str | None = None   # advance | decline | unchanged | None
    description: str = ""


@dataclass
class ScanRow:
    symbol: str
    sector: str
    ltp: float | None
    change: float | None
    change_percent: float | None
    volume: int | float | None
    oi: float | int | None
    iv: float | None
    status: str
    freshness: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "sector": self.sector,
            "ltp": self.ltp,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume": self.volume,
            "oi": self.oi,
            "iv": self.iv,
            "status": self.status,
            "freshness": self.freshness,
        }


@dataclass
class ScanResult:
    scanner: str
    universe: str
    as_of: str | None
    eligible: int
    quoted: int
    matched: int
    rows: list[ScanRow]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanner": self.scanner,
            "universe": self.universe,
            "as_of": self.as_of,
            "eligible": self.eligible,
            "quoted": self.quoted,
            "matched": self.matched,
            "rows": [r.to_dict() for r in self.rows],
        }


def _status_of(change_pct: float | None) -> str:
    if change_pct is None:
        return "unavailable"
    if change_pct > _EPS:
        return "advance"
    if change_pct < -_EPS:
        return "decline"
    return "unchanged"


class ScannerEngine:
    """Canonical scanner engine. Stateless apart from the injected catalog."""

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog
        self._defs = {d.name: d for d in self._scanner_defs()}

    @staticmethod
    def _scanner_defs() -> list[ScannerDef]:
        return [
            ScannerDef("gainers", "Top Gainers", "equity", "change_percent", "desc",
                       description="Equity cash ranked by Change % descending."),
            ScannerDef("losers", "Top Losers", "equity", "change_percent", "asc",
                       description="Equity cash ranked by Change % ascending."),
            ScannerDef("volume", "Most Active by Volume", "equity", "volume", "desc",
                       description="Equity cash ranked by canonical volume descending."),
            ScannerDef("advances", "Advances", "equity", "change_percent", "desc",
                       movement_filter="advance",
                       description="Equity cash with a positive change."),
            ScannerDef("declines", "Declines", "equity", "change_percent", "asc",
                       movement_filter="decline",
                       description="Equity cash with a negative change."),
        ]

    def list_scanners(self) -> list[dict[str, Any]]:
        return [{
            "name": d.name,
            "title": d.title,
            "instrument_class": d.instrument_class,
            "metric": d.metric,
            "order": d.order,
            "movement_filter": d.movement_filter,
            "description": d.description,
        } for d in self._defs.values()]

    def scan(self, name: str, universe: str,
             reader: Callable[[str, str], Any], limit: int = 25) -> ScanResult:
        defn = self._defs.get(name)
        if defn is None:
            raise ValueError(f"unknown scanner: {name}")
        if defn.instrument_class != "equity":
            # Futures-OI / Option-IV scanners are deferred until derivative
            # universe aggregation is architected; never fall back to cash.
            raise ValueError(
                f"scanner '{name}' requires {defn.instrument_class} data "
                f"which is not available")
        try:
            members = resolve_universe(universe, self._catalog)
        except ValueError as exc:
            raise ValueError(str(exc))
        limit = max(1, min(int(limit), _MAX_LIMIT))
        eligible = len(members)
        quoted = 0
        rows: list[ScanRow] = []
        as_of: datetime | None = None
        for m in members:
            if not m.instrument_token:
                continue
            q = reader(m.exchange, m.instrument_token)
            if q is None:
                continue
            ltp = getattr(q, "ltp", None)
            if ltp is None:
                continue
            quoted += 1
            change = getattr(q, "change", None)
            change_pct = getattr(q, "change_percent", None)
            volume = getattr(q, "volume", None)
            oi = getattr(q, "open_interest", None)
            iv = None
            greeks = getattr(q, "greeks", None)
            if greeks is not None:
                iv = getattr(greeks, "iv", None)
            status = _status_of(change_pct)
            if defn.movement_filter and status != defn.movement_filter:
                continue
            rts = getattr(q, "received_ts", None)
            fresh = rts.isoformat() if isinstance(rts, datetime) else None
            if isinstance(rts, datetime) and (as_of is None or rts > as_of):
                as_of = rts
            rows.append(ScanRow(
                symbol=m.symbol, sector=sector_for_symbol(m.symbol),
                ltp=ltp, change=change, change_percent=change_pct,
                volume=volume, oi=oi, iv=iv, status=status, freshness=fresh))

        # Deterministic ordering: primary metric, tie-break by symbol.
        rev = defn.order == "desc"

        def _key(r: ScanRow):
            val = getattr(r, defn.metric)
            if val is None:
                val = float("-inf") if rev else float("inf")
            return (val, r.symbol)

        rows.sort(key=_key, reverse=rev)
        matched = len(rows)
        rows = rows[:limit]
        return ScanResult(
            scanner=name, universe=universe,
            as_of=as_of.isoformat() if isinstance(as_of, datetime) else None,
            eligible=eligible, quoted=quoted, matched=matched, rows=rows)
