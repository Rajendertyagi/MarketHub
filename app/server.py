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
from datetime import datetime, timezone
from typing import Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.subscriptions import InMemorySubscriptionBus
from mcp.server.transport_security import TransportSecuritySettings
from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from api.routes import (
    build_auth_routes,
    build_market_routes,
    build_source_control_routes,
)
from app import __version__
from app.config import (
    ConfigError,
    get_public_base_url,
    load_config,
    oauth_callback_url,
    validate_config,
)
from app.lifecycle import print_banner, print_shutdown

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
# LOGGING (centralized: console preserved + rotating file)
# ============================================================
# Single owner of handler configuration: app.logging_setup. Console output
# is preserved; everything INFO+ is also persisted to data/logs/markethub.log
# so an incident's final event survives the console window closing.
from app.logging_setup import log_startup_diagnostics, setup_logging
from app.paths import CONFIG_PATH, PROJECT_ROOT
from core import events, runtime
from core.alerts import AlertEvaluator
from core.sse_broker import EventBroker
from market.models import Quote
from market.serialization import quote_to_dict
from market.service import MarketService
from mcp_server.contract import (
    CONTRACT_VERSION,
    RESOURCE_EVENT_LATEST,
    RESOURCE_EVENTS_PENDING,
    RESOURCE_EVENTS_RECENT,
    RESOURCE_SOURCES_STATUS,
    RESOURCE_SYSTEM_INFO,
    RESOURCE_SYSTEM_METRICS,
)
from mcp_server.metrics import RuntimeMetrics
from mcp_server.resources import register_resources
from mcp_server.services import Services
from mcp_server.tools import (
    register_alert_tools,
    register_condition_alert_tools,
    register_consumer_tools,
    register_event_tools,
    register_market_alert_tools,
    register_market_intel_tools,
    register_market_tools,
    register_options_analytics_tools,
    register_replay_tools,
    register_system_tools,
)
from sources import SourceConfigError, SourceManager, build_source_manager

_LOG_FILE = setup_logging(PROJECT_ROOT)  # never raises; may degrade console-only

_app_logger = logging.getLogger("event_server")
_app_logger.setLevel(logging.DEBUG)

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
    print(f"ERROR: Configuration error — {exc}", file=sys.stderr)
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

# Shared, mutable broker auth/restore state surfaced (read-only) by the auth
# status endpoints. Populated during startup restore so the WebUI can show
# whether a session was auto-restored after a MarketHub restart.
_broker_restore: dict[str, Any] = {}

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


# ── L1: WebUI live log buffer + SSE broker ──────────────────────────────────
from core.log_buffer import LogBuffer as _LogBuffer
from core.sse_broker import EventBroker as _LogEventBroker
from app.logging_setup import attach_webui_handler as _attach_webui_handler

_log_buffer = _LogBuffer(max_size=1000)
_log_sse_broker = _LogEventBroker()
_webui_handler = _attach_webui_handler(_log_buffer, broker=_log_sse_broker)


# ── News & Sentiment service ────────────────────────────────────────────────
from news.service import NewsService as _NewsService
from news.adapters.rss import RSSAdapter as _RSSAdapter
from news.adapters.reddit import RedditAdapter as _RedditAdapter
from app.config import DEFAULTS as _DEFAULTS

_news_service = _NewsService(store=_store)
_news_service.register_adapter(_RSSAdapter())
_news_service.register_adapter(_RedditAdapter())
try:
    _news_service.seed_defaults(_DEFAULTS.get("news", {}).get("default_sources", []))
except Exception as _exc:
    _app_logger.warning("news seed_defaults failed: %s", _exc)
try:
    _news_service.set_retention_days(
        int(_DEFAULTS.get("news", {}).get("retention_days", 30)))
except Exception as _exc:
    _app_logger.warning("news retention config failed: %s", _exc)


async def _on_market_quote_update(quote: Quote) -> None:
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
    # Market-alert triggers publish canonical durable alert.triggered events
    # through the shared event pipeline (SSE fan-out included).
    try:
        await _alert_engine.evaluate(quote)
    except Exception:
        _app_logger.warning("alert evaluation failed", exc_info=True)
    # B2: market_condition alert engine — same canonical quote, provider-
    # neutral identity resolution, atomic trigger persistence. Error-isolated
    # so a condition-engine failure never breaks the market-alert path.
    try:
        await _condition_alert_engine.evaluate(quote)
    except Exception:
        _app_logger.warning(
            "condition alert evaluation failed", exc_info=True)


