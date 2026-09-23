"""X/Twitter MCP tools — AI-facing read access over XTwitterService.

Design: 7 read-only tools (feed / search / tweet / article / bookmarks /
user_posts / user_profile). Reads go through the shared XTwitterService;
tools never see cookies (resolved server-side) and never import the CLI
layer. X rules are managed through the EXISTING generic alert tools
(source="twitter") — no new rule surface here.

Provider outcomes (expired session, missing handle, rate limit, the
upstream search outage, ...) return as DATA
``{"status": "error", "code": ..., "reason": ...}`` so callers see the
reason. Only genuine crashes and caller input errors raise (the framework
then reports "Error executing tool").

Typed codes: not_configured / expired / rate_limited / not_found /
search_unavailable / x_unavailable / api_error (+ invalid_input).
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Callable
from typing import Any

from core.errors import StorageError, ValidationError
from mcp_server.contract import (
    TOOL_X_ARTICLE,
    TOOL_X_BOOKMARKS,
    TOOL_X_FEED,
    TOOL_X_SEARCH,
    TOOL_X_TWEET,
    TOOL_X_USER_POSTS,
    TOOL_X_USER_PROFILE,
)
from mcp_server.registry import get_tool_description

logger = logging.getLogger(__name__)


def _bounded(value: Any, lo: int, hi: int, label: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be an integer") from None
    if v < lo or v > hi:
        raise ValidationError(f"{label} must be between {lo} and {hi}")
    return v


# Closed provider-code set: the ONLY values that may cross the MCP
# boundary as data. Fail-closed — an unknown code (even on an exception
# carrying a ``.code`` attribute) still raises instead of mapping.
_PROVIDER_ERROR_CODES = frozenset({
    "not_authenticated",
    "not_found",
    "rate_limited",
    "x_unavailable",
    "api_error",
    "search_unavailable",
    "invalid_input",
})

_BLOB_RE = re.compile(r"[A-Za-z0-9\-_.+/=]{40,}")
_WINPATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s]+\\)*([^\\\s]+)")
_UNIXPATH_RE = re.compile(r"(?<![\w:/])(/[^\s:]*(?:/[^\s:]+)+)")
_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?[^\s]*")


def _safe_reason(exc: BaseException) -> str:
    """Build the provider-facing reason: sanitized, never raw text.

    Rules (ordered): first line only, 200 chars max; URL query strings
    stripped (host+path kept); directory parts dropped (basename kept);
    opaque blobs >= 40 chars (tokens/keys) redacted. Public IDs (tweet
    snowflakes, queryIds) and ordinary messages pass through unchanged.
    """
    lines = str(exc).strip().splitlines()
    text = (lines[0] if lines else "").strip()[:200]
    if not text:
        return "provider error"
    text = _QUERY_RE.sub(r"\1", text)
    text = _WINPATH_RE.sub(r"\1", text)
    text = _UNIXPATH_RE.sub(lambda m: m.group(1).rsplit("/", 1)[-1], text)
    text = _BLOB_RE.sub("<redacted>", text)
    return text or "provider error"


def _provider_error_payload(exc: BaseException) -> dict[str, Any] | None:
    """Project a provider outcome to a data payload, or None to re-raise.

    Only the closed ``_PROVIDER_ERROR_CODES`` set maps. Core
    ValidationError/StorageError carry no ``.code`` and genuine crashes
    carry none either — both keep raising with type and traceback intact.
    """
    code = getattr(exc, "code", None)
    if not isinstance(code, str) or code not in _PROVIDER_ERROR_CODES:
        return None
    return {"status": "error", "code": code,
            "reason": _safe_reason(exc)}


def _guard(fn: Callable[..., Any]) -> Callable[..., Any]:
    """The single MCP boundary contract for X/Twitter tools.

    Apply as ``@_guard`` directly above each tool ``def`` (below
    ``@mcp.tool``). Provider/runtime failure (closed code set) returns the
    stable ``{"status": "error", "code": ..., "reason": ...}`` payload;
    everything else re-raises untouched. ``functools.wraps`` preserves the
    signature for tool-schema generation.
    """
    @functools.wraps(fn)
    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            payload = _provider_error_payload(exc)
            if payload is not None:
                return payload
            raise
    return _wrapper


def register_twitter_tools(mcp: Any, services: Any, **kwargs: Any) -> None:
    """Register the 7 read-only X/Twitter tools."""
    svc = getattr(services, "x_twitter", None)

    def _require() -> Any:
        if svc is None:
            raise StorageError("x/twitter service not available")
        return svc

    @mcp.tool(name=TOOL_X_FEED, description=get_tool_description(TOOL_X_FEED))
    @_guard
    async def twitter_feed_tool(limit: int = 20,
                                cursor: str | None = None,
                                live: bool = False) -> dict[str, Any]:
        s = _require()
        lim = _bounded(limit, 1, 20, "limit")
        return await s.feed(limit=lim, cursor=cursor, live=bool(live))

    @mcp.tool(name=TOOL_X_SEARCH,
              description=get_tool_description(TOOL_X_SEARCH))
    @_guard
    async def twitter_search_tool(query: str,
                                  limit: int = 10) -> dict[str, Any]:
        s = _require()
        q = (query or "").strip()
        if not q:
            raise ValidationError("query must not be empty")
        lim = _bounded(limit, 1, 20, "limit")
        return await s.search(q, limit=lim)

    @mcp.tool(name=TOOL_X_TWEET, description=get_tool_description(TOOL_X_TWEET))
    @_guard
    async def twitter_tweet_tool(url_or_id: str) -> dict[str, Any]:
        s = _require()
        target = (url_or_id or "").strip()
        if not target:
            raise ValidationError("url_or_id must not be empty")
        return await s.get_tweet(target)

    @mcp.tool(name=TOOL_X_ARTICLE,
              description=get_tool_description(TOOL_X_ARTICLE))
    @_guard
    async def twitter_article_tool(url_or_id: str) -> dict[str, Any]:
        s = _require()
        target = (url_or_id or "").strip()
        if not target:
            raise ValidationError("url_or_id must not be empty")
        return await s.get_article(target)

    @mcp.tool(name=TOOL_X_BOOKMARKS,
              description=get_tool_description(TOOL_X_BOOKMARKS))
    @_guard
    async def twitter_bookmarks_tool(limit: int = 20) -> dict[str, Any]:
        s = _require()
        lim = _bounded(limit, 1, 20, "limit")
        return await s.get_bookmarks(lim)

    @mcp.tool(name=TOOL_X_USER_POSTS,
              description=get_tool_description(TOOL_X_USER_POSTS))
    @_guard
    async def twitter_user_posts_tool(handle: str,
                                      limit: int = 10) -> dict[str, Any]:
        s = _require()
        h = (handle or "").strip().lstrip("@")
        if not h:
            raise ValidationError("handle must not be empty")
        lim = _bounded(limit, 1, 20, "limit")
        return await s.get_user_posts(h, lim)

    @mcp.tool(name=TOOL_X_USER_PROFILE,
              description=get_tool_description(TOOL_X_USER_PROFILE))
    @_guard
    async def twitter_user_profile_tool(handle: str) -> dict[str, Any]:
        s = _require()
        h = (handle or "").strip().lstrip("@")
        if not h:
            raise ValidationError("handle must not be empty")
        return await s.get_user_profile(h)
