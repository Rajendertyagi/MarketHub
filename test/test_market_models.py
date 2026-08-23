#!/usr/bin/env python3
"""Unit tests for canonical market models — no server required.

Covers (Phase A):
  * MM1: Instrument construction (required identity + optional metadata)
  * MM2: Quote construction (minimal + full snapshot; optional defaults)
  * MM3: DepthLevel construction
  * MM4: Depth coerces bids/asks sequences to immutable tuples
  * MM5: frozen dataclass behavior (mutation rejected, hashable)
  * MM6: timezone-aware timestamps accepted
  * MM7: naive datetimes rejected with ValueError (documented rule)
  * MM8: purity — stdlib-only module with no app/core/mcp_server/sources/
         brokers imports; empty-identity validation; package re-export

Each test is independently runnable via ``python test/test_market_models.py``.
Pure unit file: no server, no SQLite, no config.json, no network.
"""

from __future__ import annotations

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

from helpers.runner import R  # noqa: E402

# --- shared fixtures -------------------------------------------------------

UTC = timezone.utc
IST_LIKE = timezone(timedelta(hours=5, minutes=30))  # non-UTC fixed offset
AWARE_UTC = datetime(2026, 8, 23, 9, 15, 0, tzinfo=UTC)
AWARE_OFFSET = datetime(2026, 8, 23, 14, 45, 0, tzinfo=IST_LIKE)
NAIVE = datetime(2026, 8, 23, 9, 15, 0)


def _expect_raises(
    runner: R,
    label: str,
    exc_type: type,
    fn,
    needle: str | None = None,
) -> None:
    """Assert fn() raises exc_type (optionally with ``needle`` in the message)."""
    try:
        fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(
                label, needle in str(exc),
                f"message should contain {needle!r}: {exc}",
            )
        else:
            runner.ok(label)
        return
    except Exception as exc:  # narrow: report unexpected exception types
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")


# ---------------------------------------------------------------------------
# MM1 — Instrument
# ---------------------------------------------------------------------------


def test_instrument_construction(runner: R) -> None:
    """MM1: Instrument construction — required identity + optional metadata."""
    name = "MM1-instrument"
    from market.models import Instrument

    inst = Instrument(instrument_token="408065", exchange="NSE", tradingsymbol="RELIANCE")
    runner.assert_eq(name + "-identity-token", inst.instrument_token, "408065")
    runner.assert_eq(name + "-identity-exchange", inst.exchange, "NSE")
    runner.assert_eq(name + "-identity-symbol", inst.tradingsymbol, "RELIANCE")
    runner.assert_eq(name + "-default-name", inst.name, None)
    runner.assert_eq(name + "-default-type", inst.instrument_type, None)
    runner.assert_eq(name + "-default-tick", inst.tick_size, None)
    runner.assert_eq(name + "-default-lot", inst.lot_size, None)
    runner.assert_eq(name + "-default-expiry", inst.expiry, None)
    runner.assert_eq(name + "-default-strike", inst.strike, None)

    full = Instrument(
        instrument_token="123456",
        exchange="NFO",
        tradingsymbol="BANKNIFTY26AUG55000CE",
        name="Bank Nifty Option",
        instrument_type="CE",
        tick_size=0.05,
        lot_size=75,
        expiry=AWARE_UTC,
        strike=55000.0,
    )
    runner.assert_eq(name + "-full-name", full.name, "Bank Nifty Option")
    runner.assert_eq(name + "-full-type", full.instrument_type, "CE")
    runner.assert_eq(name + "-full-tick", full.tick_size, 0.05)
    runner.assert_eq(name + "-full-lot", full.lot_size, 75)
    runner.assert_eq(name + "-full-expiry", full.expiry, AWARE_UTC)
    runner.assert_eq(name + "-full-strike", full.strike, 55000.0)

    # Empty / blank identity is rejected with a field-naming error.
    for bad_field in ("instrument_token", "exchange", "tradingsymbol"):
        kwargs = {"instrument_token": "t", "exchange": "X", "tradingsymbol": "S"}
        kwargs[bad_field] = ""
        _expect_raises(runner, name + f"-empty-{bad_field}", ValueError,
                       lambda kw=dict(kwargs): Instrument(**kw), needle=bad_field)
        kwargs[bad_field] = "   "
        _expect_raises(runner, name + f"-blank-{bad_field}", ValueError,
                       lambda kw=dict(kwargs): Instrument(**kw), needle=bad_field)


