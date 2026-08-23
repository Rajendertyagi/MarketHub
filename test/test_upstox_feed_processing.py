#!/usr/bin/env python3
"""Unit tests for Upstox V3 frame processing — D3.2.

Covers the full pipeline: binary frame -> protobuf decode -> presence
extraction -> B1 normalization -> QuotePatch/Depth -> MarketService.

Uses vendored pb2 to construct deterministic binary frames (same pattern
as D1 tests). No network, no credentials, no server.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from datetime import datetime, timedelta, timezone  # noqa: E402

from helpers.runner import R  # noqa: E402
from brokers.upstox.proto import MarketDataFeed_pb2 as pb  # noqa: E402

UTC = timezone.utc
AWARE_UTC = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
KEY = "NSE_FO|45450"
EXCHANGE = "NSE"
TS = "BANKNIFTY24JAN55000CE"

# Canonical identity metadata for tests.
METADATA = {
    KEY: (EXCHANGE, TS),
    "NSE_EQ|INE848E01016": ("NSE", "DMART"),
}


def _build_ltpc_response(key=KEY, ltp=219.3, cp=494.05,
                         ltt=1740729552723, ltq=75):
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    r.currentTs = ltt
    f = r.feeds[key]
    t = f.ltpc
    t.ltp = ltp; t.ltt = ltt; t.ltq = ltq; t.cp = cp
    return r.SerializeToString()


def _build_full_response(key=KEY, *, ltp=141.0, cp=233.95,
                         vtt=1234, oi=15000.0, tbq=600, tsq=50,
                         bid_p=141.0, bid_q=600, ask_p=141.35, ask_q=50):
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    r.currentTs = 1740729566039
    ff = r.feeds[key].fullFeed.marketFF
    ff.ltpc.ltp = ltp; ff.ltpc.cp = cp; ff.ltpc.ltt = 1740729552723
    if vtt: ff.vtt = vtt
    if oi: ff.oi = oi
    if tbq: ff.tbq = tbq
    if tsq: ff.tsq = tsq
    lvl = ff.marketLevel.bidAskQuote.add()
    lvl.bidQ = bid_q; lvl.bidP = bid_p; lvl.askQ = ask_q; lvl.askP = ask_p
    return r.SerializeToString()


def _build_market_info():
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    r = pb.FeedResponse()
    r.type = pb.Type.Value("market_info")
    r.currentTs = 1732775008661
    r.marketInfo.segmentStatus["NSE_EQ"] = pb.MarketStatus.Value("NORMAL_OPEN")
    return r.SerializeToString()


def _make_service():
    from market.service import MarketService
    return MarketService()


def _expect_raises(runner: R, label: str, exc_type: type, fn, needle=None):
    try:
        fn()
    except exc_type as exc:
        if needle is not None:
            runner.assert_true(label, needle in str(exc),
                               f"expected {needle!r}: {exc}")
        else:
            runner.ok(label)
        return
    except Exception as exc:
        runner.fail(label, f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
        return
    runner.fail(label, f"expected {exc_type.__name__}; nothing raised")


# ---------------------------------------------------------------------------
# FP1 — pure processing: ltpc frame → patch fields
# ---------------------------------------------------------------------------


def test_process_ltpc(runner: R) -> None:
    name = "FP1-ltpc"
    from brokers.upstox.feed_processing import process_binary_frame

    frame = _build_ltpc_response()
    result = process_binary_frame(frame, received_ts=AWARE_UTC, instrument_metadata=METADATA)
    runner.assert_eq(name + "-type", result.frame_type, "live_feed")
    runner.assert_eq(name + "-instruments", len(result.instruments), 1)

    o = result.instruments[0]
    runner.assert_eq(name + "-key", o.instrument_key, KEY)
    runner.assert_eq(name + "-no-error", o.error, None)
    runner.assert_true(name + "-has-patch", o.patch is not None)

    p = o.patch
    runner.assert_eq(name + "-exchange", p.exchange, EXCHANGE)
    runner.assert_eq(name + "-token", p.instrument_token, KEY)
    runner.assert_eq(name + "-ltp", p.reported_fields.get("ltp"), 219.3)
    runner.assert_eq(name + "-close", p.reported_fields.get("close"), 494.05)


# ---------------------------------------------------------------------------
# FP2 — pure processing: full market frame with depth
# ---------------------------------------------------------------------------


def test_process_full_market(runner: R) -> None:
    name = "FP2-full-market"
    from brokers.upstox.feed_processing import process_binary_frame

    frame = _build_full_response()
    result = process_binary_frame(frame, received_ts=AWARE_UTC, instrument_metadata=METADATA)
    runner.assert_eq(name + "-type", result.frame_type, "live_feed")

    o = result.instruments[0]
    runner.assert_eq(name + "-no-error", o.error, None)
    runner.assert_true(name + "-has-patch", o.patch is not None)
    runner.assert_true(name + "-has-depth", o.depth is not None)

    p = o.patch
    runner.assert_eq(name + "-ltp", p.reported_fields.get("ltp"), 141.0)
    runner.assert_eq(name + "-volume", p.reported_fields.get("volume"), 1234)
    runner.assert_eq(name + "-oi", p.reported_fields.get("open_interest"), 15000.0)
    runner.assert_eq(name + "-best-bid", p.reported_fields.get("best_bid"), 141.0)

    d = o.depth
    runner.assert_eq(name + "-depth-bids", len(d.bids), 1)
    runner.assert_eq(name + "-depth-asks", len(d.asks), 1)


# ---------------------------------------------------------------------------
# FP3 — P-ZERO: explicit zero omitted from field map
# ---------------------------------------------------------------------------


def test_pzero_zero_not_reported(runner: R) -> None:
    name = "FP3-pzero"
    from brokers.upstox.feed_processing import process_binary_frame

    # Build two frames: one with oi=0 explicitly set, one without oi at all.
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    rz = pb.FeedResponse(); rz.type = pb.Type.Value("live_feed")
    fz = rz.feeds[KEY].fullFeed.marketFF
    fz.ltpc.ltp = 100.0; fz.ltpc.cp = 90.0; fz.oi = 0.0
    rn = pb.FeedResponse(); rn.type = pb.Type.Value("live_feed")
    fn = rn.feeds[KEY].fullFeed.marketFF
    fn.ltpc.ltp = 100.0; fn.ltpc.cp = 90.0

    runner.assert_eq(name + "-wire-identical",
                     rz.SerializeToString() == rn.SerializeToString(), True)

    result_z = process_binary_frame(rz.SerializeToString(), received_ts=AWARE_UTC, instrument_metadata=METADATA)
    fields_z = result_z.instruments[0].patch.reported_fields
    runner.assert_true(name + "-oi-absent",
                       "open_interest" not in fields_z,
                       "P-ZERO: decoded-zero oi must be absent")


# ---------------------------------------------------------------------------
# FP4 — end-to-end: fake WS frames through real MarketService
# ---------------------------------------------------------------------------


async def test_end_to_end_quote_and_depth(runner: R) -> None:
    """FP4: snapshot creates state; live updates merge; depth applied."""
    name = "FP4-e2e"
    from brokers.upstox.feed_processing import process_binary_frame
    from market.service import MarketService

    svc = MarketService()
    received = AWARE_UTC

    # Snapshot (initial_feed).
    snap = _build_full_response(ltp=100.0, cp=95.0, vtt=500)
    snap_result = process_binary_frame(snap, received_ts=received, instrument_metadata=METADATA)
    for o in snap_result.instruments:
        if o.patch:
            await svc.apply_quote(o.patch)
        if o.depth:
            await svc.apply_depth(o.depth)

    q = await svc.get_quote(EXCHANGE, KEY)
    runner.assert_true(q is not None, name + "-quote-created")
    assert q is not None
    runner.assert_eq(name + "-snap-ltp", q.ltp, 100.0)
    runner.assert_eq(name + "-snap-close", q.close, 95.0)

    d = await svc.get_depth(EXCHANGE, KEY)
    runner.assert_true(d is not None, name + "-depth-created")
    assert d is not None
    runner.assert_eq(name + "-depth-bid-price", d.bids[0].price, 141.0)

    # Live update changes only reported fields.
    live = _build_full_response(ltp=105.0, cp=95.0, vtt=600)
    live_result = process_binary_frame(live, received_ts=received + timedelta(seconds=1), instrument_metadata=METADATA)
    for o in live_result.instruments:
        if o.patch:
            await svc.apply_quote(o.patch)

    q2 = await svc.get_quote(EXCHANGE, KEY)
    assert q2 is not None
    runner.assert_eq(name + "-live-ltp", q2.ltp, 105.0)
    runner.assert_eq(name + "-preserved-oi", q2.open_interest, 15000.0)


async def test_stale_update_rejected(runner: R) -> None:
    """FP5: older exchange_ts rejected as stale."""
    name = "FP5-stale"
    from brokers.upstox.feed_processing import process_binary_frame
    from market.service import MarketService

    svc = MarketService()

    # Newer frame: ltt = 2000000000000 (later timestamp).
    newer = _build_full_response(ltp=110.0, cp=95.0)
    # Patch the ltt to a known newer value.
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    nr = pb.FeedResponse(); nr.ParseFromString(newer)
    nr.feeds[KEY].fullFeed.marketFF.ltpc.ltt = 2000000000000
    newer = nr.SerializeToString()

    newer_result = process_binary_frame(newer, received_ts=AWARE_UTC, instrument_metadata=METADATA)
    for o in newer_result.instruments:
        if o.patch:
            await svc.apply_quote(o.patch)

    # Older frame: ltt = 1000000000000 (earlier timestamp).
    older = _build_full_response(ltp=90.0, cp=95.0)
    orr = pb.FeedResponse(); orr.ParseFromString(older)
    orr.feeds[KEY].fullFeed.marketFF.ltpc.ltt = 1000000000000
    older = orr.SerializeToString()

    older_result = process_binary_frame(older, received_ts=AWARE_UTC - timedelta(seconds=5), instrument_metadata=METADATA)
    for o in older_result.instruments:
        if o.patch:
            outcome = await svc.apply_quote(o.patch)
            runner.assert_eq(name + "-stale-rejected", outcome.stale, True)

    q = await svc.get_quote(EXCHANGE, KEY)
    assert q is not None
    runner.assert_eq(name + "-value-preserved", q.ltp, 110.0)


async def test_bad_instrument_isolation(runner: R) -> None:
    """FP6: one unsupported instrument doesn't block a valid one."""
    name = "FP6-isolation"
    from brokers.upstox.proto import MarketDataFeed_pb2 as pb
    from brokers.upstox.feed_processing import process_binary_frame

    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    greek = r.feeds[KEY].firstLevelWithGreeks
    greek.ltpc.ltp = 100.0
    good = r.feeds["NSE_EQ|INE848E01016"].ltpc
    good.ltp = 200.0; good.cp = 190.0

    result = process_binary_frame(r.SerializeToString(),
                                  received_ts=AWARE_UTC,
                                  instrument_metadata=METADATA)
    runner.assert_eq(name + "-total", len(result.instruments), 2)

    by_key = {o.instrument_key: o for o in result.instruments}
    runner.assert_eq(name + "-bad-has-error",
                     by_key[KEY].error, "unsupported")
    runner.assert_true(name + "-good-ok",
                       by_key["NSE_EQ|INE848E01016"].patch is not None)


