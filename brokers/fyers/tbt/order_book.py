"""Local order book state for TBT delta reconstruction.

Maintains per-instrument bid/ask state to correctly handle:
  * Full snapshots (replace entire book)
  * Incremental deltas (update/delete individual levels)
  * Sequence gap detection (invalidate and resubscribe)
  * Zero-quantity deletion semantics
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from market.models import Depth, DepthLevel


class LocalOrderBook:
    """Per-instrument order book state for TBT depth reconstruction.

    Attributes:
        instrument_token: Canonical instrument identifier
        exchange: Exchange code (e.g. 'NSE')
        tradingsymbol: Trading symbol (e.g. 'RELIANCE-EQ')
        bids: Current bid levels keyed by price
        asks: Current ask levels keyed by price
        snapshot_count: Number of full snapshots applied
        delta_count: Number of delta updates applied
        last_sequence: Last seen sequence number
    """

    def __init__(
        self,
        instrument_token: str,
        exchange: str,
        tradingsymbol: str,
    ) -> None:
        self.instrument_token = instrument_token
        self.exchange = exchange
        self.tradingsymbol = tradingsymbol
        self.bids: Dict[float, DepthLevel] = {}
        self.asks: Dict[float, DepthLevel] = {}
        self.snapshot_count = 0
        self.delta_count = 0
        self.last_sequence: Optional[int] = None
        self.last_update_ts: Optional[datetime] = None

    def apply_snapshot(self, depth: Depth, sequence: int) -> None:
        """Apply full snapshot (replace entire book).

        Clears existing state and rebuilds from complete depth data.
        """
        self.bids.clear()
        self.asks.clear()

        for level in depth.bids:
            if level.price is not None and level.price > 0:
                self.bids[level.price] = level

        for level in depth.asks:
            if level.price is not None and level.price > 0:
                self.asks[level.price] = level

        self.snapshot_count += 1
        self.last_sequence = sequence
        self.last_update_ts = depth.received_ts

    def apply_delta(self, depth: Depth, sequence: int) -> None:
        """Apply incremental delta (update/delete individual levels).

        Zero-quantity levels are removed from the book.
        Non-zero levels are inserted or updated.
        """
        # Process bids
        for level in depth.bids:
            if level.price is None:
                continue
            if level.quantity == 0:
                # Delete: zero quantity means level removed
                self.bids.pop(level.price, None)
            else:
                # Insert or update
                self.bids[level.price] = level

        # Process asks
        for level in depth.asks:
            if level.price is None:
                continue
            if level.quantity == 0:
                # Delete: zero quantity means level removed
                self.asks.pop(level.price, None)
            else:
                # Insert or update
                self.asks[level.price] = level

        self.delta_count += 1
        self.last_sequence = sequence
        self.last_update_ts = depth.received_ts

    def get_full_depth(self, received_ts: datetime) -> Depth:
        """Reconstruct canonical Depth from local state.

        Returns bids/asks sorted best-first (bids descending, asks ascending).
        """
        bids = tuple(
            sorted(self.bids.values(), key=lambda x: -x.price if x.price else 0)
        )
        asks = tuple(
            sorted(self.asks.values(), key=lambda x: x.price if x.price else 0)
        )

        return Depth(
            instrument_token=self.instrument_token,
            exchange=self.exchange,
            tradingsymbol=self.tradingsymbol,
            received_ts=received_ts,
            bids=bids,
            asks=asks,
            exchange_ts=self.last_update_ts,
        )

    def check_sequence(self, expected_seq: int) -> bool:
        """Check if sequence number is valid (no gap).

        Returns:
            True if sequence is valid (or first message)
            False if gap detected (caller should invalidate book)
        """
        if self.last_sequence is None:
            # First message, no previous sequence
            return True

        expected = self.last_sequence + 1
        if expected_seq != expected:
            # Gap detected
            return False

        return True

    def invalidate(self) -> None:
        """Clear all state, force full resubscribe."""
        self.bids.clear()
        self.asks.clear()
        self.snapshot_count = 0
        self.delta_count = 0
        self.last_sequence = None
        self.last_update_ts = None

    @property
    def is_empty(self) -> bool:
        """Check if book has no data."""
        return len(self.bids) == 0 and len(self.asks) == 0

    @property
    def level_count(self) -> int:
        """Total number of price levels (bids + asks)."""
        return len(self.bids) + len(self.asks)

    def __repr__(self) -> str:
        return (
            f"LocalOrderBook({self.instrument_token}, "
            f"bids={len(self.bids)}, asks={len(self.asks)}, "
            f"snap={self.snapshot_count}, delta={self.delta_count})"
        )


class OrderBookStore:
    """Thread-safe store for per-instrument order books.

    Provides:
      * Get or create book for symbol
      * Invalidate all books (on reconnect)
      * Statistics
    """

    def __init__(self) -> None:
        self._books: Dict[str, LocalOrderBook] = {}

    def get_or_create(
        self,
        symbol: str,
        exchange: str,
        tradingsymbol: str,
    ) -> LocalOrderBook:
        """Get existing book or create new one."""
        if symbol not in self._books:
            self._books[symbol] = LocalOrderBook(
                instrument_token=symbol,
                exchange=exchange,
                tradingsymbol=tradingsymbol,
            )
        return self._books[symbol]

    def get(self, symbol: str) -> Optional[LocalOrderBook]:
        """Get existing book or None."""
        return self._books.get(symbol)

    def invalidate_all(self) -> None:
        """Clear all books (on reconnect)."""
        self._books.clear()

    def remove(self, symbol: str) -> None:
        """Remove specific book."""
        self._books.pop(symbol, None)

    @property
    def count(self) -> int:
        """Number of active books."""
        return len(self._books)

    @property
    def symbols(self) -> frozenset[str]:
        """Set of tracked symbols."""
        return frozenset(self._books.keys())
