# MCP-1 Market-Data Tool Contract (v2.0.0)

> Frozen reference document for the MCP-1 public tool surface.
> Any change to tool names, inputs, or output shapes requires a contract version bump.

---

## Overview

| Category | Count | Description |
|----------|-------|-------------|
| **System** | 1 | Server health check |
| **Market Data** | 6 | Quote, depth, status, search, watchlists, history |
| **Derivatives** | 2 | Option chain, futures contracts |
| **Options Analytics** | 10 | PCR, max pain, OI, IV, GEX, basis, etc. |
| **Strategy Pricing** | 6 | Straddle, strangle, spreads, condor, butterfly |
| **Composite** | 1 | Full option-chain analysis (bundled) |
| **Total Public** | **26** | |
| **Deferred** | 18 | Alerts, events, consumers, replay (registered, not public contract) |

---

## Provider-Neutral Policy

- MCP tools must **never** import `brokers.upstox` or `brokers.fyers`
- Tools must **never** expose provider-specific API keys, tokens, or auth parameters
- The `market_history` tool auto-selects the best available provider internally
- Instrument resolution uses the shared `InstrumentCatalog` — the same catalog WebUI and Chat use

---

## Instrument Reference Contract

All market-data tools that accept a single instrument use the `instrument_ref` parameter:

| Format | Example | Resolution |
|--------|---------|------------|
| Provider key (pipe) | `NSE_EQ\|INE002A01018` | Catalog lookup, falls back to split |
| Exchange\|token | `NSE\|12345` | Catalog lookup, falls back to split |
| Canonical symbol | `RELIANCE` | Catalog search |

Resolution priority:
1. **InstrumentCatalog.search(q=instrument_ref)** — returns (exchange, instrument_token)
2. **Pipe-split fallback** — if `|` present, split into (exchange, token)

---

## Serialization

- All timestamps are **ISO 8601 UTC** (e.g. `2026-08-27T10:00:00+00:00`)
- `None` values are **preserved** (not stripped from output)
- `Decimal` values serialized as JSON numbers
- Shared serializer: `market/serialization.py` (used by MCP, REST, and SSE)

---

## Error Behavior

- Tool errors return **dict** with `"error"` key: `{"error": "description"}`
- The MCP SDK wraps errors into `CallToolResult(is_error=True)` automatically
- Tool handlers must **not** raise `MCPError` for domain errors — only for protocol-level failures
- Common error patterns:
  - `"market service not available"` — MarketService not wired
  - `"could not resolve instrument reference"` — unknown instrument_ref
  - `"quote not found"` / `"depth not found"` — no data for this instrument
  - `"history failed: ProviderMarketDataError"` — provider-level failure

---

## Read-Only Boundary

- **No trading tools** (place/modify/cancel order)
- **No holdings/positions/funds** tools
- **No credential mutation** tools
- **No raw broker access** — all reads go through MarketService, MarketIntel, or ProviderMarketData

---

## Public Tool Reference

### system_ping

| | |
|---|---|
| **Purpose** | Check whether the MCP server is running |
| **Inputs** | none |
| **Output** | `{status, message, timestamp}` |
| **Backing** | none (in-memory) |

### market_quote

| | |
|---|---|
| **Purpose** | Return the latest canonical quote for one instrument |
| **Inputs** | `instrument_ref: str` (required) |
| **Output** | `{status: "ok", quote: {ltp, open, high, low, close, volume, ...}}` |
| **Backing** | MarketService (live, in-memory) |

### market_depth

| | |
|---|---|
| **Purpose** | Return the latest L2 order book for one instrument |
| **Inputs** | `instrument_ref: str` (required) |
| **Output** | `{status: "ok", depth: {bids: [...], asks: [...]}}` |
| **Backing** | MarketService (live, in-memory) |
| **Notes** | Depth levels vary by source: 5 (HSM), 30 (Upstox REST), 50 (Fyers TBT) |

### market_status

| | |
|---|---|
| **Purpose** | Return MarketService diagnostic counters |
| **Inputs** | none |
| **Output** | `{status: "ok", service: {quote_count, depth_count, accepted, stale, ...}}` |
| **Backing** | MarketService |

