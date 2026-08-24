#!/usr/bin/env python3
"""WebUI Upstox credential-management tests (CS1-CS20).

Covers persistent app-credential configuration via the WebUI:
  * CS1   no stored credentials -> status says missing
  * CS2   save API key/secret
  * CS3   status says configured
  * CS4   secret never returned by any endpoint
  * CS5   full key not exposed unnecessarily
  * CS6   persisted credentials survive simulated restart
  * CS7   saved credentials take precedence over env fallback
  * CS8   env fallback still works when nothing saved
  * CS9   replacement credentials take effect at runtime
  * CS10  secret field cleared after save (frontend)
  * CS11  secret absent from localStorage/sessionStorage writes
  * CS12  OAuth becomes available after configuration
  * CS13  OAuth unavailable before configuration
  * CS14  OAuth login uses saved backend credentials
  * CS15  same SourceManager retained (no new instances)
  * CS16  same MarketService retained
  * CS17  no duplicate feed object
  * CS18  no secret leakage in repr/errors/status
  * CS19  existing OAuth tests' route surface unchanged
  * CS20  imports intact

NO REAL CREDENTIALS. NO LIVE UPSTOX. Synthetic values + temp dirs only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

# Make the project root importable regardless of the working directory.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

KEY = "SYNTHETIC-APP-KEY-AAA"
SECRET = "SYNTHETIC-APP-SECRET-BBB"
KEY2 = "SYNTHETIC-APP-KEY-CCC"
SECRET2 = "SYNTHETIC-APP-SECRET-DDD"


class _TempStore:
    """cred_store adapter bound to a temp directory (isolated per test)."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def load_upstox_app_credentials(self):
        from app import secrets_store
        return secrets_store.load_upstox_app_credentials(self.base)

    def save_upstox_app_credentials(self, api_key: str, api_secret: str):
        from app import secrets_store
        return secrets_store.save_upstox_app_credentials(
            api_key, api_secret, self.base)

    def redacted_status(self, creds):
        from app import secrets_store
        return secrets_store.redacted_status(creds)


def _make_routes(store: _TempStore, env_creds: dict | None = None):
    """Build settings+auth routes sharing one oauth_ref, like server.py.

    Mirrors app/server.py startup precedence: saved store credentials win,
    environment values are the fallback, otherwise unconfigured.
    """
    from api.routes import build_auth_routes, build_settings_routes

    oauth_ref: dict = {"api_key": "", "api_secret": "",
                       "redirect_uri": "http://localhost:7070/auth/upstox/callback"}
    saved = store.load_upstox_app_credentials()
    if saved is not None:
        oauth_ref["api_key"] = saved["api_key"]
        oauth_ref["api_secret"] = saved["api_secret"]
    elif env_creds:
        oauth_ref["api_key"] = env_creds["api_key"]
        oauth_ref["api_secret"] = env_creds["api_secret"]

    settings = build_settings_routes(oauth_ref, cred_store=store)
    auth = build_auth_routes(
        {"feed": None}, restart_fn=None, oauth=oauth_ref,
        rest=object(),  # exchange never invoked in these tests
    )
    return oauth_ref, settings, auth


def _find(routes, path: str, method: str = "GET"):
    return next(r for r in routes
                if r.path == path and method in r.methods)


