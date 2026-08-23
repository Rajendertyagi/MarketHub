#!/usr/bin/env python3
"""Unit tests for provider normalizers + canonical serialization — no network.

Phase B1 coverage (recorded representative payloads):
  * MN-C: shared primitives — timestamp units, numeric coercion rules
          (bool/NaN/Inf/malformed rejected, blanks -> None), presence-
          preserving set_reported, derived-change policy, field-map guard
  * MN-U: Upstox — REST full quote (real docs sample), derived change,
          malformed payloads, depth zero-fill drop / zero-qty kept,
          WS ltpc + full patches (presence-exact, proto3 0-absent rule),
          WS depth, instrument master records
  * MN-F: FYERS — quotes REST snapshot, SymbolUpdate partial (incl.
          explicit-null vs absent distinction), depth + supplemental map,
          instrument master records
  * MN-S: serialization — canonical names/order, ISO-8601 UTC timestamps,
          tuples -> arrays, None preserved

Each test is independently runnable via ``python test/test_market_normalize.py``.
Pure unit file: no server, no SQLite, no config.json, no network.
"""

from __future__ import annotations

import dataclasses
import math
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

from datetime import datetime, timezone  # noqa: E402

from helpers.runner import R  # noqa: E402

UTC = timezone.utc
AWARE_UTC = datetime(2026, 8, 23, 9, 15, 0, tzinfo=UTC)


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
# Real recorded provider payloads (from official docs / SDK references)
# ---------------------------------------------------------------------------

# Upstox v2 Get Full Market Quote — official docs response sample (NHPC).
UPSTOX_NHPC = {
    "ohlc": {"open": 53.4, "high": 53.8, "low": 51.75, "close": 52.05},
    "depth": {
        "buy": [
            {"quantity": 6917, "price": 52.05, "orders": 20},
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
        ],
        "sell": [
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
            {"quantity": 0, "price": 0, "orders": 0},
        ],
    },
    "timestamp": "2023-10-19T05:21:51.099+05:30",
    "instrument_token": "NSE_EQ|INE848E01016",
    "symbol": "NHPC",
    "last_price": 52.04999923706055,
    "volume": 24123697,
    "average_price": 52.56,
    "oi": 0,
    "net_change": -1.0500000000000043,
    "total_buy_quantity": 6917,
    "total_sell_quantity": 0,
    "lower_circuit_limit": 42.5,
    "upper_circuit_limit": 63.7,
    "last_trade_time": "1697624972130",
    "oi_day_high": 0,
    "oi_day_low": 0,
}

# Upstox v3 WS ltpc-mode live feed (official docs sample, NSE_FO|45450).
UPSTOX_LTPC = {"ltp": 219.3, "ltt": "1740729552723", "ltq": "75", "cp": 494.05}

# Upstox instruments-file JSON record (official docs sample, JOCIL).
UPSTOX_MASTER_JOCIL = {
    "segment": "NSE_EQ", "name": "JOCIL LIMITED", "exchange": "NSE",
    "isin": "INE839G01010", "instrument_type": "EQ",
    "instrument_key": "NSE_EQ|INE839G01010", "lot_size": 1,
    "freeze_quantity": 100000.0, "exchange_token": "16927",
    "tick_size": 5.0, "tradingsymbol": "JOCIL", "short_name": "JOCIL",
    "security_type": "NORMAL", "cas_eligible": True,
}


# ---------------------------------------------------------------------------
# MN-C — shared primitives
# ---------------------------------------------------------------------------


def test_common_timestamps(runner: R) -> None:
    """MN-C1: parse_timestamp units, UTC normalization, strict rejection."""
    name = "MN-C1-timestamps"
    from market.normalize.common import TimestampError, parse_timestamp

    runner.assert_eq(name + "-iso-offset",
                     parse_timestamp("2023-10-19T05:21:51.099+05:30", unit="iso"),
                     datetime(2023, 10, 18, 23, 51, 51, 99000, tzinfo=UTC))
    runner.assert_eq(name + "-iso-z",
                     parse_timestamp("2024-01-02T03:04:05Z", unit="iso"),
                     datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC))
    runner.assert_eq(name + "-ms-int",
                     parse_timestamp(1740729552723, unit="ms"),
                     datetime(2025, 2, 28, 7, 59, 12, 723000, tzinfo=UTC))
    runner.assert_eq(name + "-ms-str",
                     parse_timestamp("1740729552723", unit="ms"),
                     datetime(2025, 2, 28, 7, 59, 12, 723000, tzinfo=UTC))
    runner.assert_eq(name + "-s-int",
                     parse_timestamp(1755936000, unit="s"),
                     datetime(2025, 8, 23, 8, 0, 0, tzinfo=UTC))
    runner.assert_eq(name + "-s-str",
                     parse_timestamp("1755936000", unit="s"),
                     datetime(2025, 8, 23, 8, 0, 0, tzinfo=UTC))

    _expect_raises(runner, name + "-naive-rejected", TimestampError,
                   lambda: parse_timestamp("2023-10-19T05:21:51", unit="iso"),
                   needle="naive")
    _expect_raises(runner, name + "-bool-rejected", TimestampError,
                   lambda: parse_timestamp(True, unit="ms"))
    _expect_raises(runner, name + "-garbage-rejected", TimestampError,
                   lambda: parse_timestamp("not-a-time", unit="iso"))
    _expect_raises(runner, name + "-alpha-epoch-rejected", TimestampError,
                   lambda: parse_timestamp("17x43", unit="ms"))
    _expect_raises(runner, name + "-unknown-unit", TimestampError,
                   lambda: parse_timestamp(123, unit="minutes"))
    _expect_raises(runner, name + "-none-rejected", TimestampError,
                   lambda: parse_timestamp(None, unit="s"))


