# MCP Runtime

Runtime and transport notes for the MarketHub MCP server. This document covers
*how* the MCP server runs and is tested — it does **not** duplicate the frozen
tool contract (see `docs/MCP_TOOL_CONTRACT.md`).

## 1. Endpoint

The MCP server is exposed over **Streamable HTTP** at:

```
/mcp
```

## 2. Server

The MCP endpoint is served by the **same Starlette/Uvicorn application** that
serves the REST/SSE routes. There is no separate MCP process or port in
production — `app/server.py` mounts the MCP ASGI app on the shared app.

## 3. Test runtime

Tests that need a live server start a **subprocess** running the real
application:

- real TCP socket
- dynamically assigned free port (never a hardcoded `8000`)
- the subprocess writes a temporary `config.json` and uses an isolated
  `data_test` directory

The module-scoped `mcp_server` pytest fixture (`test/conftest.py`) owns this
lifecycle for the standalone-style test files.

## 4. URL contract

The MCP SDK client (`mcp.client.streamable_http.streamable_http_client`)
requires an **absolute HTTP URL** with an explicit scheme. A bare host or an
empty string makes the SDK raise `httpx2.UnsupportedProtocol`.

Test URLs are built by `test/helpers/urls.py::build_mcp_url(host, port, path)`:

```
http://{host}:{port}/mcp
```

## 5. Bind address vs client URL

The server may bind to `0.0.0.0` (all interfaces). A test client must use a
**routable** address — never `http://0.0.0.0:<port>/mcp`:

```
bind:      0.0.0.0:<port>
client URL: http://127.0.0.1:<port>/mcp
```

## 6. `public_base_url`

`public_base_url` in the app config is used for **broker OAuth / application
callback** semantics. It is **not** the MCP test client URL and is not reused
for MCP. The MCP test client URL is constructed from the subprocess host +
ephemeral port + `/mcp`.

## 7. MCP mode

The server runs Streamable HTTP in **stateless** mode:

```python
mcp.streamable_http_app(..., stateless_http=True, ...)
```

Each request is handled independently; the server does not track session IDs.

## 8. Session behavior

Because the mode is stateless, a client session performs `initialize` per
connection and each request is self-contained. No custom session IDs are
invented by the application.

## 9. Startup ownership

- **Production:** `app/server.py` builds the app; Uvicorn runs it.
- **Tests:** `test/helpers/lifecycle.py::start_server()` starts the subprocess,
  waits for TCP, then requires a **real MCP operation** (`initialize` +
  `system_ping`) before reporting ready.

## 10. Shutdown ownership

`test/helpers/lifecycle.py::restore_environment()` owns teardown:

- stops the subprocess (bounded graceful terminate, then hard kill)
- restores the original `config.json`
- cleans the isolated `data_test` directory and `.test_logs`
- releases the port
- clears the module-level helper state

## 11. Why subprocess instead of ASGITransport

Runtime proofs use the real subprocess over real TCP because:

- it matches the production transport exactly
- MCP DNS-rebinding protection requires a port-aware `Host` header, which
  differs under `ASGITransport`
- `ASGITransport` does not run the app lifespan the same way
- MCP-2A is specifically verifying the real Streamable HTTP runtime

## 12. Live consumer inbox notifications (MCP-2B.4B)

Persistent events additionally wake the per-consumer inbox resource
`mcp-event://consumers/{consumer_id}/events` over the modern 2026-07-28
`subscriptions/listen` path. The global `mcp-event://events/latest` resource
is unchanged.

Flow (persistent event only):

```
event persisted (store.save succeeds)
    -> relevant durable consumers computed (same predicate as materialization)
    -> ResourceUpdated(uri=mcp-event://consumers/{cid}/events) per relevant consumer
    -> modern AI client listen() wakes
    -> AI reads the inbox resource (compact status) and calls
       consumer_event_pending_list for the durable replay
```

Routing mirrors durable materialization exactly:

- routing absent (broadcast) -> all registered consumers
- `routing.targets` -> only the listed consumers
- `routing.topics` -> only consumers with intersecting topics

Transient events (`persistent=False`) never notify a consumer inbox — they
only fire the global `mcp-event://events/latest`.

The inbox resource is a wake-up/status resource. It returns compact status
`{"consumer_id", "checkpoint", "pending_count", "latest_sequence"}` — never
the replay backlog. The durable replay (`consumer_event_pending_list`) is the
source of truth; the live notification is best-effort and level-triggered.

