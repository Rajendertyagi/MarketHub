"""
Server lifecycle: timeout wrapper and banner/shutdown helpers.

Extracted from server.py to keep the main module focused on MCP wiring.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.errors import OperationTimeoutError

logger = logging.getLogger("event_server")


async def run_with_timeout(
    coro: Any,
    operation: str,
    timeout_seconds: float,
) -> Any:
    """Run a coroutine with a timeout. Propagates cancellation correctly."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise OperationTimeoutError(operation, timeout_seconds) from None
    except asyncio.CancelledError:
        # Re-raise cancellation — don't swallow it
        raise


def print_banner(
    mcp_spec: str,
    listen_host: str,
    listen_port: int,
    event_resource_uri: str,
    events_pending_uri: str,
    info_resource_uri: str,
    sources_resource_uri: str,
    log_level: str,
    data_dir: str,
    timeouts: dict[str, Any],
) -> None:
    print()
    print("=" * 56)
    print("  MCP Event Server")
    print("=" * 56)
    print()
    print("  MCP SDK           : 2.0.0")
    print(f"  MCP Spec          : {mcp_spec}")
    print("  Transport         : Streamable HTTP")
    print(f"  Endpoint          : http://{listen_host}:{listen_port}/mcp")
    print(f"  Event Resource    : {event_resource_uri}")
    print(f"  Pending Events    : {events_pending_uri}")
    print(f"  Info Resource     : {info_resource_uri}")
    print(f"  Sources Resource  : {sources_resource_uri}")
    print(f"  Log Level         : {log_level}")
    print(f"  Data Directory    : {data_dir}")
    print("  Timeout (tool)    : {}s".format(timeouts["default_tool_seconds"]))
    print("  Timeout (DB)      : {}s".format(timeouts["database_seconds"]))
    print("  Timeout (shutdown): {}s".format(timeouts["shutdown_seconds"]))
    print()
    print("  Press CTRL+C to stop.")
    print()
    logger.info(
        f"started  host={listen_host}  port={listen_port}  endpoint=http://{listen_host}:{listen_port}/mcp"
    )


def print_shutdown() -> None:
    logger.info("shutting down")
    print()
    print("Stopped.")
    print()
