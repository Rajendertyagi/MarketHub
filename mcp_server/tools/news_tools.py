"""News MCP tools -- AI-facing read access over the NewsService.

Design: NOT a full RSS reader and NOT a browser. Four focused tools with
a minimal parameter surface; the WebUI News page remains the full reader.
Reads are database-first (fast, no network); news_refresh is the single
explicit network operation. Every response carries freshness metadata and
per-source health so the AI can judge recency without another lookup.

Sentiment pairing is positional (zip by index): NewsService.sentiment()
builds exactly one SentimentResult per article in article order, keyed by
an internal stable hash that MCP must not depend on.

No broker imports; all reads go through the shared NewsService.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.errors import StorageError, ValidationError
from market.models import NewsFilter, RedditPost, RSSEntry
from mcp_server.contract import (
    TOOL_NEWS_GET,
    TOOL_NEWS_LIST,
    TOOL_NEWS_REFRESH,
    TOOL_NEWS_SEARCH,
)
from mcp_server.registry import get_tool_description
from news.service import NEWS_HARD_MAX_LIMIT

logger = logging.getLogger(__name__)

_LIST_DEFAULT_LIMIT = 15
_LIST_MAX_LIMIT = 30
_SEARCH_DEFAULT_LIMIT = 10
_SEARCH_MAX_LIMIT = 20
_DEFAULT_AGE_HOURS = 24
_MAX_AGE_HOURS = 168
_WINDOW_LIMIT = NEWS_HARD_MAX_LIMIT  # cursor paging window (bounded)
_SUMMARY_MAX_CHARS = 320  # token-efficiency: truncate long summaries


def register_news_tools(mcp: Any, services: Any, **kwargs: Any) -> None:
    """Register the 4 news tools (list / search / get / refresh)."""
    news = getattr(services, "news_service", None)
    catalog = getattr(services, "instrument_catalog", None)

    def _require_news() -> Any:
        if news is None:
            raise StorageError("news service not available")
        return news

    def _bounded(value: Any, lo: int, hi: int, label: str) -> int:
        try:
            v = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{label} must be an integer") from None
        if v < lo or v > hi:
            raise ValidationError(f"{label} must be between {lo} and {hi}")
        return v

    def _item_to_dict(article: Any,
                      senti: tuple[str, float] | None) -> dict[str, Any] | None:
        """Compact, token-efficient item shape (stable across tools)."""
        if isinstance(article, RSSEntry):
            item_id = article.guid or article.link or article.title
            link = article.link
            extra: dict[str, Any] = {}
            text = article.summary or ""
        elif isinstance(article, RedditPost):
            item_id = article.permalink or article.url or article.title
            link = article.url or article.permalink
            extra = {"subreddit": article.subreddit,
                     "score": article.score}
            text = article.selftext or ""
        else:
            return None
        summary = (text or "").strip()
        if len(summary) > _SUMMARY_MAX_CHARS:
            summary = summary[:_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0] + " ..."
        label, score = senti if senti is not None else ("neutral", 0.0)
        published = (article.published if isinstance(article, RSSEntry)
                     else article.created_utc)
        sid = article.source_id or ""
        prefix = sid.split("_")[0] if "_" in sid else ""
        source_type = prefix if prefix in ("rss", "reddit") else None
        return {
            "item_id": item_id,
            "headline": article.title,
            "summary": summary or None,
            "link": link,
            "source": article.source_name or article.source_id,
            "source_type": source_type,
            "published": published.isoformat() if published else None,
            "category": getattr(article, "category", None),
            "sentiment": label,
            "sentiment_score": score,
            **extra,
        }

    def _page(items: list[dict[str, Any]], limit: int,
              cursor: str | None) -> tuple[list[dict[str, Any]], bool, str | None]:
        """Slice a cursor page out of the bounded window."""
        start = 0
        if cursor:
            ids = [it["item_id"] for it in items]
            if cursor not in ids:
                raise ValidationError(
                    "unknown cursor -- re-run without cursor for page one")
            start = ids.index(cursor) + 1
        page = items[start:start + limit]
        end = start + len(page)
        has_more = end < len(items)
        return page, has_more, (page[-1]["item_id"] if has_more and page
                                else None)

    def _resolve_symbol(query: str) -> str | None:
        """Best-effort catalog resolution (entity-aware search).

        Returns a canonical tradingsymbol or None. Never raises and
        never blocks the keyword search -- resolution is a hint.
        """
        if catalog is None:
            return None
        try:
            rows = catalog.search(q=query, limit=1)
        except Exception as exc:
            logger.debug("news entity resolution skipped: %s", exc)
            return None
        if not rows:
            return None
        row = rows[0]
        sym = row.get("tradingsymbol") if isinstance(row, dict) else None
        return sym.strip().upper() if sym and sym.strip() else None

    @mcp.tool(name=TOOL_NEWS_LIST,
              description=get_tool_description(TOOL_NEWS_LIST))
    async def news_list_tool(limit: int = _LIST_DEFAULT_LIMIT,
                             max_age_hours: int = _DEFAULT_AGE_HOURS,
                             category: str | None = None,
                             cursor: str | None = None) -> dict[str, Any]:
        """Freshest market headlines: newest-first from the stored database."""
        svc = _require_news()
        limit = _bounded(limit, 1, _LIST_MAX_LIMIT, "limit")
        hours = _bounded(max_age_hours, 1, _MAX_AGE_HOURS, "max_age_hours")
        cats = (category.strip(),) if category and category.strip() else None
        result = await svc.sentiment(NewsFilter(
            limit=_WINDOW_LIMIT, max_age_hours=hours, categories=cats))
        paired = [_item_to_dict(a, (s.sentiment, s.score))
                  for a, s in zip(result.articles, result.sentiments,
                                  strict=False)]
        # Articles without a paired sentiment (should not happen) stay valid.
        items = [d for d in paired
                 if d is not None]
        if len(result.articles) > len(result.sentiments):
            extra = [_item_to_dict(a, None)
                     for a in result.articles[len(result.sentiments):]]
            items.extend(d for d in extra if d is not None)
        page, has_more, next_cursor = _page(items, limit, cursor)
        fresh = await svc.freshness()
        return {
            "status": "ok",
            "count": len(page),
            "items": page,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "latest_published_at": fresh["latest_published_at"],
            "last_successful_fetch": fresh["last_successful_fetch"],
            "freshness": {
                "publish_age_minutes": fresh["publish_age_minutes"],
                "fetch_age_minutes": fresh["fetch_age_minutes"],
            },
            "source_health": fresh["sources"],
            "sources_queried": list(result.sources_queried),
        }

    @mcp.tool(name=TOOL_NEWS_SEARCH,
              description=get_tool_description(TOOL_NEWS_SEARCH))
    async def news_search_tool(query: str,
                               limit: int = _SEARCH_DEFAULT_LIMIT,
                               max_age_hours: int | None = None,
                               cursor: str | None = None) -> dict[str, Any]:
        """Keyword/entity search over stored news headlines and summaries."""
        svc = _require_news()
        q = (query or "").strip()
        if not q:
            raise ValidationError(
                "query must not be empty -- e.g. 'RELIANCE results'")
        limit = _bounded(limit, 1, _SEARCH_MAX_LIMIT, "limit")
        hours = (_bounded(max_age_hours, 1, _MAX_AGE_HOURS, "max_age_hours")
                 if max_age_hours is not None else None)
        symbol = _resolve_symbol(q)
        result = await svc.sentiment(NewsFilter(
            keywords_include=(q,), symbol=symbol, limit=_WINDOW_LIMIT,
            max_age_hours=hours))
        items = [d for d in (_item_to_dict(a, (s.sentiment, s.score))
                             for a, s in zip(result.articles,
                                              result.sentiments,
                                              strict=False))
                 if d is not None]
        page, has_more, next_cursor = _page(items, limit, cursor)
        fresh = await svc.freshness()
        return {
            "status": "ok",
            "query": q,
            "resolved_symbol": symbol,
            "count": len(page),
            "items": page,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "latest_published_at": fresh["latest_published_at"],
            "last_successful_fetch": fresh["last_successful_fetch"],
        }

    @mcp.tool(name=TOOL_NEWS_GET,
              description=get_tool_description(TOOL_NEWS_GET))
    async def news_get_tool(news_id: str) -> dict[str, Any]:
        """Retrieve one stored news item by stable id, with provenance."""
        svc = _require_news()
        nid = (news_id or "").strip()
        if not nid:
            raise ValidationError("news_id must not be empty")
        found = await svc.get_item(nid)
        if found is None:
            raise ValidationError(f"news item not found: {nid}")
        article, senti = found
        item = _item_to_dict(
            article, (senti.sentiment, senti.score) if senti else None)
        return {
            "status": "ok",
            "item": item,
        }

    @mcp.tool(name=TOOL_NEWS_REFRESH,
              description=get_tool_description(TOOL_NEWS_REFRESH))
    async def news_refresh_tool() -> dict[str, Any]:
        """Fetch all enabled sources now; dedupe + store + prune + health."""
        svc = _require_news()
        started = datetime.now(timezone.utc).isoformat()
        summary = await svc.refresh(limit_per_source=50)
        completed = datetime.now(timezone.utc).isoformat()
        seen = int(summary.get("seen", 0))
        inserted = int(summary.get("inserted", 0))
        refreshed = list(summary.get("refreshed", []))
        errors = dict(summary.get("errors", {}))
        fresh = await svc.freshness()
        return {
            "status": "ok",
            "started_at": started,
            "completed_at": completed,
            "sources_attempted": len(refreshed) + len(errors),
            "sources_succeeded": refreshed,
            "sources_failed": sorted(errors),
            "items_seen": seen,
            "items_added": inserted,
            "items_deduplicated": max(0, seen - inserted),
            "latest_published_at": fresh.get("latest_published_at"),
            "errors": errors,
            "refreshed_at": fresh.get("last_successful_fetch"),
            "pruned": int(summary.get("pruned", 0)),
        }
