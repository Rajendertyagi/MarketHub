"""Benchmark Part 9 — Same-chain REST dedup.

100 alerts on same chain → 1 REST call.
1000 alerts on same chain → 1 REST call.
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
from unittest.mock import MagicMock

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.market_analytics import MarketAnalyticsService
from market.models import OptionChainSnapshot


async def run():
    rows = []

    for n_alerts in [100, 1000]:
        call_count = 0

        async def mock_option_chain(**kw):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.001)  # minimal delay
            return OptionChainSnapshot(
                chain_key=kw.get("chain_key", "test"),
                canonical_underlying_id="NSE:INDEX:NIFTY",
                exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
                spot_price=25000.0, pcr_oi=1.1, pcr_volume=None,
                max_pain=25000.0, iv_skew=0.02,
                strikes=[], received_ts=None, calculated_at=None,
                stale_after_seconds=300.0
            )

        mock_ms = MagicMock()
        mock_ms.option_chain = mock_option_chain
        analytics = MarketAnalyticsService(mock_ms, refresh_interval=60.0)

        chain_key = "analytics:NSE:INDEX:NIFTY:2026-09-25"
        for i in range(n_alerts):
            analytics.register_chain(chain_key, f"alert-{i}")

        t0 = time.perf_counter()
        await analytics._refresh_one(chain_key)
        dt = time.perf_counter() - t0

        rows.append({
            "scenario": f"same_chain_{n_alerts}_alerts",
            "alerts_on_chain": n_alerts,
            "rest_calls": call_count,
            "expected_calls": 1,
            "cycle_time_s": round(dt, 4),
            "dedup_working": call_count == 1,
        })
        print(f"  {n_alerts} alerts on same chain: {call_count} REST call(s)")
        await analytics.stop(None)

    return {"rows": rows}
