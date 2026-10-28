r"""Chart bars application service: provider backfill + persisted history.

Composition:

    API route -> BarService -> (provider REST) -> market_bars (SQLite)
                           \-> live BarRecorder (forming bar)

Bars are stored at the provider's own granularity. Week and month are the
only derived timeframes (aggregated from stored daily bars), so every bar a
client sees is either provider truth or a deterministic calendar roll-up of
provider truth.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

from market.bars import (
    INTERVAL_DAY,
    INTERVAL_MONTH,
    INTERVAL_WEEK,
    IST,
    aggregate_calendar,
    ist_date,
    merge_forming_daily,
    merge_forming_intraday,
    parse_timeframe,
    timeframe_ms,
)

logger = logging.getLogger(__name__)

_UPSTOX_HISTORY_URL = "https://api.upstox.com/v3/historical-candle"
# The historical endpoint stops at the previous session; today's candles
# (including a live partial daily) are only published here.
_UPSTOX_INTRADAY_URL = "https://api.upstox.com/v3/historical-candle/intraday"

# timeframe (minutes) -> (unit, interval) for the provider's own bars.
_UPSTOX_TIMEFRAMES: dict[int, tuple[str, str]] = {
    1: ("minutes", "1"),
    5: ("minutes", "5"),
    15: ("minutes", "15"),
    30: ("minutes", "30"),
    60: ("hours", "1"),
    240: ("hours", "4"),
    INTERVAL_DAY: ("days", "1"),
}

_FYERS_TIMEFRAMES: dict[int, tuple[str, int]] = {
    1: ("minutes", 1), 5: ("minutes", 5), 15: ("minutes", 15),
    30: ("minutes", 30), 60: ("hours", 1), INTERVAL_DAY: ("days", 1),
}

# Provider per-request record limits (official V3 table: 1 month for
# 1-15 minute intervals, 1 quarter for wider minutes/hours, 1 decade for days).
_MAX_DAYS = {"minutes_small": 31, "minutes_wide": 92, "hours": 92,
             "days": 3650}
_MAX_REQUESTS_PER_CALL = 6
# An IST session is ~6.25h of a 24h day and a trading week is 5 of 7 days,
# so N intraday bars need roughly 6x their own span in calendar time before
# the requested count can actually exist.
_INTRADAY_CALENDAR_FACTOR = 6
_INTRADAY_STALE_MS = 10 * 60_000
_DAILY_STALE_MS = 36 * 60 * 60_000
_BACKFILL_COOLDOWN_SECONDS = 60.0
_PRUNE_EVERY_SECONDS = 3600.0
_INTRADAY_RETENTION_SESSIONS = 30
_HISTORY_FLOOR_MS = int(datetime(2000, 1, 1, tzinfo=IST).timestamp() * 1000)
_INTRADAY_HISTORY_FLOOR_MS = int(
    datetime(2022, 1, 1, tzinfo=IST).timestamp() * 1000)


class BarsUnavailable(RuntimeError):
    """A chart request cannot be served (no session, unsupported timeframe)."""


def _chunks(from_ms: int, to_ms: int, max_days: int) -> list[tuple[date, date]]:
    """IST calendar chunks, newest first (bounded provider ranges)."""
    end_day = ist_date(to_ms)
    start_day = ist_date(from_ms)
    chunks: list[tuple[date, date]] = []
    cursor_end = end_day
    while cursor_end >= start_day:
        cursor_start = max(start_day, cursor_end - timedelta(days=max_days - 1))
        chunks.append((cursor_start, cursor_end))
        cursor_end = cursor_start - timedelta(days=1)
    return chunks


def _max_days(unit: str, interval: str) -> int:
    if unit == "minutes":
        return (_MAX_DAYS["minutes_small"] if int(interval) <= 15
                else _MAX_DAYS["minutes_wide"])
    return _MAX_DAYS.get(unit, 92)


def _candle_row(candle: Any, timeframe: int, source: str) -> dict[str, Any] | None:
    try:
        ts = int(candle.timestamp.timestamp() * 1000)
        open_ = float(candle.open)
        high = float(candle.high)
        low = float(candle.low)
        close = float(candle.close)
    except (AttributeError, TypeError, ValueError):
        return None
    if ts <= 0 or min(open_, low, close) <= 0 or high < low:
        return None
    volume = candle.volume
    return {
        "interval_minutes": int(timeframe), "ts": ts, "open": open_,
        "high": high, "low": low, "close": close,
        "volume": float(volume) if volume is not None else None,
        "oi": candle.open_interest, "source": source,
    }


class BarService:
    """Read/backfill facade over the bar store plus the live recorder."""

    def __init__(
        self,
        store: Any,
        *,
        auth_context_fn: Callable[[], Any] | None = None,
        fyers_adapter: Any = None,
        catalog: Any = None,
        registry: Any = None,
        live: Any = None,
        now_ms: Callable[[], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._auth_context_fn = auth_context_fn
        self._fyers = fyers_adapter
        self._catalog = catalog
        self._registry = registry
        self._live = live
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._clock = clock
        self._last_attempt: dict[tuple[str, str, int], tuple[float, int]] = {}
        self._locks: dict[tuple[str, str, int], asyncio.Lock] = {}
        self._last_prune = 0.0

    # -- provider resolution ---------------------------------------------------

    def _provider_for(self, instrument_key: str) -> str:
        registry = self._registry
        if registry is None:
            try:
                from providers.registry import get_registry
                registry = get_registry()
                self._registry = registry
            except Exception:
                registry = None
        if registry is not None:
            try:
                pid = registry.provider_for_key(
                    instrument_key, catalog=self._catalog)
                if pid:
                    return str(pid)
            except Exception:
                pass
        from app.market_data import _infer_provider
        return _infer_provider(instrument_key)

    def _timeframe_supported(self, provider: str, timeframe: int) -> bool:
        table = _UPSTOX_TIMEFRAMES if provider == "upstox" else _FYERS_TIMEFRAMES
        return (timeframe in table or timeframe in (INTERVAL_WEEK, INTERVAL_MONTH))

    # -- public read -----------------------------------------------------------

    async def get_bars(
        self,
        *,
        instrument_key: str,
        timeframe: Any,
        provider: str | None = None,
        from_ts: int | None = None,
        to_ts: int | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        tf = parse_timeframe(timeframe)
        if tf is None:
            raise BarsUnavailable(f"unsupported timeframe: {timeframe!r}")
        key = (instrument_key or "").strip()
        if not key:
            raise BarsUnavailable("instrument_key is required")
        provider = (provider or self._provider_for(key)).strip().lower()
        if provider not in ("upstox", "fyers"):
            raise BarsUnavailable(f"unknown provider: {provider}")
        if not self._timeframe_supported(provider, tf):
            raise BarsUnavailable(
                f"{provider} does not provide {tf}-minute candles")

        now = self._now_ms()
        to_ms = min(int(to_ts) if to_ts is not None else now, now + 60_000)
        try:
            limit = max(1, min(int(limit), 1500))
        except (TypeError, ValueError):
            limit = 500
        from_ms = int(from_ts) if from_ts is not None else None
        needed_from = (from_ms if from_ms is not None
                       else self._lookback(tf, to_ms, limit))

        self._maybe_prune()

        source_tf = INTERVAL_DAY if tf in (INTERVAL_WEEK, INTERVAL_MONTH) else tf
        await self._ensure_coverage(
            provider, key, source_tf, needed_from, to_ms)

        rows = self._store.query_bars(
            provider=provider, instrument_key=key, interval_minutes=source_tf,
            from_ts=from_ms if from_ms is not None else needed_from,
            to_ts=to_ms,
            limit=None if tf in (INTERVAL_WEEK, INTERVAL_MONTH) else limit,
        )

        if source_tf == INTERVAL_DAY:
            rows = self._merge_live_daily(provider, key, rows)
            if tf in (INTERVAL_WEEK, INTERVAL_MONTH):
                rows = aggregate_calendar(rows, tf)
                rows = rows[-limit:]
        else:
            rows = self._merge_live_intraday(provider, key, rows, tf)

        # A derived or live-merged bar has no single provenance; it reports
        # `null` instead of dropping the field, so every row of the response
        # has the same shape.
        for row in rows:
            row.setdefault("source", None)

        return {
            "provider": provider,
            "instrument_key": key,
            "timeframe": tf,
            "bars": rows,
        }

    def _lookback(self, timeframe: int, to_ms: int, limit: int) -> int:
        """Earliest timestamp the requested bar count can possibly need.

        Session gaps are allowed for (nights/weekends), then clamped to the
        provider's own history floor so a client asking for 500 monthly bars
        cannot trigger decades of API calls.
        """
        span = timeframe_ms(timeframe) * limit
        if timeframe <= 240:
            needed = to_ms - span * _INTRADAY_CALENDAR_FACTOR
            floor = _INTRADAY_HISTORY_FLOOR_MS
        elif timeframe == INTERVAL_DAY:
            needed = to_ms - int(span * 1.6)
            floor = _HISTORY_FLOOR_MS
        else:
            needed = to_ms - int(span * 1.6)
            floor = _HISTORY_FLOOR_MS
        return max(needed, floor)

    # -- live forming bar ------------------------------------------------------

    def _merge_live_daily(
        self, provider: str, key: str, rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._live is None:
            return rows
        forming = self._live.pending_bar(key, INTERVAL_DAY)
        if forming is None or str(forming.get("source", "ws")) != "ws":
            return rows
        return merge_forming_daily(rows, forming)

    def _merge_live_intraday(
        self, provider: str, key: str, rows: list[dict[str, Any]],
        timeframe: int,
    ) -> list[dict[str, Any]]:
        if self._live is None:
            return rows
        forming = self._live.pending_bar(key, 1)
        if forming is None:
            return rows

        def minute_reader(start: int, end: int) -> list[dict[str, Any]]:
            return self._store.query_bars(
                provider=provider, instrument_key=key, interval_minutes=1,
                from_ts=start, to_ts=end)

        return merge_forming_intraday(rows, forming, minute_reader, timeframe)

    # -- coverage / backfill ---------------------------------------------------

    async def _ensure_coverage(
        self, provider: str, key: str, timeframe: int,
        needed_from: int, to_ms: int,
    ) -> None:
        earliest, latest, count = self._store.bar_bounds(
            provider=provider, instrument_key=key,
            interval_minutes=timeframe)
        stale_after = (_INTRADAY_STALE_MS if timeframe <= 240
                       else _DAILY_STALE_MS)
        gaps: list[tuple[int, int]] = []
        if count == 0 or earliest is None or latest is None:
            gaps.append((needed_from, to_ms))
        else:
            if needed_from < earliest:
                gaps.append((needed_from, earliest - 1))
            if latest < to_ms - stale_after:
                gaps.append((max(latest + timeframe_ms(timeframe),
                                 needed_from), to_ms))
        if not gaps:
            return
        # All gaps of one read are filled by one provider attempt: a tail
        # poll and a leading-edge extension must not each arm the cooldown
        # against the other.
        attempt_from = min(from_ms for from_ms, _ in gaps)
        if not self._cooldown_ready(provider, key, timeframe, attempt_from):
            return
        async with self._lock_for(provider, key, timeframe):
            if not self._cooldown_ready(
                    provider, key, timeframe, attempt_from):
                return
            self._last_attempt[(provider, key, timeframe)] = (
                self._clock(), attempt_from)
            in_session = ist_date(to_ms) == ist_date(self._now_ms())
            for from_ms, to_ms_gap in gaps:
                if to_ms_gap < from_ms:
                    continue
                try:
                    await self._backfill(
                        provider, key, timeframe, from_ms, to_ms_gap,
                        # Only the gap that reaches the read's upper bound
                        # can need the live session; an older leading-edge
                        # gap is served by the historical endpoint alone.
                        include_session=in_session and to_ms_gap >= to_ms)
                except BarsUnavailable:
                    # No session is only fatal when there is nothing stored
                    # to fall back on; otherwise serve the persisted history
                    # (the bars are still provider truth, just not fresh).
                    if count == 0:
                        raise
                    logger.info(
                        "bars: %s backfill unavailable for %s (%d min) - "
                        "serving stored history", provider, key, timeframe)
                    return
                except Exception:
                    # Provider trouble must not blank the chart: serve what
                    # is stored and let the next request retry.
                    logger.warning(
                        "bars: %s backfill failed for %s (%d min) - serving "
                        "stored history", provider, key, timeframe,
                        exc_info=True)

    def _cooldown_ready(
        self, provider: str, key: str, timeframe: int, from_ms: int,
    ) -> bool:
        last = self._last_attempt.get((provider, key, timeframe))
        if last is None:
            return True
        last_clock, last_from = last
        if from_ms < last_from:
            # Reaching further back than the previous attempt is new work,
            # not a retry the cooldown was meant to suppress. Without this a
            # narrow poll-sized attempt would blank a wider history request
            # for a full cooldown window.
            return True
        return (self._clock() - last_clock) >= _BACKFILL_COOLDOWN_SECONDS

    def _lock_for(self, provider: str, key: str, timeframe: int) -> asyncio.Lock:
        lock = self._locks.get((provider, key, timeframe))
        if lock is None:
            lock = asyncio.Lock()
            self._locks[(provider, key, timeframe)] = lock
        return lock

    async def _backfill(
        self, provider: str, key: str, timeframe: int,
        from_ms: int, to_ms: int, *, include_session: bool = False,
    ) -> int:
        rows: list[dict[str, Any]] = []
        if provider == "upstox":
            rows = await self._fetch_upstox(
                key, timeframe, from_ms, to_ms,
                include_session=include_session)
        else:
            rows = await self._fetch_fyers(key, timeframe, from_ms, to_ms)
        if not rows:
            return 0
        return self._store.upsert_bars([
            {**row, "provider": provider, "instrument_key": key}
            for row in rows])

    async def _fetch_upstox(
        self, key: str, timeframe: int, from_ms: int, to_ms: int, *,
        include_session: bool = False,
    ) -> list[dict[str, Any]]:
        unit, interval = _UPSTOX_TIMEFRAMES[timeframe]
        ctx = self._auth_context_fn() if self._auth_context_fn else None
        if ctx is None:
            raise BarsUnavailable(
                "upstox is not authenticated; log in to load history")
        rest, credentials = ctx
        from market.normalize.upstox import candles_from_rest

        encoded = quote(key, safe="")
        today = ist_date(self._now_ms())
        rows: list[dict[str, Any]] = []
        requests = 0
        for start_day, end_day in _chunks(
                from_ms, to_ms, _max_days(unit, interval)):
            if start_day >= today:
                # The historical endpoint stops at the previous session;
                # a chunk wholly inside today can never return a candle.
                continue
            url = (f"{_UPSTOX_HISTORY_URL}/{encoded}/{unit}/{interval}/"
                   f"{end_day.isoformat()}/{start_day.isoformat()}")
            payload = await rest.authenticated_request(
                method="GET", url=url,
                access_token=credentials.access_token)
            for candle in candles_from_rest(payload):
                row = _candle_row(candle, timeframe, "rest")
                if row is not None:
                    rows.append(row)
            requests += 1
            if requests >= _MAX_REQUESTS_PER_CALL:
                break
        # The current session (including the forming daily candle) is only
        # published by the intraday endpoint, which takes no date range.
        if include_session:
            url = f"{_UPSTOX_INTRADAY_URL}/{encoded}/{unit}/{interval}"
            try:
                payload = await rest.authenticated_request(
                    method="GET", url=url,
                    access_token=credentials.access_token)
            except Exception:
                logger.warning(
                    "bars: upstox intraday session unavailable for %s (%d "
                    "min)", key, timeframe, exc_info=True)
            else:
                for candle in candles_from_rest(payload):
                    row = _candle_row(candle, timeframe, "rest")
                    if row is not None:
                        rows.append(row)
        return rows

    async def _fetch_fyers(
        self, key: str, timeframe: int, from_ms: int, to_ms: int,
    ) -> list[dict[str, Any]]:
        if self._fyers is None:
            raise BarsUnavailable("fyers market data is not configured")
        unit, interval = _FYERS_TIMEFRAMES[timeframe]
        from market.normalize.fyers import fyers_resolution
        resolution = fyers_resolution(unit, interval)
        if resolution is None:
            raise BarsUnavailable(f"fyers does not support {unit}/{interval}")

        rows: list[dict[str, Any]] = []
        requests = 0
        for start_day, end_day in _chunks(from_ms, to_ms, 90):
            candles = await self._fyers.history(
                instrument_key=key, resolution=resolution,
                from_date=start_day.isoformat(), to_date=end_day.isoformat())
            for candle in candles or []:
                row = _candle_row(candle, timeframe, "rest")
                if row is not None:
                    rows.append(row)
            requests += 1
            if requests >= _MAX_REQUESTS_PER_CALL:
                break
        return rows

    # -- retention -------------------------------------------------------------

    def _maybe_prune(self) -> None:
        now = self._clock()
        if now - self._last_prune < _PRUNE_EVERY_SECONDS:
            return
        self._last_prune = now
        try:
            self._store.prune_intraday_bars(
                sessions=_INTRADAY_RETENTION_SESSIONS, interval_minutes=1)
        except Exception:
            logger.warning("bars: intraday retention prune failed",
                           exc_info=True)
