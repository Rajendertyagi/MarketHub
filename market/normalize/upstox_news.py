"""
Upstox News normalizer.

Converts raw /news API response into NewsSnapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from market.models import NewsArticle, NewsPagination, NewsSnapshot


def news_from_rest(payload: dict[str, Any], instrument_key: str = "") -> NewsSnapshot:
    """Normalize Upstox /news response into NewsSnapshot."""
    if not isinstance(payload, dict):
        raise ValueError("News payload must be a dict")

    data = payload.get("data") or {}
    metadata = payload.get("metadata") or {}
    page_info = metadata.get("page") or {}

    articles: list[NewsArticle] = []
    for key, item_list in data.items():
        if not isinstance(item_list, list):
            continue
        for item in item_list:
            if not isinstance(item, dict):
                continue
            heading = item.get("heading", "")
            if not heading:
                continue
            pub_ts = item.get("published_time")
            published_time = None
            if pub_ts is not None:
                try:
                    published_time = datetime.fromtimestamp(
                        int(pub_ts) / 1000, tz=timezone.utc
                    )
                except (ValueError, OSError, OverflowError):
                    pass
            articles.append(NewsArticle(
                heading=heading,
                summary=item.get("summary"),
                thumbnail=item.get("thumbnail"),
                article_link=item.get("article_link"),
                published_time=published_time,
                source="upstox",
            ))

    pagination = NewsPagination(
        total_records=int(page_info.get("total_records") or 0),
        page_number=int(page_info.get("page_number") or 1),
        page_size=int(page_info.get("page_size") or 0),
    )

    return NewsSnapshot(
        instrument_token=instrument_key or None,
        articles=tuple(articles),
        pagination=pagination,
    )
