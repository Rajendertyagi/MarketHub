"""Chat tool registry — ONE set of market tools for the AI agent.

Executors back onto the same application services as REST/MCP
(MarketIntel, MarketService, alert store/engine). No scraping, no raw
broker payloads, no duplicated calculations.

Each tool: JSON-schema definition (provider-neutral) + async executor.
Boundaries: read-only market data + alert management. NO trading tools
exist anywhere in this registry by design.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


def _quote_compact(quote) -> dict[str, Any] | None:
    if quote is None:
        return None
    from market.serialization import quote_to_dict
    d = quote_to_dict(quote)
    # Compact projection: identity + the fields an answer needs.
    keep = ("instrument_token", "exchange", "tradingsymbol", "ltp",
            "change", "change_percent", "open", "high", "low", "close",
            "volume", "best_bid", "best_ask", "open_interest",
            "received_ts")
    return {k: d[k] for k in keep if d.get(k) is not None}


class ChatToolRegistry:
    """Definitions + executors for the Chat agent."""

    def __init__(self, *, market_intel, market_service, store,
                 alert_engine=None) -> None:
        self._intel = market_intel
        self._msvc = market_service
        self._store = store
        self._engine = alert_engine
        self._defs: list[dict[str, Any]] = []
        self._executors: dict[str, Callable] = {}
        self._register_all()

    # -- registration -------------------------------------------------------

    def _register(self, name: str, description: str,
                  parameters: dict[str, Any], executor: Callable) -> None:
        self._defs.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        })
        self._executors[name] = executor

    def _register_all(self) -> None:
        self._register(
            "instrument_search",
            "Search instruments by human query. Understands type words "
            "('reliance future') and option descriptors ('nifty 25000 "
            "ce'). ALWAYS use this first to resolve any symbol.",
            {"type": "object",
             "properties": {
                 "query": {"type": "string"},
                 "types": {"type": "array",
                           "items": {"type": "string"}},
             },
             "required": ["query"]},
            self._tool_instrument_search)

        self._register(
            "market_quote",
            "Current canonical quote for one instrument query ('NIFTY', "
            "'RELIANCE', 'nifty 25000 ce'). Includes freshness timestamp.",
            {"type": "object",
             "properties": {"query": {"type": "string"}},
             "required": ["query"]},
            self._tool_market_quote)

        self._register(
            "market_depth",
            "Order-book depth for one instrument. Indices have no depth "
            "(returns unavailable, not an error).",
            {"type": "object",
             "properties": {"query": {"type": "string"}},
             "required": ["query"]},
            self._tool_market_depth)

        self._register(
            "futures_contracts",
            "List futures contracts for an underlying with expiries.",
            {"type": "object",
             "properties": {
                 "underlying": {"type": "string"},
                 "expiry": {"type": "string"},
             },
             "required": ["underlying"]},
            self._tool_futures_contracts)

        self._register(
            "option_expiries",
            "List available option expiries for an underlying.",
            {"type": "object",
             "properties": {"underlying": {"type": "string"}},
             "required": ["underlying"]},
            self._tool_option_expiries)

        self._register(
            "option_chain",
            "Option chain for an underlying: spot, ATM strike (nearest "
            "listed), and ATM±window CE/PE rows with live ltp/OI/volume/"
            "bid/ask when available. Default window 10.",
            {"type": "object",
             "properties": {
                 "underlying": {"type": "string"},
                 "expiry": {"type": "string"},
                 "window": {"type": "integer"},
             },
             "required": ["underlying"]},
            self._tool_option_chain)

        self._register(
            "market_history",
            "Historical OHLCV candles for an instrument query.",
            {"type": "object",
             "properties": {
                 "query": {"type": "string"},
                 "unit": {"type": "string",
                          "enum": ["minutes", "hours", "days"]},
                 "interval": {"type": "integer"},
                 "days_back": {"type": "integer"},
             },
             "required": ["query"]},
            self._tool_market_history)

        self._register(
            "market_alert_create",
            "Create a price alert. operator: gt/lt/crosses_above/"
            "crosses_below; field: ltp/change_percent/volume.",
            {"type": "object",
             "properties": {
                 "instrument_query": {"type": "string"},
                 "operator": {"type": "string"},
                 "threshold": {"type": "number"},
                 "field": {"type": "string"},
             },
             "required": ["instrument_query", "operator", "threshold"]},
            self._tool_alert_create)

        self._register(
            "market_alert_list",
            "List all configured market alerts and their state.",
            {"type": "object", "properties": {}},
            self._tool_alert_list)

        self._register(
            "market_alert_enable",
            "Enable a disabled alert by id.",
            {"type": "object",
             "properties": {"alert_id": {"type": "integer"}},
             "required": ["alert_id"]},
            self._make_alert_setter(True))

        self._register(
            "market_alert_disable",
            "Disable an alert by id without deleting it.",
            {"type": "object",
             "properties": {"alert_id": {"type": "integer"}},
             "required": ["alert_id"]},
            self._make_alert_setter(False))

        self._register(
            "market_alert_delete",
            "Delete an alert by id. Past trigger history is preserved.",
            {"type": "object",
             "properties": {"alert_id": {"type": "integer"}},
             "required": ["alert_id"]},
            self._tool_alert_delete)

    # -- executors ------------------------------------------------------------

    async def _tool_instrument_search(self, args: dict[str, Any]):
        return await asyncio.to_thread(
            self._intel.search, args.get("query", ""),
            types=args.get("types"), limit=8)

    async def _resolve_one(self, query: str):
        found = await asyncio.to_thread(
            self._intel.search, query, limit=5)
        results = found.get("results") or []
        return results[0] if results else None

    async def _tool_market_quote(self, args: dict[str, Any]):
        inst = await self._resolve_one(args.get("query", ""))
        if inst is None:
            return {"error": f"no instrument matches '{args.get('query')}'"}
        exchange, token = inst["instrument_key"].split(":", 1)
        quote = await self._msvc.get_quote(exchange, token)
        out: dict[str, Any] = {"instrument": inst}
        compact = _quote_compact(quote)
        out["quote"] = compact
        if compact is None:
            out["freshness"] = {
                "stale": True,
                "note": "no live data in market state; do not claim live"}
        else:
            out["freshness"] = {"received_at":
                                compact.get("received_ts"),
                                "stale": False}
        return out

    async def _tool_market_depth(self, args: dict[str, Any]):
        inst = await self._resolve_one(args.get("query", ""))
        if inst is None:
            return {"error": f"no instrument matches '{args.get('query')}'"}
        exchange, token = inst["instrument_key"].split(":", 1)
        depth = await self._msvc.get_depth(exchange, token)
        if depth is None:
            return {"instrument": inst, "depth": None,
                    "availability": "unavailable for this instrument"}
        from market.serialization import depth_to_dict
        return {"instrument": inst, "depth": depth_to_dict(depth),
                "availability": "ok"}

    async def _tool_futures_contracts(self, args: dict[str, Any]):
        return await asyncio.to_thread(
            self._intel.futures_contracts, args.get("underlying", ""),
            args.get("expiry"))

    async def _tool_option_expiries(self, args: dict[str, Any]):
        return await asyncio.to_thread(
            self._intel.option_expiries, args.get("underlying", ""))

    async def _tool_option_chain(self, args: dict[str, Any]):
        return await asyncio.to_thread(
            self._intel.option_chain, args.get("underlying", ""),
            expiry=args.get("expiry"),
            window=int(args.get("window") or 10))

    async def _tool_market_history(self, args: dict[str, Any]):
        # History needs a provider-backed fetch; delegate to the caller-
        # injected provider when wired (kept optional for offline tests).
        resolver = getattr(self, "_history_provider", None)
        if resolver is None:
            return {"error": "history is not configured in this deployment"}
        return await resolver(args)

    async def _tool_alert_create(self, args: dict[str, Any]):
        if self._store is None or self._intel is None:
            return {"error": "alert services not available"}
        operator = args.get("operator", "")
        field = (args.get("field") or "ltp").lower()
        if operator not in ("gt", "lt", "crosses_above", "crosses_below"):
            return {"error": "operator must be gt/lt/crosses_above/"
                             "crosses_below"}
        if field not in ("ltp", "change_percent", "volume"):
            return {"error": "field must be ltp/change_percent/volume"}
        try:
            threshold = float(args["threshold"])
        except (KeyError, TypeError, ValueError):
            return {"error": "threshold must be a number"}
        inst = await self._resolve_one(args.get("instrument_query", ""))
        if inst is None:
            return {"error": "no instrument matches query"}

        def _create():
            exchange, token = inst["instrument_key"].split(":", 1)
            return self._store.create_market_alert(
                exchange=exchange, instrument_token=token,
                tradingsymbol=inst["symbol"], field=field,
                operator=operator, threshold=threshold)

        alert = await asyncio.to_thread(_create)
        if self._engine is not None:
            try:
                self._engine.reload()
            except Exception:
                pass
        return {"status": "created", "confirmed": True, "alert": alert}

    async def _tool_alert_list(self, args: dict[str, Any]):
        if self._store is None:
            return {"error": "alert store not available"}
        alerts = await asyncio.to_thread(self._store.list_market_alerts)
        return {"status": "ok", "count": len(alerts), "alerts": alerts}

    def _make_alert_setter(self, enabled: bool) -> Callable:
        async def _setter(args: dict[str, Any]):
            if self._store is None:
                return {"error": "alert store not available"}
            try:
                alert_id = int(args["alert_id"])
            except (KeyError, TypeError, ValueError):
                return {"error": "alert_id must be an integer"}

            def _set():
                return self._store.set_alert_enabled(alert_id, enabled)

            ok = await asyncio.to_thread(_set)
            if self._engine is not None:
                try:
                    self._engine.reload()
                except Exception:
                    pass
            return {"status": "ok" if ok else "not found",
                    "alert_id": alert_id, "enabled": enabled}
        return _setter

    async def _tool_alert_delete(self, args: dict[str, Any]):
        if self._store is None:
            return {"error": "alert store not available"}
        try:
            alert_id = int(args["alert_id"])
        except (KeyError, TypeError, ValueError):
            return {"error": "alert_id must be an integer"}

        def _del():
            return self._store.delete_alert(alert_id)

        ok = await asyncio.to_thread(_del)
        if self._engine is not None:
            try:
                self._engine.reload()
            except Exception:
                pass
        return {"status": "deleted" if ok else "not found",
                "alert_id": alert_id}

    # -- access -----------------------------------------------------------------

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return self._defs

    async def execute(self, name: str, arguments: str | dict) -> dict:
        try:
            import json
            args = json.loads(arguments) if isinstance(arguments, str) \
                else dict(arguments or {})
        except Exception:
            return {"error": "invalid tool arguments JSON"}
        executor = self._executors.get(name)
        if executor is None:
            return {"error": f"unknown tool '{name}'"}
        try:
            return await executor(args)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
