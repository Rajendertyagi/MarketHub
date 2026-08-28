#!/usr/bin/env python3
"""
Live MCP consumer-inbox notification tests (MCP-2B.4B).

Covers the per-consumer inbox resource template
``mcp-event://consumers/{consumer_id}/events`` and the live wake-up path over
the modern 2026-07-28 ``subscriptions/listen`` protocol:

  * U1  contract URI builder (encoding, empty rejection)
  * U2  store inbox status (compact shape, ConsumerNotFoundError)
  * U3  store list_relevant_consumers (broadcast/targets/topics mirror)
  * U4  publish_event per-consumer notifications (stub bus)
  * U5  persistence failure -> no notification
  * U6  alert.triggered targeted notification (stub bus)
  * L1  template discovery
  * L2  resource read (compact status)
  * L3  resource read unknown consumer -> error (no fabricated inbox)
  * L4  broadcast live wake (all registered consumers)
  * L5  targeted live wake (only listed consumer)
  * L6  topic live wake (intersecting topics only)
  * L7  transient event -> global only, no inbox
  * L8  live wake + durable replay
  * L9  no auto-ack (checkpoint unchanged)
  * L10 multiple events -> multiple inbox notifications
  * L11 multiple clients same consumer both receive
  * L12 legacy subscribe_resource fallback -> MCPError
  * L13 global latest regression
  * L14 3E reconnect/restart regression
  * L15 exact 42-tool snapshot

NO FIXED SLEEPS: every wait is a bounded async wait (asyncio.wait_for /
deadline loops).

Run:
    python test/test_mcp_live_consumer_notifications.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import uuid

# Ensure project root is importable.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.lifecycle import (  # noqa: E402
    get_server_url,
    restore_environment,
    start_server,
    stop_server,
)
from helpers.mcp_client import (  # noqa: E402
    call,
    read_res,
    wait_source_ready,
)
from helpers.runner import R  # noqa: E402

from mcp.client.client import Client  # noqa: E402
from mcp.client.subscriptions import ResourceUpdated  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from mcp_server.contract import (  # noqa: E402
    RESOURCE_EVENT_LATEST,
    RESOURCE_CONSUMER_EVENTS_PREFIX,
    consumer_events_uri,
)

GLOBAL_URI = RESOURCE_EVENT_LATEST
INBOX_TEMPLATE = "mcp-event://consumers/{consumer_id}/events"

# The frozen 42-tool public surface (MCP-2B.4B must not change it).
EXPECTED_TOOLS = {
    "system_ping",
    "market_quote", "market_depth", "market_status", "instrument_search",
    "watchlists", "market_history",
    "event_list",
    "consumer_register", "consumer_topic_add",
    "consumer_event_pending_list", "consumer_event_acknowledge",
    "consumer_checkpoint_get",
    "alert_create", "alert_list", "alert_get", "alert_enable", "alert_disable",
    "option_chain", "futures_contracts",
    "compute_pcr", "compute_max_pain", "compute_top_oi_strikes", "compute_atm",
    "compute_iv_skew", "compute_oi_buildup", "compute_support_resistance",
    "compute_straddle", "compute_gex", "compute_futures_basis",
    "price_long_straddle", "price_long_strangle", "price_bull_call_spread",
    "price_bear_put_spread", "price_iron_condor", "price_long_butterfly",
    "analyze_option_chain",
    "market_alert_create", "market_alert_list", "market_alert_enable",
    "market_alert_disable", "market_alert_delete",
}


# ===================================================================
# Unit-level tests (no server)
# ===================================================================

def _mk_store():
    tmp = tempfile.TemporaryDirectory()
    from core.persistence.store import EventStore
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


class _StubBus:
    """Minimal in-memory subscription bus recording published URIs."""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, msg) -> None:
        self.published.append(msg.uri)


def u1_contract_uri_builder(runner: R) -> None:
    """U1: consumer_events_uri builds correct URIs and rejects empty input."""
    runner.assert_eq("U1-prefix", RESOURCE_CONSUMER_EVENTS_PREFIX,
                     "mcp-event://consumers/")
    runner.assert_eq("U1-simple", consumer_events_uri("live-A"),
                     "mcp-event://consumers/live-A/events")
    runner.assert_eq("U1-encoded", consumer_events_uri("my consumer"),
                     "mcp-event://consumers/my%20consumer/events")
    try:
        consumer_events_uri("")
        runner.fail("U1-empty", "empty consumer_id must raise ValueError")
    except ValueError:
        runner.ok("U1-empty")


def u2_store_inbox_status(runner: R) -> None:
    """U2: get_consumer_inbox_status returns the compact shape."""
    from core.errors import ConsumerNotFoundError
    store, tmp = _mk_store()
    try:
        store.register_consumer("c1")
        status = store.get_consumer_inbox_status("c1")
        runner.assert_eq("U2-shape", set(status.keys()),
                         {"consumer_id", "checkpoint", "pending_count",
                          "latest_sequence"})
        runner.assert_eq("U2-empty-pending", status["pending_count"], 0)
        runner.assert_eq("U2-empty-latest", status["latest_sequence"], None)
        runner.assert_eq("U2-checkpoint", status["checkpoint"], 0)

        store.save("e1", "test.evt", "u2", "2026-08-28T00:00:00+00:00",
                   {"k": 1}, None)
        status = store.get_consumer_inbox_status("c1")
        runner.assert_eq("U2-pending", status["pending_count"], 1)
        runner.assert_eq("U2-latest", status["latest_sequence"], 1)

        try:
            store.get_consumer_inbox_status("ghost")
            runner.fail("U2-ghost", "unknown consumer must raise")
        except ConsumerNotFoundError:
            runner.ok("U2-ghost")
    finally:
        tmp.cleanup()


def u3_store_list_relevant_consumers(runner: R) -> None:
    """U3: list_relevant_consumers mirrors durable routing semantics."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("c1")
        store.register_consumer("c2")
        store.add_topic("c2", "alpha")
        runner.assert_eq("U3-broadcast", store.list_relevant_consumers(None),
                         ["c1", "c2"])
        runner.assert_eq("U3-targets", store.list_relevant_consumers(
            {"targets": ["c1"]}), ["c1"])
        runner.assert_eq("U3-topics-hit", store.list_relevant_consumers(
            {"topics": ["alpha"]}), ["c2"])
        runner.assert_eq("U3-topics-miss", store.list_relevant_consumers(
            {"topics": ["beta"]}), [])
        runner.assert_eq("U3-empty", store.list_relevant_consumers(
            {"targets": []}), [])
    finally:
        tmp.cleanup()


