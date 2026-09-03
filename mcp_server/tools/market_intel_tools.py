"""Market-intelligence MCP tools: option chain + futures discovery.

These wrap app.market_intel.MarketIntel — the SAME implementation the
WebUI and Chat use — so an AI client gets canonical, bounded answers
without ever constructing broker instrument keys.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp_server.contract import (
    TOOL_FUTURES_CONTRACTS,
    TOOL_OPTION_CHAIN,
)
from mcp_server.registry import get_tool_description


def register_market_intel_tools(mcp, services, **kwargs) -> None:
    """Register derivatives-discovery read tools."""
    intel = getattr(services, "market_intel", None)

    @mcp.tool(name=TOOL_OPTION_CHAIN, description=get_tool_description(TOOL_OPTION_CHAIN))
    async def option_chain(
        underlying: str,
        expiry: str | None = None,
        window: int = 10,
    ) -> dict[str, Any]:
        """Return the option chain for an underlying (e.g. 'NIFTY', 'RELIANCE').

        Returns the current spot price, ATM strike (nearest listed strike
        to spot), and ATM +/- window rows pairing CE and PE contracts.
        Each row includes canonical identity and live fields (ltp, OI,
        volume, bid/ask) when available.

        window controls how many strikes above/below ATM to include
        (default 10). Analytics are computed over the loaded window only.
        Expiry defaults to the nearest available expiry if not specified.
        """
        if intel is None:
            return {"error": "market intelligence service not available"}
        result = await asyncio.to_thread(
            intel.option_chain, underlying, expiry=expiry, window=window)
        if "error" in result:
            return {"status": "error", **result}
        return {"status": "ok", **result}

    @mcp.tool(name=TOOL_FUTURES_CONTRACTS, description=get_tool_description(TOOL_FUTURES_CONTRACTS))
    async def futures_contracts(
        underlying: str,
        expiry: str | None = None,
    ) -> dict[str, Any]:
        """List available futures contracts for an underlying.

        Returns all listed expiries and the contracts (optionally filtered
        to one expiry) with lot size and canonical instrument identity.
        Useful for discovering which derivative contracts are available
        before fetching quotes or building strategies.
        """
        if intel is None:
            return {"error": "market intelligence service not available"}
        result = await asyncio.to_thread(
            intel.futures_contracts, underlying, expiry)
        if "error" in result:
            return {"status": "error", **result}
        return {"status": "ok", **result}
