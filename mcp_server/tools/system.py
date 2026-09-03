"""
System tools: system_ping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp_server.contract import TOOL_SYSTEM_PING
from mcp_server.registry import get_tool_description


def register_system_tools(mcp, **kwargs) -> None:
    """Register system-level tools."""

    @mcp.tool(name=TOOL_SYSTEM_PING, description=get_tool_description(TOOL_SYSTEM_PING))
    def ping() -> dict[str, Any]:
        """Check whether the MCP server is running."""
        return {
            "status": "ok",
            "message": "MCP server is running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
