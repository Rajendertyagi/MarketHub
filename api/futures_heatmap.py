"""Futures heatmap route — thin transport over the shared futures board.

GET /api/futures/heatmap?expiry=latest&buildup=all

The route translates HTTP query parameters into coverage/board/filter
parameters and serializes the result. It implements NO market-data logic:

    API  ->  FnoCoverageService (routing)  ->  market.futures_board  ->  quotes

Routing (provider selection) lives in ``FnoCoverageService``; the board owns
caching + single-flight. Expiries are catalog-derived; no per-underlying
catalog/REST loops.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.product_routes import _json
from market.futures_board import (
    BUILDUP_FILTERS,
    BoardInput,
    filter_board,
    get_futures_board,
)

logger = logging.getLogger(__name__)


def build_futures_heatmap_routes(
    coverage: Any,
    market_service: Any = None,
    store: Any = None,
) -> list[Route]:
    """Build the futures-heatmap route around the coverage service + board."""

    def _quote_reader(exchange: str, token: str) -> Any:
        if market_service is None:
            return None
        return market_service.get_quote_now(exchange, token)

    def _previous_oi_reader(exchange: str, underlying: str, expiry: str,
                            instrument_type: str) -> float | None:
        if store is None:
            return None
        return store.get_previous_oi(
            exchange=exchange, underlying=underlying, expiry=expiry,
            instrument_type=instrument_type)

    def _inputs(plan: Any) -> list[BoardInput]:
        return [
            BoardInput(
                underlying=c.underlying, kind=c.kind, exchange=c.exchange,
                expiry=c.expiry, instrument_type=c.instrument_type,
                provider=c.provider, provider_token=c.provider_token,
                coverage_state=c.state)
            for c in plan.contracts
        ]

    async def _heatmap(request: Request) -> Response:
        if coverage is None:
            return _json({"error": "service unavailable"}, 503)
        qp = request.query_params
        expiry = (qp.get("expiry") or "latest").strip() or "latest"
        buildup = (qp.get("buildup") or "all").strip().lower() or "all"
        if buildup not in BUILDUP_FILTERS:
            return _json({
                "error": f"unknown buildup '{buildup}' "
                         f"(expected one of {sorted(BUILDUP_FILTERS)})"}, 400)

        requested = None if expiry == "latest" else expiry
        try:
            plan = await asyncio.to_thread(coverage.plan, expiry=requested)
        except Exception:
            logger.exception("futures heatmap: routing failed")
            return _json({"error": "routing failed"}, 500)
        if requested and plan.expiry_resolved is None:
            return _json({
                "error": f"unknown expiry '{requested}' "
                         f"(available: {', '.join(plan.expiries[:8])})"}, 404)

        resolved = plan.expiry_resolved or expiry
        inputs = _inputs(plan)
        try:
            snap = await asyncio.to_thread(
                get_futures_board, inputs, expiry=resolved,
                quote_reader=_quote_reader,
                previous_oi_reader=_previous_oi_reader,
                expiries=plan.expiries)
        except Exception:
            logger.exception("futures heatmap: board failed")
            return _json({"error": "board failed"}, 500)

        filtered = filter_board(snap, buildup=buildup)
        payload = filtered.to_dict()
        # Compatibility: the pre-board contract exposed the requested filter
        # and a per-tile ``status`` ("classified"|"unavailable").
        payload["buildup"] = buildup
        for item in payload["items"]:
            item["status"] = ("classified" if item.get("buildup")
                              else "unavailable")
        # Provider / coverage information for the UI (broker-neutral).
        payload["providers"] = [dict(p) for p in plan.providers]
        payload["coverage"] = {
            "desired": plan.desired,
            "routed": plan.routed,
            "by_provider": dict(plan.by_provider),
            "states": dict(plan.states),
            "capacity": {k: dict(v) for k, v in plan.capacity.items()},
            "uncovered": len(plan.uncovered),
            "errors": list(plan.errors),
        }
        return _json(payload)

    return [
        Route("/api/futures/heatmap", endpoint=_heatmap, methods=["GET"]),
    ]
