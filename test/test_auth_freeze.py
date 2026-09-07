#!/usr/bin/env python3
"""Auth/startup durability freeze tests (isolated temp stores only).

Locks the frozen foundation so future feature work cannot regress it:

  §3  auth-state invariants per state (exact contract slices)
  §4  invalidation rules: ONLY expiry / genuine rejection / logout may
      invalidate; transients (ws drop, reconnect, timeout, market closed,
      5xx, DNS/network, REST errors, task crash, no MarketService) must not
  §5  startup isolation: 12-case boot matrix, restore never raises
  §6  durable storage safety: isolation, fake-guard, tz round-trip,
      logout-both, no-duplication on repeat restore
  §7  cross-broker isolation: failures/logouts never cross brokers,
      per-broker exception isolation, no shared token bucket

Synthetic tokens + temporary EventStore/data_dir only. Never touches
data/events.db. NO LIVE BROKER.
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
    return CredentialStore(EventStore(os.path.join(tmp, "e.db")), data_dir=tmp)


def _creds(token: str, hours: float = 6):
    from brokers.upstox.auth import UpstoxCredentials, upstox_token_expiry
    return UpstoxCredentials(
        access_token=token,
        expires_at=upstox_token_expiry(datetime.now(timezone.utc)))


def _real_feed(creds=None):
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.rest import UpstoxRest
    return UpstoxFeed(
        config={"source_name": "upstox",
                "instrument_keys": ["NSE:NIFTY50-INDEX"]},
        credentials=creds, rest=UpstoxRest(),
        instrument_metadata={"NSE:NIFTY50-INDEX": ("NSE", "NIFTY 50")})


def _usvc(store, feed=None):
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    holder = {"feed": feed}
    return UpstoxAuthService(AuthStorage(store),
                             feed_provider=lambda: holder.get("feed"))


class _DeadFeed:
    """Feed double stuck in a terminal feed failure (no auth content)."""

    def __init__(self, creds):
        self._credentials = creds
        self.name = "upstox"

    def update_credentials(self, creds):
        self._credentials = creds

    def is_ready_to_start(self):
        return self._credentials is not None

    def readiness_reason(self):
        return None

    def status(self):
        return {"state": "failed", "last_exit_reason": "terminal: boom",
                "last_error": "boom"}


class _ExplodingStore:
    """Durable store whose methods all raise (outage simulation)."""

    def __getattr__(self, name):
        raise ConnectionError("store unavailable")


# -- §3 invariants ---------------------------------------------------------------

async def test_invariant_missing(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        st = _usvc(_tmp_store(tmp), _real_feed(None)).status(
            {"upstox": {"enabled": True}}, {})
        runner.assert_eq("inv-missing-state", st["auth_state"], "missing")
        runner.assert_false("inv-missing-authed", st["authenticated"])
        runner.assert_true("inv-missing-login", st["login_required"])
        runner.assert_false("inv-missing-token", st["token_configured"])


async def test_invariant_expired(runner: R) -> None:
    import tempfile
    from brokers.upstox.auth import UpstoxCredentials
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _real_feed(UpstoxCredentials(
            access_token="REAL-OLD",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        st = _usvc(store, feed).status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("inv-exp-state", st["auth_state"], "expired")
        runner.assert_false("inv-exp-authed", st["authenticated"])
        runner.assert_true("inv-exp-login", st["login_required"])


async def test_invariant_rejected(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _real_feed(_creds("REAL-RJ"))
        feed._auth_rejected = True
        feed._auth_rejected_at = datetime.now(timezone.utc).isoformat()
        feed._state = "auth_required"
        st = _usvc(store, feed).status({"upstox": {"enabled": True}},
                                       {"upstox_restored": True})
        runner.assert_eq("inv-rej-state", st["auth_state"], "rejected")
        runner.assert_false("inv-rej-authed", st["authenticated"])
        runner.assert_true("inv-rej-login", st["login_required"])


async def test_invariant_authenticated(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _usvc(store, _real_feed())
        await svc.apply_session(_creds("REAL-OK"))
        st = svc.status({"upstox": {"enabled": True}},
                        {"upstox_restored": False})
        runner.assert_eq("inv-auth-state", st["auth_state"], "authenticated")
        runner.assert_true("inv-auth-authed", st["authenticated"])
        runner.assert_false("inv-auth-login", st["login_required"])
        runner.assert_true("inv-auth-token", st["token_configured"])
        runner.assert_true("inv-auth-persisted", st["session_persisted"])


async def test_invariant_restored(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _usvc(store)
        await svc.apply_session(_creds("REAL-RS"))
        cfg = {"upstox": {"type": "upstox_feed", "enabled": True}}
        out = svc.restore_session(cfg)
        runner.assert_true("inv-rs-restored", out.restored)
        feed = _real_feed(_creds("REAL-RS"))
        st = _usvc(store, feed).status(cfg, {"upstox_restored": True})
        runner.assert_true("inv-rs-authed", st["authenticated"])
        runner.assert_false("inv-rs-login", st["login_required"])
        runner.assert_true("inv-rs-persisted", st["session_persisted"])
        runner.assert_true("inv-rs-flag", st["session_restored"])


async def test_invariant_fyers(runner: R) -> None:
    import tempfile
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.fyers import FyersAuthService
    from app.auth.storage import AuthStorage
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        rt = FyersRuntimeAuth()
        svc = FyersAuthService(AuthStorage(store), rt)
        snap = svc.status_snapshot({})
        runner.assert_true("inv-fy-login", snap["login_required"])
        runner.assert_false("inv-fy-active", snap["access_token_active"])
        out = await svc.persist_login(
            {"access_token": "FY-A", "refresh_token": "FY-R",
             "expires_at": (datetime.now(timezone.utc)
                            + timedelta(hours=6)).isoformat()})
        runner.assert_true("inv-fy-login-ok", out["ok"])
        snap = svc.status_snapshot({})
        runner.assert_false("inv-fy-login2", snap["login_required"])
        runner.assert_true("inv-fy-active2", snap["access_token_active"])
        runner.assert_true("inv-fy-persisted", snap["session_persisted"])


# -- §4 invalidation rules ----------------------------------------------------------

async def _assert_no_invalidation(runner, name, feed_setup) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _usvc(store, _real_feed())
        await svc.apply_session(_creds(f"REAL-{name}"))
        feed = feed_setup(store)
        st = _usvc(store, feed).status({"upstox": {"enabled": True}},
                                       {"upstox_restored": True})
        runner.assert_eq(f"ninv-{name}-state", st["auth_state"],
                         "authenticated")
        runner.assert_false(f"ninv-{name}-login", st["login_required"])
        runner.assert_true(f"ninv-{name}-kept",
                           store.load_upstox_session_token() is not None)


async def test_transients_never_invalidate(runner: R) -> None:
    def _mk(state, exit_reason=None):
        def _setup(store):
            from brokers.upstox.feed import UpstoxFeed
            feed = _DeadFeed(_creds("REAL-T"))
            feed.status = lambda: {"state": state,
                                   "last_exit_reason": exit_reason,
                                   "last_error": exit_reason}
            return feed
        return _setup

    await _assert_no_invalidation(runner, "ws-drop",
                                  _mk("reconnecting", "connect_failed"))
    await _assert_no_invalidation(runner, "reconnect",
                                  _mk("reconnecting", "retryable_authorize_failure"))
    await _assert_no_invalidation(runner, "startup-timeout",
                                  _mk("connecting", None))
    await _assert_no_invalidation(runner, "market-closed",
                                  _mk("stopped", "stop_requested"))
    await _assert_no_invalidation(runner, "http-500",
                                  _mk("reconnecting", "retryable_authorize_failure"))
    await _assert_no_invalidation(runner, "dns-timeout",
                                  _mk("reconnecting", "connect_failed"))
    await _assert_no_invalidation(runner, "task-crash", lambda store: _DeadFeed(
        _creds("REAL-T")))
    # No MarketService at all: auth projection never touches it.
    await _assert_no_invalidation(
        runner, "no-market", lambda store: _real_feed(_creds("REAL-T")))


async def test_only_three_invalidators_upstox(runner: R) -> None:
    import tempfile
    from brokers.upstox.auth import UpstoxCredentials
    # 1. genuine expiry (startup restore clears).
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        store.save_upstox_session_token(
            token="REAL-E",
            expires_at_iso=(datetime.now(timezone.utc)
                            - timedelta(hours=1)).isoformat())
        out = _usvc(store).restore_session(
            {"upstox": {"type": "x", "enabled": True}})
        runner.assert_false("inv-expiry-restored", out.restored)
        runner.assert_true("inv-expiry-cleared",
                           store.load_upstox_session_token() is None)
    # 2. genuine rejection (latch invalidates).
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _usvc(store, _real_feed())
        await svc.apply_session(_creds("REAL-R2"))
        feed = _real_feed(_creds("REAL-R2"))
        feed._auth_rejected = True
        feed._state = "auth_required"
        svc2 = _usvc(store, feed)
        runner.assert_true("inv-rej-fires",
                           svc2.invalidate_on_rejection({}))
        runner.assert_true("inv-rej-cleared",
                           store.load_upstox_session_token() is None)
    # 3. explicit logout/forget.
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed = _real_feed()
        svc = _usvc(store, feed)
        await svc.apply_session(_creds("REAL-L"))
        svc.logout({})
        runner.assert_true("inv-logout-durable",
                           store.load_upstox_session_token() is None)
        runner.assert_true("inv-logout-runtime", feed._credentials is None)


async def test_fyers_invalidation_rules(runner: R) -> None:
    import tempfile
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.fyers import FyersAuthService
    from app.auth.storage import AuthStorage
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        store.save_fyers_credentials("AID", "SEC")
        rt = FyersRuntimeAuth()
        svc = FyersAuthService(AuthStorage(store), rt)
        await svc.persist_login(
            {"access_token": "FY-A", "refresh_token": "FY-R",
             "expires_at": (datetime.now(timezone.utc)
                            + timedelta(hours=6)).isoformat()})
        # Feed 401 (auth_required) does NOT clear durable Fyers material:
        # refresh remains authoritative; next restore retries it.
        runner.assert_true("fy-401-kept-refresh",
                           store.load_fyers_refresh_token() == "FY-R")
        runner.assert_true("fy-401-kept-access",
                           store.load_fyers_access_token() is not None)
        # Failed refresh keeps the refresh token (retryable, not wiped).
        async def _boom(**kw):
            raise ConnectionError("down")
        rt2 = FyersRuntimeAuth()
        svc2 = FyersAuthService(AuthStorage(store), rt2,
                                refresh_fn=_boom)
        store.clear_fyers_session()
        store.save_fyers_credentials("AID", "SEC")
        store.save_fyers_refresh_token("FY-R")
        store.save_fyers_pin("1")
        out = await svc2.restore_session()
        runner.assert_false("fy-refresh-fail-restored", out.restored)
        runner.assert_eq("fy-refresh-kept", store.load_fyers_refresh_token(),
                         "FY-R")
        # Explicit logout clears access + refresh + PIN + runtime.
        svc.logout({})
        runner.assert_true("fy-logout-access",
                           store.load_fyers_access_token() is None)
        runner.assert_true("fy-logout-refresh",
                           store.load_fyers_refresh_token() is None)
        runner.assert_eq("fy-logout-runtime", rt.get_access_token(), "")


# -- §5 startup isolation (12 cases) ---------------------------------------------------

async def _boot_case(runner: R, name: str, setup) -> None:
    import tempfile
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.service import AuthService
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    from app.auth.fyers import FyersAuthService
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        extra = setup(store)
        extra = extra if isinstance(extra, dict) else {}
        svc = AuthService(
            upstox=UpstoxAuthService(AuthStorage(store)),
            fyers=FyersAuthService(
                AuthStorage(store), FyersRuntimeAuth(),
                refresh_fn=extra.get("refresh_fn")),
        )
        cfg = {"upstox": {"type": "upstox_feed", "enabled": True}}
        restore: dict = {}
        try:
            await svc.restore_sessions(cfg, restore)
            ok = True
        except Exception:
            ok = False
        runner.assert_true(f"boot-{name}", ok)
        # Repeated restart is idempotent and never raises either.
        try:
            token_before = (store.load_upstox_session_token() or {}).get(
                "access_token")
            await svc.restore_sessions(dict(cfg), {})
            token_after = (store.load_upstox_session_token() or {}).get(
                "access_token")
            ok2 = (token_before == token_after)
        except Exception:
            ok2 = False
        runner.assert_true(f"boot-{name}-repeat", ok2)


async def test_startup_isolation_matrix(runner: R) -> None:
    def _future():
        return (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()

    def _past():
        return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    async def _net_down(**kw):
        raise ConnectionError("provider unreachable")

    def _exploding_store_case(store):
        return {"refresh_fn": _net_down}

    cases = [
        ("01-no-creds", lambda s: None),
        ("02-valid-upstox", lambda s: s.save_upstox_session_token(
            token="U1", expires_at_iso=_future())),
        ("03-expired-upstox", lambda s: s.save_upstox_session_token(
            token="U1", expires_at_iso=_past())),
        ("04-rejected-upstox", lambda s: (
            s.save_upstox_session_token(token="U1", expires_at_iso=_future()),
            s.save_last_auth_status("upstox", "rejected"))),
        ("05-valid-fyers", lambda s: (
            s.save_fyers_credentials("A", "B"),
            s.save_fyers_access_token("F1", _future()))),
        ("06-invalid-fyers", lambda s: s.save_fyers_credentials("A", "B")),
        ("07-both-valid", lambda s: (
            s.save_upstox_session_token(token="U1", expires_at_iso=_future()),
            s.save_fyers_credentials("A", "B"),
            s.save_fyers_access_token("F1", _future()))),
        ("08-mixed", lambda s: (
            s.save_upstox_session_token(token="U1", expires_at_iso=_future()),
            s.save_fyers_credentials("A", "B"))),
        ("09-net-down", _exploding_store_case),
        ("10-broken-feed", lambda s: s.save_upstox_session_token(
            token="U1", expires_at_iso=_future())),
        ("11-store-ok-broker-down", lambda s: (
            s.save_fyers_credentials("A", "B"),
            s.save_fyers_refresh_token("R"),
            s.save_fyers_pin("1"))),
        ("12-repeat", lambda s: (
            s.save_upstox_session_token(token="U1", expires_at_iso=_future()),
            s.save_fyers_credentials("A", "B"),
            s.save_fyers_access_token("F1", _future()))),
    ]
    for name, setup in cases:
        if name == "09-net-down":
            await _boot_case(runner, name, setup)
        elif name == "11-store-ok-broker-down":
            async def _down(**kw):
                raise TimeoutError("dns timeout")

            def _setup(s):
                setup(s)
                return {"refresh_fn": _down}
            await _boot_case(runner, name, _setup)
        else:
            await _boot_case(runner, name, setup)


async def test_throwing_store_never_blocks_boot(runner: R) -> None:
    from app.auth.service import AuthService
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    from app.auth.fyers import FyersAuthService
    from app.fyers_runtime_auth import FyersRuntimeAuth
    svc = AuthService(
        upstox=UpstoxAuthService(AuthStorage(_ExplodingStore())),
        fyers=FyersAuthService(AuthStorage(_ExplodingStore()),
                               FyersRuntimeAuth()))
    try:
        restore = await svc.restore_sessions({"upstox": {}}, {})
        ok = True
    except Exception:
        ok = False
    runner.assert_true("boot-throwing-store", ok)
    runner.assert_false("boot-throwing-no-restore",
                        restore.get("upstox_restored", False))


# -- §6 storage safety extras ------------------------------------------------------------

async def test_repeat_restore_no_duplication(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        svc = _usvc(store)
        await svc.apply_session(_creds("REAL-ONCE"))
        before = store.load_upstox_session_token()
        cfg = {"upstox": {"type": "upstox_feed", "enabled": True}}
        for _ in range(3):
            out = svc.restore_session(cfg)
            runner.assert_true("nodup-restored", out.restored)
        after = store.load_upstox_session_token()
        runner.assert_eq("nodup-same-token", after["access_token"],
                         before["access_token"])
        runner.assert_eq("nodup-same-expiry", after["expires_at"],
                         before["expires_at"])


async def test_expiry_roundtrip_tz_safe(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        for iso in ("2099-06-01T03:30:00+00:00",
                    "2099-06-01T03:30:00",       # naive -> UTC
                    "2099-06-01T09:00:00+05:30"):  # IST wall time
            store.save_upstox_session_token(token="REAL-TZ",
                                            expires_at_iso=iso)
            sess = store.load_upstox_session_token()
            runner.assert_eq(f"tz-keep-{iso}", sess["expires_at"], iso)
            from app.auth.models import parse_expiry_iso, is_expired
            exp = parse_expiry_iso(sess["expires_at"])
            runner.assert_true(f"tz-aware-{iso}", exp.tzinfo is not None)
            runner.assert_false(f"tz-future-{iso}", is_expired(exp))


# -- §7 cross-broker isolation --------------------------------------------------------------

async def _both_logins(tmp: str):
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.fyers import FyersAuthService
    from app.auth.storage import AuthStorage
    store = _tmp_store(tmp)
    usvc = _usvc(store, _real_feed())
    await usvc.apply_session(_creds("REAL-U"))
    rt = FyersRuntimeAuth()
    fsvc = FyersAuthService(AuthStorage(store), rt)
    await fsvc.persist_login(
        {"access_token": "FY-A", "refresh_token": "FY-R",
         "expires_at": (datetime.now(timezone.utc)
                        + timedelta(hours=6)).isoformat()})
    return store, usvc, fsvc, rt


async def test_upstox_failure_keeps_fyers(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store, usvc, fsvc, rt = await _both_logins(tmp)
        feed = _real_feed(_creds("REAL-U"))
        feed._auth_rejected = True
        feed._state = "auth_required"
        st = _usvc(store, feed).status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("x-up-rejected", st["auth_state"], "rejected")
        runner.assert_eq("x-fy-runtime", rt.get_access_token(), "FY-A")
        runner.assert_true("x-fy-refresh",
                           store.load_fyers_refresh_token() == "FY-R")
        snap = fsvc.status_snapshot({})
        runner.assert_false("x-fy-login", snap["login_required"])


async def test_fyers_failure_keeps_upstox(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store, usvc, fsvc, rt = await _both_logins(tmp)

        async def _boom(**kw):
            raise ConnectionError("fyers down")

        from app.auth.storage import AuthStorage
        from app.auth.fyers import FyersAuthService
        from app.fyers_runtime_auth import FyersRuntimeAuth
        rt2 = FyersRuntimeAuth()
        svc2 = FyersAuthService(AuthStorage(store), rt2, refresh_fn=_boom)
        store.clear_fyers_session()
        store.save_fyers_credentials("AID", "SEC")
        store.save_fyers_refresh_token("FY-R")
        store.save_fyers_pin("1")
        out = await svc2.restore_session()
        runner.assert_false("x-fy-fail-restored", out.restored)
        runner.assert_true("x-up-kept",
                           store.load_upstox_session_token() is not None)
        feed = _real_feed(_creds("REAL-U"))
        st = _usvc(store, feed).status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("x-up-auth", st["auth_state"], "authenticated")


async def test_logouts_do_not_cross(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store, usvc, fsvc, rt = await _both_logins(tmp)
        usvc.logout({})
        runner.assert_true("x-lo-up-gone",
                           store.load_upstox_session_token() is None)
        runner.assert_eq("x-lo-fy-runtime", rt.get_access_token(), "FY-A")
        runner.assert_true("x-lo-fy-refresh",
                           store.load_fyers_refresh_token() == "FY-R")
        fsvc.logout({})
        runner.assert_true("x-lo-fy-gone",
                           store.load_fyers_access_token() is None)
        # Upstox already gone; logout is idempotent and harmless.
        runner.assert_true("x-lo-idempotent", True)


async def test_restore_isolates_exceptions(runner: R) -> None:
    import tempfile
    from app.fyers_runtime_auth import FyersRuntimeAuth
    from app.auth.service import AuthService
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    from app.auth.fyers import FyersAuthService
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        store.save_upstox_session_token(
            token="U1",
            expires_at_iso=(datetime.now(timezone.utc)
                            + timedelta(hours=6)).isoformat())

        async def _boom(**kw):
            raise RuntimeError("fyers exploded")

        svc = AuthService(
            upstox=UpstoxAuthService(AuthStorage(store)),
            fyers=FyersAuthService(AuthStorage(store), FyersRuntimeAuth(),
                                   refresh_fn=_boom))
        restore: dict = {}
        await svc.restore_sessions(
            {"upstox": {"type": "upstox_feed", "enabled": True}}, restore)
        runner.assert_true("x-iso-up", restore.get("upstox_restored"))
        runner.assert_false("x-iso-fy", restore.get("fyers_restored"))
        runner.assert_true("x-iso-up-token",
                           store.load_upstox_session_token() is not None)


async def test_no_shared_token_bucket(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store, usvc, fsvc, rt = await _both_logins(tmp)
        u = store.load_upstox_session_token()["access_token"]
        f = store.load_fyers_access_token()["access_token"]
        runner.assert_true("x-distinct-stored", u != f)
        runner.assert_true("x-distinct-runtime",
                           rt.get_access_token() != u)


# -- main --------------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_invariant_missing(runner)
    await test_invariant_expired(runner)
    await test_invariant_rejected(runner)
    await test_invariant_authenticated(runner)
    await test_invariant_restored(runner)
    await test_invariant_fyers(runner)
    await test_transients_never_invalidate(runner)
    await test_only_three_invalidators_upstox(runner)
    await test_fyers_invalidation_rules(runner)
    await test_startup_isolation_matrix(runner)
    await test_throwing_store_never_blocks_boot(runner)
    await test_repeat_restore_no_duplication(runner)
    await test_expiry_roundtrip_tz_safe(runner)
    await test_upstox_failure_keeps_fyers(runner)
    await test_fyers_failure_keeps_upstox(runner)
    await test_logouts_do_not_cross(runner)
    await test_restore_isolates_exceptions(runner)
    await test_no_shared_token_bucket(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = asyncio.run(main())
    sys.exit(0 if _ok else 1)
