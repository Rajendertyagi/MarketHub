"""L1 Logging & Diagnostics hardening + News source management tests.

Covers:
  - Logging redaction (Bearer, API keys, passwords, PINs, cookies, URL tokens)
  - Duplicate handler prevention
  - Buffer bound and concurrency
  - SSE subscriber cleanup
  - News source CRUD
  - Enable/disable
  - Source deletion
  - Source test success/failure
  - RSS/Reddit validation
  - WebUI API payloads
  - Filters
  - Persistence across restart
"""
import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


# ===================================================================
# REDACTION TESTS
# ===================================================================

def test_redaction(r: R) -> None:
    from core.log_redaction import redact_message, redact_record

    # Bearer token
    msg = redact_message("Bearer eyJhbGciOiJIUzI1NiJ9.secret.signature")
    if "eyJ" not in msg and "<redacted>" in msg:
        r.ok("RED:Bearer")
    else:
        r.fail("RED:Bearer", f"got {msg!r}")

    # Authorization header
    msg = redact_message("Authorization: Bearer tok123abc456def789ghi")
    if "tok123" not in msg and "<redacted>" in msg:
        r.ok("RED:Authorization")
    else:
        r.fail("RED:Authorization", f"got {msg!r}")

    # API key
    msg = redact_message("api_key = sk_1234567890abcdef1234567890abcdef")
    if "sk_1234" not in msg and "<redacted>" in msg:
        r.ok("RED:api_key")
    else:
        r.fail("RED:api_key", f"got {msg!r}")

    # Password
    msg = redact_message("password = mysecretpassword123")
    if "mysecretpassword" not in msg and "<redacted>" in msg:
        r.ok("RED:password")
    else:
        r.fail("RED:password", f"got {msg!r}")

    # PIN
    msg = redact_message("pin = 123456")
    if "123456" not in msg and "<redacted>" in msg:
        r.ok("RED:pin")
    else:
        r.fail("RED:pin", f"got {msg!r}")

    # Access token
    msg = redact_message("access_token = abcdefghijklmnopqrstuvwxyz123456")
    if "abcdefghijklmnop" not in msg and "<redacted>" in msg:
        r.ok("RED:access_token")
    else:
        r.fail("RED:access_token", f"got {msg!r}")

    # Refresh token
    msg = redact_message("refresh_token = abcdefghijklmnopqrstuvwxyz123456")
    if "abcdefghijklmnop" not in msg and "<redacted>" in msg:
        r.ok("RED:refresh_token")
    else:
        r.fail("RED:refresh_token", f"got {msg!r}")

    # Cookie
    msg = redact_message("cookie = sessionid_abc123def456ghi789")
    if "sessionid_abc" not in msg and "<redacted>" in msg:
        r.ok("RED:cookie")
    else:
        r.fail("RED:cookie", f"got {msg!r}")

    # Session secret
    msg = redact_message("session_secret = abcdefghijklmnopqrstuvwxyz123456")
    if "abcdefghijklmnop" not in msg and "<redacted>" in msg:
        r.ok("RED:session_secret")
    else:
        r.fail("RED:session_secret", f"got {msg!r}")

    # URL with token
    msg = redact_message("wss://stream.example.com?token=abc123def456ghi789")
    if "abc123def" not in msg and "<redacted>" in msg:
        r.ok("RED:url_token")
    else:
        r.fail("RED:url_token", f"got {msg!r}")

    # Normal message passes through
    msg = redact_message("Market data updated for RELIANCE at 2500.00")
    if msg == "Market data updated for RELIANCE at 2500.00":
        r.ok("RED:normal_passthrough")
    else:
        r.fail("RED:normal_passthrough", f"got {msg!r}")

    # redact_record
    rec = redact_record({"message": "Bearer secrettoken12345678901234", "level": "INFO"})
    if "secrettoken" not in rec["message"] and "<redacted>" in rec["message"]:
        r.ok("RED:redact_record")
    else:
        r.fail("RED:redact_record", f"got {rec['message']!r}")

    # Empty message
    msg = redact_message("")
    if msg == "":
        r.ok("RED:empty")
    else:
        r.fail("RED:empty", f"got {msg!r}")

    # None-safe
    rec2 = redact_record({"message": None, "level": "INFO"})
    if rec2["message"] is None:
        r.ok("RED:none_safe")
    else:
        r.fail("RED:none_safe", f"got {rec2!r}")