def test_common_numerics(runner: R) -> None:
    """MN-C2/C3: numeric coercion — locked rules #2/#3/#4."""
    name = "MN-C2-float"
    from market.normalize.common import NumericError, to_float

    runner.assert_eq(name + "-none", to_float(None, field="f"), None)
    runner.assert_eq(name + "-blank-str", to_float("   ", field="f"), None)
    runner.assert_eq(name + "-zero-str", to_float("0", field="f"), 0.0)
    runner.assert_eq(name + "-str", to_float("12.5", field="f"), 12.5)
    runner.assert_eq(name + "-int", to_float(7, field="f"), 7.0)
    _expect_raises(runner, name + "-bool-rejected", NumericError,
                   lambda: to_float(True, field="f"), needle="bool")
    _expect_raises(runner, name + "-malformed", NumericError,
                   lambda: to_float("abc", field="f"), needle="malformed")
    _expect_raises(runner, name + "-nan", NumericError,
                   lambda: to_float(float("nan"), field="f"), needle="non-finite")
    _expect_raises(runner, name + "-inf", NumericError,
                   lambda: to_float(math.inf, field="f"), needle="non-finite")
    _expect_raises(runner, name + "-list", NumericError,
                   lambda: to_float([1], field="f"))

    name = "MN-C3-int"
    from market.normalize.common import to_int

    runner.assert_eq(name + "-none", to_int(None, field="f"), None)
    runner.assert_eq(name + "-blank-str", to_int("", field="f"), None)
    runner.assert_eq(name + "-str", to_int("250", field="f"), 250)
    runner.assert_eq(name + "-int", to_int(250, field="f"), 250)
    runner.assert_eq(name + "-integral-float", to_int(250.0, field="f"), 250)
    runner.assert_eq(name + "-integral-float-str", to_int("250.0", field="f"), 250)
    _expect_raises(runner, name + "-fractional", NumericError,
                   lambda: to_int(250.5, field="f"), needle="non-integral")
    _expect_raises(runner, name + "-bool-rejected", NumericError,
                   lambda: to_int(False, field="f"), needle="bool")
    _expect_raises(runner, name + "-malformed", NumericError,
                   lambda: to_int("12x", field="f"))


def test_common_field_map_contract(runner: R) -> None:
    """MN-C4..C6: presence semantics, derived change, canonical-key guard."""
    name = "MN-C4-presence"
    from market.normalize.common import (
        NormalizationError,
        apply_derived_change,
        check_quote_fields,
        set_reported,
        to_float,
    )

    payload = {"a": 1.5, "b": None}
    fields: dict = {}
    set_reported(fields, payload, "a", "ltp", to_float)
    set_reported(fields, payload, "b", "open", to_float)
    set_reported(fields, payload, "missing", "high", to_float)
    runner.assert_eq(name + "-present-value", fields.get("ltp"), 1.5)
    runner.assert_true(name + "-present-null", "open" in fields and fields["open"] is None,
                       "explicit null must stay present as None")
    runner.assert_true(name + "-absent-stays-absent", "high" not in fields,
                       "missing source key must not create a key")

    name = "MN-C5-derived"
    f = {"ltp": 110.0, "close": 100.0}
    apply_derived_change(f)
    runner.assert_eq(name + "-change", f.get("change"), 10.0)
    runner.assert_eq(name + "-pct", f.get("change_percent"), 10.0)

    f = {"ltp": 110.0, "close": 100.0, "change": 5.0}
    apply_derived_change(f)
    runner.assert_eq(name + "-explicit-wins", f.get("change"), 5.0)
    runner.assert_eq(name + "-pct-still-derived", f.get("change_percent"), 10.0)

    f = {"ltp": 110.0, "close": 100.0, "change_percent": 9.0}
    apply_derived_change(f)
    runner.assert_eq(name + "-explicit-pct-wins", f.get("change_percent"), 9.0)
    runner.assert_eq(name + "-change-derived", f.get("change"), 10.0)

    f = {"ltp": 110.0, "close": 0}
    apply_derived_change(f)
    runner.assert_true(name + "-zero-close-skipped",
                       "change" not in f and "change_percent" not in f,
                       "previous_close == 0 must block derivation")

    f = {"close": 100.0}
    apply_derived_change(f)
    runner.assert_true(name + "-no-ltp-skipped", "change" not in f,
                       "derivation requires ltp")

    f = {"ltp": 110.0, "close": 100.0, "change": None}
    apply_derived_change(f)
    runner.assert_true(name + "-explicit-null-blocks", f["change"] is None,
                       "explicit null counts as reported and blocks derivation")

    name = "MN-C6-guard"
    _expect_raises(runner, name + "-unknown-key", NormalizationError,
                   lambda: check_quote_fields({"bogus_field": 1}),
                   needle="bogus_field")
    check_quote_fields({"ltp": 1.0})  # valid map must not raise
    runner.ok(name + "-valid-map-ok")