# ---------------------------------------------------------------------------
# MM2 — Quote
# ---------------------------------------------------------------------------


def test_quote_construction(runner: R) -> None:
    """MM2: Quote construction — minimal and full snapshots; optional defaults."""
    name = "MM2-quote"
    from market.models import Quote

    q = Quote(
        instrument_token="408065",
        exchange="NSE",
        tradingsymbol="RELIANCE",
        received_ts=AWARE_UTC,
    )
    runner.assert_eq(name + "-identity", (q.instrument_token, q.exchange, q.tradingsymbol),
                     ("408065", "NSE", "RELIANCE"))
    runner.assert_eq(name + "-received", q.received_ts, AWARE_UTC)
    runner.assert_eq(name + "-ltp-default", q.ltp, None)
    runner.assert_eq(name + "-ohlc-defaults", (q.open, q.high, q.low, q.close),
                     (None, None, None, None))
    runner.assert_eq(name + "-volume-default", q.volume, None)
    runner.assert_eq(name + "-change-defaults", (q.change, q.change_percent), (None, None))
    runner.assert_eq(name + "-bidask-defaults", (q.best_bid, q.best_ask), (None, None))
    runner.assert_eq(name + "-open-interest-default", q.open_interest, None)
    runner.assert_eq(name + "-avg-trade-price-default", q.avg_trade_price, None)
    runner.assert_eq(name + "-last-traded-qty-default", q.last_traded_qty, None)
    runner.assert_eq(name + "-total-buy-qty-default", q.total_buy_qty, None)
    runner.assert_eq(name + "-total-sell-qty-default", q.total_sell_qty, None)
    runner.assert_eq(name + "-exchange-ts-default", q.exchange_ts, None)

    full = Quote(
        instrument_token="408065",
        exchange="NSE",
        tradingsymbol="RELIANCE",
        received_ts=AWARE_OFFSET,
        ltp=2985.5,
        open=2970.0,
        high=3001.0,
        low=2955.25,
        close=2968.4,
        volume=1_234_567,
        change=17.1,
        change_percent=0.58,
        best_bid=2985.35,
        best_ask=2985.65,
        open_interest=152_340_050,
        avg_trade_price=52.56,
        last_traded_qty=250,
        total_buy_qty=6917,
        total_sell_qty=8750,
        exchange_ts=AWARE_OFFSET,
    )
    runner.assert_eq(name + "-full-ltp", full.ltp, 2985.5)
    runner.assert_eq(name + "-full-ohlc", (full.open, full.high, full.low, full.close),
                     (2970.0, 3001.0, 2955.25, 2968.4))
    runner.assert_eq(name + "-full-volume", full.volume, 1_234_567)
    runner.assert_eq(name + "-full-change", (full.change, full.change_percent), (17.1, 0.58))
    runner.assert_eq(name + "-full-bidask", (full.best_bid, full.best_ask), (2985.35, 2985.65))
    runner.assert_eq(name + "-full-open-interest", full.open_interest, 152_340_050)
    runner.assert_eq(name + "-full-avg-trade-price", full.avg_trade_price, 52.56)
    runner.assert_eq(name + "-full-last-traded-qty", full.last_traded_qty, 250)
    runner.assert_eq(name + "-full-total-buy-qty", full.total_buy_qty, 6917)
    runner.assert_eq(name + "-full-total-sell-qty", full.total_sell_qty, 8750)
    runner.assert_eq(name + "-full-exchange-ts", full.exchange_ts, AWARE_OFFSET)

    # Equality/hash still work naturally with the extended field set:
    # a value-identical copy (dataclasses.replace) compares and hashes equal.
    twin = dataclasses.replace(full)
    runner.assert_true(name + "-eq-with-new-fields",
                       full == twin and hash(full) == hash(twin),
                       "Quote with populated extension fields must compare/hash equal")

    # No depth arrays on Quote — only scalar best bid/ask exist.
    runner.assert_false(name + "-no-depth-field",
                        hasattr(full, "bids") or hasattr(full, "asks"),
                        "Quote must not carry depth arrays")

    _expect_raises(runner, name + "-empty-token", ValueError,
                   lambda: Quote(instrument_token="", exchange="NSE",
                                 tradingsymbol="R", received_ts=AWARE_UTC),
                   needle="instrument_token")
    _expect_raises(runner, name + "-received-required", TypeError,
                   lambda: Quote(instrument_token="t", exchange="X", tradingsymbol="S"),
                   needle="required")  # missing positional arg surfaces as TypeError


