#!/usr/bin/env python3
"""Phase 1 analytics lifecycle/concurrency hardening regression tests.

Deterministic tests for generation-guarded commits, bounded lock/state cleanup,
and deterministic scheduler shutdown in app.market_analytics.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R

from market.models import OptionChainSnapshot


def _make_catalog():
    cat = MagicMock()

    def _search(exchange=None, q=None, limit=5):
        return [{"instrument_token": "X", "tradingsymbol": q or "X",
                 "exchange": exchange or "NSE"}]

    cat.search.side_effect = _search
    return cat


def _make_snapshot(chain_key="X", spot=25000.0):
    return OptionChainSnapshot(
        instrument_token=chain_key,
        exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
        spot_price=spot, atm_strike=25000.0, strikes=(),
    )


class _BG:
    """Minimal BackgroundTaskManager-like harness for scheduler tests."""

    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}

    async def start(self, name: str, coro):
        self.tasks[name] = asyncio.create_task(coro)

    async def cancel(self, name: str):
        task = self.tasks.get(name)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def cancel_and_wait(self, name: str):
        task = self.tasks.get(name)
        if task and not task.done():
            task.cancel()
        if task:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def _internal_maps_empty(svc) -> bool:
    return (
        not svc._locks
        and not svc._cache
        and not svc._last_refresh
        and not svc._failure_count
        and not svc._generation
        and not svc._inflight
        and not svc._scheduler_refresh_tasks
    )


async def t1_refresh_unregister_cleans_state(runner: R) -> None:
    name = "T1-refresh-unregister-cleanup"
    from app.market_analytics import MarketAnalyticsService

    async def mock_option_chain(**kw):
        return _make_snapshot()

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
    svc.register_chain(key, "a1")
    await svc._refresh_one(key)
    runner.assert_true(name + "-cached", svc.get_snapshot_raw(key) is not None,
                       "expected snapshot after refresh")
    svc.unregister_chain(key, "a1")
    runner.assert_true(name + "-no-dependents", not svc.has_chain(key),
                       "chain should be unregistered")
    runner.assert_true(name + "-maps-empty", _internal_maps_empty(svc),
                       f"expected bounded cleanup, got locks={len(svc._locks)} cache={len(svc._cache)} gen={len(svc._generation)} inflight={len(svc._inflight)}")


async def t2_300_distinct_chains_bounded(runner: R) -> None:
    name = "T2-300-bounded"
    from app.market_analytics import MarketAnalyticsService

    async def mock_option_chain(**kw):
        ct = kw.get("tradingsymbol", "X")
        return _make_snapshot(ct)

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
        max_concurrent_refreshes=8,
    )
    for i in range(300):
        key = f"analytics:NSE:INDEX:SYM{i}:2026-09-25"
        aid = f"alert-{i}"
        svc.register_chain(key, aid)
        await svc._refresh_one(key)
        svc.unregister_chain(key, aid)
    runner.assert_true(name + "-locks", len(svc._locks) == 0,
                       f"lock map not bounded: {len(svc._locks)}")
    runner.assert_true(name + "-cache", len(svc._cache) == 0,
                       f"cache not bounded: {len(svc._cache)}")
    runner.assert_true(name + "-gen", len(svc._generation) == 0,
                       f"generation map not bounded: {len(svc._generation)}")
    runner.assert_true(name + "-inflight", len(svc._inflight) == 0,
                       f"inflight map not bounded: {len(svc._inflight)}")
    runner.assert_true(name + "-deps", len(svc._dependents) == 0,
                       f"dependents not bounded: {len(svc._dependents)}")


async def t3_unregister_during_inflight_no_stale_write(runner: R) -> None:
    name = "T3-unregister-inflight"
    from app.market_analytics import MarketAnalyticsService

    started = asyncio.Event()
    release = asyncio.Event()

    async def mock_option_chain(**kw):
        started.set()
        await release.wait()
        return _make_snapshot(spot=111.0)

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
    svc.register_chain(key, "a1")
    task = asyncio.create_task(svc._refresh_one(key))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    svc.unregister_chain(key, "a1")
    release.set()
    await asyncio.wait_for(task, timeout=1.0)
    runner.assert_true(name + "-no-cache", svc.get_snapshot_raw(key) is None,
                       "stale cache written after unregister")
    runner.assert_true(name + "-no-last-refresh", key not in svc._last_refresh,
                       "stale last_refresh written after unregister")
    runner.assert_true(name + "-no-failure", key not in svc._failure_count,
                       "stale failure_count written after unregister")
    runner.assert_true(name + "-maps-empty", _internal_maps_empty(svc),
                       f"state not cleaned: locks={len(svc._locks)} gen={len(svc._generation)} inflight={len(svc._inflight)}")


async def t4_reregister_different_alert_no_stale_overwrite(runner: R) -> None:
    name = "T4-reregister-no-stale-overwrite"
    from app.market_analytics import MarketAnalyticsService

    started = asyncio.Event()
    release = asyncio.Event()
    call_n = [0]

    async def mock_option_chain(**kw):
        call_n[0] += 1
        if call_n[0] == 1:
            started.set()
            await release.wait()
            return _make_snapshot(spot=100.0)
        return _make_snapshot(spot=200.0)

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
    svc.register_chain(key, "alert-old")
    old_task = asyncio.create_task(svc._refresh_one(key))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    # Re-register under different alert while old refresh is still in-flight.
    svc.unregister_chain(key, "alert-old")
    svc.register_chain(key, "alert-new")
    # Let old refresh finish; generation guard must prevent stale commit.
    release.set()
    await asyncio.wait_for(old_task, timeout=1.0)
    runner.assert_true(name + "-no-stale-after-old", svc.get_snapshot_raw(key) is None,
                       "old generation committed after re-register")
    # New generation refresh must populate state and remain authoritative.
    await svc._refresh_one(key)
    snap_new = svc.get_snapshot_raw(key)
    runner.assert_true(name + "-new-present", snap_new is not None,
                       "new snapshot missing")
    runner.assert_eq(name + "-new-spot", getattr(snap_new, "spot_price", None), 200.0)
    runner.assert_true(name + "-active", svc.has_chain(key), "chain should remain active")
    svc.unregister_chain(key, "alert-new")
    runner.assert_true(name + "-cleanup", _internal_maps_empty(svc),
                       "cleanup failed after final unregister")


async def t5_stop_with_multiple_active_refreshes(runner: R) -> None:
    name = "T5-stop-deterministic"
    from app.market_analytics import MarketAnalyticsService

    started = asyncio.Event()
    release = asyncio.Event()
    started_count = [0]
    commits_after_stop = [0]
    stop_done = asyncio.Event()

    async def mock_option_chain(**kw):
        started_count[0] += 1
        if started_count[0] >= 3:
            started.set()
        await release.wait()
        if stop_done.is_set():
            commits_after_stop[0] += 1
        return _make_snapshot()

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
        max_concurrent_refreshes=4,
    )
    for i in range(3):
        svc.register_chain(f"analytics:NSE:INDEX:SYM{i}:2026-09-25", f"a{i}")
    bg = _BG()
    await svc.start(bg)
    # Start scheduler-owned refresh work and wait until all providers are in-flight.
    refresh_task = asyncio.create_task(svc._refresh_all_active())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    stop_task = asyncio.create_task(svc.stop(bg))
    await asyncio.sleep(0)  # let stop begin/cancel
    release.set()
    await asyncio.wait_for(stop_task, timeout=2.0)
    stop_done.set()
    try:
        await asyncio.wait_for(refresh_task, timeout=2.0)
    except (asyncio.CancelledError, Exception):
        pass
    await asyncio.sleep(0.05)
    runner.assert_eq(name + "-no-post-stop-commits", commits_after_stop[0], 0)
    runner.assert_true(name + "-no-sched-tasks", len(svc._scheduler_refresh_tasks) == 0,
                       f"scheduler refresh tasks remain: {len(svc._scheduler_refresh_tasks)}")
    runner.assert_true(name + "-registrations-preserved", len(svc.get_active_chains()) == 3,
                       f"active registrations lost: {svc.get_active_chains()}")
    # cleanup
    for i in range(3):
        svc.unregister_chain(f"analytics:NSE:INDEX:SYM{i}:2026-09-25", f"a{i}")


async def t6_concurrent_register_unregister_refresh_churn(runner: R) -> None:
    name = "T6-churn"
    from app.market_analytics import MarketAnalyticsService

    async def mock_option_chain(**kw):
        await asyncio.sleep(0)
        return _make_snapshot()

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
        max_concurrent_refreshes=4,
    )
    keys = [f"analytics:NSE:INDEX:CHURN{i}:2026-09-25" for i in range(20)]

    async def worker(i: int):
        key = keys[i % len(keys)]
        aid = f"alert-{i}"
        for _ in range(10):
            svc.register_chain(key, aid)
            await svc._refresh_one(key)
            svc.unregister_chain(key, aid)
            await asyncio.sleep(0)

    await asyncio.gather(*(worker(i) for i in range(20)))
    runner.assert_true(name + "-deps-empty", len(svc._dependents) == 0,
                       f"dependents leaked: {len(svc._dependents)}")
    runner.assert_true(name + "-locks-bounded", len(svc._locks) == 0,
                       f"locks leaked: {len(svc._locks)}")
    runner.assert_true(name + "-inflight-empty", len(svc._inflight) == 0,
                       f"inflight leaked: {len(svc._inflight)}")
    runner.assert_true(name + "-cache-empty", len(svc._cache) == 0,
                       f"cache leaked: {len(svc._cache)}")


async def t7_repeated_start_stop_restart(runner: R) -> None:
    name = "T7-restart-cycles"
    from app.market_analytics import MarketAnalyticsService

    async def mock_option_chain(**kw):
        await asyncio.sleep(0)
        return _make_snapshot()

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
    svc.register_chain(key, "a1")
    bg = _BG()
    for i in range(5):
        await svc.start(bg)
        await svc._refresh_all_active()
        await svc.stop(bg)
        runner.assert_true(f"{name}-{i}-active", svc.has_chain(key),
                           "registration lost across stop/start")
    runner.assert_true(name + "-no-sched-tasks", len(svc._scheduler_refresh_tasks) == 0,
                       "scheduler tasks leaked")
    svc.unregister_chain(key, "a1")
    runner.assert_true(name + "-cleanup", _internal_maps_empty(svc),
                       "cleanup failed after cycles")


async def t8_cancellation_and_provider_failure_paths(runner: R) -> None:
    name = "T8-cancel-failure"
    from app.market_analytics import MarketAnalyticsService

    # Provider failure path: no stale commit, failure counted for active chain.
    async def fail_option_chain(**kw):
        raise RuntimeError("boom")

    mock_ms = MagicMock()
    mock_ms.option_chain = fail_option_chain
    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
    svc.register_chain(key, "a1")
    await svc._refresh_one(key)
    runner.assert_true(name + "-no-cache-on-fail", svc.get_snapshot_raw(key) is None,
                       "cache written on provider failure")
    runner.assert_eq(name + "-failure-count", svc._failure_count.get(key), 1)
    svc.unregister_chain(key, "a1")
    runner.assert_true(name + "-cleanup-after-fail", _internal_maps_empty(svc),
                       "cleanup failed after provider failure")

    # Cancellation path: in-flight refresh cancelled, no commit, state cleaned.
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_option_chain(**kw):
        started.set()
        await release.wait()
        return _make_snapshot()

    mock_ms2 = MagicMock()
    mock_ms2.option_chain = slow_option_chain
    svc2 = MarketAnalyticsService(
        market_service=mock_ms2,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    key2 = "analytics:NSE:INDEX:BANKNIFTY:2026-09-25"
    svc2.register_chain(key2, "a2")
    task = asyncio.create_task(svc2._refresh_one(key2))
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()
    release.set()
    try:
        await task
    except asyncio.CancelledError:
        pass
    runner.assert_true(name + "-no-cache-on-cancel", svc2.get_snapshot_raw(key2) is None,
                       "cache written on cancellation")
    # chain still active (registration preserved), inflight cleaned
    runner.assert_true(name + "-still-active", svc2.has_chain(key2),
                       "registration lost on cancellation")
    runner.assert_eq(name + "-inflight-clean", svc2._inflight.get(key2, 0), 0)
    svc2.unregister_chain(key2, "a2")
    runner.assert_true(name + "-cleanup-after-cancel", _internal_maps_empty(svc2),
                       "cleanup failed after cancellation")


async def main() -> int:
    runner = R()
    try:
        print("  Phase 1 — Analytics Lifecycle Hardening Regression Tests")
        print("=" * 60)
        tests = [
            t1_refresh_unregister_cleans_state,
            t2_300_distinct_chains_bounded,
            t3_unregister_during_inflight_no_stale_write,
            t4_reregister_different_alert_no_stale_overwrite,
            t5_stop_with_multiple_active_refreshes,
            t6_concurrent_register_unregister_refresh_churn,
            t7_repeated_start_stop_restart,
            t8_cancellation_and_provider_failure_paths,
        ]
        for fn in tests:
            try:
                await fn(runner)
            except Exception as exc:
                runner.fail(fn.__name__, str(exc))
    except Exception as exc:
        runner.fail("main", str(exc))

    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    asyncio.run(main())
