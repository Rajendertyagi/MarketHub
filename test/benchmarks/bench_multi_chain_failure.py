"""Benchmark Part 10 — Multi-chain failure isolation.

Chain A = normal (50ms)
Chain B = slow (2s)
Chain C = normal (50ms)

Measure whether slow B delays C.
Then make B fail and measure effect on A and C.
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
from unittest.mock import MagicMock

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.market_analytics import MarketAnalyticsService
from market.models import OptionChainSnapshot


def _make_catalog():
    catalog = MagicMock()
    catalog.search.return_value = [{
        "exchange": "NSE", "instrument_type": "INDEX",
        "tradingsymbol": "NIFTY", "name": "Nifty 50",
    }]
    return catalog


async def run():
    rows = []

    call_log = []
    timestamps = {}

    async def mock_option_chain(**kw):
        chain = kw.get("chain_key", "unknown")
        call_log.append(chain)
        timestamps[chain] = {"start": time.perf_counter()}
        if "slow" in chain:
            await asyncio.sleep(2.0)
        elif "fail" in chain:
            await asyncio.sleep(0.01)
            raise RuntimeError("simulated failure")
        else:
            await asyncio.sleep(0.05)
        timestamps[chain]["end"] = time.perf_counter()
        return OptionChainSnapshot(
            instrument_token="NSE_INDEX|NIFTY",
            exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
            spot_price=25000.0, atm_strike=25000.0, strikes=(),
        )

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain
    mock_catalog = _make_catalog()
    analytics = MarketAnalyticsService(
        market_service=mock_ms, instrument_catalog=mock_catalog,
        refresh_interval=60.0)

    analytics.register_chain("analytics:NSE:INDEX:NIFTY:normal_A", "alert-a")
    analytics.register_chain("analytics:NSE:INDEX:NIFTY:slow_B", "alert-b")
    analytics.register_chain("analytics:NSE:INDEX:NIFTY:normal_C", "alert-c")

    t0 = time.perf_counter()
    await analytics._refresh_all_active()
    cycle_time = time.perf_counter() - t0

    rows.append({
        "scenario": "slow_chain_blocking",
        "chain_order": ["normal_A", "slow_B", "normal_C"],
        "slow_chain_latency_s": 2.0,
        "total_cycle_time_s": round(cycle_time, 4),
        "slow_chain_delays_others": cycle_time > 2.0,
        "call_log": list(call_log),
    })
    print(f"  Slow chain test: cycle={cycle_time:.3f}s (expected ~2.1s if sequential)")

    call_log.clear()
    timestamps.clear()
    analytics2 = MarketAnalyticsService(
        market_service=mock_ms, instrument_catalog=mock_catalog,
        refresh_interval=60.0)
    analytics2.register_chain("analytics:NSE:INDEX:NIFTY:normal_A", "alert-a")
    analytics2.register_chain("analytics:NSE:INDEX:NIFTY:fail_B", "alert-b")
    analytics2.register_chain("analytics:NSE:INDEX:NIFTY:normal_C", "alert-c")

    t0 = time.perf_counter()
    await analytics2._refresh_all_active()
    cycle_time2 = time.perf_counter() - t0

    a_has_ts = "normal_A" in timestamps and "end" in timestamps.get("normal_A", {})
    c_has_ts = "normal_C" in timestamps and "end" in timestamps.get("normal_C", {})

    rows.append({
        "scenario": "failed_chain_isolation",
        "total_cycle_time_s": round(cycle_time2, 4),
        "call_log": list(call_log),
        "all_chains_attempted": len(call_log) == 3,
        "normal_C_ran_after_fail_B": c_has_ts and a_has_ts,
    })
    print(f"  Failed chain test: cycle={cycle_time2:.3f}s calls={len(call_log)}")
    await analytics2.stop(None)
    await analytics.stop(None)

    return {"rows": rows}
