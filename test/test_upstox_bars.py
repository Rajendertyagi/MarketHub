#!/usr/bin/env python3
"""Upstox marketOHLC -> bar store seam (ingestion)."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from brokers.upstox.feed_processing import (
    extract_market_ohlc_bars,
    process_binary_frame,
)
from brokers.upstox.proto import MarketDataFeed_pb2 as pb
from market.bars import BarRecorder

KEY = "NSE_EQ|INE002A01018"
METADATA = {KEY: ("NSE", "RELIANCE")}
RECEIVED = datetime(2026, 10, 27, 4, 0, 0, tzinfo=timezone.utc)


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def upsert_bars(self, rows):
        self.rows.extend(rows)
        return len(rows)


def _candle(interval, ts, o, h, lo, c, vol):
    return {
        "interval": interval, "ts": ts,
        "open": o, "high": h, "low": lo, "close": c, "vol": vol,
    }


def _frame(market_ohlc):
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    r.currentTs = 1_761_533_000_000
    ff = r.feeds[KEY].fullFeed.marketFF
    ff.ltpc.ltp = 101.0
    ff.ltpc.cp = 100.0
    for candle in market_ohlc:
        item = ff.marketOHLC.ohlc.add()
        for field, value in candle.items():
            setattr(item, field, value)
    return r.SerializeToString()


def test_extract_maps_wire_intervals_only() -> None:
    bars = extract_market_ohlc_bars({"marketOHLC": {"ohlc": [
        _candle("I1", 1_761_533_000_000, 100.0, 101.0, 99.5, 100.5, 250),
        _candle("1d", 1_761_532_200_000, 99.0, 102.0, 98.0, 100.5, 90_000),
        _candle("1m", 1_761_533_060_000, 1.0, 1.0, 1.0, 1.0, 1),
    ]}})
    assert [(b["interval_minutes"], b["ts"]) for b in bars] == [
        (1, 1_761_533_000_000), (1440, 1_761_532_200_000)]
    assert bars[0]["volume"] == 250.0


def test_extract_drops_incomplete_or_inconsistent_bars() -> None:
    bars = extract_market_ohlc_bars({"marketOHLC": {"ohlc": [
        _candle("I1", 0, 100.0, 101.0, 99.0, 100.0, 5),       # no ts
        _candle("I1", 1_761_533_000_000, 0.0, 101.0, 99.0, 100.0, 5),
        _candle("I1", 1_761_533_000_000, 100.0, 99.0, 101.0, 100.0, 5),
        "not-a-bar",
    ]}})
    assert bars == ()


def test_frame_outcome_carries_bars() -> None:
    frame = _frame([
        _candle("I1", 1_761_533_000_000, 100.0, 101.0, 99.5, 100.5, 250),
    ])
    result = process_binary_frame(
        frame, received_ts=RECEIVED, instrument_metadata=METADATA)
    assert len(result.instruments) == 1
    outcome = result.instruments[0]
    assert outcome.error is None
    assert outcome.patch is not None
    assert outcome.bars[0]["interval_minutes"] == 1
    assert outcome.bars[0]["close"] == 100.5


def test_recorder_writes_closed_bar_on_rollover_and_throttles_forming() -> None:
    store = _FakeStore()
    now = [0.0]
    rec = BarRecorder(store, provider="upstox", flush_seconds=10.0,
                      clock=lambda: now[0])

    bar = {"interval_minutes": 1, "ts": 60_000, "open": 100.0,
           "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
    rec.observe(KEY, [bar])
    assert store.rows == []          # forming bar not written yet

    now[0] = 5.0
    rec.observe(KEY, [{**bar, "close": 100.8}])
    assert store.rows == []          # still inside the flush window

    now[0] = 11.0
    rec.observe(KEY, [{**bar, "close": 100.9}])
    assert len(store.rows) == 1      # throttled forming write
    assert store.rows[-1]["close"] == 100.9
    assert store.rows[-1]["source"] == "ws"
    assert store.rows[-1]["provider"] == "upstox"

    # Rollover to the next minute persists the closed bar immediately.
    nxt = {**bar, "ts": 120_000, "open": 100.9, "close": 101.2,
           "high": 101.2, "volume": 3.0}
    now[0] = 12.0
    rec.observe(KEY, [nxt])
    assert store.rows[-1]["ts"] == 60_000
    assert store.rows[-1]["close"] == 100.9
    assert rec.stats["bars_closed"] == 1


def test_recorder_ignores_out_of_order_and_unchanged_ticks() -> None:
    store = _FakeStore()
    now = [0.0]
    rec = BarRecorder(store, provider="upstox", flush_seconds=10.0,
                      clock=lambda: now[0])
    bar = {"interval_minutes": 1, "ts": 120_000, "open": 100.0,
           "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
    rec.observe(KEY, [bar])
    for _ in range(50):
        rec.observe(KEY, [bar])       # identical ticks: never dirty again
    now[0] = 11.0
    rec.observe(KEY, [bar])
    assert len(store.rows) == 1       # first persist of the forming bar

    now[0] = 21.0
    rec.observe(KEY, [bar])
    assert len(store.rows) == 1       # unchanged bar is not rewritten

    rec.observe(KEY, [{**bar, "ts": 60_000}])   # older bar: dropped
    assert rec.stats["out_of_order"] == 1


def test_feed_forwards_decoded_bars_to_the_sink() -> None:
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    class _StubRest:
        async def authorize_market_feed(self, credentials):
            raise AssertionError("not used")

    class _Result:
        accepted = True
        changed = True
        stale = False

    class _StubService:
        async def apply_quote(self, patch):
            return _Result()

        async def apply_depth(self, depth):
            return _Result()

    class _Sink:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        def observe(self, instrument_key, bars):
            self.calls.append((instrument_key, tuple(bars)))

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": [KEY]},
        credentials=UpstoxCredentials(access_token="tok"),
        rest=_StubRest(),
        market_service=_StubService(),
        instrument_metadata=METADATA,
    )
    sink = _Sink()
    feed.set_bar_sink(sink)
    frame = _frame([
        _candle("I1", 1_761_533_000_000, 100.0, 101.0, 99.5, 100.5, 250),
        _candle("1d", 1_761_532_200_000, 99.0, 102.0, 98.0, 100.5, 90_000),
    ])
    asyncio.run(feed._process_frame(frame))

    assert len(sink.calls) == 1
    assert sink.calls[0][0] == KEY
    assert [b["interval_minutes"] for b in sink.calls[0][1]] == [1, 1440]


def test_feed_isolates_bar_sink_failures() -> None:
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    class _StubRest:
        async def authorize_market_feed(self, credentials):
            raise AssertionError("not used")

    class _Sink:
        def observe(self, instrument_key, bars):
            raise RuntimeError("boom")

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": [KEY]},
        credentials=UpstoxCredentials(access_token="tok"),
        rest=_StubRest(),
        instrument_metadata=METADATA,
    )
    feed.set_bar_sink(_Sink())
    frame = _frame([
        _candle("I1", 1_761_533_000_000, 100.0, 101.0, 99.5, 100.5, 250),
    ])
    # No MarketService: the frame is counted but bars are not consumed, so
    # drive the sink through a service that accepts the patch.
    class _Result:
        accepted = True
        changed = False
        stale = False

    class _StubService:
        async def apply_quote(self, patch):
            return _Result()

        async def apply_depth(self, depth):
            return _Result()

    feed._market_service = _StubService()
    asyncio.run(feed._process_frame(frame))   # must not raise
    assert feed._counters.get("bar_sink_errors") == 1
