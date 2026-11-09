#!/usr/bin/env python3
"""Fyers-only index REST fallback tests (S10).

The guarded Fyers REST index source is a bounded, market-hours poller:
  * rate limited (shared bucket, <= 5 rps)
  * interval bounded (>= 60 s)
  * market-hours gated (no polling outside the session)
  * freshness bounded (5 min)
  * duplicate-request suppression (identical symbol+expiry within the
    window is not re-fetched)
  * provider selection: Fyers-only (Upstox is the primary index source)

The existing HSM index guard (drop ``if``-prefixed topics) is UNTOUCHED.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_limits_are_bounded() -> None:
    from app.fyers_index_source import (
        FRESHNESS_SECONDS,
        MAX_RPS,
        MIN_INTERVAL_SECONDS,
    )
    assert MAX_RPS == 5
    assert MIN_INTERVAL_SECONDS == 60
    assert FRESHNESS_SECONDS == 300


def test_market_hours_gate() -> None:
    from datetime import datetime, timezone

    from app.fyers_index_source import is_market_hours
    # 10:00 IST == 04:30 UTC (market open).
    assert is_market_hours(datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc))
    # 20:00 IST == 14:30 UTC (after close).
    assert not is_market_hours(
        datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc))
    # 03:00 UTC == 08:30 IST (before open).
    assert not is_market_hours(
        datetime(2026, 10, 20, 3, 0, tzinfo=timezone.utc))


def test_rate_bucket_limits_requests() -> None:
    from app.fyers_index_source import RateBucket
    b = RateBucket(max_rps=5)
    assert all(b.allow() for _ in range(5))
    assert not b.allow()          # 6th within the same second is blocked


def test_duplicate_requests_suppressed() -> None:
    from app.fyers_index_source import FyersIndexSource

    calls: list[str] = []

    class _Rest:
        async def quotes(self, access_token, symbols):
            calls.append(symbols[0])
            return []

    class _Auth:
        def get_access_token(self):
            return "TOK"

    src = FyersIndexSource(
        rest=_Rest(), auth=_Auth(),
        symbols=["NSE:NIFTY50-INDEX"],
        now_fn=lambda: _fixed_now())
    assert src.should_fetch("NSE:NIFTY50-INDEX") is True
    src.mark_fetched("NSE:NIFTY50-INDEX")
    assert src.should_fetch("NSE:NIFTY50-INDEX") is False


def test_freshness_expiry_allows_refetch() -> None:
    from app.fyers_index_source import FyersIndexSource

    class _Rest:
        async def quotes(self, access_token, symbols):
            return []

    class _Auth:
        def get_access_token(self):
            return "TOK"

    src = FyersIndexSource(
        rest=_Rest(), auth=_Auth(), symbols=["NSE:NIFTY50-INDEX"],
        now_fn=lambda: _fixed_now())
    src.mark_fetched("NSE:NIFTY50-INDEX")
    src._now = lambda: _fixed_now(offset_seconds=301)
    assert src.should_fetch("NSE:NIFTY50-INDEX") is True


def test_outside_market_hours_never_fetches() -> None:
    from datetime import datetime, timezone

    from app.fyers_index_source import FyersIndexSource

    class _Rest:
        async def quotes(self, access_token, symbols):
            return []

    class _Auth:
        def get_access_token(self):
            return "TOK"

    src = FyersIndexSource(
        rest=_Rest(), auth=_Auth(), symbols=["NSE:NIFTY50-INDEX"],
        now_fn=lambda: datetime(2026, 10, 20, 14, 30, tzinfo=timezone.utc))
    assert src.should_fetch("NSE:NIFTY50-INDEX") is False


def test_unauthenticated_never_fetches() -> None:
    from app.fyers_index_source import FyersIndexSource

    class _Rest:
        async def quotes(self, access_token, symbols):
            return []

    class _Auth:
        def get_access_token(self):
            return None

    src = FyersIndexSource(
        rest=_Rest(), auth=_Auth(), symbols=["NSE:NIFTY50-INDEX"],
        now_fn=lambda: _fixed_now())
    assert src.should_fetch("NSE:NIFTY50-INDEX") is False


def test_hsm_index_guard_untouched() -> None:
    from pathlib import Path
    src = Path("brokers/fyers/feed.py").read_text(encoding="utf-8")
    assert 'snap["topic"].startswith("if")' in src


def _fixed_now(offset_seconds: int = 0):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=offset_seconds)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
