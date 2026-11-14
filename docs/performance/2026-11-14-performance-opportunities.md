# MarketHub — Performance Opportunities (2026-11-14)

**Date:** 2026-11-14  
**Branch / HEAD:** `develop` @ `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Scope:** Read-only audit synthesis. Opportunities are prioritized by **expected impact vs implementation risk**. Measurements marked "prior sample" are from 2026-11-13 and must be rerun.

## Summary table

| ID | Opportunity | Area | Evidence | Expected impact | Risk | Complexity | Protected boundary | Priority |
|---|---|---|---|---|---|---|---|---|
| O-01 | Profile and fix `/api/market/quotes` snapshot/selection path | Backend/API, canonical read model | **[V prior]** timeout ~20s; **[V]** full canonical selection + serialization in `api/routes.py`, `app/quote_service.py` | Very high | High | High | Yes (canonical semantics) | P0 |
| O-02 | Cache/single-flight F&O coverage plan; reuse for `/api/fno/catalog` | Backend/API | **[V prior]** coverage ~3.7s, catalog ~3.9s; **[V]** both recompute full `FnoCoverageService.plan()` | Very high | Medium | Medium | No (if read-path only) | P0 |
| O-03 | Optimize `/api/news` query/filter pipeline | Backend/API, DB | **[V prior]** ~9.0s; **[V]** over-fetch + Python filter/sort/dedup in `news/service.py` | High | Medium | Medium–High | Persistence/query care | P0 |
| O-04 | Optimize `/api/alerts` list path | Backend/API, DB | **[V prior]** ~5.9s for 1.4 KB; **[V]** full DB list + notifications | High | Medium | Medium | Persistence/query care | P0 |
| O-05 | Reuse coverage plan/caching for `/api/futures/heatmap` | Backend/API | **[V prior]** ~3.4s; **[V]** uncached plan + board build/previous-OI reads | High | Medium | Medium | Analytics semantics preserved | P0 |
| O-06 | Remove global `echarts` preload from initial HTML | Frontend build/load | **[V]** `index.html` modulepreloads 1.12 MiB echarts on all routes | High | Low–Medium | Low–Medium | No | P1 |
| O-07 | Lazy-load non-chart routes/features | Frontend routing/bundle | **[V]** router eagerly imports almost all views; initial JS ~1.66 MiB referenced | High | Medium | Medium | No | P1 |
| O-08 | Reduce dashboard startup fan-out and defer secondary widgets | Frontend + API load | **[V]** snapshot+SSE+subscriptions+4 polling widgets (8/8/15/15s) | High (perceived) | Medium | Medium | No | P1 |
| O-09 | Profile-driven market SSE/logs update batching | Frontend/Backend realtime | **[V]** per-tick canonical reselect/re-encode, 1s quote flush allocations, logs raw EventSource per-message updates; **[U]** rates | Medium–High | High/Medium | High | Possible canonical/alert semantics | P1 |
| O-10 | Add short TTL/single-flight for breadth/option-chain/FNO universe | Backend/API | **[V prior]** breadth ~682ms, chain ~620ms, FNO universe variable; **[V]** per-request recompute | Medium | Medium | Medium | Analytics semantics preserved | P1 |
| O-11 | Consolidate proven route helper duplication | Backend maintainability | **[V/S]** repeated helper names in `api/routes.py`, `api/product_routes.py`; body equivalence unproven | Medium (maintainability) | Medium | Medium | Possible shared/auth/feed surfaces | P2 |
| O-12 | Identity layer simplification design (`market_identity` shim/overlaps) | Architecture/maintainability | **[V]** active shim; **[S]** overlap across identity modules | Medium (maintainability) | High | High | Possible canonical identity/alerts | P2 |
| O-13 | Virtualize large tables/lists in profiled hotspots | Frontend rendering | **[V]** no virtualization detected; large logs/events/tables/chains possible | Low–Medium | Low–Medium | Medium | No | P3 |
| O-14 | Build/runtime hygiene (compression, sourcemap policy, perf budgets) | Delivery/observability | **[V]** no precompressed dist assets; large sourcemaps in dist | Low–Medium | Low | Low | No | P3 |

## Detailed opportunities

### O-01 — `/api/market/quotes` snapshot/selection optimization (P0, very high impact / high risk)
- **Files/symbols:** `api/routes.py` (`_market_quotes`, `_quotes_snapshot`, `_market_stream`), `app/quote_service.py` (`QuoteService.quotes/quote`, canonical resolution, selection, payload), `market/quote_coordinator.py`, `market/service.py`.
- **Evidence:** Prior timeout ~20s; full-universe grouping/canonical resolution/selection/serialization on miss; per-tick canonical re-selection in SSE.
- **Action:** Profile breakdown, reduce repeated resolution/allocation, consider safe cached canonical read model/invalidation, preserve snapshot/SSE/reset/canonical parity.
- **Measure:** median/p95/timeout rate, concurrent request behavior, CPU/allocation profile.

### O-02 — F&O coverage plan cache/single-flight (P0, very high impact / medium risk)
- **Files/symbols:** `api/fno_coverage_routes.py` (`_coverage`, `_catalog`, `_plan_payload`), `app/fno_coverage.py` (`FnoCoverageService.plan`, `RoutingPlan.to_dict`).
- **Evidence:** Coverage ~3.7s, catalog ~3.9s despite tiny payload; both recompute full plan.
- **Action:** Short TTL cache + single-flight keyed by expiry/config/catalog epoch/relevant inputs; return summary from cached plan for catalog endpoint; invalidate on apply/release/config changes.
- **Measure:** endpoint latency, recomputation count, correctness after mutations.

### O-03 — News query/filter pipeline optimization (P0, high impact / medium risk)
- **Files/symbols:** `api/news_routes.py`, `news/service.py` (`news`, `_apply_filters`, `_dedup_items`), `core/persistence/modules/news.py` (`query_news_items`).
- **Evidence:** ~9.0s prior; over-fetch `limit*5+50`, Python regex filtering/sort/dedup.
- **Action:** Profile DB vs app time, push filters to SQL where safe, reduce over-fetch, cache common queries, optimize indexes only with evidence and schema care.
- **Measure:** latency by filter type, row counts scanned/returned, CPU time.

### O-04 — Alerts list optimization (P0, high impact / medium risk)
- **Files/symbols:** `api/product_routes.py` (`build_alert_routes._list`), `core/persistence/store.py` (`list_market_alerts`), `core/persistence/modules/products.py` (`list_alerts`).
- **Evidence:** ~5.9s for 1.4 KB payload.
- **Action:** Inspect query plan/row count/indexes, paginate/project fields if compatible, short cache if safe.
- **Measure:** DB query time, endpoint median/p95.

### O-05 — Futures heatmap plan reuse and board efficiency (P0, high impact / medium risk)
- **Files/symbols:** `api/futures_heatmap.py`, `app/fno_coverage.py`, `market/futures_board.py`, `core/persistence/store.py` (`get_previous_oi`).
- **Evidence:** ~3.4s prior; uncached coverage plan per request plus board build/previous-OI reads.
- **Action:** Share cached plan with O-02, profile previous-OI amplification and payload serialization.
- **Measure:** latency breakdown, DB read count, payload size.

### O-06 — Remove global echarts preload (P1, high impact / low-medium risk)
- **Files/symbols:** `frontend/dist/index.html` (build output), `frontend/vite.config.ts`, chart-related imports/chunking.
- **Evidence:** 1.12 MiB echarts chunk modulepreloaded on every initial load.
- **Action:** Ensure echarts loads only for routes/components that need it; adjust chunk/preload strategy.
- **Measure:** initial JS transfer/parse, FCP/LCP on dashboard/non-chart routes.

### O-07 — Lazy-load heavy routes/features (P1, high impact / medium risk)
- **Files/symbols:** `frontend/src/routes/router.tsx`, feature entrypoints (`fno`, `market-map`, `sector-heatmap`, `futures-heatmap`, `scanners`, `news`, `settings`, etc.).
- **Evidence:** only chart lazy; most views eagerly imported.
- **Action:** Route-level `lazy()`/Suspense boundaries with good fallbacks; keep dashboard shell minimal.
- **Measure:** initial chunk size, route transition times, build report.

### O-08 — Dashboard startup fan-out reduction (P1, high impact / medium risk)
- **Files/symbols:** `frontend/src/features/dashboard/*`, `useMarketQuotes.ts`, widget components.
- **Evidence:** snapshot+SSE+subscriptions+4 polling widgets on mount; depends on slow APIs.
- **Action:** Prioritize critical content, defer/sequence widgets, visibility-aware polling, shared queries, skeletons.
- **Measure:** first 30s request count/waterfall, time to first useful content.

### O-09 — Realtime update batching/coalescing (P1, medium-high impact / high-medium risk)
- **Files/symbols:** `api/routes.py` (`_market_stream`), `app/server.py` (`_on_market_quote_update`), `core/sse_broker.py`, `frontend/src/features/dashboard/useMarketQuotes.ts`, `frontend/src/features/logs/useLogs.ts`.
- **Evidence:** per-tick canonical reselect/re-encode and alert evaluation; frontend per-flush allocations; logs per-message array churn. Rates unmeasured.
- **Action:** Profile first; batch/coalesce only with semantic safety; unify logs stream ownership if beneficial.
- **Measure:** msgs/s, CPU, long tasks, dropped updates, correctness.

### O-10 — Cache moderate analytics endpoints (P1, medium impact / medium risk)
- **Files/symbols:** `market/breadth.py`, `app/market_intel.py`, FNO universe/catalog paths, route handlers.
- **Evidence:** breadth ~682ms, option chain ~620ms, FNO universe variable.
- **Action:** Short TTL/single-flight keyed by inputs and catalog/market epoch; invalidate correctly.
- **Measure:** p95 latency, cache hit rate, freshness correctness.

### O-11 — Route helper consolidation (P2, medium maintainability / medium risk)
- **Files/symbols:** `api/routes.py`, `api/product_routes.py`.
- **Evidence:** repeated helper names/patterns; body equivalence unproven.
- **Action:** Diff and extract only identical helpers with unchanged contracts.
- **Measure:** code diff size, test pass rate, no response-shape changes.

### O-12 — Identity layer simplification (P2, medium maintainability / high risk)
- **Files/symbols:** `app/market_identity.py`, `app/canonical_identity.py`, `app/instrument_identity.py`.
- **Evidence:** active compatibility shim and overlapping responsibilities.
- **Action:** Separate design/migration task with full caller mapping and parity tests.
- **Measure:** parity tests, no canonical/storage-key regressions.

### O-13 — Virtualize large lists/tables (P3, low-medium impact / low-medium risk)
- **Files/symbols:** logs, AI alerts events/consumers, scanners, F&O tables, watchlists/instruments, market map tiles as needed.
- **Evidence:** no virtualization detected; impact depends on dataset size.
- **Action:** Apply only to profiled hotspots.
- **Measure:** scroll FPS/long tasks with large datasets.

### O-14 — Build/runtime hygiene (P3, low-medium impact / low risk)
- **Files/symbols:** `frontend/vite.config.ts`, dist artifacts, deployment/static serving config.
- **Evidence:** no precompressed dist assets; large sourcemaps in dist.
- **Action:** Verify/enable compression, manage sourcemap publication, add bundle/perf budgets.
- **Measure:** transfer bytes, CI budget checks.

## Expected measurable outcomes (if top opportunities implemented correctly)

| Outcome | Target (to validate by measurement) |
|---|---|
| `/api/market/quotes` | No 20s timeouts; p95 reduced to sub-second or low-seconds depending on universe/profile |
| `/api/alerts`, `/api/news` | p95 reduced from multi-second prior samples to sub-second/low-second range |
| `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap` | p95 reduced materially via plan reuse/caching (target <1s warm cache where feasible) |
| Dashboard initial load | Meaningful reduction in initial JS transfer/parse and first useful content time after removing echarts preload + lazy routes + fan-out reduction |
| Route navigation | Warm transitions near-instant for lightweight routes; heavy routes load on demand with clear feedback |
| Realtime UI | Reduced long tasks/render churn under active ticks/logs (only if profiled and optimized) |

**Caveat:** Targets are hypotheses based on current evidence; actual gains depend on measured bottlenecks, data volume, and safe implementation within protected boundaries.