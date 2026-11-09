"""Yahoo Finance research configuration — non-secret, no credentials.

yfinance's public Yahoo endpoints need no API key, no login, and no stored
cookie. This module therefore holds ONLY non-secret tuning knobs (bounds,
delays) and deliberately has no secret fields at all.
"""
from __future__ import annotations

from typing import Any

# Pinned upstream version this capability is written and tested against.
YFINANCE_VERSION = "1.7.0"

# yfinance-supported intervals (verified against upstream docs/source).
VALID_INTERVALS = (
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h", "1d", "5d", "1wk", "1mo", "3mo",
)

# yfinance-supported lookback periods.
VALID_PERIODS = (
    "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max",
)

# Bounds — prevent a single AI turn from becoming a request storm.
MAX_SEARCH_LIMIT = 20
MAX_HISTORY_ROWS = 5000
MAX_STATEMENT_ROWS = 400
MAX_HOLDER_ROWS = 100
MAX_OPTION_ROWS = 200
MAX_BATCH_SYMBOLS = 20
MAX_SUMMARY_CHARS = 1200
MAX_SUMMARY_LIST = 5

# Live stream bounds — one bounded tick collection, never a permanent socket.
DEFAULT_STREAM_TIMEOUT_SECONDS = 10.0
MAX_STREAM_TIMEOUT_SECONDS = 30.0
MAX_STREAM_TICKS = 1

# Politeness: minimum gap between outbound yfinance calls (seconds).
MIN_REQUEST_INTERVAL_SECONDS = 0.5


def validate_config(patch: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a settings patch. Raises ValueError on anything unknown."""
    if patch is None:
        return {}
    if not isinstance(patch, dict):
        raise ValueError("yahoo config must be an object")
    allowed = {
        "min_request_interval_seconds": float,
        "stream_timeout_seconds": float,
    }
    unknown = set(patch) - set(allowed)
    if unknown:
        raise ValueError(f"unknown yahoo config keys: {sorted(unknown)}")
    out: dict[str, Any] = {}
    for key, caster in allowed.items():
        if key in patch:
            try:
                out[key] = caster(patch[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number") from None
    interval = out.get("min_request_interval_seconds")
    if interval is not None and not (0.0 <= interval <= 60.0):
        raise ValueError("min_request_interval_seconds must be 0..60")
    timeout = out.get("stream_timeout_seconds")
    if timeout is not None and not (
        0.0 < timeout <= MAX_STREAM_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"stream_timeout_seconds must be 0..{MAX_STREAM_TIMEOUT_SECONDS}")
    return out


def defaults() -> dict[str, Any]:
    return {
        "min_request_interval_seconds": MIN_REQUEST_INTERVAL_SECONDS,
        "stream_timeout_seconds": DEFAULT_STREAM_TIMEOUT_SECONDS,
    }
