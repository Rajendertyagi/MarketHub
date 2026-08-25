"""
HTTP API route builders for MarketHub.

Owns Starlette Route objects only. Routes receive every dependency
(brokers, services) as constructor/argument injection from the application
composition root — this package never creates services, never serializes
domain models itself (delegates to market.serialization), and never reads
app.state.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

import logging
logger = logging.getLogger("event_server")

__all__ = ["build_market_routes", "build_auth_routes", "build_settings_routes"]


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def build_market_routes(
    market_broker: Any,
    market_service: Any = None,
    source_status_fn: Callable[[], list[dict]] | None = None,
) -> list[Route]:
    """Build market API routes around injected dependencies."""

    # -- SSE stream ----------------------------------------------------------

    async def _market_stream(request: Request) -> Response:  # noqa: ARG001
        async def _generate():
            async with market_broker.subscribe() as lines:
                async for line in lines:
                    yield line

        return EventSourceResponse(
            _generate(), media_type="text/event-stream", ping=15,
        )

    # -- read-only market data -------------------------------------------------

    async def _market_quotes(request: Request) -> Response:  # noqa: ARG001
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        quotes = await market_service.quotes()
        from market.serialization import quote_to_dict
        return _json({"quotes": [quote_to_dict(q) for q in quotes]})

    async def _market_depths(request: Request) -> Response:  # noqa: ARG001
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        depths = await market_service.depths()
        from market.serialization import depth_to_dict
        return _json({"depths": [depth_to_dict(d) for d in depths]})

    async def _market_quote(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        exchange = request.path_params.get("exchange", "")
        token = request.path_params.get("instrument_token", "")
        q = await market_service.get_quote(exchange, token)
        if q is None:
            return _json({"error": "not found"}, 404)
        from market.serialization import quote_to_dict
        return _json(quote_to_dict(q))

    async def _market_depth(request: Request) -> Response:
        if market_service is None:
            return _json({"error": "market service unavailable"}, 503)
        exchange = request.path_params.get("exchange", "")
        token = request.path_params.get("instrument_token", "")
        d = await market_service.get_depth(exchange, token)
        if d is None:
            return _json({"error": "not found"}, 404)
        from market.serialization import depth_to_dict
        return _json(depth_to_dict(d))

    # -- source / feed status ---------------------------------------------------

    async def _source_status(request: Request) -> Response:  # noqa: ARG001
        sources: list[dict] = []
        if source_status_fn is not None:
            sources = source_status_fn()
        return _json({"sources": sources})

    return [
        Route("/api/market/stream", endpoint=_market_stream, methods=["GET"]),
        Route("/api/market/quotes", endpoint=_market_quotes, methods=["GET"]),
        Route("/api/market/depths", endpoint=_market_depths, methods=["GET"]),
        Route("/api/market/quote/{exchange}/{instrument_token}",
              endpoint=_market_quote, methods=["GET"]),
        Route("/api/market/depth/{exchange}/{instrument_token}",
              endpoint=_market_depth, methods=["GET"]),
        Route("/api/sources/status", endpoint=_source_status, methods=["GET"]),
    ]


def build_settings_routes(
    oauth_ref: dict[str, Any],
    cred_store: Any = None,
) -> list[Route]:
    """Build settings routes for persistent Upstox app-credential management.

    ``oauth_ref`` is the SAME mutable dict given to build_auth_routes —
    saving here updates OAuth availability at runtime (no restart).
    ``cred_store`` is an app.secrets_store.CredentialStore (encrypted
    SQLite-backed); injected so tests can use isolated instances.

    Responses NEVER contain credential values, ciphertext, or master key.
    """
    if cred_store is None:
        from app import secrets_store as _ss
        cred_store = _ss.build_default_store()

    async def _settings_status(request: Request) -> Response:  # noqa: ARG001
        status = cred_store.status()
        status["oauth_available"] = bool(
            isinstance(oauth_ref.get("api_key"), str)
            and oauth_ref["api_key"].strip()
            and isinstance(oauth_ref.get("api_secret"), str)
            and oauth_ref["api_secret"].strip()
        )
        return _json(status)

    async def _save_credentials(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        api_key = body.get("api_key")
        api_secret = body.get("api_secret")
        for label, value in (("api_key", api_key), ("api_secret", api_secret)):
            if not isinstance(value, str) or not value.strip():
                return _json({"error": f"{label} is required"}, 400)
            if len(value) > 512:
                return _json({"error": f"{label} too long"}, 400)
        try:
            cred_store.save_upstox_app_credentials(api_key.strip(),
                                                   api_secret.strip())
        except Exception:
            # Safe failure: no crypto/DB internals leaked to the client.
            return _json({"error": "failed to save credentials"}, 500)

        # Runtime reload: same dict object the auth routes hold, updated in
        # place so Login-with-Upstox becomes available without restart.
        oauth_ref["api_key"] = api_key.strip()
        oauth_ref["api_secret"] = api_secret.strip()
        return _json({"configured": True})

    async def _delete_credentials(request: Request) -> Response:  # noqa: ARG001
        try:
            removed = cred_store.delete_upstox_app_credentials()
        except Exception:
            return _json({"error": "failed to delete credentials"}, 500)
        oauth_ref["api_key"] = ""
        oauth_ref["api_secret"] = ""
        return _json({"configured": False, "removed": bool(removed)})

    return [
        Route("/api/settings/upstox", endpoint=_settings_status,
              methods=["GET"]),
        Route("/api/settings/upstox", endpoint=_save_credentials,
              methods=["POST"]),
        Route("/api/settings/upstox", endpoint=_delete_credentials,
              methods=["DELETE"]),
    ]


def build_auth_routes(
    feed_ref: dict[str, Any],
    restart_fn: Callable[[], Any] | None = None,
    oauth: dict[str, Any] | None = None,
    rest: Any = None,
) -> list[Route]:
    """Build auth routes for runtime token management.

    ``feed_ref`` is a mutable dict holding {"feed": UpstoxFeed | None}.
    ``restart_fn`` is an async callable that stops and restarts the source.
    ``oauth`` is an optional dict {api_key, api_secret, redirect_uri} enabling
    the OAuth login/callback flow. ``rest`` is an UpstoxRest instance used for
    the code exchange (stateless transport; stores no secrets).

    OAuth state lives ONLY in this closure: memory-only, single-use,
    10-minute TTL. Never persisted, never logged, never returned.
    """
    import hmac
    import secrets
    import time

    _STATE_TTL_S = 600  # 10 minutes
    _pending_states: dict[str, float] = {}  # state -> monotonic expiry

    def _oauth_ready() -> bool:
        if not isinstance(oauth, dict):
            return False
        return all(
            isinstance(oauth.get(k), str) and oauth.get(k).strip()
            for k in ("api_key", "api_secret", "redirect_uri")
        )

    async def _auth_status(request: Request) -> Response:  # noqa: ARG001
        feed = feed_ref.get("feed")
        base = {
            "configured": feed is not None,
            "oauth_available": _oauth_ready(),
        }
        if feed is None:
            base.update({"source": "upstox", "auth_mode": "none",
                         "token_configured": False})
            return _json(base)
        creds = feed._credentials
        status = creds.status()
        base.update({
            "source": feed.name,
            "auth_mode": status.get("auth_mode", "unknown"),
            "token_configured": status.get("token_present", False),
            "expiry_known": status.get("expiry_known", False),
            "expires_at": status.get("expires_at"),
            "expired": status.get("expired"),
            "state": feed.status().get("state", "unknown"),
        })
        return _json(base)

    async def _oauth_login(request: Request) -> Response:  # noqa: ARG001
        if not _oauth_ready():
            return _json({"error": "oauth not configured"}, 503)
        from brokers.upstox.auth import UpstoxOAuth

        # Prune expired states (memory hygiene).
        now = time.monotonic()
        expired = [s for s, exp in _pending_states.items() if exp <= now]
        for s in expired:
            del _pending_states[s]

        state = secrets.token_urlsafe(32)
        _pending_states[state] = now + _STATE_TTL_S
        try:
            url = UpstoxOAuth(
                api_key=oauth["api_key"],
                redirect_uri=oauth["redirect_uri"],
            ).authorization_url(state=state)
        except Exception:
            _pending_states.pop(state, None)
            return _json({"error": "failed to build authorization URL"}, 500)
        from starlette.responses import RedirectResponse
        return RedirectResponse(url, status_code=302)

    async def _oauth_callback(request: Request) -> Response:
        from starlette.responses import RedirectResponse

        def _fail(reason: str) -> Response:
            # Reason codes only — never provider bodies, secrets, or URIs.
            return RedirectResponse(
                    f"/ui/?auth=failed&reason={reason}#/settings",
                    status_code=302)

        state = request.query_params.get("state")
        code = request.query_params.get("code")
        if not state or not code or not _oauth_ready():
            return _fail("retry")

        # Single-use + TTL + constant-time match: consume BEFORE exchange so a
        # replayed callback can never trigger a second exchange.
        matched: str | None = None
        expiry = -1.0
        for pending, exp in _pending_states.items():
            if hmac.compare_digest(pending, state):
                matched, expiry = pending, exp
                break
        if matched is None:
            return _fail("retry")   # invalid or replayed state
        if expiry < time.monotonic():
            del _pending_states[matched]
            return _fail("expired")  # sat on the login page too long
        del _pending_states[matched]

        feed = feed_ref.get("feed")
        if feed is None or rest is None:
            return _fail("error")

        try:
            creds = await rest.exchange_authorization_code(
                code=code.strip(),
                client_id=oauth["api_key"].strip(),
                client_secret=oauth["api_secret"].strip(),
                redirect_uri=oauth["redirect_uri"].strip(),
            )
        except Exception as exc:
            # Classify safely: 4xx from Upstox almost always means bad
            # credentials or a redirect-URL mismatch in the developer app.
            status = getattr(exc, "status_code", None)
            if isinstance(status, int) and 400 <= status < 500:
                return _fail("rejected")
            return _fail("network")

        try:
            feed.update_credentials(creds)
            if restart_fn is not None:
                await restart_fn()
        except Exception:
            logger.exception("oauth callback: feed restart failed")
            return _fail("restart")

        return RedirectResponse("/ui/?auth=ok#/settings", status_code=302)

    async def _submit_token(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        token = body.get("access_token", "")
        if not isinstance(token, str) or not token.strip():
            return _json({"error": "access_token is required"}, 400)
        if len(token) > 4096:
            return _json({"error": "access_token too long"}, 400)

        from brokers.upstox.auth import UpstoxCredentials
        try:
            creds = UpstoxCredentials(access_token=token.strip())
        except Exception as exc:
            return _json({"error": f"invalid credentials: {exc}"}, 400)

        feed = feed_ref.get("feed")
        if feed is None:
            return _json({"error": "no upstox feed registered"}, 503)

        feed.update_credentials(creds)

        if restart_fn is not None:
            try:
                await restart_fn()
            except Exception:
                pass  # restart failure logged elsewhere; don't leak

        return _json({"configured": True})

    return [
        Route("/api/auth/upstox/status", endpoint=_auth_status, methods=["GET"]),
        Route("/api/auth/upstox/login", endpoint=_oauth_login, methods=["GET"]),
        Route("/auth/upstox/callback", endpoint=_oauth_callback, methods=["GET"]),
        Route("/api/auth/upstox/token", endpoint=_submit_token, methods=["POST"]),
    ]
