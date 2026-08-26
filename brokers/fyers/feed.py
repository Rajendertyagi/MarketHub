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
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import deque
from typing import Any, Callable

from brokers.fyers.auth import USER_AGENT
from market.normalize.fyers import (
    NormalizationError,
    quote_fields_from_symbol_update,
)

logger = logging.getLogger("event_server")

_FYERS_WS_URL = "wss://socket.fyers.in/hsm/v1-5/prod"

_STATE_TERMINAL = "failed"

# Fallback only — composition always injects the centralized redirect URI.
_DEFAULT_FYERS_REDIRECT = "http://localhost:7070/auth/fyers/callback"


class _Terminal:
    """Sentinel returned by _run_session on terminal failure."""


_TERMINAL = _Terminal()

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

_SYMBOL_TOKEN_API = "https://api-t1.fyers.in/data/symbol-token"
_HSM_SOURCE = "MarketHub-1.0"
_HSM_CHANNEL = 11

# fytoken[:4] -> HSM segment name (official map.json).
_EXCH_SEG = {
    "1010": "nse_cm", "1011": "nse_fo", "1120": "mcx_fo",
    "1210": "bse_cm", "1012": "cde_fo", "1211": "bse_fo",
    "1212": "bcs_fo", "1020": "nse_com",
}

