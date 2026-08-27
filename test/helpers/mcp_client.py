#!/usr/bin/env python3
"""
Shared MCP call helpers for the event-server test suite.

Provides one-shot tool calls, resource reads, and source-readiness probes
using only public MCP client APIs. Also provides a direct-publish helper
for tests that need to seed events through the canonical internal pipeline
(bypassing the removed public event_publish tool — MCP-2B.3D).

Every network-touching call is bounded by ``MCP_CALL_TIMEOUT`` via
``asyncio.wait_for`` so a stalled transport can never hang a test run for
hours — the call raises ``asyncio.TimeoutError`` and the test fails fast.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

from mcp_result import normalize_tool_result, observe_structured_output, to_payload
from .lifecycle import get_server_url, get_server_port

# Hard cap for any single MCP tool call / resource read. Tune as needed.
MCP_CALL_TIMEOUT = 30.0


async def call(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """One-shot MCP tool call, normalized via the shared SDK contract.

    On success returns the tool's JSON/structured payload.
    On error returns the normalized dict so callers can assert ``is_error``.
    Raises ``asyncio.TimeoutError`` if the transport does not respond in time.
    """
    url = get_server_url()

    async def _run() -> dict[str, Any]:
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments or {})
                return to_payload(result)

    return await asyncio.wait_for(_run(), timeout=MCP_CALL_TIMEOUT)


async def call_session(session: ClientSession, tool_name: str, arguments: dict | None = None) -> dict:
    """Call a tool on an existing session, normalized."""
    result = await session.call_tool(tool_name, arguments or {})
    return to_payload(result)


async def read_res(uri: str) -> Any:
    """One-shot MCP resource read. Raises ``asyncio.TimeoutError`` on stall."""
    url = get_server_url()

    async def _run() -> Any:
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.read_resource(uri)
                for block in result.contents:
                    text = block.text if hasattr(block, "text") else str(block)
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return text
                return None

    return await asyncio.wait_for(_run(), timeout=MCP_CALL_TIMEOUT)


async def list_tools_names() -> list[str]:
    """Return the list of tool names available on the server (bounded)."""
    url = get_server_url()

    async def _run() -> list[str]:
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.list_tools()
                return [t.name for t in result.tools]

    return await asyncio.wait_for(_run(), timeout=MCP_CALL_TIMEOUT)


async def inspect_tool_output(
    tool_name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Structured-output observation helper for SDK-alignment verification."""
    url = get_server_url()
    return await observe_structured_output(url, tool_name, arguments)


async def wait_source_ready(
    name: str, states: set[str] | None = None, timeout: float = 15.0
) -> dict[str, Any]:
    """Wait until a source appears in mcp-event://sources/status with an acceptable state."""
    if states is None:
        states = {"running", "completed", "initialized", "degraded", "failed", "stopped"}
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            status = await read_res("mcp-event://sources/status")
        except Exception:
            status = {}
        if isinstance(status, dict) and name in status:
            st = status[name].get("state")
            last = status[name]
            if st in states:
                return status[name]
        await asyncio.sleep(0.2)
    return last


async def wait_for_event_count(
    event_type_prefix: str, min_count: int, timeout: float = 15.0
) -> int:
    """Wait until at least min_count in-memory events match the prefix."""
    deadline = time.monotonic() + timeout
    count = 0
    while time.monotonic() < deadline:
        try:
            data = await call("event_list", {"limit": 100})
        except Exception:
            data = {"events": []}
        events = data.get("events", [])
        count = len([e for e in events if isinstance(e, dict)
                     and e.get("type", "").startswith(event_type_prefix)])
        if count >= min_count:
            return count
        await asyncio.sleep(0.2)
    return count


# ---------------------------------------------------------------------------
# Direct-publish helper (MCP-2B.3D)
# ---------------------------------------------------------------------------

async def publish_event(
    event_type: str,
    source: str = "manual-test",
    data: dict[str, Any] | None = None,
    persistent: bool = False,
    routing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Seed an event directly through the canonical internal pipeline.

    This is the replacement for ``call("event_publish", ...)`` now that
    ``event_publish`` has been removed from the public MCP registry
    (MCP-2B.3D). It publishes through ``core.events.publish_event()`` against
    the shared SQLite store, preserving durable-pipeline coverage without
    going through the (now-removed) MCP tool boundary.

    Note: This publishes into the DB but does NOT update the server process's
    in-memory ``_event_history`` / ``_latest_event`` buffers. Tests that need
    those buffers must use server-side sources (e.g. test_source ticks) instead.
    """
    _PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PROJECT_DIR not in sys.path:
        sys.path.insert(0, _PROJECT_DIR)

    from core.persistence.store import EventStore  # noqa: E402
    from core import events as events_mod  # noqa: E402

    # Access data_dir dynamically (not at import time) to get the current
    # server state set by helpers/lifecycle.start_server().
    from . import lifecycle as _lc  # noqa: E402
    actual_data_dir = getattr(_lc, "_server_data_dir", "") or "data_test"
    db_path = os.path.join(_PROJECT_DIR, actual_data_dir, "events.db")

    store = EventStore(db_path)
    try:
        result = await events_mod.publish_event(
            event_type=event_type,
            source=source,
            data=data or {},
            persistent=persistent,
            routing=routing,
            store=store,
            bus=None,
        )
        return {
            "status": "published",
            "event": result,
        }
    finally:
        if hasattr(store, "close"):
            store.close()