async def u4_publish_notify_routing(runner: R) -> None:
    """U4: publish_event notifies the right inboxes per routing."""
    from core import events as events_mod
    store, tmp = _mk_store()
    try:
        store.register_consumer("c1")
        store.register_consumer("c2")
        store.add_topic("c2", "alpha")
        bus = _StubBus()

        # Broadcast -> both inboxes + global
        await events_mod.publish_event(
            "test.broadcast", "u4", {"n": 1}, persistent=True,
            store=store, bus=bus)
        runner.assert_in("U4-broadcast-global", GLOBAL_URI, bus.published)
        runner.assert_in("U4-broadcast-c1", consumer_events_uri("c1"),
                         bus.published)
        runner.assert_in("U4-broadcast-c2", consumer_events_uri("c2"),
                         bus.published)

        # Targets -> only c1
        bus.published.clear()
        await events_mod.publish_event(
            "test.targeted", "u4", {"n": 2}, persistent=True,
            store=store, bus=bus, routing={"targets": ["c1"]})
        runner.assert_in("U4-targets-c1", consumer_events_uri("c1"),
                         bus.published)
        runner.assert_not_in("U4-targets-c2", consumer_events_uri("c2"),
                             bus.published)

        # Topics -> only c2
        bus.published.clear()
        await events_mod.publish_event(
            "test.topic", "u4", {"n": 3}, persistent=True,
            store=store, bus=bus, routing={"topics": ["alpha"]})
        runner.assert_in("U4-topics-c2", consumer_events_uri("c2"),
                         bus.published)
        runner.assert_not_in("U4-topics-c1", consumer_events_uri("c1"),
                             bus.published)

        # Transient -> global only, no inbox
        bus.published.clear()
        await events_mod.publish_event(
            "test.transient", "u4", {"n": 4}, persistent=False,
            store=store, bus=bus)
        runner.assert_in("U4-transient-global", GLOBAL_URI, bus.published)
        runner.assert_true(
            "U4-transient-no-inbox",
            not any(u.startswith(RESOURCE_CONSUMER_EVENTS_PREFIX)
                    for u in bus.published),
            "transient event must not notify consumer inboxes")

        # Zero relevant consumers -> global fires, no inbox, no error
        bus.published.clear()
        await events_mod.publish_event(
            "test.norelevant", "u4", {"n": 5}, persistent=True,
            store=store, bus=bus, routing={"topics": ["nope"]})
        runner.assert_in("U4-norelevant-global", GLOBAL_URI, bus.published)
        runner.assert_true(
            "U4-norelevant-no-inbox",
            not any(u.startswith(RESOURCE_CONSUMER_EVENTS_PREFIX)
                    for u in bus.published),
            "zero relevant consumers must not notify any inbox")
    finally:
        tmp.cleanup()


