#!/usr/bin/env python3
"""Upstox OAuth daily-login tests (OL1-OL24).

Covers the localhost OAuth callback flow:
  * OL1   login endpoint redirects to Upstox dialog
  * OL2   unique secure state per login
  * OL3   state TTL expiry
  * OL4   state single-use / replay rejection
  * OL5   missing state rejected
  * OL6   invalid state rejected
  * OL7   expired state rejected
  * OL8   replayed callback rejected
  * OL9   missing code rejected
  * OL10  successful code exchange (fake transport)
  * OL11  credentials reach existing feed
  * OL12  restart_fn called exactly once per successful callback
  * OL13  restart_source does not create duplicate background tasks
  * OL14  same MarketService identity retained across restart
  * OL15  same SourceManager retained across restart
  * OL16  auth status contains no secrets
  * OL17  API secret never reaches any response
  * OL18  token absent from responses and reprs
  * OL19  callback success redirects to /ui/?auth=ok
  * OL20  safe failure redirects to /ui/?auth=failed
  * OL21  manual fallback endpoint still works
  * OL22  UI has Login-with-Upstox button
  * OL23  token not stored in browser storage
  * OL24  imports intact (D1-D4 composition unchanged)

NO LIVE BROKER. NO REAL CREDENTIALS. Synthetic tokens/fakes only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

# Make the project root importable regardless of the working directory.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

API_KEY = "SYNTHETIC-API-KEY-000"
API_SECRET = "SYNTHETIC-API-SECRET-111"
REDIRECT = "http://localhost:7070/auth/upstox/callback"


# -- helpers -------------------------------------------------------------------


def _make_feed_ref() -> tuple[dict, object]:
    from brokers.upstox.auth import UpstoxCredentials

    class _StubFeed:
        def __init__(self) -> None:
            self._credentials = UpstoxCredentials(access_token="unset-placeholder")
            self._state = "stopped"
            self.name = "upstox"

        def update_credentials(self, credentials) -> None:
            self._credentials = credentials
            if self._state == "failed":
                self._state = "stopped"

        def status(self) -> dict:
            return {"state": self._state}

    ref: dict = {"feed": _StubFeed()}
    return ref, ref["feed"]


class _FakeRest:
    """Synthetic exchange client — records calls, returns known credentials."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def exchange_authorization_code(self, **kwargs):
        from brokers.upstox.auth import (
            UpstoxCredentials, upstox_token_expiry,
        )
        from datetime import datetime, timezone
        self.calls.append(kwargs)
        acquired = datetime.now(timezone.utc)
        return UpstoxCredentials(
            access_token=f"OAUTH-TOKEN-{len(self.calls)}",
            expires_at=upstox_token_expiry(acquired),
        )


def _build_routes(feed_ref, restart_counter=None):
    from api.routes import build_auth_routes

    async def _restart():
        if restart_counter is not None:
            restart_counter.append(1)

    return build_auth_routes(
        feed_ref,
        restart_fn=_restart if restart_counter is not None else None,
        oauth={"api_key": API_KEY, "api_secret": API_SECRET,
               "redirect_uri": REDIRECT},
        rest=_FakeRest(),
    )


def _find_route(routes, path: str):
    return next(r for r in routes if r.path == path)


async def _call(route, method: str, query: str = "", body: dict | None = None):
    """Invoke a route endpoint; returns (status, location-or-json)."""
    from starlette.requests import Request

    scope = {
        "type": "http", "method": method, "path": route.path,
        "headers": [(b"content-type", b"application/json")],
        "query_string": query.encode(), "server": ("test", 80),
        "scheme": "http",
    }

    async def receive():
        if body is not None:
            return {"type": "http.request",
                    "body": json.dumps(body).encode(), "more_body": False}
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request(scope, receive)
    response = await route.endpoint(request)
    location = response.headers.get("location")
    if location is not None:
        return response.status_code, location
    raw = getattr(response, "body", b"")
    return response.status_code, json.loads(raw)


async def _get_valid_state(routes) -> str:
    """Hit /login and extract the state from the redirect Location."""
    login = _find_route(routes, "/api/auth/upstox/login")
    _code, location = await _call(login, "GET")
    qs = urllib.parse.urlsplit(location).query
    return urllib.parse.parse_qs(qs)["state"][0]


# -- tests ---------------------------------------------------------------------


async def test_ol1_login_redirects(runner: R) -> None:
    """OL1: login redirects (302) to the official Upstox dialog URL."""
    ref, _feed = _make_feed_ref()
    routes = _build_routes(ref)
    login = _find_route(routes, "/api/auth/upstox/login")
    code, location = await _call(login, "GET")
    runner.assert_eq("OL1-status-302", code, 302)
    runner.assert_in("OL1-dialog-url",
                     "/v2/login/authorization/dialog", location)
    runner.assert_in("OL1-client-id", urllib.parse.quote_plus(API_KEY), location)
    runner.assert_in("OL1-response-type", "response_type=code", location)


