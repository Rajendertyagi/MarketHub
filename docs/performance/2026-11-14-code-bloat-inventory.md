# MarketHub — Code Bloat & Simplification Inventory (2026-11-14)

**Date:** 2026-11-14  
**Branch / HEAD:** `develop` @ `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Scope:** Read-only static audit across `app`, `api`, `core`, `market`, `brokers`, `news`, `sources`, `frontend/src`, `mcp_server`, `yfinance_service`, `x_twitter`, manifests, and architecture tests/docs.  
**Rule:** No item is classified as dead solely due to missing direct import; runtime registration, config-driven loading, protocol/duck-typed use, and intentional test surfaces were considered.

## Legend
- **Confirmed:** directly evidenced in code/usage.
- **Suspected:** plausible from names/patterns/structure; needs body-level or runtime proof before action.
- **Not dead/used:** explicitly verified as active despite appearing removable.

---

## 1. Confirmed candidates / complexity hotspots

| ID | File(s) / symbol(s) | Current callers/dependencies | Actually used? | Functionality | Duplicate/overlap? | Removal/simplification risk | Tests required before change |
|---|---|---|---|---|---|---|---|
| B-01 | `app/market_identity.py` — `MarketInstrumentIdentityResolver`, lazy re-exports from `app.canonical_identity` | `app/server.py` imports as `_IdentityResolver`; condition-alert/identity tests (`test/test_market_identity_resolver.py`, `test/test_condition_alert_*`, `test/test_b7_multi_target.py`, `test/test_b8_alert_locks.py`, `test/test_condition_groups.py`, `test/test_instrument_segments.py`) | **Yes** | Compatibility entry point for condition-alert identity resolution; delegates to canonical identity with alert-oriented logging | Overlays/duplicates surface of `app/canonical_identity.py` | **Medium–High** (identity diagnostics/alert semantics) | Condition-alert identity/engine suites, canonical/quote parity tests |
| B-02 | `app/previous_oi.py` — re-exports `BASIS_*`, `oi_change_and_pct`, `quote_field` from `market.oi`; defines `PreviousOiRecorder` | `app/server.py` constructs recorder; `test/test_previous_oi.py`, `test/test_fno_lifecycle.py` | **Yes** | OI snapshot recording + convenience import surface | Duplicate import surface (not logic) | **Low–Medium** (API churn) | `test/test_previous_oi.py`, `test/test_fno_lifecycle.py`, F&O/OI consumers |
| B-03 | `api/routes.py` and `api/product_routes.py` — repeated helper names/patterns (`_json`, `_fail`, `_save_feed`, `_delete_feed`, `_feed_status`, `_forget_session`, `_status`, etc.) | Local route-builder helpers in large coupled modules composed by `app/server.py` | **Yes** (local helpers) | Request/response shaping, validation, feed/config/status/session helpers | **Likely body-level duplication** (name/pattern confirmed; equivalence not fully proven) | **Medium** (response/auth/feed behavior drift) | Affected endpoint tests, auth/settings/feed smoke tests, architecture checks |
| B-04 | `core/persistence/store.py` — `EventStore` facade with many pass-through methods to `core/persistence/modules/*` | Broad app/service/MCP dependency (`app/server.py`, services, `mcp_server/services.py`, many modules) | **Yes (core)** | Stable persistence API, connection/transaction orchestration, schema migration entry | Repetitive wrapper patterns/internal duplication | **High** (transactions/migrations/replay semantics; protected zone) | Persistence/schema/migration/replay/delivery/product/news/bar/alert tests |
| B-05 | `frontend/src/routes/router.tsx` — eager imports for almost all views; only `ChartView` lazy | App router/shell | **Yes** | Route composition | Not duplicate code, but unnecessary eager loading/bundle coupling | **Medium** (route loading regressions) | Frontend build, route smoke/e2e, navigation tests |
| B-06 | `frontend/dist/index.html` — global modulepreload of `echarts` chunk | Browser initial load | **Yes** | Asset preload hints | Unnecessary preload for non-chart routes | **Low–Medium** (chunk/loading behavior) | Build output verification, browser load traces, chart/non-chart route smoke |
| B-07 | `api/fno_coverage_routes.py` (`_catalog`) + `app/fno_coverage.py` (`FnoCoverageService.plan`) — full plan recomputed for catalog summary | `/api/fno/catalog` clients/UI | **Yes** | Catalog endpoint returns sync/segment + F&O summary | Recomputes same heavy plan as `/api/fno/coverage` then discards detail | **Medium** (cache invalidation/staleness) | F&O catalog/coverage API tests, invalidation tests |
| B-08 | `frontend/src/features/logs/useLogs.ts` — raw `EventSource` per mount + per-message prepend/slice, separate from shared `SSEManager` | Logs view/settings logging panel | **Yes** | Live log stream ingestion | Parallel SSE implementation vs `streams/SSEManager.ts` | **Low–Medium** (log UX/stream behavior) | Logs UI/stream tests, manual log-burst verification |
| B-09 | Legacy/backward-compat branches (inventory, not single delete) — examples: `app/server.py` legacy Fyers credential migration, `app/subscriptions/service.py`/`__init__.py` legacy config import, `app/secrets_store.py` legacy layouts, `app/auth/upstox_session.py` legacy rejection payloads, `core/persistence/modules/{products,condition_alerts,schema,news}.py` compat behavior, broker feed protocol compat comments/paths | Runtime/migrations/adapters/persistence as applicable | **Mostly yes** (safety paths) | Backward compatibility/migration support | Redundant only when preconditions no longer exist | **Medium–Very High** (data/auth/feed correctness) | Per-branch migration/restore/auth/persistence/feed tests |

## 2. Suspected candidates (need proof before action)

| ID | File(s) / symbol(s) | Evidence | Why suspected | Risk | Proof/tests needed |
|---|---|---|---|---|---|
| S-01 | `app/canonical_identity.py`, `app/instrument_identity.py`, `app/market_identity.py` | Three identity-related modules; `instrument_identity` and `market_identity` depend on canonical concepts but serve different targets (storage-key lookup vs condition-alert resolver) | Conceptual overlap/extra abstraction layers | **High** | Full caller/semantic map, quote parity and condition-alert identity tests |
| S-02 | `api/routes.py` / `api/product_routes.py` helper bodies (`_json`, `_fail`, feed helpers) | Same helper names and similar concerns | Body-level duplication likely but unproven | **Medium** | Diff implementations, endpoint contract tests |
| S-03 | `mcp_server/services.py` — `Services` dataclass (broad service locator) | Many optional service fields passed widely | Over-broad dependency container/design smell | **Medium** | MCP registry/tool contract tests if narrowed |
| S-04 | Repeated broker feed lifecycle helper names in `brokers/upstox/feed.py`, `brokers/fyers/feed.py`, `brokers/fyers/tbt/feed.py` (`_run_session`, `_recv_loop`, `_wait_or_stop`, `_close_live_ws`, `_note_error`, etc.) | Similar names/patterns across protocol-specific feeds | Possible extractable mechanics, but implementations differ | **High** | Characterization tests per feed/protocol, reconnect/lifecycle tests |
| S-05 | Repeated generic helper names across modules (`_status`, `_now`, `_norm`, `_resolve`) | Name collisions in AST scan | Mostly heterogeneous semantics; search signal only | **Medium** | Per-helper semantic comparison and module tests |
| S-06 | Unused frontend/backend dependencies beyond manifests | No confirmed unused deps found | Some deps may be weakly used or replaceable | **Medium** | Import graph + bundle analysis + runtime/registration checks |

## 3. Explicitly not dead / not unused

| File(s) / symbol(s) | Evidence of use | Why it may look removable | Verdict |
|---|---|---|---|
| `sources/test_source.py` — `TestSource` | Registered in `sources/registry.py`; referenced by frontend settings test-source flow (`frontend/src/features/settings/api.ts`, `types.ts`, `useSettings.ts`); tests/docs reference it | Disabled by default in config | **Not dead** |
| `app/previous_oi.py` — `PreviousOiRecorder` | Constructed in `app/server.py`; tested in `test/test_previous_oi.py`, `test/test_fno_lifecycle.py` | Small module/re-export wrapper | **Not dead** |
| `app/fyers_index_source.py` — `FyersIndexSource`, `run_fyers_index_poll_loop` | Imported/wired/started in `app/server.py`; tests and F&O lifecycle coverage | Specialized fallback/poller | **Not dead** |
| `app/fyers_runtime_auth.py` — `FyersRuntimeAuth` | Wired in `app/server.py`, used in auth/runtime paths; architecture/auth tests reference it | Auth-adjacent helper | **Not dead** (protected-adjacent) |
| `yfinance_service/*` and `yfinance` dependency | `app/server.py` initializes `YahooFinanceService`; `mcp_server/services.py`, `mcp_server/tools/yahoo_tools.py`, registry/contract expose tools; tests/docs exist | External/research-only capability | **Not unused** |
| `app/market_identity.py` | Runtime import in `app/server.py` and extensive condition-alert tests | Compatibility shim | **Not dead** (consolidation candidate only) |

## 4. Recommended simplification sequence (safe)

1. **Prove before delete:** diff suspected duplicate helper bodies and map all callers (including dynamic/runtime registration).
2. **Characterize behavior:** add/identify tests for route responses, identity resolution, feed lifecycle, and persistence transactions before refactors.
3. **Start with low-risk, high-clarity wins:** remove global echarts preload, lazy-load routes, reuse F&O coverage plan computation (performance wins with limited semantic risk).
4. **Defer protected/high-risk simplifications:** identity consolidation, persistence facade refactor, auth/feed compatibility removal require separate approved tasks and strong regression coverage.
5. **Never remove compatibility paths** without confirming no supported data/config/client migration depends on them.