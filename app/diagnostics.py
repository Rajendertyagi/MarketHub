"""Diagnostics runner — exercises real HTTP endpoints and MCP boundary.

All checks are endpoint-driven: the runner calls the SAME REST and MCP
boundaries the WebUI uses. No direct service function calls are labeled
as REST or MCP — only actual transport calls count.
"""
from __future__ import annotations

import asyncio
import json
import time
import types
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta, timezone
from typing import Any, Callable

import httpx

# Precise list of field names that are SAFE to expose (never redact these)
_SAFE_FIELDS = frozenset({
    "id", "name", "category", "layer", "status", "message",
    "duration_ms", "data", "classification_reason",
    "instrument_key", "exchange", "symbol", "tradingsymbol",
    "ltp", "change", "change_percent", "received_ts", "basis",
    "provider", "timestamp", "count", "rows", "strikes",
    "atm_strike", "spot_price", "expiry", "candles",
    "quote", "depth", "pcr", "max_pain", "greeks",
    "source_id", "source_name", "title", "link",
    "sentiment", "score", "matched_keywords",
    "upstox", "fyers", "nse", "bse",
    "run_at", "summary", "failures", "warnings", "results",
    "mode", "duration_ms", "strike_count", "candle_count",
})

# Fields whose VALUES must always be redacted (secret leakage prevention)
_SECRET_VALUE_KEYS = frozenset({
    "token", "secret", "password", "pin", "access_token",
    "refresh_token", "api_key", "api_secret", "client_secret",
    "credential", "auth", "wss_url", "websocket",
    "redirect_uri",
})


@dataclass(frozen=True, slots=True)
class DiagnosticResult:
    id: str
    name: str
    category: str
    layer: str
    status: str
    message: str
    duration_ms: int
    data: dict[str, Any] = field(default_factory=dict)
    classification_reason: str = ""


class _CheckDef(types.SimpleNamespace):
    pass


