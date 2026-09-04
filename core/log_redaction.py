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
    # Bearer token value (preserves "Bearer " prefix)
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.I), r"\1<redacted>"),
    # Authorization header value (preserves "Authorization:" prefix)
    (re.compile(
        r"(Authorization['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]+=*",
        re.I), r"\1<redacted>"),
    # token= in URLs (query params)
    (re.compile(
        r"((?:access_token|auth_token|token)=[A-Za-z0-9\-._~+/]+)",
        re.I), r"<redacted>"),
    # Bearer in URL query param (e.g. ?token=Bearer eyJ...)
    (re.compile(
        r"(token=[Bb]earer\s+)[A-Za-z0-9\-._~+/]+=*",
        re.I), r"<redacted>"),
    # Key-value patterns where value looks like a secret
    (re.compile(
        r"((?:api[_-]?key|api[_-]?secret|access[_-]?token|refresh[_-]?token|"
        r"secret|password|passwd|pin|cookie|session[_-]?secret|client[_-]?secret|"
        r"app[_-]?id|app[_-]?secret)\s*[:=]\s*['\"]?)[A-Za-z0-9\-._~+/]{16,}",
        re.I), r"\1<redacted>"),
    # Passwords without quotes (e.g. password = mysecretpassword123)
    (re.compile(
        r"(password\s*[:=]\s*)[^\s'\",;]{4,}",
        re.I), r"\1<redacted>"),
    # PIN values (4-6 digits in key contexts)
    (re.compile(
        r"((?:pin|mpin|otp|security[_-]?code)\s*[:=]\s*['\"]?)\d{4,8}",
        re.I), r"\1<redacted>"),
    # WebSocket URLs with embedded auth tokens
    (re.compile(
        r"(wss?://[^\s]*?)[?&](?:auth_token|access_token|token|key)"
        r"=[A-Za-z0-9\-._~+/]+",
        re.I), r"<redacted>"),
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

    Redacts the ``message`` and ``exception`` string fields and
    recursively redacts the structured ``extra`` metadata so secrets
    smuggled in nested dicts/lists cannot reach the buffer, REST, or SSE.
    Plain correlation ids (event, request_id, …) that carry no secret
    pattern pass through unchanged.
    """
    if "message" in record_dict and record_dict["message"]:
        record_dict["message"] = bound_string(
            redact_message(record_dict["message"]), _MAX_LOG_MESSAGE_LEN)
    if "exception" in record_dict and record_dict["exception"]:
        record_dict["exception"] = bound_string(
            redact_message(record_dict["exception"]), _MAX_LOG_EXCEPTION_LEN)
    if "extra" in record_dict and record_dict["extra"] is not None:
        record_dict["extra"] = redact_value(record_dict["extra"])
    return record_dict


# ── Recursive structured redaction ──────────────────────────────────────────
# Secrets hidden in structured ``extra`` metadata (nested dicts/lists) must
# never reach the WebUI buffer, REST responses, or the SSE stream.

_SENSITIVE_KEY = re.compile(
    r"(token|authoriz|api[_-]?key|apikey|api[_-]?secret|secret|password|"
    r"passwd|pwd|pin|mpin|otp|security[_-]?code|cookie|session|client[_-]?secret|"
    r"credential|private[_-]?key|auth)",
    re.IGNORECASE,
)

# ── WebUI payload bounds ────────────────────────────────────────────────────
# KB-scale per-string caps so one pathological record (multi-MB message,
# exception, or nested extra) can never bloat the bounded buffer or the
# SSE stream.  Redaction ALWAYS runs before truncation so a secret can
# never survive by straddling the cut point.

_MAX_LOG_MESSAGE_LEN = 4096
_MAX_LOG_EXCEPTION_LEN = 8192
_MAX_LOG_EXTRA_STRING_LEN = 2048
_MAX_STRUCT_DEPTH = 6
_MAX_STRUCT_ITEMS = 200


def bound_string(text: str, max_len: int = _MAX_LOG_MESSAGE_LEN) -> str:
    """Truncate an over-long string with an explicit marker (no-op if short)."""
    if not isinstance(text, str) or len(text) <= max_len:
        return text
    return text[:max_len] + f"…<truncated +{len(text) - max_len} chars>"


def redact_value(value: Any, _depth: int = 0) -> Any:
    """Recursively redact secrets in arbitrary structured values.

    - strings → :func:`redact_message` (catches Bearer/URL/key patterns),
      then bounded to KB scale with an explicit truncation marker
    - dicts → recurse; a value under a sensitive key name is replaced
      wholesale with ``"<redacted>"``; containers are capped at
      ``_MAX_STRUCT_ITEMS`` entries and ``_MAX_STRUCT_DEPTH`` depth so
      pathological structures cannot exhaust memory
    - lists/tuples/sets → recurse element-wise, preserving container type
    - anything else (numbers, bools, None, …) → returned unchanged
    """
    if isinstance(value, str):
        return bound_string(redact_message(value), _MAX_LOG_EXTRA_STRING_LEN)
    if _depth >= _MAX_STRUCT_DEPTH:
        return "<max-depth-exceeded>"
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_STRUCT_ITEMS:
                out["…<truncated>"] = f"<+{len(value) - i} entries>"
                break
            if isinstance(k, str) and _SENSITIVE_KEY.search(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact_value(v, _depth + 1)
        return out
    if isinstance(value, list):
        truncated = value[:_MAX_STRUCT_ITEMS]
        out_list = [redact_value(v, _depth + 1) for v in truncated]
        if len(value) > _MAX_STRUCT_ITEMS:
            out_list.append(f"…<truncated +{len(value) - _MAX_STRUCT_ITEMS} items>")
        return out_list
    if isinstance(value, tuple):
        truncated = value[:_MAX_STRUCT_ITEMS]
        out_tuple = tuple(redact_value(v, _depth + 1) for v in truncated)
        if len(value) > _MAX_STRUCT_ITEMS:
            out_tuple = out_tuple + (f"…<truncated +{len(value) - _MAX_STRUCT_ITEMS} items>",)
        return out_tuple
    if isinstance(value, (set, frozenset)):
        items = list(value)[:_MAX_STRUCT_ITEMS]
        return type(value)(redact_value(v, _depth + 1) for v in items)
    return value
