"""Bounded synthetic soak: watch for task/memory/subscriber growth.

Runs ~4 minutes of continuous quote updates through the canonical market
service, then reports start vs end resource observations.
"""
import asyncio
import gc
import os
import sys
import time
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot() -> dict:
    gc.collect()
    return {
        "tasks": len(asyncio.all_tasks()),
        "objects": len(gc.get_objects()),
    }


async def main() -> bool:
    from market.service import MarketService, QuotePatch

    runner = R()
    service = MarketService()
    callbacks = {"n": 0}

    async def on_quote(q):
        callbacks["n"] += 1

    service._on_quote_update = on_quote

    start = _snapshot()
    t0 = time.monotonic()
    duration = 240.0          # bounded: 4 minutes
    cycle = 0
    errors = 0
    quotes_sent = 0
    while time.monotonic() - t0 < duration:
        cycle += 1
        try:
            for i in range(200):
                await service.apply_quote(QuotePatch(
                    exchange="NSE", instrument_token=f"SOAK{i}",
                    tradingsymbol=f"S{i}",
                    received_ts=datetime.now(timezone.utc),
                    reported_fields={"ltp": 100.0 + i + (cycle % 10)}))
                quotes_sent += 1
        except Exception as exc:
            errors += 1
            if errors <= 3:
                print("  error:", type(exc).__name__, str(exc)[:80])
        if cycle % 100 == 0:
            print(f"  cycle {cycle}: {quotes_sent} quotes, "
                  f"{errors} errors, tasks={len(asyncio.all_tasks())}")

    end = _snapshot()
    print(f"soak done: {cycle} cycles, {quotes_sent} quotes, {errors} errors")
    print(f"callbacks fired: {callbacks['n']}")
    print(f"tasks: {start['tasks']} -> {end['tasks']}")
    print(f"gc objects: {start['objects']} -> {end['objects']}")
    runner.assert_eq("SOAK-no-errors", errors, 0)
    runner.assert_eq("SOAK-callbacks-fired", callbacks["n"], quotes_sent)
    runner.assert_le("SOAK-no-task-growth", end["tasks"], start["tasks"] + 5)
    growth = end["objects"] - start["objects"]
    runner.assert_le("SOAK-bounded-memory", growth, 500_000)
    return runner.summary()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
