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
from typing import Any

from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

__all__ = ["build_market_routes"]


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
