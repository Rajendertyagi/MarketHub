#!/usr/bin/env python3
"""
Consumer-safe retention pruning tests — DIRECT (no MCP server).

Verifies F4 retention semantics against the canonical path:

  * RT-1  disabled retention (both limits 0) is a complete no-op
  * RT-2  age-only prune removes only over-age events (no consumers case:
          eligible rows prune normally when nothing requires them)
  * RT-3  row-count-only prune keeps the newest N
  * RT-4  combined age + rows criteria union correctly
  * RT-5  slow consumer (no checkpoint, unacked) → ALL its required events
          are preserved even when age-eligible (conservative floor = 0,
          matching replay_events' fallback semantics)
  * RT-6  consumer acknowledged + advanced past events → old rows prune,
          dependent consumer_event_state rows removed in the same operation
  * RT-7  mixed: acked event pruned, unacked event preserved, ces cleanup
          touches ONLY the actually-deleted event's state rows
  * RT-8  recent_events observational journal is unaffected by pruning

Pattern follows test_background_tasks.py / test_nonfinite_rejection.py:
isolated EventStore on a temp dir + injectable stub bus — ZERO server
subprocesses, sub-second runtime.

Run:
    python test/test_retention.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# Ensure project root is importable.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import events  # noqa: E402
from core.persistence.store import EventStore  # noqa: E402

from helpers.runner import R  # noqa: E402


class _StubBus:
    """Minimal subscription bus: records notifications for assertions."""

    def __init__(self) -> None:
        self.published: list[object] = []

    async def publish(self, item: object) -> None:
        self.published.append(item)


class _Harness:
    """One isolated EventStore + stub bus per test."""

    def __init__(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="mcp_retention_")
        self.db_path = os.path.join(self._tmp, "events.db")
        self.store = EventStore(self.db_path)
        self.bus = _StubBus()

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


def _sql_scalar(db_path: str, query: str, params: tuple = ()) -> object:
    """Run a single-value query against the harness DB directly."""
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(query, params).fetchone()[0]
    finally:
        conn.close()


def _backdate(db_path: str, event_id: str, days_ago: float) -> None:
    """Rewind an event's created_at so age-based retention sees it as old."""
    stamp = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).isoformat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE persistent_events SET created_at = ? WHERE id = ?",
            (stamp, event_id),
        )
        conn.commit()
    finally:
        conn.close()


async def _publish(runner: R, h: _Harness, tick: int) -> dict:
    """Publish one persistent broadcast event through the canonical path."""
    evt = await events.publish_event(
        event_type=f"test.retention.{tick}",
        source="retention-test",
        data={"tick": tick},
        persistent=True,
        store=h.store,
        bus=h.bus,
    )
    runner.assert_true(
        f"pub-{tick}-has-id", bool(evt.get("id")), "no event id"
    )
    return evt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def rt1_disabled_is_noop(runner: R) -> None:
    """RT-1: both limits 0 → no-op regardless of table contents."""
    name = "RT-1-disabled-noop"
    h = _Harness()
    try:
        e1 = await _publish(runner, h, 1)
        await _publish(runner, h, 2)
        result = h.store.prune(max_age_days=0, max_rows=0)
        runner.assert_eq(name + "-events", result["events_deleted"], 0)
        runner.assert_eq(name + "-state", result["state_deleted"], 0)
        runner.assert_eq(name + "-count", h.store.count(), 2)
        runner.assert_true(
            name + "-rows-intact",
            _sql_scalar(h.db_path, "SELECT COUNT(*) FROM persistent_events") == 2,
            f"event {e1['id']} missing",
        )
    finally:
        h.cleanup()


