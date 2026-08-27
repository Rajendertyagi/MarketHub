#!/usr/bin/env python3
"""Canonical alert.triggered delivery tests (MCP-2B.3B).

Covers the unified canonical alert.triggered payload shape and the durable
market-alert event path:

  * AT1  canonical payload shape (unit) — generic + market
  * AT2  market trigger -> exactly one durable alert.triggered event
  * AT3  market event is replayable by a registered consumer (broadcast)
  * AT4  broadcast semantics — two consumers both receive the event
  * AT5  generic owner-routing regression — owner gets it, non-owner does not
  * AT6  market trigger repeat semantics — no duplicate events until re-arm
  * AT7  re-arm emits exactly one NEW durable event
  * AT8  crossing operators emit exactly one durable canonical event
  * AT9  generic one_shot semantics unchanged (auto-disable after one fire)
  * AT10 generic repeating (one_shot=False) unchanged
  * AT11 event journal — durable recent_events contains the canonical event
  * AT12 SSE role proof — /events/stream fan-out carries the canonical payload

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402


def _mk_quote(ltp, token="T1", exchange="NSE", symbol="AAA"):
    from market.models import Quote
    return Quote(instrument_token=token, exchange=exchange,
                 tradingsymbol=symbol,
                 received_ts=datetime.now(timezone.utc), ltp=ltp)


def _mk_store():
    tmp = tempfile.TemporaryDirectory()
    from core.persistence.store import EventStore
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


def _trigger_events(store):
    return [e for e in store.list_pending(100)
            if e["type"] == "alert.triggered"]


# -- AT1: canonical payload shape (unit) -----------------------------------------


def test_at1_canonical_shape(runner: R) -> None:
    """The shared builder emits the frozen canonical shape for both families."""
    from core.alert_events import ALERT_ENGINE_SOURCE, build_alert_triggered_data

    data = build_alert_triggered_data(
        alert_family="market",
        alert_id=7,
        consumer_id=None,
        condition={"field": "ltp", "operator": "gt", "threshold": 100.0},
        observed={"value": 150.0},
        instrument={"exchange": "NSE", "instrument_token": "T1",
                    "tradingsymbol": "AAA"},
        one_shot=False,
        metadata={},
    )
    runner.assert_eq("AT1-version", data["version"], 1)
    runner.assert_eq("AT1-family", data["alert_family"], "market")
    runner.assert_eq("AT1-alert_id", data["alert_id"], 7)
    runner.assert_eq("AT1-consumer_id", data["consumer_id"], None)
    runner.assert_eq("AT1-source", data["source"], ALERT_ENGINE_SOURCE)
    runner.assert_eq("AT1-condition", data["condition"],
                     {"field": "ltp", "operator": "gt", "threshold": 100.0})
    runner.assert_eq("AT1-observed", data["observed"], {"value": 150.0})
    runner.assert_eq("AT1-instrument", data["instrument"],
                     {"exchange": "NSE", "instrument_token": "T1",
                      "tradingsymbol": "AAA"})
    runner.assert_eq("AT1-one_shot", data["one_shot"], False)
    runner.assert_eq("AT1-metadata", data["metadata"], {})
    # triggered_at must be ISO-8601 UTC (timezone-aware).
    ts = datetime.fromisoformat(data["triggered_at"])
    runner.assert_true("AT1-triggered_at-tz",
                       ts.tzinfo is not None and ts.utcoffset() is not None,
                       "triggered_at not timezone-aware ISO-8601")
    for k in ("version", "alert_family", "alert_id", "triggered_at",
              "condition", "observed"):
        runner.assert_true("AT1-required-" + k, k in data, f"missing {k}")

    gdata = build_alert_triggered_data(
        alert_family="generic",
        alert_id="abc-123",
        consumer_id="consumer-A",
        condition={"field": "price", "operator": "gte", "threshold": 10},
        observed={"value": 12, "matched_event_id": "evt-1",
                  "matched_event_type": "test.tick",
                  "matched_source": "test"},
        instrument=None,
        one_shot=True,
        metadata={"name": "my alert"},
    )
    runner.assert_eq("AT1g-family", gdata["alert_family"], "generic")
    runner.assert_eq("AT1g-consumer", gdata["consumer_id"], "consumer-A")
    runner.assert_eq("AT1g-instrument", gdata["instrument"], None)
    runner.assert_eq("AT1g-one_shot", gdata["one_shot"], True)
    runner.assert_eq("AT1g-metadata", gdata["metadata"], {"name": "my alert"})
    runner.assert_eq("AT1g-observed", gdata["observed"],
                     {"value": 12, "matched_event_id": "evt-1",
                      "matched_event_type": "test.tick",
                      "matched_source": "test"})


# -- AT2/AT3/AT4: durable market events -------------------------------------------


async def test_at2_market_durable_event(runner: R) -> None:
    """One market trigger -> exactly one durable canonical event."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="gt", threshold=100.0)
    engine = AlertEngine(store)
    fired = await engine.evaluate(_mk_quote(ltp=150.0))
    runner.assert_eq("AT2-fired", len(fired), 1)

    events = _trigger_events(store)
    runner.assert_eq("AT2-exactly-one", len(events), 1)
    evt = events[0]
    runner.assert_eq("AT2-source", evt["source"], "alert_engine")
    runner.assert_true("AT2-persistent",
                       evt.get("sequence") is not None,
                       "market trigger event not persistent")
    runner.assert_eq("AT2-routing", evt.get("routing"), None)  # broadcast
    data = evt["data"]
    runner.assert_eq("AT2-family", data["alert_family"], "market")
    runner.assert_eq("AT2-alert_id", data["alert_id"], 1)
    runner.assert_eq("AT2-consumer_id", data["consumer_id"], None)
    runner.assert_eq("AT2-condition", data["condition"],
                     {"field": "ltp", "operator": "gt", "threshold": 100.0})
    runner.assert_eq("AT2-observed", data["observed"], {"value": 150.0})
    runner.assert_eq("AT2-instrument", data["instrument"],
                     {"exchange": "NSE", "instrument_token": "T1",
                      "tradingsymbol": "AAA"})
    runner.assert_eq("AT2-one_shot", data["one_shot"], False)
    runner.assert_eq("AT2-version", data["version"], 1)


