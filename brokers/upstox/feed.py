"""
Upstox V3 WebSocket feed adapter (Phase D3.1) — transport/lifecycle only.

Owns exactly one concern: keeping ONE shared upstream WebSocket connection
per configured account alive and subscribed:

    authorize (D2 REST) -> connect -> subscribe -> receive/count
        -> reconnect with FRESH authorization -> resubscribe

Incoming frames are COUNTED ONLY in D3.1. Protobuf decoding, B1
normalization, QuotePatch creation and MarketService application arrive in
D3.2 at the single processing seam (``_process_frame`` placeholder).

Locked behaviours:
    * SEC: the authorized WSS URI embeds a one-time code — it is never
      stored, logged, or exposed through status(); websocket exception
      text is reduced to safe summaries.
    * WS-2: reconnect backoff resets only after a connection stayed up
      >= 60 s (monotonic clock), never merely on entering streaming.
    * WS-3: the injected ``ws_connect`` receives the exact production
      settings used by the default websockets connector.
    * WS-5: recv-vs-stop races cancel and await every temporary task on
      every exit path; no orphan tasks.
    * raw market data must NEVER go through the generic event pipeline —
      the EventSource ``publisher`` argument is intentionally unused here;
      low-frequency provider events are deferred to a later phase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Callable

from websockets.asyncio.client import connect as _websockets_connect

from brokers.upstox.auth import UpstoxCredentials
from brokers.upstox.errors import (
    UpstoxAuthError,
    UpstoxConfigError,
    UpstoxRateLimitError,
    UpstoxRestError,
)

logger = logging.getLogger(__name__)

__all__ = ["UpstoxFeed"]

# ---------------------------------------------------------------------------
# Provider limits / locked settings (official docs, Aug 2026)
# ---------------------------------------------------------------------------

SUPPORTED_MODES = ("ltpc", "full")
_MODE_KEY_LIMITS = {"ltpc": 5000, "full": 2000}  # individual per-mode limits

_WS_SETTINGS: dict[str, Any] = {
    "open_timeout": 10,
    "ping_interval": 20,
    "ping_timeout": 20,
    "close_timeout": 5,
    "max_size": 1048576,
    "max_queue": 32,
    "compression": None,  # protobuf payloads gain little from permessage-deflate
}

# Backoff (project choices — Upstox documents no guidance)
_BACKOFF_BASE_S = 1.0
_BACKOFF_FACTOR = 2.0
_BACKOFF_CAP_S = 30.0
_JITTER_FRACTION = 0.2
_STABLE_CONNECTION_S = 60.0       # WS-2: reset threshold (monotonic)
_RETRY_AFTER_CEILING_S = 120.0    # safety ceiling on provider Retry-After hints

_STATES = ("stopped", "authorizing", "connecting", "streaming",
           "reconnecting", "failed")

_TERMINAL = object()   # sentinel: terminal failure inside _run_session


def _safe_ws_summary(exc: BaseException) -> str:
    """Reduce a websocket-library exception to a SAFE summary.

    Exception text may embed the authorized URI (with its one-time code),
    so raw str(exc)/repr(exc) is never stored or logged.
    """
    try:
        import websockets.exceptions as wse

        if isinstance(exc, wse.ConnectionClosedOK):
            return "websocket closed (clean)"
        if isinstance(exc, wse.ConnectionClosedError):
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            return f"websocket closed: code={code}" if code is not None \
                else "websocket closed: code=1006"
        if isinstance(exc, wse.InvalidStatus):
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            return f"websocket handshake rejected: HTTP {status}"
    except Exception:  # pragma: no cover - classification must never raise
        pass
    return "websocket operation failed"


def _default_ws_connect(uri: str, **settings: Any):
    """Production connector: new asyncio API of websockets >=17."""
    return _websockets_connect(uri, **settings)


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------


class UpstoxFeed:
    """One shared upstream V3 WebSocket connection per configured account.

    EventSource-compatible shape (``name`` / ``run(publisher, stop_event)``
    / ``status()``). The ``publisher`` argument is accepted for protocol
    compatibility and intentionally unused: raw market data must never go
    through the generic event pipeline.

    D3.1 counts incoming frames only — decoding/normalization/application
    arrive in D3.2 at ``_process_frame``.
    """

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        credentials: UpstoxCredentials,
        rest: Any,
        market_service: Any = None,
        ws_connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], Any] | None = None,
        random_jitter: Callable[[float, float], float] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        # -- config validation ------------------------------------------------
        source_name = config.get("source_name")
        if not isinstance(source_name, str) or not source_name.strip():
            raise UpstoxConfigError("source_name must be a non-empty string")
        self._name = source_name.strip()

        mode = config.get("mode", "ltpc")
        if not isinstance(mode, str) or mode not in SUPPORTED_MODES:
            raise UpstoxConfigError(
                f"mode must be one of {list(SUPPORTED_MODES)}; got {mode!r}"
            )
        self._mode = mode

        keys_raw = config.get("instrument_keys")
        if isinstance(keys_raw, (str, bytes)) or not isinstance(keys_raw, Sequence):
            raise UpstoxConfigError(
                "instrument_keys must be a sequence of strings"
            )
        keys: list[str] = []
        seen: set[str] = set()
        for key in keys_raw:
            if not isinstance(key, str) or not key.strip():
                raise UpstoxConfigError(
                    "instrument_keys entries must be non-empty strings"
                )
            stripped = key.strip()
            if stripped not in seen:
                seen.add(stripped)
                keys.append(stripped)
        if not keys:
            raise UpstoxConfigError(
                "instrument_keys must contain at least one key"
            )
        limit = _MODE_KEY_LIMITS[mode]
        if len(keys) > limit:
            raise UpstoxConfigError(
                f"mode {mode!r} supports at most {limit} instrument keys; "
                f"got {len(keys)}"
            )
        self._instrument_keys: tuple[str, ...] = tuple(keys)

        # -- dependencies -----------------------------------------------------
        if not isinstance(credentials, UpstoxCredentials):
            raise UpstoxConfigError(
                "credentials must be an UpstoxCredentials instance"
            )
        self._credentials = credentials
        if rest is None or not callable(getattr(rest, "authorize_market_feed", None)):
            raise UpstoxConfigError(
                "rest must provide authorize_market_feed()"
            )
        self._rest = rest
        # MarketService is accepted now (stable constructor) but is
        # intentionally UNUSED until D3.2 wires market-data processing.
        self._market_service = market_service

        # -- test seams ---------------------------------------------------------
        self._ws_connect: Callable[..., Any] = ws_connect or _default_ws_connect
        self._sleep: Callable[[float], Any] = sleep or asyncio.sleep
        self._random_jitter = random_jitter or random.uniform
        self._monotonic = monotonic or time.monotonic

        # -- state --------------------------------------------------------------
        self._state = "stopped"
        self._ws: Any = None
        self._backoff_s = _BACKOFF_BASE_S
        self._connection_started_mono: float | None = None
        self._counters: dict[str, int] = {
            "frames_received": 0,
            "binary_frames": 0,
            "text_frames": 0,
        }
        self._last_connected_at: str | None = None
        self._last_message_at: str | None = None
        self._started_at: str | None = None
        self._connected_at: str | None = None
        self._connect_attempts: int = 0
        self._reconnect_count: int = 0
        self._subscribed_instruments: int | None = None
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._segment_status: dict[str, str] | None = None

    # -- helpers --------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            logger.info("upstox feed %s: state -> %s", self._name, state)

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _note_error(self, summary: str) -> None:
        self._last_error = summary
        self._last_error_at = self._utc_now_iso()
        logger.warning("upstox feed %s: %s", self._name, summary)

    def _fail(self, summary: str) -> None:
        self._note_error(summary)
        self._set_state("failed")
        logger.error("upstox feed %s: terminal failure - %s",
                     self._name, summary)

    def _next_backoff(self, retry_after_seconds: int | None = None) -> float:
        jitter_span = _JITTER_FRACTION * self._backoff_s
        delay = min(self._backoff_s + self._random_jitter(-jitter_span, jitter_span),
                    _BACKOFF_CAP_S)
        self._backoff_s = min(self._backoff_s * _BACKOFF_FACTOR, _BACKOFF_CAP_S)
        if retry_after_seconds is not None:
            hint = min(retry_after_seconds, _RETRY_AFTER_CEILING_S)
            delay = max(delay, hint)
        return delay

    def _reset_backoff_if_stable(self, lifetime_s: float) -> None:
        """WS-2: backoff resets only after a STABLE connection interval."""
        if lifetime_s >= _STABLE_CONNECTION_S:
            self._backoff_s = _BACKOFF_BASE_S
            logger.info("upstox feed %s: stable connection %.1fs - "
                        "backoff reset", self._name, lifetime_s)

    async def _wait_or_stop(self, stop_event: asyncio.Event, delay: float) -> bool:
        """Wait up to ``delay`` seconds. Returns True when stop_event fired
        first. WS-5: all temporary tasks are cancelled and awaited on every
        exit path."""
        stop_task = asyncio.create_task(stop_event.wait())
        sleep_task = asyncio.create_task(self._sleep(delay))
        try:
            done, pending = await asyncio.wait(
                {stop_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (stop_task, sleep_task):
                if not task.done():
                    task.cancel()
            # WS-5: always reap cancelled/pending tasks.
            await asyncio.gather(stop_task, sleep_task, return_exceptions=True)
        return stop_event.is_set()

    def _subscription_frame(self) -> bytes:
        payload = {
            "guid": uuid.uuid4().hex,
            "method": "sub",
            "data": {
                "mode": self._mode,
                "instrumentKeys": list(self._instrument_keys),
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")

    def _safe_ws_summary(self, exc: BaseException) -> str:
        return _safe_ws_summary(exc)

    async def _close_quietly(self, ws: Any) -> None:
        try:
            await ws.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("upstox feed %s: close ignored error: %s",
                         self._name, type(exc).__name__)

    # -- session ----------------------------------------------------------------

    async def _run_session(self, stop_event: asyncio.Event):
        """One authorize->connect->subscribe->recv cycle.

        Returns:
            None          clean stop requested
            _TERMINAL     terminal failure (state already failed)
            float         retryable failure; suggested delay in seconds
        """
        self._set_state("authorizing")
        self._connect_attempts += 1
        try:
            uri = await self._rest.authorize_market_feed(self._credentials)
        except UpstoxAuthError as exc:
            self._fail(f"authorization rejected: {exc}")
            return _TERMINAL
        except UpstoxRateLimitError as exc:
            self._set_state("reconnecting")
            hint = exc.retry_after_seconds
            logger.warning("upstox feed %s: rate limited during authorize "
                           "(retry_after=%s)", self._name, hint)
            return self._next_backoff(hint)
        except UpstoxRestError as exc:
            if not exc.retryable:
                self._fail(f"authorization failed: {exc}")
                return _TERMINAL
            self._set_state("reconnecting")
            logger.warning("upstox feed %s: retryable authorize failure - %s",
                           self._name, exc)
            return self._next_backoff()
        if stop_event.is_set():
            return None

        self._set_state("connecting")
        try:
            ws = await self._ws_connect(uri, **_WS_SETTINGS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error(_safe_ws_summary(exc))
            self._set_state("reconnecting")
            logger.warning("upstox feed %s: websocket connect failed - %s",
                           self._name, self._last_error)
            return self._next_backoff()

        self._connected_at = self._utc_now_iso()
        connection_started_mono = self._monotonic()
        try:
            await ws.send(self._subscription_frame())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error(_safe_ws_summary(exc))
            await self._close_quietly(ws)
            self._set_state("reconnecting")
            logger.warning("upstox feed %s: subscription send failed - %s",
                           self._name, self._last_error)
            return self._next_backoff()

        self._set_state("streaming")
        reason = await self._recv_loop(ws, stop_event)
        lifetime = self._monotonic() - connection_started_mono
        self._reset_backoff_if_stable(lifetime)
        await self._close_quietly(ws)
        if reason == "stopped" or stop_event.is_set():
            self._set_state("stopped")
            return None
        self._reconnect_count += 1
        self._set_state("reconnecting")
        logger.info("upstox feed %s: connection dropped (%s) - reconnect #%d",
                    self._name, reason, self._reconnect_count)
        return self._next_backoff()

    # -- receive loop -------------------------------------------------------------

    async def _recv_loop(self, ws: Any, stop_event: asyncio.Event) -> str:
        """Count frames until the connection drops or stop fires.

        Returns 'stopped' or 'closed'.
        """
        recv_task = asyncio.create_task(ws.recv())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            pending = {recv_task, stop_task}
            while True:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                if stop_task in done:
                    # WS-5: cancel + await the recv task before leaving.
                    recv_task.cancel()
                    await asyncio.gather(recv_task, return_exceptions=True)
                    return "stopped"
                # recv completed
                exc = recv_task.exception()
                if exc is not None:
                    self._note_error(_safe_ws_summary(exc))
                    return "closed"
                message = recv_task.result()
                self._count_frame(message)
                recv_task = asyncio.create_task(ws.recv())
                pending = {recv_task, stop_task}
        finally:
            # WS-5: no temporary task survives any exit path.
            for task in (recv_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(recv_task, stop_task, return_exceptions=True)

    def _count_frame(self, message: Any) -> None:
        self._counters["frames_received"] += 1
        if isinstance(message, bytes):
            self._counters["binary_frames"] += 1
        elif isinstance(message, str):
            self._counters["text_frames"] += 1
        self._last_message_at = self._utc_now_iso()
        # D3.2 will call _process_frame(message) here.

    # -- public API ------------------------------------------------------------

    @property
    def name(self) -> str:  # kept as attribute+property read-only pair
        return self._name

    async def run(self, publisher, stop_event: asyncio.Event) -> None:
        """Run the feed until stop_event fires or a terminal failure occurs.

        ``publisher`` is part of the EventSource protocol and intentionally
        unused: raw market data must never flow through the generic event
        pipeline (low-frequency provider events are deferred).
        """
        del publisher
        self._started_at = self._utc_now_iso()
        try:
            while not stop_event.is_set():
                outcome = await self._run_session(stop_event)
                if outcome is None or stop_event.is_set():
                    self._set_state("stopped")
                    return
                if outcome is _TERMINAL:
                    return  # state already failed
                stopped = await self._wait_or_stop(stop_event, outcome)
                if stopped:
                    self._set_state("stopped")
                    return
        except asyncio.CancelledError:
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            self._set_state("stopped")
            raise

    def status(self) -> dict[str, Any]:
        """Compact redacted snapshot (no token/URI/query/frame content)."""
        status: dict[str, Any] = {
            "name": self._name,
            "state": self._state,
            "connect_attempts": self._connect_attempts,
            "reconnect_count": self._reconnect_count,
        }
        status.update(self._counters)
        status.update({
            "mode": self._mode,
            "configured_instruments": len(self._instrument_keys),
            "subscribed_instruments": (
                len(self._instrument_keys) if self._state == "streaming" else None
            ),
            "last_connected_at": self._connected_at,
            "last_message_at": self._last_message_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
            "started_at": self._started_at,
        })
        return status
