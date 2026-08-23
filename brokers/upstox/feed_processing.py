"""
Upstox V3 frame processing (Phase D3.2) — pure decode + normalize.

Bridges the frozen D1 protobuf decoder and B1 Upstox normalizers to
produce QuotePatch / Depth objects ready for MarketService application.

This module is STATELESS and SYNCHRONOUS — no I/O, no locks, no MarketService.
UpstoxFeed calls ``process_binary_frame`` from its receive loop and applies
the returned items.

IDENTITY: canonical identity comes from the caller-supplied
``instrument_metadata`` mapping keyed by Upstox instrument_key. Keys not
present in the mapping are dropped as unknown — no fallback or provisional
identity is ever created.

P-ZERO (locked): consumed as-is via D1 presence maps. No zero-detection,
no HasField probing, no REST cross-checks, no MessageToDict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from brokers.upstox.feed_protocol import (
    ProtobufDecodeError,
    decode_feed_response,
    feed_type,
    iter_instrument_feeds,
)
from market.normalize.upstox import (
    depth_from_ws,
    quote_fields_from_ws_full,
    quote_fields_from_ws_ltpc,
)
from market.service import QuotePatch

__all__ = [
    "FrameResult",
    "InstrumentOutcome",
    "process_binary_frame",
    "extract_segment_status",
]


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstrumentOutcome:
    """One instrument's normalized output (or the error that replaced it)."""

    instrument_key: str
    patch: QuotePatch | None = None
    depth: Any | None = None            # market.models.Depth when present
    error: str | None = None            # 'normalize' | 'unsupported' | 'unknown_instrument'


@dataclass(frozen=True, slots=True)
class FrameResult:
    """Aggregated result of processing one binary WS frame."""

    frame_type: str                     # 'initial_feed' | 'live_feed' | 'market_info' | 'unknown'
    segment_status: dict[str, str] | None = None
    instruments: tuple[InstrumentOutcome, ...] = ()


# ---------------------------------------------------------------------------
# Per-instrument normalization
# ---------------------------------------------------------------------------


def _build_patch(fields: dict[str, Any]) -> QuotePatch:
    """Convert a normalizer field dict into a QuotePatch.

    Splits identity/timestamp keys from data keys; ``exchange_ts`` becomes
    a structured QuotePatch attribute rather than a reported field.
    """
    exchange_ts = fields.pop("exchange_ts", None)

    return QuotePatch(
        exchange=fields["exchange"],
        instrument_token=fields["instrument_token"],
        received_ts=fields["received_ts"],
        tradingsymbol=fields.get("tradingsymbol"),
        exchange_ts=exchange_ts,
        reported_fields={
            k: v for k, v in fields.items()
            if k not in ("instrument_token", "exchange", "tradingsymbol",
                         "received_ts")
        },
    )


def _normalize_ltpc(
    ltpc: dict[str, Any],
    *,
    instrument_key: str,
    exchange: str,
    tradingsymbol: str,
    received_ts: datetime,
) -> InstrumentOutcome:
    fields = quote_fields_from_ws_ltpc(
        ltpc,
        instrument_key=instrument_key,
        received_ts=received_ts,
        tradingsymbol=tradingsymbol,
    )
    patch = _build_patch(fields)
    return InstrumentOutcome(instrument_key=instrument_key, patch=patch)


def _normalize_full_market(
    market_ff: dict[str, Any],
    *,
    instrument_key: str,
    exchange: str,
    tradingsymbol: str,
    received_ts: datetime,
) -> InstrumentOutcome:
    fields = quote_fields_from_ws_full(
        market_ff,
        instrument_key=instrument_key,
        received_ts=received_ts,
        tradingsymbol=tradingsymbol,
    )

    depth = None
    market_level = market_ff.get("marketLevel")
    if isinstance(market_level, dict) and market_level.get("bidAskQuote"):
        depth = depth_from_ws(
            market_level,
            instrument_key=instrument_key,
            tradingsymbol=tradingsymbol,
            received_ts=received_ts,
        )

    patch = _build_patch(fields)
    return InstrumentOutcome(instrument_key=instrument_key, patch=patch,
                             depth=depth)