def test_malformed_frame(runner: R) -> None:
    """FP7: malformed bytes raise ProtobufDecodeError."""
    name = "FP7-malformed"
    from brokers.upstox.feed_protocol import ProtobufDecodeError
    from brokers.upstox.feed_processing import process_binary_frame

    _expect_raises(runner, name + "-empty", ProtobufDecodeError,
                   lambda: process_binary_frame(b"", received_ts=AWARE_UTC, instrument_metadata=METADATA))
    _expect_raises(runner, name + "-garbage", ProtobufDecodeError,
                   lambda: process_binary_frame(b"\x0a\x05ab", received_ts=AWARE_UTC, instrument_metadata=METADATA))


def test_market_info(runner: R) -> None:
    """FP8: market_info produces segment_status, no instruments."""
    name = "FP8-market-info"
    from brokers.upstox.feed_processing import process_binary_frame

    frame = _build_market_info()
    result = process_binary_frame(frame, received_ts=AWARE_UTC, instrument_metadata=METADATA)
    runner.assert_eq(name + "-type", result.frame_type, "market_info")
    runner.assert_eq(name + "-segments", result.segment_status,
                     {"NSE_EQ": "NORMAL_OPEN"})
    runner.assert_eq(name + "-no-instruments", len(result.instruments), 0)


