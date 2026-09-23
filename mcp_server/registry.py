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
        "description": "Health check: is the MarketHub MCP server running? Call this FIRST before any other tool. Takes no arguments. Use it to verify connectivity, not to fetch market data.",
        "params": [
        ],
        "examples": [{}],
        "notes": 'Returns server status. No parameters. Safe to call anytime. If this fails, do not call other tools -- the server is down.',
        "status": "active",
    },
    {
        "name": "event_list",
        "display": "List Events",
        "category": "Events",
        "description": "DIAGNOSTICS ONLY. List recent events from the in-memory history buffer (observational journal). NOT a durable replay source -- events rotate out. For guaranteed delivery use consumer_event_pending_list instead.",
        "params": [
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Max events to return (1-50, default 10). Nothing else is accepted."},
        ],
        "examples": [{'limit': 20}],
        "notes": 'In-memory only; events rotate out. Never use for alert delivery or acknowledgement workflows.',
        "status": "active",
    },
    {
        "name": "consumer_register",
        "display": "Register Consumer",
        "category": "Consumer",
        "description": "STEP 1 of any alert workflow: register a consumer identity (e.g. 'my-bot'). Idempotent -- safe to call repeatedly with the same id. You MUST call this before alert_create, consumer_topic_add, replay tools, or condition_alert_* tools, because all of them require an existing consumer_id.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Consumer identity string, e.g. 'my-bot'. Non-empty after trimming. Reuse the SAME id in all later calls."},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": 'Safe to call multiple times. Do this first; every alert/replay tool needs the consumer to exist.',
        "status": "active",
    },
    {
        "name": "consumer_topic_add",
        "display": "Add Consumer Topic",
        "category": "Consumer",
        "description": "Assign a routing topic to an EXISTING consumer. The consumer MUST already be registered via consumer_register or this fails with consumer-not-found. Both consumer_id and topic are required non-empty strings.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id, e.g. 'my-bot'. Must exist first."},
            {"name": "topic", "type": "str", "required": True, "default": None, "description": "Topic string to assign, e.g. 'market_indices'. Non-empty; never omit or send empty."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'topic': 'market_indices'}],
        "notes": 'Topics enable filtered event delivery. Register the consumer first.',
        "status": "active",
    },
    {
        "name": "consumer_event_pending_list",
        "display": "List Pending Events",
        "category": "Consumer",
        "description": "Durable replay: list unacknowledged persistent events for a consumer, in sequence order. This is the CANONICAL replay tool -- use it, not event_list. Replay does NOT acknowledge and does NOT advance the checkpoint. consumer_id must be already registered.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id, e.g. 'my-bot'."},
            {"name": "limit", "type": "int", "required": False, "default": 50, "description": "Max events to return (positive int, capped server-side)."},
            {"name": "after_sequence", "type": "int", "required": False, "default": None, "description": "Pagination cursor: non-negative int or null. Null starts from the durable checkpoint."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'limit': 10}],
        "notes": 'Durable replay. Always ACK each event after processing via consumer_event_acknowledge.',
        "status": "active",
    },
    {
        "name": "consumer_event_acknowledge",
        "display": "Acknowledge Event",
        "category": "Consumer",
        "description": "Acknowledge ONE event returned by consumer_event_pending_list, only AFTER fully processing it. Idempotent. Needs the EXACT event_id from pending_list -- inventing an id fails. If pending_list is empty (0 events) there is nothing to ACK; do not call this.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id that owns the event."},
            {"name": "event_id", "type": "str", "required": True, "default": None, "description": "Exact event id from consumer_event_pending_list. Non-empty; never invent."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'event_id': 'abc123'}],
        "notes": 'Commit point. Idempotent. Advances checkpoint. Nothing to ACK when pending_list is empty.',
        "status": "active",
    },
    {
        "name": "consumer_checkpoint_get",
        "display": "Get Consumer Checkpoint",
        "category": "Consumer",
        "description": "READ ONLY. Get the durable replay position (sequence + timestamp) for an EXISTING consumer. Never modifies anything. Use to check progress, not to replay -- replay via consumer_event_pending_list.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id, e.g. 'my-bot'."},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": 'Read-only. Returns sequence number + timestamp.',
        "status": "active",
    },
    {
        "name": "alert_create",
        "display": "Create Alert",
        "category": "Alerts",
        "description": "GENERIC event alert (NOT a price alert). Fires when a published event from 'source' satisfies 'field_path operator value'. For SIMPLE price alerts use market_alert_create instead. Operator MUST be one of: eq, ne, gt, gte, lt, lte -- crosses_above/crosses_below are INVALID here and will fail. consumer MUST already be registered via consumer_register. gt/gte/lt/lte need a NUMERIC (non-bool) value. one_shot=true (default) auto-disables after one trigger.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id, e.g. 'my-bot'. Register first via consumer_register."},
            {"name": "source", "type": "str", "required": True, "default": None, "description": "Event source filter, e.g. 'market'. Non-empty string."},
            {"name": "field_path", "type": "str", "required": True, "default": None, "description": "Dot-notation field path in event data, e.g. 'ltp'. No empty components."},
            {"name": "operator", "type": "str", "required": True, "default": None, "description": "EXACTLY one of: eq, ne, gt, gte, lt, lte. Do NOT use crosses_above/crosses_below here."},
            {"name": "value", "type": "any", "required": True, "default": None, "description": "JSON scalar threshold. Ordering operators (gt/gte/lt/lte) need a number, not bool/dict/list."},
            {"name": "name", "type": "str", "required": False, "default": None, "description": "Optional human-readable alert name."},
            {"name": "event_type", "type": "str", "required": False, "default": None, "description": "Optional event type filter, non-empty string or null."},
            {"name": "one_shot", "type": "bool", "required": False, "default": True, "description": "Boolean only. True (default) = auto-disable after one trigger; False = repeat."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'source': 'market', 'field_path': 'ltp', 'operator': 'gt', 'value': 25000, 'one_shot': False}],
        "notes": 'NOT a price alert -- use market_alert_create for NIFTY/RELIANCE price alerts. Save the returned alert_id; get/enable/disable need it. one_shot=True by default; set False for repeat alerts.',
        "status": "active",
    },
    {
        "name": "alert_list",
        "display": "List Alerts",
        "category": "Alerts",
        "description": "List generic alert definitions owned by an EXISTING consumer. Only works after consumer_register + at least one alert_create. Unknown consumer fails -- use the SAME consumer_id you registered.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id whose alerts to list."},
            {"name": "enabled", "type": "bool", "required": False, "default": None, "description": "Filter: true = enabled only, false = disabled only, null/omit = all."},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": 'Lists generic alerts only -- NOT market_alert_* (different family). Empty list is valid when no alerts created yet.',
        "status": "active",
    },
    {
        "name": "alert_get",
        "display": "Get Alert",
        "category": "Alerts",
        "description": "Get ONE generic alert by its alert_id. You MUST first create it via alert_create and reuse the EXACT returned alert_id with the SAME consumer_id. Inventing an id or using another consumer's id fails with alert-not-found (ownership enforced).",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Same consumer_id that owns the alert."},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Exact alert_id returned by alert_create. Never invent."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Ownership enforced: cross-consumer access looks like not-found.',
        "status": "active",
    },
    {
        "name": "alert_enable",
        "display": "Enable Alert",
        "category": "Alerts",
        "description": "Enable a previously disabled generic alert. Needs the EXACT alert_id from alert_create plus the SAME owning consumer_id. Returns changed=true only if it was actually disabled.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Same consumer_id that owns the alert."},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Exact alert_id returned by alert_create."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Returns changed=true only if state actually changed. Different family from market_alert_enable.',
        "status": "active",
    },
    {
        "name": "alert_disable",
        "display": "Disable Alert",
        "category": "Alerts",
        "description": "Disable a generic alert (stops evaluation without deleting it). Needs the EXACT alert_id from alert_create plus the SAME owning consumer_id.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Same consumer_id that owns the alert."},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Exact alert_id returned by alert_create."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Stops evaluation. Does not delete. Different family from market_alert_disable.',
        "status": "active",
    },
    {
        "name": "market_quote",
        "display": "Market Quote",
        "category": "Market",
        "description": "READ ONLY. Latest canonical quote (ltp, OHLC, change, volume, bid/ask, greeks when available) for ONE instrument. No history, no depth. Start from instrument_search to get a valid instrument_ref.",
        "params": [
            {"name": "instrument_ref", "type": "str", "required": True, "default": None, "description": "From instrument_search: canonical symbol (RELIANCE), provider key (NSE_EQ|INE002A01018), or alias. Use the instrument_key the search returned."},
        ],
        "examples": [{'instrument_ref': 'RELIANCE'}],
        "notes": 'Instrument must exist in the catalog. Missing quote = no live data, not a broken tool.',
        "status": "active",
    },
    {
        "name": "market_depth",
        "display": "Market Depth",
        "category": "Market",
        "description": "READ ONLY. Latest L2 order book (bids/asks) for ONE instrument, from in-memory state only -- no provider fetch. INDICES (NIFTY, BANKNIFTY, MIDCPNIFTY, SENSEX, INDIA VIX) NEVER have depth: 'depth not found' for them is CORRECT, not a bug. Use only for equities/futures that publish a book.",
        "params": [
            {"name": "instrument_ref", "type": "str", "required": True, "default": None, "description": "Equity/future ref from instrument_search. Do NOT use index symbols (NIFTY etc.) -- they have no book."},
        ],
        "examples": [{'instrument_ref': 'RELIANCE'}],
        "notes": 'Depth levels vary by data source. Indices always return depth-not-found; that is expected.',
        "status": "active",
    },
    {
        "name": "market_status",
        "display": "Market Status",
        "category": "Market",
        "description": "DIAGNOSTICS ONLY. MarketService counters (quote/depth counts, accepted vs stale updates, feed health). Takes no arguments. Use to check data freshness, not prices.",
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
        "description": "STEP 1 for all market data: resolve a human query to canonical instrument_key values. Understands symbols (reliance), type words (reliance future, nifty futures), option descriptors (nifty 25000 ce). ALWAYS call this before market_quote/market_depth/market_history and use the returned instrument_key.",
        "params": [
            {"name": "q", "type": "str", "required": True, "default": None, "description": "Human query, e.g. 'reliance', 'nifty 25000 ce', 'banknifty'."},
            {"name": "exchange", "type": "str", "required": False, "default": None, "description": "Optional exchange filter, e.g. 'NSE'."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Optional expiry filter YYYY-MM-DD."},
            {"name": "types", "type": "list[str]", "required": False, "default": None, "description": "Optional type filter, e.g. ['option'], ['future'], ['equity'] (singular lowercase)."},
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Max results 1-50, default 10."},
        ],
        "examples": [{'q': 'reliance'}, {'q': 'nifty 25000 ce', 'types': ['option']}],
        "notes": 'Always use this first to resolve any symbol. Results carry instrument_key for other tools.',
        "status": "active",
    },
    {
        "name": "watchlists",
        "display": "Watchlists",
        "category": "Market",
        "description": "READ ONLY. List all persistent watchlists with their instruments. Takes no arguments. Use to discover what the user tracks before fetching quotes.",
        "params": [
        ],
        "examples": [{}],
        "notes": 'Read-only. No parameters.',
        "status": "active",
    },
    {
        "name": "market_history",
        "display": "Market History",
        "category": "Market",
        "description": "READ ONLY. Historical OHLCV candles for ONE instrument. Upstox supports ONLY these (unit,interval) pairs: (minutes,1), (minutes,30), (days,1), (weeks,1), (months,1) -- minutes/5, minutes/15, hours/* ALWAYS fail with 'upstox does not support'. Key shape picks provider: pipe key (NSE_EQ|...) = Upstox, colon key (NSE:...) or digits = Fyers. Dates YYYY-MM-DD.",
        "params": [
            {"name": "instrument_ref", "type": "str", "required": True, "default": None, "description": "From instrument_search. Pipe key (NSE_EQ|...) routes to Upstox; colon (NSE:...) or numeric routes to Fyers. Index aliases (NIFTY) may pick the wrong provider -- prefer the search-returned key."},
            {"name": "unit", "type": "str", "required": True, "default": None, "description": "EXACTLY one of: minutes, hours, days, weeks, months. Only minutes/1, minutes/30, days/1, weeks/1, months/1 work on Upstox."},
            {"name": "interval", "type": "int", "required": True, "default": None, "description": "Integer. Valid Upstox combos: minutes+1, minutes+30, days+1, weeks+1, months+1. Anything else fails."},
            {"name": "from_date", "type": "str", "required": True, "default": None, "description": "Start YYYY-MM-DD."},
            {"name": "to_date", "type": "str", "required": True, "default": None, "description": "End YYYY-MM-DD."},
        ],
        "examples": [{'instrument_ref': 'RELIANCE', 'unit': 'days', 'interval': 1, 'from_date': '2026-08-01', 'to_date': '2026-09-01'}],
        "notes": 'Max range depends on data provider. minutes/5, minutes/15, hours/* are unsupported upstream -- do not retry them.',
        "status": "active",
    },
    {
        "name": "option_chain",
        "display": "Option Chain",
        "category": "Market",
        "description": "READ ONLY. Catalog-driven option chain for an underlying (NIFTY, BANKNIFTY, RELIANCE): spot, ATM strike, strikes window with CE/PE legs. Always returns the listed ladder; live provider quotes are merged best-effort (missing quotes = empty fields, not failure). Use window to size the view; analytics here cover the loaded window only.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE, etc."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest available."},
            {"name": "window", "type": "int", "required": False, "default": 10, "description": "Strikes above/below ATM to include (default 10)."},
        ],
        "examples": [{'underlying': 'NIFTY'}, {'underlying': 'RELIANCE', 'window': 5}],
        "notes": 'Analytics scoped to loaded window. For FULL-chain derived analytics use analyze_option_chain or compute_* instead.',
        "status": "active",
    },
    {
        "name": "futures_contracts",
        "display": "Futures Contracts",
        "category": "Market",
        "description": "READ ONLY. Catalog discovery: listed futures expiries + contracts (lot size, canonical identity) for an underlying. No live prices -- use before fetching quotes or building strategies.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, RELIANCE, etc."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Optional expiry YYYY-MM-DD filter; omit = all expiries."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Discovery only -- no quotes. Unknown underlying returns not-listed, not a crash.',
        "status": "active",
    },
    {
        "name": "market_alert_create",
        "display": "Create Market Alert",
        "category": "Market Alerts",
        "description": "SIMPLEST price alert: 'tell me when SYMBOL field crosses threshold'. Use this for NIFTY/RELIANCE price alerts -- NOT alert_create (generic events) or condition_alert_create (advanced trees). Resolves the human symbol itself; persistence confirmed before returning. operator: gt = above, lt = below, crosses_above/crosses_below = edge-triggered.",
        "params": [
            {"name": "instrument_query", "type": "str", "required": True, "default": None, "description": "Human symbol: NIFTY, BANKNIFTY, RELIANCE. Resolved via instrument search."},
            {"name": "operator", "type": "str", "required": True, "default": None, "description": "EXACTLY one of: gt, lt, crosses_above, crosses_below."},
            {"name": "threshold", "type": "float", "required": True, "default": None, "description": "Numeric threshold, e.g. 25000 for NIFTY ltp."},
            {"name": "field", "type": "str", "required": False, "default": "ltp", "description": "One of: ltp (default), change_percent, volume, oi_change_percent."},
        ],
        "examples": [{'instrument_query': 'RELIANCE', 'operator': 'gt', 'threshold': 2500, 'field': 'ltp'}, {'instrument_query': 'NIFTY', 'operator': 'crosses_above', 'threshold': 25000}],
        "notes": 'Easiest alert path. Save the returned alert id for enable/disable/delete. Persistence confirmed before returning.',
        "status": "active",
    },
    {
        "name": "market_alert_list",
        "display": "List Market Alerts",
        "category": "Market Alerts",
        "description": "READ ONLY. List all configured market (price) alerts with state. Takes no arguments. Different family from alert_list (generic) and condition_alert_list (advanced).",
        "params": [
        ],
        "examples": [{}],
        "notes": 'Market alerts only. Use the returned integer alert_id for enable/disable/delete.',
        "status": "active",
    },
    {
        "name": "market_alert_enable",
        "display": "Enable Market Alert",
        "category": "Market Alerts",
        "description": "Enable a disabled MARKET alert by its INTEGER id from market_alert_list/market_alert_create. Not for generic alert_* or condition_alert_* ids.",
        "params": [
            {"name": "alert_id", "type": "int", "required": True, "default": None, "description": "Integer market alert ID from market_alert_list. Not a string, not another family's id."},
        ],
        "examples": [{'alert_id': 42}],
        "notes": 'Market family only. No-op if already enabled.',
        "status": "active",
    },
    {
        "name": "market_alert_disable",
        "display": "Disable Market Alert",
        "category": "Market Alerts",
        "description": "Disable a MARKET alert by its INTEGER id (stops evaluation, keeps the record). Not for generic alert_* or condition_alert_* ids.",
        "params": [
            {"name": "alert_id", "type": "int", "required": True, "default": None, "description": "Integer market alert ID from market_alert_list."},
        ],
        "examples": [{'alert_id': 42}],
        "notes": 'Stops evaluation. Does not delete.',
        "status": "active",
    },
    {
        "name": "market_alert_delete",
        "display": "Delete Market Alert",
        "category": "Market Alerts",
        "description": "Delete a MARKET alert by its INTEGER id. Historical trigger records are preserved. Cannot be undone.",
        "params": [
            {"name": "alert_id", "type": "int", "required": True, "default": None, "description": "Integer market alert ID from market_alert_list."},
        ],
        "examples": [{'alert_id': 42}],
        "notes": 'History preserved. Cannot be undone. Market family only.',
        "status": "active",
    },
    {
        "name": "condition_alert_create",
        "display": "Create Condition Alert",
        "category": "Condition Alerts",
        "description": "ADVANCED tree alert for one consumer. Use ONLY when market_alert_create is not enough (multi-leg, cross-instrument, greeks/analytics metrics). condition MUST be a v1 leaf {condition_version:1, metric, operator, value, instrument:{exchange + symbol|underlying+expiry...}} or v2 group {condition_version:2, logic:'all'|'any', conditions:[...]}. metric MUST be one of the 31 enums (ltp/open/high/low/close/change/change_percent/volume/open_interest/oi_change/best_bid/best_ask/spread/greeks.delta/greeks.gamma/greeks.theta/greeks.vega/greeks.rho/greeks.iv/pcr_oi/pcr_volume/max_pain/iv_skew/...). operator MUST be eq/ne/gt/gte/lt/lte/crosses_above/crosses_below. value MUST be a number (never bool). instrument MUST include exchange plus symbol (INDEX/EQUITY/ETF) or underlying+expiry (FUTURE) or underlying+expiry+strike+option_type CE|PE (OPTION). Analytics metrics (pcr_oi/pcr_volume/max_pain/iv_skew) REQUIRE instrument.expiry YYYY-MM-DD. Flat underlying-only shapes FAIL -- always send the full condition object.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id via consumer_register, e.g. 'my-bot'."},
            {"name": "condition", "type": "dict", "required": True, "default": None, "description": "Full v1 leaf or v2 group object (see description). Include instrument.exchange + symbol/underlying+expiry (+strike+option_type for options). Example leaf: {condition_version:1, metric:'ltp', operator:'gt', value:25000, instrument:{exchange:'NSE', symbol:'NIFTY'}}."},
            {"name": "trigger_mode", "type": "str", "required": False, "default": "repeat", "description": "EXACTLY 'repeat' (default) or 'once'."},
            {"name": "name", "type": "str", "required": False, "default": None, "description": "Optional human-readable name."},
            {"name": "metadata", "type": "dict", "required": False, "default": None, "description": "Optional metadata dict."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'condition': {'condition_version': 1, 'metric': 'ltp', 'operator': 'gt', 'value': 25000, 'instrument': {'exchange': 'NSE', 'symbol': 'NIFTY'}}}],
        "notes": 'Advanced only -- prefer market_alert_create for simple price alerts. v1=single leaf, v2=nested group (logic all|any, depth<=8, leaves<=64). Save returned alert_id for get/set_enabled/delete.',
        "status": "active",
    },
    {
        "name": "condition_alert_list",
        "display": "List Condition Alerts",
        "category": "Condition Alerts",
        "description": "READ ONLY. List advanced condition alerts owned by an EXISTING consumer. Different family from alert_list (generic) and market_alert_list (price).",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Already-registered consumer id."},
            {"name": "enabled", "type": "bool", "required": False, "default": None, "description": "true = enabled only, false = disabled only, omit = all."},
            {"name": "limit", "type": "int", "required": False, "default": None, "description": "Max alerts (default 50, max 200)."},
        ],
        "examples": [{'consumer_id': 'my-bot'}],
        "notes": 'Condition family only. Empty list is valid when none created yet.',
        "status": "active",
    },
    {
        "name": "condition_alert_get",
        "display": "Get Condition Alert",
        "category": "Condition Alerts",
        "description": "READ ONLY. Get ONE condition alert by alert_id. Needs the EXACT alert_id returned by condition_alert_create plus the SAME owning consumer_id. Inventing an id fails (ownership enforced).",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Same consumer_id that owns the alert."},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Exact alert_id from condition_alert_create. Never invent."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'Ownership enforced: cross-consumer access looks like not-found.',
        "status": "active",
    },
    {
        "name": "condition_alert_set_enabled",
        "display": "Enable/Disable Condition Alert",
        "category": "Condition Alerts",
        "description": "Enable (true) or disable (false) a condition alert. Needs EXACT alert_id + SAME consumer_id. enabled MUST be a real boolean, not a string. Re-enabling a once-mode alert re-arms it (runtime state reset to UNKNOWN).",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Same consumer_id that owns the alert."},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Exact alert_id from condition_alert_create."},
            {"name": "enabled", "type": "bool", "required": True, "default": None, "description": "Boolean true = enable, false = disable. Not 'true' string."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123', 'enabled': True}],
        "notes": 'Re-enabling resets runtime state for once-mode alerts. Ownership enforced.',
        "status": "active",
    },
    {
        "name": "condition_alert_delete",
        "display": "Delete Condition Alert",
        "category": "Condition Alerts",
        "description": "Delete a condition alert. Needs EXACT alert_id + SAME consumer_id (ownership enforced). Historical trigger records are preserved. Cannot be undone.",
        "params": [
            {"name": "consumer_id", "type": "str", "required": True, "default": None, "description": "Same consumer_id that owns the alert."},
            {"name": "alert_id", "type": "str", "required": True, "default": None, "description": "Exact alert_id from condition_alert_create."},
        ],
        "examples": [{'consumer_id': 'my-bot', 'alert_id': 'abc123'}],
        "notes": 'History preserved. Cannot be undone. Ownership enforced.',
        "status": "active",
    },
    {
        "name": "compute_pcr",
        "display": "Put-Call Ratio",
        "category": "Compute",
        "description": "READ ONLY. Put-Call Ratio = total put OI / total call OI over the FULL option chain for an underlying+expiry. >1 = put-heavy (bearish lean), <1 = call-heavy. Derived locally; needs a listed options expiry.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE. Only NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY + listed stocks work."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}, {'underlying': 'NIFTY', 'expiry': '2026-09-25'}],
        "notes": '>1 = bearish sentiment. Works on last-session chain when market is closed.',
        "status": "active",
    },
    {
        "name": "compute_max_pain",
        "display": "Max Pain",
        "category": "Compute",
        "description": "READ ONLY. Max-pain strike: the expiry level that minimises total option-writer payout (max pain theory -- pinning level, not a forecast). Derived over the FULL chain for an underlying+expiry.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Max pain theory -- not a guarantee. Works on last-session chain when closed.',
        "status": "active",
    },
    {
        "name": "compute_top_oi_strikes",
        "display": "Top OI Strikes",
        "category": "Compute",
        "description": "READ ONLY. Strikes with the highest call OI and highest put OI over the FULL chain (key battle/support-resistance levels). n caps how many each side to return.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
            {"name": "n", "type": "int", "required": False, "default": 5, "description": "Positive int: how many top strikes per side (default 5)."},
        ],
        "examples": [{'underlying': 'NIFTY', 'n': 3}],
        "notes": 'OI concentration levels, not price predictions.',
        "status": "active",
    },
    {
        "name": "compute_atm",
        "display": "ATM Strike",
        "category": "Compute",
        "description": "READ ONLY. At-the-money strike for an underlying+expiry: the snapshot's flagged ATM, else the nearest listed strike to spot. Returns atm_strike plus the spot used.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'ATM = listed strike nearest spot when no flagged ATM.',
        "status": "active",
    },
    {
        "name": "compute_iv_skew",
        "display": "IV Skew",
        "category": "Compute",
        "description": "READ ONLY. Volatility skew = average OTM put IV minus average OTM call IV over the FULL chain. Negative = puts pricier than calls (fear/hedging demand). Derived from chain IVs; missing IVs are skipped.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Negative = fear (put IV > call IV). Needs IVs in the chain.',
        "status": "active",
    },
    {
        "name": "compute_oi_buildup",
        "display": "OI Buildup",
        "category": "Compute",
        "description": "READ ONLY. OI-buildup census over the FULL chain: counts legs as Long Buildup (OI up + price up), Short Buildup (OI up + price down), Long Unwinding (OI down + price down), Short Covering (OI down + price up), Neutral otherwise. Derived from OI change + price change per leg.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Classification: OI direction x price direction per leg.',
        "status": "active",
    },
    {
        "name": "compute_support_resistance",
        "display": "Support/Resistance",
        "category": "Compute",
        "description": "READ ONLY. OI-based support/resistance: support = strike with the highest put OI; resistance = strike with the highest call OI over the FULL chain. Returns both strikes with their OI.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'OI-based levels, not price-action.',
        "status": "active",
    },
    {
        "name": "compute_straddle",
        "display": "ATM Straddle",
        "category": "Compute",
        "description": "READ ONLY. ATM straddle reference: cost = ATM call ltp + ATM put ltp, with upper/lower breakevens (strike +/- cost). Indicative pricing only -- for full P&L/max-profit/max-loss strategy math use price_long_straddle.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Cost + breakevens only. Use price_long_straddle for full strategy payoff.',
        "status": "active",
    },
    {
        "name": "compute_gex",
        "display": "Gamma Exposure",
        "category": "Compute",
        "description": "READ ONLY. Gamma-exposure proxy = sum(gamma x OI) over calls minus puts across the chain, with an interpretation line (positive = dealers long gamma/stabilising, negative = amplifying). Proxy metric -- no lot-size multiplier applied.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Proxy calculation. Positive = dealer long gamma.',
        "status": "active",
    },
    {
        "name": "compute_futures_basis",
        "display": "Futures Basis",
        "category": "Compute",
        "description": "READ ONLY. Futures premium/discount vs spot per expiry (cost-of-carry basis + basis %). REQUIRES live spot AND live futures quotes at call time: when the market is closed or no futures are subscribed it returns status ok with an EMPTY contracts list -- that is expected behaviour, not an error. Takes underlying only (no expiry param).",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, RELIANCE. Must have listed futures AND live quotes."},
        ],
        "examples": [{'underlying': 'NIFTY'}],
        "notes": 'Requires live data. Empty contracts list when market closed / no futures quoted -- do not retry as if broken.',
        "status": "active",
    },
    {
        "name": "price_long_straddle",
        "display": "Long Straddle",
        "category": "Pricing",
        "description": "STRATEGY PRICING (pure math over the live/last-session chain, no extra broker calls). Long straddle: buy ATM call + buy ATM put. Returns net_debit (negative = credit), max_profit, max_loss, breakevens and the priced legs. Profits from a big move either way.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
            {"name": "strike", "type": "float", "required": False, "default": None, "description": "Optional strike override; omit = ATM."},
        ],
        "examples": [{'underlying': 'NIFTY'}, {'underlying': 'NIFTY', 'strike': 25000}],
        "notes": 'Profits on high volatility / big moves. Legs are priced at listed premiums near the requested strike.',
        "status": "active",
    },
    {
        "name": "price_long_strangle",
        "display": "Long Strangle",
        "category": "Pricing",
        "description": "STRATEGY PRICING. Long strangle: buy OTM call + buy OTM put (two different strikes, both required). Returns net_debit, max_profit, max_loss, breakevens and priced legs. Cheaper than a straddle but needs a bigger move.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "call_strike", "type": "float", "required": True, "default": None, "description": "OTM call strike (above spot), e.g. 25500. Required."},
            {"name": "put_strike", "type": "float", "required": True, "default": None, "description": "OTM put strike (below spot), e.g. 24500. Required."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY', 'call_strike': 25500, 'put_strike': 24500}],
        "notes": 'Cheaper than straddle. Needs bigger move to profit.',
        "status": "active",
    },
    {
        "name": "price_bull_call_spread",
        "display": "Bull Call Spread",
        "category": "Pricing",
        "description": "STRATEGY PRICING. Bull call spread (debit): buy the LOWER strike call, sell the HIGHER strike call (lower_strike must be < higher_strike). Returns net_debit, max_profit, max_loss, breakevens and priced legs. Bullish, capped upside.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "lower_strike", "type": "float", "required": True, "default": None, "description": "Lower strike -- long (bought) call, e.g. 25000. Required."},
            {"name": "higher_strike", "type": "float", "required": True, "default": None, "description": "Higher strike -- short (sold) call, e.g. 25500. Must be > lower_strike."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY', 'lower_strike': 25000, 'higher_strike': 25500}],
        "notes": 'Bullish. Max profit = spread width - net debit.',
        "status": "active",
    },
    {
        "name": "price_bear_put_spread",
        "display": "Bear Put Spread",
        "category": "Pricing",
        "description": "STRATEGY PRICING. Bear put spread (debit): buy the HIGHER strike put, sell the LOWER strike put (higher_strike must be > lower_strike). Returns net_debit, max_profit, max_loss, breakevens and priced legs. Bearish, capped downside.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "higher_strike", "type": "float", "required": True, "default": None, "description": "Higher strike -- long (bought) put, e.g. 25000. Required."},
            {"name": "lower_strike", "type": "float", "required": True, "default": None, "description": "Lower strike -- short (sold) put, e.g. 24500. Must be < higher_strike."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY', 'higher_strike': 25000, 'lower_strike': 24500}],
        "notes": 'Bearish. Max profit = spread width - net debit.',
        "status": "active",
    },
    {
        "name": "price_iron_condor",
        "display": "Iron Condor",
        "category": "Pricing",
        "description": "STRATEGY PRICING. Iron condor (credit): sell put_sell_strike, buy put_buy_strike (lower), buy call_buy_strike, sell call_sell_strike (higher). All FOUR strikes required and ordered: put_buy < put_sell < call_buy < call_sell. Returns net_debit (negative = credit received), max_profit, max_loss, breakevens and priced legs. Range-bound income strategy.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "put_sell_strike", "type": "float", "required": True, "default": None, "description": "Short put strike (higher of the two puts), e.g. 24500."},
            {"name": "put_buy_strike", "type": "float", "required": True, "default": None, "description": "Long put strike (lower), e.g. 24000. Must be < put_sell_strike."},
            {"name": "call_buy_strike", "type": "float", "required": True, "default": None, "description": "Long call strike (lower of the two calls), e.g. 25500."},
            {"name": "call_sell_strike", "type": "float", "required": True, "default": None, "description": "Short call strike (higher), e.g. 26000. Must be > call_buy_strike."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY', 'put_sell_strike': 24500, 'put_buy_strike': 24000, 'call_buy_strike': 25500, 'call_sell_strike': 26000}],
        "notes": 'Range-bound strategy. Max profit = net premium received.',
        "status": "active",
    },
    {
        "name": "price_long_butterfly",
        "display": "Long Butterfly",
        "category": "Pricing",
        "description": "STRATEGY PRICING. Long (call) butterfly (debit): buy lower_strike call, SELL 2x middle_strike call, buy upper_strike call. All three strikes required and ordered lower < middle < upper (equal spacing gives the classic shape). Returns net_debit, max_profit, max_loss, breakevens and priced legs. Profits if price pins near the middle strike.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "lower_strike", "type": "float", "required": True, "default": None, "description": "Lower wing strike -- long call, e.g. 24500."},
            {"name": "middle_strike", "type": "float", "required": True, "default": None, "description": "Middle (body) strike -- short 2x calls, e.g. 25000. Must be between the wings."},
            {"name": "upper_strike", "type": "float", "required": True, "default": None, "description": "Upper wing strike -- long call, e.g. 25500. Must be > middle_strike."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
        ],
        "examples": [{'underlying': 'NIFTY', 'lower_strike': 24500, 'middle_strike': 25000, 'upper_strike': 25500}],
        "notes": 'Profits at expiry near middle strike.',
        "status": "active",
    },
    {
        "name": "analyze_option_chain",
        "display": "Analyze Option Chain",
        "category": "Analytics",
        "description": "PREFERRED ONE-CALL DEEP ANALYSIS. Runs 7 derived analytics over the FULL chain in a single call: PCR, max pain, ATM, support/resistance, OI buildup, IV skew and GEX. Use this instead of calling each compute_* tool separately. Set max_strikes to also get a trimmed chain view around ATM embedded in the response.",
        "params": [
            {"name": "underlying", "type": "str", "required": True, "default": None, "description": "Human underlying: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, RELIANCE."},
            {"name": "expiry", "type": "str", "required": False, "default": None, "description": "Expiry YYYY-MM-DD; omit = nearest listed expiry."},
            {"name": "max_strikes", "type": "int", "required": False, "default": None, "description": "Optional: include a chain view trimmed to this many strikes around ATM. Omit for analytics only."},
        ],
        "examples": [{'underlying': 'NIFTY'}, {'underlying': 'NIFTY', 'max_strikes': 10}],
        "notes": 'All 7 analytics in one call -- prefer this over separate compute_* calls. Works on last-session chain when the market is closed.',
        "status": "active",
    },
    {
        "name": "news_list",
        "display": "News List",
        "category": "News",
        "description": "READ ONLY. FRESHEST MARKET HEADLINES -- the one tool to answer 'what is the news?'. Returns the newest items (default 15, max 30) from enabled RSS/Reddit sources within the age window, newest first. Every item carries headline, summary, link, source, source type, published and fetched times, category, symbols where known, and sentiment. Reads the stored database first (fast, no network); use news_refresh for an explicit fresh pull. Each response includes freshness metadata (latest published time, last successful fetch, ages) and per-source health.",
        "params": [
            {"name": "limit", "type": "int", "required": False, "default": 15, "description": "Max items 1-30 (default 15). Keep small: the AI summarizes from headlines+summaries."},
            {"name": "max_age_hours", "type": "int", "required": False, "default": 24, "description": "Only items newer than this many hours (1-168, default 24). Raise for weekend catch-up."},
            {"name": "category", "type": "str", "required": False, "default": None, "description": "Optional category filter, e.g. 'finance'. Omit for all categories."},
            {"name": "cursor", "type": "str", "required": False, "default": None, "description": "Pagination cursor (a next_cursor from a previous call). Omit for the first page."},
        ],
        "examples": [{}, {'limit': 10}, {'category': 'finance'}],
        "notes": 'Database-first read. Stale-but-useful data is returned with freshness metadata, never converted to an error. Market closure never blanks results.',
        "status": "active",
    },
    {
        "name": "news_search",
        "display": "News Search",
        "category": "News",
        "description": "READ ONLY. Keyword/entity search over stored news headlines and summaries (e.g. 'RELIANCE', 'RBI', 'IPO'). Returns the same item shape as news_list (headline, summary, link, source, published, sentiment, freshness), newest first, capped small. Entity names resolve through the MarketHub instrument catalog where possible. Searches the PERSISTED store only -- call news_refresh first if you need the very latest before searching.",
        "params": [
            {"name": "query", "type": "str", "required": True, "default": None, "description": "Non-empty keyword, phrase or entity, e.g. 'RELIANCE results'. Matched against headline+summary text."},
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Max items 1-20 (default 10)."},
            {"name": "max_age_hours", "type": "int", "required": False, "default": None, "description": "Optional age window in hours (1-168). Omit for any age."},
            {"name": "cursor", "type": "str", "required": False, "default": None, "description": "Pagination cursor (a next_cursor from a previous call). Omit for the first page."},
        ],
        "examples": [{'query': 'RELIANCE'}, {'query': 'RBI'}],
        "notes": 'Stored-history search (no network). Pair with news_refresh for latest-then-search.',
        "status": "active",
    },
    {
        "name": "news_get",
        "display": "News Get",
        "category": "News",
        "description": "READ ONLY. Retrieve one stored MarketHub news item by its stable news id (from news_list/news_search item_id). Returns the full stored record with provenance (source, source type, url, published and fetched times, category, symbols, sentiment). No network fetch; unknown ids return a not-found error.",
        "params": [
            {"name": "news_id", "type": "str", "required": True, "default": None, "description": "Stable news item id from a news_list/news_search response."},
        ],
        "examples": [{'news_id': 'abc123'}],
        "notes": 'Stored-record retrieval only. Does not fetch the full external article.',
        "status": "active",
    },
    {
        "name": "news_refresh",
        "display": "News Refresh",
        "category": "News",
        "description": "Fetch all enabled news sources NOW (RSS + Reddit), deduplicate by stable item id, store new items and prune expired ones. Call when news_list/news_search results feel stale. One failing source never blocks the others -- per-source health is recorded and reported, not raised. Takes a few seconds (network).",
        "params": [
        ],
        "examples": [{}],
        "notes": 'No parameters. Returns started/completed times, per-source succeeded/failed counts, items seen/added/deduplicated, latest published time, and errors. Health is persisted per source.',
        "status": "active",
    },
    {
        "name": "twitter_feed",
        "display": "X Feed",
        "category": "X",
        "description": "READ ONLY. Cached X/Twitter following feed (newest first) from the poll cache; pass live=true for one explicit CLI fetch. A tweet alone is NEVER an alert -- only a matching X rule (source=twitter alert row) fires alert.triggered.",
        "params": [
            {"name": "limit", "type": "int", "required": False, "default": 20, "description": "Max tweets 1-20 (default 20)."},
            {"name": "cursor", "type": "str", "required": False, "default": None, "description": "Feed cursor for paging (live mode). Omit for first page."},
            {"name": "live", "type": "bool", "required": False, "default": False, "description": "True = one live CLI fetch; False (default) = fast DB cache read."},
        ],
        "examples": [{}, {'limit': 10}, {'live': True}],
        "notes": 'Cache-first read. Live mode requires configured X credentials.',
        "status": "active",
    },
    {
        "name": "twitter_search",
        "display": "X Search",
        "category": "X",
        "description": "READ ONLY. Live X/Twitter keyword search via twitter-cli. Searches current X results (not the local cache).",
        "params": [
            {"name": "query", "type": "str", "required": True, "default": None, "description": "Non-empty keyword, phrase or query, e.g. 'NIFTY'."},
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Max results 1-20 (default 10)."},
        ],
        "examples": [{'query': 'NIFTY'}],
        "notes": 'Live search only. Requires configured X credentials. Known upstream limitation: twitter-cli 0.8.5 cannot reach the current X search endpoint (SearchTimeline 404) — failures surface as code search_unavailable, not not_found.',
        "status": "active",
    },
    {
        "name": "twitter_tweet",
        "display": "X Tweet",
        "category": "X",
        "description": "READ ONLY. Fetch one tweet by URL or id (full text + replies context).",
        "params": [
            {"name": "url_or_id", "type": "str", "required": True, "default": None, "description": "Tweet URL or snowflake id."},
        ],
        "examples": [{'url_or_id': 'https://x.com/elonmusk/status/123'}],
        "notes": 'Single-tweet read. No network cache write.',
        "status": "active",
    },
    {
        "name": "twitter_article",
        "display": "X Article",
        "category": "X",
        "description": "READ ONLY. Fetch one long-form X article by URL or id (structured payload).",
        "params": [
            {"name": "url_or_id", "type": "str", "required": True, "default": None, "description": "Article URL or id."},
        ],
        "examples": [{'url_or_id': 'https://x.com/i/article/123'}],
        "notes": 'Long-form only. Returns the article payload as JSON.',
        "status": "active",
    },
    {
        "name": "twitter_bookmarks",
        "display": "X Bookmarks",
        "category": "X",
        "description": "READ ONLY. List your own saved X bookmarks (newest first).",
        "params": [
            {"name": "limit", "type": "int", "required": False, "default": 20, "description": "Max items 1-20 (default 20)."},
        ],
        "examples": [{}],
        "notes": 'Own saved items only. Requires configured X credentials.',
        "status": "active",
    },
    {
        "name": "twitter_user_posts",
        "display": "X User Posts",
        "category": "X",
        "description": "READ ONLY. Recent posts from one X handle (without @).",
        "params": [
            {"name": "handle", "type": "str", "required": True, "default": None, "description": "X handle without @, e.g. 'elonmusk'."},
            {"name": "limit", "type": "int", "required": False, "default": 10, "description": "Max posts 1-20 (default 10)."},
        ],
        "examples": [{'handle': 'elonmusk'}],
        "notes": 'On-demand handle read. Requires configured X credentials. Protected, suspended, or nonexistent handles fail per-handle with code not_found (other handles unaffected).',
        "status": "active",
    },
    {
        "name": "twitter_user_profile",
        "display": "X User Profile",
        "category": "X",
        "description": "READ ONLY. Public profile for one X handle (without @).",
        "params": [
            {"name": "handle", "type": "str", "required": True, "default": None, "description": "X handle without @, e.g. 'elonmusk'."},
        ],
        "examples": [{'handle': 'elonmusk'}],
        "notes": 'Profile read only. No posts included.',
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