# Known index symbols -> HSM index token (official map.json, NSE section).
_INDEX_DICT = {
    "NSE:NIFTY50-INDEX": "Nifty 50",
    "NSE:NIFTYBANK-INDEX": "Nifty Bank",
    "NSE:NIFTYNEXT50-INDEX": "Nifty Next 50",
    "NSE:FINNIFTY-INDEX": "Nifty Fin Service",
    "NSE:MIDCPNIFTY-INDEX": "NIFTY MID SELECT",
    "NSE:NIFTYMIDSELECT-INDEX": "NIFTY MID SELECT",
    "NSE:INDIAVIX-INDEX": "India VIX",
    "NSE:NIFTY500-INDEX": "Nifty 500",
    "NSE:NIFTY200-INDEX": "Nifty 200",
    "NSE:NIFTY100-INDEX": "Nifty 100",
    "NSE:NIFTYMIDCAP150-INDEX": "NIFTY MIDCAP 150",
    "NSE:NIFTYMIDCAP100-INDEX": "NIFTY MIDCAP 100",
    "NSE:NIFTYMIDCAP50-INDEX": "Nifty Midcap 50",
    "NSE:NIFTYSMLCAP250-INDEX": "NIFTY SMLCAP 250",
    "NSE:NIFTYSMLCAP100-INDEX": "NIFTY SMLCAP 100",
    "NSE:NIFTYLARGEMID250-INDEX": "NIFTY LARGEMID250",
    "NSE:NIFTYIT-INDEX": "Nifty IT",
    "NSE:NIFTYPHARMA-INDEX": "Nifty Pharma",
    "NSE:NIFTYAUTO-INDEX": "Nifty Auto",
    "NSE:NIFTYFMCG-INDEX": "Nifty FMCG",
    "NSE:NIFTYMETAL-INDEX": "Nifty Metal",
    "NSE:NIFTYENERGY-INDEX": "Nifty Energy",
    "NSE:NIFTYREALTY-INDEX": "Nifty Realty",
    "NSE:NIFTYINFRA-INDEX": "Nifty Infra",
    "NSE:NIFTYPVTBANK-INDEX": "Nifty Pvt Bank",
    "NSE:NIFTYPSUBANK-INDEX": "Nifty PSU Bank",
    "NSE:NIFTYMNC-INDEX": "Nifty MNC",
    "NSE:NIFTYPSE-INDEX": "Nifty PSE",
    "NSE:NIFTYALPHA50-INDEX": "NIFTY Alpha 50",
    "NSE:NIFTYQUALITY30-INDEX": "NIFTY100 Qualty30",
    "NSE:NIFTYCONSUMPTION-INDEX": "Nifty Commodities",
    "NSE:NIFTYSERVSECTOR-INDEX": "Nifty Serv Sector",
}

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
    buf.append(2); buf.extend(struct.pack("!H", 1)); buf.append(ord("P"))
    buf.append(3); buf.extend(struct.pack("!H", 1)); buf.append(1)
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
        self._credential_store = config.get("credential_store")
        self._redirect_uri = config.get("redirect_uri", _DEFAULT_FYERS_REDIRECT)
        self._market_service = market_service
        self._ws_connect = config.get("ws_connect")
        self._utc_now_iso = config.get("utc_now_iso")

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
        self._hsm_symbols: dict[str, str] = {}      # config key -> HSM token
        self._hsm_by_topic: dict[int, str] = {}     # topic id -> config key
        self._sym_data: dict[int, dict] = {}        # topic id -> last fields
        self._frames_received = 0
        self._malformed_frames = 0
        self._last_message_at: str | None = None
        self._last_error: str | None = None
        self._connected_at: str | None = None
        self._started_at: str | None = None

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
        return {
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
        }

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

    def _mutation_frame(self, sub_type: str, symbols: list[str]) -> bytes:
        hsm = [self._hsm_symbols.get(s, s) for s in symbols]
        if sub_type == "unsub":
            return _hsm_unsubscribe_frame(hsm)
        return _hsm_subscribe_frame(hsm)

    def _full_subscribe_frame(self) -> bytes:
        return _hsm_mode_frame()

    async def _send_mutation(self, frame: bytes) -> bool:
        async with self._sub_lock:
            ws = self._live_ws
            if ws is None:
                return False
            try:
                await ws.send(frame)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("fyers feed %s: mutation send failed: %s",
                             self._name, type(exc).__name__)
                return False

    async def add_instruments(self, symbols: list[str]) -> int:
        async with self._sub_lock:
            existing = set(self._desired)
            fresh = [s for s in symbols if s and s not in existing]
            if fresh:
                self._desired = tuple(sorted(existing | set(fresh)))
        if fresh and self._state == "streaming":
            await self._send_mutation(
                self._mutation_frame("SymbolUpdate", fresh))
        return len(fresh)

    async def remove_instruments(self, symbols: list[str]) -> int:
        async with self._sub_lock:
            existing = set(self._desired)
            gone = [s for s in symbols if s in existing]
            remaining = existing - set(gone)
            if not remaining:
                return 0
            self._desired = tuple(sorted(remaining))
        if gone and self._state == "streaming":
            await self._send_mutation(self._mutation_frame("unsub", gone))
        return len(gone)

    # -- session ------------------------------------------------------------------

    async def run(self, publisher: Any, stop_event: asyncio.Event) -> None:
        """Shared lifecycle loop: authorize->connect->subscribe->recv."""
        del publisher
        self._started_at = self._now()
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
            await self._close_quietly(ws)
            return None

        self._live_ws = ws
        self._connected_at = self._now()
        try:
            # HSM handshake: binary auth frame from the token's hsm_key,
            # then the full-mode frame, then the subscription frame.
            try:
                hsm_key = _hsm_key_from_access_token(token)
            except Exception as exc:
                self._note_error(f"token_decode: {type(exc).__name__}")
                await self._close_quietly(ws)
                self._set_state("reconnecting", reason="token_decode_failed")
                return self._next_backoff()
            try:
                await ws.send(_hsm_auth_frame(hsm_key))
                ack = await self._wait_auth_response(ws, stop_event)
                if not ack:
                    # Server rejected the credentials (or stopped).
                    if stop_event.is_set():
                        await self._close_quietly(ws)
                        return None
                    self._note_error("auth_rejected")
                    await self._close_quietly(ws)
                    self._set_state("reconnecting", reason="auth_rejected")
                    return self._next_backoff()
                await ws.send(self._full_subscribe_frame())
                await self._resolve_hsm_symbols(token)
                if not self._hsm_symbols:
                    # Symbol resolution failed: retry next session rather
                    # than subscribing to useless identity tokens.
                    self._note_error("symbol_resolution_failed")
                    await self._close_quietly(ws)
                    self._set_state("reconnecting",
                                    reason="symbol_resolution_failed")
                    return self._next_backoff()
                await ws.send(_hsm_subscribe_frame(
                    list(self._hsm_symbols.values())))
            except Exception as exc:
                self._note_error(_safe_ws_summary(exc))
                await self._close_quietly(ws)
                self._set_state("reconnecting", reason="subscribe_send_failed")
                return self._next_backoff()

            self._set_state("streaming", reason="connected")
            reason = await self._recv_loop(ws, stop_event)
        except asyncio.CancelledError:
            # Parity with UpstoxFeed: close the LOCAL socket — the finally
            # below clears ``_live_ws`` during unwinding.
            await self._close_quietly(ws)
            raise
        except Exception:
            # Unexpected internal error escaping the session: still close.
            await self._close_quietly(ws)
            raise
        finally:
            async with self._sub_lock:
                self._live_ws = None

        await self._close_quietly(ws)
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
                try:
                    await ws.send(self._ping_frame())
                except Exception:
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

    async def _resolve_hsm_symbols(self, token: str) -> None:
        """Map configured instrument keys to HSM tokens via the REST API."""
        if self._hsm_symbols:
            return

        def _convert() -> dict:
            import urllib.request
            bare = token.split(":", 1)[1] if ":" in token else token
            req = urllib.request.Request(
                _SYMBOL_TOKEN_API,
                data=json.dumps({"symbols": list(self._desired)}).encode(),
                method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": bare,
                         "User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())

        try:
            payload = await asyncio.to_thread(_convert)
        except Exception as exc:
            # Leave _hsm_symbols EMPTY so the next session retries the
            # lookup instead of caching a useless identity mapping.
            logger.warning("fyers feed %s: symbol-token lookup failed: %s",
                           self._name, type(exc).__name__)
            self._hsm_by_topic = {}
            return
        valid = payload.get("validSymbol") or {}
        out: dict[str, str] = {}
        for key in self._desired:
            fytoken = valid.get(key)
            seg = _EXCH_SEG.get((fytoken or "")[:4], "nse_cm")
            if key.endswith("-INDEX"):
                tok = _INDEX_DICT.get(key) \
                    or key.split(":", 1)[-1].split("-")[0]
                out[key] = f"if|{seg}|{tok}"
            elif fytoken:
                out[key] = f"sf|{seg}|{fytoken[10:]}"
            else:
                out[key] = key
        self._hsm_symbols = out
        self._hsm_by_topic = {v: k for k, v in out.items()}
        logger.info("fyers feed %s: subscribed %d instrument(s)",
                    self._name, len(out))

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
                if not is_depth:
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
        """Scale stored raw fields and deliver one canonical patch."""
        snap = self._sym_data.get(topic_id)
        if snap is None:
            return
        scale = (10 ** snap["precision"]) * snap["multiplier"]
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
        received = self._received_ts()
        try:
            self._apply_quote(msg, received)
        except NormalizationError:
            raise
        # (inner errors are isolated by the recv loop)

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

    def _fail(self, message: str) -> None:
        self._last_error = message
        logger.error("fyers feed %s: terminal failure - %s",
                     self._name, message)