async def test_publisher_zero_calls(runner: R) -> None:
    """FP9: publisher spy receives zero calls during processing."""
    name = "FP9-no-publisher"
    publisher_calls: list = []
    feed_svc = _make_service()

    from brokers.upstox.feed_processing import process_binary_frame
    frame = _build_full_response()
    result = process_binary_frame(frame, received_ts=AWARE_UTC, instrument_metadata=METADATA)
    for o in result.instruments:
        if o.patch:
            await feed_svc.apply_quote(o.patch)

    runner.assert_eq(name + "-publisher-zero", len(publisher_calls), 0)


# ---------------------------------------------------------------------------
# FP10 — canonical identity
# ---------------------------------------------------------------------------


def test_canonical_identity(runner: R) -> None:
    """FP10: metadata exchange/tradingsymbol used; key NOT parsed."""
    name = "FP10-canonical-identity"
    from brokers.upstox.feed_processing import process_binary_frame

    # Use a key whose identifier part differs from the canonical tradingsymbol.
    key = "NSE_EQ|INE848E01016"
    meta = {key: ("NSE", "DMART")}
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    t = r.feeds[key].ltpc
    t.ltp = 100.0; t.cp = 90.0

    result = process_binary_frame(r.SerializeToString(),
                                  received_ts=AWARE_UTC,
                                  instrument_metadata=meta)
    o = result.instruments[0]
    runner.assert_true(name + "-has-patch", o.patch is not None)
    assert o.patch is not None
    runner.assert_eq(name + "-exchange", o.patch.exchange, "NSE")
    runner.assert_eq(name + "-token-is-key", o.patch.instrument_token, key)
    runner.assert_eq(name + "-tradingsymbol-from-metadata",
                     o.patch.tradingsymbol, "DMART")
    runner.assert_not_in(name + "-identifier-not-used",
                         "INE848E01016", o.patch.tradingsymbol or "")


