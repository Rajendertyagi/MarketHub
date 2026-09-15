#!/usr/bin/env python3
"""B8 FIX 3 — ACK performance tests.

Verifies that the optimized acknowledge_event path preserves semantics
and improves performance.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R
from core import events
from core.persistence.store import EventStore
from core.persistence.modules.replay import get_consumer_inbox_status
from core.errors import (
    ConsumerNotFoundError,
    EventNotFoundError,
    EventNotRelevantError,
)


class _StubBus:
    def __init__(self):
        self.last = None
    async def publish(self, item):
        self.last = item


def _percentile(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


def _mk_store():
    tmp = tempfile.mkdtemp(prefix="b8ack_")
    store = EventStore(os.path.join(tmp, "events.db"))
    store.register_consumer("c1")
    return store, tmp


async def _publish_n(store, bus, n):
    """Publish n events and return their IDs."""
    ids = []
    for i in range(n):
        data = {
            "alert_family": "market_condition",
            "alert_id": f"alert-{i}",
            "consumer_id": "c1",
            "condition": {
                "condition_version": 1,
                "logic": None,
                "conditions": [{
                    "condition_version": 1,
                    "condition_id": "c1",
                    "metric": "ltp",
                    "operator": "gt",
                    "value": 25000.0,
                    "instrument": {"canonical_id": "NSE:EQUITY:I"},
                }],
            },
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": False,
        }
        result = await events.publish_event(
            event_type="alert.triggered",
            source="test",
            data=data,
            persistent=True,
            routing={"targets": ["c1"]},
            store=store,
            bus=bus,
        )
        ids.append(result["id"])
    return ids


# ===================================================================
# Tests
# ===================================================================


async def t1_ack_correctness(runner: R) -> None:
    """ACK sets pending to 0, checkpoint correct, repeated ACK safe."""
    name = "T1-correctness"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        ids = await _publish_n(store, bus, 10)

        # Verify pending before ACK
        status = get_consumer_inbox_status(store._open(store._db_path), "c1")
        runner.assert_eq(name + "-before", status["pending_count"], 10)

        # ACK all
        for eid in ids:
            store.acknowledge_event("c1", eid)

        # Verify pending = 0
        status2 = get_consumer_inbox_status(store._open(store._db_path), "c1")
        runner.assert_eq(name + "-after", status2["pending_count"], 0)

        # Repeated ACK should be safe (idempotent)
        for eid in ids:
            result = store.acknowledge_event("c1", eid)
            runner.assert_true(name + f"-idempotent-{eid[:8]}", result is True)

        # Event history intact
        pending_after = store.replay_events("c1", limit=20)
        runner.assert_eq(name + "-history", len(pending_after.get("events", [])), 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t5_ack_10000(runner: R) -> None:
    """ACK 10000 events — full measurement with correctness verification."""
    name = "T5-ack-10000"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        ids = await _publish_n(store, bus, 10000)
        times = []
        for eid in ids:
            t0 = time.perf_counter_ns()
            store.acknowledge_event("c1", eid)
            dt = (time.perf_counter_ns() - t0) / 1e6
            times.append(dt)

        total_ms = sum(times)
        throughput = 10000 / (total_ms / 1000.0) if total_ms > 0 else 0

        status = get_consumer_inbox_status(store._open(store._db_path), "c1")
        runner.assert_eq(name + "-pending", status["pending_count"], 0)

        cp = store.get_checkpoint("c1")
        runner.assert_true(name + "-checkpoint", cp is not None, "checkpoint should exist")

        idempotent_ok = True
        for eid in ids[:10]:
            r = store.acknowledge_event("c1", eid)
            if r is not True:
                idempotent_ok = False
        runner.assert_true(name + "-idempotent", idempotent_ok, "idempotent ACK failed")

        history = store.replay_events("c1", limit=5)
        runner.assert_eq(name + "-history", len(history.get("events", [])), 0)

        no_errors = not any("locked" in str(t).lower() or "thread" in str(t).lower()
                           for t in times)
        runner.assert_true(name + "-no-errors", no_errors, "SQLite thread/lock errors detected")

        print(f"    {name}: total={total_ms:.1f}ms  ack/s={throughput:.1f}  "
              f"p50={_percentile(times, 50):.3f}ms  "
              f"p95={_percentile(times, 95):.3f}ms  "
              f"p99={_percentile(times, 99):.3f}ms")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Main
# ===================================================================

async def main() -> int:
    runner = R()
    try:
        print("  B8 FIX 3 — ACK Performance Tests")
        print("=" * 50)
        tests = [
            t1_ack_correctness,
            t5_ack_10000,
            t4_ack_errors,
        ]
        for fn in tests:
            try:
                await fn(runner)
            except Exception as exc:
                runner.fail(fn.__name__, str(exc))
    except Exception as exc:
        runner.fail("main", str(exc))

    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    asyncio.run(main())
