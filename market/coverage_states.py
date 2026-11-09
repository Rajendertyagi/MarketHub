"""Canonical coverage-state vocabulary (broker-neutral, pure).

Single source for the coverage states shared by the F&O coverage service and
the futures board. No imports beyond stdlib — safe for the canonical market
layer (``market/`` must not depend on ``app``/``brokers``).
"""

from __future__ import annotations

LIVE = "live"
STALE = "stale"
UNSUBSCRIBED = "unsubscribed"
UNCOVERED = "uncovered"
CAPACITY_LIMITED = "capacity_limited"
PROVIDER_UNAVAILABLE = "provider_unavailable"
NO_DATA = "no_data"

ALL_STATES: frozenset[str] = frozenset({
    LIVE, STALE, UNSUBSCRIBED, UNCOVERED, CAPACITY_LIMITED,
    PROVIDER_UNAVAILABLE, NO_DATA,
})

# Routing-level states that the board carries through unchanged (no quote is
# expected for these).
ROUTING_STATES: frozenset[str] = frozenset({
    UNCOVERED, CAPACITY_LIMITED, PROVIDER_UNAVAILABLE,
})
