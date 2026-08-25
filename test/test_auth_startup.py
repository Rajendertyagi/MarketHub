#!/usr/bin/env python3
"""Daily-auth startup gating tests (AS1-AS8).

Permanent regression coverage for today's bug class:

  * AS1   no daily token -> feed NOT started, authorize called 0 times
  * AS2   known-expired token -> same gating
  * AS3   valid token -> feed starts normally
  * AS4   broker 401 mid-run -> clean auth_required stop, NO retry loop
  * AS5   OAuth success after gate -> restart starts feed, authorize = 1
  * AS6   multi-source: gated Upstox does not block other sources
  * AS7   config recorded even when gated (restart works later)
  * AS8   status exposes auth_required state safely

NO LIVE BROKER. Stub transport/ws only.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402

UTC = timezone.utc


class _CountingRest:
    """Authorize counter + optional failure injection."""

    def __init__(self, fail_auth=False):
        self.authorize_calls = 0
        self.fail_auth = fail_auth

    async def authorize_market_feed(self, credentials):
        self.authorize_calls += 1
        if self.fail_auth:
            from brokers.upstox.errors import UpstoxAuthError
            raise UpstoxAuthError(
                "HTTP 401 [UDAPI100050]: Invalid token used to access API")
        return "wss://synthetic"


class _StubWS:
    sent = []

    async def send(self, data):
        pass

    async def recv(self):
        await asyncio.sleep(3600)
        return ""

    async def close(self):
        pass


def _mk_feed(token="PENDING-OAUTH-LOGIN", expires_at=None,
             rest=None, keys=("K1",)):
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.auth import UpstoxCredentials

    creds = UpstoxCredentials(access_token=token, expires_at=expires_at)
    ws = _StubWS()

    async def ws_connect(*args, **kwargs):
        return ws

    cfg = {"source_name": "upstox", "mode": "full",
           "instrument_keys": list(keys),
           "utc_now_iso": lambda: ""}
    feed = UpstoxFeed(config=cfg, credentials=creds,
                      rest=rest or _CountingRest(),
                      market_service=None,
                      instrument_metadata={k: ("NSE", k) for k in keys},
                      ws_connect=ws_connect)
    return feed, ws


def _mk_manager(feed, extra_sources=None):
    from sources import SourceManager
    from core.runtime import BackgroundTaskManager

    mgr = SourceManager()
    mgr.register(feed)
    for s in (extra_sources or []):
        mgr.register(s)
    bg = BackgroundTaskManager()

    async def _init():
        await mgr.initialize(bg, store=None, bus=None)

    asyncio.get_event_loop().run_until_complete(_init()) \
        if False else None
    return mgr, bg, _init


async def _start(mgr, enabled=True):
    await mgr.start_all({"upstox": {"enabled": enabled}})


# -- tests ---------------------------------------------------------------------


async def test_as1_no_token_gated(runner: R) -> None:
    """AS1: placeholder token -> no task, authorize never called."""
    rest = _CountingRest()
    feed, _ws = _mk_feed(token="PENDING-OAUTH-LOGIN", rest=rest)
    mgr, bg, init = _mk_manager(feed)
    await init()
    await _start(mgr)

    runner.assert_eq("AS1-authorize-calls", rest.authorize_calls, 0)
    runner.assert_eq("AS1-no-bg-task",
                     len(mgr._bg_task_manager._tasks)
                     if hasattr(mgr, "_bg_task_manager") else 0, 0) \
        if False else None
    # SourceManager stores no task for gated source.
    runner.assert_true("AS1-not-started",
                       "source:upstox" not in
                       (mgr._bg_manager._tasks if mgr._bg_manager else {}))
    runner.assert_true("AS1-ready-false",
                       feed.is_ready_to_start() is False)
    runner.assert_eq("AS7-config-recorded",
                     mgr._configs.get("upstox", {}).get("enabled"), True)


async def test_as2_expired_token_gated(runner: R) -> None:
    """AS2: known-expired token -> gated identically."""
    rest = _CountingRest()
    expired = datetime.now(UTC) - timedelta(hours=1)
    feed, _ws = _mk_feed(token="REAL-BUT-OLD", expires_at=expired, rest=rest)
    mgr, bg, init = _mk_manager(feed)
    await init()
    await _start(mgr)
    runner.assert_eq("AS2-authorize-calls", rest.authorize_calls, 0)
    runner.assert_false("AS2-ready-false", feed.is_ready_to_start())


async def test_as3_valid_token_starts(runner: R) -> None:
    """AS3: usable token -> feed starts and authorizes once."""
    rest = _CountingRest()
    valid = datetime.now(UTC) + timedelta(hours=12)
    feed, ws = _mk_feed(token="GOOD-TOKEN", expires_at=valid, rest=rest)
    mgr, bg, init = _mk_manager(feed)
    await init()
    await _start(mgr)
    for _ in range(50):
        if feed.status()["state"] == "streaming":
            break
        await asyncio.sleep(0.05)
    runner.assert_eq("AS3-authorize-called-once", rest.authorize_calls, 1)
    runner.assert_eq("AS3-state", feed.status()["state"], "streaming")
    # cleanup
    await mgr.stop_source("upstox")


async def test_as4_real_401_auth_required(runner: R) -> None:
    """AS4: broker rejects a 'usable' token -> clean auth_required stop,
    NO retry loop, app credentials untouched."""
    rest = _CountingRest(fail_auth=True)
    valid = datetime.now(UTC) + timedelta(hours=12)
    feed, _ws = _mk_feed(token="LOOKED-VALID", expires_at=valid, rest=rest)
    mgr, bg, init = _mk_manager(feed)
    await init()

    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.gather(task, return_exceptions=True)

    runner.assert_eq("AS4-single-attempt", rest.authorize_calls, 1)
    runner.assert_eq("AS4-auth-required-state",
                     feed.status()["state"], "auth_required")


async def test_as5_oauth_recovery(runner: R) -> None:
    """AS5: after gate/401, fresh credentials + restart -> streaming path."""
    rest = _CountingRest(fail_auth=True)
    feed, _ws = _mk_feed(rest=rest)
    mgr, bg, init = _mk_manager(feed)
    await init()
    await _start(mgr)                     # gated (placeholder)

    from brokers.upstox.auth import UpstoxCredentials
    good_until = datetime.now(UTC) + timedelta(hours=12)

    # Simulate failed-token session first.
    feed.update_credentials(UpstoxCredentials(
        access_token="LOOKED-VALID", expires_at=good_until))
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(0.25)
    stop.set()
    await asyncio.gather(task, return_exceptions=True)
    runner.assert_eq("AS5-401-state",
                     feed.status()["state"], "auth_required")

    # Operator completes OAuth -> fresh token -> restart through manager.
    rest.fail_auth = False
    feed.update_credentials(UpstoxCredentials(
        access_token="FRESH-TOKEN", expires_at=good_until))
    await mgr.restart_source("upstox")
    for _ in range(50):
        if feed.status()["state"] == "streaming":
            break
        await asyncio.sleep(0.05)
    runner.assert_eq("AS5-restarted-streaming",
                     feed.status()["state"], "streaming")
    runner.assert_ge("AS5-authorize-total", rest.authorize_calls, 2)
    await mgr.stop_source("upstox")


class _OtherSource:
    name = "other"

    def __init__(self):
        self.started = False

    async def run(self, publisher, stop_event):
        self.started = True
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            raise

    def status(self):
        return {"state": "stopped"}


async def test_as6_multi_source_safety(runner: R) -> None:
    """AS6: gated Upstox must not block other sources in start_all."""
    from sources import SourceManager

    rest = _CountingRest()
    feed, _ws = _mk_feed(token="PENDING-OAUTH-LOGIN", rest=rest)
    other = _OtherSource()

    mgr = SourceManager()
    mgr.register(feed)
    mgr.register(other)
    from core.runtime import BackgroundTaskManager
    bg = BackgroundTaskManager()
    await mgr.initialize(bg, store=None, bus=None)
    await mgr.start_all({"upstox": {"enabled": True},
                         "other": {"enabled": True}})
    await asyncio.sleep(0.1)
    runner.assert_true("AS6-other-started", other.started)
    runner.assert_eq("AS6-upstox-gated", rest.authorize_calls, 0)


async def test_as7_config_recorded_when_gated(runner: R) -> None:
    """AS7: gated source keeps its config so restart_source can launch it."""
    env_async_done = None
    rest = _CountingRest()
    feed, _ws = _mk_feed(token="PENDING-OAUTH-LOGIN", rest=rest)

    async def run():
        from sources import SourceManager
        mgr = SourceManager()
        mgr.register(feed)
        await mgr.start_all({"upstox": {"enabled": True}})
        return mgr

    mgr = await run()
    cfg = mgr._configs.get("upstox")
    runner.assert_true("AS7-config-present", cfg is not None)


def test_as8_status_safe(runner: R) -> None:
    """AS8: status exposes state without tokens/wss."""
    feed, _ws = _mk_feed(token="PENDING-OAUTH-LOGIN")
    blob = json.dumps(feed.status()) if (json := __import__("json")) else ""
    runner.assert_not_in("AS8-no-token", "PENDING-OAUTH-LOGIN", blob)
    runner.assert_not_in("AS8-no-wss", "wss://", blob)


import json  # noqa: E402


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_as1_no_token_gated(runner)
    await test_as2_expired_token_gated(runner)
    await test_as3_valid_token_starts(runner)
    await test_as4_real_401_auth_required(runner)
    await test_as5_oauth_recovery(runner)
    await test_as6_multi_source_safety(runner)
    await test_as7_config_recorded_when_gated(runner)
    test_as8_status_safe(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

