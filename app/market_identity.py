"""Provider-neutral instrument identity resolver for advanced alerts (B2).

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

The DERIVATION is owned by :mod:`app.canonical_identity` — the single
authority for canonical identity. This module keeps the historical public
names as the condition-alert engine's entry point and delegates to that
authority, so there is exactly ONE canonical form and ONE alias registry
(Phase A: no second, competing registry).
"""

from __future__ import annotations

import logging
from typing import Any

from app.canonical_identity import (
    EQUITY,
    ETF,
    FUTURE,
    INDEX,
    OPTION,
    CanonicalInstrumentIdentity,
    canonical_id_for_row,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MarketInstrumentIdentityResolver",
    "canonical_id_for_row",
    "EQUITY", "ETF", "INDEX", "FUTURE", "OPTION",
]

# The shared derivation helpers (owned by ``app.canonical_identity``) stay
# importable from this module for the existing consumers that already
# import them from here. They are re-exported lazily so this module keeps a
# single, sorted import block.
_REEXPORTS = (
    "_SYMBOL_ALIASES", "_TYPE_ALIASES",
    "_canonical_exchange", "_canonical_symbol", "_canonical_type",
    "_normalize_expiry", "_normalize_strike", "_normalize_symbol",
)


def __getattr__(name: str):
    if name in _REEXPORTS:
        from app import canonical_identity
        return getattr(canonical_identity, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class MarketInstrumentIdentityResolver(CanonicalInstrumentIdentity):
    """Condition-alert view of the canonical identity registry.

    Behaviour is identical to :class:`app.canonical_identity.
    CanonicalInstrumentIdentity` (the single authority); the distinct class
    name is retained so the condition-alert wiring and its diagnostics stay
    readable.
    """

    def canonical_id_for_row(self, row: dict[str, Any]) -> str | None:
        """Compute the provider-neutral canonical id for one catalog row."""
        return canonical_id_for_row(row)

    def register_catalog_rows(
        self, rows: Any
    ) -> dict[str, Any]:
        """Register many catalog rows; aggregate and report collisions.

        The ONE bounded summary is emitted on THIS module's logger (the
        condition-alert engine's own diagnostics surface) as well as the
        canonical registry's, so an ambiguity is visible where it is
        consumed.
        """
        total = super().register_catalog_rows(rows)
        if total["rejected"]:
            samples = ", ".join(repr(a) for a in total["rejected"][:3])
            logger.info(
                "identity collisions: %d ambiguous alias(es) rejected "
                "(first-wins kept; examples: %s%s)",
                len(total["rejected"]), samples,
                " …" if len(total["rejected"]) > 3 else "")
        return total
