"""Fyers broker adapter (auth + market-data normalization).

Auth flow (official v3):
  1. Login URL: https://api-t1.fyers.in/login/v3/authorize
     params: client_id (= app id "<id>-<name>"), redirect_uri,
             response_type=code, state (optional but recommended)
  2. Browser callback carries ?auth_code=...&s=ok  (NOTE: Fyers uses
     ``auth_code``, not OAuth-standard ``code``)
  3. Exchange: POST https://api-t1.fyers.in/v3/validate-authcode
     form: grant_type=authorization_code,
           appIdHash = sha256(f"{app_id}:{app_secret}"),
           code, redirect_uri
     -> {access_token, refresh_token, expires_at}
  4. Refresh: POST /v3/validate-authcode with
     grant_type=refresh_token, appIdHash, refresh_token
     (refresh tokens ARE supported by Fyers — unlike Upstox)

Access tokens are short-lived (daily); refresh tokens last ~2 weeks.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from typing import Any

_LOGIN_URL = "https://api-t1.fyers.in/login/v3/authorize"
_VALIDATE_URL = "https://api-t1.fyers.in/v3/validate-authcode"
USER_AGENT = "MarketHub/1.0 (trading-terminal)"


class FyersAuthError(ValueError):
    """Invalid Fyers auth configuration or inputs."""


def app_id_hash(app_id: str, app_secret: str) -> str:
    """sha256 hex of "appId:appSecret" per official spec."""
    return hashlib.sha256(
        f"{app_id}:{app_secret}".encode("utf-8")).hexdigest()


class FyersAuth:
    """Pure login-URL construction + token exchange via injected transport."""

    def __init__(self, *, app_id: str, secret_id: str, redirect_uri: str,
                 transport=None) -> None:
        if not isinstance(app_id, str) or not app_id.strip():
            raise FyersAuthError("app_id must be a non-empty string")
        if not isinstance(secret_id, str) or not secret_id.strip():
            raise FyersAuthError("secret_id must be a non-empty string")
        parts = urllib.parse.urlsplit(redirect_uri.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise FyersAuthError("redirect_uri must be a valid http(s) URL")
        self._app_id = app_id.strip()
        self._secret = secret_id.strip()
        self._redirect_uri = redirect_uri.strip()
        self._transport = transport

    def __repr__(self) -> str:  # never leak secrets
        return ("FyersAuth(app_id=<redacted>, "
                "secret=<redacted>, "
                f"redirect_uri={self._redirect_uri!r})")

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def login_url(self, *, state: str | None = None) -> str:
        params = {"client_id": self._app_id,
                  "redirect_uri": self._redirect_uri,
                  "response_type": "code"}
        if state is not None:
            params["state"] = state
        return f"{_LOGIN_URL}?{urllib.parse.urlencode(params)}"

    async def _post_form(self, form: dict[str, str]) -> dict[str, Any]:
        import asyncio
        if self._transport is None:
            # Default stdlib transport (User-Agent set for Cloudflare).
            def _sync():
                import urllib.request
                req = urllib.request.Request(
                    _VALIDATE_URL,
                    data=urllib.parse.urlencode(form).encode(),
                    method="POST",
                    headers={"Content-Type":
                             "application/x-www-form-urlencoded",
                             "Accept": "application/json",
                             "User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        import json as _json
                        return r.status, _json.loads(r.read())
                except Exception as exc:
                    status = getattr(exc, "code", 0)
                    try:
                        import json as _json
                        body = _json.loads(exc.read())
                    except Exception:
                        body = {}
                    return status or 0, body
            status, payload = await asyncio.to_thread(_sync)
        else:
            status, payload = await self._transport(form)
        if status != 200 or not isinstance(payload, dict) \
                or not payload.get("access_token"):
            raise FyersAuthError(
                "fyers token request rejected (check app id/secret, "
                "auth code single-use, and redirect URI match)")
        return payload

    async def validate_auth_code(self, auth_code: str) -> dict[str, Any]:
        """Exchange a single-use auth_code for token bundle.

        Returns canonical dict: access_token, refresh_token, expires_at.
        """
        if not isinstance(auth_code, str) or not auth_code.strip():
            raise FyersAuthError("auth_code must be a non-empty string")
        payload = await self._post_form({
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash(self._app_id, self._secret),
            "code": auth_code.strip(),
            "redirect_uri": self._redirect_uri,
        })
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token", "")),
            "expires_at": payload.get("expires_at"),
        }

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh an access token (Fyers supports this, ~2-week window)."""
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise FyersAuthError("refresh_token must be a non-empty string")
        payload = await self._post_form({
            "grant_type": "refresh_token",
            "appIdHash": app_id_hash(self._app_id, self._secret),
            "refresh_token": refresh_token.strip(),
        })
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": refresh_token.strip(),
            "expires_at": payload.get("expires_at"),
        }


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string comparison for state validation."""
    return hmac.compare_digest(a.encode(), b.encode())
