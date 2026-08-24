"""
Product tables: instruments catalog, watchlists, alerts (schema v11).

Generic, provider-neutral. Values here are canonical metadata only —
never raw provider payloads, never secrets.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def create_product_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            provider         TEXT NOT NULL,
            instrument_token TEXT NOT NULL,
            exchange         TEXT NOT NULL,
            tradingsymbol    TEXT NOT NULL,
            name             TEXT,
            instrument_type  TEXT,
            segment          TEXT,
            expiry           TEXT,
            strike           REAL,
            option_type      TEXT,
            lot_size         INTEGER,
            tick_size        REAL,
            isin             TEXT,
            underlying       TEXT,
            provider_symbol  TEXT,
            updated_at       TEXT NOT NULL,
            PRIMARY KEY (provider, instrument_token)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instr_symbol
        ON instruments(tradingsymbol)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instr_name
        ON instruments(name)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instr_lookup
        ON instruments(exchange, instrument_type, expiry, underlying)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id     INTEGER NOT NULL REFERENCES watchlists(id)
                             ON DELETE CASCADE,
            exchange         TEXT NOT NULL,
            instrument_token TEXT NOT NULL,
            tradingsymbol    TEXT NOT NULL,
            position         INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT NOT NULL,
            UNIQUE (watchlist_id, exchange, instrument_token)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange    TEXT NOT NULL,
            instrument_token TEXT NOT NULL,
            tradingsymbol    TEXT NOT NULL,
            field       TEXT NOT NULL,
            operator    TEXT NOT NULL,
            threshold   REAL NOT NULL,
            enabled     INTEGER NOT NULL DEFAULT 1,
            state       TEXT NOT NULL DEFAULT 'inactive',
            triggered_at TEXT,
            created_at  TEXT NOT NULL
        )
    """)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Instruments catalog
# ---------------------------------------------------------------------------

_INSTRUMENT_COLUMNS = (
    "provider", "instrument_token", "exchange", "tradingsymbol", "name",
    "instrument_type", "segment", "expiry", "strike", "option_type",
    "lot_size", "tick_size", "isin", "underlying", "provider_symbol",
)


def replace_provider_instruments(
    conn: sqlite3.Connection,
    provider: str,
    records: list[dict[str, Any]],
) -> int:
    """Transactionally replace ALL rows for one provider (stale removal).

    Records missing required identity fields are skipped (counted as
    malformed by the caller via return-value comparison).
    """
    now = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM instruments WHERE provider = ?", (provider,))
        rows = []
        for r in records:
            if not r.get("instrument_token") or not r.get("exchange") \
                    or not r.get("tradingsymbol"):
                continue
            rows.append((
                provider, r["instrument_token"], r["exchange"],
                r["tradingsymbol"], r.get("name"), r.get("instrument_type"),
                r.get("segment"), r.get("expiry"), r.get("strike"),
                r.get("option_type"), r.get("lot_size"), r.get("tick_size"),
                r.get("isin"), r.get("underlying"),
                r.get("provider_symbol"), now,
            ))
        conn.executemany(
            f"INSERT INTO instruments ({', '.join(_INSTRUMENT_COLUMNS)}, "
            "updated_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise


def search_instruments(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    exchange: str | None = None,
    instrument_type: str | None = None,
    provider: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    sql = f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments WHERE 1=1"
    args: list[Any] = []
    if q:
        sql += " AND (tradingsymbol LIKE ? OR name LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if exchange:
        sql += " AND exchange = ?"
        args.append(exchange)
    if instrument_type:
        sql += " AND instrument_type = ?"
        args.append(instrument_type)
    if provider:
        sql += " AND provider = ?"
        args.append(provider)
    sql += " ORDER BY tradingsymbol LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def get_instrument(
    conn: sqlite3.Connection,
    provider: str,
    instrument_token: str,
) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments "
            "WHERE provider = ? AND instrument_token = ?",
            (provider, instrument_token),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.row_factory = None


def instruments_sync_state(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT provider, COUNT(*) AS instruments, MAX(updated_at) "
            "AS last_sync FROM instruments GROUP BY provider")]
    finally:
        conn.row_factory = None


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------


def list_watchlists(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, created_at FROM watchlists ORDER BY id")]
    finally:
        conn.row_factory = None


def create_watchlist(conn: sqlite3.Connection, name: str) -> dict[str, Any]:
    now = _now()
    cur = conn.execute(
        "INSERT INTO watchlists (name, created_at) VALUES (?, ?)",
        (name, now))
    conn.commit()
    return {"id": cur.lastrowid, "name": name, "created_at": now}


def rename_watchlist(conn: sqlite3.Connection, wl_id: int, name: str) -> bool:
    cur = conn.execute("UPDATE watchlists SET name = ? WHERE id = ?",
                       (name, wl_id))
    conn.commit()
    return cur.rowcount > 0


def delete_watchlist(conn: sqlite3.Connection, wl_id: int) -> bool:
    conn.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?",
                 (wl_id,))
    cur = conn.execute("DELETE FROM watchlists WHERE id = ?", (wl_id,))
    conn.commit()
    return cur.rowcount > 0


