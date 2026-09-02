"""Benchmark Part 9 — Same-chain REST dedup.

100 alerts on same chain -> 1 REST call.
1000 alerts on same chain -> 1 REST call.
1000 alerts on 10 chains -> 10 REST calls.
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

    for n_alerts in [100, 1000]:
        call_count = 0

        async def mock_option_chain(**kw):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.001)
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

        chain_key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
        for i in range(n_alerts):
            analytics.register_chain(chain_key, f"alert-{i}")

        t0 = time.perf_counter()
        await analytics._refresh_one(chain_key)
        dt = time.perf_counter() - t0

        rows.append({
            "scenario": f"same_chain_{n_alerts}_alerts",
            "alerts_on_chain": n_alerts,
            "unique_chains": 1,
            "rest_calls": call_count,
            "expected_calls": 1,
            "cycle_time_s": round(dt, 4),
            "dedup_working": call_count == 1,
        })
        print(f"  {n_alerts} alerts on same chain: {call_count} REST call(s)")
        await analytics.stop(None)

    # Multi-chain: 1000 alerts across 10 unique chains
    call_count = 0

    async def mock_option_chain_multi(**kw):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.001)
        return OptionChainSnapshot(
            instrument_token="NSE_INDEX|NIFTY",
            exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
            spot_price=25000.0, atm_strike=25000.0, strikes=(),
        )

    mock_ms = MagicMock()
    mock_ms.option_chain = mock_option_chain_multi
    mock_catalog = _make_catalog()
    analytics = MarketAnalyticsService(
        market_service=mock_ms, instrument_catalog=mock_catalog,
        refresh_interval=60.0)

    for i in range(1000):
        chain_idx = i % 10
        chain_key = f"analytics:NSE:INDEX:NIFTY:E{chain_idx:04d}:2026-09-25"
        analytics.register_chain(chain_key, f"alert-{i}")

    await analytics._refresh_all_active()

    rows.append({
        "scenario": "1000_alerts_10_unique_chains",
        "alerts_on_chain": 1000,
        "unique_chains": 10,
        "rest_calls": call_count,
        "expected_calls": 10,
        "cycle_time_s": 0,
        "dedup_working": call_count == 10,
    })
    print(f"  1000 alerts, 10 chains: {call_count} REST call(s) (expected 10)")
    await analytics.stop(None)

    return {"rows": rows}
