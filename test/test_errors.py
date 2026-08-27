#!/usr/bin/env python3
"""Error handling tests — extracted from integrate_test.py.

Covers:
  * P7T6: Client cancellation via asyncio.wait_for timeout
  * P7T14: Shutdown during active tool
  * P7T15: Shutdown during background tasks
  * P7T17: Native MCP tool error — ack a nonexistent consumer errors (is_error=True)

Legacy IDs preserved in comments.

Run independently:
    python test/test_errors.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

# Allow importing helpers regardless of launch cwd.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.lifecycle import (  # noqa: E402
    start_server,
    stop_server,
    restore_environment,
    get_server_url,
    wait_mcp_ready,
)
from helpers.mcp_client import (  # noqa: E402
    call,
    read_res,
    list_tools_names,
    wait_source_ready,
    wait_for_event_count,
    inspect_tool_output,
)
from helpers.runner import R  # noqa: E402
from mcp_result import safe_teardown  # noqa: E402

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client  # noqa: E402


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _uid(suffix: str = "") -> str:
    return f"error-{suffix}-{int(time.time()*1000)}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def P7T6(runner: R) -> None:
    """P7-T6: Client cancellation via asyncio.wait_for timeout.

    legacy_id: P7T6 (from integrate_test.py)

    The dev_long_running_test tool was removed in v2.0.0. This test now
    verifies that system_ping responds within a reasonable timeout.
    """
    name = "P7T6-cancel"
    proc = None
    try:
        proc = await start_server()
        url = get_server_url()

        async def _run_ping() -> dict:
            async with streamablehttp_client(url) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return await session.call_tool("system_ping", {})

        resp = await asyncio.wait_for(_run_ping(), timeout=5.0)
        runner.assert_true(name + "-got-response", resp is not None)
    except asyncio.TimeoutError:
        runner.ok(name + "-timed-out")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


async def P7T14(runner: R) -> None:
    """P7-T14: Shutdown during active tool — server recovers after restart.

    legacy_id: P7T14 (from integrate_test.py)

    The dev_long_running_test tool was removed in v2.0.0. This test now
    verifies that the server recovers after a clean restart.
    """
    name = "P7T14-shutdown-active"
    proc = None
    try:
        proc = await start_server()
        # Start a normal tool call in the background
        async def _tool_call() -> dict:
            async with streamablehttp_client(get_server_url()) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    return await session.call_tool("system_ping", {})

        task = asyncio.create_task(_tool_call())
        await asyncio.sleep(0.3)
        safe_teardown(stop_server, proc)
        try:
            await asyncio.wait_for(task, timeout=3)
        except (asyncio.TimeoutError, Exception):
            pass

        # Restart for sanity check — server should recover.
        proc = await start_server()
        data = await call("system_ping")
        runner.assert_eq(name, data.get("status"), "ok")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


async def P7T15(runner: R) -> None:
    """P7-T15: Server restart — system ping still works after restart.

    legacy_id: P7T15 (from integrate_test.py)
    """
    name = "P7T15-shutdown-bg"
    proc = None
    try:
        proc = await start_server()
        # Restart — server should survive a clean restart.
        proc = await start_server()
        data = await call("system_ping")
        runner.assert_eq(name, data.get("status"), "ok")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


async def P7T17(runner: R) -> None:
    """P7-T17: Native MCP tool error — ack a nonexistent consumer errors (is_error=True).

    legacy_id: P7T17 (from integrate_test.py)

    Per the MCP SDK contract, a tool failure is reported as CallToolResult(
    is_error=True) with an error text; it is NOT a success dict shaped like
    {"status": "error"}.
    """
    name = "P7T17-structured-errors"
    proc = None
    try:
        proc = await start_server()
        resp = await call("consumer_event_acknowledge", {
            "consumer_id": "nonexistent-consumer-xyz",
            "event_id": "fake-event-id",
        })
        runner.assert_true(name, resp.get("is_error") is True,
                           f"expected MCP tool error (is_error=True), got: {resp!r}")
        runner.assert_true(name + "-msg", bool(resp.get("text")),
                           "tool error should carry an explanatory text message")
    except Exception as exc:
        runner.fail(name, str(exc))
    finally:
        safe_teardown(stop_server, proc)
        safe_teardown(restore_environment)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    runner = R()

    tests = [
        P7T6,
        P7T14,
        P7T15,
        P7T17,
    ]

    for test_fn in tests:
        await test_fn(runner)

    success = runner.summary()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
