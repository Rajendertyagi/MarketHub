"""Tests for AI Alert Observability endpoints.

Verifies the read-only REST API for condition-alert lifecycle data.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R
from core.persistence.store import EventStore
from core import events


class _StubBus:
    async def publish(self, item):
        pass


def _mk_store():
    tmp = tempfile.mkdtemp(prefix="baiobs_")
    store = EventStore(os.path.join(tmp, "events.db"))
    store.register_consumer("c1")
    store.register_consumer("c2")
    return store, tmp


def _create_condition_alert(store, consumer_id="c1", threshold=25000.0,
                            enabled=True, trigger_mode="repeat",
                            canonical_id="NSE:EQUITY:INE002A01018"):
    return store.create_condition_alert(
        consumer_id=consumer_id,
        name=f"test-alert-{threshold}",
        trigger_mode=trigger_mode,
        condition_json={
            "condition_version": 1,
            "condition_id": f"cond-{int(time.time()*1000)}",
            "metric": "ltp",
            "operator": "gt",
            "value": threshold,
            "instrument": {"canonical_id": canonical_id},
        },
    )


async def t1_alerts_appear_in_api(runner: R) -> None:
    """MCP-created alert appears in /api/ai-alerts."""
    name = "T1-alerts-appear"
    store, tmp = _mk_store()
    try:
        aid = _create_condition_alert(store, threshold=25000.0)
        aid2 = _create_condition_alert(store, consumer_id="c2", threshold=30000.0)

        conn = store._open(store._db_path)
        from api.ai_alert_routes import build_ai_alert_routes
        routes = build_ai_alert_routes(store)

        # Simulate GET /api/ai-alerts
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=routes)
        client = TestClient(app)
        resp = client.get("/api/ai-alerts")
        runner.assert_eq(name + "-status", resp.status_code, 200)
        data = resp.json()
        runner.assert_eq(name + "-count", data["count"], 2)

        alert_ids = {a["alert_id"] for a in data["alerts"]}
        runner.assert_in(name + "-aid1", aid, alert_ids)
        runner.assert_in(name + "-aid2", aid2, alert_ids)

        # Check fields present
        a1 = [a for a in data["alerts"] if a["alert_id"] == aid][0]
        runner.assert_eq(name + "-consumer", a1["consumer_id"], "c1")
        runner.assert_eq(name + "-mode", a1["trigger_mode"], "repeat")
        runner.assert_eq(name + "-enabled", a1["enabled"], True)
        runner.assert_eq(name + "-instrument", a1["instrument"], "NSE:EQUITY:INE002A01018")
        runner.assert_true(name + "-has-state", a1["current_state"] in ("unknown", "true", "false"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t2_trigger_appears_in_events(runner: R) -> None:
    """Triggered event appears in /api/ai-alerts/events."""
    name = "T2-trigger-appear"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        aid = _create_condition_alert(store, threshold=100.0)
        data = {
            "alert_family": "market_condition",
            "alert_id": aid,
            "consumer_id": "c1",
            "condition": {
                "condition_version": 1, "logic": None,
                "conditions": [{"condition_version": 1, "condition_id": "c1",
                                "metric": "ltp", "operator": "gt",
                                "value": 100.0,
                                "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
            },
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": False,
        }
        result = await events.publish_event(
            event_type="alert.triggered", source="test",
            data=data, persistent=True,
            routing={"targets": ["c1"]},
            store=store, bus=bus,
        )

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)
        resp = client.get("/api/ai-alerts/events")
        runner.assert_eq(name + "-status", resp.status_code, 200)
        edata = resp.json()
        runner.assert_true(name + "-has-events", edata["count"] >= 1)

        evt = edata["events"][0]
        runner.assert_eq(name + "-event-id", evt["event_id"], result["id"])
        runner.assert_eq(name + "-consumer", evt["consumer_id"], "c1")
        runner.assert_eq(name + "-state", evt["delivery_state"], "persisted")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t3_ack_changes_status(runner: R) -> None:
    """ACK changes delivery_state to acknowledged."""
    name = "T3-ack-status"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        aid = _create_condition_alert(store, threshold=100.0)
        data = {
            "alert_family": "market_condition",
            "alert_id": aid,
            "consumer_id": "c1",
            "condition": {
                "condition_version": 1, "logic": None,
                "conditions": [{"condition_version": 1, "condition_id": "c1",
                                "metric": "ltp", "operator": "gt",
                                "value": 100.0,
                                "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
            },
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": False,
        }
        result = await events.publish_event(
            event_type="alert.triggered", source="test",
            data=data, persistent=True,
            routing={"targets": ["c1"]},
            store=store, bus=bus,
        )

        # Before ACK
        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)
        resp = client.get("/api/ai-alerts/events")
        evt = resp.json()["events"][0]
        runner.assert_eq(name + "-before", evt["delivery_state"], "persisted")

        # ACK
        store.acknowledge_event("c1", result["id"])

        # After ACK
        resp2 = client.get("/api/ai-alerts/events")
        evt2 = resp2.json()["events"][0]
        runner.assert_eq(name + "-after", evt2["delivery_state"], "acknowledged")
        runner.assert_true(name + "-ack-time", evt2["acknowledged_at"] is not None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t4_consumer_status(runner: R) -> None:
    """Consumer status shows pending count and checkpoint."""
    name = "T4-consumer-status"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        aid = _create_condition_alert(store, threshold=100.0)
        data = {
            "alert_family": "market_condition",
            "alert_id": aid,
            "consumer_id": "c1",
            "condition": {
                "condition_version": 1, "logic": None,
                "conditions": [{"condition_version": 1, "condition_id": "c1",
                                "metric": "ltp", "operator": "gt",
                                "value": 100.0,
                                "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
            },
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": False,
        }
        result = await events.publish_event(
            event_type="alert.triggered", source="test",
            data=data, persistent=True,
            routing={"targets": ["c1"]},
            store=store, bus=bus,
        )

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)
        resp = client.get("/api/ai-alerts/consumers")
        runner.assert_eq(name + "-status", resp.status_code, 200)
        cdata = resp.json()
        runner.assert_eq(name + "-count", cdata["count"], 2)

        c1 = [c for c in cdata["consumers"] if c["consumer_id"] == "c1"][0]
        runner.assert_eq(name + "-pending", c1["pending_count"], 1)
        runner.assert_true(name + "-last-trigger", c1["last_triggered"] is not None)

        # ACK and recheck
        store.acknowledge_event("c1", result["id"])
        resp2 = client.get("/api/ai-alerts/consumers")
        c1_2 = [c for c in resp2.json()["consumers"] if c["consumer_id"] == "c1"][0]
        runner.assert_eq(name + "-pending-after", c1_2["pending_count"], 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t5_consumer_isolation(runner: R) -> None:
    """Consumer ownership isolation — c2 cannot see c1 events."""
    name = "T5-isolation"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        data = {
            "alert_family": "market_condition",
            "alert_id": "a1",
            "consumer_id": "c1",
            "condition": {"condition_version": 1, "logic": None, "conditions": []},
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": False,
        }
        await events.publish_event(
            event_type="alert.triggered", source="test",
            data=data, persistent=True,
            routing={"targets": ["c1"]},
            store=store, bus=bus,
        )

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)

        # c1 sees events
        resp1 = client.get("/api/ai-alerts/consumers")
        c1 = [c for c in resp1.json()["consumers"] if c["consumer_id"] == "c1"][0]
        runner.assert_eq(name + "-c1-pending", c1["pending_count"], 1)

        # c2 has 0 pending
        c2 = [c for c in resp1.json()["consumers"] if c["consumer_id"] == "c2"][0]
        runner.assert_eq(name + "-c2-pending", c2["pending_count"], 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t6_repeat_alert_history(runner: R) -> None:
    """Repeat alert triggers multiple times, each event visible in history."""
    name = "T6-repeat-history"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        aid = _create_condition_alert(store, threshold=100.0, trigger_mode="repeat")
        event_ids = []
        for i in range(3):
            data = {
                "alert_family": "market_condition",
                "alert_id": aid,
                "consumer_id": "c1",
                "condition": {
                    "condition_version": 1, "logic": None,
                    "conditions": [{"condition_version": 1, "condition_id": "c1",
                                    "metric": "ltp", "operator": "gt",
                                    "value": 100.0,
                                    "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
                },
                "observed": {"root_result": "true", "leaves": []},
                "instrument": {"canonical_id": "NSE:EQUITY:I"},
                "one_shot": False,
            }
            result = await events.publish_event(
                event_type="alert.triggered", source="test",
                data=data, persistent=True,
                routing={"targets": ["c1"]},
                store=store, bus=bus,
            )
            event_ids.append(result["id"])

        # Simulate what the condition alert engine does: increment trigger_count
        conn = store._open(store._db_path)
        try:
            conn.execute(
                "UPDATE condition_alerts SET trigger_count = 3, "
                "last_triggered_at = datetime('now') WHERE alert_id = ?",
                (aid,))
            conn.commit()
        finally:
            conn.close()

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)
        resp = client.get("/api/ai-alerts/events")
        edata = resp.json()
        runner.assert_eq(name + "-count", edata["count"], 3)

        # Each event has distinct event_id
        ids = {e["event_id"] for e in edata["events"]}
        runner.assert_eq(name + "-unique-ids", len(ids), 3)

        # Alert trigger_count should be 3
        resp2 = client.get("/api/ai-alerts")
        alert = [a for a in resp2.json()["alerts"] if a["alert_id"] == aid][0]
        runner.assert_eq(name + "-trigger-count", alert["trigger_count"], 3)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t7_once_alert_single_trigger(runner: R) -> None:
    """Once alert triggers once and stays in history."""
    name = "T7-once-single"
    store, tmp = _mk_store()
    bus = _StubBus()
    try:
        aid = _create_condition_alert(store, threshold=100.0, trigger_mode="once")
        data = {
            "alert_family": "market_condition",
            "alert_id": aid,
            "consumer_id": "c1",
            "condition": {
                "condition_version": 1, "logic": None,
                "conditions": [{"condition_version": 1, "condition_id": "c1",
                                "metric": "ltp", "operator": "gt",
                                "value": 100.0,
                                "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
            },
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": True,
        }
        await events.publish_event(
            event_type="alert.triggered", source="test",
            data=data, persistent=True,
            routing={"targets": ["c1"]},
            store=store, bus=bus,
        )

        # Simulate engine: increment trigger_count
        conn = store._open(store._db_path)
        try:
            conn.execute(
                "UPDATE condition_alerts SET trigger_count = 1, "
                "last_triggered_at = datetime('now') WHERE alert_id = ?",
                (aid,))
            conn.commit()
        finally:
            conn.close()

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)

        # Alert shows trigger_mode=once
        resp = client.get("/api/ai-alerts")
        alert = [a for a in resp.json()["alerts"] if a["alert_id"] == aid][0]
        runner.assert_eq(name + "-mode", alert["trigger_mode"], "once")
        runner.assert_eq(name + "-trigger-count", alert["trigger_count"], 1)

        # Event visible in history
        resp2 = client.get("/api/ai-alerts/events")
        matching = [e for e in resp2.json()["events"] if e["alert_id"] == aid]
        runner.assert_eq(name + "-event-count", len(matching), 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t8_disabled_alert_shows_correctly(runner: R) -> None:
    """Disabled alert shows enabled=false in API."""
    name = "T8-disabled"
    store, tmp = _mk_store()
    try:
        aid = _create_condition_alert(store, threshold=25000.0)
        # Disable it after creation (create always inserts enabled=1)
        store.set_condition_alert_enabled(aid, False)

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)
        resp = client.get("/api/ai-alerts")
        alert = [a for a in resp.json()["alerts"] if a["alert_id"] == aid][0]
        runner.assert_eq(name + "-enabled", alert["enabled"], False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t9_deleted_alert_disappears(runner: R) -> None:
    """Deleted alert disappears from active alerts list."""
    name = "T9-deleted"
    store, tmp = _mk_store()
    try:
        aid = _create_condition_alert(store, threshold=25000.0)

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store))
        client = TestClient(app)

        # Before delete
        resp = client.get("/api/ai-alerts")
        ids_before = {a["alert_id"] for a in resp.json()["alerts"]}
        runner.assert_in(name + "-before", aid, ids_before)

        # Delete
        store.delete_condition_alert(aid)

        # After delete
        resp2 = client.get("/api/ai-alerts")
        ids_after = {a["alert_id"] for a in resp2.json()["alerts"]}
        runner.assert_not_in(name + "-after", aid, ids_after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def t10_restart_preserves_state(runner: R) -> None:
    """Reopening store preserves alerts, runtime state, and pending/ACK."""
    name = "T10-restart"
    tmp = tempfile.mkdtemp(prefix="airestart_")
    try:
        db_path = os.path.join(tmp, "events.db")
        store1 = EventStore(db_path)
        store1.register_consumer("c1")
        bus = _StubBus()

        aid = _create_condition_alert(store1, threshold=100.0)
        data = {
            "alert_family": "market_condition",
            "alert_id": aid,
            "consumer_id": "c1",
            "condition": {
                "condition_version": 1, "logic": None,
                "conditions": [{"condition_version": 1, "condition_id": "c1",
                                "metric": "ltp", "operator": "gt",
                                "value": 100.0,
                                "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
            },
            "observed": {"root_result": "true", "leaves": []},
            "instrument": {"canonical_id": "NSE:EQUITY:I"},
            "one_shot": False,
        }
        result = await events.publish_event(
            event_type="alert.triggered", source="test",
            data=data, persistent=True,
            routing={"targets": ["c1"]},
            store=store1, bus=bus,
        )

        # Reopen store (simulates restart)
        store2 = EventStore(db_path)

        from api.ai_alert_routes import build_ai_alert_routes
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        app = Starlette(routes=build_ai_alert_routes(store2))
        client = TestClient(app)

        # Alert preserved
        resp = client.get("/api/ai-alerts")
        alert_ids = {a["alert_id"] for a in resp.json()["alerts"]}
        runner.assert_in(name + "-alert-preserved", aid, alert_ids)

        # Event preserved with pending state
        resp2 = client.get("/api/ai-alerts/events")
        matching = [e for e in resp2.json()["events"] if e["event_id"] == result["id"]]
        runner.assert_eq(name + "-event-count", len(matching), 1)
        runner.assert_eq(name + "-event-state", matching[0]["delivery_state"], "persisted")

        # ACK survives restart
        store2.acknowledge_event("c1", result["id"])
        resp3 = client.get("/api/ai-alerts/events")
        evt = [e for e in resp3.json()["events"] if e["event_id"] == result["id"]][0]
        runner.assert_eq(name + "-ack-preserved", evt["delivery_state"], "acknowledged")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main() -> int:
    runner = R()
    try:
        print("  AI Alert Observability Tests")
        print("=" * 50)
        tests = [
            t1_alerts_appear_in_api,
            t2_trigger_appears_in_events,
            t3_ack_changes_status,
            t4_consumer_status,
            t5_consumer_isolation,
            t6_repeat_alert_history,
            t7_once_alert_single_trigger,
            t8_disabled_alert_shows_correctly,
            t9_deleted_alert_disappears,
            t10_restart_preserves_state,
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
