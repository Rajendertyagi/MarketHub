"""Canonical single-owner sector classifier for MarketHub.

This module is the ONLY place that maps a trading symbol to a sector. Both
Market Breadth and the Sector Heatmap import :func:`sector_for_symbol`, so the
two features can never disagree about a stock's sector (acceptance: sector
totals must reconcile with breadth totals for the same universe).

Audit finding (§11)
-------------------
The synced instrument catalog carries no sector column, and the only structured
sector source is the authenticated per-ISIN Upstox ``/fundamentals`` API, which
is neither persisted nor batch-friendly. Per the mission's preferred order
(1. canonical membership data → 2. structured provider metadata → 3.
project-maintained taxonomy), we use a **curated project taxonomy**
(``market/data/sector_taxonomy.json``) as the single source of truth.

Honesty rules
-------------
* We do NOT guess sectors from symbol names.
* Anything absent from the taxonomy is classified as ``Unclassified`` and
  counted explicitly — never silently folded into an "Other" bucket that would
  masquerade as a real sector.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Callable

UNCLASSIFIED = "Unclassified"

_TAXONOMY_PATH = os.path.join(os.path.dirname(__file__), "data", "sector_taxonomy.json")


@lru_cache(maxsize=1)
def _load_taxonomy() -> dict[str, str]:
    try:
        with open(_TAXONOMY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            # Keys beginning with "_" are metadata (e.g. "_meta"), never symbols.
            return {
                str(k).strip().upper(): str(v)
                for k, v in data.items()
                if not str(k).startswith("_")
            }
    except Exception:
        # A missing/broken taxonomy must never crash the feature; it simply
        # means everything is "Unclassified" (reported, not hidden).
        pass
    return {}


def sector_for_symbol(symbol: str | None) -> str:
    """Return the canonical sector for a trading symbol.

    Returns :data:`UNCLASSIFIED` for ``None``, empty, or unknown symbols.
    """
    if not symbol:
        return UNCLASSIFIED
    return _load_taxonomy().get(symbol.strip().upper(), UNCLASSIFIED)


def known_sectors() -> list[str]:
    """Sorted list of sectors present in the taxonomy (excludes Unclassified)."""
    return sorted(set(_load_taxonomy().values()))


def reload_taxonomy() -> None:
    """Drop the cached taxonomy (used by tests / live taxonomy refresh)."""
    _load_taxonomy.cache_clear()


# A classifier has the stable signature ``(symbol) -> sector`` so it can be
# swapped (e.g. for a future persisted fundamentals-backed classifier) without
# touching callers.
SectorClassifier = Callable[[str | None], str]
