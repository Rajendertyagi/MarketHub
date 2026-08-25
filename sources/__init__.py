"""
Source connector framework for the MCP Event Server.

A source knows ONLY how to get external data and call publish_event().
It does NOT know about MCP transport, SubscriptionBus, ResourceUpdated,
SQLite internals, consumer checkpoints, replay, acknowledgement, or routing.

Usage:
    A source implements the EventSource protocol (or any object with
    async def run(self, publisher, stop_event)). The SourceManager
    starts enabled sources as named background tasks via the existing
    BackgroundTaskManager.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Publisher — the only way a source publishes events (and records dedup)
# ---------------------------------------------------------------------------

class PublishError(Exception):
    """Raised when publish_event() fails (e.g. storage error)."""
    pass


class SourceConfigError(Exception):
    """Raised when source configuration is invalid (unknown type, bad shape)."""
    pass


class Publisher:
    """
    The single output port a source uses to talk to the event server.

    A source calls ``publisher(...)`` to publish an event and uses
    ``publisher.is_seen(...)`` / ``publisher.mark_seen(...)`` for durable,
    restart-safe deduplication.  The source never touches the store, bus, or
    SQLite directly.
    """

    def __init__(self, store: Any, bus: Any, metrics: Any = None) -> None:
        self._store = store
        self._bus = bus
        self._metrics = metrics

    async def __call__(
        self,
        event_type: str,
        source: str,
        data: dict[str, Any],
        persistent: bool = False,
        routing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from core import events  # deferred to avoid circular import at module load
        try:
            result = await events.publish_event(
                event_type=event_type,
                source=source,
                data=data,
                persistent=persistent,
                routing=routing,
                store=self._store,
                bus=self._bus,
            )
            # Count source-originated success separately (distinct dimension from
            # events.published_total — not double-counting the same metric).
            if self._metrics is not None:
                try:
                    self._metrics.record_source_published()
                except Exception:
                    pass
            return result
        except Exception as exc:
            if self._metrics is not None:
                try:
                    self._metrics.record_source_failure()
                except Exception:
                    pass
            raise PublishError(
                f"publish_event failed for source={source!r} type={event_type!r}: {exc}"
            ) from exc

    async def is_seen(self, source_name: str, external_id: str) -> bool:
        """Return True if this external ID was already durably published."""
        return await asyncio.to_thread(
            self._store.source_item_seen, source_name, external_id
        )

    async def mark_seen(
        self,
        source_name: str,
        external_id: str,
        max_items: int | None = None,
    ) -> None:
        """
        Record an external ID as durably seen (after successful publication).

        If ``max_items`` is provided, oldest seen IDs beyond the limit are pruned.
        """
        seen_at = datetime.now(timezone.utc).isoformat()
        await asyncio.to_thread(
            self._store.mark_source_item_seen, source_name, external_id, seen_at
        )
        if max_items:
            await asyncio.to_thread(
                self._store.prune_source_seen_items, source_name, max_items
            )

    async def get_cursor(self, source_name: str) -> str | None:
        """
        Read the durable cursor (ingestion high-water mark) for a source.

        The cursor is stored under the ``"cursor"`` key in the generic
        ``source_state`` table.  Returns None if no cursor has been persisted.
        """
        return await asyncio.to_thread(
            self._store.get_source_state, source_name, "cursor"
        )

    async def set_cursor(self, source_name: str, value: str) -> None:
        """
        Persist the durable cursor (ingestion high-water mark) for a source.

        Stored under the ``"cursor"`` key in the generic ``source_state`` table.
        """
        await asyncio.to_thread(
            self._store.set_source_state, source_name, "cursor", value
        )


def create_publisher(store: Any, bus: Any, metrics: Any = None) -> Publisher:
    """Build a Publisher for the given store/bus."""
    return Publisher(store, bus, metrics)


# ---------------------------------------------------------------------------
# EventSource protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class EventSource(Protocol):
    """Minimal protocol for an event source connector."""

    @property
    def name(self) -> str:
        """Stable internal name, e.g. 'http_poller'."""
        ...

    async def run(self, publisher: Any, stop_event: asyncio.Event) -> None:
        """
        Main loop.  Must exit cleanly when stop_event is set.

        Args:
            publisher: async callable from create_publisher().
            stop_event: set by SourceManager on shutdown.
        """
        ...

    def status(self) -> dict[str, Any]:
        """Return current source status for health reporting."""
        ...


# ---------------------------------------------------------------------------
# SourceManager — manages enabled sources as background tasks
# ---------------------------------------------------------------------------

@dataclass
class SourceManager:
    """
    Registry of enabled source connectors.

    Integrates with the existing BackgroundTaskManager — each source runs
    as a named background task with prefix 'source:'.
    """

    _sources: dict[str, Any] = field(default_factory=dict)
    _bg_manager: Any = None  # BackgroundTaskManager
    _store: Any = None
    _bus: Any = None
    _publisher: Any = None
    # Per-source stop events so a single source can be stopped independently
    # without affecting the others. shutdown() sets all of them.
    _stop_events: dict[str, asyncio.Event] = field(default_factory=dict)
    # Config each source was last started with — enables restart_source()
    # to relaunch the SAME source identity with the SAME configuration.
    _configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Forensics: how each source's last background task ended (reason/at/
    # runtime). Written by the task wrapper; surfaced through get_status().
    _exit_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Why SourceManager itself signalled a stop (WP4/15): "operator_stop"
    # (explicit Stop Feed), "restart" (Restart Feed), or "application_shutdown"
    # (server teardown). Lets get_status() report a precise stop_reason that
    # the feed's own last_exit_reason cannot know.
    _stop_intents: dict[str, str] = field(default_factory=dict)
    # Sources the operator explicitly stopped in this runtime (WP20/CASE E):
    # start_all() must NOT auto-restart them — they stay stopped until the
    # operator starts them again. Cleared on start_source/restart_source.
    _operator_stopped: set[str] = field(default_factory=set)

    async def initialize(
        self,
        bg_manager: Any,
        store: Any,
        bus: Any,
        metrics: Any = None,
    ) -> None:
        """Set references.  Must be called before start_all()."""
        self._bg_manager = bg_manager
        self._store = store
        self._bus = bus
        self._publisher = create_publisher(store, bus, metrics)

    def register(self, source: Any) -> None:
        """Register a source instance.  Must be called before start_all()."""
        name = source.name
        if name in self._sources:
            logger.warning("source '%s' already registered — overwriting", name)
        self._sources[name] = source
        if name not in self._stop_events:
            self._stop_events[name] = asyncio.Event()

    async def start_all(self, configs: dict[str, Any]) -> None:
        """
        Start all enabled sources.

        configs: the "sources" section from config.json, e.g.
            {"http_poller": {"enabled": true, ...}, "test_source": {"enabled": false}}
        """
        for name, source in self._sources.items():
            src_cfg = configs.get(name, {})
            if not src_cfg.get("enabled", False):
                logger.info("source '%s' disabled — skipping", name)
                continue
            # Config is ALWAYS recorded (even when start is gated) so a
            # later restart_source() can launch it once prerequisites
            # (e.g. daily authentication) are satisfied.
            self._configs[name] = src_cfg
            # Operator-stopped sources stay stopped until explicitly started
            # again (WP20/CASE E): an explicit Stop Feed must never be undone
            # by a later start_all() (e.g. at server restart within the same
            # runtime, or a re-entrant start_all).
            if name in self._operator_stopped:
                logger.info(
                    "source '%s' was operator-stopped - leaving stopped "
                    "(start it explicitly to resume)", name)
                continue
            # Generic readiness gate: a source may declare prerequisites
            # via is_ready_to_start() (e.g. Upstox waiting for the daily
            # access token). Gating is per-source and never blocks others.
            ready_check = getattr(source, "is_ready_to_start", None)
            if callable(ready_check) and not ready_check():
                logger.info(
                    "source '%s' waiting for prerequisites - not starting "
                    "(will start via restart when ready)", name)
                continue
            await self._start_one(name, source, src_cfg)

    async def _start_one(self, name: str, source: Any, cfg: dict[str, Any]) -> None:
        """Start a single source as a background task."""
        task_name = f"source:{name}"
        stop_event = self._stop_events.setdefault(name, asyncio.Event())

        async def _wrapper():
            logger.info("source '%s' starting", name)
            started_mono = time.monotonic()
            try:
                await source.run(self._publisher, stop_event)
            except asyncio.CancelledError:
                self._exit_info[name] = {
                    "reason": "cancelled",
                    "at": _utc_now_iso(),
                    "ran_for_s": round(time.monotonic() - started_mono, 1),
                }
                logger.info("source '%s' cancelled", name)
                raise
            except Exception as exc:
                # Exception TYPE only — str(exc) may carry provider material.
                self._exit_info[name] = {
                    "reason": f"error: {type(exc).__name__}",
                    "at": _utc_now_iso(),
                    "ran_for_s": round(time.monotonic() - started_mono, 1),
                }
                logger.error("source '%s' failed: %s", name, exc)
            else:
                self._exit_info[name] = {
                    "reason": "exited",
                    "at": _utc_now_iso(),
                    "ran_for_s": round(time.monotonic() - started_mono, 1),
                }
                logger.info("source '%s' exited normally", name)

        try:
            await self._bg_manager.start(task_name, _wrapper())
        except Exception as exc:
            logger.error("failed to start source '%s': %s", name, exc)

    async def shutdown(self) -> None:
        """Signal ALL sources to stop (server teardown)."""
        for name in self._sources:
            self._stop_intents[name] = "application_shutdown"
        for ev in self._stop_events.values():
            ev.set()
        logger.info("source manager: stop signal sent to %d source(s)", len(self._sources))

    async def stop_source(self, name: str) -> bool:
        """
        Stop a single named source cleanly and independently of other sources.

        Signals the source's dedicated stop event, then CANCELS AND AWAITS its
        background task via BackgroundTaskManager.cancel_and_wait — when this
        returns, the task is gone and its cleanup (websocket close, finally
        blocks) has fully run. Credentials, desired instruments, watchlists
        and configuration are untouched (they live on the source instance).

        Other registered sources are unaffected.

        Returns True if the named source was known (and thus stopped),
        False otherwise.
        """
        if name not in self._sources:
            logger.warning("stop_source: unknown source '%s'", name)
            return False

        stop_event = self._stop_events.get(name)
        if stop_event is not None:
            stop_event.set()
        self._stop_intents[name] = "operator_stop"
        self._operator_stopped.add(name)

        # Brief cooperative window: let the run loop honor the stop event so
        # its exit reason reads "stop_requested" (not "cancelled"). Sources
        # that ignore their event are force-cancelled right after.
        deadline = time.monotonic() + 0.5
        while self.task_running(name) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

        if self._bg_manager is not None:
            try:
                # Await termination so "Feed State = stopped" is TRUE by the
                # time the caller observes the result.
                await self._bg_manager.cancel_and_wait(f"source:{name}")
            except Exception as exc:
                logger.debug("stop_source: cancel task for '%s' raised: %s", name, exc)

        logger.info("source '%s' stopped", name)
        return True

    def task_running(self, name: str) -> bool:
        """True when the named source currently has a running background task."""
        if self._bg_manager is None:
            return False
        task_name = f"source:{name}"
        status = self._bg_manager.status(task_name).get(task_name, {})
        return status.get("status") == "running"

    def is_ready(self, name: str) -> bool | None:
        """Source-declared readiness gate (e.g. daily authentication).

        Returns None when the source declares no gate — the ABSENCE of a gate
        must never be read as "not ready".
        """
        source = self._sources.get(name)
        if source is None:
            return None
        ready_check = getattr(source, "is_ready_to_start", None)
        if not callable(ready_check):
            return None
        return bool(ready_check())

    def readiness_reason(self, name: str) -> str | None:
        """Why a source cannot start right now, or None if ready (WP2).

        Delegates to the source's own ``readiness_reason()`` when present;
        returns None for sources that declare no gate.
        """
        source = self._sources.get(name)
        if source is None:
            return None
        reason_check = getattr(source, "readiness_reason", None)
        if not callable(reason_check):
            return None
        result = reason_check()
        return result if isinstance(result, str) else None

    async def start_source(self, name: str) -> str:
        """Start a registered-but-not-running source through the lifecycle.

        Returns one of:
          "started"          new background task created
          "already_running"  task exists; no duplicate created
          "not_ready"        source declares prerequisites unmet
                             (e.g. daily authentication) via
                             is_ready_to_start() == False
          "unknown"          source not registered / never configured

        Readiness is decided by the SOURCE itself, never by SourceManager.
        """
        source = self._sources.get(name)
        cfg = self._configs.get(name)
        if source is None or cfg is None:
            return "unknown"

        ready_check = getattr(source, "is_ready_to_start", None)
        if callable(ready_check) and not ready_check():
            logger.info(
                "source '%s' start refused: prerequisites unmet "
                "(daily authentication required)", name)
            return "not_ready"

        task_name = f"source:{name}"
        if self._bg_manager is not None:
            status = self._bg_manager.status(task_name).get(task_name, {})
            if status.get("status") == "running":
                return "already_running"

        # Fresh stop event: a previously-set event must not kill the run.
        self._stop_events[name] = asyncio.Event()
        self._operator_stopped.discard(name)
        await self._start_one(name, source, cfg)
        return "started"

    async def restart_source(self, name: str) -> bool:
        """
        Restart a single named source through the normal lifecycle.

        Stops the current background task and AWAITS its completion (via
        BackgroundTaskManager.cancel_and_wait — websocket close and finally
        blocks fully run), then starts the SAME source instance with the
        SAME configuration via _start_one(). The source's next run performs
        a fresh authorize/connect cycle (e.g. after credential rotation).

        Invariants:
          * same SourceManager, same source instance, same config identity
          * exactly one background task per source (old task is awaited gone
            before the new one starts; manager refuses duplicate names)
          * no second lifecycle system — reuses existing primitives

        Must not be called from inside the named source's own task.

        Returns True if the source was known and (re)started, False otherwise.
        """
        source = self._sources.get(name)
        cfg = self._configs.get(name)
        if source is None or cfg is None:
            logger.warning("restart_source: unknown/unstarted source '%s'", name)
            return False

        # 1. Signal graceful exit at the run loop's next stop-event check.
        stop_event = self._stop_events.get(name)
        if stop_event is not None:
            stop_event.set()
        self._stop_intents[name] = "restart"
        self._operator_stopped.discard(name)

        # 2. Cancel AND observe completion of the old task — its cleanup
        #    (websocket close, finally blocks) is guaranteed done after this.
        if self._bg_manager is not None:
            await self._bg_manager.cancel_and_wait(f"source:{name}")

        # 3. Fresh stop event — the old one is set and must not leak into
        #    the new run (a set event would stop the new run immediately).
        self._stop_events[name] = asyncio.Event()

        # 4. Start the same source with the same config.
        await self._start_one(name, source, cfg)
        logger.info("source '%s' restarted", name)
        return True

    def get_status(self) -> dict[str, Any]:
        """Status of all registered sources, merged with task liveness.

        Adds the derived fields the control API/UI need to distinguish a
        genuinely running feed from a DEAD TASK WITH STALE STATE:
          * task_running  — background task exists and is not done
          * reconnecting  — source reports reconnecting AND task is alive
          * last_task_exit — how the previous run ended (reason/at/runtime)
        """
        result: dict[str, Any] = {}
        for name, source in self._sources.items():
            task_name = f"source:{name}"
            task_info = self._bg_manager.status(task_name) if self._bg_manager else {}
            task_status = task_info.get(task_name, {})
            src_status = dict(source.status())
            src_status["task"] = task_status
            running = task_status.get("status") == "running"
            src_status["task_running"] = running
            src_status["reconnecting"] = (
                src_status.get("state") == "reconnecting" and running
            )
            exit_info = self._exit_info.get(name)
            if exit_info is not None:
                src_status["last_task_exit"] = dict(exit_info)
                # Sources without their own exit tracking still surface why
                # their last run ended (wrapper-level view).
                src_status.setdefault("last_exit_reason", exit_info.get("reason"))

            # Precise stop reason (WP4/15): prefer the feed's own terminal
            # reasons (auth_required / terminal), then the manager's recorded
            # intent (operator_stop / restart / application_shutdown), else
            # the feed's generic last_exit_reason.
            feed_exit = src_status.get("last_exit_reason")
            state = src_status.get("state")
            if state == "auth_required":
                stop_reason = "auth_required"
            elif isinstance(feed_exit, str) and feed_exit.startswith("terminal"):
                stop_reason = feed_exit
            elif isinstance(feed_exit, str) and feed_exit.startswith("error:"):
                stop_reason = "internal_error"
            elif not running:
                stop_reason = self._stop_intents.get(name) or feed_exit
            else:
                stop_reason = None
            src_status["stop_reason"] = stop_reason
            result[name] = src_status
        return result

    @property
    def enabled_sources(self) -> dict[str, Any]:
        return dict(self._sources)


# ---------------------------------------------------------------------------
# Static source registry + config-driven builder (Nuitka-safe, no discovery)
# ---------------------------------------------------------------------------

from sources.registry import SOURCE_TYPES  # noqa: E402  (bottom import avoids cycles)


def build_source_manager(
    sources_cfg: dict[str, Any] | None,
    *,
    market_service: Any = None,
) -> SourceManager:
    """
    Build a SourceManager from the ``"sources"`` section of config.json.

    This is the ONLY place that maps a config ``"type"`` string to a concrete
    source class.  server.py calls this and knows nothing about individual
    source implementations.

    Args:
        sources_cfg: the "sources" dict, e.g.
            {"market_feed": {"type": "http_poller", "enabled": true, ...}}
        market_service: optional shared MarketService injected into each
            source's config dict for sources that need it (e.g. UpstoxFeed).

    Returns:
        A SourceManager with all valid sources registered.  An empty config
        yields an empty (but valid) manager.

    Raises:
        SourceConfigError: on an unknown source type or malformed config entry.
    """
    manager = SourceManager()
    if not sources_cfg:
        return manager

    if not isinstance(sources_cfg, dict):
        raise SourceConfigError("'sources' must be a JSON object of named sources")

    for name, cfg in sources_cfg.items():
        if not isinstance(cfg, dict):
            raise SourceConfigError(
                f"source '{name}' config must be a JSON object, got "
                f"{type(cfg).__name__}"
            )

        src_type = cfg.get("type", name)
        cls = SOURCE_TYPES.get(src_type)
        if cls is None:
            raise SourceConfigError(
                f"unknown source type '{src_type}' for source '{name}'; "
                f"known types: {sorted(SOURCE_TYPES)}"
            )

        # Bind the runtime instance name (separate from the implementation type).
        instance_cfg = dict(cfg)
        instance_cfg["source_name"] = name
        try:
            # Factories whose signature accepts market_service receive the
            # shared application instance; others get config only.
            if market_service is not None:
                import inspect
                sig = inspect.signature(cls)
                if "market_service" in sig.parameters:
                    source = cls(instance_cfg, market_service=market_service)
                else:
                    source = cls(instance_cfg)
            else:
                source = cls(instance_cfg)
            manager.register(source)
        except SourceConfigError:
            raise
        except Exception as exc:
            raise SourceConfigError(
                f"failed to construct source '{name}' (type={src_type}): {exc}"
            ) from exc

    return manager
