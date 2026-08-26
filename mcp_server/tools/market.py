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
    TOOL_INSTRUMENT_SEARCH,
    TOOL_MARKET_DEPTH,
    TOOL_MARKET_HISTORY,
    TOOL_MARKET_QUOTE,
    TOOL_MARKET_STATUS,
    TOOL_WATCHLISTS,
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

    @mcp.tool(name=TOOL_INSTRUMENT_SEARCH)
    async def instrument_search(
        q: str,
        exchange: str | None = None,
        expiry: str | None = None,
        types: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search instruments by human query.

        Understands plain symbols ('reliance'), type words ('reliance
        future', 'nifty futures'), and option descriptors ('nifty 25000
        ce'). Returns compact candidates with canonical instrument_key
        usable by other market tools.
        """
        intel = getattr(services, "market_intel", None)
        if intel is not None:
            result = await asyncio.to_thread(
                intel.search, q, types=types, exchange=exchange,
                expiry=expiry, limit=min(max(limit, 1), 50))
            return {"status": "ok", **result}
        catalog = getattr(services, "instrument_catalog", None)
        if catalog is None:
            return {"error": "instrument catalog not available"}
        results = await asyncio.to_thread(
            catalog.search, q=q, exchange=exchange,
            limit=min(max(limit, 1), 50))
        return {"status": "ok", "count": len(results), "results": results}

    @mcp.tool(name=TOOL_WATCHLISTS)
    async def watchlists() -> dict[str, Any]:
        """List persistent watchlists and their instruments."""
        store = getattr(services, "store", None)
        if store is None:
            return {"error": "watchlist store not available"}
        out = []
        for wl in await asyncio.to_thread(store.list_watchlists):
            items = await asyncio.to_thread(store.list_watchlist_items,
                                            wl["id"])
            out.append({"id": wl["id"], "name": wl["name"], "items": items})
        return {"status": "ok", "watchlists": out}

    @mcp.tool(name=TOOL_MARKET_HISTORY)
    async def market_history(
        provider: str,
        instrument_key: str,
        unit: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """Get canonical OHLCV candles for an instrument (provider-backed)."""
        md = getattr(services, "provider_market_data", None)
        if md is None:
            return {"error": "market history service not available"}
        try:
            candles = await md.history(
                instrument_key=instrument_key, unit=unit,
                interval=interval, from_date=from_date, to_date=to_date,
                provider=provider)
        except Exception as exc:
            return {"error": f"history failed: {type(exc).__name__}"}
        from market.serialization import _to_json_value
        return {"status": "ok",
                "candles": [_to_json_value(c) for c in candles]}