### instrument_search

| | |
|---|---|
| **Purpose** | Search instruments by human-readable query |
| **Inputs** | `q: str` (required), `exchange?: str`, `expiry?: str`, `types?: list[str]`, `limit?: int` (default 10, max 50) |
| **Output** | `{status: "ok", count, results: [{instrument_token, exchange, tradingsymbol, ...}]}` |
| **Backing** | MarketIntel or InstrumentCatalog |

### watchlists

| | |
|---|---|
| **Purpose** | List all persistent watchlists and their instruments |
| **Inputs** | none |
| **Output** | `{status: "ok", watchlists: [{id, name, items: [...]}]}` |
| **Backing** | EventStore (SQLite) |

### market_history

| | |
|---|---|
| **Purpose** | Return historical OHLCV candles for an instrument |
| **Inputs** | `instrument_ref: str`, `unit: str` ("minutes"/"hours"/"days"/"weeks"/"months"), `interval: int`, `from_date: str` (YYYY-MM-DD), `to_date: str` (YYYY-MM-DD) |
| **Output** | `{status: "ok", candles: [{o, h, l, c, v, ts, ...}]}` |
| **Backing** | ProviderMarketData (auto-selects best provider) |
| **Notes** | Max range depends on provider (30-400 days) |

### option_chain

| | |
|---|---|
| **Purpose** | Return option chain for an underlying |
| **Inputs** | `underlying: str` (e.g. "NIFTY"), `expiry?: str`, `window?: int` (default 10) |
| **Output** | `{status: "ok", spot_price, atm_strike, strikes: [{strike, call: {...}, put: {...}}]}` |
| **Backing** | MarketIntel |

### futures_contracts

| | |
|---|---|
| **Purpose** | List available futures contracts for an underlying |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", expiries: [...], contracts: [{expiry, instrument_key, lot_size}]}` |
| **Backing** | MarketIntel |

### compute_pcr

| | |
|---|---|
| **Purpose** | Put-Call Ratio from total OI (>1 = bearish) |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", pcr, call_oi, put_oi}` |
| **Backing** | ProviderMarketData → local computation |

### compute_max_pain

| | |
|---|---|
| **Purpose** | Strike where total option-writer payout is minimised |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", max_pain_strike, total_pain}` |

### compute_top_oi_strikes

| | |
|---|---|
| **Purpose** | Strikes with highest call OI and highest put OI |
| **Inputs** | `underlying: str`, `expiry?: str`, `n?: int` (default 5) |
| **Output** | `{status: "ok", top_call_oi: [...], top_put_oi: [...]}` |

### compute_atm

| | |
|---|---|
| **Purpose** | At-the-money strike and underlying spot |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", atm_strike, spot_price}` |

### compute_iv_skew

| | |
|---|---|
| **Purpose** | IV skew: avg OTM put IV minus avg OTM call IV |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", iv_skew, avg_put_iv, avg_call_iv}` |

### compute_oi_buildup

| | |
|---|---|
| **Purpose** | Count of legs per buildup tag |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", buildup: {long_buildup: N, short_buildup: N, ...}}` |

### compute_support_resistance

| | |
|---|---|
| **Purpose** | Support = max put OI strike; resistance = max call OI strike |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", support_strike, resistance_strike}` |

### compute_straddle

| | |
|---|---|
| **Purpose** | ATM straddle cost and breakeven levels |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", straddle_cost, breakeven_up, breakeven_down}` |

### compute_gex

| | |
|---|---|
| **Purpose** | Gamma Exposure proxy: net (gamma × OI) across calls minus puts |
| **Inputs** | `underlying: str`, `expiry?: str` |
| **Output** | `{status: "ok", gex, call_gex, put_gex}` |

### compute_futures_basis

| | |
|---|---|
| **Purpose** | Futures premium/discount vs spot for each expiry |
| **Inputs** | `underlying: str` |
| **Output** | `{status: "ok", spot, basis: [{expiry, premium, pct}]}` |

### price_long_straddle

| | |
|---|---|
| **Purpose** | Long straddle: buy ATM call + buy ATM put |
| **Inputs** | `underlying: str`, `expiry?: str`, `strike?: float` |
| **Output** | `{status: "ok", net_debit, max_loss, breakeven_up, breakeven_down}` |

