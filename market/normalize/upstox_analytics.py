"""
Upstox OI analytics normalizers.

Converts raw Upstox /market/oi, /market/change-oi, /market/max-pain,
/market/pcr responses into canonical OISnapshot, OIChangeSnapshot,
MaxPainData, PCRData objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market.models import (
    OIStrikeRow,
    OISnapshot,
    OIChangeStrikeRow,
    OIChangeSnapshot,
    MaxPainData,
    PCRData,
)


# ---------------------------------------------------------------------------
# OI API normalizer
# ---------------------------------------------------------------------------


def oi_from_rest(payload: dict[str, Any]) -> OISnapshot:
    """Normalize Upstox /market/oi response into OISnapshot."""
    if not isinstance(payload, dict):
        raise ValueError("OI payload must be a dict")

    data = payload.get("data") or {}
    instrument_token = payload.get("instrument_key", "")
    exchange = instrument_token.partition("|")[0] if "|" in instrument_token else ""
    expiry = data.get("expiry", "")
    spot = data.get("spot_closing_price")

    call_oi = data.get("total_calls")
    put_oi = data.get("total_puts")

    strikes: list[OIStrikeRow] = []
    for row in data.get("call_put_oi_data_list") or []:
        if not isinstance(row, dict):
            continue
        try:
            strike = float(row.get("strike_price", 0))
        except (TypeError, ValueError):
            continue
        strikes.append(OIStrikeRow(
            strike_price=strike,
            call_oi=int(row.get("call_oi") or 0),
            put_oi=int(row.get("put_oi") or 0),
        ))

    return OISnapshot(
        instrument_token=instrument_token,
        exchange=exchange,
        expiry=expiry,
        spot_closing_price=spot,
        total_call_oi=int(call_oi) if call_oi is not None else None,
        total_put_oi=int(put_oi) if put_oi is not None else None,
        strikes=tuple(strikes),
    )


# ---------------------------------------------------------------------------
# OI Change API normalizer
# ---------------------------------------------------------------------------


def oi_change_from_rest(payload: dict[str, Any]) -> OIChangeSnapshot:
    """Normalize Upstox /market/change-oi response into OIChangeSnapshot."""
    if not isinstance(payload, dict):
        raise ValueError("OI change payload must be a dict")

    data = payload.get("data") or {}
    instrument_token = payload.get("instrument_key", "")
    exchange = instrument_token.partition("|")[0] if "|" in instrument_token else ""
    expiry = data.get("expiry", "")
    spot = data.get("spot_closing_price")
    days = data.get("interval")

    call_change = data.get("total_call_change_oi")
    put_change = data.get("total_put_change_oi")

    strikes: list[OIChangeStrikeRow] = []
    for row in data.get("call_put_oi_data_list") or []:
        if not isinstance(row, dict):
            continue
        try:
            strike = float(row.get("strike_price", 0))
        except (TypeError, ValueError):
            continue
        strikes.append(OIChangeStrikeRow(
            strike_price=strike,
            call_change_oi=int(row.get("call_change_oi") or 0),
            put_change_oi=int(row.get("put_change_oi") or 0),
        ))

    return OIChangeSnapshot(
        instrument_token=instrument_token,
        exchange=exchange,
        expiry=expiry,
        spot_closing_price=spot,
        total_call_change_oi=int(call_change) if call_change is not None else None,
        total_put_change_oi=int(put_change) if put_change is not None else None,
        days=int(days) if days is not None else None,
        strikes=tuple(strikes),
    )


# ---------------------------------------------------------------------------
# Max Pain API normalizer
# ---------------------------------------------------------------------------


def max_pain_from_rest(payload: dict[str, Any]) -> MaxPainData:
    """Normalize Upstox /market/max-pain response into MaxPainData."""
    if not isinstance(payload, dict):
        raise ValueError("Max pain payload must be a dict")

    data = payload.get("data") or {}
    instrument_token = payload.get("instrument_key", "")
    exchange = instrument_token.partition("|")[0] if "|" in instrument_token else ""
    expiry = data.get("expiry", "")

    return MaxPainData(
        instrument_token=instrument_token,
        exchange=exchange,
        expiry=expiry,
        max_pain_strike=data.get("max_pain_strike"),
        max_pain_value=data.get("max_pain_value"),
        spot_price=data.get("spot_price"),
        total_call_pain=data.get("total_call_pain"),
        total_put_pain=data.get("total_put_pain"),
    )


# ---------------------------------------------------------------------------
# PCR API normalizer
# ---------------------------------------------------------------------------


def pcr_from_rest(payload: dict[str, Any]) -> PCRData:
    """Normalize Upstox /market/pcr response into PCRData."""
    if not isinstance(payload, dict):
        raise ValueError("PCR payload must be a dict")

    data = payload.get("data") or {}
    instrument_token = payload.get("instrument_key", "")
    exchange = instrument_token.partition("|")[0] if "|" in instrument_token else ""
    expiry = data.get("expiry")

    return PCRData(
        instrument_token=instrument_token,
        exchange=exchange,
        expiry=str(expiry) if expiry else None,
        pcr=data.get("pcr"),
        total_put_oi=data.get("total_put_oi"),
        total_call_oi=data.get("total_call_oi"),
        spot_price=data.get("spot_price"),
    )
