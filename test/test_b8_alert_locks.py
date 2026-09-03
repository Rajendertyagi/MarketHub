#!/usr/bin/env python3
"""B8 FIX 1 — _alert_locks retention tests.

Verifies that per-alert locks are cleaned up during reload() when
alerts are deleted/disabled, and that repeated create/delete cycles
do not grow _alert_locks unboundedly.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import uuid

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R
from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver


CANONICAL_ID = "NSE:EQUITY:INE002A01018"


def _mk_store():
    tmp = tempfile.mkdtemp(prefix="b8locks_")
    store = EventStore(os.path.join(tmp, "events.db"))
    store.register_consumer("consumer-1")
    store.replace_provider_instruments("upstox", [
        {"exchange": "NSE", "instrument_token": "2885",
         "tradingsymbol": "RELIANCE", "name": "Reliance Industries",
         "instrument_type": "EQ", "segment": "NSE",
         "isin": "INE002A01018"},
        {"exchange": "NSE", "instrument_token": "4078",
         "tradingsymbol": "INFY", "name": "Infosys",
         "instrument_type": "EQ", "segment": "NSE",
         "isin": "INE009A01021"},
    ])
    return store, tmp


def _make_resolver(store):
    resolver = MarketInstrumentIdentityResolver()
    resolver.register_catalog_rows(store.list_all_instruments())
    return resolver


def _create_alert(store, canonical_id=CANONICAL_ID, threshold=25000.0):
    """Create a condition alert and return its alert_id."""
    return store.create_condition_alert(
        consumer_id="consumer-1",
        name=f"test-{canonical_id[:10]}",
        trigger_mode="repeat",
        condition_json={
            "condition_version": 1,
            "condition_id": f"cond-{uuid.uuid4().hex[:8]}",
            "metric": "ltp",
            "operator": "gt",
            "value": threshold,
            "instrument": {"canonical_id": canonical_id},
        },
    )


class _FakeQuote:
    def __init__(self, ltp, token="2885", exchange="NSE",
                 tradingsymbol="RELIANCE"):
        self.ltp = ltp
        self.exchange = exchange
        self.tradingsymbol = tradingsymbol
        self.instrument_token = token
        self.provider = "upstox"
        self.open = self.high = self.low = self.close = None
        self.change = self.change_percent = None
        self.avg_trade_price = self.last_traded_qty = None
        self.volume = self.total_buy_qty = self.total_sell_qty = None
        self.open_interest = self.previous_oi = None
        self.oi_change = self.oi_change_percent = None
        self.best_bid = self.best_ask = None
        self.upper_circuit = self.lower_circuit = None
        self.greeks = None


# ===================================================================
# Tests
# ===================================================================


async def t1_lock_count_after_delete(runner: R) -> None:
    """After deleting an alert, its lock should be cleaned up on reload."""
    name = "T1-lock-after-delete"
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver)
        aid = _create_alert(store)
        engine.reload()
        # Trigger lock creation by evaluating
        await engine.evaluate(_FakeQuote(100))
        locks_before = len(engine._alert_locks)
        runner.assert_eq(name + "-locks-after-create", locks_before, 1)

        store.delete_condition_alert(aid)
        engine.reload()
        locks_after = len(engine._alert_locks)
        runner.assert_eq(name + "-locks-after-delete", locks_after, 0)
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def t2_repeated_create_delete_no_growth(runner: R) -> None:
    """Repeated create/delete cycles must not grow _alert_locks unboundedly."""
    name = "T2-no-growth"
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver)

        for cycle in range(3):
            alert_ids = []
            for i in range(50):
                aid = _create_alert(store, threshold=20000.0 + i)
                alert_ids.append(aid)
            engine.reload()
            await engine.evaluate(_FakeQuote(100))

            for aid in alert_ids:
                store.delete_condition_alert(aid)
            engine.reload()

        # After 3 cycles of 50 creates + deletes, locks should be at baseline
        lock_count = len(engine._alert_locks)
        runner.assert_true(
            name + "-bounded",
            lock_count <= 1,
            f"expected <= 1 lock, got {lock_count}",
        )
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def t3_concurrent_evaluate_during_delete(runner: R) -> None:
    """Concurrent evaluate + delete must not raise or corrupt state."""
    name = "T3-concurrent-delete"
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver)
        aid1 = _create_alert(store, canonical_id=CANONICAL_ID)
        aid2 = _create_alert(store, canonical_id="NSE:EQUITY:INE009A01021")
        engine.reload()

        async def eval_a1():
            return await engine.evaluate(_FakeQuote(100, "2885"))

        async def delete_a1():
            store.delete_condition_alert(aid1)
            engine.reload()

        # Run evaluate and delete concurrently
        results = await asyncio.gather(
            eval_a1(), delete_a1(), return_exceptions=True
        )
        # No exception should propagate
        for r in results:
            if isinstance(r, Exception):
                runner.fail(name, f"unexpected exception: {r}")

        # Engine should be in a consistent state
        engine.reload()
        runner.assert_true(
            name + "-a1-gone",
            aid1 not in engine._alerts,
            "aid1 should be deleted",
        )
        runner.assert_true(
            name + "-a2-exists",
            aid2 in engine._alerts,
            "aid2 should still exist",
        )
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def t4_reload_preserves_existing_locks(runner: R) -> None:
    """reload() should keep locks for alerts that still exist."""
    name = "T4-preserves-locks"
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver)
        aid1 = _create_alert(store, canonical_id=CANONICAL_ID)
        aid2 = _create_alert(store, canonical_id="NSE:EQUITY:INE009A01021")
        engine.reload()
        # Evaluate both quotes to create locks for both alerts
        await engine.evaluate(_FakeQuote(100, "2885"))
        await engine.evaluate(_FakeQuote(100, "4078"))
        locks_before = len(engine._alert_locks)
        runner.assert_eq(name + "-two-locks", locks_before, 2)

        # Delete only aid1, reload — aid2's lock should remain
        store.delete_condition_alert(aid1)
        engine.reload()
        locks_after = len(engine._alert_locks)
        runner.assert_eq(name + "-a2-lock-kept", locks_after, 1)
        runner.assert_true(
            name + "-a2-lock-key",
            aid2 in engine._alert_locks,
            "aid2 lock should still be present",
        )
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


async def t5_baseline_lock_count(runner: R) -> None:
    """Fresh engine with no alerts should have zero locks."""
    name = "T5-baseline"
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver)
        runner.assert_eq(name, len(engine._alert_locks), 0)
    finally:
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# Main
# ===================================================================

async def main() -> int:
    runner = R()
    try:
        print("  B8 FIX 1 — Alert Locks Retention Tests")
        print("=" * 50)
        tests = [
            t1_lock_count_after_delete,
            t2_repeated_create_delete_no_growth,
            t3_concurrent_evaluate_during_delete,
            t4_reload_preserves_existing_locks,
            t5_baseline_lock_count,
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
