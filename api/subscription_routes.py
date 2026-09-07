"""Market-data subscription routes (DB-backed preferences).

Thin adapters: parse → SubscriptionService → project. All business logic
(resolution, limits, runtime reconciliation) lives in
app.subscriptions.SubscriptionService. Preferences are durable — apply
failures never roll them back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.product_routes import _json

logger = logging.getLogger(__name__)


def build_subscription_routes(subscriptions: Any,
                              feed_provider: Any) -> list[Route]:
    """Routes over the canonical SubscriptionService.

    GET  /api/subscriptions                — full preference projection
    PATCH /api/subscriptions/indices       — {label, enabled}
    POST /api/subscriptions/stocks         — {key, label}
    PATCH /api/subscriptions/stocks        — {key, enabled}
    DELETE /api/subscriptions/stocks       — ?key=
    GET  /api/subscriptions/rules          — derivative rules
    PUT  /api/subscriptions/rules          — {underlying, ...rule}
    DELETE /api/subscriptions/rules        — ?underlying=
    GET  /api/subscriptions/preview        — resolved set (no apply)
    POST /api/subscriptions/apply          — reconcile live feeds
    GET  /api/subscriptions/status         — health projection
    """

    async def _list(request: Request) -> Response:  # noqa: ARG001
        prefs = await asyncio.to_thread(subscriptions.preferences)
        return _json({"status": "ok", **prefs})

    async def _patch_index(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        label = (body or {}).get("label", "")
        if not isinstance(label, str) or not label.strip():
            return _json({"error": "label is required"}, 400)
        enabled = bool((body or {}).get("enabled", True))
        try:
            row = await asyncio.to_thread(
                subscriptions.set_index, label.strip(), enabled)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok", "subscription": row})

    async def _add_stock(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        key = (body or {}).get("key", "")
        label = (body or {}).get("label", "") or key
        if not isinstance(key, str) or not key.strip():
            return _json({"error": "key is required"}, 400)
        try:
            row = await asyncio.to_thread(
                subscriptions.add_stock, key.strip(), str(label).strip())
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok", "subscription": row})

    async def _patch_stock(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        key = (body or {}).get("key", "")
        if not isinstance(key, str) or not key.strip():
            return _json({"error": "key is required"}, 400)
        enabled = bool((body or {}).get("enabled", True))
        try:
            row = await asyncio.to_thread(
                subscriptions.set_stock_enabled, key.strip(), enabled)
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok", "subscription": row})

    async def _delete_stock(request: Request) -> Response:
        key = request.query_params.get("key", "")
        if not key.strip():
            return _json({"error": "key is required"}, 400)
        deleted = await asyncio.to_thread(
            subscriptions.remove_stock, key.strip())
        return _json({"status": "ok" if deleted else "not found"},
                     200 if deleted else 404)

    async def _list_rules(request: Request) -> Response:  # noqa: ARG001
        prefs = await asyncio.to_thread(subscriptions.preferences)
        return _json({"status": "ok",
                      "rules": prefs["derivatives"]})

    async def _put_rule(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid JSON body"}, 400)
        underlying = (body or {}).get("underlying", "")
        if not isinstance(underlying, str) or not underlying.strip():
            return _json({"error": "underlying is required"}, 400)
        try:
            row = await asyncio.to_thread(
                subscriptions.set_derivative_rule, underlying.strip(),
                futures_enabled=bool((body or {}).get("futures_enabled", False)),
                futures_count=(body or {}).get("futures_count", 1),
                options_enabled=bool((body or {}).get("options_enabled", False)),
                options_count=(body or {}).get("options_count", 1),
                strikes_below=(body or {}).get("strikes_below", 0),
                strikes_above=(body or {}).get("strikes_above", 0),
                calls_enabled=bool((body or {}).get("calls_enabled", True)),
                puts_enabled=bool((body or {}).get("puts_enabled", True)))
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        return _json({"status": "ok", "rule": row})

    async def _delete_rule(request: Request) -> Response:
        underlying = request.query_params.get("underlying", "")
        if not underlying.strip():
            return _json({"error": "underlying is required"}, 400)
        deleted = await asyncio.to_thread(
            subscriptions.remove_derivative_rule, underlying.strip())
        return _json({"status": "ok" if deleted else "not found"},
                     200 if deleted else 404)

    async def _preview(request: Request) -> Response:  # noqa: ARG001
        try:
            resolved = await asyncio.to_thread(subscriptions.resolve)
        except Exception as exc:
            from app.subscriptions.service import SubscriptionLimitError
            if isinstance(exc, SubscriptionLimitError):
                return _json({"status": "error",
                              "error": "subscription limit exceeded",
                              "detail": str(exc)}, 400)
            logger.exception("subscription preview failed")
            return _json({"error": "resolution failed"}, 500)
        return _json({
            "status": "ok",
            "resolved_count": len(resolved["contracts"]),
            "by_provider": {p: len(k) for p, k
                            in resolved["by_provider"].items()},
            "contracts": resolved["contracts"],
            "atm": resolved["atm"],
            "pending": resolved["pending"],
            "notes": resolved["notes"],
        })

    async def _apply(request: Request) -> Response:  # noqa: ARG001
        try:
            outcome = await subscriptions.reconcile(feed_provider)
        except Exception as exc:
            from app.subscriptions.service import SubscriptionLimitError
            if isinstance(exc, SubscriptionLimitError):
                return _json({"status": "error",
                              "error": "subscription limit exceeded",
                              "detail": str(exc)}, 400)
            logger.exception("subscription apply failed")
            return _json({"error": "apply failed"}, 500)
        return _json({
            "status": "ok",
            "resolved_count": len(outcome["resolved"]["contracts"]),
            "by_provider": {p: len(k) for p, k
                            in outcome["resolved"]["by_provider"].items()},
            "apply": outcome["apply"],
        })

    async def _status(request: Request) -> Response:  # noqa: ARG001
        st = await asyncio.to_thread(subscriptions.status)
        return _json({"status": "ok", **st})

    return [
        Route("/api/subscriptions", endpoint=_list, methods=["GET"]),
        Route("/api/subscriptions/indices", endpoint=_patch_index,
              methods=["PATCH"]),
        Route("/api/subscriptions/stocks", endpoint=_add_stock,
              methods=["POST"]),
        Route("/api/subscriptions/stocks", endpoint=_patch_stock,
              methods=["PATCH"]),
        Route("/api/subscriptions/stocks", endpoint=_delete_stock,
              methods=["DELETE"]),
        Route("/api/subscriptions/rules", endpoint=_list_rules,
              methods=["GET"]),
        Route("/api/subscriptions/rules", endpoint=_put_rule,
              methods=["PUT"]),
        Route("/api/subscriptions/rules", endpoint=_delete_rule,
              methods=["DELETE"]),
        Route("/api/subscriptions/preview", endpoint=_preview,
              methods=["GET"]),
        Route("/api/subscriptions/apply", endpoint=_apply,
              methods=["POST"]),
        Route("/api/subscriptions/status", endpoint=_status,
              methods=["GET"]),
    ]
