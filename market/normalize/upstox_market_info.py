"""
Upstox market information normalizers.

Converts raw /market/holidays and /market/timings responses into
canonical MarketHoliday and MarketSession objects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from market.models import MarketHoliday, MarketSession


# ---------------------------------------------------------------------------
# Market Holidays
# ---------------------------------------------------------------------------


def holidays_from_rest(payload: dict[str, Any]) -> list[MarketHoliday]:
    """Normalize Upstox /market/holidays response into MarketHoliday list."""
    if not isinstance(payload, dict):
        raise ValueError("Holidays payload must be a dict")

    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ValueError("Holidays data must be a list")

    holidays: list[MarketHoliday] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        date = item.get("date", "")
        description = item.get("description", "")
        holiday_type = item.get("holiday_type", "")

        closed = item.get("closed_exchanges") or []
        closed_tuple = tuple(str(e) for e in closed if isinstance(e, str))

        open_ex = item.get("open_exchanges") or []
        open_tuple: tuple[str, ...] = ()
        for oe in open_ex:
            if isinstance(oe, dict) and "exchange" in oe:
                open_tuple = open_tuple + (str(oe["exchange"]),)

        holidays.append(MarketHoliday(
            date=str(date),
            description=str(description),
            holiday_type=str(holiday_type),
            closed_exchanges=closed_tuple,
            open_exchanges=open_tuple,
        ))

    return holidays


def holiday_status_from_rest(payload: dict[str, Any]) -> MarketHoliday | None:
    """Normalize /market/holidays/:date response into single MarketHoliday."""
    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    date = data.get("date", "")
    description = data.get("description", "")
    holiday_type = data.get("holiday_type", "")

    closed = data.get("closed_exchanges") or []
    closed_tuple = tuple(str(e) for e in closed if isinstance(e, str))

    open_ex = data.get("open_exchanges") or []
    open_tuple: tuple[str, ...] = ()
    for oe in open_ex:
        if isinstance(oe, dict) and "exchange" in oe:
            open_tuple = open_tuple + (str(oe["exchange"]),)

    return MarketHoliday(
        date=str(date),
        description=str(description),
        holiday_type=str(holiday_type),
        closed_exchanges=closed_tuple,
        open_exchanges=open_tuple,
    )


# ---------------------------------------------------------------------------
# Market Timings
# ---------------------------------------------------------------------------


def timings_from_rest(payload: dict[str, Any]) -> list[MarketSession]:
    """Normalize Upstox /market/timings/:date response into MarketSession list."""
    if not isinstance(payload, dict):
        raise ValueError("Timings payload must be a dict")

    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ValueError("Timings data must be a list")

    sessions: list[MarketSession] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        exchange = item.get("exchange", "")
        start_ts = item.get("start_time")
        end_ts = item.get("end_time")

        start_time = None
        end_time = None
        if start_ts is not None:
            try:
                start_time = datetime.fromtimestamp(
                    int(start_ts) / 1000, tz=timezone.utc
                )
            except (ValueError, OSError, OverflowError):
                pass
        if end_ts is not None:
            try:
                end_time = datetime.fromtimestamp(
                    int(end_ts) / 1000, tz=timezone.utc
                )
            except (ValueError, OSError, OverflowError):
                pass

        if exchange and start_time and end_time:
            sessions.append(MarketSession(
                exchange=str(exchange),
                start_time=start_time,
                end_time=end_time,
            ))

    return sessions
