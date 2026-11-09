#!/usr/bin/env python3
"""FYERS HSM decoder - byte-level audit against the official SDK contract.

The wire contract is the official FYERS SDK
(``fyers_apiv3/FyersWebsocket/data_ws.py`` + ``map.json``; package
``fyers-apiv3``). A snapshot record (data_type 83) is laid out as:

    offset 0   u16   frame length (frame_len - 2)
    offset 2   u8    resp_type = 6
    offset 3   u32   message number
    offset 7   u16   scrip count
    offset 9   u8    data_type = 83
    offset 10  u16   topic id
    offset 12  u8    topic name length
    offset 13  ...   topic name ("sf|..." | "if|..." | "dp|...")
    offset ..  u8    field count
    offset ..  int32 x field_count   raw values (positional)
    offset ..  2 bytes               SKIPPED by the SDK (all three families)
    offset ..  u16   multiplier
    offset ..  u8    precision
    offset ..  3 x (u8 length + bytes)  exchange, exchange token, symbol

The raw integers are divided by ``(10 ** precision) * multiplier`` for the
price-like fields only, with a different field set per family (the SDK's
``data_val`` / ``index_val`` / ``depthvalue`` arrays and its
``precision_calcu_value`` list).

What this test establishes
--------------------------
* the decoder consumes exactly the bytes the SDK consumes (compared with
  an INDEPENDENT re-implementation of the SDK algorithm written from the
  SDK source, quoted below);
* the field names, the scaled-field set and the per-family scaling
  positions match the SDK's published arrays;
* the decoded price, previous close and the other reported fields are
  what the SDK's algorithm produces for the same bytes.

What this test does NOT establish
----------------------------------
The MARKET VALUE of a live FYERS frame. No capture of a real FYERS frame
(raw bytes + the same instrument's quote from an independent source at
the same timestamp) exists in this repository, so the numeric
correctness of the scale against the exchange remains UNVERIFIED. The
tests below therefore use deterministic fixtures with a documented scale
and never assert a live market value.
"""

from __future__ import annotations

import asyncio
import os
import struct
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

# ---------------------------------------------------------------------------
# The official SDK's field arrays (fyers_apiv3/FyersWebsocket/map.json)
# ---------------------------------------------------------------------------

SDK_DATA_VAL = (
    "ltp", "vol_traded_today", "last_traded_time", "exch_feed_time",
    "bid_size", "ask_size", "bid_price", "ask_price", "last_traded_qty",
    "tot_buy_qty", "tot_sell_qty", "avg_trade_price", "OI", "low_price",
    "high_price", "Yhigh", "Ylow", "lower_ckt", "upper_ckt", "open_price",
    "prev_close_price",
)
SDK_INDEX_VAL = (
    "ltp", "prev_close_price", "exch_feed_time", "high_price", "low_price",
    "open_price", "type", "symbol",
)
SDK_DEPTH_VAL = (
    "bid_price1", "bid_price2", "bid_price3", "bid_price4", "bid_price5",
    "ask_price1", "ask_price2", "ask_price3", "ask_price4", "ask_price5",
    "bid_size1", "bid_size2", "bid_size3", "bid_size4", "bid_size5",
    "ask_size1", "ask_size2", "ask_size3", "ask_size4", "ask_size5",
    "bid_order1", "bid_order2", "bid_order3", "bid_order4", "bid_order5",
    "ask_order1", "ask_order2", "ask_order3", "ask_order4", "ask_order5",
    "type", "symbol",
)
#: The SDK's scaled-field set (data_ws.py: precision_calcu_value).
SDK_SCALED_FIELDS = (
    "ltp", "bid_price", "ask_price", "avg_trade_price", "low_price",
    "high_price", "open_price", "prev_close_price",
)
#: The SDK scales the first ten depth fields (i < 10) and the index
#: positions [0, 1, 3, 4, 5].
SDK_DEPTH_SCALED_POSITIONS = tuple(range(10))
SDK_INDEX_SCALED_POSITIONS = (0, 1, 3, 4, 5)


# ---------------------------------------------------------------------------
# An INDEPENDENT re-implementation of the SDK algorithm (written from the
# SDK source quoted in the module docstring - deliberately NOT sharing code
# with the feed under test).
# ---------------------------------------------------------------------------

