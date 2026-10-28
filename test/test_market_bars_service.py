#!/usr/bin/env python3
"""Bar service: backfill, stored reads, live forming bar, retention."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.market_bars import BarService, BarsUnavailable
from core.persistence.store import EventStore
from market.bars import IST, BarRecorder

KEY = "NSE_EQ|INE002A01018"
NOW = int(datetime(2026, 10, 27, 10, 0, tzinfo=IST).timestamp() * 1000)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _store():
    tmp = tempfile.TemporaryDirectory()
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


class _Rest:
    def __init__(self, payload=None) -> None:
        self.urls: list[str] = []
        self.payload = payload or {"status": "ok", "data": {"candles": []}}

    async def authenticated_request(self, *, method, url, access_token):
        assert method == "GET" and access_token == "tok"
        self.urls.append(url)
        if callable(self.payload):
            return self.payload(url)
        return self.payload


class _Creds:
    access_token = "tok"


def _service(store, rest=None, live=None, now=NOW):
    return BarService(
        store,
        auth_context_fn=(lambda: (rest, _Creds())) if rest is not None else None,
        live=live,
        now_ms=lambda: now,
        clock=lambda: 0.0,
    )


def test_cache_miss_backfills_provider_interval_and_stores_rows() -> None:
    store, tmp = _store()
    try:
        ist = IST
        candles = [
            ["2026-10-27T09:15:00+05:30", 100.0, 101.0, 99.5, 100.5, 500, 0],
            ["2026-10-27T09:20:00+05:30", 100.5, 102.0, 100.0, 101.5, 700, 0],
        ]
        rest = _Rest({"status": "ok", "data": {"candles": candles}})
        svc = _service(store, rest=rest)
        payload = asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=5, limit=10, provider="upstox"))

        # The window is inside today's session, which only the intraday
        # endpoint publishes; it is still read at 5-minute granularity.
        assert rest.urls and rest.urls[0].endswith("/minutes/5")
        bars = payload["bars"]
        assert [b["ts"] for b in bars] == [
            _ms(datetime(2026, 10, 27, 9, 15, tzinfo=ist)),
            _ms(datetime(2026, 10, 27, 9, 20, tzinfo=ist)),
        ]
        assert bars[-1]["close"] == 101.5
        assert bars[-1]["volume"] == 700.0
        # persisted at the provider's own granularity
        assert store.bar_bounds(
            provider="upstox", instrument_key=KEY, interval_minutes=5)[2] == 2
    finally:
        tmp.cleanup()


def test_second_request_is_served_from_store_within_cooldown() -> None:
    store, tmp = _store()
    try:
        candles = [["2026-10-27T09:55:00+05:30", 100.0, 101.0, 99.5, 100.5, 5, 0]]
        rest = _Rest({"status": "ok", "data": {"candles": candles}})
        svc = _service(store, rest=rest)
        asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=5, limit=10, provider="upstox"))
        calls_after_first = len(rest.urls)
        asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=5, limit=10, provider="upstox"))
        # The tail is fresh (09:55 vs now 10:00) and the leading edge is
        # covered; no second provider call.
        assert len(rest.urls) == calls_after_first
    finally:
        tmp.cleanup()


def test_narrow_empty_poll_does_not_block_wider_history() -> None:
    store, tmp = _store()
    try:
        rest = _Rest()                     # the provider returns no candles
        svc = _service(store, rest=rest)
        asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=15, limit=2, provider="upstox"))
        narrow = len(rest.urls)
        assert narrow >= 1

        asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=15, limit=500, provider="upstox"))
        wide = rest.urls[narrow:]
        # The clock is frozen, so only the wider window can unlock this read:
        # it reaches further back than the poll-sized attempt ever asked for.
        assert any("/intraday/" not in url for url in wide)
    finally:
        tmp.cleanup()


def test_current_session_is_read_from_the_intraday_endpoint() -> None:
    store, tmp = _store()
    try:
        ist = IST
        now = _ms(datetime(2026, 10, 27, 15, 30, tzinfo=ist))
        prior = [["2026-10-26T15:15:00+05:30", 100.0, 101.0, 99.0, 100.5, 50, 0]]
        session = [
            ["2026-10-27T09:15:00+05:30", 101.0, 102.0, 100.0, 101.5, 20, 0],
            ["2026-10-27T09:30:00+05:30", 101.5, 103.0, 101.0, 102.5, 30, 0],
        ]

        def payload_for(url: str) -> dict:
            candles = session if "/intraday/" in url else prior
            return {"status": "ok", "data": {"candles": candles}}

        rest = _Rest(payload_for)
        svc = _service(store, rest=rest, now=now)
        payload = asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=15, limit=200, provider="upstox"))

        assert any("/intraday/" not in url for url in rest.urls)   # history
        assert [url for url in rest.urls if "/intraday/" in url] == [
            "https://api.upstox.com/v3/historical-candle/intraday/"
            "NSE_EQ%7CINE002A01018/minutes/15"]
        bars = payload["bars"]
        assert [b["ts"] for b in bars] == [
            _ms(datetime(2026, 10, 26, 15, 15, tzinfo=ist)),
            _ms(datetime(2026, 10, 27, 9, 15, tzinfo=ist)),
            _ms(datetime(2026, 10, 27, 9, 30, tzinfo=ist)),
        ]
        assert bars[-1]["close"] == 102.5
    finally:
        tmp.cleanup()


def test_poll_within_the_current_session_skips_the_historical_endpoint() -> None:
    store, tmp = _store()
    try:
        ist = IST
        now = _ms(datetime(2026, 10, 27, 16, 0, tzinfo=ist))
        five_past = _ms(datetime(2026, 10, 27, 15, 25, tzinfo=ist))
        store.upsert_bars([{
            "provider": "upstox", "instrument_key": KEY,
            "interval_minutes": 15, "ts": five_past,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 500.0, "source": "rest",
        }])
        session = [["2026-10-27T15:25:00+05:30", 100.0, 101.0, 99.0, 100.5, 500, 0]]
        rest = _Rest({"status": "ok", "data": {"candles": session}})
        svc = _service(store, rest=rest, now=now)
        asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=15, limit=2, provider="upstox"))

        # The stored tail is stale but the whole gap is today, which the
        # historical endpoint cannot serve: one intraday call, nothing else.
        assert rest.urls == [
            "https://api.upstox.com/v3/historical-candle/intraday/"
            "NSE_EQ%7CINE002A01018/minutes/15"]
    finally:
        tmp.cleanup()


def test_requested_bar_count_is_not_capped_by_the_lookback_window() -> None:
    store, tmp = _store()
    try:
        ist = IST
        now = _ms(datetime(2026, 10, 27, 16, 0, tzinfo=ist))
        candles: list[list] = []
        day = datetime(2026, 9, 28, 9, 15, tzinfo=ist)
        while len(candles) < 500:
            if day.weekday() < 5:
                for step in range(25):
                    at = day + timedelta(minutes=15 * step)
                    candles.append([at.isoformat(), 100.0, 101.0, 99.0, 100.5, 10, 0])
            day += timedelta(days=1)
        rest = _Rest({"status": "ok", "data": {"candles": candles}})
        svc = _service(store, rest=rest, now=now)
        payload = asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=15, limit=500, provider="upstox"))
        assert len(payload["bars"]) == 500
    finally:
        tmp.cleanup()


def test_live_forming_minute_updates_bucket_without_double_counting() -> None:
    store, tmp = _store()
    try:
        ist = IST
        five = _ms(datetime(2026, 10, 27, 9, 55, tzinfo=ist))
        store.upsert_bars([{
            "provider": "upstox", "instrument_key": KEY,
            "interval_minutes": 5, "ts": five,
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 300.0, "source": "rest",
        }])
        # Persisted snapshot of the forming minute (as the recorder flushes).
        for minute, close, vol in ((55, 100.5, 100.0), (56, 100.8, 50.0)):
            store.upsert_bars([{
                "provider": "upstox", "instrument_key": KEY,
                "interval_minutes": 1,
                "ts": _ms(datetime(2026, 10, 27, 9, minute, tzinfo=ist)),
                "open": 100.0, "high": close, "low": 99.5, "close": close,
                "volume": vol, "source": "ws",
            }])

        recorder = BarRecorder(store, provider="upstox",
                               clock=lambda: 0.0, flush_seconds=1e9)
        forming = {
            "interval_minutes": 1,
            "ts": _ms(datetime(2026, 10, 27, 9, 56, tzinfo=ist)),
            "open": 100.5, "high": 101.4, "low": 100.2, "close": 101.2,
            "volume": 60.0,
        }
        recorder.observe(KEY, [forming])

        svc = _service(store, live=recorder)
        payload = asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=5, limit=2, provider="upstox"))
        bar = payload["bars"][-1]
        assert bar["ts"] == five
        assert bar["close"] == 101.2
        assert bar["high"] == 101.4
        assert bar["low"] == 99.5
        # 100 (09:55) + 60 (forming 09:56); the stored forming snapshot is
        # replaced, never added.
        assert bar["volume"] == 160.0
        # Merged bars still carry the field, with no single provenance.
        assert bar["source"] is None
    finally:
        tmp.cleanup()


def test_weekly_aggregates_daily_with_live_day() -> None:
    store, tmp = _store()
    try:
        ist = IST
        # Friday 23 Oct 2026: the live day closes the Mon-Fri week.
        now = _ms(datetime(2026, 10, 23, 10, 0, tzinfo=ist))
        daily = []
        for day, close in ((19, 100.0), (20, 102.0), (21, 101.0), (22, 103.0)):
            daily.append({
                "provider": "upstox", "instrument_key": KEY,
                "interval_minutes": 1440,
                "ts": _ms(datetime(2026, 10, day, tzinfo=ist)),
                "open": close - 1, "high": close + 1, "low": close - 2,
                "close": close, "volume": 10.0, "source": "rest",
            })
        store.upsert_bars(daily)

        recorder = BarRecorder(store, provider="upstox",
                               clock=lambda: 0.0, flush_seconds=1e9)
        recorder.observe(KEY, [{
            "interval_minutes": 1440,
            "ts": _ms(datetime(2026, 10, 23, tzinfo=ist)),
            "open": 103.0, "high": 106.0, "low": 102.5, "close": 105.0,
            "volume": 11.0,
        }])

        svc = _service(store, live=recorder, now=now)
        payload = asyncio.run(svc.get_bars(
            instrument_key=KEY, timeframe=10080, limit=2, provider="upstox"))
        weeks = payload["bars"]
        assert len(weeks) == 1
        assert weeks[0]["open"] == 99.0          # first session of the week
        assert weeks[0]["high"] == 106.0         # includes the live day
        assert weeks[0]["close"] == 105.0
        assert weeks[0]["volume"] == 51.0        # 10*4 + 11
        assert weeks[0]["source"] is None        # derived roll-up
    finally:
        tmp.cleanup()


def test_unauthenticated_backfill_is_a_client_error() -> None:
    store, tmp = _store()
    try:
        svc = _service(store, rest=None)
        try:
            asyncio.run(svc.get_bars(
                instrument_key=KEY, timeframe=5, limit=10, provider="upstox"))
        except BarsUnavailable as exc:
            assert "authenticated" in str(exc)
        else:
            raise AssertionError("expected BarsUnavailable")
    finally:
        tmp.cleanup()


def test_unsupported_timeframe_and_provider_timeframe_are_explicit() -> None:
    store, tmp = _store()
    try:
        svc = _service(store)
        for tf in ("3m", "1h", None):
            try:
                asyncio.run(svc.get_bars(
                    instrument_key=KEY, timeframe=tf, provider="upstox"))
            except BarsUnavailable:
                pass
            else:
                raise AssertionError(f"expected BarsUnavailable for {tf!r}")
        try:
            asyncio.run(svc.get_bars(
                instrument_key=KEY, timeframe=240, provider="fyers"))
        except BarsUnavailable as exc:
            assert "fyers" in str(exc)
        else:
            raise AssertionError("expected BarsUnavailable for fyers 240")
    finally:
        tmp.cleanup()


def test_intraday_retention_keeps_newest_sessions_only() -> None:
    store, tmp = _store()
    try:
        ist = IST
        rows = []
        first = datetime(2026, 9, 1, 10, 0, tzinfo=ist)
        for day in range(1, 36):
            rows.append({
                "provider": "upstox", "instrument_key": KEY,
                "interval_minutes": 1,
                "ts": _ms(first + timedelta(days=day - 1)),
                "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                "volume": 1.0, "source": "ws",
            })
        store.upsert_bars(rows)
        store.upsert_bars([{
            "provider": "upstox", "instrument_key": KEY,
            "interval_minutes": 1440,
            "ts": _ms(datetime(2026, 9, 1, tzinfo=ist)),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "volume": 1.0, "source": "rest",
        }])
        removed = store.prune_intraday_bars(sessions=30)
        assert removed == 5
        assert store.bar_bounds(
            provider="upstox", instrument_key=KEY, interval_minutes=1)[2] == 30
        # daily rows are never touched by intraday retention
        assert store.bar_bounds(
            provider="upstox", instrument_key=KEY, interval_minutes=1440)[2] == 1
    finally:
        tmp.cleanup()


def test_unknown_provider_is_rejected() -> None:
    store, tmp = _store()
    try:
        svc = _service(store)
        try:
            asyncio.run(svc.get_bars(
                instrument_key=KEY, timeframe=5, provider="binance"))
        except BarsUnavailable as exc:
            assert "unknown provider" in str(exc)
        else:
            raise AssertionError("expected BarsUnavailable")
    finally:
        tmp.cleanup()
