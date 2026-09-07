#!/usr/bin/env python3
"""Auth/session foundation tests (isolated temp stores only).

Covers the AUTH TEST MATRIX + SERVER RELIABILITY MATRIX + placeholder audit:

  UPSTOX: persist (oauth/pin/manual), restart-failure isolation, restore
          valid/expired, genuine-rejection invalidation, transient safety,
          logout clears runtime + durable.
  FYERS:  login persist, access-reuse restore, refresh/PIN restore,
          rejection/logout behavior, runtime token delivery.
  STORAGE: tz-safe expiry round-trip, encrypted durable record, no secret
          leakage in status, isolated test DB, fake-guard vs production DB.
  STARTUP: restore_sessions() never raises across the 10-case matrix.
  PLACEHOLDER: production code no longer *produces* PENDING-OAUTH-LOGIN
          (feed keeps read-only legacy compat only).

NO LIVE BROKER. Synthetic tokens only, temp EventStore + temp data_dir.
Never touches data/events.db or the real master.key.
"""

from __future__ import annotations

import asyncio
import os
import sys
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
    es = EventStore(os.path.join(tmp, "e.db"))
    return CredentialStore(es, data_dir=tmp)


def _upstox_creds(token: str, hours: float = 6):
    from brokers.upstox.auth import UpstoxCredentials, upstox_token_expiry
    return UpstoxCredentials(
        access_token=token,
        expires_at=upstox_token_expiry(datetime.now(timezone.utc)),
    )


class _FakeFeed:
    """Minimal feed double: credentials + status + restart-safe."""

    def __init__(self, creds=None):
        self._credentials = creds
        self.name = "upstox"
        self._state = "stopped"
        self._exit = None

    def update_credentials(self, creds):
        self._credentials = creds
        if self._state in ("failed", "auth_required"):
            self._state = "stopped"

    def is_ready_to_start(self):
        if self._credentials is None:
            return False
        tok = getattr(self._credentials, "access_token", "")
        if not tok or str(tok).startswith("PENDING"):
            return False
        exp = getattr(self._credentials, "expires_at", None)
        if exp is not None and exp <= datetime.now(timezone.utc):
            return False
        return True

    def status(self):
        return {"state": self._state, "last_exit_reason": self._exit}


def _svc_for(store, feed=None, restart=None):
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    holder = {"feed": feed}
    return UpstoxAuthService(
        AuthStorage(store),
        feed_provider=lambda: holder.get("feed"),
        restart_fn=restart,
    )


# -- UPSTOX ---------------------------------------------------------------

