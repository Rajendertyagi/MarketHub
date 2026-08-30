#!/usr/bin/env python3
"""B2 condition-alert engine tests (state machine + restart safety).

Covers the frozen B2 state machine and engine behaviors:

  * CE1  LEVEL repeat — 99/101/102/99/101 -> exactly 2 events
  * CE2  LEVEL first observation UNKNOWN->FALSE persists baseline, no fire
  * CE3  LEVEL UNKNOWN sequence 101/None/102 -> first fires only, then
        99/101 -> second (TRUE->UNKNOWN does NOT re-arm)
  * CE4  CROSSING repeat — below/above/below/above -> exactly 2 events
  * CE5  CROSSING once — first crossing fires, then disabled
  * CE6  LEVEL once — first TRUE fires, then disabled
  * CE7  restart — runtime state reloaded; no duplicate fire after restart
  * CE8  no-global-scan — only the resolved instrument's alerts are touched
  * CE9  malformed alert row skipped, healthy alerts still evaluate
  * CE10 concurrency — concurrent quotes never double-fire
  * CE11 payload shape — canonical alert.triggered with market_condition
  * CE12 previous_value diagnostic present on repeat fires

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
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


def _mk_engine(store, resolver=None):
    from app.condition_alerts import ConditionAlertEngine
    if resolver is None:
        resolver = _mk_resolver(store)
    return ConditionAlertEngine(store, resolver=resolver)


def _create(store, *, trigger_mode="repeat", metric="ltp", operator="gt",
            value=100, condition_id="cond-1", canonical_id=RELIANCE):
    return store.create_condition_alert(
        consumer_id="consumer-1", name="test", trigger_mode=trigger_mode,
        condition_json={"condition_version": 1, "condition_id": condition_id,
                        "metric": metric, "operator": operator, "value": value,
                        "instrument": {"canonical_id": canonical_id}})


def _triggered_events(store):
    return [e for e in store.list_pending(100)
            if e["type"] == "alert.triggered"]


async def test_ce1_level_repeat(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            seq = [99, 101, 102, 99, 101]
            fired = []
            for v in seq:
                fired.extend(await engine.evaluate(_FakeQuote(v)))
            return fired
        fired = await run()
        runner.assert_eq("CE1-fired-count", len(fired), 2)
        runner.assert_eq("CE1-events", len(_triggered_events(store)), 2)
        a = store.get_condition_alert(fired[0]["alert_id"])
        runner.assert_eq("CE1-trigger_count", a["trigger_count"], 2)
    finally:
        tmp.cleanup()


async def test_ce2_first_observation_false_baseline(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            fired = await engine.evaluate(_FakeQuote(50))
            return fired
        fired = await run()
        runner.assert_eq("CE2-no-fire", len(fired), 0)
        runner.assert_eq("CE2-no-events", len(_triggered_events(store)), 0)
        # Baseline persisted (restart-safe).
        st = store.load_condition_runtime_state()
        row = list(st.values())[0]
        # row is {condition_id: state} — grab any value.
        baseline = next(iter(row.values()))["last_result"]
        runner.assert_eq("CE2-baseline", baseline, "false")
    finally:
        tmp.cleanup()


async def test_ce3_unknown_does_not_rearm(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            fired = []
            # 101 -> FIRE (first observation TRUE)
            fired.extend(await engine.evaluate(_FakeQuote(101)))
            # None (UNKNOWN) -> TRUE->UNKNOWN does NOT re-arm
            fired.extend(await engine.evaluate(_FakeQuote(None)))
            # 102 -> TRUE->TRUE no fire
            fired.extend(await engine.evaluate(_FakeQuote(102)))
            # 99 -> TRUE->FALSE re-arms
            fired.extend(await engine.evaluate(_FakeQuote(99)))
            # 101 -> FALSE->TRUE FIRES (second)
            fired.extend(await engine.evaluate(_FakeQuote(101)))
            return fired
        fired = await run()
        runner.assert_eq("CE3-fired-count", len(fired), 2)
        runner.assert_eq("CE3-events", len(_triggered_events(store)), 2)
    finally:
        tmp.cleanup()


async def test_ce4_crossing_repeat(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="crosses_above", value=100)
        engine = _mk_engine(store)
        async def run():
            fired = []
            for v in [90, 110, 120, 90, 110]:
                fired.extend(await engine.evaluate(_FakeQuote(v)))
            return fired
        fired = await run()
        # 90 (below) establishes side; 110 crosses -> fire; 120 no;
        # 90 re-arms; 110 crosses -> fire.
        runner.assert_eq("CE4-fired-count", len(fired), 2)
        runner.assert_eq("CE4-events", len(_triggered_events(store)), 2)
    finally:
        tmp.cleanup()


async def test_ce5_crossing_once(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="crosses_above", value=100, trigger_mode="once")
        engine = _mk_engine(store)
        async def run():
            fired = []
            for v in [90, 110, 90, 110]:
                fired.extend(await engine.evaluate(_FakeQuote(v)))
            return fired
        fired = await run()
        runner.assert_eq("CE5-fired-count", len(fired), 1)
        runner.assert_eq("CE5-events", len(_triggered_events(store)), 1)
        a = store.get_condition_alert(fired[0]["alert_id"])
        runner.assert_eq("CE5-disabled", a["enabled"], False)
    finally:
        tmp.cleanup()


async def test_ce6_level_once(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100, trigger_mode="once")
        engine = _mk_engine(store)
        async def run():
            fired = []
            for v in [50, 101, 50, 101]:
                fired.extend(await engine.evaluate(_FakeQuote(v)))
            return fired
        fired = await run()
        runner.assert_eq("CE6-fired-count", len(fired), 1)
        runner.assert_eq("CE6-events", len(_triggered_events(store)), 1)
        a = store.get_condition_alert(fired[0]["alert_id"])
        runner.assert_eq("CE6-disabled", a["enabled"], False)
    finally:
        tmp.cleanup()


async def test_ce7_restart_no_duplicate(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            await engine.evaluate(_FakeQuote(101))   # FIRE
            await engine.evaluate(_FakeQuote(102))   # no fire
            return None
        await run()
        runner.assert_eq("CE7-before-restart", len(_triggered_events(store)), 1)
        # Restart: fresh engine reloads runtime state (last_result=true).
        engine2 = _mk_engine(store)
        async def run2():
            fired = await engine2.evaluate(_FakeQuote(103))  # TRUE->TRUE no fire
            return fired
        fired = await run2()
        runner.assert_eq("CE7-after-restart-no-fire", len(fired), 0)
        runner.assert_eq("CE7-still-one-event", len(_triggered_events(store)), 1)
        # Re-arm then fire again after restart.
        async def run3():
            await engine2.evaluate(_FakeQuote(50))   # re-arm
            fired = await engine2.evaluate(_FakeQuote(101))  # FIRE
            return fired
        fired = await run3()
        runner.assert_eq("CE7-post-restart-fire", len(fired), 1)
        runner.assert_eq("CE7-two-events", len(_triggered_events(store)), 2)
    finally:
        tmp.cleanup()


async def test_ce8_no_global_scan(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        # Two alerts: one for RELIANCE, one for a DIFFERENT instrument.
        _create(store, operator="gt", value=100, condition_id="c-rel")
        _create(store, operator="gt", value=100, condition_id="c-other",
                canonical_id="NSE:EQUITY:INE999999999")
        engine = _mk_engine(store)
        async def run():
            fired = await engine.evaluate(_FakeQuote(101))
            return fired
        fired = await run()
        # Only the RELIANCE alert fires; the other instrument's alert is
        # never touched (instrument-indexed, no global scan).
        runner.assert_eq("CE8-fired-count", len(fired), 1)
        runner.assert_eq("CE8-fired-condition", fired[0]["condition_id"], "c-rel")
        runner.assert_eq("CE8-events", len(_triggered_events(store)), 1)
    finally:
        tmp.cleanup()


async def test_ce9_malformed_skipped(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        good_id = _create(store, operator="gt", value=100, condition_id="c-good")
        # Inject a malformed enabled row directly (bypasses validation).
        import json
        conn = store._open(store._db_path)
        conn.execute(
            "INSERT INTO condition_alerts "
            "(alert_id, consumer_id, name, enabled, trigger_mode, "
            " condition_json, canonical_instrument_id, metadata_json, "
            " created_at, updated_at, last_triggered_at, trigger_count) "
            "VALUES (?, 'consumer-1', 'bad', 1, 'repeat', ?, ?, '{}', ?, ?, NULL, 0)",
            ("bad-alert", json.dumps({"condition_version": 1, "metric": "nope"}),
             RELIANCE, "2026-08-30T00:00:00+00:00", "2026-08-30T00:00:00+00:00"))
        conn.commit()
        conn.close()
        engine = _mk_engine(store)
        async def run():
            fired = await engine.evaluate(_FakeQuote(101))
            return fired
        fired = await run()
        # Healthy alert still evaluates; malformed row skipped, no crash.
        runner.assert_eq("CE9-fired-count", len(fired), 1)
        runner.assert_eq("CE9-fired-id", fired[0]["alert_id"], good_id)
    finally:
        tmp.cleanup()


async def test_ce10_concurrency(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            # Fire many concurrent quotes; exactly ONE trigger for the
            # first TRUE observation (per-alert lock serializes).
            await asyncio.gather(*[
                engine.evaluate(_FakeQuote(101)) for _ in range(20)
            ])
            return None
        await run()
        runner.assert_eq("CE10-events", len(_triggered_events(store)), 1)
        a = store.list_condition_alerts()[0]
        runner.assert_eq("CE10-trigger_count", a["trigger_count"], 1)
    finally:
        tmp.cleanup()


async def test_ce11_payload_shape(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            await engine.evaluate(_FakeQuote(101))
            return None
        await run()
        ev = _triggered_events(store)[0]
        data = ev["data"]
        runner.assert_eq("CE11-version", data["version"], 1)
        runner.assert_eq("CE11-family", data["alert_family"], "market_condition")
        runner.assert_eq("CE11-source", data["source"], "alert_engine")
        runner.assert_eq("CE11-consumer", data["consumer_id"], "consumer-1")
        runner.assert_eq("CE11-condition-version",
                         data["condition"]["condition_version"], 1)
        runner.assert_eq("CE11-condition-logic", data["condition"]["logic"], None)
        c = data["condition"]["conditions"][0]
        runner.assert_eq("CE11-condition-metric", c["metric"], "ltp")
        runner.assert_eq("CE11-condition-operator", c["operator"], "gt")
        runner.assert_eq("CE11-condition-value", c["value"], 100)
        obs = data["observed"]
        runner.assert_eq("CE11-obs-root", obs["root_result"], "true")
        leaf = obs["leaves"][0]
        runner.assert_eq("CE11-obs-metric", leaf["metric"], "ltp")
        runner.assert_eq("CE11-obs-operator", leaf["operator"], "gt")
        runner.assert_eq("CE11-obs-expected", leaf["expected"], 100)
        runner.assert_eq("CE11-obs-value", leaf["value"], 101)
        runner.assert_eq("CE11-obs-condition_id", leaf["condition_id"], "cond-1")
        inst = data["instrument"]
        runner.assert_eq("CE11-inst-canonical", inst["canonical_id"], RELIANCE)
        runner.assert_eq("CE11-inst-exchange", inst["exchange"], "NSE")
        runner.assert_eq("CE11-inst-type", inst["instrument_type"], "EQUITY")
        runner.assert_eq("CE11-one_shot", data["one_shot"], False)
        runner.assert_eq("CE11-metadata", data["metadata"], {"trigger_mode": "repeat"})
        # Routing targets the owning consumer.
        runner.assert_eq("CE11-routing", ev["routing"], {"targets": ["consumer-1"]})
    finally:
        tmp.cleanup()


async def test_ce12_previous_value(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        _create(store, operator="gt", value=100)
        engine = _mk_engine(store)
        async def run():
            await engine.evaluate(_FakeQuote(50))    # baseline
            fired = await engine.evaluate(_FakeQuote(101))  # FIRE
            return fired
        fired = await run()
        runner.assert_eq("CE12-fired", len(fired), 1)
        runner.assert_eq("CE12-previous_value", fired[0]["previous_value"], 50)
        ev = _triggered_events(store)[0]
        leaf = ev["data"]["observed"]["leaves"][0]
        runner.assert_eq("CE12-event-previous", leaf["previous_value"], 50)
    finally:
        tmp.cleanup()


async def main() -> bool:
    runner = R()
    await test_ce1_level_repeat(runner)
    await test_ce2_first_observation_false_baseline(runner)
    await test_ce3_unknown_does_not_rearm(runner)
    await test_ce4_crossing_repeat(runner)
    await test_ce5_crossing_once(runner)
    await test_ce6_level_once(runner)
    await test_ce7_restart_no_duplicate(runner)
    await test_ce8_no_global_scan(runner)
    await test_ce9_malformed_skipped(runner)
    await test_ce10_concurrency(runner)
    await test_ce11_payload_shape(runner)
    await test_ce12_previous_value(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)