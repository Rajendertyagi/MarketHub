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
    def invalidate_on_rejection(self) -> bool:
        """Clear durable session ONLY on genuine broker rejection.

        Returns True when the session was invalidated.
        """
        feed = self._feed()
        if feed is None:
            return False
        fstate = self._feed_status_dict(feed)
        if (fstate.get("state") == "auth_required"
                and fstate.get("last_exit_reason") == "broker_rejected_token"):
            try:
                self._storage.clear_upstox_session()
                self._storage.save_status("upstox", AuthState.REJECTED)
            except Exception:
                logger.exception("upstox: failed to clear rejected session")
            return True
        return False

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
        feed = self._feed()
        feed_cfg = (sources_cfg or {}).get("upstox")
        base: dict[str, Any] = {
            "configured": feed is not None,
            "feed_configured": isinstance(feed_cfg, dict),
            "feed_enabled": (bool(feed_cfg.get("enabled"))
                             if isinstance(feed_cfg, dict) else False),
        }
        session = self._storage.load_upstox_session()
        base["session_persisted"] = session is not None
        base["restart_recovery"] = session is not None
        base["session_restored"] = bool(
            (restore_state or {}).get("upstox_restored"))
        base["last_auth_status"] = self._storage.load_status("upstox")
        if feed is None:
            base.update({"source": "upstox", "auth_mode": "none",
                         "token_configured": False, "login_required": True,
                         "auth_state": (AuthState.MISSING if session is None
                                        else AuthState.AUTHENTICATED)})
            return base
        creds = getattr(feed, "_credentials", None)
        cstatus = creds.status() if creds is not None else {
            "auth_mode": "none", "token_present": False,
            "expiry_known": False, "expires_at": None, "expired": None}
        base.update({
            "source": getattr(feed, "name", "upstox"),
            "auth_mode": cstatus.get("auth_mode", "unknown"),
            "token_configured": bool(cstatus.get("token_present", False)),
            "expiry_known": cstatus.get("expiry_known", False),
            "expires_at": cstatus.get("expires_at"),
            "expired": cstatus.get("expired"),
            "state": self._feed_status_dict(feed).get("state", "unknown"),
        })
        ready = getattr(feed, "is_ready_to_start", None)
        base["ready_to_start"] = bool(ready()) if callable(ready) else None
        try:
            raw = self._storage.raw
            base["auth_code_pending"] = bool(
                raw and raw.load_upstox_auth_code())
        except Exception:
            base["auth_code_pending"] = False
        base["login_required"] = not (
            base["token_configured"] and base.get("expired") is not True
            and base.get("state") not in ("auth_required",))
        base["auth_state"] = (
            AuthState.MISSING if base["login_required"]
            else AuthState.AUTHENTICATED)
        if self.invalidate_on_rejection():
            base["session_persisted"] = False
            base["restart_recovery"] = False
            base["auth_state"] = AuthState.REJECTED
        return base
