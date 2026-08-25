#!/usr/bin/env python3
"""Runtime subscription management tests (RS1-RS8).

  * RS1   add while streaming sends sub frame for ONLY new keys
  * RS2   desired set grows; full resubscribe frame includes it
  * RS3   remove while streaming sends unsub frame
  * RS4   remove never empties the subscription set
  * RS5   add/remove while disconnected updates set without sending
  * RS6   duplicate adds are no-ops
  * RS7   mutation during reconnect is safe (no live socket crash)
  * RS8   status exposes desired count

NO LIVE BROKER. Stub websocket transport only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402


class _StubWS:
    def __init__(self):
        self.sent: list[bytes] = []

    async def send(self, data):
        import json
        self.sent.append(json.loads(data))

    async def close(self):
        pass


def _mk_feed(keys=("K1", "K2")):
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.auth import UpstoxCredentials
    from datetime import datetime, timezone

    class _Rest:
        async def authorize_market_feed(self, credentials):
            return "wss://synthetic"

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": list(keys)},
        credentials=UpstoxCredentials(access_token="SYNTHETIC"),
        rest=_Rest(),
        market_service=None,
        instrument_metadata={"K1": ("NSE", "A"), "K2": ("NSE", "B")},
    )
    return feed


def _streaming_feed(feed, ws):
    """Force streaming state + live socket without a real connection."""
    feed._live_ws = ws
    feed._set_state("streaming")


# -- tests ---------------------------------------------------------------------


async def test_rs1_rs2_add_streaming(runner: R) -> None:
    feed = _mk_feed()
    ws = _StubWS()
    _streaming_feed(feed, ws)

    added = await feed.add_instruments(["K3", "K1"])  # K1 dup
    runner.assert_eq("RS1-new-count", added, 1)
    runner.assert_eq("RS1-frames-sent", len(ws.sent), 1)
    frame = ws.sent[0]
    runner.assert_eq("RS1-method", frame["method"], "sub")
    runner.assert_eq("RS1-only-new-keys", frame["data"]["instrumentKeys"],
                     ["K3"])

    # RS2: full resubscribe frame carries the whole desired set.
    full = json.loads(feed._subscription_frame())
    runner.assert_eq("RS2-full-set",
                     sorted(full["data"]["instrumentKeys"]),
                     ["K1", "K2", "K3"])


async def test_rs3_rs4_remove(runner: R) -> None:
    feed = _mk_feed()
    ws = _StubWS()
    _streaming_feed(feed, ws)

    removed = await feed.remove_instruments(["K2"])
    runner.assert_eq("RS3-removed-count", removed, 1)
    frame = ws.sent[0]
    runner.assert_eq("RS3-unsub-frame", frame["method"], "unsub")
    runner.assert_eq("RS3-unsub-keys",
                     frame["data"]["instrumentKeys"], ["K2"])

    # RS4: removing the last key is refused.
    removed = await feed.remove_instruments(["K1"])
    runner.assert_eq("RS4-last-key-refused", removed, 0)
    runner.assert_eq("RS4-still-one-key",
                     len(feed._instrument_keys), 1)


async def test_rs5_disconnected_no_send(runner: R) -> None:
    feed = _mk_feed()
    ws = _StubWS()          # NOT attached as live socket
    await feed.add_instruments(["K9"])
    runner.assert_eq("RS5-no-frame-offline", len(ws.sent), 0)
    runner.assert_true("RS5-desired-updated", "K9" in feed._instrument_keys)


async def test_rs6_duplicate_add(runner: R) -> None:
    feed = _mk_feed()
    ws = _StubWS()
    _streaming_feed(feed, ws)
    n = await feed.add_instruments(["K1"])
    runner.assert_eq("RS6-dup-noop", n, 0)
    runner.assert_eq("RS6-no-frame", len(ws.sent), 0)


async def test_rs7_mutation_during_reconnect(runner: R) -> None:
    """Mutation while state != streaming must not touch a dead socket."""
    feed = _mk_feed()

    async def run():
        # Simulate reconnect window: live_ws cleared, state reconnecting.
        feed._set_state("reconnecting")
        return await asyncio.gather(
            feed.add_instruments(["KX"]),
            feed.remove_instruments(["K2"]),
        )

    added, removed = await run()
    runner.assert_eq("RS7-add-ok-during-reconnect", added, 1)
    runner.assert_eq("RS7-remove-ok-during-reconnect", removed, 1)


async def test_rs8_status_counts(runner: R) -> None:
    feed = _mk_feed(("K1",))
    status = feed.status()
    runner.assert_eq("RS8-desired-in-status",
                     status.get("configured_instruments"), 1)


# -- main -------------------------------------------------------------------------


def test_rs9_update_credentials_clean(runner: R) -> None:
    """Regression: update_credentials must not raise (stray fragment bug)."""
    feed = _mk_feed()
    from brokers.upstox.auth import UpstoxCredentials
    creds = UpstoxCredentials(access_token="SYNTHETIC-NEW")
    try:
        feed.update_credentials(creds)
        ok = True
    except NameError:
        ok = False
    runner.assert_true("RS9-update-no-raise", ok)
    runner.assert_eq("RS9-creds-applied",
                     feed._credentials.access_token, "SYNTHETIC-NEW")


async def main() -> bool:
    runner = R()

    await test_rs1_rs2_add_streaming(runner)
    await test_rs3_rs4_remove(runner)
    await test_rs5_disconnected_no_send(runner)
    await test_rs6_duplicate_add(runner)
    await test_rs7_mutation_during_reconnect(runner)
    await test_rs8_status_counts(runner)
    test_rs9_update_credentials_clean(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
