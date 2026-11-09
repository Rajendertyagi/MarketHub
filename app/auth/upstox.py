"""Upstox session lifecycle owner.

Owns: credential-result handling, canonical expiry, durable persistence
(through AuthStorage), restoration, invalidation on genuine auth rejection,
logout. Does NOT own market data / feed implementation: the feed is
accessed only through injected provider callbacks.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.auth.models import AuthState, RestoreOutcome, is_expired, parse_expiry_iso
from app.auth.storage import AuthStorage
from app.auth.upstox_session import UpstoxSessionLifecycle
from brokers.upstox.auth import (
    UpstoxCredentials,
    UpstoxOAuth,
    upstox_token_expiry,
)

logger = logging.getLogger("event_server")


class UpstoxAuthService:
    """Single owner of the Upstox runtime + durable session."""

    def __init__(
        self,
        storage: AuthStorage,
        *,
        feed_provider: Callable[[], Any] | None = None,
        restart_fn: Callable[[], Any] | None = None,
        now_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._storage = storage
        self._feed_provider = feed_provider
        self._restart_fn = restart_fn
        # The session lifecycle has ONE owner (app/auth/upstox_session.py):
        # restore, login persistence, the explicit (idempotent) invalidation
        # on a genuine broker rejection and the logout.
        self._session = UpstoxSessionLifecycle(
            storage=storage, feed_provider=feed_provider,
            restart_fn=restart_fn, now_fn=now_fn)

    # -- helpers ----------------------------------------------------------
    def _feed(self) -> Any:
        try:
            return self._feed_provider() if self._feed_provider else None
        except Exception:
            return None

    @staticmethod
    def _feed_status_dict(feed: Any) -> dict:
        try:
            st = feed.status()
            return dict(st) if isinstance(st, dict) else {}
        except Exception:
            return {}

    # -- login ------------------------------------------------------------
    async def apply_session(self, creds: Any) -> dict[str, Any]:
        """Persist-then-install lifecycle shared by ALL Upstox login paths.

        Fixed order (do NOT reorder):
          1. caller already obtained + validated the token
          2. expires_at already present on creds
          3. persist token + expiry IMMEDIATELY (encrypted store = truth)
          4. mark authenticated
          5. install into runtime feed (None-safe)
          6. attempt feed (re)start -- failure is FEED-only, never auth
        """
        # The order and the ownership live in the session lifecycle owner
        # (app/auth/upstox_session.py); this is the thin adapter the
        # routes call.
        return await self._session.apply_session(creds)

    # -- login input (thin-route entry points; routes parse, service owns) --
    def build_login_url(self, oauth_cfg: dict, state: str) -> str:
        """Canonical OAuth authorization URL (pure delegation)."""
        return UpstoxOAuth(
            api_key=oauth_cfg["api_key"],
            redirect_uri=oauth_cfg["redirect_uri"],
        ).authorization_url(state=state)

    async def exchange_code(self, rest: Any, *, code: str, client_id: str,
                            client_secret: str, redirect_uri: str) -> Any:
        """Exchange an authorization code via the injected transport."""
        return await rest.exchange_authorization_code(
            code=code, client_id=client_id, client_secret=client_secret,
            redirect_uri=redirect_uri)

    async def exchange_pin(self, rest: Any, *, code: str, pin: str,
                           client_id: str, client_secret: str,
                           redirect_uri: str) -> Any:
        """Exchange a staged auth code + operator PIN via the transport."""
        return await rest.exchange_with_pin(
            code=code, pin=pin, client_id=client_id,
            client_secret=client_secret, redirect_uri=redirect_uri)

    def stage_auth_code(self, code: str) -> None:
        """Persist a single-use PIN-mode auth code (encrypted store)."""
        self._storage.stage_auth_code(code)

    def consume_auth_code(self) -> str | None:
        """Load + clear the staged auth code (single use). Never raises."""
        return self._storage.consume_auth_code()

    async def submit_manual_token(self, token: str) -> dict[str, Any]:
        """Manual/pasted token login: canonical expiry + shared lifecycle."""
        if not isinstance(token, str) or not token.strip():
            raise ValueError("access_token is required")
        creds = UpstoxCredentials(
            access_token=token.strip(),
            expires_at=upstox_token_expiry(datetime.now(timezone.utc)),
        )
        await self.apply_session(creds)
        return {"configured": True, "authenticated": True,
                "session_persisted": True}

    # -- restore ----------------------------------------------------------
    def restore_session(self, sources_cfg: dict | None) -> RestoreOutcome:
        """Startup restore: inject a still-valid persisted token into config.

        Never raises: failures yield restored=False so server boot continues.
        A restore that cannot be APPLIED (the feed is not configured yet)
        KEEPS the stored session - a configuration problem must never
        destroy valid credentials.
        """
        return self._session.restore_session(sources_cfg)

    # -- genuine rejection --------------------------------------------------
    def rejection_evidence(self) -> dict[str, Any]:
        """Explicit genuine-rejection evidence from the feed, or {}.

        The feed latches ``auth_rejection()`` ONLY when the provider itself
        refuses the runtime token (confirmed 401 / equivalent auth refusal
        at authorize). Generic ``auth_required`` state or exit-reason
        strings are NEVER treated as rejection evidence here.
        """
        return self._session.rejection_evidence()

    def invalidate_on_rejection(
        self, restore_state: dict | None = None,
    ) -> bool:
        """Clear runtime + durable session ONLY on genuine broker rejection.

        Evidence comes from ``rejection_evidence()`` (explicit provider
        refusal), never from generic feed state. No placeholder is created:
        the runtime credential is cleared to None. Returns True when the
        session was invalidated.
        """
        return self._session.note_rejection(restore_state)

    # -- logout -------------------------------------------------------------
    def logout(self, restore_state: dict | None = None) -> dict[str, Any]:
        """Forget the stored session (the API credentials are untouched)."""
        return self._session.logout(restore_state)

    # -- status ---------------------------------------------------------------
    def status(self, sources_cfg: dict | None,
               restore_state: dict | None) -> dict[str, Any]:
        """Internally consistent auth projection + separate feed facts.

        State machine (auth_state is the single source of truth;
        ``authenticated``/``login_required`` derive from it):
          rejected      genuine provider refusal (latch) -> session
                        invalidated first, then projected from post-state
          authenticated usable runtime token (feed state NEVER decides this)
          expired       known-past expiry (runtime or durable)
          missing       no usable runtime token and no unexpired durable
                        session ("missing" is never used for rejections)
        """
        feed = self._feed()
        feed_cfg = (sources_cfg or {}).get("upstox")
        fstate = self._feed_status_dict(feed) if feed is not None else {}
        base: dict[str, Any] = {
            "configured": feed is not None,
            "feed_configured": isinstance(feed_cfg, dict),
            "feed_enabled": (bool(feed_cfg.get("enabled"))
                             if isinstance(feed_cfg, dict) else False),
        }
        base["auth_code_pending"] = self._storage.has_pending_auth_code()

        # 1) Genuine rejection wins. A broker that REFUSED the token must
        # not leave a dead session in place, so the session IS rolled back
        # here. The invalidation is TOKEN-SPECIFIC and IDEMPOTENT
        # (``note_rejection``): it only ever clears the token the broker
        # actually refused, a session issued AFTER the rejection survives,
        # and repeated polls of the SAME rejection do nothing. That is what
        # stops a status poll from wiping a freshly stored token and
        # driving the user into a login loop.
        if self.rejection_evidence().get("rejected"):
            self.invalidate_on_rejection(restore_state)
            base.update({
                "source": getattr(feed, "name", "upstox") if feed is not None
                else "upstox",
                "auth_mode": "none",
                "auth_state": AuthState.REJECTED,
                "authenticated": False,
                "login_required": True,
                "token_configured": False,
                "expiry_known": False,
                "expires_at": None,
                "expired": None,
                "session_persisted": False,
                "restart_recovery": False,
                "session_restored": False,
                "last_auth_status": self._storage.load_status("upstox"),
            })
            # The feed facts live BESIDE the auth decision (never inside
            # it): a rejected session still reports whether the feed is
            # authorized, so a feed problem is never presented as "log in
            # again".
            base["user_login_required"] = True
            base["feed_authorized"] = fstate.get("state") == "streaming"
            base["last_authorize_attempt"] = fstate.get("last_connected_at")
            base["connect_attempts"] = fstate.get("connect_attempts")
            base["reconnect_count"] = fstate.get("reconnect_count")
            base["consecutive_rejections"] = self._session.rejection_count()
            base["last_rejection_at"] = self._session.last_rejection_at()
            self._project_feed(base, feed, fstate)
            return base

        # 2) Gather durable + runtime facts (no mutation below this point).
        session = self._storage.load_upstox_session()
        creds = getattr(feed, "_credentials", None) if feed is not None else None
        cstatus = creds.status() if creds is not None else {
            "auth_mode": "none", "token_present": False,
            "expiry_known": False, "expires_at": None, "expired": None}
        runtime_usable = bool(
            cstatus.get("token_present", False)
            and cstatus.get("expired") is not True)
        runtime_expired = bool(
            cstatus.get("token_present", False)
            and cstatus.get("expired") is True)
        durable_exp = parse_expiry_iso((session or {}).get("expires_at"))
        durable_expired = bool(
            session and durable_exp is not None and is_expired(durable_exp))

        base.update({
            "source": getattr(feed, "name", "upstox") if feed is not None
            else "upstox",
            "auth_mode": cstatus.get("auth_mode", "unknown")
            if feed is not None else "none",
            "token_configured": runtime_usable,
            "expiry_known": cstatus.get("expiry_known", False),
            "expires_at": cstatus.get("expires_at")
            if cstatus.get("expires_at") is not None
            else (session or {}).get("expires_at"),
            "expired": cstatus.get("expired")
            if cstatus.get("expired") is not None
            else (durable_expired or None),
            "session_persisted": session is not None,
            "restart_recovery": session is not None,
            "session_restored": bool(
                (restore_state or {}).get("upstox_restored")),
            "last_auth_status": self._storage.load_status("upstox"),
        })

        # 3) Single state decision (feed state plays no role here).
        # A rejected/expired credential stays visibly rejected/expired (via
        # the sticky last_auth_status) until a fresh login or logout
        # overwrites it — it is never relabeled "missing".
        if runtime_usable:
            auth_state = AuthState.AUTHENTICATED
        elif runtime_expired or (not runtime_usable and durable_expired):
            auth_state = AuthState.EXPIRED
        elif base["last_auth_status"] == AuthState.REJECTED:
            auth_state = AuthState.REJECTED
        elif base["last_auth_status"] == AuthState.EXPIRED:
            auth_state = AuthState.EXPIRED
        else:
            # No usable runtime token and no unexpired durable session
            # (a valid durable record without runtime only means restart
            # recovery is available — still "missing" at runtime).
            auth_state = AuthState.MISSING
        base["auth_state"] = auth_state
        base["authenticated"] = (auth_state == AuthState.AUTHENTICATED)
        base["login_required"] = (auth_state != AuthState.AUTHENTICATED)
        # A USER login is required only when there is no usable session at
        # all. The market-data feed authorization is a SEPARATE step (a
        # one-time authorized WebSocket URI per connection) and is reported
        # on its own, so a feed problem is never presented as "log in
        # again".
        base["user_login_required"] = not bool(
            runtime_usable or (session is not None and not durable_expired))
        base["feed_authorized"] = fstate.get("state") == "streaming"
        base["feed_state"] = fstate.get("state", "unknown")
        base["last_authorize_attempt"] = fstate.get("last_connected_at")
        base["connect_attempts"] = fstate.get("connect_attempts")
        base["reconnect_count"] = fstate.get("reconnect_count")
        base["consecutive_rejections"] = self._session.rejection_count()
        base["last_rejection_at"] = self._session.last_rejection_at()
        self._project_feed(base, feed, fstate)
        return base

    @staticmethod
    def _project_feed(base: dict[str, Any], feed: Any,
                      fstate: dict[str, Any]) -> None:
        """Feed/connectivity facts live beside auth, never inside it."""
        ready = getattr(feed, "is_ready_to_start", None) \
            if feed is not None else None
        base["ready_to_start"] = bool(ready()) if callable(ready) else None
        reason = getattr(feed, "readiness_reason", None) \
            if feed is not None else None
        try:
            base["not_ready_reason"] = reason() if callable(reason) else None
        except Exception:
            base["not_ready_reason"] = None
        base["state"] = fstate.get("state", "unknown")
        base["feed_state"] = fstate.get("state", "unknown")
        base["last_error"] = fstate.get("last_error")
