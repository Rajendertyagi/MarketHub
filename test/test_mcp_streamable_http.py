#!/usr/bin/env python3
"""Real Streamable HTTP runtime proofs for the frozen MCP-1 contract (MCP-2A).

These tests exercise the REAL subprocess server over REAL TCP using the MCP
SDK client against the actual ``/mcp`` endpoint. They prove:

  * initialize / protocol negotiation
  * tools/list returns exactly the 43 visible tools (26 frozen + 17 deferred,
    0 dev_*)
  * representative success calls (system_ping, instrument_search)
  * well-formed error contract for data-dependent tools when the subprocess
    test server has no seeded market data
  * session lifecycle (stateless mode)
  * 3 concurrent clients
  * malformed-request recovery
  * server restart
  * zero unintended broker connections

The subprocess server is started once per module by the ``mcp_server`` fixture
(see test/conftest.py). No ASGITransport is used here: MCP-2A is specifically
proving the real Streamable HTTP runtime transport.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from helpers.lifecycle import get_server_url, restart_server
from mcp_result import normalize_tool_result, to_payload

# These tests need a real subprocess server (module-scoped fixture).
pytestmark = pytest.mark.usefixtures("mcp_server")


# ---------------------------------------------------------------------------
# Expected visible tool surface (frozen MCP-1 contract + deferred tools)
# ---------------------------------------------------------------------------

FROZEN_MCP1_TOOLS: list[str] = [
    "system_ping",
    "market_quote",
    "market_depth",
    "market_status",
    "instrument_search",
    "watchlists",
    "market_history",
    "option_chain",
    "futures_contracts",
    "compute_pcr",
    "compute_max_pain",
    "compute_top_oi_strikes",
    "compute_atm",
    "compute_iv_skew",
    "compute_oi_buildup",
    "compute_support_resistance",
    "compute_straddle",
    "compute_gex",
    "compute_futures_basis",
    "price_long_straddle",
    "price_long_strangle",
    "price_bull_call_spread",
    "price_bear_put_spread",
    "price_iron_condor",
    "price_long_butterfly",
    "analyze_option_chain",
]

DEFERRED_TOOLS: list[str] = [
    # 5 generic alerts
    "alert_create", "alert_list", "alert_get", "alert_enable", "alert_disable",
    # 2 event pub/sub
    "event_publish", "event_list",
    # 2 consumer management
    "consumer_register", "consumer_topic_add",
    # 3 replay/checkpoint
    "consumer_event_pending_list", "consumer_event_acknowledge",
    "consumer_checkpoint_get",
    # 5 market alerts
    "market_alert_create", "market_alert_list", "market_alert_enable",
    "market_alert_disable", "market_alert_delete",
]

ALL_VISIBLE_TOOLS = set(FROZEN_MCP1_TOOLS) | set(DEFERRED_TOOLS)


# ---------------------------------------------------------------------------
# Shared client helpers
# ---------------------------------------------------------------------------

async def _open_session():
    """Open a real MCP client session against the running subprocess server."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

    url = get_server_url()
    ctx = streamablehttp_client(url)
    r, w = await ctx.__aenter__()
    session = ClientSession(r, w)
    await session.__aenter__()
    return ctx, session


async def _close_session(ctx, session) -> None:
    await session.__aexit__(None, None, None)
    await ctx.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# TEST 5.1 — initialize over real HTTP
# ---------------------------------------------------------------------------

async def test_initialize_succeeds_over_real_http() -> None:
    """initialize: protocol negotiation + serverInfo over real TCP."""
    ctx, session = await _open_session()
    try:
        init = await session.initialize()
        assert init is not None
        assert getattr(init, "protocol_version", None), "protocol version missing"
        server_info = getattr(init, "server_info", None)
        assert server_info is not None, "serverInfo missing"
        assert getattr(server_info, "name", None), "serverInfo.name missing"
        assert getattr(server_info, "version", None), "serverInfo.version missing"
    finally:
        await _close_session(ctx, session)


