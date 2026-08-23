#!/usr/bin/env python3
"""Unit tests for MarketService — merge/staleness/concurrency semantics.

Phase B2 coverage:
  * MS1..MS6   QuotePatch creation + presence rules (absent/None/value)
  * MS7..MS12  staleness & ordering (exchange_ts primary, received_ts
               fallback, equal-timestamp last-wins, no source priority)
  * MS13       unchanged accepted patch -> changed=False
  * MS14..MS17 callback firing rules + failure isolation (sync and async)
  * MS18..MS21 Depth replace/stale/equal semantics
  * MS22..MS23 snapshot reads + status counters
  * MS24       asyncio.gather concurrent writer smoke
  * MS25       immutability of returned canonical objects

Each test is independently runnable via ``python test/test_market_service.py``.
Pure unit file: no server, no SQLite, no config.json, no network.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import sys

# Make the project root importable regardless of the working directory the
# test is launched from (mirrors test_unit_sources.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from datetime import datetime, timedelta, timezone  # noqa: E402
from types import MappingProxyType  # noqa: E402

from helpers.runner import R  # noqa: E402

UTC = timezone.utc
T0 = datetime(2026, 8, 23, 9, 0, 0, tzinfo=UTC)

EXCHANGE = "NSE"
TOKEN = "INE123A01018"
SYMBOL = "SBIN-EQ"


def ts(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_patch(**overrides):
    """QuotePatch factory with sensible defaults."""
    from market.service import QuotePatch

    defaults = dict(
        exchange=EXCHANGE,
        instrument_token=TOKEN,
        received_ts=ts(1),
        tradingsymbol=SYMBOL,
        reported_fields={"ltp": 100.0},
    )
    defaults.update(overrides)
    return QuotePatch(**defaults)


def _expect_raises(runner: R, label: str, exc_type: type, fn, needle: str | None = None) -> None:
    """Assert fn() raises exc_type (optionally with ``needle`` in the message)."""
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
# MS1..MS6 — creation + presence rules
# ---------------------------------------------------------------------------


async def test_ms1_first_patch_creates(runner: R) -> None:
    """MS1: first patch creates the canonical Quote."""
    name = "MS1-first-create"
    from market.service import MarketService

    svc = MarketService()
    outcome = await svc.apply_quote(make_patch())
    runner.assert_eq(name + "-accepted", outcome.accepted, True)
    runner.assert_eq(name + "-created", outcome.created, True)
    runner.assert_eq(name + "-stale", outcome.stale, False)
    runner.assert_eq(name + "-changed", outcome.changed, True)
    runner.assert_eq(name + "-key", outcome.key, (EXCHANGE, TOKEN))

    q = await svc.get_quote(EXCHANGE, TOKEN)
    runner.assert_true(name + "-exists", q is not None)
    assert q is not None
    runner.assert_eq(name + "-identity",
                     (q.exchange, q.instrument_token, q.tradingsymbol),
                     (EXCHANGE, TOKEN, SYMBOL))
    runner.assert_eq(name + "-ltp", q.ltp, 100.0)
    runner.assert_eq(name + "-received", q.received_ts, ts(1))


async def test_ms2_absent_preserves(runner: R) -> None:
    """MS2: field absent from patch preserves prior canonical value."""
    name = "MS2-absent-preserves"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(reported_fields={"ltp": 100.0, "volume": 500}))
    await svc.apply_quote(make_patch(received_ts=ts(2), reported_fields={"ltp": 101.0}))

    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-updated-field", q.ltp, 101.0)
    runner.assert_eq(name + "-preserved-field", q.volume, 500)


async def test_ms3_explicit_none_clears(runner: R) -> None:
    """MS3: field present with None explicitly clears the canonical value."""
    name = "MS3-none-clears"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(reported_fields={"ltp": 100.0, "best_bid": 99.5}))
    await svc.apply_quote(
        make_patch(received_ts=ts(2), reported_fields={"best_bid": None})
    )

    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-cleared", q.best_bid, None)
    runner.assert_eq(name + "-untouched", q.ltp, 100.0)


async def test_ms4_value_replaces(runner: R) -> None:
    """MS4: reported value replaces prior value."""
    name = "MS4-value-replaces"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(reported_fields={"ltp": 100.0}))
    await svc.apply_quote(make_patch(received_ts=ts(2), reported_fields={"ltp": 250.5}))

    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-replaced", q.ltp, 250.5)


async def test_ms5_unknown_field_rejected(runner: R) -> None:
    """MS5: unknown patch fields are rejected deterministically."""
    name = "MS5-unknown-field"
    from market.service import MarketServiceError

    _expect_raises(runner, name + "-alias-rejected", MarketServiceError,
                   lambda: make_patch(reported_fields={"last_price": 1.0}),
                   needle="unknown patch fields")
    _expect_raises(runner, name + "-bogus-rejected", MarketServiceError,
                   lambda: make_patch(reported_fields={"bogus": 1}),
                   needle="bogus")
    _expect_raises(runner, name + "-identity-not-patchable", MarketServiceError,
                   lambda: make_patch(reported_fields={"tradingsymbol": "X"}))


async def test_ms6_missing_first_requirements_rejected(runner: R) -> None:
    """MS6: missing first-patch requirements are rejected clearly."""
    name = "MS6-first-requirements"
    from market.service import MarketService, MarketServiceError

    svc = MarketService()
    # tradingsymbol=None is LEGAL at construction (optional on later patches);
    # creation-time enforcement happens at apply (asserted below).
    anon = make_patch(tradingsymbol=None)
    try:
        await svc.apply_quote(anon)
        runner.fail(name + "-apply-rejected", "expected MarketServiceError")
    except MarketServiceError as exc:
        runner.assert_true(name + "-apply-rejected", "tradingsymbol" in str(exc),
                           f"error should mention tradingsymbol: {exc}")
    runner.ok(name + "-none-constructible")

    from market.service import MarketServiceError as MSE
    _expect_raises(runner, name + "-blank-symbol", MSE,
                   lambda: make_patch(tradingsymbol="   "), needle="tradingsymbol")

    from market.service import MarketServiceError as MSE
    _expect_raises(runner, name + "-naive-received", MSE,
                   lambda: make_patch(received_ts=datetime(2026, 8, 23, 9, 0, 0)))
    _expect_raises(runner, name + "-naive-exchange", MSE,
                   lambda: make_patch(exchange_ts=datetime(2026, 8, 23, 9, 0, 0)))
    _expect_raises(runner, name + "-empty-token", MSE,
                   lambda: make_patch(instrument_token="  "))


# ---------------------------------------------------------------------------
# MS7..MS12 — staleness / ordering
# ---------------------------------------------------------------------------


async def test_ms7_stale_rejected(runner: R) -> None:
    """MS7: older update rejected as stale; state untouched; no raise."""
    name = "MS7-stale-quote"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(received_ts=ts(50), exchange_ts=ts(100),
                                     reported_fields={"ltp": 200.0}))
    outcome = await svc.apply_quote(make_patch(received_ts=ts(60), exchange_ts=ts(99),
                                               reported_fields={"ltp": 1.0}))
    runner.assert_eq(name + "-accepted", outcome.accepted, False)
    runner.assert_eq(name + "-stale", outcome.stale, True)
    runner.assert_eq(name + "-created", outcome.created, False)

    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-state-kept", q.ltp, 200.0)


async def test_ms8_newer_accepted(runner: R) -> None:
    """MS8: newer update accepted."""
    name = "MS8-newer-accepted"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(exchange_ts=ts(100), reported_fields={"ltp": 200.0}))
    outcome = await svc.apply_quote(make_patch(received_ts=ts(2), exchange_ts=ts(101),
                                               reported_fields={"ltp": 201.0}))
    runner.assert_eq(name + "-accepted", outcome.accepted, True)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-value", q.ltp, 201.0)


async def test_ms9_equal_timestamp_last_wins(runner: R) -> None:
    """MS9: equal ordering timestamp accepted; last arrival wins."""
    name = "MS9-equal-last-wins"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(received_ts=ts(5), reported_fields={"ltp": 100.0}))
    o2 = await svc.apply_quote(make_patch(received_ts=ts(5), reported_fields={"ltp": 300.0}))
    runner.assert_eq(name + "-second-accepted", o2.accepted, True)
    runner.assert_eq(name + "-second-stale", o2.stale, False)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-last-wins", q.ltp, 300.0)


async def test_ms10_no_source_priority(runner: R) -> None:
    """MS10: REST-vs-WS has no artificial priority — timestamps only."""
    name = "MS10-no-source-priority"
    from market.service import MarketService

    svc = MarketService()
    # "REST-style" snapshot: has exchange_ts.
    await svc.apply_quote(make_patch(received_ts=ts(10), exchange_ts=ts(5),
                                     reported_fields={"ltp": 100.0}))
    # "WS-style" tick: no exchange_ts, later received_ts -> accepted via
    # received_ts fallback (a source-priority scheme might wrongly prefer
    # the snapshot carrying exchange_ts).
    o = await svc.apply_quote(make_patch(received_ts=ts(11),
                                         reported_fields={"ltp": 101.0}))
    runner.assert_eq(name + "-ws-over-rest", o.accepted, True)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-ws-value", q.ltp, 101.0)

    # Reverse direction on a FRESH service whose lineage is purely WS-style
    # (no exchange_ts ever): a newer REST-style snapshot wins on its later
    # received_ts via the fallback domain.
    svc2 = MarketService()
    await svc2.apply_quote(make_patch(received_ts=ts(20), reported_fields={"ltp": 200.0}))
    o2 = await svc2.apply_quote(make_patch(received_ts=ts(21), exchange_ts=ts(4),
                                           reported_fields={"ltp": 202.0}))
    runner.assert_eq(name + "-rest-over-ws", o2.accepted, True)


async def test_ms11_exchange_domain_primary(runner: R) -> None:
    """MS11: when both sides carry exchange_ts, that domain decides."""
    name = "MS11-exchange-domain"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(received_ts=ts(50), exchange_ts=ts(100),
                                     reported_fields={"ltp": 100.0}))
    # Newer exchange_ts despite OLDER received_ts -> accepted.
    o = await svc.apply_quote(make_patch(received_ts=ts(40), exchange_ts=ts(101),
                                         reported_fields={"ltp": 101.0}))
    runner.assert_eq(name + "-newer-exchange-accepted", o.accepted, True)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-value", q.ltp, 101.0)
    runner.assert_eq(name + "-exchange-ts-kept", q.exchange_ts, ts(101))

    # Older exchange_ts despite NEWER received_ts -> stale.
    o2 = await svc.apply_quote(make_patch(received_ts=ts(60), exchange_ts=ts(99),
                                          reported_fields={"ltp": 999.0}))
    runner.assert_eq(name + "-older-exchange-stale", o2.stale, True)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-value-kept", q.ltp, 101.0)


async def test_ms12_received_fallback(runner: R) -> None:
    """MS12: received_ts fallback when exchange_ts is not comparable."""
    name = "MS12-received-fallback"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(received_ts=ts(10), reported_fields={"ltp": 100.0}))
    # Neither side has exchange_ts -> received_ts compared on both sides.
    o = await svc.apply_quote(make_patch(received_ts=ts(9), reported_fields={"ltp": 1.0}))
    runner.assert_eq(name + "-older-received-stale", o.stale, True)
    o2 = await svc.apply_quote(make_patch(received_ts=ts(11), reported_fields={"ltp": 101.0}))
    runner.assert_eq(name + "-newer-received-accepted", o2.accepted, True)

    # Incoming carries exchange_ts but current does NOT -> still received_ts
    # domain (rule 6), so an older received_ts is stale even with fresh ex_ts.
    o3 = await svc.apply_quote(make_patch(received_ts=ts(5), exchange_ts=ts(999),
                                          reported_fields={"ltp": 2.0}))
    runner.assert_eq(name + "-mixed-domain-fallback", o3.stale, True)


# ---------------------------------------------------------------------------
# MS13..MS17 — changed semantics + callback isolation
# ---------------------------------------------------------------------------


async def test_ms13_unchanged_changed_false(runner: R) -> None:
    """MS13: accepted equal-timestamp patch with identical values -> changed=False."""
    name = "MS13-unchanged"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(received_ts=ts(5), reported_fields={"ltp": 100.0}))
    outcome = await svc.apply_quote(make_patch(received_ts=ts(5),
                                               reported_fields={"ltp": 100.0}))
    runner.assert_eq(name + "-accepted", outcome.accepted, True)
    runner.assert_eq(name + "-changed", outcome.changed, False)
    runner.assert_eq(name + "-created", outcome.created, False)


async def test_ms14_callback_fires_on_accepted_change(runner: R) -> None:
    """MS14: async callback fires after accepted+changed merge, post-commit."""
    name = "MS14-callback-fires"
    from market.service import MarketService

    seen: list = []

    async def on_quote_update(quote) -> None:
        seen.append(quote)

    svc = MarketService(on_quote_update=on_quote_update)
    await svc.apply_quote(make_patch(reported_fields={"ltp": 100.0}))
    runner.assert_eq(name + "-create-fired", len(seen), 1)
    await svc.apply_quote(make_patch(received_ts=ts(2), reported_fields={"ltp": 101.0}))
    runner.assert_eq(name + "-merge-fired", len(seen), 2)
    runner.assert_eq(name + "-latest-value", seen[-1].ltp, 101.0)

    # Sync callbacks are supported too.
    sync_seen: list = []
    svc2 = MarketService(on_quote_update=sync_seen.append)
    await svc2.apply_quote(make_patch(reported_fields={"ltp": 1.0}))
    runner.assert_eq(name + "-sync-callback", len(sync_seen), 1)


async def test_ms15_no_callback_on_stale(runner: R) -> None:
    """MS15: stale update never fires the callback."""
    name = "MS15-callback-stale"
    from market.service import MarketService

    seen: list = []
    svc = MarketService(on_quote_update=seen.append)
    await svc.apply_quote(make_patch(exchange_ts=ts(100), received_ts=ts(10)))
    await svc.apply_quote(make_patch(exchange_ts=ts(99), received_ts=ts(11)))
    runner.assert_eq(name + "-count", len(seen), 1)


async def test_ms16_no_callback_on_unchanged(runner: R) -> None:
    """MS16: unchanged result does not fire the callback."""
    name = "MS16-callback-unchanged"
    from market.service import MarketService

    seen: list = []
    svc = MarketService(on_quote_update=seen.append)
    await svc.apply_quote(make_patch(received_ts=ts(5), reported_fields={"ltp": 100.0}))
    await svc.apply_quote(make_patch(received_ts=ts(5), reported_fields={"ltp": 100.0}))
    runner.assert_eq(name + "-count", len(seen), 1)


async def test_ms17_callback_failure_isolated(runner: R) -> None:
    """MS17: callback exceptions never fail apply_quote nor roll back state."""
    name = "MS17-callback-isolation"
    from market.service import MarketService

    def bad_sync_callback(quote):
        raise RuntimeError("sync callback boom")

    svc = MarketService(on_quote_update=bad_sync_callback)
    outcome = await svc.apply_quote(make_patch(reported_fields={"ltp": 100.0}))
    runner.assert_eq(name + "-sync-isolated", outcome.accepted, True)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-state-committed", q.ltp, 100.0)

    async def bad_async_callback(quote):
        raise RuntimeError("async callback boom")

    svc2 = MarketService(on_quote_update=bad_async_callback)
    outcome2 = await svc2.apply_quote(make_patch(received_ts=ts(2),
                                                 reported_fields={"ltp": 101.0}))
    runner.assert_eq(name + "-async-isolated", outcome2.accepted, True)
    status = await svc2.status()
    runner.assert_eq(name + "-counted", status["quote_count"], 1)


# ---------------------------------------------------------------------------
# MS18..MS21 — Depth semantics
# ---------------------------------------------------------------------------


def make_depth(*, received_ts=None, exchange_ts=None, bid_price=100.0):
    from market.models import Depth, DepthLevel

    kwargs = {}
    if exchange_ts is not None:
        kwargs["exchange_ts"] = exchange_ts
    return Depth(
        instrument_token=TOKEN,
        exchange=EXCHANGE,
        tradingsymbol=SYMBOL,
        received_ts=received_ts if received_ts is not None else ts(1),
        bids=(DepthLevel(price=bid_price, quantity=10.0, orders=1),),
        asks=(DepthLevel(price=bid_price + 0.5, quantity=8.0, orders=2),),
        **kwargs,
    )


async def test_ms18_depth_insert_and_replace(runner: R) -> None:
    """MS18/MS19: first insert creates; newer snapshot replaces wholesale."""
    name = "MS18-depth-insert"
    from market.service import MarketService

    svc = MarketService()
    o1 = await svc.apply_depth(make_depth(received_ts=ts(1), bid_price=100.0))
    runner.assert_eq(name + "-created", (o1.accepted, o1.created, o1.changed),
                     (True, True, True))

    name = "MS19-depth-replace"
    o2 = await svc.apply_depth(make_depth(received_ts=ts(2), bid_price=101.0))
    runner.assert_eq(name + "-accepted", (o2.accepted, o2.created, o2.changed),
                     (True, False, True))
    d = await svc.get_depth(EXCHANGE, TOKEN)
    assert d is not None
    runner.assert_eq(name + "-replaced-wholesale",
                     [(l.price, l.quantity) for l in d.bids], [(101.0, 10.0)])
    status = await svc.status()
    runner.assert_eq(name + "-single-entry", status["depth_count"], 1)


async def test_ms20_depth_stale_rejected(runner: R) -> None:
    """MS20: older depth snapshot rejected as stale."""
    name = "MS20-depth-stale"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_depth(make_depth(received_ts=ts(10), exchange_ts=ts(100)))
    o = await svc.apply_depth(make_depth(received_ts=ts(11), exchange_ts=ts(99),
                                         bid_price=1.0))
    runner.assert_eq(name + "-stale", (o.accepted, o.stale), (False, True))
    d = await svc.get_depth(EXCHANGE, TOKEN)
    assert d is not None
    runner.assert_eq(name + "-kept", d.bids[0].price, 100.0)


async def test_ms21_depth_equal_last_wins(runner: R) -> None:
    """MS21: equal ordering timestamp -> last arrival wins."""
    name = "MS21-depth-equal"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_depth(make_depth(received_ts=ts(5), bid_price=100.0))
    o = await svc.apply_depth(make_depth(received_ts=ts(5), bid_price=200.0))
    runner.assert_eq(name + "-accepted", (o.accepted, o.stale), (True, False))
    d = await svc.get_depth(EXCHANGE, TOKEN)
    assert d is not None
    runner.assert_eq(name + "-last-wins", d.bids[0].price, 200.0)


# ---------------------------------------------------------------------------
# MS22..MS25 — reads, counters, concurrency, immutability
# ---------------------------------------------------------------------------


async def test_ms22_snapshot_reads(runner: R) -> None:
    """MS22: get_* misses return None; quotes()/depths() return tuples."""
    name = "MS22-reads"
    from market.service import MarketService

    svc = MarketService()
    runner.assert_eq(name + "-quote-miss", await svc.get_quote("BSE", "X"), None)
    runner.assert_eq(name + "-depth-miss", await svc.get_depth("BSE", "X"), None)
    runner.assert_eq(name + "-empty-quotes", await svc.quotes(), ())
    runner.assert_eq(name + "-empty-depths", await svc.depths(), ())

    await svc.apply_quote(make_patch())
    await svc.apply_depth(make_depth())
    quotes = await svc.quotes()
    depths = await svc.depths()
    runner.assert_true(name + "-tuples", isinstance(quotes, tuple) and isinstance(depths, tuple))
    runner.assert_eq(name + "-counts", (len(quotes), len(depths)), (1, 1))


async def test_ms23_status_counters(runner: R) -> None:
    """MS23: minimal service-local counters track scripted operations."""
    name = "MS23-status"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch())                                    # created
    # recv ts(9)+ex ts(1) vs current (no ex_ts): fallback received domain
    # -> 9 > 1 accepted; merged now carries exchange_ts=ts(1).
    await svc.apply_quote(make_patch(received_ts=ts(9), exchange_ts=ts(1)))
    # recv ts(2), no ex_ts vs current ex_ts=ts(1): fallback received domain
    # -> 2 < 9 stale.
    await svc.apply_quote(make_patch(received_ts=ts(2), reported_fields={"ltp": 101.0}))  # stale
    await svc.apply_depth(make_depth())                                    # created
    await svc.apply_depth(make_depth(received_ts=ts(9), exchange_ts=ts(1)))  # accepted (fallback)
    status = await svc.status()
    runner.assert_eq(name + "-exact", status, {
        "quote_count": 1,
        "depth_count": 1,
        "accepted_quote_updates": 2,
        "stale_quote_updates": 1,
        "accepted_depth_updates": 2,
        "stale_depth_updates": 0,
    })


async def test_ms24_concurrent_writers(runner: R) -> None:
    """MS24: asyncio.gather smoke — deterministic final state under contention."""
    name = "MS24-gather"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(received_ts=ts(0), reported_fields={"ltp": 100.0}))

    patches = [
        make_patch(received_ts=ts(i), reported_fields={"ltp": float(100 + i)})
        for i in range(1, 11)
    ]
    outcomes = await asyncio.gather(*(svc.apply_quote(p) for p in patches))
    runner.assert_eq(name + "-all-resolved", len(outcomes), 10)
    runner.assert_true(
        name + "-binary-outcomes",
        all((o.accepted and not o.stale) or (o.stale and not o.accepted) for o in outcomes),
        "every concurrent update must be cleanly accepted or stale",
    )

    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    # Strictly increasing timestamps: the highest-ts patch always wins.
    runner.assert_eq(name + "-final-value", q.ltp, 110.0)
    status = await svc.status()
    runner.assert_eq(name + "-one-instrument", status["quote_count"], 1)
    runner.assert_eq(name + "-accounted",
                     status["accepted_quote_updates"] + status["stale_quote_updates"], 11)


async def test_ms25_immutability(runner: R) -> None:
    """MS25: returned objects are frozen; service internals not exposed."""
    name = "MS25-immutable"
    from market.service import MarketService

    svc = MarketService()
    await svc.apply_quote(make_patch(reported_fields={"ltp": 100.0}))
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    _expect_raises(runner, name + "-frozen-quote", dataclasses.FrozenInstanceError,
                   lambda: setattr(q, "ltp", 1.0))

    quotes = await svc.quotes()
    _expect_raises(runner, name + "-tuple-immutable", AttributeError,
                   lambda: quotes.__setitem__(0, None))


async def test_ms26_reported_fields_immutable(runner: R) -> None:
    """MS26: reported_fields is a true snapshot — read-only after construction."""
    name = "MS26-patch-immutable"
    from market.service import MarketService, QuotePatch

    original = {"ltp": 100.0, "volume": 500}
    p = make_patch(reported_fields=original)
    runner.assert_true(name + "-is-mappingproxy",
                       isinstance(p.reported_fields, MappingProxyType),
                       "reported_fields must be stored as a read-only mapping")

    # A. caller-side mutation of the ORIGINAL mapping must not affect patch.
    original["ltp"] = 200.0
    runner.assert_eq(name + "-caller-alias-isolated", p.reported_fields["ltp"], 100.0)

    # B/C/D. Real statement-form mutations must fail with TypeError
    # (mappingproxy raises AttributeError only if you call the dunder
    # directly; operator syntax goes through the read-only slot).
    def _assign() -> None:
        p.reported_fields["ltp"] = 200.0  # type: ignore[index]

    def _add() -> None:
        p.reported_fields["high"] = 1.0  # type: ignore[index]

    def _delete() -> None:
        del p.reported_fields["ltp"]  # type: ignore[index]

    _expect_raises(runner, name + "-assign-fails", TypeError, _assign)
    _expect_raises(runner, name + "-add-fails", TypeError, _add)
    _expect_raises(runner, name + "-delete-fails", TypeError, _delete)

    # E. normal reads still work; service merge consumes the snapshot.
    runner.assert_eq(name + "-read", p.reported_fields["volume"], 500)
    svc = MarketService()
    outcome = await svc.apply_quote(p)
    runner.assert_eq(name + "-apply-ok", outcome.accepted, True)
    q = await svc.get_quote(EXCHANGE, TOKEN)
    assert q is not None
    runner.assert_eq(name + "-merge-uses-snapshot", (q.ltp, q.volume), (100.0, 500))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_ms1_first_patch_creates(runner)
    await test_ms2_absent_preserves(runner)
    await test_ms3_explicit_none_clears(runner)
    await test_ms4_value_replaces(runner)
    await test_ms5_unknown_field_rejected(runner)
    await test_ms6_missing_first_requirements_rejected(runner)
    await test_ms7_stale_rejected(runner)
    await test_ms8_newer_accepted(runner)
    await test_ms9_equal_timestamp_last_wins(runner)
    await test_ms10_no_source_priority(runner)
    await test_ms11_exchange_domain_primary(runner)
    await test_ms12_received_fallback(runner)
    await test_ms13_unchanged_changed_false(runner)
    await test_ms14_callback_fires_on_accepted_change(runner)
    await test_ms15_no_callback_on_stale(runner)
    await test_ms16_no_callback_on_unchanged(runner)
    await test_ms17_callback_failure_isolated(runner)
    await test_ms18_depth_insert_and_replace(runner)
    await test_ms20_depth_stale_rejected(runner)
    await test_ms21_depth_equal_last_wins(runner)
    await test_ms22_snapshot_reads(runner)
    await test_ms23_status_counters(runner)
    await test_ms24_concurrent_writers(runner)
    await test_ms25_immutability(runner)
    await test_ms26_reported_fields_immutable(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
