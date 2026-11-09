"""Previous-OI recorder + OI-change calculation (app layer).

Two responsibilities:

* :func:`oi_change_and_pct` — provider-agnostic OI change / OI% with the
  approved precedence: **provider-supplied previous OI wins**, persisted
  prior-session OI is the fallback, and the result is ``unavailable`` when no
  valid baseline exists (never fabricated).
* :class:`PreviousOiRecorder` — accumulates observed futures OI and flushes
  **batched** snapshots on a bounded cadence. It NEVER writes per tick.

Only canonical futures contracts are tracked (identity map built from the
catalog). The recorder is provider-neutral; the provider name is recorded as
data, never assumed.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from core.persistence.modules.previous_oi import FUTURE, ist_session_date

# Single source for the OI-change calculation + basis vocabulary (market layer).
from market.oi import (
    BASIS_PERSISTED,
    BASIS_PROVIDER,
    BASIS_UNAVAILABLE,
    oi_change_and_pct,
    quote_field,
)

logger = logging.getLogger(__name__)

# Batched flush cadence (seconds). Never per tick.
FLUSH_INTERVAL_SECONDS = 45.0

__all__ = [
    "PreviousOiRecorder", "oi_change_and_pct", "quote_field",
    "BASIS_PROVIDER", "BASIS_PERSISTED", "BASIS_UNAVAILABLE",
    "FLUSH_INTERVAL_SECONDS",
]


class PreviousOiRecorder:
    """Accumulates futures OI and flushes batched snapshots (never per tick)."""

    def __init__(
        self,
        store: Any,
        *,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        session_date_fn: Callable[[], str] = ist_session_date,
    ) -> None:
        self._store = store
        self._flush_interval = float(flush_interval)
        self._clock = clock
        self._session_date_fn = session_date_fn
        self._pending: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        self._identity: dict[tuple[str, str], tuple[str, str, str]] = {}
        self._lock = threading.Lock()
        self._last_flush = clock()

    # -- identity map (canonical futures only) -------------------------------

    def refresh_identity(self, contract_rows: Iterable[dict[str, Any]]) -> int:
        """Rebuild the ``(exchange, token) -> (underlying, expiry, provider)``
        map from catalog future rows. Only mapped tokens are recorded."""
        mapping: dict[tuple[str, str], tuple[str, str, str]] = {}
        for row in contract_rows or []:
            token = row.get("provider_symbol") or row.get("instrument_token")
            exchange = str(row.get("exchange") or "NSE")
            underlying = str(row.get("underlying") or "").upper()
            expiry = str(row.get("expiry") or "")
            if not token or not underlying or not expiry:
                continue
            mapping[(exchange, str(token))] = (
                underlying, expiry, str(row.get("provider") or ""))
        with self._lock:
            self._identity = mapping
        return len(mapping)

    # -- observation (per tick, cheap) ---------------------------------------

    def observe_quote(self, quote: Any) -> bool:
        """Accumulate one quote's OI if it is a tracked futures contract."""
        if quote is None:
            return False
        exchange = quote_field(quote, "exchange")
        token = quote_field(quote, "instrument_token")
        if not exchange or not token:
            return False
        with self._lock:
            identity = self._identity.get((str(exchange), str(token)))
        if identity is None:
            return False
        oi = quote_field(quote, "open_interest")
        if oi is None:
            return False
        underlying, expiry, provider = identity
        session = self._session_date_fn()
        key = (str(exchange), underlying, expiry, FUTURE, session)
        with self._lock:
            self._pending[key] = {
                "exchange": str(exchange), "underlying": underlying,
                "expiry": expiry, "instrument_type": FUTURE,
                "session_date": session, "oi": float(oi),
                "provider": provider, "instrument_token": str(token),
            }
        return True

    # -- batched flush -------------------------------------------------------

    def flush(self) -> int:
        """Write all accumulated snapshots in one batched transaction."""
        with self._lock:
            rows = list(self._pending.values())
            self._pending.clear()
            self._last_flush = self._clock()
        if not rows:
            return 0
        try:
            return int(self._store.upsert_previous_oi_snapshots(rows))
        except Exception:
            logger.exception("previous-OI flush failed")
            return 0

    def maybe_flush(self) -> int:
        """Flush only when the bounded cadence has elapsed."""
        with self._lock:
            due = (self._clock() - self._last_flush) >= self._flush_interval
        return self.flush() if due else 0

    async def run(self, stop_event: asyncio.Event) -> None:
        """Periodic flush loop; flushes once more on shutdown."""
        try:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(stop_event.wait(),
                                           timeout=min(5.0, self._flush_interval))
                    break
                except asyncio.TimeoutError:
                    self.maybe_flush()
        except asyncio.CancelledError:
            raise
        finally:
            self.flush()
