# FYERS TBT Corrective Implementation Plan (P0-A)

**Date:** 2026-11-10
**Repository:** `Rajendertyagi/MarketHub`
**Branch:** `review/scanner-phase1`
**HEAD:** `bab6d1a7869ad8aeb7a2431e32e997596adbce90` (unchanged)
**Status:** TBT-01/02/03 complete; TBT-04 (shutdown + corrective recv-task cleanup), TBT-05 (authentication), TBT-06 (channel protocol), TBT-05a (symbol propagation/runtime sync), TBT-08 (fresh-snapshot gating after invalidation), TBT-07 (entitlement-aware limits) implemented and tested with mocks (no live broker verification). Remaining: TBT-09 live runtime verification (broader mocked regression recorded).
**Scope:** FYERS TBT only. Minimal corrective changes to fix identified integration gaps.
**Source of truth:** This document. Must be updated immediately after every individual task.

---

## 1) Objectives

Fix the following confirmed/identified gaps in the current FYERS TBT integration without broad rewrites:

1. **Symbol propagation**: Parent `FyersFeed` must propagate desired symbols to `FyersTbtFeed` (and keep them in sync on runtime add/remove). Currently `FyersFeed` creates `FyersTbtFeed` with `access_token_getter`, `config`, `market_service` but never passes/updates desired symbols; `FyersTbtFeed.subscribe/unsubscribe` exist but are not called by the parent.
2. **Runtime sync**: `add_instruments`/`remove_instruments` on `FyersFeed` must sync TBT subscriptions (respecting TBT symbol limits and TBT-supported instruments only).
3. **Channel resume/pause (type-2)**: Implement confirmed protocol requirement for channel control (resume/pause) using message type `2` with `resumeChannels`/`pauseChannels` as per official docs. This is **confirmed required**, not an assumption.
4. **Entitlement-aware limits**: Enforce limits based on actual account entitlements (Standard/Prime) and connection/channel/symbol constraints. **Separate channel capacity from symbol/connection entitlements**.
5. **Idempotent shutdown**: `FyersFeed._stop_tbt_channel()` currently calls `await self._tbt_feed.stop()` but `FyersTbtFeed` has no `stop()` method (it relies on `stop_event` passed to `run()`). Must add idempotent `stop()` and ensure clean cancellation.
6. **Reconnect recovery**: On reconnect/sequence gaps, **invalidate stale order books and require fresh snapshots before treating depth as valid**. Do not assume deltas resume correctly after gaps/disconnects.
7. **Correct symbol format**: Only propagate TBT-supported instruments with verified symbol format (Fyers format `EXCHANGE:SYMBOL`, e.g. `NSE:RELIANCE-EQ`). Filter/validate before subscribing.
8. **Account-wide limits**: Determine if limits are account-wide across connections/channels and coordinate accordingly (may require coordination beyond a single feed instance).

---

## 2) TBT-01 Findings: Protocol, URL, Auth, Messages, Limits (evidence-labeled)

**Labels used:** `CONFIRMED BY OFFICIAL DOCUMENTATION`, `CONFIRMED BY EXISTING SOURCE CODE`, `INFERENCE`, `UNRESOLVED`.

### 2.1 WebSocket URL resolution

| Item | Evidence | Label |
|---|---|---|
| Official documented TBT endpoint | FYERS docs "Tick-by-Tick (TBT) Socket - Connection (50 Market Depth)" (`https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/tbt-websocket-connection/`): Endpoint `wss://rtsocket-api.fyers.in/versova`. | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| Existing dynamic URL discovery endpoint | `brokers/fyers/tbt/feed.py:384-401` (`_fetch_ws_url`): `GET https://api-t1.fyers.in/indus/home/tbtws` with `headers={"Authorization": token}` (timeout 10s). | **CONFIRMED BY EXISTING SOURCE CODE** |
| Existing response extraction | `brokers/fyers/tbt/feed.py:395-396`: on 200, returns `data.get("data", {}).get("socket_url")`; else logs error and returns `None`. | **CONFIRMED BY EXISTING SOURCE CODE** |
| Existing connection flow uses fetched URL | `brokers/fyers/tbt/feed.py:162-179`: fetches URL via `_fetch_ws_url(token)`; if falsy logs "failed to fetch WebSocket URL", sets `last_error="failed_to_fetch_url"`, returns `"transient"`; otherwise connects to fetched `ws_url` with `ws_connect(ws_url, additional_headers={"Authorization": token}, ...)`. No fallback to fixed endpoint in code. | **CONFIRMED BY EXISTING SOURCE CODE** |
| Dynamic URL mandatory/preferred/alternative (official) | Official connection doc documents fixed endpoint and does not mention `https://api-t1.fyers.in/indus/home/tbtws` or dynamic discovery. | **UNRESOLVED** (not stated in fetched official docs) |
| Fixed endpoint fallback/legacy/supported (official) | Official doc states endpoint as `wss://rtsocket-api.fyers.in/versova`; relationship to dynamic discovery not documented in fetched pages. | **UNRESOLVED** |
| Practical implication | Current implementation depends on dynamic discovery succeeding; replacing it with fixed endpoint without evidence could break behavior. | **INFERENCE** (from source behavior; not an official requirement) |

**TBT-01 decision:** Do **not** replace either URL source based on assumptions. Preserve dynamic fetch behavior unless/until authoritative evidence shows fixed endpoint is equivalent/supported for this flow; consider compatibility/fallback only with evidence in later tasks.

### 2.2 Authentication

| Item | Evidence | Label |
|---|---|---|
| Required header name and format (official) | Connection doc: Header Key `Authorization`, Header Format `<appId:accessToken>`, sample `7ABXUX38S-100:eyJ...`. | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| TBT WebSocket connect auth (existing) | `brokers/fyers/tbt/feed.py:173-177`: `ws_connect(ws_url, additional_headers={"Authorization": token}, ...)` where `token = await asyncio.to_thread(self._token_getter)` (`:159`). | **CONFIRMED BY EXISTING SOURCE CODE** |
| Dynamic URL fetch auth (existing) | `brokers/fyers/tbt/feed.py:391`: `headers={"Authorization": token}` on GET to `https://api-t1.fyers.in/indus/home/tbtws`. | **CONFIRMED BY EXISTING SOURCE CODE** |
| Token value format as passed (existing) | `FyersTbtFeed` receives `access_token_getter` from `FyersFeed` (`brokers/fyers/feed.py:1465-1469` passes `self._access_token_getter`). Exact string format returned by getter (bare token vs `<appId:accessToken>`) not fully traced in TBT-01. | **UNRESOLVED** (needs signature/contract verification in TBT-02 and runtime contract context) |
| Auth differs between dynamic and fixed URL flows (official) | Not specified in fetched docs. | **UNRESOLVED** |
| Correctness of header construction | Code sends `Authorization` header in both fetch and connect; format correctness depends on token getter contract. | **INFERENCE** (partially supported by source; not fully verified) |

**Note:** No credentials/tokens were accessed or printed.

### 2.3 Protocol message formats

| Item | Evidence | Label |
|---|---|---|
| Type-1 subscribe | Channels doc: `{"type":1,"data":{"subs":1,"symbols":[...],"mode":"depth","channel":"1"}}`. Code `brokers/fyers/tbt/feed.py:333-352` (`_send_subscribe`) sends same shape. | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** |
| Type-1 unsubscribe | Channels doc: `{"type":1,"data":{"subs":-1,"symbols":[...],"mode":"depth","channel":"1"}}`. Code `brokers/fyers/tbt/feed.py:354-367` (`_send_unsubscribe`) sends same shape. | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** |
| Type-2 switch/resume/pause | Channels doc: `{"type":2,"data":{"resumeChannels":["1"],"pauseChannels":[]}}`; "Subscribing to a channel does not start the stream. Send a switch-channel request (`type` 2) with the channel in `resumeChannels` to start it." | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| Type-2 implementation in code | `brokers/fyers/tbt/feed.py:51` defines `TBT_CHANNEL_CONTROL_TYPE = 2`; no code path sends type-2 resume/pause messages. | **CONFIRMED BY EXISTING SOURCE CODE** (constant present, behavior missing) |
| Channel identifiers/payload structure | Docs: channel number in subscribe/unsubscribe; channels 1-50 per connection; `resumeChannels`/`pauseChannels` are string lists. Code uses string channel (`str(channel)`) and allocates channels 1..50 (`_get_or_create_channel`, `MAX_CHANNELS_PER_CONNECTION=50`). | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** |
| Server acknowledgements | Not explicitly documented in fetched pages. | **UNRESOLVED** |
| Server error responses | Connection doc: "Server errors come through the error-message callback... In a response, `error` is true, `feeds` is empty and `msg` mostly holds the error message." Channels doc: response has `error`, `feeds`, `msg`, `snapshot`. Code checks `socket_msg.error` and logs `socket_msg.msg` (`tbt/feed.py:224-227`). | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** (partial handling) |
| Data format/snapshot-delta | Docs: responses are protobuf; first packet snapshot, later diffs; `snapshot` true/false. Code parses protobuf `SocketMessage`, applies snapshot/delta via `feed.snapshot` and `OrderBookStore` (`tbt/feed.py:215-283`, `tbt/order_book.py`). | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** |

### 2.4 Capacity and entitlements

| Item | Evidence | Label |
|---|---|---|
| Per-connection channel capacity | Channels doc: "A connection supports 50 channels (1 to 50)." Rate limits table: Channels per connection 50 (1-50) for Standard and Prime. Code: `MAX_CHANNELS_PER_CONNECTION = 50`. | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** |
| Symbols per connection (market depth mode) | Channels rate limits: Standard 1, Prime 5 symbols per connection. Code: `SYMBOLS_PER_CONNECTION = 5` (constant, not entitlement-aware). | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** (constant differs from Standard entitlement) |
| Active connections per app per user | Channels rate limits: Standard 1, Prime 3. Code: `MAX_CONNECTIONS_PER_USER = 3`. | **CONFIRMED BY OFFICIAL DOCUMENTATION**; **CONFIRMED BY EXISTING SOURCE CODE** |
| Symbols per user across all connections (entitlement) | Plans page: TBT Standard 1 symbol, Prime 15 symbols. "Symbol limits apply per user, across all your connections to that socket..." Connection page: Standard 1 symbol per user across all connections; Prime 15 symbols per user across all connections (up to 3 connections with 5 symbols each). | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| Account-wide vs connection-specific | Symbol limits are explicitly "per user, across all connections" (account/user-wide for that socket). Channel capacity is per connection. Connection count is per app per user. | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| Entitlement logic implementation | Not implemented in TBT feed; constants only. | **CONFIRMED BY EXISTING SOURCE CODE** (no entitlement-aware enforcement) |

**Critical separation (confirmed):** **Channel capacity** (50 channels/connection) is distinct from **symbol/connection entitlements** (Standard/Prime, per-user across connections, per-connection symbol caps, connection caps).

### 2.5 Existing implementation behavior (relevant call sites)

- **CONFIRMED BY EXISTING SOURCE CODE:** `brokers/fyers/feed.py:1456-1478` starts TBT when `_tbt_enabled` is true, constructs `FyersTbtFeed(access_token_getter=self._access_token_getter, config=self._config, market_service=self._market_service)` and runs it as a task with `stop_event`. No desired symbols passed.
- **CONFIRMED BY EXISTING SOURCE CODE:** `brokers/fyers/feed.py:1479-1491` stops TBT by calling `await self._tbt_feed.stop()` (method missing on `FyersTbtFeed`) and cancelling task.
- **CONFIRMED BY EXISTING SOURCE CODE:** `FyersTbtFeed` maintains `_desired_symbols`, supports `subscribe`/`unsubscribe`, resubscribes on reconnect (`_resubscribe_all`), but parent does not sync runtime instrument changes.
- **CONFIRMED BY EXISTING SOURCE CODE:** On sequence gap, `FyersTbtFeed` invalidates book and resubscribes symbol (`_process_feed`); on reconnect, `_cleanup` invalidates all books but fresh-snapshot-before-valid-depth handling needs verification in TBT-08 context.

### 2.6 Resolved decisions (TBT-01)

1. **Type-2 resume/pause is required:** **CONFIRMED BY OFFICIAL DOCUMENTATION** (must send type-2 to start/resume channel streams). Existing code does not implement it — corrective work required (TBT-06).
2. **Official endpoint is documented:** `wss://rtsocket-api.fyers.in/versova` **CONFIRMED BY OFFICIAL DOCUMENTATION**.
3. **Dynamic URL discovery exists and is currently used:** `https://api-t1.fyers.in/indus/home/tbtws` → `data.socket_url` **CONFIRMED BY EXISTING SOURCE CODE**. No replacement without evidence.
4. **Auth header contract (official):** `Authorization: <appId:accessToken>` **CONFIRMED BY OFFICIAL DOCUMENTATION**. Existing code sends `Authorization` with getter token in both flows **CONFIRMED BY EXISTING SOURCE CODE**; token format contract remains to be fully verified (TBT-02).
5. **Limits separation:** channel capacity vs symbol/connection entitlements and per-user-across-connections symbol limits **CONFIRMED BY OFFICIAL DOCUMENTATION**.
6. **Snapshot/delta semantics:** first packet snapshot, later diffs **CONFIRMED BY OFFICIAL DOCUMENTATION** and aligned with existing order-book handling **CONFIRMED BY EXISTING SOURCE CODE**.

### 2.7 Remaining uncertainties (post TBT-01)

