"""
Server configuration loading and validation.

Extracted from server.py to keep the main module focused on MCP wiring.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("event_server")

# Default configuration values (overridden by config.json when present)
DEFAULTS: dict[str, Any] = {
    "server_name": "MCP Event Server",
    "host": "127.0.0.1",
    "port": 7070,
    "log_level": "INFO",
    "max_request_body_size_mb": 4,
    "data_dir": "data",
    "timeouts": {
        "default_tool_seconds": 30,
        "database_seconds": 10,
        "shutdown_seconds": 10,
    },
    "replay": {
        "default_limit": 50,
        "max_limit": 500,
    },
    "retention": {
        "max_age_days": 0,   # 0 = disabled
        "max_rows": 0,       # 0 = disabled
    },
    # Public base URL used to build OAuth redirect/callback URLs. MUST be an
    # explicit, operator-configured value — never derived from request Host
    # headers (avoids DNS-rebinding / open-redirect style abuse). Defaults to
    # the local dev address; override for LAN / reverse-proxy deployments.
    "public_base_url": "http://localhost:7070",
}


class ConfigError(Exception):
    """Raised when configuration is invalid."""


def load_config(config_path: str) -> dict[str, Any]:
    """
    Load and validate configuration from config.json.

    Returns a merged dict: explicit config values override defaults.
    Missing file is not an error — defaults are used.
    """
    config: dict[str, Any] = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}

    if not os.path.isfile(config_path):
        logger.info("config.json not found — using built-in defaults")
        return config

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_config = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "config.json contains invalid JSON: {0}".format(exc)
        ) from exc
    except OSError as exc:
        raise ConfigError(
            "Unable to read config.json: {0}".format(exc)
        ) from exc

    if not isinstance(user_config, dict):
        raise ConfigError("config.json must contain a JSON object at the top level")

    known_keys = set(DEFAULTS.keys()) | {
        "allowed_hosts",
        "allowed_origins",
        "enable_dns_rebinding_protection",
        "sources",
    }
    for key, value in user_config.items():
        if key.startswith("_"):
            continue  # comment keys are ignored silently
        if key not in known_keys:
            logger.warning("Unknown config key '%s' — ignored", key)
            continue
        if isinstance(value, dict) and key in config and isinstance(config[key], dict):
            config[key].update(value)
        else:
            config[key] = value

    return config


def validate_config(config: dict[str, Any]) -> None:
    """
    Validate configuration values. Raises ConfigError with a clear message on failure.
    """
    host = config.get("host")
    if not isinstance(host, str) or not host:
        raise ConfigError("'host' must be a non-empty string")
    if host in ("0.0.0.0", "::", "*"):
        raise ConfigError(
            "Refusing to bind to '{0}': this exposes the server to the network. "
            "Set 'host' to 127.0.0.1 (or a specific LAN IP) in config.json.".format(host)
        )

    port = config.get("port")
    if not isinstance(port, int) or port < 1 or port > 65535:
        raise ConfigError("'port' must be an integer between 1 and 65535")

    valid_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    log_level = config.get("log_level", "INFO").upper()
    if log_level not in valid_levels:
        raise ConfigError(
            "'log_level' must be one of: {0}".format(", ".join(valid_levels))
        )

    base_url = config.get("public_base_url", "http://localhost:7070")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ConfigError("'public_base_url' must be a non-empty string")
    import urllib.parse as _urlparse
    _parts = _urlparse.urlsplit(base_url.strip())
    if _parts.scheme not in ("http", "https") or not _parts.netloc:
        raise ConfigError(
            "'public_base_url' must be a valid http(s) URL "
            "(e.g. http://localhost:7070)"
        )
    config["log_level"] = log_level

    max_body_mb = config.get("max_request_body_size_mb")
    if not isinstance(max_body_mb, (int, float)) or max_body_mb < 0.1 or max_body_mb > 100:
        raise ConfigError("'max_request_body_size_mb' must be a number between 0.1 and 100")
    config["max_request_body_size"] = int(max_body_mb * 1024 * 1024)

    data_dir = config.get("data_dir", "data")
    if not isinstance(data_dir, str) or not data_dir:
        raise ConfigError("'data_dir' must be a non-empty string")

    # Validate timeouts
    timeouts = config.get("timeouts", {})
    for tk in ("default_tool_seconds", "database_seconds", "shutdown_seconds"):
        tv = timeouts.get(tk, DEFAULTS["timeouts"][tk])
        if not isinstance(tv, (int, float)) or tv <= 0:
            raise ConfigError(f"'timeouts.{tk}' must be a positive number")
        timeouts[tk] = tv
    config["timeouts"] = timeouts

    # Validate replay limits
    replay = config.get("replay", {})
    for rk in ("default_limit", "max_limit"):
        rv = replay.get(rk, DEFAULTS["replay"][rk])
        if not isinstance(rv, int) or rv < 1:
            raise ConfigError(f"'replay.{rk}' must be a positive integer")
    if replay.get("default_limit", 50) > replay.get("max_limit", 500):
        raise ConfigError("'replay.default_limit' must not exceed 'replay.max_limit'")
    config["replay"] = replay

    # Validate retention limits (0 = disabled; consumer-safe pruning)
    retention = config.get("retention", {})
    max_age_days = retention.get("max_age_days", DEFAULTS["retention"]["max_age_days"])
    if isinstance(max_age_days, bool) or not isinstance(max_age_days, (int, float)) or max_age_days < 0:
        raise ConfigError("'retention.max_age_days' must be a non-negative number")
    max_rows = retention.get("max_rows", DEFAULTS["retention"]["max_rows"])
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 0:
        raise ConfigError("'retention.max_rows' must be a non-negative integer")
    config["retention"] = {"max_age_days": max_age_days, "max_rows": int(max_rows)}

    if "enable_dns_rebinding_protection" in config:
        val = config["enable_dns_rebinding_protection"]
        if not isinstance(val, bool):
            raise ConfigError("'enable_dns_rebinding_protection' must be a boolean")

    if "allowed_hosts" in config:
        val = config["allowed_hosts"]
        if not isinstance(val, list) or not all(isinstance(h, str) for h in val):
            raise ConfigError("'allowed_hosts' must be a list of strings")

    if "allowed_origins" in config:
        val = config["allowed_origins"]
        if not isinstance(val, list) or not all(isinstance(o, str) for o in val):
            raise ConfigError("'allowed_origins' must be a list of strings")


# ---------------------------------------------------------------------------
# OAuth redirect / callback URL construction (single source of truth)
# ---------------------------------------------------------------------------

_DEFAULT_BASE_URL = "http://localhost:7070"


def _normalize_base_url(raw: Any) -> str:
    """Normalize an operator-supplied base URL to a bare origin.

    Returns ``scheme://host[:port]`` (lowercased), dropping any path, query,
    or fragment accidentally supplied. Returns "" when the value cannot be
    interpreted as an http(s) origin (callers fall back to the default).
    """
    import urllib.parse as _urlparse

    if not isinstance(raw, str):
        return ""
    try:
        _parts = _urlparse.urlsplit(raw.strip())
    except ValueError:
        return ""
    _scheme = _parts.scheme.lower()
    _netloc = _parts.netloc.lower()
    if _scheme not in ("http", "https") or not _netloc:
        return ""
    return "{0}://{1}".format(_scheme, _netloc)


def get_public_base_url(config: dict[str, Any]) -> str:
    """Return the operator-configured public base URL (never from Host header).

    The value is normalized to a bare origin so stray paths/query strings can
    never leak into OAuth callback URLs.
    """
    normalized = _normalize_base_url(
        config.get("public_base_url", _DEFAULT_BASE_URL))
    return normalized or _DEFAULT_BASE_URL


def oauth_callback_url(base_url: str, provider: str) -> str:
    """Build the OAuth callback URL for a provider from the configured base.

    One source of truth: both authorization-URL generation and token-exchange
    validation must call this with the SAME ``base_url``. No duplicated string
    constants across modules. The base is normalized to a bare origin first,
    so trailing slashes / stray paths cannot produce mismatched redirects.
    """
    base = _normalize_base_url(base_url) or _DEFAULT_BASE_URL
    return "{0}/auth/{1}/callback".format(base, provider)