_market_service = MarketService(on_quote_update=_on_market_quote_update)

# ── Source Manager (needs _market_service for UpstoxFeed injection) ─────────
# Fyers feed requires a runtime access_token_getter (it cannot be expressed
# in static JSON config). The runtime access token is owned by ONE object so
# both the startup/session-restore path and the login/clear path mutate the
# same controlled owner (never a shared raw dict). The feed only reads it.
from app.fyers_runtime_auth import FyersRuntimeAuth as _FyersRuntimeAuth

_fyers_runtime_auth = _FyersRuntimeAuth()


def _inject_fyers_source_config(sources_cfg: dict[str, Any]) -> None:
    """Wire the Fyers source(s) to the single credential source of truth.

    For every fyers_feed source this injects, in place (startup-only):
      * ``access_token_getter``  — runtime-memory-only token (composition root)
      * ``credential_store``     — encrypted credential store (app_id/secret/
                                    refresh token live ONLY here, never in
                                    config.json)
      * ``redirect_uri``         — centralized OAuth callback URL

    Legacy fallback: if a fyers source block in config.json still carries
    plaintext ``app_id``/``app_secret`` AND the encrypted store has none, the
    values are migrated into the encrypted store once (deterministic) and a
    deprecation warning is logged. The store then becomes authoritative; the
    operator should remove the secrets from config.json. Secrets are never
    printed or exposed.
    """
    for _name, _cfg in (sources_cfg or {}).items():
        if not isinstance(_cfg, dict):
            continue
        if _cfg.get("type") != "fyers_feed" and _name != "fyers":
            continue
        _cfg["access_token_getter"] = _fyers_runtime_auth.get_access_token
        _cfg["credential_store"] = _credential_store
        _cfg["redirect_uri"] = FYERS_REDIRECT_URI

        _store_creds = None
        try:
            _store_creds = _credential_store.load_fyers_credentials()
        except Exception:
            _store_creds = None
        if _store_creds:
            _cfg["app_id"] = _store_creds["app_id"]
            _cfg["app_secret"] = _store_creds["app_secret"]
        elif _cfg.get("app_id") and _cfg.get("app_secret"):
            # Legacy plaintext config credentials: migrate into the encrypted
            # store (deterministic, one-time) so the store is authoritative.
            try:
                _credential_store.save_fyers_credentials(
                    str(_cfg["app_id"]), str(_cfg["app_secret"]))
                _app_logger.warning(
                    "migrated legacy plaintext Fyers credentials from "
                    "config.json into the encrypted store; you may now "
                    "remove app_id/app_secret from the fyers source block")
                _migrated = _credential_store.load_fyers_credentials()
                if _migrated:
                    _cfg["app_id"] = _migrated["app_id"]
                    _cfg["app_secret"] = _migrated["app_secret"]
            except Exception as _exc:
                _app_logger.error(
                    "failed to migrate legacy Fyers config credentials: %s",
                    type(_exc).__name__)


# Source-manager construction is deferred until AFTER the credential store
# exists (it is needed to wire Fyers sources). See block below line ~464.
def _build_source_manager() -> SourceManager:
    try:
        return build_source_manager(SOURCES_CFG, market_service=_market_service)
    except SourceConfigError as exc:
        _app_logger.error("source configuration error: %s", exc)
        return SourceManager()


async def _restart_fyers_source() -> None:
    """Restart the Fyers source through SourceManager (lifecycle owner)."""
    if _fyers_source_name is None:
        # Source may not exist yet (Fyers has no default sources entry). Build
        # and register it at runtime so login/OAuth can (re)start the feed.
        _ensure_fyers_source_registered()
    if _fyers_source_name is None:
        _app_logger.warning(
            "oauth restart skipped: no fyers source registered")
        return
    try:
        await _source_manager.restart_source(_fyers_source_name)
    except Exception:
        _app_logger.exception("oauth restart of fyers source failed")
        raise


