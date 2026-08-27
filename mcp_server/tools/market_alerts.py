"""Market-alert MCP tools — AI-manageable price alerts.

These operate on the SAME store + engine as the WebUI Alerts page
(market_alerts table, AlertEngine, durable trigger history). Creating an
alert is intentionally allowed; nothing here can place orders.

Instrument resolution accepts a human query ('NIFTY', 'RELIANCE') via
MarketIntel so callers never need raw broker keys.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp_server.contract import (
    TOOL_MARKET_ALERT_CREATE,
    TOOL_MARKET_ALERT_DELETE,
    TOOL_MARKET_ALERT_DISABLE,
    TOOL_MARKET_ALERT_ENABLE,
    TOOL_MARKET_ALERT_LIST,
)

_ALLOWED_FIELDS = ("ltp", "change_percent", "volume")
_ALLOWED_OPERATORS = ("gt", "lt", "crosses_above", "crosses_below")


def register_market_alert_tools(mcp, services, **kwargs) -> None:
    """Register AI-manageable market alert tools."""
    store = getattr(services, "store", None)
    intel = getattr(services, "market_intel", None)
    engine = getattr(services, "alert_engine", None)

    def _reload_engine() -> None:
        if engine is not None:
            try:
                engine.reload()
            except Exception:
                pass

    async def _resolve(query: str) -> dict[str, Any] | None:
        if intel is None:
            return None
        found = await asyncio.to_thread(intel.search, query, limit=5)
        results = found.get("results") or []
        return results[0] if results else None

    @mcp.tool(name=TOOL_MARKET_ALERT_CREATE)
    async def market_alert_create(
        instrument_query: str,
        operator: str,
        threshold: float,
        field: str = "ltp",
    ) -> dict[str, Any]:
        """Create a market price alert.

        instrument_query is a human symbol like 'NIFTY' or 'RELIANCE'
        (resolved through instrument search). operator is one of:
        gt, lt, crosses_above, crosses_below. field defaults to ltp;
        change_percent and volume are also supported. Returns the created
        alert summary on success — persistence is confirmed before
        returning.
        """
        if store is None or intel is None:
            return {"error": "alert services not available"}
        operator = (operator or "").strip()
        field = (field or "ltp").strip().lower()
        if operator not in _ALLOWED_OPERATORS:
            return {"error": f"operator must be one of "
                             f"{list(_ALLOWED_OPERATORS)}"}
        if field not in _ALLOWED_FIELDS:
            return {"error": f"field must be one of {list(_ALLOWED_FIELDS)}"}
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            return {"error": "threshold must be a number"}
        inst = await _resolve(instrument_query)
        if inst is None:
            return {"error": f"no instrument matches '{instrument_query}'"}
        exchange, token = inst["instrument_key"].split(":", 1)

        def _create():
            return store.create_market_alert(
                exchange=exchange, instrument_token=token,
                tradingsymbol=inst["symbol"], field=field,
                operator=operator, threshold=threshold)

        try:
            alert = await asyncio.to_thread(_create)
        except ValueError as exc:
            return {"error": str(exc)}
        _reload_engine()
        return {"status": "created", "alert": alert,
                "instrument": inst}

    @mcp.tool(name=TOOL_MARKET_ALERT_LIST)
    async def market_alert_list() -> dict[str, Any]:
        """List all configured market alerts with their state."""
        if store is None:
            return {"error": "alert store not available"}
        alerts = await asyncio.to_thread(store.list_market_alerts)
        return {"status": "ok", "count": len(alerts), "alerts": alerts}

    @mcp.tool(name=TOOL_MARKET_ALERT_ENABLE)
    async def market_alert_enable(alert_id: int) -> dict[str, Any]:
        """Enable a disabled market alert by id."""
        if store is None:
            return {"error": "alert store not available"}

        def _set():
            return store.set_alert_enabled(int(alert_id), True)

        ok = await asyncio.to_thread(_set)
        _reload_engine()
        return {"status": "enabled" if ok else "not found",
                "ok": bool(ok), "alert_id": int(alert_id)}

    @mcp.tool(name=TOOL_MARKET_ALERT_DISABLE)
    async def market_alert_disable(alert_id: int) -> dict[str, Any]:
        """Disable a market alert by id (it stops evaluating)."""
        if store is None:
            return {"error": "alert store not available"}

        def _set():
            return store.set_alert_enabled(int(alert_id), False)

        ok = await asyncio.to_thread(_set)
        _reload_engine()
        return {"status": "disabled" if ok else "not found",
                "ok": bool(ok), "alert_id": int(alert_id)}

    @mcp.tool(name=TOOL_MARKET_ALERT_DELETE)
    async def market_alert_delete(alert_id: int) -> dict[str, Any]:
        """Delete a market alert by id. Historical trigger records are
        preserved (deleting an alert never erases its past firings)."""
        if store is None:
            return {"error": "alert store not available"}

        def _del():
            return store.delete_alert(int(alert_id))

        ok = await asyncio.to_thread(_del)
        _reload_engine()
        return {"status": "deleted" if ok else "not found",
                "ok": bool(ok), "alert_id": int(alert_id)}
