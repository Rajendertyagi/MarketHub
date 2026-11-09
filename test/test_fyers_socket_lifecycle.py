#!/usr/bin/env python3
"""FYERS socket lifecycle safety (the reconnect / send-race regression).

Proves, with fake sockets only (no broker session, no network):

  1. a subscription mutation is NEVER sent to a closed socket;
  2. a concurrent shutdown and subscription mutation cannot send through a
     retired socket;
  3. socket replacement and reconnect restore the CURRENT desired
     subscription set (no ghost subscription, no duplicate topic, no
     unnecessary re-resolution or re-authentication);
  4. repeated close/reconnect events produce no uncontrolled send
     exceptions (the refusal is counted, not raised);
  5. a connected feed without an incoming market-data frame is NOT
     reported as live delivery ("awaiting_data").
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

_TOPIC = "sf|nse_cm|1234"


def _jwt_with_hsm_key(hsm_key: str = "HSMSYNTH") -> str:
    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'hsm_key': hsm_key})}."


def _auth_ok_frame() -> bytes:
    buf = bytearray()
    buf += struct.pack("!H", 15)
    buf.append(1)
    buf.append(0)
    buf.append(0)
    buf += struct.pack("!H", 1)
    buf += b"K"
    buf += b"\x00\x00\x00"
    buf += struct.pack(">I", 5)
    return bytes(buf)


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
        self._incoming: list[bytes] = []

    async def send(self, data):
        if self.state != _State.OPEN:
            self.violations.append(f"send on {self.state} socket")
            raise ConnectionError("socket is closed")
        self.sent.append(bytes(data))
        if len(data) >= 3 and data[2] == 1:
            self._incoming.insert(0, _auth_ok_frame())

    async def recv(self):
        if self._incoming:
            return self._incoming.pop(0)
        await asyncio.sleep(3600)
        return ""

    async def close(self):
        self.close_calls += 1
        self.state = _State.CLOSED


def _feed(*, sockets, keys=("NSE:SBIN-EQ", "NSE:TCS-EQ"), resolver_calls=None):
    from brokers.fyers.feed import FyersFeed
    from brokers.fyers.hsm_symbols import Resolution

    issued = list(sockets)

    async def ws_connect(token):
        return issued.pop(0)

    def wire_resolver(symbols, token):
        if resolver_calls is not None:
            resolver_calls.append(list(symbols))
        return Resolution(resolved={s: _topic_for(s) for s in symbols})

    cfg = {
        "source_name": "fyers",
        "instrument_keys": list(keys),
        "app_id": "APP-1",
        "access_token_getter": lambda: _jwt_with_hsm_key("HSMSYNTH"),
        "ws_connect": ws_connect,
        "wire_symbol_resolver": wire_resolver,
        "utc_now_iso": lambda: "2026-10-22T10:00:00+00:00",
    }
    return FyersFeed(config=cfg, auth=object(), market_service=None)


def _topic_for(symbol: str) -> str:
    """A distinct HSM wire topic per symbol (no invented protocol)."""
    return f"sf|nse_cm|{abs(hash(symbol)) % 100000}"


def _frames(ws, resp_type: int) -> list[bytes]:
    return [f for f in ws.sent if len(f) >= 3 and f[2] == resp_type]


async def _run_briefly(feed, seconds=0.3):
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(seconds)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()


# -- 1. a mutation is never sent to a closed socket -------------------------

def test_a_mutation_is_never_sent_to_a_closed_socket() -> None:
    ws = _StubSocket()
    feed = _feed(sockets=[ws])
    ws.state = _State.CLOSED
    feed._live_ws = ws

    sent = asyncio.run(feed._send_mutation(b"\x00\x01\x02"))

    assert sent is False, "a closed socket must not be written to"
    assert ws.sent == [], "nothing may be written to a closed socket"
    assert ws.violations == []
    status = feed.status()
    assert status["mutation_failures"] == 1
    assert status["last_mutation_error"] == "socket_not_open"


def test_a_mutation_is_not_sent_when_the_socket_is_closing() -> None:
    ws = _StubSocket()
    ws.state = _State.CLOSING
    feed = _feed(sockets=[ws])
    feed._live_ws = ws

    assert asyncio.run(feed._send_mutation(b"\x00\x01\x02")) is False
    assert ws.sent == []


# -- 2. concurrent shutdown and mutation ------------------------------------

def test_a_concurrent_shutdown_cannot_send_through_a_retired_socket() -> None:
    ws = _StubSocket()
    feed = _feed(sockets=[ws])
    feed._live_ws = ws

    async def _race():
        # Both run on the same loop: the retire clears the live reference
        # UNDER the subscription lock before the socket is closed.
        await asyncio.gather(
            feed._retire_socket(ws),
            feed._send_mutation(b"\x00\x01\x02"),
            feed._send_mutation(b"\x00\x01\x03"),
        )

    asyncio.run(_race())

    assert ws.violations == [], (
        "no write may reach a socket that is being retired")
    assert feed._live_ws is None, "the retired socket must not stay live"


# -- 3. reconnect restores the desired set ----------------------------------

def test_a_reconnect_restores_the_current_desired_set() -> None:
    first = _StubSocket("first")
    second = _StubSocket("second")
    resolver_calls: list[list[str]] = []
    feed = _feed(sockets=[first, second], resolver_calls=resolver_calls)
    sbin = _topic_for("NSE:SBIN-EQ").encode()
    tcs = _topic_for("NSE:TCS-EQ").encode()

    async def _scenario():
        await _run_briefly(feed, 0.3)
        assert first.sent, "the first session must send the handshake"
        # The desired set changes while the feed is down.
        await feed.remove_instruments(["NSE:SBIN-EQ"])
        await feed.add_instruments(["NSE:SBIN-EQ"])
        resolutions_before_reconnect = len(resolver_calls)
        # A second session (reconnect) with a fresh socket.
        await _run_briefly(feed, 0.3)
        assert len(resolver_calls) == resolutions_before_reconnect, (
            "the reconnect must not resolve the symbols again")

    asyncio.run(_scenario())

    assert first.close_calls == 1, "the first socket must be closed"
    assert second.close_calls == 1, "the second socket must be closed"
    # The reconnect restored the subscription on the NEW socket.
    subscribe_frames = [f for f in second.sent if sbin in f and tcs in f]
    assert subscribe_frames, "the reconnect must re-subscribe the desired set"
    # No duplicate topic on the wire.
    assert subscribe_frames[0].count(sbin) == 1
    assert subscribe_frames[0].count(tcs) == 1


def test_a_removed_symbol_is_not_restored_by_a_reconnect() -> None:
    """A symbol nobody wants any more must not come back on the wire."""
    first = _StubSocket("first")
    second = _StubSocket("second")
    resolver_calls: list[list[str]] = []
    feed = _feed(sockets=[first, second], resolver_calls=resolver_calls)
    sbin = _topic_for("NSE:SBIN-EQ").encode()

    async def _scenario():
        await _run_briefly(feed, 0.3)
        await feed.remove_instruments(["NSE:SBIN-EQ"])
        await _run_briefly(feed, 0.3)

    asyncio.run(_scenario())

    assert not [f for f in second.sent if sbin in f], (
        "a removed symbol must not be re-subscribed after a reconnect")
    assert [f for f in second.sent if _topic_for("NSE:TCS-EQ").encode() in f], (
        "the remaining desired set must still be subscribed")


# -- 4. repeated close/reconnect events -------------------------------------

def test_repeated_close_events_produce_no_uncontrolled_send_exceptions(
        ) -> None:
    ws = _StubSocket()
    feed = _feed(sockets=[ws])

    async def _scenario():
        for _ in range(50):
            # A close event while a mutation is in flight, repeatedly.
            await asyncio.gather(
                feed._retire_socket(ws),
                feed._send_mutation(b"\x00\x01\x02"),
            )
            # The socket stays retired; a mutation against it is refused
            # (counted, never raised).
            feed._live_ws = ws
            await feed._send_mutation(b"\x00\x01\x03")

    asyncio.run(_scenario())

    assert ws.violations == [], "no write may reach a closed socket"
    status = feed.status()
    # Every refusal is counted and reported - never raised.
    assert status["mutation_failures"] == 50
    assert status["last_mutation_error"] == "socket_not_open"


# -- 5. connection is not live delivery -------------------------------------

def test_a_connected_feed_without_frames_is_not_live() -> None:
    ws = _StubSocket()
    feed = _feed(sockets=[ws])

    asyncio.run(_run_briefly(feed, 0.3))

    status = feed.status()
    assert status["state"] in ("streaming", "reconnecting", "stopped")
    # No market-data frame arrived: the feed must NOT claim live data.
    assert status["data_flowing"] is False
    assert status["liveness"] != "live"
    if status["state"] == "streaming":
        assert status["awaiting_data"] is True
    assert status["last_data_at"] == {}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