Guarantees:

- Notification happens only AFTER successful persistence.
- A notification failure never fails the already-persisted event.
- No exactly-once delivery: a client that disconnects or a server that
  restarts loses no durable state — re-listen and read the pending list.
- No per-session delivery state; multiple clients on the same consumer may
  all receive the same wake-up.
- The `ResourceUpdated` carries resource identity only — never event payload,
  alert condition, or credentials.

## 13. Live + offline alert delivery (MCP-2B.4C)

The alert system provides **at-least-once** delivery with **durable replay** as
the source of truth. Two evidence classes exist:

### 13.1 Generic alerts — real MCP E2E

```
test_source tick (real subprocess)
    -> server AlertEvaluator.evaluate(event)
    -> publish_event(alert.triggered, routing={"targets":[consumer_id]}, persistent=True)
    -> store.save() succeeds
    -> InMemorySubscriptionBus.publish(ResourceUpdated(uri=inbox_uri))
    -> modern MCP Client.listen() receives ResourceUpdated
    -> consumer_event_pending_list returns the durable event
    -> consumer_event_acknowledge clears it
```

Proven by scenarios A, C, E, F, G, H, K, L, M, N, O, P, Q, T, W.

### 13.2 Market alerts — split proof

**Part A — REAL MCP DURABLE PATH** (scenarios B-PART-A, D-PART-A):

```
market_alert_create (real MCP) -> persists to SQLite
    -> in-process AlertEngine.evaluate(Quote) against shared DB
    -> publish_event(alert.triggered, routing=None, persistent=True)
    -> store.save() succeeds
    -> consumer_event_pending_list (real MCP) returns the event
    -> consumer_event_acknowledge (real MCP) clears it
    -> checkpoint advances
```

**Part B — PRODUCTION IN-PROCESS LIVE-WAKE PATH** (scenarios B-PART-B, D-PART-B):

```
EventStore + AlertEngine(store, bus=RecordingBus)
    -> evaluate(canonical_Quote)
    -> publish_event(alert.triggered, routing=None, persistent=True, bus=recording_bus)
    -> _notify_relevant_consumer_inboxes() calls bus.publish(ResourceUpdated(uri))
    -> RecordingBus captures each URI
```

Proves: persist-before-notify, broadcast to all consumers, notification-failure
durability, exactly-one durable row per trigger.

### 13.3 Intentionally untested

The live broker → MarketService → server-owned AlertEngine → subscriptions/listen
network path is **not exercised** in this offline acceptance phase. There are no
broker credentials and no test-only quote-injection surface was added. The
production code path is verified at the in-process boundary (Part B); the
upstream feed ingress is left for online integration testing.

### 13.4 Fallback behaviors proven

| Scenario | Behavior | Evidence class |
|----------|----------|----------------|
| E | Client offline when alert fires | REAL MCP E2E |
| F | Disconnect during live period | REAL MCP E2E |
| G | Wake received but not acked | REAL MCP E2E |
| H | Ack before disconnect | REAL MCP E2E |
| I | Restart with pending alert | REAL MCP E2E |
| J | Restart after ack | REAL MCP E2E |
| K | Re-subscribe required after restart | REAL MCP E2E |
| L | Existing pending does not block new wake | REAL MCP E2E |
| M | Burst/coalescing | REAL MCP E2E |
| N | Multiple clients same consumer | REAL MCP E2E |
| O | Multiple consumers independent | REAL MCP E2E |
| P | Topic routing isolation | REAL MCP E2E |
| Q | Transient event (no inbox wake) | REAL MCP E2E |
| R | Persistence failure (no notify) | SDK-UNIT |
| S | Notification failure (event persists) | SDK-UNIT |
| T | Legacy client fallback | REAL MCP E2E |
| U | Modern capability discovery | REAL MCP E2E |
| V | Inbox resource read is non-mutating | REAL MCP E2E |
| W | Global resource still fires | REAL MCP E2E |
| X | Exact 42-tool surface preserved | REAL MCP E2E |

### 13.5 Architecture invariants

- **Persist before notify**: `store.save()` completes before any
  `bus.publish(ResourceUpdated)` call. Verified by recording bus asserting
  `pending_count >= 1` at notification time.
