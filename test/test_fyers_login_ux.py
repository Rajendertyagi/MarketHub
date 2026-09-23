#!/usr/bin/env python3
"""Fyers login/session UX tests (isolated temp stores only).

Covers the Fyers login-visibility + state-model fix:

  state model    valid -> authenticated; restart -> restored/authenticated;
                 known-expired -> expired/login_required; unknown expiry ->
                 unknown/login-visible; genuine 401/403 -> auth-invalid;
                 transients (timeout/DNS/reconnect) stay authenticated
  visibility     stale/expired/unknown sessions keep login_required=True;
                 Forget Session never gates Login
  security       GET /api/settings/fyers carries no raw token/PIN values
  navigation     callback success AND failure return to the initiating
                 Fyers settings/auth location (never /dashboard); open
                 redirects are refused
  re-login       works over a stale session without Forget Session first
  upstox         shared-model edits leave Upstox behavior unchanged

Synthetic tokens + temporary EventStore only. Never touches
data/events.db. NO LIVE BROKER.
"""

from __future__ import annotations

import json as _json
import os
import sys
import tempfile
import urllib.parse as _urlparse
from datetime import datetime, timedelta, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402


def _tmp_store(tmp: str):
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    return CredentialStore(EventStore(os.path.join(tmp, "e.db")),
                           data_dir=tmp)


def _future(hours: float = 6) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(hours=hours)).isoformat()


def _past(hours: float = 1) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(hours=hours)).isoformat()


def _fsvc(store, rt=None, **kw):
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.fyers import FyersAuthService
    from app.auth.storage import AuthStorage
    rt = rt if rt is not None else FyersRuntimeAuth()
    return FyersAuthService(AuthStorage(store), rt, **kw), rt


def _seed_login(store, access="ZZ-AT-1", refresh="ZZ-RT-1",
                expires=None, pin="ZZ-PIN-1"):
    store.save_fyers_credentials("ZZ-APP-1", "ZZ-SEC-1")
    store.save_fyers_refresh_token(refresh)
    store.save_fyers_pin(pin)
    store.save_fyers_access_token(access, expires)


# -- state model ---------------------------------------------------------------

async def test_valid_session_authenticated(runner: R) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        out = await svc.persist_login(
            {"access_token": "ZZ-AT-1", "refresh_token": "ZZ-RT-1",
             "expires_at": _future()})
        runner.assert_true("ux-valid-ok", out["ok"])
        snap = svc.status_snapshot({})
        runner.assert_eq("ux-valid-state", snap["auth_state"], "authenticated")
        runner.assert_true("ux-valid-authed", snap["authenticated"])
        runner.assert_false("ux-valid-login", snap["login_required"])
        runner.assert_true("ux-valid-active", snap["access_token_active"])
        runner.assert_true("ux-valid-persisted", snap["session_persisted"])
        runner.assert_eq("ux-valid-expires",
                         snap["access_token_expires_at"] is not None, True)


