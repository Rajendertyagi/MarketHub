# MCP-1 Market-Data Tool Contract (v2.4.0)

> Frozen reference document for the MCP public tool surface.
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
| **MCP-2B Public** | 16 | Alerts, events, consumers, replay |
| **B5 Advanced Alerts** | 5 | condition_alert_* (public MCP exposure of market_condition engine) |
| **B6 Analytics Metrics** | 4 | pcr_oi, pcr_volume, max_pain, iv_skew (analytics-backed condition metrics) |
| **Total Public** | **47** | |
| **Deferred / Internal** | 0 | All previously deferred tools are now finalized |

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

## Public Tool Reference (26 MCP-1 Frozen + 16 MCP-2B Finalized)

### Frozen MCP-1 Tools (26)

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

## Finalized MCP-2B Tools (16)

These tools were previously deferred but are now finalized as part of the public MCP contract (MCP-2B.3D).

### Generic Alerts (5)

| Tool | Category | Status |
|------|----------|--------|
| `alert_create`, `alert_list`, `alert_get`, `alert_enable`, `alert_disable` | Generic alerts | **FINALIZED** |

### Event Inspection (1)

| Tool | Category | Status |
|------|----------|--------|
| `event_list` | Event diagnostics | **FINALIZED** — diagnostics/observational journal only; NOT durable replay |

### Consumer / Replay (6)

| Tool | Category | Status |
|------|----------|--------|
| `consumer_register`, `consumer_topic_add` | Consumer management | **FINALIZED** |
| `consumer_event_pending_list`, `consumer_event_acknowledge`, `consumer_checkpoint_get` | Replay/checkpoint | **FINALIZED** |

### Market Alerts (5)

| Tool | Category | Status |
|------|----------|--------|
| `market_alert_create`, `market_alert_list`, `market_alert_enable`, `market_alert_disable`, `market_alert_delete` | Market alerts | **FINALIZED** |

---

## B5: Advanced Market Condition Alerts (Public MCP)

> **Phase:** B5 — public MCP exposure of the B2/B4 `market_condition` engine
>
> **Contract version:** 2.3.0
>
> **Tool count change:** 42 → 47 (+5)
>
> These tools expose the existing production `ConditionAlertEngine` to
> external AI clients. No engine logic is duplicated. Instrument references
> are human/canonical — callers never supply broker tokens.

### condition_alert_create

| | |
|---|---|
| **Purpose** | Create a consumer-owned advanced market-condition alert |
| **Inputs** | `consumer_id: str` (required), `condition: dict` (required), `trigger_mode: str` (default `"repeat"`), `name: str | None` (optional), `metadata: dict | None` (optional) |
| **Output** | `{"status": "created", "alert": {alert_id, consumer_id, name, enabled, trigger_mode, condition, metadata, created_at, updated_at, trigger_count, last_triggered_at}}` |
| **Backing** | EventStore → ConditionAlertEngine.reload() |

**condition schema:**

- `condition_version: 1` — single leaf:
  ```json
  {"condition_version": 1, "metric": "ltp", "operator": "gt", "value": 25000,
   "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}}
  ```
- `condition_version: 2` — nested ALL/ANY group (same-instrument only):
  ```json
  {"condition_version": 2, "logic": "all",
   "conditions": [
     {"condition_version": 1, "metric": "ltp", "operator": "gt", "value": 1500,
      "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}},
     {"condition_version": 1, "metric": "volume", "operator": "gt", "value": 1000000,
      "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}}
   ]}
  ```

**Public instrument references (no broker tokens):**

| Type | Required fields | Example |
|------|----------------|---------|
| EQUITY / ETF | `exchange`, `symbol` | `{"exchange": "NSE", "symbol": "RELIANCE"}` |
| INDEX | `exchange`, `symbol` | `{"exchange": "NSE", "symbol": "NIFTY"}` |
| FUTURE | `exchange`, `underlying`, `expiry` | `{"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-09-25"}` |
| OPTION | `exchange`, `underlying`, `expiry`, `strike`, `option_type` | `{"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-09-25", "strike": 25000, "option_type": "CE"}` |

**Limits:** max depth 8, max leaves 64, same-instrument enforced within v2 groups.

**Metrics (27):** `ltp, open, high, low, close, change, change_percent, avg_trade_price, last_traded_qty, volume, total_buy_qty, total_sell_qty, open_interest, previous_oi, oi_change, oi_change_percent, best_bid, best_ask, spread, upper_circuit, lower_circuit, greeks.delta, greeks.gamma, greeks.theta, greeks.vega, greeks.rho, greeks.iv`

**Operators (8):** `eq, ne, gt, gte, lt, lte, crosses_above, crosses_below`

**trigger_mode:** `once` (fires once then disables) or `repeat` (re-arms on state transitions).

**Errors normalized:** `ConditionValidationError` (bad metric/operator/version/structure), `ValidationError` (bad trigger_mode/consumer_id), `AlertNotFoundError` (not found), `StorageError` (internal failure). No raw Python exceptions leak.

### condition_alert_list