# ---------------------------------------------------------------------------
# MN-U — Upstox normalizers
# ---------------------------------------------------------------------------


def test_upstox_quote_from_rest(runner: R) -> None:
    """MN-U1/U2: REST full quote mapping (real docs sample) + derived change."""
    name = "MN-U1-rest-quote"
    from market.normalize import upstox_quote_from_rest

    q = upstox_quote_from_rest(UPSTOX_NHPC, received_ts=AWARE_UTC)
    runner.assert_eq(name + "-token", q.instrument_token, "NSE_EQ|INE848E01016")
    runner.assert_eq(name + "-exchange", q.exchange, "NSE")
    runner.assert_eq(name + "-symbol", q.tradingsymbol, "NHPC")
    runner.assert_eq(name + "-received", q.received_ts, AWARE_UTC)
    runner.assert_eq(name + "-ltp", q.ltp, 52.04999923706055)
    runner.assert_eq(name + "-ohlc", (q.open, q.high, q.low, q.close),
                     (53.4, 53.8, 51.75, 52.05))
    runner.assert_eq(name + "-volume", q.volume, 24123697)
    runner.assert_eq(name + "-explicit-change", q.change, -1.0500000000000043)
    expected_pct = ((52.04999923706055 - 52.05) / 52.05) * 100.0
    runner.assert_eq(name + "-derived-pct", q.change_percent, expected_pct)
    runner.assert_eq(name + "-avg-trade", q.avg_trade_price, 52.56)
    runner.assert_eq(name + "-oi-literal-zero", q.open_interest, 0.0)
    runner.assert_eq(name + "-totals", (q.total_buy_qty, q.total_sell_qty), (6917, 0))
    runner.assert_eq(name + "-best-bid", q.best_bid, 52.05)
    runner.assert_true(name + "-best-ask-absent", q.best_ask is None,
                       "all-zero sell side yields no best_ask")
    runner.assert_eq(name + "-exchange-ts",
                     q.exchange_ts,
                     datetime(2023, 10, 18, 23, 51, 51, 99000, tzinfo=UTC))

    name = "MN-U2-derived-change"
    stripped = dict(UPSTOX_NHPC)
    stripped.pop("net_change")
    q2 = upstox_quote_from_rest(stripped, received_ts=AWARE_UTC)
    runner.assert_eq(name + "-change-derived", q2.change, 52.04999923706055 - 52.05)
    expected_pct2 = ((52.04999923706055 - 52.05) / 52.05) * 100.0
    runner.assert_eq(name + "-pct-derived", q2.change_percent, expected_pct2)


def test_upstox_quote_error_paths(runner: R) -> None:
    """MN-U3/U4: malformed numerics never become 0; identity is enforced."""
    name = "MN-U3-malformed"
    from market.normalize import NumericError, upstox_quote_from_rest

    bad = dict(UPSTOX_NHPC)
    bad["last_price"] = "abc"
    _expect_raises(runner, name + "-bad-string", NumericError,
                   lambda: upstox_quote_from_rest(bad, received_ts=AWARE_UTC),
                   needle="malformed")

    bad = dict(UPSTOX_NHPC)
    bad["volume"] = True
    _expect_raises(runner, name + "-bool-volume", NumericError,
                   lambda: upstox_quote_from_rest(bad, received_ts=AWARE_UTC),
                   needle="bool")

    bad = dict(UPSTOX_NHPC)
    bad["average_price"] = float("nan")
    _expect_raises(runner, name + "-nan", NumericError,
                   lambda: upstox_quote_from_rest(bad, received_ts=AWARE_UTC),
                   needle="non-finite")

    name = "MN-U4-identity"
    from market.normalize import NormalizationError, upstox_quote_from_rest

    nosym = dict(UPSTOX_NHPC)
    nosym.pop("symbol")
    _expect_raises(runner, name + "-missing-symbol", NormalizationError,
                   lambda: upstox_quote_from_rest(nosym, received_ts=AWARE_UTC))


