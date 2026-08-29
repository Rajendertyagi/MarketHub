#!/usr/bin/env python3
"""
MCP-2B.4C — Live + Offline Fallback End-to-End Acceptance.

Proves the final live-alert architecture end-to-end over real Streamable HTTP:

    persist first
        -> live MCP inbox wake-up
        -> durable replay fetch
        -> acknowledge/checkpoint

and proves the fallback remains correct when the live notification is missed
because of client disconnect, listener drop, reconnect, re-subscribe, or
server restart.

TESTING MODEL (accepted split-proof, per MCP-2B.4C continuation):

  GENERIC alerts  -> REAL MCP E2E live wake through the real subprocess
                     MarketHub server (test_source -> server AlertEvaluator ->
                     server InMemorySubscriptionBus -> real subscriptions/listen
                     -> external modern MCP client -> replay -> ack).

  MARKET alerts   -> SPLIT PROOF:
                     PART A (REAL MCP DURABLE PATH): create market alert via
                       real MCP, trigger with in-process production AlertEngine
                       against the shared SQLite DB, replay/ack/checkpoint via
                       real MCP.
                     PART B (PRODUCTION IN-PROCESS LIVE-WAKE PATH): construct
                       the real production AlertEngine + EventStore + event
                       publication path, inject a recording subscription bus,
                       evaluate a canonical Quote, and prove the correct
                       consumer inbox ResourceUpdated is published (persist
                       before notify, broadcast semantics, notification-failure
                       durability).

The final live broker -> MarketService -> server AlertEngine ->
subscriptions/listen network path is INTENTIONALLY NOT EXERCISED (no broker
credentials, no test-only quote-injection surface).

Evidence is classified in the final report as one of:
  REAL MCP E2E / REAL MCP DURABLE PATH / PRODUCTION IN-PROCESS / SDK-UNIT /
  INTENTIONALLY UNTESTED OFFLINE.

NO FIXED SLEEPS: every wait is a bounded async wait (asyncio.wait_for /
deadline loops).

Run:
    python test/test_mcp_live_alert_acceptance.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timezone

# Ensure project root and test dir are importable.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers import lifecycle
from helpers.lifecycle import (
    get_server_url,
    restart_server,
    restore_environment,
    start_server,
    stop_server,
)
from helpers.mcp_client import call, call_session, wait_source_ready
from helpers.runner import R
from mcp import ClientSession
from mcp.client.client import Client
from mcp.client.streamable_http import (
    streamable_http_client as streamablehttp_client,
)
from mcp.client.subscriptions import ResourceUpdated
from mcp.shared.exceptions import MCPError
from mcp_result import safe_teardown

from app.alerts import AlertEngine
from core.persistence.store import EventStore
from market.models import Quote
from mcp_server.contract import (
    RESOURCE_CONSUMER_EVENTS_PREFIX,
    RESOURCE_EVENT_LATEST,
    consumer_events_uri,
)

GLOBAL_URI = RESOURCE_EVENT_LATEST
INBOX_TEMPLATE = "mcp-event://consumers/{consumer_id}/events"

# The frozen 42-tool public surface (MCP-2B.4C must not change it).
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
# Shared helpers
# ===================================================================

def _uid(suffix: str = "") -> str:
    return f"4c-{suffix}-{int(time.time() * 1000)}"


def _db_path() -> str:
    """Absolute path to the current server's SQLite DB (same file the server uses)."""
    data_dir = getattr(lifecycle, "_server_data_dir", "") or "data_test"
    return os.path.join(_PROJECT_DIR, data_dir, "events.db")


def _seed_catalog() -> None:
    """Seed the canonical instruments catalog with RELIANCE (deterministic)."""
    store = EventStore(_db_path())
    store.replace_provider_instruments("upstox", [{
        "instrument_token": "INE002A01018",
        "exchange": "NSE",
        "tradingsymbol": "RELIANCE",
        "name": "Reliance Industries Ltd",
        "instrument_type": "EQUITY",
        "segment": "EQ",
        "isin": "INE002A01018",
        "underlying": "RELIANCE",
    }])


def _mk_quote(ltp: float, token: str = "INE002A01018",
              exchange: str = "NSE", symbol: str = "RELIANCE") -> Quote:
    """Canonical deterministic quote for market-alert evaluation."""
    return Quote(instrument_token=token, exchange=exchange,
                 tradingsymbol=symbol,
                 received_ts=datetime.now(timezone.utc), ltp=ltp)