async def test_restart_restores_session(runner: R) -> None:
    """Case A: login -> persist -> 'restart' (fresh runtime+service) ->
    restore -> authenticated, no unnecessary login."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, _ = _fsvc(store)
        store.save_fyers_credentials("ZZ-APP-1", "ZZ-SEC-1")
        await svc.persist_login(
            {"access_token": "ZZ-AT-1", "refresh_token": "ZZ-RT-1",
             "expires_at": _future()},
            restart_fn=None)
        # Fresh process: empty runtime, new service over the same store.
        svc2, rt2 = _fsvc(store)
        out = await svc2.restore_session()
        runner.assert_true("ux-restart-restored", out.restored)
        runner.assert_eq("ux-restart-reason", out.reason,
                         "restored_access_token")
        runner.assert_eq("ux-restart-runtime", rt2.get_access_token(),
                         "ZZ-AT-1")
        snap = svc2.status_snapshot({"fyers_restored": True})
        runner.assert_eq("ux-restart-state", snap["auth_state"],
                         "authenticated")
        runner.assert_false("ux-restart-login", snap["login_required"])
        runner.assert_true("ux-restart-flag", snap["session_restored"])


async def test_refresh_path_restores(runner: R) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        store.save_fyers_credentials("ZZ-APP-1", "ZZ-SEC-1")
        store.save_fyers_refresh_token("ZZ-RT-1")
        store.save_fyers_pin("ZZ-PIN-1")

        async def _fake_refresh(**kw):
            return {"access_token": "ZZ-AT-2", "expires_at": _future()}

        svc, rt = _fsvc(store, refresh_fn=_fake_refresh)
        out = await svc.restore_session()
        runner.assert_true("ux-refresh-restored", out.restored)
        runner.assert_eq("ux-refresh-reason", out.reason, "refreshed")
        runner.assert_eq("ux-refresh-runtime", rt.get_access_token(),
                         "ZZ-AT-2")
        snap = svc.status_snapshot({"fyers_restored": True})
        runner.assert_false("ux-refresh-login", snap["login_required"])


async def test_expired_access_projects_expired(runner: R) -> None:
    """Case B: known-past expiry -> expired/login_required; nothing wiped."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        _seed_login(store, expires=_past())
        rt.set_access_token("ZZ-AT-1")
        snap = svc.status_snapshot({})
        runner.assert_eq("ux-exp-state", snap["auth_state"], "expired")
        runner.assert_false("ux-exp-authed", snap["authenticated"])
        runner.assert_true("ux-exp-login", snap["login_required"])
        runner.assert_eq("ux-exp-flag", snap["expired"], True)
        # Status is read-only: durable recovery material survives.
        runner.assert_true("ux-exp-kept-refresh",
                           store.load_fyers_refresh_token() == "ZZ-RT-1")
        runner.assert_true("ux-exp-kept-access",
                           store.load_fyers_access_token() is not None)
        runner.assert_eq("ux-exp-kept-runtime", rt.get_access_token(),
                         "ZZ-AT-1")


async def test_unknown_expiry_projects_unknown(runner: R) -> None:
    """No usable expiry -> unknown; NEVER authenticated; login stays."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        _seed_login(store, expires=None)
        rt.set_access_token("ZZ-AT-1")
        snap = svc.status_snapshot({})
        runner.assert_eq("ux-unk-state", snap["auth_state"], "unknown")
        runner.assert_false("ux-unk-authed", snap["authenticated"])
        runner.assert_true("ux-unk-login", snap["login_required"])
        runner.assert_eq("ux-unk-expired", snap["expired"], None)
        runner.assert_false("ux-unk-known", snap["expiry_known"])


async def test_genuine_401_is_auth_invalid(runner: R) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(
            store,
            feed_provider=lambda: {
                "state": "auth_required",
                "last_error": "token_unauthorized",
                "last_exit_reason": "token_expired_or_unauthorized",
            })
        _seed_login(store, expires=_future())
        rt.set_access_token("ZZ-AT-1")
        snap = svc.status_snapshot({})
        runner.assert_eq("ux-401-state", snap["auth_state"], "expired")
        runner.assert_true("ux-401-login", snap["login_required"])
        runner.assert_false("ux-401-authed", snap["authenticated"])
        # Genuine rejection still never wipes durable recovery material.
        runner.assert_true("ux-401-kept-refresh",
                           store.load_fyers_refresh_token() == "ZZ-RT-1")
        runner.assert_true("ux-401-kept-access",
                           store.load_fyers_access_token() is not None)


async def test_transients_not_auth_failure(runner: R) -> None:
    cases = [
        ("reconnect-loop", {"state": "reconnecting",
                            "last_error": "connect_failed"}),
        ("dns-timeout", {"state": "reconnecting",
                         "last_error": "dns timeout"}),
        ("connecting", {"state": "connecting", "last_error": None}),
        ("stopped-feed", {"state": "stopped",
                          "last_exit_reason": "stop_requested"}),
        ("generic-error", {"state": "failed",
                           "last_error": "websocket reset"}),
    ]
    for name, fstate in cases:
        with tempfile.TemporaryDirectory() as tmp:
            store = _tmp_store(tmp)
            svc, rt = _fsvc(store, feed_provider=lambda s=fstate: s)
            _seed_login(store, expires=_future())
            rt.set_access_token("ZZ-AT-1")
            snap = svc.status_snapshot({})
            runner.assert_eq(f"ux-tr-{name}", snap["auth_state"],
                             "authenticated")
            runner.assert_false(f"ux-tr-login-{name}",
                                snap["login_required"])


async def test_stale_token_never_hides_login(runner: R) -> None:
    """Expired/unknown sessions keep login_required=True even though a
    persisted session exists (the data contract the UI gates on)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        _seed_login(store, expires=_past())
        rt.set_access_token("ZZ-AT-STALE")
        snap = svc.status_snapshot({})
        runner.assert_true("ux-vis-persisted", snap["session_persisted"])
        runner.assert_true("ux-vis-active", snap["access_token_active"])
        runner.assert_true("ux-vis-login", snap["login_required"])
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        _seed_login(store, expires=None)
        rt.set_access_token("ZZ-AT-STALE")
        snap = svc.status_snapshot({})
        runner.assert_true("ux-vis-login-unk", snap["login_required"])


