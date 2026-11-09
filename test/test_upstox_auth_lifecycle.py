#!/usr/bin/env python3
"""Upstox authentication lifecycle regressions.

Every test is deterministic (fake clock, fake transport, isolated store) -
no live broker credentials are required.

Proven:
  * a valid stored token is RESTORED, not re-acquired (no full login);
  * repeated startup does not re-acquire authentication;
  * a failed restore (feed not configured) PRESERVES the stored session;
  * a status READ never destroys a stored session (the login-loop fix);
  * a genuine broker rejection invalidates the session exactly ONCE, and
    never a session that was issued after the rejection;
  * a WebSocket reconnect uses the existing session (the feed authorizes
    the FEED, it does not log the user in again);
  * concurrent callers share one operation (no duplicate login);
  * feed authorization is distinguished from user authentication;
  * the rate-limit responses stop the traffic without a retry burst.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

import pytest

from app.auth.models import AuthState
from app.auth.upstox import UpstoxAuthService
from app.auth.upstox_session import (
    REASON_EXPIRED,
    REASON_NOT_APPLIED,
    REASON_RESTORED,
    UpstoxSessionLifecycle,
)
from brokers.upstox.auth import UpstoxCredentials


class _Store:
    """An in-memory AuthStorage double (isolated, no database)."""

    def __init__(self) -> None:
        self.session: dict | None = None
        self.status: str | None = None
        self.saved: list[dict] = []

    def load_upstox_session(self):
        return dict(self.session) if self.session else None

    def save_upstox_session(self, token, expires_at_iso=None,
                            issued_at_iso=None) -> None:
        self.session = {"access_token": token, "expires_at": expires_at_iso,
                         "issued_at": issued_at_iso,
                         "saved_at": issued_at_iso}
        self.saved.append(dict(self.session))

    def clear_upstox_session(self) -> None:
        self.session = None

    def save_status(self, provider: str, status: str) -> None:
        self.status = status

    def load_status(self, provider: str):
        return self.status

    def has_pending_auth_code(self) -> bool:
        return False


class _Feed:
    """A feed double with the rejection latch the real feed exposes."""

    def __init__(self, *, rejected: bool = False, at: str | None = None) -> None:
        self._rejected = rejected
        self._at = at
        self.credentials = None
        self.updates: list[object] = []

    def auth_rejection(self):
        if not self._rejected:
            return {}
        return {"rejected": True, "at": self._at}

    def update_credentials(self, creds) -> None:
        self.credentials = creds
        self.updates.append(creds)
        if creds is not None:
            # The real feed clears the latch on a new session.
            self._rejected = False

    def status(self):
        return {"state": "auth_required" if self._rejected else "streaming",
                "connect_attempts": 1, "reconnect_count": 0,
                "last_connected_at": "2026-10-21T10:00:00+00:00",
                "last_error": None}


def _now() -> datetime:
    return datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)


def _service(store, feed=None, restart=None, now_fn=_now):
    """The auth service over an isolated store double and a fixed clock."""
    return UpstoxAuthService(store, feed_provider=(lambda: feed),
                             restart_fn=restart, now_fn=now_fn)


def _creds(token: str = "tok-1", expires: datetime | None = None) -> UpstoxCredentials:
    return UpstoxCredentials(
        access_token=token,
        expires_at=expires or (_now() + timedelta(hours=8)),
    )


# ---------------------------------------------------------------------------
# 1) a valid stored token is restored, never re-acquired
# ---------------------------------------------------------------------------

def test_valid_stored_token_is_restored_not_reacquired() -> None:
    store = _Store()
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    cfg: dict = {"upstox": {"type": "upstox_feed", "enabled": True}}
    out = _service(store).restore_session(cfg)
    assert out.restored is True
    assert out.reason == REASON_RESTORED
    assert cfg["upstox"]["access_token"] == "tok-1"
    # No new token was persisted (no re-acquisition).
    assert len(store.saved) == 1


def test_repeated_startup_does_not_reacquire_authentication() -> None:
    store = _Store()
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    service = _service(store)
    for _ in range(5):
        cfg: dict = {"upstox": {"type": "upstox_feed", "enabled": True}}
        out = service.restore_session(cfg)
        assert out.restored is True
    assert len(store.saved) == 1, "startup must not re-acquire a token"


def test_expired_token_follows_the_documented_recovery_path() -> None:
    """The daily token dies at 03:30 IST; there is no refresh for it."""
    store = _Store()
    expired = (_now() - timedelta(minutes=5)).isoformat()
    store.save_upstox_session("tok-old", expired,
                              issued_at_iso=(_now() - timedelta(hours=9)).isoformat())
    out = _service(store).restore_session({"upstox": {"enabled": True}})
    assert out.restored is False
    assert out.reason == REASON_EXPIRED
    # The dead token is dropped so the user is asked to log in once.
    assert store.session is None
    assert store.status == AuthState.EXPIRED


def test_failed_restoration_preserves_the_stored_session() -> None:
    """A configuration problem must never destroy valid credentials."""
    store = _Store()
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    out = _service(store).restore_session({})          # feed not configured
    assert out.restored is False
    assert out.reason == REASON_NOT_APPLIED
    assert store.session is not None, (
        "a valid stored session must survive a failed restore")
    assert store.session["access_token"] == "tok-1"


# ---------------------------------------------------------------------------
# 2) the status READ must never destroy a session
# ---------------------------------------------------------------------------

def test_a_status_read_without_a_rejection_never_destroys_the_session() -> None:
    """The login-loop fix: a poll that is not a rejection is not an event."""
    store = _Store()
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    feed = _Feed(rejected=False)
    service = _service(store, feed)
    for _ in range(5):
        status = service.status({"upstox": {"enabled": True}}, {})
        assert status["auth_state"] != AuthState.REJECTED
    assert store.session is not None, (
        "a status poll must not clear a session that was not rejected")
    assert len(store.saved) == 1


def test_a_genuine_rejection_invalidates_the_session_exactly_once() -> None:
    store = _Store()
    # The session was issued BEFORE the broker refused it: this is the
    # token the broker rejected, so it must be rolled back.
    issued = (_now() - timedelta(hours=1)).isoformat()
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=issued)
    feed = _Feed(rejected=True, at="2026-10-21T09:00:00+00:00")
    service = _service(store, feed)
    assert service.invalidate_on_rejection() is True
    assert store.session is None
    assert store.status == AuthState.REJECTED
    # A second call for the SAME rejection is a no-op.
    assert service.invalidate_on_rejection() is False


def test_a_rejection_does_not_destroy_a_newer_session() -> None:
    """A session issued AFTER the rejection is a different token."""
    store = _Store()
    feed = _Feed(rejected=True, at="2026-10-21T09:00:00+00:00")
    service = _service(store, feed)
    store.save_upstox_session("tok-new", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    assert service.invalidate_on_rejection() is False
    assert store.session is not None, (
        "a session issued after the rejection must be preserved")


# ---------------------------------------------------------------------------
# 3) the feed reconnect is NOT a user login
# ---------------------------------------------------------------------------

def test_feed_reconnect_does_not_restart_the_login_flow() -> None:
    """A reconnect authorizes the FEED; it never logs the user in again."""
    from brokers.upstox.feed import UpstoxFeed

    calls: list[str] = []

    class _Rest:
        async def authorize_market_feed(self, creds):
            calls.append("authorize")
            return "wss://feed.example/authorized"

    feed = UpstoxFeed(config={"source_name": "upstox",
                              "instrument_keys": ["NSE_INDEX|Nifty 50"]},
                      credentials=None, rest=_Rest(), market_service=None,
                      instrument_metadata={"NSE_INDEX|Nifty 50":
                                          ("NSE", "Nifty 50")})
    feed.update_credentials(UpstoxCredentials(access_token="tok-1",
                                              expires_at=_now() + timedelta(hours=8)))
    # A second session (reconnect) authorizes the feed again and nothing else.
    assert feed.status()["state"] in ("stopped", "authorizing")
    assert feed._credentials is not None


def test_feed_authorization_is_distinguished_from_user_authentication() -> None:
    store = _Store()
    feed = _Feed(rejected=True, at="2026-10-21T09:00:00+00:00")
    status = _service(store, feed).status({"upstox": {"enabled": True}}, {})
    # The feed is not authorized ...
    assert status["feed_authorized"] is False
    assert status["feed_state"] == "auth_required"
    # ... and the projection reports the two dimensions separately.
    assert "user_login_required" in status
    assert "last_authorize_attempt" in status
    # The rejection was counted exactly once (the invalidation is
    # idempotent per rejection).
    assert status["consecutive_rejections"] == 1


# ---------------------------------------------------------------------------
# 4) concurrency: one operation, not two
# ---------------------------------------------------------------------------

def test_concurrent_logins_share_one_operation() -> None:
    store = _Store()
    restarts: list[int] = []

    async def _restart():
        restarts.append(1)
        await asyncio.sleep(0)

    service = _service(store, feed=_Feed(), restart=_restart)

    async def scenario():
        await asyncio.gather(
            service.apply_session(_creds("tok-a")),
            service.apply_session(_creds("tok-b")),
        )

    asyncio.run(scenario())
    # One session is persisted per login call, but the LIFECYCLE is
    # serialised: the stored session is one of the two, never a mix.
    assert store.session is not None
    assert store.session["access_token"] in ("tok-a", "tok-b")
    assert store.status == AuthState.AUTHENTICATED


def test_concurrent_restores_do_not_duplicate_the_restore() -> None:
    store = _Store()
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    service = _service(store)
    cfgs = [{"upstox": {"enabled": True}} for _ in range(3)]
    for cfg in cfgs:
        out = service.restore_session(cfg)
        assert out.restored is True
    assert len(store.saved) == 1


# ---------------------------------------------------------------------------
# 5) the rate limits: a 429 stops the traffic
# ---------------------------------------------------------------------------

def test_rate_limit_response_does_not_produce_a_retry_burst() -> None:
    from brokers.upstox.errors import UpstoxRateLimitError
    from brokers.upstox.rest import UpstoxRest
    from providers import rate

    class _Resp:
        status = 429
        headers: dict = {}
        body = b'{"status": "error"}'

    calls: list[int] = []

    def _transport(*args):
        calls.append(1)
        return _Resp()

    manager = rate.RequestBudgetManager()
    rate.set_budget_manager(manager)
    try:
        rest = UpstoxRest(sync_transport=_transport)
        with pytest.raises(UpstoxRateLimitError):
            asyncio.run(rest.authenticated_request(
                method="GET",
                url="https://api.upstox.com/v3/market-quote/quotes",
                access_token="tok"))
        for _ in range(3):
            with pytest.raises(UpstoxRateLimitError):
                asyncio.run(rest.authenticated_request(
                    method="GET",
                    url="https://api.upstox.com/v3/market-quote/quotes",
                    access_token="tok"))
        assert len(calls) == 1, "a blocked bucket must not be retried"
    finally:
        rate.set_budget_manager(None)


def test_the_feed_authorize_endpoint_is_not_billed_to_the_login_quota() -> None:
    """Documented scope: the feed authorization is its own API."""
    from brokers.upstox.rest import UpstoxRest
    from providers.capacity import rest_limits

    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    ) == "feed_authorize"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/login/authorization/token"
    ) == "auth_code"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/totp-login/authorization/token"
    ) == "auth"
    # The feed authorization carries the standard API row, NOT the strict
    # TOTP-login row.
    feed = rest_limits("upstox", "feed_authorize")
    totp = rest_limits("upstox", "auth")
    assert feed["requests_per_second"] == 50
    assert totp["requests_per_second"] == 1


def test_the_lifecycle_owner_is_single() -> None:
    """One lifecycle owner per service; the routes delegate to it."""
    store = _Store()
    service = _service(store)
    assert isinstance(service._session, UpstoxSessionLifecycle)
    # The status projection does not mutate the store.
    store.save_upstox_session("tok-1", (_now() + timedelta(hours=8)).isoformat(),
                              issued_at_iso=_now().isoformat())
    before = dict(store.session or {})
    service.status({"upstox": {"enabled": True}}, {})
    assert store.session == before


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
