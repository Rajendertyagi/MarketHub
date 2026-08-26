"""Derived F&O analytics package.

Pure, provider-agnostic functions over MarketHub's canonical
``OptionChainSnapshot``. Ported from TBMCP's ``analytics`` module so the same
derived analytics run whether the snapshot came from Upstox or Fyers
normalization — no broker-specific code, no network.
"""

from market.analytics.option_chain import (
    analyze_option_chain,
    buildup_color,
    classify_buildup,
    compute_atm,
    compute_futures_basis,
    compute_gex,
    compute_iv_skew,
    compute_max_pain,
    compute_oi_buildup,
    compute_pcr,
    compute_straddle,
    compute_support_resistance,
    compute_top_oi_strikes,
)
from market.analytics.strategies import (
    price_bear_put_spread,
    price_bull_call_spread,
    price_iron_condor,
    price_long_butterfly,
    price_long_straddle,
    price_long_strangle,
    price_strategy,
)

__all__ = [
    "analyze_option_chain",
    "buildup_color",
    "classify_buildup",
    "compute_atm",
    "compute_futures_basis",
    "compute_gex",
    "compute_iv_skew",
    "compute_max_pain",
    "compute_oi_buildup",
    "compute_pcr",
    "compute_straddle",
    "compute_support_resistance",
    "compute_top_oi_strikes",
    "price_bear_put_spread",
    "price_bull_call_spread",
    "price_iron_condor",
    "price_long_butterfly",
    "price_long_straddle",
    "price_long_strangle",
    "price_strategy",
]
