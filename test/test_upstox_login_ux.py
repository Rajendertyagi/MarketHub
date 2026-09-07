#!/usr/bin/env python3
"""Upstox login WebUI simplicity tests (static analysis, no browser).

Normal UX contract:

  12. exactly ONE login action in the normal (non-Advanced) UI
  13. its visible label is "Login with Upstox"
  14. no visible "Renew" terminology
  15. no PIN login in the normal UI
  16. no manual-token box in the normal UI
  17. OAuth success reveals no second login form
  18. valid auth + feed down projects authenticated (never login-required)
  19. rejected/expired states show one login button
  20. Advanced/Recovery is collapsed by default

PIN + manual-token endpoints remain (recovery/API compat) but live ONLY
inside the collapsed Advanced / Recovery section.
"""

from __future__ import annotations

import os
import re
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

_UI = os.path.join(_PROJECT_DIR, "web", "ui")
_HTML = os.path.join(_UI, "index.html")
_AUTH_JS = os.path.join(_UI, "js", "auth.js")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _normal_html(html: str) -> str:
    """index.html with the Advanced/Recovery <details> block removed."""
    start = html.find('id="upstox-advanced"')
    assert start != -1, "upstox-advanced section missing"
    open_tag = html.rfind("<details", 0, start)
    close_tag = html.find("</details>", start)
    assert open_tag != -1 and close_tag != -1, "details block malformed"
    return html[:open_tag] + html[close_tag + len("</details>"):]


def _advanced_html(html: str) -> str:
    start = html.find('id="upstox-advanced"')
    open_tag = html.rfind("<details", 0, start)
    close_tag = html.find("</details>", start)
    return html[open_tag:close_tag]


async def test_single_login_action(runner: R) -> None:
    html = _read(_HTML)
    normal = _normal_html(html)
    buttons = re.findall(r"<button[^>]*>([^<]*)</button>", normal)
    login_buttons = [b for b in buttons if "login with upstox" in b.lower()]
    runner.assert_eq("ux12-one-login", len(login_buttons), 1)
    runner.assert_eq("ux13-label", login_buttons[0].strip(),
                     "Login with Upstox")


async def test_no_renew(runner: R) -> None:
    html = _read(_HTML)
    js = _read(_AUTH_JS)
    runner.assert_false("ux14-no-renew-html", "renew" in html.lower())
    runner.assert_false("ux14-no-renew-js", "renew" in js.lower())


async def test_pin_manual_in_advanced_only(runner: R) -> None:
    html = _read(_HTML)
    normal = _normal_html(html)
    adv = _advanced_html(html)
    for ident in ("oauth-login-pin-btn", "upstox-pin-row", "upstox-pin",
                  "upstox-pin-btn", "auth-token-input", "auth-submit"):
        runner.assert_false(f"ux15-16-normal-{ident}", ident in normal)
        runner.assert_true(f"ux15-16-advanced-{ident}", ident in adv)


async def test_advanced_collapsed_by_default(runner: R) -> None:
    html = _read(_HTML)
    m = re.search(r"<details[^>]*id=\"upstox-advanced\"[^>]*>", html)
    runner.assert_true("ux20-details-exists", m is not None)
    runner.assert_false("ux20-collapsed", "open" in m.group(0))
    summary = re.search(r"<summary>([^<]*)</summary>", _advanced_html(html))
    runner.assert_true("ux20-summary", summary is not None
                       and "advanced" in summary.group(1).lower())


async def test_oauth_success_no_second_form(runner: R) -> None:
    js = _read(_AUTH_JS)
    i = js.find('auth === "ok"')
    assert i != -1
    # The ok-branch runs until the next `else if`; it must only set the
    # transient message — never unhide a token/PIN form.
    branch = js[i:js.find("pin_required", i)]
    runner.assert_false("ux17-no-unhide", "remove(\"hidden\")" in branch
                        or "remove('hidden')" in branch)
    runner.assert_false("ux17-no-pin-focus", "pinInput" in branch
                        and "focus" in branch)


async def test_auth_driven_not_feed_driven(runner: R) -> None:
    js = _read(_AUTH_JS)
    # Auth chips derive from d.authenticated, never from feed state.
    runner.assert_true("ux18-authenticated-flag",
                       "d.authenticated === true" in js)
    runner.assert_false("ux18-no-token-configured-gate",
                        "!!d.token_configured && d.expired" in js)
    # Rejected/expired map to a single login button + honest labels.
    runner.assert_true("ux19-expired-label", "Session expired" in js)
    runner.assert_true("ux19-rejected-label", "Session needs login" in js)
    runner.assert_true("ux19-single-login",
                       'loginBtn.textContent = "Login with Upstox"' in js)


async def test_endpoints_retained_for_recovery(runner: R) -> None:
    """PIN/manual-token backends stay (recovery/API compat), UI-hidden."""
    from api.routes import build_auth_routes
    paths = {r.path for r in build_auth_routes(
        {"feed": None},
        oauth={"api_key": "", "api_secret": "",
               "redirect_uri": "http://x/cb"})}
    runner.assert_true("ux-pin-endpoint", "/api/auth/upstox/pin" in paths)
    runner.assert_true("ux-token-endpoint", "/api/auth/upstox/token" in paths)
    js = _read(_AUTH_JS)
    runner.assert_true("ux-pin-handler", "/api/auth/upstox/pin" in js)
    runner.assert_true("ux-token-handler", "/api/auth/upstox/token" in js)


async def main() -> bool:
    runner = R()
    await test_single_login_action(runner)
    await test_no_renew(runner)
    await test_pin_manual_in_advanced_only(runner)
    await test_advanced_collapsed_by_default(runner)
    await test_oauth_success_no_second_form(runner)
    await test_auth_driven_not_feed_driven(runner)
    await test_endpoints_retained_for_recovery(runner)
    return runner.summary()


if __name__ == "__main__":
    import asyncio as _asyncio
    _ok = _asyncio.run(main())
    sys.exit(0 if _ok else 1)