# ---------------------------------------------------------------------------
# MM3 — DepthLevel
# ---------------------------------------------------------------------------


def test_depth_level_construction(runner: R) -> None:
    """MM3: DepthLevel construction — price/quantity required, orders optional."""
    name = "MM3-depth-level"
    from market.models import DepthLevel

    lvl = DepthLevel(price=2985.35, quantity=500)
    runner.assert_eq(name + "-price", lvl.price, 2985.35)
    runner.assert_eq(name + "-quantity", lvl.quantity, 500)
    runner.assert_eq(name + "-orders-default", lvl.orders, None)

    lvl2 = DepthLevel(price=100.05, quantity=25.5, orders=7)
    runner.assert_eq(name + "-orders", lvl2.orders, 7)


# ---------------------------------------------------------------------------
# MM4 — Depth tuple coercion
# ---------------------------------------------------------------------------


def test_depth_tuple_coercion(runner: R) -> None:
    """MM4: Depth coerces bids/asks sequences to immutable tuples."""
    name = "MM4-depth-tuples"
    from market.models import Depth, DepthLevel

    bids_list = [
        DepthLevel(price=2985.35, quantity=500, orders=3),
        DepthLevel(price=2985.30, quantity=150),
    ]
    asks_list = [DepthLevel(price=2985.65, quantity=250, orders=2)]
    d = Depth(
        instrument_token="408065",
        exchange="NSE",
        tradingsymbol="RELIANCE",
        received_ts=AWARE_UTC,
        bids=bids_list,
        asks=asks_list,
        exchange_ts=AWARE_OFFSET,
    )
    runner.assert_true(name + "-bids-tuple", isinstance(d.bids, tuple),
                       "bids must be stored as a tuple")
    runner.assert_true(name + "-asks-tuple", isinstance(d.asks, tuple),
                       "asks must be stored as a tuple")
    runner.assert_eq(name + "-bids-len", len(d.bids), 2)
    runner.assert_eq(name + "-asks-len", len(d.asks), 1)
    runner.assert_eq(name + "-bids-content", d.bids[0],
                     DepthLevel(price=2985.35, quantity=500, orders=3))

    # Mutating the ORIGINAL list afterwards must not affect the frozen model.
    bids_list.append(DepthLevel(price=1.0, quantity=1.0))
    runner.assert_eq(name + "-snapshot-isolated", len(d.bids), 2)

    # Direct tuple input works too; an empty book side is allowed.
    d2 = Depth(
        instrument_token="t", exchange="X", tradingsymbol="S",
        received_ts=AWARE_UTC,
        bids=(DepthLevel(price=1.0, quantity=1.0),),
        asks=(),
    )
    runner.assert_eq(name + "-empty-asks-ok", d2.asks, ())

    # Non-DepthLevel elements and non-sequences are TypeErrors.
    _expect_raises(runner, name + "-bad-element", TypeError,
                   lambda: Depth(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts=AWARE_UTC, bids=[100.0, 101.0], asks=()))
    _expect_raises(runner, name + "-non-sequence", TypeError,
                   lambda: Depth(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts=AWARE_UTC, bids=42, asks=()))
    _expect_raises(runner, name + "-none-bids", ValueError,
                   lambda: Depth(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts=AWARE_UTC, bids=None, asks=()))


