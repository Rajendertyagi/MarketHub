"""OHLCV bar persistence (schema v24).

Stores the finest bars a provider actually delivers — 1-minute intraday and
daily — keyed by provider instrument. Coarser intraday timeframes are derived
by aggregation at read time (never stored twice), so a chart timeframe cannot
silently disagree with its own base series.

Contract:
- ``ts`` is the bar OPEN time in epoch milliseconds (UTC).
- ``interval_minutes`` is the bar width in minutes (1 = intraday base,
  1440 = daily session bar).
- ``source`` records provenance: ``ws`` (live feed) or ``rest`` (backfill).
  On conflict the live row wins, except that a REST row may replace another
  REST row (backfill refreshes itself) and may fill a live row's missing
  volume (index feeds carry no volume).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SOURCE_WS = "ws"
SOURCE_REST = "rest"

BAR_COLUMNS = (
    "provider", "instrument_key", "interval_minutes", "ts",
    "open", "high", "low", "close", "volume", "oi", "source", "updated_at",
)


def create_market_bars_table(conn: sqlite3.Connection) -> None:
    """Create the market_bars table (idempotent).

    WITHOUT ROWID keeps rows clustered by the primary key, so a chart range
    query reads one contiguous key range instead of chasing a secondary index.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_bars (
            provider         TEXT NOT NULL,
            instrument_key   TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL,
            ts               INTEGER NOT NULL,
            open             REAL NOT NULL,
            high             REAL NOT NULL,
            low              REAL NOT NULL,
            close            REAL NOT NULL,
            volume           REAL,
            oi               REAL,
            source           TEXT NOT NULL CHECK (source IN ('ws', 'rest')),
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (provider, instrument_key, interval_minutes, ts)
        ) WITHOUT ROWID
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_bars_source_ts
        ON market_bars(source, ts)
    """)


def migrate_v23_to_v24(conn: Any) -> None:
    """Add the OHLCV bar store. Existing tables and rows are untouched."""
    create_market_bars_table(conn)
    conn.execute("PRAGMA user_version = 24")
    conn.commit()
    logger.info("migrated v23->v24: added market_bars")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_row(row: dict[str, Any], now: str) -> tuple[Any, ...] | None:
    """Map one caller row to the column tuple; None when unusable."""
    provider = str(row.get("provider") or "").strip()
    instrument_key = str(row.get("instrument_key") or "").strip()
    source = str(row.get("source") or "").strip()
    if not provider or not instrument_key or source not in (SOURCE_WS, SOURCE_REST):
        return None
    try:
        interval_minutes = int(row["interval_minutes"])
        ts = int(row["ts"])
        open_ = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if interval_minutes <= 0 or ts <= 0:
        return None
    if high < low or open_ <= 0 or close <= 0 or low <= 0:
        return None
    volume = row.get("volume")
    volume = float(volume) if volume is not None else None
    oi = row.get("oi")
    oi = float(oi) if oi is not None else None
    return (provider, instrument_key, interval_minutes, ts,
            open_, high, low, close, volume, oi, source, now)


def upsert_bars(conn: Any, rows: list[dict[str, Any]]) -> int:
    """Batched upsert of bar facts (one transaction per call).

    Safe under concurrent writers: every row is a single atomic UPSERT whose
    conflict rule is deterministic (live beats backfill), so two flushes that
    race on the same bar converge on the same value regardless of order.
    """
    if not rows:
        return 0
    now = _now()
    payload = [p for p in (_normalize_row(r, now) for r in rows) if p is not None]
    if not payload:
        return 0
    conn.executemany(
        "INSERT INTO market_bars "
        "(provider, instrument_key, interval_minutes, ts, open, high, low, "
        " close, volume, oi, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (provider, instrument_key, interval_minutes, ts) "
        "DO UPDATE SET "
        "open = excluded.open, high = excluded.high, low = excluded.low, "
        "close = excluded.close, volume = excluded.volume, oi = excluded.oi, "
        "source = excluded.source, updated_at = excluded.updated_at "
        "WHERE excluded.source = 'ws' "
        "   OR market_bars.source = 'rest' "
        "   OR (excluded.volume IS NOT NULL AND market_bars.volume IS NULL)",
        payload,
    )
    conn.commit()
    return len(payload)


def query_bars(
    conn: Any,
    *,
    provider: str,
    instrument_key: str,
    interval_minutes: int,
    from_ts: int | None = None,
    to_ts: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return bars in ascending ``ts`` order.

    ``from_ts``/``to_ts`` are inclusive epoch-ms bounds. ``limit`` selects the
    most recent N bars inside those bounds (charting's "latest N" contract),
    still returned ascending.
    """
    sql = [
        "SELECT ts, open, high, low, close, volume, oi, source",
        "FROM market_bars",
        "WHERE provider = ? AND instrument_key = ? AND interval_minutes = ?",
    ]
    params: list[Any] = [provider, instrument_key, int(interval_minutes)]
    if from_ts is not None:
        sql.append("AND ts >= ?")
        params.append(int(from_ts))
    if to_ts is not None:
        sql.append("AND ts <= ?")
        params.append(int(to_ts))
    if limit is not None and limit > 0:
        sql.append("ORDER BY ts DESC LIMIT ?")
        params.append(int(limit))
        rows = conn.execute(" ".join(sql), params).fetchall()
        rows.reverse()
    else:
        sql.append("ORDER BY ts ASC")
        rows = conn.execute(" ".join(sql), params).fetchall()
    return [
        {
            "ts": r[0], "open": r[1], "high": r[2], "low": r[3],
            "close": r[4], "volume": r[5], "oi": r[6], "source": r[7],
        }
        for r in rows
    ]