def test_upstox_depth(runner: R) -> None:
    """MN-U5/U6: zero-fill dropped, zero quantity kept, absent key -> None."""
    name = "MN-U5-depth"
    from market.normalize import upstox_depth_from_rest

    d = upstox_depth_from_rest(UPSTOX_NHPC, received_ts=AWARE_UTC)
    runner.assert_true(name + "-present", d is not None)
    assert d is not None  # narrowing for type checkers
    runner.assert_eq(name + "-bids-len", len(d.bids), 1)
    runner.assert_eq(name + "-asks-len", len(d.asks), 0)
    runner.assert_eq(name + "-bid-level",
                     (d.bids[0].price, d.bids[0].quantity, d.bids[0].orders),
                     (52.05, 6917.0, 20))
    runner.assert_eq(name + "-identity",
                     (d.instrument_token, d.exchange, d.tradingsymbol),
                     ("NSE_EQ|INE848E01016", "NSE", "NHPC"))

    zero_qty = dict(UPSTOX_NHPC)
    zero_qty["depth"] = {
        "buy": [{"quantity": 0, "price": 100.5, "orders": 1}],
        "sell": [{"quantity": 25, "price": 0, "orders": 0}],  # placeholder dropped
    }
    d2 = upstox_depth_from_rest(zero_qty, received_ts=AWARE_UTC)
    assert d2 is not None
    runner.assert_eq(name + "-zero-qty-kept",
                     (len(d2.bids), d2.bids[0].quantity), (1, 0.0))
    runner.assert_eq(name + "-placeholder-dropped", len(d2.asks), 0)

    name = "MN-U6-absent"
    nodepth = dict(UPSTOX_NHPC)
    nodepth.pop("depth")
    runner.assert_eq(name + "-none",
                     upstox_depth_from_rest(nodepth, received_ts=AWARE_UTC), None)


def test_upstox_ws_patches(runner: R) -> None:
    """MN-U7/U8: presence-exact WS field maps; proto3 0-means-not-reported."""
    name = "MN-U7-ltpc"
    from market.normalize import upstox_quote_fields_from_ws_ltpc

    fields = upstox_quote_fields_from_ws_ltpc(
        UPSTOX_LTPC, instrument_key="NSE_FO|45450", received_ts=AWARE_UTC
    )
    runner.assert_eq(name + "-exact-keys", sorted(fields.keys()),
                     sorted(["instrument_token", "exchange", "received_ts",
                             "ltp", "close", "last_traded_qty", "exchange_ts",
                             "change", "change_percent"]))
    runner.assert_eq(name + "-token", fields["instrument_token"], "NSE_FO|45450")
    runner.assert_eq(name + "-exchange", fields["exchange"], "NSE")
    runner.assert_eq(name + "-ltp", fields["ltp"], 219.3)
    runner.assert_eq(name + "-close-is-prev-close", fields["close"], 494.05)
    runner.assert_eq(name + "-ltq-int", fields["last_traded_qty"], 75)
    runner.assert_eq(name + "-ltt-ms", fields["exchange_ts"],
                     datetime(2025, 2, 28, 7, 59, 12, 723000, tzinfo=UTC))
    runner.assert_true(name + "-no-symbol-unless-resolved",
                       "tradingsymbol" not in fields,
                       "wire carries no symbol; adapter resolves it")

    resolved = upstox_quote_fields_from_ws_ltpc(
        UPSTOX_LTPC, instrument_key="NSE_FO|45450",
        received_ts=AWARE_UTC, tradingsymbol="BANKNIFTY24JAN55000CE",
    )
    runner.assert_eq(name + "-resolved-symbol", resolved.get("tradingsymbol"),
                     "BANKNIFTY24JAN55000CE")

    name = "MN-U8-full"
    from market.normalize import upstox_quote_fields_from_ws_full

    ff = {
        "ltpc": {"ltp": 141.0, "ltt": "1740729552723", "cp": 233.95},
        "atp": 0, "vtt": 0, "oi": 0, "tbq": 0, "tsq": 0,
        "marketLevel": {"bidAskQuote": [
            {"bidQ": 600, "bidP": 141, "askQ": 50, "askP": 141.35},
        ]},
    }
    fields = upstox_quote_fields_from_ws_full(
        ff, instrument_key="NSE_FO|45450", received_ts=AWARE_UTC
    )
    for absent in ("avg_trade_price", "volume", "open_interest",
                   "total_buy_qty", "total_sell_qty"):
        runner.assert_true(name + f"-zero-absent-{absent}", absent not in fields,
                           "proto3 zero must mean not-reported")
    runner.assert_eq(name + "-best-bid", fields.get("best_bid"), 141.0)
    runner.assert_eq(name + "-best-ask", fields.get("best_ask"), 141.35)

    ff2 = dict(ff)
    ff2.update({"vtt": 1234, "oi": 15000, "tbq": 600, "tsq": 50})
    fields2 = upstox_quote_fields_from_ws_full(
        ff2, instrument_key="NSE_FO|45450", received_ts=AWARE_UTC
    )
    runner.assert_eq(name + "-vtt-volume", fields2.get("volume"), 1234)
    runner.assert_eq(name + "-oi-kept", fields2.get("open_interest"), 15000.0)
    runner.assert_eq(name + "-totals",
                     (fields2.get("total_buy_qty"), fields2.get("total_sell_qty")),
                     (600, 50))


