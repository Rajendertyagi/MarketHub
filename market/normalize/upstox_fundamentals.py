"""
Upstox fundamentals normalizers.

Converts raw /fundamentals/* responses into canonical models.
"""

from __future__ import annotations

from typing import Any

from market.models import (
    CompanyProfile,
    KeyRatios,
    CorporateAction,
    Competitor,
)


# ---------------------------------------------------------------------------
# Company Profile
# ---------------------------------------------------------------------------


def company_profile_from_rest(payload: dict[str, Any]) -> CompanyProfile:
    """Normalize Upstox /fundamentals/:isin/profile response."""
    if not isinstance(payload, dict):
        raise ValueError("Company profile payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Company profile data must be a dict")

    cap_inr = data.get("sector_market_cap_inr") or {}
    cap_usd = data.get("sector_market_cap_usd") or {}

    return CompanyProfile(
        isin=payload.get("isin", "") or data.get("isin", ""),
        company_profile=str(data.get("company_profile", "")),
        sector=str(data.get("sector", "")),
        sector_market_cap_inr_crore=float(cap_inr.get("value") or 0)
        if cap_inr.get("value") is not None else None,
        sector_market_cap_usd_billion=float(cap_usd.get("value") or 0)
        if cap_usd.get("value") is not None else None,
    )


# ---------------------------------------------------------------------------
# Key Ratios
# ---------------------------------------------------------------------------


def key_ratios_from_rest(payload: dict[str, Any]) -> KeyRatios:
    """Normalize Upstox /fundamentals/:isin/ratios response."""
    if not isinstance(payload, dict):
        raise ValueError("Key ratios payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Key ratios data must be a dict")

    def safe_float(key: str) -> float | None:
        v = data.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return KeyRatios(
        isin=payload.get("isin", "") or data.get("isin", ""),
        pe_ratio=safe_float("pe_ratio"),
        pb_ratio=safe_float("pb_ratio"),
        roe=safe_float("roe"),
        roa=safe_float("roa"),
        roce=safe_float("roce"),
        ev_ebitda=safe_float("ev_ebitda"),
    )


# ---------------------------------------------------------------------------
# Corporate Actions
# ---------------------------------------------------------------------------


def corporate_actions_from_rest(payload: dict[str, Any]) -> list[CorporateAction]:
    """Normalize Upstox /fundamentals/:isin/corporate-actions response."""
    if not isinstance(payload, dict):
        raise ValueError("Corporate actions payload must be a dict")

    data = payload.get("data") or []
    if not isinstance(data, list):
        return []

    actions: list[CorporateAction] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        actions.append(CorporateAction(
            action_type=str(item.get("action_type", "")),
            description=str(item.get("description", "")),
            record_date=str(item.get("record_date")) if item.get("record_date") else None,
            ex_date=str(item.get("ex_date")) if item.get("ex_date") else None,
            payment_date=str(item.get("payment_date")) if item.get("payment_date") else None,
            value=float(item.get("value")) if item.get("value") is not None else None,
        ))

    return actions


# ---------------------------------------------------------------------------
# Competitors
# ---------------------------------------------------------------------------


def competitors_from_rest(payload: dict[str, Any]) -> list[Competitor]:
    """Normalize Upstox /fundamentals/:isin/competitors response."""
    if not isinstance(payload, dict):
        raise ValueError("Competitors payload must be a dict")

    data = payload.get("data") or []
    if not isinstance(data, list):
        return []

    competitors: list[Competitor] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        competitors.append(Competitor(
            instrument_key=str(item.get("instrument_key", "")),
            symbol=str(item.get("symbol", "")),
            name=str(item.get("name")) if item.get("name") else None,
            sector=str(item.get("sector")) if item.get("sector") else None,
        ))

    return competitors