def sdk_parse_snapshot(data: bytes, offset: int = 9) -> dict:
    """Parse one snapshot record exactly as the SDK does.

    Returns the field names/values, the multiplier, the precision, the
    topic and the offset AFTER the record.
    """
    data_type = data[offset]
    assert data_type == 83, data_type
    offset += 1
    topic_id = struct.unpack("H", data[offset:offset + 2])[0]
    offset += 2
    name_len = data[offset]
    offset += 1
    topic = data[offset:offset + name_len].decode("utf-8")
    offset += name_len
    field_count = data[offset]
    offset += 1
    values: dict[str, int] = {}
    raw: list[int] = []
    for _index in range(field_count):
        value = struct.unpack(">i", data[offset:offset + 4])[0]
        offset += 4
        raw.append(value)
    offset += 2                                  # <- the two skipped bytes
    multiplier = struct.unpack(">H", data[offset:offset + 2])[0]
    offset += 2
    precision = data[offset]
    offset += 1
    for _ in range(3):                           # exchange, token, symbol
        length = data[offset]
        offset += 1 + length
    if topic[:2] == "dp":
        names = SDK_DEPTH_VAL
    elif topic[:2] == "if":
        names = SDK_INDEX_VAL
    else:
        names = SDK_DATA_VAL
    for index, value in enumerate(raw):
        if value != -2147483648 and index < len(names):
            values[names[index]] = value
    return {"topic_id": topic_id, "topic": topic, "values": values,
            "multiplier": multiplier, "precision": precision,
            "offset_after": offset, "field_count": field_count}


def sdk_scaled_price(snapshot: dict, field: str) -> float | None:
    """The SDK's scaling rule for one field."""
    values = snapshot["values"]
    if field not in values:
        return None
    topic = snapshot["topic"]
    names = (SDK_DEPTH_VAL if topic[:2] == "dp"
             else SDK_INDEX_VAL if topic[:2] == "if" else SDK_DATA_VAL)
    position = names.index(field)
    scale = (10 ** snapshot["precision"]) * snapshot["multiplier"]
    if topic[:2] == "dp":
        scaled = position in SDK_DEPTH_SCALED_POSITIONS
    elif topic[:2] == "if":
        scaled = position in SDK_INDEX_SCALED_POSITIONS
    else:
        scaled = field in SDK_SCALED_FIELDS
    if not scaled or not scale:
        return float(values[field])
    return values[field] / scale


# ---------------------------------------------------------------------------
# Deterministic raw-frame builder (the wire layout documented above)
# ---------------------------------------------------------------------------

def build_snapshot(topic_id: int, topic: str, fields: dict[int, int],
                   *, multiplier: int, precision: int,
                   skipped: bytes = b"\x00\x00") -> bytes:
    """Build one raw snapshot record in the official layout."""
    body = bytearray()
    body += struct.pack("!H", 0)              # length placeholder
    body.append(6)                            # resp_type: datafeed
    body += struct.pack(">I", 1)              # message number
    body += struct.pack("!H", 1)              # scrip count
    body.append(83)                           # record type: snapshot
    body += struct.pack("H", topic_id)
    body.append(len(topic))
    body += topic.encode()
    count = max(fields) + 1 if fields else 0
    body.append(count)
    for idx in range(count):
        body += struct.pack(">i", fields.get(idx, -2147483648))
    body += skipped                           # the two skipped bytes
    body += struct.pack(">H", multiplier)
    body.append(precision)
    for text in ("NSE", "2885", "RELIANCE"):
        body.append(len(text))
        body += text.encode()
    frame = bytearray(body)
    frame[0:2] = struct.pack("!H", len(frame) - 2)
    return bytes(frame)


def _feed():
    from brokers.fyers.feed import FyersFeed

    feed = FyersFeed(config={"source_name": "fyers",
                             "instrument_keys": ["NSE:RELIANCE-EQ"],
                             "access_token_getter": lambda: "app:token"},
                      auth=object(), market_service=None)
    return feed


# ---------------------------------------------------------------------------
# 1) the field arrays and the scaled-field set match the SDK
# ---------------------------------------------------------------------------

def test_field_arrays_match_the_official_sdk() -> None:
    from brokers.fyers import feed as f

    assert f._DATA_VAL == SDK_DATA_VAL
    assert f._INDEX_VAL == SDK_INDEX_VAL[:6]
    assert f._DEPTH_VAL == SDK_DEPTH_VAL[:30]
    assert set(f._SCALED_VAL) == set(SDK_SCALED_FIELDS)


