"""Canonical major Indian index identities — ONE definition, shared.

Problem this solves: the header index strip, the option-chain spot lookup
and the provider option-quote enrichment each need to turn "NIFTY" into the
concrete (exchange, instrument_token) storage keys each provider's feed
writes to MarketService. Those keys differ by provider:

  * Fyers feed identity  = the API symbol, e.g. ``NSE:NIFTY50-INDEX``
    (NOT the catalog fyToken ``101000000026000``).
  * Upstox feed identity = the Upstox instrument key, e.g.
    ``NSE_INDEX|Nifty 50`` with exchange ``NSE`` (segment prefix stripped
    by the normalizer — see market.normalize.upstox.exchange_from_segment).

The Upstox keys below are verified against Upstox's official instrument
master (assets.upstox.com/.../complete.json.gz, NSE_INDEX/BSE_INDEX
segments); the Fyers symbols are verified against the synced catalog
(EXACT tradingsymbol match — never fuzzy/substring selection).

Provider order (Upstox first, Fyers fallback) is evidence-based, not a
preference hack: a captured Fyers HSM index snapshot for "Nifty 50"
delivered multiplier=28/precision=0 with an ltp (2441760 raw -> 87205.71)
that matches no real NSE value (NIFTY closed 23,897.70 that day), while the
Upstox indexFF path is the same trusted source already serving real option
quotes. When Upstox has no quote, the Fyers candidate still resolves so an
index is "stale" rather than "unavailable".

Nothing here touches the network. Callers supply the catalog (for exact
identity confirmation) and a sync (exchange, token) -> quote reader
(MarketService.get_quote_now).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# A quote older than this is labeled "stale" (last-session), never "live".
# Kept in parity with the WebUI freshness threshold (shell.js STALE_MIN).
STALE_MINUTES = 5

# label, Fyers tradingsymbol, Fyers exchange, Upstox instrument key,
# Upstox storage exchange, Upstox display symbol.
MAJOR_INDICES: tuple[dict[str, str], ...] = (
    {"label": "NIFTY", "fyers_symbol": "NSE:NIFTY50-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|Nifty 50",
     "upstox_exchange": "NSE", "upstox_symbol": "Nifty 50"},
    {"label": "BANKNIFTY", "fyers_symbol": "NSE:NIFTYBANK-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|Nifty Bank",
     "upstox_exchange": "NSE", "upstox_symbol": "Nifty Bank"},
    {"label": "FINNIFTY", "fyers_symbol": "NSE:FINNIFTY-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|Nifty Fin Service",
     "upstox_exchange": "NSE", "upstox_symbol": "Nifty Fin Service"},
    {"label": "MIDCPNIFTY", "fyers_symbol": "NSE:MIDCPNIFTY-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|NIFTY MID SELECT",
     "upstox_exchange": "NSE", "upstox_symbol": "NIFTY MID SELECT"},
    {"label": "NIFTYNXT50", "fyers_symbol": "NSE:NIFTYNXT50-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|Nifty Next 50",
     "upstox_exchange": "NSE", "upstox_symbol": "Nifty Next 50"},
    {"label": "INDIA VIX", "fyers_symbol": "NSE:INDIAVIX-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|India VIX",
     "upstox_exchange": "NSE", "upstox_symbol": "India VIX"},
    {"label": "SENSEX", "fyers_symbol": "BSE:SENSEX-INDEX",
     "fyers_exchange": "BSE", "upstox_key": "BSE_INDEX|SENSEX",
     "upstox_exchange": "BSE", "upstox_symbol": "SENSEX"},
    {"label": "BANKEX", "fyers_symbol": "BSE:BANKEX-INDEX",
     "fyers_exchange": "BSE", "upstox_key": "BSE_INDEX|BANKEX",
     "upstox_exchange": "BSE", "upstox_symbol": "BANKEX"},
)

_BY_LABEL = {e["label"]: e for e in MAJOR_INDICES}

# Catalog `underlying` column values (and labels) -> canonical label.
# Exact normalized match only.
_UNDERLYING_TO_LABEL = {
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
    "NIFTYNXT50": "NIFTYNXT50",
    "INDIAVIX": "INDIA VIX",
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}


def _norm(value: str | None) -> str:
    return "".join(ch for ch in (value or "").upper() if ch.isalnum())


def label_for_underlying(underlying: str | None) -> str | None:
    """Map a catalog underlying (or label) to its canonical index label."""
    return _UNDERLYING_TO_LABEL.get(_norm(underlying))


def entry_for_label(label: str | None) -> dict[str, str] | None:
    if not label:
        return None
    direct = _BY_LABEL.get(label)
    if direct is not None:
        return direct
    canonical = _UNDERLYING_TO_LABEL.get(_norm(label))
    return _BY_LABEL.get(canonical) if canonical else None


def _catalog_row(catalog: Any, tradingsymbol: str) -> dict[str, Any] | None:
    """EXACT tradingsymbol confirmation — never fuzzy/substring."""
    try:
        rows = catalog.search(q=tradingsymbol, instrument_type="INDEX",
                              limit=25)
    except Exception:
        return None
    for row in rows or []:
        if row.get("tradingsymbol") == tradingsymbol:
            return row
    return None


def quote_basis(quote: Any) -> str:
    """'live' when the quote is fresh, else 'stale' (last-session)."""
    if quote is None:
        return "unavailable"
    try:
        received = getattr(quote, "received_ts", None)
        if received is None and isinstance(quote, dict):
            received = quote.get("received_ts")
        if isinstance(received, str):
            received = datetime.fromisoformat(received)
        if received is None:
            return "stale"
        age_min = (datetime.now(timezone.utc) - received).total_seconds() \
            / 60.0
        return "live" if age_min <= STALE_MINUTES else "stale"
    except Exception:
        return "stale"


def resolve_spot(label_or_underlying: str | None, catalog: Any,
                 spot_provider: Any,
                 identity_resolve: Any = None) -> dict[str, Any]:
    """Resolve one index to its quote via ordered provider candidates.

    Returns {"quote", "provider", "exchange", "instrument_token",
    "basis", "entry"}. Upstox is tried first (see module docstring for the
    evidence), then Fyers via the identity registry with fallback to the
    feed-style symbol. A non-index underlying yields {"quote": None,
    "basis": "unknown-underlying"} so callers fall through to generic
    resolution.
    """
    entry = entry_for_label(label_or_underlying)
    if entry is None:
        return {"quote": None, "basis": "unknown-underlying",
                "entry": None}
    if spot_provider is None:
        return {"quote": None, "basis": "unavailable", "entry": entry}

    def _try(exchange: str, token: str):
        try:
            return spot_provider(exchange, token)
        except Exception:
            return None

    upstox = _try(entry["upstox_exchange"], entry["upstox_key"])
    if upstox is not None:
        return {"quote": upstox, "provider": "upstox",
                "exchange": entry["upstox_exchange"],
                "instrument_token": entry["upstox_key"],
                "basis": quote_basis(upstox), "entry": entry}

    row = _catalog_row(catalog, entry["fyers_symbol"]) if catalog \
        is not None else None
    fyers_tokens: list[str] = []
    if row is not None:
        for candidate in (row.get("instrument_token"),
                          row.get("tradingsymbol")):
            if not candidate:
                continue
            resolved = None
            if identity_resolve is not None:
                try:
                    resolved = identity_resolve(candidate)
                except Exception:
                    resolved = None
            fyers_tokens.append(resolved or candidate)
    fyers_tokens.append(entry["fyers_symbol"])  # feed-style identity
    seen: set[str] = set()
    for token in fyers_tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        quote = _try(entry["fyers_exchange"], token)
        if quote is not None:
            return {"quote": quote, "provider": "fyers",
                    "exchange": entry["fyers_exchange"],
                    "instrument_token": token,
                    "basis": quote_basis(quote), "entry": entry}
    return {"quote": None, "basis": "unavailable", "entry": entry}


def _mini_quote(quote: Any) -> dict[str, Any] | None:
    """Minimal serializable quote for the header strip."""
    if quote is None:
        return None
    if isinstance(quote, dict):
        get = quote.get
    else:
        get = lambda k: getattr(quote, k, None)  # noqa: E731
    received = get("received_ts")
    if hasattr(received, "isoformat"):
        received = received.isoformat()
    return {
        "ltp": get("ltp"),
        "change": get("change"),
        "change_percent": get("change_percent"),
        "received_ts": received,
    }


def resolve_indices(catalog: Any, spot_provider: Any,
                    identity_resolve: Any = None) -> list[dict[str, Any]]:
    """Endpoint payload: every catalog-confirmed major index + its quote.

    Only indices with an EXACT catalog row are listed — never fabricated.
    """
    out: list[dict[str, Any]] = []
    for entry in MAJOR_INDICES:
        row = _catalog_row(catalog, entry["fyers_symbol"]) if catalog \
            is not None else None
        if row is None:
            continue
        spot = resolve_spot(entry["label"], catalog, spot_provider,
                            identity_resolve)
        quote = spot["quote"]
        out.append({
            "label": entry["label"],
            "exchange": spot.get("exchange") or entry["fyers_exchange"],
            "instrument_token": spot.get("instrument_token")
            or entry["fyers_symbol"],
            "key": (spot.get("exchange") or entry["fyers_exchange"])
            + ":" + (spot.get("instrument_token")
                     or entry["fyers_symbol"]),
            "provider": spot.get("provider"),
            "symbol": row.get("tradingsymbol"),
            "display_name": row.get("name") or row.get("tradingsymbol"),
            "basis": spot["basis"],
            "quote": _mini_quote(quote),
        })
    return out
