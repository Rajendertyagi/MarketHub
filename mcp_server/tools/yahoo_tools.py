"""Yahoo Finance MCP tools — AI-facing research reads over yfinance.

Nine focused tools grouped by purpose (search / quote / history / company /
financials / corporate actions / holders / options / live tick). Deliberately
NOT a canonical market feed: nothing here writes quotes, feeds, alerts, or
the F&O universe. The AI calls these only when a question actually needs
Yahoo data — no prefetch on every chat turn.

Every response is a JSON-safe structured dict carrying source, resolved
symbol, data_type, timestamp, freshness, currency, exchange, data, and
warnings/errors. Raw DataFrames, Series, NaN, NaT, and infinities never cross
this boundary.

Validation errors return as data with a closed code set so the AI can react
intelligibly; the fail-closed VALIDATION_CODES set is the only path that
converts an exception into an error payload.
"""
from __future__ import annotations

import logging
from typing import Any

from mcp_server.contract import (
    TOOL_YAHOO_COMPANY,
    TOOL_YAHOO_CORPORATE_ACTIONS,
    TOOL_YAHOO_FINANCIALS,
    TOOL_YAHOO_HISTORY,
    TOOL_YAHOO_HOLDERS,
    TOOL_YAHOO_LIVE_QUOTE,
    TOOL_YAHOO_OPTIONS,
    TOOL_YAHOO_QUOTE,
    TOOL_YAHOO_SEARCH,
)
from mcp_server.registry import get_tool_description

logger = logging.getLogger(__name__)

# Closed vocabulary enforced at the MCP boundary so a bad AI-generated
# argument is rejected before any Yahoo call is attempted.
VALID_PERIODS = frozenset({
    "1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max",
})
VALID_INTERVALS = frozenset({
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h", "1d", "5d", "1wk", "1mo", "3mo",
})
VALID_STATEMENTS = frozenset({"income", "balance", "cashflow", "all"})
VALID_PERIOD_MODES = frozenset({"annual", "quarterly"})
VALID_HOLDER_TYPES = frozenset({"all", "major", "institutional", "insider"})

# The closed set of codes the MCP layer is allowed to surface as DATA.
_VALIDATION_CODES = frozenset({
    "invalid_symbol", "invalid_period", "invalid_interval",
    "invalid_statement", "invalid_holder_type", "invalid_arguments",
    "invalid_request", "symbol_not_found", "data_unavailable",
    "unavailable_for_asset", "rate_limited", "network_error",
    "unsupported_operation", "stream_unavailable",
})


def _err(code: str, reason: str, symbol: str | None = None) -> dict[str, Any]:
    return {
        "status": "error", "source": "yahoo", "code": code,
        "reason": reason, "freshness": "UNAVAILABLE", "symbol": symbol,
    }


def _clean_symbol(raw: Any) -> str | None:
    """Return a usable symbol, or None when the input is unusable."""
    s = str(raw or "").strip()
    if not s or len(s) > 64:
        return None
    return s


def _to_data(exc: BaseException, symbol: str | None = None) -> dict[str, Any]:
    """Map a service exception to a structured error payload.

    Only the closed code set maps. Anything else is logged and re-raised so
    the SDK's own error handling owns genuine crashes (never a traceback to
    the AI).
    """
    from yfinance_service.errors import YahooError
    if isinstance(exc, YahooError):
        if exc.code not in _VALIDATION_CODES:
            raise exc
        return _err(exc.code, exc.reason, symbol)
    if isinstance(exc, (ValueError, TypeError)):
        return _err("invalid_arguments", str(exc)[:200], symbol)
    logger.warning("yahoo tool unexpected failure", exc_info=True)
    raise exc


