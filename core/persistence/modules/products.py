"""
Product tables: instruments catalog, watchlists, alerts (schema v11).

Generic, provider-neutral. Values here are canonical metadata only —
never raw provider payloads, never secrets.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence
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


class ScopedReplaceConflict(RuntimeError):
    """A scoped replacement would overwrite a row outside its declared scope.

    Raised BEFORE any delete: the transaction rolls back, so the catalogue
    is left exactly as it was.
    """


def _instrument_row(
    provider: str, record: dict[str, Any], now: str,
) -> tuple[Any, ...] | None:
    """One INSERT row for a catalog record; None when identity is missing."""
    if not record.get("instrument_token") or not record.get("exchange") \
            or not record.get("tradingsymbol"):
        return None
    return (
        provider, record["instrument_token"], record["exchange"],
        record["tradingsymbol"], record.get("name"),
        record.get("instrument_type"), record.get("segment"),
        record.get("expiry"), record.get("strike"), record.get("option_type"),
        record.get("lot_size"), record.get("tick_size"), record.get("isin"),
        record.get("underlying"), record.get("provider_symbol"), now,
    )


def _insert_instrument_rows(
    conn: sqlite3.Connection, rows: Sequence[tuple[Any, ...]],
) -> None:
    if not rows:
        return
    conn.executemany(
        f"INSERT INTO instruments ({', '.join(_INSTRUMENT_COLUMNS)}, "
        "updated_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
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
    rows = [row for r in records if (row := _instrument_row(provider, r, now))]
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM instruments WHERE provider = ?", (provider,))
        _insert_instrument_rows(conn, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise


def open_instrument_connection(db_path: str) -> sqlite3.Connection:
    """Open the instrument database with the store's connection settings."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def replace_provider_instruments_segmented_at(
    db_path: str,
    provider: str,
    records: list[dict[str, Any]],
    segments: Sequence[str],
) -> int:
    """Segment-scoped replace addressed by DATABASE PATH.

    The catalogue synchronisation needs a write that touches only the
    segments it refreshed. It lives here - in the persistence module that
    owns the table - and is addressed by path, so the event store's
    public API (and the protected store module) stays untouched.

    The transaction, the rollback behaviour and the segment scope are
    identical to the connection-based variant below.
    """
    conn = open_instrument_connection(db_path)
    try:
        return replace_provider_instruments_segmented(
            conn, provider, records, segments)
    finally:
        conn.close()


def replace_provider_instruments_segmented(
    conn: sqlite3.Connection,
    provider: str,
    records: list[dict[str, Any]],
    segments: Sequence[str],
) -> int:
    """Transactionally replace ONLY the given segments of one provider.

    Rows of the same provider in OTHER segments are preserved, so a
    provider whose master files are downloaded segment by segment can
    refresh the segments that validated cleanly without touching (or
    deleting) the segments whose download failed.

    Rows whose segment is NULL are replaced together with the requested
    segments: they carry no segment ownership and would otherwise be
    orphaned by a segment-scoped delete.
    """
    now = _now()
    segs = [str(s) for s in segments if s]
    if not segs:
        return 0
    placeholders = ", ".join("?" for _ in segs)
    rows = []
    for r in records:
        if str(r.get("segment") or "") not in segs:
            continue
        row = _instrument_row(provider, r, now)
        if row is not None:
            rows.append(row)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"DELETE FROM instruments WHERE provider = ? "
            f"AND (segment IN ({placeholders}) OR segment IS NULL)",
            (provider, *segs),
        )
        _insert_instrument_rows(conn, rows)
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise


# Kept outside the writers so the scope is validated once, before any SQL.
_SCOPE_TEMP_TABLE = "_instruments_scope_tokens"


def replace_provider_instruments_scoped_at(
    db_path: str,
    provider: str,
    records: list[dict[str, Any]],
    *,
    segment: str,
    scope_column: str,
    scope_values: Sequence[str],
) -> int:
    """Predicate-scoped replace addressed by DATABASE PATH.

    Same transaction/rollback guarantees as the connection-based variant.
    """
    conn = open_instrument_connection(db_path)
    try:
        return replace_provider_instruments_scoped(
            conn, provider, records, segment=segment,
            scope_column=scope_column, scope_values=scope_values)
    finally:
        conn.close()


