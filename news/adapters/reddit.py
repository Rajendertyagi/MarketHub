"""Reddit news adapter — httpx JSON API with rate-limit awareness.

Fetches public subreddit listings via Reddit's undocumented ``.json``
endpoint.  No OAuth or API key required for read-only public access.

MarketHub controls: timeout, retry/backoff, User-Agent, logging.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from market.models import NewsSourceConfig, RedditPost

logger = logging.getLogger(__name__)

# ── Defaults ────────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.5
DEFAULT_USER_AGENT = (
    "MarketHub/1.0 (+https://github.com/Rajendertyagi/MarketHub; "
    "news-aggregator; +contact@markethub.local)"
)
REDDIT_RATE_LIMIT_RESET = 60  # seconds to wait on 429


class RedditFetchError(Exception):
    """Raised when a Reddit fetch fails after retries."""


class RedditAdapter:
    """Fetch public subreddit listings via Reddit's JSON API.

    One adapter instance is shared across all Reddit sources — per-source
    config (subreddit name) is passed through ``source.config_json``.
    """

    source_type = "reddit"

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
        since: str | None = None,
        limit: int = 50,
    ) -> list[RedditPost]:
        """Fetch hot posts from a subreddit.

        Parameters
        ----------
        source:
            Source config; ``config_json["subreddit"]`` is the subreddit name.
        since:
            Optional Reddit fullname (e.g. ``t3_abc123``) — posts *before*
            this are skipped.  Implemented by fetching the listing and
            filtering client-side.
        limit:
            Hard cap on returned items (max 100 per Reddit API call).
        """
        subreddit = (source.config_json or {}).get("subreddit", "").strip()
        if not subreddit:
            logger.warning("Reddit source %s has no subreddit — skipping",
                           source.source_id)
            return []

        posts = await self._fetch_subreddit(subreddit, source, limit=limit)

        if since:
            posts = [p for p in posts if self._full_name(p) != since]

        return posts[:limit]

    # ── HTTP fetch with retry/backoff ───────────────────────────────────────

    async def _fetch_subreddit(
        self,
        subreddit: str,
        source: NewsSourceConfig,
        *,
        limit: int,
    ) -> list[RedditPost]:
        """Fetch hot posts from r/{subreddit}.json with retries."""
        url = f"https://www.reddit.com/r/{subreddit}/hot.json"
        params: dict[str, str] = {"limit": str(min(limit, 100))}
        if limit > 100:
            params["limit"] = "100"

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": self._user_agent},
                ) as client:
                    resp = await client.get(url, params=params)

                    if resp.status_code == 200:
                        return self._parse_listing(resp.json(), source)
                    if resp.status_code == 304:
                        return []
                    if resp.status_code == 429:
                        # Rate limited — wait and retry
                        wait = REDDIT_RATE_LIMIT_RESET
                        logger.debug(
                            "Reddit %s: rate limited, waiting %ds",
                            source.source_id, wait)
                        await asyncio.sleep(wait)
                        continue
                    if resp.status_code >= 500:
                        raise RedditFetchError(
                            f"server error {resp.status_code} from {url}")
                    logger.warning(
                        "Reddit %s: HTTP %d from %s — not retrying",
                        source.source_id, resp.status_code, url)
                    return []
            except (httpx.TimeoutException, httpx.NetworkError,
                    RedditFetchError) as exc:
                last_exc = exc
                if attempt < self._max_retries:
                    wait = self._backoff_base ** attempt
                    logger.debug(
                        "Reddit %s: attempt %d failed (%s), retrying in %.1fs",
                        source.source_id, attempt, type(exc).__name__, wait)
                    await asyncio.sleep(wait)
                else:
                    logger.warning(
                        "Reddit %s: all %d attempts failed for %s: %s",
                        source.source_id, self._max_retries, url, last_exc)
        return []

    # ── Listing parsing ─────────────────────────────────────────────────────

    def _parse_listing(
        self,
        data: Any,
        source: NewsSourceConfig,
    ) -> list[RedditPost]:
        """Map Reddit JSON listing to RedditPost instances."""
        posts: list[RedditPost] = []
        children = data.get("data", {}).get("children", [])

        for child in children:
            if child.get("kind") != "t3":
                continue
            d = child.get("data", {})
            title = (d.get("title") or "").strip()
            if not title:
                continue

            created_utc = None
            ts = d.get("created_utc")
            if ts:
                try:
                    created_utc = datetime.fromtimestamp(
                        float(ts), tz=timezone.utc)
                except (ValueError, OverflowError, OSError):
                    pass

            posts.append(RedditPost(
                source_id=source.source_id,
                source_name=source.name,
                subreddit=(d.get("subreddit") or "").lower(),
                title=title,
                score=int(d.get("score") or 0),
                num_comments=int(d.get("num_comments") or 0),
                author=d.get("author"),
                url=d.get("url"),
                permalink=d.get("permalink"),
                created_utc=created_utc,
                selftext=(d.get("selftext") or "")[:2000] or None,
                upvote_ratio=(
                    float(d["upvote_ratio"])
                    if "upvote_ratio" in d else None
                ),
            ))

        return posts

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _full_name(post: RedditPost) -> str:
        """Build a Reddit fullname (t3_<id>) from permalink for dedup."""
        if post.permalink:
            return f"t3_{hashlib.md5(post.permalink.encode()).hexdigest()[:16]}"
        return f"t3_{hashlib.md5(post.title.encode()).hexdigest()[:16]}"
