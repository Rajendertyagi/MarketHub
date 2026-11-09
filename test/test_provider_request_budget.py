#!/usr/bin/env python3
"""Shared provider request budgets - enforcement evidence.

The budgets are per PROVIDER (and per documented API family) and are
shared by EVERY caller: quotes, depth, option chain, history, the
symbol-token resolution and the index fallback all consume the same
accounting state for that provider, so no caller can spend the window
alone.

Documented limits implemented (see the adapter declarations):
  * FYERS Data APIs (Standard): 5 requests/second, 50/minute, 5,000/day;
    exceeding the per-minute limit more than 3 times in a day blocks the
    account for the rest of the day.
  * Upstox Standard APIs (per API, per user): 50 requests/second,
    500/minute, 2,000 per 30 minutes; up to 500 instrument keys per
    quote request.
  * Batch sizes: FYERS quotes 50 symbols per request (Standard),
    option chain 50 strikes per side of ATM.

No live broker is contacted: every request goes through an injected
transport that only records the call.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

from providers.rate import (
    BudgetExhausted,
    RequestBudgetManager,
)


class _Recorder:
    """A transport double that records the calls and returns a 200."""

    def __init__(self, status: int = 200):
        self.calls: list[tuple] = []
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        status = self.status
        body_bytes = b'{"status": "success", "data": {}}'
        return _Response(status, {}, body_bytes)


class _Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body


class _Clock:
    """A controlled clock: the windows never roll over on their own."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


_CLOCK = _Clock()


def _budget(provider: str, **limits) -> RequestBudgetManager:
    """A budget manager with explicit limits (no clock surprises).

    The limits are applied to EVERY quota bucket of that provider, so a
    test can speak about "the provider's budget" without knowing which
    documented bucket it exercises.
    """
    manager = RequestBudgetManager(clock=_CLOCK)
    original = manager._budget

    def _patched(pid, bucket="default"):
        budget = original(pid, bucket)
        if pid == provider:
            budget.per_second.limit = limits.get("per_second")
            budget.per_minute.limit = limits.get("per_minute")
            budget.per_day.limit = limits.get("per_day")
            budget.per_day.window_seconds = limits.get(
                "day_window", 24 * 3600.0)
        return budget

    manager._budget = _patched  # type: ignore[method-assign]
    return manager


# ---------------------------------------------------------------------------
# 1) all callers of a provider share ONE accounting state
# ---------------------------------------------------------------------------

def test_all_callers_share_one_provider_budget() -> None:
    """Quotes, depth, option chain and the symbol-token call share it."""
    from brokers.fyers.hsm_symbols import resolve_wire_symbols
    from brokers.fyers.rest import FyersRateLimitError, FyersRest
    from providers import rate

    manager = _budget("fyers", per_second=2, per_minute=10, per_day=100)
    rate.set_budget_manager(manager)
    try:
        recorder = _Recorder()
        rest = FyersRest(sync_transport=recorder)

        async def scenario():
            await rest.quotes("app:token", ["NSE:SBIN-EQ"])
            await rest.depth("app:token", "NSE:SBIN-EQ")
            await rest.option_chain("app:token", "NSE:SBIN-EQ",
                                    strikecount=10)
            resolve_wire_symbols(["NSE:TCS-EQ"], "app:token",
                                 fetch=lambda token, syms: {
                                     "s": "ok",
                                     "validSymbol": {"NSE:TCS-EQ":
                                                     "1010000000123"},
                                     "invalidSymbol": []})
        # The client translates the exhausted budget into the provider's
        # rate-limit error (the caller's contract is unchanged).
        with pytest.raises(FyersRateLimitError):
            asyncio.run(scenario())
        # The limit is shared: the caller that exceeded it is refused, and
        # the ones before it were served.
        assert len(recorder.calls) == 2
        described = manager.describe()["fyers:data"]
        assert described["per_second"]["limit"] == 2
        assert described["per_second"]["used"] == 2
    finally:
        rate.set_budget_manager(None)


def test_upstox_and_fyers_have_separate_budgets() -> None:
    """A provider's budget is never consumed by the other provider."""
    manager = _budget("fyers", per_second=1, per_minute=1, per_day=1)
    from providers import rate

    rate.set_budget_manager(manager)
    try:
        manager.acquire("fyers")
        with pytest.raises(BudgetExhausted):
            manager.acquire("fyers")
        # Upstox has its own budget.
        manager.acquire("upstox")
    finally:
        rate.set_budget_manager(None)


