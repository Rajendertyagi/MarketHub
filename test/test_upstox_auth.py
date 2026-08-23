#!/usr/bin/env python3
"""Unit tests for the Upstox auth foundation — pure, HTTP-free.

Phase D2.1 coverage:
  * UA1   credential validation (empty/whitespace/non-string rejected,
          whitespace stripped)
  * UA2   secret redaction (token never in repr/status)
  * UA3   token-expiry rule: first 03:30 IST boundary STRICTLY AFTER the
          acquisition time; naive datetimes rejected; non-IST input
          converted; result canonicalized to UTC
  * UA4   unknown-expiry status (expiry_known=False, expired=None)
  * UA5   known-expiry status (boundary comparisons; no clock re-derivation)
  * UA6   OAuth authorization-URL construction and validation
  * UA7   OAuth repr redaction
  * UA8   error hierarchy

Each test is independently runnable via ``python test/test_upstox_auth.py``.
Pure unit file: no server, no SQLite, no config.json, no network.
"""

from __future__ import annotations

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

from datetime import datetime, timedelta, timezone  # noqa: E402

from helpers.runner import R  # noqa: E402

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))

TOKEN = "SYNTHETIC_ACCESS_TOKEN_ABC123"
API_KEY = "SYNTHETIC_API_KEY_XYZ789"
REDIRECT = "https://example.test/callback"


def _ist(year, month, day, hour=0, minute=0, second=0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=IST)


def _expect_raises(runner: R, label: str, exc_type: type, fn, needle: str | None = None) -> None:
    try:
        fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(label, needle in str(exc),
                               f"message should contain {needle!r}: {exc}")
        else:
            runner.ok(label)
        return
    except Exception as exc:  # narrow: report unexpected exception types
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")


# ---------------------------------------------------------------------------
# UA1/UA2 — credentials + redaction
# ---------------------------------------------------------------------------


def test_credential_validation(runner: R) -> None:
    """UA1: token validation and whitespace stripping."""
    name = "UA1-credentials"
    from brokers.upstox import UpstoxCredentials

    c = UpstoxCredentials(access_token=TOKEN)
    runner.assert_eq(name + "-normal", c.access_token, TOKEN)

    padded = UpstoxCredentials(access_token=f"  {TOKEN}  ")
    runner.assert_eq(name + "-stripped", padded.access_token, TOKEN)

    _expect_raises(runner, name + "-empty", Exception,
                   lambda: UpstoxCredentials(access_token=""), needle="non-empty")
    _expect_raises(runner, name + "-whitespace", Exception,
                   lambda: UpstoxCredentials(access_token="   "), needle="non-empty")
    _expect_raises(runner, name + "-non-string", Exception,
                   lambda: UpstoxCredentials(access_token=12345))
    _expect_raises(runner, name + "-naive-expiry", Exception,
                   lambda: UpstoxCredentials(access_token=TOKEN,
                                             expires_at=datetime(2026, 8, 24, 3, 30)),
                   needle="timezone-aware")


def test_secret_redaction(runner: R) -> None:
    """UA2: synthetic secrets never appear in repr or status output."""
    name = "UA2-redaction"
    from brokers.upstox import UpstoxCredentials, UpstoxOAuth

    c = UpstoxCredentials(access_token=TOKEN)
    r = repr(c)
    runner.assert_true(name + "-repr-type", r.startswith("UpstoxCredentials("),
                       f"unexpected repr: {r!r}")
    runner.assert_not_in(name + "-repr-no-token", TOKEN, r)
    runner.assert_in(name + "-repr-redacted", "<redacted>", r)

    dumped = json.dumps(c.status())
    runner.assert_not_in(name + "-status-no-token", TOKEN, dumped)

    oauth = UpstoxOAuth(api_key=API_KEY, redirect_uri=REDIRECT)
    ro = repr(oauth)
    runner.assert_not_in(name + "-oauth-repr-no-key", API_KEY, ro)
    runner.assert_not_in(name + "-oauth-repr-no-secret-field", "api_secret", ro)


# ---------------------------------------------------------------------------
# UA3 — token expiry rule
# ---------------------------------------------------------------------------


