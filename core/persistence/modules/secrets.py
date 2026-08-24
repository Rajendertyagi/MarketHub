"""
Secrets table: encrypted long-lived application credentials (v10).

Generic provider/name design — not broker-specific. Values stored here are
CIPHERTEXT produced by the application's EncryptionService (Fernet); this
module deliberately knows nothing about crypto. See app/secrets_store.py.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def create_secrets_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS secrets (
            provider          TEXT NOT NULL,
            name              TEXT NOT NULL,
            encrypted_value   TEXT NOT NULL,
            encryption_scheme TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            PRIMARY KEY (provider, name)
        )
    """)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_secrets(
    conn: sqlite3.Connection,
    provider: str,
    items: dict[str, tuple[str, str]],
) -> None:
    """Transactionally upsert multiple secret rows for one provider.

    ``items`` maps name -> (encrypted_value, encryption_scheme). The whole
    batch commits atomically — readers never observe a partial update
    (e.g. new API key with old API secret).
    """
    now = _now_iso()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for name, (value, scheme) in items.items():
            conn.execute(
                """
                INSERT INTO secrets
                    (provider, name, encrypted_value,
                     encryption_scheme, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, name) DO UPDATE SET
                    encrypted_value   = excluded.encrypted_value,
                    encryption_scheme = excluded.encryption_scheme,
                    updated_at        = excluded.updated_at
                """,
                (provider, name, value, scheme, now, now),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_secret(
    conn: sqlite3.Connection,
    provider: str,
    name: str,
) -> tuple[str, str] | None:
    """Return (encrypted_value, encryption_scheme) or None if absent."""
    row = conn.execute(
        "SELECT encrypted_value, encryption_scheme FROM secrets "
        "WHERE provider = ? AND name = ?",
        (provider, name),
    ).fetchone()
    return (row[0], row[1]) if row is not None else None


def has_secret(conn: sqlite3.Connection, provider: str, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM secrets WHERE provider = ? AND name = ?",
        (provider, name),
    ).fetchone()
    return row is not None


def delete_provider_secrets(
    conn: sqlite3.Connection,
    provider: str,
) -> int:
    cur = conn.execute("DELETE FROM secrets WHERE provider = ?", (provider,))
    conn.commit()
    return cur.rowcount


def migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """Add generic encrypted-secrets table."""
    create_secrets_table(conn)
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    logger.info("migrated v9→v10: added secrets table")
