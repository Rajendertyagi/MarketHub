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


# ---------------------------------------------------------------------------
# Canonical catalog vocabulary (single source of truth)
# ---------------------------------------------------------------------------
#
# The catalog is the canonical boundary: `instruments.segment` / `tradingsymbol`
# / `underlying` must hold canonical values for EVERY provider, so no read
# model needs provider-specific SQL or branching. Providers that publish raw
# numeric segment codes (Fyers 10/11) are normalized at ingestion; providers
# that already publish canonical segments (Upstox NSE_EQ/NSE_FO/NSE_INDEX)
# pass through unchanged. These helpers are shared by ingestion
# (app.instruments) and the v21 -> v22 catalog migration.

KNOWN_SEGMENTS: tuple[str, ...] = (
    "NSE_EQ", "NSE_FO", "NSE_INDEX", "NSE_COM",
    "BSE_EQ", "BSE_FO", "BSE_INDEX",
    "MCX_FO", "BCD_FO", "NCD_FO", "GLOBAL",
)

_CASH_TYPES = ("EQUITY", "ETF")
_DERIVATIVE_TYPES = ("FUTURE", "OPTION")


def canonical_segment(exchange: str | None, instrument_type: str | None,
                      segment: str | None = None) -> str | None:
    """Canonical MarketHub segment for one instrument.

    A provider-native segment that is already canonical is returned as-is;
    anything else (Fyers ``10``/``11``, or a missing value) is derived from the
    structured facts ``exchange`` + ``instrument_type``.
    """
    if segment in KNOWN_SEGMENTS:
        return segment
    ex = (exchange or "").upper()
    itype = instrument_type
    if ex in ("NSE", "BSE"):
        if itype == "INDEX":
            return f"{ex}_INDEX"
        if itype in _DERIVATIVE_TYPES:
            return f"{ex}_FO"
        if itype in _CASH_TYPES:
            return f"{ex}_EQ"
    if ex == "MCX":
        return "MCX_FO"
    return None


def strip_exchange_namespace(symbol: str | None) -> str | None:
    """Remove a leading ``EXCH:`` provider namespace (``NSE:X-EQ`` -> ``X-EQ``)."""
    s = (symbol or "").strip()
    if not s:
        return None
    head, sep, tail = s.partition(":")
    if sep and tail:
        return tail
    return s


def canonical_cash_symbol(tradingsymbol: str | None,
                          underlying: str | None) -> str | None:
    """Canonical symbol for a cash (EQUITY/ETF) row.

    The provider's authoritative underlying (Fyers ``underSym``) wins; otherwise
    the trading symbol with any exchange namespace removed (Upstox publishes the
    canonical symbol directly).
    """
    und = (underlying or "").strip().upper()
    if und:
        return und
    return (strip_exchange_namespace(tradingsymbol) or "").upper() or None


