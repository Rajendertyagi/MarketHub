"""X/Twitter persistence module (schema v23).

Owns:
- x_tweets table (durable tweet cache, snowflake id PK, INSERT OR IGNORE dedup)
- x_config single-row table (non-secret poll settings, fno_config pattern)

Does NOT own:
- alert/rule rows (existing alerts table, source="twitter")
- credential storage (app/secrets_store.py, provider "x")
- feed cursor (existing source_state KV, source_name="twitter")
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "poll_interval_seconds": 120,
    "default_limit": 20,
    "retention_days": 30,
    "cli_path": "",
    "proxy": "",
    "request_delay": 2.0,
}

POLL_INTERVAL_FLOOR = 60
DEFAULT_LIMIT_MAX = 20

_TWEET_COLUMNS = (
    "id", "handle", "author_name", "text", "url",
    "metrics", "is_retweet", "posted_at", "fetched_at",
)


def create_x_twitter_tables(conn: sqlite3.Connection) -> None:
    """Create x_tweets + x_config tables (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS x_tweets (
            id          TEXT PRIMARY KEY,
            handle      TEXT NOT NULL DEFAULT '',
            author_name TEXT NOT NULL DEFAULT '',
            text        TEXT NOT NULL DEFAULT '',
            url         TEXT NOT NULL DEFAULT '',
            metrics     TEXT NOT NULL DEFAULT '{}',
            is_retweet  INTEGER NOT NULL DEFAULT 0,
            posted_at   TEXT,
            fetched_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_x_tweets_posted
        ON x_tweets(posted_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_x_tweets_handle
        ON x_tweets(handle)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_x_tweets_fetched
        ON x_tweets(fetched_at)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS x_config (
            id                    INTEGER PRIMARY KEY CHECK (id = 1),
            enabled               INTEGER NOT NULL DEFAULT 0,
            poll_interval_seconds INTEGER NOT NULL DEFAULT 120,
            default_limit         INTEGER NOT NULL DEFAULT 20,
            retention_days        INTEGER NOT NULL DEFAULT 30,
            cli_path              TEXT NOT NULL DEFAULT '',
            proxy                 TEXT NOT NULL DEFAULT '',
            request_delay         REAL NOT NULL DEFAULT 2.0,
            updated_at            TEXT NOT NULL
        )
    """)


def migrate_v22_to_v23(conn: Any) -> None:
    """Add X/Twitter tables. Existing data untouched; starts empty."""
    create_x_twitter_tables(conn)
    conn.execute("PRAGMA user_version = 23")
    conn.commit()
    logger.info("migrated v22->v23: added x_tweets + x_config")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Tweet cache ──────────────────────────────────────────────────────────────

def upsert_x_tweets(conn: Any, rows: list[dict[str, Any]]) -> list[str]:
    """INSERT OR IGNORE batch (news pattern). Returns ids of NEWLY inserted rows.

    Already-stored ids are no-ops and are NOT returned, so callers can
    distinguish "new this cycle" (publish events) from "already seen".
    """
    new_ids: list[str] = []
    for row in rows:
        tid = (row.get("id") or "").strip()
        if not tid:
            continue
        metrics = row.get("metrics")
        if isinstance(metrics, dict):
            metrics_blob = json.dumps(metrics, ensure_ascii=False, allow_nan=False)
        elif isinstance(metrics, str):
            metrics_blob = metrics
        else:
            metrics_blob = "{}"
        cur = conn.execute("""
            INSERT OR IGNORE INTO x_tweets
                (id, handle, author_name, text, url, metrics,
                 is_retweet, posted_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tid,
            str(row.get("handle") or ""),
            str(row.get("author_name") or ""),
            str(row.get("text") or ""),
            str(row.get("url") or ""),
            metrics_blob,
            1 if row.get("is_retweet") else 0,
            row.get("posted_at"),
            row.get("fetched_at") or _now(),
        ))
        if cur.rowcount:
            new_ids.append(tid)
    conn.commit()
    return new_ids


def list_x_tweets(
    conn: Any, *, limit: int = 20, handle: str | None = None,
    newer_than: str | None = None,
) -> list[dict[str, Any]]:
    """Newest-first tweet cache read. limit clamped to [1, 100]."""
    limit = max(1, min(int(limit or 20), 100))
    clauses: list[str] = []
    params: list[Any] = []
    if handle:
        clauses.append("handle = ?")
        params.append(handle)
    if newer_than:
        clauses.append("COALESCE(posted_at, fetched_at) >= ?")
        params.append(newer_than)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(f"""
        SELECT {", ".join(_TWEET_COLUMNS)} FROM x_tweets
        {where}
        ORDER BY COALESCE(posted_at, fetched_at) DESC, id ASC
        LIMIT ?
    """, params).fetchall()
    return [_row_to_tweet(r) for r in rows]


def get_x_tweet(conn: Any, tweet_id: str) -> dict[str, Any] | None:
    """Fetch one cached tweet by id (None when unknown)."""
    if not tweet_id or not isinstance(tweet_id, str):
        return None
    row = conn.execute(f"""
        SELECT {", ".join(_TWEET_COLUMNS)} FROM x_tweets WHERE id = ? LIMIT 1
    """, (tweet_id,)).fetchone()
    return _row_to_tweet(row) if row else None


def prune_x_tweets(conn: Any, max_age_days: int, *, batch: int = 2000) -> int:
    """Delete expired tweets in bounded batches. Returns total deleted."""
    if max_age_days <= 0:
        return 0
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    total = 0
    while True:
        cur = conn.execute("""
            DELETE FROM x_tweets WHERE id IN (
                SELECT id FROM x_tweets
                WHERE COALESCE(posted_at, fetched_at) < ?
                ORDER BY COALESCE(posted_at, fetched_at) ASC
                LIMIT ?
            )
        """, (cutoff, max(1, batch)))
        if not cur.rowcount:
            break
        total += cur.rowcount
    return total


def _row_to_tweet(row: Any) -> dict[str, Any]:
    cols = _TWEET_COLUMNS
    d = dict(zip(cols, row, strict=True))
    try:
        d["metrics"] = json.loads(d.get("metrics") or "{}")
    except (ValueError, TypeError):
        d["metrics"] = {}
    d["is_retweet"] = bool(d.get("is_retweet"))
    return d


# ── Non-secret poll config (single row, fno_config pattern) ──────────────────

def get_x_config(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return persisted X poll config (defaults when unset)."""
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM x_config WHERE id = 1").fetchone()
    finally:
        conn.row_factory = None
    if row is None:
        return dict(CONFIG_DEFAULTS)
    return {
        "enabled": bool(row["enabled"]),
        "poll_interval_seconds": int(row["poll_interval_seconds"]),
        "default_limit": int(row["default_limit"]),
        "retention_days": int(row["retention_days"]),
        "cli_path": row["cli_path"] or "",
        "proxy": row["proxy"] or "",
        "request_delay": float(row["request_delay"]),
        "updated_at": row["updated_at"],
    }


def upsert_x_config(conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    """Insert-or-update the single X config row (validated, floor/clamp)."""
    current = get_x_config(conn)

    def _pick(name: str, default: Any) -> Any:
        return fields[name] if name in fields else current.get(name, default)

    enabled = bool(_pick("enabled", False))
    try:
        interval = int(_pick("poll_interval_seconds", 120))
    except (ValueError, TypeError):
        interval = 120
    interval = max(POLL_INTERVAL_FLOOR, interval)
    try:
        limit = int(_pick("default_limit", 20))
    except (ValueError, TypeError):
        limit = 20
    limit = max(1, min(DEFAULT_LIMIT_MAX, limit))
    try:
        retention = int(_pick("retention_days", 30))
    except (ValueError, TypeError):
        retention = 30
    retention = max(1, retention)
    cli_path = str(_pick("cli_path", "") or "")
    proxy = str(_pick("proxy", "") or "")
    try:
        delay = float(_pick("request_delay", 2.0))
    except (ValueError, TypeError):
        delay = 2.0
    delay = max(0.0, min(60.0, delay))

    now = _now()
    conn.execute(
        "INSERT INTO x_config (id, enabled, poll_interval_seconds, "
        "default_limit, retention_days, cli_path, proxy, request_delay, "
        "updated_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (id) DO UPDATE SET "
        "enabled = excluded.enabled, "
        "poll_interval_seconds = excluded.poll_interval_seconds, "
        "default_limit = excluded.default_limit, "
        "retention_days = excluded.retention_days, "
        "cli_path = excluded.cli_path, "
        "proxy = excluded.proxy, "
        "request_delay = excluded.request_delay, "
        "updated_at = excluded.updated_at",
        (1 if enabled else 0, interval, limit, retention,
         cli_path, proxy, delay, now))
    conn.commit()
    return get_x_config(conn)