def replace_provider_instruments_scoped(
    conn: sqlite3.Connection,
    provider: str,
    records: list[dict[str, Any]],
    *,
    segment: str,
    scope_column: str,
    scope_values: Sequence[str],
) -> int:
    """Transactionally replace ONE provider's rows inside a declared scope.

    Only rows that satisfy ``segment = ? AND <scope_column> IN (...)`` are
    deleted and re-inserted; every other row of the provider is preserved.
    This is the write used when a provider master is filtered before
    ingestion (e.g. FYERS BSE_FO -> SENSEX only): a segment-wide delete
    would destroy the other underlyings that live in the same segment.

    Safety rules, enforced before the delete:

    * ``scope_column`` must be a real catalog column (allowlist - the value
      is interpolated into SQL);
    * ``segment`` and ``scope_values`` must be non-empty - an empty scope
      would degenerate into a segment-wide delete;
    * every record must belong to the declared segment;
    * a token that already exists OUTSIDE the scope is a conflict: the
      scoped delete would not remove it and the insert would overwrite it.
      The transaction rolls back and :class:`ScopedReplaceConflict` is
      raised, leaving the catalogue untouched.
    """
    if scope_column not in _INSTRUMENT_COLUMNS:
        raise ValueError(f"invalid scope column: {scope_column!r}")
    if not segment:
        raise ValueError("segment is required")
    values = [str(v) for v in scope_values if str(v)]
    if not values:
        raise ValueError("scope_values must be non-empty")
    now = _now()
    rows: list[tuple[Any, ...]] = []
    for r in records:
        if str(r.get("segment") or "") != segment:
            raise ValueError(
                f"record outside scoped segment {segment!r}: "
                f"{r.get('segment')!r}")
        row = _instrument_row(provider, r, now)
        if row is not None:
            rows.append(row)
    if not rows:
        raise ValueError("scoped replace received no valid records")

    placeholders = ", ".join("?" for _ in values)
    scope_pred = f"segment = ? AND {scope_column} IN ({placeholders})"
    scope_args: tuple[Any, ...] = (segment, *values)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {_SCOPE_TEMP_TABLE} "
            "(instrument_token TEXT PRIMARY KEY)")
        conn.execute(f"DELETE FROM {_SCOPE_TEMP_TABLE}")
        try:
            conn.executemany(
                f"INSERT INTO {_SCOPE_TEMP_TABLE} (instrument_token) "
                "VALUES (?)",
                [(str(row[1]),) for row in rows],
            )
        except sqlite3.IntegrityError as exc:
            raise ScopedReplaceConflict(
                "duplicate instrument_token in scoped batch") from exc
        conflict = conn.execute(
            f"SELECT i.instrument_token FROM instruments i "
            f"JOIN {_SCOPE_TEMP_TABLE} t "
            "ON t.instrument_token = i.instrument_token "
            "WHERE i.provider = ? AND ("
            "i.segment IS NULL OR i.segment <> ? OR "
            f"i.{scope_column} IS NULL OR "
            f"i.{scope_column} NOT IN ({placeholders})) LIMIT 1",
            (provider, segment, *values),
        ).fetchone()
        if conflict is not None:
            raise ScopedReplaceConflict(
                f"{provider}: token {conflict[0]} already exists outside "
                f"{segment}/{scope_column}={values}")
        conn.execute(
            f"DELETE FROM instruments WHERE provider = ? AND {scope_pred}",
            (provider, *scope_args),
        )
        _insert_instrument_rows(conn, rows)
        conn.execute(f"DELETE FROM {_SCOPE_TEMP_TABLE}")
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
    isin: str | None = None,
    limit: int = 25,
    types: Sequence[str] | None = None,
    rank: str | None = None,
) -> list[dict[str, Any]]:
    """Substring search over the catalog.

    ``types`` scopes one query to several canonical types at once. Passing
    ``rank`` additionally enables the picker's ranked mode: a single pass
    scored by relevance (exact symbol -> exact underlying -> symbol prefix ->
    symbol contains -> name contains), then NSE preference, then
    length/alphabet, capped at 50 rows and scoped to cash classes
    (INDEX/EQUITY/ETF) unless an explicit type scope is given.

    The unranked mode is unchanged — alphabetical, cap 100 — for every
    existing caller.
    """
    ranked = rank is not None
    scope: list[str] = [instrument_type] if instrument_type else list(types or ())
    if ranked and not scope:
        scope = ["INDEX", "EQUITY", "ETF"]
    sql = f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments WHERE 1=1"
    args: list[Any] = []
    if q:
        match = "tradingsymbol LIKE ? OR name LIKE ?"
        if ranked:
            # A canonical underlying key ("banknifty") need not appear in
            # the display symbol; the ranked scan scores it separately.
            match += " OR underlying LIKE ?"
        sql += f" AND ({match})"
        args += [f"%{q}%"] * (3 if ranked else 2)
    if exchange:
        sql += " AND exchange = ?"
        args.append(exchange)
    if scope:
        sql += f" AND instrument_type IN ({', '.join('?' for _ in scope)})"
        args += scope
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
    if isin:
        sql += " AND isin = ?"
        args.append(isin)
    rank_q = (rank or "").strip().upper()
    if rank_q:
        sql += (
            " ORDER BY CASE"
            " WHEN UPPER(tradingsymbol) = ? THEN 0"
            " WHEN UPPER(underlying) = ? THEN 1"
            " WHEN UPPER(tradingsymbol) LIKE ? THEN 2"
            " WHEN UPPER(tradingsymbol) LIKE ? THEN 3"
            " WHEN UPPER(name) LIKE ? THEN 4"
            " ELSE 5 END,"
            " CASE WHEN exchange = 'NSE' THEN 0 ELSE 1 END,"
            " LENGTH(tradingsymbol), tradingsymbol")
        args += [rank_q, rank_q, f"{rank_q}%", f"%{rank_q}%",
                 f"%{rank_q}%"]
    elif ranked:
        sql += (" ORDER BY CASE WHEN exchange = 'NSE' THEN 0 ELSE 1 END,"
                " LENGTH(tradingsymbol), tradingsymbol")
    else:
        sql += " ORDER BY tradingsymbol"
    lim = int(limit)
    if lim > 0:
        sql += " LIMIT ?"
        args.append(max(1, min(lim, 50 if ranked else 100)))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


