#!/usr/bin/env python3
"""FYERS reconnect + dynamic symbol-mapping regression suite.

Deterministic, fake sockets only - NO broker session and NO network, so
the suite runs without a live FYERS login.

It locks down the reconnect contract:

  1. a VALID cached HSM mapping is reused after a reconnect (no wasted
     symbol-token request);
  2. an instrument removed during an outage is NOT re-subscribed after the
     reconnect - even when its cached mapping is still around;
  3. an instrument added during an outage is RESOLVED before the
     subscription frame is sent (its raw API identifier never reaches the
     wire);
  4. an instrument whose resolution FAILS is reported explicitly, never put
     on the wire, and does not stop the unrelated instruments from being
     subscribed;
  5. the reconnect restores EXACTLY the currently desired subscription set;
  6. no frame is ever written through a closed or retired socket (also
     during a concurrent shutdown and reconnect);
  7. a connected socket without market-data frames is NOT reported as live
     delivery ("awaiting_data").
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

from brokers.fyers.hsm_symbols import Resolution

_SBIN = "NSE:SBIN-EQ"
_TCS = "NSE:TCS-EQ"
_TCS_OPT = "NSE:TCS25OCT2000CE"

_TOPIC_SBIN = "sf|nse_cm|3045"
_TOPIC_TCS = "sf|nse_cm|11529"
_TOPIC_TCS_OPT = "sf|nse_fo|51352"


# ---------------------------------------------------------------------------
# socket double
# ---------------------------------------------------------------------------

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
        return b""

    async def close(self):
        self.close_calls += 1
        self.state = _State.CLOSED


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


# ---------------------------------------------------------------------------
# feed factory
# ---------------------------------------------------------------------------

class _Resolver:
    """A wire resolver that records every lookup it is asked to do."""

    def __init__(self, mapping: dict[str, str]):
        self.mapping = dict(mapping)
        self.calls: list[list[str]] = []

    def __call__(self, symbols, token):
        self.calls.append(list(symbols))
        res = Resolution()
        for sym in symbols:
            if sym in self.mapping:
                res.resolved[sym] = self.mapping[sym]
            else:
                res.unresolved[sym] = "broker rejected the symbol"
        return res


def _feed(*, resolver, keys=(_SBIN, _TCS)):
    """A feed with a fresh socket per session (a real client reconnects)."""
    from brokers.fyers.feed import FyersFeed

    sockets: list[_StubSocket] = []

    async def ws_connect(token):
        ws = _StubSocket(f"ws{len(sockets)}")
        sockets.append(ws)
        return ws

    cfg = {
        "source_name": "fyers",
        "instrument_keys": list(keys),
        "app_id": "APP-1",
        "access_token_getter": lambda: _jwt_with_hsm_key("HSMSYNTH"),
        "ws_connect": ws_connect,
        "wire_symbol_resolver": resolver,
        "utc_now_iso": lambda: "2026-10-22T10:00:00+00:00",
    }
    feed = FyersFeed(config=cfg, auth=object(), market_service=None)
    return feed, sockets


def _jwt_with_hsm_key(hsm_key: str = "HSMSYNTH") -> str:
    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{b64({'alg': 'none'})}.{b64({'hsm_key': hsm_key})}."


def _subscribe_frames(ws: _StubSocket) -> list[bytes]:
    return [f for f in ws.sent if len(f) >= 3 and f[2] == 4]


def _wire_payload(sockets) -> bytes:
    return b"".join(b"".join(_subscribe_frames(ws)) for ws in sockets)


async def _run_briefly(feed, seconds: float = 0.3):
    stop = asyncio.Event()
    task = asyncio.create_task(feed.run(None, stop))
    await asyncio.sleep(seconds)
    stop.set()
    try:
        await asyncio.wait_for(task, timeout=3)
    except asyncio.TimeoutError:
        task.cancel()


# ---------------------------------------------------------------------------
# 1. a VALID cached mapping is reused after a reconnect
# ---------------------------------------------------------------------------

def test_a_valid_cached_mapping_is_reused_after_a_reconnect() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN, _TCS: _TOPIC_TCS})
    feed, sockets = _feed(resolver=resolver)

    async def _scenario():
        await _run_briefly(feed, 0.3)
        calls_after_first_session = len(resolver.calls)
        await _run_briefly(feed, 0.3)     # reconnect
        return calls_after_first_session

    calls_after_first_session = asyncio.run(_scenario())

    assert len(sockets) == 2, "the feed must have reconnected"
    assert len(resolver.calls) == calls_after_first_session, (
        "a reconnect must NOT spend a symbol-token request on symbols that "
        "already have a valid wire mapping")
    assert resolver.calls[0] == [_SBIN, _TCS], (
        "the first session resolves the whole desired set in one lookup")
    payload = b"".join(_subscribe_frames(sockets[1]))
    assert _TOPIC_SBIN.encode() in payload
    assert _TOPIC_TCS.encode() in payload


def test_a_cached_mapping_is_reused_for_a_newly_added_instrument() -> None:
    """A valid cached mapping is not re-resolved when it is re-demanded."""
    resolver = _Resolver({_SBIN: _TOPIC_SBIN, _TCS: _TOPIC_TCS})
    feed, sockets = _feed(resolver=resolver)

    async def _scenario():
        await _run_briefly(feed, 0.3)
        # Drop the demand, but keep the mapping (the cache outlives the
        # demand - the reconnect must not trust it blindly).
        await feed.remove_instruments([_TCS])
        await feed.add_instruments([_TCS])
        calls = len(resolver.calls)
        await _run_briefly(feed, 0.3)
        return calls

    before = asyncio.run(_scenario())

    # The re-add resolved the symbol again (its mapping was dropped with
    # the demand), but the RECONNECT must not resolve it a second time.
    assert len(resolver.calls) >= 2
    assert len(resolver.calls) == before, (
        "the reconnect must not re-resolve an already valid mapping")


# ---------------------------------------------------------------------------
# 2. a symbol removed during the outage is not re-subscribed
# ---------------------------------------------------------------------------

def test_a_symbol_removed_during_the_outage_is_not_resubscribed() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN, _TCS: _TOPIC_TCS})
    feed, sockets = _feed(resolver=resolver)

    async def _scenario():
        await _run_briefly(feed, 0.3)
        # The demand is dropped while the feed is down.
        await feed.remove_instruments([_SBIN])
        # ... and the CACHE still holds the mapping (a resolver race can
        # leave a stale entry behind). The reconnect must consult the
        # DESIRED SET, not the cache.
        feed._hsm_symbols[_SBIN] = _TOPIC_SBIN
        await _run_briefly(feed, 0.3)

    asyncio.run(_scenario())

    assert len(sockets) == 2
    payload = b"".join(_subscribe_frames(sockets[1]))
    assert _TOPIC_SBIN.encode() not in payload, (
        "a removed instrument must not be re-subscribed after a reconnect, "
        "even when its cached mapping still exists")
    assert _TOPIC_TCS.encode() in payload, (
        "the remaining desired set must still be subscribed")


# ---------------------------------------------------------------------------
# 3. a symbol added during the outage is resolved before it is subscribed
# ---------------------------------------------------------------------------

def test_a_symbol_added_during_the_outage_is_resolved_before_sending() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN, _TCS: _TOPIC_TCS,
                          _TCS_OPT: _TOPIC_TCS_OPT})
    feed, sockets = _feed(resolver=resolver)

    async def _scenario():
        await _run_briefly(feed, 0.3)
        # Demanded while nothing is streaming.
        await feed.add_instruments([_TCS_OPT])
        await _run_briefly(feed, 0.3)

    asyncio.run(_scenario())

    assert len(sockets) == 2
    # The new instrument was resolved (looked up) before it went on the
    # wire - its wire topic, never its API identifier.
    assert any(_TCS_OPT in call for call in resolver.calls), (
        "a newly demanded instrument must be resolved")
    payload = b"".join(_subscribe_frames(sockets[1]))
    assert _TOPIC_TCS_OPT.encode() in payload, (
        "the newly demanded instrument must be subscribed after the "
        "reconnect")
    assert _TCS_OPT.encode() not in payload, (
        "the raw API identifier must never be on the wire")


# ---------------------------------------------------------------------------
# 4. a failed resolution is reported and does not block the others
# ---------------------------------------------------------------------------

def test_an_unresolved_instrument_is_never_sent_and_is_reported() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN})   # _TCS cannot be resolved
    feed, sockets = _feed(resolver=resolver)

    asyncio.run(_run_briefly(feed, 0.3))

    status = feed.status()
    assert _TCS in status["unresolved_symbols"], (
        "a failed resolution must be reported explicitly")
    payload = _wire_payload(sockets)
    assert _TCS.encode() not in payload, (
        "an unresolved instrument must never be put on the wire")
    assert _TOPIC_SBIN.encode() in payload, (
        "an unrelated instrument must still be subscribed")
    # The session reached "streaming" - a failed resolution of ONE
    # instrument must not stop the session.
    transitions = [t.get("to") for t in feed.status()["recent_transitions"]]
    assert "streaming" in transitions, (
        "a failed resolution must not stop the session")


def test_a_silent_resolver_does_not_lose_an_instrument_silently() -> None:
    """A resolver that returns neither a mapping nor a reason is reported."""

    def _silent_resolver(symbols, token):
        return Resolution()

    feed, sockets = _feed(resolver=_silent_resolver)

    asyncio.run(_run_briefly(feed, 0.3))

    status = feed.status()
    assert _SBIN in status["unresolved_symbols"], (
        "an instrument that was not resolved must be reported, not dropped "
        "silently")
    payload = _wire_payload(sockets)
    assert _SBIN.encode() not in payload


# ---------------------------------------------------------------------------
# 5. the reconnect restores EXACTLY the desired set
# ---------------------------------------------------------------------------

def test_the_reconnect_restores_exactly_the_desired_set() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN, _TCS: _TOPIC_TCS,
                          _TCS_OPT: _TOPIC_TCS_OPT})
    feed, sockets = _feed(resolver=resolver)

    async def _scenario():
        await _run_briefly(feed, 0.3)
        await feed.remove_instruments([_SBIN])
        await feed.add_instruments([_TCS_OPT])
        await _run_briefly(feed, 0.3)

    asyncio.run(_scenario())

    assert len(sockets) == 2
    frames = _subscribe_frames(sockets[1])
    assert frames, "the reconnect must send a subscription frame"
    payload = frames[0]
    assert _TOPIC_TCS.encode() in payload
    assert _TOPIC_TCS_OPT.encode() in payload
    assert _TOPIC_SBIN.encode() not in payload
    # No duplicate topics on the wire.
    assert payload.count(_TOPIC_TCS.encode()) == 1
    assert payload.count(_TOPIC_TCS_OPT.encode()) == 1
    # The feed reports the desired set it restored.
    status = feed.status()
    assert status["desired_instruments"] == 2
    assert status["resolved_instruments"] == 2


# ---------------------------------------------------------------------------
# 6. socket lifecycle: no write through a closed or retired socket
# ---------------------------------------------------------------------------

def test_no_frame_reaches_a_closed_socket() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN})
    feed, sockets = _feed(resolver=resolver, keys=(_SBIN,))
    ws = _StubSocket()
    ws.state = _State.CLOSED
    feed._live_ws = ws

    assert asyncio.run(feed._send_mutation(b"\x00\x01\x02")) is False
    assert ws.sent == []
    assert ws.violations == []
    status = feed.status()
    assert status["mutation_failures"] == 1
    assert status["last_mutation_error"] == "socket_not_open"


def test_a_concurrent_shutdown_and_reconnect_write_nothing_retired() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN})
    feed, sockets = _feed(resolver=resolver, keys=(_SBIN,))
    ws = _StubSocket()
    feed._live_ws = ws

    async def _race():
        await asyncio.gather(
            feed._retire_socket(ws),
            feed._send_mutation(b"\x00\x01\x02"),
            feed._send_mutation(b"\x00\x01\x03"),
            feed._send_frame(ws, b"\x00\x01\x04"),
        )

    asyncio.run(_race())

    assert ws.violations == [], (
        "no write may reach a socket that is being retired")
    assert feed._live_ws is None, "the retired socket must not stay live"


def test_repeated_close_events_produce_no_uncontrolled_send_exceptions() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN})
    feed, sockets = _feed(resolver=resolver, keys=(_SBIN,))
    ws = _StubSocket()

    async def _scenario():
        for _ in range(50):
            await asyncio.gather(
                feed._retire_socket(ws),
                feed._send_mutation(b"\x00\x01\x02"),
            )
            feed._live_ws = ws
            await feed._send_mutation(b"\x00\x01\x03")

    asyncio.run(_scenario())

    assert ws.violations == [], "no write may reach a closed socket"
    status = feed.status()
    assert status["mutation_failures"] == 50
    assert status["last_mutation_error"] == "socket_not_open"


# ---------------------------------------------------------------------------
# 7. a connected socket without frames is NOT live delivery
# ---------------------------------------------------------------------------

def test_a_connected_feed_without_frames_is_not_live() -> None:
    resolver = _Resolver({_SBIN: _TOPIC_SBIN})
    feed, sockets = _feed(resolver=resolver, keys=(_SBIN,))

    asyncio.run(_run_briefly(feed, 0.3))

    status = feed.status()
    assert status["state"] in ("streaming", "reconnecting", "stopped")
    assert status["data_flowing"] is False
    assert status["liveness"] != "live"
    if status["state"] == "streaming":
        assert status["awaiting_data"] is True
    assert status["last_data_at"] == {}


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
