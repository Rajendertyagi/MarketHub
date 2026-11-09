"""F&O coverage + catalog routes for the unified Market Data page.

Thin transports over:
  * InstrumentCatalog  — catalog sync state / segments / counts
  * FnoCoverageService — canonical F&O universe + provider routing + coverage
  * SubscriptionService — applies/releases the derivatives-universe owner

Read endpoints never change live subscriptions. Apply/release mutate only the
``_derivatives_universe`` owner (additive; never touches other owners).
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


def build_fno_coverage_routes(
    catalog: Any,
    subscriptions: Any,
    coverage: Any,
    feed_provider: Any = None,
) -> list[Route]:
    """Build the F&O catalog + coverage routes around injected services."""

    async def _catalog(request: Request) -> Response:
        if catalog is None:
            return _json({"error": "catalog unavailable"}, 503)
        try:
            sync_state = await asyncio.to_thread(catalog.sync_state)
            segments = await asyncio.to_thread(catalog.segment_counts)
        except Exception as exc:
            logger.debug("fno catalog projection failed: %s", exc)
            return _json({"error": "catalog read failed"}, 500)
        plan = await asyncio.to_thread(coverage.plan)
        return _json({
            "status": "ok",
            "sync_state": sync_state,
            "segments": segments,
            "fno": {
                "available": plan.desired > 0,
                "universe_count": len(plan.contracts),
                "expiry": plan.expiry_resolved,
                "errors": plan.errors,
            },
        })

    async def _universe(request: Request) -> Response:
        qp = request.query_params
        expiry = (qp.get("expiry") or "").strip() or None
        try:
            plan = await asyncio.to_thread(coverage.plan, expiry=expiry)
        except Exception as exc:
            logger.debug("fno universe failed: %s", exc)
            return _json({"error": "universe failed"}, 500)
        indices = sorted({c.underlying for c in plan.contracts
                          if c.kind == "index"})
        stocks = sorted({c.underlying for c in plan.contracts
                         if c.kind == "equity"})
        return _json({
            "status": "ok",
            "enabled": plan.enabled,
            "indices": indices,
            "stocks": stocks,
            "expiries": plan.expiries,
            "expiry_resolved": plan.expiry_resolved,
            "errors": plan.errors,
        })

    async def _plan_payload(expiry: str | None) -> dict[str, Any]:
        plan = await asyncio.to_thread(coverage.plan, expiry=expiry)
        return plan.to_dict()

    async def _coverage(request: Request) -> Response:
        expiry = (request.query_params.get("expiry") or "").strip() or None
        try:
            return _json({"status": "ok",
                          **await _plan_payload(expiry)})
        except Exception as exc:
            logger.debug("fno coverage failed: %s", exc)
            return _json({"error": "coverage failed"}, 500)

    async def _preview(request: Request) -> Response:
        expiry = (request.query_params.get("expiry") or "").strip() or None
        try:
            payload = await _plan_payload(expiry)
        except Exception as exc:
            logger.debug("fno preview failed: %s", exc)
            return _json({"error": "preview failed"}, 500)
        return _json({
            "status": "ok",
            "preview": True,
            "desired": payload["desired"],
            "routed": payload["routed"],
            "by_provider": payload["by_provider"],
            "states": payload["states"],
            "capacity": payload["capacity"],
            "uncovered": payload["uncovered"],
            "errors": payload["errors"],
            "expiry_resolved": payload["expiry_resolved"],
        })

    async def _apply(request: Request) -> Response:
        if subscriptions is None or feed_provider is None:
            return _json({"error": "subscription service unavailable"}, 503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        expiry = (body or {}).get("expiry")
        expiry = str(expiry).strip() if expiry else None
        try:
            plan = await asyncio.to_thread(coverage.plan, expiry=expiry)
        except Exception as exc:
            logger.debug("fno apply plan failed: %s", exc)
            return _json({"error": "routing failed"}, 500)
        subscriptions.set_derivatives_universe(plan.keys_by_provider)
        try:
            outcome = await subscriptions.reconcile(feed_provider)
        except Exception as exc:
            from app.subscriptions.service import SubscriptionBudgetError
            if isinstance(exc, SubscriptionBudgetError):
                return _json({"status": "error",
                              "error": "subscription budget exceeded",
                              "violations": exc.violations}, 400)
            logger.exception("fno apply failed")
            return _json({"error": "apply failed"}, 500)
        return _json({
            "status": "ok",
            "expiry_resolved": plan.expiry_resolved,
            "desired": plan.desired,
            "routed": plan.routed,
            "by_provider": plan.by_provider,
            "apply": outcome["apply"],
        })

    async def _release(request: Request) -> Response:
        if subscriptions is None or feed_provider is None:
            return _json({"error": "subscription service unavailable"}, 503)
        subscriptions.clear_derivatives_universe()
        try:
            outcome = await subscriptions.reconcile(feed_provider)
        except Exception:
            logger.exception("fno release failed")
            return _json({"error": "release failed"}, 500)
        return _json({"status": "ok", "released": True,
                      "apply": outcome["apply"]})

    return [
        Route("/api/fno/catalog", endpoint=_catalog, methods=["GET"]),
        Route("/api/fno/universe", endpoint=_universe, methods=["GET"]),
        Route("/api/fno/coverage", endpoint=_coverage, methods=["GET"]),
        Route("/api/fno/preview", endpoint=_preview, methods=["GET"]),
        Route("/api/fno/apply", endpoint=_apply, methods=["POST"]),
        Route("/api/fno/apply", endpoint=_release, methods=["DELETE"]),
    ]