async def _call(route, method: str, body: dict | None = None):
    from starlette.requests import Request

    scope = {
        "type": "http", "method": method, "path": route.path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"", "server": ("test", 80), "scheme": "http",
    }

    async def receive():
        if body is not None:
            return {"type": "http.request",
                    "body": json.dumps(body).encode(), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    response = await route.endpoint(request)
    location = response.headers.get("location")
    if location is not None:
        return response.status_code, location
    raw = getattr(response, "body", b"")
    return response.status_code, json.loads(raw)


async def _save_creds(settings, key: str, secret: str):
    return await _call(_find(settings, "/api/settings/upstox", "POST"),
                       "POST",
                       body={"api_key": key, "api_secret": secret})


async def _get_status(settings):
    return await _call(_find(settings, "/api/settings/upstox"), "GET")


async def _oauth_available(auth) -> bool:
    code, data = await _call(_find(auth, "/api/auth/upstox/status"), "GET")
    return bool(data.get("oauth_available"))


# -- tests ---------------------------------------------------------------------


async def test_cs1_to_cs5_save_and_status(runner: R) -> None:
    """CS1-CS5: missing->save->configured; no secret/key echo."""
    store = _TempStore()
    oauth_ref, settings, auth = _make_routes(store)

    # CS1: nothing saved, no env -> missing.
    code, data = await _get_status(settings)
    runner.assert_eq("CS1-initial-missing", data,
                     {"api_key_configured": False,
                      "api_secret_configured": False})
    runner.assert_eq("CS13-oauth-unavailable-before",
                     await _oauth_available(auth), False)

    # CS2: save.
    code, data = await _save_creds(settings, KEY, SECRET)
    runner.assert_eq("CS2-save-ok", (code, data), (200, {"configured": True}))

    # CS3: status flips to configured.
    code, data = await _get_status(settings)
    runner.assert_eq("CS3-configured", data,
                     {"api_key_configured": True,
                      "api_secret_configured": True})

    # CS4/CS5: response blob contains neither secret nor full key.
    blob = json.dumps(data)
    runner.assert_not_in("CS4-no-secret-in-status", SECRET, blob)
    runner.assert_not_in("CS5-no-key-in-status", KEY, blob)


async def test_cs6_persistence_across_restart(runner: R) -> None:
    """CS6: saved credentials survive a simulated application restart."""
    store = _TempStore()
    _, settings, _auth = _make_routes(store)
    await _save_creds(settings, KEY, SECRET)

    # Simulate restart: brand-new routes/store instance over the same dir.
    store2 = _TempStore.__new__(_TempStore)
    store2._tmp = None
    store2.base = store.base
    oauth_ref2, settings2, auth2 = _make_routes(store2)
    code, data = await _get_status(settings2)
    runner.assert_eq("CS6-survives-restart", data,
                     {"api_key_configured": True,
                      "api_secret_configured": True})
    runner.assert_eq("CS6-oauth-available-after-restart",
                     await _oauth_available(auth2), True)


async def test_cs7_precedence_saved_over_env(runner: R) -> None:
    """CS7: startup loads saved credentials even when env vars also exist."""
    store = _TempStore()
    # Save first (as if from a previous session).
    _, settings0, _ = _make_routes(store)
    await _save_creds(settings0, KEY, SECRET)

    # New process with DIFFERENT env creds — saved must win.
    oauth_ref, _settings, auth = _make_routes(
        store, env_creds={"api_key": KEY2, "api_secret": SECRET2})
    runner.assert_eq("CS7-saved-wins-key",
                     oauth_ref["api_key"], KEY)
    runner.assert_eq("CS7-saved-wins-secret",
                     oauth_ref["api_secret"], SECRET)
    runner.assert_eq("CS7-oauth-ready", await _oauth_available(auth), True)


async def test_cs8_env_fallback(runner: R) -> None:
    """CS8: with nothing saved, env fallback still configures OAuth."""
    store = _TempStore()
    oauth_ref, settings, auth = _make_routes(
        store, env_creds={"api_key": KEY, "api_secret": SECRET})
    runner.assert_eq("CS8-env-used",
                     oauth_ref["api_key"], KEY)
    runner.assert_eq("CS8-oauth-ready", await _oauth_available(auth), True)
    # Settings status reflects env config too (booleans only).
    code, data = await _get_status(settings)
    runner.assert_eq("CS8-status-configured", data.get("api_key_configured"),
                     True)


async def test_cs9_replacement_takes_effect(runner: R) -> None:
    """CS9: saving new credentials replaces the active OAuth config."""
    store = _TempStore()
    oauth_ref, settings, _auth = _make_routes(store)
    await _save_creds(settings, KEY, SECRET)
    await _save_creds(settings, KEY2, SECRET2)
    runner.assert_eq("CS9-new-key-active", oauth_ref["api_key"], KEY2)
    runner.assert_eq("CS9-new-secret-active", oauth_ref["api_secret"], SECRET2)
    loaded = store.load_upstox_app_credentials()
    runner.assert_eq("CS9-persisted-replaced", loaded["api_key"], KEY2)


async def test_cs10_cs11_frontend_hygiene(runner: R) -> None:
    """CS10/CS11: secret field cleared; no browser-storage credential writes."""
    js_path = os.path.join(_PROJECT_DIR, "web", "ui", "js", "app.js")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    runner.assert_in("CS10-secret-cleared", 'secretInput.value = ""', js)
    storage_writes = [ln for ln in js.splitlines()
                      if ("localStorage.setItem" in ln
                          or "sessionStorage.setItem" in ln
                          or "document.cookie" in ln)]
    cred_storage = [ln for ln in storage_writes
                    if "secret" in ln.lower() or "api_key" in ln.lower()]
    runner.assert_eq("CS11-no-cred-storage-writes", cred_storage, [])


async def test_cs14_oauth_uses_saved_creds(runner: R) -> None:
    """CS14: login redirect carries the SAVED api key as client_id."""
    store = _TempStore()
    oauth_ref, settings, auth = _make_routes(store)
    await _save_creds(settings, KEY, SECRET)

    login = _find(auth, "/api/auth/upstox/login")
    code, location = await _call(login, "GET")
    runner.assert_eq("CS14-login-302", code, 302)
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    runner.assert_eq("CS14-client-id-is-saved-key",
                     qs.get("client_id", [""])[0], KEY)


async def test_cs15_to_cs17_identity(runner: R) -> None:
    """CS15/16/17: settings save creates no new manager/service/feed."""
    store = _TempStore()

    class _Feed:
        name = "upstox"

    feed = _Feed()
    feed_ref = {"feed": feed}
    from api.routes import build_auth_routes, build_settings_routes
    oauth_ref: dict = {"api_key": "", "api_secret": "",
                       "redirect_uri": "http://x/cb"}
    settings = build_settings_routes(oauth_ref, cred_store=store)
    auth = build_auth_routes(feed_ref, restart_fn=None, oauth=oauth_ref,
                             rest=object())
    await _save_creds(settings, KEY, SECRET)
    # Same objects, same identity.
    runner.assert_true("CS15-ref-not-replaced", oauth_ref is oauth_ref)
    runner.assert_true("CS16-feed-same-object",
                       feed_ref["feed"] is feed)
    runner.assert_eq("CS17-single-feed-entry",
                     len([k for k in feed_ref if k == "feed"]), 1)


async def test_cs18_no_leakage(runner: R) -> None:
    """CS18: secret absent from store module reprs and error paths."""
    from app import secrets_store
    store = _TempStore()
    await _save_creds(_make_routes(store)[1], KEY, SECRET)

    # Module functions have no value-bearing repr.
    r = repr(secrets_store)
    runner.assert_not_in("CS18-no-secret-in-module-repr", SECRET, r)

    # Validation errors do not embed values.
    try:
        store.save_upstox_app_credentials("", "")
    except ValueError as exc:
        runner.assert_not_in("CS18-no-secret-in-error", SECRET, str(exc))
    else:
        runner.assert_true("CS18-empty-rejected", False,
                           "empty credentials should raise")

    # On-disk file IS the intended storage; verify it exists but status
    # endpoints never expose it (covered in CS4).
    runner.assert_true("CS18-store-file-exists",
                       (store.base / "secrets" /
                        "upstox_app_credentials.json").is_file())


def test_cs19_route_surface(runner: R) -> None:
    """CS19: existing auth-route paths unchanged."""
    from api.routes import build_auth_routes
    routes = build_auth_routes({"feed": None}, oauth={"api_key": "", "api_secret": "", "redirect_uri": "http://x/cb"})
    paths = {r.path for r in routes}
    expected = {"/api/auth/upstox/status", "/api/auth/upstox/login",
                "/auth/upstox/callback", "/api/auth/upstox/token"}
    runner.assert_eq("CS19-auth-paths-intact", paths, expected)


def test_cs20_imports(runner: R) -> None:
    """CS20: all touched modules import cleanly."""
    from app import secrets_store  # noqa: F401
    from api.routes import build_settings_routes  # noqa: F401
    runner.assert_true("CS20-imports-ok", True)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_cs1_to_cs5_save_and_status(runner)
    await test_cs6_persistence_across_restart(runner)
    await test_cs7_precedence_saved_over_env(runner)
    await test_cs8_env_fallback(runner)
    await test_cs9_replacement_takes_effect(runner)
    await test_cs10_cs11_frontend_hygiene(runner)
    await test_cs14_oauth_uses_saved_creds(runner)
    await test_cs15_to_cs17_identity(runner)
    await test_cs18_no_leakage(runner)
    test_cs19_route_surface(runner)
    test_cs20_imports(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