def bar_bounds(
    conn: Any,
    *,
    provider: str,
    instrument_key: str,
    interval_minutes: int,
) -> tuple[int | None, int | None, int]:
    """Return ``(earliest_ts, latest_ts, count)`` for one series."""
    row = conn.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM market_bars "
        "WHERE provider = ? AND instrument_key = ? AND interval_minutes = ?",
        (provider, instrument_key, int(interval_minutes))).fetchone()
    if row is None:
        return (None, None, 0)
    return (row[0], row[1], int(row[2] or 0))


def prune_bars(conn: Any, *, cutoff_ts: int,
               interval_minutes: int | None = None) -> int:
    """Delete bars whose open time is strictly before ``cutoff_ts``.

    Retention is applied to the intraday base only; daily rows are tiny and
    stay for the life of the database.
    """
    if interval_minutes is None:
        cur = conn.execute(
            "DELETE FROM market_bars WHERE ts < ?", (int(cutoff_ts),))
    else:
        cur = conn.execute(
            "DELETE FROM market_bars WHERE interval_minutes = ? AND ts < ?",
            (int(interval_minutes), int(cutoff_ts)))
    conn.commit()
    return cur.rowcount


def prune_intraday_sessions(
    conn: Any, *, sessions: int = 30, interval_minutes: int = 1,
) -> int:
    """Keep the most recent ``sessions`` IST trading dates of 1-minute bars.

    The cutoff is the 30th-newest session PRESENT IN THE DATA (not a calendar
    estimate), so weekends and holidays never delete a live session.
    """
    if sessions <= 0:
        return 0
    row = conn.execute(
        "SELECT date(ts / 1000, 'unixepoch', '+330 minutes') AS d "
        "FROM market_bars WHERE interval_minutes = ? "
        "GROUP BY d ORDER BY d DESC LIMIT 1 OFFSET ?",
        (int(interval_minutes), int(sessions) - 1)).fetchone()
    if row is None or row[0] is None:
        return 0
    cutoff = conn.execute(
        "SELECT CAST(strftime('%s', ? || ' 00:00:00') AS INTEGER) * 1000 "
        "       - 19800000",
        (row[0],)).fetchone()[0]
    cur = conn.execute(
        "DELETE FROM market_bars WHERE interval_minutes = ? AND ts < ?",
        (int(interval_minutes), int(cutoff)))
    conn.commit()
    return cur.rowcount