async def test_forget_session_semantics(runner: R) -> None:
    """Forget = session gone, credentials stay, Login visible — and it is
    never required before a login (see re-login test)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        _seed_login(store, expires=_future())
        rt.set_access_token("ZZ-AT-1")
        out = svc.logout({})
        runner.assert_true("ux-forget-ok", out["ok"])
        runner.assert_true("ux-forget-creds",
                           store.load_fyers_credentials() is not None)
        runner.assert_true("ux-forget-access",
                           store.load_fyers_access_token() is None)
        runner.assert_true("ux-forget-refresh",
                           store.load_fyers_refresh_token() is None)
        runner.assert_true("ux-forget-pin",
                           store.load_fyers_pin() is None)
        runner.assert_eq("ux-forget-runtime", rt.get_access_token(), "")
        snap = svc.status_snapshot({})
        runner.assert_eq("ux-forget-state", snap["auth_state"], "missing")
        runner.assert_true("ux-forget-login", snap["login_required"])
        runner.assert_true("ux-forget-avail", snap["login_available"])


async def test_relogin_without_forget(runner: R) -> None:
    """A fresh login bundle installs over a stale session directly."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc, rt = _fsvc(store)
        _seed_login(store, access="ZZ-AT-OLD", expires=_past())
        rt.set_access_token("ZZ-AT-OLD")
        before = svc.status_snapshot({})
        runner.assert_true("ux-relogin-needed", before["login_required"])
        out = await svc.persist_login(
            {"access_token": "ZZ-AT-NEW", "refresh_token": "ZZ-RT-NEW",
             "expires_at": _future()})
        runner.assert_true("ux-relogin-ok", out["ok"])
        runner.assert_eq("ux-relogin-runtime", rt.get_access_token(),
                         "ZZ-AT-NEW")
        after = svc.status_snapshot({})
        runner.assert_eq("ux-relogin-state", after["auth_state"],
                         "authenticated")
        runner.assert_false("ux-relogin-login", after["login_required"])


# -- security: settings endpoint ------------------------------------------------

def _settings_app():
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from api.product_routes import build_fyers_auth_routes
    tmp = tempfile.mkdtemp()
    store = _tmp_store(tmp)
    store.save_fyers_credentials("ZZ-APP-1", "ZZ-SEC-1")
    store.save_fyers_refresh_token("ZZ-RT-SECRET-1")
    store.save_fyers_pin("ZZ-PIN-SECRET-1")
    store.save_fyers_access_token("ZZ-AT-SECRET-1", _future())
    app = Starlette(routes=build_fyers_auth_routes(store))
    return TestClient(app)


