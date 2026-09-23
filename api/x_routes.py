"""REST routes for X/Twitter settings, poll config, status, and tweet cache.

Provides (thin adapters over XTwitterService — parse → service → project):
  Credentials (booleans only, never secret material):
    GET    /api/settings/x  — cookie configured? + auth state
    POST   /api/settings/x  — save {auth_token, ct0} (encrypted, provider "x")
    DELETE /api/settings/x  — delete stored cookies
  Poll config (non-secret, x_config table):
    GET    /api/x/config    — poll settings
    POST   /api/x/config    — update poll settings (floor/clamp validated)
  Health:
    GET    /api/x/status    — auth state + per-cycle health
    POST   /api/x/test      — one manual connection probe (redacted result)
  Cache:
    GET    /api/x/tweets?limit&handle — read cached tweets (offline-safe)

X rules are configured in the EXISTING Alerts UI (source=twitter rows) —
no duplicate rule UI here. No posting. No dashboard.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

logger = logging.getLogger(__name__)


def build_x_routes(x_service: Any) -> list[Route]:
    """Build the X/Twitter settings/config/status/cache routes."""

    # ── Credentials ──────────────────────────────────────────────────────────

    async def _cred_status(request: Request) -> Response:
        try:
            configured = bool(x_service.has_credentials())
            auth = x_service.auth_state()
            return Response(
                content=json.dumps({
                    "status": "ok",
                    "configured": configured,
                    "auth_state": auth.get("state"),
                    "auth_reason": auth.get("reason"),
                    "auth_at": auth.get("at"),
                }),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("x cred status failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _cred_save(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return Response(
                content=json.dumps({"status": "error",
                                    "message": "invalid JSON"}),
                media_type="application/json",
                status_code=400,
            )
        token = ((body or {}).get("auth_token") or "").strip()
        ct0 = ((body or {}).get("ct0") or "").strip()
        if not token or not ct0:
            return Response(
                content=json.dumps({"status": "error",
                                    "message": "auth_token and ct0 are required"}),
                media_type="application/json",
                status_code=400,
            )
        try:
            x_service.save_credentials(token, ct0)
            return Response(
                content=json.dumps({"status": "ok", "configured": True}),
                media_type="application/json",
            )
        except (ValueError, RuntimeError) as exc:
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=400,
            )
        except Exception as exc:
            logger.warning("x cred save failed: %s", type(exc).__name__)
            return Response(
                content=json.dumps({"status": "error",
                                    "message": "save failed"}),
                media_type="application/json",
                status_code=500,
            )

    async def _cred_delete(request: Request) -> Response:
        try:
            removed = bool(x_service.delete_credentials())
            return Response(
                content=json.dumps({"status": "ok", "configured": False,
                                    "removed": removed}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("x cred delete failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    # ── Poll config ──────────────────────────────────────────────────────────

    async def _config_get(request: Request) -> Response:
        try:
            cfg = x_service.get_config()
            return Response(
                content=json.dumps({"status": "ok", "config": cfg}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("x config get failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _config_set(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return Response(
                content=json.dumps({"status": "error",
                                    "message": "invalid JSON"}),
                media_type="application/json",
                status_code=400,
            )
        if not isinstance(body, dict):
            return Response(
                content=json.dumps({"status": "error",
                                    "message": "config must be an object"}),
                media_type="application/json",
                status_code=400,
            )
        try:
            cfg = x_service.configure(body)
            return Response(
                content=json.dumps({"status": "ok", "config": cfg}),
                media_type="application/json",
            )
        except ValueError as exc:
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=400,
            )
        except Exception as exc:
            logger.warning("x config set failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    # ── Status ───────────────────────────────────────────────────────────────

    async def _status(request: Request) -> Response:
        try:
            status = x_service.get_status()
            return Response(
                content=json.dumps({"status": "ok", **status}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("x status failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    # ── Connection test ────────────────────────────────────────────────────

    async def _test(request: Request) -> Response:
        """One manual session-auth probe; redacted result only.

        Side-effect free (no persistence/cursor/publish/backoff); works
        with polling disabled. Never returns cookies or raw CLI output.
        """
        try:
            result = await x_service.test_connection()
            return Response(
                content=json.dumps({"status": result.get("status", "ok"),
                                    "state": result.get("state"),
                                    "reason": result.get("reason"),
                                    "checked_at": result.get("checked_at")}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("x connection test failed: %s", type(exc).__name__)
            return Response(
                content=json.dumps({"status": "error",
                                    "message": "test failed"}),
                media_type="application/json",
                status_code=500,
            )

    # ── Tweet cache ──────────────────────────────────────────────────────────

    async def _tweets(request: Request) -> Response:
        try:
            params = request.query_params
            try:
                limit = max(1, min(int(params.get("limit", 20)), 100))
            except (ValueError, TypeError):
                limit = 20
            handle = (params.get("handle") or "").strip().lstrip("@") or None
            result = await x_service.feed(limit=limit)
            tweets = result.get("tweets", [])
            if handle:
                tweets = [t for t in tweets
                          if (t.get("handle") or "").lower() == handle.lower()]
            return Response(
                content=json.dumps({"status": "ok", "count": len(tweets),
                                    "tweets": tweets}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("x tweets failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    return [
        Route("/api/settings/x", endpoint=_cred_status, methods=["GET"]),
        Route("/api/settings/x", endpoint=_cred_save, methods=["POST"]),
        Route("/api/settings/x", endpoint=_cred_delete, methods=["DELETE"]),
        Route("/api/x/config", endpoint=_config_get, methods=["GET"]),
        Route("/api/x/config", endpoint=_config_set, methods=["POST"]),
        Route("/api/x/status", endpoint=_status, methods=["GET"]),
        Route("/api/x/test", endpoint=_test, methods=["POST"]),
        Route("/api/x/tweets", endpoint=_tweets, methods=["GET"]),
    ]