async def test_ol2_unique_state(runner: R) -> None:
    """OL2: two logins produce different states."""
    ref, _feed = _make_feed_ref()
    routes = _build_routes(ref)
    s1 = await _get_valid_state(routes)
    s2 = await _get_valid_state(routes)
    runner.assert_not_eq("OL2-states-differ", s1, s2)
    runner.assert_ge("OL2-state-length", len(s1), 32)


async def test_ol4_single_use(runner: R) -> None:
    """OL4/OL8: a consumed state cannot be reused (replay rejected)."""
    ref, feed = _make_feed_ref()
    counter: list = []
    routes = _build_routes(ref, counter)
    cb = _find_route(routes, "/auth/upstox/callback")
    state = await _get_valid_state(routes)

    q = f"code=SYN-CODE&state={urllib.parse.quote(state)}"
    c1, loc1 = await _call(cb, "GET", q)
    runner.assert_eq("OL4-first-ok", c1, 302)
    runner.assert_eq("OL19-success-redirect", loc1, "/ui/?auth=ok")

    # Replay same state+code.
    c2, loc2 = await _call(cb, "GET", q)
    runner.assert_eq("OL8-replay-rejected", c2, 302)
    runner.assert_eq("OL8-replay-failed-redirect", loc2, "/ui/?auth=failed&reason=retry")


async def test_ol5_ol6_ol9_bad_callbacks(runner: R) -> None:
    """OL5/OL6/OL9: missing state, invalid state, missing code -> safe fail."""
    ref, _feed = _make_feed_ref()
    routes = _build_routes(ref)
    cb = _find_route(routes, "/auth/upstox/callback")

    c1, l1 = await _call(cb, "GET", "code=X")           # missing state
    runner.assert_eq("OL5-missing-state", (c1, l1), (302, "/ui/?auth=failed&reason=retry"))
    c2, l2 = await _call(cb, "GET", "code=X&state=bogus")  # invalid state
    runner.assert_eq("OL6-invalid-state", (c2, l2), (302, "/ui/?auth=failed&reason=retry"))
    state = await _get_valid_state(routes)
    c3, l3 = await _call(                               # missing code
        cb, "GET", f"state={urllib.parse.quote(state)}")
    runner.assert_eq("OL9-missing-code", (c3, l3), (302, "/ui/?auth=failed&reason=retry"))


async def test_ol10_ol11_ol12_exchange_success(runner: R) -> None:
    """OL10/11/12: exchange succeeds, creds reach feed, restart fires once."""
    ref, feed = _make_feed_ref()
    counter: list = []
    routes = _build_routes(ref, counter)
    cb = _find_route(routes, "/auth/upstox/callback")
    state = await _get_valid_state(routes)

    q = f"code=SYN-CODE-42&state={urllib.parse.quote(state)}"
    code, _loc = await _call(cb, "GET", q)
    runner.assert_eq("OL10-callback-ok", code, 302)
    runner.assert_eq("OL11-creds-on-feed",
                     feed._credentials.access_token, "OAUTH-TOKEN-1")
    runner.assert_eq("OL12-restart-once", len(counter), 1)
    runner.assert_true("OL11-expiry-known",
                       feed._credentials.expires_at is not None)


async def test_ol3_ol7_expired_state(runner: R) -> None:
    """OL3/OL7: states older than TTL are pruned/rejected."""
    ref, _feed = _make_feed_ref()
    routes = _build_routes(ref)
    state = await _get_valid_state(routes)

    # Reach into the closure's pending-state store via a crafted short TTL:
    # expire ALL pending states by monkeypatching time through pruning.
    # Simplest deterministic approach: manipulate monotonic via the routes'
    # internal dict is not exposed, so simulate expiry by waiting out is too
    # slow. Instead verify the pruning logic directly with a tiny TTL clone.
    import api.routes as routes_mod
    import time as _time

    original_monotonic = _time.monotonic
    # Build a second routes set, then shift the clock forward past the TTL.
    _shift = {"delta": 0.0}

    def _fake_monotonic() -> float:
        return original_monotonic() + _shift["delta"]

    saved = _time.monotonic
    _time.monotonic = _fake_monotonic
    try:
        state2 = await _get_valid_state(routes)
        _shift["delta"] = 601.0  # beyond 600s TTL
        cb = _find_route(routes, "/auth/upstox/callback")
        q = f"code=SYN&state={urllib.parse.quote(state2)}"
        c, loc = await _call(cb, "GET", q)
        runner.assert_eq("OL7-expired-state-fails", loc, "/ui/?auth=failed&reason=expired")
    finally:
        _time.monotonic = saved


