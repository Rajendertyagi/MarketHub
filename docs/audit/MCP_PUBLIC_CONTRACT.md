# MCP Event Server — Public Contract

**Version:** 2.2.0 (FINALIZED) · MCP Spec: 2026-07-28
**Status:** FINALIZED — 42-tool public surface frozen (MCP-2B.3D)
**Last reviewed:** 2026-08-28
**Frozen:** 2026-08-28 — frozen after MCP-2B.3D finalization (42-tool surface, 16 MCP-2B tools, zero dev_*)

---

## 1. Endpoint & Transport

| Item | Value | Classification |
|------|-------|---------------|
| Transport | `streamable-http` | **FROZEN** |
| Path | `/mcp` | **FROZEN** |
| Host | Configurable (`config.json` → `host`) | Runtime config — not protocol |
| Port | Configurable (`config.json` → `port`) | Runtime config — not protocol |
| `stateless_http` | `True` | **FROZEN** |
| `json_response` | `True` | **FROZEN** |
| `max_request_body_size` | From config (`max_request_body_size_mb * 1024 * 1024`) | Runtime config |
| `transport_security` | Explicit `TransportSecuritySettings` (DNS rebinding protection enabled; localhost-only allowed hosts/origins) | Runtime config — constructed from `config.json` keys `enable_dns_rebinding_protection`, `allowed_hosts`, `allowed_origins` |

**Client connection:** Connect to `http://{host}:{port}/mcp`. Use the MCP Python SDK `streamable_http_client` with `ClientSession`.

**HTTP liveness endpoint (not MCP):** `GET /health` → `200 {"status": "ok"}`. Unauthenticated. Does not require MCP initialization. Intended for local watchdog / process supervisor.

---

## 2. Public Tool Surface (42 tools)

### Frozen MCP-1 Tools (26)

These tools form the core market-data read layer. They are provider-agnostic and read-only.

| Tool | Parameters | Description | Status |
|------|-----------|-------------|--------|
| `system_ping` | *(none)* | Health check. Returns `{"status": "ok", "message": "...", "timestamp": "..."}` | **FROZEN** |
| `market_quote` | `instrument_ref: str` | Latest canonical quote | **FROZEN** |
| `market_depth` | `instrument_ref: str` | Latest L2 order book | **FROZEN** |
| `market_status` | *(none)* | MarketService diagnostic counters | **FROZEN** |
| `instrument_search` | `q: str`, optional filters | Search instruments | **FROZEN** |
| `watchlists` | *(none)* | List persistent watchlists | **FROZEN** |
| `market_history` | `instrument_ref`, `unit`, `interval`, `from_date`, `to_date` | Historical OHLCV candles | **FROZEN** |
| `option_chain` | `underlying`, optional `expiry`, `window` | Option chain | **FROZEN** |
| `futures_contracts` | `underlying`, optional `expiry` | Futures contracts | **FROZEN** |
| `compute_pcr` | `underlying`, optional `expiry` | Put-Call Ratio | **FROZEN** |
| `compute_max_pain` | `underlying`, optional `expiry` | Max pain strike | **FROZEN** |
| `compute_top_oi_strikes` | `underlying`, optional `expiry`, `n` | Top OI strikes | **FROZEN** |
| `compute_atm` | `underlying`, optional `expiry` | ATM strike | **FROZEN** |
| `compute_iv_skew` | `underlying`, optional `expiry` | IV skew | **FROZEN** |
| `compute_oi_buildup` | `underlying`, optional `expiry` | OI buildup count | **FROZEN** |
| `compute_support_resistance` | `underlying`, optional `expiry` | Support/resistance strikes | **FROZEN** |
| `compute_straddle` | `underlying`, optional `expiry` | Straddle cost | **FROZEN** |
| `compute_gex` | `underlying`, optional `expiry` | Gamma Exposure proxy | **FROZEN** |
| `compute_futures_basis` | `underlying` | Futures basis | **FROZEN** |
| `price_long_straddle` | `underlying`, optional `expiry`, `strike` | Long straddle price | **FROZEN** |
| `price_long_strangle` | `underlying`, `call_strike`, `put_strike`, optional `expiry` | Long strangle price | **FROZEN** |
| `price_bull_call_spread` | `underlying`, `lower_strike`, `higher_strike`, optional `expiry` | Bull call spread price | **FROZEN** |
| `price_bear_put_spread` | `underlying`, `higher_strike`, `lower_strike`, optional `expiry` | Bear put spread price | **FROZEN** |
| `price_iron_condor` | `underlying`, `put_sell_strike`, `put_buy_strike`, `call_buy_strike`, `call_sell_strike`, optional `expiry` | Iron condor price | **FROZEN** |
| `price_long_butterfly` | `underlying`, `lower_strike`, `middle_strike`, `upper_strike`, optional `expiry` | Long butterfly price | **FROZEN** |
| `analyze_option_chain` | `underlying`, optional `expiry`, `max_strikes` | Bundled chain analysis | **FROZEN** |