def canonicalize_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Normalize one parsed catalog record in place (canonical fields only).

    ``provider_symbol`` / ``provider`` / ``instrument_token`` are never touched —
    provider identity is preserved exactly.

    INDEX rows keep their provider-native ``tradingsymbol``: consumers
    (app.market_indices._catalog_row) resolve index spots by an EXACT provider
    symbol lookup, so rewriting it would regress index quote resolution.
    """
    rec["segment"] = canonical_segment(
        rec.get("exchange"), rec.get("instrument_type"), rec.get("segment"))
    itype = rec.get("instrument_type")
    if itype in _CASH_TYPES:
        symbol = canonical_cash_symbol(rec.get("tradingsymbol"),
                                       rec.get("underlying"))
        if symbol:
            rec["tradingsymbol"] = symbol
            rec["underlying"] = symbol
    elif itype in _DERIVATIVE_TYPES:
        stripped = strip_exchange_namespace(rec.get("tradingsymbol"))
        if stripped:
            rec["tradingsymbol"] = stripped
    return rec


def migrate_v21_to_v22(conn: sqlite3.Connection) -> None:
    """Canonicalize existing catalog rows (segment / tradingsymbol / underlying).

    Uses the SAME rules as ingestion (no duplicated SQL mapping), so the
    database and freshly synced data converge. Idempotent: a second run finds
    nothing to change. Provider identity (`provider`, `instrument_token`,
    `provider_symbol`) is never modified.
    """
    rows = conn.execute(
        "SELECT rowid, exchange, instrument_type, segment, tradingsymbol, "
        "underlying FROM instruments").fetchall()
    updates: list[tuple[Any, ...]] = []
    for row in rows:
        rowid, exchange, itype, segment, tsym, underlying = row
        new_seg = canonical_segment(exchange, itype, segment)
        new_tsym, new_und = tsym, underlying
        if itype in _CASH_TYPES:
            symbol = canonical_cash_symbol(tsym, underlying)
            if symbol:
                new_tsym, new_und = symbol, symbol
        elif itype in _DERIVATIVE_TYPES:
            stripped = strip_exchange_namespace(tsym)
            if stripped:
                new_tsym = stripped
        if (new_seg, new_tsym, new_und) != (segment, tsym, underlying):
            updates.append((new_seg, new_tsym, new_und, rowid))
    if updates:
        conn.executemany(
            "UPDATE instruments SET segment = ?, tradingsymbol = ?, "
            "underlying = ? WHERE rowid = ?", updates)
    # The canonical link join (eq.underlying = f.underlying) needs its index.
    create_instrument_read_indexes(conn)
    conn.execute("PRAGMA user_version = 22")
    conn.commit()
    logger.info("migrated v21->v22: canonicalized %d catalog row(s)",
                len(updates))


# ---------------------------------------------------------------------------
# Derived-universe read-path indexes (schema v21)
# ---------------------------------------------------------------------------
#
# ``idx_instr_lookup(exchange, instrument_type, expiry, underlying)`` puts
# ``underlying`` LAST, so the F&O access patterns that filter by ``underlying``
# while leaving ``expiry`` open (``derivative_expiries``) or that filter by
# ``segment`` (``equity_universe``) could not use it and fell back to scanning
# the whole ~130k-row catalog. Measured on the real catalog:
#
#   derivative_expiries  0.0118 s -> 0.0000 s (covering index seek)
#   equity_universe      0.0770 s -> 0.0008 s (covering index seek)
#
# Created idempotently for fresh databases and by the v20 -> v21 migration for
# existing ones.


def create_instrument_read_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instr_fo
        ON instruments(exchange, instrument_type, underlying, expiry)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instr_seg
        ON instruments(segment, instrument_type, tradingsymbol)
    """)
    # Canonical underlying link (v22): the F&O universe joins a derivative's
    # `underlying` to the cash row's `underlying`. Without this the join has no
    # equality index and degrades to a cross-product scan.
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_instr_underlying
        ON instruments(underlying, provider, segment)
    """)


def migrate_v20_to_v21(conn: sqlite3.Connection) -> None:
    """Add the F&O derived-universe read indexes (idempotent)."""
    create_instrument_read_indexes(conn)
    conn.execute("PRAGMA user_version = 21")
    conn.commit()
    logger.info("migrated v20->v21: added instrument read indexes")


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
    create_instrument_read_indexes(conn)
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
    underlying: str | None = None,
    expiry: str | None = None,
    option_type: str | None = None,
    strike: float | None = None,
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
    if underlying:
        sql += " AND underlying = ?"
        args.append(underlying)
    if expiry:
        sql += " AND expiry = ?"
        args.append(expiry)
    if option_type:
        sql += " AND option_type = ?"
        args.append(option_type)
    if strike is not None:
        sql += " AND strike = ?"
        args.append(strike)
    sql += " ORDER BY tradingsymbol LIMIT ?"
    args.append(max(1, min(int(limit), 100)))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def derivative_expiries(
    conn: sqlite3.Connection,
    *,
    underlying: str,
    instrument_type: str,
    exchange: str | None = None,
) -> list[str]:
    """Sorted distinct expiries for one underlying's futures/options.

    ``exchange`` is optional (backward compatible) but F&O callers SHOULD
    pass it: it is the leading column of ``idx_instr_lookup``
    (exchange, instrument_type, expiry, underlying), so supplying it turns a
    full catalog scan into an index seek.
    """
    sql = ("SELECT DISTINCT expiry FROM instruments "
           "WHERE underlying = ? AND instrument_type = ? "
           "AND expiry IS NOT NULL")
    args: list[Any] = [underlying, instrument_type]
    if exchange:
        sql += " AND exchange = ?"
        args.append(exchange)
    sql += " ORDER BY expiry"
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, args)
        return [r["expiry"] for r in rows]
    finally:
        conn.row_factory = None


def option_strikes(
    conn: sqlite3.Connection,
    *,
    underlying: str,
    expiry: str,
    exchange: str | None = None,
) -> list[dict[str, Any]]:
    """All option contracts for one underlying+expiry, strike-sorted.

    Returns flat contract rows; callers pair CE/PE per strike. ``exchange`` is
    optional (backward compatible) but F&O callers SHOULD pass it so the
    lookup uses ``idx_instr_lookup`` instead of scanning the whole catalog.
    """
    sql = (f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments "
           "WHERE underlying = ? AND expiry = ? "
           "AND instrument_type = 'OPTION'")
    args: list[Any] = [underlying, expiry]
    if exchange:
        sql += " AND exchange = ?"
        args.append(exchange)
    sql += " ORDER BY strike, option_type"
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


# ---------------------------------------------------------------------------
# Broad NSE equity universe (read model for breadth/heatmap "ALL NSE" view)
# ---------------------------------------------------------------------------


def equity_universe(
    conn: sqlite3.Connection, *, provider: str | None = None, limit: int = 5000,
) -> list[dict[str, Any]]:
    """All NSE equity instruments (segment NSE_EQ) in the catalog.

    One grouped query — no N+1. Returns lightweight identity rows
    (tradingsymbol, name, exchange, instrument_token) for the breadth /
    sector-heatmap "all supported NSE equities" universe. The default cap is
    high; callers that only need counts can pass a smaller limit.
    """
    sql = (
        f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments "
        "WHERE segment = 'NSE_EQ' AND instrument_type = 'EQUITY'"
    )
    args: list[Any] = []
    if provider:
        sql += " AND provider = ?"
        args.append(provider)
    sql += " ORDER BY tradingsymbol LIMIT ?"
    args.append(max(1, min(int(limit), 20000)))
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


def list_all_instruments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All catalog rows (used to populate the B2 identity resolver)."""
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments "
            "ORDER BY provider, tradingsymbol")]
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