async def _try_restore_fyers_token() -> None:
    """Best-effort Fyers restore, delegated to FyersAuthService.

    Compat wrapper: the dedicated auth subsystem (app/auth/) owns the
    provider mechanics; this keeps the historic call sites (lifespan +
    tests) working while the ownership lives in one place. Never raises:
    failures leave the runtime token empty so the feed reports
    auth_required instead of a generic error.
    """
    try:
        from app.auth.fyers import FyersAuthService as _FyersSvc
        from app.auth.storage import AuthStorage as _AuthStorage
        _svc = _FyersSvc(
            _AuthStorage(_credential_store), _fyers_runtime_auth,
            redirect_uri=FYERS_REDIRECT_URI)
        _out = await _svc.restore_session()
        _broker_restore["fyers_restored"] = bool(_out.restored)
        _broker_restore["fyers_last_status"] = _out.reason
        if _out.restored:
            _app_logger.info("fyers access token restored (%s)", _out.reason)
        else:
            _app_logger.warning("fyers token restore: %s", _out.reason)
    except Exception:
        _app_logger.exception("fyers session restore failed")
        _broker_restore["fyers_restored"] = False
        _broker_restore["fyers_last_status"] = "restore_error"

# Wire low-frequency source lifecycle events into the generic EventBroker
# (WP22). Feeds call their optional on_state_change listener; we broadcast a
# redacted envelope so the WebUI can refresh immediately on state changes.
def _on_source_state_change(
    source: str, provider: str, old_state: str, new_state: str,
    reason: str | None,
) -> None:
    envelope = {
        "type": "source.state_changed",
        "data": {
            "source": source,
            "provider": provider,
            "old_state": old_state,
            "new_state": new_state,
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        },
    }
    try:
        _event_broker.broadcast(json.dumps(envelope, ensure_ascii=False))
    except Exception:  # pragma: no cover - broadcast must never break a feed
        _app_logger.debug("source state change broadcast failed", exc_info=True)


# ── Fyers source runtime registration (no restart required) ──────────────────
# Fyers is authenticated via Settings but, unlike Upstox, has no canonical
# ``sources.fyers`` entry by default and no runtime registration path. Without
# this, the Fyers feed is never constructed, so it reports "source not
# configured" even though credentials/session exist. These helpers register a
# FyersFeed in the SAME SourceManager at runtime (reusing the existing source
# lifecycle) and persist a durable (secret-free) source config so a restart
# keeps it enabled.
# NOTE: the BANKNIFTY key must be the catalog-exact "NSE:NIFTYBANK-INDEX"
# ("NSE:BANKNIFTY-INDEX" matches no catalog row and no HSM index name, so
# BANKNIFTY would silently never stream).
_DEFAULT_FYERS_INSTRUMENTS = [
    {"key": "NSE:NIFTY50-INDEX", "exchange": "NSE", "tradingsymbol": "NIFTY 50"},
    {"key": "NSE:NIFTYBANK-INDEX", "exchange": "NSE", "tradingsymbol": "NIFTY BANK"},
]