- **Best-effort notifications**: Each consumer inbox publish is wrapped in
  try/except — one failing publish cannot suppress others.
- **Durable replay is source of truth**: Even if all live notifications are
  missed, `consumer_event_pending_list` returns all unacknowledged events.
- **Idempotent ack**: Repeated acknowledges succeed silently; first ack time
  is preserved.
- **Checkpoint monotonic**: `advance_checkpoint` never regresses.
- **Broadcast semantics**: `routing=None` (market alerts) notifies all
  registered consumers; `routing.targets=[...]` (generic alerts) notifies
  only listed consumers.
- **Topic routing**: `routing.topics=[...]` intersects with consumer topics.

---

## 14. Advanced Market Condition Alerts (B2)

> **Phase:** B2 — single-quote-backed condition engine
>
> **Schema version:** 13 (two new tables: `condition_alerts`, `condition_runtime_state`)
>
> **Public MCP tools:** NONE — creation/management arrives in B5. The engine
> runs internally and delivery uses the existing `alert.triggered` event +
> MCP replay/wake infrastructure. The 42-tool public surface is unchanged.

### 14.1 Overview

The **advanced market condition alert engine** (`app/condition_alerts.py::ConditionAlertEngine`)
evaluates a single Quote-backed condition against every live canonical quote
that resolves to the alert's instrument identity. It is a per-alert,
in-memory evaluator backed by SQLite for durability and restart safety.

**Key design principles:**

- **Consumer-owned**: every alert has an owner `consumer_id`; events are
  routed only to that consumer's inbox.
- **Provider-neutral**: the engine uses the
  `MarketInstrumentIdentityResolver` — a single alert on one canonical
  identity fires regardless of which broker's quote arrives.
- **Atomic trigger**: a fire persists runtime state + alert row + the
  `alert.triggered` event + consumer materialization in **one SQLite
  transaction**. Any failure rolls back everything. **LOST TRIGGER =
  FORBIDDEN**.
- **Restart-safe**: only `last_result` (LEVEL) or `crossing_side`
  (CROSSING) are persisted. A restarted process continues evaluation
  from the correct state without duplicate-firing.

### 14.2 Alert family: `market_condition`

| Property | Value |
|----------|-------|
| **alert_family** | `market_condition` |
| **Ownership** | Mandatory consumer-owned (internal) |
| **condition_version** | `1` (leaf-only — see §14.4) |
| **Trigger event** | `alert.triggered` (existing) |
| **Routing** | Targeted to alert's `consumer_id` |
| **Live wake** | Existing `ResourceUpdated` inbox notification |
| **Durable replay** | Existing `consumer_event_pending_list` |
| **Acknowledge** | Existing `consumer_event_acknowledge` |
| **Creation tool** | **None** — internal only in B2; public MCP contract in B5 |

### 14.3 `condition_version = 1` (leaf)

Version 1 conditions are **single leaves**:

```json
{
  "condition_version": 1,
  "condition_id": "<uuid>",
  "metric": "ltp",
  "operator": "gt",
  "value": 25000,
  "instrument": {
    "canonical_id": "NSE:EQUITY:INE002A01018"
  }
}
```

### 14.3b `condition_version = 2` (nested groups — B4)

Version 2 conditions support **nested ALL/ANY groups** with a
**same-instrument restriction**: all leaves in a group must resolve to the
same canonical instrument.

- `logic: "all"` — all children must be TRUE for the group to be TRUE
- `logic: "any"` — any child TRUE makes the group TRUE
- Nesting depth max: 8
- Max leaves per group: 64

**Supported** (same instrument, mixed metrics):

```json
{
  "condition_version": 2,
  "logic": "all",
  "conditions": [
    {"condition_version": 1, "metric": "ltp", "operator": "gt",
     "value": 1500,
     "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}},
    {"condition_version": 1, "metric": "volume", "operator": "gt",
     "value": 1000000,
     "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}}
  ]
}
```

**NOT supported** (B7 — multi-instrument groups):

```json
{
  // This would be rejected at creation:
  "conditions": [
    {"instrument": {"exchange": "NSE", "symbol": "RELIANCE"}},
    {"instrument": {"exchange": "NSE", "symbol": "INFY"}}
  ]
}
```

### 14.4 Metric registry (27 metrics)

**Category: PRICE** (9)

