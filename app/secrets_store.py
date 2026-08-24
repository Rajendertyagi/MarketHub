"""Local backend credential store for long-lived application secrets.

Stores Upstox APP credentials (API key + API secret) under the project's
data directory, separated from normal config.json:

    <PROJECT_ROOT>/data/secrets/upstox_app_credentials.json

Design rules:
  * Backend-side only — never sent to the browser after save, never
    returned by any API, never logged, never serialized into reprs.
  * Atomic writes (temp file + os.replace) so a crash cannot leave a
    half-written secret file.
  * Best-effort restrictive permissions (0o600 where the OS supports it;
    Windows ACL hardening is intentionally NOT claimed).
  * Windows Credential Manager was evaluated but rejected: integrating it
    cleanly requires a third-party dependency (keyring) inside an isolated
    interpreter — more complexity than a local restricted file for a
    single-user localhost application.

This store holds ONLY long-lived app credentials. The daily access token
remains runtime-memory-only by design (see brokers.upstox.auth).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from app.paths import DATA_ROOT, ensure_dir

logger = logging.getLogger("event_server")

_SECRETS_DIR_NAME = "secrets"
_UPSTOX_FILE_NAME = "upstox_app_credentials.json"


def _store_path(base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir is not None else DATA_ROOT
    return root / _SECRETS_DIR_NAME / _UPSTOX_FILE_NAME


def load_upstox_app_credentials(
    base_dir: Path | None = None,
) -> dict[str, str] | None:
    """Load saved app credentials, or None if absent/corrupt/invalid.

    Corrupt files are treated as not-configured (fail-safe) and left in
    place — saving new credentials overwrites them atomically.
    """
    path = _store_path(base_dir)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("credential store unreadable (treated as unset): %s",
                       type(exc).__name__)
        return None
    if not isinstance(data, dict):
        return None
    api_key = data.get("api_key")
    api_secret = data.get("api_secret")
    if not isinstance(api_key, str) or not api_key.strip():
        return None
    if not isinstance(api_secret, str) or not api_secret.strip():
        return None
    return {"api_key": api_key.strip(), "api_secret": api_secret.strip()}


def save_upstox_app_credentials(
    api_key: str,
    api_secret: str,
    base_dir: Path | None = None,
) -> None:
    """Persist app credentials atomically with restrictive permissions."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")
    if not isinstance(api_secret, str) or not api_secret.strip():
        raise ValueError("api_secret must be a non-empty string")

    path = _store_path(base_dir)
    ensure_dir(path.parent)

    payload = json.dumps(
        {"api_key": api_key.strip(), "api_secret": api_secret.strip()},
        indent=2,
    ).encode("utf-8")

    # Atomic replace: write sibling temp file, then swap into place.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".upstox_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(payload)
        try:
            os.chmod(tmp_name, 0o600)  # best-effort; POSIX meaningful
        except OSError:
            pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    logger.info("upstox app credentials saved (%d bytes)", len(payload))


def clear_upstox_app_credentials(base_dir: Path | None = None) -> bool:
    """Remove stored credentials. Returns True if a file was removed."""
    path = _store_path(base_dir)
    if path.is_file():
        path.unlink()
        logger.info("upstox app credentials cleared")
        return True
    return False


def redacted_status(creds: dict[str, str] | None) -> dict[str, Any]:
    """Status shape safe for API responses — booleans only, never values."""
    return {
        "api_key_configured": bool(creds and creds.get("api_key")),
        "api_secret_configured": bool(creds and creds.get("api_secret")),
    }