async def test_settings_endpoint_has_no_secrets(runner: R) -> None:
    client = _settings_app()
    resp = client.get("/api/settings/fyers")
    runner.assert_eq("ux-sec-status", resp.status_code, 200)
    body = resp.json()
    blob = _json.dumps(body)
    for secret in ("ZZ-AT-SECRET-1", "ZZ-RT-SECRET-1", "ZZ-PIN-SECRET-1",
                   "ZZ-APP-1", "ZZ-SEC-1"):
        runner.assert_not_in(f"ux-sec-val-{secret[:8]}", secret, blob)
    for key in ("access_token", "refresh_token", "pin", "stored_access",
                "app_secret", "secret_id", "auth_code"):
        runner.assert_true(f"ux-sec-key-{key}", key not in body)
    # Safe metadata still present for the UI contract.
    for key in ("authenticated", "auth_state", "login_required",
                "session_persisted", "session_restored",
                "access_token_expires_at", "login_available"):
        runner.assert_true(f"ux-sec-kept-{key}", key in body)


# -- navigation: login/callback round-trip --------------------------------------

class _FakeFyersAuth:
    """Route-level double: no network, deterministic exchange outcome."""

    def __init__(self, fail_exchange=False):
        self.fail_exchange = fail_exchange
        self.persisted = []

    def status_snapshot(self, restore_state=None):
        return {"authenticated": False, "auth_state": "missing",
                "login_required": True, "access_token_active": False,
                "session_persisted": False, "session_restored": False,
                "login_available": True}

    def build_login_url(self, state):
        return f"https://login.fyers.example/auth?state={state}"

    async def exchange_auth_code(self, code):
        if self.fail_exchange:
            raise ValueError("rejected")
        return {"access_token": "ZZ-AT-NEW", "refresh_token": "ZZ-RT-NEW",
                "expires_at": _future()}

    async def persist_login(self, bundle, restart_fn=None):
        self.persisted.append(bundle)
        return {"ok": True}

    def logout(self, restore_state=None):
        return {"ok": True}


def _login_app(fake):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from api.product_routes import build_fyers_auth_routes
    tmp = tempfile.mkdtemp()
    store = _tmp_store(tmp)
    store.save_fyers_credentials("ZZ-APP-1", "ZZ-SEC-1")
    app = Starlette(routes=build_fyers_auth_routes(
        store, auth_service=fake))
    return TestClient(app, follow_redirects=False)


def _state_from_login_location(location: str) -> str:
    qs = _urlparse.parse_qs(_urlparse.urlparse(location).query)
    return qs["state"][0]


async def test_callback_success_returns_to_initiator(runner: R) -> None:
    fake = _FakeFyersAuth()
    client = _login_app(fake)
    nxt = "/ui/%23/settings/brokers"
    login = client.get(f"/api/auth/fyers/login?next={nxt}")
    runner.assert_eq("ux-nav-login-302", login.status_code, 302)
    state = _state_from_login_location(login.headers["location"])
    cb = client.get(f"/auth/fyers/callback?auth_code=ZZ-CODE&state={state}")
    runner.assert_eq("ux-nav-cb-302", cb.status_code, 302)
    target = cb.headers["location"]
    runner.assert_true("ux-nav-ok-flag", "fyers_auth=ok" in target)
    runner.assert_true("ux-nav-ok-dest",
                       target.startswith("/ui/") and "#/settings/brokers"
                       in target)
    runner.assert_not_in("ux-nav-no-dash", "/dashboard", target)
    runner.assert_eq("ux-nav-persisted", len(fake.persisted), 1)