- **UNRESOLVED:** Whether dynamic URL discovery (`/indus/home/tbtws`) is mandatory, preferred, or alternative per official contract; whether fixed endpoint is fallback/legacy/supported alternative for this implementation path.
- **UNRESOLVED:** Exact token string format returned by `access_token_getter` in TBT context (`<appId:accessToken>` vs other) and whether it satisfies official header format in all cases.
- **UNRESOLVED:** Whether auth differs between dynamic URL fetch and WebSocket connect beyond same header usage.
- **UNRESOLVED:** Official server acknowledgement semantics for subscribe/unsubscribe/switch-channel (not found in fetched pages).
- **UNRESOLVED:** Full TBT-supported instrument universe/symbol eligibility rules beyond observed format/examples (needs catalog/capability verification in later tasks).

### 2.8 Implications for TBT-02 through TBT-07

- **TBT-02 (signatures/lifecycle):** **DONE (corrected).** Exact signatures/call graph/lifecycle ownership, missing `stop()` defect, token-flow trace, and corrected minimal TBT-04 context documented in §2.9. No URL/auth/protocol behavior changes without evidence.
- **TBT-03 (entitlements/capabilities):** Must map official per-user/per-connection/channel limits to existing `providers/capacity.py`/`providers/registry.py`/`brokers/fyers/capabilities.py`; account-wide symbol limits require coordination beyond single feed instance (confirmed by docs).
- **TBT-04 (shutdown):** Add idempotent `stop()` consistent with existing `run(stop_event)` lifecycle and harden parent stop wiring/cleanup ordering per §2.9.5 (minimal, no new abstractions). **Dependency:** resolve/track TBT WebSocket auth-format discrepancy (§2.9.4, §2.9.6) before implementation work that depends on authenticated TBT connectivity.
- **TBT-05 (symbol propagation/sync):** Propagate only valid TBT-supported symbols in correct format; sync runtime add/remove; respect entitlement-aware limits (not current constants alone).
- **TBT-06 (type-2 resume/pause):** Implement confirmed type-2 switch-channel flow; integrate so subscribed channels are resumed as required by protocol.
- **TBT-07 (limits):** Enforce Standard/Prime symbol entitlements (per user across connections), per-connection symbol caps, connection caps, and channel capacity separately; avoid conflation and avoid new global entitlement systems.

### 2.9 TBT-02 Findings (corrected): Lifecycle, signatures, token contract, and TBT-04 context (evidence-labeled)

**Labels used:** `CONFIRMED BY SOURCE CODE`, `CONFIRMED BY TESTS`, `INFERENCE`, `UNRESOLVED`.

#### 2.9.1 Exact signatures and behavior (corrected)

| Component | Signature / behavior | Evidence | Label |
|---|---|---|---|
| `FyersTbtFeed.__init__` | `def __init__(self, access_token_getter: Callable[[], str], config: dict[str, Any], market_service: Any) -> None` — stores `_token_getter`, `_market_service`, `_config`; initializes `_state="disconnected"`, `_task: asyncio.Task \| None = None`, `_stop_event = asyncio.Event()`, `_desired_symbols`, `_lock`, `_books = OrderBookStore()`, `_stats`, `_ws=None`, `_ws_url=None`, `_channel_map`, `_next_channel=1`. `market_service` is required (no default). | `brokers/fyers/tbt/feed.py:62-109` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed.run` | `async def run(self, publisher: Any, stop_event: asyncio.Event) -> None` — uses the **caller-provided** `stop_event` (`while not stop_event.is_set()`, passes it to `_run_session`, checks it for backoff/exit). Does **not** assign `self._task` and does **not** integrate `self._stop_event` into the run lifecycle. On exit `finally` awaits `_cleanup()` and sets `_state="stopped"`. | `brokers/fyers/tbt/feed.py:114-150` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed.subscribe` | `async def subscribe(self, symbols: list[str]) -> None` — updates desired set under lock; if `_ws` and `_ws.open`, sends type-1 subscribe per new symbol; updates stats/logs. | `brokers/fyers/tbt/feed.py:289-301` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed.unsubscribe` | `async def unsubscribe(self, symbols: list[str]) -> None` — removes from desired set under lock; if connected sends type-1 unsubscribe per removed symbol; updates stats/logs. | `brokers/fyers/tbt/feed.py:303-315` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed._run_session` | `async def _run_session(self, stop_event: asyncio.Event) -> str \| None` — gets token via `asyncio.to_thread(self._token_getter)`; if empty sets `auth_required` and returns `"auth_required"`; fetches WS URL; connects via `async with ws_connect(ws_url, additional_headers={"Authorization": token}, ping_interval=20, ping_timeout=10) as ws:`; sets `_ws`, state connected, resubscribes all, awaits `_recv_loop(ws, stop_event)`; returns `None` if stop set else `"transient"`; exceptions logged and return `"transient"`. | `brokers/fyers/tbt/feed.py:152-195` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed._recv_loop` | `async def _recv_loop(self, ws: Any, stop_event: asyncio.Event) -> None` — `async for message in ws:`; breaks if `stop_event.is_set()`; ignores ping string; awaits `_handle_message(message)`; catches `ConnectionClosed` and generic exceptions (records last error). No `ws.recv()`/`stop_event.wait()` race task. | `brokers/fyers/tbt/feed.py:197-213` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed._cleanup` | `async def _cleanup(self) -> None` — invalidates all order books, sets `_ws = None`, sets state `"disconnected"`. Does **not** explicitly close the WebSocket; connection closure is handled by `async with ws_connect(...)` context-manager exit in `_run_session`. | `brokers/fyers/tbt/feed.py:433-437`, `152-195` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed.stop` | **Missing** (no method defined). | `brokers/fyers/tbt/feed.py` (full inspection) | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed.status` | `def status(self) -> dict[str, Any]` — reports enabled/state/task_running/desired_count/subscribed_count/counters/last_message_at/safe_last_error/symbols. `task_running` is derived from `self._task` but `_task` is not assigned by feed code. | `brokers/fyers/tbt/feed.py:443-459` | **CONFIRMED BY SOURCE CODE** |
| `FyersFeed._start_tbt_channel` | `async def _start_tbt_channel(self, stop_event: asyncio.Event) -> None` — no-op if disabled; constructs `FyersTbtFeed(access_token_getter=self._access_token_getter, config=self._config, market_service=self._market_service)`; creates child task `self._tbt_task = asyncio.create_task(self._tbt_feed.run(publisher=None, stop_event=stop_event))`; logs errors without propagating. | `brokers/fyers/feed.py:1457-1478` | **CONFIRMED BY SOURCE CODE** |
| `FyersFeed._stop_tbt_channel` | `async def _stop_tbt_channel(self) -> None` — if feed: `await self._tbt_feed.stop()` then clears feed ref; if task: cancel, await (swallow `CancelledError`), clear ref, log. Calls nonexistent `stop()` **before** task cancel/await/cleanup. | `brokers/fyers/feed.py:1479-1491` | **CONFIRMED BY SOURCE CODE** |
| `FyersFeed.run` / parent lifecycle | Starts TBT with same external `stop_event`; session loop uses shared stop event; `finally` always invokes `_stop_tbt_channel()` and sets stopped. | `brokers/fyers/feed.py:839-875` | **CONFIRMED BY SOURCE CODE** |
| `SourceManager` lifecycle | Owns per-source stop event and source task; stop sets event, cancels task, awaits. | `sources/__init__.py:166-175,201,237-239,279-299,319-347,435-480` | **CONFIRMED BY SOURCE CODE** |
| Existing tests | TBT tests exercise feed behavior with stubs; no `stop()` tests and no parent TBT lifecycle integration. Fyers feed tests cover HSM lifecycle, not TBT wiring. | `test/test_fyers_tbt.py`, `test/test_fyers_feed.py` (inspected) | **CONFIRMED BY TESTS** |

#### 2.9.2 Call graph and lifecycle ownership (corrected)

```text
SourceManager.start_source(name)
  -> stop_event[name] = Event()
  -> source_task = create_task(source.run(publisher, stop_event))      [FyersFeed.run]

FyersFeed.run(publisher, stop_event)
  -> _set_state("running")
  -> await _start_tbt_channel(stop_event)
       -> if not _tbt_enabled: return
       -> _tbt_feed = FyersTbtFeed(access_token_getter, config, market_service)
       -> _tbt_task = create_task(_tbt_feed.run(publisher=None, stop_event=stop_event))
  -> while not stop_event.is_set():
       -> outcome = _run_session(stop_event)                           [HSM]
       -> if outcome is None or stop_event.is_set(): break
       -> if terminal: break
       -> wait/backoff or break on stop
  -> finally:
       -> await _stop_tbt_channel()
            -> if _tbt_feed: await _tbt_feed.stop()                    [MISSING -> exception risk]
            -> _tbt_feed = None
            -> if _tbt_task: cancel(); await; _tbt_task = None
       -> _set_state("stopped")

FyersTbtFeed.run(publisher, stop_event)                                [child task, same external stop_event]
  -> while not stop_event.is_set():
       -> outcome = _run_session(stop_event)
            -> token = to_thread(_token_getter)
            -> ws_url = _fetch_ws_url(token)                           [GET /indus/home/tbtws, Authorization: token]
            -> async with ws_connect(ws_url, Authorization: token) as ws:
                 -> _ws = ws; _resubscribe_all(ws)
                 -> await _recv_loop(ws, stop_event)                   [async for message in ws]
            -> return None if stop_event set else "transient"
       -> auth_required -> break; transient -> backoff wait on stop_event
  -> finally: await _cleanup(); _state="stopped"

SourceManager.stop()/stop_source(name)
  -> stop_event.set()
  -> cancel source task; await
  -> FyersFeed.run observes stop/cancellation -> exits -> finally _stop_tbt_channel()
```

**Ownership (corrected):** `SourceManager` owns source task and stop event. `FyersFeed` owns TBT child object/task references and invokes TBT shutdown in `finally`. `FyersTbtFeed.run` uses the caller-provided `stop_event` and owns session loop/reconnect/backoff; it does not assign/manage `self._task` and does not integrate `self._stop_event` into run/cleanup. WebSocket lifetime in a session is bounded by `async with ws_connect(...)`; `_cleanup()` invalidates books and clears refs/state but does not explicitly close the socket. **CONFIRMED BY SOURCE CODE**.

#### 2.9.3 Confirmed defects and shutdown analysis (corrected)

| Defect / risk | Evidence | Label |
|---|---|---|
| `FyersFeed._stop_tbt_channel()` calls missing `await self._tbt_feed.stop()` before task cancel/await/ref cleanup | `brokers/fyers/feed.py:1481-1491`; no `stop()` in `brokers/fyers/tbt/feed.py` | **CONFIRMED BY SOURCE CODE** |
| If `await self._tbt_feed.stop()` raises (e.g. `AttributeError`), subsequent task cancellation, task await, task ref clear, and stop log in `_stop_tbt_channel()` are skipped by control flow | `brokers/fyers/feed.py:1481-1491` (no try/except around stop call) | **CONFIRMED BY SOURCE CODE** |
| `_stop_tbt_channel()` is invoked from `FyersFeed.run()` `finally`, so an exception there can propagate out of parent shutdown/finally path | `brokers/fyers/feed.py:839-875` | **CONFIRMED BY SOURCE CODE** |
| Parent and child receive the same external stop event; child run loop and recv loop check it, but recv iteration is driven by `async for message in ws` (stop observed between messages/iterations and on loop checks) | `brokers/fyers/feed.py:1471-1473`, `brokers/fyers/tbt/feed.py:114-150,197-213` | **CONFIRMED BY SOURCE CODE** |
| `FyersTbtFeed` has unused/unintegrated lifecycle fields (`_task`, `_stop_event`) relative to current run/stop behavior | `brokers/fyers/tbt/feed.py:81-82,114-150,443-459` | **CONFIRMED BY SOURCE CODE** |
| Runtime consequences of the exception/cancellation timing (e.g. leaked task, hung shutdown, or observed behavior under specific races) are not claimed | No runtime/broker testing performed | **UNRESOLVED** (not runtime-tested) |

#### 2.9.4 Token-format trace and discrepancy (corrected)

| Step | Finding | Evidence | Label |
|---|---|---|---|
| `FyersAuth.validate_auth_code()` returns | `{"access_token": str(payload["access_token"]), "refresh_token": str(payload.get("refresh_token", "")), "expires_at": <derived or payload>}` (no `app_id` prefixing) | `brokers/fyers/auth.py:117-153` | **CONFIRMED BY SOURCE CODE** |
| `FyersAuth.refresh_access_token()` returns | `{"access_token": str(payload["access_token"]), "refresh_token": refresh_token.strip(), "expires_at": ...}` | `brokers/fyers/auth.py:155-192` | **CONFIRMED BY SOURCE CODE** |
| `FyersRuntimeAuth.set_access_token()` stores | `self._access_token = token or ""` (stores provided string as-is) | `app/fyers_runtime_auth.py:33-35` | **CONFIRMED BY SOURCE CODE** |
| `FyersRuntimeAuth.get_access_token()` returns | `self._access_token` (stored runtime string) | `app/fyers_runtime_auth.py:41-43` | **CONFIRMED BY SOURCE CODE** |
| Auth service installs runtime token | `FyersAuthService` calls `self._runtime.set_access_token(bundle["access_token"])` on restore/refresh/login paths | `app/auth/fyers.py:96,128,215` | **CONFIRMED BY SOURCE CODE** |
| Parent transformation before TBT | `FyersFeed` stores getter from config and passes `self._access_token_getter` directly to `FyersTbtFeed`; no TBT-specific token transformation found | `brokers/fyers/feed.py:329,1465-1469` | **CONFIRMED BY SOURCE CODE** |
| Value TBT sends in Authorization headers | TBT obtains `token = await asyncio.to_thread(self._token_getter)` and sends `headers={"Authorization": token}` to dynamic URL endpoint and `additional_headers={"Authorization": token}` to WS connect | `brokers/fyers/tbt/feed.py:156,173,391` | **CONFIRMED BY SOURCE CODE** |
| Official TBT WebSocket auth contract | `Authorization: <appId:accessToken>` | FYERS TBT Connection doc (TBT-01, §2.2) | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| TBT WebSocket header vs official requirement | Traced code path provides the runtime access token string without composing `<appId:accessToken>`; therefore the WS Authorization value, as constructed by current code, does **not** show the documented `<appId:accessToken>` composition. Actual runtime acceptance/rejection is not tested. | `brokers/fyers/auth.py`, `app/fyers_runtime_auth.py`, `app/auth/fyers.py`, `brokers/fyers/feed.py`, `brokers/fyers/tbt/feed.py` | **CONFIRMED BY SOURCE CODE** (construction) / **UNRESOLVED** (runtime effect) |
| Dynamic URL discovery auth contract | Same token value is sent to `https://api-t1.fyers.in/indus/home/tbtws`; official contract for this discovery endpoint is not established in fetched docs and remains distinct/unverified from the TBT WS contract | `brokers/fyers/tbt/feed.py:384-401`, TBT-01 §2.1/§2.7 | **CONFIRMED BY SOURCE CODE** (value sent) / **UNRESOLVED** (endpoint contract) |

