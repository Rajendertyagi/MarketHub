"""Focused tests for FYERS feed config runtime application (API + lifecycle).

Covers:
- strict boolean validation for enabled/tbt_enabled
- runtime reconfiguration applies to actual source instance/config
- stop+await before replacement when running (real SourceManager)
- failed cancellation does not replace old source/config (real SourceManager)
- old task cannot remain active alongside replacement (real SourceManager)
- fresh stop event used for new instance (real SourceManager)
- stopped registered source gets latest config before start
- disable preserves settings and re-enable applies them
- first-time registration receives runtime deps/injection
- start_source not_ready/unknown not reported as started
- failed start with task_running=false accurately reported
- intentionally disabled feed can have runtime config applied without running
- persistence success/read failure/write failure/missing callback reporting
- failed restart/reconfiguration not reported as successfully applied

No live broker traffic. Uses fake sources/managers and narrow real SourceManager tests.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from api.product_routes import build_fyers_auth_routes
from app.fyers_runtime_auth import FyersRuntimeAuth
from app.secrets_store import CredentialStore
from core.persistence.store import EventStore
from sources import SourceManager


class FakeSource:
    def __init__(self, name: str, config: dict):
        self.name = name
        self._config = dict(config)
        self.on_state_change = None

    def status(self):
        return {"state": "stopped", "provider": "fyers"}


class FakeSourceManager:
    def __init__(self):
        self.enabled_sources: dict[str, object] = {}
        self._configs: dict[str, dict] = {}
        self._running: set[str] = set()
        self.events: list[tuple] = []
        self.fail_restart = False
        self.fail_start = False
        self.fail_stop = False

    def register(self, source):
        self.enabled_sources[source.name] = source
        self.events.append(("register", source.name, dict(getattr(source, "_config", {}))))

    def task_running(self, name: str) -> bool:
        return name in self._running

    async def start_source(self, name: str):
        self.events.append(("start", name, dict(self._configs.get(name, {}))))
        if self.fail_start:
            raise RuntimeError("start failed")
        self._running.add(name)
        return "started"

    async def restart_source(self, name: str):
        self.events.append(("restart", name, dict(self._configs.get(name, {}))))
        if self.fail_restart:
            raise RuntimeError("restart failed")
        self._running.add(name)
        return True

    async def stop_source(self, name: str):
        self.events.append(("stop", name))
        if self.fail_stop:
            raise RuntimeError("stop failed")
        self._running.discard(name)
        return True

    async def replace_source(self, name: str, source, cfg: dict):
        # emulate: stop+await if running, then replace
        if self.task_running(name):
            self.events.append(("stop_for_replace", name))
            self._running.discard(name)
        self.enabled_sources[name] = source
        self._configs[name] = dict(cfg)
        self.events.append(("replace", name, dict(cfg), dict(getattr(source, "_config", {}))))
        return True

    def update_config(self, name: str, cfg: dict):
        self._configs[name] = dict(cfg)
        return True

    def get_status(self):
        return {}


def _make_store():
    tmp = tempfile.mkdtemp()
    return CredentialStore(EventStore(os.path.join(tmp, "e.db")))


def _build_app(*, source_manager=None, sources_cfg=None, apply_source_fn=None, start_source_fn=None, persist_source_fn=None):
    store = _make_store()
    routes = build_fyers_auth_routes(
        store,
        runtime_auth=FyersRuntimeAuth(),
        redirect_uri="http://localhost:7070/auth/fyers/callback",
        source_manager=source_manager,
        sources_cfg=sources_cfg if sources_cfg is not None else {},
        start_source_fn=start_source_fn,
        persist_source_fn=persist_source_fn,
        apply_source_fn=apply_source_fn,
    )
    return Starlette(routes=routes), store


# ---------------- API validation / behavior ----------------

def test_enabled_must_be_boolean():
    app, _ = _build_app()
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": "false"})
    assert r.status_code == 400
    assert "enabled must be a boolean" in r.json().get("error", "")


def test_tbt_enabled_must_be_boolean():
    app, _ = _build_app()
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"tbt_enabled": "false"})
    assert r.status_code == 400
    assert "tbt_enabled must be a boolean" in r.json().get("error", "")


def test_valid_boolean_inputs_accepted():
    sm = FakeSourceManager()
    sources_cfg = {}
    async def apply_fn(feed_cfg, start_if_enabled=True):
        src = FakeSource("fyers", feed_cfg)
        if "fyers" in sm.enabled_sources:
            await sm.replace_source("fyers", src, feed_cfg)
        else:
            sm.register(src)
            sm._configs["fyers"] = dict(feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers"), "started": True, "start_result": "started"}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": False, "tbt_symbols": []})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["tbt_enabled"] is False
    assert body["persisted"] is True
    assert body["runtime_applied"] is True


def test_running_source_receives_updated_tbt_settings_and_stops_before_replace():
    sm = FakeSourceManager()
    old_cfg = {"type": "fyers_feed", "enabled": True, "mode": "full", "instruments": [], "tbt_enabled": False, "tbt_symbols": []}
    old_src = FakeSource("fyers", old_cfg)
    sm.register(old_src)
    sm._configs["fyers"] = dict(old_cfg)
    sm._running.add("fyers")

    async def apply_fn(feed_cfg, start_if_enabled=True):
        new_src = FakeSource("fyers", feed_cfg)
        await sm.replace_source("fyers", new_src, feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
            return {"runtime_applied": True, "registered": True, "task_running": True, "restarted": True, "start_result": "started"}
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers")}

    sources_cfg = {"fyers": dict(old_cfg)}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:RELIANCE-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["registered"] is True
    assert body["task_running"] is True
    assert ("stop_for_replace", "fyers") in sm.events
    replace_events = [e for e in sm.events if e[0] == "replace"]
    assert replace_events, sm.events
    assert replace_events[-1][2]["tbt_enabled"] is True
    assert replace_events[-1][2]["tbt_symbols"] == ["NSE:RELIANCE-EQ"]
    assert replace_events[-1][3]["tbt_enabled"] is True
    idx_stop = sm.events.index(("stop_for_replace", "fyers"))
    idx_replace = next(i for i, e in enumerate(sm.events) if e[0] == "replace")
    assert idx_stop < idx_replace


def test_stopped_registered_source_receives_latest_config_before_start():
    sm = FakeSourceManager()
    old_cfg = {"type": "fyers_feed", "enabled": True, "mode": "full", "instruments": [], "tbt_enabled": False, "tbt_symbols": []}
    old_src = FakeSource("fyers", old_cfg)
    sm.register(old_src)
    sm._configs["fyers"] = dict(old_cfg)

    async def apply_fn(feed_cfg, start_if_enabled=True):
        new_src = FakeSource("fyers", feed_cfg)
        await sm.replace_source("fyers", new_src, feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
            return {"runtime_applied": True, "registered": True, "task_running": True, "started": True, "start_result": "started"}
        return {"runtime_applied": True, "registered": True, "task_running": False}

    sources_cfg = {"fyers": dict(old_cfg)}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:INFY-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["task_running"] is True
    replace_events = [e for e in sm.events if e[0] == "replace"]
    start_events = [e for e in sm.events if e[0] == "start"]
    assert replace_events and start_events
    assert replace_events[-1][2]["tbt_enabled"] is True
    assert start_events[-1][2]["tbt_enabled"] is True
    assert start_events[-1][2]["tbt_symbols"] == ["NSE:INFY-EQ"]


def test_disable_preserves_settings_and_reenable_applies_them():
    sm = FakeSourceManager()
    sources_cfg = {}

    async def apply_fn(feed_cfg, start_if_enabled=True):
        if "fyers" not in sm.enabled_sources:
            src = FakeSource("fyers", feed_cfg)
            sm.register(src)
            sm._configs["fyers"] = dict(feed_cfg)
        else:
            src = FakeSource("fyers", feed_cfg)
            await sm.replace_source("fyers", src, feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
        else:
            if sm.task_running("fyers"):
                await sm.stop_source("fyers")
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers")}

    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)

    r1 = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:SBIN-EQ"]})
    assert r1.status_code == 200
    assert sources_cfg["fyers"]["tbt_enabled"] is True
    assert sources_cfg["fyers"]["tbt_symbols"] == ["NSE:SBIN-EQ"]

    r2 = c.post("/api/settings/fyers/feed", json={"enabled": False, "tbt_enabled": True, "tbt_symbols": ["NSE:SBIN-EQ"]})
    assert r2.status_code == 200
    assert sources_cfg["fyers"]["enabled"] is False
    assert sources_cfg["fyers"]["tbt_enabled"] is True
    assert sources_cfg["fyers"]["tbt_symbols"] == ["NSE:SBIN-EQ"]

    r3 = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r3.status_code == 200
    body = r3.json()
    assert body["tbt_enabled"] is True
    assert body["tbt_symbols"] == ["NSE:SBIN-EQ"]
    replace_events = [e for e in sm.events if e[0] == "replace"]
    assert replace_events[-1][2]["tbt_enabled"] is True
    assert replace_events[-1][2]["tbt_symbols"] == ["NSE:SBIN-EQ"]


def test_failed_restart_not_reported_as_successfully_applied():
    sm = FakeSourceManager()
    old_cfg = {"type": "fyers_feed", "enabled": True, "mode": "full", "instruments": [], "tbt_enabled": False, "tbt_symbols": []}
    old_src = FakeSource("fyers", old_cfg)
    sm.register(old_src)
    sm._configs["fyers"] = dict(old_cfg)
    sm._running.add("fyers")

    async def apply_fn(feed_cfg, start_if_enabled=True):
        new_src = FakeSource("fyers", feed_cfg)
        await sm.replace_source("fyers", new_src, feed_cfg)
        return {"runtime_applied": False, "registered": True, "task_running": False, "error": "restart_failed:RuntimeError"}

    sources_cfg = {"fyers": dict(old_cfg)}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:RELIANCE-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is True
    assert body["runtime_applied"] is False
    assert body["registered"] is True
    assert body["task_running"] is False
    assert body.get("runtime_error") or body.get("error")
    assert body.get("restart_required") is True


def test_first_time_registration_via_apply_receives_config():
    sm = FakeSourceManager()
    sources_cfg = {}

    async def apply_fn(feed_cfg, start_if_enabled=True):
        assert feed_cfg.get("tbt_enabled") is True
        assert feed_cfg.get("tbt_symbols") == ["NSE:TCS-EQ"]
        src = FakeSource("fyers", feed_cfg)
        sm.register(src)
        sm._configs["fyers"] = dict(feed_cfg)
        if start_if_enabled:
            await sm.start_source("fyers")
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers"), "started": True, "start_result": "started"}

    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:TCS-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["registered"] is True
    assert body["task_running"] is True
    register_events = [e for e in sm.events if e[0] == "register" and e[1] == "fyers"]
    assert register_events, sm.events
    assert register_events[-1][2]["tbt_enabled"] is True
    assert register_events[-1][2]["tbt_symbols"] == ["NSE:TCS-EQ"]
    assert sm._configs["fyers"]["tbt_enabled"] is True
    assert sm._configs["fyers"]["tbt_symbols"] == ["NSE:TCS-EQ"]


def test_tbt_symbols_string_and_dedup_preserved():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        src = FakeSource("fyers", feed_cfg)
        sm.register(src)
        sm._configs["fyers"] = dict(feed_cfg)
        return {"runtime_applied": True, "registered": True, "task_running": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False, "tbt_enabled": True, "tbt_symbols": "NSE:AAA-EQ, NSE:BBB-EQ\nNSE:AAA-EQ"})
    assert r.status_code == 200
    body = r.json()
    assert body["tbt_symbols"] == ["NSE:AAA-EQ", "NSE:BBB-EQ"]


# ---------------- start result handling ----------------

def test_start_not_ready_not_reported_as_started():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": True, "registered": True, "task_running": False, "started": False, "restarted": False, "start_result": "not_ready", "error": "not_ready: source prerequisites unmet (authentication/token required)"}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": []})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["started"] is False
    assert body["restarted"] is False
    assert body["task_running"] is False
    assert body.get("start_result") == "not_ready"
    assert body.get("runtime_error")


def test_start_unknown_not_reported_as_started():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": False, "registered": False, "task_running": False, "started": False, "restarted": False, "start_result": "unknown", "error": "unknown: source not registered/configured"}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is False
    assert body["restarted"] is False
    assert body["task_running"] is False
    assert body.get("start_result") == "unknown"
    assert body.get("runtime_error")


def test_failed_start_with_task_not_running_reported_accurately():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": True, "registered": True, "task_running": False, "started": False, "restarted": False, "start_result": "started", "error": "start_failed:task_not_running"}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["registered"] is True
    assert body["started"] is False
    assert body["task_running"] is False
    assert body.get("runtime_error")


def test_disabled_feed_can_apply_config_without_running():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        src = FakeSource("fyers", feed_cfg)
        sm.register(src)
        sm._configs["fyers"] = dict(feed_cfg)
        return {"runtime_applied": True, "registered": True, "task_running": False, "started": False, "restarted": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False, "tbt_enabled": True, "tbt_symbols": ["NSE:ABC-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["runtime_applied"] is True
    assert body["registered"] is True
    assert body["task_running"] is False
    assert body.get("runtime_error") is None
    assert body.get("error") is None


# ---------------- persistence reporting ----------------

def test_persistence_success_reports_persisted_true():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": True, "registered": True, "task_running": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False})
    assert r.status_code == 200
    assert r.json()["persisted"] is True


def test_persistence_read_failure_reports_persisted_false():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": True, "registered": True, "task_running": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: False)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is False
    assert body.get("persist_error")


def test_persistence_write_failure_not_reported_as_success():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": True, "registered": True, "task_running": False}
    def bad_persist(cfg):
        raise OSError("disk full")
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=bad_persist)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is False
    assert body.get("persist_error") == "OSError"
    assert body["runtime_applied"] is True  # runtime can still succeed


def test_missing_persistence_callback_not_durable():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        return {"runtime_applied": True, "registered": True, "task_running": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is False


# ---------------- real SourceManager lifecycle tests ----------------

class _FakeBgManager:
    def __init__(self):
        self.tasks: dict[str, asyncio.Task] = {}
        self.status_map: dict[str, dict] = {}
        self.cancel_calls: list[str] = []
        self.fail_cancel: set[str] = set()
        self.hang_cancel: set[str] = set()

    async def start(self, name: str, coro):
        task = asyncio.create_task(coro, name=name)
        self.tasks[name] = task
        self.status_map[name] = {"status": "running"}
        def _done(_t):
            self.status_map[name] = {"status": "done"}
        task.add_done_callback(_done)
        return task

    def status(self, name: str):
        st = self.status_map.get(name, {"status": "missing"})
        return {name: st}

    async def cancel_and_wait(self, name: str):
        self.cancel_calls.append(name)
        if name in self.fail_cancel:
            raise RuntimeError("cancel failed")
        task = self.tasks.get(name)
        if task is None:
            self.status_map[name] = {"status": "done"}
            return
        task.cancel()
        if name in self.hang_cancel:
            # simulate task not terminating
            self.status_map[name] = {"status": "running"}
            return
        try:
            await task
        except BaseException:
            pass
        self.status_map[name] = {"status": "done"}


class _RunSource:
    def __init__(self, name: str, config: dict):
        self.name = name
        self._config = dict(config)
        self.run_count = 0
        self.stop_events = []

    async def run(self, publisher, stop_event):
        self.run_count += 1
        self.stop_events.append(stop_event)
        await stop_event.wait()

    def status(self):
        return {"state": "stopped"}


async def _init_sm(bg):
    sm = SourceManager()
    await sm.initialize(bg, store=None, bus=None, metrics=None)
    return sm


def test_real_replace_stops_and_awaits_before_replacing():
    async def _run():
        bg = _FakeBgManager()
        sm = await _init_sm(bg)
        old = _RunSource("fyers", {"v": 1})
        new = _RunSource("fyers", {"v": 2})
        sm.register(old)
        sm._configs["fyers"] = {"v": 1}
        await sm._start_one("fyers", old, {"v": 1})
        await asyncio.sleep(0)
        assert sm.task_running("fyers") is True
        await sm.replace_source("fyers", new, {"v": 2})
        assert bg.cancel_calls == ["source:fyers"]
        assert sm.task_running("fyers") is False
        assert sm.enabled_sources["fyers"] is new
        assert sm._configs["fyers"] == {"v": 2}
        assert sm._stop_events["fyers"] is not old.stop_events[-1]
    asyncio.run(_run())


def test_real_replace_failed_cancel_preserves_old_source_and_config():
    async def _run():
        bg = _FakeBgManager()
        bg.fail_cancel.add("source:fyers")
        sm = await _init_sm(bg)
        old = _RunSource("fyers", {"v": 1})
        new = _RunSource("fyers", {"v": 2})
        sm.register(old)
        sm._configs["fyers"] = {"v": 1}
        await sm._start_one("fyers", old, {"v": 1})
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            await sm.replace_source("fyers", new, {"v": 2})
        assert sm.enabled_sources["fyers"] is old
        assert sm._configs["fyers"] == {"v": 1}
    asyncio.run(_run())


def test_real_replace_aborts_if_task_still_running_after_stop():
    async def _run():
        bg = _FakeBgManager()
        bg.hang_cancel.add("source:fyers")
        sm = await _init_sm(bg)
        old = _RunSource("fyers", {"v": 1})
        new = _RunSource("fyers", {"v": 2})
        sm.register(old)
        sm._configs["fyers"] = {"v": 1}
        await sm._start_one("fyers", old, {"v": 1})
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError):
            await sm.replace_source("fyers", new, {"v": 2})
        assert sm.enabled_sources["fyers"] is old
        assert sm._configs["fyers"] == {"v": 1}
        assert sm.task_running("fyers") is True
    asyncio.run(_run())


def test_real_replace_uses_fresh_stop_event_for_new_instance():
    async def _run():
        bg = _FakeBgManager()
        sm = await _init_sm(bg)
        old = _RunSource("fyers", {"v": 1})
        new = _RunSource("fyers", {"v": 2})
        sm.register(old)
        sm._configs["fyers"] = {"v": 1}
        await sm._start_one("fyers", old, {"v": 1})
        await asyncio.sleep(0)
        old_ev = sm._stop_events["fyers"]
        await sm.replace_source("fyers", new, {"v": 2})
        new_ev = sm._stop_events["fyers"]
        assert new_ev is not old_ev
        assert not new_ev.is_set()
        # start new instance and ensure it runs with fresh event
        await sm._start_one("fyers", new, {"v": 2})
        await asyncio.sleep(0)
        assert sm.task_running("fyers") is True
        assert new.stop_events[-1] is new_ev
        new_ev.set()
        await asyncio.sleep(0)
    asyncio.run(_run())


# ---------------- persistence function tests (real function, controlled config path) ----------------

def test_persist_fyers_source_config_success(tmp_path, monkeypatch):
    import app.server as srv
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"sources": {"other": {"type": "x"}}, "keep": 1}), encoding="utf-8")
    monkeypatch.setattr(srv, "CONFIG_PATH", str(cfg_path))
    ok = srv._persist_fyers_source_config({"type": "fyers_feed", "enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:AAA-EQ"]})
    assert ok is True
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["keep"] == 1
    assert data["sources"]["other"]["type"] == "x"
    assert data["sources"]["fyers"]["tbt_enabled"] is True


def test_persist_fyers_source_config_read_failure_returns_false(tmp_path, monkeypatch):
    import app.server as srv
    cfg_path = tmp_path / "config.json"
    # missing file -> read failure
    monkeypatch.setattr(srv, "CONFIG_PATH", str(cfg_path))
    ok = srv._persist_fyers_source_config({"type": "fyers_feed", "enabled": True})
    assert ok is False
    assert not cfg_path.exists()


def test_persist_fyers_source_config_write_failure_raises(tmp_path, monkeypatch):
    import app.server as srv
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({"keep": 1}), encoding="utf-8")
    monkeypatch.setattr(srv, "CONFIG_PATH", str(cfg_path))
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        srv._persist_fyers_source_config({"type": "fyers_feed", "enabled": True})
    # original preserved
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data == {"keep": 1}


# ---------------- real _apply_fyers_feed_config start result handling ----------------

class _ApplyFakeSM:
    def __init__(self, start_result="started"):
        self.enabled_sources = {}
        self._configs = {}
        self._running = set()
        self.start_result = start_result
    def register(self, source):
        self.enabled_sources[source.name] = source
    def task_running(self, name):
        return name in self._running
    async def start_source(self, name):
        if self.start_result == "started":
            self._running.add(name)
        return self.start_result
    async def replace_source(self, name, source, cfg):
        self.enabled_sources[name] = source
        self._configs[name] = dict(cfg)
        return True
    def update_config(self, name, cfg):
        self._configs[name] = dict(cfg)
        return True

def _patch_apply_env(monkeypatch, sm):
    import app.server as srv
    monkeypatch.setattr(srv, "_source_manager", sm)
    monkeypatch.setattr(srv, "_market_service", object())
    monkeypatch.setattr(srv, "_instrument_catalog", object())
    monkeypatch.setattr(srv, "_fyers_source_name", None)
    monkeypatch.setattr(srv, "_on_source_state_change", lambda *a, **k: None)
    monkeypatch.setattr(srv, "_inject_fyers_source_config", lambda cfgs: None)
    monkeypatch.setattr(srv, "_persist_fyers_source_config", lambda cfg: True)
    import sources.registry as reg
    def fake_create(cfg, market_service=None):
        return FakeSource("fyers", cfg)
    monkeypatch.setattr(reg, "_create_fyers_feed", fake_create)

def test_apply_reports_not_ready_without_claiming_started(monkeypatch):
    import app.server as srv
    sm = _ApplyFakeSM(start_result="not_ready")
    _patch_apply_env(monkeypatch, sm)
    res = asyncio.run(srv._apply_fyers_feed_config({"type":"fyers_feed","enabled":True,"mode":"full","instruments":[],"tbt_enabled":True,"tbt_symbols":[]}))
    assert res["runtime_applied"] is True
    assert res["registered"] is True
    assert res["started"] is False
    assert res["restarted"] is False
    assert res["task_running"] is False
    assert res["start_result"] == "not_ready"
    assert "not_ready" in (res.get("error") or "")

def test_apply_reports_unknown_without_claiming_started(monkeypatch):
    import app.server as srv
    sm = _ApplyFakeSM(start_result="unknown")
    _patch_apply_env(monkeypatch, sm)
    res = asyncio.run(srv._apply_fyers_feed_config({"type":"fyers_feed","enabled":True,"mode":"full","instruments":[],"tbt_enabled":False,"tbt_symbols":[]}))
    assert res["started"] is False
    assert res["restarted"] is False
    assert res["task_running"] is False
    assert res["start_result"] == "unknown"
    assert "unknown" in (res.get("error") or "")


# ---------------- legacy fallback (no apply_source_fn) ----------------

class _LegacySM:
    def __init__(self, *, running=False, registered=True, start_result="started", restart_ok=True, stop_ok=True):
        self.enabled_sources = {"fyers": object()} if registered else {}
        self._running = {"fyers"} if running else set()
        self.start_result = start_result
        self.restart_ok = restart_ok
        self.stop_ok = stop_ok
        self.start_calls = 0
        self.restart_calls = 0
        self.stop_calls = 0
        self.fail_restart = False
        self.fail_stop = False
        self.keep_running_after_stop = False
        self.set_running_on_start = True
        self.set_running_on_restart = True

    def task_running(self, name):
        return name in self._running

    async def start_source(self, name):
        self.start_calls += 1
        if self.start_result == "started" and self.set_running_on_start:
            self._running.add(name)
        return self.start_result

    async def restart_source(self, name):
        self.restart_calls += 1
        if self.fail_restart:
            raise RuntimeError("restart boom")
        # restart stops old task then starts new one if successful
        self._running.discard(name)
        if self.restart_ok and self.set_running_on_restart:
            self._running.add(name)
        return self.restart_ok

    async def stop_source(self, name):
        self.stop_calls += 1
        if self.fail_stop:
            raise RuntimeError("stop boom")
        if self.stop_ok and not self.keep_running_after_stop:
            self._running.discard(name)
        return self.stop_ok


def _build_legacy_app(*, source_manager=None, sources_cfg=None, start_source_fn=None, persist_source_fn=lambda cfg: True):
    store = _make_store()
    routes = build_fyers_auth_routes(
        store,
        runtime_auth=FyersRuntimeAuth(),
        redirect_uri="http://localhost:7070/auth/fyers/callback",
        source_manager=source_manager,
        sources_cfg=sources_cfg if sources_cfg is not None else {},
        start_source_fn=start_source_fn,
        persist_source_fn=persist_source_fn,
        apply_source_fn=None,
    )
    return Starlette(routes=routes)


def test_legacy_start_callback_returns_normally_but_task_not_running():
    sm = _LegacySM(running=False, registered=True)
    async def start_fn():
        return None  # returns without raising, but task never starts
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=start_fn)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:AAA-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is False
    assert body["task_running"] is False
    assert body["runtime_applied"] is False
    assert body["runtime_config_applied"] is False
    assert body.get("runtime_error")
    assert body.get("restart_required") is True


def test_legacy_start_callback_none_but_task_transitions_to_running():
    sm = _LegacySM(running=False, registered=True)
    async def start_fn():
        sm._running.add("fyers")  # genuinely transitions to running
        return None
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=start_fn)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["started"] is True
    assert body["task_running"] is True
    assert body["runtime_applied"] is False
    assert body["runtime_config_applied"] is False
    assert body.get("runtime_error") is None


def test_legacy_start_source_manager_not_ready_or_unknown():
    for sr in ("not_ready", "unknown"):
        sm = _LegacySM(running=False, registered=True, start_result=sr)
        sm.set_running_on_start = False
        app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
        c = TestClient(app)
        r = c.post("/api/settings/fyers/feed", json={"enabled": True})
        assert r.status_code == 200
        body = r.json()
        assert body["started"] is False
        assert body["task_running"] is False
        assert body.get("start_result") == sr
        assert body.get("runtime_error")
        assert body["runtime_applied"] is False


def test_legacy_already_running_no_new_start():
    sm = _LegacySM(running=True, registered=True)
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    # running path takes restart branch, not start
    assert sm.start_calls == 0
    assert sm.restart_calls == 1
    assert body["started"] is False


def test_legacy_restart_success_but_task_not_running():
    sm = _LegacySM(running=True, registered=True, restart_ok=True)
    sm.set_running_on_restart = False
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["restarted"] is False
    assert body["task_running"] is False
    assert body.get("runtime_error")
    assert body["runtime_applied"] is False


def test_legacy_restart_raises_or_reports_failure():
    sm = _LegacySM(running=True, registered=True)
    sm.fail_restart = True
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["restarted"] is False
    assert body.get("runtime_error")
    assert body["runtime_applied"] is False


def test_legacy_disabled_stop_succeeds_and_task_stopped():
    sm = _LegacySM(running=True, registered=True, stop_ok=True)
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert sm.stop_calls == 1
    assert body["task_running"] is False
    assert body.get("runtime_error") is None
    assert body["runtime_applied"] is False


def test_legacy_disabled_stop_fails_or_task_remains_running():
    sm = _LegacySM(running=True, registered=True, stop_ok=True)
    sm.keep_running_after_stop = True
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert body["task_running"] is True
    assert body.get("runtime_error")
    assert body["runtime_applied"] is False


def test_legacy_does_not_falsely_claim_runtime_config_applied():
    sm = _LegacySM(running=False, registered=True, start_result="started")
    app = _build_legacy_app(source_manager=sm, sources_cfg={}, start_source_fn=None)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:XYZ-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["task_running"] is True
    assert body["runtime_applied"] is False
    assert body["runtime_config_applied"] is False
    assert body.get("restart_required") is True


def test_existing_production_apply_source_fn_behavior_unchanged():
    # Production path with apply_source_fn must still report truthfully as before.
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        src = FakeSource("fyers", feed_cfg)
        if "fyers" in sm.enabled_sources:
            await sm.replace_source("fyers", src, feed_cfg)
        else:
            sm.register(src)
            sm._configs["fyers"] = dict(feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
            return {"runtime_applied": True, "registered": True, "task_running": True, "started": True, "start_result": "started"}
        return {"runtime_applied": True, "registered": True, "task_running": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn, persist_source_fn=lambda cfg: True)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:PROD-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["runtime_config_applied"] is True
    assert body["started"] is True
    assert body["task_running"] is True
    assert body.get("restart_required") is False
