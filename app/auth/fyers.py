"""Fyers session lifecycle owner (preserves working behavior).

Migrated from app/server.py::_try_restore_fyers_token +
api/product_routes.py::build_fyers_auth_routes login paths, without
changing provider mechanics: access-token reuse first, then refresh/PIN
flow, runtime token owned by FyersRuntimeAuth, durable material encrypted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.auth.models import (
    AuthState,
    RestoreOutcome,
    is_expired,
    parse_expiry_iso,
)
from app.auth.storage import AuthStorage

logger = logging.getLogger("event_server")


class FyersAuthService:
    """Single owner of the Fyers runtime + durable session."""

    def __init__(
        self,
        storage: AuthStorage,
        runtime_auth: Any = None,
        *,
        app_id_provider: Callable[[], dict | None] | None = None,
        refresh_fn: Callable[..., Any] | None = None,
        redirect_uri: str = "",
        feed_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._storage = storage
        if runtime_auth is None:
            # Test/isolation seam only: production always injects the shared
            # owner (composition root) so login unblocks the wired feed.
            from app.fyers_runtime_auth import FyersRuntimeAuth
            runtime_auth = FyersRuntimeAuth()
        self._runtime = runtime_auth
        self._app_id_provider = app_id_provider
        self._refresh_fn = refresh_fn
        self._redirect_uri = redirect_uri
        # Read-only feed-evidence hook (same discipline as Upstox): a
        # callable returning either a feed object with .status() or a plain
        # status dict. Used ONLY to observe genuine 401/403 token
        # rejection; transient feed problems must never read as auth failure.
        self._feed_provider = feed_provider

    @property
    def runtime(self) -> Any:
        """The single runtime-token owner (feed getter closes over it)."""
        return self._runtime

    # -- restore (migrated from server._try_restore_fyers_token) --------------
    async def restore_session(self) -> RestoreOutcome:
        out = RestoreOutcome(provider="fyers")
        store = self._storage.raw
        try:
            app_creds = store.load_fyers_credentials()
        except Exception:
            app_creds = None
        if self._app_id_provider is not None:
            try:
                override = self._app_id_provider()
                if override:
                    app_creds = override
            except Exception:
                pass
        if not app_creds or not app_creds.get("app_id") \
                or not app_creds.get("app_secret"):
            out.reason = "credentials_missing"
            self._storage.save_status("fyers", "credentials_missing")
            return out
        app_id = app_creds["app_id"]
        secret_id = app_creds["app_secret"]

        # 1) Reuse a still-valid stored access token first.
        try:
            stored = store.load_fyers_access_token()
        except Exception:
            stored = None
        if stored and stored.get("access_token"):
            exp = parse_expiry_iso(stored.get("expires_at"))
            still_valid = True
            if stored.get("expires_at"):
                # An undecipherable/unparseable expiry is NOT trusted.
                still_valid = exp is not None and exp > datetime.now(timezone.utc)
            if still_valid:
                self._runtime.set_access_token(stored["access_token"])
                out.restored = True
                out.reason = "restored_access_token"
                self._storage.save_status("fyers", "restored_access_token")
                return out

        # 2) Fall back to the supported refresh-token flow.
        try:
            refresh_token = store.load_fyers_refresh_token()
        except Exception:
            refresh_token = None
        if not refresh_token:
            out.reason = "refresh_token_missing"
            self._storage.save_status("fyers", "refresh_token_missing")
            return out
        try:
            try:
                pin = store.load_fyers_pin()
            except Exception:
                pin = None
            if self._refresh_fn is not None:
                bundle = await self._refresh_fn(
                    app_id=app_id, secret_id=secret_id,
                    refresh_token=refresh_token, pin=pin,
                    redirect_uri=self._redirect_uri)
            else:
                from brokers.fyers.auth import FyersAuth
                bundle = await FyersAuth(
                    app_id=app_id, secret_id=secret_id,
                    redirect_uri=self._redirect_uri or
                    "http://localhost:7070/auth/fyers/callback",
                ).refresh_access_token(refresh_token, pin=pin)
            self._runtime.set_access_token(bundle["access_token"])
            try:
                self._storage.save_fyers_access(
                    bundle["access_token"], bundle.get("expires_at"))
            except Exception:
                logger.warning("failed to persist fyers access token")
            out.restored = True
            out.reason = "refreshed"
            self._storage.save_status("fyers", "refreshed")
        except Exception as exc:
            name = type(exc).__name__
            reason = ("pin_missing" if "pin" in name.lower()
                      else "refresh_failed")
            out.reason = reason
            self._storage.save_status("fyers", reason)
        return out

    # -- login ------------------------------------------------------------------
    def save_credentials(self, app_id: str, secret_id: str,
                         pin: str | None = None) -> dict[str, Any]:
        """Save App ID/Secret (+ optional PIN) to the encrypted store.

        Request validation (length/shape) stays in the route; the service
        owns the write order. Raises ValueError on bad input.
        """
        store = self._storage.raw
        if not isinstance(app_id, str) or not app_id.strip():
            raise ValueError("app_id is required")
        if not isinstance(secret_id, str) or not secret_id.strip():
            raise ValueError("secret_id is required")
        store.save_fyers_credentials(app_id.strip(), secret_id.strip())
        if isinstance(pin, str) and pin.strip():
            store.save_fyers_pin(pin.strip())
        return {"configured": True}

    def build_login_url(self, state: str) -> str:
        """Canonical Fyers login URL from stored credentials.

        Raises FyersAuthError when credentials are not configured.
        """
        from brokers.fyers.auth import FyersAuth, FyersAuthError
        store = self._storage.raw
        try:
            creds = store.load_fyers_credentials()
        except Exception:
            creds = None
        if not creds:
            raise FyersAuthError("fyers credentials not configured")
        return FyersAuth(app_id=creds["app_id"],
                         secret_id=creds["app_secret"],
                         redirect_uri=self._redirect_uri).login_url(state=state)

    async def exchange_auth_code(self, code: str) -> dict:
        """Validate an auth callback code via the official exchange."""
        from brokers.fyers.auth import FyersAuth
        store = self._storage.raw
        try:
            creds = store.load_fyers_credentials()
        except Exception:
            creds = None
        if not creds:
            raise ValueError("fyers credentials not configured")
        auth = FyersAuth(app_id=creds["app_id"],
                         secret_id=creds["app_secret"],
                         redirect_uri=self._redirect_uri)
        return await auth.validate_auth_code(code.strip())

    async def persist_login(self, bundle: dict,
                            restart_fn: Any = None) -> dict[str, Any]:
        """Persist refresh (authoritative) + access cache, install runtime."""
        store = self._storage.raw
        try:
            import asyncio as _asyncio
            await _asyncio.to_thread(
                store.save_fyers_refresh_token, bundle["refresh_token"])
        except Exception:
            return {"ok": False, "error": "persist_failed"}
        try:
            import asyncio as _asyncio
            await _asyncio.to_thread(
                store.save_fyers_access_token,
                bundle["access_token"], bundle.get("expires_at"))
            await _asyncio.to_thread(
                store.save_last_auth_status, "fyers",
                AuthState.AUTHENTICATED)
        except Exception:
            logger.warning("failed to persist fyers access token")
        self._runtime.set_access_token(bundle["access_token"])
        if restart_fn is not None:
            try:
                await restart_fn()
            except Exception:
                logger.warning("fyers feed restart after login failed")
        return {"ok": True}

    def logout(self, restore_state: dict | None = None) -> dict[str, Any]:
        store = self._storage.raw
        try:
            store.clear_fyers_session()
            store.save_last_auth_status("fyers", AuthState.FORGOTTEN)
        except Exception:
            pass
        try:
            self._runtime.clear_access_token()
        except Exception:
            pass
        if restore_state is not None:
            restore_state["fyers_restored"] = False
        return {"ok": True}

    # -- genuine-rejection evidence (read-only; never mutates) --------------
    def _feed_status_dict(self) -> dict:
        """Best-effort feed status dict, or {} when unavailable."""
        try:
            feed = self._feed_provider() if self._feed_provider else None
        except Exception:
            return {}
        if feed is None:
            return {}
        if isinstance(feed, dict):
            return feed
        probe = getattr(feed, "status", None)
        if callable(probe):
            try:
                st = probe()
                return dict(st) if isinstance(st, dict) else {}
            except Exception:
                return {}
        return {}

    def rejection_evidence(self) -> dict[str, Any]:
        """Explicit genuine-rejection evidence from the feed, or {}.

        ONLY a provider token refusal counts: the Fyers feed reports
        ``auth_required`` with a token-specific marker
        (``token_unauthorized`` / ``auth_rejected`` /
        ``token_expired_or_unauthorized``) after a 401/403 from Fyers.
        Timeouts, DNS/network errors, reconnect loops, ``missing_token``
        (never logged in), and every other feed state are NEVER treated
        as rejection evidence here.
        """
        fstate = self._feed_status_dict()
        if not fstate or fstate.get("state") != "auth_required":
            return {"rejected": False}
        markers = ("token_unauthorized", "auth_rejected",
                   "token_expired_or_unauthorized", "broker_rejected_token")
        hay = " ".join(str(fstate.get(k) or "") for k in (
            "last_error", "last_exit_reason", "not_ready_reason",
            "stop_reason")).lower()
        if any(m in hay for m in markers):
            return {"rejected": True,
                    "reason": fstate.get("last_exit_reason") or
                    fstate.get("last_error")}
        return {"rejected": False}

    def status_snapshot(
        self, restore_state: dict | None = None,
    ) -> dict[str, Any]:
        """Authoritative Fyers auth projection.

        Canonical states (shared with Upstox semantics; ``authenticated`` /
        ``login_required`` derive from ``auth_state``):

          authenticated  usable runtime token: present AND (known-future
                         expiry OR no contrary evidence). A non-empty token
                         string alone is never sufficient.
          expired        known-past access expiry, or genuine Fyers 401/403
                         token rejection observed from the feed.
          missing        no runtime token (durable recovery material, if any,
                         is reported separately via restart_recovery — a
                         durable record without runtime is still missing).
          unknown        runtime token present but usability cannot be
                         honestly determined (expiry absent/unparseable and
                         no rejection evidence). NEVER authenticated; login
                         stays available.

        Read-only: this method never clears runtime or durable session
        material (a 401 keeps the refresh token for the next restore; an
        explicit logout/forget is the only destructive path).
        """
        store = self._storage.raw
        try:
            creds = store.load_fyers_credentials()
        except Exception:
            creds = None
        try:
            has_refresh = bool(store.load_fyers_refresh_token())
        except Exception:
            has_refresh = False
        try:
            pin_stored = bool(store.load_fyers_pin())
        except Exception:
            pin_stored = False
        try:
            stored_access = store.load_fyers_access_token()
        except Exception:
            stored_access = None
        try:
            store_state = store.store_status()
        except Exception:
            store_state = {}
        runtime_active = bool(
            self._runtime.has_access_token()
            if hasattr(self._runtime, "has_access_token") else
            bool(self._runtime.get_access_token()))

        # Known-expiry evaluation (timezone-safe; unparseable == unknown,
        # never trusted as valid).
        expires_iso = (stored_access or {}).get("expires_at")
        expiry = parse_expiry_iso(expires_iso)
        expiry_known = expiry is not None
        expired = bool(is_expired(expiry)) if expiry_known else None
        rejected = bool(self.rejection_evidence().get("rejected"))

        if not runtime_active:
            auth_state = AuthState.MISSING
        elif rejected or expired is True:
            auth_state = AuthState.EXPIRED
        elif expiry_known:
            auth_state = AuthState.AUTHENTICATED
        else:
            auth_state = AuthState.UNKNOWN
        authenticated = (auth_state == AuthState.AUTHENTICATED)
        return {
            "app_id_configured": bool(creds and creds.get("app_id")),
            "secret_configured": bool(creds and creds.get("app_secret")),
            "login_available": bool(creds),
            # Canonical state (Upstox-compatible names).
            "authenticated": authenticated,
            "auth_state": auth_state,
            "login_required": not authenticated,
            "expired": expired,
            "expiry_known": expiry_known,
            # Legacy presence signal (kept for diagnostics/compat; it is
            # NOT a usability claim — use authenticated/auth_state).
            "access_token_active": runtime_active,
            "runtime_active": runtime_active,
            "refresh_token_stored": has_refresh,
            "refresh_stored": has_refresh,
            "pin_stored": pin_stored,
            # Durable session / restart-safety signals. Only presence and
            # the expiry string cross this boundary — NEVER token values.
            "stored_access_present": stored_access is not None,
            "session_persisted": stored_access is not None,
            "restart_recovery": bool(has_refresh and pin_stored),
            "session_restored": bool(
                (restore_state or {}).get("fyers_restored")),
            "access_token_expires_at": expires_iso,
            "last_auth_status": self._storage.load_status("fyers"),
            # "key_missing"/"decrypt_failed": ciphertext exists but the
            # current master.key cannot read it — a store ERROR, distinct
            # from ordinary "not configured".
            "store_error": store_state.get("reason"),
        }