**Explicit issue (dependency before implementation):** **TBT WebSocket Authorization format discrepancy** — current traced implementation sends raw runtime access token, while official TBT WS docs require `Authorization: <appId:accessToken>`. Any implementation task that changes or depends on authenticated TBT connectivity/auth/header behavior must resolve this discrepancy with evidence before implementation. No auth/URL/protocol changes are made in this documentation task.

#### 2.9.5 Minimal TBT-04 context (shutdown only, corrected) — NOT IMPLEMENTED

**Goal:** Make TBT shutdown safe/idempotent and compatible with existing parent lifecycle without changing protocol/auth/URL/symbol behavior.

| Item | Minimal context/proposal | Evidence/rationale | Label |
|---|---|---|---|
| Files likely affected | `brokers/fyers/tbt/feed.py` (add `stop()`; decide integration with existing `_stop_event`/`_task` fields or caller stop_event), `brokers/fyers/feed.py` (harden `_stop_tbt_channel`), optional focused tests | Missing method and parent call order confirmed | **CONFIRMED BY SOURCE CODE** / **INFERENCE** |
| `FyersTbtFeed.stop()` | Add `async def stop(self) -> None` (idempotent). Must be safe before/during/after run and on repeated calls. Should cooperate with existing shutdown signaling; avoid deadlock with parent task cancel/await. Exact integration with currently unused `_stop_event`/`_task` fields must be chosen against actual lifecycle (not assumed). | Parent awaits `feed.stop()`; run uses caller `stop_event`; fields exist but are not integrated | **CONFIRMED BY SOURCE CODE** / **INFERENCE** |
| Parent `_stop_tbt_channel()` | Harden so missing/failing stop does not prevent task cancel/await/ref cleanup/logging; contain exceptions appropriately; keep idempotent. | Current call order and no containment | **CONFIRMED BY SOURCE CODE** / **INFERENCE** |
| Task ownership | `SourceManager`/`FyersFeed` remain owners of task cancellation/lifecycle; `FyersTbtFeed.stop()` must not spawn unmanaged tasks. | Existing architecture | **CONFIRMED BY SOURCE CODE** / **INFERENCE** |
| Cleanup/resources | Rely on existing session `async with` WS lifecycle and `_cleanup()` (book invalidation/ref/state reset); do not duplicate protocol logic. | Corrected cleanup behavior | **CONFIRMED BY SOURCE CODE** |
| Out of scope | No auth/URL/protocol/symbol/entitlement/type-2/reconnect-semantic changes. Auth-format discrepancy tracked separately. | Task boundaries | **CONFIRMED BY SOURCE CODE** |

**Proposed acceptance criteria/tests for future TBT-04:** unchanged in intent from prior plan but must be implemented against corrected lifecycle facts (idempotent `stop()`, hardened parent shutdown, no `AttributeError` path, regressions preserved). No tests executed in TBT-02.

#### 2.9.6 TBT-02 resolved decisions and remaining uncertainties (corrected)

- **Resolved (corrected):** Actual constructor/run/session/recv/cleanup signatures and behavior documented; `run()` uses caller `stop_event` (not `self._stop_event`), does not manage `self._task`; `_recv_loop` uses `async for`; `_cleanup` does not explicitly close WS; `market_service` required; missing `stop()` and parent call-order/exception consequences confirmed; token flow traced end-to-end in code. **CONFIRMED BY SOURCE CODE**.
- **Explicit issue/dependency:** TBT WS Authorization header is constructed from raw runtime access token in traced code, conflicting with official `<appId:accessToken>` requirement (runtime effect untested). **CONFIRMED BY SOURCE CODE** / **UNRESOLVED**.
- **Remaining:** Dynamic URL discovery auth/mandate/relationship **UNRESOLVED**; server acknowledgement semantics **UNRESOLVED**; full TBT instrument eligibility **UNRESOLVED**; runtime shutdown/exception consequences not tested **UNRESOLVED**. No auth/URL/protocol changes proposed.

### 2.10 TBT-03 Findings (investigation): Auth, channel resume, shutdown/task ownership (evidence-labeled)

**Labels used:** `CONFIRMED BY SOURCE CODE`, `CONFIRMED BY OFFICIAL DOCUMENTATION`, `CONFIRMED BY SDK/SOURCE EVIDENCE`, `INFERENCE`, `UNRESOLVED`.

#### 2.10.1 TBT-03.1 — WebSocket authentication

| Item | Finding | Evidence | Label |
|---|---|---|---|
| Token source/flow | `FyersAuth.validate_auth_code()` returns raw `access_token` (no app_id prefix). `FyersAuthService` installs it via `FyersRuntimeAuth.set_access_token(bundle["access_token"])`. `FyersRuntimeAuth.get_access_token()` returns stored string unchanged. `FyersFeed` passes getter unchanged to `FyersTbtFeed`. | `brokers/fyers/auth.py:117-153`, `app/auth/fyers.py:96,128,215`, `app/fyers_runtime_auth.py:33-43`, `brokers/fyers/feed.py:329,1465-1469` | **CONFIRMED BY SOURCE CODE** |
| TBT WS header (current) | `FyersTbtFeed._run_session` gets token via getter and passes `additional_headers={"Authorization": token}` to `ws_connect`. No app_id composition. | `brokers/fyers/tbt/feed.py:156,171-177` | **CONFIRMED BY SOURCE CODE** |
| TBT dynamic URL fetch header (current) | `_fetch_ws_url` sends `headers={"Authorization": token}` to `GET https://api-t1.fyers.in/indus/home/tbtws`. | `brokers/fyers/tbt/feed.py:384-401` | **CONFIRMED BY SOURCE CODE** |
| Official TBT WS auth | `Authorization: <appId:accessToken>` (Header Key/Format per TBT Connection doc). | FYERS TBT Connection doc (TBT-01 §2.2) | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| App ID availability | App ID available via encrypted credential store (`FyersFeed._resolve_app_id()` reads from store, falls back to config) and via `FyersAuthService`/credentials; `FyersTbtFeed` has no access to app_id currently. | `brokers/fyers/feed.py:479-494`, `app/auth/fyers.py:66-83,170-178`, `brokers/fyers/tbt/feed.py:62-109` | **CONFIRMED BY SOURCE CODE** |
| Existing auth-formatting mechanism | Parent `FyersFeed` (HSM) uses binary auth frame built from `hsm_key` extracted from JWT (`_hsm_key_from_access_token`) and does **not** use `Authorization: <appId:accessToken>` header for HSM WS; different protocol. No existing TBT header-formatter found. | `brokers/fyers/feed.py:5,273-285,909-933` | **CONFIRMED BY SOURCE CODE** |
| WS vs dynamic URL auth | Same token value passed currently; official docs do not state they are identical. Must be verified separately. | `brokers/fyers/tbt/feed.py:173,391`, TBT-01 §2.1/§2.7 | **CONFIRMED BY SOURCE CODE** / **UNRESOLVED** |
| Smallest change (minimal) | Need to construct `<appId:accessToken>` for TBT WS (and verify dynamic URL separately). Options: have `FyersTbtFeed` accept app_id/getter or formatter, or have parent pass formatted token/getter; must not change canonical token storage format. Requires evidence for dynamic URL contract. | Analysis of traced code paths | **INFERENCE** (minimal approach, not implemented) |

#### 2.10.2 TBT-03.2 — Channel resume protocol

| Item | Finding | Evidence | Label |
|---|---|---|---|
| Documented sequence | Subscribe (type 1) does **not** start stream; must send switch-channel/resume (type 2) with `resumeChannels`/`pauseChannels` (string channel IDs). | FYERS TBT Channels doc (TBT-01 §2.3) | **CONFIRMED BY OFFICIAL DOCUMENTATION** |
| Current implementation | Sends type-1 subscribe/unsubscribe only (`_send_subscribe`/`_send_unsubscribe`); defines `TBT_CHANNEL_CONTROL_TYPE = 2` but no code path sends type-2. | `brokers/fyers/tbt/feed.py:50-51,333-367` | **CONFIRMED BY SOURCE CODE** |
| Channel allocation | Channels allocated 1..50 via `_get_or_create_channel`, stored in `_channel_map` (symbol->channel), sent as string in subscribe/unsubscribe. | `brokers/fyers/tbt/feed.py:107-108,338-378` | **CONFIRMED BY SOURCE CODE** |
| Interactions | Subscriptions tracked in `_desired_symbols`; on reconnect `_resubscribe_all(ws)` resubscribes all desired symbols (type-1). Sequence gaps trigger invalidate + `_resubscribe_symbol` (unsubscribe+subscribe). No resume/pause integration. | `brokers/fyers/tbt/feed.py:289-332` | **CONFIRMED BY SOURCE CODE** |
| Acknowledgements | Not explicitly documented in fetched pages; server error responses observed in protobuf (`socket_msg.error/msg`). | TBT-01 §2.3 | **UNRESOLVED** |
| Conclusion | Type-2 resume is a confirmed protocol requirement; current implementation is missing it. Must integrate resume after subscribe/resubscribe/reconnect as per protocol; channel IDs must be retained. | Docs + source | **CONFIRMED BY OFFICIAL DOCUMENTATION** / **CONFIRMED BY SOURCE CODE** |

#### 2.10.3 TBT-03.3 — Shutdown and task ownership (corrected follow-up)

**Static source facts vs runtime behavior:** The findings below distinguish what is established by current source inspection from behavior that has not been runtime-tested.

