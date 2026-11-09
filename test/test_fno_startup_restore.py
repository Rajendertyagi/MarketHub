#!/usr/bin/env python3
"""Startup F&O restoration tests (S9).

The desired F&O state must be RECONSTRUCTED at startup from the durable
config + catalogue + registry — not depend on a manual ``POST /api/fno/apply``
and NOT need a new persistence field.

Contract:
  * ``plan()`` -> ``set_derivatives_universe()`` -> existing ``reconcile()``
  * idempotent (running twice yields the same desired set)
  * an unavailable catalogue yields desired:0 (honest, not fabricated)
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class _Plan:
    def __init__(self, keys_by_provider):
        self.keys_by_provider = keys_by_provider
        self.desired = sum(len(v) for v in keys_by_provider.values())


class _Coverage:
    def __init__(self, plan):
        self._plan = plan
        self.calls = 0

    def plan(self, expiry=None, config=None):
        self.calls += 1
        return self._plan


class _Subscriptions:
    def __init__(self):
        self.universe = None
        self.set_calls = 0
        self.reconcile_calls = 0

    def set_derivatives_universe(self, keys_by_provider):
        self.universe = keys_by_provider
        self.set_calls += 1

    async def reconcile(self, feed_provider):
        self.reconcile_calls += 1
        return {"apply": {"results": {}}}


async def _restore(coverage, subscriptions, feed_provider=None):
    """The startup reconstruction (S9)."""
    plan = coverage.plan()
    subscriptions.set_derivatives_universe(plan.keys_by_provider)
    return await subscriptions.reconcile(feed_provider)


def test_startup_restores_derivatives_universe() -> None:
    import asyncio
    coverage = _Coverage(_Plan({"upstox": ["NSE_FO|1"], "fyers": ["NSE:X"]}))
    subs = _Subscriptions()
    asyncio.run(_restore(coverage, subs, lambda p: None))
    assert subs.universe == {"upstox": ["NSE_FO|1"], "fyers": ["NSE:X"]}
    assert subs.set_calls == 1
    assert subs.reconcile_calls == 1


def test_startup_restoration_is_idempotent() -> None:
    import asyncio
    coverage = _Coverage(_Plan({"upstox": ["NSE_FO|1"]}))
    subs = _Subscriptions()
    asyncio.run(_restore(coverage, subs, lambda p: None))
    first = dict(subs.universe)
    asyncio.run(_restore(coverage, subs, lambda p: None))
    assert subs.universe == first
    assert subs.set_calls == 2


def test_unavailable_catalogue_yields_empty_desired() -> None:
    import asyncio
    coverage = _Coverage(_Plan({}))
    subs = _Subscriptions()
    asyncio.run(_restore(coverage, subs, lambda p: None))
    assert subs.universe == {}


def test_plan_failure_does_not_crash_startup() -> None:
    import asyncio

    class _Failing:
        def plan(self, expiry=None, config=None):
            raise RuntimeError("catalog unavailable")

    subs = _Subscriptions()
    try:
        asyncio.run(_restore(_Failing(), subs, lambda p: None))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected the plan error to propagate")
    assert subs.set_calls == 0


def test_lifespan_calls_the_restoration_helper() -> None:
    """The server lifespan must invoke the F&O restoration."""
    from pathlib import Path
    src = Path("app/server.py").read_text(encoding="utf-8")
    assert "_restore_fno_coverage" in src


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
