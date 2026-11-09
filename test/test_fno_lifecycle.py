#!/usr/bin/env python3
"""Background component lifecycles (Phase F).

The Fyers index fallback and the previous-session OI recorder were
implemented but never driven — constructed and left idle. These gates
assert that each component:

  * has a real EXECUTION path (the poller actually calls the provider and
    publishes a quote; the recorder actually observes and persists);
  * publishes under the CORRECT storage key (the provider's own feed
    key, so a reader never has to guess);
  * has an observable, truthful status; and
  * stops cleanly.

The Fyers HSM index guard (dropping ``if``-prefixed topics) is NOT
touched: this is a separate, explicit REST path.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class _FakeRest:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def quotes(self, token, symbols):
        from datetime import datetime, timezone

        from market.models import Quote
        self.calls.append(list(symbols))
        return [Quote(instrument_token=s, exchange=s.split(":")[0],
                      tradingsymbol=s.split(":")[-1],
                      received_ts=datetime.now(timezone.utc), ltp=123.45)
                for s in symbols]


class _Auth:
    def get_access_token(self) -> str:
        return "TOKEN"


def test_fyers_index_poller_publishes_under_the_provider_key(monkeypatch) -> None:
    """The poller publishes index quotes under the provider's API symbol."""
    # The poller is market-hours aware: pin the session so the test does
    # not depend on the wall clock.
    import app.fyers_index_source as index_src
    from app.fyers_index_source import (
        FyersIndexSource,
        run_fyers_index_poll_loop,
    )
    from market.service import MarketService
    monkeypatch.setattr(index_src, "is_market_hours", lambda *a, **k: True)

    rest = _FakeRest()
    source = FyersIndexSource(
        rest=rest, auth=_Auth(),
        symbols=["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"])
    market_service = MarketService()

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(run_fyers_index_poll_loop(
            source, market_service, interval_seconds=0.05, stop_event=stop))
        await asyncio.sleep(0.3)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(run())
    # The provider was actually called.
    assert rest.calls, "the poller never called the provider"
    assert set(rest.calls[0]) == {"NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"}
    # The quotes are stored under the provider's OWN key (the Fyers feed
    # key), so a reader never has to guess which key holds the index.
    for symbol in ("NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"):
        quote = market_service.get_quote_now("NSE", symbol)
        assert quote is not None, symbol
        assert quote.ltp == 123.45


def test_fyers_index_poller_is_bounded_by_its_guards() -> None:
    """The poller never polls when it is not ready (honest unavailable)."""
    from app.fyers_index_source import FyersIndexSource

    rest = _FakeRest()

    class _NoAuth:
        def get_access_token(self):
            return ""

    source = FyersIndexSource(rest=rest, auth=_NoAuth(),
                              symbols=["NSE:NIFTY50-INDEX"])
    # Unauthenticated -> not ready, and a poll fetches NOTHING.
    assert source.ready() is False

    async def run():
        return await source.poll()

    assert asyncio.run(run()) == []
    assert rest.calls == [], "an unauthenticated poll must not call the provider"


def test_fyers_index_poller_respects_the_freshness_window(monkeypatch) -> None:
    """A symbol fetched inside the freshness window is not re-fetched."""
    # The poller is market-hours aware: pin the session so the test does
    # not depend on the wall clock.
    import app.fyers_index_source as index_src
    from app.fyers_index_source import FyersIndexSource
    monkeypatch.setattr(index_src, "is_market_hours", lambda *a, **k: True)

    rest = _FakeRest()
    source = FyersIndexSource(rest=rest, auth=_Auth(),
                              symbols=["NSE:NIFTY50-INDEX"])

    async def run():
        first = await source.poll()
        second = await source.poll()
        return first, second

    first, second = asyncio.run(run())
    assert len(first) == 1, first
    # The second poll is suppressed by the freshness guard.
    assert second == [], second
    assert len(rest.calls) == 1, rest.calls


def test_fyers_index_source_status_is_truthful() -> None:
    """The source reports a truthful, secret-free status."""
    from app.fyers_index_source import FyersIndexSource
    rest = _FakeRest()
    source = FyersIndexSource(rest=rest, auth=_Auth(),
                              symbols=["NSE:NIFTY50-INDEX"])
    state = source.snapshot_state()
    assert state["symbols"] == ["NSE:NIFTY50-INDEX"]
    assert state["last_fetch"] == {}
    assert "ready" in state
    # No secret is ever in the status.
    assert "TOKEN" not in repr(state)


def test_previous_oi_recorder_observes_and_persists() -> None:
    """The recorder observes futures OI and persists a session snapshot."""
    from app.previous_oi import PreviousOiRecorder
    from core.persistence.store import EventStore

    tmp = tempfile.TemporaryDirectory()
    try:
        store = EventStore(os.path.join(tmp.name, "t.db"))
        rec = PreviousOiRecorder(store, flush_interval=0.0)
        mapped = rec.refresh_identity([
            {"provider": "upstox", "provider_symbol": "NSE_FO|48712",
             "instrument_token": "NSE_FO|48712", "exchange": "NSE",
             "underlying": "RELIANCE", "expiry": "2026-10-27"},
            {"provider": "fyers", "provider_symbol": "NSE:RELIANCE26OCTFUT",
             "instrument_token": "NSE:RELIANCE26OCTFUT", "exchange": "NSE",
             "underlying": "RELIANCE", "expiry": "2026-10-27"},
        ])
        assert mapped == 2, mapped

        class _Quote:
            exchange = "NSE"
            instrument_token = "NSE_FO|48712"
            open_interest = 12345.0

        assert rec.observe_quote(_Quote()) is True
        # An untracked instrument is never recorded.
        class _Other:
            exchange = "NSE"
            instrument_token = "NSE_EQ|1"
            open_interest = 1.0
        assert rec.observe_quote(_Other()) is False

        assert rec.flush() == 1
        # Today's row is the CURRENT OI, never the baseline.
        assert store.get_previous_oi(
            exchange="NSE", underlying="RELIANCE", expiry="2026-10-27",
            instrument_type="FUTURE") is None
        # A PRIOR session's row IS the baseline.
        import datetime as _dt
        prior = (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
        store.upsert_previous_oi_snapshots([{
            "exchange": "NSE", "underlying": "RELIANCE",
            "expiry": "2026-10-27", "instrument_type": "FUTURE",
            "session_date": prior, "oi": 11000.0,
            "provider": "upstox", "instrument_token": "NSE_FO|48712"}])
        assert store.get_previous_oi(
            exchange="NSE", underlying="RELIANCE", expiry="2026-10-27",
            instrument_type="FUTURE") == 11000.0
    finally:
        tmp.cleanup()


def test_previous_oi_recorder_run_loop_starts_and_stops() -> None:
    """The recorder's run loop starts and stops cleanly."""
    from app.previous_oi import PreviousOiRecorder
    from core.persistence.store import EventStore

    tmp = tempfile.TemporaryDirectory()
    try:
        store = EventStore(os.path.join(tmp.name, "t.db"))
        rec = PreviousOiRecorder(store, flush_interval=0.01)

        async def run():
            stop = asyncio.Event()
            task = asyncio.create_task(rec.run(stop))
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        asyncio.run(run())
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
