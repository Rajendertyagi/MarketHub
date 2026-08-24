"""
Market data tools (read-only) — thin adapters over the shared MarketService.

These tools expose market information through MCP without creating broker
connections or duplicating state. All reads go through MarketService, which
is the single source of truth for canonical market data.

No trading/order tools. No raw broker access.
"""

from __future__ import annotations

from typing import Any

from mcp_server.contract import (
    TOOL_MARKET_DEPTH,
    TOOL_MARKET_QUOTE,
    TOOL_MARKET_STATUS,
)


def register_market_tools(mcp, services, **kwargs) -> None:
    """Register read-only market data tools."""

    @mcp.tool(name=TOOL_MARKET_QUOTE)
    async def market_quote(
        exchange: str,
        instrument_token: str,
    ) -> dict[str, Any]:
        """Get the latest normalized quote for an instrument."""
        svc = services.market_service
        if svc is None:
            return {"error": "market service not available"}
        quote = await svc.get_quote(exchange, instrument_token)
        if quote is None:
            return {"error": "quote not found",
                    "exchange": exchange, "instrument_token": instrument_token}
        from market.serialization import quote_to_dict
        return {"status": "ok", "quote": quote_to_dict(quote)}

    @mcp.tool(name=TOOL_MARKET_DEPTH)
    async def market_depth(
        exchange: str,
        instrument_token: str,
    ) -> dict[str, Any]:
        """Get the latest market depth for an instrument."""
        svc = services.market_service
        if svc is None:
            return {"error": "market service not available"}
        depth = await svc.get_depth(exchange, instrument_token)
        if depth is None:
            return {"error": "depth not found",
                    "exchange": exchange, "instrument_token": instrument_token}
        from market.serialization import depth_to_dict
        return {"status": "ok", "depth": depth_to_dict(depth)}

    @mcp.tool(name=TOOL_MARKET_STATUS)
    async def market_status() -> dict[str, Any]:
        """Get overall market service status and counters."""
        svc = services.market_service
        if svc is None:
            return {"error": "market service not available"}
        return {"status": "ok", "service": await svc.status()}
