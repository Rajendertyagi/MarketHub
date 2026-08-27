#!/usr/bin/env python3
"""Unit tests for the MCP test URL helper (MCP-2A).

Covers the deterministic path/port normalization rules of
``helpers.urls.build_mcp_url``. Pure unit tests — no server needed.
"""

from __future__ import annotations

from helpers.urls import build_mcp_url


def test_default_path_127_0_0_1() -> None:
    assert build_mcp_url("127.0.0.1", 8765) == "http://127.0.0.1:8765/mcp"


def test_localhost() -> None:
    assert build_mcp_url("localhost", 8765) == "http://localhost:8765/mcp"


def test_custom_path() -> None:
    assert build_mcp_url("127.0.0.1", 8765, "/custom") == "http://127.0.0.1:8765/custom"


def test_path_without_leading_slash() -> None:
    assert build_mcp_url("127.0.0.1", 8765, "custom") == "http://127.0.0.1:8765/custom"


def test_path_with_leading_slash() -> None:
    assert build_mcp_url("127.0.0.1", 8765, "/mcp") == "http://127.0.0.1:8765/mcp"


def test_trailing_slash_stripped() -> None:
    assert build_mcp_url("127.0.0.1", 8765, "/mcp/") == "http://127.0.0.1:8765/mcp"


def test_different_valid_ports() -> None:
    assert build_mcp_url("127.0.0.1", 8000) == "http://127.0.0.1:8000/mcp"
    assert build_mcp_url("127.0.0.1", 9090) == "http://127.0.0.1:9090/mcp"


def test_empty_path_falls_back_to_mcp() -> None:
    assert build_mcp_url("127.0.0.1", 8765, "") == "http://127.0.0.1:8765/mcp"


def test_whitespace_path_stripped() -> None:
    assert build_mcp_url("127.0.0.1", 8765, "  /mcp  ") == "http://127.0.0.1:8765/mcp"