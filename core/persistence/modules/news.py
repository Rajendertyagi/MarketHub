"""
News & Sentiment persistence module (N1).

Handles source configuration and article deduplication for the generic
news/sentiment subsystem. All functions operate on an explicit
sqlite3.Connection and are called by EventStore facade methods.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Table creation (idempotent)
# ---------------------------------------------------------------------------


def create_news_tables(conn: Any) -> None:
    """Create news_sources and news_articles tables (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_sources (
            source_id   TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            source_type TEXT NOT NULL,
            category    TEXT NOT NULL DEFAULT '',
            enabled     INTEGER NOT NULL DEFAULT 1,
            config_json TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_sources_type_enabled
        ON news_sources(source_type, enabled)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            article_id  TEXT PRIMARY KEY,
            source_id   TEXT NOT NULL,
            source_name TEXT NOT NULL,
            title       TEXT NOT NULL,
            link        TEXT NOT NULL,
            published   TEXT,
            summary     TEXT,
            author      TEXT,
            guid        TEXT,
            article_json TEXT NOT NULL,
            fetched_at  TEXT NOT NULL,
            FOREIGN KEY (source_id) REFERENCES news_sources(source_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_articles_source_published
        ON news_articles(source_id, published)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_articles_fetched_at
        ON news_articles(fetched_at)
    """)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def migrate_v13_to_v14(conn: Any) -> None:
    """Add news_sources and news_articles tables."""
    create_news_tables(conn)
    conn.execute("PRAGMA user_version = 14")
    conn.commit()
    logger.info("migrated v13→v14: added news_sources + news_articles")


# ---------------------------------------------------------------------------
# Source configuration CRUD
# ---------------------------------------------------------------------------


def upsert_source(conn: Any, *, source_id: str, name: str,
                  source_type: str, category: str, enabled: bool,
                  config_json: dict[str, Any] | None,
                  now_iso: str) -> None:
    """Insert or update a news source configuration."""
    config_blob = json.dumps(config_json) if config_json else None
    conn.execute("""
        INSERT INTO news_sources (source_id, name, source_type, category,
                                  enabled, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            name = excluded.name,
            source_type = excluded.source_type,
            category = excluded.category,
            enabled = excluded.enabled,
            config_json = excluded.config_json,
            updated_at = excluded.updated_at
    """, (source_id, name, source_type, category,
          1 if enabled else 0, config_blob, now_iso, now_iso))


def list_sources(conn: Any, *, enabled_only: bool = False) -> list[dict[str, Any]]:
    """Return all configured news sources."""
    sql = "SELECT source_id, name, source_type, category, enabled, config_json, created_at, updated_at FROM news_sources"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY source_type, name"
    rows = conn.execute(sql).fetchall()
    return [_row_to_source(r) for r in rows]


def get_source(conn: Any, source_id: str) -> dict[str, Any] | None:
    """Return a single source config or None."""
    row = conn.execute(
        "SELECT source_id, name, source_type, category, enabled, config_json, created_at, updated_at "
        "FROM news_sources WHERE source_id = ?", (source_id,)
    ).fetchone()
    return _row_to_source(row) if row else None


def delete_source(conn: Any, source_id: str) -> bool:
    """Delete a news source. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM news_sources WHERE source_id = ?", (source_id,))
    return cur.rowcount > 0


def set_source_enabled(conn: Any, source_id: str, enabled: bool) -> bool:
    """Enable or disable a news source. Returns True if the source existed."""
    now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE news_sources SET enabled = ?, updated_at = ? WHERE source_id = ?",
        (1 if enabled else 0, now_iso, source_id))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Article dedup
# ---------------------------------------------------------------------------


def is_article_seen(conn: Any, article_id: str) -> bool:
    """Check whether an article has already been stored."""
    row = conn.execute(
        "SELECT 1 FROM news_articles WHERE article_id = ? LIMIT 1",
        (article_id,)).fetchone()
    return row is not None


def store_article(conn: Any, *, article_id: str, source_id: str,
                  source_name: str, title: str, link: str,
                  published: str | None, summary: str | None,
                  author: str | None, guid: str | None,
                  article_json: str, fetched_at: str) -> bool:
    """Store an article if not already seen. Returns True if stored."""
    if is_article_seen(conn, article_id):
        return False
    conn.execute("""
        INSERT INTO news_articles
            (article_id, source_id, source_name, title, link, published,
             summary, author, guid, article_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (article_id, source_id, source_name, title, link,
          published, summary, author, guid, article_json, fetched_at))
    return True


def store_articles_batch(conn: Any, articles: list[dict[str, Any]],
                         fetched_at: str) -> int:
    """Store a batch of articles, skipping duplicates. Returns count stored."""
    stored = 0
    for art in articles:
        if store_article(conn, article_id=art["article_id"],
                         source_id=art["source_id"],
                         source_name=art["source_name"],
                         title=art["title"], link=art["link"],
                         published=art.get("published"),
                         summary=art.get("summary"),
                         author=art.get("author"),
                         guid=art.get("guid"),
                         article_json=art.get("article_json", "{}"),
                         fetched_at=fetched_at):
            stored += 1
    return stored


def recent_articles(conn: Any, *, source_id: str | None = None,
                    limit: int = 50) -> list[dict[str, Any]]:
    """Return recent articles, optionally filtered by source."""
    if source_id:
        rows = conn.execute(
            "SELECT article_id, source_id, source_name, title, link, "
            "published, summary, author, guid, article_json, fetched_at "
            "FROM news_articles WHERE source_id = ? "
            "ORDER BY fetched_at DESC LIMIT ?", (source_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT article_id, source_id, source_name, title, link, "
            "published, summary, author, guid, article_json, fetched_at "
            "FROM news_articles ORDER BY fetched_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_article(r) for r in rows]


def prune_old_articles(conn: Any, max_age_days: int) -> int:
    """Delete articles older than max_age_days. Returns count deleted."""
    if max_age_days <= 0:
        return 0
    cur = conn.execute(
        "DELETE FROM news_articles WHERE fetched_at < datetime('now', ?)",
        (f"-{max_age_days} days",))
    return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_source(row: tuple) -> dict[str, Any]:
    config_raw = row[5]
    return {
        "source_id": row[0],
        "name": row[1],
        "source_type": row[2],
        "category": row[3],
        "enabled": bool(row[4]),
        "config_json": json.loads(config_raw) if config_raw else None,
        "created_at": row[6],
        "updated_at": row[7],
    }


def _row_to_article(row: tuple) -> dict[str, Any]:
    return {
        "article_id": row[0],
        "source_id": row[1],
        "source_name": row[2],
        "title": row[3],
        "link": row[4],
        "published": row[5],
        "summary": row[6],
        "author": row[7],
        "guid": row[8],
        "article_json": row[9],
        "fetched_at": row[10],
    }
