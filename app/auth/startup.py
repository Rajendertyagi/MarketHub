"""Startup wiring: keep app/server.py small.

Server startup conceptually does::

    auth_service.restore_sessions()
    source_manager / start feeds
    start remaining application services

Broker-specific restoration lives here + in the broker auth services,
never inline in the composition root.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("event_server")


async def restore_auth_sessions(
    auth_service: Any,
    sources_cfg: dict | None,
    broker_restore: dict,
) -> dict:
    """Restore all broker sessions with per-broker isolation.

    Returns the updated broker_restore dict. Never raises.
    """
    try:
        return await auth_service.restore_sessions(sources_cfg, broker_restore)
    except Exception:
        logger.exception("auth session restore failed")
        return broker_restore


def build_auth_service(
    cred_store: Any,
    *,
    upstox_feed_provider=None,
    upstox_restart_fn=None,
    fyers_runtime_auth=None,
    fyers_redirect_uri: str = "",
    fyers_feed_provider=None,
) -> Any:
    """Construct the application AuthService over an encrypted store."""
    from app.auth.fyers import FyersAuthService
    from app.auth.service import AuthService
    from app.auth.storage import AuthStorage
    from app.auth.upstox import UpstoxAuthService

    storage = AuthStorage(cred_store)
    upstox = UpstoxAuthService(
        storage,
        feed_provider=upstox_feed_provider,
        restart_fn=upstox_restart_fn,
    )
    fyers = FyersAuthService(
        storage,
        fyers_runtime_auth,
        redirect_uri=fyers_redirect_uri,
        feed_provider=fyers_feed_provider,
    )
    return AuthService(upstox=upstox, fyers=fyers)