def _persist_fyers_source_config(feed_cfg: dict[str, Any] | None = None) -> None:
    """Write (or remove, when None) the durable ``sources.fyers`` config.

    Secrets NEVER touch config.json — only the source shape (type/enabled/
    mode/instruments). Mirrors the Upstox feed-config persistence.

    SAFETY: writes are atomic (temp file + os.replace) so a failed write can
    never truncate or corrupt the live config. If the current config cannot be
    read we ABORT rather than overwriting a good config with an empty one.
    """
    import tempfile
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
            _cfg = json.load(_f)
    except Exception:
        # Never wipe a config we cannot read — leave the file untouched.
        _app_logger.warning(
            "fyers source config not persisted: could not read %s", CONFIG_PATH)
        return
    if not isinstance(_cfg, dict):
        _cfg = {}
    _sources = _cfg.get("sources")
    if not isinstance(_sources, dict):
        _sources = {}
    if feed_cfg is None:
        _sources.pop("fyers", None)
    else:
        _sources["fyers"] = feed_cfg
    if _sources:
        _cfg["sources"] = _sources
    else:
        _cfg.pop("sources", None)
    # Atomic write: a failed dump must never truncate the live config file.
    _dir = os.path.dirname(str(CONFIG_PATH)) or "."
    _fd, _tmp = tempfile.mkstemp(dir=_dir, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(_fd, "w", encoding="utf-8") as _f:
            json.dump(_cfg, _f, indent=2)
            _f.flush()
            os.fsync(_f.fileno())
        os.replace(_tmp, str(CONFIG_PATH))
    except Exception:
        try:
            os.unlink(_tmp)
        except OSError:
            pass
        raise


def _ensure_fyers_source_registered() -> str | None:
    """Register a Fyers source in the SourceManager at runtime (idempotent).

    Returns the registered source name, or None if the feed cannot be built.
    Reuses the existing source lifecycle — no second registry, no parallel
    runtime, no duplicate feed manager.
    """
    global _fyers_source_name
    # Already registered (name "fyers" or a FyersFeed instance)?
    for _n, _s in _source_manager.enabled_sources.items():
        if _n == "fyers" or getattr(_s, "__class__", None).__name__ == "FyersFeed":
            _fyers_source_name = _n
            return _n
    # Build the durable-ish source config (secrets stay in the encrypted store).
    _fy_cfg = dict(SOURCES_CFG.get("fyers") or {})
    if not _fy_cfg.get("type"):
        _fy_cfg["type"] = "fyers_feed"
    _fy_cfg.setdefault("enabled", True)
    _fy_cfg.setdefault("mode", "full")
    _fy_cfg.setdefault("instruments", list(_DEFAULT_FYERS_INSTRUMENTS))
    _fy_cfg["source_name"] = "fyers"
    # Inject runtime-only wiring (getter / store / redirect). Never secrets.
    _inject_fyers_source_config({"fyers": _fy_cfg})
    try:
        from sources.registry import _create_fyers_feed
        _feed = _create_fyers_feed(_fy_cfg, market_service=_market_service)
    except Exception:
        _app_logger.exception("failed to build fyers feed at runtime")
        return None
    _source_manager.register(_feed)
    try:
        _feed.on_state_change = _on_source_state_change
    except Exception:
        pass
    # Persist a secret-free source config so a restart keeps it enabled.
    try:
        _persist_fyers_source_config(_fy_cfg)
    except Exception:
        _app_logger.warning("failed to persist fyers source config")
    SOURCES_CFG["fyers"] = _fy_cfg
    _fyers_source_name = "fyers"
    return "fyers"


async def _start_fyers_source() -> None:
    """Register (if needed) and start the Fyers source via SourceManager."""
    name = _ensure_fyers_source_registered()
    if name is None:
        _app_logger.warning("fyers source could not be registered; cannot start")
        return
    if _source_manager.task_running(name):
        return
    try:
        await _source_manager.start_source(name)
    except Exception:
        _app_logger.exception("fyers source start failed")
        raise


# ── Product services: instrument catalog, alerts ─────────────────────────────
from api.chat_routes import build_chat_routes as _build_chat_routes
from api.product_routes import (
    build_admin_routes as _build_admin_routes,
)
from api.product_routes import (
    build_alert_history_routes as _build_alert_history_routes,
)
from api.product_routes import (
    build_alert_routes as _build_alert_routes,
)
from api.product_routes import (
    build_api_meta_routes as _build_api_meta_routes,
)
from api.product_routes import (
    build_app_settings_routes as _build_app_settings_routes,
)
from api.product_routes import (
    build_diagnostics_routes as _build_diagnostics_routes,
)
from api.product_routes import (
    build_fyers_auth_routes as _build_fyers_auth_routes,
)
from api.product_routes import (
    build_instrument_routes as _build_instrument_routes,
)
from api.product_routes import (
    build_intel_routes as _build_intel_routes,
)
from api.product_routes import (
    build_watchlist_portability_routes as _build_watchlist_portability_routes,
)
from api.product_routes import (
    build_watchlist_routes as _build_watchlist_routes,
)
from app.alerts import AlertEngine as _AlertEngine
from app.chat_tools import ChatToolRegistry as _ChatToolRegistry
from app.condition_alerts import ConditionAlertEngine as _ConditionAlertEngine
from app.instrument_identity import InstrumentIdentityRegistry as _IdentityRegistry
from app.instruments import InstrumentCatalog as _InstrumentCatalog
from app.market_identity import MarketInstrumentIdentityResolver as _IdentityResolver
from app.market_intel import MarketIntel as _MarketIntel

_instrument_catalog = _InstrumentCatalog(_store)

# Canonical instrument-identity registry: maps provider-normalized
# storage keys (config keys) to catalog identities so ONE real
# instrument has ONE quote state across feed/REST/intel/option-chain.
_identity_registry = _IdentityRegistry()


def _register_config_identities() -> None:
    """Bind config instrument keys to catalog identities (best-effort).

    For every configured source instrument, the storage key (config
    ``key``) is canonical; aliases include the tradingsymbol and the
    catalog provider token when a catalog row matches on
    exchange+tradingsymbol. Runs at startup and is safe to re-run.
    """
    for _src_name, _src_cfg in SOURCES_CFG.items():
        if not isinstance(_src_cfg, dict):
            continue
        for _instr in _src_cfg.get("instruments") or []:
            if not isinstance(_instr, dict):
                continue
            key = (_instr.get("key") or "").strip()
            exchange = (_instr.get("exchange") or "").strip()
            tsym = (_instr.get("tradingsymbol") or "").strip()
            if not key:
                continue
            aliases = {key, tsym} - {""}
            try:
                import re as _re

                def _norm(s: str) -> str:
                    return _re.sub(r"[^a-z0-9]", "", s.lower())

                targets = {_norm(t) for t in (tsym, key) if t}
                for row in _instrument_catalog.search(
                        q=tsym or key, exchange=exchange or None,
                        limit=25):
                    row_sym = _norm(row.get("tradingsymbol") or "")
                    if row_sym in targets and \
                            row.get("exchange") == exchange:
                        token = row.get("instrument_token")
                        if token:
                            aliases.add(token)
                        break
            except Exception:
                logger.warning("identity lookup failed for %s", key)
            _identity_registry.register(key, aliases)


_register_config_identities()


_alert_engine = _AlertEngine(_store, bus=_subscription_bus)

# ── B2: market_condition alert engine + provider-neutral identity resolver ──
# The resolver maps every catalog row to a canonical instrument id so a
# condition alert defined once evaluates identically across providers.
# The condition engine is instrument-indexed (canonical_id -> alert ids) and
# evaluates only the alerts registered for a quote's resolved identity.
_identity_resolver = _IdentityResolver()
try:
    _identity_resolver.register_catalog_rows(_store.list_all_instruments())
except Exception:
    _app_logger.warning(
        "condition identity resolver population failed", exc_info=True)

from app.market_analytics import MarketAnalyticsService as _AnalyticsService
_analytics_service = _AnalyticsService(
    _market_service, instrument_catalog=_instrument_catalog)

_condition_alert_engine = _ConditionAlertEngine(
    _store, resolver=_identity_resolver, bus=_subscription_bus,
    analytics_service=_analytics_service)

from api.product_routes import (
    build_market_data_routes as _build_market_data_routes,
)
from api.ai_alert_routes import build_ai_alert_routes as _build_ai_alert_routes
from api.log_routes import build_log_routes as _build_log_routes
from api.news_routes import build_news_routes as _build_news_routes
from api.diagnostics_routes import build_diagnostics_run_routes as _build_diag_routes
from api.subscription_routes import build_subscription_routes as _build_subscription_routes
from api.fno_routes import build_fno_routes as _build_fno_routes
from app.market_data import ProviderMarketData as _ProviderMarketData


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
    """Watchlistfeed adapter: desired-set updates on the live Upstox feed."""

    async def add(self, exchange: str, token: str) -> None:
        # Register identity aliases for runtime-added instruments so
        # incoming provider-symbol quotes resolve to the same canonical
        # state (best-effort; never blocks the subscription).
        metadata: dict[str, tuple[str, str]] = {}
        try:
            for row in _instrument_catalog.search(q=token, limit=10):
                if row.get("instrument_token") == token or \
                        row.get("tradingsymbol") == token:
                    _identity_registry.register_from_catalog_row(
                        row, primary=token)
                    # Feed tick normalization needs (exchange,
                    # tradingsymbol) for runtime keys — without it every
                    # tick is dropped as unknown instrument.
                    ex = row.get("exchange")
                    sym = row.get("tradingsymbol")
                    if ex and sym:
                        metadata[token] = (str(ex), str(sym))
                    break
        except Exception:
            logger.warning("runtime identity registration failed for %s",
                           token)
        feed = _feed_ref.get("feed")
        if feed is not None and hasattr(feed, "add_instruments"):
            await feed.add_instruments([token], metadata=metadata or None)

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
from api.routes import build_settings_routes
from app.secrets_store import (
    CredentialDecryptError as _CredentialDecryptError,
)
from app.secrets_store import (
    CredentialStore as _CredentialStore,
)

_credential_store = _CredentialStore(
    _store, data_dir=PROJECT_ROOT / DATA_DIR)

# --- Fyers source wiring (requires the credential store above) -------------
# Centralized OAuth callback URL: ONE source of truth, derived from the
# explicit operator-configured public_base_url (never from request Host).
FYERS_REDIRECT_URI = oauth_callback_url(
    get_public_base_url(_config), "fyers")
_inject_fyers_source_config(SOURCES_CFG)

# ── Upstox durable session restore (before source-manager construction) ─────
# Owned by app/auth/: UpstoxAuthService restores the persisted session into
# SOURCES_CFG so the feed registers with a usable token and restart does NOT
# force re-login. Expired sessions are cleared. Never raises: broker failure
# must not make MarketHub itself unreliable.
try:
    from app.auth.startup import build_auth_service as _build_auth_svc
    _bootstrap_auth = _build_auth_svc(_credential_store)
    _upx_out = _bootstrap_auth.upstox.restore_session(SOURCES_CFG)
    _broker_restore["upstox_restored"] = bool(_upx_out.restored)
    _broker_restore["upstox_last_status"] = _upx_out.reason
    if _upx_out.restored:
        _app_logger.info("upstox session restored from encrypted store")
    elif _upx_out.reason not in ("no_session",):
        _app_logger.info("upstox stored session (%s)", _upx_out.reason)
except Exception:
    _app_logger.exception("upstox session restore failed")
    _broker_restore["upstox_restored"] = False
    _broker_restore["upstox_last_status"] = "restore_error"

_source_manager = _build_source_manager()

# Hold a reference to the Upstox feed for runtime auth management.
_feed_ref: dict[str, Any] = {"feed": None}
_upstox_source_name: str | None = None
for _src_name, _src in _source_manager.enabled_sources.items():
    if hasattr(_src, "update_credentials"):
        _feed_ref["feed"] = _src
        _upstox_source_name = _src_name
        break

# Track the Fyers source name so the OAuth callback can (re)start it.
_fyers_source_name: str | None = None
for _src_name, _src in _source_manager.enabled_sources.items():
    if _src_name == "fyers" or getattr(_src, "__class__", None).__name__ == "FyersFeed":
        _fyers_source_name = _src_name
        break

# ── Dedicated auth subsystem (single owner of broker session lifecycle) ─────
# Startup conceptually does: auth_service.restore_sessions() -> start feeds ->
# start remaining services. Broker-specific logic lives in app/auth/.
try:
    from app.auth.startup import build_auth_service as _build_full_auth
    _auth_service = _build_full_auth(
        _credential_store,
        upstox_feed_provider=lambda: _feed_ref.get("feed"),
        # Late binding: _restart_upstox_source is defined below.
        upstox_restart_fn=lambda: _restart_upstox_source(),
        fyers_runtime_auth=_fyers_runtime_auth,
        fyers_redirect_uri=FYERS_REDIRECT_URI,
    )
except Exception:
    _app_logger.exception("auth service construction failed")
    _auth_service = None

# ── Diagnostics runner ───────────────────────────────────────────────────────
from app.diagnostics import DiagnosticsRunner as _DiagnosticsRunner
_diagnostics_runner = _DiagnosticsRunner(
    base_url=f"http://{LISTEN_HOST}:{LISTEN_PORT}",
    mcp_url=f"http://{LISTEN_HOST}:{LISTEN_PORT}/mcp",
    provider_market_data=_provider_market_data,
    news_service=_news_service,
    source_manager=_source_manager,
)

# Wire lifecycle state-change broadcasting into every source (requires the
# source manager constructed just above).
for _src in _source_manager.enabled_sources.values():
    try:
        _src.on_state_change = _on_source_state_change
    except Exception:  # non-feed sources may not accept the attribute
        _app_logger.debug(
            "source %s does not support on_state_change", getattr(_src, "name", "?"))

# One-time startup diagnostic header (safe values only — no credentials,
# no config secret values).
log_startup_diagnostics(
    _app_logger,
    version=__version__,
    source_names=_source_manager.enabled_sources.keys(),
    listen_host=LISTEN_HOST,
    listen_port=LISTEN_PORT,
    log_file=_LOG_FILE,
)

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
    if _upstox_source_name is None:
        _app_logger.warning(
            "oauth restart skipped: no upstox source registered")
        return
    try:
        await _source_manager.restart_source(_upstox_source_name)
    except Exception:
        _app_logger.exception("oauth restart of upstox source failed")
        raise


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
    instrument_catalog=_instrument_catalog,
    provider_market_data=_provider_market_data,
    alert_engine=_alert_engine,
    condition_alert_engine=_condition_alert_engine,
    condition_identity_resolver=_identity_resolver,
    identity_resolver=_identity_registry,
    analytics_service=_analytics_service,
)