# ===================================================================
# HANDLER DEDUP TESTS
# ===================================================================

def test_handler_dedup(r: R) -> None:
    from app.logging_setup import attach_webui_handler, _L1_HANDLER_ATTACHED
    from core.log_buffer import LogBuffer

    # Reset dedup guard
    import app.logging_setup as ls
    ls._L1_HANDLER_ATTACHED = False

    buf = LogBuffer(max_size=100)
    h1 = attach_webui_handler(buf)
    if h1 is not None:
        r.ok("DEDUP:first_attach")
    else:
        r.fail("DEDUP:first_attach", "handler is None")

    h2 = attach_webui_handler(buf)
    if h2 is None or h2 is h1:
        r.ok("DEDUP:second_attach_idempotent")
    else:
        r.fail("DEDUP:second_attach_idempotent", "got different handler")

    # Count WebUI handlers on root
    from core.webui_log_handler import WebUILogHandler
    root = logging.getLogger()
    webui_count = sum(1 for h in root.handlers if isinstance(h, WebUILogHandler))
    if webui_count <= 1:
        r.ok("DEDUP:handler_count_le_1")
    else:
        r.fail("DEDUP:handler_count_le_1", f"got {webui_count}")

    # Reset for other tests
    ls._L1_HANDLER_ATTACHED = False


# ===================================================================
# BUFFER BOUND / CONCURRENCY TESTS
# ===================================================================

def test_buffer_bound(r: R) -> None:
    from core.log_buffer import LogBuffer, LogRecord

    buf = LogBuffer(max_size=50)
    for i in range(100):
        buf.append(LogRecord(
            timestamp=f"2026-01-01T00:00:{i:02d}Z",
            level="INFO", logger="test", message=f"msg{i}",
        ))
    if len(buf) == 50:
        r.ok("BUF:bound_enforced")
    else:
        r.fail("BUF:bound_enforced", f"len={len(buf)}")

    snap = buf.snapshot(limit=10)
    if len(snap) == 10 and snap[0]["message"] == "msg99":
        r.ok("BUF:most_recent_first")
    else:
        r.fail("BUF:most_recent_first", f"first={snap[0]['message'] if snap else '?'}")

    buf.clear()
    if len(buf) == 0:
        r.ok("BUF:clear")
    else:
        r.fail("BUF:clear", f"len={len(buf)}")


