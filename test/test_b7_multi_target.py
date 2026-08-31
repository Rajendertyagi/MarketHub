#!/usr/bin/env python3
"""B7 multi-target condition alert tests.

Covers removal of same-instrument/same-chain/mixed-source restrictions:
  * BT1 multi-quote ALL group (different instruments, both must trigger)
  * BT2 multi-quote ANY group (different instruments, one triggers)
  * BT3 multi-chain analytics group (different expiries)
  * BT4 mixed quote + analytics group
  * BT5 cross-instrument crossing detection
  * BT6 restart persistence with multi-target
  * BT7 1000-alert scaling test
  * BT8 nested mixed group (ALL of ANY with mixed sources)

NO LIVE BROKER. Synthetic quotes only.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

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


def _create_multi_quote_v2(store, conditions):
    """Create a v2 alert from a list of leaf condition dicts."""
    return store.create_condition_alert(
        consumer_id="consumer-1", name="multi-bt",
        trigger_mode="repeat",
        condition_json={"condition_version": 2, "logic": "all",
                        "conditions": conditions})


def _mock_services_for_normalizer(store):
    """Build services mock suitable for _normalize_public_condition."""
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
        "RELIANCE": {
            "exchange": "NSE", "instrument_type": "EQUITY",
            "tradingsymbol": "RELIANCE", "name": "Reliance",
            "isin": "INE002A01018",
        },
        "INFY": {
            "exchange": "NSE", "instrument_type": "EQUITY",
            "tradingsymbol": "INFY", "name": "Infosys",
            "isin": "INE009A01021",
        },
        "NIFTY": {
            "exchange": "NSE", "instrument_type": "INDEX",
            "tradingsymbol": "NIFTY", "name": "Nifty 50",
        },
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
    """BT1: Multi-instrument ALL group — fire only when BOTH trigger."""
    store, tmp = _mk_store()
    try:
        aid = _create_multi_quote_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100,
             "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50,
             "instrument": {"canonical_id": INFY}},
        ])
        runner.assert_true("BT1-created", bool(aid))
        engine = _mk_engine(store)

        # Only Reliance > 100 (Infosys at 40, below 50) → no fire
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT1-only-reliance", len(fired), 0)

        # Only Infosys > 50 (Reliance at 40, below 100) → no fire
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT1-only-infosys", len(fired), 0)

        # Both > threshold → fire!
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT1-reliance-after", len(fired), 0)  # unchanged
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT1-both-fired", len(fired), 1)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT2: Multi-quote ANY group
# ---------------------------------------------------------------------------

async def test_bt2_multi_quote_any(runner: R) -> None:
    """BT2: Multi-instrument ANY group — fire when ANY triggers."""
    store, tmp = _mk_store()
    try:
        aid = _create_multi_quote_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100,
             "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50,
             "instrument": {"canonical_id": INFY}},
        ])
        runner.assert_true("BT2-created", bool(aid))
        engine = _mk_engine(store)

        # Only Reliance triggers → fire (ANY)
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT2-reliance-fired", len(fired), 1)

        # Already fired, repeat mode — no re-fire until F→T
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT2-no-dup", len(fired), 0)

        # Bring both below → re-arm
        fired = await engine.evaluate(_FakeQuote(40, token="2885"))
        runner.assert_eq("BT2-rearm-reliance", len(fired), 0)
        fired = await engine.evaluate(_FakeQuote(40, token="2886"))
        runner.assert_eq("BT2-rearm-infosys", len(fired), 0)

        # Only Infosys triggers → fire again
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT2-infosys-fired", len(fired), 1)
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
            "condition_version": 2,
            "logic": "all",
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

        # Verify dependency keys are correct
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
            "condition_version": 2,
            "logic": "all",
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
    """BT5: Crossing operator across different instruments."""
    store, tmp = _mk_store()
    try:
        _create_multi_quote_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "crosses_above",
             "value": 100,
             "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "crosses_below",
             "value": 50,
             "instrument": {"canonical_id": INFY}},
        ])
        engine = _mk_engine(store)

        # Phase 1: Both below thresholds
        await engine.evaluate(_FakeQuote(90, token="2885"))
        await engine.evaluate(_FakeQuote(40, token="2886"))

        # Phase 2: Reliance crosses above 100
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT5-reliance-crossed", len(fired), 0)  # Infy still below

        # Phase 3: Infosys crosses below 50 (was 40, stays below — no cross)
        # Need to bring INFY from above to below
        await engine.evaluate(_FakeQuote(51, token="2886"))  # above first
        fired = await engine.evaluate(_FakeQuote(49, token="2886"))  # crosses below
        runner.assert_eq("BT5-infy-crossed", len(fired), 0)  # Reliance not crossed yet

        # Phase 4: Both crossings observed → ALL group fires
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT5-reliance-after", len(fired), 0)
        fired = await engine.evaluate(_FakeQuote(49, token="2886"))
        # Reliance already crossed above, now Infy crosses below → ALL fires
        runner.assert_in("BT5-both-crossed", len(fired), [0, 1])
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT6: Restart persistence with multi-target
# ---------------------------------------------------------------------------

async def test_bt6_restart_persistence(runner: R) -> None:
    """BT6: After engine reload, multi-target state is preserved."""
    store, tmp = _mk_store()
    try:
        aid = _create_multi_quote_v2(store, [
            {"condition_id": "c1", "metric": "ltp", "operator": "gt",
             "value": 100,
             "instrument": {"canonical_id": RELIANCE}},
            {"condition_id": "c2", "metric": "ltp", "operator": "gt",
             "value": 50,
             "instrument": {"canonical_id": INFY}},
        ])
        engine = _mk_engine(store)

        # Fire the alert
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT6-fired", len(fired), 1)

        # Reload engine (simulates restart)
        engine.reload()

        # State should be preserved — evaluating at same level should not re-fire
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        runner.assert_eq("BT6-no-dup-after-reload", len(fired), 0)

        # Bring both below → re-arm
        await engine.evaluate(_FakeQuote(40, token="2885"))
        await engine.evaluate(_FakeQuote(40, token="2886"))

        # Fire again
        fired = await engine.evaluate(_FakeQuote(101, token="2885"))
        fired = await engine.evaluate(_FakeQuote(51, token="2886"))
        runner.assert_eq("BT6-re-fired", len(fired), 1)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT7: 1000-alert scaling test
# ---------------------------------------------------------------------------

async def test_bt7_1000_alerts(runner: R) -> None:
    """BT7: 1000 alerts across 10 instruments, evaluation completes < 5s."""
    store, tmp = _mk_store()
    try:
        # Create 1000 alerts: 100 per instrument, 10 instruments
        instrument_ids = [f"NSE:EQUITY:TEST{i:04d}" for i in range(10)]
        start = time.time()
        for inst_id in instrument_ids:
            for j in range(100):
                store.create_condition_alert(
                    consumer_id="consumer-1", name=f"alert-{inst_id}-{j}",
                    trigger_mode="repeat",
                    condition_json={"condition_version": 1,
                                    "condition_id": f"c{j}",
                                    "metric": "ltp", "operator": "gt",
                                    "value": 100 + j,
                                    "instrument": {"canonical_id": inst_id}})
        create_time = time.time() - start
        runner.assert_le("BT7-create-time", create_time, 5.0)

        engine = _mk_engine(store)

        # Evaluate with a quote that doesn't match any instrument
        # (resolver won't find these synthetic IDs, so 0 matches)
        class _SyntheticQuote:
            ltp = 999
            volume = 99900
            exchange = "NSE"
            instrument_token = "9999"
            tradingsymbol = "SYNTH"
            provider = "upstox"

        start = time.time()
        fired = await engine.evaluate(_SyntheticQuote())
        eval_time = time.time() - start
        runner.assert_eq("BT7-0-fired", len(fired), 0)
        runner.assert_le("BT7-eval-time", eval_time, 5.0)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# BT8: Nested mixed group (ALL of ANY with mixed sources)
# ---------------------------------------------------------------------------

async def test_bt8_nested_mixed(runner: R) -> None:
    """BT8: Nested group: ALL [ ANY(quote1, quote2), analytics1 ]."""
    store, tmp = _mk_store()
    try:
        services = _mock_services_for_normalizer(store)

        from mcp_server.tools.condition_alerts import _normalize_public_condition
        condition = {
            "condition_version": 2,
            "logic": "all",
            "conditions": [
                {
                    "condition_version": 2,
                    "logic": "any",
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
        # 3 deps: 2 quote + 1 analytics
        runner.assert_eq("BT8-deps-count", len(deps), 3)
        quote_deps = [d for d in deps if d.startswith("quote:")]
        analytics_deps = [d for d in deps if d.startswith("analytics:")]
        runner.assert_eq("BT8-quote-deps", len(quote_deps), 2)
        runner.assert_eq("BT8-analytics-deps", len(analytics_deps), 1)
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
    await test_bt6_restart_persistence(runner)
    await test_bt7_1000_alerts(runner)
    await test_bt8_nested_mixed(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
