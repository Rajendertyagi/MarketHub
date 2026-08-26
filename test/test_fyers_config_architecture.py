import asyncio
import json
import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


# ---------------------------------------------------------------------------
# 1. config.example.json contains NO secret fields
# ---------------------------------------------------------------------------

def test_config_example_has_no_secrets(runner: R) -> None:
    _path = os.path.join(_PROJECT_DIR, "config.example.json")
    runner.assert_true("CE-exists", os.path.isfile(_path))
    with open(_path, "r", encoding="utf-8") as _f:
        _cfg = json.load(_f)

    def _collect(obj):
        if isinstance(obj, dict):
            for _k, _v in obj.items():
                _keys_seen.append(str(_k).lower())
                _collect(_v)
        elif isinstance(obj, list):
            for _v in obj:
                _collect(_v)
    _keys_seen = []
    _collect(_cfg)
    for _bad in ("app_secret", "refresh_token", "access_token", "secret_id"):
        runner.assert_true("CE-no-secret-key:" + _bad, _bad not in _keys_seen)
    _fyers = _cfg.get("sources", {}).get("fyers", {})
    runner.assert_eq("CE-fyers-type", _fyers.get("type"), "fyers_feed")
    runner.assert_true("CE-fyers-no-app-secret", "app_secret" not in _fyers)
    runner.assert_true("CE-fyers-no-app-id", "app_id" not in _fyers)


# ---------------------------------------------------------------------------
# 2. OAuth redirect URI: ONE source of truth, default + non-default
# ---------------------------------------------------------------------------

def test_oauth_callback_url_single_source(runner: R) -> None:
    from app.config import oauth_callback_url
    runner.assert_eq(
        "OU-default",
        oauth_callback_url("http://localhost:7070", "fyers"),
        "http://localhost:7070/auth/fyers/callback")
    runner.assert_eq(
        "OU-nondefault",
        oauth_callback_url("http://192.168.1.50:9090", "fyers"),
        "http://192.168.1.50:9090/auth/fyers/callback")
    runner.assert_eq(
        "OU-upstox",
        oauth_callback_url("http://localhost:7070", "upstox"),
        "http://localhost:7070/auth/upstox/callback")
    runner.assert_eq(
        "OU-empty-fallback",
        oauth_callback_url("", "fyers"),
        "http://localhost:7070/auth/fyers/callback")


# ---------------------------------------------------------------------------
# 3. Encrypted credential store is the single source of truth
# ---------------------------------------------------------------------------

def test_credential_store_fyers_model(runner: R) -> None:
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    _tmp = tempfile.mkdtemp()
    _db = os.path.join(_tmp, "events.db")
    _store = CredentialStore(EventStore(_db))
    _store.save_fyers_credentials("APP-9", "SEC-9")
    _creds = _store.load_fyers_credentials()
    runner.assert_eq("CS-app-id", _creds["app_id"], "APP-9")
    runner.assert_eq("CS-app-secret", _creds["app_secret"], "SEC-9")
    _store.save_fyers_refresh_token("REF-9")
    runner.assert_eq("CS-refresh", _store.load_fyers_refresh_token(), "REF-9")
    # Canonical key wins over the legacy 'fyers_refresh' layout.
    _store.save_app_credentials("fyers_refresh", "refresh", "LEGACY-REF")
    runner.assert_eq("CS-canonical-wins",
                     _store.load_fyers_refresh_token(), "REF-9")
    # Legacy layout is a fallback only when no canonical token exists.
    _store2 = CredentialStore(EventStore(os.path.join(_tmp, "legacy.db")))
    _store2.save_app_credentials("fyers_refresh", "refresh", "LEGACY-ONLY")
    runner.assert_eq("CS-legacy-fallback",
                     _store2.load_fyers_refresh_token(), "LEGACY-ONLY")


# ---------------------------------------------------------------------------
# 4. FyersFeed built WITHOUT secrets in config; resolves app_id from store
# ---------------------------------------------------------------------------

def test_fyers_feed_no_config_secret(runner: R) -> None:
    from sources.registry import _create_fyers_feed

    class _Store:
        def load_fyers_credentials(self):
            return {"app_id": "APP-FROM-STORE", "app_secret": "SEC"}

    _cfg = {
        "type": "fyers_feed",
        "mode": "full",
        "instruments": [{"key": "NSE:NIFTY50-INDEX",
                         "exchange": "NSE", "tradingsymbol": "NIFTY50"}],
        "access_token_getter": lambda: "",
        "credential_store": _Store(),
        "redirect_uri": "http://localhost:7070/auth/fyers/callback",
    }
    _feed = _create_fyers_feed(_cfg)
    runner.assert_eq("FF-app-id-from-store",
                     _feed._resolve_app_id(), "APP-FROM-STORE")
    runner.assert_false("FF-not-ready-no-token", _feed.is_ready_to_start())
    _cfg2 = dict(_cfg)
    _cfg2["access_token_getter"] = lambda: "TOK"
    _feed2 = _create_fyers_feed(_cfg2)
    runner.assert_true("FF-ready-with-token", _feed2.is_ready_to_start())


