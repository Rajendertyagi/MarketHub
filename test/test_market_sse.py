#!/usr/bin/env python3
"""In-process integration tests for the dedicated market SSE stream.

Phase C coverage — proves the runtime path WITHOUT any server subprocess,
network, or real broker:

    QuotePatch -> MarketService.apply_quote
        -> post-commit callback -> canonical serialization -> envelope
        -> market EventBroker -> GET /api/market/stream

  * MSSE1  quote update arrives as exactly one {"type": "quote"} envelope
           carrying canonical Quote fields and ISO-8601 UTC timestamps
  * MSSE2  two simultaneous subscribers both receive the same update;
           subscriber cleanup observable via broker.subscriber_count
  * MSSE3  generic /events/stream is NOT polluted by raw market updates
           (bounded negative wait — no unbounded streaming)
  * MSSE4  an idle/slow subscriber never blocks update application

Uses a minimal stdlib ASGI driver (no new dependencies) against the REAL
composed application object from app/server.py. The composed objects are
reached through the documented composition/test introspection seam
(app.state.market_service / app.state.market_event_broker).

Run independently:
    python test/test_market_sse.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

# Make the project root importable regardless of the working directory the
# test is launched from (mirrors test_unit_sources.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402

HEAD_TIMEOUT = 10.0
READ_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Minimal stdlib ASGI driver for GET-streaming endpoints
# ---------------------------------------------------------------------------


class _SseDriver:
    """Drive app(scope, receive, send) in-process; expose SSE lines."""

    def __init__(self, app, path: str) -> None:
        self._app = app
        self._scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"accept", b"text/event-stream")],
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 0),
        }
        self._rx: asyncio.Queue = asyncio.Queue()
        self._lines: asyncio.Queue = asyncio.Queue()
        self._buffer = b""
        self.status: int | None = None
        self.headers: list = []
        self.task: asyncio.Task | None = None

    async def _receive(self):
        return await self._rx.get()

    async def _send(self, message) -> None:
        mtype = message["type"]
        if mtype == "http.response.start":
            self.status = message["status"]
            self.headers = message.get("headers", [])
        elif mtype == "http.response.body":
            self._buffer += message.get("body", b"")
            while b"\n" in self._buffer:
                raw, _, self._buffer = self._buffer.partition(b"\n")
                text = raw.decode("utf-8", errors="replace").rstrip("\r")
                await self._lines.put(text)

    async def start(self) -> None:
        """Send the request and bounded-wait for the response head."""
        await self._rx.put({"type": "http.request", "body": b"", "more_body": False})
        self.task = asyncio.create_task(
            self._app(self._scope, self._receive, self._send)
        )
        deadline = time.monotonic() + HEAD_TIMEOUT
        while self.status is None:
            if self.task.done():
                raise RuntimeError(f"ASGI app finished early: {self.task.exception()!r}")
            if time.monotonic() > deadline:
                raise TimeoutError("no ASGI response start within budget")
            await asyncio.sleep(0.02)

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.decode("latin-1").lower() == name:
                return value.decode("latin-1")
        return None

    async def next_json(self, timeout: float = READ_TIMEOUT):
        """Next SSE message carrying a JSON object.

        Skips ping/comment lines and empty framing noise. Raises
        asyncio.TimeoutError when nothing arrives within ``timeout``.
        """
        data_lines: list[str] = []
        while True:
            line = await asyncio.wait_for(self._lines.get(), timeout=timeout)
            if line == "":
                if data_lines:
                    payload = "\n".join(data_lines)
                    try:
                        parsed = json.loads(payload)
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict):
                        return parsed
                data_lines = []
                continue
            if line.startswith(":"):
                continue  # keepalive ping / comment
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))

    async def disconnect(self) -> None:
        await self._rx.put({"type": "http.disconnect"})
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
            except Exception:
                pass  # teardown noise must not fail the test


def _make_patch(marker: int, price: float = 123.45):
    """Valid normalized QuotePatch for the synthetic instrument."""
    from market.service import QuotePatch

    return QuotePatch(
        exchange="NSE",
        instrument_token=f"TEST|{marker}",
        received_ts=datetime.now(timezone.utc),
        tradingsymbol="TEST",
        reported_fields={"ltp": price},
    )


async def _wait_subscribers_zero(broker, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if broker.subscriber_count == 0:
            return True
        await asyncio.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_market_stream_delivers_quote(runner: R) -> None:
    """MSSE1: patch -> service -> callback -> SSE envelope on the wire."""
    name = "MSSE1-delivery"
    marker = time.time_ns()
    driver = _SseDriver(app, "/api/market/stream")
    await driver.start()

    runner.assert_eq(name + "-status", driver.status, 200)
    ctype = driver.header("content-type") or ""
    runner.assert_true(name + "-sse-content-type",
                       ctype.startswith("text/event-stream"),
                       f"unexpected content-type: {ctype!r}")

    outcome = await market_service.apply_quote(_make_patch(marker))
    runner.assert_eq(name + "-applied",
                     (outcome.accepted, outcome.created, outcome.changed),
                     (True, True, True))

    evt = await driver.next_json()
    runner.assert_eq(name + "-envelope-type", evt.get("type"), "quote")

    data = evt.get("data") or {}
    runner.assert_eq(name + "-identity",
                     (data.get("exchange"), data.get("instrument_token"),
                      data.get("tradingsymbol")),
                     ("NSE", f"TEST|{marker}", "TEST"))
    runner.assert_eq(name + "-ltp", data.get("ltp"), 123.45)

    received_ts = data.get("received_ts")
    runner.assert_true(name + "-iso-utc-ts",
                       isinstance(received_ts, str) and received_ts.endswith("+00:00"),
                       f"expected ISO-8601 UTC string: {received_ts!r}")

    # Canonical fields only — no provider aliases anywhere in the payload.
    dumped = json.dumps(data)
    for alias in ("last_price", "lp", "net_change", "chp", "vol_traded_today"):
        runner.assert_not_in(name + f"-no-alias-{alias}", alias, dumped)

    await driver.disconnect()
    runner.assert_true(name + "-cleanup",
                       await _wait_subscribers_zero(market_broker),
                       "subscriber queue must be cleaned up after disconnect")


async def test_two_subscribers_both_receive(runner: R) -> None:
    """MSSE2: both concurrent subscribers receive the same update."""
    name = "MSSE2-two-subscribers"
    marker = time.time_ns()
    driver_a = _SseDriver(app, "/api/market/stream")
    driver_b = _SseDriver(app, "/api/market/stream")
    await driver_a.start()
    await driver_b.start()
    runner.assert_eq(name + "-subscriber-count", market_broker.subscriber_count, 2)

    await market_service.apply_quote(_make_patch(marker))

    evt_a = await driver_a.next_json()
    evt_b = await driver_b.next_json()
    runner.assert_eq(name + "-a-type", evt_a.get("type"), "quote")
    runner.assert_eq(name + "-b-type", evt_b.get("type"), "quote")
    runner.assert_eq(name + "-same-token",
                     (evt_a["data"]["instrument_token"], evt_b["data"]["instrument_token"]),
                     (f"TEST|{marker}", f"TEST|{marker}"))

    await driver_a.disconnect()
    await driver_b.disconnect()
    runner.assert_true(name + "-cleanup",
                       await _wait_subscribers_zero(market_broker))


async def test_generic_stream_not_polluted(runner: R) -> None:
    """MSSE3: raw market updates NEVER reach the generic event stream."""
    name = "MSSE3-generic-isolation"
    marker = time.time_ns()
    generic = _SseDriver(app, "/events/stream")
    market = _SseDriver(app, "/api/market/stream")
    await generic.start()
    await market.start()

    outcome = await market_service.apply_quote(_make_patch(marker))
    runner.assert_eq(name + "-market-applied", outcome.accepted, True)

    # Market path delivers...
    evt = await market.next_json()
    runner.assert_eq(name + "-market-received", evt.get("type"), "quote")

    # ...while the generic stream stays silent within a bounded window.
    polluted = None
    try:
        polluted = await generic.next_json(timeout=2.0)
    except asyncio.TimeoutError:
        pass
    runner.assert_eq(name + "-generic-silent", polluted, None)

    await market.disconnect()
    await generic.disconnect()


async def test_idle_subscriber_never_blocks(runner: R) -> None:
    """MSSE4: an idle subscriber does not block updates to another reader."""
    name = "MSSE4-idle-subscriber"
    marker = time.time_ns()
    idle = _SseDriver(app, "/api/market/stream")
    reader = _SseDriver(app, "/api/market/stream")
    await idle.start()   # opened but NEVER read from
    await reader.start()

    started = time.monotonic()
    outcome = await market_service.apply_quote(_make_patch(marker))
    elapsed = time.monotonic() - started
    runner.assert_eq(name + "-apply-nonblocking", outcome.accepted, True)
    runner.assert_true(name + "-apply-fast", elapsed < 2.0,
                       f"apply_quote took {elapsed:.2f}s with an idle subscriber")

    evt = await reader.next_json()
    runner.assert_eq(name + "-reader-got-quote", evt.get("type"), "quote")

    await idle.disconnect()
    await reader.disconnect()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> bool:
    global app, market_service, market_broker
    from app.server import app as _app
    from app.server import _market_event_broker as _broker
    from app.server import _market_service as _service

    app = _app
    market_service = _service
    market_broker = _broker

    runner = R()
    await test_market_stream_delivers_quote(runner)
    await test_two_subscribers_both_receive(runner)
    await test_generic_stream_not_polluted(runner)
    await test_idle_subscriber_never_blocks(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