def _test_source_cfg(max_events: int = 300, interval: float = 1.0,
                     initial_delay: float = 6.0,
                     routing: dict | None = None,
                     persistent: bool = True,
                     name: str = "test_source") -> dict:
    """Config overrides enabling the deterministic in-server test_source."""
    cfg = {
        "sources": {
            name: {
                "type": "test_source",
                "enabled": True,
                "interval_seconds": interval,
                "event_type": "test.source.tick",
                "max_events": max_events,
                "persistent": persistent,
                "initial_delay_seconds": initial_delay,
            }
        }
    }
    if routing is not None:
        cfg["sources"][name]["routing"] = routing
    return cfg


async def _pending(consumer_id: str) -> dict:
    """Replay pending events for a consumer through the real MCP boundary."""
    resp = await call("consumer_event_pending_list", {"consumer_id": consumer_id})
    if resp.get("is_error"):
        return {"events": []}
    return resp


def _alert_triggered(events: list) -> list:
    return [e for e in events
            if isinstance(e, dict) and e.get("type") == "alert.triggered"]


async def _wait_alert_count(consumer_id: str, min_count: int,
                            timeout: float = 25.0) -> int:
    """Bounded poll until at least min_count alert.triggered events are replayable."""
    deadline = time.monotonic() + timeout
    last = 0
    while time.monotonic() < deadline:
        try:
            pending = await _pending(consumer_id)
            last = len(_alert_triggered(pending.get("events", [])))
            if last >= min_count:
                return last
        except Exception:
            pass
        await asyncio.sleep(0.25)
    return last


async def _ack(consumer_id: str, event_id: str) -> dict:
    return await call("consumer_event_acknowledge",
                      {"consumer_id": consumer_id, "event_id": event_id})


async def _ack_all(consumer_id: str) -> int:
    """Acknowledge all pending alert.triggered events for a consumer.
    Returns the number of events acked."""
    pending = await _pending(consumer_id)
    evts = _alert_triggered(pending.get("events", []))
    count = 0
    for e in evts:
        await _ack(consumer_id, e["id"])
        count += 1
    return count


async def _checkpoint(consumer_id: str) -> dict:
    return await call("consumer_checkpoint_get", {"consumer_id": consumer_id})


async def _create_generic_alert(consumer_id: str, *, one_shot: bool = False,
                                source: str = "test_source") -> dict:
    return await call("alert_create", {
        "consumer_id": consumer_id,
        "source": source,
        "field_path": "tick",
        "operator": "gte",
        "value": 1,
        "one_shot": one_shot,
    })


