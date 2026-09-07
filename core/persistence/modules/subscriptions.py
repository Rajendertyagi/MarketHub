"""Market-data subscription preferences (schema v17).

DB stores WHAT THE USER WANTS ENABLED — never canonical instrument
definitions (those live in code: app.market_indices MAJOR_INDICES + the
instruments catalog) and never concrete resolved contracts (the resolver
computes those at runtime from the catalog).

Two tables:

* ``md_subscriptions`` — direct/explicit subscriptions (indices, stocks).
  ``key`` is the canonical identity: for indices the canonical LABEL
  (e.g. ``NIFTY`` — the registry owns label→feed-key mapping); for stocks
  the concrete feed instrument key (e.g. ``NSE_EQ|INE002A01018``).
* ``md_derivative_rules`` — policy rules per underlying (futures and/or
  options with expiry count, ATM ± strikes, CE/PE). The resolver turns a
  rule into concrete catalog contracts, so expired contracts roll over
  automatically without any DB change.

Values here are preferences only — no secrets, no provider payloads.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 17


def create_subscription_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS md_subscriptions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            category         TEXT NOT NULL CHECK (category IN ('index', 'stock')),
            key              TEXT NOT NULL,
            label            TEXT NOT NULL DEFAULT '',
            enabled          INTEGER NOT NULL DEFAULT 1,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            UNIQUE (category, key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS md_derivative_rules (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            underlying        TEXT NOT NULL UNIQUE,
            futures_enabled   INTEGER NOT NULL DEFAULT 0,
            futures_count     INTEGER NOT NULL DEFAULT 1 CHECK (futures_count BETWEEN 1 AND 2),
            options_enabled   INTEGER NOT NULL DEFAULT 0,
            options_count     INTEGER NOT NULL DEFAULT 1 CHECK (options_count BETWEEN 1 AND 2),
            strikes_below     INTEGER NOT NULL DEFAULT 0 CHECK (strikes_below BETWEEN 0 AND 50),
            strikes_above     INTEGER NOT NULL DEFAULT 0 CHECK (strikes_above BETWEEN 0 AND 50),
            calls_enabled     INTEGER NOT NULL DEFAULT 1,
            puts_enabled      INTEGER NOT NULL DEFAULT 1,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL
        )
    """)


def migrate_v16_to_v17(conn: Any) -> None:
    """Add market-data subscription preference tables (idempotent)."""
    create_subscription_tables(conn)
    conn.execute("PRAGMA user_version = 17")
    conn.commit()
    logger.info("migrated v16→v17: added md_subscriptions + md_derivative_rules")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Direct subscriptions (indices / stocks)
# ---------------------------------------------------------------------------

def list_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM md_subscriptions ORDER BY category, key")]
    finally:
        conn.row_factory = None


def get_subscription(
    conn: sqlite3.Connection, *, category: str, key: str,
) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM md_subscriptions WHERE category = ? AND key = ?",
            (category, key)).fetchone()
        return dict(row) if row else None
    finally:
        conn.row_factory = None


def upsert_subscription(
    conn: sqlite3.Connection, *, category: str, key: str,
    label: str = "", enabled: bool = True,
) -> dict[str, Any]:
    """Idempotent insert-or-update. Canonical definitions stay in code."""
    now = _now()
    conn.execute(
        "INSERT INTO md_subscriptions (category, key, label, enabled, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (category, key) DO UPDATE SET "
        "label = excluded.label, enabled = excluded.enabled, "
        "updated_at = excluded.updated_at",
        (category, key, label, 1 if enabled else 0, now, now))
    conn.commit()
    return get_subscription(conn, category=category, key=key)


def delete_subscription(
    conn: sqlite3.Connection, *, category: str, key: str,
) -> bool:
    cur = conn.execute(
        "DELETE FROM md_subscriptions WHERE category = ? AND key = ?",
        (category, key))
    conn.commit()
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Derivative rules (policy per underlying)
# ---------------------------------------------------------------------------

def list_derivative_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM md_derivative_rules ORDER BY underlying")]
    finally:
        conn.row_factory = None


def get_derivative_rule(
    conn: sqlite3.Connection, *, underlying: str,
) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM md_derivative_rules WHERE underlying = ?",
            (underlying,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.row_factory = None


def upsert_derivative_rule(
    conn: sqlite3.Connection, *, underlying: str,
    futures_enabled: bool, futures_count: int,
    options_enabled: bool, options_count: int,
    strikes_below: int, strikes_above: int,
    calls_enabled: bool, puts_enabled: bool,
) -> dict[str, Any]:
    now = _now()
    conn.execute(
        "INSERT INTO md_derivative_rules (underlying, futures_enabled, "
        "futures_count, options_enabled, options_count, strikes_below, "
        "strikes_above, calls_enabled, puts_enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (underlying) DO UPDATE SET "
        "futures_enabled = excluded.futures_enabled, "
        "futures_count = excluded.futures_count, "
        "options_enabled = excluded.options_enabled, "
        "options_count = excluded.options_count, "
        "strikes_below = excluded.strikes_below, "
        "strikes_above = excluded.strikes_above, "
        "calls_enabled = excluded.calls_enabled, "
        "puts_enabled = excluded.puts_enabled, "
        "updated_at = excluded.updated_at",
        (underlying, 1 if futures_enabled else 0, futures_count,
         1 if options_enabled else 0, options_count, strikes_below,
         strikes_above, 1 if calls_enabled else 0, 1 if puts_enabled else 0,
         now, now))
    conn.commit()
    return get_derivative_rule(conn, underlying=underlying)


def delete_derivative_rule(
    conn: sqlite3.Connection, *, underlying: str,
) -> bool:
    cur = conn.execute(
        "DELETE FROM md_derivative_rules WHERE underlying = ?",
        (underlying,))
    conn.commit()
    return cur.rowcount > 0
