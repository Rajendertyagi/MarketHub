"""
Public MCP contract identifiers — single source of truth for external-facing names.

All production code MUST import from this module rather than redefining literals.
External clients depend on these exact values, not on internal Python names.

Ownership model:
    MCP public contract identifiers  -> this module
    Runtime/deployment defaults       -> app/config.py
    Schema/migration version          -> store_modules/schema.py
    Domain exceptions                 -> errors.py
    Event-core behavior               -> events.py
    Source type registry              -> sources/registry.py
"""

from __future__ import annotations

# ─── Contract version ─────────────────────────────────────────────────────────
# Bumps only when the MCP tool/resource/event contract changes in a breaking way.
# Distinct from MCP spec version, app version, Python version, and SDK version.
CONTRACT_VERSION = "2.0.0"

# ─── Resource URIs ─────────────────────────────────────────────────────────────
# mcp-event:// is an APPLICATION-PRIVATE URI scheme. It is NOT an official
# MCP-defined scheme. MCP permits application-defined resource URIs subject to
# valid URI syntax (RFC 3986). Do not describe it as standardized by MCP.
RESOURCE_EVENT_LATEST = "mcp-event://events/latest"
RESOURCE_EVENTS_PENDING = "mcp-event://events/pending"
RESOURCE_SYSTEM_INFO = "mcp-event://system/info"
RESOURCE_SOURCES_STATUS = "mcp-event://sources/status"
RESOURCE_SYSTEM_METRICS = "mcp-event://system/metrics"
RESOURCE_EVENTS_RECENT = "mcp-event://events/recent"

# ─── Production tool names ─────────────────────────────────────────────────────
TOOL_SYSTEM_PING = "system_ping"

TOOL_EVENT_PUBLISH = "event_publish"
TOOL_EVENT_LIST = "event_list"

TOOL_CONSUMER_REGISTER = "consumer_register"
TOOL_CONSUMER_TOPIC_ADD = "consumer_topic_add"
TOOL_CONSUMER_EVENT_LIST = "consumer_event_list"
TOOL_CONSUMER_EVENT_PENDING_LIST = "consumer_event_pending_list"
TOOL_CONSUMER_EVENT_ACKNOWLEDGE = "consumer_event_acknowledge"
TOOL_CONSUMER_CHECKPOINT_GET = "consumer_checkpoint_get"

# ─── Alert tool names (v1.1.0-candidate) ───────────────────────────────────────
TOOL_ALERT_CREATE = "alert_create"
TOOL_ALERT_LIST = "alert_list"
TOOL_ALERT_GET = "alert_get"
TOOL_ALERT_ENABLE = "alert_enable"
TOOL_ALERT_DISABLE = "alert_disable"

# ─── Dev/test tool names (REMOVED from public contract in v2.0.0) ──────────────
# These tools were internal testing helpers and pollute the public tool surface.
# Removed: dev_progress_test, dev_long_running_test, dev_background_publish_test,
#          dev_task_list, dev_source_start, dev_source_fail, dev_source_stop

# ─── Market data tools (read-only) ─────────────────────────────────────────
TOOL_MARKET_QUOTE = "market_quote"
TOOL_MARKET_DEPTH = "market_depth"
TOOL_MARKET_STATUS = "market_status"
TOOL_INSTRUMENT_SEARCH = "instrument_search"
TOOL_WATCHLISTS = "watchlists"
TOOL_MARKET_HISTORY = "market_history"
TOOL_OPTION_CHAIN = "option_chain"
TOOL_FUTURES_CONTRACTS = "futures_contracts"
TOOL_MARKET_ALERT_CREATE = "market_alert_create"
TOOL_MARKET_ALERT_LIST = "market_alert_list"
TOOL_MARKET_ALERT_ENABLE = "market_alert_enable"
TOOL_MARKET_ALERT_DISABLE = "market_alert_disable"
TOOL_MARKET_ALERT_DELETE = "market_alert_delete"

# ─── Options analytics tools (derived, provider-agnostic) ────────────────────
# Locally computed over the canonical OptionChainSnapshot — no extra broker API
# calls. Ported from TBMCP's 17 derived-analytics tools.
TOOL_COMPUTE_PCR = "compute_pcr"
TOOL_COMPUTE_MAX_PAIN = "compute_max_pain"
TOOL_COMPUTE_TOP_OI_STRIKES = "compute_top_oi_strikes"
TOOL_COMPUTE_ATM = "compute_atm"
TOOL_COMPUTE_IV_SKEW = "compute_iv_skew"
TOOL_COMPUTE_OI_BUILDUP = "compute_oi_buildup"
TOOL_COMPUTE_SUPPORT_RESISTANCE = "compute_support_resistance"
TOOL_COMPUTE_STRADDLE = "compute_straddle"
TOOL_COMPUTE_GEX = "compute_gex"
TOOL_COMPUTE_FUTURES_BASIS = "compute_futures_basis"
TOOL_PRICE_LONG_STRADDLE = "price_long_straddle"
TOOL_PRICE_LONG_STRANGLE = "price_long_strangle"
TOOL_PRICE_BULL_CALL_SPREAD = "price_bull_call_spread"
TOOL_PRICE_BEAR_PUT_SPREAD = "price_bear_put_spread"
TOOL_PRICE_IRON_CONDOR = "price_iron_condor"
TOOL_PRICE_LONG_BUTTERFLY = "price_long_butterfly"
TOOL_ANALYZE_OPTION_CHAIN = "analyze_option_chain"

