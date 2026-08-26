"""Fyers PIN-based session restore (PIN1-PIN4).

The operator pain: every server restart wiped the runtime access token
and refresh restore failed because Fyers' refresh endpoint requires the
account PIN. With the PIN saved (encrypted), restarts auto-restore.
"""
import asyncio
import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


def test_pin1_store_roundtrip(runner: R) -> None:
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    tmp = tempfile.mkdtemp()
    store = CredentialStore(EventStore(os.path.join(tmp, "e.db")),
                            data_dir=tmp)
    store.save_fyers_pin("1234")
    runner.assert_eq("PIN1-roundtrip", store.load_fyers_pin(), "1234")


def test_pin2_refresh_includes_pin(runner: R) -> None:
    from brokers.fyers.auth import FyersAuth
    seen = {}

    async def transport(url, body):
        seen["url"] = url
        seen["body"] = dict(body)
        return 200, {"access_token": "NEW-TOK"}

    auth = FyersAuth(app_id="A", secret_id="S",
                     redirect_uri="http://localhost:7070/auth/fyers/cb",
                     transport=transport)
    bundle = asyncio.run(auth.refresh_access_token("REF-1", pin="1234"))
    runner.assert_eq("PIN2-token", bundle["access_token"], "NEW-TOK")
    runner.assert_eq("PIN2-pin-sent", seen["body"].get("pin"), "1234")
    runner.assert_in("PIN2-refresh-url",
                     "/api/v3/validate-refresh-token", seen["url"])


def test_pin3_restore_uses_stored_pin(runner: R) -> None:
    """Full startup-restore path: stored creds + refresh + PIN in the
    credential store -> runtime token set without any manual login."""
    import app.server as srv
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    import brokers.fyers.auth as fauth

    tmp = tempfile.mkdtemp()
    cstore = CredentialStore(EventStore(os.path.join(tmp, "e.db")),
                             data_dir=tmp)
    cstore.save_fyers_credentials("APP-R", "SEC-R")
    cstore.save_fyers_refresh_token("REF-R")
    cstore.save_fyers_pin("9876")

    captured = {}

    async def fake_refresh(self, refresh_token, pin=None):
        captured["refresh"] = refresh_token
        captured["pin"] = pin
        return {"access_token": "RESTORED-TOK",
                "refresh_token": refresh_token}

    orig = fauth.FyersAuth.refresh_access_token
    fauth.FyersAuth.refresh_access_token = fake_refresh
    old_store = getattr(srv, "_credential_store", None)
    old_token = dict(srv._fyers_runtime_token)
    restored_value = None
    try:
        srv._credential_store = cstore
        srv._fyers_runtime_token["access_token"] = ""
        asyncio.run(srv._try_restore_fyers_token())
        restored_value = srv._fyers_runtime_token["access_token"]
    finally:
        srv._credential_store = old_store
        srv._fyers_runtime_token.clear()
        srv._fyers_runtime_token.update(old_token)
        fauth.FyersAuth.refresh_access_token = orig

    runner.assert_eq("PIN3-refresh-passed", captured.get("refresh"), "REF-R")
    runner.assert_eq("PIN3-pin-passed", captured.get("pin"), "9876")
    runner.assert_eq("PIN3-runtime-token-set", restored_value,
                     "RESTORED-TOK")


def test_pin4_no_pin_still_safe_fallback(runner: R) -> None:
    """Without a stored PIN the restore fails safely (Login Required)."""
    import app.server as srv
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    import brokers.fyers.auth as fauth

    tmp = tempfile.mkdtemp()
    cstore = CredentialStore(EventStore(os.path.join(tmp, "e.db")),
                             data_dir=tmp)
    cstore.save_fyers_credentials("APP-R", "SEC-R")
    cstore.save_fyers_refresh_token("REF-R")
    # No PIN saved.

    async def fake_refresh(self, refresh_token, pin=None):
        raise fauth.FyersAuthError("pin required")

    orig = fauth.FyersAuth.refresh_access_token
    fauth.FyersAuth.refresh_access_token = fake_refresh
    old_store = getattr(srv, "_credential_store", None)
    old_token = dict(srv._fyers_runtime_token)
    try:
        srv._credential_store = cstore
        srv._fyers_runtime_token["access_token"] = ""
        asyncio.run(srv._try_restore_fyers_token())
    finally:
        srv._credential_store = old_store
        srv._fyers_runtime_token.clear()
        srv._fyers_runtime_token.update(old_token)
        fauth.FyersAuth.refresh_access_token = orig

    runner.assert_eq("PIN4-safe-fallback-empty",
                     srv._fyers_runtime_token["access_token"], "")


if __name__ == "__main__":
    runner = R()
    test_pin1_store_roundtrip(runner)
    test_pin2_refresh_includes_pin(runner)
    test_pin3_restore_uses_stored_pin(runner)
    test_pin4_no_pin_still_safe_fallback(runner)
    sys.exit(0 if runner.summary() else 1)