def find_by_exact_symbol(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    provider: str | None = None,
    instrument_type: str | None = None,
    exchange: str | None = None,
) -> list[dict[str, Any]]:
    """Rows whose canonical symbol matches EXACTLY (case-insensitive).

    The general ``search_instruments`` is a substring LIKE with a LIMIT, so
    a short symbol (e.g. "LT") can match an unrelated instrument
    ("20MICRONS", "3BBLACKBIO", ...) or fall outside the limit window
    entirely. This read model matches the canonical symbol exactly, on
    ``tradingsymbol`` OR the canonicalized ``underlying``.
    """
    want = (symbol or "").strip().upper()
    if not want:
        return []
    sql = (f"SELECT {', '.join(_INSTRUMENT_COLUMNS)} FROM instruments "
           "WHERE (UPPER(tradingsymbol) = ? OR UPPER(underlying) = ?) "
           "AND instrument_type IS NOT NULL")
    args: list[Any] = [want, want]
    if provider:
        sql += " AND provider = ?"
        args.append(provider)
    if instrument_type:
        sql += " AND instrument_type = ?"
        args.append(instrument_type)
    if exchange:
        sql += " AND exchange = ?"
        args.append(exchange)
    # Cash before derivatives, then provider preference, so an exact
    # resolution is never truncated down to option noise.
    sql += (" ORDER BY CASE instrument_type"
            " WHEN 'INDEX' THEN 0 WHEN 'EQUITY' THEN 1 WHEN 'ETF' THEN 2"
            " WHEN 'FUTURE' THEN 3 WHEN 'OPTION' THEN 4 ELSE 5 END,"
            " provider, tradingsymbol LIMIT 20")
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


