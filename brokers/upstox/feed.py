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
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from websockets.asyncio.client import connect as _websockets_connect

from brokers.upstox.feed_processing import process_binary_frame
from brokers.upstox.auth import UpstoxCredentials
from brokers.upstox.errors import (
    UpstoxAuthError,
    UpstoxConfigError,
    UpstoxRateLimitError,
    UpstoxRestError,
)

if TYPE_CHECKING:
    from brokers.upstox.rest import UpstoxRest

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
        instrument_metadata: Mapping[str, tuple[str, str]] | None = None,
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

        # -- canonical identity metadata ---------------------------------------
        # Every configured instrument_key MUST have complete metadata.
        # Missing/malformed metadata is a configuration error (fail fast).
        metadata: dict[str, tuple[str, str]] = {}
        if instrument_metadata:
            for mk, mv in instrument_metadata.items():
                if not isinstance(mk, str) or not mk.strip():
                    raise UpstoxConfigError(
                        f"instrument_metadata key must be a non-empty string; "
                        f"got {mk!r}"
                    )
                if not isinstance(mv, (tuple, list)) or len(mv) != 2:
                    raise UpstoxConfigError(
                        f"instrument_metadata[{mk!r}] must be a "
                        f"(exchange, tradingsymbol) tuple"
                    )
                ex, ts = mv
                if not isinstance(ex, str) or not ex.strip():
                    raise UpstoxConfigError(
                        f"instrument_metadata[{mk!r}].exchange must be a "
                        f"non-empty string"
                    )
                if not isinstance(ts, str) or not ts.strip():
                    raise UpstoxConfigError(
                        f"instrument_metadata[{mk!r}].tradingsymbol must be a "
                        f"non-empty string"
                    )
                metadata[mk.strip()] = (ex.strip(), ts.strip())

        for key in keys:
            if key not in metadata:
                raise UpstoxConfigError(
                    f"configured instrument_key {key!r} has no canonical "
                    f"metadata; every configured key requires exchange and "
                    f"tradingsymbol"
                )

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
        # Runtime subscription management (desired-set model).
        self._sub_lock = asyncio.Lock()
        self._live_ws: Any = None
        # MarketService is accepted now (stable constructor) but is
        # intentionally UNUSED until D3.2 wires market-data processing.
        self._market_service = market_service

        # Canonical identity metadata: {instrument_key: (exchange, tradingsymbol)}.
        # Frozen at construction so caller mutation cannot affect runtime.
        from types import MappingProxyType
        self._instrument_metadata: Mapping[str, tuple[str, str]] = (
            MappingProxyType(metadata)
        )

        # -- test seams ---------------------------------------------------------
        self._ws_connect: Callable[..., Any] = ws_connect or _default_ws_connect
        self._sleep: Callable[[float], Any] = sleep or asyncio.sleep
        self._random_jitter = random_jitter or random.uniform
        self._monotonic = monotonic or time.monotonic

        # -- state --------------------------------------------------------------
        self._state = "stopped"
        self._state_updated_at: str | None = None
        # Last terminal-ish exit reason for forensics (set by run()); one of:
        # "stop_requested" | "cancelled" | "auth_required" | "terminal: <safe>"
        self._last_exit_reason: str | None = None
        self._last_exit_at: str | None = None
        # Bounded, safe-only transition history (WP14). Each entry:
        # {"at": iso, "from": str, "to": str, "reason": str|None}
        self._transitions: deque[dict[str, Any]] = deque(maxlen=20)
        # Optional external listener for state changes (WP22). Composition root
        # wires this to the generic EventBroker. Never raised into the feed.
        self.on_state_change: Callable[[str, str, str, str, str | None], None] | None = (
            None
        )
        self._provider = "upstox"
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
        self._malformed_frames: int = 0
        self._subscribed_instruments: int | None = None
        self._last_error: str | None = None
        self._last_error_at: str | None = None
        self._segment_status: dict[str, str] | None = None

    # -- helpers --------------------------------------------------------------

    def _set_state(self, state: str, reason: str | None = None) -> None:
        if state != self._state:
            old = self._state
            self._state = state
            self._state_updated_at = self._utc_now_iso()
            logger.info(
                "upstox feed %s: state -> %s (reason=%s)",
                self._name,
                state,
                reason,
            )
            self._transitions.append(
                {
                    "at": self._state_updated_at,
                    "from": old,
                    "to": state,
                    "reason": reason,
                }
            )
            listener = self.on_state_change
            if listener is not None:
                try:
                    listener(self._name, self._provider, old, state, reason)
                except Exception:  # pragma: no cover - listener must never break feed
                    logger.debug(
                        "upstox feed %s: on_state_change listener raised",
                        self._name,
                        exc_info=True,
                    )

    def _note_exit(self, reason: str) -> None:
        """Record WHY the run() loop ended (forensics; surfaced via status)."""
        self._last_exit_reason = reason
        self._last_exit_at = self._utc_now_iso()
        # The last-stop reason must survive the console window too.
        logger.info("upstox feed %s: run ended (%s)", self._name, reason)

    async def _close_live_ws(self) -> None:
        """Close and clear the live socket if one is held.

        Used on the cancellation path: cancelling the recv wait does NOT close
        the websocket, so Stop/Restart must do it explicitly and deterministically.
        """
        async with self._sub_lock:
            ws = self._live_ws
            self._live_ws = None
        if ws is not None:
            await self._close_quietly(ws)

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _note_error(self, summary: str) -> None:
        self._last_error = summary
        self._last_error_at = self._utc_now_iso()
        logger.warning("upstox feed %s: %s", self._name, summary)

    def _fail(self, summary: str) -> None:
        self._note_error(summary)
        self._set_state("failed", reason="non_retryable_failure")
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

    # -- runtime subscription management (desired-set model) -------------------

    def _mutation_frame(self, method: str, keys: list[str]) -> bytes:
        """Build a sub/unsub frame for a subset of keys."""
        payload = {
            "guid": uuid.uuid4().hex,
            "method": method,
            "data": {
                "mode": self._mode,
                "instrumentKeys": keys,
            },
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")

    async def _send_mutation(self, frame: bytes) -> bool:
        """Send one frame on the live socket under the mutation lock.

        Returns True when sent; False when no live socket exists (the
        desired set is already updated, so the next reconnect picks it up).
        """
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
                logger.debug("upstox feed %s: sub-mutation send failed: %s",
                             self._name, type(exc).__name__)
                return False

    async def add_instruments(self, keys: list[str]) -> int:
        """Add instrument keys to the desired subscription set.

        When the feed is streaming, a subscribe frame for ONLY the new
        keys is sent on the live socket. Otherwise the desired set simply
        grows and the next authorize/resubscribe cycle picks it up.
        Returns the number of genuinely new keys.
        """
        async with self._sub_lock:
            existing = set(self._instrument_keys)
            fresh = [k for k in keys if k and k not in existing]
            if fresh:
                self._instrument_keys = tuple(
                    sorted(existing | set(fresh)))
        if fresh and self._state == "streaming":
            await self._send_mutation(self._mutation_frame("sub", fresh))
        return len(fresh)

    async def remove_instruments(self, keys: list[str]) -> int:
        """Remove keys from the desired set; unsub on the live socket.

        Never removes the last key (an empty subscription is invalid for
        the feed loop). Returns the number of removed keys.
        """
        async with self._sub_lock:
            existing = set(self._instrument_keys)
            gone = [k for k in keys if k in existing]
            remaining = existing - set(gone)
            if not remaining:
                return 0   # keep at least one key subscribed
            self._instrument_keys = tuple(sorted(remaining))
        if gone and self._state == "streaming":
            await self._send_mutation(
                self._mutation_frame("unsub", gone))
        return len(gone)

    @property
    def desired_instrument_count(self) -> int:
        return len(self._instrument_keys)

    # -- daily-auth readiness (startup gate) ----------------------------------

    _PLACEHOLDER_TOKENS = frozenset({"PENDING-OAUTH-LOGIN"})

    def is_ready_to_start(self) -> bool:
        """True when a USABLE daily access token exists.

        Reuses frozen D2 credential semantics:
          * placeholder/missing token            -> not ready
          * known-expired token                  -> not ready
          * unknown-expiry token                 -> ready until broker
                                                   proves otherwise
        """
        token = self._credentials.access_token
        if not token or token in self._PLACEHOLDER_TOKENS:
            return False
        expires_at = self._credentials.expires_at
        if expires_at is not None:
            from datetime import datetime, timezone
            if expires_at <= datetime.now(timezone.utc):
                return False
        return True

    def requires_daily_auth(self) -> bool:
        return not self.is_ready_to_start()

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
        self._set_state("authorizing", reason="session_start")
        self._connect_attempts += 1
        try:
            uri = await self._rest.authorize_market_feed(self._credentials)
        except UpstoxAuthError as exc:
            # Broker rejected the current access token (e.g. expired or
            # revoked). This is an AUTHENTICATION-REQUIRED state, not a
            # broken feed: stop cleanly, no retry loop, wait for OAuth.
            self._set_state("auth_required", reason="broker_rejected_token")
            logger.warning(
                "upstox feed %s: authentication required - broker rejected "
                "current access token (%s)", self._name,
                type(exc).__name__)
            return None
        except UpstoxRateLimitError as exc:
            self._set_state("reconnecting", reason="rate_limited")
            hint = exc.retry_after_seconds
            logger.warning("upstox feed %s: rate limited during authorize "
                           "(retry_after=%s)", self._name, hint)
            return self._next_backoff(hint)
        except UpstoxRestError as exc:
            if not exc.retryable:
                self._fail(f"authorization failed: {exc}")
                return _TERMINAL
            self._set_state("reconnecting", reason="retryable_authorize_failure")
            logger.warning("upstox feed %s: retryable authorize failure - %s",
                           self._name, exc)
            return self._next_backoff()
        if stop_event.is_set():
            return None

        self._set_state("connecting", reason="ws_connect")
        try:
            ws = await self._ws_connect(uri, **_WS_SETTINGS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._note_error(_safe_ws_summary(exc))
            self._set_state("reconnecting", reason="connect_failed")
            logger.warning("upstox feed %s: websocket connect failed - %s",
                           self._name, self._last_error)
            return self._next_backoff()

        self._connected_at = self._utc_now_iso()
        self._live_ws = ws
        connection_started_mono = self._monotonic()
        try:
            try:
                await ws.send(self._subscription_frame())
            except Exception as exc:
                self._note_error(_safe_ws_summary(exc))
                await self._close_quietly(ws)
                self._set_state("reconnecting", reason="subscribe_send_failed")
                logger.warning("upstox feed %s: subscription send failed - %s",
                               self._name, self._last_error)
                return self._next_backoff()

            self._set_state("streaming", reason="connected")
            reason = await self._recv_loop(ws, stop_event)
        except asyncio.CancelledError:
            # Close the LOCAL socket here: the finally below clears
            # ``_live_ws`` while unwinding, BEFORE run()'s cancellation
            # handler could read it — cancellation must never leak the socket.
            await self._close_quietly(ws)
            raise
        except Exception:
            # Unexpected internal error escaping the session: still close.
            await self._close_quietly(ws)
            raise
        finally:
            # The live-socket reference must never outlive the session, even
            # if an unexpected error escapes the recv loop.
            async with self._sub_lock:
                self._live_ws = None

        lifetime = self._monotonic() - connection_started_mono
        self._reset_backoff_if_stable(lifetime)
        await self._close_quietly(ws)
        if reason == "stopped" or stop_event.is_set():
            if self._state != "auth_required":
                # A clean stop (operator Stop / shutdown) reaches here with
                # _last_exit_reason still None — record the canonical reason so
                # the transition history is never empty (WP14/WP7).
                self._set_state(
                    "stopped", reason=self._last_exit_reason or "stop_requested")
            return None
        self._reconnect_count += 1
        self._set_state("reconnecting", reason="websocket_closed")
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
                await self._process_frame(message)
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

    async def _process_frame(self, message: Any) -> None:
        """D3.2 market-data processing seam.

        Counts the frame, then (for binary frames with a MarketService)
        decodes/normalizes/applies via feed_processing. D3.1 callers that
        pass ``market_service=None`` get counting-only behaviour.
        """
        self._count_frame(message)

        if not isinstance(message, bytes) or not message:
            return
        if self._market_service is None:
            return

        received_ts = datetime.now(timezone.utc)
        try:
            result = process_binary_frame(
                message, received_ts=received_ts,
                instrument_metadata=self._instrument_metadata,
            )
        except Exception as exc:
            from brokers.upstox.feed_protocol import ProtobufDecodeError
            if isinstance(exc, ProtobufDecodeError):
                self._counters.setdefault("decode_errors", 0)
                self._counters["decode_errors"] += 1
                logger.debug("upstox feed %s: decode error - %s",
                             self._name, exc)
                return
            # Unexpected internal bug — surface it.
            raise

        if result.segment_status is not None:
            self._segment_status = result.segment_status

        for outcome in result.instruments:
            if outcome.error is not None:
                key = "normalization_errors"
                self._counters[key] = self._counters.get(key, 0) + 1
                continue
            # Fault isolation: one bad apply (e.g. a MarketService internal
            # error) must never kill the recv loop — count it and continue.
            try:
                if outcome.patch is not None:
                    mo = await self._market_service.apply_quote(outcome.patch)
                    if mo.accepted and mo.changed:
                        key = "quote_updates"
                        self._counters[key] = self._counters.get(key, 0) + 1
                    if mo.stale:
                        key = "quote_stale"
                        self._counters[key] = self._counters.get(key, 0) + 1
                if outcome.depth is not None:
                    do = await self._market_service.apply_depth(outcome.depth)
                    if do.accepted:
                        key = "depth_updates"
                        self._counters[key] = self._counters.get(key, 0) + 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                key = "apply_errors"
                self._counters[key] = self._counters.get(key, 0) + 1
                logger.warning(
                    "upstox feed %s: quote/depth application failed (%s) - "
                    "isolated", self._name, type(exc).__name__)

    # -- public API ------------------------------------------------------------

    def update_credentials(self, credentials: UpstoxCredentials) -> None:
        """Replace credentials for the next authorization attempt.

        Safe to call while the feed is running: the next authorize cycle
        picks up the new credentials atomically. Also clears the terminal
        failure state so the feed can retry.
        """
        self._credentials = credentials
        if self._state in ("failed", "auth_required"):
            self._set_state("stopped", reason="credentials_updated")

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
                # FORENSICS: every loop exit is logged with its trigger so
                # an unexpected streaming->stopped can never be silent.
                logger.info(
                    "upstox feed %s: session ended (outcome=%s, "
                    "stop_event=%s, state=%s)", self._name,
                    "None" if outcome is None else
                    ("TERMINAL" if outcome is _TERMINAL else
                     f"retry:{outcome:.1f}s"),
                    stop_event.is_set(), self._state)
                if outcome is None or stop_event.is_set():
                    if self._state != "auth_required":
                        self._note_exit("stop_requested")
                        self._set_state("stopped", reason="stop_requested")
                    else:
                        # Stay in auth_required — do NOT flip to stopped. The
                        # daily-login dimension is distinct from an operator
                        # stop; the UI keys off this state to show Login.
                        self._note_exit("auth_required")
                    return
                if outcome is _TERMINAL:
                    # Safe summary only — never provider bodies or URIs.
                    self._note_exit(
                        f"terminal: {self._last_error or 'unknown failure'}")
                    return  # state already failed
                stopped = await self._wait_or_stop(stop_event, outcome)
                if stopped:
                    self._note_exit("stop_requested")
                    self._set_state("stopped", reason="stop_requested")
                    return
        except asyncio.CancelledError:
            # Cancellation does NOT close a websocket by itself. Close the
            # REAL live socket here (an earlier revision closed ``self._ws``,
            # which was always None — a silent leak on Stop/Restart). The
            # subscription lock cannot deadlock: upstream context managers
            # released it while the CancelledError unwound.
            await self._close_live_ws()
            self._note_exit("cancelled")
            self._set_state("stopped", reason="cancelled")
            raise

    def status(self) -> dict[str, Any]:
        """Compact redacted snapshot (no token/URI/query/frame content)."""
        status: dict[str, Any] = {
            "name": self._name,
            "provider": "upstox",
            "state": self._state,
            "state_updated_at": self._state_updated_at,
            "connect_attempts": self._connect_attempts,
            "reconnect_count": self._reconnect_count,
        }
        status.update(self._counters)
        status.update({
            "mode": self._mode,
            "auth_required": self._state == "auth_required",
            "malformed_frames": self._malformed_frames,
            "configured_instruments": len(self._instrument_keys),
            "subscribed_instruments": (
                len(self._instrument_keys) if self._state == "streaming" else None
            ),
            "last_connected_at": self._connected_at,
            "last_message_at": self._last_message_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
            "last_exit_reason": self._last_exit_reason,
            "last_exit_at": self._last_exit_at,
            "started_at": self._started_at,
            "not_ready_reason": self.readiness_reason(),
            "recent_transitions": list(self._transitions),
        })
        return status

    def readiness_reason(self) -> str | None:
        """Why this feed cannot start right now, or None if ready (WP2).

        Distinct from ``auth_required`` (daily login): this reports the
        APP-CREDENTIALS dimension (missing/expired access token). The daily
        login dimension is surfaced by ``auth_required`` in status().
        """
        token = self._credentials.access_token
        if not token or not str(token).strip():
            return "missing_token"
        if str(token).strip().startswith("PENDING"):
            return "token_pending"
        return None

    @property
    def rest(self) -> UpstoxRest:
        """Public read access to the REST transport (composition-root use)."""
        return self._rest

    @property
    def credentials_snapshot(self) -> UpstoxCredentials | None:
        """Current credentials reference (redaction-safe object)."""
        return self._credentials





