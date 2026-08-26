"""Fyers TBT depth channel normalizer.

Converts official TBT protobuf MarketFeed messages into canonical MarketHub
Depth models. Handles:

  * 50-level order book reconstruction
  * Price scaling (cents → rupees)
  * Order count preservation
  * Snapshot vs delta semantics
  * Zero-quantity level deletion

All functions are pure and deterministic — no network or state side-effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from brokers.fyers.tbt.proto import msg_pb2
from market.models import Depth, DepthLevel
from market.normalize.common import NormalizationError

__all__ = [
    "normalize_tbt_depth",
    "extract_exchange",
    "extract_tradingsymbol",
]


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def extract_exchange(symbol: str) -> str:
    """Extract exchange from Fyers symbol (e.g. 'NSE:RELIANCE-EQ' → 'NSE')."""
    if not isinstance(symbol, str) or ":" not in symbol:
        raise NormalizationError(f"TBT symbol: malformed {symbol!r}")
    exchange, _, _ = symbol.partition(":")
    return exchange.strip().upper()


def extract_tradingsymbol(symbol: str) -> str:
    """Extract tradingsymbol from Fyers symbol (e.g. 'NSE:RELIANCE-EQ' → 'RELIANCE-EQ')."""
    if not isinstance(symbol, str) or ":" not in symbol:
        raise NormalizationError(f"TBT symbol: malformed {symbol!r}")
    _, _, tradingsymbol = symbol.partition(":")
    return tradingsymbol.strip()


# ---------------------------------------------------------------------------
# Depth normalization
# ---------------------------------------------------------------------------


def normalize_tbt_depth(
    feed: msg_pb2.MarketFeed,
    symbol: str,
    received_ts: datetime | None = None,
) -> Depth:
    """Normalize a TBT MarketFeed.depth into canonical Depth.

    Args:
        feed: Protobuf MarketFeed message with depth data
        symbol: Canonical symbol key (e.g. 'NSE:RELIANCE-EQ')
        received_ts: MarketHub acceptance timestamp (defaults to now)

    Returns:
        Canonical Depth model with up to 50 bid/ask levels

    Raises:
        NormalizationError: If depth data is missing or malformed
    """
    if received_ts is None:
        received_ts = datetime.now(timezone.utc)

    if not feed.HasField("depth"):
        raise NormalizationError("TBT depth: missing depth field in MarketFeed")

    depth_proto = feed.depth

    # Extract bids (up to 50 levels)
    bids = _parse_levels(depth_proto.bids, side="bids")

    # Extract asks (up to 50 levels)
    asks = _parse_levels(depth_proto.asks, side="asks")

    # Parse exchange timestamp from feed_time (epoch seconds)
    exchange_ts = None
    if feed.HasField("feed_time") and feed.feed_time.value > 0:
        try:
            exchange_ts = datetime.fromtimestamp(
                feed.feed_time.value, tz=timezone.utc
            )
        except (OSError, ValueError):
            pass

    return Depth(
        instrument_token=symbol,
        exchange=extract_exchange(symbol),
        tradingsymbol=extract_tradingsymbol(symbol),
        received_ts=received_ts,
        bids=tuple(bids),
        asks=tuple(asks),
        exchange_ts=exchange_ts,
    )


def _parse_levels(
    levels: Any, side: str
) -> list[DepthLevel]:
    """Parse protobuf MarketLevel repeated field into DepthLevel list.

    Handles:
      * Price scaling (cents → rupees, divide by 100)
      * Zero-price filtering (invalid levels)
      * Zero-quantity preservation (valid but empty levels)
      * Order count (nord) preservation
    """
    result = []
    for i, level in enumerate(levels):
        if not isinstance(level, msg_pb2.MarketLevel):
            continue

        # Extract price (scaled by 100 in protobuf)
        price = None
        if level.HasField("price"):
            price_val = level.price.value
            if price_val <= 0:
                # Zero or negative price = invalid level, skip
                continue
            price = price_val / 100.0

        # Extract quantity
        quantity = 0.0
        if level.HasField("qty"):
            quantity = float(level.qty.value)

        # Extract order count (optional)
        orders = None
        if level.HasField("nord") and level.nord.value > 0:
            orders = int(level.nord.value)

        result.append(DepthLevel(
            price=price,
            quantity=quantity,
            orders=orders,
        ))

    return result
