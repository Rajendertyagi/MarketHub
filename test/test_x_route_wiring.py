#!/usr/bin/env python3
"""X/Twitter route wiring tests (read-only smoke of route registration).

Verifies that build_x_routes is wired into the production app composition
and that the declared endpoints are all reachable. Does not modify state.
"""

from __future__ import annotations

import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402


def test_x_routes_registered_in_app_composition(runner: R) -> None:
    """The production app must include build_x_routes in its route list."""
    from app.server import app
    paths = {route.path for route in app.routes}
    expected = {"/api/x/status", "/api/x/test", "/api/x/tweets",
                "/api/settings/x", "/api/x/config"}
    for p in expected:
        runner.assert_true(f"route-{p}", p in paths,
                           f"missing route {p}; got {sorted(paths)}")


def test_x_status_endpoint_accessible(runner: R) -> None:
    """GET /api/x/status returns 200 with auth metadata (no crash)."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from app.server import app as _prod_app
    # Use the real app so service wiring is exercised.
    client = TestClient(_prod_app, raise_server_exceptions=False)
    resp = client.get("/api/x/status")
    runner.assert_eq("x-status-code", resp.status_code, 200)
    body = resp.json()
    runner.assert_true("x-status-has-auth", "auth" in body)
    runner.assert_true("x-status-has-enabled", "enabled" in body)


def test_x_test_endpoint_accessible(runner: R) -> None:
    """POST /api/x/test returns 200 (may be not_configured if no creds)."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from app.server import app as _prod_app
    client = TestClient(_prod_app, raise_server_exceptions=False)
    resp = client.post("/api/x/test")
    runner.assert_eq("x-test-code", resp.status_code, 200)
    body = resp.json()
    runner.assert_true("x-test-has-status", "status" in body)
    runner.assert_true("x-test-has-state", "state" in body)


def test_x_tweets_endpoint_accessible(runner: R) -> None:
    """GET /api/x/tweets returns 200 with empty list when cache is empty."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from app.server import app as _prod_app
    client = TestClient(_prod_app, raise_server_exceptions=False)
    resp = client.get("/api/x/tweets?limit=5")
    runner.assert_eq("x-tweets-code", resp.status_code, 200)
    body = resp.json()
    runner.assert_true("x-tweets-has-count", "count" in body)
    runner.assert_true("x-tweets-has-tweets", "tweets" in body)


def test_settings_x_endpoint_accessible(runner: R) -> None:
    """GET /api/settings/x returns 200 with configured flag."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from app.server import app as _prod_app
    client = TestClient(_prod_app, raise_server_exceptions=False)
    resp = client.get("/api/settings/x")
    runner.assert_eq("settings-x-code", resp.status_code, 200)
    body = resp.json()
    runner.assert_true("settings-x-has-configured", "configured" in body)


def test_fyers_unchanged(runner: R) -> None:
    """Fyers routes still work after X wiring change."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from app.server import app as _prod_app
    client = TestClient(_prod_app, raise_server_exceptions=False)
    resp = client.get("/api/settings/fyers")
    runner.assert_eq("fyers-code", resp.status_code, 200)
    body = resp.json()
    runner.assert_true("fyers-has-auth", "authenticated" in body)
    runner.assert_true("fyers-has-login-required", "login_required" in body)


def main() -> bool:
    runner = R()
    test_x_routes_registered_in_app_composition(runner)
    test_x_status_endpoint_accessible(runner)
    test_x_test_endpoint_accessible(runner)
    test_x_tweets_endpoint_accessible(runner)
    test_settings_x_endpoint_accessible(runner)
    test_fyers_unchanged(runner)
    return runner.summary()


if __name__ == "__main__":
    import sys as _sys
    _ok = main()
    _sys.exit(0 if _ok else 1)