| Metric | Quote field |
|--------|-------------|
| `ltp` | `Quote.ltp` |
| `open` | `Quote.open` |
| `high` | `Quote.high` |
| `low` | `Quote.low` |
| `close` | `Quote.close` |
| `change` | `Quote.change` |
| `change_percent` | `Quote.change_percent` |
| `avg_trade_price` | `Quote.avg_trade_price` |
| `last_traded_qty` | `Quote.last_traded_qty` |

**Category: VOLUME / TRADING** (3)

| Metric | Quote field |
|--------|-------------|
| `volume` | `Quote.volume` |
| `total_buy_qty` | `Quote.total_buy_qty` |
| `total_sell_qty` | `Quote.total_sell_qty` |

**Category: OPEN INTEREST** (4)

| Metric | Quote field |
|--------|-------------|
| `open_interest` | `Quote.open_interest` |
| `previous_oi` | `Quote.previous_oi` |
| `oi_change` | `Quote.oi_change` |
| `oi_change_percent` | `Quote.oi_change_percent` |

**Category: ORDER BOOK** (3)

| Metric | Quote field |
|--------|-------------|
| `best_bid` | `Quote.best_bid` |
| `best_ask` | `Quote.best_ask` |
| `spread` | `best_ask - best_bid` (returns None if either side missing) |

**Category: CIRCUITS** (2)

| Metric | Quote field |
|--------|-------------|
| `upper_circuit` | `Quote.upper_circuit` |
| `lower_circuit` | `Quote.lower_circuit` |

**Category: GREEKS** (6)

| Metric | Quote.greeks field |
|--------|-------------------|
| `greeks.delta` | `OptionGreeks.delta` |
| `greeks.gamma` | `OptionGreeks.gamma` |
| `greeks.theta` | `OptionGreeks.theta` |
| `greeks.vega` | `OptionGreeks.vega` |
| `greeks.rho` | `OptionGreeks.rho` |
| `greeks.iv` | `OptionGreeks.iv` |

**Unknown metric behaviour:** If the Quote lacks the field (or greeks
snapshot is absent, or one side of spread is missing), the metric
evaluates to `UNKNOWN`. `None` is NEVER treated as zero.

### 14.5 Operator semantics (8 operators)

All operators compare a **numeric** threshold. Boolean or non-numeric
thresholds are rejected at creation with `ConditionValidationError`.

**LEVEL operators** (eq, ne, gt, gte, lt, lte) — compare metric value
directly to threshold:

| Operator | TRUE condition |
|----------|----------------|
| `eq` | `value == threshold` |
| `ne` | `value != threshold` |
| `gt` | `value > threshold` |
| `gte` | `value >= threshold` |
| `lt` | `value < threshold` |
| `lte` | `value <= threshold` |

**CROSSING operators** (crosses_above, crosses_below) — detect a
threshold **crossing** based on persisted side-of-threshold state:

| Operator | Fire condition |
|----------|---------------|
| `crosses_above` | `previous_side <= threshold` AND `current_value > threshold` |
| `crosses_below` | `previous_side >= threshold` AND `current_value < threshold` |

**Operator and trigger_mode are independent.** A LEVEL operator may use
`once` or `repeat`; a CROSSING operator may also use either mode.

### 14.6 UNKNOWN / FALSE / TRUE semantics

**UNKNOWN** means the metric value is missing (`None`). It is
**semantically distinct from FALSE**.

| Transition | Behaviour |
|------------|-----------|
| `UNKNOWN → TRUE` | **FIRE** (first positive observation) |
| `UNKNOWN → FALSE` | Establish false baseline; **no fire** |
| `FALSE → TRUE` | **FIRE** |
| `TRUE → TRUE` | No fire (already armed) |
| `TRUE → FALSE` | Re-arm (level operators) — **no fire** |
| `FALSE → FALSE` | No fire |
| `TRUE → UNKNOWN` | **No re-arm** — retain current level state |
| `FALSE → UNKNOWN` | **Retains FALSE** — do not fake re-arm |

**CROSSING first observation:** the first valid metric establishes the
side (`above` or `below_or_equal`); it **never fires**. Subsequent
crossings from the opposite side fire per the crossing operator rules.

### 14.7 Trigger modes

| Mode | Behaviour |
|------|-----------|
| `once` | First valid trigger **atomically disables** the alert (`enabled=false`). `one_shot=true` on the event. |
| `repeat` | Alert remains enabled after a trigger; fires again on each new valid transition. `one_shot=false`. |