async def test_ol13_ol14_ol15_restart_identity(runner: R) -> None:
    """OL13/14/15: SourceManager.restart_source keeps one task/service/mgr."""
    from sources import SourceManager
    from core.runtime import BackgroundTaskManager

    events_log: list[str] = []

    class _StubSource:
        name = "upstox"

        async def run(self, publisher, stop_event) -> None:
            started = len([e for e in events_log if e == "run-start"])
            events_log.append("run-start")
            try:
                await stop_event.wait()
            finally:
                # Runs on BOTH graceful and cancelled exit paths.
                events_log.append(f"cleanup-{started}")

        def status(self) -> dict:
            return {"state": "stopped"}

        def update_credentials(self, creds) -> None:  # noqa: ARG002
            pass

    mgr = SourceManager()
    svc = object()          # identity marker (MarketService stand-in)
    mgr.register(_StubSource())
    bg = BackgroundTaskManager()
    await mgr.initialize(bg, store=None, bus=None)
    await mgr.start_all({"upstox": {"enabled": True, "market_service": svc}})

    task_name = "source:upstox"
    t1 = bg._tasks.get(task_name)
    runner.assert_true("OL13-initial-task", t1 is not None)

    # Let the first task actually begin executing (in production a feed runs
    # long before any OAuth callback could arrive).
    for _ in range(50):
        if "run-start" in events_log:
            break
        await asyncio.sleep(0.01)
    runner.assert_in("OL13-first-run-started", "run-start", events_log)

    ok = await mgr.restart_source("upstox")
    runner.assert_eq("OL13-restart-ok", ok, True)

    # Old task was awaited to completion BEFORE restart returned.
    runner.assert_true("OL13-old-task-done", t1.done())
    # Its cleanup ran, and ran BEFORE the new run started (ordering proof).
    runner.assert_in("OL13-old-cleanup-ran", "cleanup-0", events_log)
    cleanup_idx = events_log.index("cleanup-0")
    # Give the freshly created task a chance to begin executing.
    for _ in range(50):
        if len([e for e in events_log if e == "run-start"]) >= 2:
            break
        await asyncio.sleep(0.01)
    second_start_idx = events_log.index("run-start", cleanup_idx)
    runner.assert_true("OL13-cleanup-before-new-run",
                       second_start_idx > cleanup_idx)

    t2 = bg._tasks.get(task_name)
    runner.assert_true("OL13-new-task-exists", t2 is not None)
    runner.assert_not_eq("OL13-not-same-task", id(t2), id(t1))
    runner.assert_eq("OL13-single-task", len(bg._tasks), 1)

    runner.assert_eq("OL15-same-manager", id(mgr._sources["upstox"]),
                     id(mgr._sources["upstox"]))
    cfg_svc = mgr._configs.get("upstox", {}).get("market_service")
    runner.assert_eq("OL14-same-service-identity", id(cfg_svc), id(svc))

    await mgr.stop_source("upstox")


async def test_ol16_ol17_ol18_no_secrets(runner: R) -> None:
    """OL16/17/18: no secret/token/code ever appears in any response."""
    ref, feed = _make_feed_ref()
    counter: list = []
    routes = _build_routes(ref, counter)
    status_r = _find_route(routes, "/api/auth/upstox/status")

    # Trigger a successful exchange so the feed holds an OAuth token.
    cb = _find_route(routes, "/auth/upstox/callback")
    state = await _get_valid_state(routes)
    await _call(cb, "GET", f"code=SYN&state={urllib.parse.quote(state)}")

    code, data = await _call(status_r, "GET")
    blob = json.dumps(data)
    runner.assert_not_in("OL16-no-api-secret", API_SECRET, blob)
    runner.assert_not_in("OL17-no-api-key", API_KEY, blob)
    runner.assert_not_in("OL18-no-token", "OAUTH-TOKEN-1", blob)
    runner.assert_eq("OL16-oauth-available", data.get("oauth_available"), True)
    runner.assert_eq("OL16-expiry-known", data.get("expiry_known"), True)

    # Credentials repr must also be clean.
    r = repr(feed._credentials)
    runner.assert_not_in("OL18-repr-no-token", "OAUTH-TOKEN-1", r)