async def rt2_age_only_no_consumers(runner: R) -> None:
    """RT-2: age-only prune deletes exactly the over-age events.

    No consumers exist → nothing is required → eligible rows prune normally.
    """
    name = "RT-2-age-only"
    h = _Harness()
    try:
        e1 = await _publish(runner, h, 1)
        e2 = await _publish(runner, h, 2)
        await _publish(runner, h, 3)
        _backdate(h.db_path, e1["id"], days_ago=10)
        _backdate(h.db_path, e2["id"], days_ago=10)

        result = h.store.prune(max_age_days=1, max_rows=0)
        runner.assert_eq(name + "-deleted", result["events_deleted"], 2)
        runner.assert_eq(name + "-count", h.store.count(), 1)
        remaining = {
            r[0] for r in sqlite3.connect(h.db_path).execute(
                "SELECT id FROM persistent_events"
            ).fetchall()
        }
        # Only the fresh (non-backdated) event survives.
        e3_id = _sql_scalar(
            h.db_path,
            "SELECT id FROM persistent_events WHERE type = 'test.retention.3'",
        )
        runner.assert_true(
            name + "-kept-only-newest",
            remaining == {e3_id},
            f"expected only the fresh event to survive, got {remaining}",
        )
    finally:
        h.cleanup()


async def rt3_rows_only_no_consumers(runner: R) -> None:
    """RT-3: row-count-only prune keeps exactly the newest N events."""
    name = "RT-3-rows-only"
    h = _Harness()
    try:
        for i in range(1, 6):
            await _publish(runner, h, i)
        result = h.store.prune(max_age_days=0, max_rows=2)
        runner.assert_eq(name + "-deleted", result["events_deleted"], 3)
        runner.assert_eq(name + "-count", h.store.count(), 2)
        min_seq = _sql_scalar(h.db_path, "SELECT MIN(sequence) FROM persistent_events")
        runner.assert_ge(name + "-kept-newest-two", int(min_seq), 4)
    finally:
        h.cleanup()


async def rt4_combined_age_and_rows(runner: R) -> None:
    """RT-4: combined criteria delete the UNION of both eligible sets."""
    name = "RT-4-combined"
    h = _Harness()
    try:
        # seq 1..4; backdate seq1+seq2; newest-3 window excludes seq1.
        e1 = await _publish(runner, h, 1)
        e2 = await _publish(runner, h, 2)
        await _publish(runner, h, 3)
        await _publish(runner, h, 4)
        _backdate(h.db_path, e1["id"], days_ago=10)
        _backdate(h.db_path, e2["id"], days_ago=10)

        result = h.store.prune(max_age_days=1, max_rows=3)
        runner.assert_eq(name + "-deleted", result["events_deleted"], 2)
        remaining = {
            r[0] for r in sqlite3.connect(h.db_path).execute(
                "SELECT id FROM persistent_events"
            ).fetchall()
        }
        runner.assert_eq(name + "-count", len(remaining), 2)
        runner.assert_not_in(name + "-e1-gone", e1["id"], remaining)
        runner.assert_not_in(name + "-e2-gone", e2["id"], remaining)
    finally:
        h.cleanup()


async def rt5_slow_consumer_preserves_required(runner: R) -> None:
    """RT-5: consumer with NO checkpoint and unacked events blocks pruning.

    Conservative floor semantics: missing checkpoint row behaves as floor 0
    (exactly what replay_events assumes), so every unacked relevant event is
    required — even when age-eligible.
    """
    name = "RT-5-slow-consumer-preserves"
    h = _Harness()
    try:
        h.store.register_consumer("slow-c1")
        for i in range(1, 4):
            await _publish(runner, h, i)
        # Backdate ALL three events.
        for row in sqlite3.connect(h.db_path).execute(
            "SELECT id FROM persistent_events"
        ).fetchall():
            _backdate(h.db_path, row[0], days_ago=10)

        result = h.store.prune(max_age_days=1, max_rows=0)
        runner.assert_eq(name + "-nothing-deleted", result["events_deleted"], 0)
        runner.assert_eq(name + "-count", h.store.count(), 3)
        runner.assert_eq(
            name + "-state-intact",
            _sql_scalar(h.db_path, "SELECT COUNT(*) FROM consumer_event_state"),
            3,
        )
    finally:
        h.cleanup()


