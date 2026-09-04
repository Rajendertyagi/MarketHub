"""REST routes for News/Sentiment source management and news viewing.

Provides:
  Source management:
    GET    /api/news/sources              — list all sources
    POST   /api/news/sources              — create/update source
    PUT    /api/news/sources/{source_id}  — update source
    DELETE /api/news/sources/{source_id}  — delete source
    POST   /api/news/sources/{source_id}/enable   — enable source
    POST   /api/news/sources/{source_id}/disable  — disable source
    POST   /api/news/sources/test         — test source connectivity

  News viewing (persisted SQLite history — fast, no network):
    GET /api/news           — query history with filters
    GET /api/news/sentiment — history with sentiment analysis
    POST /api/news/refresh  — pull enabled sources, persist, prune
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from market.models import NewsFilter, NewsSourceConfig

logger = logging.getLogger(__name__)

_VALID_SOURCE_TYPES = ("rss", "reddit")
_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{3,21}$")

# ── Source-ID contract ──────────────────────────────────────────────────────
# Source ids are identifiers, not paths.  Normal ids look like
# ``rss_moneycontrol`` / ``reddit_indianstockmarket``: letters, digits,
# underscore, hyphen.  Anything path-like or unsafe (``/``, ``\\``,
# control characters, traversal segments, …) is rejected with a clear
# validation error and is NEVER persisted.  Both POST and PUT enforce
# this exact contract.
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_source_id(source_id: Any) -> str | None:
    """Validate a news source id. Returns an error message or None if valid."""
    if not isinstance(source_id, str):
        return "source_id must be a string"
    sid = source_id.strip()
    if not sid:
        return "source_id is required"
    if len(sid) > 64:
        return "source_id must be at most 64 characters"
    if ("/" in sid or "\\" in sid or ".." in sid
            or any(ord(c) < 32 or ord(c) == 127 for c in sid)):
        return ("source_id must not contain path separators, traversal "
                "segments, or control characters")
    if not _SOURCE_ID_RE.match(sid):
        return ("source_id may only contain letters, digits, underscore "
                "and hyphen")
    return None


def validate_source_config(source_type: str,
                           config_json: Any) -> str | None:
    """Validate a source config. Returns an error message or None if valid.

    Shared by POST (create) and PUT (update) so both endpoints enforce
    identical rules:

    - source_type must be ``rss`` or ``reddit``
    - rss requires ``config_json.url`` (http/https URL)
    - reddit requires ``config_json.subreddit`` (valid subreddit name)
    """
    if source_type not in _VALID_SOURCE_TYPES:
        return "source_type must be 'rss' or 'reddit'"
    cfg = config_json or {}
    if not isinstance(cfg, dict):
        return "config_json must be an object"
    if source_type == "rss":
        url = (cfg.get("url") or "")
        if not isinstance(url, str) or not url.strip():
            return "RSS source requires config_json.url"
        scheme = url.strip().lower()
        if not (scheme.startswith("http://") or scheme.startswith("https://")):
            return "RSS config_json.url must be an http(s) URL"
    else:  # reddit
        sub = cfg.get("subreddit") or ""
        if not isinstance(sub, str) or not sub.strip():
            return "Reddit source requires config_json.subreddit"
        if not _SUBREDDIT_RE.match(sub.strip()):
            return ("Reddit config_json.subreddit must be 3-21 chars: "
                    "letters, digits, underscore")
    return None


def build_news_routes(news_service: Any) -> list[Route]:
    """Build the news management and viewing routes.

    Parameters
    ----------
    news_service:
        The NewsService instance for CRUD and fetching.
    """

    # ── Source management ───────────────────────────────────────────────────

    async def _list_sources(request: Request) -> Response:
        """GET /api/news/sources — list all configured sources."""
        try:
            sources = news_service.list_sources()
            rows = [_source_to_dict(s) for s in sources]
            return Response(
                content=json.dumps({"status": "ok", "sources": rows}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("list news sources failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _create_source(request: Request) -> Response:
        """POST /api/news/sources — create or update a news source."""
        try:
            body = await request.json()
        except Exception:
            return Response(
                content=json.dumps({"status": "error", "message": "invalid JSON"}),
                media_type="application/json",
                status_code=400,
            )

        source_id = (body.get("source_id") or "").strip()
        name = (body.get("name") or "").strip()
        source_type = (body.get("source_type") or "").strip()
        category = (body.get("category") or "").strip()
        enabled = body.get("enabled", True)
        config_json = body.get("config_json")

        if not source_id or not name or not source_type:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": "source_id, name, and source_type are required",
                }),
                media_type="application/json",
                status_code=400,
            )

        # Source-id contract (identical rules to PUT): unsafe ids are
        # rejected and never persisted.
        id_error = validate_source_id(source_id)
        if id_error:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": id_error,
                }),
                media_type="application/json",
                status_code=400,
            )

        # Shared validation (identical rules to PUT)
        config_error = validate_source_config(source_type, config_json)
        if config_error:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": config_error,
                }),
                media_type="application/json",
                status_code=400,
            )

        try:
            source = NewsSourceConfig(
                source_id=source_id,
                name=name,
                source_type=source_type,
                category=category,
                enabled=enabled,
                config_json=config_json,
            )
            news_service.upsert_source(source)
            return Response(
                content=json.dumps({
                    "status": "ok",
                    "source": _source_to_dict(source),
                }),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("upsert news source failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _update_source(request: Request) -> Response:
        """PUT /api/news/sources/{source_id} — update an existing source."""
        source_id = request.path_params.get("source_id", "").strip()
        if not source_id:
            return Response(
                content=json.dumps({"status": "error", "message": "missing source_id"}),
                media_type="application/json",
                status_code=400,
            )

        # Source-id contract (identical rules to POST).
        id_error = validate_source_id(source_id)
        if id_error:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": id_error,
                }),
                media_type="application/json",
                status_code=400,
            )

        existing = news_service.get_source(source_id)
        if existing is None:
            return Response(
                content=json.dumps({"status": "error", "message": "source not found"}),
                media_type="application/json",
                status_code=404,
            )

        try:
            body = await request.json()
        except Exception:
            return Response(
                content=json.dumps({"status": "error", "message": "invalid JSON"}),
                media_type="application/json",
                status_code=400,
            )

        # Merge with existing
        name = (body.get("name") or existing.name).strip()
        source_type = (body.get("source_type") or existing.source_type).strip()
        category = (body.get("category") or existing.category).strip()
        enabled = body.get("enabled", existing.enabled)
        config_json = body.get("config_json", existing.config_json)

        # Shared validation (identical rules to POST)
        config_error = validate_source_config(source_type, config_json)
        if config_error:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": config_error,
                }),
                media_type="application/json",
                status_code=400,
            )

        try:
            source = NewsSourceConfig(
                source_id=source_id,
                name=name,
                source_type=source_type,
                category=category,
                enabled=enabled,
                config_json=config_json,
            )
            news_service.upsert_source(source)
            return Response(
                content=json.dumps({
                    "status": "ok",
                    "source": _source_to_dict(source),
                }),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("update news source failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _delete_source(request: Request) -> Response:
        """DELETE /api/news/sources/{source_id} — delete a news source."""
        source_id = request.path_params.get("source_id", "").strip()
        if not source_id:
            return Response(
                content=json.dumps({"status": "error", "message": "missing source_id"}),
                media_type="application/json",
                status_code=400,
            )

        try:
            deleted = news_service.delete_source(source_id)
            if deleted:
                return Response(
                    content=json.dumps({"status": "ok", "deleted": source_id}),
                    media_type="application/json",
                )
            return Response(
                content=json.dumps({"status": "error", "message": "source not found"}),
                media_type="application/json",
                status_code=404,
            )
        except Exception as exc:
            logger.warning("delete news source failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _enable_source(request: Request) -> Response:
        """POST /api/news/sources/{source_id}/enable — enable a news source."""
        return await _set_enabled(request, True)

    async def _disable_source(request: Request) -> Response:
        """POST /api/news/sources/{source_id}/disable — disable a news source."""
        return await _set_enabled(request, False)

    async def _set_enabled(request: Request, enabled: bool) -> Response:
        source_id = request.path_params.get("source_id", "").strip()
        if not source_id:
            return Response(
                content=json.dumps({"status": "error", "message": "missing source_id"}),
                media_type="application/json",
                status_code=400,
            )
        try:
            ok = news_service.set_source_enabled(source_id, enabled)
            if ok:
                return Response(
                    content=json.dumps({
                        "status": "ok",
                        "source_id": source_id,
                        "enabled": enabled,
                    }),
                    media_type="application/json",
                )
            return Response(
                content=json.dumps({"status": "error", "message": "source not found"}),
                media_type="application/json",
                status_code=404,
            )
        except Exception as exc:
            logger.warning("set news source enabled failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    # ── Source test ─────────────────────────────────────────────────────────

    async def _test_source(request: Request) -> Response:
        """POST /api/news/sources/test — test a source configuration.

        Body: { source_type, config_json }
        Returns test result without saving the configuration.
        """
        try:
            body = await request.json()
        except Exception:
            return Response(
                content=json.dumps({"status": "error", "message": "invalid JSON"}),
                media_type="application/json",
                status_code=400,
            )

        source_type = (body.get("source_type") or "").strip()
        config_json = body.get("config_json", {})

        if source_type not in ("rss", "reddit"):
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": "source_type must be 'rss' or 'reddit'",
                }),
                media_type="application/json",
                status_code=400,
            )

        # Create a temporary source config for the test
        temp_source = NewsSourceConfig(
            source_id="__test__",
            name="Test",
            source_type=source_type,
            category="test",
            enabled=True,
            config_json=config_json,
        )

        adapter = news_service.get_adapter(source_type)
        if adapter is None:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "message": f"no adapter registered for type '{source_type}'",
                }),
                media_type="application/json",
                status_code=500,
            )

        try:
            items = await adapter.fetch(temp_source, limit=5)
            if not items:
                return Response(
                    content=json.dumps({
                        "status": "ok",
                        "source_type": source_type,
                        "reachable": True,
                        "items_found": 0,
                        "message": "Source is reachable but returned no items",
                    }),
                    media_type="application/json",
                )

            # Build test summary
            sample_titles = [getattr(i, "title", "") for i in items[:3]]
            return Response(
                content=json.dumps({
                    "status": "ok",
                    "source_type": source_type,
                    "reachable": True,
                    "items_found": len(items),
                    "sample_titles": sample_titles,
                    "message": f"OK — fetched {len(items)} items",
                }),
                media_type="application/json",
            )
        except Exception as exc:
            return Response(
                content=json.dumps({
                    "status": "error",
                    "source_type": source_type,
                    "reachable": False,
                    "message": f"Test failed: {type(exc).__name__}: {exc}",
                }),
                media_type="application/json",
                status_code=200,  # 200 with error status in body
            )

    # ── News viewing ────────────────────────────────────────────────────────

    async def _refresh_sources(request: Request) -> Response:
        """POST /api/news/refresh — pull enabled sources into history.

        Body (optional JSON): { source_ids?: [...], limit_per_source?: N }
        Refreshes only enabled sources; one failing source never blocks
        the others; repeats insert no duplicates (stable identity).
        Retention pruning runs after a successful pass.
        """
        try:
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}

        source_ids = body.get("source_ids")
        if source_ids is not None:
            if not isinstance(source_ids, list):
                return Response(
                    content=json.dumps({
                        "status": "error",
                        "message": "source_ids must be a list",
                    }),
                    media_type="application/json",
                    status_code=400,
                )
            source_ids = [str(s).strip() for s in source_ids if str(s).strip()]
        try:
            limit_per_source = max(
                1, min(int(body.get("limit_per_source", 50)), 100))
        except (ValueError, TypeError):
            limit_per_source = 50

        try:
            summary = await news_service.refresh(
                source_ids or None, limit_per_source=limit_per_source)
            return Response(
                content=json.dumps({"status": "ok", **summary}),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("news refresh failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _get_news(request: Request) -> Response:
        """GET /api/news — query persisted news history with filters.

        Serves SQLite history (fast, no network); use
        POST /api/news/refresh to pull fresh items first.

        Query params:
          source_ids     — comma-separated source IDs
          categories     — comma-separated categories
          keywords_include — comma-separated include keywords
          keywords_exclude — comma-separated exclude keywords
          symbol         — symbol/query search
          max_age_hours  — max age in hours
          limit          — max articles (default 50, max 200)
        """
        try:
            filters = _parse_news_filters(request.query_params)
            result = await news_service.news(filters)
            articles = [_article_to_dict(a) for a in result.articles]
            return Response(
                content=json.dumps({
                    "status": "ok",
                    "count": result.total_count,
                    "sources_queried": list(result.sources_queried),
                    "articles": articles,
                }),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("fetch news failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    async def _get_sentiment(request: Request) -> Response:
        """GET /api/news/sentiment — persisted news with sentiment analysis.

        Operates on SQLite history (deduplicated by stable identity, so
        repeated ingestion never double-counts); scores are persisted
        per item for future consumers.
        """
        try:
            filters = _parse_news_filters(request.query_params)
            result = await news_service.sentiment(filters)
            articles = [_article_to_dict(a) for a in result.articles]
            sentiments = [_sentiment_to_dict(s) for s in (result.sentiments or ())]
            return Response(
                content=json.dumps({
                    "status": "ok",
                    "count": result.total_count,
                    "sources_queried": list(result.sources_queried),
                    "articles": articles,
                    "sentiments": sentiments,
                }),
                media_type="application/json",
            )
        except Exception as exc:
            logger.warning("fetch sentiment failed: %s", exc)
            return Response(
                content=json.dumps({"status": "error", "message": str(exc)}),
                media_type="application/json",
                status_code=500,
            )

    return [
        # Source management
        Route("/api/news/sources", endpoint=_list_sources, methods=["GET"]),
        Route("/api/news/sources", endpoint=_create_source, methods=["POST"]),
        Route("/api/news/sources/{source_id}", endpoint=_update_source, methods=["PUT"]),
        Route("/api/news/sources/{source_id}", endpoint=_delete_source, methods=["DELETE"]),
        Route("/api/news/sources/{source_id}/enable", endpoint=_enable_source, methods=["POST"]),
        Route("/api/news/sources/{source_id}/disable", endpoint=_disable_source, methods=["POST"]),
        Route("/api/news/sources/test", endpoint=_test_source, methods=["POST"]),
        # News viewing (persisted history) + on-demand refresh
        Route("/api/news", endpoint=_get_news, methods=["GET"]),
        Route("/api/news/sentiment", endpoint=_get_sentiment, methods=["GET"]),
        Route("/api/news/refresh", endpoint=_refresh_sources, methods=["POST"]),
    ]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _source_to_dict(source: NewsSourceConfig) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "name": source.name,
        "source_type": source.source_type,
        "category": source.category,
        "enabled": source.enabled,
        "config_json": source.config_json,
        "created_at": source.created_at,
        "updated_at": source.updated_at,
    }


def _article_to_dict(article: Any) -> dict[str, Any]:
    """Convert RSSEntry or RedditPost to a dict for JSON response.

    Wire contract is additive-only: every historical key is preserved,
    plus ``item_id`` (stable provider-derived identity shared with the
    sentiment payload and the durable store).
    """
    from market.models import RSSEntry, RedditPost
    if isinstance(article, RSSEntry):
        return {
            "type": "rss",
            "item_id": article.guid or article.link,
            "source_id": article.source_id,
            "source_name": article.source_name,
            "title": article.title,
            "link": article.link,
            "published": article.published.isoformat() if article.published else None,
            "summary": article.summary,
            "author": article.author,
        }
    elif isinstance(article, RedditPost):
        return {
            "type": "reddit",
            "item_id": article.permalink or article.url or article.title,
            "source_id": article.source_id,
            "source_name": article.source_name,
            "subreddit": article.subreddit,
            "title": article.title,
            "score": article.score,
            "num_comments": article.num_comments,
            "author": article.author,
            "url": article.url,
            "permalink": article.permalink,
            "created_utc": article.created_utc.isoformat() if article.created_utc else None,
            "selftext": article.selftext,
            "upvote_ratio": article.upvote_ratio,
        }
    return {"type": "unknown", "title": getattr(article, "title", "?")}


def _sentiment_to_dict(s: Any) -> dict[str, Any]:
    return {
        "item_id": s.item_id,
        "sentiment": s.sentiment,
        "score": s.score,
        "matched_keywords": list(s.matched_keywords) if s.matched_keywords else [],
    }


def _parse_news_filters(params: Any) -> NewsFilter:
    """Parse query params into a NewsFilter."""
    source_ids_str = params.get("source_ids", "")
    categories_str = params.get("categories", "")
    include_str = params.get("keywords_include", "")
    exclude_str = params.get("keywords_exclude", "")

    source_ids = [s.strip() for s in source_ids_str.split(",") if s.strip()] or None
    categories = [s.strip() for s in categories_str.split(",") if s.strip()] or None
    keywords_include = [s.strip() for s in include_str.split(",") if s.strip()] or None
    keywords_exclude = [s.strip() for s in exclude_str.split(",") if s.strip()] or None

    symbol = params.get("symbol", "").strip() or None
    max_age_hours = None
    limit = 50

    try:
        v = params.get("max_age_hours")
        if v:
            max_age_hours = float(v)
    except (ValueError, TypeError):
        pass

    try:
        v = params.get("limit")
        if v:
            limit = max(1, min(int(v), 200))
    except (ValueError, TypeError):
        pass

    return NewsFilter(
        source_ids=source_ids,
        categories=categories,
        keywords_include=keywords_include,
        keywords_exclude=keywords_exclude,
        symbol=symbol,
        max_age_hours=max_age_hours,
        limit=limit,
    )
