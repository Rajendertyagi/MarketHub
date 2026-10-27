"""Canonical instrument identity + provider-native identifier roles.

ONE authoritative derivation of "which real instrument is this", plus the
provider-native identifiers needed to actually REQUEST it.

Three distinct concepts (Phase A):

* **Canonical identity** — which real instrument/contract this is:
  ``{exchange}:{TYPE}:{underlying}:{expiry}:{strike}:{CE|PE}`` (options),
  ``{exchange}:FUTURE:{underlying}:{expiry}``,
  ``{exchange}:{EQUITY|ETF}:{ISIN|symbol}``, ``{exchange}:INDEX:{symbol}``.
  The same real instrument derives the SAME canonical id from every provider.

* **Provider-native identifiers** — how the provider addresses it, BY ROLE:
  ``feed_key`` (stream/subscribe), ``rest_key`` (REST quote/chain), and the
  provider's own quote identifier. These are NOT interchangeable.

* **Storage key** — the key under which legacy quote state is written/read
  (unchanged; this module never re-keys the quote store).

The derivation lives here so there is exactly ONE canonical form. The
registries that consume it (``app/instrument_identity.py`` for the
feed/storage-key lookup, the condition-alert resolver) are lookups OVER
this derivation, not independent authorities.

This module imports no broker, transport or app-composition code.
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
    "niftynext50": "NIFTYNXT50",
    "niftyfpi": "NIFTYFPI",
    "niftyfpi150": "NIFTYFPI",
    "niftyfpi150index": "NIFTYFPI",
    "niftymidcap50": "NIFTYMIDCAP50",
    "niftysmallcap250": "NIFTYSMALLCAP250",
}

# ---------------------------------------------------------------------------
# Provider-native identifier ROLES
# ---------------------------------------------------------------------------
#
# A provider addresses the same instrument differently depending on the API:
#
#   * ``feed_key``  — the key the streaming feed subscribes with and the
#     quote store is written under (Upstox ``NSE_FO|51352``,
#     Fyers ``NSE:ADANIGREEN-EQ``).
#   * ``rest_key``  — the identifier a REST quote/option-chain request uses
#     (Upstox ``NSE_FO|51352``; Fyers REST quotes use the API symbol
#     ``NSE:ADANIGREEN-EQ`` while its option-chain endpoint addresses an
#     UNDERLYING symbol, resolved per request — see the Fyers adapter).
#
# The catalogue stores, per row:
#   * ``instrument_token``  — the provider's own token (Upstox: the feed key;
#     Fyers: the numeric fyToken).
#   * ``provider_symbol``   — the provider's own symbol (Upstox: the feed key;
#     Fyers: ``EXCH:SYMBOL``).
#
# ``provider_symbol`` is therefore the provider's FEED key, and for Fyers the
# feed key IS the API symbol. These are computed from the row, never guessed
# from an identifier's string shape.

ROLE_FEED = "feed_key"
ROLE_REST = "rest_key"


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


def _row_underlying(row: dict[str, Any]) -> str:
    """Canonical underlying symbol for a catalogue row.

    Derivatives carry the underlying in ``underlying``; cash rows carry it in
    ``underlying`` too (canonicalized at ingestion). INDEX rows are the one
    case where providers disagree: Upstox publishes no ``underlying`` for an
    index, Fyers publishes the canonical symbol. The provider-native
    tradingsymbol is therefore the fallback, and the alias map reconciles
    the two forms (``Nifty 50`` <-> ``NIFTY``).
    """
    raw = row.get("underlying") or row.get("tradingsymbol") or ""
    return _canonical_symbol(str(raw))


def canonical_id_for_row(row: dict[str, Any]) -> str | None:
    """Compute the provider-neutral canonical id for one catalogue row.

    The SAME derivation is used for every provider, so the same real
    instrument yields the same id regardless of which provider's row is
    presented.
    """
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
        symbol = _row_underlying(row)
        if not symbol:
            return None
        return f"{exchange}:{INDEX}:{symbol}"
    if inst_type == FUTURE:
        underlying = _row_underlying(row)
        expiry = _normalize_expiry(row.get("expiry"))
        if not underlying or not expiry:
            return None
        return f"{exchange}:{FUTURE}:{underlying}:{expiry}"
    if inst_type == OPTION:
        underlying = _row_underlying(row)
        expiry = _normalize_expiry(row.get("expiry"))
        strike = _normalize_strike(row.get("strike"))
        option_type = (row.get("option_type") or "").upper()
        if not underlying or not expiry or not strike \
                or option_type not in ("CE", "PE"):
            return None
        return (f"{exchange}:{OPTION}:{underlying}:{expiry}:"
                f"{strike}:{option_type}")
    return None


def provider_identifiers_for_row(row: dict[str, Any]) -> dict[str, str | None]:
    """Provider-native identifiers for one catalogue row, BY ROLE.

    Returns ``{"provider", "token", "feed_key", "rest_key", "exchange",
    "tradingsymbol", "underlying"}``.

    * ``token``    — the catalogue's provider token (Upstox feed key /
      Fyers fyToken). NEVER sent to a provider endpoint that does not
      accept it.
    * ``feed_key`` — the key the provider's STREAM uses and the quote store
      is written under. Upstox: the instrument key. Fyers: the API symbol
      (``EXCH:SYMBOL``) — which is exactly what the Fyers normalizer stores
      as the quote's ``instrument_token``.
    * ``rest_key`` — the identifier a REST request addresses. For Upstox
      this is the instrument key; for Fyers it is the API symbol (the
      option-chain endpoint addresses an UNDERLYING symbol instead, which
      the Fyers adapter resolves per request).
    """
    provider = str(row.get("provider") or "") or None
    token = (row.get("instrument_token") or None)
    provider_symbol = (row.get("provider_symbol") or None)
    exchange = _canonical_exchange(row.get("exchange"))
    tradingsymbol = (row.get("tradingsymbol") or None)
    underlying = _row_underlying(row) or None

    feed_key = provider_symbol or token
    rest_key = provider_symbol or token
    if provider == "upstox":
        # Upstox's instrument key IS both the feed key and the REST key.
        feed_key = token or provider_symbol
        rest_key = token or provider_symbol
    return {
        "provider": provider,
        "token": token,
        "feed_key": feed_key,
        "rest_key": rest_key,
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "underlying": underlying,
    }


def provider_quote_key(row: dict[str, Any]) -> str | None:
    """The key under which this provider's QUOTE STATE is written.

    For Upstox that is the instrument key; for Fyers the normalizer stores
    the API symbol as the quote's ``instrument_token``
    (``market/normalize/fyers.py``), so the API symbol is the Fyers quote
    key. This is the EXISTING storage semantics — it is read, never
    re-keyed.
    """
    ids = provider_identifiers_for_row(row)
    return ids["feed_key"]


def row_aliases(row: dict[str, Any]) -> list[str]:
    """Every identifier a row may be looked up by, for alias registration.

    A row's aliases are the identifiers that NAME **this** instrument:

    * ``instrument_token`` — the provider's own token;
    * ``provider_symbol``  — the provider's own symbol;
    * ``tradingsymbol``    — the provider's trading symbol;
    * the provider-neutral canonical id.

    A derivative row's ``underlying`` is deliberately NOT an alias: it
    names a DIFFERENT instrument (the cash/index row), so registering it
    against the derivative would make the underlying symbol resolve to a
    contract instead of the instrument itself. The underlying is a
    REFERENCE, resolved through its own row.

    This is what keeps the alias space unambiguous: an alias maps to
    exactly one instrument, and a shared symbol (e.g. ``HAVELLS`` as both
    an equity and the underlying of its options) never collides.
    """
    out: list[str] = []
    for value in (row.get("instrument_token"), row.get("provider_symbol"),
                  row.get("tradingsymbol")):
        if value:
            out.append(str(value))
    # de-duplicate, preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def underlying_reference(row: dict[str, Any]) -> str | None:
    """The underlying a derivative row REFERENCES (not an alias of it).

    For a derivative (FUTURE / OPTION) this is the canonical underlying
    symbol; for a cash/index row it is the instrument itself.
    """
    inst_type = _canonical_type(row.get("instrument_type"),
                                row.get("option_type"))
    if inst_type in (FUTURE, OPTION):
        raw = row.get("underlying") or row.get("tradingsymbol") or ""
        return _canonical_symbol(str(raw)) or None
    return None


class CanonicalInstrumentIdentity:
    """Thread-safe alias -> canonical-id registry (the ONE authority).

    Aliases are provider-native identifiers (token, provider symbol,
    tradingsymbol) plus canonical ids themselves. Collisions are REJECTED
    loudly (never silently re-pointed); re-registering the same alias is
    idempotent.
    """

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
                    logger.debug(
                        "canonical identity alias collision: '%s' already "
                        "maps to '%s'; request for '%s' rejected",
                        alias, existing, canonical_id)
                    rejected.append(alias)
                    continue
                self._alias_to_canonical[alias] = canonical_id
                self._canonical_aliases[canonical_id].add(alias)
                registered += 1
        return {"registered": registered, "rejected": rejected}

    def register_catalog_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Register one catalogue row's identifiers against its canonical id."""
        canonical_id = canonical_id_for_row(row)
        if canonical_id is None:
            return {"registered": 0, "rejected": []}
        result = self.register(canonical_id, row_aliases(row))
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
        """Register many catalogue rows; aggregates registered/rejected counts.

        Rows are GROUPED BY CANONICAL ID first, then each group's alias
        set is bound once. Registering row-by-row would make the outcome
        depend on the order in which the catalogue happened to be read
        (the first provider to publish a shared symbol would win, and the
        other would be rejected as a collision). Grouping makes the
        binding a property of the CANONICAL INSTRUMENT, not of the read
        order — the same real instrument always ends up with the same
        alias set, whatever the catalogue order.

        Emits ONE bounded summary for the whole rebuild instead of a
        warning per collision. Rejection semantics are unchanged: an
        ambiguous alias is never re-pointed.
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows or ():
            canonical_id = canonical_id_for_row(row)
            if canonical_id is None:
                continue
            groups.setdefault(canonical_id, []).append(row)

        total: dict[str, Any] = {"registered": 0, "rejected": []}
        for canonical_id, group in groups.items():
            aliases: list[str] = []
            for row in group:
                for alias in row_aliases(row):
                    if alias not in aliases:
                        aliases.append(alias)
            result = self.register(canonical_id, aliases)
            total["registered"] += result["registered"]
            total["rejected"].extend(result["rejected"])
            self._store_context(canonical_id, group)
        if total["rejected"]:
            samples = ", ".join(repr(a) for a in total["rejected"][:3])
            logger.info(
                "canonical identity collisions: %d ambiguous alias(es) "
                "rejected (first-wins kept; examples: %s%s)",
                len(total["rejected"]), samples,
                " …" if len(total["rejected"]) > 3 else "")
        return total

    def _store_context(self, canonical_id: str,
                       group: list[dict[str, Any]]) -> None:
        """Store display/derivative context for a canonical id."""
        with self._lock:
            ctx = self._canonical_context.setdefault(canonical_id, {})
            for row in group:
                if ctx.get("instrument_type") is None:
                    ctx["instrument_type"] = _canonical_type(
                        row.get("instrument_type"), row.get("option_type"))
                for key in ("name", "tradingsymbol", "underlying",
                            "option_type"):
                    if row.get(key) is not None and ctx.get(key) is None:
                        ctx[key] = row[key]
                if ctx.get("expiry") is None:
                    ctx["expiry"] = _normalize_expiry(row.get("expiry"))
                if ctx.get("strike") is None and row.get("strike") is not None:
                    ctx["strike"] = row["strike"]
                if ctx.get("exchange") is None:
                    ctx["exchange"] = _canonical_exchange(row.get("exchange"))

    # -- resolution --------------------------------------------------------

    def resolve(self, any_id: str | None) -> str | None:
        """Resolve any known identifier to its canonical id (or None)."""
        if not any_id:
            return None
        with self._lock:
            direct = self._alias_to_canonical.get(str(any_id))
        if direct is not None:
            return direct
        with self._lock:
            if any_id in self._canonical_aliases:
                return str(any_id)
        return None

    def resolve_quote(self, quote: Any) -> str | None:
        """Resolve a canonical Quote to its provider-neutral canonical id."""
        for alias in (getattr(quote, "instrument_token", None),
                      getattr(quote, "tradingsymbol", None)):
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
