"""Isolated broker authentication/session subsystem.

One application-level owner for broker session lifecycle:

  AuthService -> UpstoxAuthService + FyersAuthService -> AuthStorage
                (CredentialStore, encrypted, durable source of truth)

Market data / feed implementation lives elsewhere (sources/, brokers/*/feed).
Feed failures must never invalidate authentication; only genuine auth
conditions (missing credentials, genuine expiry, provider 401/403) do.

Production model for "waiting for login":

  token=None + login_required=true + auth_state=missing/expired/rejected

Fake placeholder strings (e.g. "PENDING-OAUTH-LOGIN") are NEVER production
credentials. Tests may use fakes only in isolated temp stores.
"""

from app.auth.models import AuthState, BrokerSession, parse_expiry_iso
from app.auth.service import AuthService
from app.auth.storage import (
    FAKE_TOKEN_MARKERS,
    is_fake_token,
    is_production_db_path,
    is_production_store,
    resolve_store_db_path,
)
from app.auth.fyers import FyersAuthService
from app.auth.upstox import UpstoxAuthService

__all__ = [
    "AuthService",
    "AuthState",
    "BrokerSession",
    "FyersAuthService",
    "UpstoxAuthService",
    "FAKE_TOKEN_MARKERS",
    "is_fake_token",
    "is_production_db_path",
    "is_production_store",
    "resolve_store_db_path",
    "parse_expiry_iso",
]
