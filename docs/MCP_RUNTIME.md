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