| Item | Finding | Evidence | Label |
|---|---|---|---|
| Receive-loop mechanism | `FyersTbtFeed._recv_loop()` receives via `async for message in ws:` and checks `stop_event.is_set()` inside the loop body (after a message is yielded). It does **not** race `ws.recv()` against `stop_event.wait()` and does **not** close the socket on stop. | `brokers/fyers/tbt/feed.py:197-213` | **CONFIRMED BY SOURCE CODE** |
| Stop-event signalling vs blocked receive | Setting the shared `stop_event` signals the TBT loops, but it does **not** by itself interrupt a blocked WebSocket receive inside `async for message in ws`. If no message arrives, `_recv_loop()` cannot observe the stop flag until iteration yields; therefore `FyersTbtFeed.run()`/`_run_session()` cannot be assumed to exit promptly on stop-event signalling alone while the receive is blocked. | `brokers/fyers/tbt/feed.py:114-150,152-195,197-213` | **CONFIRMED BY SOURCE CODE** (mechanism) / **UNRESOLVED** (actual runtime timing/behavior not tested) |
| Parent/child ownership | `SourceManager` owns the source task/stop event. `FyersFeed` owns TBT child object/task refs, starts TBT as a separate task (`asyncio.create_task(self._tbt_feed.run(...))`), passes the same external `stop_event`, and stops TBT in `finally`. `FyersTbtFeed.run()` uses the caller stop_event and does not assign/manage `self._task`. | `sources/__init__.py:166-175,319-347`, `brokers/fyers/feed.py:839-875,1457-1491`, `brokers/fyers/tbt/feed.py:81-82,114-150` | **CONFIRMED BY SOURCE CODE** |
| Parent cancellation vs child task | Cancelling the parent/source task does **not** automatically cancel the separately created TBT child task. `FyersFeed.run()` handles `CancelledError` (closes its own live HSM WS, sets stopped) and relies on `finally -> _stop_tbt_channel()` to stop TBT. `SourceManager.stop_source()` sets the source stop event, waits briefly, then cancels/awaits the **source** task. | `sources/__init__.py:323-364`, `brokers/fyers/feed.py:866-876,1457-1491` | **CONFIRMED BY SOURCE CODE** |
| `_stop_tbt_channel()` defect / skip risk | `_stop_tbt_channel()` does `await self._tbt_feed.stop()` **before** cancelling/awaiting/clearing the child task and before clearing the feed ref/logging completion. `FyersTbtFeed.stop()` does not exist, and the stop call is not contained, so an `AttributeError`/exception can prevent subsequent child cancellation, awaiting, and reference cleanup. Because it is called from `FyersFeed.run()` `finally`, the exception can also propagate out of the parent shutdown path. | `brokers/fyers/feed.py:839-875,1479-1491`, `brokers/fyers/tbt/feed.py` (no `stop`) | **CONFIRMED BY SOURCE CODE** |
| Cleanup and WS context-manager behavior | `FyersTbtFeed.run()` has `finally: await _cleanup()` (invalidates books, clears `_ws`, sets disconnected). The session WebSocket is opened as `async with ws_connect(...) as ws:` inside `_run_session()`. In normal/exceptional exit paths that unwind through that context, the context manager is responsible for closing the socket; however, reliable closure/cleanup when the owning task is cancelled while blocked in `async for` receive is **not established by static inspection alone** and must not be assumed without runtime verification. | `brokers/fyers/tbt/feed.py:114-150,152-195,433-437` | **CONFIRMED BY SOURCE CODE** (structure) / **UNRESOLVED** (cancellation-while-blocked runtime behavior) |
| Comparable repository pattern | `UpstoxFeed` and `FyersFeed` (HSM) use explicit recv-vs-stop coordination (`asyncio.wait`/task cancellation and awaiting), track/retire live sockets, and close sockets on cancellation paths rather than relying on stop-event signalling alone to interrupt a blocked receive. `SourceManager` cancels and awaits source tasks. | `brokers/upstox/feed.py:764-796,729-744,866-876,937-977`, `brokers/fyers/feed.py:434-454,866-876,1038-1095`, `sources/__init__.py:323-364` | **CONFIRMED BY SOURCE CODE** |
| Recommended minimal lifecycle design (not implemented) | **Grounded in inspected code/patterns:** (1) Make `FyersFeed._stop_tbt_channel()` robust and idempotent: contain exceptions from any stop call, and **always** cancel and await the child task and clear refs even if an earlier cleanup operation raises (do not let missing `stop()` skip cancellation/await/cleanup). (2) Add a minimal idempotent `FyersTbtFeed.stop()` that cooperates with the caller stop event and can trigger prompt receive interruption (e.g. close/retire the active WS and/or integrate existing stop signalling) without owning parent task cancellation and without spawning unmanaged tasks. (3) Ensure blocked receives can be interrupted (do not rely solely on stop-event checks inside `async for`); prefer existing repo patterns (recv-vs-stop race or explicit socket close/retirement under controlled ownership). (4) Ensure child termination is awaited and `_cleanup()`/reference release run reliably on normal stop, parent cancellation, and exception paths. (5) No new task manager/abstraction/architectural rewrite. Exact mechanism must be chosen to fit current TBT structure and verified by focused tests. | `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `sources/__init__.py`, `brokers/upstox/feed.py` | **INFERENCE** (minimal approach, grounded in existing patterns; not implemented) |

#### 2.10.4 TBT-03 summary of issues/dependencies
- **Auth format:** WS requires `<appId:accessToken>` per docs but code sends raw token; app_id available via store/config/parent; need formatter/getter change with evidence for dynamic URL. **BLOCKING/DEPENDENCY for auth-dependent work.**
- **Channel resume:** Type-2 required but missing; must implement with channel tracking and integrate with subscribe/resubscribe/reconnect. **CONFIRMED REQUIRED.**
- **Shutdown (corrected):** Stop-event signalling alone does **not** interrupt a blocked `async for` receive; parent cancellation does **not** auto-cancel the separately created child task; missing `FyersTbtFeed.stop()` plus call order/exception handling in `_stop_tbt_channel()` can skip child cancel/await/ref cleanup; cleanup/WS closure on cancellation-while-blocked paths is not runtime-verified. **CONFIRMED DEFECT/RISK (static) + UNRESOLVED runtime behavior.** Minimal fix must guarantee receive interruption, child termination/await, reliable cleanup/ref release, and exception containment without new abstractions.

---

## 3) Existing Abstractions to Reuse (investigate first)

Before writing custom code, verify and reuse:

| Area | Files | Notes |
|---|---|---|
| Entitlement/capability resolution | `providers/capacity.py`, `providers/registry.py`, `brokers/fyers/capabilities.py` | `providers/capacity.py` is the single authoritative model for "how many instruments may this provider carry" derived from adapter-declared limits and account tier (default tier reported if unverified). `brokers/fyers/capabilities.py` declares FYERS tiers/modes/REST limits. **Investigate existing resolution before proposing new entitlement parameters/enforcement logic.** |
| Subscription ownership/routing | `app/subscriptions/manager.py`, `app/subscriptions/service.py`, `app/subscriptions/routing.py` | Lease-based ownership, reconciliation, failover. **Do not create a second subscription manager.** |
| Canonical market state | `market/service.py`, `market/models.py` | Single owner of quote/depth state. Preserve merge semantics. **Do not create a duplicate depth store.** |
| Order book reconstruction | `brokers/fyers/tbt/order_book.py` | Existing `LocalOrderBook`/`OrderBookStore` with snapshot/delta/sequence-gap/invalidate. **Preserve behavior.** |
| Normalization | `brokers/fyers/tbt/normalizer.py` | Existing TBT depth normalizer. **Preserve behavior.** |
| Parent feed lifecycle | `brokers/fyers/feed.py` | HSM feed lifecycle, TBT start/stop wiring. Must fix gaps without altering protected auth/startup behavior. |

No new dependencies, no new global config/entitlement systems.

---

## 4) Corrected Implementation Tasks

All tasks are individually numbered. Status must be updated after each task. Do not batch updates.

### TBT-01: Audit & reconcile protocol/URL/auth against code
- **Status:** DONE
- **Scope:** Read-only investigation and plan documentation
- **Affected files:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (documentation update only)
- **Dependencies:** None
- **Work completed:**
  1. Inspected existing TBT implementation and call sites: `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `brokers/fyers/tbt/order_book.py`, `brokers/fyers/tbt/normalizer.py`, `brokers/fyers/capabilities.py`, `providers/capacity.py`, `providers/registry.py`, `test/test_fyers_tbt.py`, `docs/FYERS_TBT_IMPLEMENTATION_PLAN.md`.
  2. Fetched authoritative FYERS docs: TBT Connection (`https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/tbt-websocket-connection/`), TBT Channels and Message Types (`https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/concept-of-channels/`), Plans & Limits (`https://fyers.in/api-docs/v3/getting-started/plans-and-limits/`).
  3. Reconciled fixed endpoint vs dynamic URL discovery, auth header contract, type-1/type-2 protocol, snapshot/delta behavior, and capacity/entitlement limits with evidence labels.
  4. Recorded resolved decisions, remaining uncertainties, and implications for TBT-02–TBT-07 in §2.
- **Acceptance criteria:** Satisfied (documented reconciliation with code paths + doc references; no code/test/config/runtime changes).
- **Required tests:** None (investigation). No tests executed.
- **Commands executed:** `git status --short`; `git rev-parse --abbrev-ref HEAD`; `git rev-parse HEAD`; file reads/grep/webfetch for evidence gathering (read-only).
- **Exact results:** Branch `review/scanner-phase1`, HEAD `bab6d1a7869ad8aeb7a2431e32e997596adbce90`. Working tree before/after task includes pre-existing `?? docs/performance/` and this plan file `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated in-place). No other files modified.
- **Failures and unresolved issues:** None blocking TBT-01 completion. Unresolved protocol/contract questions documented in §2.7 (dynamic URL mandate/relationship, token format contract, auth differences, server acknowledgements, TBT-supported instrument eligibility details).
- **Decisions and rationale:** Type-2 resume/pause treated as confirmed required per official docs; dynamic URL fetch preserved as existing behavior pending evidence; no inference presented as official requirement; limits separated (channel capacity vs entitlements).
- **Next task:** TBT-02 (dependencies satisfied: TBT-01 DONE). Do not begin TBT-02 without approval.

### TBT-02: Verify method signatures & lifecycle contracts
- **Status:** DONE (corrected against actual source at `bab6d1a`)
- **Scope:** Read-only investigation and plan documentation (correction pass)
- **Affected files:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (documentation update only)
- **Dependencies:** TBT-01 (DONE)
- **Work completed:**
  1. Re-inspected actual signatures/behavior in `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `brokers/fyers/auth.py`, `app/fyers_runtime_auth.py`, `app/auth/fyers.py`, `sources/__init__.py`.
  2. Corrected prior inaccurate TBT lifecycle descriptions (constructor fields, `run`/`_run_session`/`_recv_loop`/`_cleanup` behavior, task/stop-event ownership, `market_service` contract).
  3. Corrected parent shutdown analysis (child task creation, missing `stop()` call ordering, exception consequences, `finally` invocation, shared external stop event) without claiming untested runtime behavior.
  4. Traced token flow from `FyersAuth.validate_auth_code()`/runtime storage/getter through `FyersFeed` to `FyersTbtFeed` and documented the TBT WebSocket auth-format discrepancy vs official `Authorization: <appId:accessToken>`; distinguished WS contract from still-unverified dynamic URL discovery auth contract.
  5. Recorded corrected findings, explicit issues/dependencies, and remaining uncertainties in §2.9.
- **Acceptance criteria:** Satisfied for corrected documentation (exact signatures, call graph/lifecycle ownership, confirmed defects, token-flow findings, minimal TBT-04 proposal context, and remaining uncertainties documented with evidence labels; no code/test/config/runtime changes). Prior inaccurate statements removed/corrected.
- **Required tests:** None executed (investigation). No runtime behavior claimed as tested.
- **Commands executed:** `git status --short`; `git rev-parse --abbrev-ref HEAD`; `git rev-parse HEAD`; file reads/grep for signatures/call sites/token flow (read-only).
- **Exact results:** Branch `review/scanner-phase1`, HEAD `bab6d1a7869ad8aeb7a2431e32e997596adbce90`. Working tree reconciled: `?? docs/performance/` (pre-existing, preserved), `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated). No other files modified.
- **Failures and unresolved issues:** TBT WebSocket Authorization value appears to be the raw runtime access token (not transformed to `<appId:accessToken>` in traced code) — **explicit issue/dependency before implementation** (see §2.9.4/§2.9.6). Dynamic URL discovery auth contract remains **UNRESOLVED**. Prior TBT-01 uncertainties remain (§2.7).
- **Decisions and rationale:** Correct plan to match actual source; do not claim untested runtime behavior; do not change auth/URL/protocol; treat WS auth-format discrepancy as a tracked issue requiring resolution/evidence before implementation that depends on it.
- **Next task:** TBT-03 (dependencies satisfied for investigation sequencing: TBT-01 DONE, TBT-02 corrected). Do not begin TBT-03 or TBT-04 without approval. Implementation tasks touching auth/header behavior must resolve the explicit auth discrepancy first.

### TBT-03: Investigate FYERS TBT protocol and lifecycle integration (auth, channel resume, shutdown)
- **Status:** DONE (investigation/documentation only)
- **Scope:** Read-only investigation and plan documentation
- **Affected files:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (documentation update only)
- **Dependencies:** TBT-01 (DONE), TBT-02 (DONE, corrected)
- **Work completed:**
  1. **TBT-03.1 (Auth):** Traced token flow (`FyersAuth` -> `FyersRuntimeAuth` -> `FyersAuthService` -> `FyersFeed` -> `FyersTbtFeed`) and confirmed raw access token is sent to both dynamic URL fetch and TBT WS; official TBT WS requires `Authorization: <appId:accessToken>`; app_id available via credential store/config/parent but not used by TBT; no existing TBT header formatter found. Distinguished WS contract from unverified dynamic URL auth contract.
  2. **TBT-03.2 (Channel resume):** Confirmed official type-2 `resumeChannels`/`pauseChannels` requirement; verified current implementation sends only type-1 subscribe/unsubscribe and never sends type-2 despite constant; documented channel allocation/retention and reconnect/resubscribe interactions; server ack semantics remain unresolved.
  3. **TBT-03.3 (Shutdown/task ownership):** Rechecked parent/child ownership and stop paths; confirmed missing `FyersTbtFeed.stop()` and parent call-order exception risk; documented normal stop-event flow, cancellation paths, WS context-manager cleanup, and minimal correction options aligned with existing lifecycle patterns.
  4. Inspected comparable lifecycle patterns (`sources/__init__.py`, `brokers/upstox/feed.py`, `brokers/fyers/feed.py`) and recorded evidence-backed findings in §2.10.
- **Acceptance criteria:** Satisfied for investigation/documentation (confirmed protocol requirements, auth-format discrepancy and affected requests, channel-resume gap, shutdown/task ownership defect, remaining unknowns, and minimal next-step approach/criteria documented with evidence labels; no implementation).
- **Required tests:** None executed (investigation). No runtime behavior claimed as tested.
- **Commands executed:** `git status --short`; `git rev-parse --abbrev-ref HEAD`; `git rev-parse HEAD`; file reads/grep for auth/token flow, protocol handling, lifecycle/call sites, and comparable feed patterns (read-only).
- **Exact results:** Branch `review/scanner-phase1`, HEAD `bab6d1a7869ad8aeb7a2431e32e997596adbce90`. Working tree reconciled: `?? docs/performance/` (pre-existing, preserved), `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated). No other files modified.
- **Failures and unresolved issues:** Dynamic URL discovery auth contract/mandate remains **UNRESOLVED**; runtime effect of WS auth header construction **UNRESOLVED** (not tested); server acknowledgement semantics **UNRESOLVED**; runtime shutdown/exception consequences **UNRESOLVED** (not tested).
- **Decisions and rationale:** Preserve canonical token storage; resolve WS auth formatting with evidence (and verify dynamic URL separately) before auth-dependent implementation; implement type-2 resume per docs; fix shutdown minimally using existing lifecycle patterns without new abstractions.
- **Next task:** TBT-04 (shutdown correction) or a dedicated auth-resolution task as prioritized, but **do not begin implementation without explicit approval**. Auth-format discrepancy remains an explicit dependency for authenticated TBT connectivity.

