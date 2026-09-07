"""Application-level AuthService: coordinates broker session lifecycle."""

from __future__ import annotations

import logging
from typing import Any

from app.auth.fyers import FyersAuthService
from app.auth.upstox import UpstoxAuthService

logger = logging.getLogger("event_server")


class AuthService:
    """Owns login result, persistence, runtime session, restore, expiry,
    logout, genuine provider rejection, and status projection.

    Does NOT own market data / feed implementation.
    """

    def __init__(self, *, upstox: UpstoxAuthService,
                 fyers: FyersAuthService) -> None:
        self.upstox = upstox
        self.fyers = fyers

    async def restore_sessions(
        self,
        sources_cfg: dict | None,
        broker_restore: dict | None = None,
    ) -> dict[str, Any]:
        """Best-effort restore of every broker; isolated per broker.

        Never raises: one broker's failure must never abort the other or
        server boot. MarketHub starts with login_required=true instead.
        """
        if broker_restore is None:
            broker_restore = {}
        # Upstox (synchronous).
        try:
            out = self.upstox.restore_session(sources_cfg)
            broker_restore["upstox_restored"] = bool(out.restored)
            broker_restore["upstox_last_status"] = out.reason
        except Exception:
            logger.exception("upstox session restore failed")
            broker_restore["upstox_restored"] = False
            broker_restore["upstox_last_status"] = "restore_error"
        # Fyers (async refresh path; network failures stay isolated here).
        try:
            fout = await self.fyers.restore_session()
            broker_restore["fyers_restored"] = bool(fout.restored)
            broker_restore["fyers_last_status"] = fout.reason
        except Exception:
            logger.exception("fyers session restore failed")
            broker_restore["fyers_restored"] = False
            broker_restore["fyers_last_status"] = "restore_error"
        return broker_restore
