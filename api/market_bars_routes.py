"""Chart bar route — thin transport over BarService.

GET /api/market/bars?instrument_key=...&timeframe=5&from=<ms>&to=<ms>&limit=500

Timestamps are epoch milliseconds (bar OPEN time, UTC), matching the chart
client's own timestamp unit. The route validates parameters and serializes;
it contains no market-data logic.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from api.product_routes import _json
from app.market_bars import BarsUnavailable

logger = logging.getLogger(__name__)


def _int_param(value: str | None, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None


def build_market_bars_routes(service: Any) -> list[Route]:
    async def _bars(request: Request) -> Response:
        if service is None:
            return _json({"error": "bar service unavailable"}, 503)
        qp = request.query_params
        instrument_key = (qp.get("instrument_key") or "").strip()
        if not instrument_key:
            return _json({"error": "instrument_key is required"}, 400)
        try:
            from_ts = _int_param(qp.get("from"), "from")
            to_ts = _int_param(qp.get("to"), "to")
            limit = _int_param(qp.get("limit"), "limit")
        except ValueError as exc:
            return _json({"error": str(exc)}, 400)
        provider = (qp.get("provider") or "").strip() or None
        try:
            payload = await service.get_bars(
                instrument_key=instrument_key,
                timeframe=qp.get("timeframe"),
                provider=provider,
                from_ts=from_ts,
                to_ts=to_ts,
                limit=500 if limit is None else limit,
            )
        except BarsUnavailable as exc:
            return _json({"error": str(exc)}, 400)
        except Exception:
            logger.exception("market bars: request failed")
            return _json({"error": "bar history unavailable"}, 502)
        return _json(payload)

    return [
        Route("/api/market/bars", endpoint=_bars, methods=["GET"]),
    ]
