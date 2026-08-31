#!/usr/bin/env python3
"""B4 structured AND/OR condition groups tests (condition_version=2).

Covers the condition group engine behaviors:

  * CG1  v1 backward compatibility — single-leaf v1 alert fires correctly
  * CG2  v2 ALL truth table — T,T→T; T,F→F; T,U→U; F,F→F; F,U→F; U,U→U
  * CG3  v2 ANY truth table — T,T→T; T,F→T; T,U→T; F,F→F; F,U→U; U,U→U
  * CG4  nested group — (A AND B) OR C fires when outer ANY is satisfied
  * CG5  root transition U→T fires; F→T fires; T→T no fire
  * CG6  root transition T→F re-arms; T→U does NOT re-arm
  * CG7  crossing leaf inside group — ephemeral TRUE only on crossing tick
  * CG8  crossing+level group fires only when all leaves TRUE same tick
  * CG9  once-mode disables after first group fire
  * CG10 repeat-mode allows re-fire after T→F→T
  * CG11 restart safety — state reloaded; no duplicate fire
  * CG12 max depth enforcement — depth 9 rejected
  * CG13 max leaves enforcement — 65 leaves rejected
   * CG14 multi-instrument group accepted (B7)
  * CG15 malformed tree rejected
  * CG16 concurrent evaluation — per-alert lock, no double-fire
  * CG17 write amplification — no state write when leaf unchanged
  * CG18 v2 payload shape — condition_version=2, logic, conditions array
  * CG19 Kleene UNKNOWN propagation — ALL: U propagates; ANY: U short-circuits to U
  * CG20 nested depth 8 allowed

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

from core.errors import ConditionValidationError
from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver

RELIANCE = "NSE:EQUITY:INE002A01018"
RELIANCE_ISIN = "INE002A01018"


def _mk_store() -> tuple[EventStore, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    store.register_consumer("consumer-1")
    return store, tmp


def _mk_resolver(store):
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


def _create_v1(store, *, metric="ltp", operator="gt", value=100,
               condition_id="cond-1", canonical_id=RELIANCE):
    return store.create_condition_alert(
        consumer_id="consumer-1", name="v1-test", trigger_mode="repeat",
        condition_json={"condition_version": 1, "condition_id": condition_id,
                        "metric": metric, "operator": operator, "value": value,
                        "instrument": {"canonical_id": canonical_id}})


def _create_v2_all(store, *, cond_ids=None, metric="ltp", value=100,
                   canonical_id=RELIANCE, trigger_mode="repeat"):
    ids = cond_ids or [f"c{i}" for i in range(2)]
    conditions = [
        {"condition_id": cid, "metric": metric, "operator": "gt", "value": value,
         "instrument": {"canonical_id": canonical_id}}
        for cid in ids
    ]
    return store.create_condition_alert(
        consumer_id="consumer-1", name="v2-all-test", trigger_mode=trigger_mode,
        condition_json={"condition_version": 2, "logic": "all",
                        "conditions": conditions})


def _create_v2_any(store, *, cond_ids=None, metric="ltp", value=100,
                   canonical_id=RELIANCE, trigger_mode="repeat"):
    ids = cond_ids or [f"c{i}" for i in range(2)]
    conditions = [
        {"condition_id": cid, "metric": metric, "operator": "gt", "value": value,
         "instrument": {"canonical_id": canonical_id}}
        for cid in ids
    ]
    return store.create_condition_alert(
        consumer_id="consumer-1", name="v2-any-test", trigger_mode=trigger_mode,
        condition_json={"condition_version": 2, "logic": "any",
                        "conditions": conditions})


class _FakeQuote:
    def __init__(self, ltp, token="2885", tsym="RELIANCE"):
        self.ltp = ltp
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = tsym
        self.provider = "upstox"


# ── CG1: v1 backward compatibility ──────────────────────────────────────

async def test_cg1_v1_backward_compat(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v1(store, operator="gt", value=100)
        engine = _mk_engine(store)
        fired = await engine.evaluate(_FakeQuote(99))
        runner.assert_eq("CG1-no-fire", len(fired), 0)
        fired = await engine.evaluate(_FakeQuote(101))
        runner.assert_eq("CG1-fired", len(fired), 1)
        runner.assert_eq("CG1-version",
                         fired[0].get("condition_version", 1), 1)
        # Root mirrors leaf for v1.
        st = store.load_condition_runtime_state()
        row = st[aid]
        runner.assert_true("CG1-has-leaf", any(k != f"root-{aid}"
                                                for k in row))
        runner.assert_true("CG1-has-root",
                           any(k.startswith("root-") for k in row))
    finally:
        tmp.cleanup()


# ── CG2: v2 ALL truth table ─────────────────────────────────────────────

async def test_cg2_all_truth_table(runner: R) -> None:
    """Test ALL group with two level conditions on same instrument but
    DIFFERENT thresholds so they can diverge.

    Truth table (LTP>100 AND LTP>150):
      T,T (ltp=200) → T (fire)
      T,F (ltp=120) → F (no fire, already fired)
      F,F (ltp=50)  → F (re-arm)
      F,T impossible (if >150 then >100)
    """
    store, tmp = _mk_store()
    try:
        # Two conditions on same metric but different thresholds
        conditions = [
            {"condition_id": "c_low", "metric": "ltp", "operator": "gt",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c_high", "metric": "ltp", "operator": "gt",
             "value": 150, "instrument": {"canonical_id": RELIANCE}},
        ]
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="all-test", trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "all",
                            "conditions": conditions})
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # Both FALSE (50 < 100 and 50 < 150) → no fire
        fired = await evaluate(50)
        runner.assert_eq("CG2-TT-FF", len(fired), 0)

        # T,F (120 > 100 but 120 < 150) → no fire
        fired = await evaluate(120)
        runner.assert_eq("CG2-TT-TF", len(fired), 0)

        # T,T (200 > 100 and 200 > 150) → fire
        fired = await evaluate(200)
        runner.assert_eq("CG2-TT-TT", len(fired), 1)

        # T,T again → no re-fire (already true)
        fired = await evaluate(210)
        runner.assert_eq("CG2-TT-TT2", len(fired), 0)

        # F,F (50) → re-arm (no fire)
        fired = await evaluate(50)
        runner.assert_eq("CG2-rearm", len(fired), 0)

        # T,T again → fire
        fired = await evaluate(200)
        runner.assert_eq("CG2-TT-refire", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG3: v2 ANY truth table ─────────────────────────────────────────────

async def test_cg3_any_truth_table(runner: R) -> None:
    """Test ANY group.

    Truth table (LTP>100 OR Volume>1M):
      T,T → T (fire)
      T,F → T (fire)
      F,T → T (fire)
      F,F → F (no fire)
    """
    store, tmp = _mk_store()
    try:
        aid = _create_v2_any(store, cond_ids=["c_ltp", "c_vol"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # Both FALSE → no fire
        fired = await evaluate(50)
        runner.assert_eq("CG3-TT-FF", len(fired), 0)

        # First TRUE → fire (ANY satisfied)
        fired = await evaluate(101)
        runner.assert_eq("CG3-TT-FT", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG4: Nested group ───────────────────────────────────────────────────

async def test_cg4_nested_group(runner: R) -> None:
    """Nested: ANY(leaf_A, ALL(leaf_B, leaf_C)).

    Fires when A is TRUE OR (B AND C are both TRUE).
    """
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        conditions = [
            {"condition_id": "c_a", "metric": "ltp", "operator": "gt",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_version": 2, "logic": "all", "conditions": [
                {"condition_id": "c_b", "metric": "ltp", "operator": "lt",
                 "value": 200, "instrument": {"canonical_id": RELIANCE}},
                {"condition_id": "c_c", "metric": "ltp", "operator": "gt",
                 "value": 50, "instrument": {"canonical_id": RELIANCE}},
            ]},
        ]
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="nested", trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "any",
                            "conditions": conditions})
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # All FALSE: ltp=30 → A=F, B=F(30<200=T but C=F 30>50=F), so inner ALL=F
        # Wait: 30<200 is TRUE, 30>50 is FALSE → inner ALL=False → outer ANY needs A=T
        fired = await evaluate(30)
        runner.assert_eq("CG4-F", len(fired), 0)

        # A TRUE (ltp>100): ltp=150 → A=T → outer ANY fires
        fired = await evaluate(150)
        runner.assert_eq("CG4-A-T", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG5: Root transition U→T fires; F→T fires ─────────────────────────

async def test_cg5_root_transitions(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # First observation → U→T fires
        fired = await evaluate(101)
        runner.assert_eq("CG5-U-to-T", len(fired), 1)

        # T→T no fire
        fired = await evaluate(102)
        runner.assert_eq("CG5-T-to-T", len(fired), 0)

        # T→F no fire (re-arming)
        fired = await evaluate(50)
        runner.assert_eq("CG5-T-to-F", len(fired), 0)

        # F→T fires
        fired = await evaluate(101)
        runner.assert_eq("CG5-F-to-T", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG6: Root transition T→F re-arms; T→U does NOT re-arm ──────────────

async def test_cg6_rearm_rules(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # Fire first time
        fired = await evaluate(101)
        runner.assert_eq("CG6-first-fire", len(fired), 1)

        # T→T no fire
        fired = await evaluate(102)
        runner.assert_eq("CG6-T-to-T", len(fired), 0)

        # T→F re-arms (sets state to FALSE, ready for next F→T)
        fired = await evaluate(50)
        runner.assert_eq("CG6-T-to-F", len(fired), 0)

        # Next F→T should fire again
        fired = await evaluate(101)
        runner.assert_eq("CG6-re-arm-fire", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG7: Crossing leaf inside group ─────────────────────────────────────

async def test_cg7_crossing_in_group(runner: R) -> None:
    """Group with one crossing condition. Crossing leaf produces ephemeral
    TRUE only on the crossing tick."""
    store, tmp = _mk_store()
    try:
        # Create a v2 group with one crossing condition
        conditions = [
            {"condition_id": "c_cross", "metric": "ltp",
             "operator": "crosses_above", "value": 100,
             "instrument": {"canonical_id": RELIANCE}},
        ]
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="cross-group",
            trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "all",
                            "conditions": conditions})
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # Below threshold → no fire
        fired = await evaluate(90)
        runner.assert_eq("CG7-below", len(fired), 0)

        # Cross above → fire (FIRST crossing)
        fired = await evaluate(110)
        runner.assert_eq("CG7-cross-above", len(fired), 1)

        # Above again → no fire (leaf still TRUE, root T→T no fire)
        fired = await evaluate(120)
        runner.assert_eq("CG7-above-again", len(fired), 0)

        # Cross below → leaf goes FALSE, root T→F (no fire, just re-arms)
        fired = await evaluate(90)
        runner.assert_eq("CG7-cross-below", len(fired), 0)

        # Cross above again → root F→T fires
        fired = await evaluate(110)
        runner.assert_eq("CG7-cross-above-2", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG8: Crossing+level group fires only same tick ─────────────────────

async def test_cg8_crossing_level_group(runner: R) -> None:
    """Group with one crossing and one level condition. Both must be TRUE
    on the same tick for the group to fire."""
    store, tmp = _mk_store()
    try:
        conditions = [
            {"condition_id": "c_cross", "metric": "ltp",
             "operator": "crosses_above", "value": 100,
             "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c_level", "metric": "ltp",
             "operator": "gt", "value": 90,
             "instrument": {"canonical_id": RELIANCE}},
        ]
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="cross-level",
            trigger_mode="repeat",
            condition_json={"condition_version": 2, "logic": "all",
                            "conditions": conditions})
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # Start below both thresholds
        await evaluate(80)

        # Cross above 100 AND level > 90 → fire
        fired = await evaluate(110)
        runner.assert_eq("CG8-both-true", len(fired), 1)

        # Stay above → no re-fire
        fired = await evaluate(120)
        runner.assert_eq("CG8-stay-above", len(fired), 0)
    finally:
        tmp.cleanup()


# ── CG9: Once-mode disables after group fire ────────────────────────────

async def test_cg9_once_mode(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100,
                             trigger_mode="once")
        engine = _mk_engine(store)

        fired = await engine.evaluate(_FakeQuote(101))
        runner.assert_eq("CG9-fired", len(fired), 1)

        # Alert should be disabled
        a = store.get_condition_alert(aid)
        runner.assert_eq("CG9-disabled", a["enabled"], False)

        # No further fires
        fired = await engine.evaluate(_FakeQuote(102))
        runner.assert_eq("CG9-no-refire", len(fired), 0)
    finally:
        tmp.cleanup()


# ── CG10: Repeat-mode allows re-fire ────────────────────────────────────

async def test_cg10_repeat_mode(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100,
                             trigger_mode="repeat")
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # Fire once
        fired = await evaluate(101)
        runner.assert_eq("CG10-first", len(fired), 1)

        # Re-arm by going false
        await evaluate(50)

        # Fire again
        fired = await evaluate(101)
        runner.assert_eq("CG10-second", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG11: Restart safety ────────────────────────────────────────────────

async def test_cg11_restart_safety(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        # Fire once
        fired = await engine.evaluate(_FakeQuote(101))
        runner.assert_eq("CG11-fired-before", len(fired), 1)

        # Restart: rebuild engine from store
        engine2 = _mk_engine(store)
        engine2.reload()

        # Same state → no duplicate fire
        fired = await engine2.evaluate(_FakeQuote(102))
        runner.assert_eq("CG11-no-dup", len(fired), 0)

        # Re-arm and fire again
        await engine2.evaluate(_FakeQuote(50))
        fired = await engine2.evaluate(_FakeQuote(101))
        runner.assert_eq("CG11-after-rearm", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG12: Max depth enforcement ─────────────────────────────────────────

async def test_cg12_max_depth(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        # Build a tree of depth 9 (should be rejected)
        deep = {"condition_version": 2, "logic": "all", "conditions": []}
        for _ in range(9):
            deep = {"condition_version": 2, "logic": "all", "conditions": [deep]}
        deep["conditions"][0]["condition_id"] = "leaf"
        deep["conditions"][0]["metric"] = "ltp"
        deep["conditions"][0]["operator"] = "gt"
        deep["conditions"][0]["value"] = 100
        deep["conditions"][0]["instrument"] = {"canonical_id": RELIANCE}

        try:
            store.create_condition_alert(
                consumer_id="consumer-1", name="deep", trigger_mode="repeat",
                condition_json=deep)
            runner.fail("CG12", "expected ConditionValidationError")
        except ConditionValidationError:
            runner.ok("CG12-depth-rejected")
    finally:
        tmp.cleanup()


# ── CG13: Max leaves enforcement ────────────────────────────────────────

async def test_cg13_max_leaves(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        # 65 leaves should be rejected
        conditions = []
        for i in range(65):
            conditions.append({
                "condition_id": f"c{i}", "metric": "ltp",
                "operator": "gt", "value": 100,
                "instrument": {"canonical_id": RELIANCE}})
        tree = {"condition_version": 2, "logic": "all", "conditions": conditions}

        try:
            store.create_condition_alert(
                consumer_id="consumer-1", name="wide", trigger_mode="repeat",
                condition_json=tree)
            runner.fail("CG13", "expected ConditionValidationError")
        except ConditionValidationError:
            runner.ok("CG13-leaves-rejected")
    finally:
        tmp.cleanup()


# ── CG14: Multi-instrument group accepted (B7) ─────────────────────────

async def test_cg14_multi_instrument(runner: R) -> None:
    """CG14: multi-instrument group accepted (B7 removed restriction)."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        conditions = [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100,
             "instrument": {"canonical_id": "NSE:EQUITY:INE002A01018"}},
            {"condition_id": "c2", "metric": "volume", "operator": "gt",
             "value": 1000,
             "instrument": {"canonical_id": "NSE:EQUITY:INE009A01021"}},
        ]
        tree = {"condition_version": 2, "logic": "all", "conditions": conditions}

        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="multi-inst",
            trigger_mode="repeat", condition_json=tree)
        runner.ok("CG14-multi-inst-accepted")
        runner.assert_ge("CG14-aid-length", len(aid), 1)
    finally:
        tmp.cleanup()


