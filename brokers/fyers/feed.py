"""Fyers live market-data websocket feed (source adapter).

Protocol notes (official v3 data socket):
  * URL:    wss://socket.fyers.in/hsm/v1-5/prod
  * Auth:   ``Authorization: <app_id>:<access_token>`` header at connect
  * Join:   first outbound frame {"type": 1} opens the channel
  * Sub:    {"type": 2, "data": {"symbols": [...], "subType": "SymbolUpdate"}}
            depth variant uses subType "DepthUpdate"
  * Unsub:  same shape with subType "unsub"
  * Ping:   {"type": 3} periodically keeps the channel alive
  * Msgs:   text JSON carrying type "sf"/"dp"/"if" payloads which are
            normalized by market.normalize.fyers (canonical names only)

Desired-set subscription model mirrors UpstoxFeed: mutations update the
authoritative set under a lock; when streaming, delta frames go out on the
live socket; reconnects resubscribe the full desired set.

Malformed frames are isolated per-symbol: one bad payload never kills the
recv loop or sibling updates (errors counted, loop continues).

DECODING CONTRACT AND VALIDATION STATUS
---------------------------------------
The wire layout is the official FYERS SDK contract
(``fyers_apiv3/FyersWebsocket/data_ws.py`` + ``map.json``, package
``fyers-apiv3``): a snapshot record (type 83) is

    topic_id | topic name | field_count | field_count x int32 values
             | 2 skipped bytes | multiplier (u16) | precision (u8)
             | 3 length-prefixed strings

and the raw integers are divided by ``(10 ** precision) * multiplier`` for
the price-like fields only (the SDK's per-family field sets). This module
consumes exactly those bytes, which is verified byte-for-byte against an
independent re-implementation of the SDK algorithm in
``test/test_fyers_decoder.py``.

VALIDATED: a live capture with an INDEPENDENT reference quote is recorded
in ``test/fixtures/`` (raw HSM bytes + the FYERS REST Quotes value for
the same instrument, captured seconds apart). The captured frames decode
to the reference value with a 0.00 delta for the index, an equity and an
option, and the bytes at the skipped offset are NOT the multiplier (they
read 28/27/38 in the three captures - exactly the wrong "multiplier" the
old decoder picked up). ``test/test_fyers_decoder.py`` replays those
captures as a permanent regression.

The "index jump" guard in :meth:`_emit_tick` remains a runaway guard,
NOT evidence of correctness, and it cannot validate the first tick of an
instrument. The guarded REST index path stays available as a fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from brokers.fyers.hsm_symbols import (
    SymbolTokenAuthError,
    resolve_wire_symbols,
)
from market.normalize.fyers import (
    NormalizationError,
    quote_fields_from_symbol_update,
)

logger = logging.getLogger("event_server")

_FYERS_WS_URL = "wss://socket.fyers.in/hsm/v1-5/prod"

#: LIVENESS window (seconds). The HSM socket delivers frames continuously
#: while it is open; a gap this long means the feed STOPPED delivering -
#: the socket staying open is not evidence of market data. It matches the
#: shared quote freshness window so the feed liveness and the quote
#: freshness never disagree.
_LIVENESS_WINDOW_S = 120.0

_STATE_TERMINAL = "failed"

# Fallback only — composition always injects the centralized redirect URI.
_DEFAULT_FYERS_REDIRECT = "http://localhost:7070/auth/fyers/callback"


class _Terminal:
    """Sentinel returned by _run_session on terminal failure."""


_TERMINAL = _Terminal()


class _FyersAuthInvalid(Exception):
    """Raised when the Fyers REST/WS auth clearly rejects the access token
    (expired or unauthorized). The feed must stop cleanly and surface
    ``auth_required`` ("Daily Login Required") rather than retry forever."""


class _SocketNotWritable(Exception):
    """The session socket could not be written to (closed or retired)."""


def _is_valid_wire_topic(topic: Any) -> bool:
    """True when ``topic`` is a well-formed HSM wire topic.

    The official SDK contract has exactly two shapes:
    ``sf|<segment>|<exchange_token>`` and ``if|<segment>|<index topic>``.
    A cached mapping is reused ONLY when it still has that shape - a
    malformed cache entry is re-resolved instead of being trusted.
    """
    if not isinstance(topic, str) or not topic:
        return False
    parts = topic.split("|")
    if len(parts) != 3:
        return False
    kind, segment, payload = parts
    if kind not in ("sf", "if"):
        return False
    if not segment or not payload:
        return False
    if kind == "sf" and not payload.isdigit():
        return False
    return True

# ---------------------------------------------------------------------------
# Fyers HSM data-socket protocol (binary) — ported from the official
# fyers-apiv3 SDK (FyersWebsocket/data_ws.py). The socket speaks a packed
# binary protocol, NOT JSON:
#   * connect plain (no auth header)
#   * send auth frame built from the hsm_key inside the access-token JWT
#   * send full/lite mode frame
#   * subscribe with HSM symbol tokens ("sf|<seg>|<tok>" / "if|<seg>|<name>")
#   * ticks arrive as binary datafeed frames (resp_type 6)
# ---------------------------------------------------------------------------

_HSM_SOURCE = "MarketHub-1.0"
_HSM_CHANNEL = 11

# The exchange-segment map, the index topic map and the symbol -> HSM
# resolution live in ``brokers.fyers.hsm_symbols`` (generated from the
# official FYERS SDK assets ``map.json`` + ``data_ws.py``). Nothing here
# invents a wire identifier: an index without an official topic is
# reported as unresolved and served over REST instead.

# Field order for binary tick decoding (official map.json).
_DATA_VAL = (
    "ltp", "vol_traded_today", "last_traded_time", "exch_feed_time",
    "bid_size", "ask_size", "bid_price", "ask_price", "last_traded_qty",
    "tot_buy_qty", "tot_sell_qty", "avg_trade_price", "OI", "low_price",
    "high_price", "Yhigh", "Ylow", "lower_ckt", "upper_ckt", "open_price",
    "prev_close_price",
)
_INDEX_VAL = (
    "ltp", "prev_close_price", "exch_feed_time", "high_price", "low_price",
    "open_price",
)
_SCALED_VAL = frozenset((
    "ltp", "bid_price", "ask_price", "avg_trade_price", "low_price",
    "high_price", "open_price", "prev_close_price",
))
_DEPTH_VAL = (
    "bid_price1", "bid_price2", "bid_price3", "bid_price4", "bid_price5",
    "ask_price1", "ask_price2", "ask_price3", "ask_price4", "ask_price5",
    "bid_size1", "bid_size2", "bid_size3", "bid_size4", "bid_size5",
    "ask_size1", "ask_size2", "ask_size3", "ask_size4", "ask_size5",
    "bid_order1", "bid_order2", "bid_order3", "bid_order4", "bid_order5",
    "ask_order1", "ask_order2", "ask_order3", "ask_order4", "ask_order5",
)


def _hsm_auth_frame(hsm_key: str) -> bytes:
    """Binary auth frame (SDK __access_token_msg)."""
    import struct
    buf = bytearray()
    buf.extend(struct.pack("!H", 18 - 2 + len(hsm_key) + len(_HSM_SOURCE)))
    buf.append(1)   # ReqType: auth
    buf.append(4)   # FieldCount
    buf.append(1)                       # Field 1: hsm token
    buf.extend(struct.pack("!H", len(hsm_key)))
    buf.extend(hsm_key.encode())
    buf.append(2)
    buf.extend(struct.pack("!H", 1))
    buf.append(ord("P"))
    buf.append(3)
    buf.extend(struct.pack("!H", 1))
    buf.append(1)
    buf.append(4)                       # Field 4: source
    buf.extend(struct.pack("!H", len(_HSM_SOURCE)))
    buf.extend(_HSM_SOURCE.encode())
    return bytes(buf)


def _hsm_mode_frame() -> bytes:
    """Binary full-mode frame (SDK __full_mode_msg), channel 11."""
    import struct
    data = bytearray()
    data.extend(struct.pack(">H", 0))
    data.append(12)
    data.append(2)
    channel_bits = 1 << _HSM_CHANNEL
    data.append(1)
    data.extend(struct.pack(">H", 8))
    data.extend(struct.pack(">Q", channel_bits))
    data.append(2)
    data.extend(struct.pack(">H", 1))
    data.append(70)          # 70 = full mode (76 = lite)
    return bytes(data)


def _hsm_subscribe_frame(symbols: list[str]) -> bytes:
    """Binary subscribe frame (SDK __subscription_msg)."""
    import struct
    scrips = bytearray()
    scrips.append(len(symbols) >> 8 & 0xFF)
    scrips.append(len(symbols) & 0xFF)
    for s in symbols:
        b = str(s).encode("ascii")
        scrips.append(len(b))
        scrips.extend(b)
    out = bytearray()
    out.append(len(scrips) >> 8 & 0xFF)
    out.append(len(scrips) & 0xFF)
    out.append(4)            # request_type: subscribe
    out.append(2)            # field_count
    out.append(1)
    out.extend(struct.pack(">H", len(scrips)))
    out.extend(scrips)
    out.append(2)
    out.extend(struct.pack(">H", 1))
    out.append(_HSM_CHANNEL)
    return bytes(out)


def _hsm_unsubscribe_frame(symbols: list[str]) -> bytes:
    """Binary unsubscribe frame (SDK __unsubscription_msg)."""
    import struct
    scrips = bytearray()
    scrips.append(len(symbols) >> 8 & 0xFF)
    scrips.append(len(symbols) & 0xFF)
    for s in symbols:
        b = str(s).encode("ascii")
        scrips.append(len(b))
        scrips.extend(b)
    out = bytearray()
    out.append(len(scrips) >> 8 & 0xFF)
    out.append(len(scrips) & 0xFF)
    out.append(5)            # request_type: unsubscribe
    out.append(2)
    out.append(1)
    out.extend(struct.pack(">H", len(scrips)))
    out.extend(scrips)
    out.append(2)
    out.extend(struct.pack(">H", 1))
    out.append(_HSM_CHANNEL)
    return bytes(out)


def _hsm_ack_frame(message_number: int) -> bytes:
    """Binary acknowledgement frame (SDK __ackowledgement_msg)."""
    import struct
    buf = bytearray()
    buf.extend(struct.pack(">H", 11 - 2))
    buf.append(3)
    buf.append(1)
    buf.append(1)
    buf.extend(struct.pack(">H", 4))
    buf.extend(struct.pack(">I", message_number))
    return bytes(buf)


def _hsm_key_from_access_token(token: str) -> str:
    """Extract the hsm_key claim from the access-token JWT payload."""
    import base64
    bare = token.split(":", 1)[1] if ":" in token else token
    parts = bare.split(".")
    if len(parts) < 2:
        raise ValueError("access token is not a JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    hsm_key = payload.get("hsm_key")
    if not hsm_key:
        raise ValueError("access token payload has no hsm_key")
    return str(hsm_key)


def _safe_ws_summary(exc: BaseException) -> str:
    return f"{type(exc).__name__}"


def _socket_is_open(ws: Any) -> bool:
    """True when the socket can still be written to.

    Duck-typed on purpose: the websocket library exposes ``state``
    (OPEN / CLOSING / CLOSED) and older versions expose ``closed``; a test
    double may expose either. An unknown shape is treated as open - the
    send itself is still guarded and its failure is reported through the
    feed state, never as an exception.
    """
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        name = str(getattr(state, "name", state)).upper()
        if "OPEN" in name:
            return True
        if name:
            return False
    closed = getattr(ws, "closed", None)
    if isinstance(closed, bool):
        return not closed
    return True


class FyersFeed:
    """Live Fyers market-data source following the shared lifecycle model."""

    def __init__(self, *, config: dict[str, Any], auth: Any,
                 market_service: Any = None) -> None:
        keys_raw = config.get("instrument_keys")
        if not isinstance(keys_raw, list) or not keys_raw:
            raise ValueError("fyers feed requires non-empty instrument_keys")
        if not all(isinstance(k, str) and k.strip() for k in keys_raw):
            raise ValueError("fyers instrument_keys entries must be strings")
        self._name = config.get("source_name", "fyers")
        self._app_id = str(config.get("app_id", ""))
        self._auth = auth                      # FyersAuth (for token refresh)
        self._access_token_getter = config.get("access_token_getter")
        # Optional injected symbol -> HSM wire resolver (dependency
        # injection for tests and for a shared, rate-limited resolver).
        self._wire_resolver = config.get("wire_symbol_resolver")
        self._credential_store = config.get("credential_store")
        self._redirect_uri = config.get("redirect_uri", _DEFAULT_FYERS_REDIRECT)
        self._market_service = market_service
        self._ws_connect = config.get("ws_connect")
        # The feed's own timestamps (status reporting only - the canonical
        # quote's received_ts comes from the normalizer). Default to a real
        # clock so the feed's liveness evidence is never an empty string.
        self._utc_now_iso = config.get("utc_now_iso") or (
            lambda: __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat())
        self._config = config                  # retained for optional TBT channel

        self._desired: tuple[str, ...] = tuple(
            k.strip() for k in keys_raw)
        self._sub_lock = asyncio.Lock()
        self._live_ws: Any = None

        self._state = "stopped"
        self._state_updated_at: str | None = None
        self._last_exit_reason: str | None = None
        self._last_exit_at: str | None = None
        self._transitions: deque[dict[str, Any]] = deque(maxlen=20)
        self.on_state_change: Callable[[str, str, str, str, str | None], None] | None = (
            None
        )
        self._provider = "fyers"
        self._connect_attempts = 0
        self._reconnect_count = 0
        self._hsm_symbols: dict[str, str] = {}      # config key -> HSM topic
        self._hsm_by_topic: dict[str, str] = {}     # HSM topic -> config key
        # Instruments with no resolvable HSM wire topic (and why). They are
        # never subscribed with a raw API identifier.
        self._unresolved: dict[str, str] = {}
        # Per-instrument evidence that DATA actually arrived (the only
        # basis for claiming a feed is live).
        self._last_data_at: dict[str, str] = {}
        # The same evidence on a monotonic clock (an ISO timestamp is not
        # enough to age a value reliably).
        self._last_data_mono: dict[str, float] = {}
        # The feed-level evidence that FRAMES are still arriving.
        self._last_frame_at: str | None = None
        self._last_frame_mono: float | None = None
        # Index ticks rejected by the plausibility guard (mis-decoded scale).
        self._index_value_rejections = 0
        # Index ticks that were decoded but NOT published (the market value
        # is unverified - see the module docstring).
        self._unverified_index_ticks = 0
        # The last ACCEPTED value per instrument (the circuit breaker's
        # baseline).
        self._last_accepted_ltp: dict[str, float] = {}
        self._sym_data: dict[int, dict] = {}        # topic id -> last fields
        self._frames_received = 0
        self._malformed_frames = 0
        self._last_message_at: str | None = None
        self._last_error: str | None = None
        # Subscription mutations that could NOT be sent (the socket was
        # closed/replaced). Surfaced through the status, never as repeated
        # exceptions.
        self._mutation_failures = 0
        self._last_mutation_error: str | None = None
        self._last_mutation_error_at: str | None = None
        self._connected_at: str | None = None
        self._started_at: str | None = None

        # TBT depth channel (optional, supplements HSM)
        self._tbt_enabled = config.get("tbt_enabled", False)
        self._tbt_feed = None
        self._tbt_task = None

    # -- identity / status ------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    def _set_state(self, state: str, reason: str | None = None) -> None:
        if state != self._state:
            old = self._state
            self._state = state
            self._state_updated_at = self._now()
            logger.info("fyers feed %s: state -> %s (reason=%s)",
                        self._name, state, reason)
            self._transitions.append({
                "at": self._state_updated_at,
                "from": old,
                "to": state,
                "reason": reason,
            })
            listener = self.on_state_change
            if listener is not None:
                try:
                    listener(self._name, self._provider, old, state, reason)
                except Exception:  # pragma: no cover - listener must never break feed
                    logger.debug("fyers feed %s: on_state_change raised",
                                 self._name, exc_info=True)

    def _note_exit(self, reason: str) -> None:
        self._last_exit_reason = reason
        self._last_exit_at = self._now()
        logger.info("fyers feed %s: run ended (%s)", self._name, reason)

    async def _close_live_ws(self) -> None:
        """Close and clear the live socket if one is held (cancellation path)."""
        async with self._sub_lock:
            ws = self._live_ws
            self._live_ws = None
        if ws is not None:
            await self._close_quietly(ws)

    async def _retire_socket(self, ws: Any) -> None:
        """Retire a session socket: clear the live reference FIRST.

        The reference is cleared UNDER the subscription lock, so a
        concurrent mutation can never pick up a socket that is being
        closed; the socket itself is closed afterwards. Every close path
        of a session goes through this helper.
        """
        async with self._sub_lock:
            if self._live_ws is ws:
                self._live_ws = None
        await self._close_quietly(ws)

    async def _wait_or_stop(self, stop_event: asyncio.Event,
                            delay: float) -> bool:
        """Wait ``delay`` seconds, or return True early if stop fires."""
        stop_task = asyncio.create_task(stop_event.wait())
        sleep_task = asyncio.create_task(asyncio.sleep(delay))
        try:
            done, _pending = await asyncio.wait(
                {stop_task, sleep_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            return stop_task in done
        finally:
            for t in (stop_task, sleep_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(stop_task, sleep_task,
                                 return_exceptions=True)

    def _now(self) -> str:
        return (self._utc_now_iso or (lambda: ""))()

    def _mono(self) -> float:
        return time.monotonic()

    def _resolve_app_id(self) -> str:
        """App ID comes from the encrypted credential store (single source of
        truth), falling back to the value injected at construction.

        Resolved lazily at connect time so credentials added after startup
        (first-run WebUI setup) are picked up without rebuilding the feed.
        """
        store = self._credential_store
        if store is not None:
            try:
                creds = store.load_fyers_credentials()
                if creds and creds.get("app_id"):
                    return creds["app_id"]
            except Exception:
                pass
        return self._app_id

    def status(self) -> dict[str, Any]:
        base = {
            "name": self._name,
            "state": self._state,
            "provider": "fyers",
            "state_updated_at": self._state_updated_at,
            "configured_instruments": len(self._desired),
            "subscribed_instruments": (
                len(self._desired) if self._state == "streaming" else None
            ),
            "frames_received": self._frames_received,
            "malformed_frames": self._malformed_frames,
            "reconnect_count": self._reconnect_count,
            "connect_attempts": self._connect_attempts,
            "connected_at": self._connected_at,
            "last_message_at": self._last_message_at,
            "last_error": self._last_error,
            "last_error_at": None,
            "last_exit_reason": self._last_exit_reason,
            "last_exit_at": self._last_exit_at,
            "started_at": self._started_at,
            "auth_required": self._state == "auth_required",
            "not_ready_reason": self.readiness_reason(),
            "recent_transitions": list(self._transitions),
            # Instruments that could not be resolved to a wire topic.
            "unresolved_symbols": dict(self._unresolved),
            # The CURRENT desired set and how much of it is on the wire.
            "desired_instruments": len(self._desired),
            "resolved_instruments": sum(
                1 for sym in self._desired
                if _is_valid_wire_topic(self._hsm_symbols.get(sym))),
            "unresolved_instruments": len(self._unresolved),
            # Per-instrument last-data evidence (never a proxy for
            # "connected").
            "last_data_at": dict(self._last_data_at),
            # LIVENESS: a socket connection alone is NOT market-data
            # delivery. "awaiting_data" means the feed is connected but no
            # valid market-data frame has arrived yet; "stale" means
            # frames stopped arriving (the socket stayed open, the data
            # did not).
            "awaiting_data": bool(self._state == "streaming"
                                  and not self._last_frame_mono),
            "data_flowing": self._frames_fresh(),
            "liveness": self._liveness(),
            "last_frame_at": self._last_frame_at,
            "stale": bool(self._state == "streaming"
                          and self._last_frame_mono
                          and not self._frames_fresh()),
            # Instruments whose last data is older than the liveness
            # window (their last known value is stale, not live).
            "stale_instruments": sorted(self._stale_instruments()),
            # Subscription mutations that could not be sent.
            "mutation_failures": self._mutation_failures,
            "last_mutation_error": self._last_mutation_error,
            "last_mutation_error_at": self._last_mutation_error_at,
            # Index ticks rejected by the plausibility guard.
            "index_value_rejections": self._index_value_rejections,
            # Index ticks decoded but not published (value unverified).
            "unverified_index_ticks": self._unverified_index_ticks,
            # TBT depth channel status
            "tbt": self._tbt_status(),
        }
        return base

    def _frames_fresh(self) -> bool:
        """True when a frame arrived within the liveness window.

        A CONNECTED socket is not evidence of delivery: a feed that
        received frames and then went silent is NOT live.
        """
        if self._last_frame_mono is None:
            return False
        return (self._mono() - self._last_frame_mono) <= _LIVENESS_WINDOW_S

    def _liveness(self) -> str:
        """The feed liveness: live / awaiting_data / stale / not_connected."""
        if self._frames_fresh():
            return "live"
        if self._state != "streaming":
            return "not_connected"
        if self._last_frame_mono is not None:
            # Frames arrived, then stopped: the socket is open but the data
            # is stale.
            return "stale"
        return "awaiting_data"

    def _stale_instruments(self) -> list[str]:
        """Instruments whose last data is older than the liveness window."""
        now = self._mono()
        return [sym for sym, mono in self._last_data_mono.items()
                if (now - mono) > _LIVENESS_WINDOW_S]

    def readiness_reason(self) -> str | None:
        """Why this feed cannot start right now, or None if ready (WP2).

        The daily-login dimension is surfaced by ``auth_required`` in status();
        this reports the token-getter dimension.
        """
        getter = self._access_token_getter
        if getter is None:
            return "no_token_getter"
        try:
            token = getter()
        except Exception:
            return "token_lookup_failed"
        if asyncio.iscoroutine(token):
            return None  # async getter validated at session time
        if not isinstance(token, str) or not token.strip():
            return "missing_token"
        return None

    def is_ready_to_start(self) -> bool:
        """Synchronous readiness gate (WP37)."""
        return self.readiness_reason() is None

    @property
    def desired_instrument_count(self) -> int:
        return len(self._desired)

    def _note_error(self, summary: str) -> None:
        self._last_error = summary

    def _next_backoff(self, hint: float | None = None) -> float:
        base = min(30.0, 0.5 * (2 ** min(self._reconnect_count, 6)))
        return hint if hint else base * (0.5 + random.random())

    # -- frames (HSM binary protocol) --------------------------------------------

    def _ping_frame(self) -> bytes:
        return b"\x00\x01\x0b"     # SDK ping: bytes([0, 1, 11])

    def _has_valid_mapping(self, sym: str) -> bool:
        """True when ``sym`` has a usable cached HSM wire topic.

        A cache entry is trusted only when it is still a well-formed wire
        topic AND the symbol is still in the desired set. Anything else
        is (re)resolved instead of being sent to the socket.
        """
        if sym not in self._desired:
            return False
        return _is_valid_wire_topic(self._hsm_symbols.get(sym))

    def _desired_wire_topics(self) -> list[str]:
        """HSM wire topics for the CURRENT desired set (in desired order).

        The reconnect subscribes EXACTLY this list - never "everything
        that was ever resolved". A symbol that is no longer wanted
        therefore cannot come back on the wire, even when its cached
        mapping is still around.
        """
        topics: list[str] = []
        for sym in self._desired:
            topic = self._hsm_symbols.get(sym)
            if _is_valid_wire_topic(topic):
                topics.append(str(topic))
                continue
            self._unresolved.setdefault(
                sym, "no HSM wire symbol resolved for this instrument")
        return topics

    def _wire_topics(self, symbols: list[str]) -> list[str]:
        """Resolved wire topics for ``symbols``.

        An instrument without a resolved (and well-formed) topic is
        reported as unresolved - it is never replaced by its raw API
        identifier.
        """
        hsm: list[str] = []
        for sym in symbols:
            topic = self._hsm_symbols.get(sym)
            if _is_valid_wire_topic(topic):
                hsm.append(str(topic))
                continue
            self._unresolved.setdefault(
                sym, "no HSM wire symbol resolved for this instrument")
        return hsm

    def _mutation_frame(self, sub_type: str,
                        symbols: list[str]) -> bytes | None:
        """Build a subscribe/unsubscribe frame from RESOLVED wire topics.

        A symbol without a resolved HSM topic is NEVER sent as a raw API
        identifier - the wire format only accepts ``sf|<seg>|<token>`` /
        ``if|<seg>|<topic>``. Unresolved symbols are counted and reported
        (see ``status()['unresolved_symbols']``).

        Returns None when there is nothing to send (an empty frame would
        be a pointless write on the wire).
        """
        hsm = self._wire_topics(symbols)
        if not hsm:
            return None
        if sub_type == "unsub":
            return _hsm_unsubscribe_frame(hsm)
        return _hsm_subscribe_frame(hsm)

    def _full_subscribe_frame(self) -> bytes:
        return _hsm_mode_frame()

    async def _send_mutation(self, frame: bytes) -> bool:
        """Send one subscription frame on the ACTIVE, OPEN socket.

        The socket is verified under the subscription lock: it must still
        be the live socket and it must still be open. A mutation can
        therefore never be written to a socket that is being retired (the
        "socket.send() raised exception" flood), and a refused send is
        reported through the feed state instead of an exception.
        """
        async with self._sub_lock:
            ws = self._live_ws
            if ws is None:
                return False
            if not _socket_is_open(ws):
                self._note_mutation_failure("socket_not_open")
                return False
            try:
                await ws.send(frame)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._note_mutation_failure(type(exc).__name__)
                return False

    def _note_mutation_failure(self, reason: str) -> None:
        """Record a refused subscription mutation (truthful, no exception).

        The failure is surfaced through the feed's status (and a bounded
        log line); it never turns into a repeated exception.
        """
        self._mutation_failures += 1
        self._last_mutation_error = reason
        self._last_mutation_error_at = self._now()
        if self._mutation_failures <= 3 or self._mutation_failures % 50 == 0:
            logger.warning(
                "fyers feed %s: subscription mutation not sent (%s) "
                "- failure #%d", self._name, reason, self._mutation_failures)

    async def _send_frame(self, ws: Any, frame: bytes) -> bool:
        """Write one frame on the session socket; never raises.

        The socket is verified under the subscription lock: it must still
        be the LIVE socket and it must still be open. A frame can
        therefore never reach a socket that is being retired or that is
        already closed - the session ends with a reconnect instead of an
        exception storm ("socket.send() raised exception").
        """
        async with self._sub_lock:
            if ws is None or self._live_ws is not ws:
                return False
            if not _socket_is_open(ws):
                return False
            try:
                await ws.send(frame)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("fyers feed %s: frame not sent (%s)",
                               self._name, type(exc).__name__)
                return False

    async def add_instruments(self, symbols: list[str],
                              metadata: dict | None = None) -> int:
        """Add instruments, subscribing them with VALID wire topics.

        The wire symbol of every newly requested instrument is resolved
        FIRST (the official symbol-token contract); only resolved symbols
        reach the socket. A VALID cached mapping is reused (the
        symbol-token endpoint is a rate-limited Data API); an instrument
        without one is resolved now. Instruments that cannot be resolved
        are kept in the desired set (so a later reconnect can pick them
        up) but are reported as unresolved instead of being subscribed
        with an invalid identifier.

        ``metadata`` is accepted for Upstox-feed parity (Fyers frames are
        symbol-keyed, so no tick-normalization metadata is needed).
        """
        del metadata
        async with self._sub_lock:
            existing = set(self._desired)
            fresh = [s for s in symbols if s and s not in existing]
            if fresh:
                self._desired = tuple(sorted(existing | set(fresh)))
        if not fresh:
            return 0
        # Resolve the NEW symbols before anything goes on the wire. A
        # valid cached mapping is reused; only the instruments that still
        # need a wire topic are looked up.
        await self._resolve_hsm_symbols(await self._current_token() or "",
                                        keys=fresh)
        if self._state == "streaming":
            frame = self._mutation_frame("SymbolUpdate", fresh)
            if frame is not None:
                await self._send_mutation(frame)
        # Propagate desired-instrument additions to the TBT channel (if active).
        # Desired state is retained even if TBT is not yet connected.
        if self._tbt_feed is not None:
            try:
                await self._tbt_feed.subscribe(fresh)
            except Exception as exc:
                logger.debug("fyers feed %s: TBT subscribe sync failed: %s",
                             self._name, type(exc).__name__)
        return len(fresh)

    async def add_instruments_detailed(self, symbols: list[str],
                                        metadata: dict | None = None,
                                        ) -> dict[str, Any]:
        """Like :meth:`add_instruments` but reports the resolution detail."""
        added = await self.add_instruments(symbols, metadata=metadata)
        return {
            "added": added,
            "unresolved": dict(self._unresolved),
            "subscribed": len(self._hsm_symbols),
        }

    async def remove_instruments(self, symbols: list[str]) -> int:
        """Remove instruments from the desired set (and from the wire).

        The cached wire topic goes with the symbol, so a reconnect
        restores EXACTLY the current desired set (no ghost subscription
        for an instrument nobody wants any more). The feed keeps at least
        one instrument subscribed - an empty subscription is not a valid
        feed state - so removing the LAST instrument is reported as 0
        removed and the desired set is left untouched.
        """
        async with self._sub_lock:
            existing = set(self._desired)
            gone = [s for s in symbols if s in existing]
            remaining = existing - set(gone)
            if not remaining:
                return 0   # keep at least one instrument subscribed
            self._desired = tuple(sorted(remaining))
        if gone and self._state == "streaming":
            frame = self._mutation_frame("unsub", gone)
            if frame is not None:
                await self._send_mutation(frame)
        # Propagate desired-instrument removals to the TBT channel (if active).
        if gone and self._tbt_feed is not None:
            try:
                await self._tbt_feed.unsubscribe(gone)
            except Exception as exc:
                logger.debug("fyers feed %s: TBT unsubscribe sync failed: %s",
                             self._name, type(exc).__name__)
        # The resolved wire topic goes with the symbol: a reconnect
        # restores EXACTLY the current desired set (no ghost subscription
        # for an instrument nobody wants any more).
        for sym in gone:
            topic = self._hsm_symbols.pop(sym, None)
            if topic:
                self._hsm_by_topic.pop(topic, None)
            self._last_data_at.pop(sym, None)
            self._last_data_mono.pop(sym, None)
            self._unresolved.pop(sym, None)
        return len(gone)

    # -- session ------------------------------------------------------------------

    async def run(self, publisher: Any, stop_event: asyncio.Event) -> None:
        """Shared lifecycle loop: authorize->connect->subscribe->recv."""
        del publisher
        self._started_at = self._now()

        # Start TBT channel if enabled
        await self._start_tbt_channel(stop_event)

        try:
            while not stop_event.is_set():
                outcome = await self._run_session(stop_event)
                if outcome is None or stop_event.is_set():
                    if self._state != "auth_required":
                        self._note_exit("stop_requested")
                        self._set_state("stopped", reason="stop_requested")
                    else:
                        self._note_exit("auth_required")
                    return
                if outcome is _TERMINAL:
                    self._note_exit(
                        f"terminal: {self._last_error or 'unknown failure'}")
                    return  # state already failed
                stopped = await self._wait_or_stop(stop_event, float(outcome))
                if stopped:
                    self._note_exit("stop_requested")
                    self._set_state("stopped", reason="stop_requested")
                    return
        except asyncio.CancelledError:
            # Parity with UpstoxFeed: a cancelled task must never leave a
            # stale "streaming"/"connecting" label behind; close the live ws.
            await self._close_live_ws()
            self._note_exit("cancelled")
            self._set_state("stopped", reason="cancelled")
            raise
        finally:
            # Stop TBT channel
            await self._stop_tbt_channel()

    async def _run_session(self, stop_event: asyncio.Event):
        """One connect->join->subscribe->recv cycle.

        Returns None (clean stop / auth required), _TERMINAL (config),
        or retry delay (float).
        """
        self._set_state("connecting", reason="ws_connect")
        self._connect_attempts += 1
        token = await self._current_token()
        if token is None:
            # Daily login required — not a broken feed. Stop cleanly and
            # wait for the token to be supplied (no retry storm).
            self._set_state("auth_required", reason="missing_token")
            self._note_exit("auth_required")
            return None

        try:
            ws = await self._connect(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error(_safe_ws_summary(exc))
            self._set_state("reconnecting", reason="connect_failed")
            return self._next_backoff()
        if stop_event.is_set():
            await self._retire_socket(ws)
            return None

        async with self._sub_lock:
            self._live_ws = ws
        self._connected_at = self._now()
        try:
            # HSM handshake: binary auth frame from the token's hsm_key,
            # then the full-mode frame, then the subscription frame.
            try:
                hsm_key = _hsm_key_from_access_token(token)
            except Exception as exc:
                self._note_error(f"token_decode: {type(exc).__name__}")
                await self._retire_socket(ws)
                self._set_state("reconnecting", reason="token_decode_failed")
                return self._next_backoff()
            try:
                if not await self._send_frame(ws, _hsm_auth_frame(hsm_key)):
                    raise _SocketNotWritable("auth_frame")
                ack = await self._wait_auth_response(ws, stop_event)
                if not ack:
                    # Server rejected the credentials (or stopped).
                    if stop_event.is_set():
                        await self._retire_socket(ws)
                        return None
                    self._note_error("auth_rejected")
                    await self._retire_socket(ws)
                    self._set_state("reconnecting", reason="auth_rejected")
                    return self._next_backoff()
                if not await self._send_frame(ws, self._full_subscribe_frame()):
                    raise _SocketNotWritable("mode_frame")
                try:
                    # Resolve the desired set on every session. Symbols
                    # that already have a VALID wire topic are reused (the
                    # symbol-token endpoint is a rate-limited Data API:
                    # the daily budget must not be spent on re-resolving
                    # what we already know); symbols discovered while we
                    # were disconnected are resolved now.
                    await self._resolve_hsm_symbols(token)
                except _FyersAuthInvalid:
                    # Token is expired/unauthorized: stop cleanly and surface
                    # "Daily Login Required" instead of an infinite reconnect
                    # loop. The operator re-logs in; no retry storm.
                    self._note_error("token_unauthorized")
                    await self._retire_socket(ws)
                    self._set_state(
                        "auth_required",
                        reason="token_expired_or_unauthorized")
                    self._note_exit("auth_required")
                    return None
                # The reconnect subscribes EXACTLY the current desired set:
                # a symbol nobody wants any more is never restored, even
                # when its cached mapping is still around.
                topics = self._desired_wire_topics()
                if self._desired and not topics:
                    # Symbol resolution failed for the WHOLE desired set:
                    # retry next session rather than subscribing to
                    # useless identity tokens.
                    self._note_error("symbol_resolution_failed")
                    await self._retire_socket(ws)
                    self._set_state("reconnecting",
                                    reason="symbol_resolution_failed")
                    return self._next_backoff()
                if topics and not await self._send_frame(
                        ws, _hsm_subscribe_frame(topics)):
                    raise _SocketNotWritable("subscribe_frame")
            except _SocketNotWritable as exc:
                self._note_error(f"socket_write_failed:{exc}")
                await self._retire_socket(ws)
                self._set_state("reconnecting", reason="subscribe_send_failed")
                return self._next_backoff()
            except Exception as exc:
                self._note_error(_safe_ws_summary(exc))
                await self._retire_socket(ws)
                self._set_state("reconnecting", reason="subscribe_send_failed")
                return self._next_backoff()

            self._set_state("streaming", reason="connected")
            reason = await self._recv_loop(ws, stop_event)
        except asyncio.CancelledError:
            # Parity with UpstoxFeed: close the LOCAL socket — the finally
            # below clears ``_live_ws`` during unwinding.
            await self._retire_socket(ws)
            raise
        except Exception:
            # Unexpected internal error escaping the session: still close.
            await self._retire_socket(ws)
            raise
        finally:
            async with self._sub_lock:
                self._live_ws = None

        await self._retire_socket(ws)
        if reason == "stopped" or stop_event.is_set():
            self._set_state("stopped", reason=self._last_exit_reason)
            return None
        self._reconnect_count += 1
        self._set_state("reconnecting", reason="websocket_closed")
        return self._next_backoff()

    async def _current_token(self) -> str | None:
        getter = self._access_token_getter
        if getter is None:
            return None
        try:
            token = getter()
            if asyncio.iscoroutine(token):
                token = await token
        except Exception as exc:
            self._fail(f"token lookup failed: {type(exc).__name__}")
            return None
        return token.strip() if isinstance(token, str) and token.strip() \
            else None

    async def _connect(self, token: str):
        if self._ws_connect is not None:
            return await self._ws_connect(token)
        import websockets

        # The HSM socket authenticates via a binary frame AFTER connect —
        # no Authorization header (and therefore no websockets-version
        # header-kwarg compatibility issues).
        return await websockets.connect(
            _FYERS_WS_URL,
            close_timeout=2,
        )

    async def _close_quietly(self, ws: Any) -> None:
        try:
            await ws.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("fyers feed %s: close ignored error: %s",
                         self._name, type(exc).__name__)

    async def _recv_loop(self, ws: Any, stop_event: asyncio.Event) -> str:
        """Receive loop with per-message fault isolation.

        A malformed frame increments the malformed counter and continues;
        only transport-level failures end the session.
        """
        stop_task = asyncio.create_task(stop_event.wait())

        async def _ping_loop():
            while True:
                await asyncio.sleep(10)
                # Never write to a socket that is closing or that is no
                # longer the live socket: the ping must not be the writer
                # that trips the transport.
                if not await self._send_frame(ws, self._ping_frame()):
                    return

        ping_task = asyncio.create_task(_ping_loop())
        try:
            while True:
                get_msg = asyncio.create_task(ws.recv())
                wait = asyncio.wait({get_msg, stop_task},
                                    return_when=asyncio.FIRST_COMPLETED)
                done, _pending = await wait
                if stop_task in done:
                    get_msg.cancel()
                    await asyncio.gather(get_msg, return_exceptions=True)
                    return "stopped"
                if get_msg in done:
                    raw = get_msg.result()  # transport errors propagate
                    self._frames_received += 1
                    self._last_message_at = self._now()
                    # The evidence that FRAMES are still arriving (a
                    # connected socket is not evidence of market data).
                    self._last_frame_at = self._last_message_at
                    self._last_frame_mono = self._mono()
                    try:
                        self._handle_message(raw)
                    except NormalizationError as exc:
                        self._malformed_frames += 1
                        logger.debug("fyers feed %s: malformed frame: %s",
                                     self._name, exc)
                    except Exception as exc:
                        # Unexpected internal bug: count + log, keep loop.
                        self._malformed_frames += 1
                        logger.warning(
                            "fyers feed %s: frame handling error: %s",
                            self._name, exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error(_safe_ws_summary(exc))
            return "transport"
        finally:
            stop_task.cancel()
            ping_task.cancel()
            await asyncio.gather(stop_task, ping_task,
                                 return_exceptions=True)

    async def _wait_auth_response(self, ws: Any,
                                  stop_event: asyncio.Event) -> bool:
        """Wait for the HSM auth response; True only on 'K' (ok)."""
        get_msg = asyncio.create_task(ws.recv())
        stop_task = asyncio.create_task(stop_event.wait())
        done, _pending = await asyncio.wait(
            {get_msg, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        stop_task.cancel()
        if get_msg not in done:
            get_msg.cancel()
            await asyncio.gather(get_msg, return_exceptions=True)
            return False
        try:
            raw = get_msg.result()
        except Exception:
            return False
        # Auth response frame: resp_type byte[2] == 1; payload char 'K' = ok
        # (SDK __auth_resp: offset 4 skip, len@5:7, char@7).
        if isinstance(raw, (bytes, bytearray)) and len(raw) > 7:
            ok = raw[7:8] == b"K"
            logger.debug("fyers feed %s: hsm auth %s",
                         self._name, "ok" if ok else "rejected")
            return ok
        return False

    async def _resolve_hsm_symbols(self, token: str,
                                   keys: list[str] | None = None,
                                   force: bool = False) -> None:
        """Resolve instrument keys to HSM wire topics (official contract).

        ANY subset can be resolved - not only the initial desired set - so
        a dynamically added instrument is subscribed with a valid wire
        topic. Unresolved symbols are recorded in ``self._unresolved``
        (and surfaced in ``status()``); they are never turned into a raw
        API identifier on the wire.

        A cached mapping is reused when it is still a VALID wire topic
        for a symbol that is still wanted; anything else is resolved
        again. ``force`` re-resolves every requested symbol (used only
        when a fresh answer is explicitly required).
        """
        wanted = [k for k in (keys if keys is not None else list(self._desired))
                  if k]
        if not wanted:
            return
        if not token:
            for key in wanted:
                self._unresolved.setdefault(key, "no access token for the "
                                                  "symbol-token lookup")
            return
        pending = [k for k in wanted
                   if force or not self._has_valid_mapping(k)]
        if not pending:
            # Everything is already mapped to a VALID wire topic: no
            # symbol-token request is spent.
            for key in wanted:
                if self._has_valid_mapping(key):
                    self._unresolved.pop(key, None)
            return
        try:
            resolver = self._wire_resolver or resolve_wire_symbols
            resolution = await asyncio.to_thread(resolver, pending, token)
        except SymbolTokenAuthError as exc:
            # A 401/403 from the symbol-token REST API means the access token
            # is expired or unauthorized. Raise so _run_session transitions to
            # auth_required ("Daily Login Required") instead of looping on a
            # dead token (which would otherwise spin connecting/reconnecting
            # forever after a MarketHub restart reuses a stale stored token).
            logger.warning(
                "fyers feed %s: symbol-token auth failed (%s) - "
                "token expired/unauthorized", self._name, exc.status)
            raise _FyersAuthInvalid() from exc
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in (401, 403):
                logger.warning(
                    "fyers feed %s: symbol-token auth failed (%s) - "
                    "token expired/unauthorized", self._name, code)
                raise _FyersAuthInvalid() from exc
            logger.warning("fyers feed %s: symbol-token lookup failed: %s",
                           self._name, type(exc).__name__)
            for key in pending:
                if self._has_valid_mapping(key):
                    # A VALID cached mapping still stands: the lookup
                    # failure does not un-resolve the instrument.
                    self._unresolved.pop(key, None)
                    continue
                self._unresolved[key] = (f"symbol-token lookup failed: "
                                         f"{type(exc).__name__}")
            return
        self._hsm_symbols.update(resolution.resolved)
        # Every requested symbol ends up in exactly one of the two states:
        # it has a VALID wire topic, or it is reported as unresolved. A
        # silent drop is not allowed - an unresolved instrument must be
        # visible to the caller.
        for key in wanted:
            if self._has_valid_mapping(key):
                self._unresolved.pop(key, None)
                continue
            self._unresolved[key] = resolution.unresolved.get(
                key, "symbol-token lookup returned no wire symbol")
        self._hsm_by_topic = {v: k for k, v in self._hsm_symbols.items()}
        if resolution.resolved:
            logger.info("fyers feed %s: resolved %d instrument(s) to HSM "
                        "wire topics (%d unresolved)",
                        self._name, len(resolution.resolved),
                        len(resolution.unresolved))
        if resolution.unresolved:
            logger.warning("fyers feed %s: %d instrument(s) could not be "
                           "resolved: %s", self._name,
                           len(resolution.unresolved),
                           ", ".join(f"{k} ({v})" for k, v in
                                     list(resolution.unresolved.items())[:5]))

    def _handle_message(self, raw: Any) -> None:
        """Decode one socket frame (binary HSM or legacy text) — isolated."""
        if isinstance(raw, str):
            # Text frames are not part of the HSM protocol; tolerate them.
            logger.debug("fyers feed %s: text frame ignored (%d chars)",
                         self._name, len(raw))
            return
        if not isinstance(raw, (bytes, bytearray)) or len(raw) < 3:
            raise NormalizationError("frame too short")
        resp_type = raw[2]
        if resp_type == 6:
            self._parse_datafeed(bytes(raw))
        elif resp_type == 1:
            logger.debug("fyers feed %s: late auth ack", self._name)
        elif resp_type in (4, 5, 7, 8, 12):
            logger.debug("fyers feed %s: hsm ack type=%d", self._name,
                         resp_type)
        else:
            logger.debug("fyers feed %s: unknown frame type=%d",
                         self._name, resp_type)

    def _parse_datafeed(self, data: bytes) -> None:
        """Decode a binary datafeed frame (resp_type 6) into quote patches.

        Ports the SDK's __datafeed_resp: snapshot (83) carries the full
        field set plus multiplier/precision/identity; update (85) carries
        changed fields only. Depth ('dp') topics are tolerated and skipped
        (REST depth is used instead).
        """
        import struct
        scrip_count = struct.unpack("!H", data[7:9])[0]
        offset = 9
        for _ in range(scrip_count):
            if offset >= len(data):
                break
            data_type = data[offset]
            if data_type == 83:      # snapshot
                offset += 1
                topic_id = struct.unpack("H", data[offset:offset + 2])[0]
                offset += 2
                name_len = data[offset]
                offset += 1
                topic_name = data[offset:offset + name_len].decode(
                    "utf-8", "replace")
                offset += name_len
                field_count = data[offset]
                offset += 1
                values: dict[str, int] = {}
                is_depth = topic_name.startswith("dp")
                val_map = _DEPTH_VAL if is_depth else (
                    _INDEX_VAL if topic_name.startswith("if") else _DATA_VAL)
                for idx in range(field_count):
                    value = struct.unpack(">i", data[offset:offset + 4])[0]
                    offset += 4
                    if idx < len(val_map) and value != -2147483648:
                        values[val_map[idx]] = value
                # The official SDK skips TWO bytes between the value array
                # and the multiplier (identical in its dp / if / sf
                # snapshot branches). Reading the multiplier at this
                # offset instead reads two bytes early, which yields a
                # wrong scale for EVERY instrument type (that is how a
                # NIFTY index multiplier of 32 was observed instead of
                # 100). Reference: fyers_apiv3/FyersWebsocket/data_ws.py.
                offset += 2
                multiplier = struct.unpack(">H", data[offset:offset + 2])[0]
                offset += 2
                precision = data[offset]
                offset += 1
                for _i in range(3):   # exchange, exchange_token, symbol str
                    slen = data[offset]
                    offset += 1 + slen
                self._sym_data[topic_id] = {
                    "values": values, "multiplier": multiplier,
                    "precision": precision, "topic": topic_name}
                if is_depth:
                    # Emit depth separately (does not go through _emit_tick).
                    # The SDK scales the FIRST TEN depth fields (the five
                    # bid and five ask prices) and leaves sizes/order counts
                    # raw.
                    depth_scale = (10 ** precision) * multiplier
                    scaled = dict(values)
                    # The SDK carries the API symbol with the depth
                    # message (it is the identity of the instrument).
                    original_symbol = self._hsm_by_topic.get(topic_name)
                    if original_symbol:
                        scaled["symbol"] = original_symbol
                    for idx in range(min(10, len(_DEPTH_VAL))):
                        name = _DEPTH_VAL[idx]
                        if name in scaled and depth_scale:
                            scaled[name] = scaled[name] / depth_scale
                    received = self._received_ts()
                    try:
                        self._apply_depth(scaled, received)
                    except NormalizationError:
                        raise
                else:
                    self._emit_tick(topic_id)
            elif data_type == 85:    # delta update
                offset += 1
                topic_id = struct.unpack("H", data[offset:offset + 2])[0]
                offset += 2
                field_count = data[offset]
                offset += 1
                snap = self._sym_data.get(topic_id)
                if snap is None:
                    offset += field_count * 4   # delta before snapshot
                    continue
                val_map = (_INDEX_VAL if snap["topic"].startswith("if")
                           else _DATA_VAL)
                changed = False
                for idx in range(field_count):
                    value = struct.unpack(">i", data[offset:offset + 4])[0]
                    offset += 4
                    if idx >= len(val_map) or value == -2147483648:
                        continue
                    name = val_map[idx]
                    if snap["values"].get(name) != value:
                        snap["values"][name] = value
                        changed = True
                if changed:
                    self._emit_tick(topic_id)
            else:
                break                     # unknown record type: stop parse

    def _emit_tick(self, topic_id: int) -> None:
        """Scale stored raw fields and deliver one canonical patch.

        Scaling follows the official SDK: the raw integer is divided by
        ``(10 ** precision) * multiplier``. Index topics ("if") are scaled
        with the same formula and are validated against the previous close
        the broker sends in the SAME frame (an index cannot move 50% from
        its previous close) - that check catches a mis-decoded scale
        instead of publishing a wrong index value.
        """
        snap = self._sym_data.get(topic_id)
        if snap is None:
            return
        scale = (10 ** snap["precision"]) * snap["multiplier"]
        if not scale or scale <= 0:
            self._malformed_frames += 1
            logger.warning("fyers feed %s: invalid scale (multiplier=%s "
                           "precision=%s) for topic %s", self._name,
                           snap["multiplier"], snap["precision"],
                           snap["topic"])
            return
        msg: dict[str, Any] = {}
        for name, raw_value in snap["values"].items():
            if name in _SCALED_VAL:
                msg[name] = raw_value / scale
            elif name in ("last_traded_time", "exch_feed_time"):
                continue                  # received_ts covers timing
            else:
                msg[name] = raw_value
        original = self._hsm_by_topic.get(snap["topic"])
        if original:
            msg["symbol"] = original
        elif snap["topic"].startswith(("sf", "if")):
            return                        # unresolvable identity: skip
        if snap["topic"].startswith("if") and not self._plausible_index(msg):
            self._index_value_rejections += 1
            logger.warning("fyers feed %s: rejected implausible index value "
                           "for %s (ltp=%s prev_close=%s)", self._name,
                           original or snap["topic"], msg.get("ltp"),
                           msg.get("prev_close_price"))
            return
        if snap["topic"].startswith("if"):
            # Runaway guard: an index tick that jumps far from the last
            # ACCEPTED value of the same instrument is a mis-decoded scale
            # (the frame's own prev_close is scaled with it, so it cannot
            # detect the error on its own). This is a safety net - NOT
            # evidence of correctness (the correctness evidence is the
            # recorded live capture replayed in test/test_fyers_decoder.py).
            ltp = msg.get("ltp")
            previous = self._last_accepted_ltp.get(original or
                                                    snap["topic"])
            if (previous and isinstance(ltp, (int, float))
                    and previous > 0
                    and abs(float(ltp) / previous - 1.0) > 0.25):
                self._index_value_rejections += 1
                logger.warning("fyers feed %s: rejected index value jump for "
                               "%s (%.4f -> %.4f)", self._name,
                               original or snap["topic"], previous, ltp)
                return
            if isinstance(ltp, (int, float)) and ltp > 0:
                self._last_accepted_ltp[original or snap["topic"]] = float(ltp)
        received = self._received_ts()
        try:
            self._apply_quote(msg, received)
        except NormalizationError:
            raise
        # (inner errors are isolated by the recv loop)
        if original:
            self._last_data_at[original] = self._now()
            self._last_data_mono[original] = self._mono()

    @staticmethod
    def _plausible_index(msg: dict[str, Any]) -> bool:
        """Sanity-check a decoded index tick against its previous close.

        A mis-decoded scale (the defect this decoder used to have) shows
        up as a value far away from the previous close the broker sends in
        the same frame. Indices cannot move 50% within a session, so the
        band is deliberately wide - it only rejects a broken decode.
        """
        ltp = msg.get("ltp")
        prev = msg.get("prev_close_price")
        try:
            if ltp is None or float(ltp) <= 0:
                return False
            if prev is None or float(prev) <= 0:
                return True               # nothing to compare against
            ratio = float(ltp) / float(prev)
        except (TypeError, ValueError, ZeroDivisionError):
            return False
        return 0.5 <= ratio <= 1.5

    def _apply_quote(self, m: dict, received) -> None:
        from market.service import QuotePatch
        fields = quote_fields_from_symbol_update(m, received_ts=received)
        token = fields.pop("instrument_token")
        exchange = fields.pop("exchange")
        tradingsymbol = fields.pop("tradingsymbol", "")
        fields.pop("received_ts", None)   # patch-level, not a reported field
        patch = QuotePatch(exchange=exchange, instrument_token=token,
                           tradingsymbol=tradingsymbol,
                           received_ts=received, reported_fields=fields)
        if self._market_service is not None:
            asyncio.ensure_future(self._deliver(patch))

    def _apply_depth(self, m: dict, received) -> None:
        from market.normalize.fyers import depth_from_ws_depth

        if self._market_service is None:
            return
        depth, _extra = depth_from_ws_depth(m, received_ts=received)
        asyncio.ensure_future(self._market_service.apply_depth(depth))

    def _deliver(self, patch) -> None:
        return self._market_service.apply_quote(patch)

    def _received_ts(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    # -- TBT depth channel -------------------------------------------------------

    async def _start_tbt_channel(self, stop_event: asyncio.Event) -> None:
        """Start TBT depth channel if enabled."""
        if not self._tbt_enabled:
            return

        try:
            from brokers.fyers.tbt.feed import FyersTbtFeed

            def _app_id_getter() -> str:
                try:
                    return self._resolve_app_id()
                except Exception:
                    return str(self._app_id or "")

            self._tbt_feed = FyersTbtFeed(
                access_token_getter=self._access_token_getter,
                config=self._config,
                market_service=self._market_service,
                app_id_getter=_app_id_getter,
            )

            # Seed TBT with the current desired instrument set so initial connect
            # and reconnects restore the latest parent lifecycle state.
            async with self._sub_lock:
                desired = list(self._desired)
            if desired:
                await self._tbt_feed.subscribe(desired)

            self._tbt_task = asyncio.create_task(
                self._tbt_feed.run(publisher=None, stop_event=stop_event)
            )
            logger.info("TBT depth channel started")

        except Exception as e:
            logger.error("Failed to start TBT channel: %s", type(e).__name__)

    async def _stop_tbt_channel(self) -> None:
        """Stop TBT depth channel (robust, exception-safe)."""
        feed = self._tbt_feed
        task = self._tbt_task
        self._tbt_feed = None
        self._tbt_task = None
        if feed is not None:
            try:
                await feed.stop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("TBT feed stop raised: %s", type(exc).__name__)
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("TBT task await raised: %s", type(exc).__name__)
            logger.info("TBT depth channel stopped")

    def _tbt_status(self) -> dict[str, Any]:
        """Get TBT channel status."""
        if not self._tbt_feed:
            return {"enabled": self._tbt_enabled, "state": "not_started"}
        return self._tbt_feed.status()

    def _fail(self, message: str) -> None:
        self._last_error = message
        logger.error("fyers feed %s: terminal failure - %s",
                     self._name, message)