def test_upstox_ws_depth_and_master(runner: R) -> None:
    """MN-U9/U10: WS depth split + instrument master records."""
    name = "MN-U9-ws-depth"
    from market.normalize import NormalizationError, upstox_depth_from_ws

    ml = {"bidAskQuote": [
        {"bidQ": 600, "bidP": 141, "askQ": 50, "askP": 141.35},
        {"bidQ": 625, "bidP": 0, "askQ": 25, "askP": 141.45},  # placeholder bidP
    ]}
    d = upstox_depth_from_ws(ml, instrument_key="NSE_FO|45450",
                             tradingsymbol="BANKNIFTY", received_ts=AWARE_UTC)
    runner.assert_eq(name + "-bids",
                     [(l.price, l.quantity, l.orders) for l in d.bids],
                     [(141.0, 600.0, None)])
    runner.assert_eq(name + "-asks",
                     [(l.price, l.quantity, l.orders) for l in d.asks],
                     [(141.35, 50.0, None), (141.45, 25.0, None)])
    _expect_raises(runner, name + "-symbol-required", NormalizationError,
                   lambda: upstox_depth_from_ws(ml, instrument_key="NSE_FO|45450",
                                                tradingsymbol="", received_ts=AWARE_UTC))

    name = "MN-U10-master"
    from market.normalize import upstox_instrument_from_master

    inst = upstox_instrument_from_master(UPSTOX_MASTER_JOCIL)
    runner.assert_eq(name + "-identity",
                     (inst.instrument_token, inst.exchange, inst.tradingsymbol),
                     ("NSE_EQ|INE839G01010", "NSE", "JOCIL"))
    runner.assert_eq(name + "-name", inst.name, "JOCIL LIMITED")
    runner.assert_eq(name + "-type", inst.instrument_type, "EQ")
    runner.assert_eq(name + "-tick-lot", (inst.tick_size, inst.lot_size), (5.0, 1))
    runner.assert_eq(name + "-expiry-none", inst.expiry, None)
    runner.assert_eq(name + "-strike-none", inst.strike, None)

    deriv = upstox_instrument_from_master({
        "instrument_key": "NSE_FO|45450", "exchange": "NSE_FO",
        "tradingsymbol": "BANKNIFTY24JAN55000CE", "instrument_type": "OPTSTK",
        "expiry": "2024-01-25", "strike": 1840.0, "tick_size": 0.05, "lot_size": 25,
    })
    runner.assert_eq(name + "-deriv-expiry", deriv.expiry,
                     datetime(2024, 1, 25, tzinfo=UTC))
    runner.assert_eq(name + "-deriv-strike", deriv.strike, 1840.0)
    runner.assert_eq(name + "-deriv-exchange-prefix", deriv.exchange, "NSE")


# ---------------------------------------------------------------------------
# MN-P — Upstox V3 protobuf protocol layer (vendored bindings + P-ZERO)
# ---------------------------------------------------------------------------


