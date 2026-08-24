#!/usr/bin/env python3
"""Unit tests for D4 Upstox source factory + registry integration.

Verifies that upstox_feed is constructible through the standard
build_source_manager path with MarketService injection, without any
app/server.py special-casing.
"""

from __future__ import annotations

import inspect
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

TOKEN = "SYNTHETIC_ACCESS_TOKEN_XYZ"
KEY = "NSE_EQ|INE848E01016"


def _make_config(**over):
    cfg = {
        "source_name": "upstox",
        "type": "upstox_feed",
        "enabled": True,
        "mode": "full",
        "access_token": "$UPSTOX_ACCESS_TOKEN",
        "instruments": [
            {"key": KEY, "exchange": "NSE", "tradingsymbol": "DMART"},
        ],
    }
    cfg.update(over)
    return cfg


def _expect_raises(runner: R, label: str, exc_type: type, fn, needle=None):
    try:
        fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(label, needle in str(exc),
                               f"expected {needle!r}: {exc}")
        else:
            runner.ok(label)
        return
    except Exception as exc:
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")


# ---------------------------------------------------------------------------
# W1 — registry discovery + construction through build_source_manager
# ---------------------------------------------------------------------------


def test_registry_construction(runner: R) -> None:
    """W1: upstox_feed constructible via build_source_manager."""
    name = "W1-registry"
    from sources import build_source_manager

    svc = object()  # sentinel — identity-checked later
    os.environ["UPSTOX_ACCESS_TOKEN"] = TOKEN
    try:
        cfg = {"upstox": _make_config()}
        manager = build_source_manager(cfg, market_service=svc)
    finally:
        os.environ.pop("UPSTOX_ACCESS_TOKEN", None)

    sources = manager.enabled_sources
    runner.assert_true(name + "-has-source", "upstox" in sources,
                       f"expected 'upstox' in {list(sources.keys())}")

    feed = sources["upstox"]
    runner.assert_eq(name + "-name", feed.name, "upstox")
    runner.assert_eq(name + "-state", feed.status()["state"], "stopped")

    # Verify same shared MarketService instance is used (not a copy).
    runner.assert_true(name + "-same-service",
                       feed._market_service is svc,
                       "feed must reference the SAME MarketService instance")


def test_registry_missing_token(runner: R) -> None:
    """W2: missing env token raises SourceConfigError (visible failure)."""
    name = "W2-missing-token"
    from sources import SourceConfigError, build_source_manager

    # Ensure the env var is NOT set.
    env_key = "UPSTOX_ACCESS_TOKEN"
    old_val = os.environ.pop(env_key, None)
    try:
        cfg = {"upstox": _make_config(
            access_token=f"${{{env_key}}}"
        )}
        _expect_raises(runner, name + "-raises", SourceConfigError,
                       lambda: build_source_manager(cfg, market_service=object()),
                       needle="not resolved")
    finally:
        if old_val is not None:
            os.environ[env_key] = old_val


def test_registry_bad_instrument(runner: R) -> None:
    """W3: instrument missing exchange/tradingsymbol rejected."""
    name = "W3-bad-instrument"
    from sources import SourceConfigError, build_source_manager

    cfg = {"upstox": _make_config(
        instruments=[{"key": KEY}]  # missing exchange/tradingsymbol
    )}
    _expect_raises(runner, name + "-missing-fields", SourceConfigError,
                   lambda: build_source_manager(cfg, market_service=object()))


def test_registry_bad_mode(runner: R) -> None:
    """W4: unsupported mode rejected."""
    name = "W4-bad-mode"
    from sources import SourceConfigError, build_source_manager

    cfg = {"upstox": _make_config(mode="option_greeks")}
    _expect_raises(runner, name + "-bad-mode", SourceConfigError,
                   lambda: build_source_manager(cfg, market_service=object()))


def test_token_not_in_error(runner: R) -> None:
    """W5: synthetic token never appears in error messages."""
    name = "W5-token-safety"
    from sources import build_source_manager

    # Use a literal token so it would appear in errors if leaked.
    literal_token = "SUPER_SECRET_LITERAL_TOKEN_123"
    cfg = {"upstox": _make_config(
        access_token=literal_token,
        instruments=[{"key": KEY}],  # missing exchange/tradingsymbol → error
    )}
    try:
        build_source_manager(cfg, market_service=object())
        runner.fail(name, "expected an error for incomplete instruments")
    except Exception as exc:
        dumped = str(exc) + repr(exc)
        runner.assert_not_in(name + "-no-token-leak", literal_token, dumped)


def test_no_publish_event_in_feed(runner: R) -> None:
    """W6: feed.py does not import or call publish_event."""
    name = "W6-no-publish"
    feed_py = os.path.join(_PROJECT_DIR, "brokers", "upstox", "feed.py")
    with open(feed_py, encoding="utf-8") as f:
        source = f.read()
    runner.assert_not_in(name + "-no-publish-event", "publish_event", source)


# ---------------------------------------------------------------------------
# Imports needed
# ---------------------------------------------------------------------------


import os  # noqa: E402


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> bool:
    runner = R()

    test_registry_construction(runner)
    test_registry_missing_token(runner)
    test_registry_bad_instrument(runner)
    test_registry_bad_mode(runner)
    test_token_not_in_error(runner)
    test_no_publish_event_in_feed(runner)

    return runner.summary()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
