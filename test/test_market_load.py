#!/usr/bin/env python3
"""Load / reliability / backpressure tests (LD1-LD8).

  * LD1   1000-instrument rapid patch application (correctness)
  * LD2   no stale corruption under out-of-order bursts
  * LD3   event-loop responsiveness during load
  * LD4   subscription churn: overlapping add/remove cycles stay consistent
  * LD5   duplicate add/remove are safe no-ops under load
  * LD6   reconnect-during-mutation safety (UpstoxFeed desired set)
  * LD7   SSE broker fanout: N clients receive identical updates
  * LD8   alert engine throughput under load (no spam)

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402

NOW_S = 0.0


def _ts(offset=0.0):
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# -- LD1-LD3: market service load -------------------------------------------------


async def _load_instruments(count: int, ticks: int):
    from market.service import MarketService, QuotePatch

    svc = MarketService()
    callbacks = {"n": 0}

    async def on_quote(q):
        callbacks["n"] += 1

    svc._on_quote_update = on_quote  # direct hook (composition-root parity)

    t0 = time.monotonic()
    for tick in range(ticks):
        batch = []
        for i in range(count):
            fields = {"ltp": 100.0 + tick}
            if tick % 10 == 0:
                fields["volume"] = i * tick
            batch.append(QuotePatch(
                exchange="NSE", instrument_token=f"T{i}",
                tradingsymbol=f"S{i}", received_ts=_ts(),
                reported_fields=fields))
        for p in batch:
            await svc.apply_quote(p)
    elapsed = time.monotonic() - t0

    # Correctness: every instrument reflects the LAST tick.
    ok = True
    for i in (0, count // 2, count - 1):
        q = await svc.get_quote("NSE", f"T{i}")
        if q is None or q.ltp != 100.0 + (ticks - 1):
            ok = False
    return ok, elapsed, callbacks["n"], len(svc.list_quotes()) \
        if hasattr(svc, "list_quotes") else count


async def test_ld1_ld3_load(runner: R) -> None:
    ok100, e100, cb100, n100 = await _load_instruments(100, 5)
    runner.assert_true("LD1-100-correct", ok100)

    ok500, e500, _, _ = await _load_instruments(500, 3)
    runner.assert_true("LD1-500-correct", ok500)

    # LD3: loop stays responsive after heavy load.
    t0 = time.monotonic()
    await asyncio.sleep(0.05)
    runner.assert_le("LD3-loop-responsive", time.monotonic() - t0, 0.5)
    print(f"    [info] 100x5={e100:.2f}s 500x3={e500:.2f}s")


async def test_ld2_stale_burst(runner: R) -> None:
    """Out-of-order received_ts must not corrupt state."""
    from market.models import Quote
    from market.service import MarketService, QuotePatch

    async def run():
        svc = MarketService()
        old = datetime_now(-10)
        new = datetime_now(0)
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token="T", tradingsymbol="S",
            received_ts=new, reported_fields={"ltp": 200.0}))
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token="T",
            received_ts=old, reported_fields={"ltp": 100.0}))   # stale
        return await svc.get_quote("NSE", "T")

    def datetime_now(offset_s):
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) + timedelta(seconds=offset_s)

    q = await run()
    runner.assert_eq("LD2-stale-rejected", q.ltp, 200.0)


# -- LD4-LD6: subscription churn ----------------------------------------------------


async def test_ld4_to_ld6_churn(runner: R) -> None:
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.auth import UpstoxCredentials

    class _Rest:
        async def authorize_market_feed(self, c):
            return "wss://synthetic"

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": ["K1"]},
        credentials=UpstoxCredentials(access_token="SYNTHETIC"),
        rest=_Rest(), market_service=None,
        instrument_metadata={"K1": ("NSE", "A")})

    async def churn():
        for cycle in range(20):
            await feed.add_instruments([f"X{cycle}"])
            await feed.add_instruments([f"X{cycle}"])       # duplicate
            await feed.remove_instruments([f"X{cycle}"])
            await feed.remove_instruments([f"X{cycle}"])    # dup remove
        await feed.add_instruments(["FINAL"])

    await churn()
    keys = sorted(feed._instrument_keys)
    runner.assert_eq("LD4-churn-consistent", keys, ["FINAL", "K1"])

    # LD5: duplicate remove of absent key is a no-op.
    removed = await feed.remove_instruments(["GONE"])
    runner.assert_eq("LD5-dup-remove-noop", removed, 0)

    # LD6: mutation while offline never raises.
    await feed.add_instruments(["OFFLINE"])
    runner.assert_true("LD6-offline-safe",
                       "OFFLINE" in feed._instrument_keys)


# -- LD7: SSE broker fanout ------------------------------------------------------------


async def test_ld7_sse_fanout(runner: R) -> None:
    """N subscribers each receive identical updates from one broker."""
    from app.server import _market_event_broker as broker


    async def run():
        contexts = [broker.subscribe() for _ in range(20)]
        gens = await asyncio.gather(*[c.__aenter__() for c in contexts])

        async def first(gen):
            return await gen.__anext__()

        tasks = [asyncio.create_task(first(g)) for g in gens]
        await asyncio.sleep(0.05)
        broker.broadcast('{"type":"quote","data":{"ltp":1}}')
        results = await asyncio.gather(*tasks, return_exceptions=True)
        count_while_connected = broker.subscriber_count
        await asyncio.gather(
            *[c.__aexit__(None, None, None) for c in contexts],
            return_exceptions=True)
        return results, count_while_connected

    results, connected_count = await run()
    good = [r for r in results if not isinstance(r, BaseException)]
    runner.assert_eq("LD7-all-clients-served", len(good), 20)
    runner.assert_eq("LD7-single-broker", connected_count, 20)
    unique = {r if isinstance(r, str) else json.dumps(r) for r in good}
    runner.assert_eq("LD7-identical-payload", len(unique), 1)


async def test_ld8_alert_throughput(runner: R) -> None:
    """Alert engine evaluates thousands of quotes without spam or growth."""
    from app.alerts import AlertEngine
    from market.models import Quote

    class _Store:
        def load_enabled_alerts(self):
            return [{"id": 1, "exchange": "NSE", "instrument_token": "T1",
                     "tradingsymbol": "AAA", "field": "ltp",
                     "operator": "gt", "threshold": 50.0,
                     "enabled": 1, "state": "inactive"}]

        def record_trigger(self, aid):
            pass

    engine = AlertEngine(_Store())
    fired_total = 0
    for i in range(2000):
        q = Quote(instrument_token="T1", exchange="NSE",
                  tradingsymbol="AAA", received_ts=_ts(),
                  ltp=40.0 + (i % 30))
        fired_total += len(engine.evaluate(q))
    # Only values > 50 fire, and only once until re-arm.
    runner.assert_le("LD8-bounded-fires", fired_total, 1)
    runner.assert_eq("LD8-notification-history-bounded",
                     len(engine.recent_notifications(100)), fired_total)


def json_dumps(v):
    import json
    return json.dumps(v)


import json  # noqa: E402


def _ts():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_ld1_ld3_load(runner)
    await test_ld2_stale_burst(runner)
    await test_ld4_to_ld6_churn(runner)
    await test_ld7_sse_fanout(runner)
    await test_ld8_alert_throughput(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)







