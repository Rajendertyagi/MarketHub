"""Upstox provider capability declaration (adapter-owned).

Verified against the Upstox V3 market-data feed proto (MarketFullFeed) and the
REST quote normalizers: current OI is available (full mode); previous-day OI
and OI-change are NOT on the wire, so the provider-agnostic persisted
previous-session OI is the fallback for OI%.
"""

from __future__ import annotations

from typing import Any

from providers.capabilities import (
    CAP_DEPTH,
    CAP_HISTORY,
    CAP_LTP,
    CAP_MAX_PAIN,
    CAP_OHLC,
    CAP_OI,
    CAP_OI_ANALYTICS,
    CAP_OPTION_CHAIN,
    CAP_PCR,
    CAP_SPOT,
    STYLE_PIPE,
    ProviderCapabilities,
)

# Broker-reported per-mode instrument-key limits
# (https://upstox.com/developer/api-documentation/v3/get-market-data-feed/):
# the INDIVIDUAL limit per category and the COMBINED limit that applies
# when several categories are subscribed at once. Two connections per user
# (five with Upstox Plus) - connections do NOT multiply the limits.
MODE_KEY_LIMITS: dict[str, int] = {
    "ltpc": 5000, "option_greeks": 3000, "full": 2000, "full_d30": 50,
}
COMBINED_KEY_LIMITS: dict[str, int] = {
    "ltpc": 2000, "option_greeks": 2000, "full": 1500, "full_d30": 1500,
}
DEFAULT_TIER = "standard"
DEFAULT_MODE = "full"

# Documented REST limits, by QUOTA SCOPE.
#
# Upstox enforces its limits "per API, per user", so each API family has
# ITS OWN budget and they are never combined: the market-quote endpoints
# (/v3/market-quote/*), the historical candles (/v3/historical-candle/*),
# the option chain (/v2/option/chain) and the market information endpoints
# (/v2/market/*) each carry the "Other Standard APIs" row
# (50 requests/second, 500/minute, 2,000 per 30 minutes).
#
# "Get Token via TOTP" has its own, STRICTER row (1/second, 10/minute,
# 60 per 30 minutes) and therefore its own bucket - the auth endpoints
# are not throttled by (nor do they throttle) the market-data budgets.
REST_LIMITS: dict[str, Any] = {
    "market_quote": {
        "requests_per_second": 50,
        "requests_per_minute": 500,
        "requests_per_30_minutes": 2000,
        "max_instrument_keys_per_request": 500,
    },
    "historical": {
        "requests_per_second": 50,
        "requests_per_minute": 500,
        "requests_per_30_minutes": 2000,
    },
    "option_chain": {
        "requests_per_second": 50,
        "requests_per_minute": 500,
        "requests_per_30_minutes": 2000,
    },
    "market_info": {
        "requests_per_second": 50,
        "requests_per_minute": 500,
        "requests_per_30_minutes": 2000,
    },
    # "Get Token via TOTP" has its own, STRICTER row (1/second,
    # 10/minute, 60 per 30 minutes). The login family (the TOTP/PIN
    # login and the PIN validation) shares that one budget, so repeated
    # attempts against it cannot bypass it.
    "auth": {
        "requests_per_second": 1,
        "requests_per_minute": 10,
        "requests_per_30_minutes": 60,
    },
    # The OAuth authorization-code exchange (Get Token API) is a
    # DIFFERENT documented API family and carries the standard API row.
    # It is its own budget, so a legitimate login is never throttled by
    # the interactive-login row.
    "auth_code": {
        "requests_per_second": 50,
        "requests_per_minute": 500,
        "requests_per_30_minutes": 2000,
    },
    # The market-data feed authorization (one-time authorized WebSocket
    # URI, once per connection) is its own API and carries the standard
    # API row - it does NOT share the TOTP-login quota, and it is not
    # merged into an unrelated market-data quota either.
    "feed_authorize": {
        "requests_per_second": 50,
        "requests_per_minute": 500,
        "requests_per_30_minutes": 2000,
    },
}

CAPABILITIES = ProviderCapabilities(
    # The account ceiling is the highest documented individual limit
    # (ltpc); the feed mode in use narrows it (full = 2,000).
    tier_limits={"standard": 5000},
    default_tier=DEFAULT_TIER,
    combined_limits=COMBINED_KEY_LIMITS,
    rest_limits=REST_LIMITS,
    provider="upstox",
    label="Upstox",
    capabilities=frozenset({
        CAP_LTP, CAP_OHLC, CAP_OI, CAP_DEPTH, CAP_SPOT, CAP_HISTORY,
        CAP_OPTION_CHAIN, CAP_OI_ANALYTICS, CAP_MAX_PAIN, CAP_PCR,
    }),
    modes=MODE_KEY_LIMITS,
    default_mode=DEFAULT_MODE,
    key_styles=(STYLE_PIPE,),
)