def test_token_expiry_rule(runner: R) -> None:
    """UA3: first 03:30 IST strictly AFTER acquisition; UTC canonical."""
    name = "UA3-expiry"
    from brokers.upstox import upstox_token_expiry

    # Locked boundary examples (expected values expressed in IST).
    cases = [
        (_ist(2026, 8, 23, 2, 30, 0), _ist(2026, 8, 23, 3, 30)),      # before -> same day
        (_ist(2026, 8, 23, 3, 29, 59), _ist(2026, 8, 23, 3, 30)),     # just before -> same day
        (_ist(2026, 8, 23, 3, 30, 0), _ist(2026, 8, 24, 3, 30)),      # exactly at -> next day
        (_ist(2026, 8, 23, 20, 0, 0), _ist(2026, 8, 24, 3, 30)),      # evening -> next day
    ]
    for i, (issued, expected) in enumerate(cases):
        got = upstox_token_expiry(issued)
        runner.assert_eq(name + f"-case{i}", got, expected)

    # Non-IST aware input converts correctly: 21:00 UTC Aug 22 == 02:30 IST Aug 23.
    utc_input = datetime(2026, 8, 22, 21, 0, 0, tzinfo=UTC)
    runner.assert_eq(name + "-utc-input",
                     upstox_token_expiry(utc_input), _ist(2026, 8, 23, 3, 30))

    # Result is timezone-aware and canonical UTC.
    got = upstox_token_expiry(_ist(2026, 8, 23, 20, 0))
    runner.assert_true(name + "-aware", got.tzinfo is not None and got.utcoffset() is not None)
    runner.assert_eq(name + "-canonical-utc", got.utcoffset(), timedelta(0))

    # Naive datetimes are rejected explicitly.
    _expect_raises(runner, name + "-naive-rejected", Exception,
                   lambda: upstox_token_expiry(datetime(2026, 8, 23, 20, 0)),
                   needle="timezone-aware")
    _expect_raises(runner, name + "-non-datetime-rejected", Exception,
                   lambda: upstox_token_expiry("2026-08-23T20:00:00+05:30"))


# ---------------------------------------------------------------------------
# UA4/UA5 — credential status
# ---------------------------------------------------------------------------


def test_status_unknown_expiry(runner: R) -> None:
    """UA4: externally supplied token without known acquisition time."""
    name = "UA4-status-unknown"
    from brokers.upstox import UpstoxCredentials

    c = UpstoxCredentials(access_token=TOKEN)
    status = c.status()
    runner.assert_eq(name + "-exact", status, {
        "auth_mode": "external_token",
        "token_present": True,
        "expiry_known": False,
        "expires_at": None,
        "expired": None,
    })
    # Unknown expiry must NEVER claim validity, even far in the future.
    later = status.copy()
    assert later is not None
    runner.assert_eq(name + "-still-unknown",
                     c.status(now=datetime.now(timezone.utc))["expired"], None)


def test_status_known_expiry(runner: R) -> None:
    """UA5: known expiry comparisons; expiry never recomputed from now."""
    name = "UA5-status-known"
    from brokers.upstox import UpstoxCredentials

    expires = _ist(2026, 8, 24, 3, 30)
    c = UpstoxCredentials(access_token=TOKEN, expires_at=expires)

    before = c.status(now=_ist(2026, 8, 24, 3, 29, 59))
    runner.assert_eq(name + "-before", before["expired"], False)

    exact = c.status(now=expires)
    runner.assert_eq(name + "-at-boundary", exact["expired"], True)

    after = c.status(now=_ist(2026, 8, 25, 9, 0))
    runner.assert_eq(name + "-after", after["expired"], True)

    # Expiry is stored, not re-derived from `now`: a check one full day
    # later must still report the SAME original expiry instant.
    much_later = c.status(now=_ist(2026, 8, 26, 12, 0))
    runner.assert_eq(name + "-no-clock-derivation",
                     much_later["expires_at"],
                     expires.astimezone(UTC).isoformat())
    runner.assert_eq(name + "-expiry-known", much_later["expiry_known"], True)

    # ISO-8601 UTC serialization.
    runner.assert_eq(name + "-iso-utc", exact["expires_at"],
                     "2026-08-23T22:00:00+00:00")

    # Naive `now` rejected.
    _expect_raises(runner, name + "-naive-now", Exception,
                   lambda: c.status(now=datetime(2026, 8, 24, 3, 0)),
                   needle="timezone-aware")

    # Token never appears anywhere in the returned status.
    dumped = json.dumps(c.status(now=_ist(2026, 8, 23, 12, 0)))
    runner.assert_not_in(name + "-no-token", TOKEN, dumped)


# ---------------------------------------------------------------------------
# UA6/UA7 — OAuth authorization URL
# ---------------------------------------------------------------------------


