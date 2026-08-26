#!/usr/bin/env python3
"""Unit tests for the Upstox REST boundary — no network, no credentials.

Phase D2.2 coverage:
  * UR1   feed-authorize success: request shape + URI extraction
  * UR2   authorize success-envelope violations (missing data/URI,
          wrong types, non-wss, status != success on HTTP 200)
  * UR3   authorize HTTP failures: 3xx no-follow, 401, 403, 429
          (+Retry-After), 500, network error, secret/URI leakage guards
  * UR4   exchange success: request shape (POST/form/grant_type),
          deterministic clock, known expiry, ignored fields, redacted repr
  * UR5   injected-clock validation (aware/non-aware/non-datetime)
  * UR6   exchange input validation without transport calls
  * UR7   exchange failures: 401, provider error envelope, non-JSON,
          client_secret/code/token absence in exceptions
  * UR8   HttpResponse repr redaction + no-redirect handler policy

Uses an injected fake sync transport (records every request). Synthetic
secrets only. Each test is independently runnable via
``python test/test_upstox_rest.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse

# Make the project root importable regardless of the working directory the
# test is launched from (mirrors test_unit_sources.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from datetime import datetime, timezone  # noqa: E402

from helpers.runner import R  # noqa: E402

UTC = timezone.utc

TOKEN = "SYNTHETIC_ACCESS_TOKEN_XYZ"
EXTENDED = "SYNTHETIC_EXTENDED_TOKEN_001"
CODE = "SYNTH_ONE_TIME_CODE_42"
SECRET = "SYNTHETIC_CLIENT_SECRET_99"
API_KEY = "SYNTHETIC_API_KEY_XYZ789"
REDIRECT = "https://example.test/callback"
WSS_URI = ("wss://feeder.example/market-data-feeder/v2/upstox-developer-api/"
           "feeds?requestId=REQ-1&code=ONE_CODE_MATERIAL")

FIXED_NOW = datetime(2026, 8, 24, 10, 0, 0, tzinfo=UTC)

AUTHORIZE_URL = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"


class FakeTransport:
    """Records requests; responder returns (status, headers, body) or raises."""

    def __init__(self, responder) -> None:
        self.requests: list[dict] = []
        self._responder = responder

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append({
            "method": method, "url": url, "headers": dict(headers),
            "body": body, "timeout": timeout,
        })
        outcome = self._responder(len(self.requests) - 1)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _json_body(payload) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _resp(status, headers=None, body=b""):
    """Build an HttpResponse for fake transports."""
    from brokers.upstox.rest import HttpResponse
    return HttpResponse(status=status, headers=dict(headers or {}), body=body)


def _ok(payload):
    return _resp(200, headers={"Content-Type": "application/json"},
                 body=_json_body(payload))


def _make_rest(responder, *, now=FIXED_NOW):
    from brokers.upstox import UpstoxRest

    return UpstoxRest(sync_transport=FakeTransport(responder),
                      utc_now=lambda: now)


def _creds():
    from brokers.upstox import UpstoxCredentials
    return UpstoxCredentials(access_token=TOKEN)


def _expect_raises(runner: R, label: str, exc_type: type, fn, needle: str | None = None) -> Exception | None:
    try:
        fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(label, needle in str(exc),
                               f"message should contain {needle!r}: {exc}")
        else:
            runner.ok(label)
        return exc
    except Exception as exc:  # narrow: report unexpected exception types
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return None
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")
    return None


async def _expect_raises_async(runner: R, label: str, exc_type: type, coro_fn, needle: str | None = None) -> Exception | None:
    """Await coro_fn() and assert it raises exc_type (async counterpart of
    _expect_raises — safe to call inside a running event loop)."""
    try:
        await coro_fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(label, needle in str(exc),
                               f"message should contain {needle!r}: {exc}")
        else:
            runner.ok(label)
        return exc
    except Exception as exc:  # narrow: report unexpected exception types
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return None
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")
    return None


# ---------------------------------------------------------------------------
# UR1/UR2 — feed authorize success + success-envelope violations
# ---------------------------------------------------------------------------


async def test_authorize_success(runner: R) -> None:
    """UR1: GET/endpoint/Bearer/Accept/no-body/10s + full URI returned."""
    name = "UR1-authorize-success"
    from brokers.upstox import UpstoxRest

    rest = _make_rest(lambda i: _ok(
        {"status": "success",
         "data": {"authorized_redirect_uri": WSS_URI}}))
    uri = await rest.authorize_market_feed(_creds())

    runner.assert_eq(name + "-uri", uri, WSS_URI)

    req = rest._transport.requests[0]
    runner.assert_eq(name + "-method", req["method"], "GET")
    runner.assert_eq(name + "-endpoint", req["url"], AUTHORIZE_URL)
    runner.assert_eq(name + "-bearer", req["headers"].get("Authorization"),
                     f"Bearer {TOKEN}")
    runner.assert_eq(name + "-accept", req["headers"].get("Accept"),
                     "application/json")
    runner.assert_true(name + "-no-body", req["body"] is None)
    runner.assert_eq(name + "-timeout", req["timeout"], 10.0)


async def test_authorize_envelope_violations(runner: R) -> None:
    """UR2: missing data/URI, wrong type, non-wss, status!=success on 200."""
    name = "UR2-envelope"
    from brokers.upstox import UpstoxRest, UpstoxRestError

    error_on_200 = {"status": "error", "error_codes": ["UDAPI100057"],
                    "errors": [{"errorCode": "UDAPI100057",
                                "message": "Invalid Auth code"}]}
    cases = [
        ("missing-data", {"status": "success"}, "missing data object"),
        ("missing-uri", {"status": "success", "data": {}},
         "missing authorized_redirect_uri"),
        ("wrong-type-uri", {"status": "success",
                            "data": {"authorized_redirect_uri": 12345}},
         "missing authorized_redirect_uri"),
        ("non-wss", {"status": "success",
                     "data": {"authorized_redirect_uri": "https://x/y"}},
         "wss"),
        ("error-envelope-on-200", error_on_200, "UDAPI100057"),
    ]
    for label, payload, needle in cases:
        rest = UpstoxRest(sync_transport=lambda *a: _ok(payload),
                          utc_now=lambda: FIXED_NOW)
        try:
            await rest.authorize_market_feed(_creds())
            runner.fail(name + f"-{label}", "expected UpstoxRestError")
        except UpstoxRestError as exc:
            runner.assert_true(name + f"-{label}", needle in str(exc),
                               f"message should contain {needle!r}: {exc}")
        except Exception as exc:  # narrow: report unexpected exception types
            runner.fail(name + f"-{label}",
                        f"unexpected {type(exc).__name__}: {exc}")


async def _authorize_failure_case(runner: R, label: str, responder,
                                  exc_name: str, needle: str | None,
                                  forbidden: tuple[str, ...]) -> None:
    from brokers.upstox import UpstoxRest

    rest = UpstoxRest(sync_transport=responder, utc_now=lambda: FIXED_NOW)
    try:
        await rest.authorize_market_feed(_creds())
        runner.fail(f"{label}", f"expected {exc_name}; nothing raised")
    except Exception as exc:
        ok_type = type(exc).__name__ == exc_name
        runner.assert_true(label + "-type", ok_type,
                           f"expected {exc_name}, got {type(exc).__name__}: {exc}")
        if needle is not None:
            runner.assert_true(label + "-needle", needle in str(exc),
                               f"message should contain {needle!r}: {exc}")
        dumped = str(exc) + repr(exc)
        for secret in forbidden:
            runner.assert_not_in(label + f"-no-leak-{secret[:12]}", secret, dumped)


async def test_authorize_http_failures(runner: R) -> None:
    """UR3: 3xx no-follow, 401, 403, 429(+Retry-After), 500, network,
    malformed JSON — plus secret/URI leakage guards."""
    name = "UR3-http-failures"
    from brokers.upstox import (
        UpstoxAuthError, UpstoxRateLimitError, UpstoxRest, UpstoxRestError,
    )

    err_env = {"status": "error", "error_codes": ["UDAPI100050"],
               "errors": [{"errorCode": "UDAPI100050",
                           "message": "Invalid token used to access"}]}

    # 3xx surfaced, NOT followed, non-retryable.
    rest = UpstoxRest(sync_transport=lambda *a: _resp(302, {"Location": WSS_URI}))
    exc = await _expect_raises_async(runner, name + "-3xx-no-follow", UpstoxRestError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-3xx-retryable", exc.retryable, False)
    runner.assert_not_in(name + "-3xx-no-uri-leak", WSS_URI, str(exc))

    # 401 -> UpstoxAuthError with parsed provider code/message.
    rest = UpstoxRest(sync_transport=lambda *a: _resp(401, body=_json_body(err_env)))
    exc = await _expect_raises_async(runner, name + "-401-type", UpstoxAuthError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_in(name + "-401-code", "UDAPI100050", str(exc))
    runner.assert_in(name + "-401-msg", "Invalid token used to access", str(exc))
    runner.assert_not_in(name + "-401-no-token", TOKEN, str(exc))

    # 403 -> non-retryable RestError.
    rest = UpstoxRest(sync_transport=lambda *a: _resp(403, body=_json_body(err_env)))
    exc = await _expect_raises_async(runner, name + "-403", UpstoxRestError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-403-retryable", exc.retryable, False)

    # 429 -> RateLimitError; integer Retry-After parsed, garbage ignored.
    rest = UpstoxRest(sync_transport=lambda *a: _resp(429, {"Retry-After": "7"}, body=_json_body(err_env)))
    exc = await _expect_raises_async(runner, name + "-429-type", UpstoxRateLimitError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-retry-after", exc.retry_after_seconds, 7)
    runner.assert_eq(name + "-429-retryable", exc.retryable, True)

    rest = UpstoxRest(sync_transport=lambda *a: _resp(429, {"Retry-After": "in 5s"}))
    exc = await _expect_raises_async(runner, name + "-429-garbage-ra", UpstoxRateLimitError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-garbage-ra-none", exc.retry_after_seconds, None)

    # 500 -> retryable RestError.
    rest = UpstoxRest(sync_transport=lambda *a: _resp(500, body=b"Internal Server Error"))
    exc = await _expect_raises_async(runner, name + "-500", UpstoxRestError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-500-retryable", exc.retryable, True)
    runner.assert_not_in(name + "-500-no-body", "Internal Server Error", str(exc))

    # Network failure -> retryable, status_code=None, fixed safe message.
    def boom(*a):
        raise OSError("host unreachable https://secret.example?token=x")
    rest = UpstoxRest(sync_transport=boom)
    exc = await _expect_raises_async(runner, name + "-network", UpstoxRestError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-network-status", exc.status_code, None)
    runner.assert_eq(name + "-network-retryable", exc.retryable, True)
    runner.assert_not_in(name + "-network-no-str-exc", "secret.example", str(exc))

    # Malformed JSON on 200 -> non-retryable.
    rest = UpstoxRest(sync_transport=lambda *a: _resp(200, body=b"{not json"))
    exc = await _expect_raises_async(runner, name + "-malformed-json", UpstoxRestError,
                                     lambda: rest.authorize_market_feed(_creds()))
    assert exc is not None
    runner.assert_eq(name + "-malformed-retryable", exc.retryable, False)


# ---------------------------------------------------------------------------
# UR4/UR5/UR6/UR7 — exchange
# ---------------------------------------------------------------------------


def _exchange_ok_responder(*a):
    return _ok({"access_token": TOKEN, "extended_token": EXTENDED,
                "user_name": "SYNTH_USER", "user_type": "INDIVIDUAL",
                "poa": True, "is_active": True, "email": "synth@example.test"})


async def test_exchange_success(runner: R) -> None:
    """UR4: POST/form/grant_type/15s; token mapped; expiry from clock."""
    name = "UR4-exchange-success"
    from brokers.upstox import UpstoxCredentials, UpstoxRest

    rest = _make_rest(_exchange_ok_responder)
    creds = await rest.exchange_authorization_code(
        code=CODE, client_id=API_KEY, client_secret=SECRET,
        redirect_uri=REDIRECT)

    runner.assert_true(name + "-is-credentials",
                       isinstance(creds, UpstoxCredentials))
    runner.assert_eq(name + "-token", creds.access_token, TOKEN)

    expected_expiry = upstox_token_expiry_ref(FIXED_NOW)
    runner.assert_eq(name + "-expiry-from-acquisition",
                     creds.expires_at, expected_expiry)

    req = rest._transport.requests[0]
    runner.assert_eq(name + "-method", req["method"], "POST")
    runner.assert_eq(name + "-endpoint", req["url"], TOKEN_URL)
    runner.assert_eq(name + "-content-type",
                     req["headers"].get("Content-Type"),
                     "application/x-www-form-urlencoded")
    runner.assert_eq(name + "-accept", req["headers"].get("Accept"),
                     "application/json")
    runner.assert_eq(name + "-timeout", req["timeout"], 15.0)

    form = urllib.parse.parse_qs(req["body"].decode("utf-8"))
    runner.assert_eq(name + "-form-fields", sorted(form.keys()),
                     ["client_id", "client_secret", "code", "grant_type",
                      "redirect_uri"])
    runner.assert_eq(name + "-form-code", form.get("code"), [CODE])
    runner.assert_eq(name + "-form-secret", form.get("client_secret"), [SECRET])
    runner.assert_eq(name + "-form-grant", form.get("grant_type"),
                     ["authorization_code"])
    runner.assert_eq(name + "-form-redirect", form.get("redirect_uri"), [REDIRECT])

    # extended_token/profile deliberately unmapped; repr redacted.
    ro = repr(creds)
    runner.assert_not_in(name + "-repr-no-token", TOKEN, ro)
    runner.assert_not_in(name + "-repr-no-extended", EXTENDED, ro)


def upstox_token_expiry_ref(now):
    """Reference to the frozen D2.1 helper (kept near assertions)."""
    from brokers.upstox import upstox_token_expiry
    return upstox_token_expiry(now)


async def test_exchange_clock_validation(runner: R) -> None:
    """UR5: injected clock must be a timezone-aware datetime."""
    name = "UR5-clock"
    from brokers.upstox import UpstoxRest, UpstoxRestError

    ist_like = timezone(__import__("datetime").timedelta(hours=5, minutes=30))
    rest = UpstoxRest(sync_transport=_exchange_ok_responder,
                      utc_now=lambda: datetime(2026, 8, 24, 13, 30, tzinfo=ist_like))
    creds = await rest.exchange_authorization_code(
        code=CODE, client_id=API_KEY, client_secret=SECRET,
        redirect_uri=REDIRECT)
    # Aware non-UTC clock accepted: same instant, canonical UTC expiry.
    runner.assert_eq(name + "-non-utc-clock-ok", creds.expires_at,
                     datetime(2026, 8, 24, 22, 0, 0, tzinfo=UTC))

    rest_naive = UpstoxRest(sync_transport=_exchange_ok_responder,
                            utc_now=lambda: datetime(2026, 8, 24, 10, 0))
    exc = await _expect_raises_async(runner, name + "-naive", UpstoxRestError,
                                     lambda: rest_naive.exchange_authorization_code(
                                         code=CODE, client_id=API_KEY,
                                         client_secret=SECRET, redirect_uri=REDIRECT),
                                     needle="naive")
    assert exc is not None
    runner.assert_eq(name + "-naive-retryable", exc.retryable, False)

    rest_bad = UpstoxRest(sync_transport=_exchange_ok_responder,
                          utc_now=lambda: "not-a-datetime")
    exc = await _expect_raises_async(runner, name + "-non-datetime", UpstoxRestError,
                                     lambda: rest_bad.exchange_authorization_code(
                                         code=CODE, client_id=API_KEY,
                                         client_secret=SECRET, redirect_uri=REDIRECT))


async def test_exchange_input_validation(runner: R) -> None:
    """UR6: empty inputs rejected BEFORE any transport call."""
    name = "UR6-input-validation"
    from brokers.upstox import UpstoxAuthError, UpstoxRest

    calls: list[int] = []

    def counting(*a):
        calls.append(1)
        raise AssertionError("transport must not be called")

    rest = UpstoxRest(sync_transport=counting, utc_now=lambda: FIXED_NOW)
    base = dict(code=CODE, client_id=API_KEY, client_secret=SECRET,
                redirect_uri=REDIRECT)
    for field in ("code", "client_id", "client_secret", "redirect_uri"):
        kwargs = dict(base)
        kwargs[field] = ""
        await _expect_raises_async(runner, name + f"-empty-{field}", UpstoxAuthError,
                       lambda kw=dict(kwargs): rest.exchange_authorization_code(**kw))
    runner.assert_eq(name + "-zero-transport-calls", len(calls), 0)


async def test_exchange_failures(runner: R) -> None:
    """UR7: 401/provider-envelope/non-JSON failures + secret absence."""
    name = "UR7-exchange-failures"
    from brokers.upstox import UpstoxAuthError, UpstoxRest, UpstoxRestError

    bad_code_env = {"status": "error", "error_codes": ["UDAPI100057"],
                    "errors": [{"errorCode": "UDAPI100057",
                                "message": "Invalid Auth code"}]}
    rest = UpstoxRest(sync_transport=lambda *a: _resp(401, body=_json_body(bad_code_env)))
    exc = await _expect_raises_async(runner, name + "-invalid-code", UpstoxAuthError,
                                     lambda: rest.exchange_authorization_code(
                                         code=CODE, client_id=API_KEY,
                                         client_secret=SECRET, redirect_uri=REDIRECT))
    assert exc is not None
    runner.assert_in(name + "-code-in-message", "UDAPI100057", str(exc))

    dumped = str(exc) + repr(exc)
    runner.assert_not_in(name + "-no-secret", SECRET, dumped)
    runner.assert_not_in(name + "-no-code-value", CODE, dumped)

    rest = UpstoxRest(sync_transport=lambda *a: _resp(400, body=_json_body(bad_code_env)))
    exc = await _expect_raises_async(runner, name + "-other-4xx", UpstoxRestError,
                                     lambda: rest.exchange_authorization_code(
                                         code=CODE, client_id=API_KEY,
                                         client_secret=SECRET, redirect_uri=REDIRECT))
    assert exc is not None
    runner.assert_eq(name + "-other-4xx-retryable", exc.retryable, False)

    rest = UpstoxRest(sync_transport=lambda *a: _resp(200, body=b"<html>gateway</html>"))
    exc = await _expect_raises_async(runner, name + "-non-json-success", UpstoxRestError,
                                     lambda: rest.exchange_authorization_code(
                                         code=CODE, client_id=API_KEY,
                                         client_secret=SECRET, redirect_uri=REDIRECT),
                                     needle="non-JSON")
    assert exc is not None
    runner.assert_not_in(name + "-no-html-leak", "gateway", str(exc))

    missing_token = UpstoxRest(sync_transport=lambda *a: _resp(200, body=_json_body({"user_name": "x"})))
    exc = await _expect_raises_async(runner, name + "-missing-token", UpstoxRestError,
                                     lambda: missing_token.exchange_authorization_code(
                                         code=CODE, client_id=API_KEY,
                                         client_secret=SECRET, redirect_uri=REDIRECT),
                                     needle="access_token")


# ---------------------------------------------------------------------------
# UR8 — HttpResponse repr + redirect policy
# ---------------------------------------------------------------------------


def test_response_repr_and_redirect_policy(runner: R) -> None:
    """UR8: HttpResponse repr redacted; redirect handler refuses to follow."""
    name = "UR8-response-redirect"
    from brokers.upstox.rest import HttpResponse, _NoRedirect

    resp = HttpResponse(status=200,
                        headers={"Authorization": "Bearer LEAKED"},
                        body=json.dumps({"access_token": TOKEN}).encode())
    r = repr(resp)
    runner.assert_not_in(name + "-repr-no-token", TOKEN, r)
    runner.assert_not_in(name + "-repr-no-header", "LEAKED", r)
    runner.assert_not_in(name + "-repr-no-body", "access_token", r)
    runner.assert_in(name + "-repr-status", "status=200", r)

    handler = _NoRedirect()
    request = urllib.request.Request("https://example.test/start")
    result = handler.redirect_request(request, None, 302, "Found",
                                      {}, WSS_URI.replace("wss", "https"))
    runner.assert_eq(name + "-redirect-refused", result, None)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_authorize_success(runner)
    await test_authorize_http_failures(runner)
    await test_exchange_success(runner)
    await test_exchange_clock_validation(runner)
    await test_exchange_input_validation(runner)
    await test_exchange_failures(runner)
    test_response_repr_and_redirect_policy(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
