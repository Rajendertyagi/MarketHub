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

    # -- frames -------------------------------------------------------------------

    def _join_frame(self) -> bytes:
        return json.dumps({"type": 1}, separators=(",", ":")).encode()

    def _ping_frame(self) -> bytes:
        return json.dumps({"type": 3}, separators=(",", ":")).encode()

    def _mutation_frame(self, sub_type: str, symbols: list[str]) -> bytes:
        return json.dumps({
            "type": 2,
            "data": {"symbols": symbols, "subType": sub_type},
        }, separators=(",", ":")).encode()

    def _full_subscribe_frame(self) -> bytes:
        return self._mutation_frame("SymbolUpdate", list(self._desired))

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
                if isinstance(outcome, _TERMINAL):
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
            try:
                await ws.send(self._join_frame())
                await ws.send(self._full_subscribe_frame())
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
        import websockets

        app_id = self._resolve_app_id()
        if self._ws_connect is not None:
            return await self._ws_connect(token)
        return await websockets.connect(
            _FYERS_WS_URL,
            extra_headers={"Authorization":
                           f"{app_id}:{token}"},
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

    def _handle_message(self, raw: Any) -> None:
        """Decode one text frame into canonical patches (isolated)."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        msg = json.loads(raw)
        if not isinstance(msg, dict):
            raise NormalizationError("frame is not a JSON object")
        msgs = msg.get("data") if isinstance(msg.get("data"), list) \
            else [msg]
        for m in msgs:
            if not isinstance(m, dict):
                continue
            mtype = m.get("type", "sf")
            received = self._received_ts()
            if mtype == "dp":
                self._apply_depth(m, received)
            elif mtype in ("sf", "if"):
                self._apply_quote(m, received)
            # unknown types are ignored silently (protocol tolerance)

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

