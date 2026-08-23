"""
Upstox authentication foundation (Phase D2.1) — pure, HTTP-free.

Owns:
  * IST timezone constant and the access-token expiry rule
  * frozen credential container with redacted repr/status
  * OAuth authorization-URL construction (pure string math)

Does NOT own: HTTP, token exchange, environment-variable resolution,
secret storage, WebSocket, retries.

Token-expiry rule (locked D2.1 semantics):
    Upstox standard access tokens expire at the FIRST 03:30 IST boundary
    STRICTLY AFTER the moment the token was ACQUIRED. Expiry is therefore
    computed once from the acquisition/issuance time — never re-derived
    from the current clock. A credential whose acquisition time is unknown
    (externally supplied token) has UNKNOWN expiry: status reports
    ``expiry_known=False`` / ``expired=None`` and never claims validity.

Secret policy: access tokens and API keys never appear in reprs, status
dicts, or exception messages. URLs are represented as scheme://host/path
only (query strings may carry auth material).
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from brokers.upstox.errors import UpstoxAuthError

__all__ = [
    "IST",
    "upstox_token_expiry",
    "UpstoxCredentials",
    "UpstoxOAuth",
]

# India has no DST, so a fixed offset is semantically exact for IST and
# avoids tzdata availability concerns on portable interpreters.
IST = timezone(timedelta(hours=5, minutes=30))
_UTC = timezone.utc

_REDACTED = "<redacted>"
_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog"


def _require_aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise UpstoxAuthError(
            f"{name} must be a datetime; got {type(value).__name__}"
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise UpstoxAuthError(
            f"{name} must be timezone-aware; naive datetimes are rejected"
        )


def upstox_token_expiry(issued_at: datetime) -> datetime:
    """Return the first 03:30 IST boundary STRICTLY AFTER ``issued_at``.

    Rule 1: expiry is computed from ACQUISITION/ISSUANCE time, once.
    Rule 2: naive datetimes are rejected explicitly.
    The result is canonicalized to UTC (timezone-aware) for comparison
    and serialization.
    """
    _require_aware(issued_at, "issued_at")
    ist_issued = issued_at.astimezone(IST)
    boundary = ist_issued.replace(hour=3, minute=30, second=0, microsecond=0)
    if boundary <= ist_issued:
        boundary += timedelta(days=1)
    return boundary.astimezone(_UTC)


def _safe_url(url: str) -> str:
    """scheme://host[:port]/path form.

    Query/fragment are dropped (they may carry authentication material)
    and userinfo is stripped from the authority component, so credentials
    embedded in a URL can never surface through this representation.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass(frozen=True, slots=True)
class UpstoxCredentials:
    """Runtime credential container for an externally supplied token.

    ``access_token`` is required and whitespace-stripped. ``expires_at``
    is OPTIONAL: ``None`` means the acquisition time of an externally
    supplied token is unknown, so expiry is UNKNOWN — it is never inferred
    from the current clock.

    Secret safety: the repr never contains the token; ``status()`` exposes
    presence/expiry facts only and never claims validity.
    """

    access_token: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.access_token, str):
            raise UpstoxAuthError("access_token must be a string")
        stripped = self.access_token.strip()
        if not stripped:
            raise UpstoxAuthError("access_token must be a non-empty string")
        object.__setattr__(self, "access_token", stripped)
        if self.expires_at is not None:
            _require_aware(self.expires_at, "expires_at")

    def __repr__(self) -> str:
        expires = (
            self.expires_at.astimezone(_UTC).isoformat()
            if self.expires_at is not None
            else None
        )
        return (
            f"UpstoxCredentials(access_token={_REDACTED}, "
            f"expires_at={expires!r})"
        )

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        """Redacted credential status.

        ``now`` defaults to the current UTC time; if supplied it must be
        timezone-aware. When expiry is unknown: ``expiry_known=False``,
        ``expires_at=None``, ``expired=None`` — validity is NEVER claimed.
        """
        if now is None:
            now = datetime.now(_UTC)
        else:
            _require_aware(now, "now")
        if self.expires_at is None:
            return {
                "auth_mode": "external_token",
                "token_present": True,
                "expiry_known": False,
                "expires_at": None,
                "expired": None,
            }
        return {
            "auth_mode": "external_token",
            "token_present": True,
            "expiry_known": True,
            "expires_at": self.expires_at.astimezone(_UTC).isoformat(),
            "expired": now >= self.expires_at,
        }


class UpstoxOAuth:
    """Pure OAuth authorization-URL construction (no HTTP).

    Holds only non-secret-by-necessity values needed for URL math;
    ``api_key`` is treated conservatively as sensitive in the repr.
    ``api_secret`` is deliberately NOT part of this type — only the
    code-exchange needs it, and that lives behind the REST boundary (D2.2).
    """

    def __init__(self, *, api_key: str, redirect_uri: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise UpstoxAuthError("api_key must be a non-empty string")
        if not isinstance(redirect_uri, str) or not redirect_uri.strip():
            raise UpstoxAuthError("redirect_uri must be a non-empty string")
        parts = urllib.parse.urlsplit(redirect_uri.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise UpstoxAuthError(
                "redirect_uri must be a valid http(s) URL"
            )
        self._api_key = api_key.strip()
        self._redirect_uri = redirect_uri.strip()

    def __repr__(self) -> str:
        return (
            f"UpstoxOAuth(api_key={_REDACTED}, "
            f"redirect_uri={_safe_url(self._redirect_uri)!r})"
        )

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def authorization_url(
        self, *, state: str | None = None, scope: str | None = None
    ) -> str:
        """Build the official authorization-dialog URL.

        Required params: client_id, redirect_uri, response_type=code.
        Optional: state, scope (each validated non-empty when supplied).
        Query built with stdlib encoding — no manual concatenation.
        """
        params: dict[str, str] = {
            "client_id": self._api_key,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
        }
        if state is not None:
            if not isinstance(state, str) or not state.strip():
                raise UpstoxAuthError("state must be a non-empty string when supplied")
            params["state"] = state
        if scope is not None:
            if not isinstance(scope, str) or not scope.strip():
                raise UpstoxAuthError("scope must be a non-empty string when supplied")
            params["scope"] = scope
        query = urllib.parse.urlencode(params)
        return f"{_DIALOG_URL}?{query}"
