#!/usr/bin/env python3
"""B3 stress verification for the ConditionAlertEngine."""
from __future__ import annotations
import asyncio, os, sys, tempfile, uuid
from datetime import datetime, timezone
_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from helpers.runner import R
from core.persistence.store import EventStore
from app.condition_alerts import ConditionAlertEngine
from app.market_identity import MarketInstrumentIdentityResolver
from market.models import Quote

def _mk_store():
    tmp = tempfile.mkdtemp(prefix="stress_")
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

class _FakeQuote:
    """Minimal mock matching what the engine reads from Quote."""
    def __init__(self, ltp, exchange="NSE", tradingsymbol="RELIANCE",
                 instrument_token="1", provider="upstox"):
        self.ltp = ltp
        self.exchange = exchange
        self.tradingsymbol = tradingsymbol
        self.instrument_token = instrument_token
        self.provider = provider
        self.open = self.high = self.low = self.close = None
        self.change = self.change_percent = None
        self.avg_trade_price = self.last_traded_qty = None
        self.volume = self.total_buy_qty = self.total_sell_qty = None
        self.open_interest = self.previous_oi = None
        self.oi_change = self.oi_change_percent = None
        self.best_bid = self.best_ask = None
        self.upper_circuit = self.lower_circuit = None
        self.greeks = None

def _mk_quote(ltp, **kw):
    return _FakeQuote(ltp, **kw)

def _create_alert(store, *, consumer_id="consumer-1", canonical_id="NSE:EQUITY:INE002A01018",
                  metric="ltp", operator="gt", threshold=25000.0,
                  trigger_mode="repeat", condition_id=None):
    return store.create_condition_alert(
        consumer_id=consumer_id,
        name=f"test-{canonical_id[:10]}-{operator}-{threshold}",
        trigger_mode=trigger_mode,
        condition_json={
            "condition_version": 1,
            "condition_id": condition_id or f"cond-{uuid.uuid4().hex[:8]}",
            "metric": metric,
            "operator": operator,
            "value": threshold,
            "instrument": {"canonical_id": canonical_id},
        },
    )


