#!/usr/bin/env python3
"""B8 FIX 2 — Analytics scheduler concurrent refresh tests.

Verifies that independent chains refresh concurrently (not sequentially),
that one slow/failing chain does not block others, and that same-chain
dedup is preserved.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch

import os
import sys

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R
from market.models import OptionChainSnapshot


def _make_catalog():
    """Return a mock catalog that returns the correct tradingsymbol per chain."""
    cat = MagicMock()
    def _search(exchange=None, q=None, limit=5):
        return [{"instrument_token": "X", "tradingsymbol": q or "X",
                 "exchange": exchange or "NSE"}]
    cat.search.side_effect = _search
    return cat
    return OptionChainSnapshot(
        instrument_token=chain_key,
        exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
        spot_price=25000.0, atm_strike=25000.0, strikes=(),
    )


# ===================================================================
# Tests
# ===================================================================


async def t1_concurrent_refresh_proved(runner: R) -> None:
    """Chains A=50ms, B=1000ms, C=50ms refresh concurrently.

    If sequential: total ~ 1100ms.
    If concurrent with bound=4: total ~ 1050ms (dominated by B).
    This test proves C no longer waits behind B.
    """
    name = "T1-concurrent"
    from app.market_analytics import MarketAnalyticsService

    timestamps: dict[str, list[float]] = {}

    async def mock_option_chain(**kw):
        ct = kw.get("tradingsymbol", "X")
        latencies = {"A": 0.05, "B": 1.0, "C": 0.05}
        lat = latencies.get(ct, 0.05)
        timestamps.setdefault(ct, []).append(time.monotonic())
        await asyncio.sleep(lat)
        timestamps.setdefault(ct, []).append(time.monotonic())
        return _make_snapshot(f"NSE:INDEX:{ct}")

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    # Provide a catalog that returns the correct tradingsymbol per chain
    mock_catalog = MagicMock()
    def _catalog_search(exchange=None, q=None, limit=5):
        return [{"instrument_token": "X", "tradingsymbol": q or "X",
                 "exchange": exchange or "NSE"}]
    mock_catalog.search.side_effect = _catalog_search

    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=mock_catalog,
        refresh_interval=60.0,
        max_concurrent_refreshes=4,
    )
    svc.register_chain("analytics:A:E0", "alert-a")
    svc.register_chain("analytics:B:E0", "alert-b")
    svc.register_chain("analytics:C:E0", "alert-c")

    t0 = time.monotonic()
    await svc._refresh_all_active()
    total = time.monotonic() - t0

    # Total should be dominated by B (~1s), not sum of all (~1.1s)
    runner.assert_true(
        name + "-total",
        total < 1.5,
        f"expected < 1.5s for concurrent refresh, got {total:.3f}s",
    )
    # Verify all three chains were refreshed
    runner.assert_true(name + "-a", "A" in timestamps, "chain A not refreshed")
    runner.assert_true(name + "-b", "B" in timestamps, "chain B not refreshed")
    runner.assert_true(name + "-c", "C" in timestamps, "chain C not refreshed")


async def t2_failure_isolation(runner: R) -> None:
    """B fails → A and C still succeed."""
    name = "T2-fail-iso"
    from app.market_analytics import MarketAnalyticsService

    results: dict[str, bool] = {}

    async def mock_option_chain(**kw):
        ct = kw.get("tradingsymbol", "X")
        if ct == "B":
            raise RuntimeError("controlled failure")
        await asyncio.sleep(0.01)
        results[ct] = True
        return _make_snapshot(f"NSE:INDEX:{ct}")

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    mock_catalog = _make_catalog()

    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=mock_catalog,
        refresh_interval=60.0,
        max_concurrent_refreshes=4,
    )
    svc.register_chain("analytics:A:E0", "alert-a")
    svc.register_chain("analytics:B:E0", "alert-b")
    svc.register_chain("analytics:C:E0", "alert-c")

    await svc._refresh_all_active()

    runner.assert_true(name + "-a-ok", results.get("A") is True, "A should succeed")
    runner.assert_true(name + "-c-ok", results.get("C") is True, "C should succeed")
    runner.assert_true(
        name + "-b-failed",
        svc.get_snapshot("analytics:B:E0") is None,
        "B snapshot should be None after failure",
    )


async def t3_same_chain_dedup(runner: R) -> None:
    """Same chain key → exactly one REST refresh (per-chain lock)."""
    name = "T3-dedup"
    from app.market_analytics import MarketAnalyticsService

    call_count = [0]

    async def mock_option_chain(**kw):
        call_count[0] += 1
        await asyncio.sleep(0.01)
        return _make_snapshot()

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    mock_catalog = _make_catalog()

    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=mock_catalog,
        refresh_interval=60.0,
        max_concurrent_refreshes=4,
    )
    # Register same chain twice (two alerts depend on it)
    svc.register_chain("analytics:NSE:INDEX:NIFTY:E0", "alert-1")
    svc.register_chain("analytics:NSE:INDEX:NIFTY:E0", "alert-2")

    await svc._refresh_all_active()

    runner.assert_eq(name + "-one-call", call_count[0], 1)


async def t4_concurrency_bound_respected(runner: R) -> None:
    """Never more than max_concurrent_refreshes chains running at once."""
    name = "T4-bound"
    from app.market_analytics import MarketAnalyticsService

    active = [0]
    max_seen = [0]

    async def mock_option_chain(**kw):
        active[0] += 1
        max_seen[0] = max(max_seen[0], active[0])
        await asyncio.sleep(0.1)
        active[0] -= 1
        return _make_snapshot()

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    mock_catalog = _make_catalog()

    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=mock_catalog,
        refresh_interval=60.0,
        max_concurrent_refreshes=2,
    )
    for i in range(6):
        svc.register_chain(f"analytics:CHAIN{i}:E{i}", f"alert-{i}")

    await svc._refresh_all_active()

    runner.assert_true(
        name + "-bound",
        max_seen[0] <= 2,
        f"max concurrent was {max_seen[0]}, expected <= 2",
    )


async def t5_slow_chain_does_not_block_others(runner: R) -> None:
    """C no longer waits ~1s behind B (proves head-of-line blocking removed)."""
    name = "T5-no-block"
    from app.market_analytics import MarketAnalyticsService

    timestamps: dict[str, list[float]] = {}

    async def mock_option_chain(**kw):
        ct = kw.get("tradingsymbol", "X")
        latencies = {"A": 0.05, "B": 1.0, "C": 0.05}
        lat = latencies.get(ct, 0.05)
        timestamps.setdefault(ct, []).append(time.monotonic())
        await asyncio.sleep(lat)
        timestamps.setdefault(ct, []).append(time.monotonic())
        return _make_snapshot(f"NSE:INDEX:{ct}")

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    mock_catalog = _make_catalog()

    svc = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=mock_catalog,
        refresh_interval=60.0,
        max_concurrent_refreshes=4,
    )
    svc.register_chain("analytics:A:E0", "alert-a")
    svc.register_chain("analytics:B:E0", "alert-b")
    svc.register_chain("analytics:C:E0", "alert-c")

    t0 = time.monotonic()
    await svc._refresh_all_active()

    # C starts near A (not after B finishes)
    c_start = timestamps["C"][0] - t0

    runner.assert_true(
        name + "-c-early",
        c_start < 0.3,
        f"C started at {c_start:.3f}s — should start within 0.3s (not wait for B's 1s)",
    )


# ===================================================================
# Main
# ===================================================================

async def main() -> int:
    runner = R()
    try:
        print("  B8 FIX 2 — Analytics Scheduler Concurrent Refresh Tests")
        print("=" * 50)
        tests = [
            t1_concurrent_refresh_proved,
            t2_failure_isolation,
            t3_same_chain_dedup,
            t4_concurrency_bound_respected,
            t5_slow_chain_does_not_block_others,
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