async def test_at3_market_replay(runner: R) -> None:
    """A registered consumer can replay the durable market trigger event."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    store.register_consumer("consumer-A")
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="gt", threshold=100.0)
    engine = AlertEngine(store)
    await engine.evaluate(_mk_quote(ltp=150.0))

    relevant = store.list_relevant_events("consumer-A")
    runner.assert_eq("AT3-replay-count", len(relevant), 1)
    evt = relevant[0]
    runner.assert_eq("AT3-replay-type", evt["type"], "alert.triggered")
    runner.assert_eq("AT3-replay-family", evt["data"]["alert_family"], "market")
    runner.assert_eq("AT3-replay-source", evt["source"], "alert_engine")


async def test_at4_broadcast(runner: R) -> None:
    """routing=None broadcasts the market trigger to ALL registered consumers."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    store.register_consumer("consumer-A")
    store.register_consumer("consumer-B")
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="gt", threshold=100.0)
    engine = AlertEngine(store)
    await engine.evaluate(_mk_quote(ltp=150.0))

    a = store.list_relevant_events("consumer-A")
    b = store.list_relevant_events("consumer-B")
    runner.assert_eq("AT4-a-count", len(a), 1)
    runner.assert_eq("AT4-b-count", len(b), 1)
    runner.assert_eq("AT4-same-event", a[0]["id"], b[0]["id"])
    runner.assert_eq("AT4-type", a[0]["type"], "alert.triggered")


# -- AT5: generic owner-routing regression ----------------------------------------


