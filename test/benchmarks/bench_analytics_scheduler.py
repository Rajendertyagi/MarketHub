"""Benchmark Part 8 — Analytics scheduler scale.

Simulates REST latencies: 50ms, 250ms, 1s.
Active chains: 1, 10, 25, 50, 100.
Measures: total cycle duration, call count, max concurrency.
Verifies sequential vs concurrent behavior.
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
from market.models import OptionChainSnapshot, OptionStrikeRow


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


def _make_catalog():
    """Mock instrument catalog that returns a dummy row for any NSE query."""
    catalog = MagicMock()
    catalog.search.return_value = [{
        "exchange": "NSE", "instrument_type": "INDEX",
        "tradingsymbol": "NIFTY", "name": "Nifty 50",
    }]
    return catalog


async def run():
    rows = []
    rest_latencies = [0.050, 0.250, 1.0]
    chain_counts = [1, 10, 25, 50, 100]

    for rest_ms in rest_latencies:
        for n_chains in chain_counts:
            call_count = 0
            max_concurrent = 0
            current_concurrent = 0
            lock = asyncio.Lock()

            async def mock_option_chain(**kw):
                nonlocal call_count, max_concurrent, current_concurrent
                async with lock:
                    current_concurrent += 1
                    max_concurrent = max(max_concurrent, current_concurrent)
                await asyncio.sleep(rest_ms)
                async with lock:
                    current_concurrent -= 1
                call_count += 1
                return OptionChainSnapshot(
                    instrument_token="NSE_INDEX|NIFTY",
                    exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
                    spot_price=25000.0, atm_strike=25000.0, strikes=(),
                )

            mock_ms = MagicMock()
            mock_ms.option_chain = mock_option_chain
            mock_catalog = _make_catalog()

            analytics = MarketAnalyticsService(
                market_service=mock_ms,
                instrument_catalog=mock_catalog,
                refresh_interval=60.0,
            )

            for i in range(n_chains):
                analytics.register_chain(
                    f"analytics:NSE:INDEX:NIFTY:E{i:04d}", f"alert-{i}")

            t0 = time.perf_counter()
            await analytics._refresh_all_active()
            cycle_time = time.perf_counter() - t0

            rows.append({
                "scenario": f"chains_{n_chains}_rest_{int(rest_ms*1000)}ms",
                "active_chains": n_chains,
                "simulated_rest_latency_s": rest_ms,
                "total_cycle_time_s": round(cycle_time, 4),
                "expected_sequential_s": round(n_chains * rest_ms, 4),
                "call_count": call_count,
                "max_concurrency": max_concurrent,
                "is_sequential": max_concurrent == 1,
            })
            print(f"  chains={n_chains} rest={int(rest_ms*1000)}ms: cycle={cycle_time:.3f}s calls={call_count} max_conc={max_concurrent}")

            await analytics.stop(None)

    return {"rows": rows}
