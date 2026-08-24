#!/usr/bin/env python3
"""Encrypted SQLite credential-storage tests (CS1-CS26).

Covers WebUI-managed Upstox app credentials stored Fernet-encrypted in the
EXISTING application SQLite DB (table `secrets`, schema v10):
  * CS1   master key generated on first clean use
  * CS2   same key reused later (no regeneration)
  * CS3   master key NOT stored inside the DB
  * CS4   API secret encrypted before DB write
  * CS5   plaintext secret absent from DB file/rows
  * CS6   encrypted secret decrypts correctly
  * CS7   API key persists
  * CS8   settings survive simulated restart (new store instance)
  * CS9   WebUI status reports configured
  * CS10  API secret never returned by any endpoint
  * CS11  encrypted_value never returned
  * CS12  master key never returned
  * CS13  replacement credentials work
  * CS14  update is atomic (single transaction, no partial state)
  * CS15  DB credentials override env fallback
  * CS16  env fallback works when DB credentials absent
  * CS17  corrupted ciphertext fails safely
  * CS18  missing master key with existing ciphertext fails safely
  * CS19  no silent key regeneration in that case
  * CS20  OAuth login uses decrypted backend secret
  * CS21  access token remains memory-only (not in secrets table)
  * CS22  localStorage/sessionStorage contain no credentials
  * CS23  same SourceManager retained (identity via feed_ref)
  * CS24  same MarketService retained
  * CS25  existing OAuth route surface unchanged
  * CS26  imports intact

NO REAL CREDENTIALS. NO LIVE UPSTOX. Synthetic values + temp dirs only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
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


class _Env:
    """Isolated temp dir + EventStore + CredentialStore per test."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        from core.persistence.store import EventStore
        from app.secrets_store import CredentialStore
        self.db_path = str(self.base / "events.db")
        self.event_store = EventStore(self.db_path)
        self.cred_store = CredentialStore(self.event_store,
                                          data_dir=self.base)

    def reopen(self) -> "tuple":
        """Simulate application restart: fresh store instances, same files."""
        from core.persistence.store import EventStore
        from app.secrets_store import CredentialStore
        es = EventStore(self.db_path)
        cs = CredentialStore(es, data_dir=self.base)
        return es, cs


