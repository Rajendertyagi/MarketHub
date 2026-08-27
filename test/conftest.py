"""Pytest configuration for MarketHub test suite."""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path for imports
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def pytest_configure(config):
    """Register test markers."""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "slow: mark test as slow")


import pytest
from helpers.runner import R


@pytest.fixture
def runner():
    """Provide shared test runner instance."""
    return R()


@pytest.fixture(scope="module")
def mcp_server():
    """Start a real subprocess MCP server for the whole module.

    Module-scoped so the 12 standalone-style tests (test_sdk_alignment.py,
    test_multi_client.py, test_sse_stream.py) that were written to run via
    ``main()`` get a live server under pytest. Teardown reuses the existing
    lifecycle ownership (stop subprocess, restore config, clean data_test and
    .test_logs, release port, clear helper state).
    """
    import asyncio

    from helpers.lifecycle import restore_environment, start_server

    asyncio.run(start_server())
    try:
        yield
    finally:
        restore_environment()