### Finalized MCP-2B Tools (16)

These tools were previously deferred and are now finalized as part of the public contract.

#### Generic Alerts (5)

| Tool | Parameters | Description | Status |
|------|-----------|-------------|--------|
| `alert_create` | `consumer_id`, `source`, `field_path`, `operator`, `value`, optional `name`, `event_type`, `one_shot` | Create a generic alert definition | **FROZEN** |
| `alert_list` | `consumer_id`, optional `enabled` | List alert definitions | **FROZEN** |
| `alert_get` | `consumer_id`, `alert_id` | Get a single alert definition | **FROZEN** |
| `alert_enable` | `consumer_id`, `alert_id` | Enable a disabled alert | **FROZEN** |
| `alert_disable` | `consumer_id`, `alert_id` | Disable an alert | **FROZEN** |

#### Event Inspection (1)

| Tool | Parameters | Description | Status |
|------|-----------|-------------|--------|
| `event_list` | `limit` (int, default 10) | **DIAGNOSTICS/OSERVATIONAL ONLY** — returns recent in-memory events from the server's history buffer. Does NOT reflect per-consumer delivery state, acknowledgements, or checkpoints. For durable per-consumer replay use `consumer_event_pending_list`. | **FROZEN** |

#### Consumer / Replay (6)

| Tool | Parameters | Description | Status |
|------|-----------|-------------|--------|
| `consumer_register` | `consumer_id` (str) | Register a durable consumer identity. Idempotent. Creates checkpoint at 0. | **FROZEN** |
| `consumer_topic_add` | `consumer_id` (str), `topic` (str) | Assign a topic to a consumer for topic-based routing. Useful only where publishers route by topic. | **FROZEN** |
| `consumer_event_pending_list` | `consumer_id` (str), `limit` (int, default 50), `after_sequence` (int\|null, optional) | Canonical durable replay tool. Returns unacknowledged persistent events from consumer's checkpoint (or from explicit `after_sequence` for pagination). Primary reconnect tool. Returns `next_after_sequence` for paging. Does NOT acknowledge and does NOT advance the checkpoint. | **FROZEN** |
| `consumer_event_acknowledge` | `consumer_id` (str), `event_id` (str) | ACK an event for a consumer. Idempotent. Advances checkpoint. | **FROZEN** |
| `consumer_checkpoint_get` | `consumer_id` (str) | Get the consumer's current durable checkpoint sequence. | **FROZEN** |

#### Market Alerts (5)

| Tool | Parameters | Description | Status |
|------|-----------|-------------|--------|
| `market_alert_create` | `consumer_id`, `symbol`, `exchange`, `operator` (gt/lt/crosses_above/crosses_below), `value`, optional fields (ltp/change_percent/volume/oi_change_percent) | Create a market-data alert | **FROZEN** |
| `market_alert_list` | `consumer_id`, optional `enabled` | List market alerts | **FROZEN** |
| `market_alert_enable` | `consumer_id`, `alert_id` | Re-arm a triggered market alert (restores to inactive state) | **FROZEN** |
| `market_alert_disable` | `consumer_id`, `alert_id` | Disable a market alert | **FROZEN** |
| `market_alert_delete` | `consumer_id`, `alert_id` | Delete a market alert | **FROZEN** |

---

## 3. Removed Tools

### event_publish (removed in MCP-2B.3D)

| | |
|---|---|
| **Status** | REMOVED from public MCP registry |
| **Reason** | Manual/test-oriented tool with arbitrary JSON injection surface; no established external-AI production need |
| **Internal pipeline** | `core.events.publish_event()` remains the canonical internal publication path (used by sources, alert engine, internal code) |
| **Replacement** | None — sources publish via the internal pipeline; consumers replay via `consumer_event_pending_list` |

### consumer_event_list (removed in MCP-2B.3C)

| | |
|---|---|
| **Status** | REMOVED from public MCP registry |
| **Replacement** | `consumer_event_pending_list(after_sequence=0)` for from-beginning replay |
| **Note** | Acknowledged events are NOT resurrected by `after_sequence=0` |

