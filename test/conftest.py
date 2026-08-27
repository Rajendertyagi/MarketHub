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
