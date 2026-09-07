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

from app.auth.models import AuthState, RestoreOutcome, parse_expiry_iso
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

    def status_snapshot(
        self, restore_state: dict | None = None,
    ) -> dict[str, Any]:
        """Full auth projection (route adds source/feed fields only)."""
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
        return {
            "app_id_configured": bool(creds and creds.get("app_id")),
            "secret_configured": bool(creds and creds.get("app_secret")),
            "login_available": bool(creds),
            "access_token_active": runtime_active,
            "runtime_active": runtime_active,
            "refresh_token_stored": has_refresh,
            "refresh_stored": has_refresh,
            "pin_stored": pin_stored,
            "stored_access": stored_access,
            # Durable session / restart-safety signals.
            "session_persisted": stored_access is not None,
            "restart_recovery": bool(has_refresh and pin_stored),
            "session_restored": bool(
                (restore_state or {}).get("fyers_restored")),
            "access_token_expires_at": (
                stored_access or {}).get("expires_at"),
            "login_required": not (runtime_active or (has_refresh and pin_stored)),
            "last_auth_status": self._storage.load_status("fyers"),
            # "key_missing"/"decrypt_failed": ciphertext exists but the
            # current master.key cannot read it — a store ERROR, distinct
            # from ordinary "not configured".
            "store_error": store_state.get("reason"),
        }