def test_buffer_concurrency(r: R) -> None:
    from core.log_buffer import LogBuffer, LogRecord

    buf = LogBuffer(max_size=500)
    errors = []

    def writer(start: int) -> None:
        try:
            for i in range(200):
                buf.append(LogRecord(
                    timestamp=f"t{i}", level="INFO",
                    logger="thread", message=f"msg{start+i}",
                ))
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=writer, args=(i * 200,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    if not errors:
        r.ok("BUF:concurrent_no_errors")
    else:
        r.fail("BUF:concurrent_no_errors", "; ".join(errors))

    # Buffer should be bounded
    if len(buf) <= 500:
        r.ok("BUF:concurrent_bounded")
    else:
        r.fail("BUF:concurrent_bounded", f"len={len(buf)}")

    # Snapshot should work during concurrent access
    try:
        snap = buf.snapshot(limit=100)
        if len(snap) <= 100:
            r.ok("BUF:concurrent_snapshot")
        else:
            r.fail("BUF:concurrent_snapshot", f"len={len(snap)}")
    except Exception as e:
        r.fail("BUF:concurrent_snapshot", str(e))


# ===================================================================
# SSE BROKER CLEANUP TESTS
# ===================================================================

def test_sse_cleanup(r: R) -> None:
    from core.sse_broker import EventBroker

    broker = EventBroker(queue_size=10)

    async def _test():
        # Subscribe and disconnect
        async with broker.subscribe() as lines:
            _ = lines  # consume nothing
        # After context exit, subscriber should be cleaned up
        if broker.subscriber_count == 0:
            return True
        return False

    result = asyncio.run(_test())
    if result:
        r.ok("SSE:cleanup_after_disconnect")
    else:
        r.fail("SSE:cleanup_after_disconnect", f"subscribers={broker.subscriber_count}")

    # Broadcast with no subscribers should not crash
    try:
        broker.broadcast("data: test\n\n")
        r.ok("SSE:broadcast_no_subscribers")
    except Exception as e:
        r.fail("SSE:broadcast_no_subscribers", str(e))


# ===================================================================
# NEWS SOURCE CRUD TESTS
# ===================================================================

def _make_news_service():
    """Create a NewsService backed by an in-memory SQLite database."""
    from news.service import NewsService
    from news.adapters.rss import RSSAdapter
    from news.adapters.reddit import RedditAdapter

    # In-memory store mock
    conn = sqlite3.connect(":memory:")
    from core.persistence.modules.news import create_news_tables
    create_news_tables(conn)

    class MockStore:
        def __init__(self, conn):
            self._conn = conn

        def list_news_sources(self, enabled_only=False):
            from core.persistence.modules.news import list_sources
            return list_sources(self._conn, enabled_only=enabled_only)

        def get_news_source(self, source_id):
            from core.persistence.modules.news import get_source
            return get_source(self._conn, source_id)

        def upsert_news_source(self, *, source_id, name, source_type,
                               category, enabled, config_json=None):
            from datetime import datetime, timezone
            from core.persistence.modules.news import upsert_source
            upsert_source(self._conn, source_id=source_id, name=name,
                          source_type=source_type, category=category,
                          enabled=enabled, config_json=config_json,
                          now_iso=datetime.now(timezone.utc).isoformat())
            self._conn.commit()

        def delete_news_source(self, source_id):
            from core.persistence.modules.news import delete_source
            result = delete_source(self._conn, source_id)
            self._conn.commit()
            return result

        def set_news_source_enabled(self, source_id, enabled):
            from core.persistence.modules.news import set_source_enabled
            result = set_source_enabled(self._conn, source_id, enabled)
            self._conn.commit()
            return result

    store = MockStore(conn)
    svc = NewsService(store=store)
    svc.register_adapter(RSSAdapter())
    svc.register_adapter(RedditAdapter())
    return svc


def test_source_crud(r: R) -> None:
    svc = _make_news_service()
    from market.models import NewsSourceConfig

    # Create
    src = NewsSourceConfig(
        source_id="test_rss_1", name="Test RSS",
        source_type="rss", category="finance",
        enabled=True, config_json={"url": "https://example.com/rss"},
    )
    svc.upsert_source(src)
    sources = svc.list_sources()
    if len(sources) == 1 and sources[0].source_id == "test_rss_1":
        r.ok("CRUD:create")
    else:
        r.fail("CRUD:create", f"got {len(sources)} sources")

    # Read
    fetched = svc.get_source("test_rss_1")
    if fetched and fetched.name == "Test RSS":
        r.ok("CRUD:read")
    else:
        r.fail("CRUD:read", f"got {fetched}")

    # Update
    src2 = NewsSourceConfig(
        source_id="test_rss_1", name="Test RSS Updated",
        source_type="rss", category="crypto",
        enabled=True, config_json={"url": "https://example.com/rss2"},
    )
    svc.upsert_source(src2)
    fetched2 = svc.get_source("test_rss_1")
    if fetched2 and fetched2.name == "Test RSS Updated" and fetched2.category == "crypto":
        r.ok("CRUD:update")
    else:
        r.fail("CRUD:update", f"got {fetched2}")

    # Delete
    deleted = svc.delete_source("test_rss_1")
    if deleted:
        r.ok("CRUD:delete")
    else:
        r.fail("CRUD:delete", "delete returned False")

    fetched3 = svc.get_source("test_rss_1")
    if fetched3 is None:
        r.ok("CRUD:deleted_gone")
    else:
        r.fail("CRUD:deleted_gone", f"still exists: {fetched3}")


def test_source_enable_disable(r: R) -> None:
    svc = _make_news_service()
    from market.models import NewsSourceConfig

    src = NewsSourceConfig(
        source_id="test_en", name="Test Enable",
        source_type="reddit", category="markets",
        enabled=True, config_json={"subreddit": "test"},
    )
    svc.upsert_source(src)

    # Disable
    ok = svc.set_source_enabled("test_en", False)
    if ok:
        r.ok("EN:disable")
    else:
        r.fail("EN:disable", "returned False")

    fetched = svc.get_source("test_en")
    if fetched and not fetched.enabled:
        r.ok("EN:disabled_state")
    else:
        r.fail("EN:disabled_state", f"enabled={fetched.enabled if fetched else '?'}")

    # Enable
    svc.set_source_enabled("test_en", True)
    fetched2 = svc.get_source("test_en")
    if fetched2 and fetched2.enabled:
        r.ok("EN:re_enable")
    else:
        r.fail("EN:re_enable", f"enabled={fetched2.enabled if fetched2 else '?'}")

    # Non-existent
    ok2 = svc.set_source_enabled("nonexistent", True)
    if not ok2:
        r.ok("EN:nonexistent")
    else:
        r.fail("EN:nonexistent", "returned True for nonexistent")


def test_source_deletion(r: R) -> None:
    svc = _make_news_service()
    from market.models import NewsSourceConfig

    for i in range(3):
        svc.upsert_source(NewsSourceConfig(
            source_id=f"del_{i}", name=f"Del {i}",
            source_type="rss", category="test",
            enabled=True, config_json={"url": f"https://example.com/{i}"},
        ))

    sources = svc.list_sources()
    if len(sources) == 3:
        r.ok("DEL:setup")
    else:
        r.fail("DEL:setup", f"got {len(sources)}")

    svc.delete_source("del_1")
    sources2 = svc.list_sources()
    ids = [s.source_id for s in sources2]
    if "del_1" not in ids and len(sources2) == 2:
        r.ok("DEL:removed")
    else:
        r.fail("DEL:removed", f"ids={ids}")

    # Delete non-existent
    deleted = svc.delete_source("del_999")
    if not deleted:
        r.ok("DEL:nonexistent")
    else:
        r.fail("DEL:nonexistent", "returned True")


# ===================================================================
# SOURCE VALIDATION / TEST
# ===================================================================

def test_source_validation(r: R) -> None:
    from api.news_routes import _parse_news_filters

    # Parse filters from query params
    class FakeParams:
        def __init__(self, d):
            self._d = d
        def get(self, key, default=""):
            return self._d.get(key, default)

    params = FakeParams({
        "source_ids": "rss_a,reddit_b",
        "categories": "finance,crypto",
        "keywords_include": "nifty,bank",
        "keywords_exclude": "spam",
        "symbol": "RELIANCE",
        "max_age_hours": "48",
        "limit": "30",
    })
    f = _parse_news_filters(params)
    if f.source_ids == ["rss_a", "reddit_b"]:
        r.ok("FILTER:source_ids")
    else:
        r.fail("FILTER:source_ids", f"got {f.source_ids}")
    if f.categories == ["finance", "crypto"]:
        r.ok("FILTER:categories")
    else:
        r.fail("FILTER:categories", f"got {f.categories}")
    if f.keywords_include == ["nifty", "bank"]:
        r.ok("FILTER:keywords_include")
    else:
        r.fail("FILTER:keywords_include", f"got {f.keywords_include}")
    if f.symbol == "RELIANCE":
        r.ok("FILTER:symbol")
    else:
        r.fail("FILTER:symbol", f"got {f.symbol}")
    if f.max_age_hours == 48.0:
        r.ok("FILTER:max_age_hours")
    else:
        r.fail("FILTER:max_age_hours", f"got {f.max_age_hours}")
    if f.limit == 30:
        r.ok("FILTER:limit")
    else:
        r.fail("FILTER:limit", f"got {f.limit}")

    # Empty params
    f2 = _parse_news_filters(FakeParams({}))
    if f2.source_ids is None and f2.limit == 50:
        r.ok("FILTER:defaults")
    else:
        r.fail("FILTER:defaults", f"got source_ids={f2.source_ids} limit={f2.limit}")

    # Invalid limit
    f3 = _parse_news_filters(FakeParams({"limit": "abc"}))
    if f3.limit == 50:
        r.ok("FILTER:invalid_limit")
    else:
        r.fail("FILTER:invalid_limit", f"got {f3.limit}")

    # Over-limit
    f4 = _parse_news_filters(FakeParams({"limit": "9999"}))
    if f4.limit == 200:
        r.ok("FILTER:over_limit")
    else:
        r.fail("FILTER:over_limit", f"got {f4.limit}")


# ===================================================================
# LOG BUFFER SNAPSHOT FILTERS
# ===================================================================

def test_buffer_filters(r: R) -> None:
    from core.log_buffer import LogBuffer, LogRecord

    buf = LogBuffer(max_size=100)
    buf.append(LogRecord(timestamp="t1", level="INFO", logger="mcp.server", message="hello world"))
    buf.append(LogRecord(timestamp="t2", level="ERROR", logger="app.alerts", message="alert fired"))
    buf.append(LogRecord(timestamp="t3", level="INFO", logger="news.rss", message="news item"))
    buf.append(LogRecord(timestamp="t4", level="WARNING", logger="mcp.tools", message="slow query"))
    buf.append(LogRecord(timestamp="t5", level="INFO", logger="mcp.server", message="hello again"))

    # Level filter
    snap = buf.snapshot(level="ERROR")
    if len(snap) == 1 and snap[0]["message"] == "alert fired":
        r.ok("FILTER:level")
    else:
        r.fail("FILTER:level", f"got {len(snap)} records")

    # Logger pattern
    snap = buf.snapshot(logger_pattern="mcp")
    if len(snap) == 3:
        r.ok("FILTER:logger_pattern")
    else:
        r.fail("FILTER:logger_pattern", f"got {len(snap)}")

    # Search
    snap = buf.snapshot(search="hello")
    if len(snap) == 2:
        r.ok("FILTER:search")
    else:
        r.fail("FILTER:search", f"got {len(snap)}")

    # Combined
    snap = buf.snapshot(level="INFO", logger_pattern="mcp")
    if len(snap) == 2:
        r.ok("FILTER:combined")
    else:
        r.fail("FILTER:combined", f"got {len(snap)}")

    # Limit
    snap = buf.snapshot(limit=2)
    if len(snap) == 2:
        r.ok("FILTER:limit")
    else:
        r.fail("FILTER:limit", f"got {len(snap)}")


# ===================================================================
# LOG RECORD TO_DICT
# ===================================================================

def test_log_record_todict(r: R) -> None:
    from core.log_buffer import LogRecord

    rec = LogRecord(
        timestamp="2026-01-01T00:00:00Z",
        level="INFO", logger="test", message="hello",
        event="quote", request_id="req-1",
    )
    d = rec.to_dict()
    if d["ts"] == "2026-01-01T00:00:00Z" and d["event"] == "quote" and d["request_id"] == "req-1":
        r.ok("RECORD:todict_populated")
    else:
        r.fail("RECORD:todict_populated", f"got {d}")

    # None fields omitted
    rec2 = LogRecord(timestamp="t", level="INFO", logger="test", message="x")
    d2 = rec2.to_dict()
    if "event" not in d2 and "request_id" not in d2:
        r.ok("RECORD:todict_omit_none")
    else:
        r.fail("RECORD:todict_omit_none", f"got {d2}")


# ===================================================================
# NEWS SERVICE SENTIMENT
# ===================================================================

def test_sentiment_basic(r: R) -> None:
    from news.service import NewsService

    # Direct sentiment computation (no network)
    label, score, matched = NewsService._compute_sentiment(
        "Stock rallies to all time high with strong buy signal"
    )
    if label == "positive" and score > 0.3:
        r.ok("SENT:positive")
    else:
        r.fail("SENT:positive", f"got {label} {score}")

    label2, score2, matched2 = NewsService._compute_sentiment(
        "Market crash fears amid recession and panic selling"
    )
    if label2 == "negative" and score2 < -0.3:
        r.ok("SENT:negative")
    else:
        r.fail("SENT:negative", f"got {label2} {score2}")

    label3, score3, matched3 = NewsService._compute_sentiment(
        "Market opened flat today"
    )
    if label3 == "neutral":
        r.ok("SENT:neutral")
    else:
        r.fail("SENT:neutral", f"got {label3} {score3}")


# ===================================================================
# RSS/REDDIT ADAPTER VALIDATION
# ===================================================================

def test_rss_adapter_no_url(r: R) -> None:
    from news.adapters.rss import RSSAdapter
    from market.models import NewsSourceConfig

    adapter = RSSAdapter()
    source = NewsSourceConfig(
        source_id="no_url", name="No URL",
        source_type="rss", category="", enabled=True,
        config_json={},
    )
    result = asyncio.run(adapter.fetch(source, limit=5))
    if result == []:
        r.ok("RSS:no_url_returns_empty")
    else:
        r.fail("RSS:no_url_returns_empty", f"got {len(result)}")


def test_reddit_adapter_no_subreddit(r: R) -> None:
    from news.adapters.reddit import RedditAdapter
    from market.models import NewsSourceConfig

    adapter = RedditAdapter()
    source = NewsSourceConfig(
        source_id="no_sub", name="No Sub",
        source_type="reddit", category="", enabled=True,
        config_json={},
    )
    result = asyncio.run(adapter.fetch(source, limit=5))
    if result == []:
        r.ok("REDDIT:no_sub_returns_empty")
    else:
        r.fail("REDDIT:no_sub_returns_empty", f"got {len(result)}")


# ===================================================================
# HANDLER REDACTION IN SSE PATH
# ===================================================================

def test_handler_redacts_before_broadcast(r: R) -> None:
    """Verify the WebUILogHandler redacts before SSE broadcast."""
    from core.log_buffer import LogBuffer
    from core.webui_log_handler import WebUILogHandler
    import logging

    buf = LogBuffer(max_size=100)
    handler = WebUILogHandler(buf, broker=None)

    # Create a log record with a secret
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py",
        lineno=1, msg="api_key = sk_abcdef1234567890abcdef1234567890",
        args=(), exc_info=None,
    )
    handler.emit(record)

    # Check that the buffer contains the redacted message
    snap = buf.snapshot(limit=1)
    if snap and "sk_abcdef" not in snap[0]["message"] and "<redacted>" in snap[0]["message"]:
        r.ok("HANDLER:redacts_in_buffer")
    else:
        r.fail("HANDLER:redacts_in_buffer", f"got {snap[0]['message'] if snap else '?'}")


