#!/usr/bin/env python3
"""FYERS dynamic subscription correctness (real wire bytes).

The bug this locks down: a dynamically added instrument was put on the
HSM wire as its RAW API identifier (``NSE:RELIANCE-EQ``), which the
socket cannot resolve - the subscription silently produced no data.

These tests assert the SERIALIZED subscription frame bytes, not merely
that "a frame was sent":

  * a dynamically added instrument is subscribed with its resolved HSM
    wire topic (``sf|<segment>|<exchange_token>``);
  * a raw API identifier is NEVER present in the frame;
  * an instrument that cannot be resolved is reported and is not put on
    the wire;
  * after a reconnect, an instrument discovered while disconnected is
    resolved and subscribed.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

from brokers.fyers.hsm_symbols import Resolution


def _auth_ok_frame() -> bytes:
    """HSM auth response with 'K' (ok) at the SDK-parsed offset."""
    import struct
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


class _StubWS:
    """Stub socket that records every frame the feed sends.

    A real client reconnects to a NEW transport, so every session gets a
    fresh stub (a closed socket is never written to again - the feed
    refuses such a write, which is exactly the behaviour under test).
    """

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.incoming: list[bytes] = []
        self.closed = False

    async def send(self, data: bytes) -> None:
        self.sent.append(bytes(data))
        # Answer the HSM auth frame so the session proceeds.
        if len(data) >= 3 and data[2] == 1:
            self.incoming.insert(0, _auth_ok_frame())

    async def recv(self):
        if self.incoming:
            return self.incoming.pop(0)
        await asyncio.sleep(3600)
        return b""

    async def close(self) -> None:
        self.closed = True


def _jwt_with_hsm_key(hsm_key: str = "HSMSYNTH") -> str:
    """Synthetic access-token JWT carrying an hsm_key claim."""
    import base64
    import json

    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'hsm_key': hsm_key})}."


def _resolver(mapping: dict[str, str]):
    """A wire resolver that resolves exactly ``mapping``."""

    def _resolve(symbols, token):
        res = Resolution()
        for sym in symbols:
            if sym in mapping:
                res.resolved[sym] = mapping[sym]
            else:
                res.unresolved[sym] = "broker rejected the symbol"
        return res

    return _resolve


def _mk_feed(*, resolver, keys=("NSE:SBIN-EQ",)):
    from brokers.fyers.feed import FyersFeed

    # Every session gets a FRESH socket (a real client reconnects to a
    # new transport). The created sockets are collected so the test can
    # assert on the wire bytes of every session.
    sockets: list[_StubWS] = []

    async def ws_connect(token):
        ws = _StubWS()
        sockets.append(ws)
        return ws

    cfg = {"source_name": "fyers",
           "instrument_keys": list(keys),
           "app_id": "APP-1",
           "access_token_getter": lambda: _jwt_with_hsm_key("HSMSYNTH"),
           "ws_connect": ws_connect,
           "wire_symbol_resolver": resolver,
           "utc_now_iso": lambda: "2026-10-21T10:00:00+00:00"}
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)
    return feed, sockets


def _subscribe_frames(ws) -> list[bytes]:
    return [f for f in ws.sent if len(f) >= 3 and f[2] == 4]


def _wire_payload(sockets) -> bytes:
    """Every subscribe frame of every session, concatenated."""
    return b"".join(b"".join(_subscribe_frames(ws)) for ws in sockets)


async def _run(feed, ws, seconds=0.25):
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(seconds)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()


def test_dynamic_add_subscribes_with_the_resolved_wire_topic() -> None:
    """The frame must carry the HSM topic, never the API identifier."""
    topic = "sf|nse_fo|123456"
    feed, sockets = _mk_feed(resolver=_resolver({
        "NSE:SBIN-EQ": "sf|nse_cm|1234",
        "NSE:RELIANCE25OCT2500CE": topic,
    }))

    async def scenario():
        # The instrument is added while the session is LIVE, so the
        # subscription frame is actually put on the wire.
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.4)
        added = await feed.add_instruments(["NSE:RELIANCE25OCT2500CE"])
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=3)
        return added

    added = asyncio.run(scenario())

    assert added == 1
    frames = _subscribe_frames(sockets[0])
    assert frames, "a subscription frame must have been sent"
    payload = b"".join(frames)
    assert topic.encode() in payload, \
        "the resolved HSM wire topic must be in the frame bytes"
    assert b"NSE:RELIANCE25OCT2500CE" not in payload, \
        "the raw API identifier must never be on the wire"


def test_unresolved_instrument_is_reported_and_not_sent() -> None:
    feed, sockets = _mk_feed(resolver=_resolver({"NSE:SBIN-EQ": "sf|nse_cm|1234"}))

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.4)
        await feed.add_instruments(["NSE:UNKNOWN-EQ"])
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=3)

    asyncio.run(scenario())

    unresolved = feed.status()["unresolved_symbols"]
    assert "NSE:UNKNOWN-EQ" in unresolved, \
        "an unresolvable instrument must be reported explicitly"
    payload = _wire_payload(sockets)
    assert b"NSE:UNKNOWN-EQ" not in payload, \
        "an unresolved identifier must not be put on the wire"


def test_index_without_an_official_topic_is_not_streamed() -> None:
    """NIFTYFPI has no official HSM index topic - it must not be guessed."""
    from brokers.fyers.hsm_symbols import wire_symbol

    topic, reason = wire_symbol("NSE:NIFTYFPI150-INDEX", "101000000026137")
    assert topic is None
    assert "REST" in reason

    # A documented index IS streamable.
    topic, reason = wire_symbol("NSE:NIFTY50-INDEX", "101000000026000")
    assert topic == "if|nse_cm|Nifty 50"
    assert reason == ""


def test_reconnect_resolves_an_instrument_added_while_disconnected() -> None:
    """A contract discovered during the outage must be subscribed after it."""
    feed, sockets = _mk_feed(resolver=_resolver({
        "NSE:SBIN-EQ": "sf|nse_cm|1234",
        "NSE:TCS25OCT2000CE": "sf|nse_fo|777",
    }))

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.4)
        stop.set()
        await asyncio.wait_for(task, timeout=3)
        # The instrument is added to the desired set while nothing is
        # streaming (e.g. a workspace opened during a reconnect).
        await feed.add_instruments(["NSE:TCS25OCT2000CE"])
        # A new session starts: the desired set must be (re)resolved and
        # subscribed with the wire topic.
        stop2 = asyncio.Event()
        task2 = asyncio.create_task(feed.run(None, stop2))
        await asyncio.sleep(0.4)
        stop2.set()
        await asyncio.wait_for(task2, timeout=3)

    asyncio.run(scenario())

    # The SECOND session (the reconnect) is the one that must carry the
    # instrument that was discovered during the outage.
    assert len(sockets) == 2, "the feed must have reconnected"
    payload = b"".join(_subscribe_frames(sockets[1]))
    assert b"sf|nse_fo|777" in payload, \
        "the instrument added while disconnected must be subscribed after " \
        "the reconnect"
    assert b"NSE:TCS25OCT2000CE" not in payload


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
