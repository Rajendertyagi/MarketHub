#!/usr/bin/env python3
"""SSE Stream Tests — /events/stream regression coverage (F1).

Proves the real publication -> SSE wire path end-to-end against a REAL server
subprocess (the only harness shape that could have caught the missing-json
broadcast bug this file guards against):

  * SSE-SSE1  A published event arrives on GET /events/stream as a valid SSE
              ``data:`` frame carrying the full canonical event JSON.

NON-FINITE (NaN/Infinity) REJECTION IS COVERED ELSEWHERE — deliberately NOT
here: the MCP SDK's JSON codec converts non-finite floats to ``null`` during
argument serialization, so a remote MCP client can never deliver a raw NaN
to publish_event() (confirmed empirically on CI, 2026-08). A transport-level
NaN test would be vacuous. The deterministic rejection coverage lives in
test/test_nonfinite_rejection.py against the canonical path directly.

Wire-format notes (verified against sse-starlette 3.x source):
  * Plain-string yields are wrapped by the library as ``data: <str>`` frames,
    so no manual framing is expected from the server.
  * The stream may contain comment/ping lines (": ...") and empty data
    frames; both are skipped by the parser below.
  * Chunked transfer encoding inserts hex size lines; they carry no
    "data:"/"comment" prefix and are ignored naturally.

Bounded waits ONLY — every read has a hard timeout; no indefinite streaming.

Run independently:
    python test/test_sse_stream.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

# Allow importing helpers regardless of launch cwd.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.lifecycle import (  # noqa: E402
    get_server_port,
    get_server_url,
    restore_environment,
    start_server,
)
from helpers.runner import R  # noqa: E402
from mcp_result import safe_teardown, to_payload  # noqa: E402

# Bounded-wait budgets (seconds). No indefinite streaming waits anywhere.
SSE_HEADERS_TIMEOUT = 10.0
SSE_READ_TIMEOUT = 15.0


# ---------------------------------------------------------------------------
# Raw SSE client (stdlib asyncio only — no extra dependencies)
# ---------------------------------------------------------------------------

async def _open_sse_stream(
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a raw HTTP/1.1 connection to GET /events/stream."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    request = (
        f"GET /events/stream HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Accept: text/event-stream\r\n"
        f"Connection: keep-alive\r\n\r\n"
    )
    writer.write(request.encode("ascii"))
    await writer.drain()
    return reader, writer


async def _skip_response_headers(
    reader: asyncio.StreamReader, timeout: float = SSE_HEADERS_TIMEOUT
) -> None:
    """Read (and discard) response headers up to the blank terminator line."""

    async def _read() -> None:
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                return

    await asyncio.wait_for(_read(), timeout=timeout)


async def _next_sse_event(
    reader: asyncio.StreamReader, timeout: float = SSE_READ_TIMEOUT
) -> dict[str, Any] | None:
    """Return the next SSE message carrying a JSON object payload.

    Skips comment/ping lines and empty-data framing noise. Returns None on
    clean EOF. Raises asyncio.TimeoutError when nothing arrives within
    ``timeout`` seconds.
    """

    async def _read() -> dict[str, Any] | None:
        data_lines: list[str] = []
        while True:
            raw = await reader.readline()
            if not raw:
                return None  # EOF
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                # End of message — dispatch accumulated data lines, if any.
                if data_lines:
                    payload = "\n".join(data_lines)
                    try:
                        parsed = json.loads(payload)
                    except (json.JSONDecodeError, ValueError):
                        parsed = None
                    if isinstance(parsed, dict):
                        return parsed
                data_lines = []
                continue
            if line.startswith(":"):
                continue  # ping / comment
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
            # Other SSE fields (event:, id:, retry:) and chunk-size noise
            # are intentionally ignored.

    return await asyncio.wait_for(_read(), timeout=timeout)


async def _close_stream(writer: asyncio.StreamWriter) -> None:
    """Close the raw stream cleanly; teardown noise must not fail the test."""
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_sse_delivers_published_event(runner: R) -> None:
    """SSE-SSE1: published event arrives on /events/stream as valid SSE JSON."""
    name = "SSE-SSE1-delivery"
    port = get_server_port()
    url = get_server_url()

    from mcp import ClientSession  # noqa: E402
    from mcp.client.streamable_http import (  # noqa: E402
        streamable_http_client as streamablehttp_client,
    )

    marker = int(time.time() * 1000)
    etype = f"test.sse.{marker}"

    reader, writer = await _open_sse_stream(port)
    try:
        await _skip_response_headers(reader)

        # Publish through the REAL application path (MCP tool boundary).
        async with streamablehttp_client(url) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                result = await session.call_tool(
                    "event_publish",
                    {
                        "event_type": etype,
                        "source": "sse-test",
                        "data": {"marker": marker},
                    },
                )
                payload = to_payload(result)

        runner.assert_eq(name + "-published", payload.get("status"), "published")
        event_id = (payload.get("event") or {}).get("id")
        runner.assert_true(
            name + "-has-id", bool(event_id), "no event id in publish result"
        )

        # Bounded wait for the matching frame on the wire.
        received: dict[str, Any] | None = None
        start = time.monotonic()
        while time.monotonic() - start < SSE_READ_TIMEOUT:
            budget = max(0.5, SSE_READ_TIMEOUT - (time.monotonic() - start))
            try:
                evt = await _next_sse_event(reader, timeout=budget)
            except asyncio.TimeoutError:
                break
            if evt is None:
                break  # EOF — stream closed unexpectedly
            if evt.get("type") == etype and evt.get("id") == event_id:
                received = evt
                break

        runner.assert_true(
            name,
            received is not None,
            f"no SSE frame for {etype} within {SSE_READ_TIMEOUT}s",
        )
        if received is not None:
            for field in ("id", "type", "source", "timestamp", "data", "persistent"):
                runner.assert_in(name + f"-field-{field}", field, received)
            runner.assert_eq(
                name + "-data-marker",
                (received.get("data") or {}).get("marker"),
                marker,
            )
    finally:
        await _close_stream(writer)


# ---------------------------------------------------------------------------
# Test ordering
# ---------------------------------------------------------------------------
_TESTS = [
    test_sse_delivers_published_event,
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    import atexit

    atexit.register(restore_environment)
    print("Starting server...")
    await start_server()
    runner = R()
    try:
        print()
        print("=" * 50)
        print("  SSE Stream Tests")
        print("=" * 50)
        for fn in _TESTS:
            try:
                await fn(runner)
            except Exception as exc:
                doc = fn.__doc__ or fn.__name__
                label = doc.split(":")[0].strip() if doc else fn.__name__
                runner.fail(label, str(exc))
    finally:
        safe_teardown(restore_environment)
    success = runner.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
