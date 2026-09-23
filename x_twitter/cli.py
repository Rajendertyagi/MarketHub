"""twitter-cli adapter (read-only subprocess boundary).

Pinned dependency: twitter-cli==0.8.5 (PyPI), commit
7c634e0d396b1e7af9f63315b414925fe4f29ae7. Never auto-upgraded; drift is
detected at provisioning/startup and surfaced as x_unavailable.

Rules:
- argument arrays only, no shell
- bounded timeout per call, one retry on 404 (queryId rotation) else typed error
- cookies via env (never CLI flags/logs), optional TWITTER_PROXY
- PYTHONUTF8=1 for stable --json decoding
- typed errors only: auth_required / not_authenticated / rate_limited /
  not_found / api_error / x_unavailable
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PIN_VERSION = "0.8.5"
PIN_COMMIT = "7c634e0d396b1e7af9f63315b414925fe4f29ae7"
PIN_REPO = "https://github.com/public-clis/twitter-cli"

DEFAULT_TIMEOUT_SECONDS = 45
POLL_TIMEOUT_SECONDS = 60

# ─── Typed errors ─────────────────────────────────────────────────────────────

class XError(Exception):
    """Base X adapter error with a stable machine-readable code."""

    code = "api_error"

    def __init__(self, message: str = "", *, code: str | None = None,
                 detail: str | None = None) -> None:
        super().__init__(message or code or self.code)
        if code is not None:
            self.code = code
        self.detail = detail


class XNotConfigured(XError):
    code = "auth_required"


class XNotAuthenticated(XError):
    code = "not_authenticated"


class XRateLimited(XError):
    code = "rate_limited"


class XNotFound(XError):
    code = "not_found"


class XUnavailable(XError):
    code = "x_unavailable"


class XApiError(XError):
    code = "api_error"


def _classify_stderr(stderr: str, returncode: int) -> XError:
    s = (stderr or "").lower()
    if "rate limit" in s or "429" in s or "too many requests" in s:
        return XRateLimited("x rate limit reached")
    if ("not authenticated" in s or "auth_token" in s and "invalid" in s
            or "401" in s or "403" in s or "expired" in s
            or "login" in s and "required" in s):
        return XNotAuthenticated("x session expired or invalid")
    if returncode == 404 or "not found" in s or "404" in s:
        return XNotFound("x resource not found")
    if ("no such file" in s or "not recognized" in s or "not found" in s
            and "twitter-cli" in s):
        return XUnavailable("twitter-cli binary unavailable")
    return XApiError((stderr or "twitter-cli failed").strip()[:500])


# ─── Provisioning / pin check ─────────────────────────────────────────────────

@dataclass
class CliInfo:
    path: str
    version: str
    pinned: bool


def default_cli_path() -> str:
    """Return the best-discovered twitter-cli executable path.

    Discovery order (first match wins):
      1. Local expected install: ``~/.local/bin/twitter-cli[.exe]``
      2. ``shutil.which("twitter-cli")``
      3. ``shutil.which("twitter")``          (pip-installed name on Windows)
      4. Fall-back local path (for error messages when nothing is found)
    """
    candidates: list[str] = []
    home_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
    for name in ("twitter-cli.exe", "twitter-cli"):
        candidates.append(os.path.join(home_bin, name))
    for which_name in ("twitter-cli", "twitter"):
        found = shutil.which(which_name)
        if found:
            return found
    return candidates[0] if candidates else "twitter-cli"


def check_pin(cli_path: str, *, timeout: int = 15,
              runner: Callable[..., Any] | None = None) -> CliInfo:
    """Verify the pinned binary: `twitter-cli --version` → 0.8.5.

    Raises XUnavailable on missing binary, version drift, or timeout.
    """
    run = runner or subprocess.run
    try:
        proc = run([cli_path, "--version"], capture_output=True, text=True,
                   encoding="utf-8", errors="replace",
                   timeout=timeout, env=_base_env(None, None))
    except FileNotFoundError as exc:
        raise XUnavailable(f"twitter-cli not found at {cli_path}") from exc
    except subprocess.TimeoutExpired as exc:
        raise XUnavailable("twitter-cli version check timed out") from exc
    except Exception as exc:
        raise XUnavailable(f"twitter-cli probe failed: {type(exc).__name__}") from exc
    out = ((getattr(proc, "stdout", "") or "")
           + (getattr(proc, "stderr", "") or "")).strip()
    # Accept "0.8.5" anywhere in the output (some builds prefix the name).
    pinned = PIN_VERSION in out
    if not pinned:
        raise XUnavailable(
            f"twitter-cli version drift: expected {PIN_VERSION}, got {out[:80]!r}")
    return CliInfo(path=cli_path, version=PIN_VERSION, pinned=True)


def _base_env(cookies: dict[str, str] | None,
              proxy: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    if cookies:
        if cookies.get("auth_token"):
            env["TWITTER_AUTH_TOKEN"] = cookies["auth_token"]
        if cookies.get("ct0"):
            env["TWITTER_CT0"] = cookies["ct0"]
    if proxy:
        env["TWITTER_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
    return env


# ─── Core runner (arg arrays only, no shell) ──────────────────────────────────

def _envelope_error(stdout: str) -> XError | None:
    """Map the CLI's structured failure envelope to a typed error.

    twitter-cli prints ``{"ok": false, "error": {"code": ..., ...}}`` on
    stdout with a non-zero exit (stderr carries only log lines). Returns
    None when stdout holds no such envelope — the caller then falls back
    to stderr classification.
    """
    try:
        payload = json.loads(stdout) if (stdout or "").strip() else None
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("ok", True) is not False:
        return None
    err = payload.get("error")
    code = ""
    msg = ""
    if isinstance(err, dict):
        code = str(err.get("code") or "").strip().lower()
        lines = str(err.get("message") or "").strip().splitlines()
        msg = (lines[0] if lines else "").strip()[:300]
    if code in ("not_authenticated", "auth_required", "unauthorized",
                "expired"):
        return XNotAuthenticated(msg or "x session expired or invalid")
    if code in ("rate_limited", "too_many_requests"):
        return XRateLimited(msg or "x rate limit reached")
    if code in ("not_found", "notfound", "404"):
        return XNotFound(msg or "x resource not found")
    if code in ("invalid_input", "invalid-input", "bad_request", "400"):
        return XApiError(msg or "invalid x request", code="invalid_input")
    if code in ("unavailable", "x_unavailable"):
        return XUnavailable(msg or "twitter-cli binary unavailable")
    return XApiError(msg or "twitter-cli failed", code=code or None)


def run_cli(args: list[str], *, cli_path: str,
            cookies: dict[str, str] | None = None,
            proxy: str | None = None,
            timeout: int = DEFAULT_TIMEOUT_SECONDS,
            retry_404_once: bool = False,
            runner: Callable[..., Any] | None = None) -> Any:
    """Run one read-only twitter-cli command; return parsed --json payload.

    Raises the typed XError family. Retries ONCE on 404 (queryId rotation)
    ONLY when ``retry_404_once`` is set — only search opts in, since a 404
    elsewhere means a genuinely missing resource and a retry just doubles
    the cost (each attempt is a full CLI startup against x.com).
    """
    if not args or not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise XApiError("cli args must be a list of strings")
    run = runner or subprocess.run
    cmd = [cli_path, *args]
    attempts = 2 if retry_404_once else 1
    last_err: XError | None = None
    for attempt in range(attempts):
        try:
            # Decode as UTF-8, never the locale default: tweet bytes are
            # arbitrary Unicode (Hindi, emoji) and cp1252-class decoders
            # crash the reader thread instead (UnicodeDecodeError).
            # errors="replace" keeps one bad byte from killing a cycle.
            proc = run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=timeout, env=_base_env(cookies, proxy))
        except FileNotFoundError as exc:
            raise XUnavailable(f"twitter-cli not found at {cli_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise XApiError("twitter-cli timed out") from exc
        rc = getattr(proc, "returncode", 0)
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        if rc == 0:
            try:
                payload = json.loads(stdout) if stdout.strip() else []
            except ValueError as exc:
                raise XApiError("twitter-cli returned invalid JSON") from exc
            # Defensive: a zero exit carrying ok:false is still a failure
            # (classify it instead of returning it as success).
            if isinstance(payload, dict) and payload.get("ok", True) is False:
                raise _envelope_error(stdout) or XApiError(
                    "twitter-cli reported failure")
            return payload
        # Prefer the structured stdout envelope; stderr holds log lines only.
        last_err = _envelope_error(stdout)
        if last_err is None:
            last_err = _classify_stderr(stderr, rc)
        if isinstance(last_err, XNotFound) and attempt + 1 < attempts:
            op = args[0] if args else "?"
            logger.info("twitter-cli 404 on %r -> single retry "
                        "(queryId rotation)", op)
            continue
        raise last_err
    raise last_err or XApiError("twitter-cli failed")


# ─── Read-only command builders (all --json non-TTY contract) ─────────────────

def fetch_feed(*, cli_path: str, cursor: str | None = None, limit: int = 20,
               tab: str = "following", cookies: dict | None = None,
               proxy: str | None = None, timeout: int = POLL_TIMEOUT_SECONDS,
               runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    args = ["feed", "-t", tab, "--max", str(max(1, min(int(limit or 20), 20))),
            "--full-text", "--json"]
    if cursor:
        args += ["--cursor", cursor]
    payload = run_cli(args, cli_path=cli_path, cookies=cookies, proxy=proxy,
                      timeout=timeout, runner=runner)
    if isinstance(payload, dict):
        return payload
    return {"tweets": payload if isinstance(payload, list) else [], "cursor": None}


# Search-tab values accepted by `twitter search -t/--type` (0.8.5).
_SEARCH_TYPES = ("top", "latest", "photos", "videos")


def search(*, cli_path: str, query: str, limit: int = 20,
           from_user: str | None = None, since: str | None = None,
           exclude_retweets: bool = False, has_links: bool = False,
           tab: str = "latest", cookies: dict | None = None,
           proxy: str | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS,
           runner: Callable[..., Any] | None = None) -> Any:
    q = (query or "").strip()
    if not q:
        raise XApiError("search query must not be empty")
    search_type = (tab or "latest").strip().lower()
    if search_type not in _SEARCH_TYPES:
        search_type = "latest"
    args = ["search", q, "--type", search_type,
            "--max", str(max(1, min(int(limit or 20), 20))),
            "--full-text", "--json"]
    if from_user:
        args += ["--from", from_user]
    if since:
        args += ["--since", since]
    if exclude_retweets:
        args += ["--exclude-retweets"]
    if has_links:
        args += ["--has-links"]
    # NOTE: the tab value was already mapped to --type above; the legacy
    # --tab flag does not exist on the CLI and must never be emitted.
    # Search alone retries once on 404: only SearchTimeline suffers
    # queryId rotation; every other command's 404 is genuinely missing.
    try:
        return run_cli(args, cli_path=cli_path, cookies=cookies,
                       proxy=proxy, timeout=timeout, retry_404_once=True,
                       runner=runner)
    except XNotFound as exc:
        # A search 404 is never "no results" (empty results return 200 +
        # an empty list). It means the SearchTimeline endpoint rejected the
        # call — the known twitter-cli 0.8.5 vs current-X incompatibility
        # (missing x-client-transaction-id under the x-web frontend).
        # Report it honestly instead of a misleading not_found.
        raise XApiError(
            "x search unavailable: twitter-cli 0.8.5 cannot reach the "
            "current X search endpoint (SearchTimeline 404, upstream "
            "issue; feed/profile/tweet reads are unaffected)",
            code="search_unavailable") from exc


def get_tweet(*, cli_path: str, url_or_id: str,
              cookies: dict | None = None, proxy: str | None = None,
              timeout: int = DEFAULT_TIMEOUT_SECONDS,
              runner: Callable[..., Any] | None = None) -> Any:
    target = (url_or_id or "").strip()
    if not target:
        raise XApiError("tweet id/url must not be empty")
    return run_cli(["tweet", target, "--full-text", "--json"],
                   cli_path=cli_path, cookies=cookies, proxy=proxy,
                   timeout=timeout, runner=runner)


def get_article(*, cli_path: str, url_or_id: str,
                cookies: dict | None = None, proxy: str | None = None,
                timeout: int = DEFAULT_TIMEOUT_SECONDS,
                runner: Callable[..., Any] | None = None) -> Any:
    target = (url_or_id or "").strip()
    if not target:
        raise XApiError("article id/url must not be empty")
    # NOTE: the CLI accepts exactly one of --markdown/--json/--yaml, and
    # run_cli's contract is parsed JSON — so --json is required here.
    return run_cli(["article", target, "--json"],
                   cli_path=cli_path, cookies=cookies, proxy=proxy,
                   timeout=timeout, runner=runner)


def get_bookmarks(*, cli_path: str, limit: int = 20,
                  cookies: dict | None = None, proxy: str | None = None,
                  timeout: int = DEFAULT_TIMEOUT_SECONDS,
                  runner: Callable[..., Any] | None = None) -> Any:
    return run_cli(["bookmarks", "--max", str(max(1, min(int(limit or 20), 20))),
                    "--full-text", "--json"],
                   cli_path=cli_path, cookies=cookies, proxy=proxy,
                   timeout=timeout, runner=runner)


def get_user_posts(*, cli_path: str, handle: str, limit: int = 20,
                   cookies: dict | None = None, proxy: str | None = None,
                   timeout: int = DEFAULT_TIMEOUT_SECONDS,
                   runner: Callable[..., Any] | None = None) -> Any:
    h = (handle or "").strip().lstrip("@")
    if not h:
        raise XApiError("handle must not be empty")
    return run_cli(["user-posts", h, "--max",
                    str(max(1, min(int(limit or 20), 20))), "--json"],
                   cli_path=cli_path, cookies=cookies, proxy=proxy,
                   timeout=timeout, runner=runner)


def session_status(*, cli_path: str,
                   cookies: dict | None = None, proxy: str | None = None,
                   timeout: int = DEFAULT_TIMEOUT_SECONDS,
                   runner: Callable[..., Any] | None = None) -> Any:
    """Run `twitter status --json`: purpose-built session-auth probe.

    No timeline quota, no parameters. Non-zero exits surface as the typed
    XError family (envelope-first, see run_cli).
    """
    return run_cli(["status", "--json"], cli_path=cli_path, cookies=cookies,
                   proxy=proxy, timeout=timeout, retry_404_once=False,
                   runner=runner)


def get_user_profile(*, cli_path: str, handle: str,
                     cookies: dict | None = None, proxy: str | None = None,
                     timeout: int = DEFAULT_TIMEOUT_SECONDS,
                     runner: Callable[..., Any] | None = None) -> Any:
    h = (handle or "").strip().lstrip("@")
    if not h:
        raise XApiError("handle must not be empty")
    return run_cli(["user", h, "--json"],
                   cli_path=cli_path, cookies=cookies, proxy=proxy,
                   timeout=timeout, runner=runner)


# ── Normalization (CLI shape → generic tweet dict; nothing X-shaped leaks) ────

def normalize_tweet(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Map one CLI tweet object to the generic persisted shape.

    Generic data model: text, author, url, posted_at, is_retweet,
    metrics.likes|retweets|replies|views|bookmarks.
    """
    if not isinstance(raw, dict):
        return None
    tid = str(raw.get("id") or raw.get("id_str") or "").strip()
    text = str(raw.get("full_text") or raw.get("text") or "")
    if not tid or not text.strip():
        return None
    user = raw.get("user") or {}
    handle = str(user.get("screen_name") or raw.get("handle") or "").lstrip("@")
    author_name = str(user.get("name") or raw.get("author_name") or "")
    url = str(raw.get("url") or "")
    if not url:
        url = f"https://x.com/{handle}/status/{tid}" if handle else ""
    metrics = {
        "likes": _num(raw.get("favorite_count", raw.get("likes", 0))),
        "retweets": _num(raw.get("retweet_count", raw.get("retweets", 0))),
        "replies": _num(raw.get("reply_count", raw.get("replies", 0))),
        "views": _num(raw.get("views", raw.get("view_count", 0))),
        "bookmarks": _num(raw.get("bookmark_count", raw.get("bookmarks", 0))),
    }
    return {
        "id": tid,
        "handle": handle,
        "author_name": author_name,
        "text": text,
        "url": url,
        "metrics": metrics,
        "is_retweet": bool(raw.get("retweeted") or raw.get("is_retweet")
                            or text.startswith("RT @")),
        "posted_at": raw.get("created_at") or raw.get("posted_at"),
        "fetched_at": None,
    }


def _num(v: Any) -> int:
    try:
        n = int(v or 0)
    except (ValueError, TypeError):
        return 0
    return max(0, n)
