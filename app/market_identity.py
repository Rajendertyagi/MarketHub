"""
Provider-neutral instrument identity resolver for advanced alerts (B2).

Maps every provider-specific identifier (catalog token, tradingsymbol,
provider symbol) to ONE canonical instrument id derived from the real
instrument's stable attributes:

    EQUITY/ETF  {exchange}:{type}:{ISIN}          (fallback normalized symbol)
    INDEX       {exchange}:INDEX:{canonical symbol}
    FUTURE      {exchange}:FUTURE:{underlying}:{expiry}
    OPTION      {exchange}:OPTION:{underlying}:{expiry}:{strike}:{option_type}

The same real instrument registered from different providers converges to
the same canonical id, so a condition alert bound to one canonical id fires
regardless of which provider's quote arrives. Collisions are REJECTED loudly
(never silently re-pointed); re-registration of the same alias->canonical
pair is idempotent.

This resolver is intentionally isolated from the global
``InstrumentIdentityRegistry`` (which maps to feed/storage keys). It is
populated from the instrument catalog at startup and used ONLY by the
advanced condition-alert engine.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# Canonical instrument types (provider-normalized).
EQUITY = "EQUITY"
ETF = "ETF"
INDEX = "INDEX"
FUTURE = "FUTURE"
OPTION = "OPTION"

# Provider instrument_type -> canonical type.
_TYPE_ALIASES = {
    "EQ": EQUITY,
    "EQUITY": EQUITY,
    "ETF": ETF,
    "INDEX": INDEX,
    "FUT": FUTURE,
    "FUTURE": FUTURE,
    "FUTIDX": FUTURE,
    "FUTSTK": FUTURE,
    "OPTION": OPTION,
    "OPTIDX": OPTION,
    "OPTSTK": OPTION,
    "CE": OPTION,
    "PE": OPTION,
}

# Index/underlying symbol aliases (normalized form -> canonical symbol).
# Normalization lowercases and strips non-alphanumerics, so the keys are the
# stripped forms. "Nifty 50", "NIFTY50-INDEX" and "NIFTY 50" all converge.
_SYMBOL_ALIASES = {
    "nifty": "NIFTY",
    "nifty50": "NIFTY",
    "nifty50index": "NIFTY",
    "niftyindex": "NIFTY",
    "niftybank": "BANKNIFTY",
    "banknifty": "BANKNIFTY",
    "banknifty50": "BANKNIFTY",
    "bankniftyindex": "BANKNIFTY",
    "sensex": "SENSEX",
    "bse sensex": "SENSEX",
    "niftynext50": "NIFTYNEXT50",
    "niftymidcap50": "NIFTYMIDCAP50",
    "niftysmallcap250": "NIFTYSMALLCAP250",
}


def _normalize_symbol(s: str) -> str:
    """Lowercase + strip non-alphanumeric (deterministic)."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _canonical_exchange(exchange: str | None) -> str | None:
    """Normalize a provider exchange/segment code to the base exchange.

    Upstox rows may carry segment-style exchanges (``NSE_EQ``, ``NSE_INDEX``,
    ``NSE_FO``, ``MCX_FO``) while Fyers rows carry the base code (``NSE``,
    ``MCX``). The canonical id must converge on the base code so the same
    real instrument registered from different providers matches.
    """
    raw = (exchange or "").strip().upper()
    if not raw:
        return None
    if "_" in raw:
        return raw.split("_", 1)[0]
    return raw


def _canonical_symbol(s: str) -> str:
    """Normalize a symbol/underlying to its canonical form via the alias map."""
    raw = s or ""
    # Strip a leading "EXCH:" prefix (Fyers provider_symbol style).
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    norm = _normalize_symbol(raw)
    return _SYMBOL_ALIASES.get(norm, norm.upper())


def _canonical_type(
    instrument_type: str | None, option_type: str | None
) -> str | None:
    """Map a provider instrument_type (+ option_type) to a canonical type."""
    t = (instrument_type or "").upper()
    if t in _TYPE_ALIASES:
        return _TYPE_ALIASES[t]
    if (option_type or "").upper() in ("CE", "PE"):
        return OPTION
    return None