A `once` alert that triggers is disabled in the DB row (`enabled=0`)
inside the atomic trigger transaction. After a restart, the disabled
alert is not loaded into the engine index.

### 14.8 Provider-neutral identity

The `MarketInstrumentIdentityResolver` maps every provider-specific
identifier to one canonical instrument ID:

| Instrument type | Canonical shape |
|----------------|-----------------|
| EQUITY / ETF | `{exchange}:{type}:{ISIN}` (fallback: normalized symbol) |
| INDEX | `{exchange}:INDEX:{canonical_symbol}` |
| FUTURE | `{exchange}:FUTURE:{underlying}:{expiry_epoch}` |
| OPTION | `{exchange}:OPTION:{underlying}:{expiry_epoch}:{strike}:{option_type}` |

**Exchange normalization:** provider segment suffixes are stripped
(`NSE_EQ`, `NSE_INDEX`, `NSE_FO`, `MCX_FO` → base exchange code) so
Upstox and Fyers rows for the same real instrument converge to the same
canonical ID.

**Collision policy:** if two catalog rows resolve to the same canonical
ID but have conflicting metadata, the resolver **rejects loudly**
(raises). Re-registration of the same alias→canonical pair is
**idempotent**.

**Important:** This resolver is **intentionally isolated** from the
global `InstrumentIdentityRegistry` used by `MarketService`/feeds/storage.
The existing feed keys and global registry semantics are **unchanged**.
Advanced alert identity is provider-neutral only within the condition
engine.

### 14.9 Atomic trigger transaction

**This is the critical invariant.** `EventStore.save_condition_trigger()`
persists four things in **one** `BEGIN IMMEDIATE … COMMIT` transaction:

1. `condition_runtime_state` — upsert `last_result` / `crossing_side`
2. `condition_alerts` — update `trigger_count`, `last_triggered_at`, and
   disable the row if `trigger_mode = 'once'`
3. Insert the persistent `alert.triggered` event
4. Materialize the event into the consumer's durable inbox (routing
   `{"targets": [consumer_id]}`)

**Any failure rolls back the entire transaction.** A partial commit is
impossible.

**Only AFTER** the COMMIT completes does the engine call
`events.finalize_persisted_event()` for the live wake-up path:

```
store.save_condition_trigger(...)         # atomic DB transaction
    -> COMMIT succeeds
    -> events.finalize_persisted_event() # live bus publish, SSE, etc.
```

**LOST TRIGGER = FORBIDDEN.** The engine does NOT advance in-memory
state before the commit. If persistence fails, the trigger is rolled
back and in-memory state is unchanged — the next quote can re-evaluate.

### 14.10 Runtime state (persisted vs in-memory)

**Persisted in `condition_runtime_state` table:**

| Column | LEVEL operators | CROSSING operators |
|--------|----------------|--------------------|
| `last_result` | `unknown` / `false` / `true` | `unknown` (initial) |
| `crossing_side` | — | `unknown` / `above` / `below_or_equal` |

**NOT persisted (by design):**

- `armed` flag — `once` alerts are disabled via the `enabled` column in
  `condition_alerts`; `repeat` is the default
- `previous_value` — in-memory diagnostic only; the persisted side or
  result state is sufficient because condition definitions are immutable
  in B2

### 14.11 Restart safety

After a process restart, the engine reloads enabled alerts and their
runtime state from SQLite:

| Scenario | Post-restart behaviour |
|----------|----------------------|
| LEVEL `repeat`, state = `true` | Continues from TRUE — no fire until TRUE→FALSE then FALSE→TRUE |
| LEVEL `repeat`, state = `false` | Continues from FALSE — fires on next TRUE |
| LEVEL `once`, triggered | Alert is disabled (`enabled=0`); never reloaded |
| CROSSING `repeat`, side = `above` | Continues from above — fires when price goes below then back above |
| CROSSING `once`, triggered | Alert is disabled (`enabled=0`) |

**No duplicate fires** can occur merely because the process restarted.
The persisted state precisely captures the last evaluation result.

### 14.12 Provider data gaps

The identity resolver is canonical and provider-neutral. However:

- **Not every provider supplies every metric.** A Quote from a given
  provider may have `greeks = None` (no Greeks data) or a missing
  `best_bid`/`best_ask` (order book unavailable).
