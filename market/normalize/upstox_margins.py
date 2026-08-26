"""
Upstox margin calculator normalizer.

Converts raw /charges/margin response into MarginBasket.
"""

from __future__ import annotations

from typing import Any

from market.models import MarginEntry, MarginBasket


def margin_from_rest(payload: dict[str, Any]) -> MarginBasket:
    """Normalize Upstox /charges/margin response into MarginBasket."""
    if not isinstance(payload, dict):
        raise ValueError("Margin payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Margin data must be a dict")

    required = float(data.get("required_margin") or 0)
    final = float(data.get("final_margin") or 0)

    entries: list[MarginEntry] = []
    for item in data.get("margins") or []:
        if not isinstance(item, dict):
            continue
        entries.append(MarginEntry(
            instrument_key=str(item.get("instrument_key", "")),
            span_margin=float(item.get("span_margin") or 0),
            exposure_margin=float(item.get("exposure_margin") or 0),
            equity_margin=float(item.get("equity_margin") or 0),
            net_buy_premium=float(item.get("net_buy_premium") or 0),
            additional_margin=float(item.get("additional_margin") or 0),
            tender_margin=float(item.get("tender_margin") or 0),
            total_margin=float(item.get("total_margin") or 0),
        ))

    return MarginBasket(
        required_margin=required,
        final_margin=final,
        entries=tuple(entries),
    )