def segment_underlying_summary(
    conn: sqlite3.Connection, *, provider: str, segment: str,
) -> list[dict[str, Any]]:
    """Per-underlying instrument summary inside ONE provider segment.

    Read-only projection of what a provider's catalogue ALREADY holds —
    used to audit coverage (e.g. which BSE F&O underlyings were imported)
    without loading contract rows. One grouped query, no N+1.
    """
    sql = """
        SELECT COALESCE(underlying, '<missing>') AS underlying,
               COUNT(*) AS instruments,
               SUM(instrument_type = 'FUTURE') AS futures,
               SUM(instrument_type = 'OPTION') AS options,
               SUM(instrument_type = 'OPTION' AND option_type = 'CE')
                   AS calls,
               SUM(instrument_type = 'OPTION' AND option_type = 'PE')
                   AS puts,
               COUNT(DISTINCT expiry) AS expiries,
               MIN(expiry) AS nearest_expiry,
               MAX(expiry) AS farthest_expiry
        FROM instruments
        WHERE provider = ? AND segment = ?
        GROUP BY underlying
        ORDER BY underlying
    """
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, (provider, segment))]
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


def commit_market_alert_trigger(
    conn: sqlite3.Connection,
    *,
    alert_id: int,
    exchange: str | None,
    instrument_token: str | None,
    tradingsymbol: str | None,
    field: str | None,
    operator: str | None,
    threshold: float | None,
    observed_value: float | None,
    provider: str | None,
    triggered_at: str,
) -> int | None:
    """Atomically commit a market-alert trigger (state + history) once.

    Updates ``market_alerts`` to triggered only when the rule is still
    enabled and inactive, then inserts one ``alert_trigger_history`` row in
    the same transaction. Returns the history row id on commit, or ``None``
    when the alert was not triggerable (already triggered/disabled/deleted/
    raced). Any failure rolls back the whole transaction.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE market_alerts SET state = 'triggered', triggered_at = ? "
            "WHERE id = ? AND enabled = 1 AND state = 'inactive'",
            (triggered_at, alert_id),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return None
        hist = conn.execute(
            "INSERT INTO alert_trigger_history "
            "(alert_id, exchange, instrument_token, tradingsymbol, field, "
            " operator, threshold, observed_value, provider, triggered_at, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alert_id, exchange, instrument_token, tradingsymbol, field,
             operator, threshold if threshold is None else float(threshold),
             observed_value if observed_value is None else float(observed_value),
             provider, triggered_at, _now()),
        )
        conn.commit()
        return int(hist.lastrowid)
    except Exception:
        conn.rollback()
        raise


def commit_market_alert_trigger_with_event(
    conn: sqlite3.Connection,
    *,
    alert_id: int,
    exchange: str | None,
    instrument_token: str | None,
    tradingsymbol: str | None,
    field: str | None,
    operator: str | None,
    threshold: float | None,
    observed_value: float | None,
    provider: str | None,
    triggered_at: str,
    event_id: str,
    event_type: str,
    event_source: str,
    event_timestamp: str,
    event_data: dict[str, Any],
    event_routing: dict[str, Any] | None,
    materialize_fn,
) -> tuple[int, int] | None:
    """Atomically commit market-alert trigger, history, and canonical event.

    Performs in ONE ``BEGIN IMMEDIATE`` transaction:
      1. conditional market_alerts state transition (inactive+enabled -> triggered)
      2. alert_trigger_history insert
      3. persistent_events insert
      4. consumer materialization for the event

    Returns ``(history_id, event_sequence)`` on commit, or ``None`` when the
    alert was not triggerable (already triggered/disabled/deleted/raced).
    Any failure rolls back everything.
    """
    import json

    data_json = json.dumps(event_data, ensure_ascii=False, allow_nan=False)
    routing_json = (
        json.dumps(event_routing, ensure_ascii=False, allow_nan=False)
        if event_routing is not None else None
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "UPDATE market_alerts SET state = 'triggered', triggered_at = ? "
            "WHERE id = ? AND enabled = 1 AND state = 'inactive'",
            (triggered_at, alert_id),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return None
        hist = conn.execute(
            "INSERT INTO alert_trigger_history "
            "(alert_id, exchange, instrument_token, tradingsymbol, field, "
            " operator, threshold, observed_value, provider, triggered_at, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alert_id, exchange, instrument_token, tradingsymbol, field,
             operator, threshold if threshold is None else float(threshold),
             observed_value if observed_value is None else float(observed_value),
             provider, triggered_at, _now()),
        )
        conn.execute(
            "INSERT INTO persistent_events "
            "(id, type, source, timestamp, data, routing, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, event_type, event_source, event_timestamp,
             data_json, routing_json, event_timestamp),
        )
        seq = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        materialize_fn(conn, event_id, seq, event_routing)
        conn.commit()
        return int(hist.lastrowid), int(seq)
    except Exception:
        conn.rollback()
        raise


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
    exchange: str | None = None, limit: int | None = 5000,
) -> list[dict[str, Any]]:
    """Bulk read model: FUTURE contract rows in one query (no N+1).

    Provider-neutral shape (provider tokens included as data, never assumed).
    Ordered by underlying, expiry. Optional provider / underlying / expiry /
    exchange filters keep callers from issuing per-underlying queries;
    ``exchange`` also lets the query use ``idx_instr_lookup``.
    ``limit <= 0`` or ``None`` returns the complete result set (no silent cap).
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
    sql += " ORDER BY underlying, expiry, tradingsymbol"
    if limit is not None and int(limit) > 0:
        sql += " LIMIT ?"
        args.append(int(limit))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.row_factory = None


