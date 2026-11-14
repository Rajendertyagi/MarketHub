# MarketHub — Consolidated Performance & Code Simplification Master Plan

> **Canonical single source of truth.** This file is the authoritative, continuously maintained record for MarketHub performance and code-simplification work. Update this plan (do not create competing plans). Supporting audit/reference docs remain useful, but implementation sequence, task tracking, decisions, evidence, and progress live here.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **No implementation in a planning-only assignment unless explicitly authorized by the user.**

**Last updated:** 2026-11-14  
**Branch:** `develop`  
**Verified local HEAD at consolidation:** `d0a8b0b4fb94855738603cdfeffd16291b3fdbb8` (`docs(perf): add 2026-11-14 comprehensive codebase audit and roadmap`)  
**Verified remote `origin/develop` at consolidation:** `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Publication state (investigated 2026-11-14):** Audit docs commit `d0a8b0b` exists **locally on `develop`** and is **ahead 1** of `origin/develop`; it is **not published** to remote at time of investigation. It contains only the six 2026-11-14 audit docs. No force-push/rewrite performed. This consolidation commit must be published safely on `develop` (pushing `develop` will also publish `d0a8b0b` if still ahead; verify remote state before push).

## 1. Objective

Turn MarketHub into a lean, fast, resource-efficient application without losing existing functionality.

1. **Performance:** improve WebUI page loading/navigation, API latency, database access, realtime processing, CPU and RAM usage.
2. **Simplification:** remove proven dead code, duplicate implementations, unnecessary abstractions, redundant dependencies, and inefficient custom solutions — only with evidence and regression safety.

**Do not start implementation until the user reviews and authorizes this updated master plan.**

## 2. Planning assignment rule (this session)

- **No application code or configuration changes.**
- **No `config.json`, credentials, DB/schema/data, build output, or user-generated file changes.**
- **No broad git staging.** Stage/commit only exact intended documentation file paths.
- **No destructive git commands, no force-push, no history rewrite.**
- This session's deliverable is the consolidated master plan and its safe publication on `develop`.

## 3. Source documents consolidated

| Document | Role | Status |
|---|---|---|
| `docs/performance/2026-11-14-comprehensive-codebase-audit.md` | Full audit summary, validation, boundaries, limitations | **[V] Local commit `d0a8b0b`, unpublished to origin at consolidation** |
| `docs/performance/2026-11-14-prioritized-optimization-roadmap.md` | P0–P3 roadmap with evidence/risks/tests | **[V] Local commit `d0a8b0b`, unpublished** |
| `docs/performance/2026-11-14-route-api-performance-baseline.md` | Historical samples + static baseline + rerun protocol | **[V] Local commit `d0a8b0b`, unpublished** |
| `docs/performance/2026-11-14-code-bloat-inventory.md` | Bloat/duplication/dead-surface inventory | **[V] Local commit `d0a8b0b`, unpublished** |
| `docs/performance/2026-11-14-dependency-bundle-report.md` | Dependency and bundle-size report | **[V] Local commit `d0a8b0b`, unpublished** |
| `docs/performance/2026-11-14-performance-opportunities.md` | Opportunity list with impact/risk | **[V] Local commit `d0a8b0b`, unpublished** |
| `docs/performance/2026-11-10-webui-system-performance-audit.md` | Prior webui/system audit (suspects, gaps) | **[V] Present in repo (untracked in working tree at consolidation; preserve)** |
| `docs/performance/2026-11-13-webui-live-performance-inspection.md` | Live HTTP/build inspection evidence | **[V] Present in repo (untracked in working tree at consolidation; preserve)** |

## 4. Evidence standard and publication verification

- **[V] Confirmed:** directly observed in current code/build/config or cited measurements/artifacts.
- **[S] Strongly suspected:** plausible from tracing/structure, not profiled/measured in controlled reproduction.
- **[U] Not yet measured:** requires runtime profiling/traces/benchmarks.

**Publication discrepancy (resolved by investigation):** prior report claimed `d0a8b0b` created on develop; remote `origin/develop` was `08b3071...` and remains so at consolidation. Investigation shows `d0a8b0b` is **local-only, unpublished, on `develop` (ahead 1)**, not on another branch and not overwritten. All existing work preserved.

## 5. Global constraints and protected boundaries

- **Protected/read-only unless separately approved task:** `app/auth/**`, credential/session persistence, startup restoration, broker login/feed lifecycle, canonical market semantics/identity/ordering, production DB data/schema, `config.json`.
- **FYERS TBT:** keep current functionality intact; pending real live verification. Add regression tests around any affected shared paths; do not change TBT behavior as routine optimization.
- **Boundary-aware fixes:** frontend in `frontend/src/**` (WEBUI zones); backend limited to approved transport/API or clearly scoped non-protected surfaces per task.
- **No speculative rewrites:** no blanket memoization, arbitrary polling intervals, unnecessary caches, library swaps, or large refactors without evidence.
- **Correctness/freshness preserved:** live quote correctness, canonical selection/reset behavior, alert/news/settings behavior, login/session flows, market-data semantics and ordering.
- **Git safety:** exact-file staging only; never stage `config.json`, credentials, DB files, build output, or untracked user-generated files; no destructive commands/force-push/history rewrite.
- **Verification baseline (implementation tasks):** `pytest test/test_architecture_boundaries.py` green for repo code changes; frontend `bun run typecheck`, `bun run build`, relevant tests/traces; ruff/frontend lint as applicable.

## 6. Consolidated findings (validated)

### 6.1 Confirmed performance bottlenecks
| ID | Finding | Evidence | Status |
|---|---|---|---|
| F-01 | Slow/stalling APIs: `/api/market/quotes` timeout ~20s, `/api/news` ~9.0s, `/api/alerts` ~5.9s, `/api/fno/coverage` ~3.7s, `/api/fno/catalog` ~3.9s, `/api/futures/heatmap` ~3.4s, `/api/market/breadth` ~682ms, `/api/options/chain/view` ~620ms (prior samples) | 2026-11-13 live inspection + static tracing (`api/routes.py`, `app/quote_service.py`, `news/service.py`, `api/product_routes.py`, `app/fno_coverage.py`, `api/fno_coverage_routes.py`, `api/futures_heatmap.py`, `market/futures_board.py`, `market/breadth.py`, `app/market_intel.py`) | **[V prior + V path]** |
| F-02 | F&O coverage plan recomputed per request: `/api/fno/coverage`, `/api/fno/catalog` (and futures heatmap path) call full `coverage.plan()` | `api/fno_coverage_routes.py`, `app/fno_coverage.py`, `api/futures_heatmap.py` | **[V]** |
| F-03 | Initial frontend payload heavy: `index.html` modulepreloads `echarts` (~1.12 MiB) globally; initial JS referenced ~1.66 MiB uncompressed; no precompressed dist assets | `frontend/dist/index.html`, `frontend/dist/assets/*`, `frontend/vite.config.ts` | **[V]** |
| F-04 | Eager route graph: almost all views statically imported; only `ChartView` lazy | `frontend/src/routes/router.tsx` | **[V]** |
| F-05 | Dashboard fan-out: snapshot + market SSE + subscriptions + 4 polling widgets (8s/8s/15s/15s) | `frontend/src/features/dashboard/*`, `useMarketQuotes.ts` | **[V]** |
| F-06 | Market quote SSE/snapshot path does canonical selection/serialization work per request/tick; stream re-selects per live line | `api/routes.py`, `app/quote_service.py`, `market/quote_coordinator.py`, `app/server.py` | **[V path, U profile]** |
| F-07 | News GET does DB over-fetch + Python filter/sort/dedup/serialization | `api/news_routes.py`, `news/service.py`, `core/persistence/modules/news.py` | **[V]** |
| F-08 | Alerts GET returns full DB list + recent notifications | `api/product_routes.py`, `core/persistence/store.py`, `core/persistence/modules/products.py` | **[V path, U query plan]** |
| F-09 | Logs use raw `EventSource` per mount with per-message prepend/slice; separate from shared SSE manager | `frontend/src/features/logs/useLogs.ts`, `frontend/src/streams/SSEManager.ts` | **[V]** |
| F-10 | `useMarketQuotes` flushes up to 1s with array/map rebuild; linear consumer work over quotes | `frontend/src/features/dashboard/useMarketQuotes.ts` | **[V mechanism, U impact]** |

### 6.2 Strongly suspected / unmeasured
| ID | Finding | Status |
|---|---|---|
| S-01 | Browser long tasks, JS parse/execute, GC/memory leaks, GPU/compositing issues | **[U]** |
| S-02 | SSE tick/message rates and realtime render/CPU amplification under load | **[U/S]** |
| S-03 | SQLite contention/query-plan inefficiencies and sustained Python CPU/memory hotspots | **[U/S]** |
| S-04 | Runtime `test_source` activity in live process (config currently disabled) | **[V config disabled, U runtime]** |
| S-05 | Body-level duplication of route helpers in `api/routes.py` vs `api/product_routes.py` | **[S]** (name/pattern confirmed) |

### 6.3 Code simplification findings (consolidated)
- **Confirmed complexity/duplication:** `app/market_identity.py` compatibility shim (active), `app/previous_oi.py` re-export surface, repeated route helper patterns, large `core/persistence/store.py` facade, eager route graph/global echarts preload, `/api/fno/catalog` recomputing full plan, logs parallel SSE implementation, many legacy/compat branches.
- **Suspected:** identity abstraction overlap (`canonical_identity`/`instrument_identity`/`market_identity`), broad `mcp_server/services.py` container, broker feed lifecycle extraction opportunities, generic helper-name collisions.
- **Not dead/unused:** `sources/test_source.py`, `app/previous_oi.py`, `app/fyers_index_source.py`, `app/fyers_runtime_auth.py`, `yfinance_service/*` + `yfinance`, `app/market_identity.py` (active shim).

## 7. Independent source-code review findings (retained, with criteria)

### A. Frontend initial loading
- **Verified:** `frontend/src/routes/router.tsx` statically imports almost every route; `ChartView` is primary lazy route. `frontend/src/components/EChart.tsx` imports full `echarts`. ECharts used by breadth, sector heatmap, F&O visualizations. Built HTML preloads ECharts due to static dependency graph.
- **Planned work:** lazy-load non-critical routes; keep initial shell small; load heavy features on demand; evaluate modular imports via `echarts/core` registering only required charts/components/renderer; measure initial JS transfer, parse/execute, route chunks.
- **Preserve:** chart functionality, theme changes, disposal, resizing, interactions, chart tests.
- **Acceptance:** measurable reduction in initial JS/parse on non-chart routes; no chart regressions.

### B. Market quote SSE processing (high-priority investigation)
- **Verified:** `api/routes.py`, `app/quote_service.py`, `market/quote_coordinator.py`, `market/service.py` — live market SSE path performs canonical selection per incoming event; quote service obtains/scans full quote collection for requested instrument.
- **Potential cost:** full collection scan per event, repeated canonical selection/serialization, multiplied by connected SSE clients.
- **Proposed direction:** reuse selected canonical quote from update path where possible; avoid full-universe scans for one-instrument updates; calculate canonical selection once and fan out consistent result.
- **Must preserve:** provider priority, identity, freshness, stale-data semantics, ordering, quote contract/canonical semantics (no contract/semantic change without dedicated parity tests).
- **Acceptance:** measured reduction in per-tick/SSE CPU/allocations with parity tests passing.

### C. News API blocking work
- **Verified:** `news/service.py`, `api/news_routes.py` — `NewsService.news()` is async but calls synchronous persistence queries and performs filtering/sorting/deduplication directly.
- **Investigate:** event-loop blocking and DB operation profiling.
- **Proposed direction:** separate blocking DB I/O from async request path; offload appropriately; keep expensive CPU processing out of event loop where measurements justify; preserve filtering, ordering, deduplication, response contracts.
- **Constraint:** do not automatically move everything to `asyncio.to_thread()`; understand thread safety and actual cost first.
- **Acceptance:** measured latency improvement and no event-loop blocking regression; response contract preserved.

### D. F&O coverage plan recomputation
- **Verified:** `app/fno_coverage.py`, `api/fno_coverage_routes.py`, `api/futures_heatmap.py` — multiple endpoints independently call `coverage.plan()` (catalog-derived futures, grouping, expiry selection, provider routing/capacity).
- **Proposed direction:** cache/reuse catalog-derived immutable portion; proper invalidation after catalog sync; recalculate dynamic provider availability/subscriptions/capacity when necessary; single-flight where appropriate; reuse same valid plan across related requests when inputs unchanged.
- **Constraint:** do not cache dynamic provider state indefinitely or return stale capacity information.
- **Acceptance:** reduced recomputation/latency with correct invalidation and capacity freshness.

### E. Dashboard request fan-out
- **Verified:** dashboard market snapshot, market SSE subscription, subscriptions-related requests, sector widget 8s, breadth 8s, F&O widget 15s, movers 15s.
- **Investigate:** shared React Query keys, in-flight deduplication, initial vs secondary content, visibility-aware polling, route-active polling, appropriate `staleTime`/`refetchInterval`, shared backend results across widgets.
- **Constraint:** never reduce refresh frequency arbitrarily if it harms useful market-data freshness.
- **Acceptance:** reduced initial/request burst and improved first useful content without harmful staleness.

### F. Logs and SSE
- **Verified:** `frontend/src/features/logs/useLogs.ts` opens own `EventSource`; shared frontend SSE manager exists; logs prepend/slice retained arrays per message.
- **Investigate:** safe shared ownership, bounded buffering, batching/coalescing, subscription cleanup, message rates/rendering frequency.
- **Constraint:** do not merge stream implementations purely for aesthetics; prove reduced connections or render cost.
- **Acceptance:** measured reduction in connections/render churn or justified no-change decision.

### G. Alerts and other API endpoints
- **Investigate:** `/api/alerts` without assuming SQL is responsible; DB access already uses `asyncio.to_thread()`. Determine DB latency vs thread-pool contention vs concurrent workload vs other causes.
- **Acceptance:** root cause identified with evidence; fix only if measured and safe.

## 8. Official documentation and library-first references

Use documentation matching versions actually installed in MarketHub (`frontend/package.json`, `pyproject.toml`, `requirements.txt`). Do not recommend dependency upgrades solely because newer versions exist. Prefer existing framework features and established libraries over custom infrastructure; no unnecessary library swaps.

| Topic | Official docs | Relevance / compatibility note |
|---|---|---|
| React lazy/Suspense | https://react.dev/reference/react/lazy , https://react.dev/reference/react/Suspense | Route/feature code splitting for React 19 app; preserve fallback UX and error boundaries. |
| Vite build/code splitting | https://vite.dev/guide/features.html , https://vite.dev/config/build-options.html | Vite 8/Rolldown chunking, preload behavior, build budgets; align with current `manualChunks` function form. |
| ECharts modular import | https://echarts.apache.org/handbook/en/basics/import/ | Evaluate `echarts/core` + required charts/components/renderers to reduce bundle while preserving chart behavior/themes. |
| TanStack Query defaults/background refetching | https://tanstack.com/query/latest/docs/framework/react/guides/important-defaults | v5 defaults (`staleTime`, refetch behavior, `refetchIntervalInBackground`) for dashboard polling/fan-out tuning. |
| Python asyncio `to_thread` | https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread | Offload blocking I/O only when thread-safe and measured beneficial (news/DB paths). |
| Starlette threadpool | https://www.starlette.io/threadpool/ | Understand sync endpoint/threadpool behavior and contention implications. |
| SQLite query planning | https://www.sqlite.org/eqp.html , https://www.sqlite.org/queryplanner.html | Use `EXPLAIN QUERY PLAN`/index analysis before schema/index changes (schema protected). |
| Web Vitals | https://web.dev/articles/vitals | Reference thresholds (field, 75th percentile): LCP ≤ 2.5s, INP ≤ 200ms, CLS ≤ 0.1; not guaranteed per local run. |

## 9. Performance targets and acceptance criteria

**Do not promise millisecond cold loads.** Finalize MarketHub-specific numeric targets after baseline measurement (WS-0). Use Web Vitals as reference thresholds, not guarantees.

| Metric | Reference/target (to finalize after baseline) | Notes |
|---|---|---|
| LCP | ≤ 2.5s (Web Vitals reference) | Field 75th percentile reference; measure locally with traces |
| INP | ≤ 200ms (Web Vitals reference) | Interaction responsiveness |
| CLS | ≤ 0.1 (Web Vitals reference) | Visual stability |
| Warm route navigation | TBD after baseline | Target near-instant for lightweight routes; heavy routes on-demand with feedback |
| Dashboard first useful content | TBD after baseline | Shell/critical data prioritized |
| API p50/p95 | TBD per endpoint after baseline | Prior severe samples: quotes timeout, alerts ~5.9s, news ~9.0s, fno coverage/catalog ~3.7–3.9s, futures heatmap ~3.4s |
| Quote SSE processing cost | TBD after profiling | Per-tick CPU/allocations/client fan-out cost |
| CPU/memory | TBD after baseline | No sustained growth/regression outweighing benefit |
| Bundle sizes | TBD budgets after baseline | Reduce initial JS/preload materially (echarts off non-chart initial path) |
| Background network requests | TBD after baseline | No unnecessary duplicate/polling bursts |

**Every optimization must demonstrate:** (1) reproducible improvement, (2) no functional regression, (3) no correctness/freshness regression, (4) no increased resource usage outweighing benefits.

## 10. Workstreams and task tracking

### Status values
`NOT STARTED` | `INVESTIGATING` | `READY` | `IN PROGRESS` | `BLOCKED` | `VERIFIED` | `DEFERRED`

### Task record template (required for every task)
- **Problem / Evidence / Exact files & functions / Proposed solution / Dependencies / Functional risks / Tests / Before-after benchmark / Acceptance criteria / Actual result / Commit reference / Status**

### Workstream 0 — Baseline and instrumentation
| Task ID | Task | Status | Dependencies | Key files/areas | Acceptance criteria |
|---|---|---|---|---|---|
| WS0-01 | Verify/publish audit docs state and preserve untracked perf docs | `NOT STARTED` | none | git history/worktrees/remote, `docs/performance/*` | Publication state recorded; no data loss; safe publish plan |
| WS0-02 | Capture reproducible runtime baseline (cold/warm routes, dashboard first useful content, route API requests) | `NOT STARTED` | WS0-01 | `docs/performance/2026-11-14-route-api-performance-baseline.md` protocol | Baseline artifact with conditions/timestamps |
| WS0-03 | Capture API p50/p95, timeout/error rates for target endpoints | `NOT STARTED` | WS0-02 | `/api/market/quotes`, `/api/news`, `/api/alerts`, `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap`, `/api/market/breadth`, `/api/options/chain/view`, comparisons | Reproducible latency table |
| WS0-04 | Capture frontend metrics (JS/CSS transfer, parse/execute, LCP/INP/CLS where possible, long tasks, renders) | `NOT STARTED` | WS0-02 | browser traces, `frontend/dist/*`, routes | Trace-backed metrics |
| WS0-05 | Capture SSE/message rates, Python CPU/memory, DB query latency/plans, startup time | `NOT STARTED` | WS0-02 | `/api/market/stream`, `/api/logs/stream`, `core/persistence/*`, server process | Measured realtime/resource baseline |
| WS0-06 | Finalize MarketHub-specific targets from baseline | `NOT STARTED` | WS0-02..05 | this plan §9 | Targets recorded with rationale |

### Workstream 1 — WebUI and bundle optimization
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS1-01 | Route-level lazy loading for non-critical routes (keep shell small) | `NOT STARTED` | WS0-04 | `frontend/src/routes/router.tsx`, feature entrypoints | Initial bundle/chunks reduced; routes load on demand; navigation tests pass |
| WS1-02 | Remove global echarts preload / isolate echarts to needed routes | `NOT STARTED` | WS0-04, WS1-01 | `frontend/dist/index.html` (build output), `frontend/vite.config.ts`, `frontend/src/components/EChart.tsx`, chart/breadth/sector/fno consumers | Non-chart initial load excludes echarts preload; chart features intact |
| WS1-03 | Evaluate ECharts modular imports (`echarts/core`) with required registrations only | `NOT STARTED` | WS1-02 | `frontend/src/components/EChart.tsx`, `features/breadth/breadthOption.ts`, `features/sector-heatmap/sectorHeatmapOption.ts`, `features/fno/fnoOption.ts`, `features/market-map/marketMapOption.ts` | Bundle reduced or justified no-change; theme/disposal/resize/interactions/tests preserved |
| WS1-04 | Dashboard shell vs secondary data separation; defer non-critical widgets | `NOT STARTED` | WS0-02, WS0-03 | `frontend/src/features/dashboard/DashboardView.tsx`, `useMarketQuotes.ts`, widget components | Faster first useful content; no harmful freshness loss |
| WS1-05 | Build/chunk-size budgets and verification | `NOT STARTED` | WS1-01..04 | `frontend/vite.config.ts`, CI/build scripts (if approved) | Budgets recorded/enforced; build green |

### Workstream 2 — Backend and API performance
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS2-01 | Profile and optimize `/api/market/quotes` snapshot path (canonical parity) | `NOT STARTED` | WS0-03, WS0-05 | `api/routes.py` (`_market_quotes`, `_quotes_snapshot`), `app/quote_service.py` (`QuoteService.quotes`), `market/quote_coordinator.py` | Measured latency/timeout improvement; snapshot/SSE/reset/canonical parity tests pass |
| WS2-02 | Investigate/fix `/api/alerts` root cause (DB vs contention vs workload) | `NOT STARTED` | WS0-03, WS0-05 | `api/product_routes.py` (`build_alert_routes._list`), `core/persistence/store.py` (`list_market_alerts`), `core/persistence/modules/products.py` (`list_alerts`) | Root cause evidenced; fix measured if applied; contract preserved |
| WS2-03 | Profile/fix `/api/news` blocking I/O and processing | `NOT STARTED` | WS0-03, WS0-05 | `api/news_routes.py` (`_get_news`), `news/service.py` (`NewsService.news`, `_apply_filters`, `_dedup_items`), `core/persistence/modules/news.py` (`query_news_items`) | Measured improvement; filtering/ordering/dedup/response contract preserved; thread-safety understood |
| WS2-04 | F&O coverage plan reuse/cache/single-flight for `/api/fno/coverage` and `/api/fno/catalog` | `NOT STARTED` | WS0-03 | `api/fno_coverage_routes.py` (`_coverage`, `_catalog`, `_plan_payload`), `app/fno_coverage.py` (`FnoCoverageService.plan`, `RoutingPlan.to_dict`) | Reduced recomputation/latency; correct invalidation; no stale dynamic capacity |
| WS2-05 | `/api/futures/heatmap` plan reuse and board efficiency | `NOT STARTED` | WS2-04, WS0-03 | `api/futures_heatmap.py`, `app/fno_coverage.py`, `market/futures_board.py` | Measured improvement; OI/buildup/coverage semantics preserved |
| WS2-06 | Profile moderate endpoints (`/api/market/breadth`, `/api/options/chain/view`, `/api/market/fno/universe`) and optimize if measured | `NOT STARTED` | WS0-03 | `api/routes.py`, `market/breadth.py`, `market/market_universe.py`, `api/product_routes.py`, `app/market_intel.py`, FNO universe/catalog paths | Evidence-backed improvement or justified defer |

### Workstream 3 — Realtime and data processing
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS3-01 | Investigate market SSE per-tick canonical selection/fan-out cost | `NOT STARTED` | WS0-05 | `api/routes.py` (`_market_stream`), `app/server.py` (`_on_market_quote_update`), `app/quote_service.py`, `market/quote_coordinator.py`, `core/sse_broker.py` | Cost measured; optimization preserves provider priority/identity/freshness/stale/ordering/contract |
| WS3-02 | Frontend market quote update/render efficiency (profile-driven) | `NOT STARTED` | WS0-04, WS0-05 | `frontend/src/features/dashboard/useMarketQuotes.ts`, consumers | Measured render/allocation improvement or justified no-change |
| WS3-03 | Logs SSE ownership/buffering/batching investigation | `NOT STARTED` | WS0-04, WS0-05 | `frontend/src/features/logs/useLogs.ts`, `frontend/src/streams/SSEManager.ts` | Proven reduced connections/render cost or justified no-change |
| WS3-04 | Background-task/event-loop contention review | `NOT STARTED` | WS0-05 | `app/server.py`, `core/runtime.py`, `sources/*`, news/analytics/poller tasks | Contention sources identified with evidence |

### Workstream 4 — Database performance
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS4-01 | Query plans and index utilization for slow endpoints | `NOT STARTED` | WS0-05, WS2-02, WS2-03 | `core/persistence/modules/{products,news,events,replay,market_bars}.py`, `core/persistence/store.py` | EXPLAIN/query evidence recorded; schema changes only in separately approved task |
| WS4-02 | Repeated reads/connection/transaction contention analysis | `NOT STARTED` | WS0-05 | `core/persistence/store.py`, hot read/write paths | Contention hotspots evidenced |
| WS4-03 | Cache invalidation correctness review for any new caches | `NOT STARTED` | WS2-04, WS2-05, WS2-06 | affected API/service caches | No stale correctness regressions |

### Workstream 5 — Codebase simplification
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS5-01 | Maintain deletion/simplification inventory with caller/reachability evidence | `NOT STARTED` | none | `docs/performance/2026-11-14-code-bloat-inventory.md` (reference), this plan | Every proposed removal has evidence, risk, tests |
| WS5-02 | Route helper consolidation (body-level proven only) | `NOT STARTED` | WS0-01 | `api/routes.py`, `api/product_routes.py` | Identical/semantically equivalent helpers only; contracts unchanged; tests pass |
| WS5-03 | Identity compatibility/shim simplification design (separate approved scope) | `NOT STARTED` | WS5-01 | `app/market_identity.py`, `app/canonical_identity.py`, `app/instrument_identity.py` | Full caller map + parity plan; no implementation without approval |
| WS5-04 | Legacy/compat branch inventory and retirement preconditions | `NOT STARTED` | WS5-01 | `app/server.py`, `app/secrets_store.py`, `app/subscriptions/*`, `app/auth/upstox_session.py`, `core/persistence/modules/*`, `brokers/*` | Trigger conditions/tests documented per branch |
| WS5-05 | Unused dependency/dead-surface verification (no guesswork) | `NOT STARTED` | WS5-01 | manifests, candidate modules | Proof-based removals only |

### Workstream 6 — Final verification
| Task ID | Task | Status | Dependencies | Key checks | Acceptance criteria |
|---|---|---|---|---|---|
| WS6-01 | Full relevant backend/frontend tests, typecheck, build, lint/ruff, architecture boundaries | `NOT STARTED` | implementation tasks | pytest, frontend test/typecheck/build/lint, `test/test_architecture_boundaries.py` | All required checks green |
| WS6-02 | Browser navigation/API contract/SSE correctness checks | `NOT STARTED` | implementation tasks | routes, API contracts, market SSE/reset/canonical parity | No functional/contract regressions |
| WS6-03 | Before/after performance comparison and sustained market-update/resource checks | `NOT STARTED` | WS0 baselines + implementation tasks | baseline protocol, traces, CPU/memory | Reproducible improvement, no harmful resource increase |
| WS6-04 | FYERS TBT regression checks on affected shared paths | `NOT STARTED` | any task touching shared paths | TBT/feed/status/config related tests/flows | TBT functionality intact |

## 11. Implementation tracking rules

- **Before each implementation task:** mark `IN PROGRESS`, record baseline and files, record expected functional behavior.
- **After implementation:** record exact changed files, commit SHA, tests, before/after measurements, side effects/residual issues; mark `VERIFIED` only when acceptance criteria pass.
- **If evidence contradicts plan:** update plan with new evidence and explain decision.
- **No new plan files per phase.** No parallel agents modifying overlapping files without explicit integration plan.
- **Separate commits for logically independent optimizations.** No large all-at-once refactor.
- **Exact-file git staging only.** Never stage `config.json`, credentials, DB files, build output, or untracked user-generated files.

## 12. Decision log

| Date | Decision | Evidence | Impact |
|---|---|---|---|
| 2026-11-13 | Created initial webui performance fix plan (measurement-first) | `docs/performance/2026-11-10-webui-system-performance-audit.md`, `docs/performance/2026-11-13-webui-live-performance-inspection.md` | Established baseline tasks/priorities |
| 2026-11-13 | Reviewed TBT/Fyers commits; kept perf priorities unchanged | git commits `e8a9489..733f3ed` | No measured bottleneck impact attributed to TBT changes |
| 2026-11-14 | Completed comprehensive read-only audit; produced 6 audit docs (local commit `d0a8b0b`, unpublished at consolidation) | audit docs in `docs/performance/2026-11-14-*` | Consolidated evidence/roadmap/baseline/bloat/deps/opportunities |
| 2026-11-14 | Consolidated all findings into this canonical master plan; removed unsafe broad staging instruction from legacy task text | this file | Single source of truth for planning/execution tracking |

## 13. Progress log

| Date | Update | Commit | Notes |
|---|---|---|---|
| 2026-11-13 | Initial plan created | (see git history) | WebUI load/perf plan |
| 2026-11-13 | Plan updated with live inspection and TBT review | (see git history) | Evidence-backed priorities retained |
| 2026-11-14 | 6 audit docs committed locally | `d0a8b0b` (local, unpublished at consolidation) | Exact files only |
| 2026-11-14 | Master plan consolidated (this update) | `TBD (this session)` | Planning-only; no app/config changes |

## 14. Remaining unmeasured / conflicts / open evidence

- **Unmeasured:** live API rerun on current HEAD/runtime, browser DevTools traces (LCP/INP/CLS/long tasks/JS execution/memory), SSE tick/message rates, sustained Python CPU/memory, SQLite query plans/contention, effective runtime config/data dir for live process.
- **Publication:** `d0a8b0b` (audit docs) local-only/unpublished at consolidation; this plan commit will also need safe publication to `origin/develop`.
- **Proof gaps:** route helper body-level duplication, identity consolidation safety, broker feed lifecycle extraction, legacy branch retirement preconditions, and any "unused" claims require deeper evidence before action.
- **No known conflicting evidence** against top priorities (backend stalls + frontend initial payload/fan-out), but all fixes remain measurement-gated.

---
**End of canonical master plan. Do not start implementation until user reviews and authorizes.**