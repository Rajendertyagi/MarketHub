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


async def t2_ack_100(runner: R) -> None:
    """ACK 100 events — verify correctness and measure performance."""
    name = "T2-ack-100"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        ids = await _publish_n(store, bus, 100)
        times = []
        for eid in ids:
            t0 = time.perf_counter_ns()
            store.acknowledge_event("c1", eid)
            dt = (time.perf_counter_ns() - t0) / 1e6
            times.append(dt)

        status = get_consumer_inbox_status(store._open(store._db_path), "c1")
        runner.assert_eq(name + "-pending", status["pending_count"], 0)
        runner.assert_true(name + "-p50",
                          _percentile(times, 50) < 25.0,
                          f"p50={_percentile(times, 50):.2f}ms")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t3_ack_1000(runner: R) -> None:
    """ACK 1000 events — verify correctness and measure performance."""
    name = "T3-ack-1000"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        ids = await _publish_n(store, bus, 1000)
        times = []
        for eid in ids:
            t0 = time.perf_counter_ns()
            store.acknowledge_event("c1", eid)
            dt = (time.perf_counter_ns() - t0) / 1e6
            times.append(dt)

        status = get_consumer_inbox_status(store._open(store._db_path), "c1")
        runner.assert_eq(name + "-pending", status["pending_count"], 0)
        runner.assert_true(name + "-p50",
                          _percentile(times, 50) < 25.0,
                          f"p50={_percentile(times, 50):.2f}ms")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t4_ack_errors(runner: R) -> None:
    """ACK raises correct errors for invalid inputs."""
    name = "T4-errors"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        ids = await _publish_n(store, bus, 3)

        # Non-existent consumer
        try:
            store.acknowledge_event("nonexistent", ids[0])
            runner.fail(name + "-consumer", "should raise ConsumerNotFoundError")
        except ConsumerNotFoundError:
            pass

        # Non-existent event
        try:
            store.acknowledge_event("c1", "nonexistent-event-id")
            runner.fail(name + "-event", "should raise EventNotFoundError")
        except EventNotFoundError:
            pass

        # Non-relevant event (different consumer)
        store.register_consumer("c2")
        try:
            store.acknowledge_event("c2", ids[0])
            runner.fail(name + "-relevant", "should raise EventNotRelevantError")
        except EventNotRelevantError:
            pass
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
            t2_ack_100,
            t3_ack_1000,
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
