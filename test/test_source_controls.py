#!/usr/bin/env python3
"""Source lifecycle control tests (SC1-SC10) + hardening regressions.

Behavioral coverage for the WebUI Start/Stop/Restart controls and the
auto-recovery investigation fixes:

  * SC1   valid token + stopped source -> Start -> exactly one task
  * SC2   already streaming -> Start -> no duplicate task
  * SC3   streaming -> Restart -> old task ends, ONE new task,
          desired instruments preserved
  * SC4   streaming -> Stop -> task/ws gone, credentials remain
  * SC5   auth_required -> Start refused safely, NO authorize request
  * SC6   transient WS disconnect -> automatic reconnect -> streaming
  * SC7   invalid token / 401 -> auth_required, NO infinite reconnect
  * SC8   explicit Stop -> auto reconnect does NOT restart it
  * SC9   multiple concurrent Start clicks -> still exactly one task
  * SC10  Fyers source through the SAME generic control API
  * REG1  cancellation closes the LIVE websocket (self._ws phantom bug)
  * REG2  MarketService apply failure isolated (recv loop survives)
  * REG3  status exposes provider/task_running/last_exit_reason/state_updated_at
  * LOG1  rotating file handler configured (10 MiB x 5, utf-8)
  * LOG2  source state transition + exit reason persisted to the log file
  * LOG3  tokens / authorized URIs never reach the log file
  * LOG4  broken log destination degrades to console-only, never raises

NO LIVE BROKER. Stub transport/ws only. Run: python test/test_source_controls.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402

TOKEN = "SYNTHETIC_ACCESS_TOKEN_XYZ"
URI_1 = "wss://feeder.example/feeds?requestId=R1&code=CODE1"
URI_2 = "wss://feeder.example/feeds?requestId=R2&code=CODE2"
KEYS = ["NSE_EQ|INE001TEST01", "NSE_EQ|INE002TEST02"]


def _valid_creds():
    """Fresh, non-expired Upstox credentials for runtime start tests."""
    from datetime import datetime, timedelta, timezone
    from brokers.upstox.auth import UpstoxCredentials
    return UpstoxCredentials(
        access_token=TOKEN,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12))


# ---------------------------------------------------------------------------
# Fakes (Upstox)
# ---------------------------------------------------------------------------

class CountingRest:
    """authorize_market_feed counter + scripted outcomes."""

    def __init__(self, fail_auth=False):
        self.authorize_calls = 0
        self.fail_auth = fail_auth
        self.uris = [URI_1, URI_2]

    async def authorize_market_feed(self, credentials):
        self.authorize_calls += 1
        if self.fail_auth:
            from brokers.upstox.errors import UpstoxAuthError
            raise UpstoxAuthError(
                "HTTP 401 [UDAPI100050]: Invalid token used to access API")
        return self.uris[min(self.authorize_calls - 1, len(self.uris) - 1)]


class ScriptedWS:
    """Duck-typed WS: scripted incoming items, recorded sends."""

    def __init__(self, incoming=None):
        self.sent: list[bytes | str] = []
        self.closed = False
        self._incoming = list(incoming or [])

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._incoming:
            item = self._incoming.pop(0)
            if isinstance(item, Exception):
                raise item
            await asyncio.sleep(0)
            return item
        await asyncio.sleep(3600)
        return b""

    async def close(self):
        self.closed = True


def mk_upstox_feed(token=TOKEN, rest=None, keys=None, market_service=None,
                   expires_at=None, scripted_incoming=None):
    """Build an UpstoxFeed whose connector yields fresh ScriptedWS per call.

    ``scripted_incoming`` optionally provides per-connection incoming item
    lists (frames or exceptions); connection N gets list N (default empty).
    """
    from datetime import datetime, timedelta, timezone
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.auth import UpstoxCredentials

    connections: list[ScriptedWS] = []

    async def ws_connect(uri, **kwargs):
        idx = len(connections)
        items = (scripted_incoming[idx]
                 if scripted_incoming and idx < len(scripted_incoming) else None)
        ws = ScriptedWS(items)
        connections.append(ws)
        return ws

    if expires_at is None:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=12)

    creds = UpstoxCredentials(access_token=token, expires_at=expires_at)
    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": list(keys or KEYS)},
        credentials=creds,
        rest=rest or CountingRest(),
        market_service=market_service,
        instrument_metadata={k: ("NSE", k) for k in (keys or KEYS)},
        ws_connect=ws_connect,
    )
    return feed, rest, connections


async def mk_manager(*sources):
    """SourceManager wired to a real BackgroundTaskManager."""
    from sources import SourceManager
    from core.runtime import BackgroundTaskManager

    mgr = SourceManager()
    for s in sources:
        mgr.register(s)
    await mgr.initialize(BackgroundTaskManager(), store=None, bus=None)
    return mgr


async def wait_for(predicate, timeout_s=5.0, step=0.05):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


def sub_keys(ws: ScriptedWS) -> list[str]:
    """instrumentKeys of the first subscription frame on this socket."""
    for frame in ws.sent:
        try:
            payload = json.loads(frame)
        except Exception:
            continue
        if payload.get("method") == "sub":
            return list(payload["data"]["instrumentKeys"])
    return []


# ---------------------------------------------------------------------------
# SC1 - SC9 (Upstox through SourceManager)
# ---------------------------------------------------------------------------

async def test_sc1_start_single_task(runner: R) -> None:
    """SC1: valid token + stopped source -> Start -> one task starts."""
    rest = CountingRest()
    # Placeholder at CONSTRUCTION time so start_all gates the source.
    feed, _rest, conns = mk_upstox_feed(token="PENDING-OAUTH-LOGIN",
                                        rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    runner.assert_eq("SC1-gated-no-task", mgr.task_running("upstox"), False)

    # Operator supplies today's token (runtime-only), then clicks Start.
    from datetime import datetime, timedelta, timezone
    from brokers.upstox.auth import UpstoxCredentials
    feed.update_credentials(UpstoxCredentials(
        access_token=TOKEN,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12)))

    result = await mgr.start_source("upstox")
    runner.assert_eq("SC1-start-result", result, "started")
    ok = await wait_for(lambda: feed.status()["state"] == "streaming")
    runner.assert_true("SC1-streaming", ok)
    runner.assert_eq("SC1-one-task", mgr.task_running("upstox"), True)
    runner.assert_eq("SC1-authorize-once", rest.authorize_calls, 1)
    runner.assert_eq("SC1-one-ws", len(conns), 1)
    await mgr.stop_source("upstox")


async def test_sc2_no_duplicate_start(runner: R) -> None:
    """SC2: already streaming -> Start -> no duplicate task."""
    rest = CountingRest()
    feed, _rest, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")

    result = await mgr.start_source("upstox")
    runner.assert_eq("SC2-already-running", result, "already_running")
    runner.assert_eq("SC2-authorize-still-one", rest.authorize_calls, 1)
    runner.assert_eq("SC2-one-ws", len(conns), 1)
    await mgr.stop_source("upstox")


async def test_sc3_restart_preserves_desired(runner: R) -> None:
    """SC3: streaming -> Restart -> old task ends, ONE new task, desired set."""
    rest = CountingRest()
    feed, _rest, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")
    first_ws = conns[0]
    desired_before = feed.desired_instrument_count

    ok = await mgr.restart_source("upstox")
    runner.assert_true("SC3-restart-ok", ok)
    runner.assert_true(
        "SC3-old-task-ended",
        await wait_for(lambda: mgr.task_running("upstox")
                       and len(conns) == 2))
    runner.assert_true("SC3-first-ws-closed", first_ws.closed)
    runner.assert_eq("SC3-desired-preserved",
                     feed.desired_instrument_count, desired_before)
    runner.assert_eq("SC3-resubscribed-keys",
                     sub_keys(conns[1]), list(KEYS))
    runner.assert_true(
        "SC3-streaming-again",
        await wait_for(lambda: feed.status()["state"] == "streaming"))
    runner.assert_eq("SC3-authorize-fresh", rest.authorize_calls, 2)
    await mgr.stop_source("upstox")


async def test_sc4_stop_retains_credentials(runner: R) -> None:
    """SC4: streaming -> Stop -> task/ws gone, credentials remain."""
    rest = CountingRest()
    feed, _rest, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")

    creds_before = feed.credentials_snapshot
    token_before = creds_before.access_token

    known = await mgr.stop_source("upstox")
    runner.assert_true("SC4-known", known)
    runner.assert_eq("SC4-task-gone", mgr.task_running("upstox"), False)
    runner.assert_true("SC4-ws-closed", conns[0].closed)
    runner.assert_eq("SC4-state-stopped", feed.status()["state"], "stopped")
    runner.assert_true("SC4-same-creds-object",
                       feed.credentials_snapshot is creds_before)
    runner.assert_eq("SC4-token-unchanged",
                     feed.credentials_snapshot.access_token, token_before)
    runner.assert_eq("SC4-desired-unchanged",
                     feed.desired_instrument_count, len(KEYS))


async def test_sc5_auth_required_refused(runner: R) -> None:
    """SC5: auth_required -> Start refused safely, NO authorize request."""
    rest = CountingRest()
    feed, _rest, _conns = mk_upstox_feed(token="PENDING-OAUTH-LOGIN",
                                         rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})

    result = await mgr.start_source("upstox")
    runner.assert_eq("SC5-refused", result, "not_ready")
    runner.assert_eq("SC5-no-authorize", rest.authorize_calls, 0)
    runner.assert_eq("SC5-no-task", mgr.task_running("upstox"), False)


async def test_sc6_transient_reconnect(runner: R) -> None:
    """SC6: transient WS disconnect -> automatic reconnect -> streaming."""
    rest = CountingRest()
    from websockets.exceptions import ConnectionClosedError
    # First socket dies right after the subscription frame; second is healthy.
    feed, _rest, conns = mk_upstox_feed(
        rest=rest,
        scripted_incoming=[[ConnectionClosedError(None, None)], []])
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")

    runner.assert_true("SC6-reconnect-happens",
                       await wait_for(lambda: len(conns) >= 2))
    runner.assert_true(
        "SC6-back-to-streaming",
        await wait_for(lambda: feed.status()["state"] == "streaming"
                       and len(conns) >= 2))
    runner.assert_ge("SC6-reauthorized", rest.authorize_calls, 2)
    runner.assert_eq("SC6-resubscribed-on-new-socket",
                     sub_keys(conns[1]), list(KEYS))
    runner.assert_eq("SC6-exactly-one-live-task",
                     mgr.task_running("upstox"), True)
    await mgr.stop_source("upstox")


async def test_sc7_401_terminal_auth_required(runner: R) -> None:
    """SC7: invalid token / 401 -> auth_required, NO infinite reconnect."""
    rest = CountingRest(fail_auth=True)
    feed, _rest, _conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "auth_required")

    runner.assert_eq("SC7-auth-required",
                     feed.status()["state"], "auth_required")
    runner.assert_eq("SC7-single-attempt", rest.authorize_calls, 1)
    await asyncio.sleep(0.4)
    runner.assert_eq("SC7-no-loop", rest.authorize_calls, 1)
    runner.assert_eq("SC7-exit-reason",
                     feed.status()["last_exit_reason"], "auth_required")
    await mgr.stop_source("upstox")


async def test_sc8_stop_stays_stopped(runner: R) -> None:
    """SC8: explicit Stop -> auto reconnect does NOT restart it."""
    rest = CountingRest()
    feed, _rest, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")

    await mgr.stop_source("upstox")
    calls_after_stop = rest.authorize_calls
    await asyncio.sleep(0.6)
    runner.assert_eq("SC8-no-auto-restart-authorize",
                     rest.authorize_calls, calls_after_stop)
    runner.assert_eq("SC8-no-task", mgr.task_running("upstox"), False)
    runner.assert_eq("SC8-exit-reason",
                     feed.status()["last_exit_reason"], "stop_requested")


async def test_sc9_concurrent_starts_one_task(runner: R) -> None:
    """SC9: multiple Start clicks -> still exactly one source task."""
    rest = CountingRest()
    # Placeholder at CONSTRUCTION time so start_all gates the source.
    feed, _rest, conns = mk_upstox_feed(token="PENDING-OAUTH-LOGIN",
                                        rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    from datetime import datetime, timedelta, timezone
    from brokers.upstox.auth import UpstoxCredentials
    feed.update_credentials(UpstoxCredentials(
        access_token=TOKEN,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=12)))

    results = await asyncio.gather(
        *[mgr.start_source("upstox") for _ in range(5)])
    runner.assert_true(
        "SC9-all-handled",
        all(r in ("started", "already_running") for r in results),
        f"unexpected results: {results}")
    runner.assert_true(
        "SC9-one-task",
        await wait_for(lambda: feed.status()["state"] == "streaming"))
    runner.assert_eq("SC9-single-authorize", rest.authorize_calls, 1)
    runner.assert_eq("SC9-single-socket", len(conns), 1)
    await mgr.stop_source("upstox")


# ---------------------------------------------------------------------------
# SC10 - Fyers through the SAME generic control API
# ---------------------------------------------------------------------------

class FyersStubWS:
    def __init__(self):
        self.sent: list[bytes] = []
        self.incoming: list[bytes] = []
        self.closed = False

    async def send(self, data):
        data = bytes(data)
        self.sent.append(data)
        # Auto-answer the binary auth frame (resp_type 1) with 'K' ok.
        if len(data) >= 3 and data[2] == 1:
            self.incoming.insert(0, _FYERS_AUTH_OK)

    async def recv(self):
        if getattr(self, "incoming", None):
            return self.incoming.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def close(self):
        self.closed = True


_FYERS_AUTH_OK = (
    struct.pack("!H", 15) + b"\x01\x00\x00"
    + struct.pack("!H", 1) + b"K" + b"\x00\x00\x00"
    + struct.pack(">I", 5))


def _fy_jwt(hsm_key: str = "HSMSYNTH") -> str:
    def b64(obj):
        return base64.urlsafe_b64encode(
            json.dumps(obj).encode()).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'hsm_key': hsm_key})}."


async def test_sc10_fyers_generic_api(runner: R) -> None:
    """SC10: Fyers uses the SAME generic lifecycle (no special-case branches).

    Uses a real FyersFeed instance with stubbed transport/token boundary;
    config.json construction remains intentionally out of scope because
    access_token_getter is a runtime callable.
    """
    from brokers.fyers.feed import FyersFeed

    sockets: list[FyersStubWS] = []

    async def ws_connect(token):
        ws = FyersStubWS()
        sockets.append(ws)
        return ws

    cfg = {"source_name": "fyers",
           "instrument_keys": ["NSE:SBIN-EQ"],
           "app_id": "APP-1",
           "access_token_getter": lambda: _fy_jwt(),
           "ws_connect": ws_connect,
           "utc_now_iso": lambda: "2026-08-25T10:00:00+00:00"}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)

    mgr = await mk_manager(feed)
    await mgr.start_all({"fyers": {"enabled": True}})
    runner.assert_true(
        "SC10-started-via-generic-api",
        await wait_for(lambda: feed.status()["state"] == "streaming"))
    runner.assert_eq("SC10-instance-identity",
                     mgr.enabled_sources["fyers"] is feed, True)

    # Duplicate start refused by the SAME generic path.
    runner.assert_eq("SC10-duplicate-refused",
                     await mgr.start_source("fyers"), "already_running")
    runner.assert_eq("SC10-still-one-socket", len(sockets), 1)

    # Restart through the generic API: old socket gone, desired preserved.
    runner.assert_true("SC10-restart-ok", await mgr.restart_source("fyers"))
    runner.assert_true(
        "SC10-two-sockets-after-restart",
        await wait_for(lambda: len(sockets) == 2))
    runner.assert_true("SC10-old-fyers-ws-closed", sockets[0].closed)
    runner.assert_true(
        "SC10-streaming-again",
        await wait_for(lambda: feed.status()["state"] == "streaming"))

    types = [f[2] for f in sockets[1].sent if len(f) >= 3]
    runner.assert_true("SC10-rejoin-and-resubscribe",
                       1 in types and 4 in types, f"frame types: {types}")

    # Stop through the generic API.
    runner.assert_true("SC10-stop-ok", await mgr.stop_source("fyers"))
    runner.assert_eq("SC10-stopped", feed.status()["state"], "stopped")
    runner.assert_true("SC10-ws-closed", sockets[1].closed)
    runner.assert_eq("SC10-no-task", mgr.task_running("fyers"), False)

    # Readiness flows through the generic gate: Fyers declares a token gate
    # (WP37) and reports ready once a token is available.
    runner.assert_eq("SC10-ready-true", mgr.is_ready("fyers"), True)


# ---------------------------------------------------------------------------
# Regressions for the auto-recovery investigation fixes
# ---------------------------------------------------------------------------

async def test_reg1_cancel_closes_live_ws(runner: R) -> None:
    """REG1: cancelling the task closes the REAL live socket (not phantom)."""
    rest = CountingRest()
    feed, _rest, conns = mk_upstox_feed(rest=rest)

    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    try:
        runner.assert_true(
            "REG1-reached-streaming",
            await wait_for(lambda: feed.status()["state"] == "streaming"))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    runner.assert_true("REG1-live-ws-closed",
                       len(conns) >= 1 and conns[0].closed)
    runner.assert_eq("REG1-state-stopped",
                     feed.status()["state"], "stopped")
    runner.assert_eq("REG1-exit-cancelled",
                     feed.status()["last_exit_reason"], "cancelled")


async def test_reg2_apply_failure_isolated(runner: R) -> None:
    """REG2: a MarketService apply error must NOT kill the recv loop."""
    rest = CountingRest()

    class ExplodingService:
        def __init__(self):
            self.calls = 0

        async def apply_quote(self, patch):
            self.calls += 1
            raise RuntimeError("synthetic market-service failure")

        async def apply_depth(self, depth):
            raise RuntimeError("synthetic market-service failure")

    svc = ExplodingService()
    # Both binary frames pre-loaded on the first socket (a parked recv()
    # cannot be woken by later appends — same stub mechanics as SC6).
    feed, _rest, conns = mk_upstox_feed(
        rest=rest, market_service=svc,
        scripted_incoming=[[b"FRAME-1", b"FRAME-2"]])

    # Feed the frames through a patched decoder so they reach the
    # application seam without crafting real protobuf payloads.
    import brokers.upstox.feed as feed_mod
    from brokers.upstox.feed_processing import FrameResult, InstrumentOutcome

    original = feed_mod.process_binary_frame

    def fake_process(frame, *, received_ts, instrument_metadata):
        return FrameResult(
            frame_type="live_feed",
            instruments=(InstrumentOutcome(
                instrument_key=KEYS[0], patch=object()),),
        )

    feed_mod.process_binary_frame = fake_process
    try:
        mgr = await mk_manager(feed)
        await mgr.start_all({"upstox": {"enabled": True}})
        await wait_for(lambda: feed.status()["state"] == "streaming")
        runner.assert_true("REG2-both-frames-applied",
                           await wait_for(lambda: svc.calls >= 2))
        runner.assert_true(
            "REG2-loop-still-streaming",
            await wait_for(lambda: feed.status()["state"] == "streaming"
                           and feed.status().get("apply_errors", 0) >= 2))
        runner.assert_eq("REG2-task-alive", mgr.task_running("upstox"), True)
        await mgr.stop_source("upstox")
    finally:
        feed_mod.process_binary_frame = original


async def test_reg3_status_fields(runner: R) -> None:
    """REG3: status exposes provider/task_running/reconnecting/exit info."""
    rest = CountingRest()
    feed, _rest, _conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")

    snap = mgr.get_status().get("upstox", {})
    runner.assert_eq("REG3-provider", snap.get("provider"), "upstox")
    runner.assert_eq("REG3-task-running", snap.get("task_running"), True)
    runner.assert_false("REG3-not-reconnecting-while-streaming",
                        snap.get("reconnecting"))
    runner.assert_true("REG3-state-updated-at",
                       snap.get("state_updated_at") is not None)

    await mgr.stop_source("upstox")
    snap = mgr.get_status().get("upstox", {})
    runner.assert_eq("REG3-task-running-after-stop",
                     snap.get("task_running"), False)
    runner.assert_eq("REG3-exit-reason",
                     snap.get("last_exit_reason"), "stop_requested")
    runner.assert_in("REG3-last-task-exit-recorded",
                     "reason", snap.get("last_task_exit", {}))


# ---------------------------------------------------------------------------
# Logging configuration tests (LOG1-LOG4)
# ---------------------------------------------------------------------------

def _close_all_root_handlers() -> None:
    """Release log files so Windows can delete the temp dir (teardown)."""
    import logging
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass


def test_log1_rotating_handler_configured(runner: R) -> None:
    """LOG1: rotating handler configured with locked size/count/encoding."""
    import logging.handlers
    from app.logging_setup import setup_logging

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = setup_logging(tmp, console=False, force=True)
            runner.assert_true("LOG1-path-returned", path is not None)
            runner.assert_true("LOG1-file-created",
                               path is not None and os.path.exists(path))
            rh = [h for h in logging.getLogger().handlers
                  if isinstance(h, logging.handlers.RotatingFileHandler)]
            runner.assert_eq("LOG1-one-rotating-handler", len(rh), 1)
            if rh:
                runner.assert_eq("LOG1-max-bytes", rh[0].maxBytes,
                                 10 * 1024 * 1024)
                runner.assert_eq("LOG1-backup-count", rh[0].backupCount, 5)
            # Release the file BEFORE the temp dir cleanup runs (Windows).
            _close_all_root_handlers()
    finally:
        _close_all_root_handlers()


async def test_log2_transition_reason_logged(runner: R) -> None:
    """LOG2: state transitions + exit reasons are persisted."""
    from app.logging_setup import setup_logging

    async def scenario():
        rest = CountingRest()
        feed, _r, _c = mk_upstox_feed(rest=rest)
        mgr = await mk_manager(feed)
        await mgr.start_all({"upstox": {"enabled": True}})
        await wait_for(lambda: feed.status()["state"] == "streaming")
        await mgr.stop_source("upstox")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = setup_logging(tmp, console=False, force=True)
            await scenario()
            with open(path, "r", encoding="utf-8") as fh:
                blob = fh.read()
            # Release the file BEFORE the temp dir cleanup runs (Windows).
            _close_all_root_handlers()
        runner.assert_in("LOG2-transition-streaming",
                         "state -> streaming", blob)
        runner.assert_in("LOG2-session-ended", "session ended", blob)
        runner.assert_in("LOG2-stop-reason", "stop_requested", blob)
    finally:
        _close_all_root_handlers()


async def test_log3_no_secrets_in_log(runner: R) -> None:
    """LOG3: tokens and authorized WSS URIs never reach the log file."""
    from app.logging_setup import setup_logging

    async def scenario():
        rest = CountingRest()
        feed, _r, _c = mk_upstox_feed(rest=rest)
        mgr = await mk_manager(feed)
        await mgr.start_all({"upstox": {"enabled": True}})
        await wait_for(lambda: feed.status()["state"] == "streaming")
        await mgr.stop_source("upstox")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = setup_logging(tmp, console=False, force=True)
            await scenario()
            with open(path, "r", encoding="utf-8") as fh:
                blob = fh.read()
            # Release the file BEFORE the temp dir cleanup runs (Windows).
            _close_all_root_handlers()
        runner.assert_not_in("LOG3-no-token", TOKEN, blob)
        runner.assert_not_in("LOG3-no-wss-uri", URI_1, blob)
        runner.assert_not_in("LOG3-no-code-material", "CODE1", blob)
    finally:
        _close_all_root_handlers()


def test_log4_broken_destination_degrades(runner: R) -> None:
    """LOG4: unusable log directory -> console-only, never raises."""
    from app.logging_setup import setup_logging

    try:
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "blocker")
            with open(blocker, "w", encoding="utf-8") as fh:
                fh.write("a plain file, not a directory\n")
            # log dir path passes THROUGH the blocker file -> mkdir must fail.
            bad_root = os.path.join(blocker, "sub")
            path = setup_logging(bad_root, console=False, force=True)
            runner.assert_eq("LOG4-degraded-to-console", path, None)
    finally:
        _close_all_root_handlers()


# ---------------------------------------------------------------------------
# Stop-reason vocabulary, transition history, startup matrix, leak/race/outage
# stress, shutdown, security scan, Fyers parity (WP4/8/14/15/22/25/26/27/28/30/37)
# ---------------------------------------------------------------------------

async def test_sr1_stop_reason_vocabulary(runner: R) -> None:
    """SR1: stop_reason distinguishes operator / restart / shutdown / auth."""
    # operator_stop
    rest = CountingRest()
    feed, _r, _c = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")
    await mgr.stop_source("upstox")
    snap = mgr.get_status().get("upstox", {})
    runner.assert_eq("SR1-operator-stop-reason",
                     snap.get("stop_reason"), "operator_stop")
    runner.assert_eq("SR1-intent-recorded",
                     mgr._stop_intents.get("upstox"), "operator_stop")

    # restart intent recorded
    await mgr.start_source("upstox")
    await wait_for(lambda: feed.status()["state"] == "streaming")
    await mgr.restart_source("upstox")
    await wait_for(lambda: feed.status()["state"] == "streaming")
    runner.assert_eq("SR1-restart-intent",
                     mgr._stop_intents.get("upstox"), "restart")

    # shutdown intent
    await mgr.shutdown()
    # shutdown() signals stop; await actual task termination before reading
    # the derived stop_reason (which is only meaningful once not running).
    await wait_for(lambda: not mgr.task_running("upstox"))
    snap = mgr.get_status().get("upstox", {})
    runner.assert_eq("SR1-shutdown-reason",
                     snap.get("stop_reason"), "application_shutdown")

    # auth_required stop_reason (fresh manager)
    rest2 = CountingRest(fail_auth=True)
    feed2, _r2, _c2 = mk_upstox_feed(rest=rest2)
    mgr2 = await mk_manager(feed2)
    await mgr2.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed2.status()["state"] == "auth_required")
    snap2 = mgr2.get_status().get("upstox", {})
    runner.assert_eq("SR1-auth-required-reason",
                     snap2.get("stop_reason"), "auth_required")


async def test_th1_transition_history(runner: R) -> None:
    """TH1: bounded transition history records safe reasons (WP14)."""
    from websockets.exceptions import ConnectionClosedError
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(
        rest=rest,
        scripted_incoming=[[ConnectionClosedError(None, None)], []])
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming"
                   and len(conns) >= 2)

    trans = feed.status().get("recent_transitions", [])
    runner.assert_true("TH1-history-nonempty", len(trans) > 0)
    runner.assert_true("TH1-bounded", len(trans) <= 20)
    to_streaming = [t for t in trans if t.get("to") == "streaming"]
    runner.assert_true(
        "TH1-has-connected",
        any(t.get("reason") == "connected" for t in to_streaming))
    runner.assert_true(
        "TH1-has-reconnect",
        any(t.get("to") == "reconnecting"
             and t.get("reason") == "websocket_closed" for t in trans))
    for t in trans:
        runner.assert_not_in("TH1-no-token-in-transition", TOKEN, str(t))
    await mgr.stop_source("upstox")


async def test_th2_stop_transition_reason(runner: R) -> None:
    """TH2: operator stop records a non-empty transition reason (WP14/WP7)."""
    rest = CountingRest()
    feed, _r, _c = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")
    await mgr.stop_source("upstox")
    trans = feed.status().get("recent_transitions", [])
    stops = [t for t in trans if t.get("to") == "stopped"]
    runner.assert_true("TH2-has-stop-transition", len(stops) > 0)
    runner.assert_true(
        "TH2-stop-reason-present",
        all(t.get("reason") not in (None, "") for t in stops),
        f"stop transitions: {stops}")
    runner.assert_true(
        "TH2-stop-reason-stop_requested",
        any(t.get("reason") == "stop_requested" for t in stops))


async def test_sm1_startup_matrix(runner: R) -> None:
    """SM1: startup matrix CASE B/C/D/E/F (WP8)."""
    from brokers.upstox.feed import UpstoxFeed

    # CASE B: placeholder token -> gated, no authorize
    fb = mk_upstox_feed(token="PENDING-OAUTH-LOGIN")[0]
    mgr = await mk_manager(fb)
    await mgr.start_all({"upstox": {"enabled": True}})
    runner.assert_eq("SM1-B-gated", mgr.task_running("upstox"), False)
    runner.assert_eq("SM1-B-no-auth", fb._rest.authorize_calls, 0)

    # CASE C: expired token at construction -> gated, then start after refresh
    from datetime import datetime, timedelta, timezone
    from brokers.upstox.auth import UpstoxCredentials
    expired = UpstoxCredentials(
        access_token=TOKEN,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
    fc = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": list(KEYS)},
        credentials=expired, rest=CountingRest(),
        instrument_metadata={k: ("NSE", k) for k in KEYS})
    mgr2 = await mk_manager(fc)
    await mgr2.start_all({"upstox": {"enabled": True}})
    runner.assert_eq("SM1-C-gated", mgr2.task_running("upstox"), False)
    runner.assert_eq("SM1-C-no-auth", fc._rest.authorize_calls, 0)
    fc.update_credentials(_valid_creds())
    r = await mgr2.start_source("upstox")
    runner.assert_eq("SM1-C-start-after-refresh", r, "started")
    await wait_for(lambda: fc.status()["state"] == "streaming")
    await mgr2.stop_source("upstox")

    # CASE D: valid token -> auto-start at start_all
    fd = mk_upstox_feed()[0]
    mgr3 = await mk_manager(fd)
    await mgr3.start_all({"upstox": {"enabled": True}})
    runner.assert_true(
        "SM1-D-auto-start",
        await wait_for(lambda: fd.status()["state"] == "streaming"))
    await mgr3.stop_source("upstox")

    # CASE E: operator-stopped source skipped by a later start_all
    fe = mk_upstox_feed()[0]
    mgr4 = await mk_manager(fe)
    await mgr4.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: fe.status()["state"] == "streaming")
    await mgr4.stop_source("upstox")
    runner.assert_true("SM1-E-operator-stopped",
                       "upstox" in mgr4._operator_stopped)
    await mgr4.start_all({"upstox": {"enabled": True}})
    runner.assert_eq("SM1-E-stays-stopped",
                     mgr4.task_running("upstox"), False)
    # The initial start_all auto-started once (1 authorize); the SECOND
    # start_all must NOT re-authorize the operator-stopped source.
    runner.assert_eq("SM1-E-no-new-auth", fe._rest.authorize_calls, 1)

    # CASE F: sibling sources independent (upstox gated, fyers valid)
    fu = mk_upstox_feed(token="PENDING-OAUTH-LOGIN")[0]
    sockets: list[FyersStubWS] = []

    async def fws(token):
        ws = FyersStubWS()
        sockets.append(ws)
        return ws

    from brokers.fyers.feed import FyersFeed
    fcfg = {"source_name": "fyers", "instrument_keys": ["NSE:SBIN-EQ"],
            "app_id": "A", "access_token_getter": lambda: _fy_jwt(),
            "ws_connect": fws,
            "utc_now_iso": lambda: "2026-08-25T10:00:00+00:00"}
    ff = FyersFeed(config=fcfg, auth=object())
    mgr5 = await mk_manager(fu, ff)
    await mgr5.start_all({"upstox": {"enabled": True},
                          "fyers": {"enabled": True}})
    runner.assert_eq("SM1-F-upstox-gated", mgr5.task_running("upstox"), False)
    runner.assert_true(
        "SM1-F-fyers-started",
        await wait_for(lambda: ff.status()["state"] == "streaming"))
    await mgr5.stop_source("fyers")


async def test_lk1_leak_stress_start_stop(runner: R) -> None:
    """LK1: 20 start/stop cycles leak no task or socket (WP25)."""
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(token="PENDING-OAUTH-LOGIN", rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    feed.update_credentials(_valid_creds())
    for _ in range(20):
        await mgr.start_source("upstox")
        await wait_for(lambda: feed.status()["state"] == "streaming")
        await mgr.stop_source("upstox")
    runner.assert_eq("LK1-final-stopped", mgr.task_running("upstox"), False)
    runner.assert_eq("LK1-exactly-20-sockets", len(conns), 20)
    runner.assert_true("LK1-all-sockets-closed", all(c.closed for c in conns))
    runner.assert_eq("LK1-authorize-20", rest.authorize_calls, 20)
    runner.assert_eq("LK1-zero-tasks-left", mgr._bg_manager.active_count, 0)


async def test_lk2_restart_stress(runner: R) -> None:
    """LK2: 10 restart cycles leak no socket (WP25)."""
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")
    for _ in range(10):
        await mgr.restart_source("upstox")
        await wait_for(lambda: feed.status()["state"] == "streaming")
    runner.assert_eq("LK2-11-sockets", len(conns), 11)
    runner.assert_true("LK2-old-closed", all(c.closed for c in conns[:-1]))
    runner.assert_eq("LK2-authorize-11", rest.authorize_calls, 11)
    await mgr.stop_source("upstox")


async def test_ra1_concurrent_start_stop(runner: R) -> None:
    """RA1: concurrent Start+Stop never leaks a task (WP26)."""
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(token="PENDING-OAUTH-LOGIN", rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    feed.update_credentials(_valid_creds())
    for _ in range(10):
        await asyncio.gather(
            mgr.start_source("upstox"), mgr.stop_source("upstox"),
            return_exceptions=True)
    await asyncio.sleep(0.3)
    await mgr.stop_source("upstox")
    await asyncio.sleep(0.2)
    runner.assert_eq("RA1-final-stopped", mgr.task_running("upstox"), False)
    runner.assert_true("RA1-no-orphan-sockets",
                       sum(1 for c in conns if not c.closed) <= 1)
    runner.assert_le("RA1-auth-bounded", rest.authorize_calls, 11)


async def test_ra2_concurrent_restart(runner: R) -> None:
    """RA2: concurrent Restart x5 -> exactly one task, no leak (WP26)."""
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")
    await asyncio.gather(
        *[mgr.restart_source("upstox") for _ in range(5)],
        return_exceptions=True)
    await wait_for(lambda: feed.status()["state"] == "streaming")
    await asyncio.sleep(0.2)
    runner.assert_eq("RA2-one-task", mgr.task_running("upstox"), True)
    runner.assert_true("RA2-no-orphan",
                       sum(1 for c in conns if not c.closed) <= 1)
    await mgr.stop_source("upstox")


async def test_out1_outage_recovery(runner: R) -> None:
    """OUT1: two transient 500s then success -> recovers to streaming (WP28)."""
    from brokers.upstox.errors import UpstoxRestError

    class FlakyRest:
        def __init__(self):
            self.calls = 0

        async def authorize_market_feed(self, credentials):
            self.calls += 1
            if self.calls <= 2:
                raise UpstoxRestError("transient 500", retryable=True)
            return URI_1

    rest = FlakyRest()
    feed, _r, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    runner.assert_true(
        "OUT1-recovers",
        await wait_for(lambda: feed.status()["state"] == "streaming",
                       timeout_s=10))
    runner.assert_eq("OUT1-three-attempts", rest.calls, 3)
    await mgr.stop_source("upstox")


async def test_sd1_shutdown_clean(runner: R) -> None:
    """SD1: manager.shutdown ends tasks cleanly, no pending (WP27)."""
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(rest=rest)
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming")
    await mgr.shutdown()
    await asyncio.sleep(0.2)
    runner.assert_eq("SD1-task-gone", mgr.task_running("upstox"), False)
    runner.assert_true("SD1-ws-closed", conns[0].closed)
    snap = mgr.get_status().get("upstox", {})
    runner.assert_eq("SD1-shutdown-reason",
                     snap.get("stop_reason"), "application_shutdown")


async def test_sec1_no_secrets_in_status(runner: R) -> None:
    """SEC1: status + transitions never expose token/URI (WP30)."""
    from websockets.exceptions import ConnectionClosedError
    rest = CountingRest()
    feed, _r, conns = mk_upstox_feed(
        rest=rest,
        scripted_incoming=[[ConnectionClosedError(None, None)], []])
    mgr = await mk_manager(feed)
    await mgr.start_all({"upstox": {"enabled": True}})
    await wait_for(lambda: feed.status()["state"] == "streaming"
                   and len(conns) >= 2)
    blob = json.dumps(mgr.get_status(), ensure_ascii=False)
    runner.assert_not_in("SEC1-no-token", TOKEN, blob)
    runner.assert_not_in("SEC1-no-uri", URI_1, blob)
    runner.assert_not_in("SEC1-no-code", "CODE1", blob)
    await mgr.stop_source("upstox")


async def test_fyr_fyers_readiness_parity(runner: R) -> None:
    """FY-R: Fyers readiness gate + status schema parity (WP13/WP37)."""
    from brokers.fyers.feed import FyersFeed

    holder = {"token": None}
    sockets: list[FyersStubWS] = []

    async def fws(token):
        ws = FyersStubWS()
        sockets.append(ws)
        return ws

    fcfg = {"source_name": "fyers", "instrument_keys": ["NSE:SBIN-EQ"],
            "app_id": "A", "access_token_getter": lambda: holder["token"],
            "ws_connect": fws,
            "utc_now_iso": lambda: "2026-08-25T10:00:00+00:00"}
    feed = FyersFeed(config=fcfg, auth=object())
    mgr = await mk_manager(feed)
    # No token -> gated
    await mgr.start_all({"fyers": {"enabled": True}})
    runner.assert_eq("FY-R-gated", mgr.task_running("fyers"), False)
    runner.assert_eq("FY-R-not-ready", mgr.is_ready("fyers"), False)
    runner.assert_eq("FY-R-readiness-reason",
                     mgr.readiness_reason("fyers"), "missing_token")
    # Supply token -> start works
    holder["token"] = _fy_jwt()
    r = await mgr.start_source("fyers")
    runner.assert_eq("FY-R-start", r, "started")
    await wait_for(lambda: feed.status()["state"] == "streaming")
    st = feed.status()
    runner.assert_eq("FY-R-provider", st.get("provider"), "fyers")
    runner.assert_true("FY-R-state-updated",
                      st.get("state_updated_at") is not None)
    runner.assert_true("FY-R-transitions",
                      isinstance(st.get("recent_transitions"), list))
    runner.assert_eq("FY-R-subscribed", st.get("subscribed_instruments"), 1)
    runner.assert_eq("FY-R-not-ready-none", st.get("not_ready_reason"), None)
    await mgr.stop_source("fyers")


# -- main ---------------------------------------------------------------------

async def main() -> bool:
    runner = R()

    await test_sc1_start_single_task(runner)
    await test_sc2_no_duplicate_start(runner)
    await test_sc3_restart_preserves_desired(runner)
    await test_sc4_stop_retains_credentials(runner)
    await test_sc5_auth_required_refused(runner)
    await test_sc6_transient_reconnect(runner)
    await test_sc7_401_terminal_auth_required(runner)
    await test_sc8_stop_stays_stopped(runner)
    await test_sc9_concurrent_starts_one_task(runner)
    await test_sc10_fyers_generic_api(runner)
    await test_reg1_cancel_closes_live_ws(runner)
    await test_reg2_apply_failure_isolated(runner)
    await test_reg3_status_fields(runner)
    test_log1_rotating_handler_configured(runner)
    await test_log2_transition_reason_logged(runner)
    await test_log3_no_secrets_in_log(runner)
    test_log4_broken_destination_degrades(runner)

    # Stop-reason vocabulary, transition history, startup matrix, stress.
    await test_sr1_stop_reason_vocabulary(runner)
    await test_th1_transition_history(runner)
    await test_th2_stop_transition_reason(runner)
    await test_sm1_startup_matrix(runner)
    await test_lk1_leak_stress_start_stop(runner)
    await test_lk2_restart_stress(runner)
    await test_ra1_concurrent_start_stop(runner)
    await test_ra2_concurrent_restart(runner)
    await test_out1_outage_recovery(runner)
    await test_sd1_shutdown_clean(runner)
    await test_sec1_no_secrets_in_status(runner)
    await test_fyr_fyers_readiness_parity(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
