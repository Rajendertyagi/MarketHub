"""Fyers provider capability declaration (adapter-owned).

Verified against the Fyers v3 REST surface (quotes/depth/history/
options-chain) and the HSM data socket:

  * option chain with per-strike greeks is fetched via REST
    (``greeks=1``); depth/previous-day OI (``pdoi``) and OI-change
    percent (``oipercent``) come from the REST depth endpoint, NOT the
    HSM wire.
  * max-pain is derived LOCALLY from the returned strike window
    (``strikecount`` <= 50/side) — it is window-bounded and must never
    be labelled whole-expiry.
  * provider analytics endpoints (OI/max-pain/PCR) are not implemented
    in this integration, so those capability tokens are NOT declared.
"""

from __future__ import annotations

from typing import Any

from providers.capabilities import (
    CAP_DEPTH,
    CAP_HISTORY,
    CAP_LTP,
    CAP_OHLC,
    CAP_OI,
    CAP_OI_CHANGE,
    CAP_OI_CHANGE_PERCENT,
    CAP_OPTION_CHAIN,
    CAP_PREVIOUS_OI,
    CAP_SPOT,
    STYLE_COLON,
    STYLE_NUMERIC,
    ProviderCapabilities,
)

# Fyers data socket: the documented account tiers
# (https://fyers.in/api-docs/v3/getting-started/plans-and-limits).
# Standard is the default tier for every account (100 symbols, counted
# PER USER across all data-socket connections); Prime is 5,000. The tier
# in use is resolved by ``providers.capacity`` - Prime is never assumed.
MODE_KEY_LIMITS: dict[str, int] = {"full": 100}
TIER_KEY_LIMITS: dict[str, int] = {"standard": 100, "prime": 5000}
DEFAULT_TIER = "standard"
DEFAULT_MODE = "full"

# Documented REST limits, by QUOTA SCOPE.
#
# FYERS documents the DATA APIs as ONE shared pool per user (quotes,
# depth, history, option chain, market status and the symbol-token
# resolution all draw from it): 5 rps / 50 rmin / 5,000 per day on
# Standard (10 / 500 / 500,000 on Prime). The Quotes API accepts 50
# symbols per request on Standard (1,000 on Prime); the option chain
# accepts at most 50 strikes per side of ATM. Exceeding the per-minute
# limit more than three times in a day blocks the account for the day.
#
# The Transaction / User-Info APIs are a SEPARATE documented pool
# (10 rps / 200 rmin / 100,000 per day on Standard) and are never mixed
# with the data pool.
REST_LIMITS: dict[str, Any] = {
    "data": {
        "requests_per_second": 5,
        "requests_per_minute": 50,
        "requests_per_day": 5000,
        "max_symbols_per_request": 50,
        "max_strikes_per_side": 50,
        "per_minute_breaches_before_block": 3,
    },
    "transaction": {
        "requests_per_second": 10,
        "requests_per_minute": 200,
        "requests_per_day": 100000,
    },
}

CAPABILITIES = ProviderCapabilities(
    tier_limits=TIER_KEY_LIMITS,
    default_tier=DEFAULT_TIER,
    rest_limits=REST_LIMITS,
    provider="fyers",
    label="Fyers",
    capabilities=frozenset({
        CAP_LTP, CAP_OHLC, CAP_OI, CAP_PREVIOUS_OI, CAP_OI_CHANGE,
        CAP_OI_CHANGE_PERCENT, CAP_DEPTH, CAP_SPOT, CAP_HISTORY,
        CAP_OPTION_CHAIN,
    }),
    modes=MODE_KEY_LIMITS,
    default_mode=DEFAULT_MODE,
    key_styles=(STYLE_COLON, STYLE_NUMERIC),
)
