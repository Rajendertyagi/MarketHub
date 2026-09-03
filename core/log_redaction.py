"""Log message redaction for WebUI security.

Before any log record reaches the API/WebUI, sensitive information
must be stripped.  This module provides pattern-based redaction for
tokens, API keys, passwords, and other secrets.
"""

from __future__ import annotations

import re
from typing import Any

# ── Redaction patterns ──────────────────────────────────────────────────────
# Each pattern matches a common secret format and replaces it with <redacted>.
# Patterns are compiled once at module load.

_PATTERNS: list[re.Pattern[str]] = [
    # Bearer / Authorization headers
    re.compile(
        r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(Authorization['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]+=*",
        re.IGNORECASE,
    ),
    # API keys (common patterns)
    re.compile(
        r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{16,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(api[_-]?secret['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{16,}",
        re.IGNORECASE,
    ),
    # Access / refresh tokens
    re.compile(
        r"(access[_-]?token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{20,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(refresh[_-]?token['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{20,}",
        re.IGNORECASE,
    ),
    # PINs (numeric, 4-6 digits in key contexts)
    re.compile(
        r"(pin['\"]?\s*[:=]\s*['\"]?)\d{4,6}",
        re.IGNORECASE,
    ),
    # Passwords
    re.compile(
        r"(password['\"]?\s*[:=]\s*['\"]?)[^\s'\",;]{4,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(passwd['\"]?\s*[:=]\s*['\"]?)[^\s'\",;]{4,}",
        re.IGNORECASE,
    ),
    # Cookies / session tokens
    re.compile(
        r"(cookie['\"]?\s*[:=]\s*['\"]?)[^\s'\";]{8,}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(session[_-]?secret['\"]?\s*[:=]\s*['\"]?)[^\s'\",;]{8,}",
        re.IGNORECASE,
    ),
    # Generic secret-like base64 strings (>= 32 chars after common key names)
    re.compile(
        r"(secret['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{32,}",
        re.IGNORECASE,
    ),
    # Upstox-specific: WebSocket URLs with embedded tokens
    re.compile(
        r"(wss?://[^\s]*?)[?&](?:auth_token|access_token|token)=[A-Za-z0-9\-._~+/]+",
        re.IGNORECASE,
    ),
]

# The group-1 patterns above capture the key name and replace the value.
# We need a simpler replacement approach: match the full thing, keep the
# key portion, redact the value.

# Simpler approach: find-and-replace sensitive value patterns
_REDACT_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Bearer token value
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I), r"\1<redacted>"),
    # token= in URLs
    (re.compile(r"((?:access_token|auth_token|token)=[A-Za-z0-9\-._~+/]+)", re.I), r"<redacted>"),
    # Key-value patterns where value looks like a secret
    (re.compile(r"((?:api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|secret|password|passwd|pin|cookie|session[_-]?secret)\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{16,}", re.I), r"\1<redacted>"),
]


def redact_message(text: str) -> str:
    """Redact sensitive information from a log message string.

    This is a best-effort, performance-conscious redaction.  It catches
    the most common secret patterns without being overly aggressive.
    """
    if not text:
        return text
    for pattern, replacement in _REDACT_VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_record(record_dict: dict[str, Any]) -> dict[str, Any]:
    """Redact a structured log record dict before WebUI exposure.

    Redacts the ``message`` and ``exception`` fields.  Structured
    metadata fields (event, request_id, etc.) are never redacted.
    """
    if "message" in record_dict and record_dict["message"]:
        record_dict["message"] = redact_message(record_dict["message"])
    if "exception" in record_dict and record_dict["exception"]:
        record_dict["exception"] = redact_message(record_dict["exception"])
    return record_dict
