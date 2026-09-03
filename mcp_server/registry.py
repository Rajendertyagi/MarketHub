"""Canonical MCP Tool Registry -- single source of truth.

Every public MCP tool is defined here once. MCP tool handlers import
descriptions/schemas from this module. The WebUI API serves this data
to the browser. No description is duplicated outside this file.
"""
from __future__ import annotations
from typing import Any


TOOLS: list[dict[str, Any]] = [
    {
        "name": "system_ping",
        "display": "System Ping",
        "category": "System",
        "description": "Check whether the MCP server is running.",
        "params": [
        ],
        "examples": [{}],
        "notes": 'Returns server status. No parameters.',
        "status": "active",
    },
    {
        "name": "event_list",
        "display": "List Events",
        "category": "Events",
        "description": "List recent events from the in-memory history buffer. This is a diagnostics/observational journal of events that passed through the server -- it is NOT a durable replay source.",
        "params": [
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Maximum number of events to return (default 10, max 50)"},
        ],
        "examples": [{'limit': 20}],
        "notes": 'In-memory only; events rotate out.',
        "status": "active",
    },
    {
        "name": "consumer_register",
        "display": "Register Consumer",
        "category": "Consumer",
        "description": "Register a consumer identity. Idempotent -- safe to call repeatedly.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity string"},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": 'Safe to call multiple times.',
        "status": "active",
    },
    {
        "name": "consumer_topic_add",
        "display": "Add Consumer Topic",
        "category": "Consumer",
        "description": "Assign a topic to a consumer for topic-based routing.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity string"},
            {"name": "topic", "type": "str", "required": True, "default": None, "description": "Topic string to assign"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'topic': 'market_indices'}],
        "notes": 'Topics enable filtered event delivery.',
        "status": "active",
    },
    {
        "name": "consumer_event_pending_list",
        "display": "List Pending Events",
        "category": "Consumer",
        "description": "Replay pending (unacknowledged) persistent events for a consumer. This is the canonical durable replay tool.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity string"},
            {"name": "limit", "type": "int", "required": False, "default": 50, "description": "Max events to return"},
            {"name": "after_sequence", "type": "int", "required": False, "default": None, "description": "Pagination cursor: start after this sequence number"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'limit': 10}],
        "notes": 'Durable replay. Always ACK after processing.',
        "status": "active",
    },
    {
        "name": "consumer_event_acknowledge",
        "display": "Acknowledge Event",
        "category": "Consumer",
        "description": "Acknowledge that a consumer has successfully processed a persistent event. Idempotent.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity string"},
            {"name": "event_id", "type": "str", "required": True, "default": None, "description": "Event ID to acknowledge"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'event_id': 'abc123'}],
        "notes": 'Commit point. Idempotent. Advances checkpoint.',
        "status": "active",
    },
    {
        "name": "consumer_checkpoint_get",
        "display": "Get Consumer Checkpoint",
        "category": "Consumer",
        "description": "Get the current durable checkpoint position for a consumer.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity string"},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": 'Read-only. Returns sequence number + timestamp.',
        "status": "active",
    },
    {
        "name": "alert_create",
        "display": "Create Alert",
        "category": "Alerts",
        "description": "Create a generic alert definition for a consumer. one_shot: if true (default), the alert auto-disables after one trigger.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "source", "type": "str", "required": True, "default": None, "description": "Event source filter"},
            {"name": "field_path", "type": "str", "required": True, "default": None, "description": "Dot-notation field path in event data"},
            {"name": "operator", "type": "str", "required": True, "default": None, "description": "Comparison operator"},
            {"name": "value", "type": "any", "required": True, "default": None, "description": "Threshold value for comparison"},
            {"name": "name", "type": "str", "required": False, "default": None, "description": "Optional human-readable alert name"},
            {"name": "event_type", "type": "str", "required": False, "default": None, "description": "Optional event type filter"},
            {"name": "one_shot", "type": "bool", "required": False, "default": True, "description": "Auto-disable after one trigger"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'source': 'market', 'field_path': 'ltp', 'operator': 'gt', 'value': 25000, 'one_shot': False}],
        "notes": 'one_shot=True by default; set False for repeat alerts.',
        "status": "active",
    },
    {
        "name": "alert_list",
        "display": "List Alerts",
        "category": "Alerts",
        "description": "List alert definitions owned by a consumer.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity to list alerts for"},
            {"name": "enabled", "type": "bool", "required": False, "default": None, "description": "Filter by enabled state; null = all"},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "alert_get",
        "display": "Get Alert",
        "category": "Alerts",
        "description": "Get a single alert definition owned by a consumer.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Alert ID to retrieve"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Ownership enforced.',
        "status": "active",
    },
    {
        "name": "alert_enable",
        "display": "Enable Alert",
        "category": "Alerts",
        "description": "Enable a previously disabled alert.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Alert ID to enable"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Returns changed=true only if state actually changed.',
        "status": "active",
    },
    {
        "name": "alert_disable",
        "display": "Disable Alert",
        "category": "Alerts",
        "description": "Disable an alert (stops evaluation without deleting it).",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Alert ID to disable"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Stops evaluation. Does not delete.',
        "status": "active",
    },
    {
        "name": "market_quote",
        "display": "Market Quote",
        "category": "Market",
        "description": "Return the latest canonical quote for one instrument.",
        "params": [
            {"name": "instrument_ref", "type": "str", "required": True, "default": None, "description": "Canonical symbol, provider key, or registered alias"},
        ],
        "examples": [{'instrument_ref': 'RELIANCE'}],
        "notes": 'Instrument must exist in the catalog.',
        "status": "active",
    },
    {
        "name": "market_depth",
        "display": "Market Depth",
        "category": "Market",
        "description": "Return the latest market depth (L2 order book) for one instrument.",
        "params": [
            {"name": "instrument_ref", "type": "str", "required": True, "default": None, "description": "Canonical symbol, provider key, or registered alias"},
        ],
        "examples": [{'instrument_ref': 'NIFTY'}],
        "notes": 'Depth levels vary by data source.',
        "status": "active",
    },
    {
        "name": "market_status",
        "display": "Market Status",
        "category": "Market",
        "description": "Return MarketService diagnostic counters.",
        "params": [
        ],
        "examples": [{}],
        "notes": 'Diagnostics only. No parameters.',
        "status": "active",
    },
    {
        "name": "instrument_search",
        "display": "Search Instruments",
        "category": "Market",
        "description": "Search instruments by human-readable query.",
        "params": [
            {"name": "q", "type": "str", "required": True, "default": None, "description": "Human-readable search query"},
            {"name": "exchange", "type": "str", "required": False, "default": None, "description": "Filter by exchange"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Filter by expiry date"},
            {"name": "types", "type": "list[str]", "required": False, "default": None, "description": "Filter by instrument types"},
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Max results to return (default 10, max 50)"},
        ],
        "examples": [{'q': 'reliance'}, {'q': 'nifty 25000 ce', 'types': ['option']}],
        "notes": 'Always use this first to resolve any symbol.',
        "status": "active",
    },
    {
        "name": "watchlists",
        "display": "Watchlists",
        "category": "Market",
        "description": "List all persistent watchlists and their instruments.",
        "params": [
        ],
        "examples": [{}],
        "notes": 'Read-only.',
        "status": "active",
    },
    {
        "name": "market_history",
        "display": "Market History",
        "category": "Market",
        "description": "Return historical OHLCV candles for an instrument.",
        "params": [
            {"name": "instrument_ref", "type": "str", "required": True, "default": None, "description": "Canonical symbol, provider key, or registered alias"},
            {"name": "unit", "type": "str", "required": True, "default": None, "description": "Candle unit: minutes, hours, days, weeks, or months"},
            {"name": "interval", "type": "int", "required": True, "default": None, "description": "Number of units per candle"},
            {"name": "from_date", "type": "str", "required": True, "default": None, "description": "ISO date string (YYYY-MM-DD) start"},
            {"name": "to_date", "type": "str", "required": True, "default": None, "description": "ISO date string (YYYY-MM-DD) end"},
        ],
        "examples": [{'instrument_ref': 'RELIANCE', 'unit': 'days', 'interval': 1, 'from_date': '2026-08-01', 'to_date': '2026-09-01'}],
        "notes": 'Max range depends on data provider.',
        "status": "active",
    },
    {
        "name": "option_chain",
        "display": "Option Chain",
        "category": "Market",
        "description": "Return the option chain for an underlying.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol (e.g. NIFTY, RELIANCE)"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest available"},
            {"name": "window", "type": "int", "required": False, "default": 10, "description": "Strikes above/below ATM to include"},
        ],
        "examples": [{'underlying': 'NIFTY'}, {'underlying': 'RELIANCE', 'window': 5}],
        "notes": 'Analytics scoped to loaded window.',
        "status": "active",
    },
    {
        "name": "futures_contracts",
        "display": "Futures Contracts",
        "category": "Market",
        "description": "List available futures contracts for an underlying with expiries and lot size.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Filter to a specific expiry"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "market_alert_create",
        "display": "Create Market Alert",
        "category": "Market Alerts",
        "description": "Create a market price alert. operator is one of: gt, lt, crosses_above, crosses_below.",
        "params": [
            {"name": "instrument_query", "type": "str", "required": True, "default": None, "description": "Human symbol (e.g. NIFTY, RELIANCE)"},
            {"name": "operator", "type": "str", "required": True, "default": None, "description": "One of: gt, lt, crosses_above, crosses_below"},
            {"name": "threshold", "type": "float", "required": True, "default": None, "description": "Numeric threshold value"},
            {"name": "field", "type": "str", "required": False, "default": "ltp", "description": "Field to monitor: ltp, change_percent, volume"},
        ],
        "examples": [{'instrument_query': 'RELIANCE', 'operator': 'gt', 'threshold': 2500, 'field': 'ltp'}],
        "notes": 'Persistence confirmed before returning.',
        "status": "active",
    },
    {
        "name": "market_alert_list",
        "display": "List Market Alerts",
        "category": "Market Alerts",
        "description": "List all configured market alerts with their state.",
        "params": [
        ],
        "examples": [{}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "market_alert_enable",
        "display": "Enable Market Alert",
        "category": "Market Alerts",
        "description": "Enable a disabled market alert by id.",
        "params": [
            {"name": "alert_id", "type": "int", "required": True, "default": None, "description": "Market alert ID"},
        ],
        "examples": [{'alert_id': 42}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "market_alert_disable",
        "display": "Disable Market Alert",
        "category": "Market Alerts",
        "description": "Disable a market alert by id (it stops evaluating).",
        "params": [
            {"name": "alert_id", "type": "int", "required": True, "default": None, "description": "Market alert ID"},
        ],
        "examples": [{'alert_id': 42}],
        "notes": 'Stops evaluation. Does not delete.',
        "status": "active",
    },
    {
        "name": "market_alert_delete",
        "display": "Delete Market Alert",
        "category": "Market Alerts",
        "description": "Delete a market alert by id. Historical trigger records are preserved.",
        "params": [
            {"name": "alert_id", "type": "int", "required": True, "default": None, "description": "Market alert ID"},
        ],
        "examples": [{'alert_id': 42}],
        "notes": 'History preserved. Cannot be undone.',
        "status": "active",
    },
    {
        "name": "condition_alert_create",
        "display": "Create Condition Alert",
        "category": "Condition Alerts",
        "description": "Create a consumer-owned advanced market-condition alert. v1=single leaf, v2=nested group.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "condition", "type": "dict", "required": True, "default": None, "description": "Condition tree (v1 leaf or v2 group)"},
            {"name": "trigger_mode", "type": "str", "required": False, "default": "repeat", "description": "Trigger mode"},
            {"name": "name", "type": "str", "required": False, "default": None, "description": "Optional human-readable name"},
            {"name": "metadata", "type": "dict", "required": False, "default": None, "description": "Optional metadata dictionary"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'condition': {'condition_version': 1, 'metric': 'ltp', 'operator': 'gt', 'value': 25000}}],
        "notes": 'v1=single leaf, v2=nested group.',
        "status": "active",
    },
    {
        "name": "condition_alert_list",
        "display": "List Condition Alerts",
        "category": "Condition Alerts",
        "description": "List condition alerts owned by a consumer.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "enabled", "type": "bool", "required": False, "default": None, "description": "Filter by enabled state"},
            {"name": "limit", "type": "int", "required": False, "default": None, "description": "Max alerts to return (default 50, max 200)"},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "condition_alert_get",
        "display": "Get Condition Alert",
        "category": "Condition Alerts",
        "description": "Get one condition alert by id (ownership enforced).",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Condition alert ID"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Ownership enforced.',
        "status": "active",
    },
    {
        "name": "condition_alert_set_enabled",
        "display": "Enable/Disable Condition Alert",
        "category": "Condition Alerts",
        "description": "Enable or disable a condition alert. Enabling re-arms it.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Condition alert ID"},
            {"name": "enabled", "type": "bool", "required": True, "default": None, "description": "True to enable, false to disable"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123', 'enabled': True}],
        "notes": 'Re-enabling resets runtime state for once-mode alerts.',
        "status": "active",
    },
    {
        "name": "condition_alert_delete",
        "display": "Delete Condition Alert",
        "category": "Condition Alerts",
        "description": "Delete a condition alert (ownership enforced). History preserved.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Owning consumer identity"},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Condition alert ID"},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'History preserved. Cannot be undone.',
        "status": "active",
    },
    {
        "name": "compute_pcr",
        "display": "Put-Call Ratio",
        "category": "Compute",
        "description": "Put-Call Ratio from total open interest of the option chain (>1 = bearish).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": '>1 = bearish sentiment.',
        "status": "active",
    },
    {
        "name": "compute_max_pain",
        "display": "Max Pain",
        "category": "Compute",
        "description": "Strike where total option-writer payout is minimised (max pain theory).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Max pain theory -- not a guarantee.',
        "status": "active",
    },
    {
        "name": "compute_top_oi_strikes",
        "display": "Top OI Strikes",
        "category": "Compute",
        "description": "Strikes with the highest call OI and highest put OI (key battle levels).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
            {"name": "n", "type": "int", "required": False, "default": 5, "description": "Number of top strikes to return"},
        ],
        "examples": [{'underlying': 'NIFTY', 'n': 3}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "compute_atm",
        "display": "ATM Strike",
        "category": "Compute",
        "description": "At-the-money strike and the underlying spot used.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "compute_iv_skew",
        "display": "IV Skew",
        "category": "Compute",
        "description": "IV skew: average OTM put IV minus average OTM call IV (negative = fear).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Negative = fear (put IV > call IV).',
        "status": "active",
    },
    {
        "name": "compute_oi_buildup",
        "display": "OI Buildup",
        "category": "Compute",
        "description": "Count of legs per buildup tag (Long/Short Buildup, Long Unwinding, ...).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "compute_support_resistance",
        "display": "Support/Resistance",
        "category": "Compute",
        "description": "Support = strike with max put OI; resistance = strike with max call OI.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'OI-based levels, not price-action.',
        "status": "active",
    },
    {
        "name": "compute_straddle",
        "display": "ATM Straddle",
        "category": "Compute",
        "description": "ATM straddle cost and its two breakeven levels.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": None,
        "status": "active",
    },
    {
        "name": "compute_gex",
        "display": "Gamma Exposure",
        "category": "Compute",
        "description": "Gamma Exposure proxy: net of (gamma * OI) across calls minus puts.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Proxy calculation. Positive = dealer long gamma.',
        "status": "active",
    },
    {
        "name": "compute_futures_basis",
        "display": "Futures Basis",
        "category": "Compute",
        "description": "Futures premium/discount vs spot for each expiry (cost-of-carry).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Requires live data. Empty result if unavailable.',
        "status": "active",
    },
    {
        "name": "price_long_straddle",
        "display": "Long Straddle",
        "category": "Pricing",
        "description": "Long straddle: buy ATM call + buy ATM put. Profits on big moves either way.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
            {"name": "strike", "type": "float", "required": False, "default": None, "description": "Optional override strike (defaults to ATM)"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Profits on high volatility / big moves.',
        "status": "active",
    },
    {
        "name": "price_long_strangle",
        "display": "Long Strangle",
        "category": "Pricing",
        "description": "Long strangle: buy OTM call + buy OTM put. Cheaper than a straddle, needs bigger move.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "call_strike", "type": "float", "required": True, "default": None, "description": "OTM call strike"},
            {"name": "put_strike", "type": "float", "required": True, "default": None, "description": "OTM put strike"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY', 'call_strike': 25500, 'put_strike': 24500}],
        "notes": 'Cheaper than straddle. Needs bigger move to profit.',
        "status": "active",
    },
    {
        "name": "price_bull_call_spread",
        "display": "Bull Call Spread",
        "category": "Pricing",
        "description": "Bull call spread: buy lower-strike call, sell higher-strike call. Capped upside.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "lower_strike", "type": "float", "required": True, "default": None, "description": "Lower strike (long call)"},
            {"name": "higher_strike", "type": "float", "required": True, "default": None, "description": "Higher strike (short call)"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY', 'lower_strike': 25000, 'higher_strike': 25500}],
        "notes": 'Bullish. Max profit = spread width - net debit.',
        "status": "active",
    },
    {
        "name": "price_bear_put_spread",
        "display": "Bear Put Spread",
        "category": "Pricing",
        "description": "Bear put spread: buy higher-strike put, sell lower-strike put. Capped downside.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "higher_strike", "type": "float", "required": True, "default": None, "description": "Higher strike (long put)"},
            {"name": "lower_strike", "type": "float", "required": True, "default": None, "description": "Lower strike (short put)"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY', 'higher_strike': 25000, 'lower_strike': 24500}],
        "notes": 'Bearish. Max profit = spread width - net debit.',
        "status": "active",
    },
    {
        "name": "price_iron_condor",
        "display": "Iron Condor",
        "category": "Pricing",
        "description": "Iron condor: sell OTM put, buy lower put, buy OTM call, sell higher call.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "put_sell_strike", "type": "float", "required": True, "default": None, "description": "Short put strike"},
            {"name": "put_buy_strike", "type": "float", "required": True, "default": None, "description": "Long put strike (lower)"},
            {"name": "call_buy_strike", "type": "float", "required": True, "default": None, "description": "Long call strike"},
            {"name": "call_sell_strike", "type": "float", "required": True, "default": None, "description": "Short call strike (higher)"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY', 'put_sell_strike': 24500, 'put_buy_strike': 24000, 'call_buy_strike': 25500, 'call_sell_strike': 26000}],
        "notes": 'Range-bound strategy. Max profit = net premium received.',
        "status": "active",
    },
    {
        "name": "price_long_butterfly",
        "display": "Long Butterfly",
        "category": "Pricing",
        "description": "Long butterfly: buy lower call, sell 2 middle calls, buy upper call.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "lower_strike", "type": "float", "required": True, "default": None, "description": "Lower wing strike"},
            {"name": "middle_strike", "type": "float", "required": True, "default": None, "description": "Middle (body) strike"},
            {"name": "upper_strike", "type": "float", "required": True, "default": None, "description": "Upper wing strike"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
        ],
        "examples": [{'underlying': 'NIFTY', 'lower_strike': 24500, 'middle_strike': 25000, 'upper_strike': 25500}],
        "notes": 'Profits at expiry near middle strike.',
        "status": "active",
    },
    {
        "name": "analyze_option_chain",
        "display": "Analyze Option Chain",
        "category": "Analytics",
        "description": "One-call option-chain analysis: 7 derived analytics (PCR, max pain, ATM, support/resistance, OI buildup, IV skew, GEX) over the FULL chain.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Underlying symbol"},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry date; defaults to nearest"},
            {"name": "max_strikes", "type": "int", "required": False, "default": None, "description": "Max strikes to include in embedded chain view"},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'All 7 analytics in one call.',
        "status": "active",
    },
]

# -- Validation --
_names = [t["name"] for t in TOOLS]
assert len(_names) == len(set(_names)), f"Duplicate: {[n for n in _names if _names.count(n) > 1]}"

def categories() -> list[str]:
    """Return ordered unique category list."""
    seen: set[str] = set()
    result: list[str] = []
    for t in TOOLS:
        c = t["category"]
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result

def by_category() -> dict[str, list[dict]]:
    """Group tools by category."""
    result: dict[str, list[dict]] = {}
    for t in TOOLS:
        result.setdefault(t["category"], []).append(t)
    return result

def search(query: str) -> list[dict]:
    """Case-insensitive search across name, display, description."""
    q = query.lower()
    return [t for t in TOOLS
            if q in t["name"].lower()
            or q in t["display"].lower()
            or q in t["description"].lower()]

def get_by_name(name: str) -> dict | None:
    """Lookup a tool by its MCP name."""
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None


def get_tool_description(name: str) -> str:
    """Return the canonical description for a tool by its MCP name.

    Used by tool handlers to pull description from the registry so that
    MCP clients receive the same metadata as the WebUI API.
    """
    t = get_by_name(name)
    if t is None:
        return ""
    return t["description"]


def get_input_schema(tool: dict) -> dict:
    """Convert registry params to JSON Schema for MCP wire format."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for p in tool.get("params", []):
        prop: dict[str, Any] = {"type": p["type"], "description": p["description"]}
        if p["default"] is not None:
            prop["default"] = p["default"]
        properties[p["name"]] = prop
        if p["required"]:
            required.append(p["name"])
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
