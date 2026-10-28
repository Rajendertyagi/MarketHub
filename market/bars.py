"""Provider-neutral OHLCV bars: ingestion buffer and read/aggregation service.

Bars are stored at the finest granularity a provider actually delivers
(1-minute intraday and daily). Every other timeframe is derived on read, so a
5-minute chart can never disagree with the 1-minute series it came from.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

INTERVAL_MINUTE = 1
INTERVAL_DAY = 1440
INTERVAL_WEEK = 10080
INTERVAL_MONTH = 43200

# Chart timeframes, in minutes. These are the exact integers a chart client
# configures (bare minutes, not "5m"/"1h") and the only values the API accepts.
TIMEFRAMES: tuple[int, ...] = (
    1, 5, 15, 30, 60, 240, 1440, 10080, 43200,
)

# IST = UTC+05:30, fixed (India has no DST).
IST = timezone(timedelta(hours=5, minutes=30))

SOURCE_WS = "ws"
SOURCE_REST = "rest"

# A forming bar is persisted at most this often; closed bars are written
# immediately when the provider rolls to a new one.
DEFAULT_FLUSH_SECONDS = 10.0


def parse_timeframe(value: Any) -> int | None:
    """Canonical timeframe in minutes, or None when unsupported."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if minutes in TIMEFRAMES else None


def timeframe_ms(minutes: int) -> int:
    return int(minutes) * 60_000


def ist_date(ts_ms: int) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=IST).date()


def _calendar_key(ts_ms: int, timeframe: int) -> tuple[int, int]:
    day = ist_date(ts_ms)
    if timeframe == INTERVAL_WEEK:
        iso = day.isocalendar()
        return (iso.year, iso.week)
    return (day.year, day.month)


def aggregate_calendar(
    daily_bars: Iterable[Mapping[str, Any]], timeframe: int,
) -> list[dict[str, Any]]:
    """Aggregate daily bars into ISO-week or calendar-month buckets (IST).

    The bucket timestamp is the first session of the bucket, so every bar in
    the result carries a real session open exactly like the provider's own.
    """
    if timeframe not in (INTERVAL_WEEK, INTERVAL_MONTH):
        raise ValueError("calendar aggregation supports week/month only")
    out: list[dict[str, Any]] = []
    current_key: tuple[int, int] | None = None
    for bar in daily_bars:
        key = _calendar_key(int(bar["ts"]), timeframe)
        if key != current_key:
            out.append({
                "ts": int(bar["ts"]), "open": bar["open"], "high": bar["high"],
                "low": bar["low"], "close": bar["close"],
                "volume": bar.get("volume"), "oi": bar.get("oi"),
            })
            current_key = key
            continue
        bucket = out[-1]
        bucket["high"] = max(bucket["high"], bar["high"])
        bucket["low"] = min(bucket["low"], bar["low"])
        bucket["close"] = bar["close"]
        if bar.get("volume") is not None:
            bucket["volume"] = (bucket["volume"] or 0.0) + bar["volume"]
        if bar.get("oi") is not None:
            bucket["oi"] = bar["oi"]
    return out


