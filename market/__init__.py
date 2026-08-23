"""
MarketHub canonical market-data domain package.

Owns the provider-neutral market models (market.models). Normalization of
raw broker payloads into these models arrives in a later phase
(market.normalize), as does the shared market service/state (market.service).

This package is pure: importing it has no side effects and pulls in nothing
beyond the standard library.
"""

from market.models import Depth, DepthLevel, Instrument, Quote

__all__ = ["Instrument", "Quote", "DepthLevel", "Depth"]