async def rt6_acked_and_advanced_prunes_with_ces_cleanup(runner: R) -> None:
    """RT-6: consumer past the events (acked + checkpoint advanced) → prune.

    Dependent consumer_event_state rows must be removed together with the
    events they reference.
    """
    name = "RT-6-advanced-prunes-ces-cleaned"
    h = _Harness()
    try:
        h.store.register_consumer("done-c1")
        evts = [await _publish(runner, h, i) for i in range(1, 4)]
        for e in evts:
            h.store.acknowledge_event("done-c1", e["id"])
        h.store.advance_checkpoint("done-c1")

        for row in sqlite3.connect(h.db_path).execute(
            "SELECT id FROM persistent_events"
        ).fetchall():
            _backdate(h.db_path, row[0], days_ago=10)

        result = h.store.prune(max_age_days=1, max_rows=0)
        runner.assert_eq(name + "-deleted", result["events_deleted"], 3)
        runner.assert_eq(name + "-ces-deleted", result["state_deleted"], 3)
        runner.assert_eq(name + "-count", h.store.count(), 0)
        runner.assert_eq(
            name + "-ces-empty",
            _sql_scalar(h.db_path, "SELECT COUNT(*) FROM consumer_event_state"),
            0,
        )
    finally:
        h.cleanup()


async def rt7_mixed_selective_preservation(runner: R) -> None:
    """RT-7: acked event pruned; unacked event preserved; ces cleanup scoped.

    c1 has two events: E1 acknowledged, E2 unacked. After advancing the
    checkpoint (E1 acked → checkpoint may reach E2's sequence - 1), age-based
    retention must delete E1 (+ its state row) and preserve E2 (+ its row).
    """
    name = "RT-7-mixed-selective"
    h = _Harness()
    try:
        h.store.register_consumer("mixed-c1")
        e1 = await _publish(runner, h, 1)
        e2 = await _publish(runner, h, 2)
        h.store.acknowledge_event("mixed-c1", e1["id"])
        h.store.advance_checkpoint("mixed-c1")

        _backdate(h.db_path, e1["id"], days_ago=10)
        _backdate(h.db_path, e2["id"], days_ago=10)

        result = h.store.prune(max_age_days=1, max_rows=0)
        runner.assert_eq(name + "-deleted", result["events_deleted"], 1)
        runner.assert_eq(name + "-ces-deleted", result["state_deleted"], 1)
        remaining = {
            r[0] for r in sqlite3.connect(h.db_path).execute(
                "SELECT id FROM persistent_events"
            ).fetchall()
        }
        runner.assert_true(
            name + "-e2-preserved", remaining == {e2["id"]},
            f"expected only E2 to survive, got {remaining}",
        )
        runner.assert_eq(
            name + "-e2-state-row-intact",
            _sql_scalar(
                h.db_path,
                "SELECT COUNT(*) FROM consumer_event_state WHERE event_id = ?",
                (e2["id"],),
            ),
            1,
        )
        runner.assert_eq(
            name + "-e1-state-row-gone",
            _sql_scalar(
                h.db_path,
                "SELECT COUNT(*) FROM consumer_event_state WHERE event_id = ?",
                (e1["id"],),
            ),
            0,
        )
    finally:
        h.cleanup()


async def rt8_recent_journal_unaffected(runner: R) -> None:
    """RT-8: the recent_events observational journal is untouched by pruning."""
    name = "RT-8-recent-journal-unaffected"
    h = _Harness()
    try:
        for i in range(1, 4):
            await _publish(runner, h, i)
        before = [e["id"] for e in h.store.get_recent_events(limit=50)]
        runner.assert_eq(name + "-journal-populated", len(before), 3)

        h.store.prune(max_age_days=0, max_rows=1)
        after = [e["id"] for e in h.store.get_recent_events(limit=50)]
        runner.assert_eq(name + "-journal-identical", after, before)
    finally:
        h.cleanup()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    runner = R()
    print("  Consumer-Safe Retention Tests (direct, no server)")
    print("=" * 50)

    tests = [
        rt1_disabled_is_noop,
        rt2_age_only_no_consumers,
        rt3_rows_only_no_consumers,
        rt4_combined_age_and_rows,
        rt5_slow_consumer_preserves_required,
        rt6_acked_and_advanced_prunes_with_ces_cleanup,
        rt7_mixed_selective_preservation,
        rt8_recent_journal_unaffected,
    ]
    for fn in tests:
        try:
            await fn(runner)
        except Exception as exc:
            runner.fail(fn.__name__, str(exc))

    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    asyncio.run(main())