# Unified market-intelligence service: ONE implementation backing WebUI
# search, REST routes, MCP tools, and the Chat agent.
def _intel_spot(exchange: str, instrument_token: str):
    return _market_service.get_quote_now(exchange, instrument_token)


_market_intel = _MarketIntel(
    _instrument_catalog, spot_provider=_intel_spot,
    identity_resolver=_identity_registry)
_services.market_intel = _market_intel

# ── DB-backed market-data subscriptions (single policy owner) ────────────────
# DB stores user preferences; canonical definitions stay in code
# (app.market_indices + instruments catalog). The resolver converts
# preferences into concrete provider keys; reconcile() applies diffs to the
# live feeds without restart. Legacy config.json instruments are imported
# once (idempotent) and then deprecated for runtime ownership.
from app.subscriptions import SubscriptionService as _SubscriptionService


def _upstox_feed_for_subscriptions(provider: str | None = None):
    """Feed lookup for the subscription reconciler: Upstox by name, else None."""
    if provider not in (None, "upstox"):
        return None
    return _feed_ref.get("feed")


def _feed_for_provider(name: str):
    """Dual-provider feed lookup (Upstox + Fyers) for subscription apply."""
    if name == "upstox":
        return _upstox_feed_for_subscriptions(name)
    if name == "fyers":
        return _source_manager.enabled_sources.get("fyers")
    return None