def merge_forming_daily(
    bars: list[dict[str, Any]], forming: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Replace (or append) the live daily bar — the provider's own aggregate."""
    row = {
        "ts": int(forming["ts"]), "open": forming["open"],
        "high": forming["high"], "low": forming["low"],
        "close": forming["close"], "volume": forming.get("volume"),
        "oi": forming.get("oi"),
    }
    if bars and bars[-1]["ts"] == row["ts"]:
        bars[-1] = row
        return bars
    if bars and bars[-1]["ts"] > row["ts"]:
        return bars
    return [*bars, row]


def merge_forming_intraday(
    bars: list[dict[str, Any]],
    forming: Mapping[str, Any],
    minute_reader: Callable[[int, int], Iterable[Mapping[str, Any]]],
    timeframe: int,
) -> list[dict[str, Any]]:
    """Fold the live 1-minute bar into the timeframe's current bucket.

    ``minute_reader(start, end)`` supplies the stored 1-minute bars of the
    bucket. When they exist the bucket's OHLCV is derived from them plus the
    forming minute, so a persisted snapshot of the forming minute can never be
    counted twice. Without them the stored bucket keeps its volume and only
    its price range is extended — volume is never fabricated.
    """
    forming_ts = int(forming["ts"])
    width = timeframe_ms(timeframe)
    last = bars[-1] if bars else None

    if last is not None and int(last["ts"]) <= forming_ts:
        bucket_ts = int(last["ts"])
        if forming_ts >= bucket_ts + width:
            same_session = (
                ist_date(forming_ts) == ist_date(bucket_ts)
                and forming_ts < bucket_ts + 2 * width
            )
            if timeframe != INTERVAL_MINUTE and not same_session:
                # Cannot align a fresh-session bucket to the provider's own
                # anchor; the REST tail backfill materializes it instead.
                return bars
            bucket_ts = bucket_ts + width if timeframe != INTERVAL_MINUTE \
                else forming_ts
    elif last is not None:
        return bars                 # older than the last stored bar
    else:
        bucket_ts = forming_ts

    window = [b for b in minute_reader(bucket_ts, bucket_ts + width - 1)
              if int(b["ts"]) != forming_ts]
    if window:
        open_ = min(window, key=lambda b: b["ts"])["open"]
        high = max([forming["high"], *(b["high"] for b in window)])
        low = min([forming["low"], *(b["low"] for b in window)])
        volumes = [b.get("volume") for b in window]
        if any(v is not None for v in volumes) or forming.get("volume") is not None:
            volume: float | None = sum(
                v for v in [*volumes, forming.get("volume")] if v is not None)
        else:
            volume = None
        oi_values = [b.get("oi") for b in window if b.get("oi") is not None]
        oi = forming.get("oi")
        if oi is None and oi_values:
            oi = oi_values[-1]
    else:
        base = last if last is not None and bucket_ts == int(last["ts"]) else None
        open_ = base["open"] if base else forming["open"]
        high = max(forming["high"], base["high"]) if base else forming["high"]
        low = min(forming["low"], base["low"]) if base else forming["low"]
        volume = base.get("volume") if base else forming.get("volume")
        oi = forming.get("oi") if forming.get("oi") is not None \
            else (base.get("oi") if base else None)

    row = {
        "ts": bucket_ts, "open": open_, "high": high, "low": low,
        "close": forming["close"], "volume": volume, "oi": oi,
    }
    if last is not None and bucket_ts == last["ts"]:
        bars[-1] = row
        return bars
    return [*bars, row]


def _bar_key(instrument_key: str, bar: Mapping[str, Any]) -> tuple[str, int]:
    return (instrument_key, int(bar["interval_minutes"]))


def _same_bar(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (
        a["ts"] == b["ts"]
        and a["open"] == b["open"]
        and a["high"] == b["high"]
        and a["low"] == b["low"]
        and a["close"] == b["close"]
        and a.get("volume") == b.get("volume")
    )


class BarRecorder:
    """Buffers live bars from a feed and persists them on a bounded cadence.

    The feed calls :meth:`observe` for every decoded frame; this object writes
    only when a bar actually changed, so a 10 Hz tick stream still produces at
    most one row per instrument per minute plus one in-flight update every
    ``flush_seconds``. A closed bar is written as soon as the provider rolls to
    the next one — never dropped, and never rewritten afterwards.
    """

    def __init__(
        self,
        store: Any,
        *,
        provider: str,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
        clock: Any = time.monotonic,
    ) -> None:
        self._store = store
        self._provider = provider
        self._flush_seconds = flush_seconds
        self._clock = clock
        self._pending: dict[tuple[str, int], dict[str, Any]] = {}
        self._dirty: set[tuple[str, int]] = set()
        self._retry: list[dict[str, Any]] = []
        self._last_ts: dict[tuple[str, int], int] = {}
        self._last_flush = clock()
        self.stats = {"bars_seen": 0, "bars_closed": 0, "bars_written": 0,
                      "out_of_order": 0, "write_errors": 0}

    def pending_bar(
        self, instrument_key: str, interval_minutes: int = 1,
    ) -> dict[str, Any] | None:
        """The live (forming) bar for one instrument, or None when unseen."""
        bar = self._pending.get((instrument_key, int(interval_minutes)))
        return dict(bar) if bar is not None else None

    def _row(self, instrument_key: str, bar: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "provider": self._provider,
            "instrument_key": instrument_key,
            "interval_minutes": int(bar["interval_minutes"]),
            "ts": int(bar["ts"]),
            "open": bar["open"],
            "high": bar["high"],
            "low": bar["low"],
            "close": bar["close"],
            "volume": bar.get("volume"),
            "source": SOURCE_WS,
        }

    def observe(
        self, instrument_key: str, bars: Iterable[Mapping[str, Any]],
    ) -> None:
        """Accept one instrument's bars from a decoded frame."""
        closed: list[dict[str, Any]] = []
        for bar in bars:
            self.stats["bars_seen"] += 1
            key = _bar_key(instrument_key, bar)
            ts = int(bar["ts"])
            previous_ts = self._last_ts.get(key)
            if previous_ts is not None and ts < previous_ts:
                self.stats["out_of_order"] += 1
                continue
            if previous_ts is not None and ts > previous_ts:
                previous = self._pending.get(key)
                if previous is not None:
                    closed.append(self._row(instrument_key, previous))
                    self.stats["bars_closed"] += 1
            self._last_ts[key] = ts
            current = self._pending.get(key)
            if current is None or not _same_bar(current, bar):
                self._pending[key] = dict(bar)
                self._dirty.add(key)

        if self._clock() - self._last_flush >= self._flush_seconds:
            closed.extend(self._drain())
        if closed:
            self._write(closed)

    def _drain(self) -> list[dict[str, Any]]:
        rows = [self._row(key[0], self._pending[key])
                for key in sorted(self._dirty) if key in self._pending]
        self._dirty.clear()
        self._last_flush = self._clock()
        return rows

    def flush(self, *, include_forming: bool = True) -> int:
        """Write pending bars now (``include_forming=False`` writes closed only)."""
        rows = self._drain() if include_forming else []
        if not rows:
            self._last_flush = self._clock()
            return 0
        return self._write(rows)

    def _write(self, rows: list[dict[str, Any]]) -> int:
        pending = self._retry + rows if self._retry else rows
        try:
            written = self._store.upsert_bars(pending)
        except Exception:
            self.stats["write_errors"] += 1
            # Closed bars are not re-sent by the provider: keep them for the
            # next flush instead of losing the minute.
            self._retry = (self._retry + rows)[:5000]
            logger.warning("bar recorder: persist failed (isolated)",
                           exc_info=True)
            return 0
        self._retry = []
        self.stats["bars_written"] += written
        return written