async def u5_persistence_failure_no_notify(runner: R) -> None:
    """U5: a persistence failure fails the publication and notifies nothing."""
    from core import events as events_mod

    class _FailingStore:
        def save(self, *args, **kwargs):
            raise RuntimeError("disk full")

    bus = _StubBus()
    try:
        await events_mod.publish_event(
            "test.fail", "u5", {"n": 1}, persistent=True,
            store=_FailingStore(), bus=bus)
        runner.fail("U5-raised", "publish_event must raise on persistence failure")
    except RuntimeError:
        runner.ok("U5-raised")
    runner.assert_eq("U5-no-notify", bus.published, [])


async def u6_alert_targeted_notify(runner: R) -> None:
    """U6: alert.triggered (targets routing) wakes only the owner's inbox."""
    from core import events as events_mod
    from core.alerts import AlertEvaluator
    store, tmp = _mk_store()
    try:
        store.register_consumer("c1")
        store.register_consumer("c2")
        store.create_alert(
            alert_id=uuid.uuid4().hex, consumer_id="c1", name=None,
            source="test", event_type=None, field_path="price",
            operator="gt", value=100.0, one_shot=False,
        )
        bus = _StubBus()
        evaluator = AlertEvaluator(store=store, subscription_bus=bus)
        events_mod.configure_alert_evaluator(evaluator.evaluate)
        try:
            # The triggering event is itself targeted to c1, so the ONLY
            # c2-inbox wake-up that could occur would come from the
            # alert.triggered event — which must stay owner-routed.
            await events_mod.publish_event(
                "test.price", "test", {"price": 150.0},
                persistent=True, store=store, bus=bus,
                routing={"targets": ["c1"]})
        finally:
            events_mod.configure_alert_evaluator(None)

        runner.assert_in("U6-c1", consumer_events_uri("c1"), bus.published)
        runner.assert_not_in("U6-c2", consumer_events_uri("c2"), bus.published)
        runner.assert_in("U6-global", GLOBAL_URI, bus.published)
    finally:
        tmp.cleanup()


# ===================================================================
# Live tests (real server + real Streamable HTTP)
# ===================================================================

async def _collect_updates(uris: list[str], total: float) -> list[ResourceUpdated]:
    """Connect a real modern MCP client, subscribe to URIs, collect updates.

    Bounded: the listen window is ``total`` seconds; every read is wrapped in
    asyncio.wait_for so a stalled transport cannot hang the test.
    """
    updates: list[ResourceUpdated] = []
    async with Client(get_server_url()) as client:
        async with client.listen(resource_subscriptions=uris) as sub:
            end = time.monotonic() + total
            while time.monotonic() < end:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(sub.__anext__(), timeout=remaining)
                except (StopAsyncIteration, asyncio.TimeoutError):
                    break
                if isinstance(event, ResourceUpdated):
                    updates.append(event)
    return updates


def _count_for(updates: list[ResourceUpdated], uri: str) -> int:
    return len([u for u in updates if u.uri == uri])


async def l1_template_discovery(runner: R) -> None:
    """L1: the inbox template is discoverable via list_resource_templates."""
    async with Client(get_server_url()) as client:
        result = await client.list_resource_templates()
    uris = [t.uri_template for t in result.resource_templates]
    runner.assert_in("L1-template", INBOX_TEMPLATE, uris)


async def l2_resource_read(runner: R) -> None:
    """L2: reading a consumer inbox returns the compact status shape."""
    await call("consumer_register", {"consumer_id": "l2-consumer"})
    data = await read_res(consumer_events_uri("l2-consumer"))
    runner.assert_true("L2-dict", isinstance(data, dict), "inbox read must be a dict")
    runner.assert_eq("L2-shape", set(data.keys()),
                     {"consumer_id", "checkpoint", "pending_count",
                      "latest_sequence"})
    runner.assert_eq("L2-consumer-id", data.get("consumer_id"), "l2-consumer")
    runner.assert_eq("L2-checkpoint", data.get("checkpoint"), 0)
    runner.assert_eq("L2-pending", data.get("pending_count"), 0)


async def l3_resource_read_unknown(runner: R) -> None:
    """L3: reading an unknown consumer's inbox errors (no fabricated inbox)."""
    try:
        await read_res(consumer_events_uri("ghost-consumer"))
        runner.fail("L3-error", "unknown consumer inbox read must raise")
    except Exception:
        runner.ok("L3-error")


