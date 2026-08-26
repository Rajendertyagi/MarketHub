"""Fyers broker adapter (auth + market-data normalization).

Auth flow (official v3 — per Fyers API docs / FyersDev reference):
  1. Login URL: GET https://api-t1.fyers.in/api/v3/generate-authcode
      params: client_id (= app id "<id>-<name>"), redirect_uri,
              response_type=code, state (optional but recommended)
  2. Browser callback carries ?auth_code=...&s=ok  (NOTE: Fyers uses
      ``auth_code``, not OAuth-standard ``code``)
  3. Exchange: POST https://api-t1.fyers.in/api/v3/validate-authcode
      JSON body: {"grant_type": "authorization_code",
                  "appIdHash": sha256(f"{app_id}:{app_secret}"),
                  "code": <auth_code>}
      -> {access_token, refresh_token}
  4. Refresh: POST https://api-t1.fyers.in/api/v3/validate-refresh-token
      JSON body: {"grant_type": "refresh_token", "appIdHash": ...,
                  "refresh_token": ..., "pin": <user PIN>}
      Returns a new access_token only. NOTE: the official docs require the
      account PIN for refresh and flag the flow as possibly discontinued;
      MarketHub treats refresh as best-effort and falls back to daily login.

Access tokens are short-lived (daily); refresh tokens last ~15 days.
"""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from typing import Any

_LOGIN_URL = "https://api-t1.fyers.in/api/v3/generate-authcode"
_VALIDATE_URL = "https://api-t1.fyers.in/api/v3/validate-authcode"
_REFRESH_URL = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
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

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        if self._transport is None:
            # Default stdlib transport (User-Agent set for Cloudflare).
            import json as _json

            def _sync():
                import urllib.request
                req = urllib.request.Request(
                    url,
                    data=_json.dumps(body).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json",
                             "User-Agent": USER_AGENT})
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        return r.status, _json.loads(r.read())
                except Exception as exc:
                    status = getattr(exc, "code", 0)
                    try:
                        body_txt = exc.read()
                        payload = _json.loads(body_txt)
                    except Exception:
                        payload = {}
                    return status or 0, payload
            status, payload = await asyncio.to_thread(_sync)
        else:
            status, payload = await self._transport(url, body)
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
        payload = await self._post_json(_VALIDATE_URL, {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash(self._app_id, self._secret),
            "code": auth_code.strip(),
        })
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": str(payload.get("refresh_token", "")),
            "expires_at": payload.get("expires_at"),
        }

    async def refresh_access_token(self, refresh_token: str,
                                   pin: str | None = None) -> dict[str, Any]:
        """Refresh an access token using a stored refresh token.

        The official refresh endpoint requires the account PIN. When the
        operator has saved their PIN (encrypted in the credential store),
        it is included here and session restore works across restarts.
        Without a PIN the call fails and the feed falls back to daily
        login (safe, documented behavior).
        """
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise FyersAuthError("refresh_token must be a non-empty string")
        body: dict[str, Any] = {
            "grant_type": "refresh_token",
            "appIdHash": app_id_hash(self._app_id, self._secret),
            "refresh_token": refresh_token.strip(),
        }
        if pin:
            body["pin"] = str(pin).strip()
        payload = await self._post_json(_REFRESH_URL, body)
        return {
            "access_token": str(payload["access_token"]),
            "refresh_token": refresh_token.strip(),
            "expires_at": payload.get("expires_at"),
        }


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string comparison for state validation."""
    return hmac.compare_digest(a.encode(), b.encode())
