"""REST routes for the Test Center diagnostic runner."""
from __future__ import annotations

import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

logger = logging.getLogger(__name__)


def build_diagnostics_run_routes(runner: Any) -> list[Route]:
    """Build diagnostic test endpoints.

    GET /api/diagnostics/run?mode=quick|full&symbol=NIFTY
        Runs the requested check suite and returns results.

    GET /api/diagnostics/checks
        Returns check metadata without executing anything.
    """

    async def _run_diagnostics(request: Request) -> Response:
        mode = request.query_params.get("mode", "quick").strip().lower()
        symbol = request.query_params.get("symbol", "NIFTY").strip().upper() or "NIFTY"

        if mode not in ("quick", "full"):
            return Response(
                content='{"error": "mode must be quick or full"}',
                media_type="application/json",
                status_code=400,
            )

        try:
            if mode == "quick":
                result = await runner.run_quick()
            else:
                result = await runner.run_full()
            # Inject the selected symbol into the result for the UI
            result["symbol"] = symbol
            return Response(
                content=__import__("json").dumps(result),
                media_type="application/json",
            )
        except Exception as exc:
            logger.exception("diagnostics run failed")
            return Response(
                content=f'{{"error": "{type(exc).__name__}: {exc}"}}',
                media_type="application/json",
                status_code=500,
            )

    async def _list_checks(request: Request) -> Response:  # noqa: ARG001
        checks = runner.list_checks()
        return Response(
            content=__import__("json").dumps({"checks": checks}),
            media_type="application/json",
        )

    return [
        Route("/api/diagnostics/run", endpoint=_run_diagnostics, methods=["GET"]),
        Route("/api/diagnostics/checks", endpoint=_list_checks, methods=["GET"]),
    ]