### TBT-04: Deterministic shutdown and task lifecycle (implemented + corrective follow-up)
- **Status:** DONE
- **Scope:** Minimal code change + focused tests + plan update
- **Affected files:** `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `test/test_fyers_tbt.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`
- **Dependencies:** TBT-02, TBT-03 (corrected shutdown analysis in §2.10.3)
- **Work completed (initial):**
  1. Added idempotent `FyersTbtFeed.stop()` that signals internal stop and closes the active WebSocket to interrupt blocked receives promptly; does not own/cancel the parent-managed run task and is safe before/during/after run and on repeated calls.
  2. Replaced `async for` receive loop with explicit `ws.recv()` raced against caller/internal stop events (`asyncio.wait`), cancelling/awaiting the pending receive task on stop so blocked receives are interruptible without relying solely on stop-event checks inside iteration.
  3. Added active-WS tracking/retirement under lock, stop-aware waits/backoff, `CancelledError` handling that triggers prompt close/cleanup and propagates cancellation, and idempotent cleanup (book invalidation, channel/ref/state reset) while preserving `auth_required` terminal state semantics.
  4. Hardened `FyersFeed._stop_tbt_channel()` to be exception-safe/idempotent: contains `feed.stop()` failures and **always** cancels/awaits the child task and clears refs/log completion even if an earlier cleanup operation raises.
  5. Added focused shutdown/lifecycle tests covering normal receive shutdown, blocked-receive shutdown, parent cancellation, WS closure/termination, cleanup when close raises, repeated stop calls, no orphaned child task after parent shutdown, parent stop continuing when child stop raises, and reconnect cancellation/cleanup.
- **Corrective follow-up (Task A, this session, HEAD `43c7eb5` baseline):**
  1. Tracked the active receive task (`_active_recv_task`) in `FyersTbtFeed._recv_loop()` so it remains accessible to cleanup on all exit paths (normal completion, internal/caller stop, outer task cancellation, WS/transport errors, unexpected exceptions).
  2. Ensured `finally` cancels and awaits any pending receive task and gathers task exceptions (via `return_exceptions=True`) to prevent unhandled-task warnings; preserved `asyncio.CancelledError` propagation semantics.
  3. Reviewed `FyersFeed._stop_tbt_channel()`: child-task cancellation/await/ref cleanup still occur if `feed.stop()` raises; cancellation from `feed.stop()` is propagated after cleanup (straightforward containment + `finally`-style cleanup without changing normal cancellation semantics).
  4. Added/updated focused tests verifying no receive task remains pending after shutdown (not just outer task/socket state), including outer cancellation while blocked, internal/caller stop while blocked, receive failure, repeated stops, and parent cleanup when child stop is cancelled/raises.
- **Acceptance criteria:** Satisfied by implementation and mocked tests (idempotent stop; blocked receive interruptible; receive task cleaned up on all covered exit paths; child termination awaited/observed in tested paths; WS closed; cleanup/ref release reliable in tested paths; parent/child shutdown continues on earlier cleanup exception; no new task manager/abstraction). Runtime behavior under real broker/network conditions remains unverified.
- **Required tests:** `python -m pytest test/test_fyers_tbt.py -q` (72 passed, 1 skipped). `python -m pytest test/test_fyers_feed.py -q` (12 passed). `python -m pytest test/test_architecture_boundaries.py -q` (17 passed).
- **Commands executed (corrective session):** `git status --short`; `git rev-parse --abbrev-ref HEAD`; `git rev-parse HEAD`; source/test inspection; `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`.
- **Exact results (corrective session):** TBT tests: **72 passed, 1 skipped**. Fyers feed tests: **12 passed**. Architecture boundaries: **17 passed**. No live broker/runtime verification performed.
- **Failures and unresolved issues:** None in focused/mocked tests. Real-network cancellation timing/WS closure behavior remains runtime-unverified.
- **Decisions and rationale:** Minimal tracking of active recv task for deterministic cleanup; preserved existing recv-vs-stop design and pinned `websockets>=17,<18` compatibility; no new abstractions.
- **Next task:** TBT-05a (symbol propagation/runtime sync) — completed in this corrective session (see below).

### TBT-05: Authentication correction (implemented)
- **Status:** DONE
- **Scope:** Minimal code change + focused tests + plan update
- **Affected files:** `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `test/test_fyers_tbt.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`
- **Dependencies:** TBT-03, TBT-04
- **Work completed:**
  1. Added optional `app_id_getter` to `FyersTbtFeed` (fallback to `config["app_id"]`) and wired `FyersFeed` to supply app_id from existing owner via `_resolve_app_id()` (credential store/config fallback) without changing credential ownership.
  2. Corrected **TBT WebSocket handshake** Authorization header to official format `appId:accessToken` (raw access token remains unchanged in canonical runtime/storage paths).
  3. Preserved dynamic URL discovery endpoint and its existing Authorization behavior (raw token) because its contract is not established in fetched official docs/SDK evidence gathered in this session; recorded as unresolved/separately verified decision (no guessing).
  4. Added clear auth failures for missing token/app_id (`auth_required`, safe error state) and avoided logging tokens/Authorization headers.
  5. Added focused auth tests for WS header composition, canonical raw token unchanged, missing app_id/token failures, dynamic URL raw-token header preservation, and no token/header exposure in logs.
- **Acceptance criteria:** Satisfied for WS handshake and tested paths. Dynamic URL auth contract remains **UNRESOLVED** (preserved existing behavior by design).
- **Required tests:** Included in `python -m pytest test/test_fyers_tbt.py -q` (52 passed, 1 skipped).
- **Commands executed:** Same as TBT-04 plus online doc fetches (see §11).
- **Exact results:** WS connect receives `Authorization: <appId:accessToken>` in tests; dynamic URL fetch still sends raw token header in tests; no token/header strings observed in captured logs.
- **Failures and unresolved issues:** Dynamic URL discovery endpoint auth contract/necessity remains **UNRESOLVED** (official docs fetched do not document it; no definitive SDK evidence obtained in-session). Runtime acceptance by FYERS not verified (no live broker connection).
- **Decisions and rationale:** Follow official TBT WS docs/notice-board requirement for WS handshake; preserve canonical token storage and existing auth/session behavior; do not guess dynamic URL contract or remove discovery without evidence.
- **Next task:** TBT-06 (channel resume/protocol) — completed in this coordinated implementation (see below).

### TBT-06: Channel subscription and resume protocol (implemented)
- **Status:** DONE
- **Scope:** Minimal code change + focused tests + plan update
- **Affected files:** `brokers/fyers/tbt/feed.py`, `test/test_fyers_tbt.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`
- **Dependencies:** TBT-01, TBT-04, TBT-05
- **Work completed:**
  1. Implemented type-2 switch-channel control (`resumeChannels`/`pauseChannels`, string channel IDs) and integrated it with subscribe/resubscribe/reconnect and sequence-gap recovery flows.
  2. After (re)subscribing symbols (type 1), feed resumes relevant channels (type 2) so streams can start per official protocol; runtime subscribe resumes affected channels; unsubscribe pauses channels that become empty of desired symbols.
  3. Preserved existing channel allocation/map, snapshot/delta parsing, order-book reconstruction/invalidation semantics, MarketService integration, and server-error handling; no undocumented ack blocking added.
  4. Added focused protocol tests for type-1/type-2 payloads and channel string formatting, runtime subscribe resume, empty-channel pause on unsubscribe, reconnect resubscribe+resume, sequence-gap recovery resubscribe+resume, server error handling, and snapshot/delta behavior preservation.
- **Acceptance criteria:** Satisfied by implementation and mocked tests. Server acknowledgement semantics remain **UNRESOLVED** (not invented). Runtime broker behavior not verified.
- **Required tests:** Included in `python -m pytest test/test_fyers_tbt.py -q` (52 passed, 1 skipped).
- **Commands executed:** Same as TBT-04/05.
- **Exact results:** Correct type-1/type-2 messages observed in tests; reconnect/gap flows send resume after resubscribe; existing snapshot/delta/order-book tests remain passing.
- **Failures and unresolved issues:** Ack semantics unresolved; live protocol/runtime behavior unverified.
- **Decisions and rationale:** Implement only documented protocol requirements; keep changes localized; preserve existing book/normalizer/MarketService behavior.

### TBT-05a: Propagate desired symbols from FyersFeed to FyersTbtFeed (implemented, corrective Task B)
- **Status:** DONE
- **Scope:** Minimal code change + focused tests + plan update
- **Affected files:** `brokers/fyers/feed.py`, `brokers/fyers/tbt/feed.py`, `test/test_fyers_tbt.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`
- **Dependencies:** TBT-04, TBT-06
- **Work completed:**
  1. Seeded `FyersTbtFeed` with the parent `FyersFeed`'s current desired instrument set when starting the TBT channel (using existing `subscribe()` so desired state is retained and applied on connect/reconnect).
  2. Propagated runtime `add_instruments` additions and `remove_instruments` removals to the active TBT feed (desired state retained even if TBT not yet connected; reconnect/resubscribe uses latest desired set).
  3. Reused existing parent instrument lifecycle and TBT desired-symbol/subscribe-unsubscribe mechanism (no second registry/subscription manager). Preserved existing non-TBT feed behavior.
  4. Added focused tests for start-with-existing instruments, runtime add/remove sync, add/remove before connection readiness, reconnect restoring latest desired instruments, repeated add/remove without duplicate/inconsistent subscriptions, and non-TBT behavior unchanged.
- **Acceptance criteria:** Satisfied by implementation and mocked tests for parent/TBT desired-state synchronization and reconnect restoration of latest desired set. TBT-supported instrument eligibility/format filtering and entitlement-aware limit enforcement remain out of scope here and tracked under TBT-07 (and broader eligibility handling as applicable).
- **Required tests:** `python -m pytest test/test_fyers_tbt.py -q` (72 passed, 1 skipped). `python -m pytest test/test_fyers_feed.py -q` (12 passed).
- **Commands executed (corrective session):** `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`.
- **Exact results (corrective session):** TBT tests: **72 passed, 1 skipped**. Fyers feed tests: **12 passed**. Architecture boundaries: **17 passed**.
- **Failures and unresolved issues:** None in focused/mocked tests. Eligibility/entitlement enforcement and live broker/runtime verification remain pending.
- **Decisions and rationale:** Minimal sync at existing lifecycle points; deduplicated subscribe inputs to avoid duplicate wire/control actions; no new abstractions.
- **Notes:** Original plan listed this as TBT-05; completed here as corrective Task B / TBT-05a.

