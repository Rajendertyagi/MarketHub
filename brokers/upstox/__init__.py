"""
Upstox broker adapter package.

Current scope:
    upstox.feed_protocol   V3 FeedResponse decoder + presence-exact
                           extraction helpers (P-ZERO policy)
    upstox.proto           vendored official schema + generated bindings
    upstox.auth            authentication foundation (D2.1): credentials,
                           token-expiry rule, OAuth URL construction
    upstox.errors          adapter error hierarchy
    upstox.rest            REST boundary (D2.2): market-feed authorization
                           + authorization-code exchange

Later phases add: feed.py (D3 WebSocket adapter), limits.py. Broker
adapters depend on market/ and core/ — never the reverse. Raw market
ticks never pass through core.events.publish_event().
"""

from brokers.upstox.auth import (
    IST,
    UpstoxCredentials,
    UpstoxOAuth,
    upstox_token_expiry,
)
from brokers.upstox.errors import (
    UpstoxAuthError,
    UpstoxError,
    UpstoxRateLimitError,
    UpstoxRestError,
)
from brokers.upstox.rest import UpstoxRest

__all__ = [
    "IST",
    "UpstoxAuthError",
    "UpstoxCredentials",
    "UpstoxError",
    "UpstoxOAuth",
    "UpstoxRateLimitError",
    "UpstoxRest",
    "UpstoxRestError",
    "upstox_token_expiry",
]
