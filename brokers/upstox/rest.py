"""
Upstox REST boundary (Phase D2.2) — minimal, stdlib-only.

Owns exactly two provider-purpose calls:

    authorize_market_feed(access_token)  -> authorized WSS URI (D3 input)
    exchange_authorization_code(...)     -> UpstoxCredentials (known expiry)

Design invariants (locked):

    * one HTTP request -> one structured success/error (no retries; D3
      owns retry/backoff/re-authorize decisions)
    * injected synchronous transport, executed via asyncio.to_thread —
      no event-loop blocking, no client lifetime, no session state
    * SEC-HTTP-1: HttpResponse never exposes body/headers through repr
    * SEC-HTTP-2: automatic redirects are DISABLED — any 3xx becomes a
      non-retryable UpstoxRestError (requests carry Bearer tokens and
      client secrets; following a redirect would leak them)
    * raw response bodies NEVER enter exception messages/reprs — only
      parsed safe fields (provider error codes + truncated provider
      message) or generic status-based wording
    * TIME-1: acquisition time comes from the injected clock immediately
      after a successful exchange; expiry is computed once from it
    * secrets (client_secret, authorization code) are method-local and
      never stored on the client

Does NOT own: WebSocket connect/subscribe/reconnect (D3), credentials
storage, env resolution, SourceManager wiring.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from brokers.upstox.auth import UpstoxCredentials, upstox_token_expiry
from brokers.upstox.errors import (
    UpstoxAuthError,
    UpstoxRateLimitError,
    UpstoxRestError,
)

__all__ = ["UpstoxRest"]

_AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
_AUTHORIZE_TIMEOUT_S = 10.0   # project choice — Upstox documents no timeout guidance
_EXCHANGE_TIMEOUT_S = 15.0    # project choice — Upstox documents no timeout guidance
_MAX_PROVIDER_MESSAGE = 200


# ---------------------------------------------------------------------------
# Transport primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Neutral HTTP response. SEC-HTTP-1: body/headers are secret-bearing
    (success bodies carry tokens/URIs), so they are excluded from repr."""

    status: int
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)
    body: bytes = field(repr=False, default=b"")

    def __repr__(self) -> str:
        return (
            f"HttpResponse(status={self.status}, "
            f"headers=<redacted>, body=<redacted>)"
        )


SyncTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float], HttpResponse
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """SEC-HTTP-2: surface 3xx responses instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def _default_sync_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    """stdlib transport. Converts every HTTP status (including surfaced
    3xx and 4xx/5xx via HTTPError) into an HttpResponse; network-level
    failures (URLError/TimeoutError/OSError) propagate to the caller.

    Sends a descriptive User-Agent: api.upstox.com sits behind Cloudflare,
    which bans default library signatures (Python-urllib) with error 1010
    before the request ever reaches Upstox.
    """
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    if not request.has_header("User-agent"):
        request.add_header("User-Agent", "MarketHub/1.0 (trading-terminal)")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            status = exc.code
        finally:
            exc.close()
        return HttpResponse(status=status, headers=response_headers, body=payload)
    return HttpResponse(
        status=response.status,
        headers=dict(response.headers.items()),
        body=response.read(),
    )


# ---------------------------------------------------------------------------
# Safe error parsing / classification
# ---------------------------------------------------------------------------


def _parse_error_body(body: bytes) -> tuple[tuple[str, ...], str]:
    """Extract (codes, provider-message) from a recognized error envelope.

    Anything malformed/non-JSON/HTML yields empty results — arbitrary
    bodies are NEVER exposed (locked policy).
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return (), ""
    if not isinstance(payload, dict):
        return (), ""

    codes: list[str] = []
    raw_codes = payload.get("error_codes")
    if isinstance(raw_codes, list):
        for code in raw_codes:
            if isinstance(code, (str, int)) and not isinstance(code, bool):
                text = str(code).strip()
                if text and text not in codes:
                    codes.append(text)

    message = ""
    errors = payload.get("errors")
    if isinstance(errors, list):
        for entry in errors:
            if not isinstance(entry, dict):
                continue
            code = entry.get("errorCode") or entry.get("error_code")
            if isinstance(code, str) and code.strip() and code not in codes:
                codes.append(code)
            if not message:
                candidate = entry.get("message")
                if isinstance(candidate, str) and candidate.strip():
                    message = candidate.strip()[:_MAX_PROVIDER_MESSAGE]
    return tuple(codes), message