### price_long_strangle

| | |
|---|---|
| **Purpose** | Long strangle: buy OTM call + buy OTM put |
| **Inputs** | `underlying: str`, `call_strike: float`, `put_strike: float`, `expiry?: str` |
| **Output** | `{status: "ok", net_debit, max_loss, breakeven_up, breakeven_down}` |

### price_bull_call_spread

| | |
|---|---|
| **Purpose** | Bull call spread: buy lower call, sell higher call |
| **Inputs** | `underlying: str`, `lower_strike: float`, `higher_strike: float`, `expiry?: str` |
| **Output** | `{status: "ok", net_debit, max_profit, max_loss}` |

### price_bear_put_spread

| | |
|---|---|
| **Purpose** | Bear put spread: buy higher put, sell lower put |
| **Inputs** | `underlying: str`, `higher_strike: float`, `lower_strike: float`, `expiry?: str` |
| **Output** | `{status: "ok", net_debit, max_profit, max_loss}` |

### price_iron_condor

| | |
|---|---|
| **Purpose** | Iron condor: range-bound income strategy |
| **Inputs** | `underlying: str`, `put_sell_strike`, `put_buy_strike`, `call_buy_strike`, `call_sell_strike`, `expiry?: str` |
| **Output** | `{status: "ok", net_credit, max_profit, max_loss}` |

### price_long_butterfly

| | |
|---|---|
| **Purpose** | Long butterfly: profits at middle strike |
| **Inputs** | `underlying: str`, `lower_strike`, `middle_strike`, `upper_strike`, `expiry?: str` |
| **Output** | `{status: "ok", net_debit, max_profit, max_loss}` |

### analyze_option_chain

| | |
|---|---|
| **Purpose** | One-call analysis: 7 derived analytics over the full chain |
| **Inputs** | `underlying: str`, `expiry?: str`, `max_strikes?: int` |
| **Output** | `{status: "ok", symbol, analytics: {pcr, max_pain, atm, ...}, chain?: {...}}` |
| **Notes** | Bundles PCR + max pain + ATM + support/resistance + OI buildup + IV skew + GEX. Optional embedded chain view trimmed to max_strikes around ATM. |

---

## Deferred Tools (Registered, Not in Public Contract)

These tools are registered in the MCP server but NOT part of the MCP-1 public contract. They may be promoted in MCP-2.

| Tool | Category | Status |
|------|----------|--------|
| `alert_create`, `alert_list`, `alert_get`, `alert_enable`, `alert_disable` | Generic alerts | DEFERRED |
| `event_publish`, `event_list` | Event pub/sub | DEFERRED |
| `consumer_register`, `consumer_topic_add` | Consumer management | DEFERRED |
| `consumer_event_pending_list`, `consumer_event_acknowledge`, `consumer_checkpoint_get` | Replay/checkpoint | DEFERRED |
| `market_alert_create`, `market_alert_list`, `market_alert_enable`, `market_alert_disable`, `market_alert_delete` | Market alerts | DEFERRED |

> **MCP-2B.3C compatibility note:** `consumer_event_list` was removed from the
> public MCP surface. Use `consumer_event_pending_list(after_sequence=0)` for
> the same from-beginning replay of a consumer's relevant persistent events.
> The underlying store query (`list_relevant_events`) remains available to
> internal code and tests.

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-08 | Initial MCP-0 foundation |
| 1.1.0 | 2026-08 | Added alert tools, market_alert tools |
| 1.2.0 | 2026-08 | Added options analytics, strategy pricing, analyze_option_chain |
| **2.0.0** | **2026-08-27** | **MCP-1: Removed 7 dev tools, simplified instrument_ref schemas, removed provider leak from market_history, fixed asyncio import, polished descriptions** |
| **2.1.0** | **2026-08-27** | **MCP-2B.3C: Removed `consumer_event_list` (44→43 visible tools); `consumer_event_pending_list` is the canonical replay tool; normalized `market_alert_*` errors to shared domain exceptions; `consumer_checkpoint_get` now reports persisted `updated_at`** |
