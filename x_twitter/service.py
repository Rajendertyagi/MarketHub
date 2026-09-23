"""XTwitterService — single owner of the twitter-cli adapter + poll loop.

- ONLY consumer of x_twitter.cli (MCP / React / core never import it).
- On-demand reads back the 7 MCP tools.
- Feed poll loop runs under BackgroundTaskManager (_running flag, bounded
  sleep, per-cycle error isolation). Single asyncio.Lock — loop and tool
  calls never run the CLI concurrently.
- Durable order per cycle:
  fetch → validate/dedupe → persist → publish/evaluate → advance cursor.
  The persisted feed cursor (source_state, source_name="twitter") advances
  ONLY after the whole batch completed. Mid-batch failure → cursor stays,
  next cycle re-fetches; already-persisted tweets are INSERT OR IGNORE
  no-ops excluded from re-publish (no double-fire, no skip).
- Auth states derived ONLY from CLI results, never cookie presence:
  not_configured / configured / authenticated / expired/invalid / rate_limited.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from core import events
from x_twitter import cli as _cli
from x_twitter import config as _xconfig

logger = logging.getLogger(__name__)

SOURCE_NAME = "twitter"
CURSOR_KEY = "feed_cursor"
EVENT_TYPE_TWEET_NEW = "tweet.new"

TASK_NAME = "x_poll"

_CRED_PROVIDER = "x"


class XTwitterService:
    """X/Twitter reads + poll loop over the injected store."""

    def __init__(self, store: Any, cred_store: Any = None,
                 bus: Any = None, metrics: Any = None,
                 runner: Callable[..., Any] | None = None) -> None:
        self._store = store
        self._creds = cred_store
        self._bus = bus
        self._metrics = metrics
        self._runner = runner
        self._cli_lock = asyncio.Lock()
        self._running = False
        self._auth_state = "not_configured"
        self._auth_reason: str | None = None
        self._auth_at: str | None = None
        self._last_fetch_at: str | None = None
        self._last_new_count = 0
        self._last_error: str | None = None
        self._backoff_until = 0.0

    # ── Credentials (encrypted store, provider "x") ──────────────────────────

    def _cookies(self) -> dict[str, str] | None:
        if self._creds is None:
            return None
        try:
            creds = self._creds.load_app_credentials(_CRED_PROVIDER)
        except Exception:
            return None
        if not creds:
            return None
        token = (creds.get("api_key") or "").strip()
        ct0 = (creds.get("api_secret") or "").strip()
        if not token or not ct0:
            return None
        return {"auth_token": token, "ct0": ct0}

    def has_credentials(self) -> bool:
        return self._cookies() is not None

    def save_credentials(self, auth_token: str, ct0: str) -> None:
        if self._creds is None:
            raise RuntimeError("credential store not available")
        token = (auth_token or "").strip()
        secret = (ct0 or "").strip()
        if not token or not secret:
            raise ValueError("auth_token and ct0 are required")
        self._creds.save_app_credentials(_CRED_PROVIDER, token, secret)
        self._set_auth("configured", "credentials saved, unverified")

    def delete_credentials(self) -> bool:
        if self._creds is None:
            return False
        try:
            ok = bool(self._creds.delete_app_credentials(_CRED_PROVIDER))
        except Exception:
            ok = False
        self._set_auth("not_configured", "credentials removed")
        return ok

    # ── Config (non-secret, x_config table) ──────────────────────────────────

    def get_config(self) -> dict[str, Any]:
        try:
            return self._store.get_x_config()
        except Exception:
            return _xconfig.defaults()

    def configure(self, patch: dict[str, Any]) -> dict[str, Any]:
        validated = _xconfig.validate_poll_config(patch or {})
        return self._store.set_x_config(**validated)

    # ── Auth state (CLI results only) ────────────────────────────────────────

    def _set_auth(self, state: str, reason: str | None = None) -> None:
        self._auth_state = state
        self._auth_reason = reason
        self._auth_at = datetime.now(timezone.utc).isoformat()

    def auth_state(self) -> dict[str, Any]:
        if not self.has_credentials():
            return {"state": "not_configured", "reason": "no credentials stored",
                    "at": self._auth_at}
        if self._auth_state == "not_configured":
            # Durable cookies exist but this process never verified them
            # (fresh boot, polling disabled): report presence honestly
            # instead of the volatile default. Verified states
            # (authenticated / expired/invalid / rate_limited) still come
            # ONLY from live CLI results, never from cookie presence.
            return {"state": "configured",
                    "reason": "credentials stored, unverified",
                    "at": self._auth_at}
        return {"state": self._auth_state, "reason": self._auth_reason,
                "at": self._auth_at}

    async def test_connection(self) -> dict[str, Any]:
        """One manual session-auth probe (`twitter status --json`).

        Side-effect free by design: no tweet persistence, no cursor
        advance, no event publish, no backoff change; works with polling
        disabled. Updates only the in-memory auth state so the UI reflects
        the verification. Never raises — outcomes return as typed results
        with redacted reasons (no cookies, no raw payloads).
        """
        checked = datetime.now(timezone.utc).isoformat()
        cookies = self._cookies()
        if not cookies:
            self._set_auth("not_configured", "no credentials stored")
            return {"status": "error", "state": "not_configured",
                    "reason": "no credentials stored", "checked_at": checked}
        try:
            path = self._require_cli()
        except _cli.XUnavailable as exc:
            return {"status": "error", "state": "unavailable",
                    "reason": f"x_unavailable: {exc}", "checked_at": checked}
        async with self._cli_lock:
            try:
                await asyncio.to_thread(
                    _cli.session_status, cli_path=path, cookies=cookies,
                    proxy=self._proxy(), runner=self._runner)
            except _cli.XNotAuthenticated as exc:
                self._set_auth("expired/invalid", str(exc))
                return {"status": "error", "state": "expired/invalid",
                        "reason": str(exc), "checked_at": checked}
            except _cli.XRateLimited as exc:
                self._set_auth("rate_limited", str(exc))
                return {"status": "error", "state": "rate_limited",
                        "reason": str(exc), "checked_at": checked}
            except _cli.XUnavailable as exc:
                return {"status": "error", "state": "unavailable",
                        "reason": f"x_unavailable: {exc}",
                        "checked_at": checked}
            except _cli.XError as exc:
                return {"status": "error", "state": "error",
                        "reason": exc.code, "checked_at": checked}
            except Exception as exc:
                return {"status": "error", "state": "error",
                        "reason": type(exc).__name__, "checked_at": checked}
        self._set_auth("authenticated", "connection test ok")
        return {"status": "ok", "state": "authenticated",
                "reason": "connection test ok", "checked_at": checked}

    def get_status(self) -> dict[str, Any]:
        auth = self.auth_state()
        try:
            cfg = self.get_config()
        except Exception:
            cfg = _xconfig.defaults()
        try:
            cursor = self._store.get_source_state(SOURCE_NAME, CURSOR_KEY)
        except Exception:
            cursor = None
        try:
            cached = len(self._store.list_x_tweets(limit=1))
        except Exception:
            cached = 0
        return {
            "auth": auth,
            "enabled": bool(cfg.get("enabled")),
            "poll_interval_seconds": cfg.get("poll_interval_seconds"),
            "default_limit": cfg.get("default_limit"),
            "cli_path": cfg.get("cli_path") or _cli.default_cli_path(),
            "cli_pinned": _cli.PIN_VERSION,
            "cursor_set": cursor is not None,
            "cached_tweets": cached,
            "last_fetch_at": self._last_fetch_at,
            "last_new_count": self._last_new_count,
            "last_error": self._last_error,
        }

    def _cli_path(self) -> str:
        try:
            cfg = self.get_config()
            if cfg.get("cli_path"):
                return str(cfg["cli_path"])
        except Exception:
            pass
        return _cli.default_cli_path()

    def _proxy(self) -> str | None:
        try:
            proxy = (self.get_config().get("proxy") or "").strip()
            return proxy or None
        except Exception:
            return None

    def _require_cli(self) -> str:
        path = self._cli_path()
        try:
            _cli.check_pin(path, runner=self._runner)
        except _cli.XUnavailable as exc:
            self._set_auth(self._auth_state, f"x_unavailable: {exc}")
            raise
        return path

    # ── On-demand reads (7 MCP tools) ────────────────────────────────────────

    async def feed(self, *, limit: int = 20, cursor: str | None = None,
                   live: bool = False) -> dict[str, Any]:
        lim = max(1, min(int(limit or 20), 20))
        if live:
            cookies = self._cookies()
            if not cookies:
                raise _cli.XNotConfigured("x credentials not configured")
            path = self._require_cli()
            async with self._cli_lock:
                payload = await asyncio.to_thread(
                    _cli.fetch_feed, cli_path=path, cursor=cursor, limit=lim,
                    cookies=cookies, proxy=self._proxy(), runner=self._runner)
            tweets = self._normalize_batch(payload)
            return {"status": "ok", "count": len(tweets), "tweets": tweets,
                    "cursor": payload.get("cursor") if isinstance(payload, dict) else None}
        rows = await asyncio.to_thread(
            self._store.list_x_tweets, limit=lim)
        return {"status": "ok", "count": len(rows), "tweets": rows,
                "cursor": None}

    async def search(self, query: str, **kw: Any) -> dict[str, Any]:
        cookies = self._cookies()
        if not cookies:
            raise _cli.XNotConfigured("x credentials not configured")
        path = self._require_cli()
        async with self._cli_lock:
            payload = await asyncio.to_thread(
                _cli.search, cli_path=path, query=query, cookies=cookies,
                proxy=self._proxy(), runner=self._runner, **kw)
        tweets = self._normalize_batch(payload)
        return {"status": "ok", "count": len(tweets), "tweets": tweets}

    async def get_tweet(self, url_or_id: str) -> dict[str, Any]:
        cookies = self._cookies()
        if not cookies:
            raise _cli.XNotConfigured("x credentials not configured")
        path = self._require_cli()
        async with self._cli_lock:
            payload = await asyncio.to_thread(
                _cli.get_tweet, cli_path=path, url_or_id=url_or_id,
                cookies=cookies, proxy=self._proxy(), runner=self._runner)
        tweet = _cli.normalize_tweet(payload) if isinstance(payload, dict) else None
        if tweet is None and isinstance(payload, dict) and payload.get("text"):
            tweet = payload
        return {"status": "ok", "tweet": tweet}

    async def get_article(self, url_or_id: str) -> dict[str, Any]:
        cookies = self._cookies()
        if not cookies:
            raise _cli.XNotConfigured("x credentials not configured")
        path = self._require_cli()
        async with self._cli_lock:
            payload = await asyncio.to_thread(
                _cli.get_article, cli_path=path, url_or_id=url_or_id,
                cookies=cookies, proxy=self._proxy(), runner=self._runner)
        return {"status": "ok", "article": payload}

    async def get_bookmarks(self, limit: int = 20) -> dict[str, Any]:
        cookies = self._cookies()
        if not cookies:
            raise _cli.XNotConfigured("x credentials not configured")
        path = self._require_cli()
        async with self._cli_lock:
            payload = await asyncio.to_thread(
                _cli.get_bookmarks, cli_path=path, limit=limit,
                cookies=cookies, proxy=self._proxy(), runner=self._runner)
        return {"status": "ok", "tweets": self._normalize_batch(payload)}

    async def get_user_posts(self, handle: str,
                             limit: int = 20) -> dict[str, Any]:
        cookies = self._cookies()
        if not cookies:
            raise _cli.XNotConfigured("x credentials not configured")
        path = self._require_cli()
        async with self._cli_lock:
            payload = await asyncio.to_thread(
                _cli.get_user_posts, cli_path=path, handle=handle, limit=limit,
                cookies=cookies, proxy=self._proxy(), runner=self._runner)
        return {"status": "ok", "tweets": self._normalize_batch(payload)}

    async def get_user_profile(self, handle: str) -> dict[str, Any]:
        cookies = self._cookies()
        if not cookies:
            raise _cli.XNotConfigured("x credentials not configured")
        path = self._require_cli()
        async with self._cli_lock:
            payload = await asyncio.to_thread(
                _cli.get_user_profile, cli_path=path, handle=handle,
                cookies=cookies, proxy=self._proxy(), runner=self._runner)
        return {"status": "ok", "profile": payload}

    @staticmethod
    def _normalize_batch(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, dict):
            raw_list = payload.get("tweets", payload.get("items", []))
        elif isinstance(payload, list):
            raw_list = payload
        else:
            return []
        out: list[dict[str, Any]] = []
        for raw in raw_list if isinstance(raw_list, list) else []:
            tweet = _cli.normalize_tweet(raw)
            if tweet is not None:
                out.append(tweet)
        return out

    # ── Poll loop ────────────────────────────────────────────────────────────

    async def poll_once(self) -> dict[str, Any]:
        """One poll cycle with strict durable ordering + cursor safety."""
        try:
            cfg = self.get_config()
        except Exception as exc:
            return {"status": "error", "error": f"config: {exc}", "new": 0}
        if not cfg.get("enabled"):
            return {"status": "skipped", "reason": "disabled", "new": 0}
        if time.monotonic() < self._backoff_until:
            return {"status": "skipped", "reason": "backoff", "new": 0}
        cookies = self._cookies()
        if not cookies:
            self._set_auth("not_configured", "no credentials stored")
            return {"status": "error", "error": "auth_required", "new": 0}
        try:
            path = self._require_cli()
        except _cli.XUnavailable as exc:
            self._last_error = f"x_unavailable: {exc}"
            return {"status": "error", "error": "x_unavailable", "new": 0}
        limit = max(1, min(int(cfg.get("default_limit", 20)), 20))
        try:
            cursor = self._store.get_source_state(SOURCE_NAME, CURSOR_KEY)
        except Exception:
            cursor = None

        # 1. fetch (single CLI lock, per-cycle isolation)
        async with self._cli_lock:
            try:
                payload = await asyncio.to_thread(
                    _cli.fetch_feed, cli_path=path, cursor=cursor, limit=limit,
                    cookies=cookies, proxy=self._proxy(), runner=self._runner)
            except _cli.XNotConfigured as exc:
                self._set_auth("not_configured", str(exc))
                self._last_error = "auth_required"
                return {"status": "error", "error": "auth_required", "new": 0}
            except _cli.XNotAuthenticated as exc:
                # Polling PAUSES (loop stays alive), UI red chip.
                self._set_auth("expired/invalid", str(exc))
                self._last_error = "expired/invalid"
                return {"status": "error", "error": "expired/invalid", "new": 0}
            except _cli.XRateLimited as exc:
                self._set_auth("rate_limited", str(exc))
                self._last_error = "rate_limited"
                self._backoff_until = time.monotonic() + max(
                    60.0, float(cfg.get("poll_interval_seconds", 120)))
                return {"status": "error", "error": "rate_limited", "new": 0}
            except _cli.XUnavailable as exc:
                self._last_error = f"x_unavailable: {exc}"
                return {"status": "error", "error": "x_unavailable", "new": 0}
            except _cli.XError as exc:
                self._last_error = exc.code
                return {"status": "error", "error": exc.code, "new": 0}
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                return {"status": "error", "error": "api_error", "new": 0}

        # 2. validate/dedupe (normalize; drop invalid)
        tweets = self._normalize_batch(payload)
        next_cursor = payload.get("cursor") if isinstance(payload, dict) else None

        # 3. persist (INSERT OR IGNORE → new_ids only)
        try:
            new_ids = await asyncio.to_thread(self._store.save_x_tweets, tweets)
        except Exception as exc:
            # Cursor NOT advanced — next cycle re-fetches the same window.
            self._last_error = f"persist: {type(exc).__name__}"
            return {"status": "error", "error": "persist_failed", "new": 0}
        new_set = set(new_ids)
        fresh = [t for t in tweets if t.get("id") in new_set]

        # 4. publish/evaluate one-by-one (tweet.new is data only; only
        #    configured X rules in the existing alert pipeline can fire).
        #    A mid-batch failure stops here — cursor NOT advanced, completed
        #    tweets are no-ops on re-persist and excluded from re-fire.
        try:
            for tweet in fresh:
                await events.publish_event(
                    event_type=EVENT_TYPE_TWEET_NEW,
                    source=SOURCE_NAME,
                    data={
                        "text": tweet.get("text", ""),
                        "author": ("@" + tweet["handle"]) if tweet.get("handle") else "",
                        "url": tweet.get("url", ""),
                        "posted_at": tweet.get("posted_at"),
                        "is_retweet": bool(tweet.get("is_retweet")),
                        "metrics": tweet.get("metrics") or {},
                    },
                    persistent=True,
                    store=self._store,
                    bus=self._bus,
                )
        except Exception as exc:
            self._last_error = f"publish: {type(exc).__name__}"
            return {"status": "error", "error": "publish_failed",
                    "new": 0, "persisted": len(new_ids)}

        # 5. advance cursor ONLY after the whole batch fully done.
        if next_cursor:
            try:
                await asyncio.to_thread(
                    self._store.set_source_state, SOURCE_NAME, CURSOR_KEY,
                    str(next_cursor))
            except Exception as exc:
                self._last_error = f"cursor: {type(exc).__name__}"
                return {"status": "error", "error": "cursor_failed",
                        "new": len(new_ids)}

        self._set_auth("authenticated", "poll ok")
        self._last_fetch_at = datetime.now(timezone.utc).isoformat()
        self._last_new_count = len(new_ids)
        self._last_error = None
        try:
            retention = int(cfg.get("retention_days", 30))
            if retention > 0:
                await asyncio.to_thread(self._store.prune_x_tweets, retention)
        except Exception:
            logger.debug("x retention prune failed", exc_info=True)
        return {"status": "ok", "new": len(new_ids),
                "cursor_advanced": bool(next_cursor)}

    async def _run_loop(self) -> None:
        self._running = True
        while self._running:
            try:
                cfg = self.get_config()
                interval = max(60, int(cfg.get("poll_interval_seconds", 120)))
                enabled = bool(cfg.get("enabled"))
            except Exception:
                interval, enabled = 120, False
            if enabled:
                try:
                    await self.poll_once()
                except Exception:
                    logger.exception("x poll cycle failed")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def start(self, bg_manager: Any) -> None:
        """Start the poll loop under BackgroundTaskManager (idempotent)."""
        if bg_manager is None:
            return
        try:
            await bg_manager.start(TASK_NAME, self._run_loop())
        except Exception:
            logger.warning("x poll loop start failed", exc_info=True)

    async def stop(self, bg_manager: Any) -> None:
        """Stop the poll loop (idempotent)."""
        self._running = False
        if bg_manager is None:
            return
        try:
            await bg_manager.cancel_and_wait(TASK_NAME)
        except Exception:
            logger.debug("x poll loop stop failed", exc_info=True)
