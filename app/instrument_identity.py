"""Canonical instrument-identity registry — ONE resolution mechanism.

Problem this solves: the same real instrument is known by different
identifiers in different layers:

  * MarketService storage key = the provider-normalized config ``key``
    (e.g. ``NSE:NIFTY50-INDEX`` for Fyers, ``NSE_EQ|INE002A01018`` for
    Upstox) — frozen normalization semantics, unchanged here.
  * Instrument-catalog identity = the provider token (e.g. Fyers
    fyToken ``101000000026000``).
  * Config aliases = whatever the operator configured (always kept
    working; never requires manual migration).

The registry maps every known identifier (alias) to ONE canonical id —
by default the feed/storage key — so lookups anywhere (MarketIntel,
option-chain spot, REST quote/depth) resolve to the same quote state.
It does NOT change how feeds normalize or store quotes.

Collision safety: an alias already bound to a different canonical id is
REJECTED (existing binding wins, registration reports failure) — never
silently re-pointed. Re-registering the same alias→canonical pair is
idempotent. Catalog refreshes re-register safely because identical
bindings are no-ops.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class InstrumentIdentityRegistry:
    """Thread-safe alias → canonical-id registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._alias_to_canonical: dict[str, str] = {}
        self._canonical_aliases: dict[str, set[str]] = {}

    def register(self, canonical_id: str,
                 aliases: Iterable[str]) -> dict[str, Any]:
        """Bind aliases to one canonical id.

        Returns {"registered": n_added, "rejected": [alias, ...]}.
        An alias is rejected only when it is already bound to a
        DIFFERENT canonical id (ambiguity — never silently re-pointed).
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
                    logger.warning(
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

    def aliases_for(self, canonical_id: str) -> frozenset[str]:
        with self._lock:
            return frozenset(self._canonical_aliases.get(
                str(canonical_id), ()))

    def register_from_catalog_row(self, row: dict[str, Any],
                                  *, primary: str | None = None) -> dict:
        """Register aliases from one instrument-catalog row.

        Aliases: catalog token, tradingsymbol, provider_symbol.
        Primary (canonical) defaults to the tradingsymbol when not given.
        """
        token = row.get("instrument_token")
        ts = row.get("tradingsymbol")
        canonical = primary or ts or token
        aliases = [a for a in (token, ts, row.get("provider_symbol"))
                   if a]
        return self.register(canonical, aliases)