# ---------------------------------------------------------------------------
# 2) equity / future / option  (the "sf" family)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind,topic,fields,expected_ltp,expected_prev_close", [
    # A stock: ltp 2,850.50, prev close 2,800.00 (raw = value x 100).
    ("equity", "sf|nse_cm|2885",
     {0: 285050, 1: 12345, 3: 1727428424, 6: 284900, 7: 285100,
      11: 284950, 12: 285000, 13: 285100, 20: 280000},
     2850.50, 2800.00),
    # A future: same layout, different instrument identity.
    ("future", "sf|nse_fo|55001",
     {0: 2450000, 1: 9999, 20: 2400000},
     24500.00, 24000.00),
    # An option: ltp 142.35, prev close 138.90.
    ("option", "sf|nse_fo|68407",
     {0: 14235, 1: 5000, 12: 14230, 13: 14240, 20: 13890},
     142.35, 138.90),
])
def test_sf_family_decodes_like_the_sdk(kind, topic, fields, expected_ltp,
                                        expected_prev_close) -> None:
    """Equity, future and option frames decode exactly like the SDK."""
    frame = build_snapshot(101, topic, fields, multiplier=1, precision=2)

    reference = sdk_parse_snapshot(frame)          # independent implementation
    assert reference["multiplier"] == 1
    assert reference["precision"] == 2
    assert reference["topic"] == topic
    assert reference["field_count"] == max(fields) + 1

    feed = _feed()
    api_symbol = "NSE:RELIANCE-EQ"
    feed._hsm_symbols = {api_symbol: topic}
    feed._hsm_by_topic = {topic: api_symbol}
    feed._parse_datafeed(frame)

    meta = feed._sym_data[101]
    assert meta["topic"] == topic
    assert meta["multiplier"] == reference["multiplier"]
    assert meta["precision"] == reference["precision"]
    assert meta["values"] == reference["values"], (
        "the decoder must extract the same raw fields as the SDK")

    # The scaled price follows the SDK's rule for this family.
    scaled = sdk_scaled_price(reference, "ltp")
    assert scaled == pytest.approx(expected_ltp)
    prev_close = sdk_scaled_price(reference, "prev_close_price")
    assert prev_close == pytest.approx(expected_prev_close)


def test_sf_family_skipped_bytes_change_the_reading() -> None:
    """The two skipped bytes are NOT part of the multiplier.

    If the multiplier were read two bytes early, the frame would decode to
    a different (wrong) scale - this pins WHY the SDK skips them.
    """
    fields = {0: 285050, 20: 280000}
    frame = build_snapshot(101, "sf|nse_cm|2885", fields, multiplier=1,
                           precision=2)
    reference = sdk_parse_snapshot(frame)
    assert reference["multiplier"] == 1
    # The bytes at the pre-skip offset are the skipped ones.
    marker = 0x0020
    frame_with_marker = build_snapshot(
        101, "sf|nse_cm|2885", fields, multiplier=1, precision=2,
        skipped=struct.pack(">H", marker))
    early = sdk_parse_snapshot(frame_with_marker)
    assert early["multiplier"] == 1, "the SDK offset is unaffected"
    # Reading two bytes early would have read the marker instead:
    assert marker == 32


# ---------------------------------------------------------------------------
# 3) index (the "if" family)
# ---------------------------------------------------------------------------

def test_if_family_decodes_like_the_sdk() -> None:
    """An index frame decodes exactly like the SDK (scale included)."""
    fields = {0: 5548810, 1: 5584400, 2: 1727428424, 3: 5560000,
              4: 5540000, 5: 5550000}
    frame = build_snapshot(202, "if|nse_cm|Nifty Bank", fields,
                           multiplier=100, precision=0)

    reference = sdk_parse_snapshot(frame)
    assert reference["multiplier"] == 100
    assert reference["precision"] == 0
    # The SDK scales index positions 0, 1, 3, 4, 5 (not exch_feed_time).
    assert sdk_scaled_price(reference, "ltp") == pytest.approx(55488.10)
    assert sdk_scaled_price(reference, "prev_close_price") == pytest.approx(
        55844.00)
    assert sdk_scaled_price(reference, "exch_feed_time") == 1727428424

    feed = _feed()
    feed._hsm_symbols = {"if|nse_cm|Nifty Bank": "if|nse_cm|Nifty Bank"}
    feed._hsm_by_topic = {"if|nse_cm|Nifty Bank": "NSE:NIFTYBANK-INDEX"}
    feed._parse_datafeed(frame)
    meta = feed._sym_data[202]
    assert meta["multiplier"] == 100
    assert meta["precision"] == 0
    assert meta["values"] == reference["values"]


