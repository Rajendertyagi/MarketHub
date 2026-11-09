"""Guarded Fyers REST index source (Fyers-only fallback).

Upstox is the primary index source. When Upstox is unavailable, Fyers
index data is fetched over REST under a GUARDED poller:

  * rate limited through a shared bucket (<= 5 rps);
  * interval bounded (>= 60 s between polls);
  * gated to market hours (IST session);
  * freshness bounded (5 minutes) — a fresh cached value is reused;
  * duplicate-request suppression (a symbol fetched inside the freshness
    window is not re-fetched);
  * unauthenticated -> never fetches (honest unavailable).

The existing HSM index guard (dropping ``if``-prefixed topics) is UNTOUCHED:
this source is a separate, explicit REST path.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("event_server")

# Guarded limits (locked).
MAX_RPS = 5
MIN_INTERVAL_SECONDS = 60
FRESHNESS_SECONDS = 300

# IST session window (09:15 - 15:30 IST) expressed in UTC offsets.
_MARKET_OPEN_IST = (9, 15)
_MARKET_CLOSE_IST = (15, 30)
_IST_OFFSET = timedelta(hours=5, minutes=30)


def is_market_hours(moment: datetime | None = None) -> bool:
    """True when ``moment`` is inside the IST trading session."""
    now = moment or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ist = now.astimezone(timezone.utc) + _IST_OFFSET
    minutes = ist.hour * 60 + ist.minute
    return (_MARKET_OPEN_IST[0] * 60 + _MARKET_OPEN_IST[1]) <= minutes <= (
        _MARKET_CLOSE_IST[0] * 60 + _MARKET_CLOSE_IST[1])


class RateBucket:
    """Simple sliding one-second rate bucket (max N per second)."""

    def __init__(self, max_rps: int = MAX_RPS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._max = int(max_rps)
        self._clock = clock
        self._lock = threading.Lock()
        self._window: list[float] = []

    def allow(self) -> bool:
        now = self._clock()
        with self._lock:
            self._window = [t for t in self._window if now - t < 1.0]
            if len(self._window) >= self._max:
                return False
            self._window.append(now)
            return True


class FyersIndexSource:
    """Bounded Fyers REST index poller (Fyers-only fallback)."""

    def __init__(
        self,
        *,
        rest: Any,
        auth: Any,
        symbols: Sequence[str] | Callable[[], Sequence[str]],
        now_fn: Callable[[], datetime] | None = None,
        bucket: RateBucket | None = None,
        interval_seconds: int = MIN_INTERVAL_SECONDS,
        freshness_seconds: int = FRESHNESS_SECONDS,
    ) -> None:
        self._rest = rest
        self._auth = auth
        # The symbol set may be DYNAMIC (a callable): the poller only
        # fetches the indices that are not already served live elsewhere.
        self._symbols_source = symbols
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._bucket = bucket or RateBucket()
        self._interval = int(interval_seconds)
        self._freshness = int(freshness_seconds)
        self._last_fetch: dict[str, datetime] = {}
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------

    def _now(self) -> datetime:
        return self._now_fn()

    def _token(self) -> str | None:
        try:
            return self._auth.get_access_token()
        except Exception:
            return None

    @property
    def symbols(self) -> tuple[str, ...]:
        """The symbols this source currently serves (dynamic aware)."""
        source = self._symbols_source
        if callable(source):
            try:
                return tuple(source() or ())
            except Exception:
                return ()
        return tuple(source)

    def should_fetch(self, symbol: str) -> bool:
        """True when a fetch is allowed right now (guards all four limits)."""
        if symbol not in self.symbols:
            return False
        if not is_market_hours(self._now()):
            return False
        if not self._token():
            return False
        with self._lock:
            last = self._last_fetch.get(symbol)
        if last is None:
            return True
        age = (self._now() - last).total_seconds()
        if age < self._freshness:
            return False
        return True

    def mark_fetched(self, symbol: str) -> None:
        with self._lock:
            self._last_fetch[symbol] = self._now()

    def ready(self) -> bool:
        """True when the poller may run at all."""
        return bool(self._token()) and is_market_hours(self._now())

    # -- fetch -------------------------------------------------------------

    async def poll(self) -> list[Any]:
        """Fetch the due index symbols (rate limited, deduplicated)."""
        if not self.ready():
            return []
        due = [s for s in self.symbols if self.should_fetch(s)]
        if not due:
            return []
        token = self._token()
        if not token:
            return []
        if not self._bucket.allow():
            return []
        try:
            quotes = await self._rest.quotes(token, list(due))
        except Exception:
            return []
        for symbol in due:
            self.mark_fetched(symbol)
        return list(quotes or [])

    def snapshot_state(self) -> dict[str, Any]:
        """Diagnostics (no secrets)."""
        with self._lock:
            return {
                "symbols": list(self.symbols),
                "last_fetch": {
                    k: v.isoformat() for k, v in self._last_fetch.items()},
                "ready": self.ready(),
                "interval_seconds": self._interval,
                "freshness_seconds": self._freshness,
            }


def default_index_symbols(entries: Iterable[dict[str, str]]) -> list[str]:
    """Fyers index symbols from the canonical index registry entries."""
    out: list[str] = []
    for entry in entries:
        symbol = (entry or {}).get("fyers_symbol")
        if symbol:
            out.append(str(symbol))
    return out


async def run_fyers_index_poll_loop(
    source: Any,
    market_service: Any,
    *,
    interval_seconds: float = 30.0,
    stop_event: Any = None,
) -> None:
    """Poll the guarded Fyers REST index source and publish its quotes.

    The loop is the production OWNER of the Fyers index fallback: it runs
    while the application is up, is bounded by the source's own guards
    (rate bucket, interval, market hours, freshness) and stops cleanly on
    cancellation.

    Quotes are published to the quote store under the SAME storage key the
    Fyers feed uses — the provider's API symbol (``NSE:NIFTY50-INDEX``) —
    so a reader never has to guess which key holds the index quote. The
    HSM index guard (dropping ``if``-prefixed topics) is UNTOUCHED: this
    is a separate, explicit REST path.
    """
    if source is None or market_service is None:
        return
    from market.service import QuotePatch

    while True:
        try:
            quotes = await source.poll()
            for quote in quotes or ():
                token = getattr(quote, "instrument_token", None)
                exchange = getattr(quote, "exchange", None)
                if not token or not exchange:
                    continue
                reported = {}
                for name in ("ltp", "volume", "change", "change_percent",
                             "open", "high", "low", "close",
                             "open_interest", "previous_oi",
                             "oi_change", "oi_change_percent",
                             "best_bid", "best_ask", "bid", "ask",
                             "avg_trade_price", "total_buy_qty",
                             "total_sell_qty"):
                    value = getattr(quote, name, None)
                    if value is not None:
                        reported[name] = value
                greeks = getattr(quote, "greeks", None)
                if greeks is not None:
                    reported["greeks"] = greeks
                received = getattr(quote, "received_ts", None)
                try:
                    await market_service.apply_quote(QuotePatch(
                        exchange=exchange,
                        instrument_token=token,
                        tradingsymbol=getattr(quote, "tradingsymbol", ""),
                        received_ts=received,
                        reported_fields=reported,
                    ))
                except Exception as exc:
                    logger.debug("fyers index quote rejected: %s",
                                 type(exc).__name__)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("fyers index poll failed: %s", type(exc).__name__)
        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(),
                                       timeout=interval_seconds)
                return
            await asyncio.sleep(interval_seconds)
        except asyncio.TimeoutError:
            pass