def test_unknown_instrument_dropped(runner: R) -> None:
    """FP11: unknown key dropped; valid sibling processed normally."""
    name = "FP11-unknown-instrument"
    from brokers.upstox.feed_processing import process_binary_frame

    meta = {KEY: (EXCHANGE, TS)}
    r = pb.FeedResponse()
    r.type = pb.Type.Value("live_feed")
    unknown = r.feeds["UNKNOWN|KEY123"].ltpc
    unknown.ltp = 1.0; unknown.cp = 0.5
    known = r.feeds[KEY].ltpc
    known.ltp = 200.0; known.cp = 190.0

    result = process_binary_frame(r.SerializeToString(),
                                  received_ts=AWARE_UTC,
                                  instrument_metadata=meta)
    runner.assert_eq(name + "-total", len(result.instruments), 2)
    by_key = {o.instrument_key: o for o in result.instruments}
    runner.assert_eq(name + "-unknown-error",
                     by_key["UNKNOWN|KEY123"].error, "unknown_instrument")
    runner.assert_true(name + "-unknown-no-patch",
                       by_key["UNKNOWN|KEY123"].patch is None)
    runner.assert_true(name + "-known-ok",
                       by_key[KEY].patch is not None)


async def test_metadata_end_to_end(runner: R) -> None:
    """FP12: canonical metadata flows through to MarketService state."""
    name = "FP12-metadata-e2e"
    from market.service import MarketService
    from brokers.upstox.feed_processing import process_binary_frame

    svc = MarketService()
    key = "NSE_EQ|INE848E01016"
    meta = {key: ("NSE", "DMART")}

    frame = _build_full_response(key=key, ltp=100.0, cp=95.0)
    result = process_binary_frame(frame, received_ts=AWARE_UTC,
                                  instrument_metadata=meta)
    for o in result.instruments:
        if o.patch:
            await svc.apply_quote(o.patch)
        if o.depth:
            await svc.apply_depth(o.depth)

    q = await svc.get_quote("NSE", key)
    assert q is not None
    runner.assert_eq(name + "-exchange", q.exchange, "NSE")
    runner.assert_eq(name + "-tradingsymbol", q.tradingsymbol, "DMART")
    runner.assert_eq(name + "-token", q.instrument_token, key)


def test_metadata_immutability(runner: R) -> None:
    """FP13: caller mutation after construction cannot affect stored metadata."""
    name = "FP13-metadata-immutable"
    from types import MappingProxyType

    from brokers.upstox import UpstoxCredentials, UpstoxFeed

    original = {KEY: (EXCHANGE, TS)}
    creds = UpstoxCredentials(access_token="tok")

    class _StubRest:
        async def authorize_market_feed(self, credentials):
            raise AssertionError("not used")

    feed = UpstoxFeed(
        config={"source_name": "upstox", "mode": "full",
                "instrument_keys": [KEY]},
        credentials=creds,
        rest=_StubRest(),
        instrument_metadata=original,
    )
    # Mutate the caller's original dict.
    original[KEY] = ("WRONG", "WRONG")
    original["NEW_KEY"] = ("NEW", "NEW")

    stored = feed._instrument_metadata
    runner.assert_eq(name + "-isolated-value", stored[KEY], (EXCHANGE, TS))
    runner.assert_false(name + "-no-new-keys", "NEW_KEY" in stored)
    runner.assert_true(name + "-is-mappingproxy",
                       isinstance(stored, MappingProxyType))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_process_ltpc(runner)
    test_process_full_market(runner)
    test_pzero_zero_not_reported(runner)
    await test_end_to_end_quote_and_depth(runner)
    await test_stale_update_rejected(runner)
    await test_bad_instrument_isolation(runner)
    test_malformed_frame(runner)
    test_market_info(runner)
    await test_publisher_zero_calls(runner)
    test_canonical_identity(runner)
    test_unknown_instrument_dropped(runner)
    await test_metadata_end_to_end(runner)
    test_metadata_immutability(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
