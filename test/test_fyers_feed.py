#!/usr/bin/env python3
"""Fyers live feed lifecycle tests (FF1-FF10).

  * FF1   registry contains fyers_feed factory
  * FF2   factory requires access_token_getter (honest config error)
  * FF3   connect + join + full subscribe frames on start
  * FF4   sf message -> canonical QuotePatch delivered to MarketService
  * FF5   malformed frame isolated (loop continues, counter increments)
  * FF6   dp message -> depth applied to MarketService
  * FF7   add/remove while streaming sends delta frames
  * FF8   stop event ends session cleanly (state stopped)
  * FF9   transport failure triggers reconnect state, not terminal
  * FF10  status exposes safe counters only

NO LIVE BROKER. Stub websocket only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402


class _StubWS:
    def __init__(self, incoming=None):
        self.sent = []
        self.incoming = list(incoming or [])
        self.closed = False

    async def send(self, data):
        self.sent.append(json.loads(data) if isinstance(data, bytes)
                         else json.loads(data))

    async def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def close(self):
        self.closed = True


def _mk_feed(incoming=None, market_service=None):
    from brokers.fyers.feed import FyersFeed

    ws = _StubWS(incoming)

    async def ws_connect(token):
        assert token == "SYNTHETIC-FY-TOKEN"
        return ws

    cfg = {"source_name": "fyers",
           "instrument_keys": ["NSE:SBIN-EQ"],
           "app_id": "APP-1",
           "access_token_getter": lambda: "SYNTHETIC-FY-TOKEN",
           "ws_connect": ws_connect,
           "utc_now_iso": lambda: "2026-08-24T10:00:00+00:00"}
    feed = FyersFeed(config=cfg, auth=object(),
                     market_service=market_service)
    return feed, ws


async def _run_briefly(feed, seconds=0.4):
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(seconds)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()


# -- tests ---------------------------------------------------------------------


async def test_ff1_registry(runner: R) -> None:
    from sources.registry import SOURCE_TYPES
    runner.assert_in("FF1-fyers-in-registry", "fyers_feed", SOURCE_TYPES)


async def test_ff2_factory_requires_getter(runner: R) -> None:
    from sources.registry import SOURCE_TYPES
    try:
        SOURCE_TYPES["fyers_feed"]({"instrument_keys": ["X"]})
        ok = False
    except ValueError:
        ok = True
    runner.assert_true("FF2-honest-config-error", ok)


async def test_ff3_to_ff5_lifecycle(runner: R) -> None:
    from market.service import MarketService

    svc = MarketService()
    sf = json.dumps({"type": "sf", "symbol": "NSE:SBIN-EQ",
                     "ltp": 810.5, "vol_traded_today": 1000,
                     "prev_close_price": 800.0})
    bad = "{not-json"
    feed, ws = _mk_feed(incoming=[sf, bad], market_service=svc)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.5)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    await run()
    # FF3: join + subscribe frames sent.
    runner.assert_eq("FF3-join-frame", ws.sent[0], {"type": 1})
    sub = ws.sent[1]
    runner.assert_eq("FF3-sub-type", sub.get("type"), 2)
    runner.assert_eq("FF3-sub-symbols",
                     sub["data"]["symbols"], ["NSE:SBIN-EQ"])

    # FF4: canonical quote reached MarketService.
    q = await svc.get_quote("NSE", "NSE:SBIN-EQ")
    runner.assert_true("FF4-quote-applied", q is not None)
    if q:
        runner.assert_eq("FF4-ltp", q.ltp, 810.5)

    # FF5: malformed frame did not kill the loop.
    runner.assert_eq("FF5-malformed-count",
                     feed.status()["malformed_frames"], 1)
    runner.assert_ge("FF5-frames-received", feed.status()["frames_received"], 2)


async def test_ff6_depth_message(runner: R) -> None:
    from market.service import MarketService

    svc = MarketService()
    sf = json.dumps({"type": "sf", "symbol": "NSE:SBIN-EQ", "ltp": 810.5})
    dp = json.dumps({"type": "dp", "symbol": "NSE:SBIN-EQ",
                     "bid_price1": 810.0, "bid_size1": 100,
                     "bid_order1": 3, "ask_price1": 810.5,
                     "ask_size1": 200, "ask_order1": 4})
    feed, ws = _mk_feed(incoming=[sf, dp], market_service=svc)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.4)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    await run()
    d = await svc.get_depth("NSE", "NSE:SBIN-EQ")
    runner.assert_true("FF6-depth-applied", d is not None)
    if d:
        runner.assert_eq("FF6-bid-orders", d.bids[0].orders, 3)


async def test_ff7_delta_frames(runner: R) -> None:
    feed, ws = _mk_feed()

    async def run():
        _streaming = None
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.3)
        added = await feed.add_instruments(["NSE:TCS-EQ"])
        removed = await feed.remove_instruments(["NSE:TCS-EQ"])
        stop.set()
        await asyncio.gather(task, return_exceptions=True)
        return added, removed

    added, removed = await run()
    runner.assert_eq("FF7-add-ok", added, 1)
    runner.assert_eq("FF7-remove-ok", removed, 1)
    subs = [f for f in ws.sent if isinstance(f, dict)
            and f.get("type") == 2 and f["data"].get("subType")
            == "SymbolUpdate"]
    runner.assert_ge("FF7-delta-sub-sent", len(subs), 1)


async def test_ff8_clean_stop(runner: R) -> None:
    feed, ws = _mk_feed()

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=3)

    await run()
    runner.assert_eq("FF8-stopped-state", feed.status()["state"], "stopped")
    runner.assert_true("FF8-ws-closed", ws.closed)


async def test_ff9_transport_failure_reconnects(runner: R) -> None:
    """Transport error -> reconnecting/backoff, never terminal failed."""
    from brokers.fyers.feed import FyersFeed

    class _BrokenWS:
        async def send(self, data):
            raise ConnectionError("boom")

        async def recv(self):
            raise ConnectionError("boom")

        async def close(self):
            pass

    cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
           "app_id": "A", "access_token_getter": lambda: "T",
           "ws_connect": lambda token: asyncio.sleep(0, result=_BrokenWS()),
           "utc_now_iso": lambda: ""}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.gather(task, return_exceptions=True)

    await run()
    st = feed.status()
    runner.assert_not_eq("FF9-not-terminal", st["state"], "failed")


async def test_ff11_terminal_outcome_no_crash(runner: R) -> None:
    """_run_session returning the _TERMINAL sentinel must exit cleanly.

    Regression: the run loop used isinstance(outcome, _TERMINAL) against a
    sentinel INSTANCE, raising TypeError('isinstance() arg 2 must be a
    type...') on every real connect - found during first live login.
    """
    from brokers.fyers.feed import FyersFeed, _TERMINAL

    cfg = {"source_name": "fyers", "instrument_keys": ["NSE:X"],
           "app_id": "A", "access_token_getter": lambda: "T",
           "ws_connect": lambda token: asyncio.sleep(0, result=None),
           "utc_now_iso": lambda: ""}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    async def _terminal_session(stop_event):
        # Mirror the real session: terminal failures set state first.
        feed._set_state("failed", reason="config")
        return _TERMINAL

    feed._run_session = _terminal_session

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.wait_for(task, timeout=3)

    await run()
    st = feed.status()
    runner.assert_eq("FF11-terminal-state", st["state"], "failed")
    runner.assert_in("FF11-terminal-exit", "terminal",
                     st.get("last_exit_reason") or "")


async def test_ff10_status_safe(runner: R) -> None:
    feed, _ws = _mk_feed()
    blob = json.dumps(feed.status())
    runner.assert_not_in("FF10-no-token", "SYNTHETIC-FY-TOKEN", blob)
    runner.assert_not_in("FF10-no-wss", "wss://", blob)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_ff1_registry(runner)
    test_ff2_factory_requires_getter(runner)

    # Lifecycle tests need a fresh loop each; run sequentially.
    for coro_fn in (test_ff3_to_ff5_lifecycle, test_ff6_depth_message,
                    test_ff7_delta_frames, test_ff8_clean_stop,
                    test_ff9_transport_failure_reconnects,
                    test_ff11_terminal_outcome_no_crash,
                    test_ff10_status_safe):
        fn = getattr(sys.modules[__name__], coro_fn.__name__)
        await fn(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)




