"""Generic News & Sentiment service (N1 foundation).

Service-level business logic — adapter dispatch, deduplication, filtering,
and sentiment keyword matching.  MCP tools, WebUI, and alerts will consume
this service; MCP does NOT own this logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any

from market.models import (
    NewsFilter,
    NewsResult,
    NewsSourceConfig,
    RedditPost,
    RSSEntry,
    SentimentResult,
)

logger = logging.getLogger(__name__)

# ── Default sentiment keywords ──────────────────────────────────────────────
# Weighted keywords: positive → +score, negative → -score.
# Word-boundary matching is used (not substring).

_BULLISH_KEYWORDS: dict[str, float] = {
    "rally": 0.6, "surge": 0.7, "bullish": 0.8, "breakout": 0.6,
    "upgrade": 0.5, "outperform": 0.5, "buy": 0.4, "accumulate": 0.5,
    "strong buy": 0.7, "all time high": 0.8, "ath": 0.7,
    "beat estimates": 0.6, "revenue growth": 0.5, "profit growth": 0.5,
    "record high": 0.7, "jump": 0.5, "soar": 0.7, "gains": 0.4,
    "positive": 0.3, "optimistic": 0.4, "recovery": 0.4,
}

_BEARISH_KEYWORDS: dict[str, float] = {
    "crash": -0.7, "bearish": -0.8, "sell off": -0.6, "selloff": -0.6,
    "downgrade": -0.5, "underperform": -0.5, "sell": -0.4,
    "decline": -0.4, "slump": -0.6, "tumble": -0.6, "plunge": -0.7,
    "miss estimates": -0.6, "loss": -0.4, "debt": -0.3,
    "recession": -0.7, "fear": -0.5, "panic": -0.6, "risk": -0.3,
    "negative": -0.3, "warning": -0.4, "concern": -0.3,
}


class NewsService:
    """Generic news/sentiment service.

    Ingestion flow: adapter fetch → normalize → stable dedup → persist
    (SQLite ``news_items``) → query.  ``news()`` / ``sentiment()`` read
    persisted history (fast, no network); ``refresh()`` pulls enabled
    sources on demand.  One bad source never blocks the others, and
    deleting a source configuration preserves its historical items.
    """

    def __init__(self, store: Any, *,
                 retention_days: int = 30) -> None:
        self._store = store
        self._adapters: dict[str, Any] = {}
        self._retention_days = retention_days if retention_days > 0 else 30
        self._refresh_locks: dict[str, asyncio.Lock] = {}
        self._refresh_locks_guard = threading.Lock()

    # ── Adapter registration ────────────────────────────────────────────────

    def register_adapter(self, adapter: Any) -> None:
        """Register an adapter by its ``source_type``."""
        st = getattr(adapter, "source_type", None)
        if st is None:
            raise ValueError("adapter has no source_type attribute")
        self._adapters[st] = adapter
        logger.info("registered news adapter: %s", st)

    def get_adapter(self, source_type: str) -> Any | None:
        return self._adapters.get(source_type)

    def set_retention_days(self, days: int) -> None:
        """Configure history retention (days). Non-positive keeps default."""
        try:
            days = int(days)
        except (ValueError, TypeError):
            return
        if days > 0:
            self._retention_days = days

    # ── Source configuration (delegates to store) ───────────────────────────

    def list_sources(self, *, enabled_only: bool = False) -> list[NewsSourceConfig]:
        rows = self._store.list_news_sources(enabled_only=enabled_only)
        return [self._row_to_config(r) for r in rows]

    def get_source(self, source_id: str) -> NewsSourceConfig | None:
        row = self._store.get_news_source(source_id)
        return self._row_to_config(row) if row else None

    def upsert_source(self, source: NewsSourceConfig) -> None:
        self._store.upsert_news_source(
            source_id=source.source_id,
            name=source.name,
            source_type=source.source_type,
            category=source.category,
            enabled=source.enabled,
            config_json=source.config_json,
        )

    def delete_source(self, source_id: str) -> bool:
        return self._store.delete_news_source(source_id)

    def set_source_enabled(self, source_id: str, enabled: bool) -> bool:
        return self._store.set_news_source_enabled(source_id, enabled)

    # ── On-demand refresh (fetch → normalize → persist) ────────────────────

    def _refresh_lock_for(self, source_id: str) -> asyncio.Lock:
        """Per-source refresh lock (prevents duplicate simultaneous fetches)."""
        with self._refresh_locks_guard:
            lock = self._refresh_locks.get(source_id)
            if lock is None:
                lock = asyncio.Lock()
                self._refresh_locks[source_id] = lock
            return lock

    async def refresh(
        self,
        source_ids: list[str] | None = None,
        *,
        limit_per_source: int = 50,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        """Refresh enabled sources: fetch, dedup, persist, prune.

        Only enabled sources are refreshed; one failing source never
        blocks the others.  Repeated refreshes insert no duplicates
        (stable ``item_id`` identity).  Retention pruning runs after a
        successful pass.  Returns a summary dict with per-source
        ``fetched`` / ``inserted`` / ``error`` entries.
        """
        limit_per_source = max(1, min(int(limit_per_source or 50), 100))
        sources = self.list_sources(enabled_only=True)
        if source_ids:
            id_set = set(source_ids)
            sources = [s for s in sources if s.source_id in id_set]

        fetched_at = datetime.now(timezone.utc).isoformat()
        summary: dict[str, Any] = {"refreshed": [], "inserted": 0, "errors": {}}
        for source in sources:
            adapter = self._adapters.get(source.source_type)
            if adapter is None:
                logger.warning("no adapter for source type %s — skipping %s",
                               source.source_type, source.source_id)
                summary["errors"][source.source_id] = "no adapter"
                continue
            lock = self._refresh_lock_for(source.source_id)
            async with lock:
                try:
                    items = await adapter.fetch(source, limit=limit_per_source)
                except Exception as exc:
                    logger.warning("fetch failed for source %s: %s",
                                   source.source_id, exc)
                    summary["errors"][source.source_id] = (
                        f"{type(exc).__name__}: {exc}")
                    continue
                rows = [self._normalize_item(source, it, fetched_at)
                        for it in items]
                rows = [r for r in rows if r]
                try:
                    inserted = self._store.upsert_news_items(rows)
                except Exception as exc:
                    logger.warning("persist failed for source %s: %s",
                                   source.source_id, exc)
                    summary["errors"][source.source_id] = (
                        f"persist failed: {type(exc).__name__}")
                    continue
                summary["refreshed"].append(source.source_id)
                summary["inserted"] += inserted

        days = self._retention_days if retention_days is None else retention_days
        try:
            summary["pruned"] = self.prune_history(days) if days > 0 else 0
        except Exception as exc:
            logger.warning("news retention prune failed: %s", exc)
            summary["pruned"] = 0
        return summary

    def prune_history(self, max_age_days: int | None = None) -> int:
        """Delete expired items (bounded, transaction-safe).

        Source configurations and tombstones are never touched.
        """
        days = self._retention_days if max_age_days is None else max_age_days
        if not days or days <= 0:
            return 0
        return self._store.prune_news_items(int(days))

    # ── Core news query (persisted history) ─────────────────────────────────

    async def news(self, filters: NewsFilter | None = None) -> NewsResult:
        """Query persisted news history with filters (no network fetch).

        SQL scopes source/category/symbol/age; keyword include/exclude
        refine the bounded result set.  Ordering is deterministic:
        newest published first (fetched_at fallback).  Use ``refresh()``
        to pull fresh items from enabled sources first.
        """
        if filters is None:
            filters = NewsFilter()

        sources = self._resolve_sources(filters)
        sources_queried = [s.source_id for s in sources]

        newer_than: str | None = None
        if filters.max_age_hours is not None:
            try:
                from datetime import timedelta
                cutoff = (datetime.now(timezone.utc)
                          - timedelta(hours=float(filters.max_age_hours)))
                newer_than = cutoff.isoformat()
            except (ValueError, TypeError):
                newer_than = None

        # Over-fetch headroom for application-side keyword refinement.
        # NOTE: symbol matching stays application-side (title/summary text)
        # because provider symbol tags are sparsely populated; the SQL
        # symbols scope exists for stores whose symbols column is filled.
        query_limit = max(filters.limit * 5, filters.limit + 50)
        rows = self._store.query_news_items(
            source_ids=[s.source_id for s in sources] or None,
            categories=list(filters.categories) if filters.categories else None,
            symbols=None,
            newer_than=newer_than,
            limit=query_limit,
        )
        names = {s.source_id: s.name for s in sources}
        items = [self._row_to_item(r, names.get(r.get("source_id")))
                 for r in rows]
        items = [i for i in items if i is not None]

        # Keyword include/exclude (+ symbol text fallback) refine the set.
        filtered = self._apply_filters(items, filters)

        # Deterministic order: newest published first, fetched fallback.
        filtered.sort(
            key=lambda x: getattr(x, "published", None)
            or getattr(x, "created_utc", None)
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Stable cross-source dedup as a final guard.
        filtered = self._dedup_items(filtered)

        result_items = filtered[:filters.limit]

        return NewsResult(
            articles=tuple(result_items),
            total_count=len(result_items),
            sources_queried=tuple(sources_queried),
            filtered=filters is not None,
        )

    # ── Sentiment analysis ──────────────────────────────────────────────────

    async def sentiment(
        self, filters: NewsFilter | None = None
    ) -> NewsResult:
        """Query persisted news and compute keyword-based sentiment.

        Returns a NewsResult with both ``articles`` and ``sentiments``
        populated.  Sentiment is computed over title + summary/selftext
        using weighted keyword matching with word boundaries, then
        persisted per stable item id so repeated ingestion never
        double-counts an item.
        """
        result = await self.news(filters)
        sentiments: list[SentimentResult] = []
        scored: list[tuple[str, float, str]] = []
        # Stable ids (shared with the durable rows) so scores persist
        # and repeated ingestion never double-counts an item.
        sources = {s.source_id: s for s in self._resolve_sources(filters)}

        for item in result.articles:
            text = self._item_text(item)
            src = sources.get(item.source_id)
            item_id = (self._stable_item_id(src, item) if src is not None
                       else self._item_id(item))
            sentiment, score, matched = self._compute_sentiment(text)
            sentiments.append(SentimentResult(
                item_id=item_id,
                sentiment=sentiment,
                score=score,
                matched_keywords=tuple(matched),
            ))
            scored.append((item_id, score, sentiment))

        if scored:
            try:
                self._store.update_news_sentiments(scored)
            except Exception as exc:
                logger.debug("sentiment persist skipped: %s", exc)

        return NewsResult(
            articles=result.articles,
            sentiments=tuple(sentiments),
            total_count=result.total_count,
            sources_queried=result.sources_queried,
            filtered=result.filtered,
        )

    # ── Seed defaults (idempotent) ─────────────────────────────────────────

    def seed_defaults(self, defaults: list[dict[str, Any]]) -> None:
        """Insert default sources that don't already exist.

        Durable semantics: ids the user has deleted are recorded as
        tombstones in the store and are NEVER re-created by seeding —
        across restarts and repeated calls.  Explicitly re-adding an id
        via upsert clears its tombstone.  This is idempotent: calling it
        multiple times is safe.
        """
        existing = {s.source_id for s in self.list_sources()}
        try:
            tombstoned = set(self._store.list_news_source_tombstones())
        except Exception:
            tombstoned = set()
            logger.debug("tombstone list unavailable — seeding without it")
        for cfg in defaults:
            sid = cfg.get("source_id", "")
            if not sid or sid in existing or sid in tombstoned:
                continue
            self._store.upsert_news_source(
                source_id=sid,
                name=cfg.get("name", sid),
                source_type=cfg.get("source_type", "rss"),
                category=cfg.get("category", ""),
                enabled=cfg.get("enabled", True),
                config_json=cfg.get("config_json"),
            )
            logger.info("seeded news source: %s", sid)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _resolve_sources(self, filters: NewsFilter) -> list[NewsSourceConfig]:
        """Get sources to query based on filters."""
        sources = self.list_sources(enabled_only=True)

        if filters.source_ids:
            id_set = set(filters.source_ids)
            sources = [s for s in sources if s.source_id in id_set]

        if filters.categories:
            cat_set = set(filters.categories)
            sources = [s for s in sources if s.category in cat_set]

        return sources

    @staticmethod
    def _dedup_items(items: list[RSSEntry | RedditPost]) -> list[RSSEntry | RedditPost]:
        """Deduplicate by title+link hash."""
        seen: set[str] = set()
        out: list[RSSEntry | RedditPost] = []
        for item in items:
            key_data = f"{item.title}|{getattr(item, 'link', '') or getattr(item, 'permalink', '')}"
            h = hashlib.md5(key_data.encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                out.append(item)
        return out

    def _apply_filters(
        self,
        items: list[RSSEntry | RedditPost],
        filters: NewsFilter,
    ) -> list[RSSEntry | RedditPost]:
        """Apply keyword include/exclude and age filters."""
        result = items

        # Keyword include (any match)
        if filters.keywords_include:
            patterns = [
                re.compile(re.escape(kw), re.IGNORECASE)
                for kw in filters.keywords_include
            ]
            result = [
                item for item in result
                if any(p.search(self._item_text(item)) for p in patterns)
            ]

        # Keyword exclude (any match → drop)
        if filters.keywords_exclude:
            patterns = [
                re.compile(re.escape(kw), re.IGNORECASE)
                for kw in filters.keywords_exclude
            ]
            result = [
                item for item in result
                if not any(p.search(self._item_text(item)) for p in patterns)
            ]

        # Symbol search (title + summary)
        if filters.symbol:
            sym_pat = re.compile(re.escape(filters.symbol), re.IGNORECASE)
            result = [
                item for item in result
                if sym_pat.search(self._item_text(item))
            ]

        # Max age (absolute UTC cutoff; items without a timestamp are kept)
        if filters.max_age_hours is not None:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(
                hours=filters.max_age_hours)
            result = [
                item for item in result
                if self._item_age(item) is None
                or self._item_age(item) >= cutoff
            ]

        return result

    @staticmethod
    def _item_text(item: RSSEntry | RedditPost) -> str:
        """Extract searchable text from an item."""
        parts = [item.title]
        if isinstance(item, RSSEntry) and item.summary:
            parts.append(item.summary)
        elif isinstance(item, RedditPost):
            if item.selftext:
                parts.append(item.selftext)
        return " ".join(parts)

    @staticmethod
    def _item_id(item: RSSEntry | RedditPost) -> str:
        """Stable identity string for an item."""
        if isinstance(item, RSSEntry):
            return item.guid or item.link
        return item.permalink or item.title

    @staticmethod
    def _item_age(item: RSSEntry | RedditPost) -> datetime | None:
        """Return the published/created datetime (tz-aware)."""
        if isinstance(item, RSSEntry):
            return item.published
        return item.created_utc

    @staticmethod
    def _compute_sentiment(text: str) -> tuple[str, float, list[str]]:
        """Keyword-based sentiment scoring.

        Returns (sentiment_label, normalised_score, matched_keywords).
        Score range: -1.0 (bearish) to +1.0 (bullish).
        """
        text_lower = text.lower()
        total_score = 0.0
        matched: list[str] = []

        for kw, weight in _BULLISH_KEYWORDS.items():
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                total_score += weight
                matched.append(kw)

        for kw, weight in _BEARISH_KEYWORDS.items():
            if re.search(r"\b" + re.escape(kw) + r"\b", text_lower):
                total_score += weight  # weight is negative
                matched.append(kw)

        # Normalise to -1..+1 using tanh-like squashing
        import math
        normalised = math.tanh(total_score / 2.0)

        if normalised > 0.1:
            label = "positive"
        elif normalised < -0.1:
            label = "negative"
        else:
            label = "neutral"

        return label, round(normalised, 3), matched

    @staticmethod
    def _row_to_config(row: dict[str, Any]) -> NewsSourceConfig:
        return NewsSourceConfig(
            source_id=row["source_id"],
            name=row["name"],
            source_type=row["source_type"],
            category=row.get("category", ""),
            enabled=row.get("enabled", True),
            config_json=row.get("config_json"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    # ── Item normalization / stable identity ────────────────────────────────

    @staticmethod
    def _norm_text(text: str | None) -> str:
        """Normalize text for hashing: lowercase, collapsed whitespace."""
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @classmethod
    def _stable_item_id(cls, source: NewsSourceConfig,
                        item: RSSEntry | RedditPost) -> str:
        """Deterministic durable identity for a fetched item.

        Prefers provider identity (RSS guid/link, Reddit permalink/url);
        falls back to a normalized title+link hash.  Namespaced by source
        type so identical provider ids across types cannot collide.
        """
        if isinstance(item, RSSEntry):
            provider_id = (item.guid or item.link or "").strip()
        else:
            provider_id = (item.permalink or item.url or "").strip()
        if provider_id:
            base = f"{source.source_type}:{provider_id}"
        else:
            link = getattr(item, "link", "") or ""
            base = (f"{source.source_id}|{cls._norm_text(item.title)}"
                    f"|{cls._norm_text(link)}")
        return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def _normalize_item(cls, source: NewsSourceConfig,
                        item: RSSEntry | RedditPost,
                        fetched_iso: str) -> dict[str, Any] | None:
        """Map an adapter item to a news_items row dict."""
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            published = (item.published if isinstance(item, RSSEntry)
                         else item.created_utc)
            published_iso = published.isoformat() if published else None
            if isinstance(item, RSSEntry):
                provider = {
                    "guid": item.guid,
                    "author": item.author,
                }
                row = {
                    "item_id": cls._stable_item_id(source, item),
                    "source_id": source.source_id,
                    "source_type": source.source_type,
                    "category": source.category or "",
                    "title": item.title.strip(),
                    "summary": item.summary,
                    "url": item.link,
                    "author": item.author,
                    "symbols": "",
                    "published_at": published_iso,
                    "fetched_at": fetched_iso,
                    "sentiment_score": None,
                    "sentiment_label": None,
                    "provider_json": json.dumps(provider, ensure_ascii=False,
                                                default=str),
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
            else:
                provider = {
                    "subreddit": item.subreddit,
                    "score": item.score,
                    "num_comments": item.num_comments,
                    "author": item.author,
                    "url": item.url,
                    "permalink": item.permalink,
                    "selftext": item.selftext,
                    "upvote_ratio": item.upvote_ratio,
                }
                row = {
                    "item_id": cls._stable_item_id(source, item),
                    "source_id": source.source_id,
                    "source_type": source.source_type,
                    "category": source.category or "",
                    "title": item.title.strip(),
                    "summary": item.selftext,
                    "url": item.url or item.permalink,
                    "author": item.author,
                    "symbols": "",
                    "published_at": published_iso,
                    "fetched_at": fetched_iso,
                    "sentiment_score": None,
                    "sentiment_label": None,
                    "provider_json": json.dumps(provider, ensure_ascii=False,
                                                default=str),
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
            if not row["title"]:
                return None
            return row
        except Exception as exc:
            logger.debug("normalize skipped item for %s: %s",
                         source.source_id, exc)
            return None

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        """Parse an ISO timestamp, coercing naive values to UTC."""
        if not value:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(str(value))
            except (ValueError, TypeError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    @classmethod
    def _row_to_item(cls, row: dict[str, Any],
                     source_name: str | None = None,
                     ) -> RSSEntry | RedditPost | None:
        """Reconstruct a canonical model from a persisted news_items row."""
        try:
            provider: dict[str, Any] = {}
            if row.get("provider_json"):
                try:
                    provider = json.loads(row["provider_json"]) or {}
                except (ValueError, TypeError):
                    provider = {}
            published = cls._parse_dt(row.get("published_at"))
            name = source_name or row.get("source_name") or row["source_id"]
            if (row.get("source_type") or "") == "reddit":
                return RedditPost(
                    source_id=row["source_id"],
                    source_name=name,
                    subreddit=str(provider.get("subreddit") or ""),
                    title=row["title"],
                    score=int(provider.get("score") or 0),
                    num_comments=int(provider.get("num_comments") or 0),
                    author=row.get("author") or provider.get("author"),
                    url=row.get("url") or provider.get("url"),
                    permalink=provider.get("permalink"),
                    created_utc=published,
                    selftext=row.get("summary") or provider.get("selftext"),
                    upvote_ratio=provider.get("upvote_ratio"),
                )
            return RSSEntry(
                source_id=row["source_id"],
                source_name=name,
                title=row["title"],
                link=row.get("url") or "",
                published=published,
                summary=row.get("summary"),
                author=row.get("author") or provider.get("author"),
                guid=provider.get("guid") or row.get("url"),
            )
        except Exception as exc:
            logger.debug("row_to_item skipped %s: %s",
                         row.get("item_id"), exc)
            return None
