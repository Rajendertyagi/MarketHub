#!/usr/bin/env python3
"""Test Center broker auth-health tests (no secrets, read-only).

Backend: /api/diagnostics exposes an allow-listed `auth` subsection per
broker (auth facts + feed facts, never secret material).
WebUI: the diagnostics page renders the five distinct states
(Authenticated+Streaming / Authenticated+Feed-problem / Login Required /
Expired / Rejected) from read-only facts.
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

# Key NAMES like access_token_active carry no secret; what must never
# appear are secret VALUES and bare secret-bearing keys.
FORBIDDEN_VALUES = ("MUST-NOT-LEAK",)
FORBIDDEN_KEYS = (
    '"access_token"', '"refresh_token"', '"api_secret"', '"api_key"',
    '"secret_id"', '"client_secret"', '"pin"', '"auth_code"',
    '"redirect_uri"',
)
FORBIDDEN_SUBSTRINGS = ("wss", "pending",)


def _routes():
    from api.product_routes import build_diagnostics_routes

    def _sources():
        return [
            {"name": "upstox", "provider": "upstox", "state": "streaming",
             "task_running": True},
            {"name": "fyers", "provider": "fyers", "state": "reconnecting",
             "task_running": True},
        ]

    def _auth():
        return {
            "upstox": {
                "auth_state": "authenticated", "authenticated": True,
                "login_required": False, "token_configured": True,
                "session_persisted": True, "session_restored": True,
                "expires_at": "2099-01-01T00:00:00+00:00",
                "last_auth_status": "restored",
                "access_token": "MUST-NOT-LEAK",
                "redirect_uri": "http://x/cb",
            },
            "fyers": {
                "access_token_active": True, "login_required": False,
                "session_persisted": True, "session_restored": True,
                "access_token_expires_at": "2099-01-01T00:00:00+00:00",
                "last_auth_status": "restored_access_token",
                "refresh_token": "MUST-NOT-LEAK",
            },
        }

    return build_diagnostics_routes(
        "9.9.9", None, _sources, lambda: "http://localhost:7070",
        auth_status_fn=_auth)


def _get(routes, path):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    return TestClient(Starlette(routes=routes)).get(path).json()


async def test_auth_section_present(runner: R) -> None:
    d = _get(_routes(), "/api/diagnostics")
    runner.assert_true("diag-auth-present", isinstance(d.get("auth"), dict))
    runner.assert_true("diag-auth-upstox", isinstance(
        d["auth"].get("upstox"), dict))
    runner.assert_true("diag-auth-fyers", isinstance(
        d["auth"].get("fyers"), dict))


async def test_auth_fields_allow_listed(runner: R) -> None:
    d = _get(_routes(), "/api/diagnostics")
    u = d["auth"]["upstox"]
    for k in ("auth_state", "authenticated", "login_required",
              "token_configured", "session_persisted", "session_restored",
              "expires_at", "last_auth_status", "feed"):
        runner.assert_true(f"diag-up-{k}", k in u)
    f = d["auth"]["fyers"]
    for k in ("authenticated", "auth_state", "login_required",
              "session_persisted", "session_restored", "feed"):
        runner.assert_true(f"diag-fy-{k}", k in f)
    runner.assert_eq("diag-up-feed", u["feed"]["state"], "streaming")
    runner.assert_eq("diag-fy-feed", f["feed"]["state"], "reconnecting")


async def test_no_secrets_anywhere(runner: R) -> None:
    import json as _json
    d = _get(_routes(), "/api/diagnostics")
    blob = _json.dumps(d).lower()
    for needle in FORBIDDEN_VALUES:
        runner.assert_not_in(f"diag-nosecret-val-{needle}", needle.lower(),
                             blob)
    for needle in FORBIDDEN_KEYS:
        runner.assert_not_in(f"diag-nosecret-key-{needle}", needle, blob)
    for needle in FORBIDDEN_SUBSTRINGS:
        runner.assert_not_in(f"diag-nosecret-{needle}", needle, blob)


async def test_auth_section_absent_without_fn(runner: R) -> None:
    from api.product_routes import build_diagnostics_routes
    routes = build_diagnostics_routes("9.9.9", None, lambda: [],
                                      lambda: "http://localhost:7070")
    d = _get(routes, "/api/diagnostics")
    runner.assert_eq("diag-auth-empty", d.get("auth"), {})



async def main() -> bool:
    runner = R()
    await test_auth_section_present(runner)
    await test_auth_fields_allow_listed(runner)
    await test_no_secrets_anywhere(runner)
    await test_auth_section_absent_without_fn(runner)
    return runner.summary()


if __name__ == "__main__":
    import asyncio as _asyncio
    _ok = _asyncio.run(main())
    sys.exit(0 if _ok else 1)
