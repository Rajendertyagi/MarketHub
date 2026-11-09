#!/usr/bin/env python3
"""Live-validation harness readiness tests (S11).

The harness must be runnable OFFLINE and must never claim a PASS without
the evidence behind it. Without a session every item is BLOCKED; with a
Fyers session the captured U1/U2/U3 evidence is reported. It performs NO
authenticated live call of its own.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

sys.path.insert(0, os.path.join(_PROJECT_DIR, "test"))

import pytest
from live_validation_harness import (
    BLOCKED,
    FAILED,
    NOT_RUN,
    PASSED,
    LiveValidationHarness,
)


def test_report_covers_u1_to_u6() -> None:
    report = LiveValidationHarness().report()
    items = {i["item"] for i in report["items"]}
    assert {"U1", "U2", "U3", "U4", "U5", "U6"} <= items


def test_u1_records_the_measured_scaling_defect() -> None:
    """U1 is MEASURED: the HSM index scale is wrong, so the guard is right."""
    ev = next(e for e in LiveValidationHarness().collect() if e.item == "U1")
    if ev.status == BLOCKED:
        pytest.skip("no Fyers session available")
    assert ev.status == FAILED
    assert ev.observed["multiplier"] == 32
    assert ev.observed["ratio"] == 3.125
    assert "guard" in ev.reason


def test_u2_and_u3_carry_the_captured_evidence() -> None:
    """U2/U3 evidence is recorded with real observed values."""
    items = {e.item: e for e in LiveValidationHarness().collect()}
    u2, u3 = items["U2"], items["U3"]
    for ev in (u2, u3):
        if ev.status == BLOCKED:
            pytest.skip("no Fyers session available")
    assert u2.status == PASSED
    assert u2.observed["NIFTY_50"]["diff_pt"] == 0.0
    assert u3.status == PASSED
    assert u3.observed["aggregate_scope"] == "whole-expiry"
    assert u3.observed["legs_with_greeks"] > 0


def test_upstox_items_remain_blocked_without_a_session() -> None:
    """U4-U6 must never claim a pass without an Upstox session."""
    items = {e.item: e for e in LiveValidationHarness().collect()}
    for name in ("U4", "U5", "U6"):
        ev = items[name]
        assert ev.status != PASSED, f"{name} must not pass without a session"
        assert ev.status in (BLOCKED, NOT_RUN, FAILED)


def test_all_items_blocked_without_session() -> None:
    """Without any session, EVERY item is blocked and nothing passes."""
    harness = LiveValidationHarness(token_provider=lambda: None)
    report = harness.report()
    assert report["authenticated"] is False
    assert report["passed"] == 0
    assert report["failed"] == 0
    assert report["blocked"] == report["total"]


def test_blocked_items_state_a_reason() -> None:
    """Every item states WHY it is not passed (no silent items)."""
    for ev in LiveValidationHarness().collect():
        if ev.status in (BLOCKED, NOT_RUN, FAILED):
            assert ev.reason, f"{ev.item} must state a reason"


def test_harness_performs_no_live_call() -> None:
    """A supplied rest/feed must never be called while blocked."""
    calls: list[str] = []

    class _Rest:
        async def quotes(self, *a, **k):
            calls.append("quotes")
            return []

    class _Feed:
        pass

    harness = LiveValidationHarness(
        token_provider=lambda: None, rest=_Rest(), feed=_Feed())
    harness.collect()
    assert calls == []


def test_market_hours_is_reported() -> None:
    report = LiveValidationHarness().report()
    assert isinstance(report["market_hours"], bool)


def test_report_declares_no_false_passes() -> None:
    report = LiveValidationHarness().report()
    assert "No U1-U6 item may be declared PASSED" in report["note"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
