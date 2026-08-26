#!/usr/bin/env python3
"""
Web UI + static serving + API surface tests — in-process (no subprocess).

Verifies the compact trading dashboard is actually served by the app and
that the market/SSE/source APIs are unchanged by the UI work:

  * W1  GET /ui/ serves the dashboard HTML with top navigation,
        all six views, a theme toggle, and NO sidebar layout
  * W2  static CSS loads (text/css, custom-property theming present)
  * W3  static JS loads and wires exactly one EventSource to the
        market stream
  * W4  self-hosted Web Awesome assets load (autoloader + base styles)
  * W5  market quote API shape unchanged
  * W6  source status API shape unchanged
  * W7  SSE + health routes still registered on the composed app

Run:
    python test/test_web_ui.py
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402


def _client():
    from starlette.testclient import TestClient
    from app.server import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def w1_index_html(runner: R, c) -> None:
    """W1: /ui/ serves dashboard HTML: top nav, six views, theme toggle, no sidebar."""
    name = "W1-index-html"
    res = c.get("/ui/")
    runner.assert_eq(name + "-status", res.status_code, 200)
    runner.assert_in(name + "-html", "text/html", res.headers.get("content-type", ""))
    html = res.text
    for marker in (
        'class="topnav"',                # top navigation bar
    'data-view="dashboard"',
    'data-view="markets"',
    'data-view="options"',
    'data-view="mcp"',
        'id="view-settings"',            # settings view section
        'id="theme-toggle"',
        ">Dashboard<",
        ">Markets<",
        ">Option Chain<",
        ">MCP<",
        ">Settings<",
    ):
        runner.assert_in(name + "-has:" + marker.strip("<>/"), marker, html)
    runner.assert_false(name + "-no-sidebar", "sidebar" in html.lower(),
                        "layout must use top navigation, not a sidebar")


def w2_static_css(runner: R, c) -> None:
    """W2: stylesheet served as CSS with the theming variables."""
    name = "W2-static-css"
    res = c.get("/ui/css/style.css")
    runner.assert_eq(name + "-status", res.status_code, 200)
    runner.assert_in(name + "-ctype", "text/css", res.headers.get("content-type", ""))
    for token in ("--bg", "--bg-panel", "html.wa-theme-light",
                  "html.wa-theme-dark"):
        runner.assert_in(name + "-has:" + token, token, res.text)


def w3_static_js(runner: R, c) -> None:
    """W3: app JS served; exactly one EventSource wired to the market stream."""
    name = "W3-static-js"
    res = c.get("/ui/js/app.js")
    runner.assert_eq(name + "-status", res.status_code, 200)
    body = res.text
    # Two EventSources BY DESIGN: market stream + low-frequency generic
    # event stream (alert push). Never more.
    runner.assert_eq(name + "-eventsource-count",
                     body.count("new EventSource"), 2)
    runner.assert_eq(name + "-one-market-eventsource",
                     body.count('new EventSource("/api/market/stream")'), 1)
    runner.assert_in(name + "-stream-url", '/api/market/stream"', body)
    # setInterval is allowed ONLY for UI/status refreshes — market data
    # itself must always arrive via the single EventSource, never polling.
    # Current refresh intervals: pollSources, pollAuthStatus, loadWatchlists,
    # loadAlerts, loadAlertHistory (alert trigger history), loadMarkets/...
    runner.assert_eq(name + "-poll-count", body.count("setInterval"), 6)
    runner.assert_in(name + "-poll-source-status",
                      "setInterval(pollSources", body)
    runner.assert_in(name + "-poll-auth-status",
                      "setInterval(pollAuthStatus", body)
    runner.assert_in(name + "-poll-watchlists",
                      "setInterval(loadWatchlists", body)
    runner.assert_in(name + "-poll-alerts",
                      "setInterval(loadAlerts", body)
    runner.assert_in(name + "-poll-alert-history",
                      "setInterval(loadAlertHistory", body)
    # Duplicate top-level identifier declarations make the WHOLE file fail
    # to parse (every nav link dies with a blank console error). Guard the
    # shared helpers against ever being declared twice.
    import re as _re
    for _ident in ("esc", "fmt", "fmtVol", "friendlyState", "friendlyShort",
                   "connectSSE", "switchView", "$"):
        _decls = _re.findall(
            r"\b(?:const|let|var|function)\s+" + _re.escape(_ident) + r"\b",
            body)
        runner.assert_le(name + "-single-decl:" + _ident, len(_decls), 1)


def w4_webawesome_vendored(runner: R, c) -> None:
    """W4: Web Awesome autoloader + base styles are self-hosted under /ui/vendor."""
    name = "W4-webawesome-vendored"
    loader = c.get("/ui/vendor/webawesome/dist/webawesome.loader.js")
    css = c.get("/ui/vendor/webawesome/dist/styles/webawesome.css")
    runner.assert_eq(name + "-loader-status", loader.status_code, 200)
    runner.assert_in(name + "-loader-js", "javascript", loader.headers.get("content-type", ""))
    runner.assert_eq(name + "-css-status", css.status_code, 200)
    runner.assert_in(name + "-css-ctype", "text/css", css.headers.get("content-type", ""))


def w5_market_quotes_api_unchanged(runner: R, c) -> None:
    """W5: /api/market/quotes keeps its canonical envelope."""
    name = "W5-quotes-api"
    res = c.get("/api/market/quotes")
    runner.assert_eq(name + "-status", res.status_code, 200)
    data = res.json()
    runner.assert_true(name + "-envelope", isinstance(data.get("quotes"), list),
                       "'quotes' list missing from response")


def w6_source_status_api_unchanged(runner: R, c) -> None:
    """W6: /api/sources/status keeps its canonical envelope."""
    name = "W6-sources-api"
    res = c.get("/api/sources/status")
    runner.assert_eq(name + "-status", res.status_code, 200)
    data = res.json()
    runner.assert_true(name + "-envelope", isinstance(data.get("sources"), list),
                       "'sources' list missing from response")


def w7_live_routes_registered(runner: R, c) -> None:
    """W7: SSE/health routes remain registered on the composed app."""
    name = "W7-routes"
    from app.server import app
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
    for expected in ("/api/market/stream", "/events/stream", "/health",
                     "/api/market/quotes", "/api/sources/status", "/ui"):
        runner.assert_true(name + ":" + expected, expected in paths,
                           f"route {expected} missing from composed app")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    runner = R()
    print("  Web UI / Static / API Tests (in-process)")
    print("=" * 50)

    tests = [
        w1_index_html,
        w2_static_css,
        w3_static_js,
        w4_webawesome_vendored,
        w5_market_quotes_api_unchanged,
        w6_source_status_api_unchanged,
        w7_live_routes_registered,
    ]
    # ONE shared client for the whole run: the MCP StreamableHTTPSessionManager
    # is a module-level singleton and its lifespan can only start once.
    with _client() as c:
        for fn in tests:
            try:
                fn(runner, c)
            except Exception as exc:
                runner.fail(fn.__name__, str(exc))

    success = runner.summary()
    sys.exit(0 if success else 1)
    return success


if __name__ == "__main__":
    main()