_subscription_service = _SubscriptionService(
    _store, _instrument_catalog, spot_provider=_intel_spot)
try:
    _subscription_service.ensure_defaults()
    _sub_migration = _subscription_service.migrate_from_config(SOURCES_CFG)
    _app_logger.info(
        "market-data subscriptions: %d indices seeded, migration %s",
        len(_subscription_service.preferences()["indices"]), _sub_migration)
except Exception:
    _app_logger.exception("subscription bootstrap failed")

# ── N1: News & Sentiment service ────────────────────────────────────────────
from news.service import NewsService as _NewsService
from news.adapters.rss import RSSAdapter as _RSSAdapter
from news.adapters.reddit import RedditAdapter as _RedditAdapter

_news_service = _NewsService(store=_store)
_news_service.register_adapter(_RSSAdapter())
_news_service.register_adapter(_RedditAdapter())

# Seed default sources on startup (idempotent — deleted defaults stay deleted).
_news_cfg = _config.get("news", {})
try:
    _news_service.set_retention_days(int(_news_cfg.get("retention_days", 30)))
except Exception:
    _app_logger.warning("news retention config failed", exc_info=True)
if _news_cfg.get("enabled", True):
    _defaults = _news_cfg.get("default_sources", [])
    if _defaults:
        try:
            _news_service.seed_defaults(_defaults)
        except Exception:
            _app_logger.warning("news default seed failed", exc_info=True)

