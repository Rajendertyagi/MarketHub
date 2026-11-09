#!/usr/bin/env python3
"""The listen address comes from config.json - and tests never rewrite it.

Why this matters: ``config.json`` is the user's live configuration (it is
gitignored). The test harness used to write a RANDOM free port into it and
restore it afterwards - if a run was killed, the file was left with the test
port and the application then bound that port instead of the configured one
(the FYERS OAuth callback, which uses ``public_base_url``, broke).

Contract proven here:
  * the host and the port come from ``config.json``;
  * an explicit per-run override (``MARKETHUB_HOST`` / ``MARKETHUB_PORT``)
    wins WITHOUT touching the file;
  * starting and stopping a test server does not change a single byte of
    the user's config.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

_CONFIG = _PROJECT_DIR / "config.json"


def _config_bytes() -> bytes:
    return _CONFIG.read_bytes() if _CONFIG.exists() else b""


def test_host_and_port_come_from_the_config_file() -> None:
    from app.config import load_config

    cfg = load_config(str(_CONFIG))
    raw = json.loads(_CONFIG.read_text(encoding="utf-8-sig"))
    assert cfg["host"] == raw["host"]
    assert cfg["port"] == raw["port"]
    assert cfg["port"] == 7070, (
        "the application's listen port must be the configured one (7070)")


def test_env_override_wins_without_touching_the_file(monkeypatch) -> None:
    from app.config import load_config

    before = _config_bytes()
    monkeypatch.setenv("MARKETHUB_PORT", "7099")
    monkeypatch.setenv("MARKETHUB_HOST", "127.0.0.1")
    cfg = load_config(str(_CONFIG))
    assert cfg["port"] == 7099
    assert cfg["host"] == "127.0.0.1"
    assert _config_bytes() == before, "the override must not rewrite the file"


def test_invalid_override_is_rejected() -> None:
    from app.config import ConfigError, load_config

    os.environ["MARKETHUB_PORT"] = "not-a-port"
    try:
        try:
            load_config(str(_CONFIG))
        except ConfigError:
            return
        raise AssertionError("a non-numeric port override must be rejected")
    finally:
        os.environ.pop("MARKETHUB_PORT", None)


def test_starting_a_test_server_does_not_modify_the_config() -> None:
    """A full server start/stop cycle leaves config.json byte-identical."""
    from helpers import lifecycle

    before = _config_bytes()
    lifecycle.restore_leftover_config()

    async def scenario():
        proc = await lifecycle.start_server()
        try:
            url = lifecycle.get_server_url()
            assert url
        finally:
            lifecycle.stop_server(proc)
            lifecycle.restore_environment()

    asyncio.run(scenario())
    assert _config_bytes() == before, (
        "the test harness must never rewrite the user's config.json")


def test_the_leftover_backup_is_removed_after_a_clean_run() -> None:

    asyncio.run(_start_stop())
    assert not (_PROJECT_DIR / "config.json.testbak").exists()


async def _start_stop() -> None:
    from helpers import lifecycle

    proc = await lifecycle.start_server()
    lifecycle.stop_server(proc)
    lifecycle.restore_environment()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