- **Missing metric → UNKNOWN.** The condition engine treats `None` as
  UNKNOWN, not as zero or false.
- **Creation is not rejected** merely because an active provider may
  lack the field. A condition on `greeks.delta` can be created even
  if no provider currently supplies Greeks for the instrument. The
  condition evaluates to UNKNOWN on every tick until data appears.

Do **not** claim every provider supplies every metric. The engine
handles missing data gracefully via the UNKNOWN path.

### 14.13 B2–B5 non-goals (explicitly out of scope)

| Item | Status |
|------|--------|
| AND/OR composition (`logic` field) | **Supported in B4** (v2 groups) |
| Nested condition groups (`conditions[]`) | **Supported in B4** (v2, max depth 8, max 64 leaves) |
| PCR (Put-Call Ratio) analytics | **Added in B6B** (pcr_oi metric) |
| PCR volume analytics | **Added in B6B** (pcr_volume metric) |
| Max Pain analytics | **Added in B6B** (max_pain metric) |
| IV skew analytics | **Added in B6B** (iv_skew metric) |
| GEX (Gamma Exposure) analytics | Deferred — production-correct GEX requires lot-size multiplier not yet available in chain snapshot |
| Historical indicator conditions | Out of scope — future work |
| Market status conditions | Out of scope — future work |
| Public `condition_alert_*` MCP tools | **Added in B5** (5 tools, CONTRACT_VERSION 2.3.0) |
| Cross-instrument expressions | Out of scope — B7 work |
| Multi-instrument v2 groups | Out of scope — B7 work (same-instrument enforced) |
| Mixed quote + analytics groups | Out of scope — B7 work (rejected in B6B) |
| WebUI condition management | Out of scope — future work |

### 14.14 Public MCP availability (B5)

**As of B5, the advanced `market_condition` alert engine is exposed via
5 public MCP tools:**

- `condition_alert_create` — create v1 leaf or v2 nested group alerts
- `condition_alert_list` — list consumer-owned alerts (with enabled filter + limit)
- `condition_alert_get` — get single alert (ownership enforced)
- `condition_alert_set_enabled` — enable/disable with re-arm semantics
- `condition_alert_delete` — delete alert (ownership enforced, history preserved)

**Contract version is now `2.4.0` with 47 public tools and 31 condition metrics.**

### 14.15 Analytics Metrics (B6B)

B6B adds 4 analytics-derived condition metrics that are extracted from an
`OptionChainAnalyticsSnapshot` instead of a `Quote`:

| Metric | Source | Description |
|--------|--------|-------------|
| `pcr_oi` | `OptionChainAnalyticsSnapshot.pcr_oi` | Put-Call Ratio from total OI. `None` when call OI denominator is zero. |
| `pcr_volume` | `OptionChainAnalyticsSnapshot.pcr_volume` | Put-Call Ratio from total volume. `None` when call volume denominator is zero. |
| `max_pain` | `OptionChainAnalyticsSnapshot.max_pain` | Strike with minimum total option-writer payout. `None` when no usable OI data. |
| `iv_skew` | `OptionChainAnalyticsSnapshot.iv_skew` | Average OTM put IV minus average OTM call IV. `None` when no usable IV data. |

**Metric registry:** `market/condition_metrics.py` defines `METRIC_NAMES` (27 Quote-backed),
`ANALYTICS_METRIC_NAMES` (4 chain-derived), `METRIC_SET` (31 total),
`METRIC_SOURCE` (`"quote"` or `"analytics"`), and `METRIC_EVAL_CLASS`
(`"event"` for per-quote or `"snapshot"` for per-refresh).

**Analytics evaluation trigger:** Analytics metrics are evaluated against the
latest `OptionChainAnalyticsSnapshot` from `MarketAnalyticsService`, NOT on
every Quote tick. The `ConditionAlertEngine._evaluate_leaf_v1()` method
checks `METRIC_SOURCE[metric]` and routes to
`extract_analytics_metric()` when the source is `"analytics"`.

**Same-chain restriction (B6B):** All leaves in an analytics v2 group must
share the same `canonical_id` AND the same `expiry`. Mixed-chain or
mixed-expiry groups are rejected at condition normalization time with
`ConditionValidationError`. Mixed quote + analytics groups are also
rejected — this is a B7 feature.