# ---------------------------------------------------------------------------
# 2) the documented batch sizes are obeyed
# ---------------------------------------------------------------------------

def test_fyers_quotes_are_split_into_documented_batches() -> None:
    """The Quotes API accepts 50 symbols per request (Standard)."""
    from brokers.fyers.rest import FyersRest
    from providers import rate

    manager = _budget("fyers", per_second=50, per_minute=100, per_day=1000)
    rate.set_budget_manager(manager)
    try:
        recorder = _Recorder()
        rest = FyersRest(sync_transport=recorder)
        symbols = [f"NSE:SYM{i}-EQ" for i in range(120)]

        async def scenario():
            await rest.quotes("app:token", symbols)

        asyncio.run(scenario())
        assert len(recorder.calls) == 3, "120 symbols -> 3 batches of 50"
        for _method, url, _headers, _body, _timeout in recorder.calls:
            sent = url.split("symbols=", 1)[1]
            assert len(sent.split(",")) <= 50
    finally:
        rate.set_budget_manager(None)


def test_upstox_quote_batch_limit_is_500() -> None:
    """The documented Upstox quote batch size is 500 instrument keys."""
    from providers.capacity import max_keys_per_request

    assert max_keys_per_request("upstox", "market_quote") == 500
    assert max_keys_per_request("fyers", "data") == 50


# ---------------------------------------------------------------------------
# 3) the FYERS windows are enforced independently
# ---------------------------------------------------------------------------

def test_fyers_per_second_minute_and_day_windows_are_independent() -> None:
    manager = _budget("fyers", per_second=2, per_minute=3, per_day=4)
    # per second
    manager.acquire("fyers", "data")
    manager.acquire("fyers", "data")
    with pytest.raises(BudgetExhausted):
        manager.acquire("fyers", "data")
    # a new second frees the per-second window (but not the others)
    _CLOCK.advance(1.5)
    manager.acquire("fyers", "data")
    # per minute
    with pytest.raises(BudgetExhausted):
        manager.acquire("fyers", "data")
    # a new minute frees the per-minute window (but not the day)
    _CLOCK.advance(61.0)
    manager.acquire("fyers", "data")
    with pytest.raises(BudgetExhausted):
        manager.acquire("fyers", "data")


def test_the_daily_window_is_enforced() -> None:
    manager = _budget("fyers", per_second=100, per_minute=100, per_day=2)
    manager.acquire("fyers", "data")
    manager.acquire("fyers", "data")
    with pytest.raises(BudgetExhausted):
        manager.acquire("fyers", "data")


def test_upstox_uses_the_thirty_minute_window() -> None:
    """Upstox documents 2,000 requests per 30 minutes (not per day)."""
    from providers.capacity import rest_limits

    limits = rest_limits("upstox", "market_quote")
    assert limits["requests_per_second"] == 50
    assert limits["requests_per_minute"] == 500
    assert limits["requests_per_30_minutes"] == 2000

    manager = _budget("upstox", per_second=100, per_minute=1000,
                      per_day=2000, day_window=30 * 60)
    manager.acquire("upstox", "market_quote")
    described = manager.describe()["upstox:market_quote"]
    assert described["per_day"]["limit"] == 2000


# ---------------------------------------------------------------------------
# 4) a 429 activates the backoff / block behaviour
# ---------------------------------------------------------------------------

def test_a_rate_limit_response_stops_further_calls() -> None:
    """After a 429 the client refuses to call the provider again."""
    from brokers.upstox.errors import UpstoxRateLimitError
    from brokers.upstox.rest import UpstoxRest
    from providers import rate

    manager = _budget("upstox", per_second=50, per_minute=500, per_day=2000)
    rate.set_budget_manager(manager)
    try:
        recorder = _Recorder(status=429)
        rest = UpstoxRest(sync_transport=recorder)

        async def scenario():
            await rest.authenticated_request(
                method="GET", url="https://api.upstox.com/v3/market-quote/quotes",
                access_token="token")

        with pytest.raises(UpstoxRateLimitError):
            asyncio.run(scenario())
        assert len(recorder.calls) == 1
        # The provider is now blocked: the next call never reaches it.
        assert manager.describe()["upstox:market_quote"]["blocked"] is True
        with pytest.raises(UpstoxRateLimitError):
            asyncio.run(scenario())
        assert len(recorder.calls) == 1, (
            "a blocked provider must not be called again")
    finally:
        rate.set_budget_manager(None)


