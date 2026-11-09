"""Upstox session lifecycle: single owner, restore, rejection, status.

This module owns the **user authentication** lifecycle for Upstox. It is
deliberately separate from the market-data feed:

* a **user login** (``POST /v2/login/authorization/token``) issues the
  daily access token. Per the official documentation that token is valid
  **until 03:30 AM the following day**, whatever time it was generated
  at, and there is **no refresh flow** for it - when it expires the user
  has to authenticate again. The expiry rule is implemented once, in
  :func:`brokers.upstox.auth.upstox_token_expiry`.
* the **market-data feed authorization**
  (``GET /v3/feed/market-data-feed/authorize``) returns the one-time
  authorized WebSocket URI. It is called once per feed CONNECTION (a
  reconnect needs a fresh URI) and it is **not** a user login: it never
  issues or replaces the access token.

The defects this module fixes (evidence in the completion report):

1. A **read** path (the status projection) used to destroy the stored
   session on every poll, so a single rejection wiped a freshly stored
   token and the user was asked to log in again, and again. The status
   projection is now PURE; the invalidation is an explicit, idempotent
   lifecycle step (:meth:`UpstoxSessionLifecycle.note_rejection`).
2. A **failed restore** (e.g. the feed is not configured yet) used to
   delete a valid stored session. A configuration problem must never
   destroy credentials; the session is kept and reported as not applied.
3. Concurrent logins/restores could interleave. The lifecycle is now
   serialised and a restore in progress is shared.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.auth.models import AuthState, RestoreOutcome, is_expired, parse_expiry_iso

logger = logging.getLogger("event_server")

#: Restore reasons (diagnostic, never a secret).
REASON_NO_SESSION = "no_session"
REASON_EXPIRED = "expired"
REASON_RESTORED = "restored"
REASON_NOT_APPLIED = "not_applied"      # usable session, feed not configured
REASON_REJECTED = "rejected"
REASON_LOGGED_OUT = "logged_out"


class UpstoxSessionLifecycle:
    """The single owner of the Upstox user-session lifecycle.

    It owns: restore, login persistence, the explicit invalidation on a
    genuine broker rejection, the status projection and the logout. It
    never performs a feed authorization and never issues an access token
    on its own.
    """

    def __init__(self, *, storage: Any, feed_provider: Any = None,
                 restart_fn: Any = None, now_fn: Any = None) -> None:
        self._storage = storage
        self._feed_provider = feed_provider
        self._restart_fn = restart_fn
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        # Serialises the session mutations (login / restore / invalidation)
        # so concurrent callers cannot interleave.
        self._lock = asyncio.Lock()
        # The rejection this lifecycle has already acted on (idempotence:
        # a status poll must never invalidate the same rejection twice).
        self._handled_rejection_at: str | None = None
        # When a feed does not publish a rejection timestamp, the moment the
        # rejection was FIRST observed is the correlation point: a session
        # issued after it is a different (fresher) token and survives.
        self._legacy_rejection_seen_at: str | None = None
        self._consecutive_rejections = 0

    # -- helpers -------------------------------------------------------------

    def _feed(self) -> Any:
        provider = self._feed_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:
            return None

    def _now_iso(self) -> str:
        try:
            return self._now().isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    def load_session(self) -> dict[str, Any] | None:
        try:
            return self._storage.load_upstox_session()
        except Exception:
            return None

    # -- restore -------------------------------------------------------------

    def restore_session(self, sources_cfg: dict | None) -> RestoreOutcome:
        """Restore a still-valid stored session into the runtime config.

        Never raises. A restore that cannot be APPLIED (the feed is not
        configured) keeps the stored session: a configuration problem must
        never destroy valid credentials. Only a session that is known to
        be EXPIRED is cleared (Upstox offers no refresh for the daily
        token - the documented recovery is a fresh user login).
        """
        out = RestoreOutcome(provider="upstox")
        session = self.load_session()
        if not session or not (session.get("access_token") or "").strip():
            out.reason = REASON_NO_SESSION
            return out
        token = session["access_token"].strip()
        exp = parse_expiry_iso(session.get("expires_at"))
        if exp is not None and is_expired(exp, self._now()):
            # The token is genuinely dead (documented 03:30 AM IST expiry).
            self._storage.clear_upstox_session()
            self._storage.save_status("upstox", AuthState.EXPIRED)
            out.reason = REASON_EXPIRED
            return out
        cfg = (sources_cfg or {}).get("upstox")
        if not isinstance(cfg, dict):
            # No feed configuration yet: KEEP the stored session so the
            # operator can enable the feed later without logging in again.
            out.reason = REASON_NOT_APPLIED
            out.extra = {"session_preserved": True}
            return out
        updated = dict(cfg)
        updated["access_token"] = token
        if session.get("expires_at"):
            updated["access_token_expires_at"] = session["expires_at"]
        sources_cfg["upstox"] = updated
        self._storage.save_status("upstox", AuthState.RESTORED)
        self._handled_rejection_at = None
        self._legacy_rejection_seen_at = None
        self._consecutive_rejections = 0
        out.restored = True
        out.reason = REASON_RESTORED
        return out

    # -- login ---------------------------------------------------------------

    async def apply_session(self, creds: Any) -> dict[str, Any]:
        """Persist-then-install a freshly obtained session.

        Order (do NOT reorder):
          1. the caller already obtained and validated the token;
          2. ``expires_at`` is already present on ``creds``;
          3. persist token + expiry IMMEDIATELY (encrypted store = truth);
          4. mark authenticated;
          5. install into the runtime feed (None-safe);
          6. attempt the feed (re)start - a feed failure is FEED-only and
             never turns a successful login into a failure.
        """
        token = getattr(creds, "access_token", "")
        expires_at = getattr(creds, "expires_at", None)
        expires_iso = expires_at.isoformat() if expires_at is not None else None
        async with self._lock:
            self._storage.save_upstox_session(
                token, expires_iso,
                issued_at_iso=self._now_iso())
            self._storage.save_status("upstox", AuthState.AUTHENTICATED)
            # A new session is a fresh assessment: a previous rejection
            # must not taint it.
            self._handled_rejection_at = None
            self._legacy_rejection_seen_at = None
            self._consecutive_rejections = 0
            feed = self._feed()
            if feed is not None:
                try:
                    feed.update_credentials(creds)
                except Exception:
                    logger.exception("upstox login: failed to install credentials")
        if self._restart_fn is not None and feed is not None:
            try:
                await self._restart_fn()
            except Exception:
                logger.exception("upstox login: feed restart failed")
        return {"authenticated": True, "session_persisted": True}

    # -- rejection -----------------------------------------------------------

    def rejection_evidence(self) -> dict[str, Any]:
        """Explicit genuine-rejection evidence from the feed, or {}.

        The feed latches ``auth_rejection()`` ONLY when the provider itself
        refuses the runtime token (a confirmed 401 at the feed
        authorization). Generic ``auth_required`` states and transient feed
        problems NEVER count.

        A feed that does not expose the latch (an older adapter, a test
        double) is recognised by its historic signal instead: the feed
        reports ``auth_required`` WITH the broker's own exit reason
        ``broker_rejected_token``. That is the same evidence the real feed
        publishes, so the two paths agree on what "genuine rejection"
        means.
        """
        feed = self._feed()
        if feed is None:
            return {}
        probe = getattr(feed, "auth_rejection", None)
        if callable(probe):
            try:
                result = probe()
                return dict(result) if isinstance(result, dict) else {}
            except Exception:
                return {}
        # Legacy doubles without the latch: the historic broker-rejected
        # exit signal (same semantics, narrower source).
        try:
            status = feed.status()
        except Exception:
            return {}
        if not isinstance(status, dict):
            return {}
        if status.get("state") == "auth_required" and \
                status.get("last_exit_reason") == "broker_rejected_token":
            return {"rejected": True, "at": None, "legacy": True}
        return {"rejected": False, "at": None, "legacy": True}

    def note_rejection(self, restore_state: dict | None = None) -> bool:
        """Act ONCE on a genuine broker rejection.

        Clears the runtime credential and the durable session (the token
        was refused by the broker - it cannot be used again) and records
        the REJECTED state. It is idempotent per rejection, so a status
        poll can never wipe a session again and again. A session that was
        issued AFTER the rejection is left untouched.
        """
        evidence = self.rejection_evidence()
        if not evidence.get("rejected"):
            return False
        at = str(evidence.get("at") or "")
        if at:
            # The feed published WHEN the broker refused the token.
            rejection_at = parse_expiry_iso(at)
            key = at
        else:
            # A feed without the latch: the rejection is recognised, but
            # only the moment it was FIRST observed is known. That moment
            # is the correlation point: a session issued after it is a
            # DIFFERENT (fresher) token and must survive.
            if self._legacy_rejection_seen_at is None:
                self._legacy_rejection_seen_at = self._now_iso()
            rejection_at = parse_expiry_iso(self._legacy_rejection_seen_at)
            key = f"legacy:{self._legacy_rejection_seen_at}"
        if self._handled_rejection_at == key:
            return False        # already acted on THIS rejection
        session = self.load_session()
        issued_at = parse_expiry_iso((session or {}).get("issued_at"))
        if rejection_at is not None and issued_at is not None:
            try:
                if issued_at > rejection_at:
                    # The stored session is NEWER than the rejection: it
                    # is a different (fresher) token - do not destroy it.
                    self._handled_rejection_at = key
                    return False
            except Exception:
                pass
        feed = self._feed()
        if feed is not None:
            try:
                feed.update_credentials(None)
            except Exception:
                logger.exception("upstox: failed to clear rejected runtime")
        try:
            self._storage.clear_upstox_session()
            self._storage.save_status("upstox", AuthState.REJECTED)
        except Exception:
            logger.exception("upstox: failed to clear rejected session")
        self._handled_rejection_at = key
        self._consecutive_rejections += 1
        if restore_state is not None:
            restore_state["upstox_restored"] = False
        return True

    def invalidate_on_rejection(self, restore_state: dict | None = None) -> bool:
        """Backwards-compatible alias of :meth:`note_rejection`."""
        return self.note_rejection(restore_state)

    def rejection_count(self) -> int:
        """How many consecutive genuine rejections were acted upon."""
        return self._consecutive_rejections

    def last_rejection_at(self) -> str | None:
        """The timestamp of the rejection that was last acted upon."""
        return self._handled_rejection_at

    # -- logout --------------------------------------------------------------

    def logout(self, restore_state: dict | None = None) -> dict[str, Any]:
        self._storage.clear_upstox_session()
        self._storage.save_status("upstox", AuthState.FORGOTTEN)
        feed = self._feed()
        if feed is not None:
            try:
                feed.update_credentials(None)
            except Exception:
                logger.exception("failed to reset upstox runtime creds")
        self._handled_rejection_at = None
        self._legacy_rejection_seen_at = None
        self._consecutive_rejections = 0
        if restore_state is not None:
            restore_state["upstox_restored"] = False
        return {"ok": True}