# ---------------------------------------------------------------------------
# TEST 5.2 — tools/list returns exactly 43 visible tools
# ---------------------------------------------------------------------------

async def test_tools_list_exactly_43_visible() -> None:
    """tools/list: 26 frozen + 17 deferred + 0 dev_* = 43 visible tools."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.list_tools()
        names = [t.name for t in result.tools]
    finally:
        await _close_session(ctx, session)

    assert len(names) == 43, f"expected 43 visible tools, got {len(names)}"

    missing_frozen = [t for t in FROZEN_MCP1_TOOLS if t not in names]
    assert not missing_frozen, f"frozen MCP-1 tools missing: {missing_frozen}"

    missing_deferred = [t for t in DEFERRED_TOOLS if t not in names]
    assert not missing_deferred, f"deferred tools missing: {missing_deferred}"

    dev_tools = [n for n in names if n.startswith("dev_")]
    assert not dev_tools, f"dev_* tools re-registered: {dev_tools}"

    unexpected = set(names) - ALL_VISIBLE_TOOLS
    assert not unexpected, f"unexpected tools visible: {unexpected}"


# ---------------------------------------------------------------------------
# TEST 5.3 — system_ping representative success call
# ---------------------------------------------------------------------------

async def test_system_ping_succeeds() -> None:
    """system_ping returns the canonical ok payload over real HTTP."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("system_ping", {})
        payload = to_payload(result)
    finally:
        await _close_session(ctx, session)

    assert payload.get("status") == "ok", f"unexpected system_ping payload: {payload}"


# ---------------------------------------------------------------------------
# TEST 5.4 — instrument_search executes successfully (empty catalog)
# ---------------------------------------------------------------------------

async def test_instrument_search_executes() -> None:
    """instrument_search: valid success with empty results on empty catalog."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("instrument_search", {"q": "RELIANCE"})
        payload = to_payload(result)
    finally:
        await _close_session(ctx, session)

    assert payload.get("status") == "ok", f"instrument_search failed: {payload}"
    assert "results" in payload or "count" in payload, (
        f"instrument_search response not well-formed: {payload}"
    )


# ---------------------------------------------------------------------------
# TEST 5.5 — data-dependent tools prove the ERROR contract (no seeded data)
# ---------------------------------------------------------------------------

async def test_market_quote_error_contract() -> None:
    """market_quote: well-formed error (no seeded data), not a traceback."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("market_quote", {"instrument_ref": "RELIANCE"})
        norm = normalize_tool_result(result)
    finally:
        await _close_session(ctx, session)

    assert not norm["is_error"] or "error" in (norm["parsed"] or {}), (
        f"market_quote did not return a well-formed error: {norm['text'][:200]}"
    )
    payload = norm["parsed"] or {}
    assert "error" in payload, f"market_quote missing error key: {payload}"
    assert "quote" not in payload, (
        f"market_quote fabricated data: {payload}"
    )


async def test_compute_pcr_error_contract() -> None:
    """compute_pcr: well-formed error (no seeded data), not a traceback."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("compute_pcr", {"underlying": "NIFTY"})
        norm = normalize_tool_result(result)
    finally:
        await _close_session(ctx, session)

    payload = norm["parsed"] or {}
    assert payload.get("status") == "error" or "error" in payload, (
        f"compute_pcr did not return a well-formed error: {norm['text'][:200]}"
    )


async def test_market_history_error_contract() -> None:
    """market_history: well-formed error (no seeded data), not a traceback."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool(
            "market_history",
            {
                "instrument_ref": "RELIANCE",
                "unit": "days",
                "interval": 1,
                "from_date": "2026-08-01",
                "to_date": "2026-08-27",
            },
        )
        norm = normalize_tool_result(result)
    finally:
        await _close_session(ctx, session)

    payload = norm["parsed"] or {}
    assert "error" in payload, f"market_history missing error key: {payload}"
    assert "candles" not in payload, (
        f"market_history fabricated data: {payload}"
    )


