"""
Upstox option Greeks standalone normalizer.

Converts raw /market-quote/option-greek response into OptionGreekSnapshot.
"""

from __future__ import annotations

from typing import Any

from market.models import OptionGreekEntry, OptionGreekSnapshot


def option_greeks_from_rest(payload: dict[str, Any]) -> OptionGreekSnapshot:
    """Normalize Upstox /market-quote/option-greek response."""
    if not isinstance(payload, dict):
        raise ValueError("Option Greeks payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Option Greeks data must be a dict")

    entries: list[OptionGreekEntry] = []
    for key, item in data.items():
        if not isinstance(item, dict):
            continue
        try:
            entries.append(OptionGreekEntry(
                instrument_key=str(key),
                last_price=float(item.get("last_price") or 0) if item.get("last_price") is not None else None,
                delta=float(item.get("delta") or 0) if item.get("delta") is not None else None,
                gamma=float(item.get("gamma") or 0) if item.get("gamma") is not None else None,
                theta=float(item.get("theta") or 0) if item.get("theta") is not None else None,
                vega=float(item.get("vega") or 0) if item.get("vega") is not None else None,
                rho=float(item.get("rho") or 0) if item.get("rho") is not None else None,
                iv=float(item.get("iv") or 0) if item.get("iv") is not None else None,
                oi=int(item.get("oi") or 0) if item.get("oi") is not None else None,
                volume=int(item.get("volume") or 0) if item.get("volume") is not None else None,
                last_traded_qty=int(item.get("ltq") or 0) if item.get("ltq") is not None else None,
                previous_close=float(item.get("cp") or 0) if item.get("cp") is not None else None,
            ))
        except (TypeError, ValueError):
            continue

    return OptionGreekSnapshot(
        entries=tuple(entries),
    )