def test_oauth_url(runner: R) -> None:
    """UA6: endpoint, params, encoding, optional state/scope, validation."""
    name = "UA6-oauth-url"
    from brokers.upstox import UpstoxOAuth

    oauth = UpstoxOAuth(api_key=API_KEY, redirect_uri=REDIRECT)
    url = oauth.authorization_url(state="abc123")
    parsed = urllib.parse.urlsplit(url)
    runner.assert_eq(name + "-endpoint",
                     f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                     "https://api.upstox.com/v2/login/authorization/dialog")
    query = urllib.parse.parse_qs(parsed.query)
    runner.assert_eq(name + "-client-id", query.get("client_id"), [API_KEY])
    runner.assert_eq(name + "-redirect-uri", query.get("redirect_uri"), [REDIRECT])
    runner.assert_eq(name + "-response-type", query.get("response_type"), ["code"])
    runner.assert_eq(name + "-state", query.get("state"), ["abc123"])
    runner.assert_false(name + "-scope-absent", "scope" in query)

    # Special characters encoded correctly (no manual concatenation).
    url2 = oauth.authorization_url(state="a b&c=d")
    q2 = urllib.parse.parse_qs(urllib.parse.urlsplit(url2).query)
    runner.assert_eq(name + "-state-encoded", q2.get("state"), ["a b&c=d"])

    # Scope included when supplied.
    url3 = oauth.authorization_url(scope="read")
    q3 = urllib.parse.parse_qs(urllib.parse.urlsplit(url3).query)
    runner.assert_eq(name + "-scope", q3.get("scope"), ["read"])

    # Validation.
    from brokers.upstox.errors import UpstoxAuthError
    _expect_raises(runner, name + "-empty-api-key", UpstoxAuthError,
                   lambda: UpstoxOAuth(api_key="", redirect_uri=REDIRECT))
    _expect_raises(runner, name + "-blank-api-key", UpstoxAuthError,
                   lambda: UpstoxOAuth(api_key="   ", redirect_uri=REDIRECT))
    _expect_raises(runner, name + "-bad-scheme", UpstoxAuthError,
                   lambda: UpstoxOAuth(api_key=API_KEY, redirect_uri="ftp://x/y"))
    _expect_raises(runner, name + "-no-netloc", UpstoxAuthError,
                   lambda: UpstoxOAuth(api_key=API_KEY, redirect_uri="just-a-path"))
    _expect_raises(runner, name + "-blank-state", UpstoxAuthError,
                   lambda: oauth.authorization_url(state="  "))
    _expect_raises(runner, name + "-blank-scope", UpstoxAuthError,
                   lambda: oauth.authorization_url(scope="  "))


def test_oauth_no_secret_and_safe_repr(runner: R) -> None:
    """UA7: api_secret has no home here; repr is redacted."""
    name = "UA7-oauth-no-secret"
    import inspect

    from brokers.upstox import UpstoxOAuth

    # api_secret must not be a constructor parameter or stored attribute;
    # the class docstring may legitimately *mention* its exclusion, so the
    # scan targets the constructor source only.
    init_source = inspect.getsource(UpstoxOAuth.__init__)
    runner.assert_not_in(name + "-no-api-secret-param", "api_secret", init_source)
    oauth = UpstoxOAuth(api_key=API_KEY, redirect_uri="https://user:pass@example.test/cb")
    runner.assert_false(name + "-no-attribute",
                        any("api_secret" in attr for attr in dir(oauth)))
    source = inspect.getsource(UpstoxOAuth)
    runner.assert_not_in(name + "-no-urllib-request", "urllib.request", source)

    oauth = UpstoxOAuth(api_key=API_KEY, redirect_uri="https://user:pass@example.test/cb")
    ro = repr(oauth)
    runner.assert_not_in(name + "-repr-no-key", API_KEY, ro)
    # Safe URL representation drops userinfo/query material.
    runner.assert_not_in(name + "-repr-no-userinfo", "user:pass", ro)
    runner.assert_in(name + "-repr-safe-url", "https://example.test/cb", ro)


# ---------------------------------------------------------------------------
# UA8 — error hierarchy
# ---------------------------------------------------------------------------


def test_error_hierarchy(runner: R) -> None:
    """UA8: small hierarchy with safe-message discipline."""
    name = "UA8-errors"
    from brokers.upstox import UpstoxAuthError, UpstoxError

    exc = UpstoxAuthError("invalid credentials supplied")
    runner.assert_true(name + "-subclass", isinstance(exc, UpstoxError))
    runner.assert_eq(name + "-message", str(exc), "invalid credentials supplied")

    # Exceptions carry only their safe message — no attribute storage that
    # could smuggle secrets through tracebacks.
    runner.assert_false(name + "-no-extra-state",
                        any(not k.startswith("__") for k in vars(exc)),
                        "auth errors must not carry extra attributes")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> bool:
    runner = R()

    test_credential_validation(runner)
    test_secret_redaction(runner)
    test_token_expiry_rule(runner)
    test_status_unknown_expiry(runner)
    test_status_known_expiry(runner)
    test_oauth_url(runner)
    test_oauth_no_secret_and_safe_repr(runner)
    test_error_hierarchy(runner)

    return runner.summary()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
