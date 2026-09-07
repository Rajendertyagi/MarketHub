"""Durable auth storage + production/test isolation guards.

The encrypted CredentialStore remains the durable source of truth.
This module wraps it with:

  * timezone-safe expiry helpers (via models.parse_expiry_iso)
  * a fake-token guard: fake/test placeholder tokens must NEVER be
    written into the real user credential store (data/events.db).

Production model: token=None + explicit auth_state. No placeholders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # pragma: no cover - import-time path resolution only
    from app.paths import DATA_ROOT as _DATA_ROOT
except Exception:  # pragma: no cover
    _DATA_ROOT = None


def production_db_path() -> Path | None:
    if _DATA_ROOT is None:
        return None
    try:
        return (Path(_DATA_ROOT) / "events.db").resolve()
    except Exception:
        return Path(str(_DATA_ROOT)) / "events.db"


FAKE_TOKEN_MARKERS = frozenset({
    "PENDING-OAUTH-LOGIN",
    "PENDING",
    "FAKE",
    "DUMMY",
    "MOCK",
    "PLACEHOLDER",
})


def is_fake_token(token: Any) -> bool:
    """True for placeholder/fake tokens that must never reach production."""
    if not isinstance(token, str) or not token.strip():
        return False
    t = token.strip()
    if t in FAKE_TOKEN_MARKERS:
        return True
    upper = t.upper()
    if upper.startswith("PENDING"):
        return True
    for prefix in ("FAKE-", "DUMMY-", "MOCK-", "TEST-", "PLACEHOLDER"):
        if upper.startswith(prefix):
            return True
    if upper in ("GOOD-TOKEN", "REAL-BUT-OLD", "LOOKED-VALID", "FRESH-TOKEN",
                 "RT-TOK"):
        # Canonical test-fixture tokens used across test_auth_startup.py.
        return True
    return False


def resolve_store_db_path(store: Any) -> Path | None:
    """Best-effort: resolve the SQLite DB path behind a CredentialStore."""
    try:
        inner = getattr(store, "_store", None)
        path = getattr(inner, "_db_path", None) or getattr(inner, "db_path", None)
        if path:
            return Path(str(path)).resolve()
    except Exception:
        return None
    return None


def is_production_db_path(path: Any) -> bool:
    prod = production_db_path()
    if prod is None or path is None:
        return False
    try:
        return Path(str(path)).resolve() == prod
    except Exception:
        return False


def is_production_store(store: Any) -> bool:
    return is_production_db_path(resolve_store_db_path(store))


def guard_fake_token_write(store: Any, token: Any, *, provider: str = "") -> None:
    """Raise if a fake/test token would be written to the production store.

    Called by AuthService persistence paths BEFORE any write. Isolated temp
    stores (every test) are unaffected.
    """
    if is_fake_token(token) and is_production_store(store):
        raise ValueError(
            f"refusing to write fake {provider or 'broker'} token "
            f"{str(token)[:24]!r} into the production credential store "
            f"({resolve_store_db_path(store)})"
        )


class AuthStorage:
    """Thin durable facade over CredentialStore (no feed/runtime logic)."""

    def __init__(self, cred_store: Any) -> None:
        self._store = cred_store

    @property
    def raw(self) -> Any:
        return self._store

    # -- Upstox -----------------------------------------------------------
    def load_upstox_session(self) -> dict | None:
        try:
            return self._store.load_upstox_session_token()
        except Exception:
            return None

    def save_upstox_session(self, token: str, expires_at_iso: str | None,
                            issued_at_iso: str | None = None) -> None:
        guard_fake_token_write(self._store, token, provider="upstox")
        self._store.save_upstox_session_token(
            token=token, expires_at_iso=expires_at_iso,
            issued_at_iso=issued_at_iso)

    def clear_upstox_session(self) -> None:
        try:
            self._store.clear_upstox_session_token()
        except Exception:
            pass

    # -- Fyers ------------------------------------------------------------
    def load_fyers_access(self) -> dict | None:
        try:
            return self._store.load_fyers_access_token()
        except Exception:
            return None

    def save_fyers_access(self, token: str, expires_at_iso: str | None) -> None:
        guard_fake_token_write(self._store, token, provider="fyers")
        self._store.save_fyers_access_token(token, expires_at_iso=expires_at_iso)

    # -- Upstox PIN-mode auth code (single-use, encrypted) --------------------
    def stage_auth_code(self, code: str) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("auth code must be a non-empty string")
        self._store.save_upstox_auth_code(code.strip())

    def consume_auth_code(self) -> str | None:
        try:
            code = self._store.load_upstox_auth_code()
        except Exception:
            code = None
        try:
            self._store.clear_upstox_auth_code()
        except Exception:
            pass
        return code if isinstance(code, str) and code.strip() else None

    def has_pending_auth_code(self) -> bool:
        try:
            return bool(self._store.load_upstox_auth_code())
        except Exception:
            return False

    # -- shared -----------------------------------------------------------
    def save_status(self, provider: str, status: str) -> None:
        try:
            self._store.save_last_auth_status(provider, status)
        except Exception:
            pass

    def load_status(self, provider: str) -> str | None:
        try:
            return self._store.load_last_auth_status(provider)
        except Exception:
            return None