def test_protobuf_layer(runner: R) -> None:
    """MN-P1..P6: vendored bindings, presence-exact decode, P-ZERO policy."""
    from brokers.upstox.feed_protocol import (
        ProtobufDecodeError,
        decode_feed_response,
        feed_type,
        iter_instrument_feeds,
        which_feed_union,
    )
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    from market.normalize import upstox_quote_fields_from_ws_full

    name = "MN-P1-market-info"
    r = pb.FeedResponse()
    r.type = pb.Type.Value("market_info")
    r.currentTs = 1732775008661
    r.marketInfo.segmentStatus["NSE_EQ"] = pb.MarketStatus.Value("NORMAL_OPEN")
    r.marketInfo.segmentStatus["NSE_FO"] = pb.MarketStatus.Value("NORMAL_CLOSE")
    resp = decode_feed_response(r.SerializeToString())
    runner.assert_eq(name + "-type", feed_type(resp), "market_info")
    runner.assert_eq(name + "-currentts", resp.currentTs, 1732775008661)
    runner.assert_eq(name + "-segment-status",
                     dict(resp.marketInfo.segmentStatus),
                     {"NSE_EQ": 2, "NSE_FO": 3})  # NORMAL_OPEN / NORMAL_CLOSE
    runner.assert_eq(name + "-no-feeds", len(list(iter_instrument_feeds(resp))), 0)

    name = "MN-P2-snapshot"
    r = pb.FeedResponse()
    r.type = pb.Type.Value("initial_feed")
    r.currentTs = 1740729566039
    f = r.feeds["NSE_FO|45450"].fullFeed.marketFF
    f.ltpc.ltp = 219.3; f.ltpc.ltt = 1740729552723; f.ltpc.ltq = 75; f.ltpc.cp = 494.05
    lvl = f.marketLevel.bidAskQuote.add()
    lvl.bidQ = 600; lvl.bidP = 141.0; lvl.askQ = 50; lvl.askP = 141.35
    f.vtt = 1234; f.oi = 15000.0; f.tbq = 600; f.tsq = 50
    resp = decode_feed_response(r.SerializeToString())
    runner.assert_eq(name + "-type", feed_type(resp), "initial_feed")
    pairs = list(iter_instrument_feeds(resp))
    runner.assert_eq(name + "-one-feed", len(pairs), 1)
    key, fd = pairs[0]
    runner.assert_eq(name + "-key", key, "NSE_FO|45450")
    runner.assert_eq(name + "-union", which_feed_union(resp.feeds[key]), "fullFeed")
    mff = fd["fullFeed"]["marketFF"]
    fields = upstox_quote_fields_from_ws_full(
        mff, instrument_key=key, received_ts=AWARE_UTC
    )
    runner.assert_eq(name + "-ltp", fields["ltp"], 219.3)
    runner.assert_eq(name + "-close-is-prev-close", fields["close"], 494.05)
    runner.assert_eq(name + "-volume", fields["volume"], 1234)
    runner.assert_eq(name + "-oi", fields["open_interest"], 15000.0)
    runner.assert_eq(name + "-totals",
                     (fields["total_buy_qty"], fields["total_sell_qty"]), (600, 50))
    runner.assert_eq(name + "-best", (fields["best_bid"], fields["best_ask"]),
                     (141.0, 141.35))
    runner.assert_eq(name + "-exchange-ts", fields["exchange_ts"],
                     datetime(2025, 2, 28, 7, 59, 12, 723000, tzinfo=UTC))

    name = "MN-P3-pzero"
    rz = pb.FeedResponse()
    fz = rz.feeds["NSE_FO|45450"].fullFeed.marketFF
    fz.atp = 52.5; fz.vtt = 1234; fz.oi = 0.0          # explicit zero
    rn = pb.FeedResponse()
    fn = rn.feeds["NSE_FO|45450"].fullFeed.marketFF
    fn.atp = 52.5; fn.vtt = 1234                        # oi unset
    runner.assert_eq(name + "-wire-identical",
                     rz.SerializeToString() == rn.SerializeToString(), True)
    _, fdz = list(iter_instrument_feeds(decode_feed_response(rz.SerializeToString())))[0]
    fields_z = upstox_quote_fields_from_ws_full(
        fdz["fullFeed"]["marketFF"], instrument_key="NSE_FO|45450",
        received_ts=AWARE_UTC,
    )
    runner.assert_true(name + "-zero-not-reported",
                       "open_interest" not in fields_z,
                       "decoded-zero oi must be absent from the field map (P-ZERO)")

    name = "MN-P4-ltpc-live"
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    t = r.feeds["NSE_EQ|INE002A01018"].ltpc
    t.ltp = 101.25; t.ltt = 1740729552723; t.ltq = 10; t.cp = 100.0
    resp = decode_feed_response(r.SerializeToString())
    key, fd = list(iter_instrument_feeds(resp))[0]
    runner.assert_eq(name + "-union", which_feed_union(resp.feeds[key]), "ltpc")
    from market.normalize import upstox_quote_fields_from_ws_ltpc
    fields = upstox_quote_fields_from_ws_ltpc(
        fd["ltpc"], instrument_key=key, received_ts=AWARE_UTC
    )
    runner.assert_eq(name + "-ltp", fields["ltp"], 101.25)
    runner.assert_eq(name + "-derived-change", round(fields["change"], 6), 1.25)

    name = "MN-P5-index"
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    idx = r.feeds["NSE_INDEX|Nifty 50"].fullFeed.indexFF
    idx.ltpc.ltp = 22000.5; idx.ltpc.cp = 21900.0
    key, fd = list(iter_instrument_feeds(decode_feed_response(r.SerializeToString())))[0]
    ff_branches = fd.get("fullFeed") or {}
    runner.assert_true(name + "-index-branch",
                       "indexFF" in ff_branches and "marketFF" not in ff_branches,
                       "index instruments arrive on the indexFF branch")
    fields = upstox_quote_fields_from_ws_full(
        ff_branches["indexFF"], instrument_key=key, received_ts=AWARE_UTC
    )
    runner.assert_eq(name + "-index-ltp", fields["ltp"], 22000.5)
    runner.assert_true(name + "-index-no-depth",
                       "best_bid" not in fields and "best_ask" not in fields,
                       "index feeds carry no depth")

    name = "MN-P6-malformed"
    _expect_raises(runner, name + "-empty", ProtobufDecodeError,
                   lambda: decode_feed_response(b""))
    _expect_raises(runner, name + "-truncated", ProtobufDecodeError,
                   lambda: decode_feed_response(b"\x0a\x05ab"))


# ---------------------------------------------------------------------------
# MN-F — FYERS normalizers
# ---------------------------------------------------------------------------

FYERS_V = {
    "lp": 740.5, "open_price": 735.0, "high_price": 742.0, "low_price": 733.2,
    "prev_close_price": 735.25, "ch": 5.25, "chp": 0.71, "volume": 123456,
    "bid": 740.45, "ask": 740.55, "atp": 738.9, "tt": 1755936000,
    "fyToken": "11213", "spread": "0.10",  # no canonical home -> dropped
}