# ---------------------------------------------------------------------------
# Browse + navigation pool read models (Phase 1, unified catalog)
# ---------------------------------------------------------------------------
#
# Both read models dedupe provider rows to ONE row per canonical instrument,
# using the SAME derivation as app.canonical_identity.canonical_id_for_row.
# Core must not import app, so the symbol alias map is a parameter supplied by
# the app layer; the normalization itself (lowercase, strip non-alphanumerics,
# EXCH: prefix stripping, alias resolution) is mirrored in SQL here.

_BROWSE_ALNUM = "abcdefghijklmnopqrstuvwxyz0123456789"


def _sql_normalize(expr: str) -> str:
    """SQL mirror of canonical_identity._normalize_symbol for the catalog's
    actual symbol alphabet: lowercase + strip the five non-alphanumeric
    characters present in provider symbols (space, '-', ':', '.', '&').
    A short REPLACE chain keeps the dedupe query inside the latency budget;
    any other character would be preserved, so this mirror is exact for
    every row the catalog can hold today.
    """
    out = f"LOWER({expr})"
    for ch in (" ", "-", ":", ".", "&"):
        out = f"REPLACE({out}, '{ch}', '')"
    return out


def _sql_canonical_symbol(
    expr: str, aliases: dict[str, str] | None,
) -> tuple[str, list[Any]]:
    """SQL mirror of canonical_identity._canonical_symbol.

    Normalizes (lowercase, strip non-alphanumerics — which also removes any
    ``EXCH:`` prefix separator), then resolves the alias map (passed in by
    the app layer) and upper-cases the remainder.
    """
    norm = _sql_normalize(expr)
    args: list[Any] = []
    if aliases:
        whens = []
        for key, value in aliases.items():
            whens.append("WHEN ? THEN ?")
            args += [key, value]
        norm = f"CASE {norm} {' '.join(whens)} ELSE UPPER({norm}) END"
    else:
        norm = f"UPPER({norm})"
    return norm, args


