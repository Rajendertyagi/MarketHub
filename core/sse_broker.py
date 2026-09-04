"""
In-memory SSE (Server-Sent Events) broadcast broker.

Provides a single canonical fan-out point so that every canonical
event published through ``events.publish_event()`` is also pushed to
all connected SSE subscribers on ``GET /events/stream``.

Design:
  - One process-wide ``EventBroker`` instance.
  - Each subscriber owns a bounded ``asyncio.Queue`` (default 256).
  - Broadcast is fire-and-forget: if a subscriber's queue is full the
    event is dropped for that subscriber only — the canonical publish
    path must never block on SSE delivery.
  - Disconnect cleanup is automatic via the ``subscribe()`` context
    manager which unregisters the subscriber on exit.

Not responsible for:
  - persistence, replay, or ACK semantics (those belong to EventStore).
  - MCP ResourceUpdated notifications (those go through the SubscriptionBus).
  - Alert evaluation or metrics recording.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Default maximum number of events buffered per subscriber before drop.
_DEFAULT_QUEUE_SIZE = 256


class EventBroker:
    """Process-wide SSE event broadcaster."""

    def __init__(self, queue_size: int = _DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        # {subscriber_id: asyncio.Queue[str]}
        self._subscribers: dict[int, asyncio.Queue[str]] = {}
        self._lock = asyncio.Lock()
        # Guards every _subscribers mutation/snapshot so broadcast() is
        # safe to call from ANY thread (e.g. logging threads).  Lock
        # ordering is always: asyncio _lock -> _sync_lock, never reverse.
        self._sync_lock = threading.Lock()
        self._next_id = 0

    # ─── Public API ──────────────────────────────────────────────────────────

    def broadcast(self, event_line: str) -> None:
        """
        Push a pre-encoded SSE data line to all active subscribers.

        Thread-safe: may be called from any thread (logging emission
        paths included).  Fire-and-forget: if a subscriber's queue is
        full the event is dropped for that subscriber only.  The
        canonical publish path must never block on SSE delivery.
        """
        with self._sync_lock:
            if not self._subscribers:
                return
            items = list(self._subscribers.items())
        dead: list[int] = []
        for sid, q in items:
            try:
                q.put_nowait(event_line)
            except asyncio.QueueFull:
                logger.debug("SSE subscriber %d queue full — dropping event", sid)
                dead.append(sid)
        if dead:
            with self._sync_lock:
                for sid in dead:
                    self._subscribers.pop(sid, None)

    @property
    def subscriber_count(self) -> int:
        """Number of currently connected SSE subscribers."""
        with self._sync_lock:
            return len(self._subscribers)

    # ─── Subscriber lifecycle ────────────────────────────────────────────────

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[str]]:
        """
        Register a new SSE subscriber and yield an async iterator of
        event lines.

        Usage::

            async with broker.subscribe() as events:
                async for line in events:
                    yield line

        On exit (client disconnect or cancellation) the subscriber is
        unregistered and its queue is drained.
        """
        async with self._lock:
            sid = self._next_id
            self._next_id += 1
            q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
            with self._sync_lock:
                self._subscribers[sid] = q

        try:
            yield self._reader(sid, q)
        finally:
            async with self._lock:
                with self._sync_lock:
                    self._subscribers.pop(sid, None)

    # ─── Internal ────────────────────────────────────────────────────────────

    async def _reader(
        self, sid: int, q: asyncio.Queue[str]
    ) -> AsyncIterator[str]:
        """Yield queued event lines until the queue is closed."""
        while True:
            try:
                line = await q.get()
                yield line
            except (asyncio.CancelledError, GeneratorExit):
                break
