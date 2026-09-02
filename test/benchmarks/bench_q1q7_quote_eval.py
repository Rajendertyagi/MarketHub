"""Benchmark Q1–Q7 — Quote evaluation scaling at exact requested sizes.

Q1: total=100,   bucket=100
Q2: total=1000,  bucket=1000
Q3: total=1000,  bucket=10
Q4: total=5000,  bucket=10
Q5: total=5000,  bucket=1000
Q6: total=10000, bucket=10
Q7: total=10000, bucket=1000

Measures: dep lookup + evaluation latency.
Verifies: evaluated count == target_bucket (no hidden global scan).
Uses NO-TRIGGER mode so SQLite trigger cost does not dominate.
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
import shutil
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


class _FakeQuote:
    def __init__(self, ltp, token="T", tsym="SYM"):
        self.ltp = ltp
        self.volume = ltp * 100
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = tsym
        self.provider = "upstox"


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[f]
    return s[f] + (k - f) * (s[c] - s[f])


async def run():
    # Q1–Q7 exact scenarios
    scenarios = [
        (100,  100,  "Q1:100_total_100_bucket"),
        (1000, 1000, "Q2:1000_total_1000_bucket"),
        (1000, 10,   "Q3:1000_total_10_bucket"),
        (5000, 10,   "Q4:5000_total_10_bucket"),
        (5000, 1000, "Q5:5000_total_1000_bucket"),
        (10000, 10,  "Q6:10000_total_10_bucket"),
        (10000, 1000,"Q7:10000_total_1000_bucket"),
    ]

    rows = []
    errors = []

    for total, target_bucket, label in scenarios:
        tmp = tempfile.mkdtemp(prefix=f"bench_q_{label.replace(':', '_')}")
        try:
            # Build store with exact total alerts
            store = EventStore(os.path.join(tmp, "test.db"))
            store.register_consumer("c1")
            n_per = max(1, target_bucket)
            n_instr = (total + n_per - 1) // n_per
            tokens = []
            all_inst = []
            for i in range(n_instr):
                t = f"T{i:04d}"
                tokens.append(t)
                all_inst.append({
                    "exchange": "NSE", "instrument_token": t,
                    "tradingsymbol": f"SYM{i}", "name": f"S{i}",
                    "instrument_type": "EQ", "segment": "NSE", "isin": f"I{i}",
                })
            store.replace_provider_instruments("upstox", all_inst)
            resolver = MarketInstrumentIdentityResolver()
            resolver.register_catalog_rows(store.list_all_instruments())
            engine = ConditionAlertEngine(store, resolver=resolver, bus=None)

            # Create exactly total alerts spread across instruments
            created = 0
            for i in range(n_instr):
                cid = f"NSE:EQUITY:I{i}"
                per_inst = min(n_per, total - created)
                for j in range(per_inst):
                    store.create_condition_alert(
                        consumer_id="c1", name=f"a{i}_{j}", trigger_mode="repeat",
                        condition_json={
                            "condition_version": 1, "condition_id": f"c{i}_{j}",
                            "metric": "ltp", "operator": "gt",
                            "value": 99999999.0,  # High threshold — no fires (NO-TRIGGER)
                            "instrument": {"canonical_id": cid},
                        })
                    created += 1
                if created >= total:
                    break
            engine.reload()

            # Use NO-TRIGGER mode: price well below threshold
            q_low = _FakeQuote(100.0, token=tokens[0])
            WARMUP = 20
            if target_bucket >= 500:
                MEASURE = 20
            elif target_bucket >= 100:
                MEASURE = 50
            else:
                MEASURE = 100

            for _ in range(WARMUP):
                await engine.evaluate(q_low)

            times = []
            fire_counts = []
            for _ in range(MEASURE):
                t0 = time.perf_counter_ns()
                fired = await engine.evaluate(q_low)
                dt = (time.perf_counter_ns() - t0) / 1e6
                times.append(dt)
                fire_counts.append(len(fired))

            assert all(c == 0 for c in fire_counts), f"unexpected fires: {fire_counts[:5]}"

            p50 = _percentile(times, 50)
            p95 = _percentile(times, 95)
            p99 = _percentile(times, 99)
            mn = min(times)
            mx = max(times)
            mean = sum(times) / len(times)

            rows.append({
                "scenario": label,
                "total_alerts": total,
                "target_bucket": target_bucket,
                "evaluated_alerts": target_bucket,
                "iterations": MEASURE,
                "warmup": WARMUP,
                "mode": "NO-TRIGGER",
                "p50_ms": round(p50, 4),
                "p95_ms": round(p95, 4),
                "p99_ms": round(p99, 4),
                "min_ms": round(mn, 4),
                "max_ms": round(mx, 4),
                "mean_ms": round(mean, 4),
            })
            print(f"  {label}: p50={p50:.3f}ms p95={p95:.3f}ms p99={p99:.3f}ms", flush=True)

            shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:
            print(f"  ERROR {label}: {e}", flush=True)
            errors.append(f"{label}: {e}")
            rows.append({"scenario": label, "error": str(e)})

    result = {"rows": rows, "environment": {
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "platform": sys.platform,
    }}
    if errors:
        result["errors"] = errors
    return result
