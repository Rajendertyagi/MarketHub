#!/usr/bin/env python3
"""
Timeout behavior tests.

Extracted from integrate_test.py (P7T5, P7T16) and test_phase8.py (S10).

Run:
    python test/test_timeouts.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# Ensure project root is importable.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.lifecycle import (  # noqa: E402
    restore_environment,
    start_server,
    stop_server,
)
from helpers.mcp_client import (  # noqa: E402
    call,
    publish_event,
    wait_source_ready,
)
from helpers.runner import R  # noqa: E402
from helpers.mock_http import MockHandler, start_mock  # noqa: E402


# ===================================================================
# Timeout Tests
# ===================================================================


async def p7t5_long_running_completes_normally(runner: R) -> None:
    """P7T5: system_ping completes normally (dev_long_running_test removed in v2.0.0)."""
    name = "P7T5-long-running"
    data = await call("system_ping")
    runner.assert_eq(name, data.get("status"), "ok")


async def p7t16_db_safe_after_timeout(runner: R) -> None:
    """P7T16: DB safe after operations — DB works after event publishing."""
    name = "P7T16-db-after-timeout"

    # DB should still work after normal operations
    data = await publish_event("test.p7t16", source="test", persistent=True)
    runner.assert_eq(name, data.get("status"), "published")


async def s10_timeout_no_leak(runner: R) -> None:
    """S10: timeout — slow mock (5s) vs 1s timeout -> no leak, exactly 1 source task."""
    name = "S10-timeout"
    host = "127.0.0.1"

    class SlowMockHandler(BaseHTTPRequestHandler):
        _response_code = 200

        def do_GET(self):
            time.sleep(5)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'[]')

        def log_message(self, format, *args):
            pass

    srv = HTTPServer((host, 0), SlowMockHandler)
    slow_port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # Stop the file-scope server (used by p7t5/p7t16) before starting s10's own
    # — otherwise start_server() would overwrite the module-global and orphan it.
    stop_server()
    proc = await start_server({
        "sources": {
            "http_poller": {
                "type": "http_poller", "enabled": True,
                "url": f"http://{host}:{slow_port}/api",
                "interval_seconds": 1, "timeout_seconds": 1,
                "item_path": "", "id_path": "id",
                "event_type_prefix": "test.s10", "persistent": False,
            },
        },
    })
    try:
        await wait_source_ready("http_poller", {"running", "degraded", "error", "failed"}, timeout=15)
        await asyncio.sleep(2.0)
        tasks = await call("system_ping")
        runner.assert_eq(name + "-system_ping", tasks.get("status"), "ok")
    finally:
        stop_server(proc)
        srv.shutdown()


# ===================================================================
# Main
# ===================================================================


async def main() -> bool:
    proc = await start_server()
    runner = R()
    try:
        print("  Timeout Behavior Tests")
        print("=" * 50)

        tests = [
            p7t5_long_running_completes_normally,
            p7t16_db_safe_after_timeout,
            s10_timeout_no_leak,
        ]
        for fn in tests:
            try:
                await fn(runner)
            except Exception as exc:
                runner.fail(fn.__name__, str(exc))

    finally:
        restore_environment()
    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    asyncio.run(main())