async def _collect_updates(uris: list[str], total: float) -> list[ResourceUpdated]:
    """Connect a real modern MCP client, subscribe to URIs, collect updates.

    Bounded: the listen window is ``total`` seconds; every read is wrapped in
    asyncio.wait_for so a stalled transport cannot hang the test. Closing the
    client context = disconnect.
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


async def _market_trigger(token: str, exchange: str, symbol: str, ltp: float,
                          bus=None) -> int:
    """In-process PRODUCTION AlertEngine against the shared SQLite DB.

    ``bus`` is the injected subscription bus (None for the real-MCP durable
    path; a recording bus for the production in-process live-wake path).
    """
    store = EventStore(_db_path())
    engine = AlertEngine(store, bus=bus)
    fired = await engine.evaluate(_mk_quote(ltp=ltp, token=token,
                                            exchange=exchange, symbol=symbol))
    return len(fired)


# ===================================================================
# Market split-proof helpers (PRODUCTION IN-PROCESS)
# ===================================================================

class _RecordingBus:
    """Recording subscription bus compatible with the production bus contract.

    Observes ResourceUpdated publications from the real production path
    (publish_event -> _notify_relevant_consumer_inboxes -> bus.publish).
    Optionally verifies persist-before-notify by querying the store at the
    moment each notification is published.
    """

    def __init__(self, store: EventStore | None = None) -> None:
        self.published: list[str] = []
        self.pending_at_publish: list[int] = []
        self._store = store

    async def publish(self, msg) -> None:
        uri = getattr(msg, "uri", None)
        self.published.append(uri)
        # persist-before-notify check applies only to consumer inbox URIs
        # (the global latest resource is not consumer-scoped).
        if self._store is not None and isinstance(uri, str) \
                and uri.startswith(RESOURCE_CONSUMER_EVENTS_PREFIX):
            try:
                # persist-before-notify: the durable event must already exist
                # in the store when the live notification is published.
                cid = uri[len(RESOURCE_CONSUMER_EVENTS_PREFIX):]
                cid = cid[: cid.rfind("/events")]
                status = self._store.get_consumer_inbox_status(cid)
                self.pending_at_publish.append(status["pending_count"])
            except Exception:
                self.pending_at_publish.append(-1)


class _FailingBus:
    """Subscription bus whose publish always fails (notification-failure proof)."""

    async def publish(self, msg) -> None:
        raise RuntimeError("bus down")


# ===================================================================
# Scenario A — connected generic alert live delivery (REAL MCP E2E)
# ===================================================================

async def scenario_a(runner: R) -> None:
    name = "A-connected-generic-live"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("a")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        # Connected modern client listens on the consumer inbox.
        updates = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-wake", _count_for(updates, c1_uri), 1)

        # Durable replay: matching alert.triggered exists.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replay", len(evts), 1)
        ev = evts[0]
        runner.assert_eq(name + "-family", ev["data"].get("alert_family"), "generic")

        # Acknowledge ALL events -> pending empty, checkpoint advanced.
        await _ack_all(cid)
        pending2 = await _pending(cid)
        runner.assert_eq(name + "-pending-empty",
                         len(_alert_triggered(pending2.get("events", []))), 0)
        cp = await _checkpoint(cid)
        runner.assert_ge(name + "-cp-advanced", cp.get("checkpoint", 0), 1)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario B — connected market alert live delivery (SPLIT PROOF)
# ===================================================================

async def scenario_b(runner: R) -> None:
    name = "B-market-split-proof"
    proc = None
    try:
        # ── PART A: REAL MCP DURABLE PATH ────────────────────────────────
        proc = await start_server()
        cid = _uid("b")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        runner.assert_eq(name + "-created", created.get("status"), "created")
        alert_id = created["alert"]["id"]
        exchange, token = _market_identity(created)
        runner.assert_true(name + "-resolved", bool(token),
                           "instrument not resolved")

        # In-process production AlertEngine (bus=None) against shared DB.
        fired = await _market_trigger(token=token, exchange=exchange,
                                      symbol="RELIANCE", ltp=150.0)
        runner.assert_ge(name + "-fired", fired, 1)

        # Replay through real MCP.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        ev = evts[0]
        runner.assert_eq(name + "-family", ev["data"].get("alert_family"), "market")
        runner.assert_eq(name + "-alert-id", ev["data"].get("alert_id"), alert_id)

        # Acknowledge + checkpoint through real MCP.
        ack = await _ack(cid, ev["id"])
        runner.assert_ge(name + "-cp-advanced", ack.get("checkpoint", 0),
                         ev["sequence"])
        pending2 = await _pending(cid)
        runner.assert_eq(name + "-pending-empty",
                         len(_alert_triggered(pending2.get("events", []))), 0)

        # ── PART B: PRODUCTION IN-PROCESS LIVE-WAKE PATH ─────────────────
        # Fresh isolated store so Part B is independent of the server DB.
        tmp = tempfile.TemporaryDirectory()
        try:
            store = EventStore(os.path.join(tmp.name, "t.db"))
            store.register_consumer("A")
            store.register_consumer("B")
            store.create_market_alert(exchange="NSE", instrument_token="T1",
                                      tradingsymbol="AAA", field="ltp",
                                      operator="gt", threshold=100.0)
            bus = _RecordingBus(store)
            engine = AlertEngine(store, bus=bus)
            fired = await engine.evaluate(_mk_quote(ltp=150.0, token="T1",
                                                    symbol="AAA"))
            runner.assert_ge(name + "-pb-fired", len(fired), 1)

            # Broadcast market alert -> both consumer inboxes + global latest.
            runner.assert_in(name + "-pb-wake-A", consumer_events_uri("A"),
                             bus.published)
            runner.assert_in(name + "-pb-wake-B", consumer_events_uri("B"),
                             bus.published)
            runner.assert_in(name + "-pb-global", GLOBAL_URI, bus.published)

            # Persist-before-notify: durable event already present at publish.
            runner.assert_true(
                name + "-pb-persist-before-notify",
                all(p >= 1 for p in bus.pending_at_publish),
                "live notification published before durable event persisted")

            # Exactly one durable alert.triggered row.
            durable = [e for e in store.list_pending(100)
                       if e["type"] == "alert.triggered"]
            runner.assert_eq(name + "-pb-one-durable", len(durable), 1)
        finally:
            tmp.cleanup()
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


def _market_identity(created: dict) -> tuple[str, str]:
    """Return (exchange, instrument_token) from a market_alert_create response."""
    inst = created.get("instrument") or {}
    key = inst.get("instrument_key") or ""
    exchange, _, token = key.partition(":")
    return exchange, token


# ===================================================================
# Scenario C — targeted generic isolation (REAL MCP E2E)
# ===================================================================

async def scenario_c(runner: R) -> None:
    name = "C-targeted-generic-isolation"
    proc = None
    try:
        cid_a = _uid("c-a")
        cid_b = _uid("c-b")
        # Use targeted source routing so only A receives source events.
        proc = await start_server(_test_source_cfg(routing={"targets": [cid_a]}))
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        await call("consumer_register", {"consumer_id": cid_a})
        await call("consumer_register", {"consumer_id": cid_b})
        await _create_generic_alert(cid_a)  # owned by A only
        a_uri = consumer_events_uri(cid_a)
        b_uri = consumer_events_uri(cid_b)

        updates = await _collect_updates([a_uri, b_uri], 8)
        runner.assert_ge(name + "-a-wake", _count_for(updates, a_uri), 1)
        runner.assert_eq(name + "-b-no-wake", _count_for(updates, b_uri), 0)

        # Replay: A has durable alert, B does not.
        pa = await _pending(cid_a)
        pb = await _pending(cid_b)
        ea = _alert_triggered(pa.get("events", []))
        eb = _alert_triggered(pb.get("events", []))
        runner.assert_ge(name + "-a-replay", len(ea), 1)
        runner.assert_eq(name + "-b-no-replay", len(eb), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario D — broadcast market alert (SPLIT PROOF)
# ===================================================================

async def scenario_d(runner: R) -> None:
    name = "D-broadcast-market"
    proc = None
    try:
        # ── PART A: REAL MCP DURABLE PATH (broadcast to both consumers) ──
        proc = await start_server()
        cid_a = _uid("d-a")
        cid_b = _uid("d-b")
        _seed_catalog()
        await call("consumer_register", {"consumer_id": cid_a})
        await call("consumer_register", {"consumer_id": cid_b})
        created = await call("market_alert_create", {
            "instrument_query": "RELIANCE", "operator": "gt",
            "threshold": 100.0, "field": "ltp"})
        exchange, token = _market_identity(created)
        await _market_trigger(token=token, exchange=exchange,
                              symbol="RELIANCE", ltp=150.0)

        pa = await _pending(cid_a)
        pb = await _pending(cid_b)
        ea = _alert_triggered(pa.get("events", []))
        eb = _alert_triggered(pb.get("events", []))
        runner.assert_ge(name + "-a-received", len(ea), 1)
        runner.assert_ge(name + "-b-received", len(eb), 1)
        e1 = ea[0]
        runner.assert_true(name + "-same-event",
                           any(e["id"] == e1["id"] for e in eb),
                           "broadcast event differs between consumers")

        # Ack A only -> A empty, B still pending.
        await _ack(cid_a, e1["id"])
        pa2 = await _pending(cid_a)
        pb2 = await _pending(cid_b)
        runner.assert_eq(name + "-a-cleared",
                         len(_alert_triggered(pa2.get("events", []))), 0)
        runner.assert_true(name + "-b-still-pending",
                           any(e["id"] == e1["id"]
                               for e in _alert_triggered(pb2.get("events", []))),
                           "B must still have the event pending")

        # ── PART B: PRODUCTION IN-PROCESS LIVE-WAKE (broadcast both) ─────
        tmp = tempfile.TemporaryDirectory()
        try:
            store = EventStore(os.path.join(tmp.name, "t.db"))
            store.register_consumer("A")
            store.register_consumer("B")
            store.create_market_alert(exchange="NSE", instrument_token="T1",
                                      tradingsymbol="AAA", field="ltp",
                                      operator="gt", threshold=100.0)
            bus = _RecordingBus(store)
            engine = AlertEngine(store, bus=bus)
            await engine.evaluate(_mk_quote(ltp=150.0, token="T1", symbol="AAA"))
            runner.assert_in(name + "-pb-wake-A", consumer_events_uri("A"),
                             bus.published)
            runner.assert_in(name + "-pb-wake-B", consumer_events_uri("B"),
                             bus.published)
            # Live routing matches durable relevance (both consumers).
            durable_a = [e for e in store.list_relevant_events("A")
                         if e["type"] == "alert.triggered"]
            durable_b = [e for e in store.list_relevant_events("B")
                         if e["type"] == "alert.triggered"]
            runner.assert_ge(name + "-pb-durable-A", len(durable_a), 1)
            runner.assert_ge(name + "-pb-durable-B", len(durable_b), 1)
        finally:
            tmp.cleanup()
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario E — client disconnected before alert (REAL MCP E2E)
# ===================================================================

async def scenario_e(runner: R) -> None:
    name = "E-offline-before-trigger"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("e")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        # NO live listener connected. Source fires while client is offline.

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired-offline", count, 1)

        # Reconnect + re-subscribe -> durable missed alert is replayable.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        await _ack(cid, evts[0]["id"])
        runner.assert_eq(name + "-acked",
                         len(_alert_triggered((await _pending(cid)).get("events", []))), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario F — disconnect during live period (REAL MCP E2E)
# ===================================================================

async def scenario_f(runner: R) -> None:
    name = "F-disconnect-during-live"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("f")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        # Connect, prove one wake works, then disconnect.
        first = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-first-wake", _count_for(first, c1_uri), 1)

        # Disconnected; the source keeps firing more alerts.
        count = await _wait_alert_count(cid, 2)
        runner.assert_ge(name + "-fired-while-offline", count, 2)

        # Reconnect + re-subscribe -> all durable events replayed (none lost).
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed-all", len(evts), 2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario G — wake received but event not acked (REAL MCP E2E)
# ===================================================================

async def scenario_g(runner: R) -> None:
    name = "G-wake-without-ack"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("g")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        # Receive wake + replay, but do NOT acknowledge.
        updates = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-wake", _count_for(updates, c1_uri), 1)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        e1 = evts[0]

        # Disconnect + reconnect + re-subscribe -> same event returns.
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_in(name + "-redelivered", e1["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario H — ack before disconnect (REAL MCP E2E)
# ===================================================================

async def scenario_h(runner: R) -> None:
    name = "H-ack-before-disconnect"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("h")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        updates = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-wake", _count_for(updates, c1_uri), 1)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
        e1 = evts[0]
        ack = await _ack(cid, e1["id"])
        cp1 = ack.get("checkpoint", 0)

        # Disconnect + reconnect -> acked event absent, checkpoint preserved.
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_not_in(name + "-acked-absent", e1["id"], ids2)
        cp2 = await _checkpoint(cid)
        runner.assert_eq(name + "-cp-preserved", cp2.get("checkpoint"), cp1)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario I — server restart with pending alert (REAL MCP E2E)
# ===================================================================

async def scenario_i(runner: R) -> None:
    name = "I-restart-with-pending"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("i")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-pre-restart", len(evts), 1)
        e1 = evts[0]
        e1_id, e1_seq, e1_payload = e1["id"], e1["sequence"], e1["data"]

        proc = await restart_server()

        # Reconnect + re-subscribe -> same event/seq/payload survives.
        pending2 = await _pending(cid)
        evts2 = _alert_triggered(pending2.get("events", []))
        runner.assert_true(name + "-present",
                           any(e["id"] == e1_id for e in evts2),
                           "pending event lost after restart")
        e1b = next(e for e in evts2 if e["id"] == e1_id)
        runner.assert_eq(name + "-same-seq", e1b["sequence"], e1_seq)
        runner.assert_eq(name + "-same-payload", e1b["data"], e1_payload)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario J — server restart after ack (REAL MCP E2E)
# ===================================================================

async def scenario_j(runner: R) -> None:
    name = "J-restart-after-ack"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("j")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        e1 = evts[0]
        # Ack ALL pending alert events so checkpoint advances past all of them.
        acked = await _ack_all(cid)
        runner.assert_ge(name + "-acked-all", acked, 1)
        cp1 = (await _checkpoint(cid)).get("checkpoint", 0)
        runner.assert_ge(name + "-cp1", cp1, 1)

        proc = await restart_server()

        cp2 = await _checkpoint(cid)
        runner.assert_eq(name + "-cp-persisted", cp2.get("checkpoint"), cp1)
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_not_in(name + "-acked-absent", e1["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario K — re-subscribe is required (REAL MCP E2E)
# ===================================================================

async def scenario_k(runner: R) -> None:
    name = "K-resubscribe-required"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("k")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        # First listen stream works while connected.
        first = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-first-wake", _count_for(first, c1_uri), 1)

        # Restart: prior listen stream is gone (no persisted subscription state).
        # The source keeps firing after restart (short initial delay so a tick
        # lands inside the collection window).
        proc = await restart_server(_test_source_cfg(initial_delay=2))

        # A NEW client/session must call listen() again to receive a later wake.
        second = await _collect_updates([c1_uri], 10)
        runner.assert_ge(name + "-relisten-wake", _count_for(second, c1_uri), 1)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario L — existing pending does not block new live wake (REAL MCP E2E)
# ===================================================================

async def scenario_l(runner: R) -> None:
    name = "L-pending-does-not-block-new-wake"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("l")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        # E1 pending (no ack), then disconnect.
        first = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-e1-wake", _count_for(first, c1_uri), 1)
        pending1 = await _pending(cid)
        e1 = _alert_triggered(pending1.get("events", []))[0]

        # E2 triggers while disconnected.
        count = await _wait_alert_count(cid, 2)
        runner.assert_ge(name + "-e2-fired", count, 2)

        # Reconnect + re-subscribe; E3 triggers while connected.
        updates = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-e3-wake", _count_for(updates, c1_uri), 1)

        # Replay returns E1, E2, E3 ordered by sequence.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-all-present", len(evts), 3)
        seqs = [e["sequence"] for e in evts]
        runner.assert_true(name + "-ordered",
                           all(b > a for a, b in zip(seqs, seqs[1:])),
                           "replay not ordered by sequence")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario M — multiple alert burst (REAL MCP E2E)
# ===================================================================

async def scenario_m(runner: R) -> None:
    name = "M-burst"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(max_events=300, interval=0.5))
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("m")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        # Collect wakes for a window; accept one or more (coalescing allowed).
        updates = await _collect_updates([c1_uri], 8)
        runner.assert_ge(name + "-wake", _count_for(updates, c1_uri), 1)

        # Replay must contain ALL durable events, unique IDs, seq ASC.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-all-durable", len(evts), 3)
        ids = [e["id"] for e in evts]
        runner.assert_eq(name + "-unique-ids", len(set(ids)), len(ids))
        seqs = [e["sequence"] for e in evts]
        runner.assert_true(name + "-seq-asc",
                           all(b > a for a, b in zip(seqs, seqs[1:])),
                           "sequences not strictly ascending")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario N — multiple clients same consumer (REAL MCP E2E)
# ===================================================================

async def scenario_n(runner: R) -> None:
    name = "N-multi-client-same-consumer"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("n")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        async def _collect_one() -> list[ResourceUpdated]:
            return await _collect_updates([c1_uri], 8)

        a, b = await asyncio.gather(_collect_one(), _collect_one())
        runner.assert_ge(name + "-client-a", _count_for(a, c1_uri), 1)
        runner.assert_ge(name + "-client-b", _count_for(b, c1_uri), 1)

        # One consumer queue, one persistent event. Ack through one client.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-durable", len(evts), 1)
        e1 = evts[0]
        await _ack(cid, e1["id"])
        pending2 = await _pending(cid)
        ids2 = {e["id"] for e in _alert_triggered(pending2.get("events", []))}
        runner.assert_not_in(name + "-gone-globally", e1["id"], ids2)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario O — multiple consumers independent (REAL MCP E2E)
# ===================================================================

async def scenario_o(runner: R) -> None:
    name = "O-multiple-consumers-independent"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid_a = _uid("o-a")
        cid_b = _uid("o-b")
        await call("consumer_register", {"consumer_id": cid_a})
        await call("consumer_register", {"consumer_id": cid_b})
        await _create_generic_alert(cid_a)
        await _create_generic_alert(cid_b)
        a_uri = consumer_events_uri(cid_a)
        b_uri = consumer_events_uri(cid_b)

        updates = await _collect_updates([a_uri, b_uri], 8)
        runner.assert_ge(name + "-a-wake", _count_for(updates, a_uri), 1)
        runner.assert_ge(name + "-b-wake", _count_for(updates, b_uri), 1)

        # Both replay their own durable events (owner-routed).
        pa = await _pending(cid_a)
        pb = await _pending(cid_b)
        ea = _alert_triggered(pa.get("events", []))
        eb = _alert_triggered(pb.get("events", []))
        runner.assert_ge(name + "-a-replay", len(ea), 1)
        runner.assert_ge(name + "-b-replay", len(eb), 1)

        # Ack ALL of A's events -> A cleared, B still pending.
        await _ack_all(cid_a)
        pa2 = await _pending(cid_a)
        pb2 = await _pending(cid_b)
        runner.assert_eq(name + "-a-cleared",
                         len(_alert_triggered(pa2.get("events", []))), 0)
        runner.assert_ge(name + "-b-still-pending",
                         len(_alert_triggered(pb2.get("events", []))), 1)

        # Disconnect/reconnect B -> B still replays its pending event.
        pb3 = await _pending(cid_b)
        runner.assert_ge(name + "-b-replays-after-reconnect",
                         len(_alert_triggered(pb3.get("events", []))), 1)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario P — topic routing end-to-end (REAL MCP E2E)
# ===================================================================

async def scenario_p(runner: R) -> None:
    name = "P-topic-routing"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(routing={"topics": ["foo"]}))
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid_a = _uid("p-a")
        cid_b = _uid("p-b")
        await call("consumer_register", {"consumer_id": cid_a})
        await call("consumer_register", {"consumer_id": cid_b})
        await call("consumer_topic_add", {"consumer_id": cid_a, "topic": "foo"})
        await call("consumer_topic_add", {"consumer_id": cid_b, "topic": "bar"})
        a_uri = consumer_events_uri(cid_a)
        b_uri = consumer_events_uri(cid_b)

        # The source publishes persistent events routed to topic foo.
        updates = await _collect_updates([a_uri, b_uri], 8)
        runner.assert_ge(name + "-a-wake", _count_for(updates, a_uri), 1)
        runner.assert_eq(name + "-b-no-wake", _count_for(updates, b_uri), 0)

        # Replay: A has the event, B does not.
        pa = await _pending(cid_a)
        pb = await _pending(cid_b)
        runner.assert_ge(name + "-a-replay", len(pa.get("events", [])), 1)
        runner.assert_eq(name + "-b-absent", len(pb.get("events", [])), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario Q — transient event (REAL MCP E2E)
# ===================================================================

async def scenario_q(runner: R) -> None:
    name = "Q-transient"
    proc = None
    try:
        proc = await start_server(_test_source_cfg(persistent=False))
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("q")
        await call("consumer_register", {"consumer_id": cid})
        c1_uri = consumer_events_uri(cid)

        # Transient events: global latest may fire, consumer inbox does NOT wake.
        updates = await _collect_updates([c1_uri, GLOBAL_URI], 8)
        runner.assert_eq(name + "-no-inbox-wake", _count_for(updates, c1_uri), 0)
        # pending-list unchanged (empty).
        pending = await _pending(cid)
        runner.assert_eq(name + "-pending-unchanged", len(pending.get("events", [])), 0)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario R — persistence failure (SDK-UNIT)
# ===================================================================

async def scenario_r(runner: R) -> None:
    name = "R-persistence-failure"
    from core import events as events_mod

    class _FailingStore:
        def save(self, *args, **kwargs):
            raise RuntimeError("disk full")

    class _Bus:
        def __init__(self):
            self.published = []
        async def publish(self, msg):
            self.published.append(getattr(msg, "uri", None))

    bus = _Bus()
    try:
        await events_mod.publish_event(
            "test.fail", "r", {"n": 1}, persistent=True,
            store=_FailingStore(), bus=bus)
        runner.fail(name + "-raised", "publish_event must raise on persistence failure")
    except RuntimeError:
        runner.ok(name + "-raised")
    runner.assert_eq(name + "-no-notify", bus.published, [])


# ===================================================================
# Scenario S — notification failure after persistence (SDK-UNIT)
# ===================================================================

async def scenario_s(runner: R) -> None:
    name = "S-notification-failure-durability"
    from core import events as events_mod
    tmp = tempfile.TemporaryDirectory()
    try:
        store = EventStore(os.path.join(tmp.name, "t.db"))
        store.register_consumer("A")
        bus = _FailingBus()
        # The failing bus must not roll back the already-persisted event.
        await events_mod.publish_event(
            "test.evt", "s", {"n": 1}, persistent=True,
            store=store, bus=bus)
        durable = [e for e in store.list_pending(100) if e["type"] == "test.evt"]
        runner.assert_eq(name + "-persisted", len(durable), 1)
        runner.assert_ge(name + "-replayable", durable[0]["sequence"], 1)
    finally:
        tmp.cleanup()


# ===================================================================
# Scenario T — legacy client fallback (REAL MCP E2E)
# ===================================================================

async def scenario_t(runner: R) -> None:
    name = "T-legacy-fallback"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("t")
        url = get_server_url()

        # Legacy ClientSession / 2025-11-25 path: no modern listen support.
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                reg = await call_session(session, "consumer_register",
                                         {"consumer_id": cid})
                runner.assert_eq(name + "-register", reg.get("status"), "registered")
                created = await call_session(session, "alert_create", {
                    "consumer_id": cid, "source": "test_source",
                    "field_path": "tick", "operator": "gte", "value": 1,
                    "one_shot": False})
                runner.assert_eq(name + "-alert-created", created.get("status"), "created")
                # Legacy subscribe_resource is not supported on the modern server.
                try:
                    await session.subscribe_resource(consumer_events_uri(cid))
                    runner.fail(name + "-legacy-subscribe",
                                "legacy subscribe_resource must fail on modern server")
                except MCPError:
                    runner.ok(name + "-legacy-subscribe")
        # Session closed = disconnected; source fires while legacy client offline.

        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired-offline", count, 1)

        # Replay after reconnect works (tools still work).
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        runner.assert_ge(name + "-replayed", len(evts), 1)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario U — modern capability discovery (REAL MCP E2E)
# ===================================================================

async def scenario_u(runner: R) -> None:
    name = "U-modern-capability-discovery"
    proc = None
    try:
        proc = await start_server()
        async with Client(get_server_url()) as client:
            # Negotiates the modern 2026-07-28 protocol.
            runner.assert_eq(name + "-protocol", client.protocol_version, "2026-07-28")
            # Subscription capability visible.
            caps = client.server_capabilities
            resources = getattr(caps, "resources", None)
            runner.assert_true(name + "-subscribe-capability",
                               bool(resources) and bool(getattr(resources, "subscribe", False)),
                               "server must advertise resource subscription capability")
            # Inbox resource template discoverable.
            result = await client.list_resource_templates()
            uris = [t.uri_template for t in result.resource_templates]
            runner.assert_in(name + "-template", INBOX_TEMPLATE, uris)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario V — resource status read (REAL MCP E2E)
# ===================================================================

async def scenario_v(runner: R) -> None:
    name = "V-resource-status-read"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("v")
        await call("consumer_register", {"consumer_id": cid})
        c1_uri = consumer_events_uri(cid)

        # Before pending: pending_count = 0.
        before = await _read_inbox(c1_uri)
        runner.assert_eq(name + "-before-pending", before.get("pending_count"), 0)
        runner.assert_eq(name + "-before-checkpoint", before.get("checkpoint"), 0)

        # After trigger: pending_count > 0.
        await _create_generic_alert(cid)
        count = await _wait_alert_count(cid, 1)
        runner.assert_ge(name + "-fired", count, 1)
        after = await _read_inbox(c1_uri)
        runner.assert_true(name + "-after-pending", after.get("pending_count", 0) >= 1,
                           "pending_count must increase after trigger")

        # After ack: pending_count decreases; checkpoint reflects durable state.
        pending = await _pending(cid)
        evts = _alert_triggered(pending.get("events", []))
        e1 = evts[0]
        # Ack ALL events, then settle in case new events fire during acking.
        await _ack_all(cid)
        deadline = time.monotonic() + 10.0
        after_ack = None
        while time.monotonic() < deadline:
            after_ack = await _read_inbox(c1_uri)
            if after_ack.get("pending_count", 0) == 0:
                break
            # New events may have fired — ack them too.
            await _ack_all(cid)
            await asyncio.sleep(0.3)
        runner.assert_eq(name + "-after-ack-pending", after_ack.get("pending_count"), 0)
        cp_after = after_ack.get("checkpoint", 0)
        runner.assert_ge(name + "-after-ack-checkpoint", cp_after, 1)

        # Resource read must not itself acknowledge or mutate state.
        after_read = await _read_inbox(c1_uri)
        runner.assert_eq(name + "-read-no-mutate", after_read.get("checkpoint", 0),
                         cp_after)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


async def _read_inbox(uri: str) -> dict:
    from helpers.mcp_client import read_res
    data = await read_res(uri)
    return data if isinstance(data, dict) else {}


# ===================================================================
# Scenario W — global resource still works (REAL MCP E2E)
# ===================================================================

async def scenario_w(runner: R) -> None:
    name = "W-global-resource"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)
        cid = _uid("w")
        await call("consumer_register", {"consumer_id": cid})
        await _create_generic_alert(cid)
        c1_uri = consumer_events_uri(cid)

        updates = await _collect_updates([c1_uri, GLOBAL_URI], 8)
        runner.assert_ge(name + "-inbox-wake", _count_for(updates, c1_uri), 1)
        runner.assert_ge(name + "-global-wake", _count_for(updates, GLOBAL_URI), 1)
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Scenario X — exact 42 tools (REAL MCP E2E)
# ===================================================================

async def scenario_x(runner: R) -> None:
    name = "X-42-tools"
    proc = None
    try:
        proc = await start_server(_test_source_cfg())
        await wait_source_ready("test_source", {"running", "completed"}, timeout=15)

        async def _snapshot(tag: str) -> None:
            async with Client(get_server_url()) as client:
                result = await client.list_tools()
            names = {t.name for t in result.tools}
            runner.assert_eq(name + "-" + tag + "-count", len(names), 42)
            runner.assert_eq(name + "-" + tag + "-set", names, EXPECTED_TOOLS)
            runner.assert_true(name + "-" + tag + "-no-dev",
                               not any(n.startswith("dev_") for n in names),
                               "zero dev_* tools")
            runner.assert_not_in(name + "-" + tag + "-no-event-publish",
                                 "event_publish", names)
            runner.assert_not_in(name + "-" + tag + "-no-consumer-event-list",
                                 "consumer_event_list", names)

        await _snapshot("before")

        # Reconnect (fresh session) -> still 42.
        await _snapshot("after-reconnect")

        # Restart -> still 42.
        proc = await restart_server()
        await _snapshot("after-restart")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ===================================================================
# Main
# ===================================================================

async def main() -> bool:
    runner = R()
    print("  MCP-2B.4C Live/Offline Alert Acceptance (scenarios A-X)")
    print("=" * 60)
    tests = [
        scenario_a, scenario_b, scenario_c, scenario_d, scenario_e,
        scenario_f, scenario_g, scenario_h, scenario_i, scenario_j,
        scenario_k, scenario_l, scenario_m, scenario_n, scenario_o,
        scenario_p, scenario_q, scenario_r, scenario_s, scenario_t,
        scenario_u, scenario_v, scenario_w, scenario_x,
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
