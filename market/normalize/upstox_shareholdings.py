"""
Upstox shareholdings normalizer.

Converts raw /fundamentals/:isin/share-holdings response into Shareholdings.
"""

from __future__ import annotations

from typing import Any

from market.models import (
    ShareholdingRecord,
    ShareholdingCategory,
    Shareholdings,
)


def shareholdings_from_rest(payload: dict[str, Any], isin: str = "") -> Shareholdings:
    """Normalize Upstox /fundamentals/:isin/share-holdings response."""
    if not isinstance(payload, dict):
        raise ValueError("Shareholdings payload must be a dict")

    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ValueError("Shareholdings data must be a list")

    categories: list[ShareholdingCategory] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        category_name = str(item.get("category", ""))
        history_raw = item.get("history") or []
        if not isinstance(history_raw, list):
            continue

        records = []
        for rec in history_raw:
            if not isinstance(rec, dict):
                continue
            try:
                records.append(ShareholdingRecord(
                    period=str(rec.get("period", "")),
                    value=float(rec.get("value") or 0),
                ))
            except (TypeError, ValueError):
                continue

        categories.append(ShareholdingCategory(
            category=category_name,
            history=tuple(records),
        ))

    return Shareholdings(
        isin=isin or str(payload.get("isin", "")),
        categories=tuple(categories),
    )