def _normalize_expiry(raw: Any) -> str | None:
    """Epoch-seconds string or ISO date -> canonical YYYY-MM-DD."""
    if raw in (None, "", 0):
        return None
    s = str(raw)
    if s.isdigit():
        try:
            return datetime.fromtimestamp(
                int(s), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    return s[:10]


def _normalize_strike(value: Any) -> str | None:
    """Decimal-normalize a strike so 25000 == 25000.0 -> '25000'."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (ValueError, TypeError):
        return None
    if d == d.to_integral_value():
        return str(int(d))
    return str(d)


class MarketInstrumentIdentityResolver:
    """Thread-safe provider-neutral alias -> canonical-id registry (B2)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._alias_to_canonical: dict[str, str] = {}
        self._canonical_aliases: dict[str, set[str]] = {}
        self._canonical_context: dict[str, dict[str, Any]] = {}

    # -- registration ------------------------------------------------------

    def register(
        self, canonical_id: str, aliases: Iterable[str]
    ) -> dict[str, Any]:
        """Bind aliases to one canonical id.

        Returns ``{"registered": n_added, "rejected": [alias, ...]}``. An
        alias is rejected only when it is already bound to a DIFFERENT
        canonical id (ambiguity — never silently re-pointed). Same-pair
        re-registration is idempotent.
        """
        canonical_id = str(canonical_id)
        if not canonical_id:
            return {"registered": 0, "rejected": []}
        registered = 0
        rejected: list[str] = []
        with self._lock:
            if canonical_id not in self._canonical_aliases:
                self._canonical_aliases[canonical_id] = set()
            for alias in aliases:
                if not alias:
                    continue
                alias = str(alias)
                existing = self._alias_to_canonical.get(alias)
                if existing == canonical_id:
                    continue                      # idempotent
                if existing is not None:
                    logger.warning(
                        "condition identity alias collision: '%s' already "
                        "maps to '%s'; request for '%s' rejected",
                        alias, existing, canonical_id)
                    rejected.append(alias)
                    continue
                self._alias_to_canonical[alias] = canonical_id
                self._canonical_aliases[canonical_id].add(alias)
                registered += 1
        return {"registered": registered, "rejected": rejected}

    def canonical_id_for_row(self, row: dict[str, Any]) -> str | None:
        """Compute the provider-neutral canonical id for one catalog row."""
        exchange = _canonical_exchange(row.get("exchange"))
        if not exchange:
            return None
        inst_type = _canonical_type(
            row.get("instrument_type"), row.get("option_type"))
        if inst_type is None:
            return None
        if inst_type in (EQUITY, ETF):
            isin = (row.get("isin") or "").strip()
            if isin:
                return f"{exchange}:{inst_type}:{isin}"
            symbol = _canonical_symbol(row.get("tradingsymbol") or "")
            if not symbol:
                return None
            return f"{exchange}:{inst_type}:{symbol}"
        if inst_type == INDEX:
            symbol = _canonical_symbol(
                row.get("tradingsymbol") or row.get("name") or "")
            if not symbol:
                return None
            return f"{exchange}:{INDEX}:{symbol}"
        if inst_type == FUTURE:
            underlying = _canonical_symbol(row.get("underlying") or "")
            expiry = _normalize_expiry(row.get("expiry"))
            if not underlying or not expiry:
                return None
            return f"{exchange}:{FUTURE}:{underlying}:{expiry}"
        if inst_type == OPTION:
            underlying = _canonical_symbol(row.get("underlying") or "")
            expiry = _normalize_expiry(row.get("expiry"))
            strike = _normalize_strike(row.get("strike"))
            option_type = (row.get("option_type") or "").upper()
            if not underlying or not expiry or not strike \
                    or option_type not in ("CE", "PE"):
                return None
            return (f"{exchange}:{OPTION}:{underlying}:{expiry}:"
                    f"{strike}:{option_type}")
        return None

    def register_catalog_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Register one catalog row's identifiers against its canonical id."""
        canonical_id = self.canonical_id_for_row(row)
        if canonical_id is None:
            return {"registered": 0, "rejected": []}
        aliases = [row.get("instrument_token"), row.get("tradingsymbol"),
                   row.get("provider_symbol")]
        result = self.register(canonical_id, aliases)
        # Store display/derivative context for the canonical id (first row
        # wins; provider rows for the same instrument agree on these).
        with self._lock:
            ctx = self._canonical_context.setdefault(canonical_id, {})
            if ctx.get("instrument_type") is None:
                ctx["instrument_type"] = _canonical_type(
                    row.get("instrument_type"), row.get("option_type"))
            for key in ("name", "tradingsymbol", "underlying", "option_type"):
                if row.get(key) is not None and ctx.get(key) is None:
                    ctx[key] = row[key]
            if ctx.get("expiry") is None:
                ctx["expiry"] = _normalize_expiry(row.get("expiry"))
            if ctx.get("strike") is None and row.get("strike") is not None:
                ctx["strike"] = row["strike"]
            if ctx.get("exchange") is None:
                ctx["exchange"] = _canonical_exchange(row.get("exchange"))
        return result

    def register_catalog_rows(
        self, rows: Iterable[dict[str, Any]]
    ) -> dict[str, Any]:
        """Register many catalog rows; aggregates registered/rejected counts."""
        total: dict[str, Any] = {"registered": 0, "rejected": []}
        for row in rows:
            result = self.register_catalog_row(row)
            total["registered"] += result["registered"]
            total["rejected"].extend(result["rejected"])
        return total

    # -- resolution --------------------------------------------------------

    def resolve(self, any_id: str | None) -> str | None:
        """Resolve any known identifier to its canonical id (or None)."""
        if not any_id:
            return None
        with self._lock:
            direct = self._alias_to_canonical.get(str(any_id))
        if direct is not None:
            return direct
        # A canonical id resolves to itself even with no extra aliases.
        with self._lock:
            if any_id in self._canonical_aliases:
                return str(any_id)
        return None

    def resolve_quote(self, quote: Any) -> str | None:
        """Resolve a canonical Quote to its provider-neutral canonical id."""
        for alias in (quote.instrument_token, quote.tradingsymbol):
            cid = self.resolve(alias)
            if cid:
                return cid
        return None

    def aliases_for(self, canonical_id: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._canonical_aliases.get(
                str(canonical_id), ()))

    def context_for(self, canonical_id: str) -> dict[str, Any]:
        """Display/derivative context for a canonical id (or empty dict)."""
        with self._lock:
            return dict(self._canonical_context.get(str(canonical_id), {}))