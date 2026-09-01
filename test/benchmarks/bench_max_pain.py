"""Benchmark Part 6 — Max Pain O(N^2) actual measurement.

DOES NOT change formula. Measures actual runtime.
"""
from __future__ import annotations
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from market.models import OptionChainSnapshot, OptionContractData
from market.analytics.option_chain import compute_max_pain


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


def _make_snapshot(n_strikes):
    """Create a deterministic option chain snapshot with N strikes."""
    strikes = []
    base_strike = 25000.0
    for i in range(n_strikes):
        strike = base_strike + (i - n_strikes // 2) * 50.0
        ce = OptionContractData(
            strike=strike, atm=False,
            open_interest=100000.0 - abs(i - n_strikes // 2) * 1000.0,
            volume=50000.0, iv=0.20, ltp=100.0, close=95.0,
            gamma=0.01, buildup_tag="Neutral"
        )
        pe = OptionContractData(
            strike=strike, atm=False,
            open_interest=80000.0 - abs(i - n_strikes // 2) * 800.0,
            volume=40000.0, iv=0.22, ltp=80.0, close=75.0,
            gamma=0.01, buildup_tag="Neutral"
        )
        # Ensure positive OI
        ce.open_interest = max(ce.open_interest, 1000.0)
        pe.open_interest = max(pe.open_interest, 800.0)
        strikes.append(type('R', (), {
            'strike': strike, 'atm': False,
            'call': ce, 'put': pe
        })())
    snap = OptionChainSnapshot(
        chain_key=f"test:NSE:INDEX:NIFTY:2026-09-25",
        canonical_underlying_id="NSE:INDEX:NIFTY",
        exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
        spot_price=25000.0, pcr_oi=1.1, pcr_volume=None,
        max_pain=None, iv_skew=None,
        strikes=strikes, received_ts=None, calculated_at=None,
        stale_after_seconds=300.0
    )
    return snap


async def run():
    rows = []
    WARMUP = 50
    MEASURE = 200
    strike_counts = [20, 50, 100, 200, 500]

    for n in strike_counts:
        snap = _make_snapshot(n)
        # Verify correctness
        result = compute_max_pain(snap)
        assert result["max_pain"] is not None, f"max_pain should not be None for N={n}"

        # Warmup
        for _ in range(WARMUP):
            compute_max_pain(snap)

        # Measure
        times = []
        for _ in range(MEASURE):
            t0 = time.perf_counter_ns()
            r = compute_max_pain(snap)
            dt = (time.perf_counter_ns() - t0) / 1e6
            times.append(dt)
            assert r["max_pain"] is not None

        rows.append({
            "scenario": f"max_pain_N{n}_strikes",
            "strikes": n,
            "p50_ms": round(_percentile(times, 50), 4),
            "p95_ms": round(_percentile(times, 95), 4),
            "p99_ms": round(_percentile(times, 99), 4),
            "min_ms": round(min(times), 4),
            "max_ms": round(max(times), 4),
            "mean_ms": round(sum(times)/len(times), 4),
            "iterations": MEASURE,
            "correctness": "verified",
        })
        print(f"  N={n}: p50={_percentile(times,50):.4f}ms p99={_percentile(times,99):.4f}ms")

    return {"rows": rows}
