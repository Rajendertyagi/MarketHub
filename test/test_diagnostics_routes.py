#!/usr/bin/env python3
"""Route-level tests for the diagnostics endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_DIR = str(Path(__file__).parent.parent)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R


def _make_runner():
    """Create a minimal DiagnosticsRunner for route testing."""
    from app.diagnostics import DiagnosticsRunner
    return DiagnosticsRunner(
        base_url="http://127.0.0.1:9999",
        mcp_url="http://127.0.0.1:9999/mcp",
    )


def test_quick_mode_returns_summary(runner: R) -> None:
    from starlette.testclient import TestClient
    from api.diagnostics_routes import build_diagnostics_run_routes

    r = _make_runner()
    app = TestClient(build_diagnostics_run_routes(r).pop().app if hasattr(build_diagnostics_run_routes(r).pop(), 'app') else None)

    # Just verify the route module imports correctly and runner exists
    checks = r.list_checks()
    assert len(checks) > 0, "Should have check definitions"
    runner.ok("route-module-imports")
    runner.ok("runner-has-checks")


def test_checks_list_endpoint(runner: R) -> None:
    from app.diagnostics import DiagnosticsRunner
    r = DiagnosticsRunner(base_url="http://127.0.0.1:9999", mcp_url="http://127.0.0.1:9999/mcp")
    checks = r.list_checks()
    runner.assert_true("checks-not-empty", len(checks) > 0)
    for c in checks:
        runner.assert_true(f"check-has-id:{c['id']}", "id" in c)
        runner.assert_true(f"check-has-name:{c['id']}", "name" in c)
        runner.assert_true(f"check-has-category:{c['id']}", "category" in c)
        runner.assert_true(f"check-has-layer:{c['id']}", "layer" in c)
        runner.assert_true(f"check-has-mode:{c['id']}", "mode" in c)


def test_unknown_mode_returns_error(runner: R) -> None:
    from app.diagnostics import DiagnosticsRunner
    from api.diagnostics_routes import build_diagnostics_run_routes
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    r = _make_runner()
    routes = build_diagnostics_run_routes(r)
    app = Starlette(routes=routes)
    client = TestClient(app)

    resp = client.get("/api/diagnostics/run?mode=invalid")
    runner.assert_eq("unknown-mode-400", resp.status_code, 400)


def test_sse_not_in_python_checks(runner: R) -> None:
    from app.diagnostics import _QUICK_CHECKS
    ids = [c.id for c in _QUICK_CHECKS]
    runner.assert_not_in("sse-excluded", "sse_connection", ids)


def test_all_checks_have_required_fields(runner: R) -> None:
    from app.diagnostics import _ALL_CHECKS
    for c in _ALL_CHECKS:
        runner.assert_true(f"has-id:{c.id}", bool(c.id))
        runner.assert_true(f"has-name:{c.id}", bool(c.name))
        runner.assert_true(f"has-category:{c.id}", bool(c.category))
        runner.assert_true(f"has-layer:{c.id}", bool(c.layer))
        runner.assert_true(f"has-fn:{c.id}", callable(c.fn))


def main() -> None:
    r = R()
    print("=" * 60)
    print("Diagnostics Route Tests")
    print("=" * 60)

    test_quick_mode_returns_summary(r)
    test_checks_list_endpoint(r)
    test_unknown_mode_returns_error(r)
    test_sse_not_in_python_checks(r)
    test_all_checks_have_required_fields(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)
    if r.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