def test_fyers_quotes_rest(runner: R) -> None:
    """MN-F1/F2: REST snapshot mapping; explicit ch/chp beat derivation."""
    name = "MN-F1-rest"
    from market.normalize import fyers_quote_from_quotes_rest

    q = fyers_quote_from_quotes_rest(FYERS_V, symbol="NSE:SBIN-EQ",
                                     received_ts=AWARE_UTC)
    runner.assert_eq(name + "-identity",
                     (q.instrument_token, q.exchange, q.tradingsymbol),
                     ("NSE:SBIN-EQ", "NSE", "SBIN-EQ"))
    runner.assert_eq(name + "-ltp", q.ltp, 740.5)
    runner.assert_eq(name + "-ohlc", (q.open, q.high, q.low, q.close),
                     (735.0, 742.0, 733.2, 735.25))
    runner.assert_eq(name + "-explicit-change", (q.change, q.change_percent),
                     (5.25, 0.71))
    runner.assert_eq(name + "-volume", q.volume, 123456)
    runner.assert_eq(name + "-bidask", (q.best_bid, q.best_ask), (740.45, 740.55))
    runner.assert_eq(name + "-atp", q.avg_trade_price, 738.9)
    runner.assert_eq(name + "-tt-epoch-s", q.exchange_ts,
                     datetime(2025, 8, 23, 8, 0, 0, tzinfo=UTC))

    name = "MN-F2-derived"
    stripped = {k: v for k, v in FYERS_V.items() if k not in ("ch", "chp")}
    q2 = fyers_quote_from_quotes_rest(stripped, symbol="NSE:SBIN-EQ",
                                      received_ts=AWARE_UTC)
    runner.assert_eq(name + "-change-derived", q2.change, 740.5 - 735.25)
    expected_pct = ((740.5 - 735.25) / 735.25) * 100.0
    runner.assert_eq(name + "-pct-derived", q2.change_percent, expected_pct)


def test_fyers_symbol_update(runner: R) -> None:
    """MN-F3/F4: presence-exact patches; explicit null vs absent distinction."""
    name = "MN-F3-partial"
    from market.normalize import fyers_quote_fields_from_symbol_update

    msg = {"symbol": "NSE:SBIN-EQ", "ltp": 501.0, "last_traded_time": 1755936001}
    fields = fyers_quote_fields_from_symbol_update(msg, received_ts=AWARE_UTC)
    runner.assert_eq(name + "-exact-keys", sorted(fields.keys()),
                     sorted(["instrument_token", "exchange", "tradingsymbol",
                             "received_ts", "ltp", "exchange_ts"]))
    runner.assert_eq(name + "-ltp", fields["ltp"], 501.0)
    runner.assert_eq(name + "-ts", fields["exchange_ts"],
                     datetime(2025, 8, 23, 8, 0, 1, tzinfo=UTC))

    name = "MN-F4-null-vs-absent"
    msg = {"symbol": "NSE:SBIN-EQ", "ltp": 501.0, "ch": None}
    fields = fyers_quote_fields_from_symbol_update(msg, received_ts=AWARE_UTC)
    runner.assert_true(name + "-explicit-null-present",
                       "change" in fields and fields["change"] is None,
                       "provider-sent null must stay present as None")
    runner.assert_true(name + "-absent-stays-absent", "change_percent" not in fields,
                       "keys the provider omitted must not appear")