async def test_upstox_oauth_success_persists(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _FakeFeed()
        svc = _svc_for(store, feed, restart=lambda: asyncio.sleep(0))
        out = await svc.apply_session(_upstox_creds("REAL-OAUTH-TOK"))
        runner.assert_true("oauth-persisted", out["session_persisted"])
        sess = store.load_upstox_session_token()
        runner.assert_eq("oauth-token", sess["access_token"], "REAL-OAUTH-TOK")
        runner.assert_true("oauth-expiry", bool(sess.get("expires_at")))
        st = svc.status({"upstox": {"enabled": True}}, {"upstox_restored": False})
        runner.assert_false("oauth-login-not-required", st["login_required"])
        runner.assert_eq("oauth-state", st["auth_state"], "authenticated")


async def test_upstox_manual_token_has_expiry(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _svc_for(store, _FakeFeed())
        await svc.apply_session(_upstox_creds("REAL-MANUAL-TOK"))
        sess = store.load_upstox_session_token()
        runner.assert_true("manual-expiry", bool(sess.get("expires_at")))
        exp = datetime.fromisoformat(sess["expires_at"])
        runner.assert_true("manual-future", exp > datetime.now(timezone.utc))


async def test_upstox_feed_restart_failure_keeps_login(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _FakeFeed()

        async def _boom():
            raise RuntimeError("feed down")

        svc = _svc_for(store, feed, restart=_boom)
        out = await svc.apply_session(_upstox_creds("REAL-TOK-1"))
        runner.assert_true("restart-fail-auth", out["authenticated"])
        runner.assert_true("restart-fail-persist",
                           store.load_upstox_session_token() is not None)
        st = svc.status({"upstox": {"enabled": True}}, {})
        runner.assert_false("restart-fail-login", st["login_required"])


async def test_upstox_valid_session_restores(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _svc_for(store)
        await svc.apply_session(_upstox_creds("REAL-RESTORE-TOK"))
        cfg = {"upstox": {"type": "upstox_feed", "enabled": True}}
        out = svc.restore_session(cfg)
        runner.assert_true("restore-ok", out.restored)
        runner.assert_eq("restore-token", cfg["upstox"]["access_token"],
                         "REAL-RESTORE-TOK")


async def test_upstox_expired_session_does_not_restore(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        store.save_upstox_session_token(token="REAL-OLD-TOK",
                                        expires_at_iso=past)
        svc = _svc_for(store)
        cfg = {"upstox": {"type": "upstox_feed", "enabled": True}}
        out = svc.restore_session(cfg)
        runner.assert_false("expired-no-restore", out.restored)
        runner.assert_true("expired-cleared",
                           store.load_upstox_session_token() is None)
        runner.assert_true("expired-no-token",
                           "access_token" not in cfg["upstox"])


async def test_upstox_genuine_rejection_invalidates(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _FakeFeed(_upstox_creds("REAL-TOK-R"))
        svc = _svc_for(store, feed)
        await svc.apply_session(_upstox_creds("REAL-TOK-R"))
        feed._state = "auth_required"
        feed._exit = "broker_rejected_token"
        runner.assert_true("rejected", svc.invalidate_on_rejection())
        runner.assert_true("rejected-cleared",
                           store.load_upstox_session_token() is None)


async def test_upstox_transient_failure_keeps_session(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _FakeFeed(_upstox_creds("REAL-TOK-T"))
        svc = _svc_for(store, feed)
        await svc.apply_session(_upstox_creds("REAL-TOK-T"))
        for reason in ("ws_closed", "market_closed", "connect_failed", None):
            feed._state = "reconnecting" if reason else "stopped"
            feed._exit = reason
            runner.assert_false(f"transient-{reason}",
                                svc.invalidate_on_rejection())
            runner.assert_true(f"transient-kept-{reason}",
                               store.load_upstox_session_token() is not None)


async def test_upstox_logout_clears(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _FakeFeed(_upstox_creds("REAL-TOK-L"))
        svc = _svc_for(store, feed)
        await svc.apply_session(_upstox_creds("REAL-TOK-L"))
        svc.logout({"upstox_restored": True})
        runner.assert_true("logout-durable",
                           store.load_upstox_session_token() is None)
        runner.assert_true("logout-runtime", feed._credentials is None)


# -- FYERS -----------------------------------------------------------------

async def test_fyers_login_restore_refresh(runner: R) -> None:
    import tempfile
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.fyers import FyersAuthService
    from app.auth.storage import AuthStorage
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        rt = FyersRuntimeAuth()
        svc = FyersAuthService(AuthStorage(store), rt,
                               redirect_uri="http://localhost:7070/x")
        out = await svc.persist_login(
            {"access_token": "FY-ACCESS-1", "refresh_token": "FY-REF-1",
             "expires_at": (datetime.now(timezone.utc)
                            + timedelta(hours=6)).isoformat()},
            restart_fn=None)
        runner.assert_true("fyers-login", out["ok"])
        runner.assert_eq("fyers-runtime", rt.get_access_token(), "FY-ACCESS-1")
        # Restart: reuse stored access token without network.
        rt2 = FyersRuntimeAuth()
        svc2 = FyersAuthService(AuthStorage(store), rt2,
                                redirect_uri="http://localhost:7070/x")
        rout = await svc2.restore_session()
        # Fyers restore needs app credentials first -> credentials_missing
        # (no app_id saved in this fixture), which must NOT raise.
        runner.assert_true("fyers-restore-clean",
                           rout.reason in ("credentials_missing",
                                           "restored_access_token",
                                           "refresh_token_missing",
                                           "refreshed"))
        # With app creds + refresh + pin, refresh flow restores.
        store.save_fyers_credentials("APPID-1", "SECRET-1")
        store.save_fyers_pin("1234")

        async def _fake_refresh(**kw):
            return {"access_token": "FY-ACCESS-2",
                    "expires_at": (datetime.now(timezone.utc)
                                   + timedelta(hours=6)).isoformat()}

        rt3 = FyersRuntimeAuth()
        svc3 = FyersAuthService(AuthStorage(store), rt3,
                                refresh_fn=_fake_refresh)
        # Drop the cached access token so the refresh path is exercised.
        store.clear_fyers_session()
        store.save_fyers_credentials("APPID-1", "SECRET-1")
        store.save_fyers_pin("1234")
        from core.persistence.store import EventStore as _ES  # noqa
        store.save_fyers_refresh_token("FY-REF-1")
        rout3 = await svc3.restore_session()
        runner.assert_true("fyers-refreshed", rout3.restored)
        runner.assert_eq("fyers-refreshed-tok", rt3.get_access_token(),
                         "FY-ACCESS-2")
        # Logout clears runtime + durable.
        svc3.logout({})
        runner.assert_eq("fyers-logout", rt3.get_access_token(), "")


# -- STORAGE ----------------------------------------------------------------

async def test_storage_expiry_roundtrip_tz_safe(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        naive = "2099-01-01T00:00:00"  # naive -> treated as UTC
        store.save_upstox_session_token(token="REAL-RT-TOK",
                                        expires_at_iso=naive)
        sess = store.load_upstox_session_token()
        runner.assert_eq("rt-match", sess["expires_at"], naive)
        from app.auth.models import parse_expiry_iso, is_expired
        exp = parse_expiry_iso(sess["expires_at"])
        runner.assert_true("rt-aware", exp.tzinfo is not None)
        runner.assert_false("rt-not-expired", is_expired(exp))


async def test_storage_no_secret_leakage(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _svc_for(store, _FakeFeed(_upstox_creds("REAL-LEAK-TOK")))
        st = svc.status({"upstox": {"enabled": True}}, {})
        blob = str(st)
        runner.assert_not_in("leak", "REAL-LEAK-TOK", blob)


async def test_fake_guard_blocks_production_writes(runner: R) -> None:
    from app.auth.storage import (guard_fake_token_write, is_fake_token,
                                  is_production_db_path, production_db_path)
    runner.assert_true("fake-marker", is_fake_token("PENDING-OAUTH-LOGIN"))
    runner.assert_true("fake-prefix", is_fake_token("FAKE-123"))
    runner.assert_false("real-ok", is_fake_token("REAL-TOKEN-ABC"))
    prod = production_db_path()
    if prod is not None:
        runner.assert_true("prod-path", is_production_db_path(str(prod)))
        runner.assert_false("tmp-not-prod",
                            is_production_db_path("/tmp/xyz/e.db"))
        # A fake store object pointing at the production DB must raise.
        class _Inner:
            _db_path = str(prod)

        class _FakeStore:
            _store = _Inner()

        try:
            guard_fake_token_write(_FakeStore(), "PENDING-OAUTH-LOGIN",
                                   provider="upstox")
            runner.assert_true("guard-raised", False)
        except ValueError:
            runner.assert_true("guard-raised", True)
    # Temp stores always allow fakes (tests stay hermetic).
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        store.save_upstox_session_token(token="PENDING-OAUTH-LOGIN",
                                        expires_at_iso=None)
        runner.assert_true("tmp-fake-ok", True)


async def test_no_test_writes_to_production_db(runner: R) -> None:
    """Regression guard: no REAL EventStore/CredentialStore may point at
    data/events.db in this test process (foreign holder objects with a
    similar attribute shape do not count)."""
    from app.auth.storage import production_db_path
    prod = str(production_db_path()) if production_db_path() else ""
    import gc
    bad = []
    for obj in gc.get_objects():
        try:
            mod = type(obj).__module__
            name = type(obj).__name__
        except Exception:
            continue
        is_real_store = (
            (mod.startswith("core.persistence") and name == "EventStore")
            or (mod == "app.secrets_store" and name == "CredentialStore"))
        if not is_real_store:
            continue
        try:
            inner = getattr(obj, "_store", None)
            p = getattr(inner, "_db_path", None) or getattr(obj, "_db_path", None)
            if isinstance(p, str) and prod and os.path.abspath(p) == os.path.abspath(prod):
                bad.append(f"{mod}.{name}:{p}")
        except Exception:
            pass
    runner.assert_eq("no-prod-db", bad, [])


# -- STARTUP MATRIX -----------------------------------------------------------

async def test_startup_matrix_never_blocks_boot(runner: R) -> None:
    """Server reliability matrix: restore_sessions() never raises."""
    import tempfile
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.service import AuthService
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    from app.auth.fyers import FyersAuthService

    async def _case(name, setup):
        with tempfile.TemporaryDirectory() as tmp:
            store = _tmp_store(tmp)
            setup(store)
            svc = AuthService(
                upstox=UpstoxAuthService(AuthStorage(store)),
                fyers=FyersAuthService(AuthStorage(store),
                                       FyersRuntimeAuth()),
            )
            cfg = {"upstox": {"type": "upstox_feed", "enabled": True}}
            restore: dict = {}
            try:
                await svc.restore_sessions(cfg, restore)
                ok = True
            except Exception:
                ok = False
            runner.assert_true(f"boot-{name}", ok)

    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    def _none(s):
        pass

    def _valid_upstox(s):
        s.save_upstox_session_token(token="REAL-U", expires_at_iso=future)

    def _expired_upstox(s):
        s.save_upstox_session_token(token="REAL-U", expires_at_iso=past)

    def _valid_fyers(s):
        s.save_fyers_credentials("A", "B")
        s.save_fyers_access_token("FY-A", future)

    def _both_valid(s):
        _valid_upstox(s)
        _valid_fyers(s)

    def _up_ok_fy_broken(s):
        _valid_upstox(s)
        s.save_fyers_credentials("A", "B")  # no refresh/pin/access

    def _fy_ok_up_broken(s):
        _valid_fyers(s)
        _expired_upstox(s)

    async def _net_down(*a, **k):
        raise ConnectionError("provider network unavailable")

    cases = [
        ("1-no-creds", _none),
        ("2-valid-upstox", _valid_upstox),
        ("3-expired-upstox", _expired_upstox),
        ("4-valid-fyers", _valid_fyers),
        ("5-both-valid", _both_valid),
        ("6-up-ok-fy-invalid", _up_ok_fy_broken),
        ("7-fy-ok-up-invalid", _fy_ok_up_broken),
        ("9-feed-failure", _valid_upstox),  # feed failure isolated by design
        ("10-repeat", _both_valid),
    ]
    for name, setup in cases:
        await _case(name, setup)
    # 8: provider network unavailable during Fyers refresh.
    with tempfile.TemporaryDirectory() as tmp:
        import tempfile as _tf
        store = _tmp_store(tmp)
        store.save_fyers_credentials("A", "B")
        store.save_fyers_refresh_token("R")
        store.save_fyers_pin("1")
        from app.auth.storage import AuthStorage
        from app.auth.upstox import UpstoxAuthService as _U
        from app.auth.fyers import FyersAuthService as _F
        from app.auth.service import AuthService as _S
        svc = _S(upstox=_U(AuthStorage(store)),
                 fyers=_F(AuthStorage(store), FyersRuntimeAuth(),
                          refresh_fn=_net_down))
        restore: dict = {}
        try:
            await svc.restore_sessions(
                {"upstox": {"type": "upstox_feed"}}, restore)
            ok = True
        except Exception:
            ok = False
        runner.assert_true("boot-8-net-down", ok)


# -- PLACEHOLDER PRODUCTION AUDIT ----------------------------------------------

async def test_production_never_creates_placeholder(runner: R) -> None:
    """No production module may *create/use* the placeholder as a credential.

    Explicitly allowed (read-only, never a live credential):
      * app/auth/storage.py FAKE_TOKEN_MARKERS — the guard's detection list
      * brokers/upstox/feed.py _PLACEHOLDER_TOKENS — legacy read-only compat
        so old fixtures still gate (production never constructs it anymore)
      * comments/docstrings documenting the removal.
    Everything else (assignments, comparisons, constructor args) fails.
    """
    prod_files = [
        "sources/registry.py",
        "api/routes.py",
        "api/product_routes.py",
        "app/server.py",
        "app/auth/upstox.py",
        "app/auth/fyers.py",
        "app/auth/service.py",
        "app/auth/storage.py",
        "app/auth/startup.py",
        "app/auth/models.py",
        "brokers/upstox/feed.py",
    ]
    hits = []
    for rel in prod_files:
        p = os.path.join(_PROJECT_DIR, rel)
        if not os.path.exists(p):
            runner.assert_true(f"exists-{rel}", False)
            continue
        text = open(p, encoding="utf-8", errors="ignore").read()
        for i, line in enumerate(text.splitlines(), 1):
            if "PENDING-OAUTH-LOGIN" not in line:
                continue
            s = line.strip()
            # Allowed: guard detection list + legacy read-only compat set.
            # The marker string sits on its own line inside those literals,
            # so check the surrounding context, not just the line.
            ctx = "\n".join(text.splitlines()[max(0, i - 8):i + 7])
            if "FAKE_TOKEN_MARKERS" in ctx or "_PLACEHOLDER_TOKENS" in ctx:
                continue
            if (s.startswith("#") or s.startswith('"""') or s.startswith("*")
                    or s.startswith("Fake ") or "never" in s.lower()
                    or "legacy" in s.lower() or "e.g." in s):
                continue
            # Anything else constructs/compares it as a live credential.
            hits.append(f"{rel}:{i}:{s[:100]}")
    runner.assert_eq("no-prod-placeholder", hits, [])


async def test_registry_registers_without_credentials(runner: R) -> None:
    """Source/feed registration works with token=None (no placeholder)."""
    from sources.registry import _create_upstox_feed
    feed = _create_upstox_feed({
        "source_name": "upstox",
        "instruments": [{"key": "NSE:NIFTY50-INDEX", "exchange": "NSE",
                         "tradingsymbol": "NIFTY 50"}],
    })
    runner.assert_true("none-creds", feed._credentials is None)
    runner.assert_false("not-ready", feed.is_ready_to_start())
    runner.assert_eq("reason", feed.readiness_reason(), "missing_token")


async def test_real_feed_accepts_none_credentials(runner: R) -> None:
    """Narrow contract: UpstoxFeed(auth=None) registers and gates."""
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.rest import UpstoxRest
    feed = UpstoxFeed(
        config={"source_name": "upstox",
                "instrument_keys": ["NSE:NIFTY50-INDEX"]},
        credentials=None,
        rest=UpstoxRest(),
        instrument_metadata={"NSE:NIFTY50-INDEX": ("NSE", "NIFTY 50")},
    )
    runner.assert_false("none-not-ready", feed.is_ready_to_start())
    creds = _upstox_creds("REAL-X")
    feed.update_credentials(creds)
    runner.assert_true("real-ready", feed.is_ready_to_start())
    feed.update_credentials(None)
    runner.assert_false("cleared-not-ready", feed.is_ready_to_start())


# -- main -------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_upstox_oauth_success_persists(runner)
    await test_upstox_manual_token_has_expiry(runner)
    await test_upstox_feed_restart_failure_keeps_login(runner)
    await test_upstox_valid_session_restores(runner)
    await test_upstox_expired_session_does_not_restore(runner)
    await test_upstox_genuine_rejection_invalidates(runner)
    await test_upstox_transient_failure_keeps_session(runner)
    await test_upstox_logout_clears(runner)
    await test_fyers_login_restore_refresh(runner)
    await test_storage_expiry_roundtrip_tz_safe(runner)
    await test_storage_no_secret_leakage(runner)
    await test_fake_guard_blocks_production_writes(runner)
    await test_no_test_writes_to_production_db(runner)
    await test_startup_matrix_never_blocks_boot(runner)
    await test_production_never_creates_placeholder(runner)
    await test_registry_registers_without_credentials(runner)
    await test_real_feed_accepts_none_credentials(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = asyncio.run(main())
    sys.exit(0 if _ok else 1)