async def l12_legacy_fallback(runner: R) -> None:
    """L12: legacy resources/subscribe is not served (modern listen only)."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    url = get_server_url()
    async with streamable_http_client(url) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            try:
                await session.subscribe_resource(
                    consumer_events_uri("l12-consumer"))
                runner.fail("L12-error",
                            "legacy subscribe_resource must fail on modern server")
            except MCPError:
                runner.ok("L12-error")


async def l15_42_tool_snapshot(runner: R) -> None:
    """L15: the public tool surface is exactly the frozen 42 tools."""
    async with Client(get_server_url()) as client:
        result = await client.list_tools()
    names = {t.name for t in result.tools}
    runner.assert_eq("L15-count", len(names), 42)
    runner.assert_eq("L15-set", names, EXPECTED_TOOLS)


async def _live_session_broadcast(runner: R) -> None:
    """L4/L8/L9/L10/L13: broadcast persistent source wakes all inboxes."""
    proc = await start_server({
        "sources": {
            "test_source": {"type": "test_source", "enabled": True,
                            "interval_seconds": 1.0, "max_events": 100,
                            "initial_delay_seconds": 6, "persistent": True},
        },
    })
    try:
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l4-c1"})
        await call("consumer_register", {"consumer_id": "l4-c2"})
        c1_uri = consumer_events_uri("l4-c1")
        c2_uri = consumer_events_uri("l4-c2")
        updates = await _collect_updates([c1_uri, c2_uri, GLOBAL_URI], 8)

        # L4: broadcast wakes all registered consumers
        runner.assert_ge("L4-c1", _count_for(updates, c1_uri), 1)
        runner.assert_ge("L4-c2", _count_for(updates, c2_uri), 1)
        # L13: global latest still fires
        runner.assert_ge("L13-global", _count_for(updates, GLOBAL_URI), 1)
        # L10: multiple events -> multiple inbox notifications
        runner.assert_ge("L10-multiple", _count_for(updates, c1_uri), 2)

        # L8: live wake + durable replay (replay is the source of truth)
        pending = await call("consumer_event_pending_list",
                             {"consumer_id": "l4-c1", "limit": 50})
        runner.assert_true("L8-replay", len(pending.get("events", [])) >= 1,
                           "persistent event must be replayable after live wake")

        # L9: no auto-ack — checkpoint unchanged, pending_count still > 0
        status = await read_res(c1_uri)
        runner.assert_eq("L9-checkpoint", status.get("checkpoint"), 0)
        runner.assert_true("L9-pending", status.get("pending_count", 0) >= 1,
                           "live wake must not auto-acknowledge")
    finally:
        stop_server(proc)


async def _live_session_targeted(runner: R) -> None:
    """L5: targeted persistent source wakes only the listed consumer."""
    proc = await start_server({
        "sources": {
            "test_source": {"type": "test_source", "enabled": True,
                            "interval_seconds": 1.0, "max_events": 100,
                            "initial_delay_seconds": 6, "persistent": True,
                            "routing": {"targets": ["l5-c1"]}},
        },
    })
    try:
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l5-c1"})
        await call("consumer_register", {"consumer_id": "l5-c2"})
        c1_uri = consumer_events_uri("l5-c1")
        c2_uri = consumer_events_uri("l5-c2")
        updates = await _collect_updates([c1_uri, c2_uri], 8)
        runner.assert_ge("L5-c1", _count_for(updates, c1_uri), 1)
        runner.assert_eq("L5-c2", _count_for(updates, c2_uri), 0)
    finally:
        stop_server(proc)


async def _live_session_topic(runner: R) -> None:
    """L6: topic-routed persistent source wakes only intersecting consumers."""
    proc = await start_server({
        "sources": {
            "test_source": {"type": "test_source", "enabled": True,
                            "interval_seconds": 1.0, "max_events": 100,
                            "initial_delay_seconds": 6, "persistent": True,
                            "routing": {"topics": ["alpha"]}},
        },
    })
    try:
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l6-c1"})
        await call("consumer_register", {"consumer_id": "l6-c2"})
        await call("consumer_topic_add", {"consumer_id": "l6-c1", "topic": "alpha"})
        await call("consumer_topic_add", {"consumer_id": "l6-c2", "topic": "beta"})
        c1_uri = consumer_events_uri("l6-c1")
        c2_uri = consumer_events_uri("l6-c2")
        updates = await _collect_updates([c1_uri, c2_uri], 8)
        runner.assert_ge("L6-c1", _count_for(updates, c1_uri), 1)
        runner.assert_eq("L6-c2", _count_for(updates, c2_uri), 0)
    finally:
        stop_server(proc)


async def _live_session_transient(runner: R) -> None:
    """L7: transient source fires global latest but never a consumer inbox."""
    proc = await start_server({
        "sources": {
            "test_source": {"type": "test_source", "enabled": True,
                            "interval_seconds": 1.0, "max_events": 100,
                            "initial_delay_seconds": 6, "persistent": False},
        },
    })
    try:
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l7-c1"})
        c1_uri = consumer_events_uri("l7-c1")
        updates = await _collect_updates([c1_uri, GLOBAL_URI], 8)
        runner.assert_ge("L7-global", _count_for(updates, GLOBAL_URI), 1)
        runner.assert_eq("L7-no-inbox", _count_for(updates, c1_uri), 0)
    finally:
        stop_server(proc)


async def _live_session_multi_client(runner: R) -> None:
    """L11: two clients on the same consumer inbox both receive."""
    proc = await start_server({
        "sources": {
            "test_source": {"type": "test_source", "enabled": True,
                            "interval_seconds": 1.0, "max_events": 100,
                            "initial_delay_seconds": 6, "persistent": True},
        },
    })
    try:
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l11-c1"})
        c1_uri = consumer_events_uri("l11-c1")

        async def _collect_one() -> list[ResourceUpdated]:
            return await _collect_updates([c1_uri], 8)

        a, b = await asyncio.gather(_collect_one(), _collect_one())
        runner.assert_ge("L11-client-a", _count_for(a, c1_uri), 1)
        runner.assert_ge("L11-client-b", _count_for(b, c1_uri), 1)
    finally:
        stop_server(proc)


async def _live_session_reconnect(runner: R) -> None:
    """L14: after server restart, re-listen works and durable replay survives."""
    proc = await start_server({
        "sources": {
            "test_source": {"type": "test_source", "enabled": True,
                            "interval_seconds": 1.0, "max_events": 100,
                            "initial_delay_seconds": 6, "persistent": True},
        },
    })
    try:
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l14-c1"})
        c1_uri = consumer_events_uri("l14-c1")
        first = await _collect_updates([c1_uri], 8)
        runner.assert_ge("L14-first-wake", _count_for(first, c1_uri), 1)
    finally:
        stop_server(proc)

    # Restart with a fresh source instance (fresh dedup keys) on the SAME data dir.
    proc = await start_server({
        "sources": {
            "test_source_2": {"type": "test_source", "enabled": True,
                              "interval_seconds": 1.0, "max_events": 100,
                              "initial_delay_seconds": 6, "persistent": True},
        },
    })
    try:
        await wait_source_ready("test_source_2", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": "l14-c1"})
        c1_uri = consumer_events_uri("l14-c1")
        second = await _collect_updates([c1_uri], 8)
        runner.assert_ge("L14-relisten-wake", _count_for(second, c1_uri), 1)

        # Durable replay survives the restart (no persisted subscription state).
        pending = await call("consumer_event_pending_list",
                             {"consumer_id": "l14-c1", "limit": 50})
        runner.assert_true("L14-replay", len(pending.get("events", [])) >= 1,
                           "durable replay must survive server restart")
    finally:
        stop_server(proc)


# ===================================================================
# Main
# ===================================================================

async def main() -> bool:
    runner = R()
    try:
        print("  Live MCP Consumer-Notification Tests (MCP-2B.4B)")
        print("=" * 50)

        # ── Unit-level (no server) ────────────────────────────────────────
        u1_contract_uri_builder(runner)
        u2_store_inbox_status(runner)
        u3_store_list_relevant_consumers(runner)
        await u4_publish_notify_routing(runner)
        await u5_persistence_failure_no_notify(runner)
        await u6_alert_targeted_notify(runner)

        # ── Live session 1: no source (discovery / reads / legacy / tools) ─
        proc = await start_server({})
        try:
            await l1_template_discovery(runner)
            await l2_resource_read(runner)
            await l3_resource_read_unknown(runner)
            await l12_legacy_fallback(runner)
            await l15_42_tool_snapshot(runner)
        finally:
            stop_server(proc)

        # ── Live sessions 2-7: source-driven routing / lifecycle ──────────
        await _live_session_broadcast(runner)
        await _live_session_targeted(runner)
        await _live_session_topic(runner)
        await _live_session_transient(runner)
        await _live_session_multi_client(runner)
        await _live_session_reconnect(runner)

    finally:
        restore_environment()
    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    asyncio.run(main())