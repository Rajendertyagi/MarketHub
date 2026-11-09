"""The single canonical quote service (REST, SSE, MCP, analytics, alerts).

Every consumer reads quotes through this service, so they all see the
SAME value, the SAME freshness verdict and the SAME provenance.

Rules:

* **Identity**: the canonical instrument id is the only identity. A
  provider-native storage key is a store detail, never an identity.
* **Provider**: resolved from the CATALOGUE (which provider published the
  key), with the provider registry's key styles only as a fallback - the
  shape of a key never decides the provider.
* **Selection**: among the fresh candidates for a canonical instrument
  the most recent wins; provider preference is a tie-break only.
* **Staleness**: a fresh quote is one whose age is within the freshness
  window (the project's documented 5-minute "last session" threshold,
  configurable per use case). When ONLY stale data exists the last known
  value is KEPT and returned with ``stale: true``, its ``as_of``
  timestamp and its source - never dropped, never called live.
* **Liveness**: derived from the actual data recency, never from a
  connected socket or an open SSE session.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("event_server")

#: The project's documented freshness threshold for an instrument whose
#: kind is unknown (the fallback of the per-kind policy below).
DEFAULT_FRESH_WINDOW_SECONDS = 120.0

#: The freshness policy, PER INSTRUMENT KIND.
#:
#: A liquid index or equity ticks at least once per second while the
#: session is open, so an update that is missing for a minute means the
#: FEED stopped - not that the instrument is quiet. A derivative contract
#: updates on trades only, so it gets a wider window. This is a POLICY,
#: not a fact about the feed: it is configurable (constructor argument or
#: ``freshness_windows`` in config.json).
DEFAULT_FRESH_WINDOWS: dict[str, float] = {
    "INDEX": 60.0,
    "EQUITY": 60.0,
    "ETF": 60.0,
    "FUTURE": 120.0,
    "OPTION": 120.0,
}

STATE_LIVE = "live"
STATE_STALE = "stale"
STATE_UNAVAILABLE = "unavailable"


def fresh_window_for_canonical(canonical_id: str | None,
                                windows: dict[str, float] | None = None,
                                default: float = DEFAULT_FRESH_WINDOW_SECONDS
                                ) -> float:
    """The freshness window of an instrument KIND (the shared policy).

    A liquid index or equity ticks at least once per second while the
    session is open, so an update that is missing for a minute means the
    FEED stopped - not that the instrument is quiet. A derivative contract
    updates on trades only, so it gets a wider window.

    Every consumer of a freshness verdict uses THIS function, so the read
    path and the alert engine can never disagree about the same quote.
    """
    kind = ""
    try:
        parts = str(canonical_id or "").split(":")
        if len(parts) >= 2:
            kind = parts[1].upper()
    except Exception:
        kind = ""
    table = dict(DEFAULT_FRESH_WINDOWS)
    table.update({str(k).upper(): float(v)
                  for k, v in (windows or {}).items()})
    return float(table.get(kind, default))


def quote_freshness(canonical_id: str | None, received: Any, *,
                    windows: dict[str, float] | None = None,
                    default_window: float = DEFAULT_FRESH_WINDOW_SECONDS,
                    now: datetime | None = None) -> dict[str, Any]:
    """The SHARED freshness verdict for one quote.

    The policy is per instrument KIND (the same table the quote service
    uses), and it FAILS CLOSED: a quote without a trustworthy timestamp
    is STALE - "a quote exists" is never evidence that it is live.

    Consumers that only have a raw quote (the chat tool, MarketIntel)
    use this instead of inventing their own verdict.
    """
    kind = ""
    try:
        parts = str(canonical_id or "").split(":")
        if len(parts) >= 2:
            kind = parts[1].upper()
    except Exception:
        kind = ""
    table = dict(DEFAULT_FRESH_WINDOWS)
    table.update({str(k).upper(): float(v)
                  for k, v in (windows or {}).items()})
    window = float(table.get(kind, default_window))
    moment: datetime | None = None
    if isinstance(received, datetime):
        moment = received
    elif isinstance(received, str) and received:
        try:
            moment = datetime.fromisoformat(
                received.replace("Z", "+00:00"))
        except Exception:
            moment = None
    age: float | None = None
    if moment is not None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        try:
            age = ((now or datetime.now(timezone.utc)) - moment).total_seconds()
        except Exception:
            age = None
    stale = age is None or age > window
    return {
        "received_at": received.isoformat()
        if hasattr(received, "isoformat") else received,
        "age_seconds": age,
        "stale": stale,
        "live": not stale,
        "state": STATE_STALE if stale else STATE_LIVE,
        "fresh_window_seconds": window,
    }


class QuoteService:
    """Read-side access to the canonical quote store."""

    def __init__(
        self,
        market_service: Any,
        *,
        identity_registry: Any = None,
        catalog: Any = None,
        registry: Any = None,
        fresh_window_seconds: float = DEFAULT_FRESH_WINDOW_SECONDS,
        fresh_windows: dict[str, float] | None = None,
        provider_of: Callable[[str], str] | None = None,
    ) -> None:
        self._market_service = market_service
        self._identity = identity_registry
        self._catalog = catalog
        self._registry = registry
        self._fresh_window = float(fresh_window_seconds)
        self._fresh_windows = dict(DEFAULT_FRESH_WINDOWS)
        self._fresh_windows.update(
            {str(k).upper(): float(v)
             for k, v in (fresh_windows or {}).items()})
        self._provider_of = provider_of or self._catalogue_provider
        # Cache of catalogue rows by reference (the catalogue is read-only
        # for a reader; the epoch changes on every sync).
        self._row_cache: dict[str, dict[str, Any]] = {}
        # Cache of the store keys per reference (the resolution is a pure
        # function of the catalogue).
        self._keys_cache: dict[str, list[tuple[str, str]]] = {}
        # Narrow lock for cache mutations: quote_now/storage_keys may run in
        # worker threads (scanner offload). Protects epoch check/clear/read/write;
        # catalog/market lookups happen outside the lock.
        self._cache_lock = threading.RLock()
        self._catalog_epoch: Any = None

    def _refresh_catalog_cache(self) -> None:
        """Drop the catalogue read models when the catalogue changed."""
        epoch = None
        try:
            epoch = self._catalog.catalog_epoch() if self._catalog else None
        except Exception:
            epoch = None
        with self._cache_lock:
            if epoch != self._catalog_epoch:
                self._catalog_epoch = epoch
                self._row_cache.clear()
                self._keys_cache.clear()

    # -- freshness policy ----------------------------------------------------

    def fresh_window_for(self, canonical_id: str | None) -> float:
        """The freshness window of an instrument kind (seconds).

        The window is derived from the canonical id's kind, so an index
        quote and an option quote are judged by their own policy instead
        of one blanket timeout.
        """
        return fresh_window_for_canonical(
            canonical_id, self._fresh_windows, self._fresh_window)

    def _is_fresh(self, quote: Any, now: datetime | None = None
                  ) -> bool:
        """True when the quote's data is within its kind's window."""
        received = getattr(quote, "received_ts", None)
        if received is None:
            return False
        moment = now or datetime.now(timezone.utc)
        try:
            age = (moment - received).total_seconds()
        except TypeError:
            return False
        cid = self.canonical_id_for(
            getattr(quote, "instrument_token", None)) \
            or getattr(quote, "canonical_id", None)
        return age <= self.fresh_window_for(cid)

    # -- helpers --------------------------------------------------------------

    def _catalogue_provider(self, key: str) -> str:
        """The provider that owns a storage key (catalogue first)."""
        if not key:
            return "unknown"
        try:
            if self._registry is None:
                from providers.registry import get_registry
                self._registry = get_registry()
            provider = self._registry.provider_for_key(
                key, catalog=self._catalog)
            if provider:
                return str(provider)
        except Exception:
            pass
        from market.quote_coordinator import provider_of_key
        return provider_of_key(key)

    # -- canonical identity ---------------------------------------------------

    def _catalogue_row(self, reference: str | None) -> dict[str, Any] | None:
        """The catalogue row that OWNS a reference (provider key or ISIN).

        The catalogue is the authority: a provider-native key and a cash
        instrument's ISIN both resolve to the row that published them.
        """
        ref = str(reference or "").strip()
        if not ref or self._catalog is None:
            return None
        self._refresh_catalog_cache()
        with self._cache_lock:
            cached = self._row_cache.get(ref)
        if cached is not None:
            return cached or None
        row = None
        try:
            row = self._catalog.get_by_key(ref)
        except Exception:
            row = None
        if not row:
            # The ISIN form: a cash instrument's cross-provider identity.
            try:
                if len(ref) == 12 and ref[:3].upper() == "INE":
                    rows = self._catalog.search(isin=ref, limit=20) or []
                    row = rows[0] if rows else None
            except Exception:
                row = None
        with self._cache_lock:
            try:
                epoch = int(self._catalog.catalog_epoch()) if self._catalog else 0
            except Exception:
                epoch = 0
            if epoch == self._catalog_epoch:
                if len(self._row_cache) > 4096:
                    self._row_cache.clear()
                self._row_cache[ref] = row or {}
        return row

    def canonical_id_for(self, reference: str | None) -> str | None:
        """The AUTHORITATIVE canonical id of any known reference.

        One instrument, one identity: the provider-neutral canonical id
        derived from the catalogue row that owns the reference. A
        provider-native key stays a STORAGE key (it is never an identity),
        and a reference that is not known resolves to itself.
        """
        ref = str(reference or "").strip()
        if not ref:
            return None
        if ref.count(":") >= 2:
            # Already the canonical form (EXCHANGE:KIND:...).
            return ref
        row = self._catalogue_row(ref)
        if row:
            try:
                from app.canonical_identity import canonical_id_for_row
                cid = canonical_id_for_row(row)
                if cid:
                    return cid
            except Exception:
                pass
        resolved = self.resolve_canonical(ref)
        if resolved and resolved != ref:
            row = self._catalogue_row(resolved)
            if row:
                try:
                    from app.canonical_identity import canonical_id_for_row
                    cid = canonical_id_for_row(row)
                    if cid:
                        return cid
                except Exception:
                    pass
        return None

    def _quote_ids(self, quote: Any) -> set[str]:
        """Every id a stored quote answers to."""
        ids: set[str] = set()
        for value in (getattr(quote, "canonical_id", None),
                      getattr(quote, "instrument_token", None),
                      getattr(quote, "tradingsymbol", None)):
            if value:
                ids.add(str(value))
        own = getattr(quote, "canonical_id", None)
        if own:
            resolved = self.resolve_canonical(str(own))
            if resolved:
                ids.add(str(resolved))
        neutral = self.canonical_id_for(
            getattr(quote, "instrument_token", None))
        if neutral:
            ids.add(neutral)
        return ids

    def _reference_ids(self, reference: str | None) -> set[str]:
        """Every id a reference may be known by."""
        ref = str(reference or "").strip()
        if not ref:
            return set()
        ids = {ref}
        resolved = self.resolve_canonical(ref)
        if resolved:
            ids.add(str(resolved))
        neutral = self.canonical_id_for(ref)
        if neutral:
            ids.add(neutral)
        return ids

    def storage_keys(self, reference: str | None
                     ) -> list[tuple[str, str]]:
        """The (exchange, storage key) pairs that may hold a reference.

        Every provider that lists the instrument contributes its own key,
        so a quote served by ANY provider is found - the consumer never
        assumes the serving provider's key.
        """
        out: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def _add(exchange: Any, key: Any) -> None:
            if not key:
                return
            pair = (str(exchange or ""), str(key))
            if pair in seen:
                return
            seen.add(pair)
            out.append(pair)

        ref = str(reference or "").strip()
        if not ref:
            return out
        self._refresh_catalog_cache()
        with self._cache_lock:
            cached = self._keys_cache.get(ref)
        if cached is not None:
            return list(cached)
        row = self._catalogue_row(ref)
        cid = self.canonical_id_for(ref)
        if row is None and cid:
            try:
                rows = self._catalog.find_canonical_rows(cid) if \
                    self._catalog is not None else []
            except Exception:
                rows = []
            for r in rows or ():
                row = r
                break
        if row is not None:
            try:
                from app.canonical_identity import provider_quote_key
                key = provider_quote_key(row)
            except Exception:
                key = None
            _add(row.get("exchange"), key)
        if cid and self._catalog is not None:
            try:
                rows = self._catalog.find_canonical_rows(cid) or []
            except Exception:
                rows = []
            for r in rows:
                try:
                    from app.canonical_identity import provider_quote_key
                    key = provider_quote_key(r)
                except Exception:
                    key = None
                _add(r.get("exchange"), key)
        resolved = self.resolve_canonical(ref)
        if resolved:
            _add(None, resolved)
        with self._cache_lock:
            try:
                epoch = int(self._catalog.catalog_epoch()) if self._catalog else 0
            except Exception:
                epoch = 0
            if epoch == self._catalog_epoch:
                if len(self._keys_cache) > 4096:
                    self._keys_cache.clear()
                self._keys_cache[ref] = list(out)
        return out

    def quote_now(self, reference: str | None,
                  exchange: str | None = None) -> Any:
        """The stored quote for a reference (synchronous, canonical-aware).

        The store is keyed by the provider's storage key; the reference may
        be any known identifier. Every candidate key of the instrument is
        tried, so a quote served by another provider is still found.
        """
        market = self._market_service
        if market is None or not reference:
            return None
        get_now = getattr(market, "get_quote_now", None)
        if get_now is None:
            return None
        ref = str(reference).strip()
        candidates = self.storage_keys(ref)
        for ex, key in candidates:
            exchanges = [ex] if ex else []
            if exchange:
                exchanges.insert(0, str(exchange))
            if "|" in key:
                exchanges.append(key.split("|", 1)[0].split("_", 1)[0])
            for candidate_ex in exchanges:
                try:
                    quote = get_now(candidate_ex, key)
                except Exception:
                    quote = None
                if quote is not None:
                    return quote
        # The reference may already be the store key.
        try:
            quote = get_now(str(exchange or ""), ref)
        except Exception:
            quote = None
        if quote is not None:
            return quote
        return None

    def resolve_canonical(self, reference: str | None) -> str | None:
        """Resolve any identifier to a canonical instrument id."""
        if not reference:
            return None
        if self._identity is not None:
            try:
                resolved = self._identity.resolve(reference)
                if resolved:
                    return str(resolved)
            except Exception:
                pass
        return str(reference)

    def _candidates(self, canonical_id: str) -> list[Any]:
        """Every stored quote for a canonical instrument."""
        try:
            quotes = self._market_service.quotes()
        except Exception:
            return []
        if hasattr(quotes, "__await__"):
            return quotes  # type: ignore[return-value]
        return [q for q in quotes if getattr(q, "canonical_id", None) == canonical_id]

    async def _quotes(self) -> tuple[Any, ...]:
        quotes = self._market_service.quotes()
        if hasattr(quotes, "__await__"):
            quotes = await quotes
        return tuple(quotes or ())

    # -- reads ----------------------------------------------------------------

    async def quote(self, canonical_id: str) -> dict[str, Any] | None:
        """The selected quote for one canonical instrument (fresh or stale)."""
        from market.quote_coordinator import select_best_quote

        cid = self.resolve_canonical(canonical_id)
        if not cid:
            return None
        wanted = self._reference_ids(canonical_id)
        quotes = await self._quotes()
        candidates = [q for q in quotes
                      if self._quote_ids(q) & wanted]
        if not candidates:
            # The reference may be a provider STORAGE key: find the
            # instrument it addresses and use ITS canonical id.
            candidates = [q for q in quotes
                          if getattr(q, "instrument_token", None) == cid]
            if candidates:
                cid = getattr(candidates[0], "canonical_id", None) or cid
        fresh = [q for q in candidates if self._is_fresh(q)]
        selection = select_best_quote(
            fresh or candidates, stale_after_seconds=self._fresh_window,
            provider_of=self._catalogue_provider)
        if selection is None:
            return None
        return self._payload(selection)

    async def quotes(self, canonical_ids: list[str] | None = None
                     ) -> list[dict[str, Any]]:
        """The selected quote per canonical instrument.

        Stale-only instruments are INCLUDED (explicitly marked stale).
        """
        from market.quote_coordinator import select_best_quote

        all_quotes = await self._quotes()
        wanted: set[str] = set()
        for cid in canonical_ids or ():
            wanted |= self._reference_ids(cid)
        groups: dict[str, list[Any]] = {}
        for q in all_quotes:
            ids = self._quote_ids(q)
            if not ids:
                continue
            if wanted and not (ids & wanted):
                continue
            # ONE group per INSTRUMENT: the provider-neutral canonical id,
            # so the quotes of two providers for the same instrument are
            # selected against each other instead of reported twice.
            key = self.canonical_id_for(
                getattr(q, "instrument_token", None)) \
                or getattr(q, "canonical_id", None) or ""
            groups.setdefault(str(key), []).append(q)
        out: list[dict[str, Any]] = []
        for group in groups.values():
            fresh = [q for q in group if self._is_fresh(q)]
            selection = select_best_quote(
                fresh or group, stale_after_seconds=self._fresh_window,
                provider_of=self._catalogue_provider)
            if selection is not None:
                out.append(self._payload(selection))
        return out

    async def depth(self, canonical_id: str) -> dict[str, Any] | None:
        """The depth snapshot for a canonical instrument (with provenance)."""
        cid = self.resolve_canonical(canonical_id)
        if not cid:
            return None
        # The storage key of the canonical instrument is found through the
        # stored quotes (the canonical id is the identity; the storage key
        # is a store detail).
        key = None
        exchange = None
        wanted = self._reference_ids(canonical_id)
        for q in await self._quotes():
            if self._quote_ids(q) & wanted:
                key = getattr(q, "instrument_token", None)
                exchange = getattr(q, "exchange", None)
                break
        if not key:
            # No quote for the instrument (yet): the CATALOGUE still knows
            # the storage key the depth may be written under.
            for ex, candidate in self.storage_keys(canonical_id):
                try:
                    if self._market_service.get_depth_now(ex, candidate) \
                            is not None:
                        key, exchange = candidate, ex
                        break
                except Exception:
                    continue
        if not key or not exchange:
            return None
        try:
            depth = self._market_service.get_depth_now(exchange, key)
        except Exception:
            depth = None
        if depth is None:
            return None
        payload = self.depth_payload(depth)
        payload["canonical_id"] = cid or payload.get("canonical_id")
        return payload

    def depth_payload(self, depth: Any) -> dict[str, Any]:
        """The canonical depth payload (identity + provenance + ``as_of``).

        Every depth consumer (REST list, REST single, MCP) uses this, so
        the same instrument reports the same identity, provider and
        timestamp on every surface.
        """
        from market.serialization import depth_to_dict

        key = getattr(depth, "instrument_token", None)
        payload = depth_to_dict(depth)
        payload["canonical_id"] = (
            self.canonical_id_for(key) or getattr(depth, "canonical_id", None))
        payload["storage_key"] = key
        payload["provider"] = self._catalogue_provider(key) if key else None
        received = getattr(depth, "received_ts", None)
        payload["as_of"] = received.isoformat() \
            if hasattr(received, "isoformat") else None
        return payload

    # -- payload --------------------------------------------------------------

    def enrich(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add the canonical identity and the freshness verdict to a payload.

        Works on a RAW quote payload (the store's own serialization), so
        every consumer - REST, SSE, MCP - reports the SAME identity and the
        SAME freshness for the same instrument.
        """
        if not isinstance(data, dict):
            return data
        token = str(data.get("instrument_token") or "")
        cid = self.canonical_id_for(token) or data.get("canonical_id")
        received = data.get("received_ts")
        age = None
        if received:
            try:
                moment = datetime.fromisoformat(
                    str(received).replace("Z", "+00:00"))
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - moment).total_seconds()
            except Exception:
                age = None
        window = self.fresh_window_for(cid)
        stale = bool(data.get("stale"))
        if age is not None:
            stale = stale or age > window
        data["canonical_id"] = cid
        data["storage_key"] = token or data.get("storage_key")
        data["as_of"] = received
        data["age_seconds"] = age
        data["stale"] = stale
        data["live"] = not stale
        data["state"] = STATE_STALE if stale else STATE_LIVE
        data["fresh_window_seconds"] = window
        if not data.get("provider") and data.get("selected_from"):
            data["provider"] = data.get("selected_from")
        if not data.get("provider") and token:
            # The provider that published the key (the catalogue is the
            # authority; the key's shape never decides it).
            data["provider"] = self._catalogue_provider(token)
        return data

    def _payload(self, selection: Any) -> dict[str, Any]:
        """The shared quote payload (identical for REST, MCP and the UI)."""
        from market.serialization import quote_to_dict

        quote = selection.quote
        data = quote_to_dict(quote)
        received = getattr(quote, "received_ts", None)
        now = datetime.now(timezone.utc)
        age = None
        if received is not None:
            try:
                age = (now - received).total_seconds()
            except TypeError:
                age = None
        cid = self.canonical_id_for(
            getattr(quote, "instrument_token", None)) \
            or getattr(quote, "canonical_id", None)
        window = self.fresh_window_for(cid)
        stale = bool(selection.stale) or (
            age is not None and age > window)
        data.update({
            # The AUTHORITATIVE identity of the instrument. The
            # provider-native storage key stays available under
            # ``storage_key`` - it is a store detail, never an identity.
            "canonical_id": cid,
            "storage_key": getattr(quote, "instrument_token", None),
            "provider": selection.selected_from,
            "source": selection.selected_from,
            "selected_from": selection.selected_from,
            "as_of": received.isoformat() if hasattr(received, "isoformat")
            else None,
            "age_seconds": age,
            "stale": stale,
            "live": not stale,
            "state": STATE_STALE if stale else STATE_LIVE,
            "fresh_window_seconds": window,
            "candidate_count": selection.candidate_count,
        })
        return data

    def status(self) -> dict[str, Any]:
        """A diagnostic projection (no secrets)."""
        return {
            "fresh_window_seconds": self._fresh_window,
            "fresh_windows": dict(self._fresh_windows),
            "catalog_backed": self._catalog is not None,
            "identity_registry": self._identity is not None,
        }
