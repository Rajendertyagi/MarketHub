"""L1 Logging & Diagnostics hardening + News source management tests.

Covers:
  - Logging redaction (Bearer, API keys, passwords, PINs, cookies, URL tokens)
  - Recursive structured redaction (D5: nested dict/list/tuple)
  - Handler lifecycle: dedup, force-reset rebind, no stale globals (D6)
  - Buffer bound and concurrency
  - SSE live delivery (D2) + subscriber cleanup/concurrency (D10)
  - News source CRUD
  - Enable/disable
  - Source deletion + durable tombstones across restart (D3)
  - Exclude-keyword filtering without crash (D1)
  - RSS UTC timestamps (D4)
  - Shared POST/PUT source validation (D9)
  - WebUI static wiring: live filters + no inline onclick (D7/D8)
  - L1 suite registration in run_all.py (D12)
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

def _remove_webui_handlers():
    """Detach all WebUILogHandler instances from the root logger."""
    from core.webui_log_handler import WebUILogHandler
    root = logging.getLogger()
    removed = [h for h in root.handlers if isinstance(h, WebUILogHandler)]
    for h in removed:
        try:
            root.removeHandler(h)
            h.close()
        except Exception:
            pass
    return len(removed)


def _count_webui_handlers():
    from core.webui_log_handler import WebUILogHandler
    root = logging.getLogger()
    return sum(1 for h in root.handlers if isinstance(h, WebUILogHandler))


def test_handler_dedup(r: R) -> None:
    from app.logging_setup import attach_webui_handler
    from core.log_buffer import LogBuffer

    _remove_webui_handlers()

    buf = LogBuffer(max_size=100)
    h1 = attach_webui_handler(buf)
    if h1 is not None:
        r.ok("DEDUP:first_attach")
    else:
        r.fail("DEDUP:first_attach", "handler is None")

    # Second attach rebinds (same instance) instead of duplicating
    buf2 = LogBuffer(max_size=100)
    h2 = attach_webui_handler(buf2)
    if h2 is h1:
        r.ok("DEDUP:second_attach_rebinds")
    else:
        r.fail("DEDUP:second_attach_rebinds", "got different handler")

    if _count_webui_handlers() == 1:
        r.ok("DEDUP:handler_count_eq_1")
    else:
        r.fail("DEDUP:handler_count_eq_1", f"got {_count_webui_handlers()}")

    _remove_webui_handlers()


def test_handler_force_rebind(r: R) -> None:
    """D6: handler survives setup_logging(force=True)-style handler wipe."""
    from app.logging_setup import attach_webui_handler
    from core.log_buffer import LogBuffer

    _remove_webui_handlers()
    buf1 = LogBuffer(max_size=100)
    h1 = attach_webui_handler(buf1)
    if h1 is None:
        r.fail("REBIND:attach", "handler is None")
        return
    r.ok("REBIND:attach")

    # Simulate setup_logging(force=True): all root handlers stripped.
    _remove_webui_handlers()
    if _count_webui_handlers() == 0:
        r.ok("REBIND:wiped")
    else:
        r.fail("REBIND:wiped", "handlers remain")

    # Re-attach must create a FRESH handler bound to the NEW buffer
    # (a stale module-global flag would return None here).
    buf2 = LogBuffer(max_size=100)
    h2 = attach_webui_handler(buf2, broker=None)
    if h2 is not None and h2 is not h1:
        r.ok("REBIND:fresh_after_wipe")
    else:
        r.fail("REBIND:fresh_after_wipe", f"got {h2!r}")

    # Emit routes to the new buffer only, exactly once
    rec = logging.LogRecord(
        name="rebind.test", level=logging.INFO, pathname="t.py",
        lineno=1, msg="rebind probe", args=(), exc_info=None,
    )
    h2.emit(rec)
    if len(buf2) == 1 and len(buf1) == 0:
        r.ok("REBIND:routes_to_new_buffer")
    else:
        r.fail("REBIND:routes_to_new_buffer",
               f"buf1={len(buf1)} buf2={len(buf2)}")

    # Duplicate collapse: plant an extra handler, attach collapses to one
    from core.webui_log_handler import WebUILogHandler
    from core.log_buffer import LogBuffer as _LB
    dup = WebUILogHandler(_LB(max_size=10))
    logging.getLogger().addHandler(dup)
    h3 = attach_webui_handler(buf2)
    if h3 is h2 and _count_webui_handlers() == 1:
        r.ok("REBIND:collapse_duplicates")
    else:
        r.fail("REBIND:collapse_duplicates",
               f"same={h3 is h2} count={_count_webui_handlers()}")

    _remove_webui_handlers()


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

        def list_news_source_tombstones(self):
            from core.persistence.modules.news import list_tombstones
            return list_tombstones(self._conn)

        def upsert_news_items(self, rows):
            from core.persistence.modules.news import upsert_news_items
            n = upsert_news_items(self._conn, rows)
            self._conn.commit()
            return n

        def query_news_items(self, **kw):
            from core.persistence.modules.news import query_news_items
            return query_news_items(self._conn, **kw)

        def update_news_sentiments(self, scored):
            from core.persistence.modules.news import update_news_sentiments
            n = update_news_sentiments(self._conn, scored)
            self._conn.commit()
            return n

        def prune_news_items(self, max_age_days):
            from core.persistence.modules.news import prune_news_items
            n = prune_news_items(self._conn, max_age_days)
            self._conn.commit()
            return n

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
# P1-A — SSE TRANSPORT EXCLUSION
# ===================================================================
def _emit_named(handler, name, msg="probe"):
    rec = logging.LogRecord(
        name=name, level=logging.DEBUG, pathname="t.py",
        lineno=1, msg=msg, args=(), exc_info=None,
    )
    handler.emit(rec)


def test_sse_transport_excluded(r: R) -> None:
    """P1: sse_starlette (+children) never enter the WebUI pipeline."""
    from core.log_buffer import LogBuffer
    from core.webui_log_handler import (
        WebUILogHandler, _EXCLUDED_LOGGER_PREFIXES, _is_excluded_logger)

    if "sse_starlette" not in _EXCLUDED_LOGGER_PREFIXES:
        r.fail("P1:prefix_configured", f"got {_EXCLUDED_LOGGER_PREFIXES}")
    else:
        r.ok("P1:prefix_configured")
    if (_is_excluded_logger("sse_starlette")
            and _is_excluded_logger("sse_starlette.sse")
            and not _is_excluded_logger("app.news")
            and not _is_excluded_logger("sse.test")
            and not _is_excluded_logger(None)):
        r.ok("P1:prefix_matching")
    else:
        r.fail("P1:prefix_matching", "name matching wrong")

    buf = LogBuffer(max_size=100)
    handler = WebUILogHandler(buf, broker=None)
    _emit_named(handler, "sse_starlette", "chunk: data: hello\n\n")
    _emit_named(handler, "sse_starlette.sse", "chunk: data: world\n\n")
    _emit_named(handler, "app.news", "normal application log")
    snap = buf.snapshot(limit=10)
    if len(snap) == 1 and snap[0]["message"] == "normal application log":
        r.ok("P1:transport_ignored_app_flows")
    else:
        r.fail("P1:transport_ignored_app_flows",
               f"got {[s['message'] for s in snap]}")


def test_transport_debug_zero_records(r: R) -> None:
    """P1: a burst of transport debugs yields zero WebUI records."""
    from core.log_buffer import LogBuffer
    from core.webui_log_handler import WebUILogHandler

    buf = LogBuffer(max_size=1000)
    handler = WebUILogHandler(buf, broker=None)
    for i in range(50):
        _emit_named(handler, "sse_starlette.sse", f"chunk: data: ping {i}\n\n")
    for i in range(5):
        _emit_named(handler, "app.worker", f"normal {i}")
    if len(buf) == 5:
        r.ok("P1:burst_contained")
    else:
        r.fail("P1:burst_contained", f"len={len(buf)}")


def test_payload_bounds(r: R) -> None:
    """P1: huge payloads truncated with marker; secrets still redacted."""
    from core.log_buffer import LogBuffer
    from core.webui_log_handler import WebUILogHandler

    buf = LogBuffer(max_size=100)
    handler = WebUILogHandler(buf, broker=None)

    secret = "sk_live_" + "A" * 60
    big_msg = "Bearer " + secret + " " + "x" * 100000
    rec = logging.LogRecord(
        name="app.big", level=logging.INFO, pathname="t.py",
        lineno=1, msg=big_msg, args=(), exc_info=None,
    )
    rec.log_extra = {
        "blob": "y" * 10000,
        "nested": {"level1": {"level2": {"level3": {"level4": {
            "level5": {"level6": {"level7": {"token": "deep"}}}}}}}},
        "biglist": list(range(500)),
    }
    handler.emit(rec)
    snap = buf.snapshot(limit=1)
    if not snap:
        r.fail("P1:bounds_stored", "nothing stored")
        return
    s = snap[0]
    if len(s["message"]) <= 4200 and "…<truncated" in s["message"]:
        r.ok("P1:message_bounded_marked")
    else:
        r.fail("P1:message_bounded_marked", f"len={len(s['message'])}")
    if secret not in repr(s) and "sk_live_AAAA" not in repr(s):
        r.ok("P1:huge_secret_redacted")
    else:
        r.fail("P1:huge_secret_redacted", "raw secret present")
    extra = s.get("extra") or {}
    if len(repr(extra)) < 20000 and "<max-depth-exceeded>" in repr(extra):
        r.ok("P1:extra_bounded_depth")
    else:
        r.fail("P1:extra_bounded_depth", f"extra repr len={len(repr(extra))}")
    biglist = (extra.get("biglist") or [])
    if len(biglist) <= 201:
        r.ok("P1:list_capped")
    else:
        r.fail("P1:list_capped", f"len={len(biglist)}")

    # Huge exception text is bounded too
    try:
        raise RuntimeError("boom " + "z" * 100000)
    except RuntimeError:
        import sys as _sys
        rec2 = logging.LogRecord(
            name="app.exc", level=logging.ERROR, pathname="t.py",
            lineno=1, msg="failed", args=(), exc_info=_sys.exc_info(),
        )
    buf2 = LogBuffer(max_size=10)
    WebUILogHandler(buf2, broker=None).emit(rec2)
    snap2 = buf2.snapshot(limit=1)
    if (snap2 and len(snap2[0].get("exception") or "") <= 8400
            and "…<truncated" in (snap2[0].get("exception") or "")):
        r.ok("P1:exception_bounded")
    else:
        r.fail("P1:exception_bounded",
               f"len={len(snap2[0].get('exception') or '') if snap2 else 'none'}")


# ===================================================================
# P2-B — SOURCE_ID CONTRACT
# ===================================================================

def test_source_id_contract(r: R) -> None:
    """P2: identifier contract accepts normal ids, rejects unsafe ones."""
    from api.news_routes import validate_source_id

    valid = ["rss_moneycontrol", "reddit_indianstockmarket", "a-b_c9",
             "x", "ABC-123_xyz", "a" * 64]
    for sid in valid:
        err = validate_source_id(sid)
        if err is None:
            r.ok(f"P2:accept:{sid[:20]}")
        else:
            r.fail(f"P2:accept:{sid[:20]}", err)

    invalid = ["", "   ", "a/b", "a\\b", "../x", "a/../b", "a b",
               "a" * 65, "a'b", 'x");alert(1);//', "a\u0001b",
               "a\nb", "a:b", "a?b", "a*b", "<tag>", "ünïcode", None, 123]
    for sid in invalid:
        err = validate_source_id(sid)
        if err is not None:
            r.ok(f"P2:reject:{str(sid)[:16]!r}")
        else:
            r.fail(f"P2:reject:{str(sid)[:16]!r}", "accepted unsafe id")


def test_invalid_id_not_persisted_routes(r: R) -> None:
    """P2: invalid ids get 400 on POST/PUT and are never stored."""
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    from api.news_routes import build_news_routes

    # Dedicated service: TestClient serves requests from a portal thread,
    # so the in-memory sqlite handle must allow cross-thread use here.
    # (Production opens a fresh connection per call and is unaffected.)
    import sqlite3 as _sqlite3
    from core.persistence.modules.news import create_news_tables
    from news.service import NewsService
    conn = _sqlite3.connect(":memory:", check_same_thread=False)
    create_news_tables(conn)

    class _ThreadStore:
        def list_news_sources(self, enabled_only=False):
            from core.persistence.modules.news import list_sources
            return list_sources(conn, enabled_only=enabled_only)

        def get_news_source(self, source_id):
            from core.persistence.modules.news import get_source
            return get_source(conn, source_id)

        def upsert_news_source(self, **kw):
            from datetime import datetime, timezone
            from core.persistence.modules.news import upsert_source
            upsert_source(conn, now_iso=datetime.now(timezone.utc).isoformat(),
                          **kw)
            conn.commit()

        def delete_news_source(self, source_id):
            from core.persistence.modules.news import delete_source
            res = delete_source(conn, source_id)
            conn.commit()
            return res

        def set_news_source_enabled(self, source_id, enabled):
            from core.persistence.modules.news import set_source_enabled
            res = set_source_enabled(conn, source_id, enabled)
            conn.commit()
            return res

        def list_news_source_tombstones(self):
            from core.persistence.modules.news import list_tombstones
            return list_tombstones(conn)

    svc = NewsService(_ThreadStore())
    app = Starlette(routes=build_news_routes(svc))
    client = TestClient(app, raise_server_exceptions=False)

    bad = {"source_id": "evil/id", "name": "Evil", "source_type": "rss",
           "category": "t",
           "config_json": {"url": "https://example.com/rss"}}
    resp = client.post("/api/news/sources", json=bad)
    if resp.status_code == 400:
        r.ok("P2:post_slash_400")
    else:
        r.fail("P2:post_slash_400", f"got {resp.status_code}")
    if svc.get_source("evil/id") is None:
        r.ok("P2:post_not_persisted")
    else:
        r.fail("P2:post_not_persisted", "unsafe id stored!")

    bad2 = dict(bad, source_id="bad id!")
    resp = client.post("/api/news/sources", json=bad2)
    if resp.status_code == 400 and svc.get_source("bad id!") is None:
        r.ok("P2:post_space_400_unstored")
    else:
        r.fail("P2:post_space_400_unstored", f"got {resp.status_code}")

    # PUT with unsafe path id → 400 (no slash: slashes 404 at routing).
    resp = client.put("/api/news/sources/bad id!", json={"name": "X"})
    if resp.status_code == 400:
        r.ok("P2:put_unsafe_400")
    else:
        r.fail("P2:put_unsafe_400", f"got {resp.status_code}")

    # Normal CRUD still works through the same routes.
    good = dict(bad, source_id="rss_okid",
                config_json={"url": "https://example.com/rss"})
    resp = client.post("/api/news/sources", json=good)
    if resp.status_code == 200 and svc.get_source("rss_okid") is not None:
        r.ok("P2:routes_crud_ok")
    else:
        r.fail("P2:routes_crud_ok", f"got {resp.status_code}")


# ===================================================================
# P4/P5/P6/P7 — SQLITE NEWS STORE + INGESTION (real EventStore)
# ===================================================================

def _file_store_service(tmpdir, adapters=None, retention_days=30):
    """Real EventStore on a temp file + NewsService. Simulates restart by
    constructing a second EventStore on the same path."""
    from core.persistence.store import EventStore
    from news.service import NewsService
    db_path = os.path.join(tmpdir, "e2e_news.db")
    store = EventStore(db_path)
    svc = NewsService(store, retention_days=retention_days)
    for ad in (adapters or ()):
        svc.register_adapter(ad)
    return store, svc


class _FakeRSS:
    source_type = "rss"

    def __init__(self, items=(), fail=False):
        self._items = list(items)
        self._fail = fail

    async def fetch(self, source, *, since=None, limit=50):
        if self._fail:
            raise RuntimeError("rss boom")
        return list(self._items)[:limit]


class _FakeReddit:
    source_type = "reddit"

    def __init__(self, items=(), fail=False):
        self._items = list(items)
        self._fail = fail

    async def fetch(self, source, *, since=None, limit=50):
        if self._fail:
            raise RuntimeError("reddit boom")
        return list(self._items)[:limit]


def _mk_rss(sid, title, guid, published=None, summary="sum"):
    from market.models import RSSEntry
    return RSSEntry(source_id=sid, source_name=sid, title=title,
                    link=f"https://example.com/{guid}", published=published,
                    summary=summary, author="a", guid=guid)


def _mk_reddit(sid, title, permalink, created=None):
    from market.models import RedditPost
    return RedditPost(source_id=sid, source_name=sid, subreddit="t",
                      title=title, score=3, num_comments=1, author="u",
                      url=f"https://example.com{permalink}",
                      permalink=permalink, created_utc=created,
                      selftext="body", upvote_ratio=0.9)


def _seed_two_sources(svc):
    from market.models import NewsSourceConfig
    svc.upsert_source(NewsSourceConfig(
        source_id="s_rss", name="R", source_type="rss", category="finance",
        enabled=True, config_json={"url": "https://example.com/rss"}))
    svc.upsert_source(NewsSourceConfig(
        source_id="s_red", name="D", source_type="reddit", category="markets",
        enabled=True, config_json={"subreddit": "TestSub"}))


def test_items_migration(r: R) -> None:
    """P4: v15→v16 migration + fresh schema create table/indexes."""
    import sqlite3 as _sqlite3
    from core.persistence.modules import news as _news
    from core.persistence.modules.schema import SCHEMA_VERSION

    if SCHEMA_VERSION == 16:
        r.ok("P4:schema_version_16")
    else:
        r.fail("P4:schema_version_16", f"got {SCHEMA_VERSION}")

    # Simulate a v15 database (tables, no news_items), then migrate.
    conn = _sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE news_sources (source_id TEXT PRIMARY KEY, name TEXT NOT NULL, source_type TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1, config_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("CREATE TABLE news_source_tombstones (source_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)")
    conn.execute("PRAGMA user_version = 15")
    _news.migrate_v15_to_v16(conn)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    idx = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    if ver == 16 and "news_items" in tables:
        r.ok("P4:migrate_table_version")
    else:
        r.fail("P4:migrate_table_version", f"ver={ver} tables={tables}")
    want_idx = {"idx_news_items_source", "idx_news_items_category",
                "idx_news_items_published", "idx_news_items_fetched",
                "idx_news_items_symbols"}
    if want_idx <= idx:
        r.ok("P4:migrate_indexes")
    else:
        r.fail("P4:migrate_indexes", f"missing={want_idx - idx}")
    # Idempotent re-run
    _news.migrate_v15_to_v16(conn)
    if conn.execute("PRAGMA user_version").fetchone()[0] == 16:
        r.ok("P4:migrate_idempotent")
    else:
        r.fail("P4:migrate_idempotent", "version drifted")
    conn.close()

    # Fresh EventStore carries the table too.
    tmp = tempfile.mkdtemp()
    try:
        from core.persistence.store import EventStore
        store = EventStore(os.path.join(tmp, "fresh.db"))
        rows = store.query_news_items(limit=5)
        if rows == []:
            r.ok("P4:fresh_store_items")
        else:
            r.fail("P4:fresh_store_items", f"got {rows}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_ingest_dedup_restart(r: R) -> None:
    """P5: refresh persists; repeats insert nothing; restart retains."""
    from datetime import datetime, timezone
    tmp = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc)
        rss_items = [_mk_rss("s_rss", "Alpha rallies", "g-alpha", now, "bull market"),
                     _mk_rss("s_rss", "Beta steady", "g-beta", now, "flat")]
        red_items = [_mk_reddit("s_red", "Gamma thread", "/r/t/gamma", now)]
        store, svc = _file_store_service(
            tmp, adapters=[_FakeRSS(rss_items), _FakeReddit(red_items)])
        _seed_two_sources(svc)

        first = asyncio.run(svc.refresh())
        if first["inserted"] == 3 and not first["errors"]:
            r.ok("P5:first_refresh_inserts")
        else:
            r.fail("P5:first_refresh_inserts", f"got {first}")

        second = asyncio.run(svc.refresh())
        if second["inserted"] == 0:
            r.ok("P5:repeat_no_dupes")
        else:
            r.fail("P5:repeat_no_dupes", f"got {second}")
        if len(store.query_news_items(limit=100)) == 3:
            r.ok("P5:row_count_stable")
        else:
            r.fail("P5:row_count_stable",
                   f"got {len(store.query_news_items(limit=100))}")

        # Restart: new store+service on the same file.
        from core.persistence.store import EventStore
        from news.service import NewsService
        store2 = EventStore(os.path.join(tmp, "e2e_news.db"))
        svc2 = NewsService(store2)
        rows = store2.query_news_items(limit=100)
        if len(rows) == 3:
            r.ok("P5:restart_retains")
        else:
            r.fail("P5:restart_retains", f"got {len(rows)}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_item_ordering(r: R) -> None:
    """P6: deterministic newest-first ordering with fetched fallback."""
    from datetime import datetime, timedelta, timezone
    tmp = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=2)
        items = [_mk_rss("s_rss", "Old news", "g-old", old),
                 _mk_rss("s_rss", "New news", "g-new", now),
                 _mk_rss("s_rss", "Undated", "g-und", None)]
        store, svc = _file_store_service(tmp, adapters=[_FakeRSS(items)])
        _seed_two_sources(svc)
        asyncio.run(svc.refresh())
        rows = store.query_news_items(limit=10)
        titles = [row["title"] for row in rows
                  if row["source_id"] == "s_rss"]
        # Newest published first; undated (fetched now) sorts with now.
        if titles[0] in ("New news", "Undated") and titles[-1] == "Old news":
            r.ok("P6:ordering")
        else:
            r.fail("P6:ordering", f"got {titles}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_keeps_history(r: R) -> None:
    """P5: deleting a source config preserves its historical items."""
    from datetime import datetime, timezone
    tmp = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc)
        store, svc = _file_store_service(
            tmp, adapters=[_FakeRSS([_mk_rss("s_rss", "Keep me", "g-keep", now)])])
        _seed_two_sources(svc)
        asyncio.run(svc.refresh())
        if not svc.delete_source("s_rss"):
            r.fail("P5:delete_config", "delete returned False")
            return
        r.ok("P5:delete_config")
        rows = [row for row in store.query_news_items(limit=100)
                if row["source_id"] == "s_rss"]
        if len(rows) == 1:
            r.ok("P5:history_preserved")
        else:
            r.fail("P5:history_preserved", f"got {len(rows)} rows")
        if "s_rss" in store.list_news_source_tombstones():
            r.ok("P5:tombstone_recorded")
        else:
            r.fail("P5:tombstone_recorded", "no tombstone")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_disabled_not_refreshed_and_isolation(r: R) -> None:
    """P5/P7: disabled sources skipped; one bad source blocks nothing."""
    from datetime import datetime, timezone
    tmp = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc)
        store, svc = _file_store_service(tmp, adapters=[
            _FakeRSS([_mk_rss("s_rss", "Good", "g-good", now)]),
            _FakeReddit([], fail=True),
        ])
        _seed_two_sources(svc)
        svc.set_source_enabled("s_red", False)
        summary = asyncio.run(svc.refresh())
        if ("s_red" not in summary["refreshed"]
                and summary["inserted"] == 1
                and not summary["errors"]):
            r.ok("P5:disabled_skipped")
        else:
            r.fail("P5:disabled_skipped", f"got {summary}")

        # Re-enable with a failing adapter: good source still ingests.
        svc.set_source_enabled("s_red", True)
        summary2 = asyncio.run(svc.refresh())
        if ("s_red" in summary2["errors"]
                and "s_rss" in summary2["refreshed"]
                and summary2["inserted"] == 0):
            r.ok("P5:failure_isolated")
        else:
            r.fail("P5:failure_isolated", f"got {summary2}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# P8 — RETENTION
# ===================================================================

def test_retention(r: R) -> None:
    """P8: expiry removes old items only; configs/tombstones survive."""
    from datetime import datetime, timedelta, timezone
    tmp = tempfile.mkdtemp()
    try:
        from core.persistence.store import EventStore
        store = EventStore(os.path.join(tmp, "ret.db"))
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=40)).isoformat()
        recent = (now - timedelta(hours=2)).isoformat()
        now_iso = now.isoformat()
        long_ago = (now - timedelta(days=100)).isoformat()
        store.upsert_news_source(source_id="s_keep", name="K",
                                 source_type="rss", category="c", enabled=True,
                                 config_json={"url": "https://example.com/x"})
        store.delete_news_source("s_gone")
        tomb_before = store.list_news_source_tombstones()
        rows = [
            {"item_id": "old1", "source_id": "s_keep", "source_type": "rss",
             "category": "c", "title": "Old", "summary": None, "url": None,
             "author": None, "symbols": "", "published_at": old,
             "fetched_at": old, "sentiment_score": None,
             "sentiment_label": None, "provider_json": None,
             "created_at": old, "updated_at": old},
            {"item_id": "new1", "source_id": "s_keep", "source_type": "rss",
             "category": "c", "title": "New", "summary": None, "url": None,
             "author": None, "symbols": "", "published_at": recent,
             "fetched_at": recent, "sentiment_score": None,
             "sentiment_label": None, "provider_json": None,
             "created_at": recent, "updated_at": recent},
            {"item_id": "und1", "source_id": "s_keep", "source_type": "rss",
             "category": "c", "title": "Undated", "summary": None, "url": None,
             "author": None, "symbols": "", "published_at": None,
             "fetched_at": long_ago, "sentiment_score": None,
             "sentiment_label": None, "provider_json": None,
             "created_at": long_ago, "updated_at": long_ago},
        ]
        if store.upsert_news_items(rows) != 3:
            r.fail("P8:setup", "insert failed")
            return
        r.ok("P8:setup")
        pruned = store.prune_news_items(30)
        remaining = {row["item_id"] for row in store.query_news_items(limit=50)}
        if pruned == 2 and remaining == {"new1"}:
            r.ok("P8:expiry_semantics")
        else:
            r.fail("P8:expiry_semantics",
                   f"pruned={pruned} remaining={remaining}")
        if (store.get_news_source("s_keep") is not None
                and store.list_news_source_tombstones() == tomb_before):
            r.ok("P8:configs_tombstones_untouched")
        else:
            r.fail("P8:configs_tombstones_untouched", "config state changed")
        if store.prune_news_items(0) == 0:
            r.ok("P8:nonpositive_noop")
        else:
            r.fail("P8:nonpositive_noop", "pruned with 0 days")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# P6-E — PERSISTED FILTERING
# ===================================================================

def test_persisted_filters(r: R) -> None:
    """P6: source/category/symbol/include/exclude/age/limit over history."""
    from datetime import datetime, timedelta, timezone
    from market.models import NewsFilter
    tmp = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=3)
        store, svc = _file_store_service(tmp, adapters=[
            _FakeRSS([
                _mk_rss("s_rss", "Nifty bank rally", "g-n1", now, "bull market"),
                _mk_rss("s_rss", "Old crypto crash", "g-n2", old, "bear market"),
            ]),
            _FakeReddit([
                _mk_reddit("s_red", "Bank discussion thread", "/r/t/bank", now),
            ]),
        ])
        _seed_two_sources(svc)
        asyncio.run(svc.refresh())

        async def _q(**kw):
            return await svc.news(NewsFilter(**kw))

        res = asyncio.run(_q(source_ids=("s_rss",)))
        if res.total_count == 2 and all(
                a.source_id == "s_rss" for a in res.articles):
            r.ok("P6F:source")
        else:
            r.fail("P6F:source", f"got {res.total_count}")

        res = asyncio.run(_q(categories=("markets",)))
        if res.total_count == 1:
            r.ok("P6F:category")
        else:
            r.fail("P6F:category", f"got {res.total_count}")

        res = asyncio.run(_q(symbol="bank"))
        if res.total_count == 2:
            r.ok("P6F:symbol")
        else:
            r.fail("P6F:symbol", f"got {res.total_count}")

        res = asyncio.run(_q(keywords_include=("crash",)))
        if res.total_count == 1:
            r.ok("P6F:include")
        else:
            r.fail("P6F:include", f"got {res.total_count}")

        res = asyncio.run(_q(keywords_exclude=("bank",)))
        if res.total_count == 1:
            r.ok("P6F:exclude")
        else:
            r.fail("P6F:exclude", f"got {res.total_count}")

        res = asyncio.run(_q(max_age_hours=48))
        if res.total_count == 2:
            r.ok("P6F:max_age")
        else:
            r.fail("P6F:max_age", f"got {res.total_count}")

        res = asyncio.run(_q(limit=1))
        if res.total_count == 1:
            r.ok("P6F:limit")
        else:
            r.fail("P6F:limit", f"got {res.total_count}")

        res = asyncio.run(_q(categories=("finance",),
                             keywords_include=("bank",),
                             keywords_exclude=("rally",), limit=10))
        if res.total_count == 0:
            r.ok("P6F:combo")
        else:
            r.fail("P6F:combo",
                   f"got {[a.title for a in res.articles]}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_symbols_sql_scope(r: R) -> None:
    """P6: SQL symbols scope matches populated symbols columns."""
    from datetime import datetime, timezone
    tmp = tempfile.mkdtemp()
    try:
        from core.persistence.store import EventStore
        store = EventStore(os.path.join(tmp, "sym.db"))
        now = datetime.now(timezone.utc).isoformat()
        base = {"source_type": "rss", "category": "finance", "summary": None,
                "url": None, "author": None, "published_at": now,
                "fetched_at": now, "sentiment_score": None,
                "sentiment_label": None, "provider_json": None,
                "created_at": now, "updated_at": now}
        store.upsert_news_items([
            {**base, "item_id": "sym1", "source_id": "s1",
             "title": "Alpha", "symbols": ",BANK,NIFTY,"},
            {**base, "item_id": "sym2", "source_id": "s1",
             "title": "Beta", "symbols": ""},
        ])
        got = {row["item_id"] for row in
               store.query_news_items(symbols=["bank"], limit=10)}
        if got == {"sym1"}:
            r.ok("P6F:sql_symbols")
        else:
            r.fail("P6F:sql_symbols", f"got {got}")
        got2 = {row["item_id"] for row in
                store.query_news_items(symbols=["nifty", "bank"], limit=10)}
        if got2 == {"sym1"}:
            r.ok("P6F:sql_symbols_multi")
        else:
            r.fail("P6F:sql_symbols_multi", f"got {got2}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# P9-F — SENTIMENT OVER PERSISTED ITEMS
# ===================================================================

def test_sentiment_persisted_no_dupes(r: R) -> None:
    """P9: repeated ingestion never double-counts sentiment contributions."""
    from datetime import datetime, timezone
    from market.models import NewsFilter
    tmp = tempfile.mkdtemp()
    try:
        now = datetime.now(timezone.utc)
        store, svc = _file_store_service(tmp, adapters=[
            _FakeRSS([
                _mk_rss("s_rss", "Huge rally breakout", "g-pos", now, "soar"),
                _mk_rss("s_rss", "Market flat note", "g-neu", now, "flat"),
            ]),
        ])
        _seed_two_sources(svc)
        asyncio.run(svc.refresh())
        first = asyncio.run(svc.sentiment(NewsFilter(limit=10)))
        asyncio.run(svc.refresh())  # repeat: zero new rows
        second = asyncio.run(svc.sentiment(NewsFilter(limit=10)))
        if (len(first.sentiments) == 2 == len(second.sentiments)
                and [s.sentiment for s in first.sentiments]
                == [s.sentiment for s in second.sentiments]):
            r.ok("P9:stable_across_refresh")
        else:
            r.fail("P9:stable_across_refresh",
                   f"got {len(first.sentiments)}/{len(second.sentiments)}")
        labels = {s.sentiment for s in second.sentiments}
        if labels == {"positive", "neutral"}:
            r.ok("P9:labels")
        else:
            r.fail("P9:labels", f"got {labels}")
        rows = store.query_news_items(limit=10)
        if all(row["sentiment_label"] in ("positive", "neutral")
               for row in rows if row["source_id"] == "s_rss"):
            r.ok("P9:persisted_scores")
        else:
            r.fail("P9:persisted_scores",
                   f"got {[(x['item_id'], x['sentiment_label']) for x in rows]}")

        # Word-boundary behavior preserved: "buyers" is not "buy",
        # while "selloff"/"panic" match as whole words.
        from news.service import NewsService
        label, _score, matched = NewsService._compute_sentiment(
            "buyers face a selloff amid panic")
        if (label == "negative" and "selloff" in matched
                and "panic" in matched and "buy" not in matched):
            r.ok("P9:word_boundary")
        else:
            r.fail("P9:word_boundary", f"got {label} {matched}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# P10/P11/P3-G — ES MODULES / ROUTING / HISTORY UI
# ===================================================================

def test_es_modules(r: R) -> None:
    """P10/P11/P3: module files, imports, router boot, no inline handlers."""
    base = os.path.join(_PROJECT_DIR, "web", "ui", "js")
    mods = {}
    for name in ("app.js", "utils.js", "api.js", "router.js", "logs.js",
                 "news.js", "sources.js"):
        try:
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                mods[name] = fh.read()
        except Exception as exc:
            r.fail(f"G:readable:{name}", str(exc))
            return
    r.ok("G:readable")

    for name in ("utils.js", "api.js", "router.js", "logs.js", "news.js",
                 "sources.js"):
        if "export " in mods[name]:
            r.ok(f"G:exports:{name}")
        else:
            r.fail(f"G:exports:{name}", "no exports")

    for imp in ('from "./router.js"', 'from "./sources.js"',
                'from "./news.js"', 'from "./logs.js"'):
        if imp in mods["app.js"]:
            r.ok(f"G:app_imports:{imp}")
        else:
            r.fail(f"G:app_imports:{imp}", "missing import")

    # Router: single-registration boot covering click + hashchange + load.
    router = mods["router.js"]
    if ("hashchange" in router and "_fire(_currentView())" in router
            and "_routerBound" in router and "onViewEnter" in router):
        r.ok("G:router_boot")
    else:
        r.fail("G:router_boot", "router boot wiring missing")

    # No inline handlers anywhere in the news/sources path.
    if ("onclick=" not in mods["sources.js"]
            and "onclick=" not in mods["news.js"]):
        r.ok("G:no_inline_handlers")
    else:
        r.fail("G:no_inline_handlers", "onclick present")

    # History-first UI: openNews loads sources + history; refresh persists.
    # N-UI1: news.js is a thin compat shim; orchestration lives in
    # features/news/index.js, selection state in features/news/state.js.
    try:
        with open(os.path.join(base, "features", "news", "index.js"),
                  encoding="utf-8") as fh:
            mods["news/index.js"] = fh.read()
    except Exception as exc:
        r.fail("G:readable:features/news/index.js", str(exc))
        return
    if ("openNews" in mods["news.js"]
            and "openNews" in mods["news/index.js"]
            and "/api/news/refresh" in mods["news/index.js"]):
        r.ok("G:history_refresh_flow")
    else:
        r.fail("G:history_refresh_flow", "news flow incomplete")

    # Logs SSE ownership unchanged: exactly one logs stream constructor.
    if mods["logs.js"].count('new EventSource("/api/logs/stream")') == 1:
        r.ok("G:logs_sse_single")
    else:
        r.fail("G:logs_sse_single", "stream count changed")

    # EventSource budget across the phase-2 split: still 3 streams total
    # (market.js + alerts.js + logs.js), none left in app.js. This
    # supersedes the old "3 total, 2 in app.js" layout assertion — the
    # runtime topology is unchanged, only the file layout moved.
    import glob as _glob
    _all = ""
    for _p in _glob.glob(os.path.join(base, "*.js")):
        with open(_p, encoding="utf-8") as _fh:
            _all += _fh.read()
    if _all.count("new EventSource") == 3:
        r.ok("G:eventsource_budget")
    else:
        r.fail("G:eventsource_budget",
               f"total={_all.count('new EventSource')}")

    # index.html loads app as a module (cache-busted).
    try:
        with open(os.path.join(_PROJECT_DIR, "web", "ui", "index.html"),
                  encoding="utf-8") as fh:
            html = fh.read()
    except Exception as exc:
        r.fail("G:html_readable", str(exc))
        return
    if 'type="module"' in html and "/ui/js/app.js?v=" in html:
        r.ok("G:module_script_tag")
    else:
        r.fail("G:module_script_tag", "script tag not modularized")


# ===================================================================
# D1 — EXCLUDE-KEYWORD FILTER REGRESSION
# ===================================================================

def test_exclude_filter_no_crash(r: R) -> None:
    """D1: keywords_exclude must filter without raising ValueError."""
    from market.models import NewsFilter, RSSEntry, RedditPost

    items = [
        RSSEntry(source_id="s1", source_name="S1", title="Nifty rallies today",
                 link="https://x/1", published=None, summary="bull market",
                 author=None, guid="g1"),
        RSSEntry(source_id="s1", source_name="S1", title="Spam coin promo",
                 link="https://x/2", published=None, summary="buy spam now",
                 author=None, guid="g2"),
        RedditPost(source_id="s2", source_name="S2", subreddit="t",
                   title="Bank stocks gain", score=5, num_comments=2,
                   author="u", url="https://x/3", permalink="/r/t/3",
                   created_utc=None, selftext=None, upvote_ratio=None),
    ]
    svc = _make_news_service()
    try:
        out = svc._apply_filters(items, NewsFilter(keywords_exclude=["spam"]))
    except Exception as exc:
        r.fail("D1:no_crash", f"{type(exc).__name__}: {exc}")
        return
    r.ok("D1:no_crash")
    titles = [i.title for i in out]
    if "Spam coin promo" not in titles and len(out) == 2:
        r.ok("D1:actually_excludes")
    else:
        r.fail("D1:actually_excludes", f"got {titles}")

    # Include + exclude combined
    out2 = svc._apply_filters(
        items, NewsFilter(keywords_include=["nifty", "bank", "spam"],
                          keywords_exclude=["spam"]))
    titles2 = [i.title for i in out2]
    if "Spam coin promo" not in titles2 and len(out2) == 2:
        r.ok("D1:include_exclude_combo")
    else:
        r.fail("D1:include_exclude_combo", f"got {titles2}")


# ===================================================================
# D2 — LIVE SSE DELIVERY REGRESSION
# ===================================================================

def test_live_sse_delivery(r: R) -> None:
    """D2: handler.emit must deliver a (redacted) record to SSE subscribers."""
    from core.log_buffer import LogBuffer
    from core.sse_broker import EventBroker
    from core.webui_log_handler import WebUILogHandler

    buf = LogBuffer(max_size=100)
    broker = EventBroker(queue_size=64)
    handler = WebUILogHandler(buf, broker=broker)

    async def _run():
        async with broker.subscribe() as lines:
            rec = logging.LogRecord(
                name="sse.test", level=logging.WARNING, pathname="t.py",
                lineno=1,
                msg="live probe api_key = sk_live0987654321abcdef0987654321",
                args=(), exc_info=None,
            )
            handler.emit(rec)  # running loop: schedules broadcast
            try:
                line = await asyncio.wait_for(anext(lines), timeout=5)
                return line
            except asyncio.TimeoutError:
                return None

    line = asyncio.run(_run())
    if line is None:
        r.fail("D2:live_delivery", "no SSE line received within 5s")
        return
    r.ok("D2:live_delivery")
    if "sk_live0987" not in line and "<redacted>" in line:
        r.ok("D2:payload_redacted")
    else:
        r.fail("D2:payload_redacted", f"got {line[:160]!r}")


# ===================================================================
# D3 — TOMBSTONE / RESTART REGRESSION
# ===================================================================

def test_seed_tombstone_restart(r: R) -> None:
    """D3: deleted seeded sources stay deleted across store restart."""
    from news.service import NewsService
    from core.persistence.modules.news import create_news_tables

    tmp = tempfile.mkdtemp()
    try:
        db_path = os.path.join(tmp, "news_restart.db")

        # Build two service generations over the same DB file
        import news.service as _ns
        from news.adapters.rss import RSSAdapter
        from news.adapters.reddit import RedditAdapter
        from core.persistence.modules import news as _news_mod

        class FileStore:
            def __init__(self, path):
                self._path = path

            def _conn(self):
                c = sqlite3.connect(self._path)
                create_news_tables(c)
                return c

            def list_news_sources(self, enabled_only=False):
                c = self._conn()
                try:
                    return _news_mod.list_sources(c, enabled_only=enabled_only)
                finally:
                    c.close()

            def get_news_source(self, source_id):
                c = self._conn()
                try:
                    return _news_mod.get_source(c, source_id)
                finally:
                    c.close()

            def upsert_news_source(self, **kw):
                from datetime import datetime, timezone
                c = self._conn()
                try:
                    _news_mod.upsert_source(c, now_iso=datetime.now(timezone.utc).isoformat(), **kw)
                    c.commit()
                finally:
                    c.close()

            def delete_news_source(self, source_id):
                c = self._conn()
                try:
                    res = _news_mod.delete_source(c, source_id)
                    c.commit()
                    return res
                finally:
                    c.close()

            def set_news_source_enabled(self, source_id, enabled):
                c = self._conn()
                try:
                    res = _news_mod.set_source_enabled(c, source_id, enabled)
                    c.commit()
                    return res
                finally:
                    c.close()

            def list_news_source_tombstones(self):
                c = self._conn()
                try:
                    return _news_mod.list_tombstones(c)
                finally:
                    c.close()

        defaults = [
            {"source_id": "seed_a", "name": "Seed A", "source_type": "rss",
             "category": "finance", "config_json": {"url": "https://example.com/a"}},
            {"source_id": "seed_b", "name": "Seed B", "source_type": "reddit",
             "category": "markets", "config_json": {"subreddit": "TestSub"}},
        ]

        # Generation 1: seed, then user deletes seed_a
        svc1 = _ns.NewsService(store=FileStore(db_path))
        svc1.register_adapter(RSSAdapter())
        svc1.register_adapter(RedditAdapter())
        svc1.seed_defaults(defaults)
        if svc1.get_source("seed_a") is None:
            r.fail("D3:seeded", "seed_a missing after seed")
            return
        r.ok("D3:seeded")
        svc1.delete_source("seed_a")
        del svc1  # simulate shutdown

        # Generation 2 (restart): seed again — seed_a must NOT reappear
        svc2 = _ns.NewsService(store=FileStore(db_path))
        svc2.register_adapter(RSSAdapter())
        svc2.register_adapter(RedditAdapter())
        svc2.seed_defaults(defaults)
        if svc2.get_source("seed_a") is None and svc2.get_source("seed_b") is not None:
            r.ok("D3:stays_deleted_after_restart")
        else:
            r.fail("D3:stays_deleted_after_restart",
                   f"seed_a={svc2.get_source('seed_a')} seed_b={svc2.get_source('seed_b') is not None}")

        # Explicit re-add revives the id (tombstone cleared)
        from market.models import NewsSourceConfig
        svc2.upsert_source(NewsSourceConfig(
            source_id="seed_a", name="Seed A2", source_type="rss",
            category="finance", enabled=True,
            config_json={"url": "https://example.com/a2"}))
        svc3 = _ns.NewsService(store=FileStore(db_path))
        svc3.seed_defaults(defaults)
        got = svc3.get_source("seed_a")
        if got is not None and got.name == "Seed A2":
            r.ok("D3:explicit_readd_revives")
        else:
            r.fail("D3:explicit_readd_revives", f"got {got}")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ===================================================================
# D4 — RSS UTC TIMESTAMP REGRESSION
# ===================================================================

def test_rss_utc_conversion(r: R) -> None:
    """D4: feedparser UTC tuples convert to exact UTC datetimes."""
    import calendar
    from datetime import datetime, timezone
    from unittest.mock import patch
    from news.adapters.rss import RSSAdapter

    entry = {"published_parsed": (2024, 1, 15, 12, 30, 0, 0, 15, 0)}
    expected = datetime.fromtimestamp(
        calendar.timegm((2024, 1, 15, 12, 30, 0, 0, 15, 0)), tz=timezone.utc)
    got = RSSAdapter._parse_datetime(entry)
    if got == expected and got.tzinfo is not None:
        r.ok("D4:utc_exact")
    else:
        r.fail("D4:utc_exact", f"got {got!r} want {expected!r}")

    # Prove the conversion goes through the UTC-safe function even if the
    # host timezone differs: patch calendar.timegm to observe the call.
    seen = {}
    real_timegm = calendar.timegm

    def _spy(tp):
        seen["called"] = True
        return real_timegm(tp)

    with patch("news.adapters.rss.calendar.timegm", side_effect=_spy):
        RSSAdapter._parse_datetime(entry)
    if seen.get("called"):
        r.ok("D4:uses_timegm")
    else:
        r.fail("D4:uses_timegm", "calendar.timegm was not used")


# ===================================================================
# D5 — RECURSIVE STRUCTURED REDACTION REGRESSION
# ===================================================================

def test_recursive_redaction(r: R) -> None:
    """D5: nested secrets never enter WebUI buffer/SSE output."""
    from core.log_redaction import redact_value, redact_record

    nested = {
        "event": "auth",
        "credentials": {"api_key": "sk_nested_TOPSECRET_1234567890",
                        "user": "alice"},
        "items": [{"token": "tok_nested_SECRET_abcdef123456"},
                  "plain",
                  ("pin", "pin = 999888")],
        "meta": {"note": "Bearer nested_bearer_zzzYYYxxx111222333"},
    }
    out = redact_value(nested)
    blob = repr(out)
    leaked = [s for s in ("sk_nested_TOPSECRET", "tok_nested_SECRET",
                          "nested_bearer_zzzYYYxxx", "999888")
              if s in blob]
    if not leaked and out["items"][1] == "plain" and out["event"] == "auth":
        r.ok("D5:recursive_value")
    else:
        r.fail("D5:recursive_value", f"leaked={leaked} out={blob[:200]}")

    # redact_record recurses into extra
    rec = redact_record({
        "message": "hello",
        "extra": {"session": {"secret": "sess_SUPERSECRET_1234567890abcdef"}},
    })
    if "sess_SUPERSECRET" not in repr(rec["extra"]):
        r.ok("D5:record_extra")
    else:
        r.fail("D5:record_extra", f"got {rec['extra']!r}")

    # Handler path: log_extra secrets must not land in the buffer
    from core.log_buffer import LogBuffer
    from core.webui_log_handler import WebUILogHandler
    buf = LogBuffer(max_size=100)
    handler = WebUILogHandler(buf, broker=None)
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="t.py",
        lineno=1, msg="login attempt", args=(), exc_info=None,
    )
    record.log_extra = {"auth": {"password": "hunter2_hunter2_hunter2_x"},
                        "event": "login"}
    handler.emit(record)
    snap = buf.snapshot(limit=1)
    if snap and "hunter2" not in repr(snap[0]):
        r.ok("D5:handler_extra_redacted")
    else:
        r.fail("D5:handler_extra_redacted", f"got {snap[0] if snap else None!r}")


# ===================================================================
# D9 — SHARED POST/PUT VALIDATION REGRESSION
# ===================================================================

def test_shared_source_validation(r: R) -> None:
    """D9: POST and PUT enforce identical validation rules."""
    from api.news_routes import validate_source_config

    cases = [
        (("bogus", {}), True, "D9:bad_type"),
        (("rss", None), True, "D9:rss_missing_cfg"),
        (("rss", {"url": "ftp://example.com/x"}), True, "D9:rss_bad_scheme"),
        (("rss", {"url": "https://example.com/rss"}), False, "D9:rss_ok"),
        (("reddit", {}), True, "D9:reddit_missing_cfg"),
        (("reddit", {"subreddit": "ab"}), True, "D9:reddit_too_short"),
        (("reddit", {"subreddit": "bad name!"}), True, "D9:reddit_bad_chars"),
        (("reddit", {"subreddit": "IndianStockMarket"}), False, "D9:reddit_ok"),
        (("rss", "not-a-dict"), True, "D9:cfg_not_dict"),
    ]
    for (stype, cfg), expect_err, name in cases:
        err = validate_source_config(stype, cfg)
        if (err is not None) == expect_err:
            r.ok(name)
        else:
            r.fail(name, f"err={err!r}")


# ===================================================================
# D10 — SUBSCRIBER CONCURRENCY REGRESSION
# ===================================================================

def test_sse_thread_churn(r: R) -> None:
    """D10: threaded broadcast + subscribe churn is safe and contained."""
    from core.sse_broker import EventBroker

    broker = EventBroker(queue_size=8)
    errors = []

    async def _churn(n):
        try:
            for _ in range(n):
                async with broker.subscribe():
                    pass
        except Exception as exc:  # noqa: BLE001
            errors.append(f"churn: {exc}")

    def _blast(n):
        try:
            for i in range(n):
                broker.broadcast(f"data: {i}\n\n")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"blast: {exc}")

    async def _run():
        await asyncio.gather(*[_churn(30) for _ in range(4)])

    threads = [threading.Thread(target=_blast, args=(300,)) for _ in range(4)]
    for t in threads:
        t.start()
    asyncio.run(_run())
    for t in threads:
        t.join(timeout=10)

    if not errors and broker.subscriber_count == 0:
        r.ok("D10:churn_safe_and_clean")
    else:
        r.fail("D10:churn_safe_and_clean",
               f"errors={errors[:3]} count={broker.subscriber_count}")

    # Full-queue subscribers are dropped, never block the publisher
    async def _fill():
        async with broker.subscribe():
            for _ in range(50):
                broker.broadcast("data: x\n\n")
            return broker.subscriber_count

    asyncio.run(_fill())
    if broker.subscriber_count == 0:
        r.ok("D10:full_queue_no_leak")
    else:
        r.fail("D10:full_queue_no_leak", f"count={broker.subscriber_count}")


# ===================================================================
# D7/D8 — WEBUI STATIC WIRING REGRESSION
# ===================================================================

def _read_webui(rel: str) -> str | None:
    try:
        with open(os.path.join(_PROJECT_DIR, "web", "ui", "js", rel),
                  encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def test_webui_static_wiring(r: R) -> None:
    """D7/D8: live-filter gating + delegation present, inline JS absent.

    News/Logs UI now lives in ES modules (sources.js / logs.js); app.js
    keeps only orchestration.  Assertions target the owning modules.
    """
    app = _read_webui("app.js")
    sources = _read_webui("sources.js")
    logs = _read_webui("logs.js")
    if app is None or sources is None or logs is None:
        r.fail("WEBUI:readable", "missing js module(s)")
        return
    r.ok("WEBUI:readable")

    blob = app + sources + logs
    if ("window._newsToggle" in blob or "window._newsEdit" in blob
            or "window._newsDelete" in blob):
        r.fail("D8:no_window_handlers", "inline window.* news handlers remain")
    else:
        r.ok("D8:no_window_handlers")
    if 'onclick="window._news' in blob or "onclick='window._news" in blob:
        r.fail("D8:no_inline_onclick", "inline onclick with source id remains")
    else:
        r.ok("D8:no_inline_onclick")
    if "data-news-action" in sources and "_onNewsActionClick" in sources:
        r.ok("D8:delegation_present")
    else:
        r.fail("D8:delegation_present", "data-attribute delegation missing")

    if "_logRecordPassesFilters(record)" in logs:
        r.ok("D7:live_filter_gated")
    else:
        # tolerate formatting drift: check both halves co-occur in onmessage
        i = logs.find("es.onmessage")
        window_ = logs[i:i + 600] if i >= 0 else ""
        if "_logRecordPassesFilters" in window_:
            r.ok("D7:live_filter_gated")
        else:
            r.fail("D7:live_filter_gated", "onmessage bypasses filters")


# ===================================================================
# D12 — RUN_ALL REGISTRATION REGRESSION
# ===================================================================

def test_runall_registered(r: R) -> None:
    """D12: the L1 suite is reachable through test/run_all.py."""
    run_all = os.path.join(_SCRIPT_DIR, "run_all.py")
    try:
        with open(run_all, encoding="utf-8") as fh:
            src = fh.read()
    except Exception as exc:
        r.fail("D12:readable", str(exc))
        return
    if '"l1_logging_news": "test_l1_logging_news.py"' in src:
        r.ok("D12:mapped")
    else:
        r.fail("D12:mapped", "key missing from _TEST_FILES")
    fast_start = src.find('"fast"')
    fast_end = src.find("],", fast_start)
    fast_block = src[fast_start:fast_end] if 0 <= fast_start < fast_end else ""
    if '"l1_logging_news"' in fast_block:
        r.ok("D12:in_fast_group")
    else:
        r.fail("D12:in_fast_group", "not in fast group")


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
    test_handler_force_rebind(r)

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

    print("\n--- D1 Exclude Filter ---")
    test_exclude_filter_no_crash(r)

    print("\n--- D2 Live SSE ---")
    test_live_sse_delivery(r)

    print("\n--- D3 Tombstone Restart ---")
    test_seed_tombstone_restart(r)

    print("\n--- D4 RSS UTC ---")
    test_rss_utc_conversion(r)

    print("\n--- D5 Recursive Redaction ---")
    test_recursive_redaction(r)

    print("\n--- D9 Shared Validation ---")
    test_shared_source_validation(r)

    print("\n--- D10 SSE Concurrency ---")
    test_sse_thread_churn(r)

    print("\n--- D7/D8 WebUI Wiring ---")
    test_webui_static_wiring(r)

    print("\n--- D12 run_all Registration ---")
    test_runall_registered(r)

    print("\n--- P1 SSE Exclusion + Bounds ---")
    test_sse_transport_excluded(r)
    test_transport_debug_zero_records(r)
    test_payload_bounds(r)

    print("\n--- P2 Source-ID Contract ---")
    test_source_id_contract(r)
    test_invalid_id_not_persisted_routes(r)

    print("\n--- P4 Migration ---")
    test_items_migration(r)

    print("\n--- P5 Ingest/Dedup/Restart/Isolation ---")
    test_ingest_dedup_restart(r)
    test_item_ordering(r)
    test_delete_keeps_history(r)
    test_disabled_not_refreshed_and_isolation(r)

    print("\n--- P8 Retention ---")
    test_retention(r)

    print("\n--- P6 Persisted Filters ---")
    test_persisted_filters(r)
    test_symbols_sql_scope(r)

    print("\n--- P9 Sentiment Persisted ---")
    test_sentiment_persisted_no_dupes(r)

    print("\n--- P10/P11/P3 ES Modules ---")
    test_es_modules(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)

    if r.failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
