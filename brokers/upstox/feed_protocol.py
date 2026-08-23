"""
Upstox V3 market-data-feed protocol decoder (Phase D1).

Owns the boundary between raw WebSocket frames and MarketHub's
presence-preserving normalization layer:

    WS binary frame
        -> decode_feed_response()      (strict protobuf parse)
        -> iter_instrument_feeds()     (ListFields-based, presence-exact)
        -> per-key feed dicts consumed by market.normalize.upstox

P-ZERO (locked policy, verbatim):

    For Upstox V3 WebSocket scalar fields without protobuf presence,
    MarketHub treats decoded default-zero scalars as not reported when
    producing presence-preserving quote patches. This is an explicit
    adapter policy necessitated by the protocol's lack of scalar presence
    and consistent with the current official SDK decoding behavior.
    REST JSON retains literal-zero semantics. Fields for which zero later
    proves semantically significant must be handled with provider-specific
    state/snapshot evidence rather than inferred from protobuf presence.

Implementation note: extraction uses ``ListFields()`` (plus HasField /
WhichOneof where relevant), which yields exactly the wire-present,
non-default field set. That makes the P-ZERO semantics structural rather
than key-sniffed, and keeps native protobuf numeric types (no
MessageToDict int64-to-string conversion).
"""

from __future__ import annotations

from typing import Any, Iterator

from google.protobuf.message import Message

from brokers.upstox.proto import MarketDataFeed_pb2 as pb

__all__ = [
    "ProtobufDecodeError",
    "decode_feed_response",
    "feed_type",
    "which_feed_union",
    "iter_instrument_feeds",
]


class ProtobufDecodeError(ValueError):
    """A WebSocket frame could not be parsed as a V3 FeedResponse."""


def decode_feed_response(buffer: bytes) -> "pb.FeedResponse":
    """Parse a binary WS frame into a FeedResponse.

    Empty frames and undecodable payloads raise ProtobufDecodeError —
    callers treat these as drop-and-count, never fatal.
    """
    if not buffer:
        raise ProtobufDecodeError("upstox feed: empty frame")
    response = pb.FeedResponse()
    try:
        response.ParseFromString(buffer)
    except Exception as exc:  # protobuf DecodeError and runtime variants
        raise ProtobufDecodeError(
            f"upstox feed: undecodable protobuf frame ({exc})"
        ) from exc
    return response


def feed_type(response: "pb.FeedResponse") -> str:
    """Message type name: 'initial_feed' | 'live_feed' | 'market_info'."""
    return pb.Type.Name(response.type)


def which_feed_union(feed_message: "pb.Feed") -> str | None:
    """Active oneof branch for a Feed message: 'ltpc'|'fullFeed'|
    'firstLevelWithGreeks', or None when nothing is set."""
    return feed_message.WhichOneof("FeedUnion")


def _enum_name(field_descriptor: Any, value: int) -> str:
    return field_descriptor.enum_type.values_by_number[value].name


def _convert_scalar(field_descriptor: Any, value: Any) -> Any:
    if field_descriptor.type == field_descriptor.TYPE_ENUM:
        return _enum_name(field_descriptor, value)
    return value


def _message_to_presence_dict(message: Any) -> dict[str, Any]:
    """Convert a protobuf message to a dict containing ONLY wire-present
    fields (ListFields = non-default set), per the P-ZERO contract.

    - singular/repeated messages -> nested dicts / list of dicts
    - map<string, enum>          -> {key: enum-name}
    - map<string, message>       -> {key: nested-dict}
    - enums                      -> value names
    - scalars                    -> native Python numerics/strings
    """
    out: dict[str, Any] = {}
    for fd, value in message.ListFields():
        if fd.type == fd.TYPE_MESSAGE and fd.message_type.GetOptions().map_entry:
            value_fd = fd.message_type.fields_by_name["value"]
            inner: dict[str, Any] = {}
            for key in value:
                inner[key] = _convert_scalar(value_fd, value[key]) \
                    if value_fd.type != value_fd.TYPE_MESSAGE \
                    else _message_to_presence_dict(value[key])
            out[fd.name] = inner
        elif fd.type == fd.TYPE_MESSAGE:
            # Structural repetition check (label access is deprecated in
            # protobuf 6.x): a singular message IS a Message instance; a
            # repeated field yields a composite container.
            if isinstance(value, Message):
                out[fd.name] = _message_to_presence_dict(value)
            else:
                out[fd.name] = [_message_to_presence_dict(item) for item in value]
        elif fd.type == fd.TYPE_ENUM:
            out[fd.name] = _enum_name(fd, value)
        else:
            out[fd.name] = value
    return out


def iter_instrument_feeds(
    response: "pb.FeedResponse",
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(instrument_key, feed_dict)`` for every instrument in the
    frame. ``feed_dict`` carries only wire-present keys (P-ZERO); branch
    routing is the caller's job via ``which_feed_union`` or key checks
    ('ltpc' vs 'fullFeed' vs 'firstLevelWithGreeks').
    """
    for key in response.feeds:
        yield key, _message_to_presence_dict(response.feeds[key])