_services.news_service = _news_service

# Chat tool registry: same services, same semantics as REST/MCP.
_chat_tools = _ChatToolRegistry(
    market_intel=_market_intel,
    market_service=_market_service,
    store=_store,
    alert_engine=_alert_engine)

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
register_alert_tools(mcp, _services)
register_market_intel_tools(mcp, _services)
register_options_analytics_tools(mcp, _services)
register_market_alert_tools(mcp, _services)
register_condition_alert_tools(mcp, _services)

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

async def _health_check(request: Request) -> JSONResponse:
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
async def _lifespan(app: Starlette) -> None:
    """
    Top-level lifespan owner.

    Delegates to the MCP SDK session manager via the SDK-supported pattern
    (async with session_manager.run(): yield).  The SDK, in turn, calls the
    application lifespan (_lifespan passed to MCPServer) so source managers
    and background tasks start/stop correctly.
    """
    # Restore a Fyers access token from the stored refresh token BEFORE the
    # SDK starts sources, so an enabled Fyers feed is READY without re-login.
    await _try_restore_fyers_token()

    # Auto-register + start a Fyers feed when a usable session already exists
    # (restored access token), so an authenticated Fyers broker runs without a
    # manual enable click and without editing config.json. Registration is
    # idempotent and writes a secret-free durable source config.
    if _fyers_runtime_auth.has_access_token():
        try:
            await _start_fyers_source()
        except Exception:
            _app_logger.warning("fyers source auto-start failed", exc_info=True)

    # Start the analytics scheduler.
    try:
        await _analytics_service.start(_bg_task_manager)
        # Reconstruct active chains from persisted enabled analytics alerts.
        def _load_enabled():
            return _store.load_enabled_condition_alerts()
        _analytics_service.reconstruct_from_alerts(_load_enabled)
    except Exception:
        _app_logger.warning("analytics service startup failed", exc_info=True)

    # Reconcile DB subscription preferences into the live feeds (startup
    # apply — desired set restored after every restart, feed failures
    # never roll preferences back).
    try:
        await _subscription_service.reconcile(_feed_for_provider)
    except Exception:
        _app_logger.warning("subscription reconcile failed", exc_info=True)

    async with mcp_asgi_app.router.lifespan_context(app):
        yield

    # Stop the analytics scheduler.
    try:
        await _analytics_service.stop(_bg_task_manager)
    except Exception:
        _app_logger.warning("analytics service shutdown failed", exc_info=True)