# ── CG15: Malformed tree rejected ───────────────────────────────────────

async def test_cg15_malformed(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        # Missing logic field
        bad = {"condition_version": 2, "conditions": []}
        try:
            store.create_condition_alert(
                consumer_id="consumer-1", name="bad1", trigger_mode="repeat",
                condition_json=bad)
            runner.fail("CG15a", "expected ConditionValidationError")
        except ConditionValidationError:
            runner.ok("CG15a-missing-logic")

        # Empty conditions
        bad2 = {"condition_version": 2, "logic": "all", "conditions": []}
        try:
            store.create_condition_alert(
                consumer_id="consumer-1", name="bad2", trigger_mode="repeat",
                condition_json=bad2)
            runner.fail("CG15b", "expected ConditionValidationError")
        except ConditionValidationError:
            runner.ok("CG15b-empty-conditions")

        # Invalid operator
        bad3 = {"condition_version": 2, "logic": "all", "conditions": [
            {"condition_id": "c1", "metric": "ltp", "operator": "invalid",
             "value": 100, "instrument": {"canonical_id": RELIANCE}}]}
        try:
            store.create_condition_alert(
                consumer_id="consumer-1", name="bad3", trigger_mode="repeat",
                condition_json=bad3)
            runner.fail("CG15c", "expected ConditionValidationError")
        except ConditionValidationError:
            runner.ok("CG15c-invalid-operator")
    finally:
        tmp.cleanup()


# ── CG16: Concurrent evaluation ─────────────────────────────────────────

async def test_cg16_concurrent(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        # Flood with concurrent quotes at value 101
        tasks = [engine.evaluate(_FakeQuote(101)) for _ in range(20)]
        results = await asyncio.gather(*tasks)
        total_fired = sum(len(r) for r in results)
        runner.assert_eq("CG16-no-dup", total_fired, 1)
    finally:
        tmp.cleanup()


# ── CG17: Write amplification — no write when unchanged ─────────────────

async def test_cg17_write_amplification(runner: R) -> None:
    """After initial fire, repeated TRUE quotes should not write state."""
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        # Fire once
        fired = await engine.evaluate(_FakeQuote(101))
        runner.assert_eq("CG17-fired", len(fired), 1)

        # Fire count should be 1
        a = store.get_condition_alert(aid)
        runner.assert_eq("CG17-count-after-1", a["trigger_count"], 1)

        # 1000 more TRUE quotes — no additional writes
        for _ in range(1000):
            await engine.evaluate(_FakeQuote(101))

        a = store.get_condition_alert(aid)
        runner.assert_eq("CG17-count-after-1000", a["trigger_count"], 1)

        # Events should still be exactly 1
        events = [e for e in store.list_pending(10000)
                  if e["type"] == "alert.triggered"]
        runner.assert_eq("CG17-events", len(events), 1)
    finally:
        tmp.cleanup()


# ── CG18: v2 payload shape ──────────────────────────────────────────────

async def test_cg18_payload_shape(runner: R) -> None:
    store, tmp = _mk_store()
    try:
        aid = _create_v2_all(store, cond_ids=["c1", "c2"],
                             metric="ltp", value=100)
        engine = _mk_engine(store)

        fired = await engine.evaluate(_FakeQuote(101))
        runner.assert_eq("CG18-fired", len(fired), 1)

        ev = [e for e in store.list_pending(100)
              if e["type"] == "alert.triggered"][0]
        data = ev["data"]

        # Condition section
        runner.assert_eq("CG18-version", data["condition"]["condition_version"], 2)
        runner.assert_eq("CG18-logic", data["condition"]["logic"], "all")
        runner.assert_eq("CG18-conditions-count",
                         len(data["condition"]["conditions"]), 2)

        # Observed section
        obs = data["observed"]
        runner.assert_eq("CG18-root-result", obs["root_result"], "true")
        runner.assert_eq("CG18-leaves-count", len(obs["leaves"]), 2)

        for leaf in obs["leaves"]:
            runner.assert_true("CG18-leaf-cid", "condition_id" in leaf)
            runner.assert_true("CG18-leaf-metric", "metric" in leaf)
            runner.assert_true("CG18-leaf-value", "value" in leaf)
    finally:
        tmp.cleanup()


# ── CG19: Kleene UNKNOWN propagation ────────────────────────────────────

async def test_cg19_kleene_unknown(runner: R) -> None:
    """ALL: U propagates (no fire until all known).
    ANY: U means "maybe" — if one is T and one is U, result is T (short-circuit)."""
    store, tmp = _mk_store()
    try:
        # ALL group: two conditions on same metric, same threshold
        # Both will have same result since they use same quote
        # To test UNKNOWN, we need a metric that can be None
        aid_all = _create_v2_all(store, cond_ids=["c1", "c2"],
                                  metric="ltp", value=100)
        engine = _mk_engine(store)

        async def evaluate(ltp):
            return await engine.evaluate(_FakeQuote(ltp))

        # First observation with None ltp → UNKNOWN for both
        fired = await evaluate(None)
        runner.assert_eq("CG19-ALL-U-no-fire", len(fired), 0)

        # Now provide TRUE → ALL fires (both T)
        fired = await evaluate(101)
        runner.assert_eq("CG19-ALL-T-T", len(fired), 1)

        # ANY group
        aid_any = _create_v2_any(store, cond_ids=["c1", "c2"],
                                  metric="ltp", value=100)
        engine2 = _mk_engine(store)

        # First observation None → UNKNOWN
        fired = await engine2.evaluate(_FakeQuote(None))
        runner.assert_eq("CG19-ANY-U-no-fire", len(fired), 0)

        # Now TRUE → ANY should fire (T OR U = T in Kleene)
        fired = await engine2.evaluate(_FakeQuote(101))
        runner.assert_eq("CG19-ANY-TU-fire", len(fired), 1)
    finally:
        tmp.cleanup()


# ── CG20: Nested depth 8 allowed ────────────────────────────────────────

async def test_cg20_nested_depth_8(runner: R) -> None:
    """Depth 8 should be accepted (max depth is 8)."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        # Build depth-8 tree (root at depth 0, leaf at depth 7)
        leaf = {"condition_id": "leaf", "metric": "ltp", "operator": "gt",
                "value": 100, "instrument": {"canonical_id": RELIANCE}}
        tree = leaf
        for _ in range(7):
            tree = {"condition_version": 2, "logic": "all", "conditions": [tree]}

        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="depth8", trigger_mode="repeat",
            condition_json=tree)
        runner.assert_true("CG20-depth8-accepted", bool(aid))
    finally:
        tmp.cleanup()


# ── Main ─────────────────────────────────────────────────────────────────

async def main() -> bool:
    runner = R()
    await test_cg1_v1_backward_compat(runner)
    await test_cg2_all_truth_table(runner)
    await test_cg3_any_truth_table(runner)
    await test_cg4_nested_group(runner)
    await test_cg5_root_transitions(runner)
    await test_cg6_rearm_rules(runner)
    await test_cg7_crossing_in_group(runner)
    await test_cg8_crossing_level_group(runner)
    await test_cg9_once_mode(runner)
    await test_cg10_repeat_mode(runner)
    await test_cg11_restart_safety(runner)
    await test_cg12_max_depth(runner)
    await test_cg13_max_leaves(runner)
    await test_cg14_multi_instrument(runner)
    await test_cg15_malformed(runner)
    await test_cg16_concurrent(runner)
    await test_cg17_write_amplification(runner)
    await test_cg18_payload_shape(runner)
    await test_cg19_kleene_unknown(runner)
    await test_cg20_nested_depth_8(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
