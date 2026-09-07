"""Upstox session lifecycle owner.

Owns: credential-result handling, canonical expiry, durable persistence
(through AuthStorage), restoration, invalidation on genuine auth rejection,
logout. Does NOT own market data / feed implementation: the feed is
accessed only through injected provider callbacks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.auth.models import AuthState, RestoreOutcome, is_expired, parse_expiry_iso
from app.auth.storage import AuthStorage

logger = logging.getLogger("event_server")


class UpstoxAuthService:
    """Single owner of the Upstox runtime + durable session."""

    def __init__(
        self,
        storage: AuthStorage,
        *,
        feed_provider: Callable[[], Any] | None = None,
        restart_fn: Callable[[], Any] | None = None,
    ) -> None:
        self._storage = storage
        self._feed_provider = feed_provider
        self._restart_fn = restart_fn

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
        token = getattr(creds, "access_token", "")
        expires_at = getattr(creds, "expires_at", None)
        expires_iso = expires_at.isoformat() if expires_at is not None else None
        self._storage.save_upstox_session(
            token, expires_iso,
            issued_at_iso=datetime.now(timezone.utc).isoformat())
        self._storage.save_status("upstox", AuthState.AUTHENTICATED)
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

    # -- restore ----------------------------------------------------------
    def restore_session(self, sources_cfg: dict | None) -> RestoreOutcome:
        """Startup restore: inject a still-valid persisted token into config.

        Never raises: failures yield restored=False so server boot continues.
        """
        out = RestoreOutcome(provider="upstox")
        session = self._storage.load_upstox_session()
        if not session or not (session.get("access_token") or "").strip():
            out.reason = "no_session"
            return out
        token = session["access_token"].strip()
        exp = parse_expiry_iso(session.get("expires_at"))
        expired = is_expired(exp)
        if exp is not None and expired:
            self._storage.clear_upstox_session()
            self._storage.save_status("upstox", AuthState.EXPIRED)
            out.reason = "expired"
            return out
        cfg = (sources_cfg or {}).get("upstox")
        if not isinstance(cfg, dict):
            self._storage.clear_upstox_session()
            self._storage.save_status("upstox", AuthState.EXPIRED)
            out.reason = "no_source_config"
            return out
        updated = dict(cfg)
        updated["access_token"] = token
        if session.get("expires_at"):
            updated["access_token_expires_at"] = session["expires_at"]
        sources_cfg["upstox"] = updated
        self._storage.save_status("upstox", AuthState.RESTORED)
        out.restored = True
        out.reason = "restored"
        return out

    # -- genuine rejection --------------------------------------------------
    def rejection_evidence(self) -> dict[str, Any]:
        """Explicit genuine-rejection evidence from the feed, or {}.

        The feed latches ``auth_rejection()`` ONLY when the provider itself
        refuses the runtime token (confirmed 401 / equivalent auth refusal
        at authorize). Generic ``auth_required`` state or exit-reason
        strings are NEVER treated as rejection evidence here.
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
        # Legacy doubles without the latch: fall back to the historic
        # broker-rejected exit signal (same semantics, narrower source).
        fstate = self._feed_status_dict(feed)
        if (fstate.get("state") == "auth_required"
                and fstate.get("last_exit_reason") == "broker_rejected_token"):
            return {"rejected": True, "at": None, "legacy": True}
        return {"rejected": False, "at": None, "legacy": True}

    def invalidate_on_rejection(
        self, restore_state: dict | None = None,
    ) -> bool:
        """Clear runtime + durable session ONLY on genuine broker rejection.

        Evidence comes from ``rejection_evidence()`` (explicit provider
        refusal), never from generic feed state. No placeholder is created:
        the runtime credential is cleared to None. Returns True when the
        session was invalidated.
        """
        evidence = self.rejection_evidence()
        if not evidence.get("rejected"):
            return False
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
        if restore_state is not None:
            restore_state["upstox_restored"] = False
        return True

    # -- logout -------------------------------------------------------------
    def logout(self, restore_state: dict | None = None) -> dict[str, Any]:
        self._storage.clear_upstox_session()
        self._storage.save_status("upstox", AuthState.FORGOTTEN)
        feed = self._feed()
        if feed is not None:
            try:
                feed.update_credentials(None)
            except Exception:
                logger.exception("failed to reset upstox runtime creds")
        if restore_state is not None:
            restore_state["upstox_restored"] = False
        return {"ok": True}

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
        try:
            raw = self._storage.raw
            base["auth_code_pending"] = bool(
                raw and raw.load_upstox_auth_code())
        except Exception:
            base["auth_code_pending"] = False

        # 1) Genuine rejection wins: invalidate first so every projected
        # field reflects the resulting storage/runtime state.
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
        durable_usable = bool(
            session and (session.get("access_token") or "").strip()
            and not (durable_exp is not None
                     and is_expired(durable_exp)))
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
        if runtime_usable:
            auth_state = AuthState.AUTHENTICATED
        elif runtime_expired or (not runtime_usable and durable_expired):
            auth_state = AuthState.EXPIRED
        elif durable_usable:
            # Durable session exists but no runtime token (e.g. feed not yet
            # re-registered): restart recovery is available, but the operator
            # must log in (or restart) — still "missing" at runtime.
            auth_state = AuthState.MISSING
        else:
            auth_state = AuthState.MISSING
        base["auth_state"] = auth_state
        base["authenticated"] = (auth_state == AuthState.AUTHENTICATED)
        base["login_required"] = (auth_state != AuthState.AUTHENTICATED)
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