---

## 4. Resources (6)

| Resource URI | Data Shape | Description | Status |
|-------------|-----------|-------------|--------|
| `mcp-event://events/latest` | Event dict (see §5) | The most recently published event. Updated via `ResourceUpdated` notification. | **FREEZE** |
| `mcp-event://events/pending` | Array of event dicts (newest first, max 100) | All persistent events, newest first. | **FREEZE** |
| `mcp-event://events/recent` | Array of event dicts (newest first, max 200) | Bounded durable observational journal. Includes both persistent and nonpersistent events. **NOT pending delivery, NOT replay API, NOT ACKable, NOT checkpoint input.** Restart-safe. | **v1.2.0-candidate** |
| `mcp-event://system/info` | Dict with server metadata | Server name, version, features, limits, endpoint, uptime, counts. See §17 for field classification. | **FREEZE** |
| `mcp-event://system/metrics` | Dict with operational metrics | Process-wide counters: events, alerts, notifications, sources, recent history, system aggregates. No secrets or payloads. | **v1.2.0-candidate** |
| `mcp-event://sources/status` | Dict of source status objects | Status of each registered source connector. Includes name, type, state, error, cursor, dedup stats. Secrets are sanitized. | **FREEZE** |

---

## 5. Event Schema

### Published Event Shape

```json
{
  "id": "32-char lowercase UUID v4 hex",
  "type": "dot.namespaced.identifier",
  "source": "source-identifier",
  "timestamp": "ISO 8601 with UTC offset",
  "data": {},
  "persistent": false,
  "sequence": 42,
  "routing": {
    "targets": ["consumer_id_1"],
    "topics": ["alpha", "beta"]
  }
}
```

| Field | Type | Required | Frozen? | Notes |
|-------|------|----------|---------|-------|
| `id` | string (32 chars) | Always | **FREEZE** | UUID v4 hex. Collision-resistant. Stable identity. |
| `type` | string | Always | **FREEZE** | Dot-namespaced. Strip whitespace. Convention: `domain.action` (e.g. `alert.triggered`, `broker.price_changed`). |
| `source` | string | Always | **FREEZE** | Identifies the origin connector/instance. Strip whitespace. NOT the connector type — the instance name. |
| `timestamp` | string | Always | **FREEZE** | UTC ISO 8601. Generated server-side at publish time. |
| `data` | object | Always | **FREEZE** | Arbitrary JSON-compatible dict. Empty `{}` when none. |
| `persistent` | bool | Always | **FREEZE** | True if stored to SQLite. False for transient events. |
| `sequence` | int | When persistent | **FREEZE** | Monotonic SQLite auto-increment. Assigned at publish time. Only present when `persistent=True`. |
| `routing` | object | Optional | **FREEZE** | Present only when routing metadata was provided at publish. See §15. |

### Event ID Contract

- Format: `uuid.uuid4().hex` → 32 lowercase hex characters
- Generation: server-side at publish time
- Stability: immutable once assigned
- Broker should treat `id` as the stable event identity

### Event Type Convention

Recommended convention: `domain.action`

Examples:
- `alert.triggered` — alert engine fired
- `broker.price_changed` — market data update
- `source.failed` — source connector failure
- `system.warning` — server-side warning

The broker project should adopt this convention for its own event types.

---

## 6. Routing Contract

Routing metadata is **frozen at publication time**. It is never recomputed from current consumer subscriptions/topics.

| Routing value | Meaning |
|--------------|---------|
| `null` / absent | Broadcast — event is relevant to ALL registered consumers |
| `{"targets": ["c1", "c2"]}` | Targeted — event is relevant ONLY to listed consumers |
| `{"topics": ["alpha", "beta"]}` | Topic-based — event is relevant to consumers whose subscribed topics intersect |
| `{"targets": [...], "topics": [...]}` | Both — relevant to listed targets OR consumers with matching topics |
| `{"targets": []}` | Broadcast — empty targets list is treated as no-target filter (all consumers) |
| `{"topics": []}` | Broadcast — empty topics list is treated as no-topic filter (all consumers) |

**Important:** Routing is materialized into `consumer_event_state` at publish time. A consumer's later topic changes do NOT affect historical event relevance.

---

## 7. Consumer Identity Contract

