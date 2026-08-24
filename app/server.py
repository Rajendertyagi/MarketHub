"""
EventHub — Generic self-hosted MCP event server (application composition).

Canonical entrypoint:  python -m app.server

This module owns ONLY application composition:
  - top-level Starlette app (MCP routes + /health + /events/stream)
  - Uvicorn configuration/startup
  - wiring of core services, MCP surface, and sources

Generic event/alert/runtime/SSE/persistence logic lives under core/.
MCP contract/tools/resources live under mcp_server/.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mcp.server.mcpserver import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.server.transport_security import TransportSecuritySettings

from app.config import ConfigError, load_config, validate_config
from app.lifecycle import print_banner, print_shutdown
from app.paths import CONFIG_PATH, PROJECT_ROOT
from app import __version__
from core import events
from core import runtime
from core.alerts import AlertEvaluator
from core.errors import (
    ConsumerNotFoundError,
    EventNotRelevantError,
    EventNotFoundError,
    MCPEventServerError,
    OperationTimeoutError,
    StorageError,
    ValidationError,
)
from core.runtime import BackgroundTaskManager
from core.sse_broker import EventBroker
from mcp_server.contract import (
    CONTRACT_VERSION,
    RESOURCE_EVENT_LATEST,
    RESOURCE_EVENTS_PENDING,
    RESOURCE_SYSTEM_INFO,
    RESOURCE_SOURCES_STATUS,
    RESOURCE_SYSTEM_METRICS,
    RESOURCE_EVENTS_RECENT,
)
from mcp_server.metrics import RuntimeMetrics
from mcp_server.resources import register_resources
from mcp_server.services import Services
from mcp_server.tools import (
    register_alert_tools,
    register_background_tools,
    register_consumer_tools,
    register_dev_tools,
    register_event_tools,
    register_replay_tools,
    register_source_tools,
    register_system_tools,
)
from sources import SourceManager, build_source_manager, SourceConfigError
from api.routes import build_market_routes
from market.models import Quote
from market.serialization import quote_to_dict
from market.service import MarketService

# ---------------------------------------------------------------------------
# SDK responsibility vs. application responsibility
# ---------------------------------------------------------------------------
#
# SDK handles:   MCP request dispatch, JSON Schema generation, transport,
#                 tools/call result formatting (is_error wrapping),
#                 subscriptions/listen, Context injection, protocol errors.
#
# Application
# handles:       event model, consumers, routing, persistence, ACK/checkpoints,
#                 replay, sources, background runtime, safe domain exceptions.
#
# Tool handlers MUST raise ordinary exceptions for tool-execution failures.
# The SDK wraps them into CallToolResult(is_error=True) automatically.
# MCPError is reserved for genuine protocol-level failures only.
# ---------------------------------------------------------------------------

# ============================================================
# LOGGER
# ============================================================

_app_logger = logging.getLogger("event_server")
_app_logger.setLevel(logging.DEBUG)

_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.INFO)
_handler.setFormatter(logging.Formatter("%(message)s"))
_app_logger.addHandler(_handler)

_debug_handler = logging.StreamHandler(sys.stdout)
_debug_handler.setLevel(logging.DEBUG)
_debug_handler.setFormatter(logging.Formatter("[DEBUG] %(name)s - %(message)s"))
_app_logger.addHandler(_debug_handler)

# Suppress the SDK's rich-format debug/info output to the console.
# SDK errors (WARNING / ERROR) still propagate through uvicorn / standard error.
for _sdk_name in ("mcp", "mcp.server", "mcp.server.mcpserver"):
    _sdk_logger = logging.getLogger(_sdk_name)
    _sdk_logger.setLevel(logging.WARNING)

# ============================================================
# LOAD CONFIGURATION
# ============================================================

try:
    _config = load_config(str(CONFIG_PATH))
    validate_config(_config)
except ConfigError as exc:
    print("ERROR: Configuration error — {0}".format(exc), file=sys.stderr)
    print("Fix config.json and restart.", file=sys.stderr)
    sys.exit(1)

# ============================================================
# CONSTANTS (from config)
# ============================================================

SERVER_NAME = _config["server_name"]
LISTEN_HOST = _config["host"]
LISTEN_PORT = _config["port"]
LOG_LEVEL = _config["log_level"]
MAX_REQUEST_BODY_SIZE = _config["max_request_body_size"]
DATA_DIR = _config["data_dir"]
TIMEOUTS = _config["timeouts"]
REPLAY_CFG = _config["replay"]
SOURCES_CFG = _config.get("sources", {})
RETENTION_CFG = _config["retention"]

# ── Transport security (DNS-rebinding protection) ────────────────────────────
# Explicit construction removes ambiguity vs. SDK auto-detection.
# Defaults match what the SDK applies when host is localhost and no settings
# are provided — but making them explicit means config.json can override them.
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=_config.get("enable_dns_rebinding_protection", True),
    allowed_hosts=_config.get("allowed_hosts", ["127.0.0.1:*", "localhost:*", "[::1]:*"]),
    allowed_origins=_config.get("allowed_origins", ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]),
)

# Resource URIs — canonical definitions are in mcp_server.contract
EVENT_RESOURCE_URI = RESOURCE_EVENT_LATEST
EVENTS_PENDING_URI = RESOURCE_EVENTS_PENDING
INFO_RESOURCE_URI = RESOURCE_SYSTEM_INFO
SOURCES_RESOURCE_URI = RESOURCE_SOURCES_STATUS

# ============================================================
# EVENT STORE
# ============================================================

_db_path = os.path.join(str(PROJECT_ROOT), DATA_DIR, "events.db")
_store = runtime.event_store_module.EventStore(_db_path)
_app_logger.info("event store: %s", _store.db_path)

# ── Startup: restore durable recent history ──────────────────────────────────
from core import events as _events_mod
_recent = _store.get_recent_events(
    limit=_events_mod.RECENT_HISTORY_CAPACITY,
    newest_first=False,
)
_events_mod.restore_recent_history(_recent)
_app_logger.info("recent-history restored: %d event(s)", len(_recent))

# ============================================================
# INFRASTRUCTURE (must precede MCPServer construction)
# ============================================================

_metrics = RuntimeMetrics()

_subscription_bus = InMemorySubscriptionBus()
_bg_task_manager = runtime.BackgroundTaskManager()


# ── SSE broadcast broker ──────────────────────────────────────────────────────
# Wired to the canonical publish path so every published event fans out to
# connected GET /events/stream subscribers automatically.
_event_broker = EventBroker()
events.configure_sse_broker(_event_broker)


# ── Dedicated MARKET SSE broker + shared market service ──────────────────────
# Second EventBroker INSTANCE (same class as the generic stream above):
# different subscribers, different traffic profile. Raw/synthetic market
# quotes intentionally bypass publish_event() — they must never enter the
# generic journal/history/metrics/alert pipeline.
_market_event_broker = EventBroker()


def _on_market_quote_update(quote: Quote) -> None:
    """Post-commit MarketService hook: canonical quote -> market SSE fan-out.

    Single JSON encoding point: the canonical serializer produces the dict,
    this wraps it in the stable market envelope and encodes once —
    EventBroker.broadcast expects a pre-encoded string.
    """
    envelope = {"type": "quote", "data": quote_to_dict(quote)}
    try:
        line = json.dumps(envelope, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _app_logger.warning("market quote failed canonical JSON encoding; dropped")
        return
    _market_event_broker.broadcast(line)


_market_service = MarketService(on_quote_update=_on_market_quote_update)

# ── Source Manager (needs _market_service for UpstoxFeed injection) ─────────
try:
    _source_manager = build_source_manager(
        SOURCES_CFG, market_service=_market_service,
    )
except SourceConfigError as exc:
    _app_logger.error("source configuration error: %s", exc)
    _source_manager = SourceManager()

# ── Lifespan ─────────────────────────────────────────────────────────────────
_lifespan = runtime.make_lifespan(
    _store,
    bg_manager=_bg_task_manager,
    shutdown_timeout=TIMEOUTS["shutdown_seconds"],
    source_manager=_source_manager,
    bus=_subscription_bus,
    source_configs=SOURCES_CFG,
    metrics=_metrics,
    retention_cfg=RETENTION_CFG,
)


# ============================================================
# MCP SERVER
# ============================================================

mcp = MCPServer(
    name=SERVER_NAME,
    version=__version__,
    description="Generic self-hosted MCP event server with native event delivery",
    log_level=LOG_LEVEL,
    subscriptions=_subscription_bus,
    lifespan=_lifespan,
)

# ============================================================
# SERVICES BUNDLE
# ============================================================


_services = Services(
    store=_store,
    subscription_bus=_subscription_bus,
    bg_task_manager=_bg_task_manager,
    source_manager=_source_manager,
    timeouts=TIMEOUTS,
    replay_cfg=REPLAY_CFG,
    metrics=_metrics,
)

# ── Alert engine (generic, Context-free) ──────────────────────────────────────
# Single-process MVP: the evaluator is wired to the canonical publish path via
# events.configure_alert_evaluator(). It depends only on the store and the
# subscription bus — no MCP Context, ClientSession, or request state.
_alert_evaluator = AlertEvaluator(store=_store, subscription_bus=_subscription_bus, metrics=_metrics)
events.configure_alert_evaluator(_alert_evaluator.evaluate)
events.configure_metrics(_metrics)
# ============================================================
# REGISTER RESOURCES
# ============================================================

_constants = {
    "SERVER_NAME": SERVER_NAME,
    "SERVER_VERSION": __version__,
    "CONTRACT_VERSION": CONTRACT_VERSION,
    "MCP_SPEC": "2026-07-28",
    "EVENT_RESOURCE_URI": EVENT_RESOURCE_URI,
    "EVENTS_PENDING_URI": EVENTS_PENDING_URI,
    "INFO_RESOURCE_URI": INFO_RESOURCE_URI,
    "SOURCES_RESOURCE_URI": SOURCES_RESOURCE_URI,
    "METRICS_RESOURCE_URI": RESOURCE_SYSTEM_METRICS,
    "RECENT_RESOURCE_URI": RESOURCE_EVENTS_RECENT,
    "LISTEN_HOST": LISTEN_HOST,
    "LISTEN_PORT": LISTEN_PORT,
}

register_resources(mcp, _services, _constants)

# ============================================================
# REGISTER TOOLS
# ============================================================

register_system_tools(mcp)
register_event_tools(mcp, _services)
register_consumer_tools(mcp, _services)
register_replay_tools(mcp, _services)
register_source_tools(mcp, _services)
register_background_tools(mcp, _services)
register_dev_tools(mcp, _services)
register_alert_tools(mcp, _services)

# ============================================================
# ASGI APPLICATION (top-level Starlette + Uvicorn)
# ============================================================

# Build the MCP Streamable HTTP ASGI app.  This creates the internal
# StreamableHTTPSessionManager, registers the /mcp route, and includes any
# custom routes added via @mcp.custom_route.  The SDK nests the application
# lifespan inside the session-manager lifespan automatically.
mcp_asgi_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    json_response=True,
    max_request_body_size=MAX_REQUEST_BODY_SIZE,
    transport_security=_transport_security,
)


async def _health_check(request: Request) -> JSONResponse:  # noqa: ARG001
    """Minimal liveness probe — returns 200 without requiring MCP init."""
    return JSONResponse({"status": "ok"})


async def _event_stream(request: Request) -> Response:
    """
    Generic SSE live event stream at GET /events/stream.

    Each connected client receives every canonical event published through
    publish_event() as an SSE ``event: event`` message carrying the full
    event JSON as its ``data:`` field.  Slow subscribers are dropped
    silently so the publish path is never blocked.

    The stream is independent of MCP transport security — it is a plain
    HTTP endpoint served by the top-level Starlette app.
    """
    async def _generate() -> Any:
        async with _event_broker.subscribe() as events_async_iter:
            async for line in events_async_iter:
                yield line
                yield "\r\n"  # terminate the SSE message

    return EventSourceResponse(
        _generate(),
        media_type="text/event-stream",
        ping=15,  # keepalive interval in seconds
    )


@asynccontextmanager
async def _lifespan(app: Starlette) -> None:  # noqa: ARG001
    """
    Top-level lifespan owner.

    Delegates to the MCP SDK session manager via the SDK-supported pattern
    (async with session_manager.run(): yield).  The SDK, in turn, calls the
    application lifespan (_lifespan passed to MCPServer) so source managers
    and background tasks start/stop correctly.
    """
    async with mcp_asgi_app.router.lifespan_context(app):
        yield


# One top-level Starlette app: MCP protocol routes + /health + /events/stream
# + dedicated market SSE stream.
app = Starlette(
    routes=list(mcp_asgi_app.routes)
    + [
        Route("/health", endpoint=_health_check, methods=["GET"]),
        Route("/events/stream", endpoint=_event_stream, methods=["GET"]),
    ]
    + build_market_routes(_market_event_broker),
    middleware=list(mcp_asgi_app.user_middleware),
    lifespan=_lifespan,
)

# Composition/test introspection seam ONLY: lets the in-process integration
# test reach the real composed objects. Production modules receive these via
# constructor/argument injection and must NEVER read app.state.
app.state.market_service = _market_service
app.state.market_event_broker = _market_event_broker

# ============================================================
# RUN SERVER
# ============================================================

def main() -> None:
    """Canonical application entrypoint (python -m app.server)."""
    print_banner(
        mcp_spec="2026-07-28",
        listen_host=LISTEN_HOST,
        listen_port=LISTEN_PORT,
        event_resource_uri=EVENT_RESOURCE_URI,
        events_pending_uri=EVENTS_PENDING_URI,
        info_resource_uri=INFO_RESOURCE_URI,
        sources_resource_uri=SOURCES_RESOURCE_URI,
        log_level=LOG_LEVEL,
        data_dir=DATA_DIR,
        timeouts=TIMEOUTS,
    )

    config = uvicorn.Config(
        app,
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        log_level=LOG_LEVEL.lower(),
    )
    server = uvicorn.Server(config)

    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        print_shutdown()
        sys.exit(0)
    except Exception as exc:
        _app_logger.error("unexpected error during server run: {0}".format(exc), exc)
        print_shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