def test_repeated_fyers_breaches_block_the_account_for_the_day() -> None:
    """FYERS blocks the account after 3 per-minute breaches in a day."""
    from brokers.fyers.rest import FyersRateLimitError, FyersRest
    from providers import rate

    manager = _budget("fyers", per_second=100, per_minute=1000, per_day=5000)
    rate.set_budget_manager(manager)
    try:
        recorder = _Recorder(status=429)
        rest = FyersRest(sync_transport=recorder)

        async def scenario():
            await rest.quotes("app:token", ["NSE:SBIN-EQ"])

        with pytest.raises(FyersRateLimitError):
            asyncio.run(scenario())
        described = manager.describe()["fyers:data"]
        assert described["breaches"] == 1
        assert described["blocked"] is True
        # The client STOPS after the first breach: the account can never
        # accumulate the three per-minute breaches that would block it for
        # the rest of the day.
        for _ in range(3):
            with pytest.raises(FyersRateLimitError):
                asyncio.run(scenario())
        assert len(recorder.calls) == 1
        assert manager.describe()["fyers:data"]["breaches"] == 1
    finally:
        rate.set_budget_manager(None)


def test_the_daily_block_is_the_managers_own_rule() -> None:
    """Three per-minute breaches in a day block the account for the day."""
    manager = _budget("fyers", per_second=100, per_minute=1000, per_day=5000)
    for _ in range(3):
        manager.report_block("fyers", "data")
    described = manager.describe()["fyers:data"]
    assert described["breaches"] == 3
    assert described["blocked"] is True


# ---------------------------------------------------------------------------
# 5) counters and block status are observable without a broker
# ---------------------------------------------------------------------------

def test_counters_and_block_status_are_observable() -> None:
    manager = RequestBudgetManager(clock=_CLOCK)
    manager.acquire("upstox", "market_quote")
    manager.acquire("upstox", "market_quote")
    described = manager.describe()["upstox:market_quote"]
    assert described["per_second"]["used"] == 2
    assert described["blocked"] is False
    manager.report_block("upstox", "market_quote", seconds=30)
    assert manager.describe()["upstox:market_quote"]["blocked"] is True
    manager.reset()
    assert not any(key.startswith("upstox")
                   for key in manager.describe())


# ---------------------------------------------------------------------------
# 6) no speculative polling is introduced by the budget
# ---------------------------------------------------------------------------

def test_the_budget_itself_never_makes_a_request() -> None:
    """The manager only accounts; it never calls a provider."""
    manager = RequestBudgetManager()
    assert manager.describe() == {}
    manager.acquire("fyers", "data")
    assert set(manager.describe()) == {"fyers:data"}


# ---------------------------------------------------------------------------
# 7) the QUOTA SCOPE: which callers share a bucket, which do not
# ---------------------------------------------------------------------------

def test_fyers_data_endpoints_share_one_documented_pool() -> None:
    """FYERS documents ONE shared Data-API pool per user.

    Quotes, depth, history, option chain, market status and the
    symbol-token resolution all draw from the same bucket.
    """
    from brokers.fyers.hsm_symbols import resolve_wire_symbols
    from brokers.fyers.rest import FyersRateLimitError, FyersRest
    from providers import rate

    manager = _budget("fyers", per_second=2, per_minute=10, per_day=100)
    rate.set_budget_manager(manager)
    try:
        recorder = _Recorder()
        rest = FyersRest(sync_transport=recorder)

        async def scenario():
            await rest.quotes("app:token", ["NSE:SBIN-EQ"])
            await rest.depth("app:token", "NSE:SBIN-EQ")
            resolve_wire_symbols(["NSE:TCS-EQ"], "app:token",
                                 fetch=lambda token, syms: {
                                     "s": "ok",
                                     "validSymbol": {"NSE:TCS-EQ":
                                                     "1010000000123"},
                                     "invalidSymbol": []})

        with pytest.raises(FyersRateLimitError):
            asyncio.run(scenario())
        described = manager.describe()["fyers:data"]
        assert described["per_second"]["limit"] == 2
        assert described["per_second"]["used"] == 2
        # The symbol-token call is the third one and is refused with the
        # SAME bucket (it did not get its own allowance).
        assert len(recorder.calls) == 2
    finally:
        rate.set_budget_manager(None)


