#!/usr/bin/env python3
"""Truthful liveness and stale-data safety (deterministic, no broker).

Proves, with fake feeds and an isolated store:

  * a feed that stays CONNECTED but stops delivering frames is NOT live
    ("stale", never "live");
  * the last known quote is RETAINED (never pruned because a provider
    stopped sending) and is reported as stale, not live;
  * a stale-only instrument is visible but never presented as live;
  * STALE or UNDATED data never triggers a condition alert (the guard
    fails closed);
  * a LEVEL condition is never satisfied by another instrument's stale
    last-known value;
  * the F&O coverage does not mark a contract LIVE for a provider that is
    not delivering.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest


def _ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# 1. a connected feed that stopped delivering frames is NOT live
# ---------------------------------------------------------------------------

def test_a_connected_feed_without_frames_is_not_live() -> None:
    """The Upstox feed must not report "live" for a socket that is open."""
    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    class _Rest:
        async def authorize_market_feed(self, credentials):
            return "wss://example.invalid/feed"

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": ["NSE_EQ|INE002A01018"]},
        credentials=UpstoxCredentials(access_token="token"),
        rest=_Rest(),
        market_service=None,
        instrument_metadata={"NSE_EQ|INE002A01018": ("NSE", "RELIANCE")},
    )
    feed._state = "streaming"
    assert feed.status()["liveness"] == "awaiting_data"
    assert feed.status()["data_flowing"] is False
    assert feed.status()["stale"] is False

    # A frame arrived ...
    feed._counters["frames_received"] = 1
    feed._last_frame_at = datetime.now(timezone.utc).isoformat()
    feed._last_frame_mono = feed._monotonic()
    assert feed.status()["liveness"] == "live"

    # ... and then the provider went silent (the socket stayed open).
    feed._last_frame_mono = feed._monotonic() - 600.0
    status = feed.status()
    assert status["liveness"] == "stale", (
        "a connected socket that stopped delivering frames is not live")
    assert status["data_flowing"] is False
    assert status["stale"] is True


def test_a_fyers_feed_without_frames_is_not_live() -> None:
    from brokers.fyers.feed import FyersFeed

    feed = FyersFeed(
        config={"source_name": "fyers",
                "instrument_keys": ["NSE:SBIN-EQ"],
                "access_token_getter": lambda: "token"},
        auth=object(),
        market_service=None,
    )
    feed._state = "streaming"
    assert feed.status()["liveness"] == "awaiting_data"
    assert feed.status()["data_flowing"] is False

    feed._last_frame_mono = feed._mono()
    assert feed.status()["liveness"] == "live"
    feed._last_frame_mono = feed._mono() - 600.0
    assert feed.status()["liveness"] == "stale"
    assert feed.status()["data_flowing"] is False


# ---------------------------------------------------------------------------
# 2. the last known quote is retained and reported as stale
# ---------------------------------------------------------------------------

def test_the_last_known_quote_is_retained_and_marked_stale() -> None:
    from market.service import MarketService, QuotePatch

    svc = MarketService()
    patch = QuotePatch(
        exchange="NSE", instrument_token="NSE_EQ|INE002A01018",
        received_ts=_ago(3600), tradingsymbol="RELIANCE",
        reported_fields={"ltp": 2500.0},
    )
    asyncio_run(svc.apply_quote(patch))

    quote = asyncio_run(svc.get_quote("NSE", "NSE_EQ|INE002A01018"))
    assert quote is not None, "the last known quote must be retained"
    assert quote.ltp == 2500.0

    from app.quote_service import QuoteService

    qs = QuoteService(svc)
    payload = asyncio_run(qs.quote("NSE:EQUITY:INE002A01018"))
    if payload is None:
        # No catalogue in this fixture: the storage key still resolves.
        payload = asyncio_run(qs.quote("NSE_EQ|INE002A01018"))
    assert payload is not None
    assert payload["stale"] is True, "an hour-old quote is not live"
    assert payload["live"] is False
    assert payload["state"] == "stale"
    assert payload["as_of"], "the last known value keeps its timestamp"


def test_a_stale_only_instrument_stays_visible() -> None:
    from market.service import MarketService, QuotePatch

    svc = MarketService()
    asyncio_run(svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token="NSE_EQ|INE002A01018",
        received_ts=_ago(7200), tradingsymbol="RELIANCE",
        reported_fields={"ltp": 2500.0})))

    from app.quote_service import QuoteService

    qs = QuoteService(svc)
    payloads = asyncio_run(qs.quotes())
    assert len(payloads) == 1, "a stale-only instrument is not dropped"
    assert payloads[0]["stale"] is True
    assert payloads[0]["live"] is False
    assert payloads[0]["ltp"] == 2500.0


# ---------------------------------------------------------------------------
# 3. stale or undated data never triggers an alert
# ---------------------------------------------------------------------------

def test_an_undated_quote_never_triggers_a_condition_alert() -> None:
    """The freshness guard FAILS CLOSED on an unknown age."""
    with tempfile.TemporaryDirectory() as tmp:
        from app.condition_alerts import ConditionAlertEngine
        from core.persistence.store import EventStore

        store = EventStore(os.path.join(tmp, "t.db"))
        store.register_consumer("consumer-1")
        store.create_condition_alert(
            consumer_id="consumer-1", name="test", trigger_mode="repeat",
            condition_json={
                "condition_version": 1, "condition_id": "c1",
                "metric": "ltp", "operator": "gt", "value": 100,
                "instrument": {"canonical_id": "NSE:EQUITY:INE002A01018"}})

        class _UndatedQuote:
            exchange = "NSE"
            instrument_token = "2885"
            tradingsymbol = "RELIANCE"
            ltp = 500.0

        engine = ConditionAlertEngine(store, resolver=_Resolver())
        fired = asyncio_run(engine.evaluate(_UndatedQuote()))
        assert fired == [], "an undated quote must never fire an alert"
        assert engine._stale_evaluations >= 1


def test_a_stale_quote_never_triggers_a_market_alert() -> None:
    """The market alert engine has the same absolute guard."""
    from app.alerts import AlertEngine

    class _Store:
        def __init__(self):
            self.triggered = []

        def load_enabled_alerts(self):
            return [{"id": 1, "exchange": "NSE", "instrument_token": "T1",
                     "tradingsymbol": "AAA", "field": "ltp",
                     "operator": "gt", "threshold": 100.0,
                     "state": "inactive"}]

        def record_trigger(self, rule_id):
            self.triggered.append(rule_id)

        def record_alert_trigger_history(self, **kw):
            return None

    class _Quote:
        exchange = "NSE"
        instrument_token = "T1"
        tradingsymbol = "AAA"
        ltp = 500.0
        received_ts = _ago(7200)

    engine = AlertEngine(_Store())
    fired = asyncio_run(engine.evaluate(_Quote()))
    assert fired == [], "a stale quote must never fire an alert"
    assert engine._stale_evaluations >= 1


def test_a_level_condition_is_not_satisfied_by_a_stale_last_known_value(
        ) -> None:
    """A second instrument's STALE value must never fire a LEVEL alert."""
    with tempfile.TemporaryDirectory() as tmp:
        from app.condition_alerts import ConditionAlertEngine
        from core.persistence.store import EventStore

        store = EventStore(os.path.join(tmp, "t.db"))
        store.register_consumer("consumer-1")
        store.create_condition_alert(
            consumer_id="consumer-1", name="test", trigger_mode="repeat",
            condition_json={
                "condition_version": 2, "condition_id": "root",
                "logic": "all", "conditions": [
                    {"condition_version": 1, "condition_id": "leaf-a",
                     "metric": "ltp", "operator": "gt", "value": 100,
                     "instrument": {"canonical_id": "NSE:EQUITY:INE002A01018"}},
                    {"condition_version": 1, "condition_id": "leaf-b",
                     "metric": "ltp", "operator": "gt", "value": 100,
                     "instrument": {"canonical_id": "NSE:EQUITY:INE585B01010"}},
                ]})

        class _Quote:
            def __init__(self, token, ltp):
                self.exchange = "NSE"
                self.instrument_token = token
                self.tradingsymbol = token
                self.ltp = ltp
                self.received_ts = datetime.now(timezone.utc)

        engine = ConditionAlertEngine(store, resolver=_Resolver())

        # Instrument B ticks once and is above the threshold ...
        asyncio_run(engine.evaluate(_Quote("b", 500.0)))
        # ... and then goes silent (its last observation ages out).
        alert_id = next(iter(engine._state))
        engine._dep_last_seen[(alert_id, "leaf-b")] = _ago(7200)

        # Instrument A ticks: leaf B is evaluated against its STALE stored
        # value - it must not satisfy the condition.
        fired = asyncio_run(engine.evaluate(_Quote("a", 500.0)))
        assert fired == [], (
            "a stale last-known value of another instrument must never "
            "satisfy a LEVEL condition")


