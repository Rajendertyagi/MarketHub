#!/usr/bin/env python3
"""OHLCV bar store tests (schema v24+; current schema v25)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.store import EventStore

P = "upstox"
K = "NSE_EQ|INE002A01018"


def _store():
    tmp = tempfile.TemporaryDirectory()
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


def _bar(ts: int, o: float, h: float, lo: float, c: float,
         volume: float | None = 10.0, source: str = "ws",
         interval: int = 1, key: str = K, provider: str = P) -> dict:
    return {
        "provider": provider, "instrument_key": key,
        "interval_minutes": interval, "ts": ts,
        "open": o, "high": h, "low": lo, "close": c,
        "volume": volume, "source": source,
    }


def test_schema_v24_has_market_bars() -> None:
    store, tmp = _store()
    try:
        con = sqlite3.connect(store.db_path)
        try:
            # Fresh DB is at current schema; market_bars was introduced in v24.
            assert con.execute("PRAGMA user_version").fetchone()[0] == 25
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert "market_bars" in names
        finally:
            con.close()
    finally:
        tmp.cleanup()


def test_v23_database_migrates_without_data_loss() -> None:
    store, tmp = _store()
    try:
        con = sqlite3.connect(store.db_path)
        con.execute(
            "INSERT INTO x_tweets (id, fetched_at) VALUES ('t1', 'now')")
        con.execute("DROP TABLE market_bars")
        con.execute("PRAGMA user_version = 23")
        con.commit()
        con.close()

        reopened = EventStore(store.db_path)
        con = sqlite3.connect(reopened.db_path)
        try:
            # v23 migrates through v24 (market_bars) to current schema.
            assert con.execute("PRAGMA user_version").fetchone()[0] == 25
            names = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert "market_bars" in names
            assert con.execute(
                "SELECT COUNT(*) FROM x_tweets WHERE id = 't1'"
            ).fetchone()[0] == 1
        finally:
            con.close()
    finally:
        tmp.cleanup()


def test_upsert_and_ordered_range_query() -> None:
    store, tmp = _store()
    try:
        assert store.upsert_bars([
            _bar(120_000, 102, 103, 101, 102.5),
            _bar(60_000, 100, 101, 99, 100.5),
            _bar(180_000, 103, 104, 102, 103.5),
        ]) == 3
        assert store.upsert_bars([
            _bar(240_000, 104, 105, 103, 104.5, key="NSE_EQ|OTHER"),
        ]) == 1

        bars = store.query_bars(
            provider=P, instrument_key=K, interval_minutes=1)
        assert [b["ts"] for b in bars] == [60_000, 120_000, 180_000]

        window = store.query_bars(
            provider=P, instrument_key=K, interval_minutes=1,
            from_ts=90_000, to_ts=180_000)
        assert [b["ts"] for b in window] == [120_000, 180_000]

        latest = store.query_bars(
            provider=P, instrument_key=K, interval_minutes=1, limit=2)
        assert [b["ts"] for b in latest] == [120_000, 180_000]
    finally:
        tmp.cleanup()


def test_live_row_beats_backfill_and_backfill_refreshes_itself() -> None:
    store, tmp = _store()
    try:
        store.upsert_bars([_bar(60_000, 100, 101, 99, 100.5, source="ws")])
        # stale REST row must not overwrite the live bar
        store.upsert_bars([
            _bar(60_000, 1, 1, 1, 1, volume=1, source="rest")])
        bar = store.query_bars(
            provider=P, instrument_key=K, interval_minutes=1)[0]
        assert (bar["close"], bar["source"]) == (100.5, "ws")

        # a later REST row may fill missing volume on the live row
        store.upsert_bars([_bar(120_000, 100, 101, 99, 100.5,
                                volume=None, source="ws")])
        store.upsert_bars([_bar(120_000, 100, 101, 99, 100.5,
                                volume=42, source="rest")])
        filled = store.query_bars(
            provider=P, instrument_key=K, interval_minutes=1)[1]
        assert filled["volume"] == 42

        # REST replacing REST is a normal refresh
        store.upsert_bars([_bar(180_000, 100, 101, 99, 100.5, source="rest")])
        store.upsert_bars([_bar(180_000, 100, 101, 99, 101.0, source="rest")])
        refreshed = store.query_bars(
            provider=P, instrument_key=K, interval_minutes=1)[2]
        assert refreshed["close"] == 101.0
    finally:
        tmp.cleanup()


def test_invalid_rows_are_rejected_without_poisoning_the_batch() -> None:
    store, tmp = _store()
    try:
        bad = _bar(60_000, 100, 99, 101, 100.5)  # high < low
        assert store.upsert_bars([bad, _bar(120_000, 100, 101, 99, 100.5)]) == 1
        assert store.bar_bounds(
            provider=P, instrument_key=K, interval_minutes=1) == (
                120_000, 120_000, 1)
        # unknown source / missing fields are dropped, not raised
        assert store.upsert_bars([{"provider": P}]) == 0
    finally:
        tmp.cleanup()


def test_bounds_and_prune_scoped_to_intraday_base() -> None:
    store, tmp = _store()
    try:
        store.upsert_bars([
            _bar(60_000, 100, 101, 99, 100.5, interval=1),
            _bar(120_000, 100, 101, 99, 100.5, interval=1),
            _bar(86_400_000, 100, 101, 99, 100.5, interval=1440),
        ])
        assert store.bar_bounds(
            provider=P, instrument_key=K, interval_minutes=1) == (
                60_000, 120_000, 2)

        removed = store.prune_bars(cutoff_ts=120_000, interval_minutes=1)
        assert removed == 1
        assert store.bar_bounds(
            provider=P, instrument_key=K, interval_minutes=1)[2] == 1
        # daily rows survive intraday retention
        assert store.bar_bounds(
            provider=P, instrument_key=K, interval_minutes=1440)[2] == 1
    finally:
        tmp.cleanup()
