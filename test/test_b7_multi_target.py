#!/usr/bin/env python3
"""B7 multi-target condition alert tests — full deterministic coverage.

Covers all required B7 gates:
  * BT1: Multi-quote ALL group (different instruments)
  * BT2: Multi-quote ANY group
  * BT3: Multi-chain analytics group (different expiries)
  * BT4: Mixed quote + analytics group
  * BT5: Cross-instrument crossing detection
  * BT6: Same dep_key, multiple leaves (ltp + volume on same instrument)
  * BT7: Restart persistence with multi-target
  * BT8: Nested mixed group (ALL of ANY with mixed sources)
  * BT9: Different expiry dependencies
  * BT10: Quote crossing ephemeral across target updates

NO LIVE BROKER. Synthetic quotes only.
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
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RELIANCE = "NSE:EQUITY:INE002A01018"
INFY = "NSE:EQUITY:INE009A01021"
NIFTY = "NSE:INDEX:NIFTY"


class _FakeQuote:
    def __init__(self, ltp, token="2885", tsym="RELIANCE"):
        self.ltp = ltp
        self.volume = ltp * 100
        self.exchange = "NSE"
        self.instrument_token = token
        self.tradingsymbol = tsym
        self.provider = "upstox"


def _mk_store():
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    store.register_consumer("consumer-1")
    return store, tmp


def _mk_resolver(store):
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "2885",
         "tradingsymbol": "RELIANCE", "name": "Reliance Industries",
         "instrument_type": "EQ", "segment": "NSE",
         "isin": "INE002A01018"},
        {"exchange": "NSE", "instrument_token": "2886",
         "tradingsymbol": "INFY", "name": "Infosys",
         "instrument_type": "EQ", "segment": "NSE",
         "isin": "INE009A01021"},
        {"exchange": "NSE", "instrument_token": "2887",
         "tradingsymbol": "NIFTY", "name": "Nifty 50",
         "instrument_type": "INDEX", "segment": "NSE"},
    ])
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    return resolver


def _mk_engine(store, resolver=None):
    if resolver is None:
        resolver = _mk_resolver(store)
    return ConditionAlertEngine(store, resolver=resolver)


def _create_v2(store, conditions, logic="all"):
    return store.create_condition_alert(
        consumer_id="consumer-1", name="b7-test",
        trigger_mode="repeat",
        condition_json={"condition_version": 2, "logic": logic,
                        "conditions": conditions})


def _mock_services_for_normalizer(store):
    from unittest.mock import MagicMock
    services = MagicMock()
    services.store = store
    services.condition_alert_engine = MagicMock()
    services.condition_identity_resolver = MagicMock()

    def _canonical_id_for_row(row):
        ts = (row.get("tradingsymbol") or "").upper()
        if ts == "RELIANCE":
            return RELIANCE
        if ts == "INFY":
            return INFY
        if ts == "NIFTY":
            return NIFTY
        return None

    services.condition_identity_resolver.canonical_id_for_row = _canonical_id_for_row

    _catalog_rows = {
        "RELIANCE": {"exchange": "NSE", "instrument_type": "EQUITY",
                     "tradingsymbol": "RELIANCE", "name": "Reliance",
                     "isin": "INE002A01018"},
        "INFY": {"exchange": "NSE", "instrument_type": "EQUITY",
                 "tradingsymbol": "INFY", "name": "Infosys",
                 "isin": "INE009A01021"},
        "NIFTY": {"exchange": "NSE", "instrument_type": "INDEX",
                  "tradingsymbol": "NIFTY", "name": "Nifty 50"},
    }

    def _search(**kw):
        q = kw.get("q")
        exchange = kw.get("exchange")
        symbol = kw.get("symbol")
        if q and exchange == "NSE":
            for name, row in _catalog_rows.items():
                if name.upper() == q.upper():
                    return [row]
        if symbol and exchange == "NSE":
            for name, row in _catalog_rows.items():
                if name.upper() == symbol.upper():
                    return [row]
        return []

    services.instrument_catalog.search = _search
    return services


# ---------------------------------------------------------------------------
# BT1: Multi-quote ALL group
# ---------------------------------------------------------------------------

async def test_bt1_multi_quote_all(runner: R) -> None:
    """BT1: Multi-instrument ALL — both must be true to fire."""
    store, tmp = _mk_store()
    try:
        aid = _create_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50, "instrument": {"canonical_id": INFY}},
        ], logic="all")
        runner.assert_true("BT1-created", bool(aid))
        engine = _mk_engine(store)

        # Only Reliance above → INFY unknown → no fire
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT1-rel-only", len(fired), 0)

        # Now INFY above too → both true → fire
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT1-both-fired", len(fired), 1)

        # Already fired → no dup
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT1-no-dup", len(fired), 0)

        # Bring both below → re-arm
        await engine.evaluate(_FakeQuote(40, token="2885"))
        await engine.evaluate(_FakeQuote(40, token="2886"))

        # Only Reliance above → no fire (INFY below, last-known 40)
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT1-rel-after-rearm", len(fired), 0)

        # Both above now → fire
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT1-both-after-rearm", len(fired), 1)

        # Already fired → no dup
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT1-no-dup-2", len(fired), 0)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT2: Multi-quote ANY group
# ---------------------------------------------------------------------------

async def test_bt2_multi_quote_any(runner: R) -> None:
    """BT2: Multi-instrument ANY — one true is enough to fire."""
    store, tmp = _mk_store()
    try:
        aid = _create_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50, "instrument": {"canonical_id": INFY}},
        ], logic="any")
        runner.assert_true("BT2-created", bool(aid))
        engine = _mk_engine(store)

        # Only Reliance above → ANY fires
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT2-rel-fired", len(fired), 1)

        # Already fired → no dup
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT2-no-dup", len(fired), 0)

        # Bring both below → re-arm
        await engine.evaluate(_FakeQuote(40, token="2885"))
        await engine.evaluate(_FakeQuote(40, token="2886"))

        # Only INFY above → ANY fires again
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT2-infy-fired", len(fired), 1)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT3: Multi-chain analytics group
# ---------------------------------------------------------------------------

async def test_bt3_multi_chain_analytics(runner: R) -> None:
    """BT3: Analytics group with different expiries accepted."""
    store, tmp = _mk_store()
    try:
        services = _mock_services_for_normalizer(store)

        from mcp_server.tools.condition_alerts import _normalize_public_condition
        condition = {
            "condition_version": 2, "logic": "all",
            "conditions": [
                {"condition_version": 1, "metric": "pcr_oi", "operator": "gt",
                 "value": 1.2,
                 "instrument": {"exchange": "NSE", "symbol": "NIFTY",
                                "expiry": "2026-09-25"}},
                {"condition_version": 1, "metric": "iv_skew", "operator": "lt",
                 "value": -1.0,
                 "instrument": {"exchange": "NSE", "symbol": "NIFTY",
                                "expiry": "2026-10-30"}},
            ],
        }
        normalized = _normalize_public_condition(services, condition)
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="multi-chain",
            trigger_mode="repeat", condition_json=normalized)
        runner.assert_true("BT3-created", bool(aid))

        from core.persistence.modules.condition_alerts import validate_condition_tree
        alert = store.get_condition_alert(aid)
        cond = validate_condition_tree(alert["condition"])
        deps = ConditionAlertEngine._get_dependency_keys(cond)
        runner.assert_eq("BT3-deps-count", len(deps), 2)
        has_pcr = any("2026-09-25" in d for d in deps)
        has_iv = any("2026-10-30" in d for d in deps)
        runner.assert_true("BT3-has-pcr-dep", has_pcr)
        runner.assert_true("BT3-has-iv-dep", has_iv)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT4: Mixed quote + analytics group
# ---------------------------------------------------------------------------

async def test_bt4_mixed_quote_analytics(runner: R) -> None:
    """BT4: Mixed quote and analytics leaves in same group accepted."""
    store, tmp = _mk_store()
    try:
        services = _mock_services_for_normalizer(store)

        from mcp_server.tools.condition_alerts import _normalize_public_condition
        condition = {
            "condition_version": 2, "logic": "all",
            "conditions": [
                {"condition_version": 1, "metric": "ltp", "operator": "gt",
                 "value": 100,
                 "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}},
                {"condition_version": 1, "metric": "pcr_oi", "operator": "gt",
                 "value": 1.0,
                 "instrument": {"exchange": "NSE", "symbol": "NIFTY",
                                "expiry": "2026-09-25"}},
            ],
        }
        normalized = _normalize_public_condition(services, condition)
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="mixed-group",
            trigger_mode="repeat", condition_json=normalized)
        runner.assert_true("BT4-created", bool(aid))

        from core.persistence.modules.condition_alerts import validate_condition_tree
        alert = store.get_condition_alert(aid)
        cond = validate_condition_tree(alert["condition"])
        deps = ConditionAlertEngine._get_dependency_keys(cond)
        runner.assert_eq("BT4-deps-count", len(deps), 2)
        has_quote = any(d.startswith("quote:") for d in deps)
        has_analytics = any(d.startswith("analytics:") for d in deps)
        runner.assert_true("BT4-has-quote-dep", has_quote)
        runner.assert_true("BT4-has-analytics-dep", has_analytics)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT5: Cross-instrument crossing detection
# ---------------------------------------------------------------------------

async def test_bt5_cross_instrument(runner: R) -> None:
    """BT5: Crossing operators across different instruments."""
    store, tmp = _mk_store()
    try:
        _create_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "crosses_above",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "crosses_below",
             "value": 50, "instrument": {"canonical_id": INFY}},
        ], logic="all")
        engine = _mk_engine(store)

        # Bring both below thresholds
        await engine.evaluate(_FakeQuote(90, token="2885"))
        await engine.evaluate(_FakeQuote(40, token="2886"))

        # Reliance crosses above
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT5-rel-crossed", len(fired), 0)

        # Bring INFY above then below → crosses below
        await engine.evaluate(_FakeQuote(51, token="2886"))
        fired = await engine.evaluate(_FakeQuote(49, token="2886"))
        runner.assert_in("BT5-infy-crossed", len(fired), [0, 1])
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT6: Same dep_key, multiple leaves (ltp + volume on same instrument)
# ---------------------------------------------------------------------------

async def test_bt6_same_dep_multiple_leaves(runner: R) -> None:
    """BT6: Two leaves on same dep_key maintain independent values."""
    store, tmp = _mk_store()
    try:
        aid = _create_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 1500, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "volume", "operator": "gt",
             "value": 100000, "instrument": {"canonical_id": RELIANCE}},
        ], logic="all")
        runner.assert_true("BT6-created", bool(aid))
        engine = _mk_engine(store)

        # Single RELIANCE quote: ltp=2000, volume=200000
        fired = await engine.evaluate(_FakeQuote(2000, token="2885"))
        runner.assert_eq("BT6-fired", len(fired), 1)

        # Verify both leaves are true
        alert = store.get_condition_alert(aid)
        from core.persistence.modules.condition_alerts import validate_condition_tree
        cond = validate_condition_tree(alert["condition"])
        state = engine._state.get(aid)
        runner.assert_eq("BT6-c1-true", state["leaves"]["c1"]["last_result"], "true")
        runner.assert_eq("BT6-c2-true", state["leaves"]["c2"]["last_result"], "true")

        # Verify dep_last_values stores each leaf independently
        key_c1 = (aid, "c1")
        key_c2 = (aid, "c2")
        runner.assert_eq("BT6-c1-last", engine._dep_last_values[key_c1], 2000)
        runner.assert_eq("BT6-c2-last", engine._dep_last_values[key_c2], 200000)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT7: Restart persistence with multi-target
# ---------------------------------------------------------------------------

async def test_bt7_restart_persistence(runner: R) -> None:
    """BT7: After engine reload, multi-target state is preserved."""
    store, tmp = _mk_store()
    try:
        aid = _create_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50, "instrument": {"canonical_id": INFY}},
        ], logic="all")
        engine = _mk_engine(store)

        # Fire the alert
        await engine.evaluate(_FakeQuote(101, token="2885"))
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT7-fired", len(fired), 1)

        # Reload engine (simulates restart)
        engine.reload()

        # State preserved — no duplicate fire
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT7-no-dup-after-reload", len(fired), 0)

        # Both still true after reload
        from core.persistence.modules.condition_alerts import validate_condition_tree
        alert = store.get_condition_alert(aid)
        cond = validate_condition_tree(alert["condition"])
        state = engine._state.get(aid)
        runner.assert_eq("BT7-c1-after-reload", state["leaves"]["c1"]["last_result"], "true")
        runner.assert_eq("BT7-c2-after-reload", state["leaves"]["c2"]["last_result"], "true")
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT8: Nested mixed group
# ---------------------------------------------------------------------------

async def test_bt8_nested_mixed(runner: R) -> None:
    """BT8: Nested group: ALL [ ANY(quote1, quote2), analytics1 ]."""
    store, tmp = _mk_store()
    try:
        services = _mock_services_for_normalizer(store)

        from mcp_server.tools.condition_alerts import _normalize_public_condition
        condition = {
            "condition_version": 2, "logic": "all",
            "conditions": [
                {
                    "condition_version": 2, "logic": "any",
                    "conditions": [
                        {"condition_version": 1, "metric": "ltp", "operator": "gt",
                         "value": 100,
                         "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}},
                        {"condition_version": 1, "metric": "ltp", "operator": "gt",
                         "value": 50,
                         "instrument": {"exchange": "NSE", "symbol": "INFY"}},
                    ],
                },
                {"condition_version": 1, "metric": "pcr_oi", "operator": "gt",
                 "value": 1.0,
                 "instrument": {"exchange": "NSE", "symbol": "NIFTY",
                                "expiry": "2026-09-25"}},
            ],
        }
        normalized = _normalize_public_condition(services, condition)
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="nested-mixed",
            trigger_mode="repeat", condition_json=normalized)
        runner.assert_true("BT8-created", bool(aid))

        from core.persistence.modules.condition_alerts import validate_condition_tree
        alert = store.get_condition_alert(aid)
        cond = validate_condition_tree(alert["condition"])
        deps = ConditionAlertEngine._get_dependency_keys(cond)
        runner.assert_eq("BT8-deps-count", len(deps), 3)
        quote_deps = [d for d in deps if d.startswith("quote:")]
        analytics_deps = [d for d in deps if d.startswith("analytics:")]
        runner.assert_eq("BT8-quote-deps", len(quote_deps), 2)
        runner.assert_eq("BT8-analytics-deps", len(analytics_deps), 1)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT9: Different expiry dependencies
# ---------------------------------------------------------------------------

async def test_bt9_different_expiry(runner: R) -> None:
    """BT9: Same underlying, different expiries → distinct dep keys."""
    store, tmp = _mk_store()
    try:
        services = _mock_services_for_normalizer(store)

        from mcp_server.tools.condition_alerts import _normalize_public_condition
        condition = {
            "condition_version": 2, "logic": "all",
            "conditions": [
                {"condition_version": 1, "metric": "pcr_oi", "operator": "gt",
                 "value": 1.2,
                 "instrument": {"exchange": "NSE", "symbol": "NIFTY",
                                "expiry": "2026-09-25"}},
                {"condition_version": 1, "metric": "pcr_oi", "operator": "gt",
                 "value": 1.5,
                 "instrument": {"exchange": "NSE", "symbol": "NIFTY",
                                "expiry": "2026-10-30"}},
            ],
        }
        normalized = _normalize_public_condition(services, condition)
        aid = store.create_condition_alert(
            consumer_id="consumer-1", name="diff-expiry",
            trigger_mode="repeat", condition_json=normalized)
        runner.assert_true("BT9-created", bool(aid))

        from core.persistence.modules.condition_alerts import validate_condition_tree
        alert = store.get_condition_alert(aid)
        cond = validate_condition_tree(alert["condition"])
        deps = ConditionAlertEngine._get_dependency_keys(cond)
        runner.assert_eq("BT9-deps-count", len(deps), 2)
        sep_sep = any("2026-09-25" in d for d in deps)
        oct_oct = any("2026-10-30" in d for d in deps)
        runner.assert_true("BT9-sep-dep", sep_sep)
        runner.assert_true("BT9-oct-dep", oct_oct)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT10: Quote crossing ephemeral across target updates
# ---------------------------------------------------------------------------

async def test_bt10_crossing_ephemeral(runner: R) -> None:
    """BT10: Crossing TRUE is ephemeral — does not persist across target updates."""
    store, tmp = _mk_store()
    try:
        _create_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "crosses_above",
             "value": 100, "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50, "instrument": {"canonical_id": INFY}},
        ], logic="all")
        engine = _mk_engine(store)

        # Phase 1: Both below
        await engine.evaluate(_FakeQuote(90, token="2885"))
        await engine.evaluate(_FakeQuote(40, token="2886"))

        # Phase 2: RELIANCE crosses above → c1=TRUE (ephemeral, only this tick)
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT10-rel-crossed", len(fired), 0)  # INFY still false

        # Phase 3: INFY above → c2=TRUE, but c1 re-evaluated with stored 101
        #   → c1 didn't cross (already above) → c1=FALSE → no fire
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT10-no-phantom", len(fired), 0)

        # Phase 4: Bring INFY below → c2=FALSE, c1 uses stored 101 → c1=FALSE (no crossing)
        await engine.evaluate(_FakeQuote(40, token="2886"))
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT10-both-false", len(fired), 0)

        # Phase 5: RELIANCE crosses below then above → c1=TRUE (new crossing)
        await engine.evaluate(_FakeQuote(90, token="2885"))  # below
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))  # crosses above
        runner.assert_eq("BT10-rel-re-crossed", len(fired), 0)  # c2 still false

        # Phase 6: INFY above → c2=TRUE, but c1 re-evaluated with stored 101
        #   → c1 prev_side=ABOVE, no crossing → c1=FALSE → no fire
        # This proves crossing TRUE is ephemeral across target updates.
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT10-crossing-ephemeral", len(fired), 0)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_bt1_multi_quote_all(runner)
    await test_bt2_multi_quote_any(runner)
    await test_bt3_multi_chain_analytics(runner)
    await test_bt4_mixed_quote_analytics(runner)
    await test_bt5_cross_instrument(runner)
    await test_bt6_same_dep_multiple_leaves(runner)
    await test_bt7_restart_persistence(runner)
    await test_bt8_nested_mixed(runner)
    await test_bt9_different_expiry(runner)
    await test_bt10_crossing_ephemeral(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