async def test_100_alerts_one_instrument(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        alert_ids = [_create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", threshold=25000.0+i) for i in range(100)]
        engine.reload()
        assert len(engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set())) == 100
        quote = _mk_quote(26000.0)
        fired = await engine.evaluate(quote)
        runner.assert_eq("S1-100-fired", len(fired), 100)
        fired2 = await engine.evaluate(quote)
        runner.assert_eq("S1-no-dup", len(fired2), 0)
        for aid in alert_ids:
            a = store.get_condition_alert(aid)
            runner.assert_eq(f"S1-count-{aid[:6]}", a["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_500_alerts_one_instrument(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        alert_ids = [_create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", threshold=20000.0+i*10) for i in range(500)]
        engine.reload()
        assert len(engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set())) == 500
        quote = _mk_quote(25000.0)
        fired = await engine.evaluate(quote)
        runner.assert_eq("S2-fired", len(fired), 500)
        fired2 = await engine.evaluate(quote)
        runner.assert_eq("S2-no-dup", len(fired2), 0)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_1000_alerts_indexed(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        reliance_ids = [_create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", threshold=25000.0+i) for i in range(10)]
        other_ids = [_create_alert(store, canonical_id="NSE:EQUITY:INE009A01021", threshold=1500.0+i) for i in range(990)]
        engine.reload()
        assert len(engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set())) == 10
        assert len(engine._dep_index.get(f"quote:NSE:EQUITY:INE009A01021", set())) == 990
        quote = _mk_quote(26000.0)
        fired = await engine.evaluate(quote)
        runner.assert_eq("S3-reliance-fired", len(fired), 10)
        for oid in other_ids:
            a = store.get_condition_alert(oid)
            runner.assert_eq(f"S3-infy-{oid[:6]}", a["trigger_count"], 0)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_burst_duplicate_race(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        quote = _mk_quote(26000.0)
        fired = await asyncio.gather(*[engine.evaluate(quote) for _ in range(100)])
        total = sum(len(f) for f in fired)
        runner.assert_eq("S4-total-fired", total, 1)
        a = store.get_condition_alert(aid)
        runner.assert_eq("S4-trigger_count", a["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_multi_alert_concurrency(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        alert_ids = [_create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", threshold=20000.0+i*100) for i in range(50)]
        engine.reload()
        quote = _mk_quote(25000.0)
        fired = await engine.evaluate(quote)
        runner.assert_eq("S5-fired", len(fired), 50)
        fired2 = await engine.evaluate(quote)
        runner.assert_eq("S5-no-dup", len(fired2), 0)
        for aid in alert_ids:
            a = store.get_condition_alert(aid)
            runner.assert_eq(f"S5-count-{aid[:6]}", a["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_multi_instrument_concurrency(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        ids_rel = [_create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", threshold=25000.0+i) for i in range(20)]
        ids_infy = [_create_alert(store, canonical_id="NSE:EQUITY:INE009A01021", threshold=1500.0+i) for i in range(20)]
        engine.reload()
        q_rel = _mk_quote(26000.0)
        f_rel = await engine.evaluate(q_rel)
        q_inf = _mk_quote(1520.0, tradingsymbol="INFY", instrument_token="4078")
        f_inf = await engine.evaluate(q_inf)
        runner.assert_eq("S6-reliance-fired", len(f_rel), 20)
        runner.assert_eq("S6-infy-fired", len(f_inf), 20)
        for aid in ids_rel:
            a = store.get_condition_alert(aid)
            runner.assert_eq(f"S6-rel-{aid[:6]}", a["trigger_count"], 1)
        for aid in ids_infy:
            a = store.get_condition_alert(aid)
            runner.assert_eq(f"S6-inf-{aid[:6]}", a["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_level_repeat_under_load(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        seq = [24000, 26000, 26000, 24000, 26000]
        expected = [0, 1, 0, 0, 1]
        for i, ltp in enumerate(seq):
            q = _mk_quote(float(ltp))
            fired = await engine.evaluate(q)
            runner.assert_eq(f"S7-fire-{i}", len(fired), expected[i])
        a = store.get_condition_alert(aid)
        runner.assert_eq("S7-trigger_count", a["trigger_count"], 2)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_unknown_no_rearm(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        q1 = _mk_quote(26000.0)
        fired1 = await engine.evaluate(q1)
        runner.assert_eq("S8-t1", len(fired1), 1)
        q2 = _mk_quote(None)
        fired2 = await engine.evaluate(q2)
        runner.assert_eq("S8-unknown-no-fire", len(fired2), 0)
        q3 = _mk_quote(26000.0)
        fired3 = await engine.evaluate(q3)
        runner.assert_eq("S8-true-after-unknown-no-fire", len(fired3), 0)
        a = store.get_condition_alert(aid)
        runner.assert_eq("S8-trigger_count", a["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_crossing_under_load(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        # crosses_above alert
        aid_above = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="crosses_above", threshold=25000.0, trigger_mode="repeat")
        # crosses_below alert
        aid_below = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="crosses_below", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        q1 = _mk_quote(24000.0)
        fired1 = await engine.evaluate(q1)
        # Frozen contract: the first valid observation establishes the side and
        # NEVER fires. crosses_below with value already below threshold is the
        # baseline (side = below_or_equal), not a crossing event.
        runner.assert_eq("S9-first-no-fire", len(fired1), 0)
        q2 = _mk_quote(26000.0)
        fired2 = await engine.evaluate(q2)
        runner.assert_eq("S9-cross-above", len(fired2), 1)
        q3 = _mk_quote(27000.0)
        fired3 = await engine.evaluate(q3)
        runner.assert_eq("S9-stay-above", len(fired3), 0)
        q4 = _mk_quote(24000.0)
        fired4 = await engine.evaluate(q4)
        runner.assert_eq("S9-cross-below", len(fired4), 1)
        a = store.get_condition_alert(aid_above)
        runner.assert_eq("S9-trigger_count_above", a["trigger_count"], 1)
        b = store.get_condition_alert(aid_below)
        # aid_below establishes its side on the first observation (no fire) and
        # fires only once on the genuine above->below crossing at q4.
        runner.assert_eq("S9-trigger_count_below", b["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_write_amplification_level(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        q = _mk_quote(26000.0)
        await engine.evaluate(q)
        a = store.get_condition_alert(aid)
        runner.assert_eq("S10-trigger", a["trigger_count"], 1)
        for _ in range(1000):
            await engine.evaluate(q)
        a2 = store.get_condition_alert(aid)
        runner.assert_eq("S10-no-extra-trigger", a2["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_write_amplification_crossing(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="crosses_above", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        q1 = _mk_quote(24000.0)
        await engine.evaluate(q1)
        for _ in range(1000):
            await engine.evaluate(q1)
        state = store.load_condition_runtime_state()
        runner.assert_in("S11-state-below",
                         next(iter(state[aid].values()))["crossing_side"],
                         ["below_or_equal"])
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_restart_stress(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        # Create 50 alerts all on same instrument with same threshold
        level_ids = [_create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat") for _ in range(50)]
        engine1 = ConditionAlertEngine(store, resolver=resolver)
        engine1.reload()
        # Fire all 50 with one quote
        q = _mk_quote(26000.0)
        fired = await engine1.evaluate(q)
        runner.assert_eq("S12-fired-count", len(fired), 50)
        # Verify all have trigger_count=1
        for aid in level_ids:
            a = store.get_condition_alert(aid)
            runner.assert_eq(f"S12-count-{aid[:6]}", a["trigger_count"], 1)
        # Simulate restart
        engine2 = ConditionAlertEngine(store, resolver=resolver)
        engine2.reload()
        # Re-evaluate same quote — no duplicate fires (TRUE→TRUE)
        fired2 = await engine2.evaluate(q)
        runner.assert_eq("S12-no-dup-after-restart", len(fired2), 0)
        # Verify counts unchanged
        for aid in level_ids:
            a = store.get_condition_alert(aid)
            runner.assert_eq(f"S12-count-after-restart-{aid[:6]}", a["trigger_count"], 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_enable_disable_delete_index(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        assert aid in engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set())
        store.set_condition_alert_enabled(aid, False)
        engine.reload()
        runner.assert_true("S13-disabled-not-indexed", aid not in engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set()))
        store.set_condition_alert_enabled(aid, True)
        engine.reload()
        runner.assert_true("S13-re-enabled-indexed", aid in engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set()))
        store.delete_condition_alert(aid)
        engine.reload()
        runner.assert_true("S13-deleted-not-indexed", not any(aid in v for v in engine._dep_index.values()))
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_malformed_row_startup(runner):
    import sqlite3
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        conn = sqlite3.connect(os.path.join(tmp, "events.db"))
        bad_id = "bad-alert-" + uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO condition_alerts (alert_id, consumer_id, name, enabled, trigger_mode, condition_json, canonical_instrument_id, metadata_json, created_at, updated_at, last_triggered_at, trigger_count) VALUES (?, ?, ?, 1, 'repeat', ?, ?, '', ?, ?, NULL, 0)",
                     (bad_id, "consumer-1", "malformed", "NOT_VALID_JSON", "NSE:EQUITY:INE002A01018", now, now))
        conn.commit(); conn.close()
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        engine.reload()
        runner.assert_true("S14-valid-loaded", aid in engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set()))
        runner.assert_true("S14-bad-skipped", not any(bad_id in a2 for a2 in engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set())))
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_identity_resolver_stress(runner):
    resolver = MarketInstrumentIdentityResolver()
    # Register catalog rows like a real store would
    for provider in ["upstox", "fyers", "test"]:
        row = {"provider": provider, "exchange": "NSE", "instrument_type": "EQUITY",
               "isin": "INE002A01018", "tradingsymbol": "RELIANCE-EQ",
               "instrument_token": f"{provider}-token",
               "name": "Reliance", "segment": "NSE"}
        cid = resolver.canonical_id_for_row(row)
        resolver.register(cid, [row["provider"], row["tradingsymbol"],
                                row["instrument_token"]])
    for alias in ["upstox", "RELIANCE-EQ", "upstox-token",
                  "fyers", "fyers-token", "test", "test-token"]:
        resolved = resolver.resolve(alias)
        runner.assert_eq(f"S15-resolve-{alias}", resolved, "NSE:EQUITY:INE002A01018")


async def test_single_event_per_transition(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        q = _mk_quote(26000.0)
        fired = await engine.evaluate(q)
        runner.assert_eq("S16-fires", len(fired), 1)
        events = store.list_relevant_events("consumer-1", None, 99999)
        runner.assert_eq("S16-events", len(events), 1)
        fired2 = await engine.evaluate(q)
        runner.assert_eq("S16-no-dup-event", len(fired2), 0)
        events2 = store.list_relevant_events("consumer-1", None, 99999)
        runner.assert_eq("S16-events-still-1", len(events2), 1)
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def test_rapid_enable_disable(runner):
    store, tmp = _mk_store()
    try:
        resolver = _make_resolver(store)
        engine = ConditionAlertEngine(store, resolver=resolver, bus=None)
        engine.reload()
        aid = _create_alert(store, canonical_id="NSE:EQUITY:INE002A01018", operator="gt", threshold=25000.0, trigger_mode="repeat")
        engine.reload()
        for _ in range(50):
            store.set_condition_alert_enabled(aid, False)
            engine.reload()
            store.set_condition_alert_enabled(aid, True)
            engine.reload()
        runner.assert_true("S17-still-indexed", aid in engine._dep_index.get(f"quote:NSE:EQUITY:INE002A01018", set()))
    finally:
        import shutil; shutil.rmtree(tmp, ignore_errors=True)


async def main():
    runner = R()
    await test_100_alerts_one_instrument(runner)
    await test_500_alerts_one_instrument(runner)
    await test_1000_alerts_indexed(runner)
    await test_burst_duplicate_race(runner)
    await test_multi_alert_concurrency(runner)
    await test_multi_instrument_concurrency(runner)
    await test_level_repeat_under_load(runner)
    await test_unknown_no_rearm(runner)
    await test_crossing_under_load(runner)
    await test_write_amplification_level(runner)
    await test_write_amplification_crossing(runner)
    await test_restart_stress(runner)
    await test_enable_disable_delete_index(runner)
    await test_malformed_row_startup(runner)
    await test_identity_resolver_stress(runner)
    await test_single_event_per_transition(runner)
    await test_rapid_enable_disable(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
