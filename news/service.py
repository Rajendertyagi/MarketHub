"""Generic News & Sentiment service (N1 foundation).

Service-level business logic — adapter dispatch, deduplication, filtering,
and sentiment keyword matching.  MCP tools, WebUI, and alerts will consume
this service; MCP does NOT own this logic.
"""

from __future__ import annotations

import hashlib
import logging
import re
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

    Adapters are registered by source type.  The service dispatches fetches
    to the correct adapter, deduplicates articles via the store, applies
    filters, and computes keyword-based sentiment.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._adapters: dict[str, Any] = {}

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

    # ── Core news fetch ─────────────────────────────────────────────────────

    async def news(self, filters: NewsFilter | None = None) -> NewsResult:
        """Fetch and filter news across all enabled sources.

        Parameters
        ----------
        filters:
            Optional filter criteria.  If None, returns latest from all
            enabled sources with default limit.
        """
        if filters is None:
            filters = NewsFilter()

        sources = self._resolve_sources(filters)
        all_items: list[RSSEntry | RedditPost] = []
        sources_queried: list[str] = []

        for source in sources:
            adapter = self._adapters.get(source.source_type)
            if adapter is None:
                logger.warning("no adapter for source type %s — skipping %s",
                               source.source_type, source.source_id)
                continue

            sources_queried.append(source.source_id)
            try:
                items = await adapter.fetch(
                    source,
                    limit=filters.limit * 2,  # fetch extra for filtering
                )
                all_items.extend(items)
            except Exception as exc:
                logger.warning("fetch failed for source %s: %s",
                               source.source_id, exc)
                continue

        # Dedup in-memory (by link/title hash)
        all_items = self._dedup_items(all_items)

        # Apply filters
        filtered = self._apply_filters(all_items, filters)

        # Sort by published date (newest first)
        filtered.sort(
            key=lambda x: getattr(x, "published", None)
            or getattr(x, "created_utc", None)
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        # Limit
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
        """Fetch news and compute keyword-based sentiment per article.

        Returns a NewsResult with both ``articles`` and ``sentiments``
        populated.  Sentiment is computed over title + summary/selftext
        using weighted keyword matching with word boundaries.
        """
        result = await self.news(filters)
        sentiments: list[SentimentResult] = []

        for item in result.articles:
            text = self._item_text(item)
            item_id = self._item_id(item)
            sentiment, score, matched = self._compute_sentiment(text)
            sentiments.append(SentimentResult(
                item_id=item_id,
                sentiment=sentiment,
                score=score,
                matched_keywords=tuple(matched),
            ))

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

        Deleted defaults are NOT re-created on restart.  This is
        idempotent: calling it multiple times is safe.
        """
        existing = {s.source_id for s in self.list_sources()}
        now_iso = datetime.now(timezone.utc).isoformat()
        for cfg in defaults:
            sid = cfg.get("source_id", "")
            if sid in existing:
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
                re.compile(re.compile(re.escape(kw)), re.IGNORECASE)
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

        # Max age
        if filters.max_age_hours is not None:
            cutoff = datetime.now(timezone.utc).replace(
                hour=datetime.now(timezone.utc).hour - filters.max_age_hours
                if filters.max_age_hours <= datetime.now(timezone.utc).hour
                else 0
            )
            # Simpler: compute absolute cutoff
            import datetime as _dt
            cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
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
