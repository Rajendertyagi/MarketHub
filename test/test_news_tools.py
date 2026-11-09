#!/usr/bin/env python3
"""News layer tests: source-health persistence, item resolution, MCP handlers.

No live network. Handlers run against a real EventStore-backed NewsService;
validation paths use a sentinel fake service.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.errors import ValidationError
from market.models import NewsSourceConfig, RSSEntry


def _mk_store():
    from core.persistence.store import EventStore
    tmp = tempfile.mkdtemp(prefix="newstools_")
    return EventStore(os.path.join(tmp, "events.db")), tmp


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _row(item_id, source_id="rss_x", title="T", url="https://e.example/a",
         published=None, fetched=None,
         summary="S",
         score=None, label=None, guid=None):
    now = _now_iso()
    return {
        "item_id": item_id, "source_id": source_id, "source_type": "rss",
        "category": "finance", "title": title, "summary": "S",
        "url": url, "author": None, "symbols": "",
        "published_at": published or now, "fetched_at": fetched or now,
        "sentiment_score": score, "sentiment_label": label,
        "provider_json": json.dumps({"guid": guid or url}),
        "created_at": fetched or now, "updated_at": fetched or now,
    }


def _svc_with_items(n=2):
    from news.adapters.reddit import RedditAdapter
    from news.adapters.rss import RSSAdapter
    from news.service import NewsService
    store, tmp = _mk_store()
    svc = NewsService(store=store)
    svc.register_adapter(RSSAdapter())
    svc.register_adapter(RedditAdapter())
    svc.upsert_source(NewsSourceConfig(
        source_id="rss_x", name="ET", source_type="rss",
        category="finance", enabled=True,
        config_json={"url": "https://e.example/rss"}))
    store.upsert_news_items([
        _row("h1", title="RBI holds rates",
             summary="Repo unchanged at 5.5%.", url="https://e.example/r"),
        _row("h2", title="RELIANCE results beat",
             summary="Profit jumps on retail.", url="https://e.example/rel"),
    ][:n])
    return svc, store, tmp


def _register(news_service, catalog=None):
    from mcp_server.tools.news_tools import register_news_tools
    fake = SimpleNamespace(tools={})
    fake.tool = (lambda name=None, description=None:
                 (lambda fn: (fake.tools.__setitem__(name, fn), fn)[1]))
    register_news_tools(
        fake, SimpleNamespace(news_service=news_service,
                              instrument_catalog=catalog))
    return fake.tools


# ── Health persistence ──────────────────────────────────────────────────────

def test_health_failed_vs_zero_new_vs_never():
    store, tmp = _mk_store()
    try:
        assert store.get_news_source_health() == []
        assert store.last_news_successful_fetch_at() is None
        store.record_news_source_health(
            source_id="ok_empty", success=True, seen=4, added=0)
        store.record_news_source_health(
            source_id="bad", success=False, error="Timeout: x")
        h = {r["source_id"]: r for r in store.get_news_source_health()}
        assert h["ok_empty"]["last_success_at"] is not None
        assert h["ok_empty"]["last_error"] is None
        assert h["ok_empty"]["items_seen"] == 4
        assert h["ok_empty"]["items_added"] == 0
        assert h["bad"]["last_success_at"] is None
        assert h["bad"]["last_error"] == "Timeout: x"
        assert store.last_news_successful_fetch_at() == (
            h["ok_empty"]["last_success_at"])
        assert store.get_news_source_health("missing") == []
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_freshness_snapshot():
    svc, store, tmp = _svc_with_items()
    try:
        store.record_news_source_health(
            source_id="rss_x", success=True, seen=2, added=2)
        fresh = asyncio.run(svc.freshness())
        # Items are seeded with the current time, so just assert it is set.
        assert fresh["latest_published_at"] is not None
        assert fresh["last_successful_fetch"] is not None
        assert fresh["fetch_age_minutes"] is not None
        assert len(fresh["sources"]) == 1
        assert asyncio.run(svc.last_refresh_at()) == (
            fresh["last_successful_fetch"])
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ── Item resolution ─────────────────────────────────────────────────────────

def test_get_item_by_guid_url_and_pk():
    svc, store, tmp = _svc_with_items()
    try:
        by_url = asyncio.run(svc.get_item("https://e.example/r"))
        assert by_url is not None and by_url[0].title == "RBI holds rates"
        by_pk = asyncio.run(svc.get_item("h1"))
        assert by_pk is not None and by_pk[0].title == "RBI holds rates"
        assert asyncio.run(svc.get_item("nope")) is None
        assert asyncio.run(svc.get_item("")) is None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ── Handler behavior ────────────────────────────────────────────────────────

def test_news_list_shape_and_pagination():
    svc, _, tmp = _svc_with_items()
    try:
        tools = _register(svc)
        out = asyncio.run(tools["news_list"]())
        assert out["status"] == "ok" and out["count"] == 2
        item = out["items"][0]
        for key in ("item_id", "headline", "link", "source",
                    "published", "sentiment", "sentiment_score"):
            assert key in item, key
        assert out["latest_published_at"] and "source_health" in out
        assert out["has_more"] is False and out["next_cursor"] is None
        p1 = asyncio.run(tools["news_list"](limit=1))
        assert p1["has_more"] and p1["next_cursor"]
        p2 = asyncio.run(tools["news_list"](limit=1,
                                            cursor=p1["next_cursor"]))
        assert p2["count"] == 1
        assert p2["items"][0]["item_id"] != p1["items"][0]["item_id"]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_news_search_and_get():
    svc, _, tmp = _svc_with_items()
    try:
        tools = _register(svc)
        s = asyncio.run(tools["news_search"](query="RELIANCE"))
        assert s["status"] == "ok" and s["count"] >= 1
        assert "RELIANCE" in s["items"][0]["headline"]
        g = asyncio.run(
            tools["news_get"](news_id=s["items"][0]["item_id"]))
        assert g["status"] == "ok"
        assert g["item"]["item_id"] == s["items"][0]["item_id"]
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_news_validation_errors():
    svc, _, tmp = _svc_with_items()
    try:
        tools = _register(svc)
        with pytest.raises(ValidationError):
            asyncio.run(tools["news_search"](query="   "))
        with pytest.raises(ValidationError):
            asyncio.run(tools["news_list"](limit=999))
        with pytest.raises(ValidationError):
            asyncio.run(tools["news_get"](news_id="nope"))
        with pytest.raises(ValidationError):
            asyncio.run(tools["news_list"](cursor="bogus"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_news_service_unavailable():
    from core.errors import StorageError
    tools = _register(None)
    with pytest.raises(StorageError):
        asyncio.run(tools["news_list"]())


def test_news_refresh_reports_and_preserves():
    svc, _, tmp = _svc_with_items()
    try:
        tools = _register(svc)
        r = asyncio.run(tools["news_refresh"]())
        assert r["status"] == "ok"
        assert r["sources_attempted"] >= 1
        assert {"items_seen", "items_added",
                "items_deduplicated"} <= set(r)
        assert r["started_at"] and r["completed_at"]
        # Failed fake-URL fetch must preserve stored data.
        assert asyncio.run(tools["news_list"]())["count"] == 2
        health = {h["source_id"]: h
                  for h in asyncio.run(svc.freshness())["sources"]}
        assert "rss_x" in health  # attempt recorded even on failure
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_limit_clamp():
    svc, _, tmp = _svc_with_items()
    try:
        out = asyncio.run(svc.news(
            __import__("market.models", fromlist=["NewsFilter"])
            .NewsFilter(limit=99999)))
        assert len(out.articles) <= 200
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_future_dated_item_rejected():
    from datetime import datetime, timezone

    from news.service import NewsService
    svc, _, tmp = _svc_with_items(n=0)
    try:
        src = NewsSourceConfig(
            source_id="rss_x", name="ET", source_type="rss",
            category="finance", enabled=True,
            config_json={"url": "https://e.example/rss"})
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        fut = RSSEntry(
            source_id="rss_x", source_name="ET", title="Future",
            link="https://e.example/f",
            published=now + timedelta(hours=2), guid="g-fut")
        assert (NewsService._normalize_item(src, fut, now.isoformat())
                is None)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
