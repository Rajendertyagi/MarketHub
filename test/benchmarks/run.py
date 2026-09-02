"""B8A benchmark runner — orchestrates all benchmark modules.

Produces:
  b8-benchmark-results.json
  b8-benchmark-results.csv
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
import traceback

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from bench_quote_eval import run as bench_quote_eval
from bench_condition_complexity import run as bench_condition_complexity
from bench_trigger_persistence import run as bench_trigger_persistence
from bench_sqlite_txn import run as bench_sqlite_txn
from bench_write_amplification import run as bench_write_amp
from bench_max_pain import run as bench_max_pain
from bench_analytics_metrics import run as bench_analytics_metrics
from bench_analytics_scheduler import run as bench_analytics_scheduler
from bench_same_chain_dedup import run as bench_same_chain_dedup
from bench_memory import run as bench_memory
from bench_cleanup import run as bench_cleanup
from bench_restart import run as bench_restart
from bench_replay import run as bench_replay
from bench_ack import run as bench_ack
from bench_live_wake import run as bench_live_wake
from bench_concurrency import run as bench_concurrency
from bench_explain_plan import run as bench_explain_plan
from bench_q1q7_quote_eval import run as bench_q1q7
from bench_slow_chain_blocking import run as bench_slow_chain_blocking
from bench_restart_10k import run as bench_restart_10k
from bench_replay_10k import run as bench_replay_10k
from bench_ack_10k import run as bench_ack_10k


async def main():
    results: dict[str, dict] = {}
    errors: list[str] = []

    runners = [
        ("quote_eval", bench_quote_eval),
        ("condition_complexity", bench_condition_complexity),
        ("trigger_persistence", bench_trigger_persistence),
        ("sqlite_txn", bench_sqlite_txn),
        ("write_amplification", bench_write_amp),
        ("max_pain", bench_max_pain),
        ("analytics_metrics", bench_analytics_metrics),
        ("analytics_scheduler", bench_analytics_scheduler),
        ("same_chain_dedup", bench_same_chain_dedup),
        ("memory", bench_memory),
        ("cleanup", bench_cleanup),
        ("restart", bench_restart),
        ("replay", bench_replay),
        ("ack", bench_ack),
        ("live_wake", bench_live_wake),
        ("concurrency", bench_concurrency),
        ("explain_plan", bench_explain_plan),
        ("q1q7_quote_scale", bench_q1q7),
        ("slow_chain_blocking", bench_slow_chain_blocking),
        ("restart_10k", bench_restart_10k),
        ("replay_10k", bench_replay_10k),
        ("ack_10k", bench_ack_10k),
    ]

    for name, fn in runners:
        print(f"\n{'='*60}")
        print(f"RUNNING: {name}")
        print("="*60)
        try:
            results[name] = await fn()
            print(f"  DONE: {name}")
        except Exception as e:
            print(f"  ERROR: {name}: {e}")
            traceback.print_exc()
            errors.append(f"{name}: {e}")
            results[name] = {"error": str(e)}

    # Write JSON
    out_json = os.path.join(_SCRIPT_DIR, "b8-benchmark-results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nJSON results written to: {out_json}")

    # Write CSV summary
    out_csv = os.path.join(_SCRIPT_DIR, "b8-benchmark-results.csv")
    with open(out_csv, "w", encoding="utf-8") as f:
        f.write("section,scenario,key_metric,value_unit\n")
        for section, data in results.items():
            if "error" in data:
                f.write(f"{section},error,,{data['error']}\n")
                continue
            for row in data.get("rows", []):
                f.write(f"{section},{row.get('scenario','')},{row.get('metric','')},{row.get('value','')}\n")
    print(f"CSV results written to: {out_csv}")

    if errors:
        print(f"\n{len(errors)} benchmark(s) had errors")
        sys.exit(1)
    else:
        print("\nAll benchmarks completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