# ---------------------------------------------------------------------------
# Alert trigger history (schema v12) — durable, restart-safe record of every
# individual alert firing (distinct from market_alerts.trigger_count which is
# only an aggregate). Never duplicates live evaluation state.
# ---------------------------------------------------------------------------

def create_alert_trigger_history_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_trigger_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id        INTEGER NOT NULL,
            exchange        TEXT,
            instrument_token TEXT,
            tradingsymbol   TEXT,
            field           TEXT,
            operator        TEXT,
            threshold       REAL,
            observed_value  REAL,
            provider        TEXT,
            triggered_at    TEXT NOT NULL,
            created_at      TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ath_alert
        ON alert_trigger_history(alert_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ath_triggered
        ON alert_trigger_history(triggered_at)
    """)


def insert_alert_trigger_history(
    conn: sqlite3.Connection, *, alert_id: int, exchange: str | None,
    instrument_token: str | None, tradingsymbol: str | None, field: str | None,
    operator: str | None, threshold: float | None, observed_value: float | None,
    provider: str | None, triggered_at: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO alert_trigger_history "
        "(alert_id, exchange, instrument_token, tradingsymbol, field, "
        " operator, threshold, observed_value, provider, triggered_at, "
        " created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (alert_id, exchange, instrument_token, tradingsymbol, field,
         operator, threshold if threshold is None else float(threshold),
         observed_value if observed_value is None else float(observed_value),
         provider, triggered_at, _now()))
    conn.commit()
    return cur.lastrowid


def list_alert_trigger_history(
    conn: sqlite3.Connection, alert_id: int | None = None,
    limit: int = 50, offset: int = 0, provider: str | None = None,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM alert_trigger_history WHERE 1=1"
    args: list[Any] = []
    if alert_id is not None:
        sql += " AND alert_id = ?"
        args.append(alert_id)
    if provider is not None:
        sql += " AND provider = ?"
        args.append(provider)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args.extend([int(limit), int(offset)])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def count_alert_trigger_history(
    conn: sqlite3.Connection, alert_id: int | None = None,
    provider: str | None = None,
) -> int:
    sql = "SELECT COUNT(*) FROM alert_trigger_history WHERE 1=1"
    args: list[Any] = []
    if alert_id is not None:
        sql += " AND alert_id = ?"
        args.append(alert_id)
    if provider is not None:
        sql += " AND provider = ?"
        args.append(provider)
    return conn.execute(sql, args).fetchone()[0]


def clear_alert_trigger_history(
    conn: sqlite3.Connection, alert_id: int | None = None,
) -> int:
    if alert_id is not None:
        cur = conn.execute(
            "DELETE FROM alert_trigger_history WHERE alert_id = ?",
            (alert_id,))
    else:
        cur = conn.execute("DELETE FROM alert_trigger_history")
    conn.commit()
    return cur.rowcount


def migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """Add alert_trigger_history table (durable per-trigger audit log)."""
    create_alert_trigger_history_table(conn)
    conn.execute("PRAGMA user_version = 12")
    conn.commit()
    logger.info("migrated v11→v12: added alert_trigger_history")


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


# ---------------------------------------------------------------------------
# F&O stock universe (derived read model — no second table)
# ---------------------------------------------------------------------------

def fno_underlying_rows(
    conn: sqlite3.Connection, *, provider: str, today: str, limit: int = 2000,
) -> list[dict[str, Any]]:
    """ENUMERATION read model: F&O stock underlyings, no contract counts.

    Identical membership to :func:`fno_universe` but without the per-underlying
    ``count(DISTINCT ...)`` aggregation — for callers that only need the
    universe (navigation, coverage, universe resolution). Count consumers must
    keep using :func:`fno_universe`.
    """
    sql = """
        SELECT DISTINCT f.underlying AS symbol,
               eq.name AS name,
               eq.instrument_token AS equity_key
        FROM instruments f
        JOIN instruments eq
             ON eq.provider = f.provider
             AND eq.segment = 'NSE_EQ'
             AND eq.underlying = f.underlying
        WHERE f.provider = ?
          AND f.segment = 'NSE_FO'
          AND f.instrument_type IN ('FUTURE', 'OPTION')
          AND f.expiry >= ?
        ORDER BY f.underlying LIMIT ?
    """
    args: list[Any] = [provider, today, max(1, min(int(limit), 1000))]
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def fno_universe(
    conn: sqlite3.Connection, *, provider: str, today: str,
    q: str | None = None, limit: int = 500,
) -> list[dict[str, Any]]:
    """Equity underlyings with live (non-expired) F&O contracts.

    Derived from structured catalog relationships — never a hard-coded
    list, never substring matching:

      * derivatives: segment NSE_FO, instrument_type FUTURE/OPTION,
        underlying = the derivative's `underlying` column;
      * the underlying is a STOCK (not an index) because an NSE_EQ cash row
        whose canonical ``underlying`` equals the derivative's ``underlying``
        must exist (indices live in the NSE_INDEX segment and have no cash
        row). The link is the canonical ``underlying`` column for EVERY
        provider — never a provider-native symbol;
      * membership requires at least one contract with expiry >= today,
        so expired-only underlyings drop out automatically after sync.

    One grouped query — the universe is cheap to browse (no N+1, no
    contract dumps). Returns underlyings, not contracts.
    """
    sql = """
        SELECT f.underlying AS symbol,
               eq.name AS name,
               eq.instrument_token AS equity_key,
               COUNT(DISTINCT CASE WHEN f.instrument_type = 'FUTURE'
                    THEN f.expiry END) AS future_expiries,
               COUNT(DISTINCT CASE WHEN f.instrument_type = 'OPTION'
                    THEN f.expiry END) AS option_expiries,
               COUNT(DISTINCT CASE WHEN f.instrument_type = 'FUTURE'
                    THEN f.instrument_token END) AS futures_count,
               COUNT(DISTINCT CASE WHEN f.instrument_type = 'OPTION'
                    THEN f.instrument_token END) AS options_count,
               MIN(CASE WHEN f.instrument_type = 'FUTURE'
                    THEN f.expiry END) AS nearest_future,
               MIN(CASE WHEN f.instrument_type = 'OPTION'
                    THEN f.expiry END) AS nearest_option
        FROM instruments f
        JOIN instruments eq
             ON eq.provider = f.provider
             AND eq.segment = 'NSE_EQ'
             AND eq.underlying = f.underlying
        WHERE f.provider = ?
          AND f.segment = 'NSE_FO'
          AND f.instrument_type IN ('FUTURE', 'OPTION')
          AND f.expiry >= ?
        GROUP BY f.underlying
    """
    args: list[Any] = [provider, today]
    if q:
        sql += " HAVING f.underlying LIKE ? OR eq.name LIKE ?"
        like = f"%{q}%"
        args += [like, like]
    sql += " ORDER BY f.underlying LIMIT ?"
    args.append(max(1, min(int(limit), 1000)))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def list_future_contracts(
    conn: sqlite3.Connection, *, provider: str | None = None,
    underlying: str | None = None, expiries: list[str] | None = None,
    exchange: str | None = None, limit: int = 5000,
) -> list[dict[str, Any]]:
    """Bulk read model: every FUTURE contract row in one query (no N+1).

    Provider-neutral shape (provider tokens included as data, never assumed).
    Ordered by underlying, expiry. Optional provider / underlying / expiry /
    exchange filters keep callers from issuing per-underlying queries;
    ``exchange`` also lets the query use ``idx_instr_lookup``.
    """
    sql = (f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments "
           "WHERE instrument_type = 'FUTURE'")
    args: list[Any] = []
    if exchange:
        sql += " AND exchange = ?"
        args.append(exchange)
    if provider:
        sql += " AND provider = ?"
        args.append(provider)
    if underlying:
        sql += " AND underlying = ?"
        args.append(underlying)
    if expiries:
        placeholders = ",".join("?" for _ in expiries)
        sql += f" AND expiry IN ({placeholders})"
        args.extend(str(e) for e in expiries)
    sql += " ORDER BY underlying, expiry, tradingsymbol LIMIT ?"
    args.append(max(1, min(int(limit), 20000)))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None

