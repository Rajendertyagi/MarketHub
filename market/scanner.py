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

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from .market_universe import Member, resolve_universe
from .sector_classification import sector_for_symbol

_EPS = 1e-9
_MAX_LIMIT = 100

# Scanner metric name -> ScanRow attribute (canonical quote fields differ from
# the public metric label, e.g. "open_interest" is stored as `oi`).
_METRIC_ATTR = {
    "open_interest": "oi",
    "oi_change": "oi_change",
    "oi_change_percent": "oi_change_percent",
    "change_percent": "change_percent",
    "volume": "volume",
    "iv": "iv",
}


@dataclass
class ScannerDef:
    name: str
    title: str
    instrument_class: str            # "equity" | "future" | "option"
    metric: str                      # change_percent | volume | open_interest | iv
    order: str                       # "desc" | "asc"
    movement_filter: str | None = None   # advance | decline | unchanged | None
    contract_kind: str | None = None    # "future" | "option" (derivative scanners)
    extra: list[str] | None = None      # supported contextual controls
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
    contract: str | None = None       # derivative contract tradingsymbol
    expiry: str | None = None
    option_type: str | None = None    # "CE" | "PE"
    strike: float | None = None
    oi_change: float | int | None = None
    oi_change_percent: float | None = None

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
            "contract": self.contract,
            "expiry": self.expiry,
            "option_type": self.option_type,
            "strike": self.strike,
            "oi_change": self.oi_change,
            "oi_change_percent": self.oi_change_percent,
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
            ScannerDef("futures_oi", "Futures — Highest OI", "future",
                       "open_interest", "desc", contract_kind="future",
                       extra=["expiry"],
                       description="Actual FUTURE contracts ranked by open interest."),
            ScannerDef("futures_oi_change", "Futures — OI Change", "future",
                       "oi_change", "desc", contract_kind="future",
                       extra=["expiry"],
                       description="Actual FUTURE contracts ranked by OI change."),
            ScannerDef("futures_oi_change_pct", "Futures — OI Change %", "future",
                       "oi_change_percent", "desc", contract_kind="future",
                       extra=["expiry"],
                       description="Actual FUTURE contracts ranked by OI change %."),
            ScannerDef("option_iv", "Option IV", "option", "iv", "desc",
                       contract_kind="option",
                       extra=["expiry", "atm_range", "option_type"],
                       description="Actual OPTION contracts ranked by implied volatility."),
        ]

    def list_scanners(self) -> list[dict[str, Any]]:
        return [{
            "name": d.name,
            "title": d.title,
            "instrument_class": d.instrument_class,
            "metric": d.metric,
            "order": d.order,
            "movement_filter": d.movement_filter,
            "contract_kind": d.contract_kind,
            "extra": d.extra,
            "description": d.description,
        } for d in self._defs.values()]

    def scan(self, name: str, universe: str,
              reader: Callable[[str, str], Any], limit: int = 25,
              *, expiry: str | None = None, atm_range: int = 5,
              option_type: str = "both", today: str | None = None) -> ScanResult:
        defn = self._defs.get(name)
        if defn is None:
            raise ValueError(f"unknown scanner: {name}")
        try:
            members = resolve_universe(universe, self._catalog)
        except ValueError as exc:
            raise ValueError(str(exc))
        limit = max(1, min(int(limit), _MAX_LIMIT))
        atm_range = max(0, min(int(atm_range), 50))
        if option_type not in ("CE", "PE", "both"):
            option_type = "both"
        today = today or date.today().isoformat()

        if defn.instrument_class == "equity":
            rows, eligible, quoted, as_of = self._scan_equity(
                defn, members, reader)
        elif defn.contract_kind == "future":
            rows, eligible, quoted, as_of = self._scan_futures(
                defn, members, reader, today, expiry)
        elif defn.contract_kind == "option":
            rows, eligible, quoted, as_of = self._scan_options(
                defn, members, reader, today, expiry, atm_range, option_type)
        else:
            raise ValueError(
                f"scanner '{name}' has no resolvable instrument class")

        # Rows without the ranking metric cannot be ranked — exclude them
        # (e.g. a future with no OI, an option with no IV). Never fabricate.
        metric_attr = _METRIC_ATTR.get(defn.metric, defn.metric)
        rows = [r for r in rows if getattr(r, metric_attr) is not None]

        # Deterministic ordering: primary metric, tie-break by symbol/contract.
        rev = defn.order == "desc"

        def _key(r: ScanRow):
            val = getattr(r, metric_attr)
            if val is None:
                val = float("-inf") if rev else float("inf")
            return (val, r.symbol or "", r.contract or "")

        rows.sort(key=_key, reverse=rev)
        matched = len(rows)
        rows = rows[:limit]
        return ScanResult(
            scanner=name, universe=universe,
            as_of=as_of.isoformat() if isinstance(as_of, datetime) else None,
            eligible=eligible, quoted=quoted, matched=matched, rows=rows)

    def _scan_equity(self, defn, members, reader):
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
        return rows, eligible, quoted, as_of

    def _current_expiries(self, underlying: str, instrument_type: str,
                          today: str) -> list[str]:
        try:
            exps = self._catalog.derivative_expiries(underlying, instrument_type)
        except Exception:
            return []
        return [e for e in exps if e and e >= today]

    def _scan_futures(self, defn, members, reader, today, expiry):
        # One catalog query for all futures, then group by underlying — avoids
        # N sequential per-underlying lookups (the `underlying` column is not
        # indexed, so per-symbol scans are slow over large universes).
        try:
            all_futs = self._catalog.search(instrument_type="FUTURE") or []
        except Exception:
            all_futs = []
        by_und: dict[str, list[dict]] = {}
        for c in all_futs:
            by_und.setdefault(c.get("underlying"), []).append(c)
        eligible = 0
        quoted = 0
        rows: list[ScanRow] = []
        as_of: datetime | None = None
        for m in members:
            if not m.symbol:
                continue
            contracts = by_und.get(m.symbol, [])
            if not contracts:
                continue
            if expiry:
                contracts = [c for c in contracts if c.get("expiry") == expiry]
            else:
                contracts = [c for c in contracts
                             if (c.get("expiry") or "") >= today]
            if not contracts:
                continue
            contracts.sort(key=lambda c: c.get("expiry") or "")
            c = contracts[0]
            token = c.get("instrument_token")
            exch = c.get("exchange")
            if not token:
                continue
            eligible += 1
            q = reader(exch, token) if exch else None
            if q is None:
                continue
            ltp = getattr(q, "ltp", None)
            if ltp is None:
                continue
            quoted += 1
            change = getattr(q, "change", None)
            change_pct = getattr(q, "change_percent", None)
            oi = getattr(q, "open_interest", None)
            oi_change = getattr(q, "oi_change", None)
            oi_change_pct = getattr(q, "oi_change_percent", None)
            status = _status_of(change_pct)
            rts = getattr(q, "received_ts", None)
            fresh = rts.isoformat() if isinstance(rts, datetime) else None
            if isinstance(rts, datetime) and (as_of is None or rts > as_of):
                as_of = rts
            rows.append(ScanRow(
                symbol=m.symbol, sector=m.symbol, ltp=ltp, change=change,
                change_percent=change_pct, volume=getattr(q, "volume", None),
                oi=oi, iv=None, status=status, freshness=fresh,
                contract=c.get("tradingsymbol"), expiry=c.get("expiry"),
                option_type="FUT", oi_change=oi_change,
                oi_change_percent=oi_change_pct))
        return rows, eligible, quoted, as_of

    def _scan_options(self, defn, members, reader, today, expiry,
                      atm_range, option_type):
        eligible = 0
        quoted = 0
        rows: list[ScanRow] = []
        as_of: datetime | None = None
        wanted = (["CE", "PE"] if option_type == "both"
                  else [option_type])
        for m in members:
            if not m.symbol:
                continue
            exps = self._current_expiries(m.symbol, "OPTION", today)
            if expiry:
                exps = [e for e in exps if e == expiry]
            if not exps:
                continue
            target = exps[0]
            try:
                contracts = self._catalog.option_strikes(m.symbol, target)
            except Exception:
                continue
            if not contracts:
                continue
            # Spot for ATM: underlying equity quote if available.
            spot = None
            sq = reader(m.exchange, m.instrument_token) if m.instrument_token else None
            if sq is not None:
                spot = getattr(sq, "ltp", None)
            strikes = sorted({c.get("strike") for c in contracts
                              if c.get("strike") is not None})
            if not strikes:
                continue
            if spot is not None:
                atm = min(strikes, key=lambda s: abs(s - spot))
            else:
                atm = strikes[len(strikes) // 2]
            try:
                atm_idx = strikes.index(atm)
            except ValueError:
                atm_idx = 0
            lo = max(0, atm_idx - atm_range)
            hi = min(len(strikes), atm_idx + atm_range + 1)
            sel = set(strikes[lo:hi])
            by_key: dict[tuple, dict] = {}
            for c in contracts:
                if c.get("strike") in sel and c.get("option_type") in wanted:
                    by_key[(c.get("strike"), c.get("option_type"))] = c
            for (strike, ot), c in sorted(by_key.items()):
                token = c.get("instrument_token")
                exch = c.get("exchange")
                if not token or not exch:
                    continue
                eligible += 1
                q = reader(exch, token)
                if q is None:
                    continue
                ltp = getattr(q, "ltp", None)
                if ltp is None:
                    continue
                quoted += 1
                change = getattr(q, "change", None)
                change_pct = getattr(q, "change_percent", None)
                oi = getattr(q, "open_interest", None)
                iv = None
                greeks = getattr(q, "greeks", None)
                if greeks is not None:
                    iv = getattr(greeks, "iv", None)
                status = _status_of(change_pct)
                rts = getattr(q, "received_ts", None)
                fresh = rts.isoformat() if isinstance(rts, datetime) else None
                if isinstance(rts, datetime) and (as_of is None or rts > as_of):
                    as_of = rts
                rows.append(ScanRow(
                    symbol=m.symbol, sector=m.symbol, ltp=ltp, change=change,
                    change_percent=change_pct,
                    volume=getattr(q, "volume", None), oi=oi, iv=iv,
                    status=status, freshness=fresh,
                    contract=c.get("tradingsymbol"), expiry=target,
                    option_type=ot, strike=strike))
        return rows, eligible, quoted, as_of