def test_upstox_api_families_are_not_conflated() -> None:
    """Upstox documents its limits "per API, per user".

    The market-quote, historical, option-chain and market-info families
    each have their OWN budget; exhausting one must not throttle another.
    """
    from providers import rate

    manager = _budget("upstox", per_second=1, per_minute=10, per_day=100)
    rate.set_budget_manager(manager)
    try:
        manager.acquire("upstox", "market_quote")
        with pytest.raises(BudgetExhausted):
            manager.acquire("upstox", "market_quote")
        # A different API family is unaffected.
        manager.acquire("upstox", "historical")
        manager.acquire("upstox", "option_chain")
        manager.acquire("upstox", "market_info")
        # ... and the auth bucket (a stricter documented row) is separate.
        manager.acquire("upstox", "auth")
        described = manager.describe()
        assert "upstox:market_quote" in described
        assert "upstox:auth" in described
        assert described["upstox:auth"]["per_second"]["limit"] == 1
    finally:
        rate.set_budget_manager(None)


def test_the_fyers_transaction_pool_is_separate_from_the_data_pool() -> None:
    """The FYERS Transaction / User-Info pool is a different quota."""
    from providers import rate

    manager = _budget("fyers", per_second=1, per_minute=10, per_day=100)
    rate.set_budget_manager(manager)
    try:
        manager.acquire("fyers", "data")
        with pytest.raises(BudgetExhausted):
            manager.acquire("fyers", "data")
        # The transaction pool is untouched by the data pool.
        manager.acquire("fyers", "transaction")
    finally:
        rate.set_budget_manager(None)


def test_every_upstox_call_path_maps_to_its_documented_bucket() -> None:
    """The real endpoints are assigned to the documented quota scope."""
    from brokers.upstox.rest import UpstoxRest

    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v3/market-quote/quotes") == "market_quote"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v3/market-quote/ltp") == "market_quote"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v3/historical-candle/...") == "historical"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/option/chain") == "option_chain"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/market/oi") == "market_info"
    # The interactive login family shares the ONE strict auth budget; the
    # OAuth code exchange is a different documented API family.
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/totp-login/authorization/token") == "auth"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/login/validate") == "auth"
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v2/login/authorization/token") == "auth_code"
    # The feed authorization is its own scope (standard row).
    assert UpstoxRest._bucket_for(
        "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    ) == "feed_authorize"


def test_the_upstox_auth_bucket_uses_the_stricter_documented_row() -> None:
    """TOTP/token: 1/second, 10/minute, 60 per 30 minutes."""
    from providers.capacity import rest_limits

    auth = rest_limits("upstox", "auth")
    assert auth["requests_per_second"] == 1
    assert auth["requests_per_minute"] == 10
    assert auth["requests_per_30_minutes"] == 60
    quote = rest_limits("upstox", "market_quote")
    assert quote["requests_per_second"] == 50
    assert quote["requests_per_30_minutes"] == 2000


def test_a_rate_limit_response_stops_the_bucket_without_retry_bursts() -> None:
    """A 429 stops the bucket; repeated calls never reach the broker."""
    from brokers.upstox.errors import UpstoxRateLimitError
    from brokers.upstox.rest import UpstoxRest
    from providers import rate

    manager = _budget("upstox", per_second=50, per_minute=500, per_day=2000)
    rate.set_budget_manager(manager)
    try:
        recorder = _Recorder(status=429)
        rest = UpstoxRest(sync_transport=recorder)

        async def scenario():
            await rest.authenticated_request(
                method="GET",
                url="https://api.upstox.com/v3/market-quote/quotes",
                access_token="token")

        for _ in range(5):
            with pytest.raises(UpstoxRateLimitError):
                asyncio.run(scenario())
        assert len(recorder.calls) == 1, (
            "a blocked bucket must not produce a retry burst")
    finally:
        rate.set_budget_manager(None)


def test_the_documented_buckets_are_declared_by_the_adapters() -> None:
    """The bucket map is the adapter's declaration, not a hard-coded list."""
    from providers.capacity import rest_limits

    fyers = rest_limits("fyers")
    assert set(fyers) == {"data", "transaction"}
    assert fyers["data"]["requests_per_second"] == 5
    assert fyers["data"]["requests_per_minute"] == 50
    assert fyers["data"]["requests_per_day"] == 5000
    assert fyers["data"]["max_symbols_per_request"] == 50
    assert fyers["data"]["per_minute_breaches_before_block"] == 3

    upstox = rest_limits("upstox")
    assert set(upstox) == {"market_quote", "historical", "option_chain",
                           "market_info", "auth", "auth_code",
                           "feed_authorize"}

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