def test_fyers_depth_and_master(runner: R) -> None:
    """MN-F5/F6: depth + supplemental map; master records incl. sentinels."""
    name = "MN-F5-depth"
    from market.normalize import fyers_depth_from_rest

    payload = {
        "bids": [
            {"price": 100.5, "volume": 0, "ord": 1},   # zero qty KEPT
            {"price": 0, "volume": 500, "ord": 0},     # placeholder DROPPED
        ],
        "ask": [{"price": 101.0, "volume": 300, "ord": 2}],
        "totalbuyqty": 700, "totalsellqty": 300,
        "ltp": 100.75, "v": 999, "atp": 100.6, "oi": 15000,
    }
    d, supp = fyers_depth_from_rest(payload, symbol="NSE:SBIN-EQ",
                                    received_ts=AWARE_UTC)
    runner.assert_eq(name + "-identity",
                     (d.instrument_token, d.exchange, d.tradingsymbol),
                     ("NSE:SBIN-EQ", "NSE", "SBIN-EQ"))
    runner.assert_eq(name + "-bids",
                     [(l.price, l.quantity, l.orders) for l in d.bids],
                     [(100.5, 0.0, 1)])
    runner.assert_eq(name + "-asks",
                     [(l.price, l.quantity, l.orders) for l in d.asks],
                     [(101.0, 300.0, 2)])
    runner.assert_eq(name + "-supplemental", sorted(supp.keys()),
                     sorted(["total_buy_qty", "total_sell_qty", "ltp",
                             "volume", "avg_trade_price", "open_interest"]))
    runner.assert_eq(name + "-supp-values",
                     (supp["total_buy_qty"], supp["volume"], supp["open_interest"]),
                     (700, 999, 15000.0))

    name = "MN-F6-master"
    from market.normalize import fyers_instrument_from_master

    equity = fyers_instrument_from_master({
        "symTicker": "NSE:SBIN-EQ", "exSymName": "STATE BANK OF INDIA",
        "tickSize": 0.05, "minLotSize": 1, "optType": "XX",
        "strikePrice": -1, "expiryDate": "",
    })
    runner.assert_eq(name + "-equity-identity",
                     (equity.instrument_token, equity.exchange, equity.tradingsymbol),
                     ("NSE:SBIN-EQ", "NSE", "SBIN-EQ"))
    runner.assert_eq(name + "-sentinels-cleared",
                     (equity.instrument_type, equity.strike, equity.expiry),
                     (None, None, None))
    runner.assert_eq(name + "-name", equity.name, "STATE BANK OF INDIA")

    option = fyers_instrument_from_master({
        "symTicker": "NSE:NIFTY26AUG55000CE", "exSymName": "NIFTY 26AUG 55000 CE",
        "tickSize": 0.05, "minLotSize": 75, "optType": "CE",
        "strikePrice": 55000, "expiryDate": 1755936000,
    })
    runner.assert_eq(name + "-option-type", option.instrument_type, "CE")
    runner.assert_eq(name + "-option-strike", option.strike, 55000.0)
    runner.assert_eq(name + "-option-expiry", option.expiry,
                     datetime(2025, 8, 23, 8, 0, 0, tzinfo=UTC))

    name = "MN-F7-symbol-guard"
    from market.normalize import NormalizationError, split_fyers_symbol

    _expect_raises(runner, name + "-no-colon", NormalizationError,
                   lambda: split_fyers_symbol("NSE-SBIN-EQ"))
    runner.assert_eq(name + "-split-ok", split_fyers_symbol(" NSE:SBIN-EQ "),
                     ("NSE", "SBIN-EQ"))


# ---------------------------------------------------------------------------
# MN-S — canonical serialization
# ---------------------------------------------------------------------------


def test_serialization(runner: R) -> None:
    """MN-S1..S4: canonical names/order, ISO-UTC timestamps, arrays, None."""
    name = "MN-S1-quote"
    from market.models import Quote
    from market.normalize import (
        upstox_depth_from_rest,
        upstox_instrument_from_master,
        upstox_quote_from_rest,
    )
    from market.serialization import quote_to_dict

    q = upstox_quote_from_rest(UPSTOX_NHPC, received_ts=AWARE_UTC)
    d = quote_to_dict(q)
    runner.assert_eq(name + "-canonical-names-order",
                     list(d.keys()),
                     [f.name for f in dataclasses.fields(Quote)])
    runner.assert_true(name + "-received-iso-utc",
                       isinstance(d["received_ts"], str)
                       and d["received_ts"].endswith("+00:00"),
                       f"expected ISO-8601 UTC string: {d['received_ts']!r}")
    runner.assert_true(name + "-none-preserved", "best_ask" in d and d["best_ask"] is None,
                       "None values must be preserved verbatim")

    name = "MN-S2-depth"
    from market.serialization import depth_to_dict

    dep = upstox_depth_from_rest(UPSTOX_NHPC, received_ts=AWARE_UTC)
    assert dep is not None
    dd = depth_to_dict(dep)
    runner.assert_true(name + "-tuples-to-lists",
                       isinstance(dd["bids"], list) and isinstance(dd["asks"], list),
                       "depth tuples must serialize as arrays")
    runner.assert_eq(name + "-level-dict", dd["bids"][0],
                     {"price": 52.05, "quantity": 6917.0, "orders": 20})

    name = "MN-S3-instrument"
    from market.serialization import instrument_to_dict

    inst = upstox_instrument_from_master(UPSTOX_MASTER_JOCIL)
    idict = instrument_to_dict(inst)
    runner.assert_eq(name + "-spot", (idict["instrument_token"], idict["tick_size"]),
                     ("NSE_EQ|INE839G01010", 5.0))
    runner.assert_eq(name + "-expiry-none", idict["expiry"], None)

    name = "MN-S4-non-model"
    from market.serialization import quote_to_dict as qtd

    _expect_raises(runner, name + "-type-error", TypeError,
                   lambda: qtd({"not": "a model"}))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> bool:
    runner = R()

    # Shared primitives
    test_common_timestamps(runner)
    test_common_numerics(runner)
    test_common_field_map_contract(runner)

    # Upstox
    test_upstox_quote_from_rest(runner)
    test_upstox_quote_error_paths(runner)
    test_upstox_depth(runner)
    test_upstox_ws_patches(runner)
    test_upstox_ws_depth_and_master(runner)
    test_protobuf_layer(runner)

    # FYERS
    test_fyers_quotes_rest(runner)
    test_fyers_symbol_update(runner)
    test_fyers_depth_and_master(runner)

    # Serialization
    test_serialization(runner)

    return runner.summary()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
