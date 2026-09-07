"""Auth/session models: no secrets, no I/O, no broker imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class AuthState:
    """Explicit auth lifecycle states (never inferred from feed state)."""

    MISSING = "missing"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"
    REJECTED = "rejected"
    FORGOTTEN = "forgotten"
    RESTORED = "restored"


@dataclass
class BrokerSession:
    """Durable session projection for one broker."""

    provider: str
    token_present: bool = False
    expires_at_iso: str | None = None
    expired: bool | None = None
    expiry_known: bool = False
    auth_state: str = AuthState.MISSING
    last_auth_status: str | None = None
    session_restored: bool = False

    @property
    def login_required(self) -> bool:
        if not self.token_present:
            return True
        if self.expired is True:
            return True
        if self.auth_state in (AuthState.MISSING, AuthState.EXPIRED,
                               AuthState.REJECTED, AuthState.FORGOTTEN):
            return True
        return False

    def to_status(self) -> dict[str, Any]:
        return {
            "token_present": self.token_present,
            "expires_at": self.expires_at_iso,
            "expired": self.expired,
            "expiry_known": self.expiry_known,
            "auth_state": self.auth_state,
            "last_auth_status": self.last_auth_status,
            "session_restored": self.session_restored,
            "login_required": self.login_required,
        }


def parse_expiry_iso(value: Any) -> datetime | None:
    """Parse an ISO expiry string, timezone-safe (naive -> UTC).

    Returns None when absent/unparseable (caller treats as unknown, never
    as valid).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_expired(expiry: datetime | None, now: datetime | None = None) -> bool | None:
    """True/False when expiry known, None when unknown."""
    if expiry is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= expiry


@dataclass
class RestoreOutcome:
    """Per-broker startup restore result (forensics, no secrets)."""

    provider: str
    restored: bool = False
    reason: str = "not_attempted"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "restored": self.restored,
                "reason": self.reason, **self.extra}