def _sql_canonical_key(
    aliases: dict[str, str] | None,
) -> tuple[str, list[Any]]:
    """SQL expression for the canonical identity key, mirroring
    canonical_id_for_row. Partition key = exchange-base : type : key parts.
    Rows whose canonical id cannot be derived get a NULL key (kept as-is by
    the app layer; excluded from SQL dedupe only when the key is NULL).
    """
    exch = ("CASE WHEN INSTR(exchange, '_') > 0 THEN "
            "SUBSTR(exchange, 1, INSTR(exchange, '_') - 1) "
            "ELSE UPPER(TRIM(exchange)) END")
    sym, sym_args = _sql_canonical_symbol("tradingsymbol", aliases)
    und, und_args = _sql_canonical_symbol(
        "COALESCE(NULLIF(underlying, ''), tradingsymbol)", aliases)
    # Mirror _normalize_strike: integral strikes render without ".0".
    strike = (
        "CASE WHEN strike = CAST(strike AS INTEGER) "
        "THEN CAST(CAST(strike AS INTEGER) AS TEXT) "
        "ELSE CAST(strike AS TEXT) END"
    )
    key = (
        f"CASE instrument_type "
        f"WHEN 'EQUITY' THEN {exch} || ':EQUITY:' || "
        f"COALESCE(NULLIF(TRIM(isin), ''), {sym}) "
        f"WHEN 'ETF' THEN {exch} || ':ETF:' || "
        f"COALESCE(NULLIF(TRIM(isin), ''), {sym}) "
        f"WHEN 'INDEX' THEN {exch} || ':INDEX:' || {und} "
        f"WHEN 'FUTURE' THEN {exch} || ':FUTURE:' || {und} || ':' || expiry "
        f"WHEN 'OPTION' THEN {exch} || ':OPTION:' || {und} || ':' || expiry "
        f"|| ':' || {strike} || ':' || UPPER(option_type) "
        f"ELSE NULL END"
    )
    return key, sym_args * 2 + und_args * 3


def _provider_order_sql(provider_order: Sequence[str]) -> tuple[str, list[Any]]:
    """ORDER BY expression: configured preference first, unknown last."""
    if not provider_order:
        return "provider", []
    whens = " ".join("WHEN ? THEN ?" for _ in provider_order)
    args: list[Any] = []
    for i, p in enumerate(provider_order):
        args += [p, i]
    return f"CASE provider {whens} ELSE {len(provider_order)} END", args


def browse_instruments(
    conn: sqlite3.Connection,
    *,
    types: Sequence[str] | None = None,
    exchange: str | None = None,
    segment: str | None = None,
    underlying: str | None = None,
    expiry: str | None = None,
    option_type: str | None = None,
    strike: float | None = None,
    include_expired: bool = False,
    after: tuple[str, str, str] | None = None,
    limit: int = 50,
    provider_order: Sequence[str] = (),
    symbol_aliases: dict[str, str] | None = None,
    today: str | None = None,
) -> dict[str, Any]:
    """Paginated, provider-deduped browse over the canonical catalog.

    One row per canonical instrument (SQL mirror of canonical_id_for_row);
    the preferred provider's row wins. Keyset pagination on
    (tradingsymbol, provider, instrument_token) — stable under catalog
    syncs, no OFFSET drift. ``total`` is computed only on the first page.

    Derivative rows with expiry < ``today`` are excluded unless
    ``include_expired``. Untyped rows (instrument_type NULL/'') are never
    browsable. OPTION browse without an ``underlying`` scope is rejected by
    the app layer (unscoped option dedupe exceeds the latency budget).
    """
    where = ["instrument_type IS NOT NULL", "instrument_type != ''"]
    args: list[Any] = []
    if types:
        where.append(
            f"instrument_type IN ({', '.join('?' for _ in types)})")
        args += list(types)
    if exchange:
        where.append("exchange = ?")
        args.append(exchange)
    if segment:
        where.append("segment = ?")
        args.append(segment)
    if underlying:
        where.append("underlying = ?")
        args.append(underlying)
    if expiry:
        where.append("expiry = ?")
        args.append(expiry)
    if option_type:
        where.append("option_type = ?")
        args.append(option_type)
    if strike is not None:
        where.append("strike = ?")
        args.append(float(strike))
    if not include_expired and today:
        where.append(
            "(instrument_type NOT IN ('FUTURE', 'OPTION') "
            "OR (expiry IS NOT NULL AND expiry >= ?))")
        args.append(today)
    key, key_args = _sql_canonical_key(symbol_aliases)
    rank, rank_args = _provider_order_sql(provider_order)
    # Narrow projection through the window (rowid + dedupe/sort keys only),
    # then join back for the page columns — keeps the temp B-tree small.
    narrow = (
        f"SELECT rowid AS _rid, ({key}) AS _cid, provider, tradingsymbol, "
        f"instrument_token FROM instruments WHERE {' AND '.join(where)}"
    )
    ranked = (
        f"SELECT _rid, ROW_NUMBER() OVER ("
        f"PARTITION BY _cid ORDER BY {rank}, provider, tradingsymbol, "
        f"instrument_token) AS _rn FROM narrow"
    )
    page_where = ""
    page_args: list[Any] = []
    if after is not None:
        page_where = ("WHERE (i.tradingsymbol, i.provider, i.instrument_token) "
                      "> (?, ?, ?) ")
        page_args += list(after)
    icols = ", ".join(f"i.{c}" for c in _INSTRUMENT_COLUMNS)
    # COUNT(*) OVER () on the first page rides the same pass — a second
    # COUNT query would double the first-page latency.
    total_col = ", COUNT(*) OVER () AS _total" if after is None else ""
    page_sql = (
        f"WITH narrow AS ({narrow}), ranked AS ({ranked}), "
        f"winners AS (SELECT _rid FROM ranked WHERE _rn = 1) "
        f"SELECT {icols}{total_col} FROM winners w "
        f"JOIN instruments i ON i.rowid = w._rid {page_where}"
        f"ORDER BY i.tradingsymbol, i.provider, i.instrument_token LIMIT ?"
    )
    fetch = max(1, min(int(limit), 200)) + 1
    conn.row_factory = sqlite3.Row
    try:
        raw = [dict(r) for r in conn.execute(
            page_sql, key_args + args + rank_args + page_args + [fetch])]
    finally:
        conn.row_factory = None
    total = raw[0].get("_total") if raw and after is None else None
    for r in raw:
        r.pop("_total", None)
    rows = raw
    next_after = None
    if len(rows) > fetch - 1:
        rows = rows[:fetch - 1]
        last = rows[-1]
        next_after = (last["tradingsymbol"], last["provider"],
                      last["instrument_token"])
    return {"rows": rows, "next_after": next_after, "total": total}