# ---------------------------------------------------------------------------
# MM5 — Frozen behavior
# ---------------------------------------------------------------------------


def test_frozen_behavior(runner: R) -> None:
    """MM5: frozen dataclass behavior — mutation rejected; instances hashable."""
    name = "MM5-frozen"
    import dataclasses

    from market.models import Depth, DepthLevel, Instrument, Quote

    inst = Instrument(instrument_token="t", exchange="X", tradingsymbol="S")
    q = Quote(instrument_token="t", exchange="X", tradingsymbol="S",
              received_ts=AWARE_UTC, ltp=10.0)
    lvl = DepthLevel(price=1.0, quantity=1.0)
    d = Depth(instrument_token="t", exchange="X", tradingsymbol="S",
              received_ts=AWARE_UTC, bids=(lvl,), asks=())

    _expect_raises(runner, name + "-instrument", dataclasses.FrozenInstanceError,
                   lambda: setattr(inst, "exchange", "Y"))
    _expect_raises(runner, name + "-quote", dataclasses.FrozenInstanceError,
                   lambda: setattr(q, "ltp", 11.0))
    _expect_raises(runner, name + "-depth-level", dataclasses.FrozenInstanceError,
                   lambda: setattr(lvl, "price", 2.0))
    _expect_raises(runner, name + "-depth", dataclasses.FrozenInstanceError,
                   lambda: setattr(d, "bids", ()))

    # frozen+eq dataclasses gain a deterministic __hash__.
    runner.assert_eq(name + "-hash-stable", hash(q) == hash(q), True)
    runner.assert_true(
        name + "-eq-same-fields",
        q == Quote(instrument_token="t", exchange="X", tradingsymbol="S",
                   received_ts=AWARE_UTC, ltp=10.0),
        "identical field values should compare equal",
    )


# ---------------------------------------------------------------------------
# MM6 — Timezone-aware timestamps accepted
# ---------------------------------------------------------------------------


def test_tz_aware_accepted(runner: R) -> None:
    """MM6: timezone-aware timestamps accepted (UTC and fixed-offset)."""
    name = "MM6-tz-aware"
    from market.models import Depth, DepthLevel, Instrument, Quote

    q = Quote(instrument_token="t", exchange="X", tradingsymbol="S",
              received_ts=AWARE_UTC, exchange_ts=AWARE_OFFSET)
    runner.assert_eq(name + "-quote-received-utc", q.received_ts.utcoffset(), timedelta(0))
    runner.assert_eq(name + "-quote-exchange-offset", q.exchange_ts.utcoffset(),
                     timedelta(hours=5, minutes=30))

    inst = Instrument(instrument_token="t", exchange="X", tradingsymbol="S",
                      expiry=AWARE_OFFSET)
    runner.assert_true(name + "-expiry-aware", inst.expiry.tzinfo is not None,
                       "expiry keeps its tzinfo")

    d = Depth(instrument_token="t", exchange="X", tradingsymbol="S",
              received_ts=AWARE_OFFSET, bids=(DepthLevel(1.0, 1.0),), asks=())
    runner.assert_true(name + "-depth-received-aware", d.received_ts.tzinfo is not None,
                       "depth received_ts keeps its tzinfo")