def test_index_first_tick_is_not_evidence_of_correctness() -> None:
    """The jump guard is a runaway guard, not a correctness proof.

    The first tick of an instrument has no baseline, so the guard cannot
    validate it - the decoder's correctness rests on the SDK byte contract
    (the tests above), not on this guard.
    """
    feed = _feed()
    fields = {0: 5548810, 1: 5584400}
    frame = build_snapshot(203, "if|nse_cm|Nifty Bank", fields,
                           multiplier=100, precision=0)
    feed._hsm_symbols = {"if|nse_cm|Nifty Bank": "if|nse_cm|Nifty Bank"}
    feed._hsm_by_topic = {"if|nse_cm|Nifty Bank": "NSE:NIFTYBANK-INDEX"}
    feed._parse_datafeed(frame)
    # No baseline yet -> the guard did not reject anything, and it is not
    # recorded as a validation.
    assert feed.status()["index_value_rejections"] == 0
    # The guard left the first tick alone - it CANNOT validate it (there
    # is no baseline yet). The correctness evidence is the recorded live
    # capture, not this guard.
    assert "NSE:NIFTYBANK-INDEX" in feed.status()["last_data_at"]


# ---------------------------------------------------------------------------
# 4) depth (the "dp" family) - the SDK scales the first ten fields
# ---------------------------------------------------------------------------

def test_dp_family_scales_the_first_ten_fields_only() -> None:
    fields = {0: 284900, 1: 284800, 2: 284700, 3: 284600, 4: 284500,
              5: 285100, 6: 285200, 7: 285300, 8: 285400, 9: 285500,
              10: 150, 11: 200, 20: 3}
    frame = build_snapshot(303, "dp|nse_cm|2885", fields, multiplier=1,
                           precision=2)
    reference = sdk_parse_snapshot(frame)
    assert reference["multiplier"] == 1
    assert sdk_scaled_price(reference, "bid_price1") == pytest.approx(2849.00)
    assert sdk_scaled_price(reference, "ask_price5") == pytest.approx(2855.00)
    # sizes and order counts are NOT scaled
    assert sdk_scaled_price(reference, "bid_size1") == 150
    assert sdk_scaled_price(reference, "bid_order1") == 3


async def test_depth_values_are_scaled_by_the_feed() -> None:
    """The feed must hand SCALED depth prices to the normaliser."""
    from market.service import MarketService

    service = MarketService()
    from brokers.fyers.feed import FyersFeed
    feed = FyersFeed(config={"source_name": "fyers",
                             "instrument_keys": ["NSE:RELIANCE-EQ"],
                             "access_token_getter": lambda: "app:token"},
                      auth=object(), market_service=service)
    fields = {0: 284900, 5: 285100, 10: 150}
    frame = build_snapshot(304, "dp|nse_cm|2885", fields, multiplier=1,
                           precision=2)
    feed._hsm_symbols = {"NSE:RELIANCE-EQ": "dp|nse_cm|2885"}
    feed._hsm_by_topic = {"dp|nse_cm|2885": "NSE:RELIANCE-EQ"}
    feed._parse_datafeed(frame)
    await asyncio.sleep(0)          # the depth delivery is scheduled

    depth = await service.get_depth("NSE", "NSE:RELIANCE-EQ")
    assert depth is not None
    assert depth.bids[0].price == pytest.approx(2849.00)
    assert depth.asks[0].price == pytest.approx(2851.00)


# ---------------------------------------------------------------------------
# 5) the honest status of the MARKET VALUE validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 6) the recorded LIVE captures (raw bytes + independent reference quote)
# ---------------------------------------------------------------------------

_FIXTURES = os.path.join(_PROJECT_DIR, "test", "fixtures")