def _parse_retry_after(headers: Mapping[str, str]) -> int | None:
    """Retry-After accepted ONLY as a plain non-negative integer seconds;
    HTTP-date form is deliberately not parsed in D2.2."""
    value = headers.get("Retry-After") or headers.get("retry-after")
    if value is None:
        return None
    text = value.strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def _raise_for_status(response: HttpResponse, operation: str) -> None:
    """Classify a non-200 response into the typed error hierarchy."""
    codes, provider_message = _parse_error_body(response.body)
    message = f"upstox {operation} failed: HTTP {response.status}"
    if codes:
        message += f" [{', '.join(codes)}]"
    if provider_message:
        message += f": {provider_message}"

    status = response.status
    if 300 <= status < 400:
        # SEC-HTTP-2: redirects must never be followed for authenticated
        # requests; surface them as contract violations.
        raise UpstoxRestError(
            f"{message} (unexpected redirect)", status_code=status,
            upstox_codes=codes, retryable=False,
        )
    if status == 401:
        raise UpstoxAuthError(message)
    if status == 429:
        raise UpstoxRateLimitError(
            message, upstox_codes=codes,
            retry_after_seconds=_parse_retry_after(response.headers),
        )
    if status >= 500:
        raise UpstoxRestError(message, status_code=status,
                              upstox_codes=codes, retryable=True)
    raise UpstoxRestError(message, status_code=status,
                          upstox_codes=codes, retryable=False)


def _require_success_json(response: HttpResponse, operation: str) -> dict[str, Any]:
    """HTTP 200 + strict JSON-dict decoding for a success envelope."""
    if response.status != 200:
        _raise_for_status(response, operation)
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpstoxRestError(
            f"upstox {operation} failed: HTTP {response.status} (non-JSON body)",
            status_code=response.status, retryable=False,
        ) from None
    if not isinstance(payload, dict):
        raise UpstoxRestError(
            f"upstox {operation} failed: HTTP {response.status} "
            "(unexpected response shape)",
            status_code=response.status, retryable=False,
        )
    return payload


def _wrap_network_error(operation: str, exc: Exception) -> UpstoxRestError:
    """Fixed generic wording — str(exc) is deliberately NOT used."""
    return UpstoxRestError(
        f"upstox {operation} failed: network error",
        status_code=None, retryable=True,
    )


