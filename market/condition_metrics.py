"""
Condition metric registry — canonical Quote-backed metrics for B2.

Single source of truth for the 27 metrics a ``market_condition`` alert may
reference. Each metric maps to exactly one canonical Quote field (or a
deterministic derivation such as ``spread``). Extraction returns a numeric
value or ``None`` (UNKNOWN) — ``None`` is NEVER treated as zero, and a
reported zero is a valid value (never treated as missing).

B6B adds 4 analytics-derived metrics (pcr_oi, pcr_volume, max_pain, iv_skew)
that are extracted from an ``OptionChainAnalyticsSnapshot`` instead of a
``Quote``. These are tracked in ``METRIC_SOURCE`` and ``METRIC_EVAL_CLASS``.
"""

from __future__ import annotations

from typing import Any

from market.models import Quote

# The 27 B2 metrics (frozen order — canonical for docs/tests).
METRIC_NAMES = (
    "ltp",
    "open",
    "high",
    "low",
    "close",
    "change",
    "change_percent",
    "avg_trade_price",
    "last_traded_qty",
    "volume",
    "total_buy_qty",
    "total_sell_qty",
    "open_interest",
    "previous_oi",
    "oi_change",
    "oi_change_percent",
    "best_bid",
    "best_ask",
    "spread",
    "upper_circuit",
    "lower_circuit",
    "greeks.delta",
    "greeks.gamma",
    "greeks.theta",
    "greeks.vega",
    "greeks.rho",
    "greeks.iv",
)

# B6B analytics metrics (added to the registry).
ANALYTICS_METRIC_NAMES = (
    "pcr_oi",
    "pcr_volume",
    "max_pain",
    "iv_skew",
)

METRIC_SET = frozenset(METRIC_NAMES) | frozenset(ANALYTICS_METRIC_NAMES)

# METRIC_SOURCE: "quote" for Quote-backed metrics, "analytics" for chain-derived.
METRIC_SOURCE: dict[str, str] = {m: "quote" for m in METRIC_NAMES}
METRIC_SOURCE.update({m: "analytics" for m in ANALYTICS_METRIC_NAMES})

# METRIC_EVAL_CLASS: "event" for per-quote evaluation, "snapshot" for per-chain-refresh.
METRIC_EVAL_CLASS: dict[str, str] = {m: "event" for m in METRIC_NAMES}
METRIC_EVAL_CLASS.update({m: "snapshot" for m in ANALYTICS_METRIC_NAMES})

# Direct Quote attribute for each scalar metric.
_DIRECT_ATTRS = {
    "ltp": "ltp",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "change": "change",
    "change_percent": "change_percent",
    "avg_trade_price": "avg_trade_price",
    "last_traded_qty": "last_traded_qty",
    "volume": "volume",
    "total_buy_qty": "total_buy_qty",
    "total_sell_qty": "total_sell_qty",
    "open_interest": "open_interest",
    "previous_oi": "previous_oi",
    "oi_change": "oi_change",
    "oi_change_percent": "oi_change_percent",
    "best_bid": "best_bid",
    "best_ask": "best_ask",
    "upper_circuit": "upper_circuit",
    "lower_circuit": "lower_circuit",
}

# OptionGreeks attribute for each greeks.* metric.
_GREEKS_ATTRS = {
    "greeks.delta": "delta",
    "greeks.gamma": "gamma",
    "greeks.theta": "theta",
    "greeks.vega": "vega",
    "greeks.rho": "rho",
    "greeks.iv": "iv",
}


def extract_metric(quote: Quote, metric: str) -> float | None:
    """Extract one metric value from a canonical Quote.

    Returns ``None`` (UNKNOWN) when the metric is not reportable for the
    quote: field absent, greeks snapshot absent, or ``spread`` with a
    missing side. A reported zero is returned as-is (never treated as
    missing).

    Raises ``KeyError`` for an unknown metric name.
    """
    if metric in _DIRECT_ATTRS:
        return getattr(quote, _DIRECT_ATTRS[metric], None)
    if metric == "spread":
        bid = quote.best_bid
        ask = quote.best_ask
        if bid is None or ask is None:
            return None
        return ask - bid
    if metric in _GREEKS_ATTRS:
        greeks = quote.greeks
        if greeks is None:
            return None
        return getattr(greeks, _GREEKS_ATTRS[metric], None)
    raise KeyError(f"unknown metric: {metric}")


def metric_value(quote: Quote, metric: str) -> Any:
    """Alias of :func:`extract_metric` (kept for symmetry with the registry)."""
    return extract_metric(quote, metric)


def extract_analytics_metric(snapshot: Any, metric: str) -> float | None:
    """Extract one analytics metric from an OptionChainAnalyticsSnapshot.

    Returns ``None`` (UNKNOWN) when the metric is not available or the
    snapshot is stale. A reported zero is returned as-is.

    Raises ``KeyError`` for an unknown metric name.
    """
    if metric == "pcr_oi":
        return snapshot.pcr_oi
    if metric == "pcr_volume":
        return snapshot.pcr_volume
    if metric == "max_pain":
        return snapshot.max_pain
    if metric == "iv_skew":
        return snapshot.iv_skew
    raise KeyError(f"unknown metric: {metric!r}")