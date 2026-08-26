"""
Upstox futures smartlist and FII/DII normalizers.

Converts raw API responses into canonical FuturesSmartlist, FIIActivity,
and DIIActivity objects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from market.models import (
    FuturesSmartlist,
    FuturesSmartlistEntry,
    FIIRecord,
    FIIActivity,
    DIIRecord,
    DIIActivity,
)


# ---------------------------------------------------------------------------
# Futures Smartlist
# ---------------------------------------------------------------------------


def futures_smartlist_from_rest(payload: dict[str, Any]) -> FuturesSmartlist:
    """Normalize Upstox /market/smartlist/futures response."""
    if not isinstance(payload, dict):
        raise ValueError("Futures smartlist payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("Futures smartlist data must be a dict")

    asset_type = str(data.get("asset_type", ""))
    category = str(data.get("category", ""))
    metric_key = str(data.get("metric_key", ""))
    ts = data.get("time_stamp")

    timestamp = None
    if ts is not None:
        try:
            timestamp = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass

    entries: list[FuturesSmartlistEntry] = []
    for item in data.get("smartlist") or []:
        if not isinstance(item, dict):
            continue

        price = item.get("price") or {}
        metric = item.get("metric") or {}

        try:
            entries.append(FuturesSmartlistEntry(
                instrument_key=str(item.get("instrument_key", "")),
                price_current=float(price.get("current", 0) or 0),
                price_close=float(price.get("close_price", 0) or 0),
                price_change_abs=float(price.get("change_abs", 0) or 0),
                price_change_pct=float(price.get("change_pct", 0) or 0),
                metric_current=float(metric.get("current", 0) or 0),
                metric_previous=float(metric.get("previous", 0) or 0),
                metric_change_abs=float(metric.get("change_abs", 0) or 0),
                metric_change_pct=float(metric.get("change_pct", 0) or 0),
                metric_key=metric_key,
            ))
        except (TypeError, ValueError):
            continue

    return FuturesSmartlist(
        asset_type=asset_type,
        category=category,
        metric_key=metric_key,
        timestamp=timestamp,
        entries=tuple(entries),
        page_number=int(data.get("page_number") or 1),
        page_size=int(data.get("page_size") or 0),
        total_pages=int(data.get("total_pages") or 1),
    )


# ---------------------------------------------------------------------------
# FII Activity
# ---------------------------------------------------------------------------


def _parse_fii_dii_record(item: dict, cls) -> Any:
    """Parse a single FII/DII record item."""
    ts = item.get("time_stamp")
    timestamp = None
    if ts is not None:
        try:
            timestamp = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass

    return cls(
        timestamp=timestamp,
        buy_amount=float(item.get("buy_amount") or 0),
        sell_amount=float(item.get("sell_amount") or 0),
        buy_contracts=int(item.get("buy_contracts") or 0),
        sell_contracts=int(item.get("sell_contracts") or 0),
        oi_contracts=int(item.get("oi_contracts") or 0),
        oi_amount=float(item.get("oi_amount") or 0),
        total_long_contracts=int(item.get("total_long_contracts") or 0),
        total_short_contracts=int(item.get("total_short_contracts") or 0),
        total_call_long_contracts=int(item.get("total_call_long_contracts") or 0),
        total_put_long_contracts=int(item.get("total_put_long_contracts") or 0),
        total_call_short_contracts=int(item.get("total_call_short_contracts") or 0),
        total_put_short_contracts=int(item.get("total_put_short_contracts") or 0),
    )


def fii_from_rest(payload: dict[str, Any]) -> dict[str, FIIActivity]:
    """Normalize Upstox /market/fii response into per-segment FIIActivity."""
    if not isinstance(payload, dict):
        raise ValueError("FII payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("FII data must be a dict")

    interval = payload.get("interval", "1D")

    result: dict[str, FIIActivity] = {}
    for key, records in data.items():
        if not isinstance(records, list):
            continue
        entries = [_parse_fii_dii_record(r, FIIRecord) for r in records]
        result[key] = FIIActivity(
            data_type=key,
            interval=str(interval),
            records=tuple(entries),
        )

    return result


def fii_single_from_rest(payload: dict[str, Any], data_type: str) -> FIIActivity:
    """Normalize FII response for a single data_type."""
    if not isinstance(payload, dict):
        raise ValueError("FII payload must be a dict")

    data = payload.get("data") or {}
    records = data.get(data_type) or []
    if not isinstance(records, list):
        records = []

    entries = [_parse_fii_dii_record(r, FIIRecord) for r in records]
    return FIIActivity(
        data_type=data_type,
        interval=str(payload.get("interval", "1D")),
        records=tuple(entries),
    )


# ---------------------------------------------------------------------------
# DII Activity
# ---------------------------------------------------------------------------


def dii_from_rest(payload: dict[str, Any]) -> dict[str, DIIActivity]:
    """Normalize Upstox /market/dii response into per-segment DIIActivity."""
    if not isinstance(payload, dict):
        raise ValueError("DII payload must be a dict")

    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("DII data must be a dict")

    interval = payload.get("interval", "1D")

    result: dict[str, DIIActivity] = {}
    for key, records in data.items():
        if not isinstance(records, list):
            continue
        entries = [_parse_fii_dii_record(r, DIIRecord) for r in records]
        result[key] = DIIActivity(
            data_type=key,
            interval=str(interval),
            records=tuple(entries),
        )

    return result


def dii_single_from_rest(payload: dict[str, Any], data_type: str) -> DIIActivity:
    """Normalize DII response for a single data_type."""
    if not isinstance(payload, dict):
        raise ValueError("DII payload must be a dict")

    data = payload.get("data") or {}
    records = data.get(data_type) or []
    if not isinstance(records, list):
        records = []

    entries = [_parse_fii_dii_record(r, DIIRecord) for r in records]
    return DIIActivity(
        data_type=data_type,
        interval=str(payload.get("interval", "1D")),
        records=tuple(entries),
    )