def _validate_utc_clock(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise UpstoxRestError(
            "injected utc_now returned a non-datetime value", retryable=False
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise UpstoxRestError(
            "injected utc_now returned a naive datetime", retryable=False
        )
    return value


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class UpstoxRest:
    """Minimal stateless Upstox REST boundary (see module docstring).

    Stores only the injected transport/clock — never credentials, tokens,
    client secrets, authorization codes, or authorized URIs.
    """

    def __init__(
        self,
        *,
        sync_transport: SyncTransport | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        self._transport: SyncTransport = sync_transport or _default_sync_transport
        self._utc_now: Callable[[], datetime] = utc_now or (
            lambda: datetime.now(timezone.utc)
        )

    # -- market-data feed authorization --------------------------------------

    async def authorize_market_feed(
        self, credentials: UpstoxCredentials
    ) -> str:
        """Authorize a V3 market-data feed connection.

        Returns the FULL ``authorized_redirect_uri`` (including its
        single-use query material) for immediate consumption by D3.
        Memory-only: never cached, persisted, logged, or sanitized here.
        """
        if not isinstance(credentials, UpstoxCredentials):
            raise UpstoxAuthError(
                "credentials must be an UpstoxCredentials instance"
            )
        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "Accept": "application/json",
        }
        operation = "market-data feed authorization"
        try:
            response = await asyncio.to_thread(
                self._transport, "GET", _AUTHORIZE_URL, headers, None,
                _AUTHORIZE_TIMEOUT_S,
            )
        except OSError as exc:  # URLError/TimeoutError/socket.timeout
            raise _wrap_network_error(operation, exc) from exc

        payload = _require_success_json(response, operation)
        if payload.get("status") != "success":
            codes, provider_message = _parse_error_body(response.body)
            message = (
                f"upstox {operation} failed: HTTP {response.status} "
                "(status != success)"
            )
            if codes:
                message += f" [{', '.join(codes)}]"
            if provider_message:
                message += f": {provider_message}"
            raise UpstoxRestError(message, status_code=response.status,
                                  upstox_codes=codes, retryable=False)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise UpstoxRestError(
                f"upstox {operation} failed: missing data object",
                status_code=response.status, retryable=False,
            )
        uri = data.get("authorized_redirect_uri")
        if not isinstance(uri, str) or not uri.strip():
            raise UpstoxRestError(
                f"upstox {operation} failed: missing authorized_redirect_uri",
                status_code=response.status, retryable=False,
            )
        parts = urllib.parse.urlsplit(uri)
        if parts.scheme != "wss" or not parts.hostname:
            raise UpstoxRestError(
                f"upstox {operation} failed: authorized URI must be a wss URL "
                f"with a hostname (got scheme={parts.scheme!r})",
                status_code=response.status, retryable=False,
            )
        return uri

    # -- authorization-code exchange ------------------------------------------

    async def exchange_authorization_code(
        self,
        *,
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> UpstoxCredentials:
        """Exchange a single-use authorization code for credentials with
        KNOWN expiry (computed from the injected clock at acquisition).

        All inputs are method-local; nothing is retained on the client.
        """
        for label, value in (
            ("code", code),
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("redirect_uri", redirect_uri),
        ):
            if not isinstance(value, str) or not value.strip():
                raise UpstoxAuthError(f"{label} must be a non-empty string")

        form = urllib.parse.urlencode({
            "code": code.strip(),
            "client_id": client_id.strip(),
            "client_secret": client_secret.strip(),
            "redirect_uri": redirect_uri.strip(),
            "grant_type": "authorization_code",
        }).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        operation = "authorization-code exchange"
        try:
            response = await asyncio.to_thread(
                self._transport, "POST", _TOKEN_URL, headers, form,
                _EXCHANGE_TIMEOUT_S,
            )
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc

        if response.status != 200:
            _raise_for_status(response, operation)
        payload = _require_success_json(response, operation)

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise UpstoxRestError(
                f"upstox {operation} failed: missing access_token",
                status_code=response.status, retryable=False,
            )

        acquired_at = _validate_utc_clock(self._utc_now())
        return UpstoxCredentials(
            access_token=access_token.strip(),
            expires_at=upstox_token_expiry(acquired_at),
        )

    # -- generic authenticated JSON request (market-data APIs) ----------------

    async def authenticated_request(
        self,
        *,
        method: str,
        url: str,
        access_token: str,
        json_body: dict[str, Any] | None = None,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Perform one Bearer-authenticated JSON API call.

        ``access_token`` is method-local; nothing is retained. Non-200
        responses raise the standard classified errors (safe wording).
        """
        if not isinstance(access_token, str) or not access_token.strip():
            raise UpstoxAuthError("access_token must be a non-empty string")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token.strip()}",
        }
        body: bytes | None = None
        if json_body is not None:
            import json as _json
            body = _json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        operation = f"upstox api call ({method})"
        try:
            response = await asyncio.to_thread(
                self._transport, method, url, headers, body, timeout)
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc
        if response.status != 200:
            _raise_for_status(response, operation)
        return _require_success_json(response, operation)
