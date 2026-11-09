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

from typing import Any

from market.index_registry import CANONICAL_INDICES
from market.index_registry import FNO_INDEX_LABELS as _FNO_INDEX_LABELS

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
    {"label": "NIFTYFPI", "fyers_symbol": "NSE:NIFTYFPI150-INDEX",
     "fyers_exchange": "NSE", "upstox_key": "NSE_INDEX|Nifty FPI 150",
     "upstox_exchange": "NSE", "upstox_symbol": "Nifty FPI 150"},
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

# Canonical index option-chain underlyings (liquid F&O option chains).
# ONE source of truth for React + legacy consumers — do NOT duplicate this
# list in the frontend. These are the indices supported by
# /api/options/chain/view.
#
# Derived from the canonical F&O index registry (``market.index_registry``)
# so the picker can never drift from the established universe: every
# canonical F&O index is offered, including NIFTYNXT50 and NIFTYFPI.
OPTION_CHAIN_INDICES: tuple[str, ...] = tuple(
    e.label for e in CANONICAL_INDICES if e.is_fno
)

# Re-exported from the neutral registry (single source of truth for F&O indices).
FNO_INDEX_LABELS = _FNO_INDEX_LABELS


def index_option_underlyings() -> list[dict[str, str]]:
    """Canonical index option-chain underlyings (human labels + exchange).

    Returns the subset of MAJOR_INDICES that support option chains, projected
    to the minimal fields a picker needs. The backend owns this list; the
    frontend consumes it via the API rather than hard-coding it.
    """
    out: list[dict[str, str]] = []
    for label in OPTION_CHAIN_INDICES:
        entry = _BY_LABEL.get(label)
        if entry is None:
            continue
        out.append({
            "label": entry["label"],
            "exchange": entry["upstox_exchange"],
        })
    return out


