#!/usr/bin/env python3
"""Web token authentication tests (WA1-WA14).

Covers runtime Upstox token submission via WebUI API:
  * WA1   auth status endpoint with no token
  * WA2   ENV token status reflected
  * WA3   runtime token submission updates feed credentials
  * WA4   submitted token never returned in any response
  * WA5   token absent from repr/str of credentials
  * WA6   empty/whitespace/non-string token rejected
  * WA7   runtime token overrides ENV for current process
  * WA8   frontend clears input after success
  * WA9   no localStorage/sessionStorage/cookie persistence
  * WA10  auth failure returns safe message only
  * WA11  shared MarketService untouched by credential update
  * WA12  no duplicate Upstox source/WS (same feed object reused)
  * WA13  SourceManager remains lifecycle owner
  * WA14  existing imports intact

NO LIVE UPSTOX CONNECTION. Synthetic tokens only.
Pure unit file: no server, no SQLite, no config.json, no network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Make the project root importable regardless of the working directory.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402


# -- helpers ------------------------------------------------------------------


def _make_feed_ref() -> tuple[dict, object]:
    """Build a feed_ref dict with a stub feed exposing update_credentials."""
    from brokers.upstox.auth import UpstoxCredentials

    class _StubFeed:
        def __init__(self) -> None:
            self._credentials = UpstoxCredentials(access_token="unset-placeholder")
            self._state = "stopped"
            self.name = "upstox"

        def update_credentials(self, credentials: UpstoxCredentials) -> None:
            self._credentials = credentials
            if self._state == "failed":
                self._state = "stopped"

        def status(self) -> dict:
            return {"state": self._state}

    ref: dict = {"feed": _StubFeed()}
    return ref, ref["feed"]


def _build_auth_routes(feed_ref: dict):
    from api.routes import build_auth_routes
    return build_auth_routes(feed_ref)


def _find_route(routes, suffix: str):
    return next(r for r in routes if r.path.endswith(suffix))


async def _call(route, method: str, body: dict | None = None) -> tuple[int, dict]:
    """Invoke a Starlette route endpoint directly with a fake request."""
    from starlette.requests import Request

    scope = {
        "type": "http", "method": method, "path": route.path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": b"", "server": ("test", 80), "scheme": "http",
    }

    async def receive():
        if body is not None:
            return {"type": "http.request",
                    "body": json.dumps(body).encode(), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    response = await route.endpoint(request)
    if hasattr(response, "body_iterator"):
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk
    else:
        resp_body = bytes(response.body)
    return response.status_code, json.loads(resp_body)


# -- tests ---------------------------------------------------------------------


async def test_wa1_status_no_token(runner: R) -> None:
    """WA1: auth status endpoint responds without a configured token."""
    async def run():
        ref, _feed = _make_feed_ref()
        routes = _build_auth_routes(ref)
        code, data = await _call(_find_route(routes, "/status"), "GET")
        return code, data

    code, data = await run()
    runner.assert_eq("WA1-code", code, 200)
    runner.assert_eq("WA1-configured-true", data.get("configured"), True)
    runner.assert_false("WA1-no-token-value",
                        any(k not in ("token_configured", "expiry_known")
                            and isinstance(v, str) and len(v) > 40
                            for k, v in data.items()))


async def test_wa2_env_token_status(runner: R) -> None:
    """WA2: pre-existing (ENV-style) credentials show token_configured."""
    from brokers.upstox.auth import UpstoxCredentials

    async def run():
        ref, feed = _make_feed_ref()
        feed._credentials = UpstoxCredentials(access_token="env-token-abc123")
        routes = _build_auth_routes(ref)
        return await _call(_find_route(routes, "/status"), "GET")

    code, data = await run()
    runner.assert_eq("WA2-code", code, 200)
    runner.assert_true("WA2-token-present", data.get("token_configured") is True)


async def test_wa3_runtime_token_submission(runner: R) -> None:
    """WA3: POST /token updates feed credentials."""
    async def run():
        ref, feed = _make_feed_ref()
        routes = _build_auth_routes(ref)
        code, data = await _call(
            _find_route(routes, "/token"), "POST",
            {"access_token": "synthetic-runtime-tok-xyz"})
        return code, data, feed

    code, data, feed = await run()
    runner.assert_eq("WA3-code", code, 200)
    runner.assert_eq("WA3-configured", data.get("configured"), True)
    runner.assert_eq("WA3-feed-updated",
                     feed._credentials.access_token,
                     "synthetic-runtime-tok-xyz")


async def test_wa4_token_never_returned(runner: R) -> None:
    """WA4: submitted secret never echoed in submit or status responses."""
    secret = "SECRET-TOKEN-NEVER-ECHO-9931"

    async def run():
        ref, _feed = _make_feed_ref()
        routes = _build_auth_routes(ref)
        c1, d1 = await _call(_find_route(routes, "/token"), "POST",
                             {"access_token": secret})
        c2, d2 = await _call(_find_route(routes, "/status"), "GET")
        return c1, d1, c2, d2

    c1, d1, c2, d2 = await run()
    runner.assert_eq("WA4-submit-ok", c1, 200)
    runner.assert_not_in("WA4-no-echo-submit", secret, json.dumps(d1))
    runner.assert_not_in("WA4-no-echo-status", secret, json.dumps(d2))


async def test_wa5_token_absent_from_repr(runner: R) -> None:
    """WA5: credentials repr/str redact the token."""
    from brokers.upstox.auth import UpstoxCredentials

    secret = "REPR-SAFETY-SECRET-7742"
    creds = UpstoxCredentials(access_token=secret)
    r = repr(creds)
    s = str(creds)
    runner.assert_not_in("WA5-repr-clean", secret, r)
    runner.assert_not_in("WA5-str-clean", secret, s)


async def test_wa6_empty_token_rejected(runner: R) -> None:
    """WA6: empty/whitespace/missing/non-string tokens all rejected."""

    async def run():
        ref, _feed = _make_feed_ref()
        routes = _build_auth_routes(ref)
        results = []
        for body in [{"access_token": ""},
                     {"access_token": "   \t\n"},
                     {},
                     {"access_token": 12345}]:
            results.append(await _call(_find_route(routes, "/token"),
                                       "POST", body))
        return results

    results = await run()
    for i, (code, _data) in enumerate(results):
        runner.assert_eq(f"WA6-case-{i}-rejected", code, 400)


async def test_wa7_runtime_overrides_env(runner: R) -> None:
    """WA7: runtime submission replaces pre-existing ENV credentials."""
    from brokers.upstox.auth import UpstoxCredentials

    env_tok = "ENV-PREEXISTING-TOKEN"
    runtime_tok = "RUNTIME-OVERRIDE-TOKEN"

    async def run():
        ref, feed = _make_feed_ref()
        feed._credentials = UpstoxCredentials(access_token=env_tok)
        routes = _build_auth_routes(ref)
        await _call(_find_route(routes, "/token"), "POST",
                    {"access_token": runtime_tok})
        return feed

    feed = await run()
    runner.assert_eq("WA7-overridden",
                     feed._credentials.access_token, runtime_tok)
    runner.assert_not_in("WA7-not-env", env_tok,
                         feed._credentials.access_token)


async def test_wa8_wa9_frontend_hygiene(runner: R) -> None:
    """WA8/WA9: input cleared after success; no browser-storage writes."""
    js_path = os.path.join(_PROJECT_DIR, "web", "ui", "js", "app.js")
    with open(js_path, encoding="utf-8") as f:
        src = f.read()
    runner.assert_in("WA8-clears-input", 'input.value = ""', src)
    # Theme persistence via localStorage is allowed; token storage is not.
    # Assert no storage write occurs on any line mentioning a token.
    storage_writes = [ln for ln in src.splitlines()
                      if ("localStorage.setItem" in ln
                          or "sessionStorage.setItem" in ln
                          or "document.cookie" in ln)]
    token_storage = [ln for ln in storage_writes
                     if "token" in ln.lower()]
    runner.assert_eq("WA9-no-token-storage-writes", token_storage, [])


async def test_wa10_auth_failure_safe_message(runner: R) -> None:
    """WA10: failure wording is safe; no raw error or WSS leak in client."""
    js_path = os.path.join(_PROJECT_DIR, "web", "ui", "js", "app.js")
    with open(js_path, encoding="utf-8") as f:
        src = f.read()
    runner.assert_in("WA10-safe-wording",
                     "Access token may be invalid or expired", src)
    runner.assert_not_in("WA10-no-raw-error-passthrough", "data.raw", src)
    runner.assert_not_in("WA10-no-wss-leak", "wss://", src)


async def test_wa11_shared_market_service(runner: R) -> None:
    """WA11: credential update does not construct a new MarketService."""
    async def run():
        ref, feed = _make_feed_ref()
        routes = _build_auth_routes(ref)
        await _call(_find_route(routes, "/token"), "POST",
                    {"access_token": "tok-for-svc-check"})
        return feed

    feed = await run()
    # The stub feed has no market_service attribute — update_credentials must
    # only touch credentials/state. Verify nothing else was added.
    extra = [a for a in vars(feed) if a not in ("_credentials", "_state", "name")]
    runner.assert_eq("WA11-only-creds-changed", extra, [])


async def test_wa12_no_duplicate_source(runner: R) -> None:
    """WA12: same feed object is reused (no second WS/source constructed)."""
    async def run():
        ref, feed = _make_feed_ref()
        original_id = id(feed)
        routes = _build_auth_routes(ref)
        await _call(_find_route(routes, "/token"), "POST",
                    {"access_token": "same-object-check"})
        return id(ref["feed"]), original_id

    new_id, original_id = await run()
    runner.assert_eq("WA12-same-feed-id", new_id, original_id)


async def test_wa13_source_manager_owner(runner: R) -> None:
    """WA13: auth routes delegate lifecycle; never construct feeds directly."""
    routes_path = os.path.join(_PROJECT_DIR, "api", "routes.py")
    with open(routes_path, encoding="utf-8") as f:
        src = f.read()
    runner.assert_not_in("WA13-no-direct-construction", "UpstoxFeed(", src)
    runner.assert_in("WA13-delegated-restart", "restart_fn", src)


async def test_wa14_imports_intact(runner: R) -> None:
    """WA14: app composition and route builders still import cleanly."""
    from api.routes import build_market_routes, build_auth_routes  # noqa: F401
    from brokers.upstox.feed import UpstoxFeed  # noqa: F401
    runner.assert_true("WA14-all-imports-ok", True)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_wa1_status_no_token(runner)
    await test_wa2_env_token_status(runner)
    await test_wa3_runtime_token_submission(runner)
    await test_wa4_token_never_returned(runner)
    await test_wa5_token_absent_from_repr(runner)
    await test_wa6_empty_token_rejected(runner)
    await test_wa7_runtime_overrides_env(runner)
    await test_wa8_wa9_frontend_hygiene(runner)
    await test_wa10_auth_failure_safe_message(runner)
    await test_wa11_shared_market_service(runner)
    await test_wa12_no_duplicate_source(runner)
    await test_wa13_source_manager_owner(runner)
    await test_wa14_imports_intact(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

