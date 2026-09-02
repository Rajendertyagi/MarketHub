"""Benchmark Part 7 — PCR / IV-Skew benchmark.

Measures compute_pcr, compute_pcr_volume, compute_iv_skew
at 20, 50, 100, 200, 500 strikes.
"""
from __future__ import annotations
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from market.models import OptionChainSnapshot, OptionContractData, OptionStrikeRow
from market.analytics.option_chain import compute_pcr, compute_pcr_volume, compute_iv_skew


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


def _make_snapshot(n_strikes):
    strikes = []
    base_strike = 25000.0
    for i in range(n_strikes):
        strike = base_strike + (i - n_strikes // 2) * 50.0
        is_atm = (i == n_strikes // 2)
        ce = OptionContractData(
            ltp=100.0, volume=50000, oi=100000, close=95.0,
            iv=0.18 + abs(i - n_strikes // 2) * 0.001, gamma=0.01, oi_change=0.0,
        )
        pe = OptionContractData(
            ltp=90.0, volume=55000, oi=110000, close=85.0,
            iv=0.20 + abs(i - n_strikes // 2) * 0.001, gamma=0.01, oi_change=0.0,
        )
        strikes.append(OptionStrikeRow(strike=strike, atm=is_atm, call=ce, put=pe))
    snap = OptionChainSnapshot(
        instrument_token="NSE_INDEX|NIFTY",
        exchange="NSE", tradingsymbol="NIFTY", expiry="2026-09-25",
        spot_price=25000.0, atm_strike=base_strike,
        strikes=tuple(strikes),
    )
    return snap


async def run():
    rows = []
    WARMUP = 50
    MEASURE = 200
    strike_counts = [20, 50, 100, 200, 500]

    for fn_name, fn in [("pcr", compute_pcr), ("pcr_volume", compute_pcr_volume), ("iv_skew", compute_iv_skew)]:
        for n in strike_counts:
            snap = _make_snapshot(n)
            for _ in range(WARMUP):
                fn(snap)
            times = []
            for _ in range(MEASURE):
                t0 = time.perf_counter_ns()
                fn(snap)
                dt = (time.perf_counter_ns() - t0) / 1e6
                times.append(dt)
            rows.append({
                "scenario": f"{fn_name}_N{n}",
                "metric": fn_name,
                "strikes": n,
                "p50_ms": round(_percentile(times, 50), 4),
                "p95_ms": round(_percentile(times, 95), 4),
                "p99_ms": round(_percentile(times, 99), 4),
                "min_ms": round(min(times), 4),
                "max_ms": round(max(times), 4),
                "iterations": MEASURE,
            })
            print(f"  {fn_name} N={n}: p50={_percentile(times,50):.4f}ms")

    return {"rows": rows}
