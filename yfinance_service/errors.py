"""Structured error types for the Yahoo Finance research capability.

Closed code set — these are the ONLY values that cross the MCP boundary.
Provider outcomes (rate limits, dead symbols, upstream outages) become DATA
payloads, never raw exceptions, so the AI gets a reason it can act on.

No cookies, crumbs, headers, or tracebacks ever leave this module.
"""
from __future__ import annotations

import re
from typing import Any

# The closed, fail-closed code set.
INVALID_SYMBOL = "invalid_symbol"
SYMBOL_NOT_FOUND = "symbol_not_found"
DATA_UNAVAILABLE = "data_unavailable"
UNAVAILABLE_FOR_ASSET = "unavailable_for_asset"
RATE_LIMITED = "rate_limited"
NETWORK_ERROR = "network_error"
UNSUPPORTED_OPERATION = "unsupported_operation"
STREAM_UNAVAILABLE = "stream_unavailable"
SERVICE_UNAVAILABLE = "service_unavailable"
INVALID_PERIOD = "invalid_period"
INVALID_INTERVAL = "invalid_interval"
INVALID_STATEMENT = "invalid_statement"
INVALID_HOLDER_TYPE = "invalid_holder_type"
INVALID_ARGUMENTS = "invalid_arguments"
INVALID_REQUEST = "invalid_request"

YAHOO_ERROR_CODES = frozenset({
    INVALID_SYMBOL, SYMBOL_NOT_FOUND, DATA_UNAVAILABLE,
    UNAVAILABLE_FOR_ASSET, RATE_LIMITED, NETWORK_ERROR,
    UNSUPPORTED_OPERATION, STREAM_UNAVAILABLE, SERVICE_UNAVAILABLE,
    INVALID_PERIOD, INVALID_INTERVAL, INVALID_STATEMENT,
    INVALID_HOLDER_TYPE, INVALID_ARGUMENTS, INVALID_REQUEST,
})

_REASON_MAX = 200

# Redaction patterns — blobs, URLs with query strings, filesystem paths.
_BLOB_RE = re.compile(r"[A-Za-z0-9\-_.+/=]{40,}")
_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]*")
_WINPATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\)*([^\\\s]+)")


def safe_reason(exc: BaseException) -> str:
    """First line, length-capped, redacted. Never a traceback."""
    text = str(exc).strip().splitlines()
    out = (text[0] if text else "").strip()
    out = _QUERY_RE.sub(r"\1", out)
    out = _WINPATH_RE.sub(r"\1", out)
    out = _BLOB_RE.sub("<redacted>", out)
    return out[:_REASON_MAX] or type(exc).__name__


class YahooError(Exception):
    """Base for Yahoo research errors. Carries a closed-set ``code``."""

    def __init__(self, code: str, reason: str,
                 freshness: str = "UNAVAILABLE") -> None:
        if code not in YAHOO_ERROR_CODES:
            raise ValueError(f"unknown yahoo error code: {code}")
        self.code = code
        self.reason = safe_reason(Exception(reason))
        self.freshness = freshness
        super().__init__(self.reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "error",
            "source": "yahoo",
            "code": self.code,
            "reason": self.reason,
            "freshness": self.freshness,
        }


class YahooDataError(YahooError):
    def __init__(self, reason: str, code: str = DATA_UNAVAILABLE) -> None:
        super().__init__(code, reason)


class YahooInvalidSymbolError(YahooDataError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason, INVALID_SYMBOL)


class YahooRateLimitError(YahooError):
    def __init__(self, reason: str) -> None:
        super().__init__(RATE_LIMITED, reason)


class YahooUnsupportedOperationError(YahooError):
    def __init__(self, reason: str) -> None:
        super().__init__(UNSUPPORTED_OPERATION, reason)


class YahooStreamUnavailableError(YahooError):
    def __init__(self, reason: str) -> None:
        super().__init__(STREAM_UNAVAILABLE, reason)


# ── Provider exception mapping ───────────────────────────────────────────────

_RATE_LIMIT_MARKERS = (
    "too many requests", "rate limit", "rate limited", "429",
    "throttle", "quota exceeded", "yy/ratelimit",
)
_NOT_FOUND_MARKERS = (
    "no data found", "not found", "404", "delisted", "no price data",
    "symbol may be delisted", "quote not found",
)
_UNSUPPORTED_MARKERS = (
    "not implemented", "unsupported", "cannot fetch", "unavailable for",
    "has not implemented",
)


def map_yfinance_exception(exc: BaseException) -> YahooError:
    """Map any yfinance/urllib failure onto the closed code set.

    Fails closed: an unrecognized failure becomes ``network_error`` with a
    redacted reason — never a raw traceback, never a silent success.
    """
    if isinstance(exc, YahooError):
        return exc
    text = str(exc).lower()
    if any(m in text for m in _RATE_LIMIT_MARKERS):
        return YahooRateLimitError(safe_reason(exc))
    if any(m in text for m in _UNSUPPORTED_MARKERS):
        return YahooUnsupportedOperationError(safe_reason(exc))
    if any(m in text for m in _NOT_FOUND_MARKERS):
        return YahooInvalidSymbolError(safe_reason(exc))
    return YahooDataError(safe_reason(exc), NETWORK_ERROR)
