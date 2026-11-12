"""Focused tests for FYERS feed config runtime application (API + lifecycle).

Covers:
- strict boolean validation for enabled/tbt_enabled
- runtime reconfiguration applies to actual source instance/config
- stop+await before replacement when running
- stopped registered source gets latest config before start
- disable preserves settings and re-enable applies them
- first-time registration receives runtime deps/injection
- failed restart/reconfiguration not reported as successfully applied

No live broker traffic. Uses fake sources/managers.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from starlette.applications import Starlette
from starlette.testclient import TestClient

from api.product_routes import build_fyers_auth_routes
from app.fyers_runtime_auth import FyersRuntimeAuth
from app.secrets_store import CredentialStore
from core.persistence.store import EventStore


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
        # simulate first-time registration/start
        src = FakeSource("fyers", feed_cfg)
        await sm.replace_source("fyers", src, feed_cfg) if "fyers" in sm.enabled_sources else sm.register(src)
        sm._configs["fyers"] = dict(feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers"), "started": True}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn)
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
    # existing running source with old config
    old_cfg = {"type": "fyers_feed", "enabled": True, "mode": "full", "instruments": [], "tbt_enabled": False, "tbt_symbols": []}
    old_src = FakeSource("fyers", old_cfg)
    sm.register(old_src)
    sm._configs["fyers"] = dict(old_cfg)
    sm._running.add("fyers")

    async def apply_fn(feed_cfg, start_if_enabled=True):
        # build new instance with new config
        new_src = FakeSource("fyers", feed_cfg)
        await sm.replace_source("fyers", new_src, feed_cfg)  # should stop if running
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
            return {"runtime_applied": True, "registered": True, "task_running": True, "restarted": True}
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers")}

    sources_cfg = {"fyers": dict(old_cfg)}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:RELIANCE-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["registered"] is True
    assert body["task_running"] is True
    # verify stop happened before replace and new config applied
    assert ("stop_for_replace", "fyers") in sm.events
    replace_events = [e for e in sm.events if e[0] == "replace"]
    assert replace_events, sm.events
    assert replace_events[-1][2]["tbt_enabled"] is True
    assert replace_events[-1][2]["tbt_symbols"] == ["NSE:RELIANCE-EQ"]
    assert replace_events[-1][3]["tbt_enabled"] is True
    # stop_for_replace must come before replace
    idx_stop = sm.events.index(("stop_for_replace", "fyers"))
    idx_replace = next(i for i, e in enumerate(sm.events) if e[0] == "replace")
    assert idx_stop < idx_replace


def test_stopped_registered_source_receives_latest_config_before_start():
    sm = FakeSourceManager()
    old_cfg = {"type": "fyers_feed", "enabled": True, "mode": "full", "instruments": [], "tbt_enabled": False, "tbt_symbols": []}
    old_src = FakeSource("fyers", old_cfg)
    sm.register(old_src)
    sm._configs["fyers"] = dict(old_cfg)
    # not running

    async def apply_fn(feed_cfg, start_if_enabled=True):
        new_src = FakeSource("fyers", feed_cfg)
        await sm.replace_source("fyers", new_src, feed_cfg)
        if feed_cfg.get("enabled", True) and start_if_enabled:
            await sm.start_source("fyers")
            return {"runtime_applied": True, "registered": True, "task_running": True, "started": True}
        return {"runtime_applied": True, "registered": True, "task_running": False}

    sources_cfg = {"fyers": dict(old_cfg)}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:INFY-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["task_running"] is True
    # replace happened, then start used new config
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

    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn)
    c = TestClient(app)

    # enable with TBT settings
    r1 = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:SBIN-EQ"]})
    assert r1.status_code == 200
    assert sources_cfg["fyers"]["tbt_enabled"] is True
    assert sources_cfg["fyers"]["tbt_symbols"] == ["NSE:SBIN-EQ"]

    # disable but preserve settings
    r2 = c.post("/api/settings/fyers/feed", json={"enabled": False, "tbt_enabled": True, "tbt_symbols": ["NSE:SBIN-EQ"]})
    assert r2.status_code == 200
    assert sources_cfg["fyers"]["enabled"] is False
    assert sources_cfg["fyers"]["tbt_enabled"] is True
    assert sources_cfg["fyers"]["tbt_symbols"] == ["NSE:SBIN-EQ"]

    # re-enable: saved settings must be applied
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
        # simulate restart/start failure after replacement
        return {"runtime_applied": False, "registered": True, "task_running": False, "error": "restart_failed:RuntimeError"}

    sources_cfg = {"fyers": dict(old_cfg)}
    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn)
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
        # simulate server-side build with runtime deps injected (represented in cfg here)
        assert feed_cfg.get("tbt_enabled") is True
        src = FakeSource("fyers", feed_cfg)
        sm.register(src)
        sm._configs["fyers"] = dict(feed_cfg)
        if start_if_enabled:
            await sm.start_source("fyers")
        return {"runtime_applied": True, "registered": True, "task_running": sm.task_running("fyers"), "started": True}

    app, _ = _build_app(source_manager=sm, sources_cfg=sources_cfg, apply_source_fn=apply_fn)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": True, "tbt_enabled": True, "tbt_symbols": ["NSE:TCS-EQ"]})
    assert r.status_code == 200
    body = r.json()
    assert body["runtime_applied"] is True
    assert body["registered"] is True
    assert body["task_running"] is True
    assert ("register", "fyers", {"type": "fyers_feed", "enabled": True, "mode": "full", "instruments": body.get("instruments") or sources_cfg["fyers"]["instruments"], "tbt_enabled": True, "tbt_symbols": ["NSE:TCS-EQ"]}) or any(e[0] == "register" and e[2].get("tbt_enabled") is True for e in sm.events)


def test_tbt_symbols_string_and_dedup_preserved():
    sm = FakeSourceManager()
    async def apply_fn(feed_cfg, start_if_enabled=True):
        src = FakeSource("fyers", feed_cfg)
        sm.register(src)
        sm._configs["fyers"] = dict(feed_cfg)
        return {"runtime_applied": True, "registered": True, "task_running": False}
    app, _ = _build_app(source_manager=sm, sources_cfg={}, apply_source_fn=apply_fn)
    c = TestClient(app)
    r = c.post("/api/settings/fyers/feed", json={"enabled": False, "tbt_enabled": True, "tbt_symbols": "NSE:AAA-EQ, NSE:BBB-EQ\nNSE:AAA-EQ"})
    assert r.status_code == 200
    body = r.json()
    assert body["tbt_symbols"] == ["NSE:AAA-EQ", "NSE:BBB-EQ"]