| Aspect | Rule |
|--------|------|
| Meaning | Stable, durable application-level identity |
| Who creates | The client/application via `consumer_register` |
| Lifetime | Persistent across server restarts (stored in SQLite) |
| Re-registration | Idempotent — repeated calls are no-ops |
| Case | Case-sensitive (treated as opaque string) |
| Characters | Any non-empty string (no validation beyond non-empty after trim) |
| NOT bound to | MCP session, HTTP connection, Context, ClientSession, transport |

---

## 8. Delivery Contract

| Aspect | Rule |
|--------|------|
| `delivered_at` | First time the event was retrieved via `consumer_event_pending_list` for this consumer |
| Preserved | First delivery time is preserved on subsequent replays (CASE WHEN NULL) |
| NOT delivery | `ResourceUpdated` notification, publication, or SQLite write alone does NOT count as delivery |
| Idempotent | Repeated calls to `mark_delivered` for same (consumer, event) pair preserve first timestamp |

---

## 9. Acknowledgement Contract

| Aspect | Rule |
|--------|------|
| `consumer_event_acknowledge(consumer_id, event_id)` | Marks an event as processed by a consumer |
| Idempotent | Repeated calls succeed silently; first `acknowledged_at` timestamp is preserved |
| Per-consumer | Each consumer has independent ACK state per event |
| Does NOT delete | The persistent event remains in `persistent_events` after ACK |
| Checkpoint advance | After ACK, `advance_checkpoint` may advance the consumer's durable cursor |
| Unknown consumer | Raises `ConsumerNotFoundError` (internal application exception) → SDK wraps as `CallToolResult(is_error=True)`; client sees semantic message `consumer not found: <consumer_id>` |
| Unknown event | Raises `EventNotFoundError` → SDK wraps as error |
| Irrelevant event | Raises `EventNotRelevantError` → SDK wraps as error |

---

## 10. Checkpoint Contract

**Invariant:** The checkpoint is the highest persistent sequence `N` such that there is no relevant, unacknowledged event with sequence ≤ `N`.

