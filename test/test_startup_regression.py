#!/usr/bin/env python3
"""Application startup regression tests.

Guards the lifespan path: the composed app must START. A mis-decorated
coroutine (e.g. an ``@asynccontextmanager`` accidentally applied to a plain
coroutine) turns into a startup crash that only shows at runtime, so it is
asserted here.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_restore_fno_coverage_is_a_plain_coroutine() -> None:
    """The S9 startup helper must be an awaitable coroutine function.

    An ``@asynccontextmanager`` on it would make the call site yield a
    context manager instead of running the restore -> the lifespan would
    crash with "object can't be awaited".
    """
    import inspect

    from app.server import _restore_fno_coverage
    assert not inspect.isasyncgenfunction(_restore_fno_coverage), (
        "must not be an async generator (no stray @asynccontextmanager)")
    assert inspect.iscoroutinefunction(_restore_fno_coverage), (
        "must be a plain coroutine function")
    # It must be directly awaitable (not wrapped in a context manager).
    assert not hasattr(_restore_fno_coverage, "__wrapped__"), (
        "must not be wrapped by a decorator that changes the await contract")


def test_restore_fno_coverage_runs_and_isolates_failures() -> None:
    """The restore must run, and a plan failure must never raise."""
    import asyncio

    from app import server as srv

    calls: list[str] = []

    class _Plan:
        desired = 3
        keys_by_provider = {"upstox": ["NSE_FO|1"]}

    class _Coverage:
        def plan(self, *a, **k):
            calls.append("plan")
            return _Plan()

    class _Subs:
        def set_derivatives_universe(self, keys):
            calls.append("set")

        def lease_requests(self):
            calls.append("lease_requests")
            return []

    class _Manager:
        """The lease manager owns the subscription apply step."""

        def apply_demand(self, requests):
            calls.append("apply_demand")
            return {"requested": 0}

        async def reconcile(self):
            calls.append("reconcile")
            return {"results": {}}

    orig_cov = srv._fno_coverage_service
    orig_sub = srv._subscription_service
    orig_mgr = srv._subscription_manager
    try:
        srv._fno_coverage_service = _Coverage()
        srv._subscription_service = _Subs()
        srv._subscription_manager = _Manager()
        asyncio.run(srv._restore_fno_coverage())
        assert calls == ["plan", "set", "lease_requests", "apply_demand",
                         "reconcile"]
    finally:
        srv._fno_coverage_service = orig_cov
        srv._subscription_service = orig_sub
        srv._subscription_manager = orig_mgr


def test_restore_fno_coverage_swallows_plan_failure() -> None:
    """A plan failure must be logged, never propagated (startup must go on)."""
    import asyncio

    from app import server as srv

    class _Failing:
        def plan(self, *a, **k):
            raise RuntimeError("catalog unavailable")

    class _Subs:
        def set_derivatives_universe(self, keys):
            raise AssertionError("must not be reached")

        async def reconcile(self, feed_provider):
            raise AssertionError("must not be reached")

    orig_cov, orig_sub = srv._fno_coverage_service, srv._subscription_service
    try:
        srv._fno_coverage_service = _Failing()
        srv._subscription_service = _Subs()
        asyncio.run(srv._restore_fno_coverage())  # must not raise
    finally:
        srv._fno_coverage_service, srv._subscription_service = orig_cov, orig_sub


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
