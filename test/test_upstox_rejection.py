#!/usr/bin/env python3
"""Upstox genuine-rejection propagation tests (isolated temp stores only).

Proves the Part-1 contract:

  1. genuine 401 at authorize latches rejection (explicit evidence)
  2. rejected token is invalidated (runtime None + durable cleared)
  3. rejected status != missing (auth_state + last_auth_status differ)
  4. transient failures (rate-limit / 5xx / network / ws-drop) preserve session
  5. feed failure never causes login_required
  6. expired -> expired/login-required; missing -> missing/login-required
  7. valid restored token -> authenticated/no-login-required
  8. latch resets on new credentials (fresh assessment)

Uses the REAL UpstoxFeed with stub REST transports. Synthetic tokens only,
temporary EventStore + data_dir. Never touches data/events.db.

NO LIVE BROKER.
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


def _logged_in_feed(store, token="REAL-SESS-TOK", hours: float = 6):
    """Persist a session + install it into a real feed (stub rest)."""
    from brokers.upstox.feed import UpstoxFeed
    from brokers.upstox.rest import UpstoxRest
    creds = _creds(token, hours)
    store.save_upstox_session_token(
        token=token,
        expires_at_iso=creds.expires_at.isoformat(),
        issued_at_iso=datetime.now(timezone.utc).isoformat())
    store.save_last_auth_status("upstox", "authenticated")
    feed = UpstoxFeed(
        config={"source_name": "upstox",
                "instrument_keys": ["NSE:NIFTY50-INDEX"]},
        credentials=creds,
        rest=UpstoxRest(),
        instrument_metadata={"NSE:NIFTY50-INDEX": ("NSE", "NIFTY 50")},
    )
    return feed, creds


def _svc(store, feed):
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService
    holder = {"feed": feed}
    return UpstoxAuthService(AuthStorage(store),
                             feed_provider=lambda: holder.get("feed"))


class _Rest401:
    async def authorize_market_feed(self, creds):
        from brokers.upstox.errors import UpstoxAuthError
        raise UpstoxAuthError("upstox market-data feed authorization failed: "
                              "HTTP 401 [UDAPI100050]")


class _RestRateLimited:
    async def authorize_market_feed(self, creds):
        from brokers.upstox.errors import UpstoxRateLimitError
        raise UpstoxRateLimitError("rate limited", retry_after_seconds=5)


class _RestFlaky:
    async def authorize_market_feed(self, creds):
        from brokers.upstox.errors import UpstoxRestError
        raise UpstoxRestError("upstox authorize failed: HTTP 503",
                              status_code=503, retryable=True)


class _RestDown:
    async def authorize_market_feed(self, creds):
        from brokers.upstox.errors import UpstoxRestError
        raise UpstoxRestError("upstox authorize failed: network error",
                              status_code=None, retryable=True)


async def _one_cycle(feed) -> None:
    import asyncio as _a
    await feed._run_session(_a.Event())


# -- 1+2: genuine rejection latches + invalidates ------------------------------

async def test_genuine_401_latches_and_invalidates(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store)
        feed._rest = _Rest401()
        runner.assert_false("latch-off-before",
                            feed.auth_rejection()["rejected"])
        await _one_cycle(feed)
        runner.assert_eq("feed-auth-required", feed.status()["state"],
                         "auth_required")
        ev = feed.auth_rejection()
        runner.assert_true("latch-on", ev["rejected"])
        runner.assert_true("latch-at", bool(ev["at"]))
        svc = _svc(store, feed)
        st = svc.status({"upstox": {"enabled": True}},
                        {"upstox_restored": True})
        # Post-invalidation projection: the exact REJECTED contract.
        runner.assert_eq("rej-state", st["auth_state"], "rejected")
        runner.assert_false("rej-authenticated", st["authenticated"])
        runner.assert_true("rej-login", st["login_required"])
        runner.assert_false("rej-token", st["token_configured"])
        runner.assert_false("rej-persisted", st["session_persisted"])
        runner.assert_false("rej-restored", st["session_restored"])
        runner.assert_eq("rej-status", st["last_auth_status"], "rejected")
        runner.assert_true("rej-runtime-cleared", feed._credentials is None)
        runner.assert_true("rej-durable-cleared",
                           store.load_upstox_session_token() is None)
        # Sticky: later polls (latch consumed by the invalidation) must
        # still report rejected — never relabeled "missing".
        st2 = svc.status({"upstox": {"enabled": True}},
                         {"upstox_restored": False})
        runner.assert_eq("rej-sticky", st2["auth_state"], "rejected")
        runner.assert_true("rej-sticky-login", st2["login_required"])
        runner.assert_false("rej-sticky-persisted",
                            st2["session_persisted"])


# -- sticky rejected survives polls but clears on fresh login ---------------------

async def test_rejected_stays_rejected_until_login(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store)
        feed._rest = _Rest401()
        await _one_cycle(feed)
        svc = _svc(store, feed)
        st = svc.status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("sticky-rejected", st["auth_state"], "rejected")
        # A fresh successful login overwrites the marker -> authenticated.
        await svc.apply_session(_creds("REAL-T-NEW"))
        st = svc.status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("login-clears", st["auth_state"], "authenticated")
        runner.assert_false("login-clears-login", st["login_required"])


# -- 3: rejected != missing -----------------------------------------------------

async def test_rejected_distinct_from_missing(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store)
        feed._rest = _Rest401()
        await _one_cycle(feed)
        svc = _svc(store, feed)
        rej = svc.status({"upstox": {"enabled": True}}, {})
        # Fresh store, no session, no runtime: the MISSING contract.
        store2 = _tmp_store(tmp + "2") if False else None
        import tempfile as _tf
        with _tf.TemporaryDirectory() as tmp2:
            store_m = _tmp_store(tmp2)
            from brokers.upstox.feed import UpstoxFeed
            from brokers.upstox.rest import UpstoxRest
            feed_m = UpstoxFeed(
                config={"source_name": "upstox",
                        "instrument_keys": ["NSE:NIFTY50-INDEX"]},
                credentials=None, rest=UpstoxRest(),
                instrument_metadata={"NSE:NIFTY50-INDEX": ("NSE", "NIFTY 50")})
            miss = _svc(store_m, feed_m).status(
                {"upstox": {"enabled": True}}, {})
        runner.assert_eq("rej-is-rejected", rej["auth_state"], "rejected")
        runner.assert_eq("miss-is-missing", miss["auth_state"], "missing")
        runner.assert_true("states-differ",
                           rej["auth_state"] != miss["auth_state"])
        runner.assert_true("status-differs",
                           rej["last_auth_status"] != miss["last_auth_status"])
        runner.assert_eq("rej-marker", rej["last_auth_status"], "rejected")


# -- 4+5: transient failures preserve the session ---------------------------------

async def _transient_case(runner: R, name: str, rest) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store, token=f"REAL-T-{name}")
        feed._rest = rest
        await _one_cycle(feed)
        runner.assert_false(f"{name}-no-latch",
                            feed.auth_rejection()["rejected"])
        svc = _svc(store, feed)
        st = svc.status({"upstox": {"enabled": True}},
                        {"upstox_restored": True})
        runner.assert_eq(f"{name}-state", st["auth_state"], "authenticated")
        runner.assert_true(f"{name}-authed", st["authenticated"])
        runner.assert_false(f"{name}-no-login", st["login_required"])
        runner.assert_true(f"{name}-kept",
                           store.load_upstox_session_token() is not None)
        runner.assert_true(f"{name}-runtime",
                           feed._credentials is not None)


async def test_transient_failures_preserve_session(runner: R) -> None:
    await _transient_case(runner, "ratelimit", _RestRateLimited())
    await _transient_case(runner, "flaky500", _RestFlaky())
    await _transient_case(runner, "netdown", _RestDown())


async def test_ws_connect_failure_preserves_session(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store, token="REAL-T-WS")

        async def _good_auth(creds):
            return "wss://fake.local/stream"

        async def _bad_ws(*a, **k):
            raise ConnectionError("ws down")

        feed._rest.authorize_market_feed = _good_auth
        feed._ws_connect = _bad_ws
        await _one_cycle(feed)
        runner.assert_false("ws-no-latch",
                            feed.auth_rejection()["rejected"])
        st = _svc(store, feed).status({"upstox": {"enabled": True}},
                                      {"upstox_restored": True})
        runner.assert_eq("ws-state", st["auth_state"], "authenticated")
        runner.assert_false("ws-no-login", st["login_required"])
        runner.assert_true("ws-kept",
                           store.load_upstox_session_token() is not None)


# -- 6+7: contract consistency table ----------------------------------------------

async def test_status_contract_consistency(runner: R) -> None:
    import tempfile
    from brokers.upstox.auth import UpstoxCredentials
    with tempfile.TemporaryDirectory() as tmp:
        # VALID restored token.
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store, token="REAL-T-OK")
        st = _svc(store, feed).status({"upstox": {"enabled": True}},
                                      {"upstox_restored": True})
        runner.assert_eq("ok-state", st["auth_state"], "authenticated")
        runner.assert_true("ok-authed", st["authenticated"])
        runner.assert_false("ok-login", st["login_required"])
        runner.assert_true("ok-token", st["token_configured"])
        runner.assert_true("ok-persisted", st["session_persisted"])
        runner.assert_true("ok-restored", st["session_restored"])
        # EXPIRED runtime token.
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        feed.update_credentials(
            UpstoxCredentials(access_token="REAL-T-OLD", expires_at=past))
        st = _svc(store, feed).status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("exp-state", st["auth_state"], "expired")
        runner.assert_false("exp-authed", st["authenticated"])
        runner.assert_true("exp-login", st["login_required"])
        # MISSING (logout clears everything).
        _svc(store, feed).logout({})
        st = _svc(store, feed).status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("miss-state", st["auth_state"], "missing")
        runner.assert_false("miss-authed", st["authenticated"])
        runner.assert_true("miss-login", st["login_required"])
        runner.assert_false("miss-token", st["token_configured"])
        runner.assert_false("miss-persisted", st["session_persisted"])


# -- 8: latch resets on new credentials --------------------------------------------

async def test_latch_resets_on_new_credentials(runner: R) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = _tmp_store(tmp)
        feed, _ = _logged_in_feed(store)
        feed._rest = _Rest401()
        await _one_cycle(feed)
        runner.assert_true("latched", feed.auth_rejection()["rejected"])
        feed.update_credentials(_creds("REAL-T-FRESH"))
        runner.assert_false("latch-reset",
                            feed.auth_rejection()["rejected"])
        st = _svc(store, feed).status({"upstox": {"enabled": True}}, {})
        runner.assert_eq("fresh-state", st["auth_state"], "authenticated")
        runner.assert_false("fresh-login", st["login_required"])


# -- main --------------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_genuine_401_latches_and_invalidates(runner)
    await test_rejected_distinct_from_missing(runner)
    await test_rejected_stays_rejected_until_login(runner)
    await test_transient_failures_preserve_session(runner)
    await test_ws_connect_failure_preserves_session(runner)
    await test_status_contract_consistency(runner)
    await test_latch_resets_on_new_credentials(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = asyncio.run(main())
    sys.exit(0 if _ok else 1)
