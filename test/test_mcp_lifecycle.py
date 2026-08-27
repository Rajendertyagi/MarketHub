#!/usr/bin/env python3
"""MCP server lifecycle proofs (MCP-2A).

These tests manage the subprocess server lifecycle themselves (no module
``mcp_server`` fixture) because they intentionally stop the server:

  * 5 start/stop cycles — each cycle starts a real server, verifies it
    responds over real TCP, then tears it down cleanly.
  * clean shutdown — verifies the subprocess is terminated, the port is
    released, and the helper state is fully reset.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from helpers import lifecycle
from helpers.lifecycle import (
    get_server_url,
    restore_environment,
    start_server,
)
from mcp_result import to_payload


def _port_is_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.connect((host, port))
            return True
        except (ConnectionRefusedError, OSError):
            return False


async def _ping_ok() -> bool:
    """Return True if a fresh MCP client can initialize + system_ping."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

    url = get_server_url()
    try:
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool("system_ping", {})
                return to_payload(result).get("status") == "ok"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# TEST 6.1 — five start/stop cycles
# ---------------------------------------------------------------------------

async def test_five_start_stop_cycles() -> None:
    """5 cycles: start -> initialize/ping -> stop, with clean teardown each time."""
    for cycle in range(5):
        proc = await start_server()
        url = get_server_url()

        # Valid absolute URL with scheme.
        assert url.startswith("http://"), f"cycle {cycle}: invalid URL {url!r}"
        assert url.endswith("/mcp"), f"cycle {cycle}: URL missing /mcp path: {url}"

        # Server responds over real TCP.
        assert await _ping_ok(), f"cycle {cycle}: server did not respond to ping"

        port = lifecycle._server_port
        assert port > 0, f"cycle {cycle}: invalid port {port}"

        # Tear down.
        restore_environment()

        # Process terminated.
        assert proc.poll() is not None, f"cycle {cycle}: process still running"

        # Port released.
        assert not _port_is_open("127.0.0.1", port), (
            f"cycle {cycle}: port {port} still open after shutdown"
        )

        # Helper state reset.
        assert lifecycle._server_proc is None, f"cycle {cycle}: _server_proc not cleared"
        assert lifecycle._server_url == "", f"cycle {cycle}: _server_url not cleared"
        assert lifecycle._server_port == 0, f"cycle {cycle}: _server_port not cleared"


# ---------------------------------------------------------------------------
# TEST 6.2 — clean shutdown
# ---------------------------------------------------------------------------

async def test_clean_shutdown() -> None:
    """restore_environment(): subprocess terminated, port released, state reset."""
    proc = await start_server()
    url = get_server_url()
    port = lifecycle._server_port

    # Verify running before shutdown.
    assert await _ping_ok(), "server did not respond before shutdown"
    assert _port_is_open("127.0.0.1", port), "port not open before shutdown"

    restore_environment()

    # Subprocess terminated.
    assert proc.poll() is not None, "subprocess still running after shutdown"

    # Port released.
    assert not _port_is_open("127.0.0.1", port), "port still open after shutdown"

    # Helper state reset.
    assert lifecycle._server_proc is None, "_server_proc not cleared"
    assert lifecycle._server_url == "", "_server_url not cleared"
    assert lifecycle._server_port == 0, "_server_port not cleared"
    assert lifecycle._server_data_dir == "", "_server_data_dir not cleared"
    assert lifecycle._server_original_config is None, (
        "_server_original_config not cleared"
    )

    # get_server_url()/get_server_port() now fail explicitly (no silent ""/0).
    with pytest.raises(RuntimeError):
        get_server_url()
    with pytest.raises(RuntimeError):
        lifecycle.get_server_port()