# MarketHub — Prioritized Optimization Roadmap (2026-11-14)

**Date:** 2026-11-14  
**Branch / HEAD:** `develop` @ `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Basis:** Read-only audit (`docs/performance/2026-11-14-comprehensive-codebase-audit.md`), prior live inspection (`docs/performance/2026-11-13-webui-live-performance-inspection.md`), static tracing of current code/build.  
**Constraint:** No implementation in this audit. Preserve functionality, canonical market semantics/ordering, auth/startup/broker lifecycle, FYERS TBT behavior, and protected boundaries.

## Ranking method

Ranked by **expected performance improvement relative to implementation risk**, with evidence quality noted. Latency figures marked "prior sample" come from 2026-11-13 live inspection and must be rerun before/after implementation.

---

## P0 — Critical performance bottlenecks

### P0-1. `/api/market/quotes` snapshot timeout / severe stall risk
- **Exact files/symbols:** `api/routes.py` (`build_market_routes`, `_market_quotes`, `_quotes_snapshot`, `_snapshot/_snapshot_lock/_SNAPSHOT_TTL_S`, `_json_maybe_gzip`), `app/quote_service.py` (`QuoteService.quotes`, `_quotes`, `canonical_id_for`, `storage_keys`, `select_best_quote`, `_payload`), `market/quote_coordinator.py` (`canonical_quotes`, `select_canonical_quotes`), `market/service.py` (`MarketService.quotes`), `market/serialization.py`.
- **Evidence/measurement:** **[V prior]** `/api/market/quotes` timed out ~20,063 ms (2026-11-13). **[V]** handler rebuilds canonical-selected snapshot on cache miss by grouping/selecting over full quote set and serializing payload; 1s TTL + single-flight lock reduces burst duplication but first/contended miss pays full cost.
- **Root cause (hypothesis):** Full-universe canonical resolution/selection + payload construction/serialization cost, potentially amplified by catalog/identity lookups and large instrument universes; server contention also possible. **[U]** CPU/query/profile attribution not captured.
- **Recommended solution:** Profile request path (wall time breakdown: market snapshot read, canonical grouping, selection, catalog lookups, serialization/gzip). Add targeted fixes only where measured: precomputed/cached canonical view with safe invalidation, incremental updates, reduce repeated catalog resolution, limit/shape payload if contract allows, or move heavy work off request path while preserving canonical semantics. Keep 1s single-flight behavior unless replaced by better equivalent.
- **Expected benefit:** High — unblocks dashboard snapshot and reduces severe stall/timeout risk.
- **Functional risks:** High if canonical selection/freshness/identity semantics change; snapshot consistency with SSE/reset must be preserved.
- **Tests/benchmarks required:** Quote/canonical parity tests, market snapshot/SSE contract tests, load test for concurrent `/api/market/quotes` + `/api/market/stream`, before/after latency (median/p95/timeout rate).
- **Estimated complexity:** High.
- **Protected boundary involved:** **Yes (canonical market semantics/read models)**; implementation must be separately scoped and carefully reviewed.

### P0-2. `/api/alerts` slow response (~5.9s prior for small payload)
- **Exact files/symbols:** `api/product_routes.py` (`build_alert_routes`, `_list`), `core/persistence/store.py` (`EventStore.list_market_alerts`), `core/persistence/modules/products.py` (`list_alerts`), alert engine `recent_notifications(10)` (engine implementation).
- **Evidence/measurement:** **[V prior]** `/api/alerts` ~5,883 ms, 1,391 B. **[V]** endpoint does `asyncio.to_thread(store.list_market_alerts)` returning full alert list plus recent notifications; no pagination/cache shown.
- **Root cause (hypothesis):** DB/query cost, table size/indexing, serialization, or contention; small payload suggests compute/IO/query issue rather than transfer. **[U]** query plan and DB timing not captured.
- **Recommended solution:** Measure DB query time/plan and row counts; add appropriate index/pagination/projection if contract-compatible; cache short-lived read model if safe; avoid returning unnecessary fields/history.
- **Expected benefit:** High for alerts page and any polling consumers.
- **Functional risks:** Medium (response shape/filtering/pagination compatibility).
- **Tests/benchmarks required:** Alert API contract tests, DB query benchmark, endpoint latency before/after.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** Persistence read path (careful with schema/index changes; schema is protected).

### P0-3. `/api/news` slow response (~9.0s prior)
- **Exact files/symbols:** `api/news_routes.py` (`_get_news`, `_parse_news_filters`, `_article_to_dict`), `news/service.py` (`NewsService.news`, `_resolve_sources`, `_apply_filters`, `_dedup_items`, `_row_to_item`), `core/persistence/modules/news.py` (`query_news_items`, indexes), `core/persistence/store.py`.
- **Evidence/measurement:** **[V prior]** `/api/news` ~9,001 ms, 37 KB. **[V]** over-fetches `max(limit*5, limit+50)`, maps rows, applies regex keyword/symbol/age filters in Python, sorts, dedups, serializes; DB query + app-side processing per request.
- **Root cause (hypothesis):** Over-fetch + post-filtering/sorting/dedup over large result sets, regex cost, and/or DB query/index inefficiency. **[U]** row counts/query plan not captured.
- **Recommended solution:** Profile DB vs Python time; push filters to SQL where safe, reduce over-fetch with better query strategy, precompute/store searchable fields if needed, cache recent query results, and paginate/limit payload.
- **Expected benefit:** High for news/sentiment experiences.
- **Functional risks:** Medium (filter semantics, ordering, dedup behavior must remain consistent).
- **Tests/benchmarks required:** News API/filter tests, sentiment/news correctness tests, latency benchmark with representative DB size.
- **Estimated complexity:** Medium–High.
- **Protected boundary involved:** Persistence/query behavior (schema/index changes protected).

### P0-4. `/api/fno/coverage` and `/api/fno/catalog` recompute heavy routing plan (~3.7s/~3.9s prior)
- **Exact files/symbols:** `api/fno_coverage_routes.py` (`_coverage`, `_catalog`, `_plan_payload`), `app/fno_coverage.py` (`FnoCoverageService.plan`, `_universe_symbols`, `_equity_spot_map`, `_route_spot`, `_route_one`, `RoutingPlan.to_dict`), catalog/registry helpers used by plan.
- **Evidence/measurement:** **[V prior]** `/api/fno/coverage` ~3,697 ms (160 KB), `/api/fno/catalog` ~3,934 ms (378 B). **[V]** both endpoints compute full `coverage.plan()` per request (catalog does extra metadata reads too); plan scans/routes full universe and serializes large contract/uncovered structures for coverage endpoint.
- **Root cause (hypothesis):** Full plan recomputation on every request, expensive catalog/routing loops, and large serialization for coverage payload. **[U]** internal timing breakdown not captured.
- **Recommended solution:** Add request-level short TTL cache/single-flight for plan (keyed by relevant inputs/epoch), let `/api/fno/catalog` reuse cached/summary plan data instead of recomputing full detail, and profile hotspots inside `plan()`.
- **Expected benefit:** High, especially for repeated UI polling/navigation.
- **Functional risks:** Medium (stale coverage view if cached too long; must reflect apply/release/config changes correctly).
- **Tests/benchmarks required:** F&O coverage/catalog API tests, invalidation tests on apply/release/expiry/config changes, latency benchmark.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** No direct protected-core edit required if limited to API/service read path; verify no broker/auth/startup coupling.

### P0-5. `/api/futures/heatmap` slow (~3.4s prior)
- **Exact files/symbols:** `api/futures_heatmap.py` (`_heatmap`, `_inputs`, `_quote_reader`, `_previous_oi_reader`), `app/fno_coverage.py` (`FnoCoverageService.plan`), `market/futures_board.py` (`get_futures_board`, `compute_futures_board`, `_build_tile`, `filter_board`, cache/single-flight), `core/persistence/store.py` (`get_previous_oi`).
- **Evidence/measurement:** **[V prior]** `/api/futures/heatmap` ~3,408 ms (166 KB). **[V]** route computes uncached coverage plan each request, then builds/reads board snapshot (20s cache) with per-contract quote/possible previous-OI reads and serializes large payload.
- **Root cause (hypothesis):** Repeated `coverage.plan()` dominates; board build/previous-OI lookups and payload size add cost. **[U]** breakdown not captured.
- **Recommended solution:** Reuse cached/single-flight coverage plan (shared with P0-4), profile previous-OI read amplification, and reduce payload/compute where contract-compatible.
- **Expected benefit:** High.
- **Functional risks:** Medium (freshness/correctness of coverage, OI basis, buildup semantics must be preserved).
- **Tests/benchmarks required:** Futures heatmap API tests, OI/buildup correctness tests, latency benchmark.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** Canonical/analytics semantics must be preserved; persistence reads only.

---

## P1 — High-impact optimizations

### P1-1. Remove global initial frontend payload cost (echarts preload + eager routes)
- **Exact files/symbols:** `frontend/dist/index.html`, `frontend/src/routes/router.tsx`, `frontend/vite.config.ts` (`manualChunks`), heavy feature entrypoints under `frontend/src/features/*`.
- **Evidence/measurement:** **[V]** initial HTML modulepreloads `echarts` (~1.12 MB uncompressed) on every load; only chart route lazy; most views eagerly imported; initial JS referenced by HTML ~1.66 MiB uncompressed; no dist compression artifacts.
- **Root cause:** Global vendor preload/chunking and lack of route-level code splitting for non-chart pages.
- **Recommended solution:** Stop preloading echarts on non-chart routes, lazy-load heavy routes/features (F&O, maps/heatmaps, scanners, news/settings subviews as appropriate), and adjust chunking so dashboard shell loads minimal JS/CSS.
- **Expected benefit:** High improvement in cold/warm initial load and parse/execute time, especially on low-end CPUs.
- **Functional risks:** Medium (suspense/loading states, chunk load failures, route regressions).
- **Tests/benchmarks required:** `bun run build` chunk report, route smoke/e2e, browser network/Performance traces before/after.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** No (WEBUI only).

### P1-2. Reduce dashboard startup request/SSE fan-out
- **Exact files/symbols:** `frontend/src/features/dashboard/DashboardView.tsx`, `useMarketQuotes.ts`, `api.ts`, `components/{IndicesWidget,SectorsWidget,BreadthWidget,FnoWidget,MoversWidget,TickerStrip}.tsx`, `frontend/src/streams/useStream.ts`, `SSEManager.ts`.
- **Evidence/measurement:** **[V]** dashboard mounts snapshot + market SSE + subscriptions query + 4 polling widgets (8s/8s/15s/15s). **[V prior]** depends on slow `/api/market/quotes` and moderately slow breadth/FNO endpoints.
- **Root cause:** Eager concurrent data loading for all widgets regardless of visibility/priority.
- **Recommended solution:** Prioritize shell/ticker/critical data, defer/sequence secondary widgets, add visibility-aware polling (`refetchIntervalInBackground` review), dedupe/shared queries, and render skeletons immediately.
- **Expected benefit:** High perceived dashboard responsiveness and lower backend burst load.
- **Functional risks:** Medium (staler secondary widgets, loading-state UX changes).
- **Tests/benchmarks required:** Dashboard component/e2e tests, network waterfall comparison, interaction latency traces.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** No (WEBUI; backend endpoint fixes separate).

### P1-3. Market SSE/logs live-update efficiency (profile-driven)
- **Exact files/symbols:** `api/routes.py` (`_market_stream`), `app/server.py` (`_on_market_quote_update`), `core/sse_broker.py`, `frontend/src/features/dashboard/useMarketQuotes.ts`, `frontend/src/features/logs/useLogs.ts`, `frontend/src/streams/SSEManager.ts`.
- **Evidence/measurement:** **[V]** per-tick canonical re-selection/re-encode on market SSE; per changed quote serialize+broadcast+sequential alert evaluation; frontend 1s flush with array/map rebuild; logs raw EventSource with per-message prepend/slice. **[U]** tick/message rates and render cost.
- **Root cause:** Correctness-first per-event processing and unbounded-feeling UI update patterns.
- **Recommended solution:** Measure tick/message rates and client render cost first; then batch/coalesce where semantics allow, unify logs stream ownership if beneficial, cap/buffer updates, and avoid unnecessary allocations in hot paths.
- **Expected benefit:** Medium–High under active market/log load.
- **Functional risks:** High for backend stream semantics (canonical/reset/freshness/ordering) and medium for UI freshness.
- **Tests/benchmarks required:** SSE contract tests, alert/quote correctness tests, browser Performance/Memory traces, message-rate benchmarks.
- **Estimated complexity:** High.
- **Protected boundary involved:** **Potentially yes** (canonical market semantics, alert evaluation ordering behavior).

### P1-4. Moderate-latency analytics endpoints (`/api/market/breadth`, `/api/options/chain/view`, variable `/api/market/fno/universe`)
- **Exact files/symbols:** `api/routes.py` (`_breadth`), `market/breadth.py`, `market/market_universe.py`; `api/product_routes.py` (`_intel_chain`), `app/market_intel.py`; `api/routes.py`/FNO universe handlers and `app/fno_coverage.py`/catalog paths as applicable.
- **Evidence/measurement:** **[V prior]** breadth ~682ms, option chain view ~620ms, FNO universe variable 225ms–3.7s. **[V]** linear per-member/per-contract computation and catalog/quote enrichment paths.
- **Root cause:** Per-request recomputation over universe/window without endpoint-level reuse where safe.
- **Recommended solution:** Profile and add short TTL/single-flight caches keyed by universe/expiry/window/epoch; reduce duplicate catalog/quote lookups.
- **Expected benefit:** Medium.
- **Functional risks:** Medium (staleness/invalidation correctness).
- **Tests/benchmarks required:** Endpoint correctness tests, cache invalidation tests, latency benchmarks.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** Canonical/analytics semantics must be preserved.

---

## P2 — Code simplification (proven/low-regret first)

### P2-1. Route helper duplication/consolidation (`api/routes.py`, `api/product_routes.py`)
- **Exact files/symbols:** repeated helper names/patterns including `_json`, `_fail`, `_save_feed`, `_delete_feed`, `_feed_status`, `_forget_session` (body-level equivalence to be proven).
- **Evidence/measurement:** **[V]** name/pattern duplication in large coupled route modules; architecture doc flags coupling.
- **Root cause:** Organic growth in mixed-responsibility route files.
- **Recommended solution:** Diff helper bodies, extract only identical/semantically equivalent helpers into shared module with unchanged response contracts; avoid touching auth/settings behavior.
- **Expected benefit:** Medium maintainability, lower drift/bug risk.
- **Functional risks:** Medium (subtle response/auth/feed behavior changes).
- **Tests/benchmarks required:** Route/API tests for affected endpoints, auth/settings/feed smoke tests.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** **Yes if auth/settings/feed surfaces touched** (red-zone/shared files).

### P2-2. Identity compatibility layers (`app/market_identity.py`, overlap with `app/canonical_identity.py`, `app/instrument_identity.py`)
- **Exact files/symbols:** `app/market_identity.py` (`MarketInstrumentIdentityResolver`, re-exports), `app/canonical_identity.py`, `app/instrument_identity.py` (`InstrumentIdentityRegistry`).
- **Evidence/measurement:** **[V]** `market_identity` is active compatibility shim used by server/tests; **[S]** conceptual overlap across three identity modules with different targets/roles.
- **Root cause:** Historical evolution and separate canonical vs storage-key lookup needs.
- **Recommended solution:** Design consolidation/migration plan only after full caller mapping; preserve canonical derivation, storage-key parity, and collision semantics.
- **Expected benefit:** Medium maintainability/clarity.
- **Functional risks:** High (quote resolution, condition alerts, provider parity).
- **Tests/benchmarks required:** `test/test_identity.py`, `test/test_canonical_identity.py`, `test/test_market_identity_resolver.py`, quote parity/condition-alert/subscription tests.
- **Estimated complexity:** High.
- **Protected boundary involved:** **Potentially yes** (canonical market identity/alert behavior).

### P2-3. Persistence facade/internal duplication (`core/persistence/store.py`)
- **Exact files/symbols:** `core/persistence/store.py` (`EventStore` pass-through methods), `core/persistence/modules/*`.
- **Evidence/measurement:** **[V]** large repetitive facade over module APIs.
- **Root cause:** Centralized stable API over evolving schema/modules.
- **Recommended solution:** Internal helper refactors only, preserving public API/transaction boundaries; no schema/behavior changes.
- **Expected benefit:** Medium maintainability.
- **Functional risks:** High if transactions/migrations/replay semantics affected.
- **Tests/benchmarks required:** Persistence, replay/delivery, migration, product/news/bar/alert tests.
- **Estimated complexity:** High.
- **Protected boundary involved:** **Yes** (persistence/schema/store).

### P2-4. Compatibility/legacy branch inventory and retirement plan
- **Exact files/symbols:** examples in `app/server.py`, `app/secrets_store.py`, `app/subscriptions/*`, `app/auth/upstox_session.py`, `core/persistence/modules/*`, `brokers/*/feed*.py` (legacy/backward/compat paths).
- **Evidence/measurement:** **[V]** many compatibility branches exist; **[S]** retirement value depends on migration/data preconditions.
- **Root cause:** Backward compatibility across schema/config/protocol evolution.
- **Recommended solution:** Inventory each branch with trigger conditions and telemetry/tests; remove only when unsupported.
- **Expected benefit:** Low–Medium maintainability/perf (case-dependent).
- **Functional risks:** Medium–Very High depending on path.
- **Tests/benchmarks required:** Migration/restore/auth/persistence/feed regression suites per branch.
- **Estimated complexity:** Medium–High (cumulative).
- **Protected boundary involved:** **Often yes**.

---

## P3 — Low-impact cleanup

### P3-1. Frontend list virtualization and render micro-optimizations (measured hotspots only)
- **Exact files/symbols:** large table/list views (`features/logs`, `features/ai-alerts`, `features/scanners`, `features/fno`, `features/watchlists`, `features/instruments`, `features/market-map`), `components/EChart.tsx` consumers.
- **Evidence/measurement:** **[V]** no virtualization detected; **[U]** runtime jank not profiled.
- **Root cause:** Simple rendering adequate for small datasets but risky for large ones.
- **Recommended solution:** Add virtualization/windowing and targeted memoization only for profiled hotspots.
- **Expected benefit:** Low–Medium (workload dependent).
- **Functional risks:** Low–Medium (UI behavior/scroll/accessibility).
- **Tests/benchmarks required:** UI interaction tests, browser traces with large datasets.
- **Estimated complexity:** Medium.
- **Protected boundary involved:** No.

### P3-2. Dist/build hygiene and observability
- **Exact files/symbols:** `frontend/vite.config.ts`, `frontend/dist/*`, server static/compression behavior (if applicable).
- **Evidence/measurement:** **[V]** sourcemaps large in dist; no precompressed assets in dist folder.
- **Root cause:** Build/output configuration choices.
- **Recommended solution:** Confirm runtime compression, avoid shipping unnecessary sourcemaps to production artifacts, add bundle/perf budgets in CI.
- **Expected benefit:** Low–Medium (deploy/transfer clarity).
- **Functional risks:** Low.
- **Tests/benchmarks required:** Build verification, deployment smoke.
- **Estimated complexity:** Low.
- **Protected boundary involved:** No.

### P3-3. Dependency and dead-surface verification passes
- **Exact files/symbols:** manifests (`pyproject.toml`, `requirements.txt`, `frontend/package.json`) and candidate modules from bloat inventory.
- **Evidence/measurement:** **[V]** no confirmed unused dependency from static review; several "maybe unused" modules disproven.
- **Root cause:** Normal codebase evolution.
- **Recommended solution:** Periodic import/usage/bundle analysis with runtime-registration awareness before removals.
- **Expected benefit:** Low.
- **Functional risks:** Medium if removals are guessed.
- **Tests/benchmarks required:** Full relevant test suites/builds.
- **Estimated complexity:** Low–Medium.
- **Protected boundary involved:** Depends on target.

---

## Recommended execution order (first implementation wave, after approval)

1. **Measure & profile (prerequisite):** rerun route/API baseline, capture browser traces, profile `/api/market/quotes`, `/api/alerts`, `/api/news`, `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap`.
2. **P0-4/P0-5:** shared F&O coverage plan cache/single-flight + `/api/fno/catalog` reuse (high value, bounded scope).
3. **P0-2/P0-3:** alerts/news query-path fixes based on measured DB/app hotspots.
4. **P0-1:** market quotes snapshot/stream optimization with strict canonical parity tests.
5. **P1-1/P1-2:** frontend initial-load and dashboard fan-out reductions.
6. **P1-3/P1-4:** profile-driven SSE/analytics refinements.
7. **P2/P3:** simplification and cleanup with characterization tests.

**Do not touch:** `app/auth/**`, credentials/session persistence, startup restoration, broker login/feed lifecycle, canonical market semantics, production DB data/schema, `config.json`, FYERS TBT functionality.