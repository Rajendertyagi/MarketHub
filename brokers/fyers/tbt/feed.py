"""Fyers TBT (Total Book) async WebSocket depth feed.

Provides 50-level market depth via the official Fyers TBT protocol,
supplementing the existing HSM quote feed. Both feeds update the same
canonical MarketService state.

Protocol reference (official docs, verified 2026-11-10):
  * Connection: https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/tbt-websocket-connection/
    - Endpoint: wss://rtsocket-api.fyers.in/versova
    - Header: Authorization: <appId:accessToken>
  * Channels: https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/concept-of-channels/
    - type 1 subscribe/unsubscribe (subs 1/-1, mode depth, channel string)
    - type 2 switch-channel with resumeChannels/pauseChannels (required to start stream)
  * Dynamic URL discovery: undocumented credential-bearing discovery to
    https://api-t1.fyers.in/indus/home/tbtws is disabled (security-first;
    endpoint necessity/auth contract not authoritatively verified).

Lifecycle:
  1. Resolve app_id and raw access token (canonical token storage unchanged)
  2. Resolve WebSocket URL via _fetch_ws_url() (returns fixed documented endpoint;
     no HTTP discovery, no credential transmission to unverified endpoint)
  3. Connect with Authorization: <appId:accessToken> (official TBT WS contract)
  4. Subscribe (type 1) and resume channels (type 2)
  5. Receive protobuf depth updates (snapshot + deltas)
  6. Reconstruct local order book and publish canonical Depth to MarketService
  7. Reconnect on transient failures; stop on auth failures or explicit shutdown
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.asyncio.client import connect as ws_connect

from brokers.fyers.tbt.normalizer import normalize_tbt_depth
from brokers.fyers.tbt.order_book import OrderBookStore
from brokers.fyers.tbt.proto import msg_pb2
from market.normalize.common import NormalizationError

logger = logging.getLogger(__name__)

# Official TBT limits (FYERS docs: plans-and-limits, TBT channels/connection)
# - Channels per connection: 50 (1-50)
# - Connections per app per user: Standard 1, Prime 3
# - Symbols per connection (market depth): Standard 1, Prime 5
# - Symbols per user across all connections (TBT): Standard 1, Prime 15
# Account-wide/per-user-across-connections enforcement beyond this feed instance
# requires external coordination; this feed enforces documented per-connection and
# per-user ceilings for its own desired/subscribed set and fails gracefully.
MAX_CHANNELS_PER_CONNECTION = 50
_TBT_TIER_LIMITS = {
    "standard": {
        "connections_per_user": 1,
        "symbols_per_connection": 1,
        "symbols_per_user": 1,
    },
    "prime": {
        "connections_per_user": 3,
        "symbols_per_connection": 5,
        "symbols_per_user": 15,
    },
}

# TBT protocol constants
TBT_SUBSCRIBE_TYPE = 1
TBT_CHANNEL_CONTROL_TYPE = 2
TBT_PING_MESSAGE = "ping"


def _socket_is_open(ws: Any) -> bool:
    """True when the socket can still be written to (duck-typed)."""
    if ws is None:
        return False
    open_attr = getattr(ws, "open", None)
    if isinstance(open_attr, bool):
        return open_attr
    closed = getattr(ws, "closed", None)
    if isinstance(closed, bool):
        return not closed
    state = getattr(ws, "state", None)
    if state is not None:
        name = str(getattr(state, "name", state)).upper()
        if "OPEN" in name:
            return True
        if name:
            return False
    return True


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
        app_id_getter: Callable[[], str] | None = None,
    ) -> None:
        """Initialize TBT feed.

        Args:
            access_token_getter: Callable that returns current raw OAuth access token.
            config: Source configuration (includes tbt_enabled flag).
            market_service: Shared MarketService instance.
            app_id_getter: Optional callable returning FYERS app_id for TBT WS auth
                (Authorization: <appId:accessToken>). If omitted, config["app_id"]
                is used as fallback.
        """
        self._token_getter = access_token_getter
        self._app_id_getter = app_id_getter
        self._market_service = market_service
        self._config = config

        # State
        self._state = "disconnected"
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()  # internal stop signal (also set by stop())
        self._caller_stop_event: asyncio.Event | None = None
        self._desired_symbols: set[str] = set()
        self._lock = asyncio.Lock()
        self._ws_lock = asyncio.Lock()

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
        self._active_recv_task: asyncio.Task | None = None

        # Channel management
        self._channel_map: dict[str, int] = {}  # symbol -> channel
        self._next_channel = 1
        self._active_channels: set[int] = set()  # channels resumed on current connection

        # Entitlement-aware limits (resolved lazily; tier may be provided in config)
        self._tbt_limits: dict[str, Any] | None = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def _is_stop_requested(self, stop_event: asyncio.Event | None = None) -> bool:
        if self._stop_event.is_set():
            return True
        if stop_event is not None and stop_event.is_set():
            return True
        caller = self._caller_stop_event
        return caller is not None and caller.is_set()

    async def stop(self) -> None:
        """Idempotent stop request.

        Signals shutdown and closes the active WebSocket to interrupt a blocked
        receive promptly. Does not own/cancel the parent-managed run task.
        Safe before/during/after run and on repeated calls.
        """
        self._stop_event.set()
        await self._close_active_ws()

    async def _close_active_ws(self) -> None:
        async with self._ws_lock:
            ws = self._ws
            self._ws = None
        if ws is None:
            return
        try:
            await ws.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("TBT feed: ws close ignored: %s", type(exc).__name__)

    async def run(self, publisher: Any, stop_event: asyncio.Event) -> None:
        """Main lifecycle loop with reconnect logic.

        Args:
            publisher: Event publisher (unused, for compatibility)
            stop_event: Global stop event
        """
        del publisher
        self._caller_stop_event = stop_event
        logger.info("TBT feed: starting")
        self._state = "starting"

        try:
            while not self._is_stop_requested(stop_event):
                outcome = await self._run_session(stop_event)
                if outcome is None or self._is_stop_requested(stop_event):
                    break
                if self._state == "auth_required":
                    logger.info("TBT feed: auth required, stopping until re-login")
                    break
                # Transient failure - reconnect with backoff. Interruptible so
                # explicit shutdown is responsive even during backoff.
                delay = self._calculate_backoff()
                logger.warning(
                    "TBT feed: reconnecting in %.1fs (attempt %d)",
                    delay,
                    self._stats["reconnects"] + 1,
                )
                self._stats["reconnects"] += 1
                stopped = await self._wait_or_stop(stop_event, delay)
                if stopped:
                    break

        except asyncio.CancelledError:
            # Ensure prompt shutdown and cleanup on parent cancellation.
            self._stop_event.set()
            await self._close_active_ws()
            raise
        finally:
            try:
                await self._cleanup()
            finally:
                if self._state != "auth_required":
                    self._state = "stopped"
                logger.info("TBT feed: stopped")

    async def _wait_or_stop(self, stop_event: asyncio.Event, delay: float) -> bool:
        """Wait for delay or stop (caller/internal). Returns True if stopped."""
        if self._is_stop_requested(stop_event):
            return True
        waiters = [asyncio.create_task(stop_event.wait()),
                   asyncio.create_task(self._stop_event.wait()),
                   asyncio.create_task(asyncio.sleep(delay))]
        try:
            done, _pending = await asyncio.wait(
                set(waiters), return_when=asyncio.FIRST_COMPLETED
            )
            return waiters[0] in done or waiters[1] in done
        finally:
            for t in waiters:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _run_session(self, stop_event: asyncio.Event) -> str | None:
        """Single connection session with reconnect logic."""
        ws = None
        try:
            # Get auth token (raw; canonical storage unchanged)
            token = await asyncio.to_thread(self._token_getter)
            if not token or not str(token).strip():
                self._set_state("auth_required")
                return "auth_required"
            token = str(token).strip()

            # Resolve app_id for official TBT WS auth header (<appId:accessToken>)
            app_id = await self._resolve_app_id()
            if not app_id:
                self._set_state("auth_required")
                self._stats["last_error"] = "missing_app_id"
                logger.error("TBT feed: missing app_id for TBT WebSocket auth")
                return "auth_required"

            # Resolve WebSocket URL. Official docs specify fixed endpoint
            # wss://rtsocket-api.fyers.in/versova with Authorization: <appId:accessToken>.
            # Undocumented credential-bearing dynamic discovery is not used.
            ws_url = await self._fetch_ws_url(token)
            if not ws_url:
                logger.error("TBT feed: failed to resolve WebSocket URL")
                self._stats["last_error"] = "failed_to_resolve_url"
                return "transient"

            self._ws_url = ws_url
            ws_auth = f"{app_id}:{token}"

            # Connect and subscribe
            async with ws_connect(
                ws_url,
                additional_headers={"Authorization": ws_auth},
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                async with self._ws_lock:
                    self._ws = ws
                self._active_channels.clear()
                self._set_state("connected")
                logger.info("TBT feed: connected")

                # Subscribe to desired symbols and resume channels
                await self._resubscribe_all(ws)

                # Receive loop (interruptible on stop/cancel)
                reason = await self._recv_loop(ws, stop_event)

                if self._is_stop_requested(stop_event) or reason == "stopped":
                    return None

                return "transient"  # Connection lost

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("TBT feed: session error: %s", type(e).__name__)
            self._stats["last_error"] = type(e).__name__
            return "transient"
        finally:
            async with self._ws_lock:
                if self._ws is ws:
                    self._ws = None
            self._active_channels.clear()

    async def _resolve_app_id(self) -> str:
        getter = self._app_id_getter
        if getter is not None:
            try:
                value = getter()
                if asyncio.iscoroutine(value):
                    value = await value
            except Exception:
                value = None
            if isinstance(value, str) and value.strip():
                return value.strip()
        cfg_app_id = self._config.get("app_id")
        if isinstance(cfg_app_id, str) and cfg_app_id.strip():
            return cfg_app_id.strip()
        return ""

    async def _recv_loop(self, ws: Any, stop_event: asyncio.Event) -> str:
        """Receive and process WebSocket messages (interruptible)."""
        stop_task = asyncio.create_task(stop_event.wait())
        internal_stop_task = asyncio.create_task(self._stop_event.wait())
        recv_task: asyncio.Task | None = None
        try:
            while True:
                if self._is_stop_requested(stop_event):
                    return "stopped"
                recv_task = asyncio.create_task(ws.recv())
                self._active_recv_task = recv_task
                done, _pending = await asyncio.wait(
                    {recv_task, stop_task, internal_stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done or internal_stop_task in done:
                    if recv_task in done and not recv_task.cancelled():
                        try:
                            recv_task.result()
                        except Exception:
                            pass
                    else:
                        recv_task.cancel()
                        await asyncio.gather(recv_task, return_exceptions=True)
                    self._active_recv_task = None
                    recv_task = None
                    return "stopped"
                try:
                    message = recv_task.result()
                    self._active_recv_task = None
                    recv_task = None
                except asyncio.CancelledError:
                    self._active_recv_task = None
                    recv_task = None
                    raise
                except websockets.exceptions.ConnectionClosed as e:
                    logger.warning("TBT feed: connection closed: %s", type(e).__name__)
                    self._active_recv_task = None
                    recv_task = None
                    return "closed"
                except Exception as e:
                    logger.error("TBT feed: recv error: %s", type(e).__name__)
                    self._stats["last_error"] = type(e).__name__
                    self._active_recv_task = None
                    recv_task = None
                    return "transport"

                if message == TBT_PING_MESSAGE:
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("TBT feed: recv loop error: %s", type(e).__name__)
            self._stats["last_error"] = type(e).__name__
            return "transport"
        finally:
            if recv_task is not None and not recv_task.done():
                recv_task.cancel()
            for t in (stop_task, internal_stop_task):
                if not t.done():
                    t.cancel()
            tasks_to_gather = []
            if recv_task is not None:
                tasks_to_gather.append(recv_task)
            tasks_to_gather.extend([stop_task, internal_stop_task])
            if tasks_to_gather:
                await asyncio.gather(*tasks_to_gather, return_exceptions=True)
            self._active_recv_task = None

    async def _handle_message(self, message: Any) -> None:
        """Parse and dispatch protobuf message."""
        try:
            socket_msg = msg_pb2.SocketMessage()
            socket_msg.ParseFromString(message)

            # Server error response. Official docs: error=true, empty feeds, msg
            # describes the error. No authoritative evidence in available docs/SDK
            # artifacts/proto inspected establishes a positive per-subscribe/resume
            # acknowledgement or request/symbol/channel correlation, so ws.send()
            # success must not be treated as server acceptance and this error cannot
            # be safely attributed to a specific request. Record for diagnostics only
            # without mutating desired subscriptions or channel mappings.
            if socket_msg.error:
                logger.warning("TBT feed: server error: %s", socket_msg.msg)
                self._stats["last_error"] = socket_msg.msg
                return

            # Process each symbol's feed
            for symbol, feed in socket_msg.feeds.items():
                await self._process_feed(symbol, feed)

            self._stats["last_message_at"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            logger.error("TBT feed: malformed message: %s", type(e).__name__)
            self._stats["malformed"] += 1
            self._stats["last_error"] = f"malformed: {type(e).__name__}"

    async def _process_feed(self, symbol: str, feed: msg_pb2.MarketFeed) -> None:
        """Process a single symbol's MarketFeed."""
        # Extract sequence number
        sequence = feed.sequence_no

        # Get or create order book
        exchange = self._extract_exchange(symbol)
        tradingsymbol = self._extract_tradingsymbol(symbol)
        book = self._books.get_or_create(symbol, exchange, tradingsymbol)

        # Normalize depth
        received_ts = datetime.now(timezone.utc)
        try:
            depth = normalize_tbt_depth(feed, symbol, received_ts)
        except NormalizationError as e:
            logger.error("TBT feed: normalization error for %s: %s", symbol, e)
            return

        # Apply snapshot or delta. Deltas require a valid snapshot-initialized
        # book and in-order sequence; snapshots (re)initialize the book.
        if feed.snapshot:
            book.apply_snapshot(depth, sequence)
            self._stats["snapshots"] += 1
        else:
            if not book.check_sequence(sequence):
                if book.awaiting_snapshot:
                    logger.warning(
                        "TBT feed: delta before fresh snapshot for %s (seq %d), awaiting snapshot",
                        symbol,
                        sequence,
                    )
                else:
                    logger.warning(
                        "TBT feed: sequence gap for %s (expected %d, got %d)",
                        symbol,
                        book.last_sequence + 1 if book.last_sequence is not None else 0,
                        sequence,
                    )
                    self._stats["sequence_gaps"] += 1
                book.invalidate()
                # Request resubscribe for this symbol to obtain a fresh snapshot.
                await self._resubscribe_symbol(symbol)
                return
            book.apply_delta(depth, sequence)
            self._stats["deltas"] += 1

        # Publish to MarketService only when a valid book state is available.
        if self._market_service and not book.awaiting_snapshot:
            full_depth = book.get_full_depth(received_ts)
            try:
                await self._market_service.apply_depth(full_depth)
            except Exception as e:
                logger.error("TBT feed: failed to apply depth: %s", type(e).__name__)

    # -------------------------------------------------------------------------
    # Subscription management
    # -------------------------------------------------------------------------

    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to additional symbols at runtime (entitlement-aware)."""
        limits = self._resolve_tbt_limits()
        per_user = int(limits.get("symbols_per_user", 1))
        per_conn = int(limits.get("symbols_per_connection", 1))
        # This feed instance manages one TBT connection; enforce per-connection and
        # per-user (for this instance's desired set) ceilings. Account-wide limits
        # across multiple connections/users require external coordination (documented).
        effective_limit = min(per_user, per_conn)

        async with self._lock:
            seen: set[str] = set()
            candidates: list[str] = []
            for s in symbols:
                if s in seen or s in self._desired_symbols:
                    continue
                seen.add(s)
                candidates.append(s)
            available = max(0, effective_limit - len(self._desired_symbols))
            accepted = candidates[:available]
            rejected = candidates[available:]
            self._desired_symbols.update(accepted)

        if rejected:
            logger.warning(
                "TBT feed: entitlement limit reached (tier=%s, per_user=%d, per_connection=%d, effective=%d); "
                "rejected %d symbol(s): %s",
                limits.get("tier"), per_user, per_conn, effective_limit,
                len(rejected), ", ".join(rejected[:5]),
            )
            self._stats["last_error"] = "entitlement_limit_reached"

        # If connected, send subscribe immediately. If channel allocation fails,
        # remove the symbol from desired state so it cannot be retried/subscribed
        # accidentally on reconnect (channel capacity is enforced separately).
        async with self._ws_lock:
            ws = self._ws
            subscribed_count = 0
            if ws and _socket_is_open(ws):
                channels_to_resume: set[int] = set()
                for symbol in accepted:
                    async with self._lock:
                        if symbol not in self._desired_symbols:
                            continue
                        ch = self._get_or_create_channel(symbol)
                        if ch is None:
                            self._desired_symbols.discard(symbol)
                            self._release_channel(symbol)
                            continue
                    sent = await self._send_subscribe(symbol, ws=ws, channel=ch)
                    if sent:
                        channels_to_resume.add(ch)
                        subscribed_count += 1
                    else:
                        # Send failed (socket issue); keep desired/mapping for reconnect.
                        pass
                if channels_to_resume:
                    await self._send_channel_control(ws, resume_channels=sorted(channels_to_resume))
            else:
                async with self._lock:
                    subscribed_count = sum(1 for s in accepted if s in self._desired_symbols)

        logger.info(
            "TBT feed: subscribed to %d symbols (total: %d, tier=%s, limit=%d)",
            subscribed_count, len(self._desired_symbols), limits.get("tier"), effective_limit,
        )
        self._stats["subscribes"] += subscribed_count

    async def unsubscribe(self, symbols: list[str]) -> None:
        """Unsubscribe from symbols at runtime."""
        async with self._lock:
            removed = [s for s in symbols if s in self._desired_symbols]
            self._desired_symbols.difference_update(removed)
            removed_channels = {s: self._channel_map.get(s) for s in removed}

        # If connected, send unsubscribe immediately
        async with self._ws_lock:
            ws = self._ws
            if ws and _socket_is_open(ws):
                channels_to_pause: set[int] = set()
                for symbol in removed:
                    async with self._lock:
                        if symbol in self._desired_symbols:
                            continue
                        ch = self._channel_map.get(symbol, removed_channels.get(symbol))
                    if ch is None:
                        continue
                    await self._send_unsubscribe(symbol, ws=ws, channel=ch)
                    async with self._lock:
                        if symbol not in self._desired_symbols and not self._channel_has_desired_symbols(ch):
                            channels_to_pause.add(ch)
                if channels_to_pause:
                    await self._send_channel_control(ws, pause_channels=sorted(channels_to_pause))

        # Reclaim channel mappings for symbols no longer desired so channel IDs can
        # be safely reused over the connection lifetime (capacity stays separate).
        async with self._lock:
            for symbol in removed:
                self._release_channel(symbol)

        logger.info("TBT feed: unsubscribed from %d symbols (remaining: %d)", len(removed), len(self._desired_symbols))
        self._stats["unsubscribes"] += len(removed)

    async def _resubscribe_all(self, ws: Any) -> None:
        """Resubscribe to all desired symbols after (re)connect and resume channels."""
        async with self._ws_lock:
            if not ws or not _socket_is_open(ws):
                return
            async with self._lock:
                symbols = list(self._desired_symbols)

            channels: set[int] = set()
            failed: list[str] = []
            for symbol in symbols:
                async with self._lock:
                    if symbol not in self._desired_symbols:
                        continue
                    ch = self._get_or_create_channel(symbol)
                if ch is None:
                    failed.append(symbol)
                    continue
                sent = await self._send_subscribe(symbol, ws=ws, channel=ch)
                if sent:
                    channels.add(ch)
                else:
                    # Send failed; keep desired/mapping for a future reconnect attempt.
                    pass

            # If channel allocation fails, drop from desired so rejected/unallocatable
            # symbols cannot remain desired or be retried indefinitely on reconnect.
            if failed:
                async with self._lock:
                    for symbol in failed:
                        self._desired_symbols.discard(symbol)
                        self._release_channel(symbol)

            # Official protocol: subscribing does not start stream; resume channels (type 2).
            if channels:
                await self._send_channel_control(ws, resume_channels=sorted(channels))

        logger.info(
            "TBT feed: resubscribed to %d symbols after reconnect (dropped=%d)",
            len(channels), len(failed),
        )

    async def _resubscribe_symbol(self, symbol: str) -> None:
        """Resubscribe to a single symbol (after sequence gap)."""
        async with self._ws_lock:
            ws = self._ws
            if not ws or not _socket_is_open(ws):
                return
            async with self._lock:
                if symbol not in self._desired_symbols:
                    return
                ch = self._channel_map.get(symbol) or self._get_or_create_channel(symbol)
                if ch is None:
                    self._desired_symbols.discard(symbol)
                    self._release_channel(symbol)
                    return
            await self._send_unsubscribe(symbol, ws=ws, channel=ch)
            async with self._lock:
                if symbol not in self._desired_symbols:
                    return
            sent = await self._send_subscribe(symbol, ws=ws, channel=ch)
            if sent:
                async with self._lock:
                    if symbol not in self._desired_symbols:
                        return
                await self._send_channel_control(ws, resume_channels=[ch])

    async def _send_subscribe(self, symbol: str, ws: Any | None = None, channel: int | None = None) -> bool:
        """Send subscribe message for a symbol. Returns True if sent.

        Callers that need serialized send sequences should hold _ws_lock.
        """
        if ws is None:
            async with self._ws_lock:
                ws = self._ws
        if not ws or not _socket_is_open(ws):
            return False

        if channel is None:
            async with self._lock:
                channel = self._get_or_create_channel(symbol)
        if channel is None:
            return False
        msg = {
            "type": TBT_SUBSCRIBE_TYPE,
            "data": {
                "subs": 1,
                "symbols": [symbol],
                "mode": "depth",
                "channel": str(channel),
            },
        }
        await ws.send(json.dumps(msg))
        logger.debug("TBT feed: subscribed to %s on channel %d", symbol, channel)
        return True

    async def _send_unsubscribe(self, symbol: str, ws: Any | None = None, channel: int | None = None) -> bool:
        """Send unsubscribe message for a symbol. Returns True if sent.

        Callers that need serialized send sequences should hold _ws_lock.
        """
        if ws is None:
            async with self._ws_lock:
                ws = self._ws
        if not ws or not _socket_is_open(ws):
            return False

        if channel is None:
            async with self._lock:
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
        await ws.send(json.dumps(msg))
        logger.debug("TBT feed: unsubscribed from %s", symbol)
        return True

    async def _send_channel_control(
        self,
        ws: Any,
        resume_channels: list[int] | None = None,
        pause_channels: list[int] | None = None,
    ) -> None:
        """Send type-2 switch-channel message (resume/pause).

        Callers that need serialized send sequences should hold _ws_lock.
        """
        if not ws or not _socket_is_open(ws):
            return
        resume = [str(c) for c in (resume_channels or [])]
        pause = [str(c) for c in (pause_channels or [])]
        if not resume and not pause:
            return
        msg = {
            "type": TBT_CHANNEL_CONTROL_TYPE,
            "data": {
                "resumeChannels": resume,
                "pauseChannels": pause,
            },
        }
        await ws.send(json.dumps(msg))
        async with self._lock:
            for c in (resume_channels or []):
                self._active_channels.add(int(c))
            for c in (pause_channels or []):
                self._active_channels.discard(int(c))
        logger.debug("TBT feed: channel control resume=%s pause=%s", resume, pause)

    def _channel_has_desired_symbols(self, channel: int) -> bool:
        """Caller must hold self._lock when concurrent access is possible."""
        for sym, ch in self._channel_map.items():
            if ch == channel and sym in self._desired_symbols:
                return True
        return False

    def _release_channel(self, symbol: str) -> None:
        """Release a symbol's channel mapping when it is no longer desired.

        Caller must hold self._lock when concurrent access is possible.
        A channel ID becomes reusable only when no desired symbols remain mapped to it.
        Active-channel state is cleared for fully released channels.
        """
        if symbol in self._desired_symbols:
            return
        channel = self._channel_map.pop(symbol, None)
        if channel is None:
            return
        if not self._channel_has_desired_symbols(channel):
            self._active_channels.discard(channel)

    def _resolve_tbt_limits(self) -> dict[str, Any]:
        """Resolve TBT entitlement limits (tier-aware) using existing resolution where possible.

        Tier hints: `tbt_tier` preferred, then `tier`, then `plan` (first non-empty).
        Missing/invalid/conflicting hints fall back to documented default (standard)
        with TBT-specific limits; invalid tiers never escalate entitlements.
        """
        if self._tbt_limits is not None:
            return self._tbt_limits

        # Prefer explicit TBT hint; fall back to generic hints. First non-empty wins.
        raw_hint = None
        for key in ("tbt_tier", "tier", "plan"):
            val = self._config.get(key)
            if isinstance(val, str) and val.strip():
                raw_hint = val.strip().lower()
                break

        resolved_tier = "standard"
        source = "declared_default"
        if raw_hint is not None:
            if raw_hint in _TBT_TIER_LIMITS:
                resolved_tier = raw_hint
                source = "config"
                # If provider entitlement resolution is available, keep its source
                # semantics when it agrees; disagreement/unknown stays non-escalating.
                try:
                    from providers.capacity import resolve_entitlement
                    ent = resolve_entitlement("fyers", tier=raw_hint)
                    ent_tier = str(getattr(ent, "tier", "") or "").lower()
                    if ent_tier == raw_hint:
                        source = str(getattr(ent, "source", source) or source)
                except Exception:
                    pass
            else:
                # Invalid/unknown tier hint: do not escalate; use documented default.
                resolved_tier = "standard"
                source = "invalid_tier_hint"

        limits = dict(_TBT_TIER_LIMITS.get(resolved_tier, _TBT_TIER_LIMITS["standard"]))
        limits["tier"] = resolved_tier
        limits["source"] = source
        limits["channels_per_connection"] = MAX_CHANNELS_PER_CONNECTION
        self._tbt_limits = limits
        return limits

    def _get_or_create_channel(self, symbol: str) -> int | None:
        """Get or create a channel for a symbol, enforcing channel capacity.

        Caller must hold self._lock when concurrent access is possible.
        Channel IDs (1..max_channels) are reused when freed (no desired symbols mapped),
        so lifetime unique symbols do not exhaust capacity while active usage stays within limits.
        """
        if symbol in self._channel_map:
            return self._channel_map[symbol]
        limits = self._resolve_tbt_limits()
        max_channels = int(limits.get("channels_per_connection", MAX_CHANNELS_PER_CONNECTION))
        used = set(self._channel_map.values())
        for channel in range(1, max_channels + 1):
            if channel not in used:
                self._channel_map[symbol] = channel
                if channel >= self._next_channel:
                    self._next_channel = channel + 1
                return channel
        logger.warning(
            "TBT feed: channel capacity reached (%d/%d), cannot allocate channel for %s",
            max_channels, max_channels, symbol,
        )
        self._stats["last_error"] = "channel_capacity_reached"
        return None

    # -------------------------------------------------------------------------
    # WebSocket URL fetch
    # -------------------------------------------------------------------------

    async def _fetch_ws_url(self, token: str) -> str | None:
        """Resolve TBT WebSocket URL.

        Official docs specify fixed endpoint wss://rtsocket-api.fyers.in/versova
        with Authorization: <appId:accessToken>. Dynamic discovery via
        GET https://api-t1.fyers.in/indus/home/tbtws was previously attempted as
        best-effort, but its official necessity and authentication contract are
        not established by available authoritative evidence. As a security-first
        decision, this implementation does not transmit credentials to that
        unverified endpoint and uses the documented fixed endpoint directly.
        No credentials are logged.
        """
        del token  # intentionally unused: no credential-bearing discovery request
        return "wss://rtsocket-api.fyers.in/versova"

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
        """Clean up resources (idempotent)."""
        try:
            self._books.invalidate_all()
        finally:
            async with self._lock:
                self._active_channels.clear()
                # Drop stale channel mappings for symbols no longer desired; keep mappings
                # for desired symbols so reconnect/resubscribe remains consistent.
                stale = [sym for sym in list(self._channel_map) if sym not in self._desired_symbols]
                for sym in stale:
                    self._channel_map.pop(sym, None)
            async with self._ws_lock:
                self._ws = None
            if self._state != "auth_required":
                self._set_state("disconnected")

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return current status and statistics."""
        limits = self._resolve_tbt_limits()
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
            "protocol_notes": {
                "ws_endpoint": "wss://rtsocket-api.fyers.in/versova",
                "dynamic_url_discovery": "disabled_unverified_credential_bearing_endpoint",
                "send_success_is_server_ack": False,
                "server_error_correlation": "undocumented",
                "positive_subscribe_ack": "undocumented",
                "positive_resume_ack": "undocumented",
            },
            "tbt_limits": {
                "tier": limits.get("tier"),
                "source": limits.get("source"),
                "symbols_per_user": limits.get("symbols_per_user"),
                "symbols_per_connection": limits.get("symbols_per_connection"),
                "channels_per_connection": limits.get("channels_per_connection"),
                "connections_per_user": limits.get("connections_per_user"),
                "enforcement_scope": "local_feed_instance",
                "account_wide_enforced": False,
            },
        }

    @property
    def is_connected(self) -> bool:
        """Check if feed is connected."""
        return self._state == "connected"

    @property
    def desired_symbols(self) -> set[str]:
        """Get desired symbols set."""
        return self._desired_symbols.copy()


