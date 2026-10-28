"""Previous-session open-interest persistence (schema v20).

Provider-agnostic baseline for OI change / OI%. Upstox streams only current
OI (verified wire format); Fyers streams previous OI natively. When the
provider does not supply previous OI, the most recent prior-session OI
recorded here is the fallback — never fabricated.

Canonical contract identity: ``(exchange, underlying, expiry,
instrument_type)``. Session date is the IST calendar date (Asia/Kolkata,
fixed +05:30 — India has no DST, so no tzdata dependency).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# IST = UTC+05:30, fixed (no DST).
IST = timezone(timedelta(hours=5, minutes=30))

FUTURE = "FUTURE"


def ist_session_date(ts: datetime | None = None) -> str:
    """IST calendar date (YYYY-MM-DD) for a moment (default: now UTC)."""
    moment = ts or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).date().isoformat()


def create_previous_oi_table(conn: sqlite3.Connection) -> None:
    """Create the futures OI snapshot table (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS futures_oi_snapshot (
            exchange         TEXT NOT NULL,
            underlying       TEXT NOT NULL,
            expiry           TEXT NOT NULL,
            instrument_type  TEXT NOT NULL DEFAULT 'FUTURE',
            session_date     TEXT NOT NULL,
            oi               REAL NOT NULL,
            provider         TEXT,
            instrument_token TEXT,
            captured_at      TEXT NOT NULL,
            PRIMARY KEY (exchange, underlying, expiry, instrument_type,
                         session_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_futures_oi_session
        ON futures_oi_snapshot(session_date)
    """)


def migrate_v19_to_v20(conn: Any) -> None:
    """Add the previous-session OI snapshot table (idempotent)."""
    create_previous_oi_table(conn)
    conn.execute("PRAGMA user_version = 20")
    conn.commit()
    logger.info("migrated v19→v20: added futures_oi_snapshot")


def get_previous_oi(
    conn: sqlite3.Connection, *, exchange: str, underlying: str,
    expiry: str, instrument_type: str = FUTURE,
    before_session: str | None = None,
) -> float | None:
    """Most recent prior-session OI (strictly before ``before_session``).

    Returns None when no valid baseline exists (new contract / no history).
    """
    session = before_session or ist_session_date()
    row = conn.execute(
        "SELECT oi FROM futures_oi_snapshot "
        "WHERE exchange = ? AND underlying = ? AND expiry = ? "
        "AND instrument_type = ? AND session_date < ? "
        "ORDER BY session_date DESC LIMIT 1",
        (exchange, underlying, expiry, instrument_type, session)).fetchone()
    return float(row[0]) if row is not None else None


def upsert_oi_snapshots(conn: sqlite3.Connection,
                        rows: list[dict[str, Any]]) -> int:
    """Batched upsert (last-write-wins per contract + session date).

    Called on a bounded cadence — NEVER per market-data tick.
    """
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    payload = [
        (str(r["exchange"]), str(r["underlying"]), str(r["expiry"]),
         str(r.get("instrument_type", FUTURE)), str(r["session_date"]),
         float(r["oi"]), r.get("provider"), r.get("instrument_token"), now)
        for r in rows if r.get("oi") is not None
    ]
    if not payload:
        return 0
    conn.executemany(
        "INSERT INTO futures_oi_snapshot "
        "(exchange, underlying, expiry, instrument_type, session_date, oi, "
        " provider, instrument_token, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (exchange, underlying, expiry, instrument_type, "
        "session_date) DO UPDATE SET "
        "oi = excluded.oi, provider = excluded.provider, "
        "instrument_token = excluded.instrument_token, "
        "captured_at = excluded.captured_at",
        payload)
    conn.commit()
    return len(payload)


def prune_previous_oi(conn: sqlite3.Connection,
                      *, today: str | None = None) -> int:
    """Delete snapshots for expired contracts (``expiry < today``)."""
    day = today or ist_session_date()
    cur = conn.execute(
        "DELETE FROM futures_oi_snapshot WHERE expiry < ?", (day,))
    conn.commit()
    return cur.rowcount
