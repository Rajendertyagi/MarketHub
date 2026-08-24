"""
Server runtime: lifespan, background task supervisor, source manager, and typed app context.

This module is separate from server.py to keep the MCP server definition clean
and to make the lifecycle explicit.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from core.persistence import store as event_store_module

logger = logging.getLogger(__name__)

# Canonical retention prune cadence (F4). Single owner — do not scatter
# hardcoded intervals elsewhere. The loop prunes once immediately at startup
# (clears any restart backlog), then sleeps this long between runs.
RETENTION_PRUNE_INTERVAL_SECONDS = 24 * 3600.0


# ---------------------------------------------------------------------------
# Typed app context — passed through MCP lifespan
# ---------------------------------------------------------------------------

@dataclass
class AppContext:
    """Process-wide state available to tools and background sources."""

    store: event_store_module.EventStore
    # Set during lifespan startup; cleared during teardown.
    _background_tasks: dict[str, asyncio.Task[Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    _running: bool = field(default=False, init=False, repr=False)
    _source_manager: Any = field(default=None, init=False, repr=False)


# ---------------------------------------------------------------------------
# Background task supervisor
# ---------------------------------------------------------------------------

class BackgroundTaskManager:
    """Manages process-owned long-running coroutines.

    This is NOT MCP Tasks. It is a simple application-level registry for
    trusted background sources (future REST listeners, WebSocket bridges,
    periodic pollers, maintenance tasks, etc.).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    async def start(self, name: str, coro: Any) -> None:
        """Start a named background coroutine. No-op if already running."""
        async with self._lock:
            if name in self._tasks:
                logger.warning("background task '%s' already running", name)
                return
            task = asyncio.create_task(coro, name=name)
            self._tasks[name] = task
            logger.info("background task started: %s", name)

    async def cancel(self, name: str) -> None:
        """Cancel a named background task. Safe to call multiple times."""
        async with self._lock:
            task = self._tasks.get(name)
            if task is None:
                return
            task.cancel()
            del self._tasks[name]
            logger.info("background task cancelled: %s", name)

    async def cancel_and_wait(self, name: str) -> bool:
        """Cancel a named background task and AWAIT its completion.

        Unlike cancel(), this observes the task's actual termination, so any
        cleanup inside the task (websocket close, finally blocks) has fully
        run before this returns. Returns True if a task existed.

        Must NOT be called from inside the named task itself (awaiting own
        cancellation would deadlock); callers are HTTP handlers or lifecycle
        code running on separate tasks.
        """
        async with self._lock:
            task = self._tasks.pop(name, None)
        if task is None:
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # expected — cancellation delivered and observed
        except Exception as exc:
            # Task-specific errors were already logged by the task wrapper;
            # restart must not be blocked by them.
            logger.debug("background task '%s' raised during cancel: %s",
                         name, exc)
        logger.info("background task cancelled and awaited: %s", name)
        return True

    async def shutdown_all(self, timeout: float = 10.0) -> None:
        """Cancel all running tasks and wait bounded time for cleanup."""
        async with self._lock:
            names = list(self._tasks.keys())
            tasks = list(self._tasks.values())
            self._tasks.clear()

        if not tasks:
            return

        logger.info("shutting down %d background task(s)", len(tasks))
        for t in tasks:
            if not t.done():
                t.cancel()

        # Wait bounded time for each task to finish cleanup
        deadline = time.monotonic() + timeout
        for t in tasks:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(asyncio.shield(t), timeout=remaining)
            except (asyncio.CancelledError, asyncio.TimeoutError, StopAsyncIteration):
                pass
            except Exception as exc:
                logger.debug("background task cleanup exception: %s", exc)

    def status(self, name: str | None = None) -> dict[str, Any]:
        """Return status of one or all background tasks."""
        result: dict[str, Any] = {}
        targets = [name] if name else list(self._tasks.keys())
        for n in targets:
            task = self._tasks.get(n)
            if task is None:
                result[n] = {"status": "not_found"}
            else:
                result[n] = {
                    "status": "done" if task.done() else "running",
                    "cancelled": task.cancelled() if task.done() else False,
                    "exception": (
                        str(task.exception())
                        if task.done() and task.exception() is not None
                        else None
                    ),
                }
        return result

    @property
    def active_count(self) -> int:
        return len(self._tasks)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