def _normalize_instrument(
    key: str,
    feed_dict: dict[str, Any],
    *,
    exchange: str,
    tradingsymbol: str,
    received_ts: datetime,
) -> InstrumentOutcome:
    """Route one instrument feed dict to the appropriate normalizer."""
    if "ltpc" in feed_dict:
        return _normalize_ltpc(
            feed_dict["ltpc"], instrument_key=key, exchange=exchange,
            tradingsymbol=tradingsymbol, received_ts=received_ts,
        )
    if "fullFeed" in feed_dict:
        inner = feed_dict["fullFeed"]
        if "marketFF" in inner:
            return _normalize_full_market(
                inner["marketFF"], instrument_key=key, exchange=exchange,
                tradingsymbol=tradingsymbol, received_ts=received_ts,
            )
        if "indexFF" in inner:
            return _normalize_full_market(
                inner["indexFF"], instrument_key=key, exchange=exchange,
                tradingsymbol=tradingsymbol, received_ts=received_ts,
            )
    return InstrumentOutcome(instrument_key=key, error="unsupported")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_segment_status(response: Any) -> dict[str, str]:
    """Extract segment status names from a market_info FeedResponse."""
    info = response.marketInfo
    if not info or not info.segmentStatus:
        return {}
    result: dict[str, str] = {}
    for key in info.segmentStatus:
        entry = info.segmentStatus[key]
        try:
            field_desc = response.DESCRIPTOR.fields_by_name["marketInfo"]
            enum_fd = field_desc.message_type.fields_by_name["segmentStatus"]
            enum_type = enum_fd.message_type.fields_by_name["value"].enum_type
            result[key] = enum_type.values_by_number[entry].name
        except Exception:
            result[key] = str(entry)
    return result


def process_binary_frame(
    frame: bytes,
    *,
    received_ts: datetime,
    instrument_metadata: Mapping[str, tuple[str, str]],
) -> FrameResult:
    """Decode and normalize one binary WebSocket frame.

    ``instrument_metadata`` maps Upstox instrument_key to
    ``(exchange, tradingsymbol)``. Keys absent from this mapping are
    dropped as unknown instruments — no fallback identity is created.

    Returns a FrameResult with per-instrument outcomes. Known bad provider
    data produces per-instrument errors; unexpected internal bugs propagate.
    """
    from brokers.upstox.feed_protocol import ProtobufDecodeError

    try:
        response = decode_feed_response(frame)
    except ProtobufDecodeError:
        raise
    except Exception as exc:
        raise ProtobufDecodeError(
            f"upstox feed: unexpected decoder failure ({exc})"
        ) from exc

    ftype = feed_type(response)

    if ftype == "market_info":
        return FrameResult(
            frame_type=ftype,
            segment_status=extract_segment_status(response),
        )

    if ftype not in ("initial_feed", "live_feed"):
        return FrameResult(frame_type=ftype)

    outcomes: list[InstrumentOutcome] = []
    for key, feed_dict in iter_instrument_feeds(response):
        metadata = instrument_metadata.get(key)
        if metadata is None:
            outcomes.append(InstrumentOutcome(
                instrument_key=key, error="unknown_instrument"
            ))
            continue
        exchange, tradingsymbol = metadata
        try:
            outcome = _normalize_instrument(
                key, feed_dict, exchange=exchange,
                tradingsymbol=tradingsymbol, received_ts=received_ts,
            )
        except Exception as exc:
            outcome = InstrumentOutcome(
                instrument_key=key, error="normalize"
            )
        outcomes.append(outcome)

    return FrameResult(frame_type=ftype, instruments=tuple(outcomes))
