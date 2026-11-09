#!/usr/bin/env python3
"""Provider registry + capability model tests (Phase 2).

Verifies the broker-neutral provider registry:
  * both providers declared; preference order = declared order
  * capacity comes from the adapter's own declaration (single source)
  * key inference preserves the historical behaviour
  * capability declarations are accurate (OI vs previous-OI)
  * importing the registry never imports broker code
  * the API projection is broker-neutral and secret-free
"""

from __future__ import annotations

import os
import subprocess
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_registry_declares_both_providers() -> None:
    from providers.registry import get_registry
    r = get_registry()
    assert r.ids() == ["upstox", "fyers"]
    assert r.preference_order() == ["upstox", "fyers"]
    assert r.enabled_ids() == ["upstox", "fyers"]


def test_capacity_comes_from_adapter_declaration() -> None:
    from providers.registry import get_registry
    r = get_registry()
    assert r.capacity("upstox") == 2000          # full mode
    assert r.mode_limit("upstox", "ltpc") == 5000
    # Fyers default tier is Standard (100); Prime (5000) is a plan override.
    assert r.capacity("fyers") == 100
    assert r.capacity("unknown") is None


def test_inference_matches_historical_behaviour() -> None:
    from app.market_data import _infer_provider
    cases = {
        "NSE_INDEX|Nifty 50": "upstox",
        "NSE_EQ|RELIANCE": "upstox",
        "NSE:NIFTY50-INDEX": "fyers",
        "BSE:RELIANCE": "fyers",
        "101000000026000": "fyers",
        "K": "upstox",
        "": "upstox",
    }
    for key, expected in cases.items():
        assert _infer_provider(key) == expected, key


def test_capability_declarations_are_accurate() -> None:
    from providers.registry import get_registry
    r = get_registry()
    # Only Fyers streams previous-day OI / OI-change.
    assert r.providers_with("previous_oi") == ["fyers"]
    assert r.providers_with("oi_change") == ["fyers"]
    assert set(r.providers_with("oi")) == {"upstox", "fyers"}
    # Upstox supplies OI but not previous OI (verified wire format).
    assert r.get("upstox").supports("oi")
    assert not r.get("upstox").supports("previous_oi")
    # Fyers has no analytics endpoints in this integration.
    assert not r.get("fyers").supports("max_pain")


def test_no_second_capability_table() -> None:
    """The Upstox feed's mode limits come from its own declaration."""
    from brokers.upstox.capabilities import CAPABILITIES
    from brokers.upstox.feed import _MODE_KEY_LIMITS, SUPPORTED_MODES
    assert SUPPORTED_MODES == tuple(CAPABILITIES.modes)
    assert _MODE_KEY_LIMITS == dict(CAPABILITIES.modes)


def test_capability_tokens_are_canonical() -> None:
    from providers.capabilities import ALL_CAPABILITIES
    from providers.registry import get_registry
    for provider in get_registry().all():
        assert provider.capabilities <= ALL_CAPABILITIES


def test_projection_is_broker_neutral_and_secret_free() -> None:
    from providers.registry import get_registry
    proj = get_registry().projection()
    assert {p["provider"] for p in proj} == {"upstox", "fyers"}
    for p in proj:
        assert set(p) >= {"provider", "label", "enabled", "capabilities",
                          "capacity", "modes", "default_mode", "status"}
        assert not any(k in p for k in ("token", "secret", "api_key",
                                        "access_token", "refresh_token"))


def test_importing_registry_does_not_import_brokers() -> None:
    """Broker-neutrality: the registry must not pull in broker adapters."""
    code = (
        "import sys\n"
        "import providers.registry\n"
        "bad = [m for m in sys.modules if m.startswith('brokers')]\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=_PROJECT_DIR,
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
