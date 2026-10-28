"""F&O configuration persistence (schema v19).

Durable user/application configuration for the F&O coverage layer — NOT
generic runtime state (that is ``source_state``). One row (``id = 1``) holds
canonical *selections*; the actual universe, indices and expiries are always
resolved dynamically from the neutral registry + catalog. Nothing here
hardcodes the six indices, a stock list, or expiry dates.

Selection semantics:
  * ``index_labels = []``  -> all canonical F&O indices
  * ``index_labels = [...]`` -> exactly those canonical labels
  * ``stock_mode = 'all'`` -> every catalog F&O stock underlying
  * ``stock_mode = 'selected'`` -> only ``stock_symbols``
  * ``preferred_provider`` / ``fallback_provider`` = provider id or None (auto)
  * ``expiry_policy = 'nearest'`` -> nearest listed expiry
  * ``expiry_policy = 'selected'`` -> ``selected_expiry`` (ISO date)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

STOCK_MODES = ("all", "selected")
EXPIRY_POLICIES = ("nearest", "selected")


def create_fno_config_tables(conn: sqlite3.Connection) -> None:
    """Create the single-row F&O configuration table (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fno_config (
            id                 INTEGER PRIMARY KEY CHECK (id = 1),
            enabled            INTEGER NOT NULL DEFAULT 1,
            index_labels       TEXT NOT NULL DEFAULT '[]',
            stock_mode         TEXT NOT NULL DEFAULT 'all'
                               CHECK (stock_mode IN ('all', 'selected')),
            stock_symbols      TEXT NOT NULL DEFAULT '[]',
            preferred_provider TEXT,
            fallback_provider  TEXT,
            expiry_policy      TEXT NOT NULL DEFAULT 'nearest'
                               CHECK (expiry_policy IN ('nearest', 'selected')),
            selected_expiry    TEXT,
            updated_at         TEXT NOT NULL
        )
    """)


def migrate_v18_to_v19(conn: Any) -> None:
    """Add the F&O configuration table (idempotent)."""
    create_fno_config_tables(conn)
    conn.execute("PRAGMA user_version = 19")
    conn.commit()
    logger.info("migrated v18→v19: added fno_config")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _defaults() -> dict[str, Any]:
    return {
        "enabled": True,
        "index_labels": [],
        "stock_mode": "all",
        "stock_symbols": [],
        "preferred_provider": None,
        "fallback_provider": None,
        "expiry_policy": "nearest",
        "selected_expiry": None,
        "updated_at": None,
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "enabled": bool(row["enabled"]),
        "index_labels": json.loads(row["index_labels"] or "[]"),
        "stock_mode": row["stock_mode"],
        "stock_symbols": json.loads(row["stock_symbols"] or "[]"),
        "preferred_provider": row["preferred_provider"],
        "fallback_provider": row["fallback_provider"],
        "expiry_policy": row["expiry_policy"],
        "selected_expiry": row["selected_expiry"],
        "updated_at": row["updated_at"],
    }


def get_fno_config(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return the persisted F&O configuration (defaults when unset)."""
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM fno_config WHERE id = 1").fetchone()
    finally:
        conn.row_factory = None
    return _row_to_dict(row) if row is not None else _defaults()


def upsert_fno_config(conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    """Insert-or-update the single F&O configuration row (validated)."""
    current = get_fno_config(conn)

    def _pick(name: str, default: Any) -> Any:
        return fields[name] if name in fields else current.get(name, default)

    enabled = bool(_pick("enabled", True))
    index_labels = [str(x).strip().upper()
                    for x in (_pick("index_labels", []) or []) if str(x).strip()]
    stock_mode = str(_pick("stock_mode", "all"))
    if stock_mode not in STOCK_MODES:
        raise ValueError(f"stock_mode must be one of {STOCK_MODES}")
    stock_symbols = [str(x).strip().upper()
                     for x in (_pick("stock_symbols", []) or []) if str(x).strip()]
    preferred = _pick("preferred_provider", None) or None
    fallback = _pick("fallback_provider", None) or None
    expiry_policy = str(_pick("expiry_policy", "nearest"))
    if expiry_policy not in EXPIRY_POLICIES:
        raise ValueError(f"expiry_policy must be one of {EXPIRY_POLICIES}")
    selected_expiry = _pick("selected_expiry", None) or None

    now = _now()
    conn.execute(
        "INSERT INTO fno_config (id, enabled, index_labels, stock_mode, "
        "stock_symbols, preferred_provider, fallback_provider, expiry_policy, "
        "selected_expiry, updated_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET "
        "enabled = excluded.enabled, "
        "index_labels = excluded.index_labels, "
        "stock_mode = excluded.stock_mode, "
        "stock_symbols = excluded.stock_symbols, "
        "preferred_provider = excluded.preferred_provider, "
        "fallback_provider = excluded.fallback_provider, "
        "expiry_policy = excluded.expiry_policy, "
        "selected_expiry = excluded.selected_expiry, "
        "updated_at = excluded.updated_at",
        (1 if enabled else 0, json.dumps(index_labels), stock_mode,
         json.dumps(stock_symbols), preferred, fallback, expiry_policy,
         selected_expiry, now))
    conn.commit()
    return get_fno_config(conn)
