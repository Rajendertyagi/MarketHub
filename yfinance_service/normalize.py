"""JSON-safe conversion + honest freshness classification.

Two rules this module exists to enforce:
  1. Nothing raw (NaN, NaT, inf, numpy scalars, Timestamps, DataFrames,
     Series) ever crosses the MCP boundary.
  2. "Real-time" is never inferred. Freshness is a claim backed by evidence
     Yahoo actually supplied, or it is UNKNOWN.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

# The closed freshness vocabulary the AI sees.
LIVE_STREAM = "LIVE_STREAM"
SNAPSHOT = "SNAPSHOT"
DELAYED = "DELAYED"
UNKNOWN = "UNKNOWN"
UNAVAILABLE = "UNAVAILABLE"

FRESHNESS_VALUES = frozenset(
    {LIVE_STREAM, SNAPSHOT, DELAYED, UNKNOWN, UNAVAILABLE})


def json_safe(value: Any) -> Any:
    """Recursively convert to a JSON-serializable, NaN-free structure."""
    if value is None:
        return None
    # NaN / NaT / pd.NA
    try:
        if value is pd.NaT or value is pd.NA:
            return None
    except Exception:
        pass
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    # numpy scalars first — they masquerade as ints/floats
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(value, (pd.Timestamp, datetime)):
        if value is pd.NaT:
            return None
        ts = value if isinstance(value, pd.Timestamp) else pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        return ts.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(v) for v in value]
    if isinstance(value, pd.Series):
        return json_safe(value.to_dict())
    if isinstance(value, pd.DataFrame):
        return json_safe(records_from_frame(value))
    # Last resort — str() is the honest projection of an unknown type.
    return str(value)


def records_from_frame(df: Any, *, limit: int | None = None) -> list[dict]:
    """DataFrame -> list of JSON-safe row dicts (index becomes a column)."""
    if df is None:
        return []
    try:
        if len(df) == 0:
            return []
    except TypeError:
        return []
    out = df.reset_index()
    if limit is not None and limit > 0:
        out = out.head(limit)
    return [json_safe({str(k): v for k, v in row.items()})
            for row in out.to_dict(orient="records")]


def series_to_records(series: Any, *, value_name: str = "value",
                      date_name: str = "date",
                      limit: int | None = None) -> list[dict]:
    """Index->value Series (dividends, splits) -> [{date, value}, ...]."""
    if series is None:
        return []
    try:
        if len(series) == 0:
            return []
    except TypeError:
        return []
    items: list[dict] = []
    for idx, val in series.items():
        items.append({date_name: json_safe(idx), value_name: json_safe(val)})
    items.sort(key=lambda r: str(r.get(date_name) or ""))
    if limit is not None and limit > 0:
        items = items[-limit:]
    return items


def classify_freshness(info: dict[str, Any] | None) -> str:
    """Freshness from Yahoo's own evidence. Never guesses 'real-time'.

    LIVE_STREAM requires positive evidence: zero reported delay AND a
    real-time source name. Anything else is SNAPSHOT / DELAYED / UNKNOWN.
    """
    if not info:
        return UNKNOWN
    delay = info.get("exchangeDataDelayedBy")
    source = str(info.get("quoteSourceName") or "")
    if delay is not None:
        try:
            d = float(delay)
        except (TypeError, ValueError):
            return UNKNOWN
        if d <= 0 and "real time" in source.lower():
            return LIVE_STREAM
        if d > 0:
            return DELAYED
        return SNAPSHOT
    if "real time" in source.lower():
        return LIVE_STREAM
    return UNKNOWN