# ---------------------------------------------------------------------------
# MM7 — Naive datetimes rejected
# ---------------------------------------------------------------------------


def test_naive_rejected(runner: R) -> None:
    """MM7: naive datetimes rejected with ValueError (documented rule)."""
    name = "MM7-naive-rejected"
    from market.models import Depth, Instrument, Quote

    _expect_raises(runner, name + "-quote-received", ValueError,
                   lambda: Quote(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts=NAIVE),
                   needle="timezone-aware")
    _expect_raises(runner, name + "-quote-exchange", ValueError,
                   lambda: Quote(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts=AWARE_UTC, exchange_ts=NAIVE),
                   needle="timezone-aware")
    _expect_raises(runner, name + "-instrument-expiry", ValueError,
                   lambda: Instrument(instrument_token="t", exchange="X", tradingsymbol="S",
                                      expiry=NAIVE),
                   needle="timezone-aware")
    _expect_raises(runner, name + "-depth-received", ValueError,
                   lambda: Depth(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts=NAIVE, bids=(), asks=()),
                   needle="timezone-aware")

    # Non-datetime values are TypeErrors, not ValueErrors.
    _expect_raises(runner, name + "-received-not-datetime", TypeError,
                   lambda: Quote(instrument_token="t", exchange="X", tradingsymbol="S",
                                 received_ts="2026-08-23T09:15:00+00:00"))


# ---------------------------------------------------------------------------
# MM8 — Purity + re-export
# ---------------------------------------------------------------------------


def test_purity_and_reexport(runner: R) -> None:
    """MM8: stdlib-only purity, forbidden-import scan, re-export identity."""
    name = "MM8-purity"
    import market
    import market.models as mm

    # Package re-export exposes the canonical types cleanly.
    runner.assert_true(name + "-reexport-quote", market.Quote is mm.Quote,
                       "market.Quote must be the same object as market.models.Quote")
    runner.assert_eq(name + "-all-exports", sorted(market.__all__),
                     ["Depth", "DepthLevel", "Instrument", "Quote"])

    # Static source scan: no application-package or third-party imports.
    with open(mm.__file__, "r", encoding="utf-8") as f:
        source = f.read()

    forbidden_project = (
        "import app", "from app", "import core", "from core",
        "import mcp_server", "from mcp_server",
        "import sources", "from sources",
        "import brokers", "from brokers",
    )
    for frag in forbidden_project:
        runner.assert_not_in(name + "-no-" + frag.replace(" ", "-"), frag, source)

    allowed_import_lines = {
        "from __future__ import annotations",
        "from dataclasses import dataclass",
        "from datetime import datetime",
        "from typing import Any",
    }
    import_lines = [
        line.strip() for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    unexpected = [ln for ln in import_lines if ln not in allowed_import_lines]
    runner.assert_eq(name + "-stdlib-only-imports", unexpected, [])

    # Empty/blank/non-string identity is rejected across all three models.
    _expect_raises(runner, name + "-quote-empty-token", ValueError,
                   lambda: mm.Quote(instrument_token="", exchange="X", tradingsymbol="S",
                                    received_ts=AWARE_UTC))
    _expect_raises(runner, name + "-depth-blank-symbol", ValueError,
                   lambda: mm.Depth(instrument_token="t", exchange="X", tradingsymbol="  ",
                                    received_ts=AWARE_UTC, bids=(), asks=()))
    _expect_raises(runner, name + "-instrument-nonstring", ValueError,
                   lambda: mm.Instrument(instrument_token=12345, exchange="X",
                                         tradingsymbol="S"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> bool:
    runner = R()

    test_instrument_construction(runner)
    test_quote_construction(runner)
    test_depth_level_construction(runner)
    test_depth_tuple_coercion(runner)
    test_frozen_behavior(runner)
    test_tz_aware_accepted(runner)
    test_naive_rejected(runner)
    test_purity_and_reexport(runner)

    return runner.summary()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