def test_a_level_condition_fires_when_both_values_are_fresh() -> None:
    """The legitimate multi-instrument alert still fires."""
    with tempfile.TemporaryDirectory() as tmp:
        from app.condition_alerts import ConditionAlertEngine
        from core.persistence.store import EventStore

        store = EventStore(os.path.join(tmp, "t.db"))
        store.register_consumer("consumer-1")
        store.create_condition_alert(
            consumer_id="consumer-1", name="test", trigger_mode="repeat",
            condition_json={
                "condition_version": 2, "condition_id": "root",
                "logic": "all", "conditions": [
                    {"condition_version": 1, "condition_id": "leaf-a",
                     "metric": "ltp", "operator": "gt", "value": 100,
                     "instrument": {"canonical_id": "NSE:EQUITY:INE002A01018"}},
                    {"condition_version": 1, "condition_id": "leaf-b",
                     "metric": "ltp", "operator": "gt", "value": 100,
                     "instrument": {"canonical_id": "NSE:EQUITY:INE585B01010"}},
                ]})

        class _Quote:
            def __init__(self, token, ltp):
                self.exchange = "NSE"
                self.instrument_token = token
                self.tradingsymbol = token
                self.ltp = ltp
                self.received_ts = datetime.now(timezone.utc)

        engine = ConditionAlertEngine(store, resolver=_Resolver())
        asyncio_run(engine.evaluate(_Quote("b", 500.0)))
        fired = asyncio_run(engine.evaluate(_Quote("a", 500.0)))
        assert len(fired) == 1, (
            "an alert whose conditions are all met by FRESH data must fire")


# ---------------------------------------------------------------------------
# 4. the F&O coverage never claims LIVE for a provider that is not delivering
# ---------------------------------------------------------------------------

def test_fno_coverage_does_not_mark_a_contract_live_for_an_idle_provider(
        ) -> None:
    from app.fno_coverage import FnoCoverageService

    service = FnoCoverageService(
        catalog=None,
        status_provider=lambda provider: "reconnecting")
    assert service._available("upstox") is False
    service = FnoCoverageService(
        catalog=None,
        status_provider=lambda provider: "streaming")
    assert service._available("upstox") is True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _Resolver:
    """A minimal identity resolver (canonical id per storage key)."""

    def resolve_quote(self, quote):
        token = getattr(quote, "instrument_token", None)
        if token == "2885":
            return "NSE:EQUITY:INE002A01018"
        if token == "b":
            return "NSE:EQUITY:INE585B01010"
        if token == "a":
            return "NSE:EQUITY:INE002A01018"
        return None

    def context_for(self, canonical_id):
        return {}


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
