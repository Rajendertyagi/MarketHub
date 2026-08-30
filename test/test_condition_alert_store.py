#!/usr/bin/env python3
"""B2 condition-alert persistence tests (schema v13).

Covers the condition_alerts + condition_runtime_state tables and the
EventStore facade:

  * CA1  fresh DB creates both tables (schema version 13)
  * CA2  create/list/get — definition round-trip
  * CA3  validation rejections — version, groups, metric, operator,
        threshold, canonical_id
  * CA4  consumer must exist (FK)
  * CA5  set_enabled / delete
  * CA6  runtime state upsert + reload
  * CA7  atomic save_condition_trigger — state + alert + event + inbox
  * CA8  once-mode disables the alert inside the same transaction
  * CA9  v12 -> v13 migration preserves existing content
  * CA10 repeated startup idempotent (no migration re-run errors)
  * CA11 load_enabled_condition_alerts returns only enabled rows

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

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

from core.errors import ConditionValidationError, ConsumerNotFoundError
from core.persistence.store import EventStore

RELIANCE = "NSE:EQUITY:INE002A01018"


def _mk_store() -> tuple[EventStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


def _condition(**over) -> dict:
    c = dict(condition_version=1, condition_id="cond-1", metric="ltp",
             operator="gt", value=25000,
             instrument={"canonical_id": RELIANCE})
    c.update(over)
    return c


def test_ca1_fresh_schema(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        runner.assert_eq("CA1-version", store.schema_version(), 13)
        conn = sqlite3.connect(store._db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        runner.assert_in("CA1-condition_alerts", "condition_alerts", tables)
        runner.assert_in("CA1-condition_runtime_state",
                         "condition_runtime_state", tables)
    finally:
        tmp.cleanup()


def test_ca2_crud_roundtrip(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="NIFTY above 25000",
            trigger_mode="repeat", condition_json=_condition(),
            metadata={"note": "test"})
        runner.assert_true("CA2-id", isinstance(alert_id, str) and len(alert_id) == 32,
                           f"alert_id not a 32-char uuid hex: {alert_id!r}")
        alerts = store.list_condition_alerts("consumer-1")
        runner.assert_eq("CA2-count", len(alerts), 1)
        a = alerts[0]
        runner.assert_eq("CA2-name", a["name"], "NIFTY above 25000")
        runner.assert_eq("CA2-trigger_mode", a["trigger_mode"], "repeat")
        runner.assert_eq("CA2-enabled", a["enabled"], True)
        runner.assert_eq("CA2-trigger_count", a["trigger_count"], 0)
        runner.assert_eq("CA2-condition", a["condition"], _condition())
        runner.assert_eq("CA2-metadata", a["metadata"], {"note": "test"})
        got = store.get_condition_alert(alert_id)
        runner.assert_eq("CA2-get-id", got["alert_id"], alert_id)
        runner.assert_eq("CA2-get-consumer", got["consumer_id"], "consumer-1")
        # list without filter returns all.
        runner.assert_eq("CA2-list-all", len(store.list_condition_alerts()), 1)
    finally:
        tmp.cleanup()


def test_ca3_validation_rejections(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        bad_cases = {
            "version": _condition(condition_version=2),
            "groups-logic": _condition(logic="AND"),
            "groups-conditions": _condition(conditions=[_condition()]),
            "metric": _condition(metric="pcr"),
            "operator": _condition(operator="between"),
            "threshold-bool": _condition(value=True),
            "threshold-str": _condition(value="25000"),
            "no-canonical": _condition(instrument={"canonical_id": ""}),
            "no-instrument": _condition(instrument=None),
        }
        for label, cond in bad_cases.items():
            try:
                store.create_condition_alert(
                    consumer_id="consumer-1", name="bad",
                    trigger_mode="repeat", condition_json=cond)
                runner.fail(f"CA3-{label}", "expected ConditionValidationError")
            except ConditionValidationError:
                runner.ok(f"CA3-{label}")
        # No bad rows were persisted.
        runner.assert_eq("CA3-nothing-persisted",
                         len(store.list_condition_alerts()), 0)
    finally:
        tmp.cleanup()


def test_ca4_consumer_required(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        try:
            store.create_condition_alert(
                consumer_id="ghost", name="x", trigger_mode="repeat",
                condition_json=_condition())
            runner.fail("CA4-fk", "expected ConsumerNotFoundError")
        except ConsumerNotFoundError:
            runner.ok("CA4-fk")
    finally:
        tmp.cleanup()


def test_ca5_enable_disable_delete(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="x", trigger_mode="repeat",
            condition_json=_condition())
        store.set_condition_alert_enabled(alert_id, False)
        runner.assert_eq("CA5-disabled",
                         store.get_condition_alert(alert_id)["enabled"], False)
        runner.assert_eq("CA5-load-enabled-empty",
                         len(store.load_enabled_condition_alerts()), 0)
        store.set_condition_alert_enabled(alert_id, True)
        runner.assert_eq("CA5-re-enabled",
                         store.get_condition_alert(alert_id)["enabled"], True)
        runner.assert_eq("CA5-load-enabled-one",
                         len(store.load_enabled_condition_alerts()), 1)
        store.delete_condition_alert(alert_id)
        runner.assert_eq("CA5-deleted", store.get_condition_alert(alert_id), None)
        runner.assert_eq("CA5-empty-after-delete",
                         len(store.list_condition_alerts()), 0)
    finally:
        tmp.cleanup()


def test_ca6_runtime_state(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="x", trigger_mode="repeat",
            condition_json=_condition())
        store.save_condition_runtime_state(
            alert_id=alert_id, condition_id="cond-1",
            last_result="false", crossing_side="unknown")
        st = store.load_condition_runtime_state()
        runner.assert_eq("CA6-state", st[alert_id]["last_result"], "false")
        # Upsert overwrites.
        store.save_condition_runtime_state(
            alert_id=alert_id, condition_id="cond-1",
            last_result="true", crossing_side="unknown")
        st = store.load_condition_runtime_state()
        runner.assert_eq("CA6-upsert", st[alert_id]["last_result"], "true")
        runner.assert_eq("CA6-condition_id", st[alert_id]["condition_id"], "cond-1")
    finally:
        tmp.cleanup()


def test_ca7_atomic_trigger(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="x", trigger_mode="repeat",
            condition_json=_condition())
        seq = store.save_condition_trigger(
            alert_id=alert_id, condition_id="cond-1",
            consumer_id="consumer-1", event_id="evt-1",
            event_type="alert.triggered", source="alert_engine",
            timestamp="2026-08-30T00:00:00+00:00",
            data={"version": 1, "alert_family": "market_condition"},
            routing={"targets": ["consumer-1"]},
            last_result="true", crossing_side="unknown",
            enabled=True, trigger_count=1,
            last_triggered_at="2026-08-30T00:00:00+00:00")
        runner.assert_eq("CA7-seq", seq, 1)
        # Alert row updated.
        a = store.get_condition_alert(alert_id)
        runner.assert_eq("CA7-trigger_count", a["trigger_count"], 1)
        runner.assert_eq("CA7-last_triggered_at", a["last_triggered_at"],
                         "2026-08-30T00:00:00+00:00")
        # Runtime state updated.
        st = store.load_condition_runtime_state()
        runner.assert_eq("CA7-state", st[alert_id]["last_result"], "true")
        # Persistent event + consumer inbox materialized.
        pending = store.list_relevant_events("consumer-1", None, 10)
        runner.assert_eq("CA7-pending", len(pending), 1)
        runner.assert_eq("CA7-pending-id", pending[0]["id"], "evt-1")
        runner.assert_eq("CA7-pending-type", pending[0]["type"], "alert.triggered")
    finally:
        tmp.cleanup()


def test_ca8_once_disables_in_txn(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="x", trigger_mode="once",
            condition_json=_condition())
        store.save_condition_trigger(
            alert_id=alert_id, condition_id="cond-1",
            consumer_id="consumer-1", event_id="evt-1",
            event_type="alert.triggered", source="alert_engine",
            timestamp="2026-08-30T00:00:00+00:00",
            data={"version": 1, "alert_family": "market_condition"},
            routing={"targets": ["consumer-1"]},
            last_result="true", crossing_side="unknown",
            enabled=False, trigger_count=1,
            last_triggered_at="2026-08-30T00:00:00+00:00")
        a = store.get_condition_alert(alert_id)
        runner.assert_eq("CA8-disabled", a["enabled"], False)
        runner.assert_eq("CA8-load-enabled-empty",
                         len(store.load_enabled_condition_alerts()), 0)
    finally:
        tmp.cleanup()


def test_ca9_migration_preserves(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        # Seed a v12-era generic alert + event, then downgrade to v12.
        store.create_alert(
            alert_id="legacy-alert-1", consumer_id="consumer-1",
            name="legacy", source="test", event_type="test.tick",
            field_path="tick", operator="gte", value=1, one_shot=False)
        conn = sqlite3.connect(store._db_path)
        conn.execute("DROP TABLE condition_runtime_state")
        conn.execute("DROP TABLE condition_alerts")
        conn.execute("PRAGMA user_version = 12")
        conn.commit()
        conn.close()
        # Reopen -> migrates v12->v13.
        store2 = EventStore(store._db_path)
        runner.assert_eq("CA9-version", store2.schema_version(), 13)
        # Existing content preserved.
        alerts = store2.list_alerts("consumer-1")
        runner.assert_eq("CA9-legacy-preserved", len(alerts), 1)
        runner.assert_eq("CA9-legacy-name", alerts[0]["name"], "legacy")
        # New tables usable.
        alert_id = store2.create_condition_alert(
            consumer_id="consumer-1", name="post-migration",
            trigger_mode="repeat", condition_json=_condition())
        runner.assert_true("CA9-post-migration", bool(alert_id))
    finally:
        tmp.cleanup()


def test_ca10_repeated_startup(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        store.create_condition_alert(
            consumer_id="consumer-1", name="x", trigger_mode="repeat",
            condition_json=_condition())
        # Reopen the same DB repeatedly — no migration re-run errors.
        for i in range(3):
            s = EventStore(store._db_path)
            runner.assert_eq(f"CA10-reopen-{i}", s.schema_version(), 13)
            runner.assert_eq(f"CA10-alerts-{i}",
                             len(s.list_condition_alerts()), 1)
    finally:
        tmp.cleanup()


def test_ca11_load_enabled_only(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        a1 = store.create_condition_alert(
            consumer_id="consumer-1", name="on", trigger_mode="repeat",
            condition_json=_condition(condition_id="c-on"))
        a2 = store.create_condition_alert(
            consumer_id="consumer-1", name="off", trigger_mode="repeat",
            condition_json=_condition(condition_id="c-off"))
        store.set_condition_alert_enabled(a2, False)
        enabled = store.load_enabled_condition_alerts()
        runner.assert_eq("CA11-count", len(enabled), 1)
        runner.assert_eq("CA11-id", enabled[0]["alert_id"], a1)
    finally:
        tmp.cleanup()


async def main() -> bool:
    runner = R()
    test_ca1_fresh_schema(runner)
    test_ca2_crud_roundtrip(runner)
    test_ca3_validation_rejections(runner)
    test_ca4_consumer_required(runner)
    test_ca5_enable_disable_delete(runner)
    test_ca6_runtime_state(runner)
    test_ca7_atomic_trigger(runner)
    test_ca8_once_disables_in_txn(runner)
    test_ca9_migration_preserves(runner)
    test_ca10_repeated_startup(runner)
    test_ca11_load_enabled_only(runner)
    return runner.summary()


if __name__ == "__main__":
    import asyncio
    success = asyncio.run(main())
    sys.exit(0 if success else 1)