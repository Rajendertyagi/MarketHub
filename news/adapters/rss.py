"""RSS/Atom news adapter — httpx fetch + feedparser parse.

MarketHub controls: timeout, retry/backoff, HTTP status, redirects,
User-Agent, logging, source health.  feedparser is used ONLY for XML
parsing — never for network I/O.
"""

from __future__ import annotations

import asyncio
import calendar
import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

from market.models import NewsSourceConfig, RSSEntry

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.5
DEFAULT_USER_AGENT = (
    "MarketHub/1.0 (+https://github.com/Rajendertyagi/MarketHub; "
    "news-aggregator; +contact@markethub.local)"
)
MAX_SUMMARY_LEN = 2000  # truncate overly long summaries


class RSSFetchError(Exception):
    """Raised when an RSS fetch fails after retries."""


class RSSAdapter:
    """Fetch and parse RSS/Atom feeds via httpx + feedparser.

    One adapter instance is shared across all RSS sources — per-source
    config is passed through the ``source`` argument to ``fetch``.
    """

    source_type = "rss"

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._user_agent = user_agent

    # ── Public API ──────────────────────────────────────────────────────────

    async def fetch(
        self,
        source: NewsSourceConfig,
        *,
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[RSSEntry]:
        """Fetch articles from an RSS/Atom feed.

        Parameters
        ----------
        source:
            Source config; ``config_json["url"]`` is the feed URL.
        since:
            If provided, only articles published after this datetime are
            returned.  Adapters that support incremental fetch may also
            use this to set ``If-Modified-Since`` / ``ETag`` headers in
            the future.
        limit:
            Hard cap on returned items.
        """
        feed_url = (source.config_json or {}).get("url")
        if not feed_url:
            logger.warning("RSS source %s has no URL — skipping", source.source_id)
            return []

        raw_xml = await self._fetch_xml(feed_url, source.source_id)
        if raw_xml is None:
            return []

        entries = self._parse_feed(raw_xml, source, limit=limit)

        if since is not None:
            entries = [e for e in entries
                       if e.published is not None and e.published > since]

        return entries[:limit]

    # ── HTTP fetch with retry/backoff ───────────────────────────────────────

    async def _fetch_xml(self, url: str, source_id: str) -> str | None:
        """Download the XML body with retries and exponential backoff."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": self._user_agent},
                ) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return resp.text
                    if resp.status_code == 304:
                        return None  # not modified — nothing to parse
                    if resp.status_code >= 500:
                        raise RSSFetchError(
                            f"server error {resp.status_code} from {url}")
                    # 4xx — permanent failure, no retry
                    logger.warning(
                        "RSS %s: HTTP %d from %s — not retrying",
                        source_id, resp.status_code, url)
                    return None
            except (httpx.TimeoutException, httpx.NetworkError,
                    RSSFetchError) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    wait = self._backoff_base ** attempt
                    logger.debug(
                        "RSS %s: attempt %d failed (%s), retrying in %.1fs",
                        source_id, attempt, type(exc).__name__, wait)
                    await asyncio.sleep(wait)
                else:
                    logger.warning(
                        "RSS %s: all %d attempts failed for %s: %s",
                        source_id, self._max_retries, url, last_exc)
        return None

    # ── Feed parsing ────────────────────────────────────────────────────────

    def _parse_feed(
        self,
        raw_xml: str,
        source: NewsSourceConfig,
        *,
        limit: int,
    ) -> list[RSSEntry]:
        """Parse XML with feedparser and map to RSSEntry instances."""
        feed = feedparser.parse(raw_xml)
        entries: list[RSSEntry] = []

        for entry in feed.entries[:limit * 2]:  # parse extra in case some are skipped
            try:
                title = self._clean_html(entry.get("title", ""))
                if not title.strip():
                    continue
                link = entry.get("link", "")
                if not link:
                    continue
                published = self._parse_datetime(entry)
                summary = self._clean_html(
                    entry.get("summary") or entry.get("description") or "")
                summary = self._truncate(summary, MAX_SUMMARY_LEN)
                author = entry.get("author")
                guid = entry.get("id") or entry.get("guid") or link

                entries.append(RSSEntry(
                    source_id=source.source_id,
                    source_name=source.name,
                    title=title.strip(),
                    link=link,
                    published=published,
                    summary=summary or None,
                    author=author,
                    guid=guid,
                ))
            except (ValueError, KeyError) as exc:
                logger.debug("RSS %s: skipping entry: %s",
                             source.source_id, exc)
                continue

        return entries

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_html(text: str) -> str:
        """Strip HTML tags and decode entities."""
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."

    @staticmethod
    def _parse_datetime(entry: Any) -> datetime | None:
        """Extract a tz-aware datetime from a feedparser entry.

        feedparser ``*_parsed`` tuples are in UTC, so they must be
        converted with ``calendar.timegm`` (``time.mktime`` would
        misinterpret them as local time on non-UTC hosts).
        """
        for key in ("published_parsed", "updated_parsed"):
            tp = entry.get(key)
            if tp is not None:
                try:
                    ts = calendar.timegm(tp)
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                except (OverflowError, ValueError):
                    continue
        return None