# Catalog `underlying` column values (and labels) -> canonical label.
# Exact normalized match only.
#
# The canonical label set is owned by ``market.index_registry``; this map
# only bridges the CATALOGUE's spelling of an index underlying to that
# canonical label. Every F&O index must appear here, including NIFTYFPI
# (whose catalogue symbol is ``NSE:NIFTYFPI150-INDEX`` while its canonical
# label is ``NIFTYFPI``).
_UNDERLYING_TO_LABEL = {
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "MIDCPNIFTY": "MIDCPNIFTY",
    "NIFTYNXT50": "NIFTYNXT50",
    "NIFTYFPI": "NIFTYFPI",
    "NIFTYFPI150": "NIFTYFPI",
    "NIFTYFPI150INDEX": "NIFTYFPI",
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


def _catalog_index_row(catalog: Any, label: str) -> dict[str, Any] | None:
    """The catalogue's INDEX row for a canonical index label.

    The catalogue is the authority on the provider's OWN identifier for an
    index. The lookup matches the canonical label against the row's
    ``underlying`` (the canonicalized column both providers populate for
    derivatives) and, failing that, against the label's own symbol.
    """
    for row in _catalog_index_rows(catalog, label):
        return row
    return None


def _catalog_index_rows(catalog: Any, label: str) -> list[dict[str, Any]]:
    """Every catalogue INDEX row for a canonical index label.

    One row PER PROVIDER — the index is listed by both providers, and each
    has its own identifier for it. The rows are returned in catalogue
    order; the caller decides the preference.

    The label is matched against the row's ``underlying`` (the canonicalized
    column) OR its tradingsymbol: Upstox publishes an index row with the
    canonical symbol as its tradingsymbol and no ``underlying`` at all.

    The EXACT-symbol read model is used — the general catalogue search is
    a substring LIKE with a LIMIT, so it could truncate before reaching
    the row (and could match an unrelated index).
    """
    if catalog is None or not label:
        return []
    want = _norm(label)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    try:
        finder = getattr(catalog, "find_index_rows", None)
        if callable(finder):
            rows = list(finder(label) or [])
        else:
            rows = list(catalog.find_index(label) or [])
    except Exception:
        rows = []
    if not rows:
        try:
            rows = list(catalog.search(instrument_type="INDEX", limit=5000)
                         or [])
        except Exception:
            rows = []
    for row in rows:
        if _norm(row.get("underlying")) != want and \
                _norm(row.get("tradingsymbol")) != want:
            continue
        token = str(row.get("instrument_token") or "")
        if token in seen:
            continue
        seen.add(token)
        out.append(row)
    return out


def index_provider_keys(catalog: Any, label: str) -> dict[str, str]:
    """Provider feed keys for a canonical index label, from the catalogue.

    Returns ``{"upstox": <feed key>, "fyers": <feed key>}`` — the key each
    provider's stream subscribes with and stores quotes under. A provider
    that does not list the index is simply absent (never invented).
    """
    out: dict[str, str] = {}
    for row in _catalog_index_rows(catalog, label):
        try:
            from app.canonical_identity import provider_quote_key
            key = provider_quote_key(row)
        except Exception:
            key = None
        provider = str(row.get("provider") or "")
        if provider and key and provider not in out:
            out[provider] = str(key)
    return out


def quote_basis(quote: Any, canonical_id: str | None = None) -> str:
    """'live' when the quote is fresh, else 'stale' (last-session).

    The verdict uses the SHARED per-instrument-kind freshness policy (an
    INDEX quote is judged by the INDEX window), so the index strip and
    the quote service can never disagree about the same index. A quote
    without a trustworthy timestamp is STALE - "a quote exists" is never
    evidence that it is live.
    """
    if quote is None:
        return "unavailable"
    try:
        from app.quote_service import quote_freshness
        received = getattr(quote, "received_ts", None)
        if received is None and isinstance(quote, dict):
            received = quote.get("received_ts")
        verdict = quote_freshness(canonical_id, received)
        return "live" if verdict["live"] else "stale"
    except Exception:
        return "stale"


def resolve_spot(label_or_underlying: str | None, catalog: Any,
                 spot_provider: Any,
                 identity_resolve: Any = None) -> dict[str, Any]:
    """Resolve one index to its quote via ordered provider candidates.

    Returns {"quote", "provider", "exchange", "instrument_token",
    "basis", "entry"}.

    Candidates come from the CATALOGUE (each provider's own feed key for
    the index), NOT from a hardcoded per-provider key table. The registry
    (``identity_resolve``) maps any known identifier — the catalogue
    token, the tradingsymbol — onto the same storage key, so a lookup by
    any of them reaches the same quote state. The static
    ``MAJOR_INDICES`` keys remain as a last-resort fallback for a provider
    the catalogue has not synced yet.

    A non-index underlying yields {"quote": None,
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

    def _resolve(candidate: Any) -> Any:
        if not candidate:
            return None
        if identity_resolve is not None:
            try:
                return identity_resolve(candidate) or candidate
            except Exception:
                return candidate
        return candidate

    # Ordered candidates per provider: catalogue feed key first (the
    # provider's own identifier), then the static registry fallback.
    candidates: list[tuple[str, str, str]] = []
    catalogue_keys = index_provider_keys(catalog, entry["label"])
    for provider, key in catalogue_keys.items():
        candidates.append((provider, entry.get(f"{provider}_exchange")
                           or entry["upstox_exchange"], key))
    if "upstox" not in catalogue_keys:
        candidates.append(("upstox", entry["upstox_exchange"],
                           entry["upstox_key"]))
    if "fyers" not in catalogue_keys:
        candidates.append(("fyers", entry["fyers_exchange"],
                           entry["fyers_symbol"]))
    # The catalogue row's own token/tradingsymbol, resolved through the
    # registry, so a provider that stores under a different key still
    # resolves.
    row = _catalog_index_row(catalog, entry["label"])
    canonical_id = None
    if row is not None:
        try:
            from app.canonical_identity import canonical_id_for_row
            canonical_id = canonical_id_for_row(row)
        except Exception:
            canonical_id = None
        for candidate in (row.get("instrument_token"),
                          row.get("tradingsymbol")):
            if not candidate:
                continue
            resolved = _resolve(candidate)
            provider = str(row.get("provider") or "")
            exchange = str(row.get("exchange") or "")
            if provider and resolved:
                candidates.append((provider, exchange, str(resolved)))

    seen: set[tuple[str, str]] = set()
    for provider, exchange, token in candidates:
        if not token:
            continue
        marker = (provider, token)
        if marker in seen:
            continue
        seen.add(marker)
        quote = _try(exchange, token)
        if quote is not None:
            return {"quote": quote, "provider": provider,
                    "exchange": exchange,
                    "instrument_token": token,
                    "canonical_id": canonical_id,
                    "basis": quote_basis(quote, canonical_id), "entry": entry}
    return {"quote": None, "basis": "unavailable", "entry": entry}


def _mini_quote(quote: Any, provider: str | None = None,
                canonical_id: str | None = None) -> dict[str, Any] | None:
    """Minimal serializable quote for the header strip.

    It carries the SAME provenance the canonical quote payload carries
    (provider, canonical id and ``as_of``), so the header strip can never
    claim a value without saying where and when it came from.
    """
    if quote is None:
        return None
    if isinstance(quote, dict):
        get = quote.get
    else:
        def get(k, _quote=quote):
            return getattr(_quote, k, None)
    received = get("received_ts")
    if hasattr(received, "isoformat"):
        received = received.isoformat()
    out = {
        "ltp": get("ltp"),
        "change": get("change"),
        "change_percent": get("change_percent"),
        "received_ts": received,
        "as_of": received,
    }
    if provider:
        out["provider"] = provider
    if canonical_id:
        out["canonical_id"] = canonical_id
    return out


def resolve_indices(catalog: Any, spot_provider: Any,
                    identity_resolve: Any = None) -> list[dict[str, Any]]:
    """Endpoint payload: every catalog-confirmed major index + its quote.

    Only indices with an EXACT catalog row are listed — never fabricated.
    """
    out: list[dict[str, Any]] = []
    for entry in MAJOR_INDICES:
        # A catalogue-confirmed index row is required — never fabricated.
        # The row is matched on the canonical label, so an index whose
        # provider symbol differs from the static table (e.g. NIFTYFPI,
        # catalogued as ``NSE:NIFTYFPI150-INDEX``) is still recognised.
        row = _catalog_index_row(catalog, entry["label"])
        if row is None:
            continue
        spot = resolve_spot(entry["label"], catalog, spot_provider,
                            identity_resolve)
        quote = spot["quote"]
        mini = _mini_quote(quote, spot.get("provider"),
                           spot.get("canonical_id"))
        out.append({
            "label": entry["label"],
            "exchange": spot.get("exchange") or entry["fyers_exchange"],
            "instrument_token": spot.get("instrument_token")
            or entry["fyers_symbol"],
            "key": (spot.get("exchange") or entry["fyers_exchange"])
            + ":" + (spot.get("instrument_token")
                     or entry["fyers_symbol"]),
            "provider": spot.get("provider"),
            "canonical_id": spot.get("canonical_id"),
            "symbol": row.get("tradingsymbol"),
            "display_name": row.get("name") or row.get("tradingsymbol"),
            "basis": spot["basis"],
            "as_of": (mini or {}).get("as_of"),
            "quote": mini,
        })
    return out