| | |
|---|---|
| **Purpose** | List condition alerts owned by a consumer |
| **Inputs** | `consumer_id: str` (required), `enabled: bool | None` (optional filter), `limit: int | None` (optional, default 50, max 200) |
| **Output** | `{"status": "ok", "count": int, "alerts": [{alert_id, consumer_id, name, enabled, trigger_mode, condition, metadata, created_at, updated_at, trigger_count, last_triggered_at}]}` |

### condition_alert_get

| | |
|---|---|
| **Purpose** | Get one condition alert by id (ownership enforced) |
| **Inputs** | `consumer_id: str` (required), `alert_id: str` (required) |
| **Output** | `{"status": "ok", "alert": {alert_id, consumer_id, name, enabled, trigger_mode, condition, metadata, created_at, updated_at, trigger_count, last_triggered_at}}` |
| **Ownership** | Cross-owner access returns not-found (same as missing alert) |

### condition_alert_set_enabled

| | |
|---|---|
| **Purpose** | Enable or disable a condition alert (ownership enforced) |
| **Inputs** | `consumer_id: str` (required), `alert_id: str` (required), `enabled: bool` (required) |
| **Output** | `{"status": "enabled"|"disabled", "ok": true, "alert_id": str, "enabled": bool}` |
| **Re-arm semantics** | Enabling a disabled alert **resets runtime state to UNKNOWN** so the alert re-arms fresh. For LEVEL operators this means a new FALSE→TRUE transition can fire. For CROSSING operators the first valid observation re-establishes the crossing side baseline. |

### condition_alert_delete

| | |
|---|---|
| **Purpose** | Delete a condition alert (ownership enforced) |
| **Inputs** | `consumer_id: str` (required), `alert_id: str` (required) |
| **Output** | `{"status": "deleted", "ok": true, "alert_id": str}` |
| **History** | Deletes the alert definition and runtime state. Historical `alert.triggered` events are **preserved** in the durable event store. |
| **Ownership** | Cross-owner access returns not-found. |

### B5 invariants

- **No provider tokens** in any input or output
- **Same-instrument restriction**: all leaves in a v2 group must resolve to the same canonical instrument
- **Atomic trigger**: engine evaluates against live quotes; a fire persists runtime state + alert row + event + consumer materialization in one SQLite transaction
- **Delivery**: trigger event flows through the existing `alert.triggered` → `consumer_event_pending_list` → `consumer_event_acknowledge` path
- **B6/B7/B8 exclusions**: no PCR, no Max Pain, no multi-instrument groups, no quote-injection tool, no analytics-layer calls

---

## Removed Tools

### event_publish (removed in MCP-2B.3D)

| | |
|---|---|
| **Status** | REMOVED from public MCP registry |
| **Reason** | Manual/test-oriented; arbitrary JSON injection surface; no established external-AI production need |
| **Internal pipeline** | `core.events.publish_event()` remains the canonical internal publication path |
| **Replacement** | None — sources publish via the internal pipeline; consumers replay via `consumer_event_pending_list` |

### consumer_event_list (removed in MCP-2B.3C)

| | |
|---|---|
| **Status** | REMOVED from public MCP registry |
| **Replacement** | `consumer_event_pending_list(after_sequence=0)` for from-beginning replay |

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-08 | Initial MCP-0 foundation |
| 1.1.0 | 2026-08 | Added alert tools, market_alert tools |
| 1.2.0 | 2026-08 | Added options analytics, strategy pricing, analyze_option_chain |
| **2.0.0** | **2026-08-27** | **MCP-1: Removed 7 dev tools, simplified instrument_ref schemas, removed provider leak from market_history, fixed asyncio import, polished descriptions** |
| **2.1.0** | **2026-08-27** | **MCP-2B.3C: Removed `consumer_event_list` (44→43 visible tools); `consumer_event_pending_list` is the canonical replay tool; normalized `market_alert_*` errors to shared domain exceptions; `consumer_checkpoint_get` now reports persisted `updated_at`** |
| **2.2.0** | **2026-08-28** | **MCP-2B.3D: Removed `event_publish` from public registry (43→42 visible tools); froze 16 MCP-2B tools as final public surface; `event_list` documented as diagnostics-only; at-least-once replay contract frozen; live notification deferred to MCP-2B.4** |
| **2.3.0** | **2026-08-31** | **B5: Added 5 public `condition_alert_*` tools (42→47 tools); `CONTRACT_VERSION` bumped to 2.3.0; v1 leaf + v2 same-instrument ALL/ANY groups exposed via MCP; human/canonical instrument references (no broker tokens); re-arm on enable; ownership enforcement on get/set_enabled/delete** |
| **2.4.0** | **2026-08-31** | **B6B: Added 4 analytics-backed condition metrics (pcr_oi, pcr_volume, max_pain, iv_skew); `CONTRACT_VERSION` bumped to 2.4.0; total metrics 27→31; analytics conditions require `instrument.expiry`; same-chain restriction enforced for analytics groups; mixed quote+analytics groups rejected (B7); MarketAnalyticsService cache + scheduler; PCR zero-denominator returns None** |