| Aspect | Rule |
|--------|------|
| Purpose | Durable cursor for replay/reconnect |
| Monotonic | Never regresses — `MAX(current, candidate)` |
| Gap-tolerant | Irrelevant events (not in consumer's `consumer_event_state`) do NOT block advancement |
| Initial value | 0 (created at `consumer_register`) |
| Advance trigger | Called after `consumer_event_acknowledge`; can also be called independently |
| `consumer_checkpoint_get` | Reports `{consumer_id, checkpoint, updated_at}` — `updated_at` is the persisted ISO-8601 timestamp of the last checkpoint write (registration or advance) |

---

## 11. Replay Contract

| Aspect | Rule |
|--------|------|
| Tool | `consumer_event_pending_list(consumer_id, limit=50)` |
| Cursor | Starts from consumer's checkpoint (`last_sequence`) |
| Filter | Events with `sequence > checkpoint`, relevant to consumer, unacknowledged |
| Order | Ascending by sequence (no OFFSET pagination) |
| Default limit | 50 |
| Max limit | 500 (`MAX_REPLAY_LIMIT`) |
| Semantics | At-least-once — events may be returned multiple times if not ACKed |
| Delivery marking | Returned events are marked as delivered (preserving first delivery time) |
| Unknown consumer | Raises `ConsumerNotFoundError` → SDK wraps as `CallToolResult(is_error=True)` |

---

## 12. Subscription / Notification Contract

| Aspect | Rule |
|--------|------|
| Mechanism | MCP `subscriptions/listen` + `ResourceUpdated` |
| Resource | `mcp-event://events/latest` |
| Trigger | Every call to `publish_event(persistent=True/False)` |
| Content | Notification carries URI only; client must `read_resource` or query tools for payload |
| Durable vs live | `ResourceUpdated` is a **live signal only** — it does NOT carry event history |
| Reconnect flow | Client receives `ResourceUpdated` → reads `mcp-event://events/latest` → calls `consumer_event_pending_list` for history |
| Missing bus | If bus not initialized, notification is silently skipped (clients can always poll) |

---

## 12a. Final Delivery Model (MCP-2B.3D)

```
alert triggers
    → persistent alert.triggered event
    → durable event store (SQLite)
    → consumer_event_pending_list (replay)
    → external AI consumer processes event
    → consumer_event_acknowledge (ACK)
    → checkpoint advances monotonically
```

**Current guaranteed delivery:** Durable replay via MCP tool calls (`consumer_event_pending_list` → `consumer_event_acknowledge`).

**Future planned live delivery:** Streamable MCP live notification/subscription work belongs to MCP-2B.4. Not yet implemented. Do NOT claim live unsolicited MCP alert delivery.

---

## 12b. Reconnect Workflow

```
consumer_register(consumer_id)
    ↓
create alert (optional)
    ↓
disconnect (client goes offline)
    ↓
alert triggers and persists (alert.triggered → SQLite)
    ↓
reconnect (client returns)
    ↓
consumer_event_pending_list(consumer_id)  ← replay pending events
    ↓
process event (external AI)
    ↓
consumer_event_acknowledge(consumer_id, event_id)
    ↓
consumer_checkpoint_get(consumer_id)  ← verify checkpoint advanced
```

This is the current guaranteed solution for at-least-once event delivery with reconnect support.

---

## 12c. At-Least-Once Semantics (FROZEN)

| Aspect | Rule |
|--------|------|
| Persistent events remain pending | Until explicitly acknowledged via `consumer_event_acknowledge` |
| Replay may return same event again | Yes — if not ACKed, subsequent replays return it again |
| Replay does not imply processing success | A returned event may have failed downstream processing |
| Replay does not advance checkpoint | Only `acknowledge` + internal `advance_checkpoint` advances the cursor |
| Acknowledge advances checkpoint monotonically | `MAX(current, candidate)` — never regresses |
| **NOT exactly-once** | No exactly-once delivery guarantee exists or is claimed |

---

## 12d. Live Notification Status (Roadmap)

| Item | Status |
|------|--------|
| `event_publish` public tool | REMOVED (MCP-2B.3D) |
| MCP subscription resources | NOT implemented |
| `stateless_http` change | NOT changed |
| GET SSE MCP streams | NOT added |
| Server-initiated notification code | NOT added |
| Live unsolicited alert delivery | **FUTURE — MCP-2B.4** |

Live notification/subscription work is deferred to a future phase (MCP-2B.4) after existing MCP completion. The current architecture supports it (subscription bus exists), but no subscription resources, SSE MCP streams, or server-initiated notification code have been added.

---

## 13. Error Contract

| Pattern | Behavior |
|---------|----------|
| Validation error (bad params) | Raises `ValidationError` → SDK wraps as `CallToolResult(is_error=True)` |
| Storage error (DB failure) | Raises `StorageError` → SDK wraps as `CallToolResult(is_error=True)` |
| Timeout error | Raises `OperationTimeoutError` → SDK wraps as `CallToolResult(is_error=True)` |
| Consumer not found | Raises `ConsumerNotFoundError` (internal application exception) → SDK wraps as `CallToolResult(is_error=True)`; client sees semantic message `consumer not found: <consumer_id>` |
| Protocol error | Reserved for `MCPError` (genuine protocol-level failures only) |

**Unknown-consumer policy (v1.0.0):** All four production operations that require an existing consumer — `consumer_topic_add`, `consumer_event_pending_list`, `consumer_event_acknowledge`, `consumer_checkpoint_get` — raise `ConsumerNotFoundError` for an unregistered consumer. The MCP SDK exposes this as `CallToolResult(is_error=True)` with the semantic message `consumer not found: <consumer_id>`. `consumer_register` remains an idempotent create/register operation. `ConsumerNotFoundError` is an **internal application/domain exception**, not a public MCP protocol type; broker clients must depend on `is_error=True` and the semantic message, not on Python exception class names.

**MCP-2B.3C compatibility note:** `consumer_event_list` was removed from the public MCP surface (visible tool count 44→43). Use `consumer_event_pending_list(after_sequence=0)` for the same from-beginning replay of a consumer's relevant persistent events. The underlying store query (`list_relevant_events`) remains available to internal code and tests.

**MCP-2B.3D compatibility note:** `event_publish` was removed from the public MCP surface (visible tool count 43→42). The canonical internal `core.events.publish_event()` pipeline remains intact for sources and the alert engine. External clients use `consumer_event_pending_list` for durable replay and `consumer_event_acknowledge` for acknowledgment.

---

## 14. Source Contract

| Concept | Meaning |
|---------|---------|
| Source type | Implementation class (e.g. `http_poller`, `test_source`) — mapped via `sources/registry.py` `SOURCE_TYPES` |
| Source name | Instance identifier from config (`source_name` key). Used for cursor/dedup identity. |
| Cursor | Durable progress marker stored in `source_state` table under key `"cursor"` |
| Dedup identity | Composite `(source_name, external_id)` in `source_seen_items` table |
| Status | Available via `mcp-event://sources/status` resource. URL secrets sanitized. |

---

## 15. `mcp-event://events/pending` Resource (formerly `alerts://pending`)

**RESOLVED BEFORE v1.0.0 FREEZE:** The resource was renamed from `alerts://pending` to `mcp-event://events/pending`. It returns ALL persistent events (not just "alerts"), newest first (max 100). The old `alerts://pending` URI no longer exists. The generic persistent-event listing is intentionally generic; a future alert engine will use separate `alert_definitions` / `alert_triggers` tables (see §16) and will NOT reuse this resource.

---

## 16. Generic Alert Engine (v1.1.0-candidate — NOT FROZEN)

Implemented in v1.1.0-candidate (additive on frozen v1.0.0). The engine is generic: it
matches a published event against persisted alert definitions and, on a match, publishes a
canonical `alert.triggered` event via the same `publish_event()` path used by all sources.
No broker-specific logic is present.

| Concept | Implemented name / pattern |
|---------|---------------------------|
| Alert definition | Table `alerts` (single table; supersedes the reserved `alert_definitions` + `alert_triggers` split from the pre-freeze plan) |
| Alert identity | `alert_id` (UUID v4 hex), `consumer_id` (owner) |
| Condition | `field_path` (dotted, e.g. `data.price`), `operator` (`eq`, `ne`, `gt`, `gte`, `lt`, `lte`), `value` (JSON scalar) |
| Optional scoping | `event_type` (null = any type from `source`), `source` (required) |
| Lifecycle | `enabled` (bool), `one_shot` (bool, default True → auto-disables after one trigger) |
| Trigger event | `type="alert.triggered"`, `source="alert_engine"`, `persistent=True`, `routing.targets=[consumer_id]` |
| Trigger payload | `alert_id`, `consumer_id`, `matched_event_id`, `matched_event_type`, `matched_source`, `field_path`, `operator`, `expected_value`, `observed_value`, `one_shot` (+ `name` when set) |

**Semantics (v1.1.0-candidate):**
- Field absence (`field_path` not present in `data`) → condition does NOT match. JSON `null` is a legitimate value that participates in `eq`/`ne`.
- `eq`/`ne`: scalar equality (type-aware; bool distinct from numeric). `gt`/`gte`/`lt`/`lte`: numeric only (bool is NOT numeric); non-numeric observed/expected → no match (not an error). No string→number coercion.
- `alert_enable` / `alert_disable` return `changed=true` only when the state actually flips; idempotent calls return `changed=false`. `AlertNotFoundError` is raised for unknown/non-owned alerts; `ConsumerNotFoundError` for unknown consumers.
- Recursion guard: `alert.triggered` events are never re-evaluated as alert input.
- Per-alert concurrency lock prevents double-trigger within a process; `one_shot` alerts disable atomically after the trigger event is published.

**No naming conflicts** with the frozen v1.0.0 event schema or tool names. The `alerts://pending`
URI was renamed to `mcp-event://events/pending` before freeze (see §15).

---

## 17. mcp-event://system/info Field Classification

| Field | Classification | Notes |
|-------|---------------|-------|
| `name` | **FREEZE** | Server identity from config |
| `version` | **FREEZE** | App version (semver) |
| `purpose` | Internal | Descriptive, not contract-critical |
| `transport` | **FREEZE** | Fixed: `streamable-http` |
| `endpoint` | **FREEZE** | Derived from config host/port |
| `python` | Internal | Build detail — broker should not depend on |
| `mcp_sdk` | Internal | SDK version — may change |
| `mcp_spec` | **FREEZE** | Protocol spec date |
| `event_resource` | **FREEZE** | URI constant |
| `events_pending_resource` | **FREEZE** | URI constant (`mcp-event://events/pending`) |
| `info_resource` | **FREEZE** | URI constant (`mcp-event://system/info`) |
| `event_count` | Diagnostic | Dynamic counter |
| `persistent_event_count` | **FREEZE** | Count of all persistent events |
| `consumer_count` | Diagnostic | Dynamic counter |
| `uptime_seconds` | Diagnostic | Derivable |
| `started_at` | Diagnostic | Derivable |
| `features` | **FREEZE** | Feature capability map |
| `limits` | **FREEZE** | Config-driven limits |

---

## 18. Compatibility Policy

### After contract freeze:

| Change type | Allowed? | Notes |
|------------|----------|-------|
| Add new tool | ✅ Additive | Must not conflict with existing names |
| Add optional event fields | ✅ Additive | Consumers should ignore unknown fields |
| Add new resource URI | ✅ Additive | Must not conflict with existing URIs |
| Rename tool | ❌ Breaking | Requires contract version bump |
| Rename resource URI | ❌ Breaking | Requires contract version bump |
| Change event field meaning | ❌ Breaking | Must not silently change semantics |
| Change routing semantics | ❌ Breaking | Routing freeze at publication is a hard invariant |
| Remove tool | ❌ Breaking | Must deprecate first |
| Change default parameter values | ⚠️ Risky | May break clients relying on defaults |
| Change error shape | ⚠️ Risky | SDK wraps all app exceptions as `is_error=True` |

### Recommended versioning approach:

Add a `contract_version` field to `mcp-event://system/info` when ready:
```json
{"contract_version": "1.0.0", ...}
```

This allows the broker project to declare compatibility requirements.

---

## 19. Broker Project Integration Rules

### ✅ Safe to depend on:

- MCP tool names and parameters (as listed in §2)
- Resource URIs (as listed in §4)
- Event schema fields (as listed in §5)
- Routing semantics (as listed in §6)
- Consumer identity semantics (as listed in §7)
- Checkpoint/replay semantics (as listed in §§9-11)
- Subscription/notification model (as listed in §12)

### ❌ Must NOT depend on:

- `store_modules/*` — internal persistence modules
- `server_modules/*` — internal server modules
- `EventStore` class directly — use MCP tools
- SQLite schema details — use tools
- Internal logger names
- Process IDs or internal globals
- Test tool names (prefix `_test` or listed in §3)

---

## 19. Canonical Contract Module

Public MCP contract identifiers are canonically defined in:

    server_modules/contract.py

Production code imports from this module rather than redefining literals.
External clients depend on literal protocol values, not internal Python module paths.

Ownership model:
    MCP public contract identifiers  → server_modules/contract.py
    Runtime/deployment defaults       → server_modules/config.py
    Schema/migration version          → store_modules/schema.py
    Domain exceptions                 → errors.py
    Event-core behavior               → events.py
    Source type registry              → sources/registry.py

Engineering principle: ONE STABLE CONCEPT → ONE CANONICAL OWNER → IMPORT/REUSE.

---

## 19b. mcp-event://system/metrics Resource (v1.2.0-candidate)

**Status:** CANDIDATE · NOT FROZEN

Returns a JSON object with process-wide operational counters. No secrets, payloads, tokens, or filesystem paths are exposed. All counters are process-lifetime; they reset on restart.

```json
{
  "started_at": "ISO8601",
  "uptime_seconds": 123.4,
  "events": {
    "published_total": 0,
    "persistent_total": 0,
    "nonpersistent_total": 0,
    "publication_failures_total": 0,
    "alert_triggered_total": 0
  },
  "alerts": {
    "evaluations_total": 0,
    "matches_total": 0,
    "failures_total": 0
  },
  "notifications": {
    "attempted_total": 0,
    "failed_total": 0
  },
  "sources": {
    "published_total": 0,
    "failures_total": 0
  },
  "recent_history": {
    "failures_total": 0,
    "count": 0,
    "capacity": 200
  },
  "system": {
    "persistent_event_count": 0,
    "persistent_high_water": 0,
    "consumer_count": 0,
    "pending_deliveries": 0
  }
}
```

**Counter semantics:**
- `events.publication_failures_total` counts `publish_event` attempts that fail **before** successful acceptance (validation failures + persistent-save failures). It does NOT count recent-journal, notification, or alert-evaluation failures (those have their own counters).
- `alerts.evaluations_total` increments once per event handed to the evaluator (not once per candidate).
- `alerts.matches_total` increments per matching alert definition.
- `alerts.failures_total` counts unexpected evaluator exceptions.
- `sources.published_total` is a distinct dimension from `events.published_total`; a source event increments both (source-originated subset).
- `system.persistent_event_count = COUNT(*)`; `system.persistent_high_water = MAX(sequence)`.

---

## 19c. mcp-event://events/recent Resource (v1.2.0-candidate)

**Status:** CANDIDATE · NOT FROZEN

Returns the bounded durable observational event journal (max 200 events, newest-first).

**Semantics:**
- **Observational only** — not pending delivery, not replay API, not ACKable, not checkpoint input.
- Includes both `persistent=True` and `persistent=False` events.
- Survives restart (hydrated from SQLite on startup).
- A nonpersistent event in this journal does NOT enter `consumer_event_state`, pending, ACK, or checkpoint.

**Response shape:** Array of event dicts (same schema as §5), newest first.

---

## 19d. consumer_event_pending_list Pagination (v1.2.0-candidate)

**Status:** CANDIDATE · NOT FROZEN

The tool now accepts an optional `after_sequence` parameter for pagination:

```
consumer_event_pending_list(
    consumer_id: str,
    limit: int = 50,
    after_sequence: int | None = None
)
```

- `after_sequence = None` → existing checkpoint-based behavior (unchanged).
- `after_sequence = N` → returns relevant pending events with `sequence > N`.
- Returns `next_after_sequence` which is valid input for the next page.
- No OFFSET; ascending sequence; max limit 500.
- ACK and checkpoint semantics unchanged.
- Validation rejects `bool`, float, negative, or string values.

---

## 20. Open Issues (Tracked Separately — Non-Blocking for Frozen Contract)

| # | Issue | Severity | Status |
|---|-------|----------|--------|
| 1 | `alerts://pending` → `mcp-event://events/pending` rename | **RESOLVED** | Renamed before freeze; old URI removed |
| 2 | `persistent_alert_count` → `persistent_event_count` rename | **RESOLVED** | Renamed before freeze |
| 3 | Unknown consumer behavior inconsistency | **RESOLVED** | All five consumer-requiring tools now raise `ConsumerNotFoundError` → `is_error=True` with `consumer not found: <id>` |
| 4 | `mcp-event://system/info` exposes `python` and `mcp_sdk` versions | **LOW** | Classified as diagnostic (not frozen broker dependency) |
| 5 | `json_response=True` + background publication acceptance test not yet run | **INFO** | Tracked separately; architecture supports it; not a contract blocker |
| 6 | Test harness teardown port-race (WinError 10048/10053) discovered during verification | **HARNESS** | Tracked separately; NOT a contract defect; does not affect the frozen public contract |

---

## 21. Contract Status

| Section | Status |
|---------|--------|
| Endpoint & Transport | ✅ FROZEN |
| Public Tools (42) | ✅ FROZEN (26 MCP-1 + 16 MCP-2B) |
| Dev/Test Tools | ✅ NONE (removed in v2.0.0, zero dev_* present) |
| Resources | ✅ FROZEN |
| Event Schema | ✅ FROZEN |
| Routing | ✅ FROZEN |
| Consumer Identity | ✅ FROZEN |
| Delivery | ✅ FROZEN |
| ACK | ✅ FROZEN |
| Checkpoint | ✅ FROZEN |
| Replay | ✅ FROZEN |
| At-Least-Once Semantics | ✅ FROZEN |
| Error Contract | ✅ FROZEN |
| Source Contract | ✅ FROZEN |
| Versioning Policy | ✅ FROZEN |
| Live Notification | 📋 DEFERRED to MCP-2B.4 |
| Generic Alert Engine | ✅ FROZEN (5 tools) |
| Market Alert Engine | ✅ FROZEN (5 tools) |
| Observability / Metrics | ⚠️ v1.2.0-candidate — NOT FROZEN |
| Durable Recent History | ⚠️ v1.2.0-candidate — NOT FROZEN |
| Replay Pagination | ⚠️ v1.2.0-candidate — NOT FROZEN |

---

## 22. Verdict

```text
PUBLIC MCP CONTRACT v2.2.0 — FINALIZED
```

**Finalized:** 2026-08-28 — MCP-2B.3D public contract finalization (42-tool surface frozen).

**Frozen surface:**
- 26 frozen MCP-1 tools (market data, analytics, strategy pricing)
- 16 finalized MCP-2B tools (alerts, event diagnostics, consumer/replay, market alerts)
- 0 dev_* tools

**Removed tools (documented):**
- `consumer_event_list` → replaced by `consumer_event_pending_list(after_sequence=0)` (MCP-2B.3C)
- `event_publish` → removed from public registry; internal `publish_event()` pipeline unchanged (MCP-2B.3D)

**Deferred (not implemented):**
- Live MCP notification/subscription (roadmap: MCP-2B.4)

**Historical frozen baselines:**
- v1.0.0 — initial MCP-0 foundation
- v1.1.0-candidate — alert engine (now frozen)
- v1.2.0-candidate — observability features (still candidate)
- v2.0.0 — MCP-1 cleanup (removed 7 dev tools)
- v2.1.0 — MCP-2B.3C (removed consumer_event_list)
- **v2.2.0 — MCP-2B.3D (removed event_publish, finalized 16 MCP-2B tools)**