async def test_callback_failure_returns_to_initiator(runner: R) -> None:
    fake = _FakeFyersAuth(fail_exchange=True)
    client = _login_app(fake)
    nxt = "/ui/%23/settings/brokers"
    login = client.get(f"/api/auth/fyers/login?next={nxt}")
    state = _state_from_login_location(login.headers["location"])
    cb = client.get(f"/auth/fyers/callback?auth_code=ZZ-CODE&state={state}")
    runner.assert_eq("ux-nav-fail-302", cb.status_code, 302)
    target = cb.headers["location"]
    runner.assert_true("ux-nav-fail-flag", "fyers_auth=rejected" in target)
    runner.assert_true("ux-nav-fail-dest",
                       target.startswith("/ui/") and "#/settings/brokers"
                       in target)
    runner.assert_not_in("ux-nav-fail-dash", "/dashboard", target)


async def test_login_rejects_open_redirect(runner: R) -> None:
    fake = _FakeFyersAuth(fail_exchange=True)
    client = _login_app(fake)
    login = client.get(
        "/api/auth/fyers/login?next=https%3A%2F%2Fevil.example%2Fx")
    runner.assert_eq("ux-nav-evil-302", login.status_code, 302)
    state = _state_from_login_location(login.headers["location"])
    cb = client.get(f"/auth/fyers/callback?auth_code=ZZ-CODE&state={state}")
    target = cb.headers["location"]
    runner.assert_not_in("ux-nav-evil-host", "evil.example", target)
    runner.assert_true("ux-nav-evil-default", target.startswith("/ui/"))


# -- UI contract guard + upstox unchanged ----------------------------------------

async def test_ui_gates_on_auth_state(runner: R) -> None:
    """Regression guard: the Fyers panel must gate Login on the canonical
    backend state, never on raw token presence."""
    path = os.path.join(_PROJECT_DIR, "frontend", "src", "features",
                        "settings", "components", "BrokersPanel.tsx")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    runner.assert_true("ux-ui-authed-gate", "s.authenticated === true" in src)
    runner.assert_true("ux-ui-no-presence-gate",
                       "{!loggedIn" not in src
                       and "const loggedIn" not in src)
    runner.assert_true("ux-ui-relogin", "Re-login with Fyers" in src)
    runner.assert_true("ux-ui-result", "fyers_auth" in src)


async def test_upstox_behavior_unchanged(runner: R) -> None:
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    from brokers.upstox.auth import UpstoxCredentials, upstox_token_expiry
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.rest import UpstoxRest
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        creds = UpstoxCredentials(
            access_token="REAL-UX-1",
            expires_at=upstox_token_expiry(datetime.now(timezone.utc)))
        feed = UpstoxFeed(
            config={"source_name": "upstox",
                    "instrument_keys": ["NSE:NIFTY50-INDEX"]},
            credentials=None, rest=UpstoxRest(),
            instrument_metadata={"NSE:NIFTY50-INDEX": ("NSE", "NIFTY 50")})
        svc = UpstoxAuthService(AuthStorage(store),
                                feed_provider=lambda: feed)
        await svc.apply_session(creds)
        st = svc.status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("ux-up-state", st["auth_state"], "authenticated")
        runner.assert_true("ux-up-authed", st["authenticated"])
        runner.assert_false("ux-up-login", st["login_required"])


async def main() -> bool:
    runner = R()
    await test_valid_session_authenticated(runner)
    await test_restart_restores_session(runner)
    await test_refresh_path_restores(runner)
    await test_expired_access_projects_expired(runner)
    await test_unknown_expiry_projects_unknown(runner)
    await test_genuine_401_is_auth_invalid(runner)
    await test_transients_not_auth_failure(runner)
    await test_stale_token_never_hides_login(runner)
    await test_forget_session_semantics(runner)
    await test_relogin_without_forget(runner)
    await test_settings_endpoint_has_no_secrets(runner)
    await test_callback_success_returns_to_initiator(runner)
    await test_callback_failure_returns_to_initiator(runner)
    await test_login_rejects_open_redirect(runner)
    await test_ui_gates_on_auth_state(runner)
    await test_upstox_behavior_unchanged(runner)
    return runner.summary()


if __name__ == "__main__":
    import asyncio as _asyncio
    _ok = _asyncio.run(main())
    sys.exit(0 if _ok else 1)
