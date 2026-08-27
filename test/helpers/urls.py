#!/usr/bin/env python3
"""
Test URL construction helpers for the MCP event server test suite.

Single source of truth for building the absolute HTTP URL that the MCP SDK
client needs to reach the Streamable HTTP endpoint of the test subprocess
server. No production code depends on this module.
"""

from __future__ import annotations


def build_mcp_url(host: str, port: int, path: str = "/mcp") -> str:
    """Build a valid absolute HTTP URL for the MCP Streamable HTTP endpoint.

    The MCP SDK client requires an absolute URL with an explicit scheme
    (``http://``); a bare host or empty string makes the SDK raise
    ``httpx2.UnsupportedProtocol``. This helper guarantees a well-formed URL.

    Path handling is deliberately small and deterministic:

    * whitespace is stripped
    * an empty path falls back to ``/mcp``
    * a missing leading slash is added
    * a trailing slash is removed (``/mcp/`` -> ``/mcp``)

    Examples::

        build_mcp_url("127.0.0.1", 8765)          -> "http://127.0.0.1:8765/mcp"
        build_mcp_url("localhost", 8765)          -> "http://localhost:8765/mcp"
        build_mcp_url("127.0.0.1", 8765, "custom") -> "http://127.0.0.1:8765/custom"
        build_mcp_url("127.0.0.1", 8765, "/mcp/")  -> "http://127.0.0.1:8765/mcp"
    """
    path = path.strip()
    if not path:
        path = "/mcp"
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/") or "/"
    return f"http://{host}:{port}{path}"