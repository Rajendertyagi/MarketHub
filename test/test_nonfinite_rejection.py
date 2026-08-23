#!/usr/bin/env python3
"""
Non-finite JSON rejection tests — DIRECT (no MCP server).

Verifies the F3 hardening in events.publish_event(): NaN / Infinity /
-Infinity anywhere in ``data`` or ``routing`` must be rejected with an
explicit ValueError BEFORE any side effect (persistence, recent-history
journal, subscription notification, SSE fan-out, alert evaluation).
No coercion (NaN->null/string) may occur.

WHY DIRECT AND NOT VIA MCP TRANSPORT:
    The MCP SDK's JSON codec (pydantic-based) converts non-finite floats
    to ``null`` during argument serialization, so a remote MCP client can
    never deliver a raw NaN to publish_event() — confirmed empirically on
    CI (2026-08): a NaN publish arrived as {"price": null} and succeeded.
    The real exposure path is DIRECT callers, e.g. sources/http_poller.py
    running json.loads() on arbitrary upstream JSON (Python's json.loads
    accepts NaN/Infinity literals and yields float('nan')). These tests
    exercise exactly that boundary: publish_event() itself.

Pattern follows test_background_tasks.py: isolated EventStore on a temp
dir + injectable stub bus — ZERO server subprocesses, sub-second runtime.

Run:
    python test/test_nonfinite_rejection.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

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
        self._tmp = tempfile.mkdtemp(prefix="mcp_nonfinite_")
        self.store = EventStore(os.path.join(self._tmp, "events.db"))
        self.bus = _StubBus()

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)


async def _expect_rejection(
    runner: R,
    name: str,
    harness: _Harness,
    *,
    data=None,
    routing=None,
    persistent: bool = False,
) -> None:
    """Assert publish_event raises ValueError and leaves zero side effects."""
    rejected = False
    try:
        await events.publish_event(
            event_type="test.nonfinite",
            source="unit-test",
            data=data if data is not None else {},
            routing=routing,
            persistent=persistent,
            store=harness.store,
            bus=harness.bus,
        )
    except ValueError as exc:
        rejected = True
        msg = str(exc).lower()
        runner.assert_true(
            name + "-explicit-message",
            ("json" in msg) or ("nan" in msg) or ("infinity" in msg) or ("range" in msg),
            f"error text should identify invalid JSON input, got: {exc}",
        )
    except Exception as exc:  # wrong exception type = inconsistent rejection
        runner.fail(name, f"expected ValueError, got {type(exc).__name__}: {exc}")
        return
    runner.assert_true(name, rejected, "publish_event must reject non-finite JSON data")

    # Zero side effects: nothing persisted, nothing journalled, nothing notified.
    runner.assert_eq(name + "-not-persisted", harness.store.count(), 0)
    runner.assert_eq(
        name + "-not-journalled",
        len(harness.store.get_recent_events(limit=10)),
        0,
    )
    runner.assert_eq(
        name + "-not-notified",
        len(harness.bus.published),
        0,
    )


async def t1_nan_persistent_rejected(runner: R) -> None:
    """NF-T1: NaN in persistent event data is rejected before any write."""
    h = _Harness()
    try:
        await _expect_rejection(
            runner, "NF-T1-nan-persistent", h,
            data={"price": float("nan")}, persistent=True,
        )
    finally:
        h.cleanup()


async def t2_infinity_nonpersistent_rejected(runner: R) -> None:
    """NF-T2: +Infinity in non-persistent data is rejected (no memory/journal leak)."""
    h = _Harness()
    try:
        await _expect_rejection(
            runner, "NF-T2-inf-nonpersistent", h,
            data={"delta": float("inf")}, persistent=False,
        )
    finally:
        h.cleanup()


async def t3_neg_infinity_nested_rejected(runner: R) -> None:
    """NF-T3: -Infinity nested inside lists/dicts is still caught."""
    h = _Harness()
    try:
        await _expect_rejection(
            runner, "NF-T3-neginf-nested", h,
            data={"readings": [1.0, {"low": float("-inf")}]},
            persistent=True,
        )
    finally:
        h.cleanup()


async def t4_nan_routing_rejected(runner: R) -> None:
    """NF-T4: NaN inside routing metadata is rejected."""
    h = _Harness()
    try:
        await _expect_rejection(
            runner, "NF-T4-nan-routing", h,
            data={"ok": 1},
            routing={"targets": ["c1"], "weight": float("nan")},
            persistent=False,
        )
    finally:
        h.cleanup()


async def t5_valid_floats_still_accepted(runner: R) -> None:
    """NF-T5: control — finite floats must NOT be over-rejected."""
    h = _Harness()
    try:
        evt = await events.publish_event(
            event_type="test.nonfinite.control",
            source="unit-test",
            data={"price": 3.14, "zero": 0.0, "neg": -2.5},
            persistent=True,
            store=h.store,
            bus=h.bus,
        )
        runner.assert_eq("NF-T5-control-persisted", h.store.count(), 1)
        runner.assert_true(
            "NF-T5-control-has-seq", evt.get("sequence") is not None, "no sequence"
        )
        runner.assert_eq("NF-T5-control-journalled",
                         len(h.store.get_recent_events(limit=10)), 1)
        runner.assert_eq("NF-T5-control-notified", len(h.bus.published), 1)
    finally:
        h.cleanup()


# ===================================================================
# Main
# ===================================================================


async def main() -> int:
    runner = R()
    print("  Non-finite JSON Rejection Tests (direct, no server)")
    print("=" * 50)

    tests = [
        t1_nan_persistent_rejected,
        t2_infinity_nonpersistent_rejected,
        t3_neg_infinity_nested_rejected,
        t4_nan_routing_rejected,
        t5_valid_floats_still_accepted,
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
