"""Fyers TBT (Total Book) async WebSocket depth feed.

Provides 50-level market depth via the official Fyers TBT protocol,
supplementing the existing HSM quote feed. Both feeds update the same
canonical MarketService state.

Protocol reference:
  * Official implementation: fyers-apiv3.FyersWebsocket.tbt_ws
  * WebSocket URL: dynamically fetched from https://api-t1.fyers.in/indus/home/tbtws
  * Subscription: JSON control messages
  * Data: Protobuf binary (SocketMessage with MarketFeed)
  * Depth: Up to 50 levels with price, quantity, order counts

Lifecycle:
  1. Fetch WebSocket URL from Fyers API
  2. Connect with Authorization header
  3. Subscribe to channels with symbols
  4. Receive protobuf depth updates
  5. Reconstruct local order book (snapshot + deltas)
  6. Publish canonical Depth to MarketService
  7. Reconnect on transient failures
  8. Stop on auth failures or explicit shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Set

import websockets
import httpx
from websockets.client import connect as ws_connect

from brokers.fyers.tbt.normalizer import normalize_tbt_depth
from brokers.fyers.tbt.order_book import LocalOrderBook, OrderBookStore
from brokers.fyers.tbt.proto import msg_pb2
from market.models import Depth
from market.normalize.common import NormalizationError

logger = logging.getLogger(__name__)

# Official rate limits (verified from fyers-apiv3 implementation)
MAX_CONNECTIONS_PER_USER = 3
SYMBOLS_PER_CONNECTION = 5
MAX_CHANNELS_PER_CONNECTION = 50

# TBT protocol constants
TBT_SUBSCRIBE_TYPE = 1
TBT_CHANNEL_CONTROL_TYPE = 2
TBT_PING_MESSAGE = "ping"


class FyersTbtFeed:
    """Async-native Fyers TBT depth feed.

    Supplements existing HSM feed with 50-level depth data.
    Reuses existing OAuth access token and MarketService.
    """

    def __init__(
        self,
        access_token_getter: Callable[[], str],
        config: dict[str, Any],
        market_service: Any,
    ) -> None:
        """Initialize TBT feed.

        Args:
            access_token_getter: Callable that returns current OAuth token
            config: Source configuration (includes tbt_enabled flag)
            market_service: Shared MarketService instance
        """
        self._token_getter = access_token_getter
        self._market_service = market_service
        self._config = config

        # State
        self._state = "disconnected"
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._desired_symbols: Set[str] = set()
        self._lock = asyncio.Lock()

        # Order book state
        self._books = OrderBookStore()

        # Stats
        self._stats = {
            "snapshots": 0,
            "deltas": 0,
            "malformed": 0,
            "sequence_gaps": 0,
            "reconnects": 0,
            "subscribes": 0,
            "unsubscribes": 0,
            "last_message_at": None,
            "last_error": None,
        }

        # WebSocket
        self._ws = None
        self._ws_url = None

        # Channel management
        self._channel_map: Dict[str, int] = {}  # symbol -> channel
        self._next_channel = 1

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def run(self, publisher: Any, stop_event: asyncio.Event) -> None:
        """Main lifecycle loop with reconnect logic.

        Args:
            publisher: Event publisher (unused, for compatibility)
            stop_event: Global stop event
        """
        logger.info("TBT feed: starting")
        self._state = "starting"

        try:
            while not stop_event.is_set():
                outcome = await self._run_session(stop_event)
                if outcome is None or stop_event.is_set():
                    break
                if self._state != "auth_required":
                    logger.info("TBT feed: session ended, stopping")
                    break
                # Auth failure - exit loop
                break

                # Transient failure - reconnect
                if not stop_event.is_set():
                    delay = self._calculate_backoff()
                    logger.warning(
                        "TBT feed: reconnecting in %.1fs (attempt %d)",
                        delay,
                        self._stats["reconnects"] + 1,
                    )
                    self._stats["reconnects"] += 1
                    await asyncio.sleep(delay)

        finally:
            await self._cleanup()
            self._state = "stopped"
            logger.info("TBT feed: stopped")

    async def _run_session(self, stop_event: asyncio.Event) -> Optional[str]:
        """Single connection session with reconnect logic."""
        try:
            # Get auth token
            token = await asyncio.to_thread(self._token_getter)
            if not token or not token.strip():
                self._set_state("auth_required")
                return "auth_required"

            # Fetch WebSocket URL
            ws_url = await self._fetch_ws_url(token)
            if not ws_url:
                logger.error("TBT feed: failed to fetch WebSocket URL")
                self._stats["last_error"] = "failed_to_fetch_url"
                return "transient"

            self._ws_url = ws_url

            # Connect and subscribe
            async with ws_connect(
                ws_url,
                additional_headers={"Authorization": token},
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                self._ws = ws
                self._set_state("connected")
                logger.info("TBT feed: connected to %s", ws_url[:50] + "...")

                # Subscribe to desired symbols
                await self._resubscribe_all(ws)

                # Receive loop
                await self._recv_loop(ws, stop_event)

                if stop_event.is_set():
                    return None

                return "transient"  # Connection lost

        except Exception as e:
            logger.error("TBT feed: session error: %s", e)
            self._stats["last_error"] = str(e)
            return "transient"

    async def _recv_loop(self, ws: Any, stop_event: asyncio.Event) -> None:
        """Receive and process WebSocket messages."""
        try:
            async for message in ws:
                if stop_event.is_set():
                    break

                if message == TBT_PING_MESSAGE:
                    continue

                await self._handle_message(message)

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("TBT feed: connection closed: %s", e)
        except Exception as e:
            logger.error("TBT feed: recv loop error: %s", e)
            self._stats["last_error"] = str(e)

    async def _handle_message(self, message: Any) -> None:
        """Parse and dispatch protobuf message."""
        try:
            socket_msg = msg_pb2.SocketMessage()
            socket_msg.ParseFromString(message)

            if socket_msg.error:
                logger.warning("TBT feed: server error: %s", socket_msg.msg)
                self._stats["last_error"] = socket_msg.msg
                return

            # Process each symbol's feed
            for symbol, feed in socket_msg.feeds.items():
                await self._process_feed(symbol, feed)

            self._stats["last_message_at"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            logger.error("TBT feed: malformed message: %s", e)
            self._stats["malformed"] += 1
            self._stats["last_error"] = f"malformed: {e}"

    async def _process_feed(self, symbol: str, feed: msg_pb2.MarketFeed) -> None:
        """Process a single symbol's MarketFeed."""
        # Extract sequence number
        sequence = feed.sequence_no

        # Get or create order book
        exchange = self._extract_exchange(symbol)
        tradingsymbol = self._extract_tradingsymbol(symbol)
        book = self._books.get_or_create(symbol, exchange, tradingsymbol)

        # Check sequence
        if not book.check_sequence(sequence):
            logger.warning(
                "TBT feed: sequence gap for %s (expected %d, got %d)",
                symbol,
                book.last_sequence + 1 if book.last_sequence else 0,
                sequence,
            )
            self._stats["sequence_gaps"] += 1
            book.invalidate()
            # Request resubscribe for this symbol
            await self._resubscribe_symbol(symbol)
            return

        # Normalize depth
        received_ts = datetime.now(timezone.utc)
        try:
            depth = normalize_tbt_depth(feed, symbol, received_ts)
        except NormalizationError as e:
            logger.error("TBT feed: normalization error for %s: %s", symbol, e)
            return

        # Apply snapshot or delta
        if feed.snapshot:
            book.apply_snapshot(depth, sequence)
            self._stats["snapshots"] += 1
        else:
            book.apply_delta(depth, sequence)
            self._stats["deltas"] += 1

        # Publish to MarketService
        if self._market_service:
            full_depth = book.get_full_depth(received_ts)
            try:
                await self._market_service.apply_depth(full_depth)
            except Exception as e:
                logger.error("TBT feed: failed to apply depth: %s", e)

    # -------------------------------------------------------------------------
    # Subscription management
    # -------------------------------------------------------------------------

    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to additional symbols at runtime."""
        async with self._lock:
            new_symbols = [s for s in symbols if s not in self._desired_symbols]
            self._desired_symbols.update(new_symbols)

        # If connected, send subscribe immediately
        if self._ws and self._ws.open:
            for symbol in new_symbols:
                await self._send_subscribe(symbol)

        logger.info("TBT feed: subscribed to %d symbols (total: %d)", len(new_symbols), len(self._desired_symbols))
        self._stats["subscribes"] += len(new_symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        """Unsubscribe from symbols at runtime."""
        async with self._lock:
            removed = [s for s in symbols if s in self._desired_symbols]
            self._desired_symbols.difference_update(removed)

        # If connected, send unsubscribe immediately
        if self._ws and self._ws.open:
            for symbol in removed:
                await self._send_unsubscribe(symbol)

        logger.info("TBT feed: unsubscribed from %d symbols (remaining: %d)", len(removed), len(self._desired_symbols))
        self._stats["unsubscribes"] += len(removed)

    async def _resubscribe_all(self, ws: Any) -> None:
        """Resubscribe to all desired symbols after reconnect."""
        async with self._lock:
            symbols = list(self._desired_symbols)

        for symbol in symbols:
            await self._send_subscribe(symbol)

        logger.info("TBT feed: resubscribed to %d symbols after reconnect", len(symbols))

    async def _resubscribe_symbol(self, symbol: str) -> None:
        """Resubscribe to a single symbol (after sequence gap)."""
        if self._ws and self._ws.open:
            await self._send_unsubscribe(symbol)
            await self._send_subscribe(symbol)

    async def _send_subscribe(self, symbol: str) -> None:
        """Send subscribe message for a symbol."""
        if not self._ws or not self._ws.open:
            return

        channel = self._get_or_create_channel(symbol)
        msg = {
            "type": TBT_SUBSCRIBE_TYPE,
            "data": {
                "subs": 1,
                "symbols": [symbol],
                "mode": "depth",
                "channel": str(channel),
            },
        }
        await self._ws.send(json.dumps(msg))
        logger.debug("TBT feed: subscribed to %s on channel %d", symbol, channel)

    async def _send_unsubscribe(self, symbol: str) -> None:
        """Send unsubscribe message for a symbol."""
        if not self._ws or not self._ws.open:
            return

        channel = self._channel_map.get(symbol, 1)
        msg = {
            "type": TBT_SUBSCRIBE_TYPE,
            "data": {
                "subs": -1,
                "symbols": [symbol],
                "mode": "depth",
                "channel": str(channel),
            },
        }
        await self._ws.send(json.dumps(msg))
        logger.debug("TBT feed: unsubscribed from %s", symbol)

    def _get_or_create_channel(self, symbol: str) -> int:
        """Get or create a channel for a symbol."""
        if symbol not in self._channel_map:
            if self._next_channel <= MAX_CHANNELS_PER_CONNECTION:
                self._channel_map[symbol] = self._next_channel
                self._next_channel += 1
            else:
                # Reuse channel 1 (shouldn't happen with proper limits)
                self._channel_map[symbol] = 1
        return self._channel_map[symbol]

    # -------------------------------------------------------------------------
    # WebSocket URL fetch
    # -------------------------------------------------------------------------

    async def _fetch_ws_url(self, token: str) -> Optional[str]:
        """Fetch dynamic WebSocket URL from Fyers API."""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    "https://api-t1.fyers.in/indus/home/tbtws",
                    headers={"Authorization": token},
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("data", {}).get("socket_url")
                else:
                    logger.error("TBT feed: failed to fetch URL: %d", resp.status_code)
        except Exception as e:
            logger.error("TBT feed: error fetching URL: %s", e)
        return None

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _calculate_backoff(self) -> float:
        """Calculate reconnect delay with exponential backoff."""
        attempts = self._stats["reconnects"]
        # Exponential backoff: 1s, 2s, 4s, 8s, ..., max 60s
        delay = min(2 ** min(attempts, 6), 60)
        # Add jitter
        delay *= (0.5 + 0.5 * __import__("random").random())
        return delay

    def _set_state(self, state: str) -> None:
        """Update feed state."""
        self._state = state
        logger.debug("TBT feed: state -> %s", state)

    def _extract_exchange(self, symbol: str) -> str:
        """Extract exchange from symbol."""
        if ":" in symbol:
            return symbol.split(":")[0].upper()
        return "UNKNOWN"

    def _extract_tradingsymbol(self, symbol: str) -> str:
        """Extract tradingsymbol from symbol."""
        if ":" in symbol:
            return symbol.split(":", 1)[1]
        return symbol

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._books.invalidate_all()
        self._ws = None
        self._set_state("disconnected")

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return current status and statistics."""
        return {
            "enabled": self._config.get("tbt_enabled", False),
            "state": self._state,
            "task_running": self._task is not None and not self._task.done(),
            "desired_count": len(self._desired_symbols),
            "subscribed_count": self._books.count,
            "snapshot_count": self._stats["snapshots"],
            "delta_count": self._stats["deltas"],
            "malformed_count": self._stats["malformed"],
            "sequence_gap_count": self._stats["sequence_gaps"],
            "reconnect_count": self._stats["reconnects"],
            "last_message_at": self._stats["last_message_at"],
            "safe_last_error": self._stats["last_error"],
            "symbols": list(self._desired_symbols),
        }

    @property
    def is_connected(self) -> bool:
        """Check if feed is connected."""
        return self._state == "connected"

    @property
    def desired_symbols(self) -> Set[str]:
        """Get desired symbols set."""
        return self._desired_symbols.copy()


