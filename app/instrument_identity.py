"""Instrument identity registry — alias -> quote-STORAGE-key lookup.

This is a LOOKUP over the single canonical authority
(:mod:`app.canonical_identity`), NOT a second authority:

* **Canonical identity** — which real instrument this is. Derived once in
  ``app.canonical_identity.canonical_id_for_row`` and shared by every
  consumer (condition alerts, the quote coordinator, the F&O router).
* **Provider-native identifiers** — how the provider addresses it, BY ROLE
  (``feed_key`` / ``rest_key``). See
  ``app.canonical_identity.provider_identifiers_for_row``.
* **Storage key** — the key under which legacy quote state is written and
  read. This module maps identifiers onto it; it NEVER re-keys the store.

Problem this solves: the same real instrument is known by different
identifiers in different layers:

  * MarketService storage key = the provider-normalized config ``key``
    (e.g. ``NSE:NIFTY50-INDEX`` for Fyers, ``NSE_EQ|INE002A01018`` for
    Upstox) — frozen normalization semantics, unchanged here.
  * Instrument-catalog identity = the provider token (e.g. Fyers
    fyToken ``101000000026000``).
  * Config aliases = whatever the operator configured (always kept
    working; never requires manual migration).

The registry maps every known identifier (alias) to ONE storage key — by
default the feed/storage key — so lookups anywhere (MarketIntel,
option-chain spot, REST quote/depth) resolve to the same quote state.
It does NOT change how feeds normalize or store quotes.

Collision safety: an alias already bound to a different storage key is
REJECTED (existing binding wins, registration reports failure) — never
silently re-pointed. Re-registering the same alias->key pair is
idempotent. Catalog refreshes re-register safely because identical
bindings are no-ops.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from typing import Any

from app.canonical_identity import (
    canonical_id_for_row,
    provider_quote_key,
)

logger = logging.getLogger(__name__)


class InstrumentIdentityRegistry:
    """Thread-safe alias -> storage-key registry.

    Backed by the canonical derivation: a catalogue row's aliases are
    registered against the row's QUOTE STORAGE KEY (the provider's feed
    key), and the row's CANONICAL ID is registered as an alias too, so a
    canonical id also resolves to the same quote state.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._alias_to_canonical: dict[str, str] = {}
        self._canonical_aliases: dict[str, set[str]] = {}

    # -- registration -------------------------------------------------------

    def register(self, canonical_id: str,
                 aliases: Iterable[str]) -> dict[str, Any]:
        """Bind aliases to one canonical id.

        Returns {"registered": n_added, "rejected": [alias, ...]}.
        An alias is rejected only when it is already bound to a DIFFERENT
        canonical id (ambiguity — never silently re-pointed).
        Same-pair re-registration is idempotent.
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
                    # Ambiguity is expected with a full multi-provider
                    # catalogue (a short symbol, a legacy alias). The
                    # rejection IS the safety mechanism; the noise
                    # belongs in a bounded summary, not a warning per
                    # alias.
                    logger.debug(
                        "identity alias collision: '%s' already maps to "
                        "'%s'; request for '%s' rejected",
                        alias, existing, canonical_id)
                    rejected.append(alias)
                    continue
                self._alias_to_canonical[alias] = canonical_id
                self._canonical_aliases[canonical_id].add(alias)
                registered += 1
        return {"registered": registered, "rejected": rejected}

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
        """Resolve a canonical Quote to its storage key (or None).

        The quote's ``instrument_token`` IS the storage key (feeds write
        under it), so the quote resolves to itself once registered; the
        tradingsymbol alias is the fallback for quotes whose token was
        never registered.
        """
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

    def register_from_catalog_row(self, row: dict[str, Any],
                                  *, primary: str | None = None) -> dict:
        """Register aliases from one instrument-catalog row.

        The row's QUOTE STORAGE KEY is the canonical id of this registry
        (the provider's feed key). Aliases: the provider token, the
        provider symbol and the tradingsymbol — the identifiers that NAME
        this instrument — plus the provider-neutral canonical id, so a
        lookup by any of them reaches the same quote state.

        A derivative row's ``underlying`` is NOT an alias: it names a
        different instrument (the cash/index row), so registering it here
        would make the underlying symbol resolve to a contract instead of
        the instrument itself.

        ``primary`` overrides the storage key (used when a runtime key
        differs from the catalogue's feed key).
        """
        storage_key = primary or provider_quote_key(row)
        if not storage_key:
            return {"registered": 0, "rejected": []}
        aliases: list[str] = []
        for value in (row.get("instrument_token"), row.get("provider_symbol"),
                      row.get("tradingsymbol")):
            if value:
                aliases.append(str(value))
        cid = canonical_id_for_row(row)
        if cid:
            aliases.append(cid)
        return self.register(storage_key, aliases)

    def register_catalog_rows(
        self, rows: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        """Register many catalogue rows; aggregates registered/rejected counts.

        Rows are GROUPED BY CANONICAL ID first, then each group's alias
        set is bound ONCE, to the group's storage key.

        Registering row-by-row made the outcome depend on the catalogue
        READ ORDER: the first provider to publish a shared identifier won
        the binding and the other was rejected as a collision. Grouping
        makes the binding a property of the CANONICAL INSTRUMENT — every
        provider's identifier for the same real instrument resolves to
        the same quote state, in any order.

        The group's storage key is the feed key of the provider that owns
        the row; when several providers list the same instrument, the
        preferred provider's key wins (deterministically).
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows or ():
            canonical_id = canonical_id_for_row(row)
            if canonical_id is None:
                continue
            groups.setdefault(canonical_id, []).append(row)

        total: dict[str, Any] = {"registered": 0, "rejected": []}
        for canonical_id, group in groups.items():
            storage_key = self._group_storage_key(group)
            if not storage_key:
                continue
            aliases: list[str] = []
            for row in group:
                for value in (row.get("instrument_token"),
                              row.get("provider_symbol"),
                              row.get("tradingsymbol")):
                    if value and str(value) not in aliases:
                        aliases.append(str(value))
            if canonical_id not in aliases:
                aliases.append(canonical_id)
            result = self.register(storage_key, aliases)
            total["registered"] += result["registered"]
            total["rejected"].extend(result["rejected"])
        if total["rejected"]:
            samples = ", ".join(repr(a) for a in total["rejected"][:3])
            logger.info(
                "identity collisions: %d ambiguous alias(es) rejected "
                "(first-wins kept; examples: %s%s)",
                len(total["rejected"]), samples,
                " ..." if len(total["rejected"]) > 3 else "")
        return total

    def _group_storage_key(self, group: list[dict[str, Any]]) -> str | None:
        """The storage key for a group of rows describing ONE instrument.

        Deterministically the feed key of the PREFERRED provider that
        lists the instrument; the registry's preference order decides, so
        the result does not depend on the catalogue read order.
        """
        try:
            from providers.registry import get_registry
            reg = get_registry()
            order = list(reg.preference_order())
        except Exception:
            order = []
        ranked = sorted(
            group,
            key=lambda r: (order.index(str(r.get("provider") or ""))
                            if str(r.get("provider") or "") in order
                            else len(order) + 1))
        for row in ranked:
            key = provider_quote_key(row)
            if key:
                return str(key)
        return None