def instrument_pool(
    conn: sqlite3.Connection,
    *,
    provider_order: Sequence[str] = (),
    enabled: Sequence[str] | None = None,
    symbol_aliases: dict[str, str] | None = None,
    today: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Curated navigation pool for the native Vela symbol picker.

    Members: every INDEX, every ETF, and the EQUITY cash row of every
    underlying that has at least one live (expiry >= today) F&O contract.
    Provider-deduped by canonical identity (preferred provider wins);
    disabled providers are excluded BEFORE dedupe so a disabled provider's
    row can never shadow an enabled one. Ordered by the app layer; this
    read model returns the deduped rows in symbol order.
    """
    where = ["instrument_type IN ('INDEX', 'ETF', 'EQUITY')"]
    args: list[Any] = []
    if enabled is not None:
        if not enabled:
            return []
        where.append(f"provider IN ({', '.join('?' for _ in enabled)})")
        args += list(enabled)
    live_underlyings = ""
    if today:
        live_underlyings = (
            " AND (instrument_type != 'EQUITY' OR EXISTS ("
            "SELECT 1 FROM instruments f "
            "WHERE f.segment IN ('NSE_FO', 'BSE_FO', 'MCX_FO') "
            "AND f.instrument_type IN ('FUTURE', 'OPTION') "
            "AND f.expiry >= ? AND f.underlying = instruments.underlying))")
        args.append(today)
    key, key_args = _sql_canonical_key(symbol_aliases)
    rank, rank_args = _provider_order_sql(provider_order)
    cols = ", ".join(_INSTRUMENT_COLUMNS)
    sql = (
        f"WITH filtered AS ("
        f"SELECT {cols}, ({key}) AS _cid FROM instruments "
        f"WHERE {' AND '.join(where)}{live_underlyings}), "
        f"ranked AS (SELECT *, ROW_NUMBER() OVER ("
        f"PARTITION BY _cid ORDER BY {rank}, provider, tradingsymbol, "
        f"instrument_token) AS _rn FROM filtered) "
        f"SELECT {cols} FROM ranked WHERE _rn = 1 "
        f"ORDER BY tradingsymbol LIMIT ?"
    )
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            sql, key_args + args + rank_args + [max(1, min(int(limit), 5000))])]
    finally:
        conn.row_factory = None

