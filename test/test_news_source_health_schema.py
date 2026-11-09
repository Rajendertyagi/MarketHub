#!/usr/bin/env python3
"""The news source-health write path (schema + self-heal).

Proves the contract the live run violated: the health row is written for
EVERY source, on every refresh, with the house ``updated_at`` column -
on a table that already has the column and on one created by an older
DDL that does not.

Isolated: a temporary SQLite file, no live database.
"""

from __future__ import annotations

import os
import sqlite3
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.modules import news


def _table(sql: str) -> sqlite3.Connection:
    fd, path = __import__("tempfile").mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    conn = sqlite3.connect(path)
    conn.execute(sql)
    conn.commit()
    return conn


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(news_source_health)")]


def test_the_house_schema_carries_updated_at() -> None:
    conn = _table("SELECT 1")
    try:
        news.create_news_tables(conn)
        assert "updated_at" in _columns(conn)
    finally:
        conn.close()


def test_a_write_records_the_attempt_and_the_update_time() -> None:
    conn = _table("SELECT 1")
    try:
        news.create_news_tables(conn)
        news.record_source_health(conn, source_id="s1", success=True,
                                  seen=3, added=1,
                                  now_iso="2026-10-22T04:00:00+00:00")
        row = conn.execute(
            "SELECT last_attempt_at, last_success_at, items_seen, updated_at "
            "FROM news_source_health WHERE source_id = 's1'").fetchone()
        assert row is not None
        assert row[0] == "2026-10-22T04:00:00+00:00"
        assert row[1] == "2026-10-22T04:00:00+00:00"
        assert row[2] == 3
        assert row[3] == "2026-10-22T04:00:00+00:00"
    finally:
        conn.close()


def test_a_failed_attempt_keeps_the_last_success() -> None:
    conn = _table("SELECT 1")
    try:
        news.create_news_tables(conn)
        news.record_source_health(conn, source_id="s1", success=True,
                                  seen=3, added=1,
                                  now_iso="2026-10-22T04:00:00+00:00")
        news.record_source_health(conn, source_id="s1", success=False,
                                  error="HTTP 403", seen=0, added=0,
                                  now_iso="2026-10-22T04:05:00+00:00")
        row = conn.execute(
            "SELECT last_attempt_at, last_success_at, last_error, updated_at "
            "FROM news_source_health WHERE source_id = 's1'").fetchone()
        assert row[0] == "2026-10-22T04:05:00+00:00", "the attempt is newer"
        assert row[1] == "2026-10-22T04:00:00+00:00", "the last good fetch survives"
        assert row[2] == "HTTP 403"
        assert row[3] == "2026-10-22T04:05:00+00:00"
    finally:
        conn.close()


def test_an_older_table_repairs_itself_before_the_write() -> None:
    """A table created without ``updated_at`` heals itself and writes."""
    conn = _table("""
        CREATE TABLE news_source_health (
            source_id      TEXT PRIMARY KEY,
            last_attempt_at TEXT NOT NULL,
            last_success_at TEXT,
            last_error     TEXT,
            items_seen     INTEGER NOT NULL DEFAULT 0,
            items_added    INTEGER NOT NULL DEFAULT 0
        )""")
    try:
        conn.execute("INSERT INTO news_source_health "
                     "(source_id, last_attempt_at) VALUES ('old', 'x')")
        conn.commit()
        news.record_source_health(conn, source_id="old", success=True,
                                  seen=1, added=0,
                                  now_iso="2026-10-22T04:10:00+00:00")
        assert "updated_at" in _columns(conn)
        row = conn.execute(
            "SELECT last_attempt_at, updated_at FROM news_source_health "
            "WHERE source_id = 'old'").fetchone()
        assert row[0] == "2026-10-22T04:10:00+00:00"
        assert row[1] == "2026-10-22T04:10:00+00:00"
    finally:
        conn.close()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
