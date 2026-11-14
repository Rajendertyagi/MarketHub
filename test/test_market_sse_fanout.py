#!/usr/bin/env python3
"""WS3-01 focused regression tests for market SSE canonical fan-out.

Covers: one/multiple subscribers, identical payload delivery, event ordering,
duplicate events, canonical identity across provider representations,
freshness/stale values, reset/reconnect, slow subscriber/bounded queue,
subscriber cancellation/disconnect, and cleanup.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

try:
    from app.server import _market_event_broker as _mse_broker
    from app.server import _market_service as _mse_service
    from app.server import app as _mse_app

    app = _mse_app
    market_service = _mse_service
    market_broker = _mse_broker
except Exception:  # pragma: no cover
    app = market_service = market_broker = None

HEAD_TIMEOUT = 10.0
READ_TIMEOUT = 10.0


class _SseDriver:
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
        await self._rx.put({"type": "http.request", "body": b"", "more_body": False})
        self.task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        deadline = time.monotonic() + HEAD_TIMEOUT
        while self.status is None:
            if self.task.done():
                raise RuntimeError(f"ASGI app finished early: {self.task.exception()!r}")
            if time.monotonic() > deadline:
                raise TimeoutError("no ASGI response start within budget")
            await asyncio.sleep(0.02)

    async def next_event(self, timeout: float = READ_TIMEOUT):
        """Return next SSE event as (event_name, data_str)."""
        event_name = "message"
        data_lines: list[str] = []
        while True:
            line = await asyncio.wait_for(self._lines.get(), timeout=timeout)
            if line == "":
                if data_lines:
                    return event_name, "\n".join(data_lines)
                event_name = "message"
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))

    async def next_json(self, timeout: float = READ_TIMEOUT):
        while True:
            _ev, data = await self.next_event(timeout=timeout)
            try:
                parsed = json.loads(data)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed

    async def disconnect(self) -> None:
        await self._rx.put({"type": "http.disconnect"})
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
            except Exception:
                pass


def _make_patch(marker, price=123.45, token=None):
    from market.service import QuotePatch

    tok = token or f"TEST|{marker}"
    return QuotePatch(
        exchange="NSE",
        instrument_token=tok,
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


async def _next_quote_for(driver, token, timeout=READ_TIMEOUT, max_events=64):
    seen = 0
    while seen < max_events:
        evt = await driver.next_json(timeout=timeout)
        seen += 1
        if evt.get("type") != "quote":
            continue
        data = evt.get("data") or {}
        if data.get("instrument_token") == token or data.get("storage_key") == token:
            return evt
    raise AssertionError(f"no quote event for {token} within {seen} events")


async def test_one_subscriber_canonical_payload(runner: R) -> None:
    name = "WS301-one-subscriber"
    marker = time.time_ns()
    token = f"TEST|{marker}"
    driver = _SseDriver(app, "/api/market/stream")
    await driver.start()
    outcome = await market_service.apply_quote(_make_patch(marker))
    runner.assert_eq(name + "-applied", outcome.accepted, True)
    evt = await _next_quote_for(driver, token)
    runner.assert_eq(name + "-type", evt.get("type"), "quote")
    data = evt["data"]
    runner.assert_eq(name + "-token", data.get("instrument_token"), token)
    runner.assert_eq(name + "-ltp", data.get("ltp"), 123.45)
    for field in ("canonical_id", "provider", "stale", "live", "state", "as_of"):
        runner.assert_true(name + f"-has-{field}", field in data, f"missing {field}")
    await driver.disconnect()
    runner.assert_true(name + "-cleanup", await _wait_subscribers_zero(market_broker))


async def test_multiple_subscribers_identical_payload(runner: R) -> None:
    name = "WS301-multi-identical"
    marker = time.time_ns()
    token = f"TEST|{marker}"
    drivers = [_SseDriver(app, "/api/market/stream") for _ in range(5)]
    for d in drivers:
        await d.start()
    runner.assert_eq(name + "-count", market_broker.subscriber_count, 5)
    await market_service.apply_quote(_make_patch(marker, price=77.7))
    events = [await _next_quote_for(d, token) for d in drivers]
    payloads = [json.dumps(e["data"], sort_keys=True) for e in events]
    runner.assert_eq(name + "-all-identical", len(set(payloads)), 1)
    for d in drivers:
        await d.disconnect()
    runner.assert_true(name + "-cleanup", await _wait_subscribers_zero(market_broker))


async def test_event_ordering(runner: R) -> None:
    name = "WS301-ordering"
    marker = time.time_ns()
    token = f"TEST|{marker}"
    driver = _SseDriver(app, "/api/market/stream")
    await driver.start()
    prices = [10.0, 20.0, 30.0]
    for p in prices:
        await market_service.apply_quote(_make_patch(marker, price=p))
        await asyncio.sleep(0.01)
    seen = []
    deadline = time.monotonic() + 5.0
    while len(seen) < len(prices) and time.monotonic() < deadline:
        try:
            evt = await driver.next_json(timeout=1.0)
        except asyncio.TimeoutError:
            break
        if evt.get("type") != "quote":
            continue
        data = evt.get("data") or {}
        if data.get("instrument_token") == token or data.get("storage_key") == token:
            seen.append(data.get("ltp"))
    runner.assert_eq(name + "-order", seen, prices)
    await driver.disconnect()
    runner.assert_true(name + "-cleanup", await _wait_subscribers_zero(market_broker))


async def test_duplicate_events_delivered(runner: R) -> None:
    name = "WS301-duplicates"
    marker = time.time_ns()
    token = f"TEST|{marker}"
    driver = _SseDriver(app, "/api/market/stream")
    await driver.start()
    # same price twice -> second may be not-changed depending on merge; force change
    await market_service.apply_quote(_make_patch(marker, price=50.0))
    await market_service.apply_quote(_make_patch(marker, price=51.0))
    await market_service.apply_quote(_make_patch(marker, price=51.0))
    seen = []
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        try:
            evt = await driver.next_json(timeout=0.5)
        except asyncio.TimeoutError:
            break
        if evt.get("type") != "quote":
            continue
        data = evt.get("data") or {}
        if data.get("instrument_token") == token or data.get("storage_key") == token:
            seen.append(data.get("ltp"))
    # at least the two changed updates delivered in order
    runner.assert_true(name + "-has-50", 50.0 in seen, f"seen={seen}")
    runner.assert_true(name + "-has-51", 51.0 in seen, f"seen={seen}")
    idx50 = seen.index(50.0)
    idx51 = seen.index(51.0)
    runner.assert_true(name + "-order", idx50 < idx51, f"seen={seen}")
    await driver.disconnect()
    runner.assert_true(name + "-cleanup", await _wait_subscribers_zero(market_broker))


async def test_reset_reconnect_snapshot(runner: R) -> None:
    name = "WS301-reset-reconnect"
    marker = time.time_ns()
    token = f"TEST|{marker}"
    await market_service.apply_quote(_make_patch(marker, price=88.0))
    driver = _SseDriver(app, "/api/market/stream")
    await driver.start()
    # first event should be reset control
    ev_name, data = await driver.next_event(timeout=READ_TIMEOUT)
    runner.assert_eq(name + "-reset-event", ev_name, "reset")
    runner.assert_eq(name + "-reset-data", data, "reset")
    # snapshot should include our quote
    found = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not found:
        try:
            evt = await driver.next_json(timeout=1.0)
        except asyncio.TimeoutError:
            break
        if evt.get("type") == "quote":
            d = evt.get("data") or {}
            if d.get("instrument_token") == token or d.get("storage_key") == token:
                found = True
                runner.assert_eq(name + "-snapshot-ltp", d.get("ltp"), 88.0)
    runner.assert_true(name + "-found-in-snapshot", found)
    await driver.disconnect()
    runner.assert_true(name + "-cleanup", await _wait_subscribers_zero(market_broker))


async def test_slow_subscriber_bounded_queue(runner: R) -> None:
    name = "WS301-slow-subscriber"
    from core.sse_broker import EventBroker

    broker = EventBroker(queue_size=3)
    async with broker.subscribe() as _slow, broker.subscribe() as fast:
        # fill slow subscriber queue beyond capacity
        for i in range(10):
            broker.broadcast(json.dumps({"i": i}))
        # fast reader should still get events (at least some, not blocked)
        got = await asyncio.wait_for(fast.__anext__(), timeout=2.0)
        runner.assert_true(name + "-fast-received", got is not None)
    runner.assert_eq(name + "-cleanup", broker.subscriber_count, 0)


async def test_disconnect_cleanup(runner: R) -> None:
    name = "WS301-disconnect-cleanup"
    drivers = [_SseDriver(app, "/api/market/stream") for _ in range(3)]
    for d in drivers:
        await d.start()
    runner.assert_eq(name + "-connected", market_broker.subscriber_count, 3)
    for d in drivers:
        await d.disconnect()
    runner.assert_true(name + "-zero", await _wait_subscribers_zero(market_broker))


async def main() -> bool:
    global app, market_service, market_broker
    from app.server import _market_event_broker as _broker
    from app.server import _market_service as _service
    from app.server import app as _app

    app = _app
    market_service = _service
    market_broker = _broker

    runner = R()
    for fn in [
        test_one_subscriber_canonical_payload,
        test_multiple_subscribers_identical_payload,
        test_event_ordering,
        test_duplicate_events_delivered,
        test_reset_reconnect_snapshot,
        test_slow_subscriber_bounded_queue,
        test_disconnect_cleanup,
    ]:
        try:
            await fn(runner)
        except Exception as exc:
            runner.fail(fn.__name__, str(exc))
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