### TBT-07: Enforce entitlement-aware limits (separate channel capacity from entitlements)
- **Status:** DONE (audited + hardened)
- **Scope:** Minimal code change + focused tests + plan update
- **Affected files:** `brokers/fyers/tbt/feed.py`, `test/test_fyers_tbt.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`
- **Dependencies:** TBT-03, TBT-05a
- **Work completed:**
  1. Added TBT-specific entitlement limit resolution using existing provider tier resolution where possible (`providers.capacity.resolve_entitlement("fyers", tier=...)` when available) with config tier hints (`tbt_tier` preferred, then `tier`, then `plan`; first non-empty wins), mapped to documented TBT limits: Standard (1 symbol/user across connections, 1 symbol/connection, 1 connection/user) and Prime (15 symbols/user across connections, 5 symbols/connection, 3 connections/user).
  2. Enforced symbol limits in `subscribe()` (per-connection and per-user ceilings for this feed instance's desired set) with graceful rejection and clear logging (`entitlement_limit_reached`); deduplication preserved.
  3. Enforced channel capacity (50/connection) separately in channel allocation (`channel_capacity_reached`) without conflating with symbol entitlements.
  4. Hardened rejection/reconnect behavior: entitlement-rejected symbols never enter desired state; if channel allocation fails for an accepted symbol (connected subscribe or reconnect resubscribe), the symbol is removed from desired state so it cannot remain desired or be retried/subscribed accidentally on reconnect.
  5. Exposed resolved TBT limits in `status()` for observability. No new global entitlement systems, no hardcoded account-plan assumptions beyond documented tier mapping, no dependency/config schema/DB/auth changes.
- **Audit findings (feed construction/ownership/lifecycle):**
  - `FyersFeed` owns exactly one `FyersTbtFeed` instance (`_tbt_feed`) when `tbt_enabled`; started in `_start_tbt_channel()` during `FyersFeed.run()` and stopped in `_stop_tbt_channel()` (idempotent/exception-safe). TBT is seeded from the parent feed's current desired instrument set and kept in sync on add/remove/reconnect.
  - `FyersTbtFeed` manages one TBT WebSocket connection per instance (`_ws`/`_ws_lock`, single run loop). No multi-connection fanout exists inside a single TBT feed instance.
  - Multiple `FyersFeed`/`FyersTbtFeed` instances can exist if multiple FYERS feed sources are configured/instantiated (source construction is per configured source). **Account-wide/per-user-across-connections limits are therefore not globally enforceable within this process without external coordination**; this implementation enforces documented ceilings locally per TBT feed instance/connection and fails gracefully.
- **Tier resolution verification (missing/invalid/conflicting hints):**
  - Missing hints: default to `standard` with documented default semantics.
  - Invalid/unknown tier hint: falls back to `standard` with `source=invalid_tier_hint` (non-escalating).
  - Conflicting hints: first non-empty of `tbt_tier`, `tier`, `plan` wins (`tbt_tier` preferred). Cached after first resolution for the feed instance.
- **Locally enforced vs genuinely account-wide enforced limits:**
  - **Locally enforced (this feed instance/connection):** channels per connection (50), symbols per connection (tier-mapped), symbols per user for this instance's desired set (tier-mapped ceiling applied as `min(per_user, per_connection)`), graceful rejection/logging, no accidental desired-state retention on channel-allocation failure.
  - **Not globally/account-wide enforced here:** symbols per user across multiple connections/apps/users, connections per user across multiple feed instances/processes. These require external coordination (documented); status reports documented tier limits including `connections_per_user` for visibility only.
- **Acceptance criteria:** Satisfied by implementation and mocked tests (tier-aware symbol limits enforced locally, channel capacity enforced separately, graceful logging/errors, rejected/unallocatable symbols do not remain desired or resubscribe on reconnect, no new global entitlement systems). Runtime/live broker behavior remains unverified.
- **Required tests:** `python -m pytest test/test_fyers_tbt.py -q` → **81 passed, 1 skipped**. `python -m pytest test/test_fyers_feed.py -q` → **12 passed**. `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
- **Commands executed:** `git status --short`; `git rev-parse --short HEAD`; source inspection (`brokers/fyers/feed.py`, `brokers/fyers/tbt/feed.py`, `sources/registry.py`, `providers/capacity.py`, `brokers/fyers/capabilities.py`); `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`; broader TBT-09 suites (see §9/§13).
- **Exact results:** TBT **81 passed, 1 skipped**. FYERS feed **12 passed**. Architecture boundaries **17 passed**. Broader FYERS **203 passed, 1 skipped**. Source-manager **33 passed**. Subscription **52 passed**. WebSocket/socket lifecycle **13 passed**. No failures/regressions observed.
- **Failures and unresolved issues:** None in executed suites. Account-wide coordination across multiple connections/users not implemented (documented). Dynamic URL discovery auth contract and server subscribe/resume ack semantics remain **UNRESOLVED** (preserved). Live runtime verification pending.
- **Decisions and rationale:** Reused existing FYERS tier resolution where possible; applied TBT-specific documented limits (distinct from HSM capabilities); enforced per-instance ceilings with clear diagnostics; hardened desired-state handling so rejected/unallocatable symbols cannot persist or reconnect-subscribe; kept changes minimal/localized. Existing tests/configs updated to specify `tbt_tier` where multi-symbol behavior is required (explicit entitlement context).

### TBT-08: Fix reconnect recovery (invalidate stale books + require fresh snapshots) (implemented, corrective Task C)
- **Status:** DONE
- **Scope:** Minimal code change + focused tests + plan update
- **Affected files:** `brokers/fyers/tbt/feed.py`, `brokers/fyers/tbt/order_book.py`, `test/test_fyers_tbt.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`
- **Dependencies:** TBT-04, TBT-05a, TBT-06
- **Work completed:**
  1. Added explicit book state distinguishing uninitialized/invalidated-awaiting-snapshot from snapshot-initialized valid state (`awaiting_snapshot`); `invalidate()` requires a fresh snapshot and `apply_snapshot()` (re)establishes validity/sequence.
  2. Enforced fresh-snapshot gating in `FyersTbtFeed._process_feed()`: snapshots (re)initialize and may publish; deltas are rejected (not applied/published) when awaiting snapshot or on sequence gap, with invalidation and resubscribe/recovery as appropriate.
  3. Applied consistently to initial/uninitialized books, explicit invalidation, sequence-gap recovery, and reconnect recovery paths (reconnect cleanup invalidates books; new session must receive fresh snapshots before deltas publish).
  4. Preserved existing snapshot/delta parsing, normalizer, and core order-book reconstruction semantics; updated existing sequence-check tests to reflect corrected delta-vs-snapshot semantics (not weakened).
  5. Added focused tests for delta before initial snapshot, delta after invalidation, delta after sequence gap, delta during reconnect recovery, fresh snapshot restore permitting subsequent deltas, invalid snapshot not restoring validity, and no incomplete publish while awaiting snapshot.
- **Acceptance criteria:** Satisfied by implementation and mocked tests. Runtime/live broker behavior remains unverified.
- **Required tests:** `python -m pytest test/test_fyers_tbt.py -q` (72 passed, 1 skipped). `python -m pytest test/test_fyers_feed.py -q` (12 passed). `python -m pytest test/test_architecture_boundaries.py -q` (17 passed).
- **Commands executed (corrective session):** `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`.
- **Exact results (corrective session):** TBT tests: **72 passed, 1 skipped**. Fyers feed tests: **12 passed**. Architecture boundaries: **17 passed**.
- **Failures and unresolved issues:** None in focused/mocked tests. Live broker/runtime verification pending.
- **Decisions and rationale:** Minimal explicit validity state and feed-level gating to prevent publishing incomplete depth; snapshots remain authoritative reinitialization points.

### TBT-09: Verification & regression
- **Status:** IN PROGRESS (partial)
- **Scope:** Verification only
- **Affected files:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (plan update only; no implementation code changes)
- **Dependencies:** TBT-04, TBT-05, TBT-06, TBT-05a, TBT-08 (and remaining tasks as applicable)
- **Work completed (prior session):**
  1. Ran TBT tests: `python -m pytest test/test_fyers_tbt.py -q` → **52 passed, 1 skipped**.
  2. Ran FYERS feed regression: `python -m pytest test/test_fyers_feed.py -q` → **12 passed**.
  3. Ran architecture boundaries: `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
- **Work completed (corrective session, HEAD `43c7eb5` baseline):**
  1. Ran TBT tests: `python -m pytest test/test_fyers_tbt.py -q` → **72 passed, 1 skipped**.
  2. Ran FYERS feed regression: `python -m pytest test/test_fyers_feed.py -q` → **12 passed**.
  3. Ran architecture boundaries: `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
  4. Reviewed diff scope (FYERS TBT/feed + order book + focused tests + plan only).
- **Work completed (broader regression session, HEAD `2833138`, no implementation changes):**
  1. **Architecture:** `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
  2. **FYERS (broader):** `python -m pytest test/test_fyers_bse_fo_catalogue.py test/test_fyers_capabilities.py test/test_fyers_chain_request_form.py test/test_fyers_chain_symbol.py test/test_fyers_config_architecture.py test/test_fyers_decoder.py test/test_fyers_dynamic_subscriptions.py test/test_fyers_expiry_timestamp.py test/test_fyers_feed.py test/test_fyers_greeks_oi.py test/test_fyers_index_source.py test/test_fyers_integration.py test/test_fyers_login_ux.py test/test_fyers_pin.py test/test_fyers_reconnect_mapping.py test/test_fyers_socket_lifecycle.py test/test_fyers_tbt.py -q` → **194 passed, 1 skipped**.
  3. **Source-manager:** `python -m pytest test/test_source_controls.py test/test_source_dedup.py test/test_source_lifecycle.py -q` → **33 passed**.
  4. **Subscription:** `python -m pytest test/test_subscription_demand.py test/test_subscription_failover_lifecycle.py test/test_subscription_manager.py test/test_subscription_reconcile_lifecycle.py test/test_subscriptions.py test/test_md_subscriptions.py test/test_runtime_subscriptions.py -q` → **52 passed**.
  5. **WebSocket/socket lifecycle:** `python -m pytest test/test_fyers_socket_lifecycle.py test/test_upstox_socket_lifecycle.py -q` → **13 passed** (note: `test_fyers_socket_lifecycle.py` also included in FYERS batch; results consistent).
- **Exact results summary (broader regression session):** No failures. No regressions observed in executed suites; no pre-existing failures to distinguish.
- **Work completed (TBT-07 audit/hardening + TBT-09 rerun, HEAD `2833138` baseline, uncommitted):**
  1. Audited feed construction/ownership/lifecycle: `FyersFeed` owns one `FyersTbtFeed` per feed instance (one TBT WS connection per TBT feed); multiple feed instances can exist per configured source, so account-wide limits across connections/users require external coordination (documented). Local per-instance enforcement implemented.
  2. Verified tier resolution: missing hints default to standard; invalid/unknown hints fall back to standard (`invalid_tier_hint`, non-escalating); conflicting hints resolved by precedence `tbt_tier` > `tier` > `plan`.
  3. Hardened rejection behavior: entitlement-rejected symbols never enter desired state; channel-allocation failures remove symbols from desired so they cannot remain desired or be resubscribed on reconnect.
  4. `python -m pytest test/test_fyers_tbt.py -q` → **81 passed, 1 skipped**.
  5. `python -m pytest test/test_fyers_feed.py -q` → **12 passed**.
  6. `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
  7. Broader FYERS: `python -m pytest test/test_fyers_bse_fo_catalogue.py test/test_fyers_capabilities.py test/test_fyers_chain_request_form.py test/test_fyers_chain_symbol.py test/test_fyers_config_architecture.py test/test_fyers_decoder.py test/test_fyers_dynamic_subscriptions.py test/test_fyers_expiry_timestamp.py test/test_fyers_feed.py test/test_fyers_greeks_oi.py test/test_fyers_index_source.py test/test_fyers_integration.py test/test_fyers_login_ux.py test/test_fyers_pin.py test/test_fyers_reconnect_mapping.py test/test_fyers_socket_lifecycle.py test/test_fyers_tbt.py -q` → **203 passed, 1 skipped**.
  8. Source-manager: `python -m pytest test/test_source_controls.py test/test_source_dedup.py test/test_source_lifecycle.py -q` → **33 passed**.
  9. Subscription: `python -m pytest test/test_subscription_demand.py test/test_subscription_failover_lifecycle.py test/test_subscription_manager.py test/test_subscription_reconcile_lifecycle.py test/test_subscriptions.py test/test_md_subscriptions.py test/test_runtime_subscriptions.py -q` → **52 passed**.
  10. WebSocket/socket lifecycle: `python -m pytest test/test_fyers_socket_lifecycle.py test/test_upstox_socket_lifecycle.py -q` → **13 passed**.
- **Remaining work:**
  1. Perform live runtime verification with approved credentials/environment when available (kept pending).
  2. Run any additional broader suites if later identified as directly relevant.
- **Acceptance criteria:** Not fully satisfied (live/runtime verification pending).
- **Required tests:** As above. No failures/regressions observed.

---

## 5) Task Tracking Protocol (MANDATORY)

- **Single source of truth:** This file.
- **Statuses:** `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `DONE`.
- **After every individual task:** Immediately update this plan on disk before starting the next task.
- **Each update must record:** What was completed, files changed, commands and tests executed, exact results, failures and unresolved issues, decisions and rationale, next task.
- **No batching:** Do not mark multiple tasks DONE at once.
- **No partial DONE:** Do not mark partially completed work as DONE.
- **End of session:** Reconcile plan against actual Git working tree and leave next task clearly identified.

---

## 6) Constraints & Guardrails

- **Plan-revision only now:** No implementation until explicit approval. This document is the revised plan.
- **Preserve existing work:** Do not overwrite/discard unrelated files (including `docs/performance/`).
- **Protected zones:** Do not modify `app/auth/*`, credential storage, startup restoration, broker login/OAuth, session persistence without explicit approval.
- **Architecture:** Reuse existing abstractions. No second subscription manager, no duplicate depth store, no new deps/global config/entitlement systems.
- **Behavior:** Preserve snapshot/delta parsing, normalizer, `OrderBookStore`, `MarketService` behavior.
- **Scope:** FYERS TBT only. No unrelated HSM/Upstox/API/frontend/startup changes.
- **Git:** No commits/pushes/merges/rebases/branch operations unless explicitly requested/approved.
- **Boundaries:** Follow `docs/ARCHITECTURE_BOUNDARIES.md` and `AGENTS.md`. Run `pytest test/test_architecture_boundaries.py` and keep green.

---

## 7) Current State

- **Plan file:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated through TBT-07 audit/hardening + TBT-09 broader regression rerun)
- **Git:** `review/scanner-phase1` at `2833138d81da356fc827e5c6c7b925ee4c4cd603` (HEAD unchanged this session; working tree has uncommitted TBT-07 changes)
- **Working tree (reconciled):** `?? docs/performance/` (pre-existing, preserved), modified `brokers/fyers/tbt/feed.py`, modified `test/test_fyers_tbt.py`, modified `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`. No other implementation files changed.
- **Implemented (prior sessions):** TBT-04 (shutdown + corrective recv-task cleanup), TBT-05 (auth), TBT-06 (channel protocol), TBT-05a (symbol propagation/runtime sync), TBT-08 (fresh-snapshot gating). Focused and broader mocked regressions executed (see TBT-09, §11, §12).
- **Implemented/audited (this session):** TBT-07 entitlement-aware limits audited and hardened (tier-aware TBT symbol limits via existing resolution where possible, separate channel-capacity enforcement, graceful rejection/logging, status reporting; rejected/unallocatable symbols cannot remain desired or resubscribe on reconnect). **Locally enforced:** per-instance/connection ceilings (channels 50, tier-mapped symbol limits for this feed instance). **Not globally enforced:** account-wide symbols/connections across multiple feed instances/connections/users (requires external coordination; documented).
- **Remaining gaps:** TBT-09 live runtime verification (pending approved credentials/environment).
- **Explicit issues/uncertainties (tracked):** (1) Dynamic URL discovery endpoint (`https://api-t1.fyers.in/indus/home/tbtws`) auth contract/necessity remains **UNRESOLVED** (preserved). (2) Server acknowledgement semantics for subscribe/resume remain **UNRESOLVED**. (3) Runtime/live broker behavior **not verified** (mocked tests only). (4) Account-wide TBT limits across multiple connections/users require external coordination (documented).
- **TBT-01/02/03/04/05/06/05a/07/08 outcomes:** Preserved/extended (see §2, §9, §10, §11, §12, §13). TBT-09 broader regression rerun recorded (no failures/regressions observed in executed suites).

---

## 8) Implementation Order / Status (updated)

