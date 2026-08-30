#!/usr/bin/env python3
"""B2 condition-alert live delivery tests (in-process PRODUCTION path).

B2 deliberately exposes NO public MCP ``condition_alert_*`` tools (the
42-tool surface is frozen), so live delivery is proven through the real
production in-process path: production ``ConditionAlertEngine`` + real
``EventStore`` + the canonical ``events.finalize_persisted_event`` pipeline.

  * LD1  live wake-up — a trigger publishes the consumer-inbox resource
        notification (persist-before-notify: the durable event already
        exists when the notification fires)
  * LD2  durable replay — the consumer's pending inbox contains the
        canonical market_condition alert.triggered event
  * LD3  acknowledge — ack removes the event from the pending inbox
  * LD4  SSE fan-out — the canonical payload reaches the SSE broker
  * LD5  no duplicate wake-up — a non-triggering tick publishes nothing
  * LD6  once-mode — after the first trigger the alert is disabled and
        later ticks publish nothing new

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

from core.persistence.store import EventStore
from market.models import Quote

RELIANCE = "NSE:EQUITY:INE002A01018"
RELIANCE_ISIN = "INE002A01018"


class _RecordingBus:
    """Recording subscription bus compatible with the production bus contract.

    Observes ResourceUpdated publications from the real production path
    (finalize_persisted_event -> _notify_relevant_consumer_inboxes ->
    bus.publish). Verifies persist-before-notify by querying the store at
    the moment each consumer-inbox notification is published.
    """

    def __init__(self, store: EventStore) -> None:
        self.published: list[str] = []
        self.pending_at_publish: list[int] = []
        self._store = store

    async def publish(self, msg) -> None:
        uri = getattr(msg, "uri", None)
        self.published.append(uri)
        if self._store is not None and isinstance(uri, str) \
                and uri.startswith("mcp-event://consumers/"):
            try:
                cid = uri[len("mcp-event://consumers/"):]
                cid = cid[: cid.rfind("/events")]
                status = self._store.get_consumer_inbox_status(cid)
                self.pending_at_publish.append(status["pending_count"])
            except Exception:
                self.pending_at_publish.append(-1)


class _FakeQuote:
    def __init__(self, ltp, token="2885", tsym="RELIANCE"):
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = tsym
        self.ltp = ltp
        self.provider = "upstox"


def _mk_store() -> tuple[EventStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    store.register_consumer("consumer-1")
    return store, tmp


def _mk_resolver(store):
    from app.market_identity import MarketInstrumentIdentityResolver
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "2885",
         "tradingsymbol": "RELIANCE", "name": "Reliance Industries",
         "instrument_type": "EQ", "segment": "NSE",
         "isin": RELIANCE_ISIN},
    ])
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    return resolver


def _mk_engine(store, bus=None):
    from app.condition_alerts import ConditionAlertEngine
    return ConditionAlertEngine(store, resolver=_mk_resolver(store), bus=bus)


def _create(store, trigger_mode="repeat"):
    return store.create_condition_alert(
        consumer_id="consumer-1", name="test", trigger_mode=trigger_mode,
        condition_json={"condition_version": 1, "condition_id": "cond-1",
                        "metric": "ltp", "operator": "gt", "value": 100,
                        "instrument": {"canonical_id": RELIANCE}})


def _triggered(events):
    return [e for e in events if e.get("type") == "alert.triggered"]


async def test_ld1_live_wakeup_persist_before_notify(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store)
        bus = _RecordingBus(store)
        engine = _mk_engine(store, bus=bus)
        async def run():
            await engine.evaluate(_FakeQuote(50))    # baseline, no notify
            await engine.evaluate(_FakeQuote(101))   # FIRE
            return None
        await run()
        # The consumer-inbox resource was notified exactly once.
        inbox_uris = [u for u in bus.published
                      if isinstance(u, str) and u.startswith("mcp-event://consumers/")]
        runner.assert_eq("LD1-inbox-notify-count", len(inbox_uris), 1)
        # persist-before-notify: the durable event already existed when the
        # live notification was published.
        runner.assert_true("LD1-persist-before-notify",
                           len(bus.pending_at_publish) >= 1
                           and bus.pending_at_publish[0] >= 1,
                           f"pending_at_publish={bus.pending_at_publish}")
    finally:
        tmp.cleanup()


async def test_ld2_durable_replay(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store)
        engine = _mk_engine(store)
        async def run():
            await engine.evaluate(_FakeQuote(101))
            return None
        await run()
        pending = store.list_relevant_events("consumer-1", None, 10)
        evts = _triggered(pending)
        runner.assert_eq("LD2-count", len(evts), 1)
        data = evts[0]["data"]
        runner.assert_eq("LD2-family", data["alert_family"], "market_condition")
        runner.assert_eq("LD2-consumer", data["consumer_id"], "consumer-1")
        runner.assert_eq("LD2-canonical", data["instrument"]["canonical_id"],
                         RELIANCE)
        runner.assert_eq("LD2-routing", evts[0]["routing"],
                         {"targets": ["consumer-1"]})
    finally:
        tmp.cleanup()


async def test_ld3_acknowledge(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store)
        engine = _mk_engine(store)
        async def run():
            await engine.evaluate(_FakeQuote(101))
            return None
        await run()
        pending = store.list_relevant_events("consumer-1", None, 10)
        evt = _triggered(pending)[0]
        ok = store.acknowledge_event("consumer-1", evt["id"])
        runner.assert_true("LD3-acked", ok)
        pending2 = store.list_relevant_events("consumer-1", None, 10)
        runner.assert_eq("LD3-pending-empty", len(_triggered(pending2)), 0)
        status = store.get_consumer_inbox_status("consumer-1")
        runner.assert_eq("LD3-inbox-zero", status["pending_count"], 0)
    finally:
        tmp.cleanup()


async def test_ld4_sse_fanout(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store)
        from core import events as core_events
        from core.sse_broker import EventBroker
        broker = EventBroker(queue_size=10)
        core_events.configure_sse_broker(broker)
        engine = _mk_engine(store)
        try:
            ctx = broker.subscribe()
            gen = await ctx.__aenter__()
            async def run():
                await engine.evaluate(_FakeQuote(101))
                return None
            await run()
            line = await asyncio.wait_for(gen.__anext__(), timeout=2)
            await ctx.__aexit__(None, None, None)
        finally:
            core_events.configure_sse_broker(None)
        payload = json.loads(line)
        runner.assert_eq("LD4-type", payload["type"], "alert.triggered")
        runner.assert_eq("LD4-source", payload["source"], "alert_engine")
        runner.assert_eq("LD4-family", payload["data"]["alert_family"],
                         "market_condition")
        runner.assert_eq("LD4-version", payload["data"]["version"], 1)
    finally:
        tmp.cleanup()


async def test_ld5_no_duplicate_wakeup(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store)
        bus = _RecordingBus(store)
        engine = _mk_engine(store, bus=bus)
        async def run():
            await engine.evaluate(_FakeQuote(101))   # FIRE
            await engine.evaluate(_FakeQuote(102))   # TRUE->TRUE no fire
            await engine.evaluate(_FakeQuote(103))   # TRUE->TRUE no fire
            return None
        await run()
        inbox_uris = [u for u in bus.published
                      if isinstance(u, str) and u.startswith("mcp-event://consumers/")]
        runner.assert_eq("LD5-inbox-notify-count", len(inbox_uris), 1)
        runner.assert_eq("LD5-events",
                         len(_triggered(store.list_relevant_events(
                             "consumer-1", None, 10))), 1)
    finally:
        tmp.cleanup()


async def test_ld6_once_disables(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, trigger_mode="once")
        bus = _RecordingBus(store)
        engine = _mk_engine(store, bus=bus)
        async def run():
            await engine.evaluate(_FakeQuote(50))    # baseline
            await engine.evaluate(_FakeQuote(101))   # FIRE (once)
            await engine.evaluate(_FakeQuote(50))    # re-arm attempt
            await engine.evaluate(_FakeQuote(101))   # disabled -> no fire
            return None
        await run()
        inbox_uris = [u for u in bus.published
                      if isinstance(u, str) and u.startswith("mcp-event://consumers/")]
        runner.assert_eq("LD6-inbox-notify-count", len(inbox_uris), 1)
        runner.assert_eq("LD6-events",
                         len(_triggered(store.list_relevant_events(
                             "consumer-1", None, 10))), 1)
        a = store.list_condition_alerts()[0]
        runner.assert_eq("LD6-disabled", a["enabled"], False)
    finally:
        tmp.cleanup()


async def main() -> bool:
    runner = R()
    await test_ld1_live_wakeup_persist_before_notify(runner)
    await test_ld2_durable_replay(runner)
    await test_ld3_acknowledge(runner)
    await test_ld4_sse_fanout(runner)
    await test_ld5_no_duplicate_wakeup(runner)
    await test_ld6_once_disables(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)