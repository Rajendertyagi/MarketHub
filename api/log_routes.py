"""REST + SSE routes for WebUI live log viewing.

Provides:
  GET /api/logs        — recent log records with filters
  GET /api/logs/stream — SSE live stream of new log records
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from sse_starlette.sse import EventSourceResponse

from core.log_redaction import redact_record

logger = logging.getLogger(__name__)


def build_log_routes(log_buffer: Any, log_broker: Any) -> list[Route]:
    """Build the log viewing routes.

    Parameters
    ----------
    log_buffer:
        The LogBuffer instance for querying recent records.
    log_broker:
        The EventBroker instance for SSE live streaming.
    """

    async def _get_logs(request: Request) -> Response:
        """GET /api/logs — return recent log records with optional filters.

        Query params:
          level       — filter by level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
          logger      — filter by logger name substring
          search      — free-text search in message
          consumer_id — filter by consumer_id
          alert_id    — filter by alert_id
          request_id  — filter by request_id
          limit       — max records to return (default 200, max 1000)
        """
        level = request.query_params.get("level")
        logger_pat = request.query_params.get("logger")
        search = request.query_params.get("search")
        consumer_id = request.query_params.get("consumer_id")
        alert_id = request.query_params.get("alert_id")
        request_id = request.query_params.get("request_id")
        limit_str = request.query_params.get("limit", "200")

        try:
            limit = max(1, min(int(limit_str), 1000))
        except (ValueError, TypeError):
            limit = 200

        records = log_buffer.snapshot(
            limit=limit,
            level=level,
            logger_pattern=logger_pat,
            search=search,
            consumer_id=consumer_id,
            alert_id=alert_id,
            request_id=request_id,
        )

        # Redact each record before serving
        records = [redact_record(r) for r in records]

        return Response(
            content=json.dumps({
                "status": "ok",
                "count": len(records),
                "records": records,
            }),
            media_type="application/json",
            status_code=200,
        )

    async def _stream_logs(request: Request) -> Response:
        """GET /api/logs/stream — SSE live stream of new log records.

        Sends each new log record as a ``data:`` SSE message containing
        the structured JSON.  Handles disconnect cleanly via the
        EventBroker subscribe context manager.
        """
        async def _generate():
            async with log_broker.subscribe() as lines:
                async for line in lines:
                    yield line

        return EventSourceResponse(
            _generate(),
            media_type="text/event-stream",
            ping=15,
        )

    return [
        Route("/api/logs", endpoint=_get_logs, methods=["GET"]),
        Route("/api/logs/stream", endpoint=_stream_logs, methods=["GET"]),
    ]