def register_yahoo_tools(mcp: Any, services: Any, **kwargs: Any) -> None:
    """Register the 9 Yahoo Finance research tools."""
    svc = getattr(services, "yahoo_service", None)

    def _require() -> Any:
        if svc is None:
            raise _NoService()
        return svc

    class _NoService(Exception):
        pass

    def _wrap(symbol: str | None = None):
        def deco(fn):
            async def _runner(*args, **kw):
                try:
                    return await fn(*args, **kw)
                except _NoService:
                    return _err("service_unavailable",
                                "Yahoo Finance research service unavailable",
                                symbol)
                except Exception as exc:
                    return _to_data(exc, symbol)
            return _runner
        return deco

    # -- search / symbol lookup ------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_SEARCH,
              description=get_tool_description(TOOL_YAHOO_SEARCH))
    @_wrap()
    async def yahoo_search_tool(query: str, limit: int = 10,
                                include_news: bool = False) -> dict[str, Any]:
        s = _require()
        q = str(query or "").strip()
        if not q:
            return _err("invalid_arguments", "query must not be empty")
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            return _err("invalid_arguments", "limit must be an integer")
        if lim < 1 or lim > 20:
            return _err("invalid_arguments", "limit must be between 1 and 20")
        return await s.search(query=q, limit=lim,
                              include_news=bool(include_news))

    # -- quote ------------------------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_QUOTE,
              description=get_tool_description(TOOL_YAHOO_QUOTE))
    @_wrap()
    async def yahoo_quote_tool(symbol: str,
                               fields: list[str] | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        return await s.quote(symbol=sym, fields=fields)

    # -- historical -------------------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_HISTORY,
              description=get_tool_description(TOOL_YAHOO_HISTORY))
    @_wrap()
    async def yahoo_history_tool(symbol: str, period: str = "1mo",
                                 interval: str = "1d",
                                 start: str | None = None,
                                 end: str | None = None,
                                 limit: int | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string",
                        symbol)
        iv = str(interval or "1d").strip().lower()
        if iv not in VALID_INTERVALS:
            return _err(
                "invalid_interval",
                f"interval must be one of: {', '.join(sorted(VALID_INTERVALS))}",
                sym)
        has_range = bool(start or end)
        per: str | None = None
        if not has_range:
            per = str(period or "1mo").strip().lower()
            if per not in VALID_PERIODS:
                return _err(
                    "invalid_period",
                    f"period must be one of: {', '.join(sorted(VALID_PERIODS))}",
                    sym)
        elif period and str(period) not in ("1mo", "", "None"):
            return _err("invalid_arguments",
                        "use either period or start/end, not both", sym)
        return await s.history(symbol=sym, period=per, interval=iv,
                               start=start, end=end, limit=limit)

    # -- company ----------------------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_COMPANY,
              description=get_tool_description(TOOL_YAHOO_COMPANY))
    @_wrap()
    async def yahoo_company_tool(symbol: str) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        return await s.company(symbol=sym)

    # -- financial statements ---------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_FINANCIALS,
              description=get_tool_description(TOOL_YAHOO_FINANCIALS))
    @_wrap()
    async def yahoo_financials_tool(symbol: str, period: str = "annual",
                                    statement: str = "income",
                                    limit: int | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        mode = str(period or "annual").strip().lower()
        if mode not in VALID_PERIOD_MODES:
            return _err(
                "invalid_period",
                f"period must be exactly one of: {', '.join(sorted(VALID_PERIOD_MODES))}",
                sym)
        what = str(statement or "income").strip().lower()
        if what not in VALID_STATEMENTS:
            return _err(
                "invalid_statement",
                f"statement must be one of: {', '.join(sorted(VALID_STATEMENTS))}",
                sym)
        return await s.financials(symbol=sym, period=mode,
                                  statement=what, limit=limit)

    # -- corporate actions -------------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_CORPORATE_ACTIONS,
              description=get_tool_description(TOOL_YAHOO_CORPORATE_ACTIONS))
    @_wrap()
    async def yahoo_corporate_actions_tool(
            symbol: str, limit: int | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        return await s.corporate_actions(symbol=sym, limit=limit)

    # -- holders / analysts ------------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_HOLDERS,
              description=get_tool_description(TOOL_YAHOO_HOLDERS))
    @_wrap()
    async def yahoo_holders_tool(symbol: str, holder_type: str = "all",
                                 limit: int | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        kind = str(holder_type or "all").strip().lower()
        if kind not in VALID_HOLDER_TYPES:
            return _err(
                "invalid_holder_type",
                f"holder_type must be one of: {', '.join(sorted(VALID_HOLDER_TYPES))}",
                sym)
        return await s.holders(symbol=sym, holder_type=kind, limit=limit)

    # -- options ------------------------------------------------------------
    @mcp.tool(name=TOOL_YAHOO_OPTIONS,
              description=get_tool_description(TOOL_YAHOO_OPTIONS))
    @_wrap()
    async def yahoo_options_tool(symbol: str, date: str | None = None,
                                 limit: int | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        return await s.options(symbol=sym, date=date, limit=limit)

    # -- live quote (bounded stream) ---------------------------------------
    @mcp.tool(name=TOOL_YAHOO_LIVE_QUOTE,
              description=get_tool_description(TOOL_YAHOO_LIVE_QUOTE))
    @_wrap()
    async def yahoo_live_quote_tool(symbol: str,
                                    timeout: float | None = None) -> dict[str, Any]:
        s = _require()
        sym = _clean_symbol(symbol)
        if sym is None:
            return _err("invalid_symbol", "symbol must be a non-empty string")
        return await s.live_quote(symbol=sym, timeout=timeout)