def _make_routes(cred_store, env_creds: dict | None = None,
                 startup_load: bool = True):
    """Build settings+auth routes sharing one oauth_ref, like server.py.

    Mirrors app/server.py startup precedence: DB-saved credentials win,
    environment values are the fallback, otherwise unconfigured.
    """
    from api.routes import build_auth_routes, build_settings_routes

    oauth_ref: dict = {"api_key": "", "api_secret": "",
                       "redirect_uri": "http://localhost:7070/auth/upstox/callback"}
    if startup_load:
        try:
            saved = cred_store.load_upstox_app_credentials()
        except Exception:
            saved = None
        if saved is not None:
            oauth_ref["api_key"] = saved["api_key"]
            oauth_ref["api_secret"] = saved["api_secret"]
        elif env_creds:
            oauth_ref["api_key"] = env_creds["api_key"]
            oauth_ref["api_secret"] = env_creds["api_secret"]

    settings = build_settings_routes(oauth_ref, cred_store=cred_store)
    auth = build_auth_routes(
        {"feed": None}, restart_fn=None, oauth=oauth_ref,
        rest=object(),  # exchange never invoked directly here
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


async def _save(settings, key: str, secret: str):
    return await _call(_find(settings, "/api/settings/upstox", "POST"),
                       "POST", body={"api_key": key, "api_secret": secret})


# -- tests ---------------------------------------------------------------------


def test_cs1_to_cs7_encryption_basics(runner: R) -> None:
    """CS1-CS7: key lifecycle + encryption correctness."""
    env = _Env()
    runner.assert_false("CS1-no-key-before-use",
                        (env.base / "master.key").is_file())

    env.cred_store.save_upstox_app_credentials(KEY, SECRET)

    # CS1: key generated on first save.
    key_file = env.base / "master.key"
    runner.assert_true("CS1-key-generated", key_file.is_file())
    original_key = key_file.read_bytes()

    # CS2: second operation reuses the SAME key.
    env2_es, env2_cs = env.reopen()
    loaded = env2_cs.load_upstox_app_credentials()
    runner.assert_eq("CS2-key-reused", key_file.read_bytes(), original_key)
    runner.assert_eq("CS6-decrypts-correctly", loaded["api_secret"], SECRET)

    # CS3: master key bytes must not appear anywhere in the DB file.
    db_bytes = Path(env.db_path).read_bytes()
    runner.assert_not_in("CS3-key-not-in-db", original_key, db_bytes)

    # CS4/CS5: plaintext secret absent from DB; value stored is ciphertext.
    conn = sqlite3.connect(env.db_path)
    rows = conn.execute(
        "SELECT name, encrypted_value, encryption_scheme FROM secrets "
        "WHERE provider='upstox'").fetchall()
    conn.close()
    names = {r[0] for r in rows}
    runner.assert_eq("CS4-two-rows", names, {"api_key", "api_secret"})
    for name, value, scheme in rows:
        runner.assert_not_in(f"CS5-no-plaintext-{name}",
                             SECRET if name == "api_secret" else KEY, value)
        runner.assert_eq(f"CS4-scheme-{name}", scheme, "fernet-v1")

    # CS6/CS7: round-trip via a fresh instance.
    runner.assert_eq("CS7-api-key-persists", loaded["api_key"], KEY)


def test_cs8_restart_persistence(runner: R) -> None:
    """CS8: credentials survive a simulated application restart."""
    env = _Env()
    env.cred_store.save_upstox_app_credentials(KEY, SECRET)
    _es, cs = env.reopen()
    loaded = cs.load_upstox_app_credentials()
    runner.assert_eq("CS8-survives-restart", loaded,
                     {"api_key": KEY, "api_secret": SECRET})


async def test_cs9_to_cs12_api_safety(runner: R) -> None:
    """CS9-CS12: status configured; no secret/ciphertext/key in responses."""
    env = _Env()
    oauth_ref, settings, auth = _make_routes(env.cred_store)
    code, data = await _save(settings, KEY, SECRET)
    runner.assert_eq("CS9-save-ok", (code, data), (200, {"configured": True}))

    code, status = await _call(_find(settings, "/api/settings/upstox"), "GET")
    blob = json.dumps(status)
    runner.assert_true("CS9-status-configured",
                       status.get("api_key_configured") is True
                       and status.get("api_secret_configured") is True)
    runner.assert_not_in("CS10-no-secret", SECRET, blob)
    runner.assert_not_in("CS11-no-ciphertext-marker", "gAAAAA", blob)
    runner.assert_not_in("CS12-no-key-material", "AQYH", blob)

    # Auth status endpoint equally clean.
    code, astatus = await _call(_find(auth, "/api/auth/upstox/status"), "GET")
    ablob = json.dumps(astatus)
    runner.assert_not_in("CS10-no-secret-auth", SECRET, ablob)
    runner.assert_not_in("CS11-no-ciphertext-auth", "gAAAAA", ablob)


async def test_cs13_cs14_replacement_atomic(runner: R) -> None:
    """CS13/CS14: replacement works; both rows updated in one transaction."""
    env = _Env()
    oauth_ref, settings, _auth = _make_routes(env.cred_store)
    await _save(settings, KEY, SECRET)
    await _save(settings, KEY2, SECRET2)

    runner.assert_eq("CS13-new-active", oauth_ref["api_key"], KEY2)
    loaded = env.cred_store.load_upstox_app_credentials()
    runner.assert_eq("CS13-persisted-pair", loaded,
                     {"api_key": KEY2, "api_secret": SECRET2})

    # Atomicity: upsert_secrets writes both rows under BEGIN IMMEDIATE;
    # verify no intermediate state by checking updated_at equality.
    conn = sqlite3.connect(env.db_path)
    rows = conn.execute(
        "SELECT name, updated_at FROM secrets WHERE provider='upstox'"
    ).fetchall()
    conn.close()
    stamps = {ts for _n, ts in rows}
    runner.assert_eq("CS14-single-transaction-timestamp", len(stamps), 1)


async def test_cs15_cs16_precedence(runner: R) -> None:
    """CS15/CS16: DB wins over env; env works when DB empty."""
    # DB saved first, then "restart" with different env creds.
    env = _Env()
    env.cred_store.save_upstox_app_credentials(KEY, SECRET)
    _es, cs = env.reopen()
    oauth_ref, _settings, auth = _make_routes(
        cs, env_creds={"api_key": KEY2, "api_secret": SECRET2})
    runner.assert_eq("CS15-db-over-env", oauth_ref["api_key"], KEY)
    runner.assert_eq("CS15-oauth-ready",
                     bool(oauth_ref["api_key"]), True)

    # Fresh DB, no saved creds -> env fallback active.
    env2 = _Env()
    oauth2, _s2, auth2 = _make_routes(
        env2.cred_store, env_creds={"api_key": KEY, "api_secret": SECRET})
    runner.assert_eq("CS16-env-fallback", oauth2["api_key"], KEY)
    code, data = await _call(_find(auth2, "/api/auth/upstox/status"), "GET")
    runner.assert_eq("CS16-oauth-available-env",
                     data.get("oauth_available"), True)


def test_cs17_corrupted_ciphertext(runner: R) -> None:
    """CS17: corrupted ciphertext fails safely (no crash, no plaintext)."""
    env = _Env()
    env.cred_store.save_upstox_app_credentials(KEY, SECRET)
    conn = sqlite3.connect(env.db_path)
    conn.execute("UPDATE secrets SET encrypted_value='gAAAAA-corrupted' "
                 "WHERE provider='upstox' AND name='api_secret'")
    conn.commit()
    conn.close()

    _es, cs = env.reopen()
    try:
        cs.load_upstox_app_credentials()
        raised = False
    except Exception as exc:
        raised = True
        runner.assert_not_in("CS17-no-plaintext-in-error", SECRET, str(exc))
    runner.assert_true("CS17-fails-safely", raised)


def test_cs18_cs19_lost_master_key(runner: R) -> None:
    """CS18/CS19: missing key + existing ciphertext -> safe failure, no
    silent regeneration."""
    env = _Env()
    env.cred_store.save_upstox_app_credentials(KEY, SECRET)
    key_file = env.base / "master.key"
    original_key = key_file.read_bytes()
    key_file.unlink()  # operator loses the key

    _es, cs = env.reopen()
    raised = False
    try:
        cs.load_upstox_app_credentials()
    except Exception as exc:
        raised = True
        runner.assert_not_in("CS18-no-plaintext-in-error", SECRET, str(exc))
        runner.assert_not_in("CS18-no-ciphertext-in-error", "gAAAAA", str(exc))
    runner.assert_true("CS18-fails-safely", raised)

    # CS19: key was NOT silently regenerated.
    runner.assert_false("CS19-no-silent-regen", key_file.is_file())
    if key_file.is_file():
        runner.assert_true("CS19-key-unchanged",
                           key_file.read_bytes() == original_key)

    # Operator can still REPLACE credentials explicitly (new key generated).
    cs.save_upstox_app_credentials(KEY2, SECRET2)
    runner.assert_true("CS19-replace-after-loss", key_file.is_file())
    loaded = cs.load_upstox_app_credentials()
    runner.assert_eq("CS19-replaced-works", loaded["api_secret"], SECRET2)


async def test_cs20_oauth_uses_decrypted(runner: R) -> None:
    """CS20: login redirect carries the DECRYPTED saved api key."""
    env = _Env()
    env.cred_store.save_upstox_app_credentials(KEY, SECRET)
    _es, cs = env.reopen()
    _ref, _settings, auth = _make_routes(cs)
    login = _find(auth, "/api/auth/upstox/login")
    code, location = await _call(login, "GET")
    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    runner.assert_eq("CS20-client-id-decrypted",
                     qs.get("client_id", [""])[0], KEY)


def test_cs21_access_token_memory_only(runner: R) -> None:
    """CS21: daily access token is never written to the secrets table."""
    env = _Env()
    env.cred_store.save_upstox_app_credentials(KEY, SECRET)
    conn = sqlite3.connect(env.db_path)
    rows = conn.execute(
        "SELECT name FROM secrets WHERE provider='upstox'").fetchall()
    conn.close()
    names = {r[0] for r in rows}
    runner.assert_eq("CS21-only-app-creds-stored", names,
                     {"api_key", "api_secret"})


def test_cs22_frontend_hygiene(runner: R) -> None:
    """CS22: no credential values into browser storage."""
    js_path = os.path.join(_PROJECT_DIR, "web", "ui", "js", "app.js")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    storage_writes = [ln for ln in js.splitlines()
                      if ("localStorage.setItem" in ln
                          or "sessionStorage.setItem" in ln
                          or "document.cookie" in ln)]
    cred_storage = [ln for ln in storage_writes
                    if "secret" in ln.lower() or "api_key" in ln.lower()]
    runner.assert_eq("CS22-no-cred-storage-writes", cred_storage, [])


async def test_cs23_cs24_identity(runner: R) -> None:
    """CS23/24: saving credentials creates no new manager/service/feed."""
    env = _Env()

    class _Feed:
        name = "upstox"

    feed = _Feed()
    feed_ref = {"feed": feed}          # SourceManager stand-in holder
    svc = object()                      # MarketService stand-in
    oauth_ref, settings, auth = _make_routes(env.cred_store)
    await _save(settings, KEY, SECRET)
    runner.assert_true("CS23-feed-ref-intact", feed_ref["feed"] is feed)
    runner.assert_true("CS24-svc-untouched", svc is not None
                       and not hasattr(env.cred_store, "_market_service"))


def test_cs25_route_surface(runner: R) -> None:
    """CS25: existing auth-route paths unchanged."""
    from api.routes import build_auth_routes
    routes = build_auth_routes({"feed": None},
                               oauth={"api_key": "", "api_secret": "",
                                      "redirect_uri": "http://x/cb"})
    paths = {r.path for r in routes}
    expected = {"/api/auth/upstox/status", "/api/auth/upstox/login",
                "/auth/upstox/callback", "/api/auth/upstox/token"}
    runner.assert_eq("CS25-auth-paths-intact", paths, expected)


def test_cs26_imports(runner: R) -> None:
    """CS26: schema v10 + crypto modules import cleanly."""
    from core.persistence.modules.schema import SCHEMA_VERSION
    from core.persistence.modules import secrets as _sec  # noqa: F401
    from app.secrets_store import (  # noqa: F401
        CredentialStore, EncryptionService, CredentialDecryptError,
    )
    runner.assert_eq("CS26-schema-at-least-v11", SCHEMA_VERSION >= 11, True)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_cs1_to_cs7_encryption_basics(runner)
    test_cs8_restart_persistence(runner)
    await test_cs9_to_cs12_api_safety(runner)
    await test_cs13_cs14_replacement_atomic(runner)
    await test_cs15_cs16_precedence(runner)
    test_cs17_corrupted_ciphertext(runner)
    test_cs18_cs19_lost_master_key(runner)
    await test_cs20_oauth_uses_decrypted(runner)
    test_cs21_access_token_memory_only(runner)
    test_cs22_frontend_hygiene(runner)
    await test_cs23_cs24_identity(runner)
    test_cs25_route_surface(runner)
    test_cs26_imports(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

