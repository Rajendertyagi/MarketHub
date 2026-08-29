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