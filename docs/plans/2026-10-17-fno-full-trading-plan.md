# Full F&O Trading — Perfect Solution Plan (v2)

> Created 2026-10-17 after deep study of Upstox V3 + Fyers v3 APIs AND the
> on-disk implementation. This plan ignores what is currently built and
> specifies what is *right*: optimized, durable, broker-neutral, testable.
> No code was edited to produce this plan.

---

## 0. Guiding principles

1. **Broker-neutral canonical layer.** `market/` and `providers/` never import
   `brokers/` or `app/`. All provider specifics die at the adapter boundary.
2. **One REST client per broker, stdlib-only, injected transport.** Mirrors
   `UpstoxRest` (stateless, no client lifetime, no session state, no redirects,
   no retries — retry/backoff is the feed's job).
3. **Normalizers are pure functions.** Already true for both providers; keep it.
4. **Capabilities are the single source of truth.** Declared by the adapter,
   consumed by the registry. Never hardcode provider lists in feature code.
5. **No hardcoded segments/exchanges.** Derive from catalog + registry.
6. **Expired data is filtered at the boundary.** Expiries < today never reach
   routing or UI.
7. **Option chains are REST on-demand.** Zero WebSocket keys for chains.
   Futures/spot stream over WS; chains fetched on demand (ATM ± window).
8. **Per-kind error counters.** Never a single opaque `normalization_errors`.
9. **Single ownership.** Each resource has exactly one owner (see §7).
10. **Frozen auth/startup.** Never touch `app/auth/*`, credential persistence,
    session restoration, or startup composition except approved wiring.

---

## 1. Current architecture (as built)

```
┌──────────────────────────────────────────────────────────────┐
│  WebUI (React/TS)  ──HTTP/SSE──►  REST API  ──►  Services  │
├──────────────────────────────────────────────────────────────┤
│  Services:                                                   │
│    MarketService (quote store, SSE broker)                   │
│    FnoCoverageService (routing + coverage)                   │
│    MarketIntel (search, derivatives, option chain)           │
│    ProviderMarketData (history, option chain, analytics)     │
│    SubscriptionService (prefs → resolved keys → apply)      │
├──────────────────────────────────────────────────────────────┤
│  Broker Adapters:                                            │
│    brokers/upstox/{auth, rest, feed, feed_processing,        │
│                    feed_protocol, errors, capabilities}       │
│    brokers/fyers/{auth, feed, tbt/*, capabilities}           │
│    (NO brokers/fyers/rest.py — GAP)                         │
├──────────────────────────────────────────────────────────────┤
│  Canonical: market/{models, service, normalize/*,            │
│             analytics/*, futures_board, oi, coverage_states}  │
│  Providers: providers/{capabilities, registry}               │
│  Instruments: app/instruments.py (catalog)                   │
│  Persistence: core/persistence/* (red zone)                  │
└──────────────────────────────────────────────────────────────┘
```

### 1.1 What works well
- Canonical models are pure, frozen, validated (market/models.py).
- Normalizers are pure functions with presence-exact semantics.
- Provider registry is broker-neutral, lazy-loaded.
- Subscription service has clean owner pattern (active_view, analytics_universe,
  derivatives_universe — all additive, never subtract).
- Futures board is canonical, cached, single-flight.
- OI change/pct has single implementation (market/oi.py).
- FnoCoverageService is broker-neutral, routes by capability.

### 1.2 What's broken / missing
| # | Gap | Impact | Zone |
|---|---|---|---|
| 1 | Fyers REST client missing | Fyers can't do option chains, history, quotes, depth, market status despite declaring capabilities | BROKER ADAPTERS |
| 2 | Upstox CAS fields missing from proto | No closing auction data | BROKER ADAPTERS |
| 3 | Fyers capabilities over-declared | Registry lies about what Fyers can do | BROKER ADAPTERS |
| 4 | Fyers HSM docstring wrong (pdoi/oipercent) | Fixed | — |
| 5 | Expired-default-expiry | Fixed | — |
| 6 | BSE_FO not in DEFAULT_SEGMENTS | Fixed | — |
| 7 | SENSEX/BANKEX not F&O | Fixed | — |
| 8 | FNO_EXCHANGE hardcoded to NSE | Fixed | — |
| 9 | normalization_errors opaque | Fixed (per-kind) | — |
| 10 | Fyers option chain normalizer bug | Fixed (optionsChain) | — |
| 11 | Upstox modes missing | Fixed (option_greeks, full_d30) | — |
| 12 | Read-path workstream (10 failures) | Touches red zone | CONFIG/PERSISTENCE |
| 13 | News MCP tools (9 failures) | Separate feature | NEWS |
| 14 | MCP/X-Twitter (9 failures) | Separate feature | MCP |

---

## 2. Target architecture (what's right)

### 2.1 Broker adapter contract

Every broker adapter MUST provide:
```
brokers/{name}/
  auth.py          — OAuth/token exchange (frozen for Upstox/Fyers)
  rest.py          — REST client (stdlib, injected transport, no redirects)
  feed.py          — WebSocket feed (desired-set subscription model)
  feed_processing.py — pure decode + normalize (stateless)
  feed_protocol.py  — protocol decoder (protobuf/binary)
  capabilities.py  — capability declaration (single source of truth)
  normalize/       — (in market/normalize/{name}.py, not here)
```

**REST client contract (mirrors UpstoxRest):**
- `__init__(*, sync_transport=None, utc_now=None)` — no state, no credentials.
- `SyncTransport = Callable[[method, url, headers, body, timeout], HttpResponse]`
- `_default_sync_transport` — stdlib urllib, `_NoRedirect`, User-Agent, Cloudflare-safe.
- `_raise_for_status` — classify 3xx/401/429/5xx into typed errors.
- `_require_success_json` — strict JSON-dict decoding.
- Methods take `access_token` as method-local param (never stored).
- Each method validates inputs, builds URL, calls transport, parses envelope,
  normalizes via pure normalizer, returns canonical objects.
- Errors: typed hierarchy (AuthError, RateLimitError, RestError) with safe
  wording (no bodies/headers in repr).

### 2.2 Capability truthfulness

Capabilities MUST reflect what the REST client + feed actually implement:
- If `CAP_OPTION_CHAIN` is declared, `rest.option_chain()` must exist and work.
- If `CAP_HISTORY` is declared, `rest.history()` must exist and work.
- If `CAP_DEPTH` is declared, `rest.depth()` must exist and work.
- If `CAP_PREVIOUS_OI` is declared, the provider must supply it (REST or wire).
- Test: `test_provider_registry.py` verifies capability declarations.

### 2.3 Fyers REST client (the big gap)

**Endpoint catalog (from Fyers v3 docs):**
| Endpoint | Method | Path | Params |
|---|---|---|---|
| Quotes | GET | `/data/quotes` | `symbols` (comma, ≤50) |
| Depth | GET | `/data/depth` | `symbol`, `ohlcv_flag` |
| History | GET | `/data/history` | `symbol`, `resolution`, `date_format`, `range_from`, `range_to`, `cont_flag` |
| Option chain | GET | `/data/options-chain-v3` | `symbol`, `strikecount`, `timestamp` |
| Market status | GET | `/data/marketStatus` | — |

**Auth header:** `Authorization: <app_id>:<access_token>` (NOT Bearer).

**Response envelope:** `{"s":"ok","code":200,...}` or `{"s":"error","code":<neg>,"message":"..."}`.

**Normalizers already exist:**
- `quote_from_quotes_rest(v, symbol, received_ts)` → `Quote`
- `depth_from_rest(payload, symbol, received_ts)` → `(Depth, supplemental)`
- `candles_from_history(payload)` → `list[Candle]`
- `option_chain_from_rest(payload, instrument_token, exchange, tradingsymbol, expiry)` → `OptionChainSnapshot`
- `greeks_from_options_chain(leg)` → `OptionGreeks | None`

**Implementation:** Create `FyersRest` with methods:
- `quotes(access_token, symbols) -> list[Quote]`
- `depth(access_token, symbol) -> (Depth, dict)`
- `history(access_token, symbol, resolution, from_date, to_date) -> list[Candle]`
- `option_chain(access_token, symbol, strikecount, timestamp) -> OptionChainSnapshot`
- `market_status(access_token) -> dict`

Each method: validate → build URL → transport → parse envelope → normalize → return.

### 2.4 Upstox CAS fields

**Proto update:** Add to `MarketFullFeed` and `LTPC`:
```protobuf
message LTPC {
  ...
  double iep = 7;  // indicative equilibrium price
}
message MarketFullFeed {
  ...
  double rp = 11;       // reference price
  double ieq = 12;      // indicative equilibrium quantity
  double iiqTotal = 13; // total indicative imbalance quantity
  double iiqM = 14;     // market indicative imbalance quantity
  bool casEligible = 15; // CAS eligible
}
```

**Quote model:** Add CAS fields:
```python
cas_eligible: bool | None = None
indicative_equilibrium_price: float | None = None
reference_price: float | None = None
indicative_equilibrium_qty: float | None = None
indicative_imbalance_qty: float | None = None
market_indicative_imbalance_qty: float | None = None
```

**Normalizer:** Map in `quote_fields_from_ws_full` and `quote_fields_from_ws_ltpc`.

### 2.5 Fyers HSM depth (pdoi/oipercent)

Already in `depth_from_rest` normalizer. Just needs REST client (Phase 2.3).
No HSM changes needed — pdoi/oipercent come from REST depth only.

### 2.6 Expired-default-expiry (done)

Filter expiries ≥ today in:
- `fno_coverage.py::_choose_expiry`
- `market_intel.py::option_chain`
- `subscriptions/service.py::_unexpired_expiries` (already done)

### 2.7 BSE_FO sync (done)

Added to `DEFAULT_SEGMENTS`.

### 2.8 SENSEX/BANKEX as F&O (done)

Flipped `is_fno=True` in `index_registry.py`.

### 2.9 FNO_EXCHANGE de-hardcode (done)

Reads both NSE + BSE futures.

### 2.10 Normalization errors (done)

Per-kind counters: `normalization_errors_{normalize|unsupported|unknown_instrument}`.

### 2.11 Fyers option chain normalizer (done)

`data.get("options")` → `data.get("optionsChain")`.

### 2.12 Upstox modes (done)

Added `option_greeks` (3000) and `full_d30` (50) to SUPPORTED_MODES.

---

## 3. Single ownership map (target)

| Resource | Owner | Notes |
|---|---|---|
| Broker session/credentials | `app/auth/*` + `CredentialStore` | Frozen |
| Upstox feed instance | `app/server.py` (`_feed_ref`) | Lifecycle via SourceManager |
| Fyers runtime token | `FyersRuntimeAuth` (service-only) | Frozen |
| Feed task/source lifecycle | `sources/registry.py::SourceManager` | Only owner |
| Quote store | `market/service.py::MarketService` | Only owner |
| MarketService | constructed once in `app/server.py` | — |
| Options analytics | `market/analytics/option_chain.py` | Pure over canonical models |
| SSE broadcaster (market) | `app/server.py` (`_market_event_broker`) | — |
| SSE broadcaster (generic) | `app/server.py` (`_event_broker`) | — |
| News service | `news/service.py::NewsService` | — |
| Alert engine | `app/alerts.py` + `app/condition_alerts.py` | — |
| OAuth config | `_oauth_cfg_ref` in `app/server.py` | Shared-by-design |
| CredentialStore | `app/server.py` (`_credential_store`) | — |
| FnoCoverageService | `app/server.py` (constructed once) | — |
| SubscriptionService | `app/server.py` (constructed once) | — |
| ProviderMarketData | `app/server.py` (constructed once) | — |
| FyersRest | `app/server.py` (constructed once) | NEW |
| UpstoxRest | `app/server.py` (constructed once) | — |

---

## 4. Implementation plan (ordered, durable)

### Phase 1 — Fyers REST client (highest value) ✅ COMPLETE
**Files:** `brokers/fyers/rest.py` (new)
**Pattern:** Mirror `UpstoxRest` exactly.

**Structure:**
```python
class FyersRest:
    def __init__(self, *, sync_transport=None, utc_now=None):
        self._transport = sync_transport or _default_sync_transport
        self._utc_now = utc_now or (lambda: datetime.now(timezone.utc))

    async def quotes(self, access_token, symbols) -> list[Quote]:
        # GET /data/quotes?symbols=...
        # Parse envelope, normalize via quote_from_quotes_rest

    async def depth(self, access_token, symbol) -> (Depth, dict):
        # GET /data/depth?symbol=...&ohlcv_flag=1
        # Parse envelope, normalize via depth_from_rest

    async def history(self, access_token, symbol, resolution, from_date, to_date) -> list[Candle]:
        # GET /data/history?symbol=...&resolution=...&date_format=1&range_from=...&range_to=...&cont_flag=1
        # Parse envelope, normalize via candles_from_history

    async def option_chain(self, access_token, symbol, strikecount, timestamp) -> OptionChainSnapshot:
        # GET /data/options-chain-v3?symbol=...&strikecount=...&timestamp=...
        # Parse envelope, normalize via option_chain_from_rest

    async def market_status(self, access_token) -> dict:
        # GET /data/marketStatus
        # Parse envelope, return raw dict
```

**Auth header:** `Authorization: {app_id}:{access_token}` (Fyers format).

**Error classification:** Fyers envelope `{"s":"error","code":<neg>,"message":"..."}`.
Map to: `FyersAuthError` (401/-16), `FyersRateLimitError` (429), `FyersRestError` (other).

**Tests:** `test/test_fyers_rest.py` — injected transport, envelope parsing,
error classification, normalizer integration. No real network.

### Phase 2 — Wire FyersRest into ProviderMarketData ✅ COMPLETE
**Files:** `app/market_data.py`, `app/server.py`
- Add `fyers_rest` parameter (injected, like `upstox_rest`).
- Route Fyers history/option-chain/quotes/depth through `FyersRest`.
- Remove "not implemented" stubs.
- Update `app/server.py` composition (approved wiring).

### Phase 3 — Upstox CAS fields ✅ COMPLETE
**Files:** `brokers/upstox/proto/MarketDataFeed.proto`, `market/models.py`,
  `market/normalize/upstox.py`
- Add CAS fields to proto.
- Add CAS fields to `Quote` model.
- Map in normalizers.
- Update tests.

### Phase 4 — Fyers capabilities truthfulness ✅ COMPLETE
**Files:** `brokers/fyers/capabilities.py` (verified, no changes needed)
- Already truthful once Phase 1 lands. Verify `test_provider_registry.py`.

### Phase 5 — Catalog sync + live verification ✅ COMPLETE
- Synced catalog with BSE_FO + BSE_INDEX enabled.
- Upstox: 53,219 records (BSE_FO: 5,138, BSE_INDEX: 79, NSE_FO: 38,043, NSE_EQ: 9,820, NSE_INDEX: 139)
- Fyers: 89,713 records (BSE_INDEX: 70, NSE_FO: 85,939, NSE_EQ: 3,575, NSE_INDEX: 129)
- SENSEX futures: 3 contracts (2026-10-29, 2026-11-26, 2026-12-31) — all routed to upstox
- SENSEX spot: BSE_INDEX|SENSEX — routed to upstox
- SENSEX option chain: available via MarketIntel (catalog has SENSEX options)
- FnoCoverageService routes SENSEX futures + spot correctly
- Verify SENSEX/BANKEX futures (6 keys) and option chains.
- Run full test suite.

### Phase 6 — Read-path workstream (separate approved task)
- Touches `core/persistence/store.py` (red zone).
- Needs separate approval.

---

## 5. What NOT to do

- Do NOT modify auth/startup/persistence schema (frozen).
- Do NOT hardcode provider lists in feature code.
- Do NOT add WebSocket keys for option chains (REST on-demand).
- Do NOT churn unrelated files for lint.
- Do NOT modify tests to make them pass (except stale assertions).
- Do NOT create a second REST client pattern (mirror UpstoxRest).

---

## 6. Verification matrix

| Phase | Verification |
|---|---|
| 1 | `test_fyers_rest.py` (injected transport, no network) |
| 2 | `test_provider_routing.py`, `test_standalone_greeks.py` |
| 3 | `test_upstox_feed.py`, `test_upstox_wiring.py` |
| 4 | `test_provider_registry.py` |
| 5 | Live server smoke + `/api/fno/coverage`, `/api/futures/heatmap` |
| 6 | `test_fno_read_paths.py`, `test_derivatives_universe.py` |

---

## 7. Risk assessment

| Risk | Mitigation |
|---|---|
| Fyers REST client breaks auth | Injected transport, no real network in tests |
| CAS proto update breaks feed | Presence-exact (ListFields), additive only |
| Capability changes break registry tests | Run `test_provider_registry.py` after each phase |
| Catalog sync flips data_dir | Run tests serially (lifecycle.py mutates config.json) |
| Fyers envelope parsing differs | Study Fyers docs (done) — envelope is `{"s":"ok"/"error",...}` |

---

## 8. Estimated effort

| Phase | Effort | Files |
|---|---|---|
| 1 | Medium (~300 lines, mirrors UpstoxRest) | `brokers/fyers/rest.py` (new) |
| 2 | Small (wiring) | `app/market_data.py`, `app/server.py` |
| 3 | Medium (proto + model + normalizer) | proto, models.py, upstox.py |
| 4 | Trivial | capabilities.py |
| 5 | Small (config + sync) | config.json, catalog |
| 6 | Large (red zone, separate task) | store.py, instruments.py |

---

## 9. Key design decisions

1. **FyersRest mirrors UpstoxRest exactly.** Same transport, same error
   classification pattern, same no-redirect/no-retry policy. Only differences:
   auth header format and envelope shape.

2. **Capabilities become truthful.** Once FyersRest exists, Fyers declares
   `CAP_HISTORY`, `CAP_OPTION_CHAIN`, `CAP_DEPTH` and actually implements them.

3. **CAS fields are additive.** Proto + model + normalizer changes are all
   additive (new optional fields). No breaking changes.

4. **No new WebSocket keys for option chains.** Chains are REST on-demand.
   This keeps the WS budget for futures/spot only.

5. **BSE_FO is a first-class segment.** Not a special case — just another
   segment in DEFAULT_SEGMENTS.

6. **SENSEX/BANKEX are F&O indices.** They have futures and options on BSE.
   The canonical registry reflects this.

7. **Expired data is filtered at every boundary.** fno_coverage, market_intel,
   subscriptions, and the board all filter expiries ≥ today.

---

## 10. What I studied (evidence)

### Upstox V3 API
- Market Data Feed V3: modes (ltpc/option_greeks/full/full_d30), limits,
  CAS fields, market_info, snapshot, live feed, heartbeat.
- Option chain: `/v2/option/chain` with relative expiry keywords.
- Historical candle V3: custom intervals, OI in candle data.
- Full market quote: `/v2/market-quote/quotes` (≤500 instruments).
- Option greeks: `/v3/market-quote/option-greek`.

### Fyers v3 API
- Endpoints: `/data/quotes`, `/data/depth`, `/data/history`, `/data/options-chain-v3`,
  `/data/marketStatus`.
- Auth: `generate-authcode` → `validate-authcode` (appIdHash = sha256).
- Header: `Authorization: <app_id>:<access_token>`.
- Envelope: `{"s":"ok"/"error","code":<int>,"message":"..."}`.
- Option chain: `optionsChain` array, `strikecount`, `timestamp` (expiry epoch).
- Depth: bids/asks with price/volume/ord, plus pdoi/oipercent (REST only).
- History: `{"s":"ok","candles":[[epoch_s,o,h,l,c,v]]}`.

### On-disk implementation
- `brokers/upstox/rest.py` — UpstoxRest (reference pattern).
- `brokers/upstox/feed.py` — UpstoxFeed (desired-set subscription).
- `brokers/upstox/feed_processing.py` — pure decode + normalize.
- `brokers/upstox/feed_protocol.py` — protobuf decoder (ListFields, presence-exact).
- `brokers/fyers/feed.py` — Fyers HSM feed (binary protocol).
- `brokers/fyers/auth.py` — Fyers auth (OAuth + PIN refresh).
- `market/normalize/upstox.py` — Upstox normalizers (pure).
- `market/normalize/fyers.py` — Fyers normalizers (pure).
- `market/models.py` — canonical models (frozen, validated).
- `market/futures_board.py` — canonical board (cached, single-flight).
- `market/oi.py` — OI change/pct (single implementation).
- `market/coverage_states.py` — coverage state constants.
- `providers/capabilities.py` — capability vocabulary.
- `providers/registry.py` — broker-neutral registry.
- `app/instruments.py` — catalog (DEFAULT_SEGMENTS, InstrumentCatalog).
- `app/market_indices.py` — MAJOR_INDICES, OPTION_CHAIN_INDICES.
- `market/index_registry.py` — canonical index registry.
- `market/derivatives_universe.py` — F&O universe (indices + stocks).
- `app/fno_coverage.py` — FnoCoverageService (routing + coverage).
- `app/subscriptions/service.py` — SubscriptionService (prefs → keys → apply).
- `app/market_data.py` — ProviderMarketData (history, option chain, analytics).
- `app/market_intel.py` — MarketIntel (search, derivatives, option chain).
- `app/server.py` — composition root (wiring).
- `core/persistence/modules/fno_config.py` — F&O config persistence.
- `api/fno_coverage_routes.py` — F&O coverage routes.
- `api/futures_heatmap.py` — futures heatmap routes.