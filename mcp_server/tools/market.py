"""
Market data tools (read-only) — thin adapters over the shared MarketService.

These tools expose market information through MCP without creating broker
connections or duplicating state. All reads go through MarketService, which
is the single source of truth for canonical market data.

No trading/order tools. No raw broker access. No provider leakage.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp_server.contract import (
    TOOL_INSTRUMENT_SEARCH,
    TOOL_MARKET_DEPTH,
    TOOL_MARKET_HISTORY,
    TOOL_MARKET_QUOTE,
    TOOL_MARKET_STATUS,
    TOOL_WATCHLISTS,
)
from mcp_server.registry import get_tool_description


def _parse_instrument_ref(
    instrument_ref: str, services: Any,
) -> tuple[str, str] | None:
    """Resolve a single instrument reference to (exchange, instrument_token).

    Supported formats:
      - Canonical symbol: ``"RELIANCE"`` or ``"NIFTY"`` → resolved via catalog
      - Provider key: ``"NSE_EQ|INE002A01018"`` → resolved via catalog
      - Legacy exchange|token: ``"NSE|12345"`` → resolved via catalog

    Returns ``(exchange, instrument_token)`` or ``None`` if unresolvable.
    """
    catalog = getattr(services, "instrument_catalog", None)
    if catalog is not None:
        try:
            results = catalog.search(q=instrument_ref, limit=1)
            if results:
                row = results[0]
                exchange = row.get("exchange", "")
                token = row.get("instrument_token", "")
                if exchange and token:
                    return exchange, token
        except Exception:
            pass

    # Fallback: if pipe-delimited, try to use it directly as exchange|token
    if "|" in instrument_ref:
        parts = instrument_ref.split("|", 1)
        return parts[0].strip(), parts[1].strip()

    return None


def register_market_tools(mcp, services, **kwargs) -> None:
    """Register read-only market data tools."""

    @mcp.tool(name=TOOL_MARKET_QUOTE, description=get_tool_description(TOOL_MARKET_QUOTE))
    async def market_quote(
        instrument_ref: str,
    ) -> dict[str, Any]:
        """Return the latest canonical quote for one instrument.

        instrument_ref accepts a canonical symbol (e.g. 'RELIANCE', 'NIFTY'),
        a provider instrument key (e.g. 'NSE_EQ|RELIANCE'), or any alias
        registered in the instrument identity registry. The instrument must
        already be known to MarketHub (seeded via instrument catalog or
        feed). Returns an error if the instrument is unknown or no quote
        is available.
        """
        svc = services.market_service
        if svc is None:
            return {"error": "market service not available"}
        resolved = _parse_instrument_ref(instrument_ref, services)
        if resolved is None:
            return {"error": "could not resolve instrument reference",
                    "instrument_ref": instrument_ref}
        exchange, instrument_token = resolved
        quote = await svc.get_quote(exchange, instrument_token)
        if quote is None:
            return {"error": "quote not found",
                    "instrument_ref": instrument_ref}
        from market.serialization import quote_to_dict
        return {"status": "ok", "quote": quote_to_dict(quote)}

    @mcp.tool(name=TOOL_MARKET_DEPTH, description=get_tool_description(TOOL_MARKET_DEPTH))
    async def market_depth(
        instrument_ref: str,
    ) -> dict[str, Any]:
        """Return the latest market depth (L2 order book) for one instrument.

        instrument_ref accepts a canonical symbol, provider key, or registered
        alias. Depth may be 5-level (HSM), 30-level (Upstox REST), or
        50-level (Fyers TBT) depending on the active source. Returns an
        error if depth is unavailable for this instrument.
        """
        svc = services.market_service
        if svc is None:
            return {"error": "market service not available"}
        resolved = _parse_instrument_ref(instrument_ref, services)
        if resolved is None:
            return {"error": "could not resolve instrument reference",
                    "instrument_ref": instrument_ref}
        exchange, instrument_token = resolved
        depth = await svc.get_depth(exchange, instrument_token)
        if depth is None:
            return {"error": "depth not found",
                    "instrument_ref": instrument_ref}
        from market.serialization import depth_to_dict
        return {"status": "ok", "depth": depth_to_dict(depth)}

    @mcp.tool(name=TOOL_MARKET_STATUS, description=get_tool_description(TOOL_MARKET_STATUS))
    async def market_status() -> dict[str, Any]:
        """Return MarketService diagnostic counters.

        Shows quote/depth counts, accepted vs stale updates, and overall
        service health. Useful for checking data freshness and whether the
        feed is actively streaming.
        """
        svc = services.market_service
        if svc is None:
            return {"error": "market service not available"}
        return {"status": "ok", "service": await svc.status()}

    @mcp.tool(name=TOOL_INSTRUMENT_SEARCH, description=get_tool_description(TOOL_INSTRUMENT_SEARCH))
    async def instrument_search(
        q: str,
        exchange: str | None = None,
        expiry: str | None = None,
        types: list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search instruments by human-readable query.

        Understands plain symbols ('reliance'), type words ('reliance
        future', 'nifty futures'), and option descriptors ('nifty 25000
        ce'). Returns compact candidates with canonical instrument_key
        usable by other tools (quote, depth, history).

        Results are limited by the 'limit' parameter (default 10, max 50).
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

    @mcp.tool(name=TOOL_WATCHLISTS, description=get_tool_description(TOOL_WATCHLISTS))
    async def watchlists() -> dict[str, Any]:
        """List all persistent watchlists and their instruments.

        Returns watchlist names, IDs, and the instruments in each.
        Useful for discovering which instruments the user is tracking.
        """
        store = getattr(services, "store", None)
        if store is None:
            return {"error": "watchlist store not available"}
        out = []
        for wl in await asyncio.to_thread(store.list_watchlists):
            items = await asyncio.to_thread(store.list_watchlist_items,
                                            wl["id"])
            out.append({"id": wl["id"], "name": wl["name"], "items": items})
        return {"status": "ok", "watchlists": out}

    @mcp.tool(name=TOOL_MARKET_HISTORY, description=get_tool_description(TOOL_MARKET_HISTORY))
    async def market_history(
        instrument_ref: str,
        unit: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> dict[str, Any]:
        """Return historical OHLCV candles for an instrument.

        instrument_ref: canonical symbol, provider key, or registered alias.
        unit: 'minutes', 'hours', 'days', 'weeks', or 'months'.
        interval: number of units per candle (e.g. 5 for 5-minute candles).
        from_date / to_date: ISO date strings (YYYY-MM-DD).

        Maximum range depends on provider (typically 30-400 days).
        """
        md = getattr(services, "provider_market_data", None)
        if md is None:
            return {"error": "market history service not available"}
        # Resolve instrument_ref to an instrument_key the provider understands.
        # Legacy pipe format ("NSE_EQ|RELIANCE") provides the token directly;
        # canonical symbols are resolved via the catalog.
        resolved = _parse_instrument_ref(instrument_ref, services)
        if resolved is None:
            return {"error": "could not resolve instrument reference",
                    "instrument_ref": instrument_ref}
        _exchange, instrument_key = resolved
        try:
            candles = await md.history(
                instrument_key=instrument_key, unit=unit,
                interval=interval, from_date=from_date, to_date=to_date)
        except Exception as exc:
            return {"error": f"history failed: {type(exc).__name__}"}
        from market.serialization import _to_json_value
        return {"status": "ok",
                "candles": [_to_json_value(c) for c in candles]}
