#!/usr/bin/env python3
"""Deterministic concurrency/reliability tests for market AlertEngine.

Covers duplicate-trigger protection, crossing semantics, isolation,
lifecycle races, and failure recovery for the market alert engine.
No arbitrary sleeps for race creation; barriers/events/futures only.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

UTC = timezone.utc


def _ts(offset_s: float = 0.0) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=offset_s)


def _quote(token: str, ltp: float, *, exchange: str = "NSE", ts=None, **extra):
    return SimpleNamespace(
        exchange=exchange,
        instrument_token=token,
        tradingsymbol=extra.pop("tradingsymbol", token),
        ltp=ltp,
        received_ts=ts or _ts(),
        provider=extra.pop("provider", "upstox"),
        **extra,
    )


def _mk_store(tmpdir: str):
    from core.persistence.store import EventStore

    return EventStore(os.path.join(tmpdir, "t.db"))


def _create_alert(store, *, token="T1", field="ltp", operator="gt",
                  threshold=100.0, exchange="NSE", tradingsymbol="AAA"):
    return store.create_market_alert(
        exchange=exchange,
        instrument_token=token,
        tradingsymbol=tradingsymbol,
        field=field,
        operator=operator,
        threshold=threshold,
    )


async def _run_concurrent_evals(engine, quotes):
    # Start all evaluations together for deterministic overlap.
    start = asyncio.Event()

    async def one(q):
        await start.wait()
        return await engine.evaluate(q)

    tasks = [asyncio.create_task(one(q)) for q in quotes]
    await asyncio.sleep(0)
    start.set()
    return await asyncio.gather(*tasks)


# -- duplicate-trigger protection --------------------------------------------

async def test_dup_gt_same_rule(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)

        # Hold first live finalization so second evaluation overlaps trigger path.
        import core.events as ev
        orig = ev.finalize_persisted_event
        gate = asyncio.Event()
        entered = 0

        async def slow_finalize(*a, **k):
            nonlocal entered
            entered += 1
            if entered == 1:
                try:
                    await asyncio.wait_for(gate.wait(), timeout=2.0)
                except Exception:
                    pass
            else:
                gate.set()
            return await orig(*a, **k)

        ev.finalize_persisted_event = slow_finalize
        try:
            results = await _run_concurrent_evals(
                engine,
                [_quote("T1", 110.0, ts=_ts(1)), _quote("T1", 111.0, ts=_ts(2))],
            )
        finally:
            ev.finalize_persisted_event = orig

        fired_total = sum(len(r) for r in results)
        alerts = store.list_market_alerts()
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("dup-gt-fired-once", fired_total, 1)
        runner.assert_eq("dup-gt-state-triggered", alerts[0]["state"], "triggered")
        runner.assert_eq("dup-gt-history-once", len(hist), 1)


async def test_dup_lt_same_rule(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="lt", threshold=100.0)
        engine = AlertEngine(store)
        results = await _run_concurrent_evals(
            engine,
            [_quote("T1", 90.0, ts=_ts(1)), _quote("T1", 89.0, ts=_ts(2))],
        )
        fired_total = sum(len(r) for r in results)
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("dup-lt-fired-once", fired_total, 1)
        runner.assert_eq("dup-lt-history-once", len(hist), 1)


async def test_dup_threshold_burst_10(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        quotes = [_quote("T1", 101.0 + i, ts=_ts(i + 1)) for i in range(10)]
        results = await _run_concurrent_evals(engine, quotes)
        fired_total = sum(len(r) for r in results)
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=20)
        runner.assert_eq("burst-fired-once", fired_total, 1)
        runner.assert_eq("burst-history-once", len(hist), 1)


# -- crossing semantics ------------------------------------------------------

async def test_crosses_above_two_quotes(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="crosses_above", threshold=100.0)
        engine = AlertEngine(store)
        r1 = await engine.evaluate(_quote("T1", 99.0, ts=_ts(1)))
        r2 = await engine.evaluate(_quote("T1", 101.0, ts=_ts(2)))
        runner.assert_eq("cross-above-no-fire-below", len(r1), 0)
        runner.assert_eq("cross-above-fire-once", len(r2), 1)
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("cross-above-history-once", len(hist), 1)


async def test_crosses_below_two_quotes(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        _create_alert(store, operator="crosses_below", threshold=100.0)
        engine = AlertEngine(store)
        r1 = await engine.evaluate(_quote("T1", 101.0, ts=_ts(1)))
        r2 = await engine.evaluate(_quote("T1", 99.0, ts=_ts(2)))
        runner.assert_eq("cross-below-no-fire-above", len(r1), 0)
        runner.assert_eq("cross-below-fire-once", len(r2), 1)


async def test_crossing_rapid_succession(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="crosses_above", threshold=100.0)
        engine = AlertEngine(store)
        await engine.evaluate(_quote("T1", 99.0, ts=_ts(1)))
        results = await _run_concurrent_evals(
            engine,
            [_quote("T1", 101.0, ts=_ts(2)), _quote("T1", 102.0, ts=_ts(3))],
        )
        fired_total = sum(len(r) for r in results)
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("rapid-cross-fired-once", fired_total, 1)
        runner.assert_eq("rapid-cross-history-once", len(hist), 1)


async def test_repeated_identical_values(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        r1 = await engine.evaluate(_quote("T1", 101.0, ts=_ts(1)))
        r2 = await engine.evaluate(_quote("T1", 101.0, ts=_ts(2)))
        r3 = await engine.evaluate(_quote("T1", 101.0, ts=_ts(3)))
        runner.assert_eq("identical-first-fires", len(r1), 1)
        runner.assert_eq("identical-second-no", len(r2), 0)
        runner.assert_eq("identical-third-no", len(r3), 0)
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("identical-history-once", len(hist), 1)


async def test_first_quote_no_false_crossing(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        _create_alert(store, operator="crosses_above", threshold=100.0)
        engine = AlertEngine(store)
        fired = await engine.evaluate(_quote("T1", 150.0, ts=_ts(1)))
        runner.assert_eq("first-no-false-cross", fired, [])


# -- isolation ---------------------------------------------------------------

async def test_different_alerts_concurrent(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        a1 = _create_alert(store, token="T1", tradingsymbol="AAA", operator="gt", threshold=100.0)
        a2 = _create_alert(store, token="T2", tradingsymbol="BBB", operator="gt", threshold=200.0)
        engine = AlertEngine(store)
        results = await _run_concurrent_evals(
            engine,
            [_quote("T1", 110.0, ts=_ts(1)), _quote("T2", 210.0, ts=_ts(1), tradingsymbol="BBB")],
        )
        fired_ids = sorted(n["alert_id"] for r in results for n in r)
        runner.assert_eq("different-alerts-both-fire", fired_ids, sorted([a1["id"], a2["id"]]))


async def test_different_instruments_independent(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        a1 = _create_alert(store, token="T1", tradingsymbol="AAA", operator="crosses_above", threshold=100.0)
        a2 = _create_alert(store, token="T2", tradingsymbol="BBB", operator="crosses_above", threshold=100.0)
        engine = AlertEngine(store)
        await engine.evaluate(_quote("T1", 90.0, ts=_ts(1)))
        await engine.evaluate(_quote("T2", 90.0, ts=_ts(1), tradingsymbol="BBB"))
        results = await _run_concurrent_evals(
            engine,
            [_quote("T1", 110.0, ts=_ts(2)), _quote("T2", 110.0, ts=_ts(2), tradingsymbol="BBB")],
        )
        fired_ids = sorted(n["alert_id"] for r in results for n in r)
        runner.assert_eq("different-instruments-both-fire", fired_ids, sorted([a1["id"], a2["id"]]))


async def test_failure_in_one_alert_does_not_block_others(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        a1 = _create_alert(store, token="T1", tradingsymbol="AAA", operator="gt", threshold=100.0)
        a2 = _create_alert(store, token="T2", tradingsymbol="BBB", operator="gt", threshold=100.0)
        engine = AlertEngine(store)

        orig_commit = store.commit_market_alert_trigger_with_event

        def flaky_commit(**kw):
            if kw.get("alert_id") == a1["id"]:
                raise RuntimeError("boom")
            return orig_commit(**kw)

        store.commit_market_alert_trigger_with_event = flaky_commit  # type: ignore[assignment]
        try:
            results = await _run_concurrent_evals(
                engine,
                [_quote("T1", 110.0, ts=_ts(1)), _quote("T2", 110.0, ts=_ts(1), tradingsymbol="BBB")],
            )
        finally:
            store.commit_market_alert_trigger_with_event = orig_commit  # type: ignore[assignment]

        fired_ids = sorted(n["alert_id"] for r in results for n in r)
        runner.assert_eq("one-fails-other-fires", fired_ids, [a2["id"]])
        alerts = {a["id"]: a for a in store.list_market_alerts()}
        runner.assert_eq("failed-alert-still-inactive", alerts[a1["id"]]["state"], "inactive")
        runner.assert_eq("other-alert-triggered", alerts[a2["id"]]["state"], "triggered")


# -- state and lifecycle -----------------------------------------------------

async def test_alert_disabled_while_waiting(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)

        # Pause before durable commit (no DB transaction held) so the alert
        # can be disabled concurrently; conditional commit must not fire.
        # Commit runs on the event-loop thread, so mutations/release happen in
        # a background thread.
        orig_commit = store.commit_market_alert_trigger_with_event
        import threading
        entered = threading.Event()
        release = threading.Event()

        def mutate_and_release():
            entered.wait(timeout=2.0)
            store.set_alert_enabled(alert["id"], False)
            engine.reload()
            release.set()

        def blocking_commit(**kw):
            entered.set()
            release.wait(timeout=2.0)
            return orig_commit(**kw)

        store.commit_market_alert_trigger_with_event = blocking_commit  # type: ignore[assignment]
        t = threading.Thread(target=mutate_and_release, daemon=True)
        t.start()
        try:
            fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        finally:
            release.set()
            t.join(timeout=2.0)
            store.commit_market_alert_trigger_with_event = orig_commit  # type: ignore[assignment]

        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        alerts = {a["id"]: a for a in store.list_market_alerts()}
        runner.assert_eq("disabled-no-fire", len(fired), 0)
        runner.assert_eq("disabled-no-history", len(hist), 0)
        runner.assert_eq("disabled-state", alerts[alert["id"]]["state"], "disabled")


async def test_alert_deleted_while_waiting(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)

        # Pause before durable commit (no DB transaction held) so the alert
        # can be deleted concurrently; conditional commit must not fire.
        # Commit runs on the event-loop thread, so mutations/release happen in
        # a background thread.
        orig_commit = store.commit_market_alert_trigger_with_event
        import threading
        entered = threading.Event()
        release = threading.Event()

        def mutate_and_release():
            entered.wait(timeout=2.0)
            store.delete_alert(alert["id"])
            engine.reload()
            release.set()

        def blocking_commit(**kw):
            entered.set()
            release.wait(timeout=2.0)
            return orig_commit(**kw)

        store.commit_market_alert_trigger_with_event = blocking_commit  # type: ignore[assignment]
        t = threading.Thread(target=mutate_and_release, daemon=True)
        t.start()
        try:
            fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        finally:
            release.set()
            t.join(timeout=2.0)
            store.commit_market_alert_trigger_with_event = orig_commit  # type: ignore[assignment]

        # Deleted alert: atomic commit updates 0 rows -> no trigger.
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("deleted-no-history", len(hist), 0)
        runner.assert_eq("deleted-no-fire", len(fired), 0)


async def test_rearm_allows_one_new_fire(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        r1 = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        runner.assert_eq("initial-fire", len(r1), 1)
        store.rearm_alert(alert["id"])
        engine.reload()
        results = await _run_concurrent_evals(
            engine,
            [_quote("T1", 111.0, ts=_ts(2)), _quote("T1", 112.0, ts=_ts(3))],
        )
        fired_total = sum(len(r) for r in results)
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("rearm-fire-once", fired_total, 1)
        runner.assert_eq("rearm-history-total-two", len(hist), 2)


async def test_lock_registry_cleanup_after_delete(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        ids = []
        for i in range(20):
            a = _create_alert(store, token=f"T{i}", tradingsymbol=f"S{i}", operator="gt", threshold=100.0)
            ids.append(a["id"])
        engine = AlertEngine(store)
        # Touch locks by evaluating matching quotes (no hits needed to create locks? hit creates lock)
        for i, _aid in enumerate(ids):
            await engine.evaluate(_quote(f"T{i}", 110.0, ts=_ts(i + 1), tradingsymbol=f"S{i}"))
        for aid in ids:
            store.delete_alert(aid)
        engine.reload()
        # Idle locks for deleted alerts should be cleaned.
        runner.assert_le("lock-registry-bounded", len(engine._alert_locks), 1)


# -- failure recovery --------------------------------------------------------

async def test_trigger_persistence_fails_no_fire(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        orig = store.commit_market_alert_trigger_with_event

        def boom(**kw):
            raise RuntimeError("persist boom")

        store.commit_market_alert_trigger_with_event = boom  # type: ignore[assignment]
        try:
            fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        finally:
            store.commit_market_alert_trigger_with_event = orig  # type: ignore[assignment]
        runner.assert_eq("persist-fail-no-fire", len(fired), 0)
        alerts = store.list_market_alerts()
        runner.assert_eq("persist-fail-still-inactive", alerts[0]["state"], "inactive")
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("persist-fail-no-history", len(hist), 0)
        events = [e for e in store.list_pending(limit=10)
                  if e.get("type") == "alert.triggered"]
        runner.assert_eq("persist-fail-no-event", len(events), 0)


async def test_event_publish_fails_after_durable_commit(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        import core.events as ev
        orig_finalize = ev.finalize_persisted_event
        orig_publish = ev.publish_event

        async def boom(*a, **k):
            raise RuntimeError("publish boom")

        ev.finalize_persisted_event = boom
        ev.publish_event = boom
        try:
            fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        finally:
            ev.finalize_persisted_event = orig_finalize
            ev.publish_event = orig_publish

        # Durable trigger commitment must remain (at-most-once commitment).
        alerts = store.list_market_alerts()
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        events = store.list_pending(limit=10)
        triggered = [e for e in events if e.get("type") == "alert.triggered"]
        runner.assert_eq("publish-fail-durable-state", alerts[0]["state"], "triggered")
        runner.assert_eq("publish-fail-durable-history", len(hist), 1)
        runner.assert_eq("publish-fail-durable-event", len(triggered), 1)
        # Notification reflects committed trigger in current implementation.
        runner.assert_eq("publish-fail-notification-once", len(fired), 1)


async def test_restart_after_durable_commit_no_duplicate(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        # Simulate restart: new engine instance, same store.
        engine2 = AlertEngine(store)
        fired = await engine2.evaluate(_quote("T1", 120.0, ts=_ts(2)))
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("restart-no-refire", len(fired), 0)
        runner.assert_eq("restart-history-once", len(hist), 1)


async def test_retry_after_partial_failure_no_duplicate(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        import core.events as ev
        orig_finalize = ev.finalize_persisted_event
        orig_publish = ev.publish_event
        calls = {"n": 0}

        async def flaky_finalize(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("publish boom once")
            return await orig_finalize(*a, **k)

        ev.finalize_persisted_event = flaky_finalize
        ev.publish_event = flaky_finalize
        try:
            fired1 = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
            # Retry evaluation with another satisfying quote; commit must not duplicate.
            fired2 = await engine.evaluate(_quote("T1", 111.0, ts=_ts(2)))
        finally:
            ev.finalize_persisted_event = orig_finalize
            ev.publish_event = orig_publish

        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        alerts = store.list_market_alerts()
        events = [e for e in store.list_pending(limit=10)
                  if e.get("type") == "alert.triggered"]
        runner.assert_eq("partial-first-committed", len(fired1), 1)
        runner.assert_eq("partial-retry-no-duplicate-fire", len(fired2), 0)
        runner.assert_eq("partial-history-once", len(hist), 1)
        runner.assert_eq("partial-event-once", len(events), 1)
        runner.assert_eq("partial-state-triggered", alerts[0]["state"], "triggered")


async def test_atomic_commit_state_history_event(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        alerts = store.list_market_alerts()
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        events = [e for e in store.list_pending(limit=10)
                  if e.get("type") == "alert.triggered"]
        runner.assert_eq("atomic-fire-once", len(fired), 1)
        runner.assert_eq("atomic-state-triggered", alerts[0]["state"], "triggered")
        runner.assert_eq("atomic-history-once", len(hist), 1)
        runner.assert_eq("atomic-event-once", len(events), 1)
        runner.assert_eq("atomic-event-alert-id",
                         events[0]["data"]["alert_id"], alert["id"])
        runner.assert_eq("atomic-event-source", events[0]["source"], "alert_engine")


async def test_persistence_failure_rolls_back_all_three(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        orig = store.commit_market_alert_trigger_with_event

        def boom(**kw):
            raise RuntimeError("persist boom")

        store.commit_market_alert_trigger_with_event = boom  # type: ignore[assignment]
        try:
            fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        finally:
            store.commit_market_alert_trigger_with_event = orig  # type: ignore[assignment]

        alerts = store.list_market_alerts()
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        events = [e for e in store.list_pending(limit=10)
                  if e.get("type") == "alert.triggered"]
        runner.assert_eq("rollback-no-fire", len(fired), 0)
        runner.assert_eq("rollback-state-inactive", alerts[0]["state"], "inactive")
        runner.assert_eq("rollback-no-history", len(hist), 0)
        runner.assert_eq("rollback-no-event", len(events), 0)


async def test_finalize_failure_keeps_durable_event_discoverable(runner: R) -> None:
    from app.alerts import AlertEngine

    with tempfile.TemporaryDirectory() as tmp:
        store = _mk_store(tmp)
        alert = _create_alert(store, operator="gt", threshold=100.0)
        engine = AlertEngine(store)
        import core.events as ev
        orig_finalize = ev.finalize_persisted_event

        async def boom(*a, **k):
            raise RuntimeError("finalize boom")

        ev.finalize_persisted_event = boom
        try:
            fired = await engine.evaluate(_quote("T1", 110.0, ts=_ts(1)))
        finally:
            ev.finalize_persisted_event = orig_finalize

        events = [e for e in store.list_pending(limit=10)
                  if e.get("type") == "alert.triggered"]
        runner.assert_eq("discoverable-fire-once", len(fired), 1)
        runner.assert_eq("discoverable-event-once", len(events), 1)
        runner.assert_true("discoverable-event-id", bool(events[0].get("id")))
        runner.assert_eq("discoverable-event-payload-alert",
                         events[0]["data"]["alert_id"], alert["id"])

        # "Restart"/new engine context: durable committed event remains
        # discoverable with original id/payload and no duplicate trigger.
        engine2 = AlertEngine(store)
        fired2 = await engine2.evaluate(_quote("T1", 120.0, ts=_ts(2)))
        events2 = [e for e in store.list_pending(limit=10)
                   if e.get("type") == "alert.triggered"]
        hist = store.list_alert_trigger_history(alert_id=alert["id"], limit=10)
        runner.assert_eq("restart-no-refire", len(fired2), 0)
        runner.assert_eq("restart-event-still-once", len(events2), 1)
        runner.assert_eq("restart-history-still-once", len(hist), 1)
        runner.assert_eq("restart-same-event-id", events2[0]["id"], events[0]["id"])
        runner.assert_eq("restart-same-payload", events2[0]["data"], events[0]["data"])


async def main() -> bool:
    runner = R()
    tests = [
        test_dup_gt_same_rule,
        test_dup_lt_same_rule,
        test_dup_threshold_burst_10,
        test_crosses_above_two_quotes,
        test_crosses_below_two_quotes,
        test_crossing_rapid_succession,
        test_repeated_identical_values,
        test_first_quote_no_false_crossing,
        test_different_alerts_concurrent,
        test_different_instruments_independent,
        test_failure_in_one_alert_does_not_block_others,
        test_alert_disabled_while_waiting,
        test_alert_deleted_while_waiting,
        test_rearm_allows_one_new_fire,
        test_lock_registry_cleanup_after_delete,
        test_trigger_persistence_fails_no_fire,
        test_event_publish_fails_after_durable_commit,
        test_restart_after_durable_commit_no_duplicate,
        test_retry_after_partial_failure_no_duplicate,
        test_atomic_commit_state_history_event,
        test_persistence_failure_rolls_back_all_three,
        test_finalize_failure_keeps_durable_event_discoverable,
    ]
    for fn in tests:
        try:
            await fn(runner)
        except Exception as exc:
            runner.fail(fn.__name__, str(exc))
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