# ---------------------------------------------------------------------------
# 5. OAuth routes + feed both read the SAME credential store
# ---------------------------------------------------------------------------

def test_oauth_and_feed_share_store(runner: R) -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    from api.product_routes import build_fyers_auth_routes
    from sources.registry import _create_fyers_feed

    _tmp = tempfile.mkdtemp()
    _store = CredentialStore(EventStore(os.path.join(_tmp, "e.db")))
    _store.save_fyers_credentials("APP-SHARED", "SEC-SHARED")

    _app = Starlette(routes=build_fyers_auth_routes(
        _store, runtime_token={"access_token": ""},
        redirect_uri="http://localhost:7070/auth/fyers/callback"))
    _c = TestClient(_app)
    _r = _c.get("/api/settings/fyers")
    runner.assert_eq("OA-status-code", _r.status_code, 200)
    _body = _r.json()
    runner.assert_true("OA-app-id-configured", _body["app_id_configured"])
    _feed = _create_fyers_feed({
        "type": "fyers_feed", "mode": "full",
        "instruments": [{"key": "NSE:NIFTY50-INDEX"}],
        "access_token_getter": lambda: "",
        "credential_store": _store,
        "redirect_uri": "http://localhost:7070/auth/fyers/callback",
    })
    runner.assert_eq("OA-feed-app-id",
                     _feed._resolve_app_id(), "APP-SHARED")


# ---------------------------------------------------------------------------
# 6. Login route uses the configured redirect URI (one source of truth)
# ---------------------------------------------------------------------------

def test_login_route_uses_configured_redirect(runner: R) -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore
    from api.product_routes import build_fyers_auth_routes
    import brokers.fyers.auth as _fauth

    _tmp = tempfile.mkdtemp()
    _store = CredentialStore(EventStore(os.path.join(_tmp, "e.db")))
    _store.save_fyers_credentials("APP-X", "SEC-X")

    _CUSTOM = "http://lan-host:8080/auth/fyers/callback"
    _orig = _fauth.FyersAuth.login_url
    try:
        def _fake_login_url(self, *, state=None):
            return "https://api.fyers.in/login?redirect_uri=" + self._redirect_uri
        _fauth.FyersAuth.login_url = _fake_login_url
        _app = Starlette(routes=build_fyers_auth_routes(
            _store, runtime_token={"access_token": ""},
            redirect_uri=_CUSTOM))
        _c = TestClient(_app, follow_redirects=False)
        _r = _c.get("/api/auth/fyers/login")
        runner.assert_eq("LR-status", _r.status_code, 302)
        runner.assert_in("LR-custom-redirect", _CUSTOM, _r.headers["location"])
    finally:
        _fauth.FyersAuth.login_url = _orig


# ---------------------------------------------------------------------------
# 7. Startup refresh-token restore -> runtime readiness (and failure path)
# ---------------------------------------------------------------------------

def test_refresh_restore_and_failure(runner: R) -> None:
    import app.server as _srv
    import brokers.fyers.auth as _fauth
    from core.persistence.store import EventStore
    from app.secrets_store import CredentialStore

    _tmp = tempfile.mkdtemp()
    _store = CredentialStore(EventStore(os.path.join(_tmp, "e.db")))
    _store.save_fyers_credentials("APP-R", "SEC-R")
    _store.save_fyers_refresh_token("REF-R")

    _srv._credential_store = _store
    _srv._fyers_runtime_token["access_token"] = ""

    async def _ok_refresh(self, rt, pin=None):
        return {"access_token": "TOK-RESTORED", "refresh_token": rt}
    async def _bad_refresh(self, rt, pin=None):
        raise _fauth.FyersAuthError("refresh rejected")

    _orig = _fauth.FyersAuth.refresh_access_token
    try:
        _fauth.FyersAuth.refresh_access_token = _ok_refresh
        asyncio.run(_srv._try_restore_fyers_token())
        runner.assert_eq("RR-token-set",
                         _srv._fyers_runtime_token["access_token"],
                         "TOK-RESTORED")
        _srv._fyers_runtime_token["access_token"] = ""
        _fauth.FyersAuth.refresh_access_token = _bad_refresh
        asyncio.run(_srv._try_restore_fyers_token())
        runner.assert_eq("RR-failure-no-token",
                         _srv._fyers_runtime_token["access_token"], "")
    finally:
        _fauth.FyersAuth.refresh_access_token = _orig


if __name__ == "__main__":
    _runner = R()
    test_config_example_has_no_secrets(_runner)
    test_oauth_callback_url_single_source(_runner)
    test_credential_store_fyers_model(_runner)
    test_fyers_feed_no_config_secret(_runner)
    test_oauth_and_feed_share_store(_runner)
    test_login_route_uses_configured_redirect(_runner)
    test_refresh_restore_and_failure(_runner)
    _success = _runner.summary()
    sys.exit(0 if _success else 1)