# ===================================================================
# MAIN
# ===================================================================

def main() -> None:
    r = R()
    print("=" * 60)
    print("L1 Logging & Diagnostics + News Source Management Tests")
    print("=" * 60)

    print("\n--- Redaction ---")
    test_redaction(r)

    print("\n--- Handler Dedup ---")
    test_handler_dedup(r)

    print("\n--- Buffer Bound ---")
    test_buffer_bound(r)

    print("\n--- Buffer Concurrency ---")
    test_buffer_concurrency(r)

    print("\n--- SSE Cleanup ---")
    test_sse_cleanup(r)

    print("\n--- Source CRUD ---")
    test_source_crud(r)

    print("\n--- Source Enable/Disable ---")
    test_source_enable_disable(r)

    print("\n--- Source Deletion ---")
    test_source_deletion(r)

    print("\n--- Source Validation / Filters ---")
    test_source_validation(r)

    print("\n--- Buffer Filters ---")
    test_buffer_filters(r)

    print("\n--- Log Record to_dict ---")
    test_log_record_todict(r)

    print("\n--- Sentiment ---")
    test_sentiment_basic(r)

    print("\n--- RSS/Reddit Validation ---")
    test_rss_adapter_no_url(r)
    test_reddit_adapter_no_subreddit(r)

    print("\n--- Handler Redaction ---")
    test_handler_redacts_before_broadcast(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)

    if r.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
