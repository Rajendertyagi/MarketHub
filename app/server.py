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
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.routing import Route, Mount

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
    register_market_tools,
)
from sources import SourceManager, build_source_manager, SourceConfigError
from api.routes import (  # noqa: F401  (build_settings_routes wired below)
    build_market_routes,
    build_auth_routes,
)
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
    # Alert engine consumes the same canonical quote (never polls REST).
    try:
        _alert_engine.evaluate(quote)
    except Exception:
        _app_logger.warning("alert evaluation failed", exc_info=True)


_market_service = MarketService(on_quote_update=_on_market_quote_update)

# ── Source Manager (needs _market_service for UpstoxFeed injection) ─────────
try:
    _source_manager = build_source_manager(
        SOURCES_CFG, market_service=_market_service,
    )
except SourceConfigError as exc:
    _app_logger.error("source configuration error: %s", exc)
    _source_manager = SourceManager()

# Hold a reference to the Upstox feed for runtime auth management.
_feed_ref: dict[str, Any] = {"feed": None}
_upstox_source_name: str | None = None
for _src_name, _src in _source_manager.enabled_sources.items():
    if hasattr(_src, "update_credentials"):
        _feed_ref["feed"] = _src
        _upstox_source_name = _src_name
        break

# ── Product services: instrument catalog, alerts ─────────────────────────────
from app.instruments import InstrumentCatalog as _InstrumentCatalog
from app.alerts import AlertEngine as _AlertEngine
from api.product_routes import (
    build_instrument_routes as _build_instrument_routes,
    build_watchlist_routes as _build_watchlist_routes,
    build_alert_routes as _build_alert_routes,
)

_instrument_catalog = _InstrumentCatalog(_store)
_alert_engine = _AlertEngine(_store)

from app.market_data import ProviderMarketData as _ProviderMarketData
from api.product_routes import (
    build_market_data_routes as _build_market_data_routes,
)


def _upstox_auth_context():
    """(rest, credentials) for the live feed, or None when unauthenticated."""
    feed = _feed_ref.get("feed")
    if feed is None:
        return None
    creds = getattr(feed, "credentials_snapshot", None)
    rest = getattr(feed, "rest", None)
    if creds is None or rest is None:
        return None
    if not creds.status().get("token_present"):
        return None
    return rest, creds


_provider_market_data = _ProviderMarketData(_upstox_auth_context)


class _FeedSubscription:
    """Watchlist→feed adapter: desired-set updates on the live Upstox feed."""

    async def add(self, exchange: str, token: str) -> None:
        feed = _feed_ref.get("feed")
        if feed is not None and hasattr(feed, "add_instruments"):
            await feed.add_instruments([token])

    async def remove(self, exchange: str, token: str) -> None:
        feed = _feed_ref.get("feed")
        if feed is not None and hasattr(feed, "remove_instruments"):
            await feed.remove_instruments([token])


_feed_subscription = _FeedSubscription()

# ── OAuth login configuration (backend-only secrets) ─────────────────────────
# Credential precedence (documented + tested):
#   1. Credentials saved via WebUI  (encrypted in the app SQLite DB,
#      table `secrets`; master key at data/master.key, outside the DB)
#   2. Environment variables        (UPSTOX_API_KEY / UPSTOX_API_SECRET)
#   3. Not configured               (manual token entry remains available)
# The secret NEVER reaches the browser, API responses, or logs. The mutable
# _oauth_cfg_ref dict is shared with the settings routes so saving new
# credentials in the WebUI enables OAuth at runtime — no restart needed.
from app.secrets_store import (
    CredentialStore as _CredentialStore,
    CredentialDecryptError as _CredentialDecryptError,
)
from api.routes import build_settings_routes

_credential_store = _CredentialStore(_store)

_oauth_cfg_ref: dict[str, str] = {
    "api_key": "",
    "api_secret": "",
    "redirect_uri": os.environ.get(
        "UPSTOX_REDIRECT_URI",
        f"http://localhost:{LISTEN_PORT}/auth/upstox/callback",
    ).strip(),
}

try:
    _saved_creds = _credential_store.load_upstox_app_credentials()
except _CredentialDecryptError:
    # Lost/corrupt master key: do NOT fail startup and do NOT regenerate
    # silently. Operator sees "cannot decrypt" in Settings and re-enters.
    _saved_creds = None
    _app_logger.error(
        "stored upstox credentials cannot be decrypted - "
        "re-enter credentials in Settings"
    )

if _saved_creds is not None:
    # Precedence 1: WebUI-saved credentials win over env fallback.
    _oauth_cfg_ref["api_key"] = _saved_creds["api_key"]
    _oauth_cfg_ref["api_secret"] = _saved_creds["api_secret"]
    _app_logger.info("upstox oauth configured from saved credentials")
else:
    # Precedence 2: environment-variable fallback.
    _env_key = os.environ.get("UPSTOX_API_KEY", "").strip()
    _env_secret = os.environ.get("UPSTOX_API_SECRET", "").strip()
    if _env_key and _env_secret:
        _oauth_cfg_ref["api_key"] = _env_key
        _oauth_cfg_ref["api_secret"] = _env_secret
        _app_logger.info(
            "upstox oauth configured from environment fallback"
        )
    else:
        _app_logger.info(
            "upstox oauth not configured - set credentials in Settings"
        )


async def _restart_upstox_source() -> None:
    """Restart the Upstox source through SourceManager (lifecycle owner)."""
    if _upstox_source_name is not None:
        await _source_manager.restart_source(_upstox_source_name)


# Stateless REST transport for the OAuth code exchange. Stores no secrets.
# Created unconditionally: OAuth may become available at runtime when the
# operator saves credentials in Settings.
from brokers.upstox.rest import UpstoxRest as _UpstoxRest
_oauth_rest: Any = _UpstoxRest()

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
    market_service=_market_service,
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
register_market_tools(mcp, _services)
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


async def _root_redirect(request: Request) -> Response:
    return RedirectResponse(url="/ui/", status_code=302)

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
        Route("/", endpoint=_root_redirect, methods=["GET"]),
        Route("/health", endpoint=_health_check, methods=["GET"]),
        Route("/events/stream", endpoint=_event_stream, methods=["GET"]),
    ]
    + build_market_routes(
        _market_event_broker,
        market_service=_market_service,
        source_status_fn=lambda: [
            s.status() for s in _source_manager.enabled_sources.values()
        ],
    )
    + build_auth_routes(
        _feed_ref,
        restart_fn=_restart_upstox_source,
        oauth=_oauth_cfg_ref,
        rest=_oauth_rest,
    )
    + build_settings_routes(_oauth_cfg_ref)
    + _build_instrument_routes(_instrument_catalog, store=_store)
    + _build_watchlist_routes(_store, subscription=_feed_subscription)
    + _build_alert_routes(_store, _alert_engine)
    + _build_market_data_routes(_provider_market_data)
    + [Mount("/ui", app=StaticFiles(directory=str(PROJECT_ROOT / "web" / "ui"), html=True),
            name="ui")],
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
