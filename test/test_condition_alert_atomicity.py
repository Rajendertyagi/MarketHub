#!/usr/bin/env python3
"""B2 condition-trigger atomicity tests (fault injection).

Proves the atomic trigger transaction (B2 §38/§46) can never leave a
half-persisted trigger: runtime state + alert row + persistent event +
consumer materialization commit together, and ANY failure rolls back
everything — a lost trigger is forbidden.

  * AT1  materialization failure -> full rollback (no event, no state,
        no trigger_count bump, no "already fired")
  * AT2  event-insert failure -> full rollback
  * AT3  engine-level: a persistence failure never double-fires later
        (the alert stays armed because nothing was committed)
  * AT4  success path commits all four artifacts atomically
  * AT5  concurrent atomic triggers serialize (no lost updates)

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

from core.persistence.store import EventStore

RELIANCE = "NSE:EQUITY:INE002A01018"


def _mk_store() -> tuple[EventStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    store.register_consumer("consumer-1")
    return store, tmp


def _create(store, trigger_mode="repeat"):
    return store.create_condition_alert(
        consumer_id="consumer-1", name="test", trigger_mode=trigger_mode,
        condition_json={"condition_version": 1, "condition_id": "cond-1",
                        "metric": "ltp", "operator": "gt", "value": 100,
                        "instrument": {"canonical_id": RELIANCE}})


def _snapshot(store, alert_id):
    a = store.get_condition_alert(alert_id)
    st = store.load_condition_runtime_state()
    events = [e for e in store.list_pending(100)
              if e["type"] == "alert.triggered"]
    state = st.get(alert_id)
    # Normalize: extract root state for compatibility.
    root_key = "root-" + alert_id
    if state and root_key in state:
        norm_state = state[root_key]
    elif state:
        norm_state = next(iter(state.values())) if state else None
    else:
        norm_state = None
    return {
        "trigger_count": a["trigger_count"],
        "enabled": a["enabled"],
        "state": norm_state,
        "events": len(events),
    }


def _trigger_kwargs(alert_id, **over):
    kw = dict(
        alert_id=alert_id, consumer_id="consumer-1",
        event_id="evt-1", event_type="alert.triggered",
        source="alert_engine", timestamp="2026-08-30T00:00:00+00:00",
        data={"version": 1, "alert_family": "market_condition"},
        routing={"targets": ["consumer-1"]},
        enabled=True, trigger_count=1,
        last_triggered_at="2026-08-30T00:00:00+00:00",
        state_updates={
            "root-" + alert_id: {"last_result": "true",
                                  "crossing_side": "unknown"}})
    kw.update(over)
    return kw


async def test_at1_materialize_failure_rollback(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        alert_id = _create(store)
        before = _snapshot(store, alert_id)
        # Fault-inject: materialize_fn raises AFTER the event insert. We
        # exercise the module-level atomic function directly (the store
        # facade wraps it with the production materializer).
        from core.persistence.modules import condition_alerts as ca
        import sqlite3 as _sqlite3
        conn = store._open(store._db_path)
        try:
            def boom(conn, event_id, seq, routing):
                raise RuntimeError("materialize boom")
            try:
                ca.save_condition_trigger(
                    conn,
                    **_trigger_kwargs(alert_id),
                    materialize_fn=boom)
                runner.fail("AT1", "expected RuntimeError")
            except RuntimeError:
                runner.ok("AT1-raised")
        finally:
            conn.close()
        after = _snapshot(store, alert_id)
        runner.assert_eq("AT1-no-event", after["events"], before["events"])
        runner.assert_eq("AT1-no-count", after["trigger_count"],
                         before["trigger_count"])
        runner.assert_eq("AT1-no-state", after["state"], before["state"])
        runner.assert_eq("AT1-still-enabled", after["enabled"], True)
    finally:
        tmp.cleanup()


async def test_at2_event_insert_failure_rollback(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        alert_id = _create(store)
        before = _snapshot(store, alert_id)
        # Fault-inject: the persistent_events insert fails (duplicate id).
        store.save_condition_trigger(
            **_trigger_kwargs(alert_id, event_id="dup-event"))
        # Second attempt with the SAME event id -> UNIQUE constraint failure
        # inside the transaction -> full rollback.
        try:
            store.save_condition_trigger(
                **_trigger_kwargs(alert_id, event_id="dup-event",
                                  trigger_count=2))
            runner.fail("AT2", "expected sqlite3.IntegrityError")
        except sqlite3.IntegrityError:
            runner.ok("AT2-raised")
        after = _snapshot(store, alert_id)
        # The failed second trigger changed nothing.
        runner.assert_eq("AT2-count", after["trigger_count"], 1)
        runner.assert_eq("AT2-events", after["events"], 1)
        runner.assert_eq("AT2-state", after["state"]["last_result"], "true")
    finally:
        tmp.cleanup()


async def test_at3_engine_no_double_fire_after_failure(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        alert_id = _create(store)
        from app.market_identity import MarketInstrumentIdentityResolver
        from app.condition_alerts import ConditionAlertEngine
        store.replace_provider_instruments("upstox", [
            {"exchange": "NSE", "instrument_token": "2885",
             "tradingsymbol": "RELIANCE", "name": "Reliance Industries",
             "instrument_type": "EQ", "segment": "NSE",
             "isin": "INE002A01018"},
        ])
        resolver = MarketInstrumentIdentityResolver()
        resolver.register_catalog_rows(store.list_all_instruments())
        engine = ConditionAlertEngine(store, resolver=resolver)

        class _Q:
            exchange = "NSE"
            instrument_token = "2885"
            tradingsymbol = "RELIANCE"
            ltp = 101
            provider = "upstox"

        # Sabotage the store's atomic method so the FIRST trigger fails.
        original = store.save_condition_trigger
        calls = {"n": 0}

        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("first trigger boom")
            return original(**kw)

        store.save_condition_trigger = flaky
        async def run():
            fired = await engine.evaluate(_Q())
            return fired
        fired = await run()
        runner.assert_eq("AT3-first-failed", len(fired), 0)
        # Nothing committed: alert still armed, no event, no state.
        snap = _snapshot(store, alert_id)
        runner.assert_eq("AT3-no-event", snap["events"], 0)
        runner.assert_eq("AT3-no-state", snap["state"], None)
        runner.assert_eq("AT3-armed", snap["enabled"], True)
        # Next quote (still TRUE) fires exactly once — the failed attempt
        # did NOT consume the trigger.
        store.save_condition_trigger = original
        async def run2():
            fired = await engine.evaluate(_Q())
            return fired
        fired = await run2()
        runner.assert_eq("AT3-second-fires", len(fired), 1)
        runner.assert_eq("AT3-one-event", _snapshot(store, alert_id)["events"], 1)
    finally:
        tmp.cleanup()


async def test_at4_success_commits_all(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        alert_id = _create(store)
        seq = store.save_condition_trigger(**_trigger_kwargs(alert_id))
        runner.assert_eq("AT4-seq", seq, 1)
        snap = _snapshot(store, alert_id)
        runner.assert_eq("AT4-count", snap["trigger_count"], 1)
        runner.assert_eq("AT4-state", snap["state"]["last_result"], "true")
        runner.assert_eq("AT4-events", snap["events"], 1)
        # Consumer inbox materialized.
        pending = store.list_relevant_events("consumer-1", None, 10)
        runner.assert_eq("AT4-inbox", len(pending), 1)
    finally:
        tmp.cleanup()


async def test_at5_concurrent_atomic_triggers(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        # Concurrent atomic triggers on DIFFERENT alerts: every event commits,
        # no cross-alert interference, each alert's count is exactly 1.
        alert_ids = [_create(store) for _ in range(10)]
        async def run():
            await asyncio.gather(*[
                asyncio.to_thread(
                    store.save_condition_trigger,
                    **_trigger_kwargs(alert_id, event_id=f"evt-{i}",
                                      trigger_count=1))
                for i, alert_id in enumerate(alert_ids)
            ])
            return None
        await run()
        snap = _snapshot(store, alert_ids[0])
        runner.assert_eq("AT5-events", snap["events"], 10)
        for alert_id in alert_ids:
            a = store.get_condition_alert(alert_id)
            runner.assert_eq(f"AT5-count-{alert_id[:6]}",
                             a["trigger_count"], 1)
    finally:
        tmp.cleanup()


async def main() -> bool:
    runner = R()
    await test_at1_materialize_failure_rollback(runner)
    await test_at2_event_insert_failure_rollback(runner)
    await test_at3_engine_no_double_fire_after_failure(runner)
    await test_at4_success_commits_all(runner)
    await test_at5_concurrent_atomic_triggers(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)