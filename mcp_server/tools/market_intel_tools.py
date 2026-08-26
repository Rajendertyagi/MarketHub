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


def register_market_intel_tools(mcp, services, **kwargs) -> None:
    """Register derivatives-discovery read tools."""
    intel = getattr(services, "market_intel", None)

    @mcp.tool(name=TOOL_OPTION_CHAIN)
    async def option_chain(
        underlying: str,
        expiry: str | None = None,
        window: int = 10,
    ) -> dict[str, Any]:
        """Option chain for an underlying (e.g. 'NIFTY', 'RELIANCE').

        Returns spot (with freshness basis), the ATM strike (nearest
        ACTUAL listed strike), and ATM±window rows pairing CE and PE
        contracts with canonical identity and live fields (ltp/OI/volume/
        bid/ask) when market state has them. window defaults to 10;
        analytics cover the loaded window only.
        """
        if intel is None:
            return {"error": "market intelligence service not available"}
        result = await asyncio.to_thread(
            intel.option_chain, underlying, expiry=expiry, window=window)
        if "error" in result:
            return {"status": "error", **result}
        return {"status": "ok", **result}

    @mcp.tool(name=TOOL_FUTURES_CONTRACTS)
    async def futures_contracts(
        underlying: str,
        expiry: str | None = None,
    ) -> dict[str, Any]:
        """List futures contracts for an underlying.

        Returns the underlying's identity, all listed expiries, and the
        contracts (optionally filtered to one expiry) with lot size.
        """
        if intel is None:
            return {"error": "market intelligence service not available"}
        result = await asyncio.to_thread(
            intel.futures_contracts, underlying, expiry)
        if "error" in result:
            return {"status": "error", **result}
        return {"status": "ok", **result}