**Max Pain formula:** Uses OI only (not volume or premium). For each candidate
strike S:
```
call_payout(S) = Σ max(0, S - K) × CallOI(K)
put_payout(S)  = Σ max(0, K - S) × PutOI(K)
total_payout(S) = call_payout(S) + put_payout(S)
```
Max Pain = strike with minimum total_payout. Ties broken by lowest strike.
Missing OI on individual legs → that leg contributes zero. No usable OI
across the chain → `max_pain = None`.

**PCR zero-denominator:** Returns `None` (UNKNOWN) when call OI or call volume
is zero, NOT `0.0` or `Infinity`.

**GEX deferred:** Production-correct GEX requires a contract multiplier
(lot size) which is not yet available in the `OptionChainSnapshot`. The
existing `compute_gex()` function is retained for read-only analytics tools
but GEX is NOT added as a condition metric in B6B.

The same production `ConditionAlertEngine` and `EventStore` are reused —
no second engine or store was added. Delivery uses the existing
`alert.triggered` event path + MCP replay/wake infrastructure unchanged.

**v2 group support (B4):** Conditions may use `condition_version: 2` with
nested ALL/ANY groups. All leaves in a group must resolve to the **same
canonical instrument** (same-instrument restriction enforced at creation).

```json
{
  "condition_version": 2,
  "logic": "all",
  "conditions": [
    {"condition_version": 1, "metric": "ltp", "operator": "gt",
     "value": 1500, "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}},
    {"condition_version": 1, "metric": "volume", "operator": "gt",
     "value": 1000000, "instrument": {"exchange": "NSE", "symbol": "RELIANCE"}}
  ]
}
```

**Public instrument references** (no broker tokens required):

| Instrument type | Reference shape |
|----------------|-----------------|
| EQUITY / ETF | `{exchange, symbol}` |
| INDEX | `{exchange, symbol}` |
| FUTURE | `{exchange, underlying, expiry}` |
| OPTION | `{exchange, underlying, expiry, strike, option_type}` |

**Re-enable semantics:** Enabling a disabled alert calls
`reset_condition_runtime_state(alert_id)` which deletes runtime-state
rows. On the next `engine.reload()`, the alert starts from UNKNOWN
(baseline), allowing a `once` alert to fire again.

**UNKNOWN semantics:** Missing metric data → UNKNOWN (not FALSE).
UNKNOWN ≠ FALSE. UNKNOWN does not fake re-arm.

| Transition | LEVEL | CROSSING |
|------------|-------|----------|
| UNKNOWN → TRUE | FIRE | N/A |
| UNKNOWN → FALSE | No fire | First observation establishes side |
| FALSE → TRUE | FIRE | N/A |
| TRUE → TRUE | No fire | N/A |
| TRUE → FALSE | Re-arm (no fire) | N/A |
| TRUE → UNKNOWN | Retain TRUE | N/A |
| FALSE → UNKNOWN | Retain FALSE | N/A |

**CROSSING-group semantics:** A crossing leaf's TRUE is ephemeral — it is
TRUE only on the crossing evaluation tick. Crossing side is restart-safe.
An old crossing does NOT remain TRUE waiting for another leaf later.
In an ALL group: `crosses_above 25000 AND volume > 1M` fires only when
BOTH are TRUE on the **same** evaluation tick.

**Ownership:** Every condition alert is consumer-owned. `get`,
`set_enabled`, and `delete` enforce ownership — cross-owner access
returns not-found (same pattern as market alerts).

---

## 15. Schema version history

| Version | Date | Change |
|---------|------|--------|
| 1 | Initial | Core event store |
| … | … | … |
| 12 | Pre-B2 | Last version before condition alerts |
| **13** | **B2** | **Added `condition_alerts` + `condition_runtime_state` tables** |

The v12→v13 migration (`migrate_v12_to_v13`) adds the two new tables
without modifying any existing tables. Downgrading and reopening a v12
DB migrates transparently to v13.

---

## 16. Code reference

| Component | File |
|-----------|------|
| Metric registry + extraction | `market/condition_metrics.py` |
| Operator constants + validation | `core/persistence/modules/condition_alerts.py` |
| Engine (state machine, index, atomic trigger) | `app/condition_alerts.py` |
| Identity resolver | `app/market_identity.py` |
| Server wiring | `app/server.py` (`_on_market_quote_update` hook) |
| Error class | `core/errors.py::ConditionValidationError` |