1. **TBT-01: Audit & reconcile protocol/URL/auth — DONE**
2. **TBT-02: Verify method signatures & lifecycle contracts — DONE (corrected)**
3. **TBT-03: Investigate FYERS TBT protocol and lifecycle integration (auth, channel resume, shutdown) — DONE** (investigation/documentation only)
4. **TBT-04: Deterministic shutdown and task lifecycle — DONE** (implemented + corrective recv-task cleanup; mocked tests)
5. **TBT-05: Authentication correction (TBT WS `appId:accessToken`) — DONE** (implemented + mocked tests; dynamic URL auth contract preserved/unresolved)
6. **TBT-06: Channel subscription and resume protocol (type-2) — DONE** (implemented + mocked tests)
7. **TBT-05a: Propagate desired symbols from FyersFeed to FyersTbtFeed — DONE** (corrective Task B; mocked tests)
8. **TBT-08: Fix reconnect recovery (invalidate stale books + require fresh snapshots) — DONE** (corrective Task C; mocked tests)
9. **TBT-07: Enforce entitlement-aware limits — DONE** (tier-aware TBT limits, separate channel capacity, graceful handling; mocked tests)
10. **TBT-09: Verification & regression — IN PROGRESS (partial)** (broader mocked regression recorded; live runtime verification pending)

**Dependency notes:**
- **Dynamic URL discovery auth contract** (`/indus/home/tbtws`) remains **UNRESOLVED**; existing behavior preserved (no guessing/removal without evidence).
- **Server acknowledgement semantics** for subscribe/resume remain **UNRESOLVED**.
- **Live broker/runtime verification** not performed.
- **Account-wide TBT limits** across multiple connections/users require external coordination (documented).

**Next task:** TBT-09 live runtime verification with approved credentials/environment when available (or additional broader regression if identified). No Git history operations performed.

---

## 9) TBT-03 Completion Record (this session)

1. **Exact plan path:** `D:\Temp\MarketHub\docs\plans\2026-11-10-fyers-tbt-integration-plan.md`
2. **Git branch and HEAD:** `review/scanner-phase1` @ `bab6d1a7869ad8aeb7a2431e32e997596adbce90` (unchanged)
3. **Files changed:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (TBT-03 investigation/documentation update only). No application code, tests, dependencies, config, or DB changes.
4. **Inspected files (source/docs):** `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `brokers/fyers/auth.py`, `app/fyers_runtime_auth.py`, `app/auth/fyers.py`, `sources/__init__.py`, `brokers/upstox/feed.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`.
5. **Commands/tests executed:** Read-only inspection only: `git status --short`, `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD`, file reads/grep for auth/token flow, protocol handling, lifecycle/call sites, and comparable feed patterns. **No tests run. No runtime/broker/network actions. No service restarts.**
6. **Findings recorded (§2.10):**
   - **TBT-03.1 (Auth):** Raw runtime access token flows unchanged to TBT and is sent as `Authorization` for dynamic URL fetch and WS connect; official TBT WS requires `<appId:accessToken>`; app_id available in existing owners but not used by TBT; no existing TBT formatter; dynamic URL auth contract unverified/distinct.
   - **TBT-03.2 (Channel resume):** Type-2 `resumeChannels`/`pauseChannels` required by docs but not implemented (type-1 only); channel allocation/retention and reconnect/resubscribe interactions documented; ack semantics unresolved.
   - **TBT-03.3 (Shutdown/task ownership):** Parent/child ownership and shared stop event confirmed; missing `FyersTbtFeed.stop()` and parent call-order exception risk confirmed; normal/cancellation/cleanup paths documented; minimal correction should follow existing lifecycle patterns without new abstractions.
7. **Status reconciliation:** TBT-03 marked **DONE (investigation/documentation only)**. No implementation marked complete.
8. **Explicit issues/dependencies:** WS auth-format discrepancy (and separate dynamic URL auth verification) remains a dependency for authenticated TBT connectivity; type-2 resume is a confirmed required gap.
9. **Remaining uncertainties:** Dynamic URL discovery auth contract/mandate; runtime effect of WS auth header construction; server acknowledgement semantics; runtime shutdown/exception consequences (not tested).
10. **Plan reconciliation (working tree):** `?? docs/performance/` (pre-existing, preserved), `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated). No other files changed.
11. **Next task:** **TBT-04 — Add idempotent shutdown to FyersTbtFeed** (NOT STARTED) or a dedicated auth-resolution implementation task, as prioritized. **Not started.**
12. **Confirmations:** Investigation/documentation only. No application/source/test/config/dependency/DB changes. No auth/URL/protocol changes. No broker login/OAuth/WebSocket connections. No runtime testing. No credential access or token exposure. No Git staging/commits/pushes/merges/rebases/resets/branch operations. All existing modified/untracked files preserved.

**Status:** Stopped after TBT-03. Awaiting explicit approval to begin TBT-04 or any implementation.

---

## 10) TBT-03 Follow-up Completion Record (shutdown lifecycle correction, this session)

1. **Exact plan path:** `D:\Temp\MarketHub\docs\plans\2026-11-10-fyers-tbt-integration-plan.md`
2. **Git branch and HEAD:** `review/scanner-phase1` @ `bab6d1a7869ad8aeb7a2431e32e997596adbce90` (unchanged)
3. **Files changed:** `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (shutdown lifecycle correction/documentation only). No application code, tests, dependencies, config, or DB changes.
4. **Inspected files (source/docs):** `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `sources/__init__.py`, `brokers/upstox/feed.py`, `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`.
5. **Commands/tests executed:** Read-only inspection only: `git status --short`, `git rev-parse --abbrev-ref HEAD`, `git rev-parse HEAD`, file reads. **No tests run. No runtime/broker/network actions. No service restarts.**
6. **Corrections made:**
   - Re-verified and documented actual receive-loop behavior (`async for message in ws` with in-loop stop-event check only).
   - Corrected plan to state stop-event signalling does **not** by itself interrupt a blocked WebSocket receive; prompt exit on stop while blocked cannot be assumed.
   - Documented that parent task cancellation does **not** automatically cancel the separately created TBT child task.
   - Documented that `_stop_tbt_channel()` can skip child cancellation/await/ref cleanup because it calls missing `FyersTbtFeed.stop()` first without containment.
   - Distinguished stop-event signalling, blocked-receive interruption, child-task cancellation, awaiting termination, and cleanup/ref release; marked cancellation-while-blocked WS/cleanup behavior as **UNRESOLVED** (not runtime-tested).
   - Updated minimal lifecycle recommendation (grounded in existing repo patterns) and added explicit future implementation acceptance criteria/focused tests for TBT-04.
7. **Status reconciliation:** TBT-03 remains **DONE (investigation/documentation only)**; follow-up correction recorded. No implementation marked complete.
8. **Remaining uncertainties:** Runtime behavior when stop/cancellation occurs while receive is blocked (promptness, WS context-manager closure, cleanup completion) remains **UNRESOLVED** (not tested). Prior unresolved items (dynamic URL auth contract, WS auth runtime effect, server ack semantics) unchanged.
9. **Plan reconciliation (working tree):** `?? docs/performance/` (pre-existing, preserved), `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated). No other files changed.
10. **Next task:** **TBT-04 — Add idempotent shutdown to FyersTbtFeed** (NOT STARTED) or a dedicated auth-resolution implementation task, as prioritized. **Not started.**
11. **Confirmations:** Documentation only. No application/source/test/config/dependency/DB changes. No auth/URL/protocol changes. No broker login/OAuth/WebSocket connections. No runtime testing. No credential access or token exposure. No Git staging/commits/pushes/merges/rebases/resets/branch operations. All existing modified/untracked files preserved.

**Status:** Stopped after TBT-03 follow-up correction. Awaiting explicit approval to begin TBT-04 or any implementation.

---

## 11) Coordinated Implementation Record (TBT-04/05/06, this session)

### 11.1 Baseline and scope
- **Branch/HEAD (before/after):** `review/scanner-phase1` @ `bab6d1a7869ad8aeb7a2431e32e997596adbce90` (unchanged).
- **Working tree (before):** `?? docs/performance/`, `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md`.
- **Working tree (after):** `?? docs/performance/` (preserved), `?? docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (updated), `M brokers/fyers/feed.py`, `M brokers/fyers/tbt/feed.py`, `M test/test_fyers_tbt.py`.
- **Authorized scope respected:** FYERS TBT implementation + parent TBT wiring + focused tests + canonical plan only. No canonical token storage/OAuth/login/credential persistence/session restoration redesign; no unrelated providers; no new dependencies; no DB/config schema changes; no Git history operations; existing untracked/modified user files preserved.

### 11.2 Online research / references consulted (and behavior established)
- **FYERS TBT Connection (official):** https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/tbt-websocket-connection/
  - Establishes TBT WS endpoint `wss://rtsocket-api.fyers.in/versova` and header `Authorization: <appId:accessToken>`; snapshot-first then diffs; server errors via error response/callback semantics noted.
- **FYERS TBT Channels and Message Types (official):** https://fyers.in/api-docs/v3/api/websockets/tick-by-tick-socket/overview/concept-of-channels/
  - Establishes type-1 subscribe/unsubscribe (`subs` 1/-1, `mode:"depth"`, channel string), type-2 switch-channel `{"type":2,"data":{"resumeChannels":[...],"pauseChannels":[...]}}` required to start stream, channel string IDs, 50 channels/connection, protobuf responses/snapshot flag/error fields.
- **FYERS WebSocket authentication notice (official):** https://fyers.in/notice-board/mandatory-websocket-authentication-from-30-september-2026
  - Establishes mandatory valid access token for WS connections and `app_id:access_token` format guidance (official SDK/custom implementations).
- **FYERS Plans & Limits (official):** https://fyers.in/api-docs/v3/getting-started/plans-and-limits/
  - Establishes TBT symbol limits per user across connections (Standard 1, Prime 15) and related plan context (entitlement enforcement not fully implemented in this session).
- **websockets asyncio connection docs (stable, consulted):** https://websockets.readthedocs.io/en/stable/reference/asyncio/connection.html
  - Establishes `recv()`/`close()`/`wait_closed()` behavior and cancellation guidance; implementation chosen to be compatible with pinned project dependency `websockets>=17,<18` (from `requirements.txt`) using explicit recv-vs-stop race and socket close for prompt interruption.
- **Python asyncio task docs (consulted):** https://docs.python.org/3/library/asyncio-task.html
  - Task cancellation/awaiting semantics informed exception-safe shutdown design.
- **Official FYERS SDK behavior:** Not directly inspected as installed package source in-session; official docs/notice-board used as primary protocol/auth evidence. SDK mentioned in repo/docs as reference only; no SDK code copied/replaced MarketHub architecture.
- **Dynamic URL discovery endpoint:** `https://api-t1.fyers.in/indus/home/tbtws` exists in current implementation but was **not documented** in fetched official pages above; contract/necessity/auth format **UNRESOLVED** (preserved existing behavior).

### 11.3 Implementation details

#### TBT-04 — Shutdown/task lifecycle
- **`brokers/fyers/tbt/feed.py`:** Added idempotent `stop()` (signals internal stop + closes active WS), active WS tracking/retirement under lock, stop-aware wait/backoff, and `CancelledError` propagation with prompt close/cleanup. Replaced `async for` receive with explicit `ws.recv()` raced against caller/internal stop events; pending receive task cancelled/awaited on stop to interrupt blocked receives. Cleanup remains idempotent (book invalidation, channel/ref/state reset) and preserves `auth_required` terminal state.
- **`brokers/fyers/feed.py`:** Hardened `_stop_tbt_channel()` to contain `feed.stop()` exceptions and always cancel/await child task and clear refs/log completion. Wired TBT construction with `app_id_getter` sourced from existing `_resolve_app_id()` (credential store/config fallback).

#### TBT-05 — Authentication
- **`brokers/fyers/tbt/feed.py`:** Added optional `app_id_getter` (fallback `config["app_id"]`), resolves app_id/token per session, and sets **TBT WebSocket** `Authorization` to `appId:accessToken` per official docs. Missing token/app_id fail clearly (`auth_required`, safe error). Canonical raw token getter/storage unchanged.
- **Dynamic URL discovery decision:** Preserved existing `GET /indus/home/tbtws` with raw token `Authorization` header and existing endpoint/flow because official contract was not established in fetched docs; **not changed/removed/guessed**.

#### TBT-06 — Channel protocol
- **`brokers/fyers/tbt/feed.py`:** Implemented type-2 switch-channel control (`resumeChannels`/`pauseChannels`, string channel IDs) and integrated with initial subscribe/resubscribe/reconnect and sequence-gap recovery (subscribe type-1 then resume type-2). Runtime subscribe resumes affected channels; unsubscribe pauses channels that become empty of desired symbols. Preserved existing book/normalizer/MarketService behavior and defensive server-error handling; no undocumented ack blocking.