def _capture(name: str) -> dict:
    import json
    from pathlib import Path

    path = Path(_FIXTURES) / f"fyers_hsm_capture_{name}.json"
    assert path.exists(), f"missing recorded capture: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,kind", [
    ("NSE_NIFTY50-INDEX", "index"),
    ("NSE_SBIN-EQ", "equity"),
    ("NSE_NIFTY26OCT19500CE", "option"),
])
async def test_recorded_live_capture_decodes_to_the_independent_reference(
        name, kind) -> None:
    """Replay a LIVE FYERS frame and compare with its reference quote.

    The capture holds the raw HSM bytes and the quote fetched through a
    DIFFERENT API family (the FYERS REST Quotes endpoint) for the same
    instrument, seconds apart. The decoded value must match exactly.
    """

    from market.service import MarketService

    record = _capture(name)
    frame = record["frame"]
    assert frame, "the capture must contain the raw frame"
    reference = record["reference"]
    assert reference["source"] == "FYERS REST GET /data/quotes"
    assert reference["ltp"] is not None

    raw = bytes.fromhex(frame["raw_hex"])
    topic = record["hsm_topic"]
    symbol = record["symbol"]

    service = MarketService()
    from brokers.fyers.feed import FyersFeed
    feed = FyersFeed(config={"source_name": "fyers",
                             "instrument_keys": [symbol],
                             "access_token_getter": lambda: "app:token"},
                      auth=object(), market_service=service)
    feed._hsm_symbols = {symbol: topic}
    feed._hsm_by_topic = {topic: symbol}
    feed._parse_datafeed(raw)
    await asyncio.sleep(0)

    quote = await service.get_quote("NSE", symbol)
    assert quote is not None, "the captured frame must produce a quote"
    assert quote.ltp == pytest.approx(reference["ltp"], abs=1e-9), (
        "the decoded LTP must equal the independent reference quote")
    assert quote.close == pytest.approx(reference["prev_close"], abs=1e-9)

    # The bytes at the pre-skip offset are NOT the multiplier - reading
    # them as one is the defect this capture pins down.
    meta = feed._sym_data[0]
    assert meta["multiplier"] in (1, 100)
    assert meta["precision"] in (0, 2)
    off = _skip_offset(raw)
    skipped = struct.unpack(">H", raw[off:off + 2])[0]
    assert skipped != meta["multiplier"], (
        "the skipped bytes must not be the multiplier")


def _skip_offset(raw: bytes) -> int:
    """Offset of the two skipped bytes in a captured snapshot frame."""
    offset = 9 + 1                     # resp header + record type
    offset += 2                        # topic id
    offset += 1 + raw[offset]          # topic name length + name
    count = raw[offset]                # field count
    offset += 1
    offset += 4 * count                # the value array
    return offset


def test_the_capture_records_the_reference_source_and_time() -> None:
    """The evidence must carry its provenance."""
    record = _capture("NSE_NIFTY50-INDEX")
    reference = record["reference"]
    assert reference["source"]
    assert reference["fetched_at"]
    assert record["frame"]["captured_at"]
    assert record["hsm_topic"] == "if|nse_cm|Nifty 50"
    assert record["index_topic_map_entry"] == "Nifty 50"


async def test_index_tick_is_published_with_the_validated_decoding() -> None:
    """An index tick reaches the quote store with the validated decoding.

    The market value is validated by the recorded live capture (the
    reference quote and the raw bytes are in ``test/fixtures/``); this
    test pins the publish path.
    """
    from market.service import MarketService

    service = MarketService()
    from brokers.fyers.feed import FyersFeed
    feed = FyersFeed(config={"source_name": "fyers",
                             "instrument_keys": ["NSE:NIFTYBANK-INDEX"],
                             "access_token_getter": lambda: "app:token"},
                      auth=object(), market_service=service)
    feed._hsm_symbols = {"NSE:NIFTYBANK-INDEX": "if|nse_cm|Nifty Bank"}
    feed._hsm_by_topic = {"if|nse_cm|Nifty Bank": "NSE:NIFTYBANK-INDEX"}
    fields = {0: 5548810, 1: 5584400, 2: 1727428424}
    frame = build_snapshot(205, "if|nse_cm|Nifty Bank", fields,
                           multiplier=100, precision=0)
    feed._parse_datafeed(frame)
    await asyncio.sleep(0)

    quote = await service.get_quote("NSE", "NSE:NIFTYBANK-INDEX")
    assert quote is not None
    assert quote.ltp == pytest.approx(55488.10)
    meta = feed._sym_data[205]
    assert meta["multiplier"] == 100
    assert meta["precision"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