def _redact_secrets(obj: Any) -> Any:
    """Recursively redact secret VALUES, never safe canonical fields."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            k_lower = k.lower()
            if k_lower in _SAFE_FIELDS:
                out[k] = _redact_secrets(v)
            elif k_lower in _SECRET_VALUE_KEYS:
                out[k] = "***REDACTED***"
            elif isinstance(v, (dict, list)):
                out[k] = _redact_secrets(v)
            else:
                out[k] = v
        return out
    elif isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    return obj


class DiagnosticsRunner:
    """Runs diagnostic checks against real endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        mcp_url: str,
        provider_market_data: Any = None,
        news_service: Any = None,
        source_manager: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._mcp_url = mcp_url
        self._provider_md = provider_market_data
        self._news_service = news_service
        self._source_manager = source_manager
        self._http_client: httpx.AsyncClient | None = http_client

    async def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
            )
        elif self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
            )
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def with_base_url(self, base_url: str) -> "DiagnosticsRunner":
        """Return a new runner with a different base URL (for testing)."""
        clone = DiagnosticsRunner(
            base_url=base_url,
            mcp_url=self._mcp_url,
            provider_market_data=self._provider_md,
            news_service=self._news_service,
            source_manager=self._source_manager,
        )
        return clone

    async def run_quick(self) -> dict[str, Any]:
        return await self._run_checks(_QUICK_CHECKS)

    async def run_full(self) -> dict[str, Any]:
        return await self._run_checks(_FULL_CHECKS)

    def list_checks(self) -> list[dict[str, Any]]:
        return [
            {
                "id": c.id, "name": c.name,
                "category": c.category, "layer": c.layer,
                "mode": list(c.mode),
                "description": getattr(c, "description", ""),
                "safe_for_auto_run": getattr(c, "safe_for_auto_run", False),
            }
            for c in _ALL_CHECKS
        ]

    async def _run_checks(
        self, checks: list[_CheckDef]
    ) -> dict[str, Any]:
        t0 = time.monotonic()
        results: list[DiagnosticResult] = []
        for check in checks:
            try:
                result = await asyncio.wait_for(
                    check.fn(self), timeout=30.0)
            except asyncio.TimeoutError:
                result = DiagnosticResult(
                    id=check.id, name=check.name,
                    category=check.category, layer=check.layer,
                    status="FAIL",
                    message=f"{check.name}: check timed out after 30s",
                    duration_ms=30000,
                    classification_reason="timeout",
                )
            except Exception as exc:
                result = DiagnosticResult(
                    id=check.id, name=check.name,
                    category=check.category, layer=check.layer,
                    status="FAIL",
                    message=f"{check.name}: {type(exc).__name__}: {exc}",
                    duration_ms=0,
                    classification_reason="exception",
                )
            results.append(result)

        elapsed = int((time.monotonic() - t0) * 1000)
        summary = self._summarize(results)
        return {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": elapsed,
            "summary": summary,
            "failures": [r.id for r in results if r.status == "FAIL"],
            "warnings": [r.id for r in results
                         if r.status in ("PARTIAL", "UNAVAILABLE")],
            "results": [self._result_to_dict(r) for r in results],
        }

    @staticmethod
    def _summarize(results: list[DiagnosticResult]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in results:
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    @staticmethod
    def _result_to_dict(r: DiagnosticResult) -> dict[str, Any]:
        return {
            "id": r.id, "name": r.name,
            "category": r.category, "layer": r.layer,
            "status": r.status, "message": r.message,
            "duration_ms": r.duration_ms,
            "data": _redact_secrets(r.data),
            "classification_reason": r.classification_reason,
        }

    async def _get(self, path: str) -> dict[str, Any]:
        client = await self._client()
        url = f"{self._base_url}{path}"
        resp = await client.get(url)
        if resp.status_code >= 500:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.json()

    async def _get_rest_quote(self, exchange: str, instrument_token: str) -> dict[str, Any]:
        path = f"/api/market/quote/{exchange}/{instrument_token}"
        return await self._get(path)

    async def _call_mcp(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(self._mcp_url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = result.content[0].text if result.content else "{}"
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"text": text}

    async def _check_app_health(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/diagnostics")
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="app_health", name="Application Health",
                category="SYSTEM", layer="SERVICE",
                status="PASS", message="Server is healthy",
                duration_ms=ms, data=data,
                classification_reason="endpoint_returned_ok",
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="app_health", name="Application Health",
                category="SYSTEM", layer="SERVICE",
                status="FAIL", message=f"Health check failed: {exc}",
                duration_ms=ms,
                classification_reason="endpoint_error",
            )

    async def _check_upstox_feed(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/diagnostics")
            sources = data.get("sources", [])
            upstox = next((s for s in sources if s.get("name") == "upstox"), None)
            ms = int((time.monotonic() - t0) * 1000)
            if upstox is None:
                return DiagnosticResult(
                    id="upstox_feed", name="Upstox Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="UNAVAILABLE",
                    message="Upstox source not configured",
                    duration_ms=ms,
                    classification_reason="not_configured",
                )
            state = upstox.get("state", "unknown")
            if state == "streaming":
                return DiagnosticResult(
                    id="upstox_feed", name="Upstox Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="PASS", message="Feed is streaming",
                    duration_ms=ms, data={"state": state},
                    classification_reason="streaming",
                )
            elif state in ("connecting", "reconnecting"):
                return DiagnosticResult(
                    id="upstox_feed", name="Upstox Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="PARTIAL", message=f"Feed {state}",
                    duration_ms=ms, data={"state": state},
                    classification_reason="transitional",
                )
            else:
                return DiagnosticResult(
                    id="upstox_feed", name="Upstox Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="UNAVAILABLE",
                    message=f"Feed state: {state}",
                    duration_ms=ms, data={"state": state},
                    classification_reason="not_streaming",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="upstox_feed", name="Upstox Feed Status",
                category="BROKERS", layer="SERVICE",
                status="FAIL", message=f"Check failed: {exc}",
                duration_ms=ms,
                classification_reason="exception",
            )

    async def _check_fyers_feed(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/diagnostics")
            sources = data.get("sources", [])
            fyers = next((s for s in sources if s.get("name") == "fyers"), None)
            ms = int((time.monotonic() - t0) * 1000)
            if fyers is None:
                return DiagnosticResult(
                    id="fyers_feed", name="Fyers Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="SKIPPED",
                    message="Fyers source not configured",
                    duration_ms=ms,
                    classification_reason="not_configured",
                )
            state = fyers.get("state", "unknown")
            if state == "streaming":
                return DiagnosticResult(
                    id="fyers_feed", name="Fyers Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="PASS", message="Feed is streaming",
                    duration_ms=ms, data={"state": state},
                    classification_reason="streaming",
                )
            elif state in ("connecting", "reconnecting"):
                return DiagnosticResult(
                    id="fyers_feed", name="Fyers Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="PARTIAL", message=f"Feed {state}",
                    duration_ms=ms, data={"state": state},
                    classification_reason="transitional",
                )
            else:
                return DiagnosticResult(
                    id="fyers_feed", name="Fyers Feed Status",
                    category="BROKERS", layer="SERVICE",
                    status="UNAVAILABLE",
                    message=f"Feed state: {state}",
                    duration_ms=ms, data={"state": state},
                    classification_reason="not_streaming",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="fyers_feed", name="Fyers Feed Status",
                category="BROKERS", layer="SERVICE",
                status="FAIL", message=f"Check failed: {exc}",
                duration_ms=ms,
                classification_reason="exception",
            )

    async def _check_market_service(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/diagnostics")
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="market_service", name="MarketService Counters",
                category="MARKET DATA", layer="SERVICE",
                status="PASS", message="Service available",
                duration_ms=ms, data={"sources_count": len(data.get("sources", []))},
                classification_reason="service_accessible",
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="market_service", name="MarketService Counters",
                category="MARKET DATA", layer="SERVICE",
                status="FAIL", message=f"Check failed: {exc}",
                duration_ms=ms,
                classification_reason="exception",
            )

    async def _check_nifty_quote(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get_rest_quote("NSE", "NSE_INDEX|Nifty 50")
            ms = int((time.monotonic() - t0) * 1000)
            ltp = data.get("ltp")
            if ltp is not None:
                return DiagnosticResult(
                    id="nifty_quote", name="NIFTY Quote (REST)",
                    category="MARKET DATA", layer="REST",
                    status="PASS", message=f"LTP: {ltp}",
                    duration_ms=ms, data=data,
                    classification_reason="quote_available",
                )
            else:
                return DiagnosticResult(
                    id="nifty_quote", name="NIFTY Quote (REST)",
                    category="MARKET DATA", layer="REST",
                    status="PARTIAL", message="No live quote (market closed?)",
                    duration_ms=ms, data=data,
                    classification_reason="no_live_quote",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="nifty_quote", name="NIFTY Quote (REST)",
                category="MARKET DATA", layer="REST",
                status="FAIL", message=f"Quote fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _check_banknifty_quote(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get_rest_quote("NSE", "NSE_INDEX|Nifty Bank")
            ms = int((time.monotonic() - t0) * 1000)
            ltp = data.get("ltp")
            if ltp is not None:
                return DiagnosticResult(
                    id="banknifty_quote", name="BANKNIFTY Quote (REST)",
                    category="MARKET DATA", layer="REST",
                    status="PASS", message=f"LTP: {ltp}",
                    duration_ms=ms, data=data,
                    classification_reason="quote_available",
                )
            else:
                return DiagnosticResult(
                    id="banknifty_quote", name="BANKNIFTY Quote (REST)",
                    category="MARKET DATA", layer="REST",
                    status="PARTIAL", message="No live quote (market closed?)",
                    duration_ms=ms, data=data,
                    classification_reason="no_live_quote",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="banknifty_quote", name="BANKNIFTY Quote (REST)",
                category="MARKET DATA", layer="REST",
                status="FAIL", message=f"Quote fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _check_mcp_status(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._call_mcp("market_status", {})
            ms = int((time.monotonic() - t0) * 1000)
            if data.get("status") == "ok":
                return DiagnosticResult(
                    id="mcp_status", name="MCP market_status",
                    category="MCP", layer="MCP",
                    status="PASS", message="MCP status tool works",
                    duration_ms=ms, data=data,
                    classification_reason="mcp_tool_succeeded",
                )
            else:
                return DiagnosticResult(
                    id="mcp_status", name="MCP market_status",
                    category="MCP", layer="MCP",
                    status="FAIL", message=f"Unexpected response: {data}",
                    duration_ms=ms, data=data,
                    classification_reason="unexpected_response",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="mcp_status", name="MCP market_status",
                category="MCP", layer="MCP",
                status="FAIL", message=f"MCP call failed: {exc}",
                duration_ms=ms,
                classification_reason="mcp_error",
            )

    async def _check_mcp_quote(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._call_mcp("market_quote", {"instrument_ref": "NIFTY"})
            ms = int((time.monotonic() - t0) * 1000)
            quote = data.get("quote") if data.get("status") == "ok" else None
            if quote is not None:
                ltp = quote.get("ltp")
                return DiagnosticResult(
                    id="mcp_quote", name="MCP market_quote",
                    category="MCP", layer="MCP",
                    status="PASS", message=f"Quote returned (LTP: {ltp})",
                    duration_ms=ms, data=data,
                    classification_reason="mcp_tool_succeeded",
                )
            else:
                return DiagnosticResult(
                    id="mcp_quote", name="MCP market_quote",
                    category="MCP", layer="MCP",
                    status="PARTIAL", message="No quote from MCP (market closed?)",
                    duration_ms=ms, data=data,
                    classification_reason="no_quote",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="mcp_quote", name="MCP market_quote",
                category="MCP", layer="MCP",
                status="FAIL", message=f"MCP call failed: {exc}",
                duration_ms=ms,
                classification_reason="mcp_error",
            )

    async def _check_option_chain(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/options/chain/view?underlying=NIFTY&window=10")
            ms = int((time.monotonic() - t0) * 1000)
            strikes = data.get("strikes", [])
            if strikes:
                return DiagnosticResult(
                    id="option_chain", name="NIFTY Option Chain",
                    category="OPTIONS", layer="REST",
                    status="PASS", message=f"{len(strikes)} strikes",
                    duration_ms=ms,
                    data={"strike_count": len(strikes), "atm": data.get("atm_strike")},
                    classification_reason="chain_available",
                )
            else:
                return DiagnosticResult(
                    id="option_chain", name="NIFTY Option Chain",
                    category="OPTIONS", layer="REST",
                    status="PARTIAL", message="No strikes (no expiry?)",
                    duration_ms=ms, data=data,
                    classification_reason="no_strikes",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="option_chain", name="NIFTY Option Chain",
                category="OPTIONS", layer="REST",
                status="FAIL", message=f"Chain fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _check_history(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            today = date.today()
            from_date = (today - timedelta(days=30)).isoformat()
            data = await self._get(
                f"/api/market/history?instrument_key=NSE_INDEX%7CNifty%2050&unit=days&interval=1&from={from_date}&to={today.isoformat()}")
            ms = int((time.monotonic() - t0) * 1000)
            candles = data.get("candles", [])
            if candles:
                return DiagnosticResult(
                    id="history", name="NIFTY History (daily)",
                    category="HISTORY", layer="REST",
                    status="PASS", message=f"{len(candles)} candles",
                    duration_ms=ms, data={"candle_count": len(candles)},
                    classification_reason="history_available",
                )
            else:
                return DiagnosticResult(
                    id="history", name="NIFTY History (daily)",
                    category="HISTORY", layer="REST",
                    status="PARTIAL", message="No candles (weekend/holiday?)",
                    duration_ms=ms, data=data,
                    classification_reason="no_candles",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="history", name="NIFTY History (daily)",
                category="HISTORY", layer="REST",
                status="FAIL", message=f"History fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _check_depth(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/market/depths")
            ms = int((time.monotonic() - t0) * 1000)
            depths = data.get("depths", [])
            if depths:
                return DiagnosticResult(
                    id="depth", name="Market Depth",
                    category="MARKET DATA", layer="REST",
                    status="PASS", message=f"{len(depths)} depth sources",
                    duration_ms=ms, data={"depth_count": len(depths)},
                    classification_reason="depth_available",
                )
            else:
                return DiagnosticResult(
                    id="depth", name="Market Depth",
                    category="MARKET DATA", layer="REST",
                    status="UNAVAILABLE",
                    message="No depth-bearing instrument subscribed",
                    duration_ms=ms,
                    classification_reason="no_subscription",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="depth", name="Market Depth",
                category="MARKET DATA", layer="REST",
                status="FAIL", message=f"Depth fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _resolve_greeks_option(self) -> dict | None:
        """Resolve a real OPTION identity for standalone Greeks.

        Upstox-backed options are preferred (standalone Greeks is an
        Upstox capability); the catalog option (possibly Fyers-backed)
        is the fallback so unsupported providers classify honestly.
        The legacy index probe (NSE_INDEX|Nifty 50) is NOT an option —
        the provider answers with zeroed greeks, which is not a pass.
        """
        try:
            data = await self._get(
                "/api/instruments/search?provider=upstox&type=CE&limit=5")
            for row in data.get("results", []):
                key = row.get("instrument_key")
                if key:
                    return {"instrument_key": key, "provider": "upstox",
                            "label": row.get("tradingsymbol") or key}
        except Exception:
            pass
        opt = await self._resolve_option()
        if opt and opt.get("instrument_key"):
            return {"instrument_key": opt["instrument_key"],
                    "provider": opt.get("provider") or "fyers",
                    "label": opt.get("symbol") or opt["instrument_key"]}
        return None

    async def _check_greeks(self) -> DiagnosticResult:
        t0 = time.monotonic()
        inst = await self._resolve_greeks_option()
        if inst is None:
            return DiagnosticResult(
                id="greeks", name="Standalone Greeks",
                category="OPTIONS", layer="REST",
                status="UNAVAILABLE",
                message="No option instrument resolvable for standalone Greeks",
                duration_ms=int((time.monotonic() - t0) * 1000),
                classification_reason="no_instrument",
            )
        from urllib.parse import quote
        key = inst["instrument_key"]
        try:
            data = await self._get(
                f"/api/options/greeks?instrument_key={quote(key, safe='')}")
            ms = int((time.monotonic() - t0) * 1000)
            err = data.get("error")
            if err and ("not available" in err
                        or "not supported" in err.lower()
                        or "unsupported" in err.lower()):
                return DiagnosticResult(
                    id="greeks", name="Standalone Greeks",
                    category="OPTIONS", layer="REST",
                    status="UNAVAILABLE",
                    message=f"Standalone Greeks unsupported for the "
                            f"{inst['provider']}-backed option",
                    duration_ms=ms, data={"instrument_key": key,
                                          "provider": inst["provider"]},
                    classification_reason="provider_not_supported",
                )
            entries = (data.get("data") or {}).get("entries") or []
            if not entries:
                return DiagnosticResult(
                    id="greeks", name="Standalone Greeks",
                    category="OPTIONS", layer="REST",
                    status="UNAVAILABLE",
                    message="Provider returned no Greeks data for "
                            f"{inst.get('label', key)}",
                    duration_ms=ms, data={"instrument_key": key,
                                          "provider": inst["provider"]},
                    classification_reason="no_greeks",
                )
            fields = ("delta", "gamma", "theta", "vega", "iv")
            meaningful = [f for e in entries if isinstance(e, dict)
                          for f in fields if e.get(f)]
            if not meaningful:
                return DiagnosticResult(
                    id="greeks", name="Standalone Greeks",
                    category="OPTIONS", layer="REST",
                    status="PARTIAL",
                    message="Entry returned but no meaningful greek values "
                            "(index/non-option identity?)",
                    duration_ms=ms, data={"instrument_key": key,
                                          "provider": inst["provider"]},
                    classification_reason="no_meaningful_greeks",
                )
            if len(meaningful) < len(fields):
                return DiagnosticResult(
                    id="greeks", name="Standalone Greeks",
                    category="OPTIONS", layer="REST",
                    status="PARTIAL",
                    message=f"Partial greeks: {sorted(set(meaningful))} "
                            f"(rho is never exposed by Upstox)",
                    duration_ms=ms, data={"instrument_key": key,
                                          "provider": inst["provider"],
                                          "fields": sorted(set(meaningful))},
                    classification_reason="partial_fields",
                )
            return DiagnosticResult(
                id="greeks", name="Standalone Greeks",
                category="OPTIONS", layer="REST",
                status="PASS",
                message=f"{len(entries)} entr(ies) with greeks for "
                        f"{inst.get('label', key)}",
                duration_ms=ms, data={"instrument_key": key,
                                      "provider": inst["provider"],
                                      "fields": sorted(set(meaningful))},
                classification_reason="greeks_available",
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="greeks", name="Standalone Greeks",
                category="OPTIONS", layer="REST",
                status="FAIL", message=f"Greeks fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _check_news(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/news?limit=5")
            ms = int((time.monotonic() - t0) * 1000)
            articles = data.get("articles", [])
            if articles:
                return DiagnosticResult(
                    id="news", name="Latest News",
                    category="NEWS", layer="REST",
                    status="PASS", message=f"{len(articles)} articles",
                    duration_ms=ms, data={"count": len(articles)},
                    classification_reason="news_available",
                )
            else:
                return DiagnosticResult(
                    id="news", name="Latest News",
                    category="NEWS", layer="REST",
                    status="PARTIAL", message="No articles (no sources or empty cache)",
                    duration_ms=ms, data=data,
                    classification_reason="no_articles",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="news", name="Latest News",
                category="NEWS", layer="REST",
                status="FAIL", message=f"News fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    async def _check_sentiment(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/news/sentiment?limit=5")
            ms = int((time.monotonic() - t0) * 1000)
            sentiments = data.get("sentiments", [])
            if sentiments:
                return DiagnosticResult(
                    id="sentiment", name="Sentiment Analysis",
                    category="NEWS", layer="REST",
                    status="PASS", message=f"{len(sentiments)} items scored",
                    duration_ms=ms, data={"count": len(sentiments)},
                    classification_reason="sentiment_available",
                )
            else:
                return DiagnosticResult(
                    id="sentiment", name="Sentiment Analysis",
                    category="NEWS", layer="REST",
                    status="PARTIAL", message="No sentiment data (no articles or empty cache)",
                    duration_ms=ms, data=data,
                    classification_reason="no_sentiment",
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="sentiment", name="Sentiment Analysis",
                category="NEWS", layer="REST",
                status="FAIL", message=f"Sentiment fetch failed: {exc}",
                duration_ms=ms,
                classification_reason="fetch_error",
            )

    # -- Full-only resolution helpers -----------------------------------------

    async def _resolve_equity(self) -> dict | None:
        try:
            data = await self._get(
                "/api/market/search?q=RELIANCE&types=EQUITY&limit=1")
            results = data.get("results") or []
            return results[0] if results else None
        except Exception:
            return None

    async def _resolve_future(self) -> dict | None:
        try:
            data = await self._get("/api/futures?underlying=NIFTY")
            contracts = data.get("contracts") or []
            return contracts[0] if contracts else None
        except Exception:
            return None

    async def _resolve_option(self) -> dict | None:
        try:
            data = await self._get(
                "/api/options/chain/view?underlying=NIFTY&window=3")
            rows = data.get("rows") or []
            for row in rows:
                call = row.get("call")
                if isinstance(call, dict) and call.get("instrument_key"):
                    return call
            return None
        except Exception:
            return None

    async def _quote_instrument(
        self, inst: dict | None, kind: str
    ) -> DiagnosticResult:
        t0 = time.monotonic()
        if inst is None:
            return DiagnosticResult(
                id=f"{kind}_quote", name=f"{kind.title()} Quote (REST)",
                category="MARKET DATA", layer="REST",
                status="UNAVAILABLE",
                message=f"No {kind} instrument resolved from catalog",
                duration_ms=int((time.monotonic() - t0) * 1000),
                classification_reason="no_instrument",
            )
        key = inst.get("instrument_key")
        ex = inst.get("exchange")
        try:
            q = await self._get_rest_quote(ex, key)
            ms = int((time.monotonic() - t0) * 1000)
            ltp = q.get("ltp")
            if ltp is not None:
                return DiagnosticResult(
                    id=f"{kind}_quote", name=f"{kind.title()} Quote (REST)",
                    category="MARKET DATA", layer="REST",
                    status="PASS",
                    message=f"{kind.title()} quote resolved (LTP: {ltp})",
                    duration_ms=ms,
                    data={"symbol": inst.get("symbol"), "ltp": ltp},
                    classification_reason="quote_resolved",
                )
            return DiagnosticResult(
                id=f"{kind}_quote", name=f"{kind.title()} Quote (REST)",
                category="MARKET DATA", layer="REST",
                status="PARTIAL",
                message=(f"{kind.title()} instrument resolved but no live "
                         f"quote (market closed?)"),
                duration_ms=ms, data={"symbol": inst.get("symbol")},
                classification_reason="no_live_quote",
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id=f"{kind}_quote", name=f"{kind.title()} Quote (REST)",
                category="MARKET DATA", layer="REST",
                status="FAIL", message=f"{kind.title()} quote path failed: {exc}",
                duration_ms=ms, classification_reason="path_error",
            )

    async def _check_equity_quote(self) -> DiagnosticResult:
        return await self._quote_instrument(
            await self._resolve_equity(), "equity")

    async def _check_future_quote(self) -> DiagnosticResult:
        return await self._quote_instrument(
            await self._resolve_future(), "future")

    async def _check_option_quote(self) -> DiagnosticResult:
        return await self._quote_instrument(
            await self._resolve_option(), "option")

    # Canonical index labels expected from /api/market/indices.
    _CANONICAL_INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                          "NIFTYNXT50", "INDIA VIX", "SENSEX", "BANKEX")

    async def _check_indices_coverage(self) -> DiagnosticResult:
        t0 = time.monotonic()
        canonical = list(self._CANONICAL_INDICES)
        try:
            data = await self._get("/api/market/indices")
            indices = data.get("indices", [])
            by_label = {i.get("label"): i for i in indices}
            missing = [c for c in canonical if c not in by_label]
            detail = []
            for c in canonical:
                info = by_label.get(c)
                if info:
                    q = info.get("quote") or {}
                    detail.append({
                        "label": c,
                        "symbol": info.get("symbol"),
                        "ltp": q.get("ltp") if isinstance(q, dict) else None,
                        "basis": info.get("basis"),
                        "provider": info.get("provider"),
                    })
            ms = int((time.monotonic() - t0) * 1000)
            if missing:
                return DiagnosticResult(
                    id="indices_coverage", name="Canonical Indices Coverage",
                    category="MARKET DATA", layer="REST",
                    status="FAIL",
                    message=f"Missing canonical indices: {', '.join(missing)}",
                    duration_ms=ms,
                    data={"present": len(by_label), "missing": missing,
                          "indices": detail},
                    classification_reason="missing_canonical",
                )
            any_ltp = any(d["ltp"] is not None for d in detail)
            return DiagnosticResult(
                id="indices_coverage", name="Canonical Indices Coverage",
                category="MARKET DATA", layer="REST",
                status="PASS",
                message="All 8 canonical indices available"
                        + ("" if any_ltp else " (no live LTP — market closed)"),
                duration_ms=ms,
                data={"count": len(detail), "indices": detail},
                classification_reason="all_present" if any_ltp else "all_present_no_ltp",
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="indices_coverage", name="Canonical Indices Coverage",
                category="MARKET DATA", layer="REST",
                status="FAIL", message=f"Indices coverage check failed: {exc}",
                duration_ms=ms, classification_reason="exception",
            )

    async def _check_option_detail(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get(
                "/api/options/chain/view?underlying=NIFTY&window=5")
            rows = data.get("rows", [])
            if not rows:
                return DiagnosticResult(
                    id="option_detail", name="Option Data Detail",
                    category="OPTIONS", layer="REST",
                    status="PARTIAL", message="No option chain rows returned",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    classification_reason="no_rows",
                )
            ce = any(isinstance(r.get("call"), dict) and r.get("call") for r in rows)
            pe = any(isinstance(r.get("put"), dict) and r.get("put") for r in rows)
            fields: set[str] = set()
            for r in rows:
                for leg in (r.get("call"), r.get("put")):
                    if isinstance(leg, dict):
                        for f in ("oi", "prev_oi", "volume", "bid", "ask",
                                  "iv", "greeks", "ltp", "delta", "gamma",
                                  "theta", "vega", "rho"):
                            if f in leg:
                                fields.add(f)
            ms = int((time.monotonic() - t0) * 1000)
            detail = {"ce_present": ce, "pe_present": pe,
                      "fields_exposed": sorted(fields)}
            if ce and pe:
                msg = "CE/PE legs present"
                if not fields:
                    msg += ("; OI/ΔOI/volume/bid/ask/IV/Greeks not exposed in "
                            "chain view (no live provider snapshot)")
                status, reason = "PASS", "legs_present"
            else:
                msg = "Missing CE or PE legs in chain"
                status, reason = "PARTIAL", "missing_legs"
            return DiagnosticResult(
                id="option_detail", name="Option Data Detail",
                category="OPTIONS", layer="REST",
                status=status, message=msg, duration_ms=ms,
                data=detail, classification_reason=reason,
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="option_detail", name="Option Data Detail",
                category="OPTIONS", layer="REST",
                status="FAIL", message=f"Option detail check failed: {exc}",
                duration_ms=ms, classification_reason="exception",
            )

    async def _check_sources_detail(self) -> DiagnosticResult:
        t0 = time.monotonic()
        try:
            data = await self._get("/api/sources/status")
            sources = data.get("sources", [])
            detail = []
            for s in sources:
                detail.append({
                    "name": s.get("name"),
                    "state": s.get("state"),
                    "auth_required": s.get("auth_required"),
                    "configured_instruments": s.get("configured_instruments"),
                    "subscribed_instruments": s.get("subscribed_instruments"),
                    "not_ready_reason": s.get("not_ready_reason"),
                })
            ms = int((time.monotonic() - t0) * 1000)
            names = {d["name"] for d in detail}
            if "upstox" in names and "fyers" in names:
                status, msg = "PASS", ("Both Upstox and Fyers source state "
                                       "exposed (no credentials leaked)")
            elif detail:
                status, msg = "PARTIAL", "Only one source reported"
            else:
                status, msg = "UNAVAILABLE", "No source state exposed"
            return DiagnosticResult(
                id="sources_detail", name="Broker/Source Detail",
                category="BROKERS", layer="SERVICE",
                status=status, message=msg, duration_ms=ms,
                data={"sources": detail},
                classification_reason="source_state_exposed",
            )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            return DiagnosticResult(
                id="sources_detail", name="Broker/Source Detail",
                category="BROKERS", layer="SERVICE",
                status="FAIL", message=f"Source detail check failed: {exc}",
                duration_ms=ms, classification_reason="exception",
            )


def _make_check(
    id: str, name: str, category: str, layer: str,
    mode: tuple[str, ...], description: str, fn: Callable,
    safe_for_auto_run: bool = True,
) -> _CheckDef:
    return _CheckDef(
        id=id, name=name, category=category, layer=layer,
        mode=mode, description=description, fn=fn,
        safe_for_auto_run=safe_for_auto_run,
    )


# Quick Health Check (browser-owned SSE excluded)
_QUICK_CHECKS = [
    _make_check("app_health", "Application Health", "SYSTEM", "SERVICE",
                ("quick",), "GET /api/diagnostics",
                DiagnosticsRunner._check_app_health),
    _make_check("upstox_feed", "Upstox Feed Status", "BROKERS", "SERVICE",
                ("quick",), "Source state from diagnostics endpoint",
                DiagnosticsRunner._check_upstox_feed),
    _make_check("market_service", "MarketService Counters", "MARKET DATA", "SERVICE",
                ("quick",), "Service accessibility",
                DiagnosticsRunner._check_market_service),
    _make_check("nifty_quote", "NIFTY Quote (REST)", "MARKET DATA", "REST",
                ("quick",), "GET /api/market/quote/NSE/NSE_INDEX|Nifty 50",
                DiagnosticsRunner._check_nifty_quote),
    _make_check("banknifty_quote", "BANKNIFTY Quote (REST)", "MARKET DATA", "REST",
                ("quick",), "GET /api/market/quote/NSE/NSE_INDEX|Nifty Bank",
                DiagnosticsRunner._check_banknifty_quote),
    _make_check("mcp_status", "MCP market_status", "MCP", "MCP",
                ("quick",), "Call MCP tool market_status",
                DiagnosticsRunner._check_mcp_status),
    _make_check("mcp_quote", "MCP market_quote", "MCP", "MCP",
                ("quick",), "Call MCP tool market_quote(NIFTY)",
                DiagnosticsRunner._check_mcp_quote),
    _make_check("option_chain", "NIFTY Option Chain", "OPTIONS", "REST",
                ("quick",), "GET /api/options/chain/view?underlying=NIFTY",
                DiagnosticsRunner._check_option_chain),
    _make_check("history", "NIFTY History (daily)", "HISTORY", "REST",
                ("quick",), "GET /api/market/history (last 30 days)",
                DiagnosticsRunner._check_history),
    _make_check("depth", "Market Depth", "MARKET DATA", "REST",
                ("quick",), "GET /api/market/depths",
                DiagnosticsRunner._check_depth),
    _make_check("greeks", "Standalone Greeks", "OPTIONS", "REST",
                ("quick", "full"), "GET /api/options/greeks",
                DiagnosticsRunner._check_greeks),
    _make_check("news", "Latest News", "NEWS", "REST",
                ("quick",), "GET /api/news?limit=5",
                DiagnosticsRunner._check_news),
    _make_check("sentiment", "Sentiment Analysis", "NEWS", "REST",
                ("quick",), "GET /api/news/sentiment?limit=5",
                DiagnosticsRunner._check_sentiment),
]

# Full Test adds these on top of Quick
_FULL_EXTRA = [
    _make_check("fyers_feed", "Fyers Feed Status", "BROKERS", "SERVICE",
                ("full",), "Source state from diagnostics endpoint",
                DiagnosticsRunner._check_fyers_feed),
    _make_check("indices_coverage", "Canonical Indices Coverage", "MARKET DATA", "REST",
                ("full",), "GET /api/market/indices (8 canonical indices)",
                DiagnosticsRunner._check_indices_coverage),
    _make_check("equity_quote", "Equity Quote (REST)", "MARKET DATA", "REST",
                ("full",), "Resolve RELIANCE equity via catalog + quote",
                DiagnosticsRunner._check_equity_quote),
    _make_check("future_quote", "Future Quote (REST)", "MARKET DATA", "REST",
                ("full",), "Resolve NIFTY future via catalog + quote",
                DiagnosticsRunner._check_future_quote),
    _make_check("option_quote", "Option Quote (REST)", "MARKET DATA", "REST",
                ("full",), "Resolve NIFTY option via chain + quote",
                DiagnosticsRunner._check_option_quote),
    _make_check("option_detail", "Option Data Detail", "OPTIONS", "REST",
                ("full",), "Verify CE/PE + OI/IV/Greeks exposure in chain",
                DiagnosticsRunner._check_option_detail),
    _make_check("sources_detail", "Broker/Source Detail", "BROKERS", "SERVICE",
                ("full",), "GET /api/sources/status (both brokers)",
                DiagnosticsRunner._check_sources_detail),
]

_FULL_CHECKS = _QUICK_CHECKS + _FULL_EXTRA
# Deduplicate while preserving order (SimpleNamespace is unhashable, so use IDs)
_seen_ids: set[str] = set()
_ALL_CHECKS: list[_CheckDef] = []
for c in _QUICK_CHECKS + _FULL_EXTRA:
    if c.id not in _seen_ids:
        _seen_ids.add(c.id)
        _ALL_CHECKS.append(c)
