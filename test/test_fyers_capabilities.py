#!/usr/bin/env python3
"""Fyers capability + routing tests (S5).

Verifies the Fyers declaration matches the actual supported
implementation: option chain with greeks, OI/previous-OI/OI-change from
REST depth, provider-specific depth, and NO claim of provider analytics
endpoints (max-pain/PCR/OI-analytics are not provider-supplied).
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_fyers_declares_option_chain_and_oi_capabilities() -> None:
    from brokers.fyers.capabilities import CAPABILITIES
    assert CAPABILITIES.supports("option_chain")
    assert CAPABILITIES.supports(
        "oi", "previous_oi", "oi_change", "oi_change_percent")
    assert CAPABILITIES.supports("depth", "spot", "history", "ltp", "ohlc")


def test_fyers_does_not_claim_provider_analytics() -> None:
    from brokers.fyers.capabilities import CAPABILITIES
    assert not CAPABILITIES.supports("max_pain")
    assert not CAPABILITIES.supports("pcr")
    assert not CAPABILITIES.supports("oi_analytics")


def test_fyers_key_styles_are_colon_and_numeric() -> None:
    from brokers.fyers.capabilities import CAPABILITIES
    assert set(CAPABILITIES.key_styles) == {"colon", "numeric"}


def test_fyers_capacity_is_standard_default() -> None:
    from providers.registry import get_registry
    r = get_registry()
    assert r.capacity("fyers") == 100


def test_routing_option_chain_includes_fyers() -> None:
    from providers.registry import get_registry
    r = get_registry()
    assert "fyers" in r.providers_with("option_chain")


def test_routing_max_pain_is_upstox_only() -> None:
    from providers.registry import get_registry
    r = get_registry()
    assert r.providers_with("max_pain") == ["upstox"]


def test_routing_previous_oi_is_fyers_only() -> None:
    from providers.registry import get_registry
    r = get_registry()
    assert r.providers_with("previous_oi") == ["fyers"]


def test_fyers_depth_capability_present() -> None:
    from brokers.fyers.capabilities import CAPABILITIES
    assert CAPABILITIES.supports("depth")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
