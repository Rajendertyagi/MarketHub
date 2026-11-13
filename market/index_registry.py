"""Canonical index registry — broker-neutral, app-independent (single source).

The authoritative list of indices MarketHub knows, defined WITHOUT any
provider-specific data. Upstox instrument keys, Fyers symbols and Fyers HSM
topics live in the app layer (``app.market_indices``) — never here.

Two concepts are kept deliberately separate:

* **F&O indices** — underlyings with listed futures/options in MarketHub's
  supported product scope (``FNO_INDEX_LABELS``). This is the canonical F&O
  index set; nothing may maintain a second copy of it.
* **broader market indices** — tracked/displayed indices that are NOT in the
  supported F&O scope (e.g. INDIA VIX, BANKEX, NIFTYFPI). They remain in the
  registry for canonical identity but are never part of the F&O universe.

This module imports no ``app`` and no ``brokers`` code (architecture
boundary: the canonical market layer depends on nothing above it).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalIndex:
    """One canonical index identity (no provider-specific fields)."""

    label: str
    exchange: str
    is_fno: bool = False


# Canonical index set. Supported F&O scope: NSE index F&O + BSE SENSEX F&O
# only. This is the single source of truth — no other module may define its
# own copy. Order is the canonical presentation order.
CANONICAL_INDICES: tuple[CanonicalIndex, ...] = (
    CanonicalIndex("NIFTY", "NSE", is_fno=True),
    CanonicalIndex("BANKNIFTY", "NSE", is_fno=True),
    CanonicalIndex("FINNIFTY", "NSE", is_fno=True),
    CanonicalIndex("MIDCPNIFTY", "NSE", is_fno=True),
    CanonicalIndex("NIFTYNXT50", "NSE", is_fno=True),
    CanonicalIndex("NIFTYFPI", "NSE", is_fno=False),
    CanonicalIndex("INDIA VIX", "NSE", is_fno=False),
    CanonicalIndex("SENSEX", "BSE", is_fno=True),
    CanonicalIndex("BANKEX", "BSE", is_fno=False),
)

# The ONLY authoritative F&O index list. Derived from CANONICAL_INDICES.
FNO_INDEX_LABELS: tuple[str, ...] = tuple(
    e.label for e in CANONICAL_INDICES if e.is_fno)

# Every canonical index label (F&O + broader), in canonical order.
INDEX_LABELS: tuple[str, ...] = tuple(e.label for e in CANONICAL_INDICES)

_BY_LABEL = {e.label: e for e in CANONICAL_INDICES}


def is_fno_index(label: str | None) -> bool:
    """True when ``label`` is one of the canonical F&O index underlyings."""
    entry = _BY_LABEL.get(label or "")
    return bool(entry and entry.is_fno)


def canonical_index(label: str | None) -> CanonicalIndex | None:
    return _BY_LABEL.get(label or "")
