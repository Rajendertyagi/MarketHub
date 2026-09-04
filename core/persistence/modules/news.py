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
    """Create news_sources, news_articles, tombstones and news_items (idempotent)."""
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_source_tombstones (
            source_id  TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        )
    """)
    create_news_items_table(conn)


def create_news_items_table(conn: Any) -> None:
    """Create the durable fetched-item store (idempotent).

    One row per fetched article/post, keyed by a stable provider-derived
    or deterministic-hash identity (``item_id`` PRIMARY KEY) so repeated
    fetches never duplicate rows.  Deleting a *source configuration*
    intentionally leaves its historical items intact.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS news_items (
            item_id        TEXT PRIMARY KEY,
            source_id      TEXT NOT NULL,
            source_type    TEXT NOT NULL DEFAULT '',
            category       TEXT NOT NULL DEFAULT '',
            title          TEXT NOT NULL,
            summary        TEXT,
            url            TEXT,
            author         TEXT,
            symbols        TEXT NOT NULL DEFAULT '',
            published_at   TEXT,
            fetched_at     TEXT NOT NULL,
            sentiment_score REAL,
            sentiment_label TEXT,
            provider_json  TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_items_source
        ON news_items(source_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_items_category
        ON news_items(category)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_items_published
        ON news_items(published_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_items_fetched
        ON news_items(fetched_at)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_items_symbols
        ON news_items(symbols)
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


def migrate_v14_to_v15(conn: Any) -> None:
    """Add news_source_tombstones so user-deleted sources stay deleted.

    Tombstones are durable deletion markers consulted by seed_defaults().
    Existing installations migrate safely: the table starts empty, so
    currently-present sources are unaffected; only deletions performed
    after this migration are remembered.
    """
    create_news_tables(conn)  # idempotent; adds the tombstones table
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    logger.info("migrated v14→v15: added news_source_tombstones")


def migrate_v15_to_v16(conn: Any) -> None:
    """Add the durable news_items store for fetched article history.

    Existing installations migrate safely: the table starts empty, so
    current source configs, tombstones, and legacy articles are
    untouched; history simply accumulates from the next refresh on.
    """
    create_news_items_table(conn)
    conn.execute("PRAGMA user_version = 16")
    conn.commit()
    logger.info("migrated v15→v16: added news_items")


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
    # An explicit user upsert revives the id: clear any tombstone so a
    # re-added source is treated as live again.
    conn.execute(
        "DELETE FROM news_source_tombstones WHERE source_id = ?",
        (source_id,))


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
    """Delete a news source. Returns True if a row was deleted.

    A durable tombstone is recorded so seed_defaults() will not
    resurrect the id on restart.  Re-adding the id explicitly via
    upsert clears the tombstone.
    """
    cur = conn.execute("DELETE FROM news_sources WHERE source_id = ?", (source_id,))
    if cur.rowcount > 0:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO news_source_tombstones (source_id, deleted_at)"
            " VALUES (?, ?)",
            (source_id, now_iso))
        return True
    return False


def list_tombstones(conn: Any) -> list[str]:
    """Return all tombstoned (user-deleted) source ids."""
    rows = conn.execute("SELECT source_id FROM news_source_tombstones").fetchall()
    return [r[0] for r in rows]


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
# Durable fetched-item store (news_items)
# ---------------------------------------------------------------------------

_ITEM_COLUMNS = (
    "item_id", "source_id", "source_type", "category", "title",
    "summary", "url", "author", "symbols", "published_at", "fetched_at",
    "sentiment_score", "sentiment_label", "provider_json",
    "created_at", "updated_at",
)


def upsert_news_items(conn: Any, rows: list[dict[str, Any]]) -> int:
    """Insert fetched items, skipping already-stored identities.

    Uses ``INSERT OR IGNORE`` on the ``item_id`` PRIMARY KEY so repeated
    fetches of the same feed never duplicate rows.  Returns the number
    of newly inserted rows.
    """
    inserted = 0
    for row in rows:
        if not row.get("item_id") or not row.get("source_id"):
            continue
        cur = conn.execute(f"""
            INSERT OR IGNORE INTO news_items ({", ".join(_ITEM_COLUMNS)})
            VALUES ({", ".join("?" for _ in _ITEM_COLUMNS)})
        """, tuple(row.get(col) for col in _ITEM_COLUMNS))
        inserted += cur.rowcount
    return inserted


def query_news_items(
    conn: Any, *,
    source_ids: list[str] | None = None,
    categories: list[str] | None = None,
    symbols: list[str] | None = None,
    newer_than: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query persisted items, newest published first.

    SQL handles source/category/symbol/age scoping; keyword text
    matching stays application-side (see NewsService).  Ordering is
    deterministic: ``COALESCE(published_at, fetched_at)`` descending,
    then ``item_id`` ascending as a stable tiebreak.  ``limit`` is
    clamped to a sane bound so callers cannot load unbounded history.
    """
    limit = max(1, min(int(limit), 500))
    clauses: list[str] = []
    params: list[Any] = []
    if source_ids:
        clauses.append(
            f"source_id IN ({', '.join('?' for _ in source_ids)})")
        params.extend(source_ids)
    if categories:
        clauses.append(
            f"category IN ({', '.join('?' for _ in categories)})")
        params.extend(categories)
    if symbols:
        sym_clauses = []
        for sym in symbols:
            token = f"%,{sym.upper()},%"
            sym_clauses.append("(',' || UPPER(symbols) || ',' LIKE ?)")
            params.append(token)
        clauses.append("(" + " OR ".join(sym_clauses) + ")")
    if newer_than:
        clauses.append("COALESCE(published_at, fetched_at) >= ?")
        params.append(newer_than)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(f"""
        SELECT {", ".join(_ITEM_COLUMNS)} FROM news_items
        {where}
        ORDER BY COALESCE(published_at, fetched_at) DESC, item_id ASC
        LIMIT ?
    """, params).fetchall()
    cols = _ITEM_COLUMNS
    return [dict(zip(cols, r, strict=True)) for r in rows]


def update_news_sentiments(
    conn: Any, scored: list[tuple[str, float, str]]
) -> int:
    """Persist computed sentiment per item id. Returns rows updated."""
    updated = 0
    for item_id, score, label in scored:
        cur = conn.execute(
            "UPDATE news_items SET sentiment_score = ?, sentiment_label = ?, "
            "updated_at = datetime('now') WHERE item_id = ?",
            (score, label, item_id))
        updated += cur.rowcount
    return updated


def prune_news_items(conn: Any, max_age_days: int,
                     *, batch: int = 2000) -> int:
    """Delete expired items in bounded batches. Returns total deleted.

    Expiry uses ``COALESCE(published_at, fetched_at)`` semantics so
    undated items age out by fetch time.  Source configs and tombstones
    are never touched.
    """
    if max_age_days <= 0:
        return 0
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days))
    cutoff_iso = cutoff.isoformat()
    total = 0
    while True:
        cur = conn.execute("""
            DELETE FROM news_items WHERE item_id IN (
                SELECT item_id FROM news_items
                WHERE COALESCE(published_at, fetched_at) < ?
                ORDER BY COALESCE(published_at, fetched_at) ASC
                LIMIT ?
            )
        """, (cutoff_iso, max(1, batch)))
        if not cur.rowcount:
            break
        total += cur.rowcount
    return total


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