# ---------------------------------------------------------------------------
# TEST 5.6 — session lifecycle (initialize -> list -> ping)
# ---------------------------------------------------------------------------

async def test_session_lifecycle() -> None:
    """One session: initialize -> tools/list -> system_ping."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        tools = await session.list_tools()
        assert len(tools.tools) == 43
        result = await session.call_tool("system_ping", {})
        payload = to_payload(result)
        assert payload.get("status") == "ok"
    finally:
        await _close_session(ctx, session)


# ---------------------------------------------------------------------------
# TEST 5.7 — 3 concurrent clients
# ---------------------------------------------------------------------------

async def test_three_concurrent_clients() -> None:
    """3 clients concurrently: initialize + tools/list + system_ping each."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

    url = get_server_url()

    async def _client(idx: int) -> dict[str, Any]:
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("system_ping", {})
                payload = to_payload(result)
                return {
                    "idx": idx,
                    "tool_count": len(tools.tools),
                    "status": payload.get("status"),
                }

    results = await asyncio.gather(*[_client(i) for i in range(3)])
    for r in results:
        assert r["tool_count"] == 43, f"client {r['idx']} saw {r['tool_count']} tools"
        assert r["status"] == "ok", f"client {r['idx']} ping failed: {r}"


# ---------------------------------------------------------------------------
# TEST 5.8 — malformed request recovery
# ---------------------------------------------------------------------------

async def test_malformed_request_recovery() -> None:
    """Malformed JSON-RPC gets an error; a fresh valid client still works."""
    import httpx2

    url = get_server_url()

    # Send a malformed JSON body to /mcp.
    async with httpx2.AsyncClient() as client:
        resp = await client.post(
            url,
            content=b"{not valid json",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        # The server must answer (any 4xx/5xx is acceptable) — not hang/crash.
        assert resp.status_code >= 400, (
            f"malformed request unexpectedly accepted: {resp.status_code}"
        )

    # Immediately after, a fresh valid client must work.
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("system_ping", {})
        payload = to_payload(result)
        assert payload.get("status") == "ok", (
            f"server unhealthy after malformed request: {payload}"
        )
    finally:
        await _close_session(ctx, session)


# ---------------------------------------------------------------------------
# TEST 5.9 — restart
# ---------------------------------------------------------------------------

async def test_restart_proof() -> None:
    """restart_server(): fresh server serves initialize + ping with no stale state."""
    # Verify the current server works first.
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("system_ping", {})
        assert to_payload(result).get("status") == "ok"
    finally:
        await _close_session(ctx, session)

    # Restart the server (stops old process, starts fresh one).
    await restart_server()

    # Fresh client against the new server.
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.call_tool("system_ping", {})
        payload = to_payload(result)
        assert payload.get("status") == "ok", (
            f"fresh server ping failed after restart: {payload}"
        )
        tools = await session.list_tools()
        assert len(tools.tools) == 43
    finally:
        await _close_session(ctx, session)


# ---------------------------------------------------------------------------
# TEST 5.10 — zero unintended broker connections
# ---------------------------------------------------------------------------

async def test_zero_broker_connections() -> None:
    """mcp-event://sources/status shows no running broker sources."""
    ctx, session = await _open_session()
    try:
        await session.initialize()
        result = await session.read_resource("mcp-event://sources/status")
        text = ""
        for block in result.contents:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                text = block_text
                break
        try:
            status = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            status = {}
    finally:
        await _close_session(ctx, session)

    # With no broker auth/data configured, no source should be running.
    if isinstance(status, dict):
        for name, info in status.items():
            state = (info or {}).get("state", "")
            assert state not in {"running", "connected"}, (
                f"unexpected running broker source: {name} state={state}"
            )