### 11.4 Tests executed (exact results)
- `python -m pytest test/test_fyers_tbt.py -q` → **52 passed, 1 skipped** (includes existing TBT tests + new focused shutdown/auth/channel protocol tests; no live broker credentials/connections).
- `python -m pytest test/test_fyers_feed.py -q` → **12 passed**.
- `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.

**Focused coverage added (mocked):** shutdown while receiving/blocked, parent cancellation, WS closure/child termination, cleanup when close raises, repeated stop, no orphaned child task, parent stop continues when child stop raises, reconnect cancellation/cleanup; WS `appId:accessToken` header, raw token unchanged, missing app_id/token failures, dynamic URL raw-token header preserved, no token/header in logs; type-1/type-2 payloads and channel strings, runtime subscribe resume, empty-channel pause, reconnect resubscribe+resume, sequence-gap resubscribe+resume, server error handling, snapshot/delta preservation.

### 11.5 Remaining issues / runtime verification still required
- **Dynamic URL discovery auth contract/necessity:** UNRESOLVED (preserved existing behavior).
- **Server subscribe/resume acknowledgement semantics:** UNRESOLVED (not invented).
- **Live broker/runtime verification:** Not performed (auth acceptance, real WS/protocol behavior, reconnect timing, cancellation-while-blocked under real network).
- **Not completed in this session:** TBT-05a symbol propagation/runtime sync, TBT-07 entitlement-aware limits, TBT-08 reconnect recovery hardening/fresh-snapshot gating verification, TBT-09 full broader regression.
- **No credentials/tokens/secrets introduced** into source, tests, logs, or documentation.

### 11.6 Final reconciliation
- **Exact files changed:** `brokers/fyers/tbt/feed.py` (shutdown/auth/channel protocol/lifecycle), `brokers/fyers/feed.py` (TBT wiring app_id + robust child stop/cancel/await), `test/test_fyers_tbt.py` (focused tests + fixture compatibility), `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (plan/status/records).
- **Git diff stat:** `3 files changed, 887 insertions(+), 102 deletions(-)` (plus untracked plan/performance preserved).
- **Confirmations:** No Git staging/commit/push/merge/rebase/reset/branch switch performed. No unrelated files modified/deleted/renamed. Existing untracked/modified user files preserved. No live broker connections or real credentials used. No dependencies/config/DB/credential storage/auth-startup foundation changes.

---

## 12) Corrective Implementation Record (Tasks A/B/C, this session)

### 12.1 Baseline and scope
- **Branch/HEAD (verified):** `review/scanner-phase1` @ `43c7eb5e60ad828c4aab6fd3e5765115df6dce06` (unchanged this session).
- **Working tree (before):** `?? docs/performance/` only.
- **Working tree (after):** `?? docs/performance/` (preserved), modified `brokers/fyers/feed.py`, modified `brokers/fyers/tbt/feed.py`, modified `brokers/fyers/tbt/order_book.py`, modified `test/test_fyers_tbt.py`, modified `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`.
- **Authorized scope respected:** FYERS TBT/feed implementation, order-book lifecycle, focused tests, and canonical plan only. No auth/startup foundation, credential storage/token persistence/OAuth/login/session restoration, unrelated providers, DB/config schema, or dependency changes. No live broker operations/credentials. No Git history operations, staging, commits, or pushes.

### 12.2 Task A — Complete TBT receive-task cancellation cleanup (TBT-04 corrective follow-up)
- **Status:** DONE
- **Actual implementation changes:** Tracked active receive task in `FyersTbtFeed._recv_loop()` (`_active_recv_task`) so cleanup can access it on all exit paths (normal receive completion, internal/caller stop, outer task cancellation, WS/transport errors, unexpected exceptions). `finally` cancels/awaits any pending receive task and gathers exceptions to avoid unhandled-task warnings; `asyncio.CancelledError` semantics preserved. Reviewed `FyersFeed._stop_tbt_channel()` to ensure child-task cancellation/await/ref cleanup still occur if `feed.stop()` raises or is cancelled (cleanup contained, cancellation propagated after cleanup).
- **Exact files changed:** `brokers/fyers/tbt/feed.py`, `brokers/fyers/feed.py`, `test/test_fyers_tbt.py`.
- **Tests added/modified:** Added `TestTBTCorrectiveTaskA` covering outer cancellation while `ws.recv()` blocked, internal stop while blocked, caller stop while blocked, websocket receive failure, repeated stop calls, and parent cleanup when child stop is cancelled/raises; tests verify no receive task remains pending (`_active_recv_task` cleared/done) in covered paths.
- **Commands executed:** `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`.
- **Results:** TBT **72 passed, 1 skipped**; FYERS feed **12 passed**; architecture boundaries **17 passed**.
- **Decisions/deviations:** Added minimal `_active_recv_task` tracking attribute for deterministic cleanup/verification (no new abstraction/framework). Existing WS auth and type-2 channel resume behavior unchanged.
- **Remaining limitations:** Real-network cancellation timing/closure behavior not runtime-verified.

### 12.3 Task B — Synchronize parent instrument lifecycle with TBT subscriptions (TBT-05a)
- **Status:** DONE
- **Actual implementation changes:** Seeded TBT feed with parent `FyersFeed._desired` on TBT start (via existing `subscribe()` so desired state retained/applied on connect). Propagated runtime `add_instruments` additions and `remove_instruments` removals to active TBT feed (desired state retained before connection readiness; reconnect/resubscribe uses latest desired set). Reused existing parent lifecycle and TBT desired-symbol mechanism (no second registry/subscription manager). Deduplicated subscribe inputs to avoid duplicate wire/control actions. Preserved existing non-TBT feed behavior.
- **Exact files changed:** `brokers/fyers/feed.py`, `brokers/fyers/tbt/feed.py`, `test/test_fyers_tbt.py`.
- **Tests added/modified:** Added `TestTBTCorrectiveTaskB` covering TBT start with instruments already registered in parent feed, runtime add/remove sync, add/remove before TBT connection readiness, reconnection restoring latest desired instruments, repeated add/remove without duplicate/inconsistent subscriptions, and existing non-TBT feed behavior unchanged. Updated existing sequence-gap recovery test to use delta-gap input consistent with corrected snapshot semantics.
- **Commands executed:** `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`.
- **Results:** TBT **72 passed, 1 skipped**; FYERS feed **12 passed**; architecture boundaries **17 passed**.
- **Decisions/deviations:** Synced parent desired instruments to TBT without adding eligibility/entitlement filtering (TBT-07 and dynamic URL auth remain out of scope/unresolved as specified).
- **Remaining limitations:** TBT-supported instrument eligibility/format validation and entitlement-aware limits not enforced (TBT-07). Live broker/runtime verification pending.

### 12.4 Task C — Require a fresh snapshot after order-book invalidation (TBT-08)
- **Status:** DONE
- **Actual implementation changes:** Added explicit `LocalOrderBook.awaiting_snapshot` state (initial/uninitialized and invalidated-awaiting-snapshot vs snapshot-initialized valid). `invalidate()` requires fresh snapshot; `apply_snapshot()` reinitializes validity/sequence; `check_sequence()` rejects deltas when awaiting snapshot or on gaps. `FyersTbtFeed._process_feed()` now accepts snapshots as (re)initialization points, rejects/applies-no-publish for deltas when awaiting snapshot or sequence-invalid, invalidates and triggers resubscribe/recovery as appropriate, and publishes only valid snapshot-initialized book state. Applied consistently to initial connection, reconnect recovery, sequence gaps, and explicit invalidation paths.
- **Exact files changed:** `brokers/fyers/tbt/order_book.py`, `brokers/fyers/tbt/feed.py`, `test/test_fyers_tbt.py`.
- **Tests added/modified:** Added `TestTBTCorrectiveTaskC` covering delta before initial snapshot, delta after explicit invalidation, delta after sequence gap, delta during reconnect recovery, fresh snapshot restore permitting subsequent valid deltas, invalid snapshot not restoring validity, and no incomplete order book published while awaiting snapshot. Updated existing sequence-check tests (`test_tbt10_sequence_gap_detected`, `test_order_book_sequence_check`) to reflect corrected delta-vs-snapshot semantics (not weakened).
- **Commands executed:** `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`.
- **Results:** TBT **72 passed, 1 skipped**; FYERS feed **12 passed**; architecture boundaries **17 passed**.
- **Decisions/deviations:** Preserved existing snapshot/delta parsing, normalizer, and core book reconstruction; minimal gating added to prevent publishing incomplete depth. Existing WS auth and type-2 channel resume behavior unchanged.
- **Remaining limitations:** Live broker/runtime verification pending.

### 12.5 Verification summary (executed this session)
- `python -m pytest test/test_fyers_tbt.py -q` → **72 passed, 1 skipped**
- `python -m pytest test/test_fyers_feed.py -q` → **12 passed**
- `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**
- No live broker operations or real credentials used. No additional directly relevant test suites identified/executed beyond the above.

### 12.6 Remaining blockers and limitations
- **TBT-07:** entitlement and plan-limit handling not implemented (out of scope).
- **Dynamic URL discovery auth contract:** remains **UNRESOLVED**; existing behavior preserved.
- **Server subscribe/resume acknowledgement semantics:** **UNRESOLVED** (not invented).
- **Live broker/runtime verification:** not performed.
- **TBT-09:** broader regression/runtime verification pending.

### 12.7 Confirmations
- Existing untracked files preserved (`docs/performance/` untouched).
- No Git history operations, staging, commits, or pushes performed.
- No credentials/tokens/secrets introduced into source, tests, logs, or documentation.
- Changes limited to Tasks A/B/C corrective scope plus necessary tests and plan documentation.

---

## 13) TBT-07 Audit/Hardening + TBT-09 Rerun Record (this session)

### 13.1 Baseline and scope
- **Branch/HEAD (verified):** `review/scanner-phase1` @ `2833138d81da356fc827e5c6c7b925ee4c4cd603` (unchanged this session).
- **Working tree (before):** `?? docs/performance/`, modified `brokers/fyers/tbt/feed.py`, modified `test/test_fyers_tbt.py`, modified `docs/plans/2026-11-10-fyers-tbt-integration-plan.md` (uncommitted TBT-07 baseline).
- **Working tree (after):** `?? docs/performance/` (preserved), modified `brokers/fyers/tbt/feed.py`, modified `test/test_fyers_tbt.py`, modified `docs/plans/2026-11-10-fyers-tbt-integration-plan.md`.
- **Authorized scope respected:** FYERS TBT implementation + focused tests + canonical plan only. No auth/startup foundation, credential storage/token persistence/OAuth/login/session restoration, unrelated providers, DB/config schema, or dependency changes. No live broker operations/credentials. No Git history operations, staging, commits, or pushes.

### 13.2 Audit findings (construction/ownership/lifecycle)
- **Construction/ownership:** `FyersFeed` creates and owns one `FyersTbtFeed` instance when `tbt_enabled` (`_start_tbt_channel()`), seeds it with the parent feed's current desired instrument set, and runs it as a child task; stopped via `_stop_tbt_channel()` (idempotent/exception-safe). Parent add/remove operations sync desired instruments to TBT.
- **Connection lifecycle:** each `FyersTbtFeed` instance manages one TBT WebSocket connection (`_ws`/`_ws_lock`, single run loop). No multi-connection fanout exists within a single TBT feed instance.
- **Multiple instances:** multiple `FyersFeed`/`FyersTbtFeed` instances can exist if multiple FYERS feed sources are configured/instantiated. Therefore **account-wide/per-user-across-connections limits are not globally enforceable in-process without external coordination**; enforcement is local to each TBT feed instance/connection and fails gracefully.

### 13.3 Tier resolution verification
- **Hints/precedence:** `tbt_tier` preferred, then `tier`, then `plan` (first non-empty wins). Cached per feed instance after first resolution.
- **Missing:** defaults to documented `standard` tier/limits.
- **Invalid/unknown:** falls back to `standard` with `source=invalid_tier_hint` (non-escalating; never grants higher limits).
- **Conflicting:** resolved by precedence above (e.g. `tbt_tier=prime` wins over differing `tier`/`plan`).
- **Existing resolution reuse:** attempts `providers.capacity.resolve_entitlement("fyers", tier=...)` where possible for tier/source semantics, but applies **TBT-specific** documented limits (distinct from HSM capability limits).

### 13.4 Rejection behavior hardening
- **Entitlement-rejected symbols:** never added to desired state and never sent/subscribed.
- **Channel-capacity failures:** if channel allocation fails during connected subscribe or reconnect resubscribe, the symbol is removed from desired state so it cannot remain desired or be retried/subscribed accidentally on reconnect.
- **Reconnect:** `_resubscribe_all()` restores only allocatable desired symbols and drops unallocatable ones (with safe error/logging).

### 13.5 Locally enforced vs genuinely account-wide enforced limits
- **Locally enforced (per TBT feed instance/connection):** channels per connection (50), symbols per connection (tier-mapped), symbols per user ceiling applied to this instance's desired set as `min(per_user, per_connection)`, graceful rejection/logging, no accidental desired-state retention for rejected/unallocatable symbols.
- **Not globally/account-wide enforced here:** symbols per user across multiple connections/apps/users, connections per user across multiple feed instances/processes. Requires external coordination (documented). `status().tbt_limits.connections_per_user` is reported for visibility only.

### 13.6 Tests added/modified and results
- **Added/updated:** `TestTBTEntitlementLimits` coverage for missing/invalid/conflicting tier hints, channel-capacity failure not leaving desired state, reconnect dropping unallocatable desired symbols (plus prior entitlement/status checks).
- **Commands executed:** `python -m pytest test/test_fyers_tbt.py -q`; `python -m pytest test/test_fyers_feed.py -q`; `python -m pytest test/test_architecture_boundaries.py -q`; broader FYERS/source-manager/subscription/socket-lifecycle suites (see §9).
- **Exact results:** TBT **81 passed, 1 skipped**. FYERS feed **12 passed**. Architecture boundaries **17 passed**. Broader FYERS **203 passed, 1 skipped**. Source-manager **33 passed**. Subscription **52 passed**. WebSocket/socket lifecycle **13 passed**. No failures/regressions observed.

### 13.7 Remaining issues / runtime verification
- **Account-wide TBT limits** across multiple connections/users require external coordination (documented).
- **Dynamic URL discovery auth contract:** UNRESOLVED (preserved).
- **Server subscribe/resume acknowledgement semantics:** UNRESOLVED.
- **Live broker/runtime verification:** not performed (pending approved credentials/environment).

### 13.8 Confirmations
- Existing untracked files preserved (`docs/performance/` untouched).
- No Git history operations, staging, commits, or pushes performed.
- No credentials/tokens/secrets introduced into source, tests, logs, or documentation.