def list_watchlist_items(
    conn: sqlite3.Connection, wl_id: int,
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, exchange, instrument_token, tradingsymbol, position "
            "FROM watchlist_items WHERE watchlist_id = ? "
            "ORDER BY position, id", (wl_id,))]
    finally:
        conn.row_factory = None


def add_watchlist_item(
    conn: sqlite3.Connection, wl_id: int,
    *, exchange: str, instrument_token: str, tradingsymbol: str,
) -> dict[str, Any] | None:
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM watchlist_items "
        "WHERE watchlist_id = ?", (wl_id,)).fetchone()[0]
    try:
        cur = conn.execute(
            "INSERT INTO watchlist_items (watchlist_id, exchange, "
            "instrument_token, tradingsymbol, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (wl_id, exchange, instrument_token, tradingsymbol, pos, _now()))
        conn.commit()
    except sqlite3.IntegrityError:
        return None  # duplicate
    return {"id": cur.lastrowid, "exchange": exchange,
            "instrument_token": instrument_token,
            "tradingsymbol": tradingsymbol, "position": pos}


def remove_watchlist_item(conn: sqlite3.Connection, item_id: int) -> bool:
    cur = conn.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
    conn.commit()
    return cur.rowcount > 0


def reorder_watchlist_items(
    conn: sqlite3.Connection, wl_id: int, item_ids: list[int],
) -> bool:
    try:
        conn.execute("BEGIN IMMEDIATE")
        for pos, item_id in enumerate(item_ids):
            conn.execute(
                "UPDATE watchlist_items SET position = ? "
                "WHERE id = ? AND watchlist_id = ?", (pos, item_id, wl_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

_ALERT_FIELDS = frozenset({
    "ltp", "change_percent", "volume", "oi_change_percent",
})
_ALERT_OPERATORS = frozenset({"gt", "lt", "crosses_above", "crosses_below"})


def create_alert(
    conn: sqlite3.Connection, *, exchange: str, instrument_token: str,
    tradingsymbol: str, field: str, operator: str, threshold: float,
) -> dict[str, Any]:
    if field not in _ALERT_FIELDS:
        raise ValueError(f"unsupported alert field: {field}")
    if operator not in _ALERT_OPERATORS:
        raise ValueError(f"unsupported alert operator: {operator}")
    now = _now()
    cur = conn.execute(
        "INSERT INTO market_alerts (exchange, instrument_token, tradingsymbol, "
        "field, operator, threshold, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (exchange, instrument_token, tradingsymbol, field, operator,
         float(threshold), now))
    conn.commit()
    return {"id": cur.lastrowid, "exchange": exchange,
            "instrument_token": instrument_token,
            "tradingsymbol": tradingsymbol, "field": field,
            "operator": operator, "threshold": float(threshold),
            "enabled": True, "state": "inactive"}


def list_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM market_alerts ORDER BY id")]
    finally:
        conn.row_factory = None


def delete_alert(conn: sqlite3.Connection, alert_id: int) -> bool:
    cur = conn.execute("DELETE FROM market_alerts WHERE id = ?", (alert_id,))
    conn.commit()
    return cur.rowcount > 0


def set_alert_enabled(
    conn: sqlite3.Connection, alert_id: int, enabled: bool,
) -> bool:
    state = "inactive" if enabled else "disabled"
    cur = conn.execute(
        "UPDATE market_alerts SET enabled = ?, state = ? WHERE id = ?",
        (1 if enabled else 0, state, alert_id))
    conn.commit()
    return cur.rowcount > 0


def rearm_alert(conn: sqlite3.Connection, alert_id: int) -> bool:
    cur = conn.execute(
        "UPDATE market_alerts SET state = 'inactive', triggered_at = NULL "
        "WHERE id = ?", (alert_id,))
    conn.commit()
    return cur.rowcount > 0


def record_trigger(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute(
        "UPDATE market_alerts SET state = 'triggered', triggered_at = ? "
        "WHERE id = ?", (_now(), alert_id))
    conn.commit()


def load_enabled_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM market_alerts WHERE enabled = 1")]
    finally:
        conn.row_factory = None


def migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """Add product tables: instruments, watchlists, alerts."""
    create_product_tables(conn)
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    logger.info("migrated v10→v11: added instruments/watchlists/alerts")


def option_underlyings(
    conn: sqlite3.Connection, q: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Distinct option underlyings, optionally filtered by name/symbol."""
    sql = ("SELECT DISTINCT underlying FROM instruments "
           "WHERE underlying IS NOT NULL")
    args: list[Any] = []
    if q:
        sql += " AND (underlying LIKE ? OR tradingsymbol LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY underlying LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def option_expiries(
    conn: sqlite3.Connection, underlying: str,
) -> list[str]:
    """Distinct expiries for one underlying, ascending."""
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT DISTINCT expiry FROM instruments "
            "WHERE underlying = ? AND expiry IS NOT NULL "
            "ORDER BY expiry", (underlying,))
        return [r["expiry"] for r in rows]
    finally:
        conn.row_factory = None