async def test_at5_generic_owner_routing(runner: R) -> None:
    """Generic triggers stay owner-routed: A gets it, B does not."""
    from core import events as core_events
    from core.alerts import AlertEvaluator

    store, tmp = _mk_store()
    store.register_consumer("consumer-A")
    store.register_consumer("consumer-B")
    store.create_alert(
        alert_id=uuid.uuid4().hex, consumer_id="consumer-A", name=None,
        source="test", event_type=None, field_path="price",
        operator="gt", value=100.0, one_shot=False,
    )
    evaluator = AlertEvaluator(store=store, subscription_bus=None)
    core_events.configure_alert_evaluator(evaluator.evaluate)
    try:
        published = await core_events.publish_event(
            event_type="test.price", source="test", data={"price": 150.0},
            persistent=True, store=store, bus=None,
        )
    finally:
        core_events.configure_alert_evaluator(None)

    a = store.list_relevant_events("consumer-A")
    b = store.list_relevant_events("consumer-B")
    triggers_a = [e for e in a if e["type"] == "alert.triggered"]
    triggers_b = [e for e in b if e["type"] == "alert.triggered"]
    runner.assert_eq("AT5-a-gets", len(triggers_a), 1)
    runner.assert_eq("AT5-b-not", len(triggers_b), 0)
    runner.assert_eq("AT5-routing", triggers_a[0]["routing"],
                     {"targets": ["consumer-A"]})
    runner.assert_eq("AT5-family", triggers_a[0]["data"]["alert_family"],
                     "generic")
    runner.assert_eq("AT5-consumer", triggers_a[0]["data"]["consumer_id"],
                     "consumer-A")
    runner.assert_eq("AT5-condition", triggers_a[0]["data"]["condition"],
                     {"field": "price", "operator": "gt", "threshold": 100.0})
    runner.assert_eq("AT5-observed", triggers_a[0]["data"]["observed"],
                     {"value": 150.0, "matched_event_id": published["id"],
                      "matched_event_type": "test.price",
                      "matched_source": "test"})


# -- AT6/AT7/AT8: market repeat / re-arm / crossing semantics ---------------------


async def test_at6_repeat_semantics(runner: R) -> None:
    """State machine unchanged: further qualifying ticks emit NO event."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="gt", threshold=100.0)
    engine = AlertEngine(store)
    await engine.evaluate(_mk_quote(ltp=150.0))   # fires
    await engine.evaluate(_mk_quote(ltp=160.0))   # already triggered
    await engine.evaluate(_mk_quote(ltp=170.0))   # already triggered
    runner.assert_eq("AT6-exactly-one", len(_trigger_events(store)), 1)


async def test_at7_rearm_emits_new_event(runner: R) -> None:
    """Re-arm allows exactly one NEW durable event."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    a = store.create_market_alert(exchange="NSE", instrument_token="T1",
                                  tradingsymbol="AAA", field="ltp",
                                  operator="gt", threshold=100.0)
    engine = AlertEngine(store)
    await engine.evaluate(_mk_quote(ltp=150.0))   # 1st event
    store.rearm_alert(a["id"])
    engine.reload()
    await engine.evaluate(_mk_quote(ltp=160.0))   # 2nd event
    await engine.evaluate(_mk_quote(ltp=170.0))   # already triggered
    events = _trigger_events(store)
    runner.assert_eq("AT7-two-events", len(events), 2)
    runner.assert_eq("AT7-distinct", events[0]["id"] != events[1]["id"], True)


