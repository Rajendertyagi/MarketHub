"""
HTTP API route builders for MarketHub.

Responsibility boundary: build Starlette Route objects, nothing else.
No business logic, no model serialization, no service construction, no
config ownership — dependencies are injected by app/server.py.

Phase C exposes exactly one route:

    GET /api/market/stream
        Dedicated LIVE market SSE stream backed by the dedicated market
        EventBroker instance (NOT the generic event stream). Live only —
        no replay. Framing: plain-string yields are wrapped as ``data:``
        frames by sse-starlette 3.x (verified in test_sse_stream.py wire
        notes), so no manual ``\\r\\n`` framing is emitted here.
"""

from __future__ import annotations

from typing import Any

from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

__all__ = ["build_market_routes"]


def build_market_routes(market_broker: Any) -> list[Route]:
    """Build the market API routes around the injected market EventBroker."""

    async def _market_stream(request: Request) -> Response:  # noqa: ARG001
        async def _generate():
            async with market_broker.subscribe() as lines:
                async for line in lines:
                    yield line

        return EventSourceResponse(
            _generate(),
            media_type="text/event-stream",
            ping=15,
        )

    return [
        Route("/api/market/stream", endpoint=_market_stream, methods=["GET"]),
    ]
