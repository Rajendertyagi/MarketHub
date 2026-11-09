"""X/Twitter non-secret settings schema/validation.

Secrets (auth_token/ct0) live ONLY in the encrypted CredentialStore
(provider "x") and never in this config. This module validates the
poll/display settings persisted in the x_config table.
"""

from __future__ import annotations

from typing import Any

POLL_INTERVAL_FLOOR_SECONDS = 60
DEFAULT_LIMIT_MAX = 20
DEFAULT_POLL_INTERVAL = 120
DEFAULT_LIMIT = 20
DEFAULT_RETENTION_DAYS = 30


def validate_poll_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a poll-config dict. Raises ValueError."""
    if not isinstance(cfg, dict):
        raise ValueError("x config must be an object")
    out: dict[str, Any] = {}
    if "enabled" in cfg:
        out["enabled"] = bool(cfg["enabled"])
    if "poll_interval_seconds" in cfg:
        try:
            iv = int(cfg["poll_interval_seconds"])
        except (ValueError, TypeError):
            raise ValueError("poll_interval_seconds must be an integer") from None
        if iv < POLL_INTERVAL_FLOOR_SECONDS:
            raise ValueError(
                f"poll_interval_seconds must be >= {POLL_INTERVAL_FLOOR_SECONDS}")
        out["poll_interval_seconds"] = iv
    if "default_limit" in cfg:
        try:
            lim = int(cfg["default_limit"])
        except (ValueError, TypeError):
            raise ValueError("default_limit must be an integer") from None
        if lim < 1 or lim > DEFAULT_LIMIT_MAX:
            raise ValueError(
                f"default_limit must be between 1 and {DEFAULT_LIMIT_MAX}")
        out["default_limit"] = lim
    if "retention_days" in cfg:
        try:
            ret = int(cfg["retention_days"])
        except (ValueError, TypeError):
            raise ValueError("retention_days must be an integer") from None
        if ret < 1:
            raise ValueError("retention_days must be >= 1")
        out["retention_days"] = ret
    if "proxy" in cfg:
        proxy = cfg["proxy"] or ""
        if proxy is not None and not isinstance(proxy, str):
            raise ValueError("proxy must be a string")
        out["proxy"] = (proxy or "").strip()
    if "request_delay" in cfg:
        try:
            delay = float(cfg["request_delay"])
        except (ValueError, TypeError):
            raise ValueError("request_delay must be a number") from None
        if delay < 0 or delay > 60:
            raise ValueError("request_delay must be between 0 and 60")
        out["request_delay"] = delay
    if "cli_path" in cfg:
        cp = cfg["cli_path"] or ""
        if cp is not None and not isinstance(cp, str):
            raise ValueError("cli_path must be a string")
        out["cli_path"] = (cp or "").strip()
    unknown = set(cfg) - {"enabled", "poll_interval_seconds", "default_limit",
                           "retention_days", "proxy", "request_delay", "cli_path"}
    if unknown:
        raise ValueError(f"unknown x config keys: {sorted(unknown)}")
    return out


def defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        "default_limit": DEFAULT_LIMIT,
        "retention_days": DEFAULT_RETENTION_DAYS,
        "cli_path": "",
        "proxy": "",
        "request_delay": 2.0,
    }
