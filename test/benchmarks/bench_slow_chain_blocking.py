"""Benchmark — Slow-chain blocking measurement.

Measures actual start/end timestamps for three chains:
  A = 50ms   B = 1000ms   C = 50ms

Records exact timestamps to prove head-of-line blocking.
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

    # Scenario 1: A=50ms, B=1000ms, C=50ms
    timestamps = {"A_start": None, "A_end": None, "B_start": None, "B_end": None, "C_start": None, "C_end": None}

    async def slow_chain_mock(name, latency_ms):
        async def _mock_option_chain(**kw):
            key = f"{name}_start"
            timestamps[key] = time.perf_counter_ns()
            await asyncio.sleep(latency_ms / 1000.0)
            key = f"{name}_end"
            timestamps[key] = time.perf_counter_ns()
            return OptionChainSnapshot(
                instrument_token=f"NSE_INDEX|{name}",
                exchange="NSE", tradingsymbol=name, expiry="2026-09-25",
                spot_price=25000.0, atm_strike=25000.0, strikes=(),
            )
        return _mock_option_chain

    mock_ms = MagicMock()
    mock_ms.option_chain = slow_chain_mock("A", 50)
    mock_catalog = _make_catalog()

    analytics = MarketAnalyticsService(
        market_service=mock_ms,
        instrument_catalog=mock_catalog,
        refresh_interval=60.0,
    )
    analytics.register_chain("analytics:NSE:INDEX:NIFTY:E0", "alert-0")
    analytics.register_chain("analytics:NSE:INDEX:NIFTY:E1", "alert-1")
    analytics.register_chain("analytics:NSE:INDEX:NIFTY:E2", "alert-2")

    # Patch individual chains with different latencies
    calls_log = []
    async def patched_chain(i, latency_ms):
        async def _inner(**kw):
            calls_log.append(("start", i, time.perf_counter_ns()))
            await asyncio.sleep(latency_ms / 1000.0)
            calls_log.append(("end", i, time.perf_counter_ns()))
            return OptionChainSnapshot(
                instrument_token=f"NSE_INDEX|CHAIN{i}",
                exchange="NSE", tradingsymbol=f"CHAIN{i}", expiry="2026-09-25",
                spot_price=25000.0, atm_strike=25000.0, strikes=(),
            )
        return _inner

    # Rebuild with per-chain latency control
    mock_ms2 = MagicMock()
    latencies = [50, 1000, 50]
    for i in range(3):
        setattr(mock_ms2, f"option_chain_chain_{i}", patched_chain(i, latencies[i]))

    # Use a single mock that picks latency by chain index
    call_order = []
    async def unified_mock(**kw):
        chain_id = kw.get("chain_index", kw.get("instrument_token", ""))
        # Extract chain index from keyword
        idx = None
        for k, v in kw.items():
            if k == "chain_index":
                idx = v
                break
        if idx is None:
            idx = 0
        call_order.append(("call_start", int(time.perf_counter_ns())))
        await asyncio.sleep(latencies[idx] / 1000.0)
        call_order.append(("call_end", int(time.perf_counter_ns())))
        return OptionChainSnapshot(
            instrument_token=f"NSE_INDEX|CHAIN{idx}",
            exchange="NSE", tradingsymbol=f"CHAIN{idx}", expiry="2026-09-25",
            spot_price=25000.0, atm_strike=25000.0, strikes=(),
        )

    mock_ms_real = MagicMock()
    mock_ms_real.option_chain = unified_mock

    analytics2 = MarketAnalyticsService(
        market_service=mock_ms_real,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    for i in range(3):
        analytics2.register_chain(f"analytics:NSE:INDEX:CHAIN{i}:E{i}", f"alert-{i}")

    t0 = time.perf_counter_ns()
    await analytics2._refresh_all_active()
    total_ns = time.perf_counter_ns() - t0
    total_ms = total_ns / 1e6

    rows.append({
        "scenario": "slow_B_50_1000_50",
        "A_latency_ms": 50,
        "B_latency_ms": 1000,
        "C_latency_ms": 50,
        "total_cycle_ms": round(total_ms, 2),
        "expected_sequential_ms": 1100,
        "call_count": len(call_order) // 2,
        "observed_c_delay_caused_by_b_ms": round(total_ms - 1100, 2) if total_ms > 1100 else 0,
    })
    print(f"  slow_B: total={total_ms:.1f}ms (expected ~1100ms)", flush=True)

    await analytics2.stop(None)

    # Scenario 2: A=50ms, B=throws, C=50ms (failure isolation)
    call_order2 = []
    fail_count = [0]
    c_called = [False]

    async def failing_mock(**kw):
        idx = 0
        for k, v in kw.items():
            if k == "chain_index":
                idx = v
                break
        call_order2.append(("call_start", idx, int(time.perf_counter_ns())))
        if idx == 1:
            fail_count[0] += 1
            raise RuntimeError(f"controlled failure in chain {idx}")
        await asyncio.sleep(50 / 1000.0)
        if idx == 2:
            c_called[0] = True
        call_order2.append(("call_end", idx, int(time.perf_counter_ns())))
        return OptionChainSnapshot(
            instrument_token=f"NSE_INDEX|CHAIN{idx}",
            exchange="NSE", tradingsymbol=f"CHAIN{idx}", expiry="2026-09-25",
            spot_price=25000.0, atm_strike=25000.0, strikes=(),
        )

    mock_ms_fail = MagicMock()
    mock_ms_fail.option_chain = failing_mock

    analytics3 = MarketAnalyticsService(
        market_service=mock_ms_fail,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    for i in range(3):
        analytics3.register_chain(f"analytics:NSE:INDEX:CHAIN{i}:E{i}", f"alert-{i}")

    t0 = time.perf_counter_ns()
    try:
        await analytics3._refresh_all_active()
        cycle_ok = True
    except Exception as e:
        cycle_ok = False
    total_ns = time.perf_counter_ns() - t0
    total_ms = total_ns / 1e6

    rows.append({
        "scenario": "B_fails_A_then_C",
        "A_latency_ms": 50,
        "B_behavior": "raises RuntimeError",
        "C_latency_ms": 50,
        "total_cycle_ms": round(total_ms, 2),
        "B_call_count": fail_count[0],
        "C_was_called": c_called[0],
        "cycle_completed": cycle_ok,
        "call_order": [(op, idx) for op, idx, _ in call_order2],
    })
    print(f"  B_fails: total={total_ms:.1f}ms B_calls={fail_count[0]} C_called={c_called[0]}", flush=True)

    await analytics3.stop(None)

    # Scenario 3: A=50ms, B=500ms then fail, C=50ms
    call_order3 = []
    fail_count3 = [0]
    c_called3 = [False]

    async def slow_then_fail_mock(**kw):
        idx = 0
        for k, v in kw.items():
            if k == "chain_index":
                idx = v
                break
        call_order3.append(("call_start", idx, int(time.perf_counter_ns())))
        if idx == 1:
            await asyncio.sleep(500 / 1000.0)
            fail_count3[0] += 1
            raise RuntimeError(f"delayed failure in chain {idx}")
        await asyncio.sleep(50 / 1000.0)
        if idx == 2:
            c_called3[0] = True
        call_order3.append(("call_end", idx, int(time.perf_counter_ns())))
        return OptionChainSnapshot(
            instrument_token=f"NSE_INDEX|CHAIN{idx}",
            exchange="NSE", tradingsymbol=f"CHAIN{idx}", expiry="2026-09-25",
            spot_price=25000.0, atm_strike=25000.0, strikes=(),
        )

    mock_ms_slowfail = MagicMock()
    mock_ms_slowfail.option_chain = slow_then_fail_mock

    analytics4 = MarketAnalyticsService(
        market_service=mock_ms_slowfail,
        instrument_catalog=_make_catalog(),
        refresh_interval=60.0,
    )
    for i in range(3):
        analytics4.register_chain(f"analytics:NSE:INDEX:CHAIN{i}:E{i}", f"alert-{i}")

    t0 = time.perf_counter_ns()
    try:
        await analytics4._refresh_all_active()
        cycle_ok2 = True
    except Exception as e:
        cycle_ok2 = False
    total_ns = time.perf_counter_ns() - t0
    total_ms = total_ns / 1e6

    rows.append({
        "scenario": "B_slow_then_fail_50_500_50",
        "A_latency_ms": 50,
        "B_behavior": "500ms delay then raises RuntimeError",
        "C_latency_ms": 50,
        "total_cycle_ms": round(total_ms, 2),
        "B_call_count": fail_count3[0],
        "C_was_called": c_called3[0],
        "cycle_completed": cycle_ok2,
    })
    print(f"  B_slow_fail: total={total_ms:.1f}ms B_calls={fail_count3[0]} C_called={c_called3[0]}", flush=True)

    await analytics4.stop(None)

    return {"rows": rows}
