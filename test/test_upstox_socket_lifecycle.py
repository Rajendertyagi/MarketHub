#!/usr/bin/env python3
"""Upstox socket lifecycle safety (the same send/close race, guarded).

Proves, with fake sockets only (no broker session, no network):

  1. a subscription mutation is NEVER sent to a closed socket;
  2. a concurrent shutdown and subscription mutation cannot send through a
     retired socket;
  3. socket replacement and reconnect restore the CURRENT desired
     subscription set (no duplicate subscription, no extra authorization);
  4. repeated close/reconnect events produce no uncontrolled send
     exceptions (the refusal is counted, not raised);
  5. a connected feed without an incoming frame is NOT reported as live
     delivery ("awaiting_data").

The Upstox adapter had the same defect as FYERS (a mutation could be
written to a socket that was being retired), so it carries the same
minimal guard.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

_TOKEN = "test-token"
_URI = "wss://example.invalid/feed"
_KEYS = ["NSE_EQ|A", "NSE_FO|B"]


class _State:
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"


class _StubSocket:
    """A socket double that behaves like the real one when closed.

    A write on a closed socket is a VIOLATION (the real transport raises
    there); it is recorded instead of raised so the test can assert on it.
    """

    def __init__(self, name: str = "ws"):
        self.name = name
        self.state = _State.OPEN
        self.sent: list[bytes] = []
        self.violations: list[str] = []
        self.close_calls = 0

    async def send(self, data) -> None:
        if self.state != _State.OPEN:
            self.violations.append(f"send on {self.state} socket")
            raise ConnectionError("socket is closed")
        self.sent.append(bytes(data))

    async def recv(self):
        await asyncio.sleep(3600)
        return b""

    async def close(self) -> None:
        self.close_calls += 1
        self.state = _State.CLOSED


class _FakeRest:
    def __init__(self, results):
        self.calls = 0
        self._results = list(results)

    async def authorize_market_feed(self, credentials):
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _feed(*, sockets, rest_results=None):
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    rest = _FakeRest(rest_results if rest_results is not None
                     else [_URI] * (len(sockets) + 2))
    issued = list(sockets)

    async def connector(uri, **kwargs):
        return issued.pop(0)

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": list(_KEYS)},
        credentials=UpstoxCredentials(access_token=_TOKEN),
        rest=rest,
        market_service=None,
        instrument_metadata={k: ("NSE", k.split("|")[-1]) for k in _KEYS},
        ws_connect=connector,
    )
    return feed, rest


async def _run_briefly(feed, seconds=0.25):
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(seconds)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()


def _subscribed_keys(ws) -> list[list[str]]:
    out = []
    for raw in ws.sent:
        try:
            payload = json.loads(raw.decode())
        except Exception:
            continue
        if payload.get("method") == "sub":
            out.append(list(payload.get("data", {}).get("instrumentKeys") or []))
    return out


# -- 1. a mutation is never sent to a closed socket -------------------------

def test_a_mutation_is_never_sent_to_a_closed_socket() -> None:
    ws = _StubSocket()
    feed, _rest = _feed(sockets=[ws])
    ws.state = _State.CLOSED
    feed._live_ws = ws

    sent = asyncio.run(feed._send_mutation(b'{"method":"sub"}'))

    assert sent is False, "a closed socket must not be written to"
    assert ws.sent == [], "nothing may be written to a closed socket"
    assert ws.violations == []
    status = feed.status()
    assert status["mutation_failures"] == 1
    assert status["last_mutation_error"] == "socket_not_open"


def test_a_mutation_is_not_sent_when_the_socket_is_closing() -> None:
    ws = _StubSocket()
    ws.state = _State.CLOSING
    feed, _rest = _feed(sockets=[ws])
    feed._live_ws = ws

    assert asyncio.run(feed._send_mutation(b'{"method":"sub"}')) is False
    assert ws.sent == []


# -- 2. concurrent shutdown and mutation ------------------------------------

def test_a_concurrent_shutdown_cannot_send_through_a_retired_socket() -> None:
    ws = _StubSocket()
    feed, _rest = _feed(sockets=[ws])
    feed._live_ws = ws

    async def _race():
        await asyncio.gather(
            feed._retire_socket(ws),
            feed._send_mutation(b'{"method":"sub"}'),
            feed._send_mutation(b'{"method":"unsub"}'),
        )

    asyncio.run(_race())

    assert ws.violations == [], (
        "no write may reach a socket that is being retired")
    assert feed._live_ws is None, "the retired socket must not stay live"


# -- 3. reconnect restores the desired set ----------------------------------

def test_a_reconnect_restores_the_current_desired_set() -> None:
    first = _StubSocket("first")
    second = _StubSocket("second")
    feed, rest = _feed(sockets=[first, second])

    async def _scenario():
        await _run_briefly(feed, 0.25)
        # The desired set changes while the feed is down.
        await feed.remove_instruments(["NSE_FO|B"])
        await feed.add_instruments(["NSE_FO|B"])
        await _run_briefly(feed, 0.25)

    asyncio.run(_scenario())

    assert first.close_calls == 1, "the first socket must be closed"
    assert second.close_calls == 1, "the second socket must be closed"
    subs = _subscribed_keys(second)
    assert subs, "the reconnect must re-subscribe the desired set"
    assert subs[-1] == list(feed._instrument_keys)
    assert len(subs[-1]) == len(set(subs[-1])), "no duplicate instrument key"
    # The feed authorizes the feed once per session (no extra
    # authorization per mutation).
    assert rest.calls == 2


# -- 4. repeated close/reconnect events -------------------------------------

def test_repeated_close_events_produce_no_uncontrolled_send_exceptions(
        ) -> None:
    ws = _StubSocket()
    feed, _rest = _feed(sockets=[ws])

    async def _scenario():
        for _ in range(50):
            await asyncio.gather(
                feed._retire_socket(ws),
                feed._send_mutation(b'{"method":"sub"}'),
            )
            feed._live_ws = ws
            await feed._send_mutation(b'{"method":"unsub"}')

    asyncio.run(_scenario())

    assert ws.violations == [], "no write may reach a closed socket"
    status = feed.status()
    assert status["mutation_failures"] == 50
    assert status["last_mutation_error"] == "socket_not_open"


# -- 5. connection is not live delivery -------------------------------------

def test_a_connected_feed_without_frames_is_not_live() -> None:
    ws = _StubSocket()
    feed, _rest = _feed(sockets=[ws])

    asyncio.run(_run_briefly(feed, 0.25))

    status = feed.status()
    # No market-data frame arrived: the feed must NOT claim live data.
    assert status["data_flowing"] is False
    assert status["liveness"] != "live"
    if status["state"] == "streaming":
        assert status["awaiting_data"] is True


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