async def _default_lifespan(
    server: Any,
) -> Any:
    """Default no-op lifespan when none is provided by the application."""
    yield {}


def make_lifespan(
    store: event_store_module.EventStore,
    bg_manager: BackgroundTaskManager | None = None,
    shutdown_timeout: float = 10.0,
    source_manager: Any = None,
    bus: Any = None,
    source_configs: dict[str, Any] | None = None,
    metrics: Any = None,
    retention_cfg: dict[str, Any] | None = None,
) -> Any:
    """
    Build a lifespan context manager for the MCP server.

    Returns a callable that the MCPServer accepts as its ``lifespan`` parameter.
    The yielded context is an ``AppContext`` instance.

    Args:
        store: The event store instance.
        bg_manager: Background task supervisor.
        shutdown_timeout: Seconds to wait for background tasks on shutdown.
        source_manager: Optional SourceManager for source connectors.
        bus: The subscription bus (needed by SourceManager for publisher).
        source_configs: The "sources" section from config.json.
        metrics: RuntimeMetrics instance for source metrics tracking.
        retention_cfg: The "retention" section from config.json
            (max_age_days / max_rows; both 0 = disabled → no task started).
    """
    bg = bg_manager or BackgroundTaskManager()
    ctx = AppContext(store=store)
    ctx._background_tasks = bg  # type: ignore[assignment]
    ctx._running = True
    ctx._source_manager = source_manager

    @asynccontextmanager
    async def lifespan(app: Any):  # noqa: ARG001
        logger.info("lifespan startup: store=%s", store.db_path)

        # Initialize and start sources (if any)
        if source_manager is not None:
            try:
                await source_manager.initialize(bg, store, bus, metrics=metrics)
                await source_manager.start_all(source_configs or {})
                logger.info("source manager: started sources")
            except Exception as exc:
                logger.error("source manager initialization failed: %s", exc)
                # Source failure must not prevent MCP server from starting

        # ── Consumer-safe retention pruning (F4) ─────────────────────────────
        # Started only when at least one limit is > 0. Prunes once immediately
        # (clears restart backlog), then every RETENTION_PRUNE_INTERVAL_SECONDS.
        _ret = retention_cfg or {}
        _ret_age = float(_ret.get("max_age_days", 0) or 0)
        _ret_rows = int(_ret.get("max_rows", 0) or 0)
        if _ret_age > 0 or _ret_rows > 0:
            async def _retention_loop() -> None:
                while True:
                    try:
                        result = await asyncio.to_thread(
                            store.prune, _ret_age, _ret_rows
                        )
                        if result.get("events_deleted") or result.get("state_deleted"):
                            logger.info(
                                "retention prune: %s event(s), %s state row(s) deleted",
                                result.get("events_deleted"),
                                result.get("state_deleted"),
                            )
                    except Exception:
                        logger.exception("retention prune failed")
                    await asyncio.sleep(RETENTION_PRUNE_INTERVAL_SECONDS)

            await bg.start("retention_prune", _retention_loop())
            logger.info(
                "retention task started (max_age_days=%s, max_rows=%s, "
                "interval_seconds=%s)",
                _ret_age, _ret_rows, RETENTION_PRUNE_INTERVAL_SECONDS,
            )

        try:
            yield ctx
        finally:
            # Signal sources to stop before background task shutdown
            if source_manager is not None:
                try:
                    await source_manager.shutdown()
                except Exception as exc:
                    logger.debug("source manager shutdown error: %s", exc)

            logger.info("lifespan teardown: stopping %d background task(s)", bg.active_count)
            ctx._running = False
            await bg.shutdown_all(timeout=shutdown_timeout)
            logger.info("lifespan teardown complete")

    return lifespan