def _auth_health_snapshot_fn() -> dict[str, Any]:
    """Broker auth snapshots for the diagnostics Test Center (observes only).

    Reads through the same service methods as the Settings UI, so the
    Test Center can never disagree with it. Genuine-rejection invalidation
    semantics are identical to a UI status poll (transient feed problems
    never invalidate); the endpoint itself performs no writes.
    """
    out: dict[str, Any] = {}
    if _auth_service is None:
        return out
    try:
        out["upstox"] = _auth_service.upstox.status(
            SOURCES_CFG, _broker_restore)
    except Exception:
        out["upstox"] = {"error": "unavailable"}
    try:
        out["fyers"] = _auth_service.fyers.status_snapshot(_broker_restore)
    except Exception:
        out["fyers"] = {"error": "unavailable"}
    return out


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
        identity_resolver=_identity_registry,
        index_catalog=_instrument_catalog,
        subscriptions=_subscription_service,
        feed_provider=_feed_for_provider,
        # Merged view: source status + task liveness + exit forensics, so the
        # UI can distinguish "streaming" from "dead task with stale state".
        source_status_fn=lambda: [
            dict(info, name=name) if isinstance(info, dict) else {"name": name}
            for name, info in _source_manager.get_status().items()
        ],
    )
    + build_source_control_routes(_source_manager)
    + build_auth_routes(
        _feed_ref,
        restart_fn=_restart_upstox_source,
        oauth=_oauth_cfg_ref,
        rest=_oauth_rest,
        cred_store=_credential_store,
        sources_cfg=SOURCES_CFG,
        restore_state=_broker_restore,
        auth_service=(_auth_service.upstox if _auth_service is not None
                      else None),
    )
    + build_settings_routes(
        _oauth_cfg_ref,
        cred_store=_credential_store,
        source_manager=_source_manager,
        config_path=str(CONFIG_PATH),
        sources_cfg=SOURCES_CFG,
    )
    + _build_intel_routes(_market_intel, provider_md=_provider_market_data)
    + _build_instrument_routes(_instrument_catalog, store=_store)
    + _build_watchlist_routes(_store, subscription=_feed_subscription)
    + _build_watchlist_portability_routes(_store)
    + _build_alert_routes(_store, _alert_engine)
    + _build_alert_history_routes(_store)
    + _build_market_data_routes(_provider_market_data)
    + _build_subscription_routes(
        _subscription_service, _feed_for_provider)
    + _build_fno_routes(
        _instrument_catalog, subscriptions=_subscription_service,
        market_service=_market_service, feed_provider=_feed_for_provider,
        provider_md=_provider_market_data)
    + _build_admin_routes(_store, PROJECT_ROOT / DATA_DIR)
    + _build_fyers_auth_routes(
        _credential_store,
        runtime_auth=_fyers_runtime_auth,
        restart_fn=_restart_fyers_source,
        redirect_uri=FYERS_REDIRECT_URI,
        restore_state=_broker_restore,
        source_manager=_source_manager,
        sources_cfg=SOURCES_CFG,
        register_source_fn=_ensure_fyers_source_registered,
        start_source_fn=_start_fyers_source,
        persist_source_fn=_persist_fyers_source_config,
        auth_service=(_auth_service.fyers if _auth_service is not None
                      else None),
    )
    + _build_app_settings_routes(str(CONFIG_PATH))
    + _build_chat_routes(str(CONFIG_PATH), _credential_store, _chat_tools)
    + _build_diagnostics_routes(
        __version__,
        _store,
        lambda: [
            dict(info, name=name) if isinstance(info, dict) else {"name": name}
            for name, info in _source_manager.get_status().items()
        ],
        lambda: get_public_base_url(_config),
        # Read-only broker auth-health (Test Center observes; never mutates).
        auth_status_fn=_auth_health_snapshot_fn,
    )
    + _build_api_meta_routes()
    + _build_ai_alert_routes(_store, mcp)
    + _build_log_routes(_log_buffer, _log_sse_broker)
    + _build_news_routes(_news_service)
    + _build_diag_routes(_diagnostics_runner)
    + [Mount("/ui", app=StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "dist"), html=True),
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
        _app_logger.error(f"unexpected error during server run: {exc}", exc)
        print_shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()