async def test_at8_crossing(runner: R) -> None:
    """Crossing operators emit exactly one durable canonical event."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="crosses_above", threshold=100.0)
    engine = AlertEngine(store)
    await engine.evaluate(_mk_quote(ltp=90.0))    # below, no cross
    await engine.evaluate(_mk_quote(ltp=110.0))   # crosses above -> fires
    await engine.evaluate(_mk_quote(ltp=120.0))   # already triggered
    events = _trigger_events(store)
    runner.assert_eq("AT8-exactly-one", len(events), 1)
    runner.assert_eq("AT8-operator",
                     events[0]["data"]["condition"]["operator"],
                     "crosses_above")
    runner.assert_eq("AT8-observed", events[0]["data"]["observed"],
                     {"value": 110.0})


# -- AT9/AT10: generic one_shot / repeating semantics -----------------------------


async def test_at9_generic_one_shot(runner: R) -> None:
    """one_shot=True auto-disables after one fire (unchanged)."""
    from core import events as core_events
    from core.alerts import AlertEvaluator

    store, tmp = _mk_store()
    store.register_consumer("consumer-A")
    store.create_alert(
        alert_id=uuid.uuid4().hex, consumer_id="consumer-A", name=None,
        source="test", event_type=None, field_path="price",
        operator="gt", value=100.0, one_shot=True,
    )
    evaluator = AlertEvaluator(store=store, subscription_bus=None)
    core_events.configure_alert_evaluator(evaluator.evaluate)
    try:
        await core_events.publish_event(
            event_type="test.price", source="test", data={"price": 150.0},
            persistent=True, store=store, bus=None,
        )
        await core_events.publish_event(
            event_type="test.price", source="test", data={"price": 200.0},
            persistent=True, store=store, bus=None,
        )
    finally:
        core_events.configure_alert_evaluator(None)

    runner.assert_eq("AT9-one-shot-once", len(_trigger_events(store)), 1)
    # Alert auto-disabled after the one-shot fire.
    runner.assert_eq("AT9-disabled",
                     len(store.list_alerts_by_source_enabled("test")), 0)


async def test_at10_generic_repeating(runner: R) -> None:
    """one_shot=False repeats on every matching event (unchanged)."""
    from core import events as core_events
    from core.alerts import AlertEvaluator

    store, tmp = _mk_store()
    store.register_consumer("consumer-A")
    store.create_alert(
        alert_id=uuid.uuid4().hex, consumer_id="consumer-A", name=None,
        source="test", event_type=None, field_path="price",
        operator="gt", value=100.0, one_shot=False,
    )
    evaluator = AlertEvaluator(store=store, subscription_bus=None)
    core_events.configure_alert_evaluator(evaluator.evaluate)
    try:
        await core_events.publish_event(
            event_type="test.price", source="test", data={"price": 150.0},
            persistent=True, store=store, bus=None,
        )
        await core_events.publish_event(
            event_type="test.price", source="test", data={"price": 200.0},
            persistent=True, store=store, bus=None,
        )
    finally:
        core_events.configure_alert_evaluator(None)

    runner.assert_eq("AT10-repeating-two", len(_trigger_events(store)), 2)


# -- AT11: durable recent-event journal -------------------------------------------


async def test_at11_journal(runner: R) -> None:
    """The durable recent_events journal contains the canonical event."""
    from app.alerts import AlertEngine

    store, tmp = _mk_store()
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="gt", threshold=100.0)
    engine = AlertEngine(store)
    await engine.evaluate(_mk_quote(ltp=150.0))

    recent = store.get_recent_events(50, newest_first=False)
    triggers = [e for e in recent if e.get("type") == "alert.triggered"]
    runner.assert_eq("AT11-journal-count", len(triggers), 1)
    runner.assert_eq("AT11-journal-family",
                     triggers[0]["data"]["alert_family"], "market")


# -- AT12: SSE role proof ----------------------------------------------------------


async def test_at12_sse_role(runner: R) -> None:
    """SSE fan-out carries the canonical payload — old envelope is gone."""
    from app.alerts import AlertEngine
    from core import events as core_events
    from core.sse_broker import EventBroker

    store, tmp = _mk_store()
    store.create_market_alert(exchange="NSE", instrument_token="T1",
                              tradingsymbol="AAA", field="ltp",
                              operator="gt", threshold=100.0)
    engine = AlertEngine(store)

    broker = EventBroker(queue_size=10)
    core_events.configure_sse_broker(broker)
    try:
        ctx = broker.subscribe()
        gen = await ctx.__aenter__()
        await engine.evaluate(_mk_quote(ltp=150.0))
        line = await asyncio.wait_for(gen.__anext__(), timeout=2)
        await ctx.__aexit__(None, None, None)
    finally:
        core_events.configure_sse_broker(None)

    payload = json.loads(line)
    runner.assert_eq("AT12-type", payload["type"], "alert.triggered")
    runner.assert_eq("AT12-source", payload["source"], "alert_engine")
    runner.assert_eq("AT12-family", payload["data"]["alert_family"], "market")
    runner.assert_eq("AT12-version", payload["data"]["version"], 1)
    # The old SSE-only envelope fields must NOT appear (removed in 3B).
    runner.assert_true("AT12-no-old-envelope",
                       "observed_value" not in payload["data"],
                       "old SSE envelope leaked into canonical payload")
    runner.assert_true("AT12-no-old-envelope-2",
                       "tradingsymbol" not in payload["data"],
                       "old SSE envelope leaked into canonical payload")


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_at1_canonical_shape(runner)
    await test_at2_market_durable_event(runner)
    await test_at3_market_replay(runner)
    await test_at4_broadcast(runner)
    await test_at5_generic_owner_routing(runner)
    await test_at6_repeat_semantics(runner)
    await test_at7_rearm_emits_new_event(runner)
    await test_at8_crossing(runner)
    await test_at9_generic_one_shot(runner)
    await test_at10_generic_repeating(runner)
    await test_at11_journal(runner)
    await test_at12_sse_role(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)