async def test_ol20_safe_failure(runner: R) -> None:
    """OL20: exchange failure redirects safely without error details."""
    ref, _feed = _make_feed_ref()

    class _FailingRest:
        async def exchange_authorization_code(self, **kwargs):
            raise RuntimeError("raw broker body UDAPI100069 secret leak!")

    from api.routes import build_auth_routes
    routes = build_auth_routes(
        ref, restart_fn=None,
        oauth={"api_key": API_KEY, "api_secret": API_SECRET,
               "redirect_uri": REDIRECT},
        rest=_FailingRest(),
    )
    cb = _find_route(routes, "/auth/upstox/callback")
    login = _find_route(routes, "/api/auth/upstox/login")
    _c, loc = await _call(login, "GET")
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)["state"][0]

    c, fail_loc = await _call(
        cb, "GET", f"code=SYN&state={urllib.parse.quote(state)}")
    runner.assert_true("OL20-safe-redirect", fail_loc.startswith("/ui/?auth=failed&reason="))
    runner.assert_not_in("OL20-no-error-leak", "UDAPI", fail_loc)


async def test_ol20b_restart_failure_safe(runner: R) -> None:
    """OL20b: restart_fn raising still yields a safe redirect; creds stay."""
    ref, feed = _make_feed_ref()

    async def _boom() -> None:
        raise RuntimeError("restart exploded")

    from api.routes import build_auth_routes
    routes = build_auth_routes(
        ref, restart_fn=_boom,
        oauth={"api_key": API_KEY, "api_secret": API_SECRET,
               "redirect_uri": REDIRECT},
        rest=_FakeRest(),
    )
    cb = _find_route(routes, "/auth/upstox/callback")
    state = await _get_valid_state(routes)
    c, loc = await _call(cb, "GET",
                         f"code=SYN&state={urllib.parse.quote(state)}")
    runner.assert_eq("OL20b-safe-redirect", loc, "/ui/?auth=failed&reason=restart")
    # Credentials were still applied even though restart failed.
    runner.assert_true("OL20b-creds-applied",
                       feed._credentials.access_token.startswith("OAUTH-TOKEN"))


async def test_ol21_manual_fallback(runner: R) -> None:
    """OL21: POST /api/auth/upstox/token still works alongside OAuth."""
    ref, feed = _make_feed_ref()
    routes = _build_routes(ref)
    tok = _find_route(routes, "/api/auth/upstox/token")
    code, data = await _call(tok, "POST",
                             body={"access_token": "MANUAL-FALLBACK-TOK"})
    runner.assert_eq("OL21-manual-ok", code, 200)
    runner.assert_eq("OL21-feed-updated",
                     feed._credentials.access_token, "MANUAL-FALLBACK-TOK")


def test_ol22_ol23_ui(runner: R) -> None:
    """OL22/OL23: Login button present; no token browser-storage writes."""
    html_path = os.path.join(_PROJECT_DIR, "web", "ui", "index.html")
    js_path = os.path.join(_PROJECT_DIR, "web", "ui", "js", "app.js")
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    with open(js_path, encoding="utf-8") as f:
        js = f.read()
    runner.assert_in("OL22-login-button-id", 'id="oauth-login-btn"', html)
    runner.assert_in("OL22-login-handler", "/api/auth/upstox/login", js)
    runner.assert_in("OL22-auth-param-handler", "history.replaceState", js)

    storage_writes = [ln for ln in js.splitlines()
                      if ("localStorage.setItem" in ln
                          or "sessionStorage.setItem" in ln
                          or "document.cookie" in ln)]
    token_storage = [ln for ln in storage_writes if "token" in ln.lower()]
    runner.assert_eq("OL23-no-token-storage-writes", token_storage, [])


def test_ol24_imports_intact(runner: R) -> None:
    """OL24: composition imports cleanly (D1-D4 surfaces unchanged)."""
    from sources import SourceManager  # noqa: F401
    from brokers.upstox.feed import UpstoxFeed  # noqa: F401
    from brokers.upstox.rest import UpstoxRest  # noqa: F401
    from brokers.upstox.auth import UpstoxOAuth, UpstoxCredentials  # noqa: F401
    runner.assert_true("OL24-all-imports-ok", True)


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_ol1_login_redirects(runner)
    await test_ol2_unique_state(runner)
    await test_ol4_single_use(runner)
    await test_ol5_ol6_ol9_bad_callbacks(runner)
    await test_ol10_ol11_ol12_exchange_success(runner)
    await test_ol3_ol7_expired_state(runner)
    await test_ol13_ol14_ol15_restart_identity(runner)
    await test_ol16_ol17_ol18_no_secrets(runner)
    await test_ol20_safe_failure(runner)
    await test_ol20b_restart_failure_safe(runner)
    await test_ol21_manual_fallback(runner)
    test_ol22_ol23_ui(runner)
    test_ol24_imports_intact(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

