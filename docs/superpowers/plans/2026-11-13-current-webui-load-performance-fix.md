# MarketHub — Consolidated Performance & Code Simplification Master Plan

> **Canonical single source of truth.** This file is the authoritative, continuously maintained record for MarketHub performance and code-simplification work. Update this plan (do not create competing plans). Supporting audit/reference docs remain useful, but implementation sequence, task tracking, decisions, evidence, and progress live here.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **No implementation in a planning-only/measurement-only assignment unless explicitly authorized by the user.**

**Last updated:** 2026-11-14 (WS0 measurement/documentation pass)
**Branch:** `develop`
**Verified local HEAD (this pass start):** `81610ccc092f1b4f15bea6f2547adc3ad410a522` (`docs(plan): consolidate MarketHub performance and simplification master plan`)
**Verified remote `origin/develop` (this pass):** `81610ccc092f1b4f15bea6f2547adc3ad410a522` (in sync after `git fetch origin`)
**Publication state (verified 2026-11-14):** Audit docs commit `d0a8b0b4fb94855738603cdfeffd16291b3fdbb8` and consolidated master plan commit `81610ccc092f1b4f15bea6f2547adc3ad410a522` are **both published on `origin/develop`**. Earlier consolidation noted `d0a8b0b` as local-only/unpublished at that time; subsequent safe push published it (and `81610cc`). No force-push/rewrite performed.

## 1. Objective

Turn MarketHub into a lean, fast, resource-efficient application without losing existing functionality.

1. **Performance:** improve WebUI page loading/navigation, API latency, database access, realtime processing, CPU and RAM usage.
2. **Simplification:** remove proven dead code, duplicate implementations, unnecessary abstractions, redundant dependencies, and inefficient custom solutions — only with evidence and regression safety.

**Do not start implementation until the user reviews and authorizes this updated master plan.**

## 2. Assignment rules (this session: measurement/documentation only)

- **No application code or configuration changes.**
- **No `config.json`, credentials, DB/schema/data, build output, or user-generated file changes.**
- **No optimization/refactor/implementation (WS1–WS5).**
- **No POST/PUT/PATCH/DELETE or state-changing endpoints.** No broker login/refresh, no new broker WebSocket/market-data connections, no FYERS/TBT/Upstox setting changes, no `test_source` enable/disable.
- **No starting a second app instance; do not stop/restart user process.** Do not start app blindly if startup may initiate broker connections/external side effects without config changes.
- **No broad git staging.** Stage/commit only exact intended documentation file paths.
- **No destructive git commands, no force-push, no history rewrite.**

## 3. Source documents consolidated

| Document | Role | Status |
|---|---|---|
| `docs/performance/2026-11-14-comprehensive-codebase-audit.md` | Full audit summary, validation, boundaries, limitations | **[V] Published on `origin/develop` (`d0a8b0b`)** |
| `docs/performance/2026-11-14-prioritized-optimization-roadmap.md` | P0–P3 roadmap with evidence/risks/tests | **[V] Published on `origin/develop` (`d0a8b0b`)** |
| `docs/performance/2026-11-14-route-api-performance-baseline.md` | Historical samples + static baseline + rerun protocol | **[V] Published on `origin/develop` (`d0a8b0b`)** |
| `docs/performance/2026-11-14-code-bloat-inventory.md` | Bloat/duplication/dead-surface inventory | **[V] Published on `origin/develop` (`d0a8b0b`)** |
| `docs/performance/2026-11-14-dependency-bundle-report.md` | Dependency and bundle-size report | **[V] Published on `origin/develop` (`d0a8b0b`)** |
| `docs/performance/2026-11-14-performance-opportunities.md` | Opportunity list with impact/risk | **[V] Published on `origin/develop` (`d0a8b0b`)** |
| `docs/performance/2026-11-10-webui-system-performance-audit.md` | Prior webui/system audit (suspects, gaps) | **[V] Present in repo (untracked in working tree; preserved)** |
| `docs/performance/2026-11-13-webui-live-performance-inspection.md` | Live HTTP/build inspection evidence | **[V] Present in repo (untracked in working tree; preserved)** |

## 4. Evidence standard and publication verification

- **[V] Confirmed:** directly observed in current code/build/config or cited measurements/artifacts.
- **[S] Strongly suspected:** plausible from tracing/structure, not profiled/measured in controlled reproduction.
- **[U] Not yet measured:** requires runtime profiling/traces/benchmarks.

**Publication verification (2026-11-14, this pass):** `git status --short` clean of tracked changes at start (untracked preserved files only); `git branch --show-current` = `develop`; `git rev-parse HEAD` = `81610ccc092f1b4f15bea6f2547adc3ad410a522`; `git fetch origin`; `git ls-remote origin refs/heads/develop` = `81610ccc092f1b4f15bea6f2547adc3ad410a522`; `git worktree list --porcelain` shows main worktree on `develop` plus detached/other worktrees (no overlap). `d0a8b0b` and `81610cc` confirmed published on `origin/develop`. No divergence, no reset/force-push/rebase/clean.

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

**Installed versions (verified 2026-11-14, `frontend/package.json`):** React `^19.3.1`, react-router-dom `^7.18.5`, Vite `^8.3.7`, `@tanstack/react-query` `^5.107.1`, echarts `^6.1.0`, TypeScript `^7.0.2`.

## 9. Performance targets and acceptance criteria

**Do not promise millisecond cold loads.** Finalize MarketHub-specific numeric targets after baseline measurement (WS-0). Use Web Vitals as reference thresholds, not guarantees.

| Metric | Current baseline (2026-11-14 static / 2026-11-13 prior runtime) | Proposed target (improvement goal unless noted) | Rationale | Measurement method | Status |
|---|---|---|---|---|---|
| LCP | **[U]** no browser trace this pass (no running app) | ≤ 2.5s (Web Vitals reference) | Field 75th percentile reference; local trace needed | Browser DevTools/Lighthouse trace on cold/warm dashboard | **DEFERRED** (needs runtime) |
| INP | **[U]** | ≤ 200ms (Web Vitals reference) | Interaction responsiveness | Browser interaction trace | **DEFERRED** |
| CLS | **[U]** | ≤ 0.1 (Web Vitals reference) | Visual stability | Browser trace | **DEFERRED** |
| Frontend asset sizes (uncompressed, from existing `frontend/dist` build output; not measured browser network-transfer sizes) | **[V]** All 7 listed production assets: 3,421,444 B. Lazy-loaded `ChartView` chunk (not initial-load): 1,613,661 B. Initial JavaScript resources identified through entry/preload graph: 1,737,246 B (`echarts` 1,116,589 + `index` 326,558 + `react` 250,006 + `query` 43,367 + runtime 726). Initial CSS: 70,537 B. Combined initial JS+CSS asset sizes: 1,807,783 B. ECharts global preload: 1,116,589 B. | Reduce initial-route JS/CSS by removing global echarts preload from non-chart routes and lazy-loading non-critical routes; budget TBD after WS1 design | `frontend/dist/index.html` modulepreloads echarts on all routes | `Get-ChildItem frontend/dist/assets` + `index.html` inspection | **Baseline recorded (static)** |
| Initial preload graph | **[V]** `index.html` preloads `rolldown-runtime`, `query`, `react`, `echarts` + loads `index.js`/`index.css` | Non-chart routes must not preload echarts chunk | Eager route graph + full echarts import | `frontend/dist/index.html`, `frontend/src/routes/router.tsx`, `frontend/src/components/EChart.tsx` | **Baseline recorded** |
| Route chunking | **[V]** Only `ChartView` lazy (`ChartView-BGB_DAs1.js` 1,613,661 B); other routes eager in router | Lazy-load non-critical/heavy routes; keep shell small | Reduce initial parse/execute and request burst | Router/build inspection | **Baseline recorded** |
| API p50/p95 (target endpoints) | **[V prior, 2026-11-13]** quotes timeout ~20s, news ~9.0s, alerts ~5.9s, fno/coverage ~3.7s, fno/catalog ~3.9s, futures/heatmap ~3.4s, breadth ~682ms, options/chain/view ~620ms | p95 < 1s for read endpoints where feasible; no timeouts; finalize per-endpoint after fresh baseline | Prior samples show severe stalls; must rerun on current HEAD/runtime | Controlled GET requests, p50/p95/max/status/size, cold/warm | **DEFERRED** (no safe running app) |
| Dashboard first useful content / warm nav | **[U]** | TBD after runtime baseline | Shell/critical data prioritized | Browser trace + route timing | **DEFERRED** |
| SSE/message rates & CPU/memory | **[U]** | TBD after runtime baseline | Realtime cost/fan-out unknown | SSE connection/message counts, process CPU/RAM sampling | **DEFERRED** |
| DB query plans (alerts/news) | **[V static, read-only]** `market_alerts` (5 rows): `SCAN market_alerts` + `USE TEMP B-TREE FOR ORDER BY` (created_at). `news_items` (816 rows): `SCAN news_items USING INDEX idx_news_items_published` for recent query. `news_articles` has no `published_at` column (query shape differs). | Avoid unnecessary full scans/temp sorts on hot paths as data grows; confirm with runtime latency | Small current row counts but plan indicates sort strategy | `sqlite3` read-only `EXPLAIN QUERY PLAN` via Python | **Baseline recorded (static)** |

**Every optimization must demonstrate:** (1) reproducible improvement, (2) no functional regression, (3) no correctness/freshness regression, (4) no increased resource usage outweighing benefits.

## 10. Workstreams and task tracking

### Status values
`NOT STARTED` | `INVESTIGATING` | `READY` | `IN PROGRESS` | `BLOCKED` | `VERIFIED` | `DEFERRED`

### Task record template (required for every task)
- **Problem / Evidence / Exact files & functions / Proposed solution / Dependencies / Functional risks / Tests / Before-after benchmark / Acceptance criteria / Actual result / Commit reference / Status**

### Workstream 0 — Baseline and instrumentation

| Task ID | Task | Status | Dependencies | Key files/areas | Acceptance criteria |
|---|---|---|---|---|---|
| WS0-01 | Verify/publish audit docs state and preserve untracked perf docs | `VERIFIED` | none | git history/worktrees/remote, `docs/performance/*` | Publication state recorded; no data loss; safe publish plan |
| WS0-02 | Capture reproducible runtime baseline (cold/warm routes, dashboard first useful content, route API requests) | `BLOCKED` | WS0-01 | `docs/performance/2026-11-14-route-api-performance-baseline.md` protocol | Baseline artifact with conditions/timestamps |
| WS0-03 | Capture API p50/p95, timeout/error rates for target endpoints | `BLOCKED` | WS0-02 | `/api/market/quotes`, `/api/news`, `/api/alerts`, `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap`, `/api/market/breadth`, `/api/options/chain/view`, comparisons | Reproducible latency table |
| WS0-04 | Capture frontend metrics (JS/CSS transfer, parse/execute, LCP/INP/CLS where possible, long tasks, renders) | `PARTIAL` (static verified, runtime/browser blocked) | WS0-02 | browser traces, `frontend/dist/*`, routes | Trace-backed metrics |
| WS0-05 | Capture SSE/message rates, Python CPU/memory, DB query latency/plans, startup time | `PARTIAL` (static DB plans verified, runtime blocked) | WS0-02 | `/api/market/stream`, `/api/logs/stream`, `core/persistence/*`, server process | Measured realtime/resource baseline |
| WS0-06 | Finalize MarketHub-specific targets from baseline | `PARTIAL` (provisional targets recorded; runtime finalization deferred) | WS0-02..05 | this plan §9 | Targets recorded with rationale |

#### WS0 detailed records (2026-11-14)

**WS0-01 — VERIFIED**
- **Problem:** Confirm audit/master-plan publication state and preserve existing work.
- **Evidence:** `git status --short` showed only untracked preserved files (`config.json.pre_tbt_activation_20261111_191221.bak`, 7 untracked perf docs). `git branch --show-current`=`develop`. `git rev-parse HEAD`=`81610ccc092f1b4f15bea6f2547adc3ad410a522`. `git fetch origin` completed. `git ls-remote origin refs/heads/develop`=`81610ccc092f1b4f15bea6f2547adc3ad410a522`. `git worktree list --porcelain` inspected (main + detached/other worktrees). No listener on port 7070; no `python/python3/uvicorn` processes observed.
- **Exact files/functions involved:** git metadata, `docs/performance/*`, `docs/superpowers/plans/2026-11-13-current-webui-load-performance-fix.md`.
- **Actual result:** `d0a8b0b4fb94855738603cdfeffd16291b3fdbb8` (audit docs) and `81610ccc092f1b4f15bea6f2547adc3ad410a522` (master plan) confirmed published on `origin/develop`; local/remote in sync; no divergence/data loss.
- **Acceptance criteria outcome:** Met.
- **Commit reference:** `d0a8b0b`, `81610cc` (published).

**WS0-02 — BLOCKED (runtime/browser)**
- **Problem:** Need cold/warm load, dashboard first useful content, route navigation, initial API request fan-out, console/network errors.
- **Evidence/conditions:** 2026-11-14, Windows, repo `D:\Temp\MarketHub`, branch `develop` @ `81610cc`. No existing MarketHub instance detected (no port 7070 listener, no python/uvicorn processes). `app/server.py` contains startup/restore/source wiring and broker-related initialization paths (e.g. auth restore, source manager/feed setup); starting app may initiate broker connections or external side effects without config changes. Assignment forbids starting second instance, triggering broker login/refresh/websocket/market-data connections, or changing config.
- **Method attempted/available:** Static inspection only; no safe runtime/browser measurement possible.
- **Limitations:** No cold/warm timings, no dashboard first-useful-content, no route navigation timings, no live request waterfall/duplicates/console errors.
- **Reproducible:** N/A (blocked).
- **Acceptance criteria outcome:** Not met (blocked by safety constraints).

**WS0-03 — BLOCKED (runtime API)**
- **Problem:** Need p50/p95/max/status/size/timeout/error for target GET endpoints.
- **Evidence/conditions:** Same as WS0-02; no safe running instance. Prior samples retained as historical evidence only (2026-11-13): `/api/market/quotes` timeout ~20s, `/api/news` ~9.0s, `/api/alerts` ~5.9s, `/api/fno/coverage` ~3.7s, `/api/fno/catalog` ~3.9s, `/api/futures/heatmap` ~3.4s, `/api/market/breadth` ~682ms, `/api/options/chain/view` ~620ms. `/api/market/fno/universe` not remeasured.
- **Method:** No GET requests issued (no app). No POST/PUT/PATCH/DELETE/state-changing calls.
- **Limitations:** No current-head latency table; cold/warm not measured.
- **Acceptance criteria outcome:** Not met (blocked).

**WS0-04 — PARTIAL (static verified; browser/runtime blocked)**
- **Problem:** Need JS/CSS transfer, parse/execute, LCP/INP/CLS, long tasks, route chunks, ECharts loading behavior.
- **Evidence (2026-11-14, static):**
  - `frontend/dist/index.html`: modulepreloads `/ui/assets/rolldown-runtime-XKyaHtl9.js`, `/ui/assets/query-B0JyiKZ_.js`, `/ui/assets/react-DviEPBuS.js`, `/ui/assets/echarts-BBcsMa3e.js`; loads `/ui/assets/index-CJsytrag.js` and `/ui/assets/index-ni0kl9kI.css`.
  - `frontend/dist/assets` (non-map, 7 production files, uncompressed asset sizes from existing build output; not measured browser network-transfer sizes): all 7 listed assets 3,421,444 B total — `echarts-BBcsMa3e.js` 1,116,589, `ChartView-BGB_DAs1.js` 1,613,661 (lazy-loaded, **not** an initial-load resource), `index-CJsytrag.js` 326,558, `react-DviEPBuS.js` 250,006, `index-ni0kl9kI.css` 70,537, `query-B0JyiKZ_.js` 43,367, `rolldown-runtime-XKyaHtl9.js` 726. Initial JS resources identified through entry/preload graph: 1,737,246 B; initial CSS: 70,537 B; combined initial JS+CSS: 1,807,783 B; ECharts global preload: 1,116,589 B. Source maps (5 files) total 14,681,142 B (not initial transfer).
  - `frontend/src/routes/router.tsx`: all major routes eagerly imported; only `ChartView` lazy-loaded via `React.lazy`/`Suspense`.
  - `frontend/vite.config.ts`: Vite 8/Rolldown `manualChunks` function splits `echarts/zrender`, `react/react-dom/react-router*`, `@tanstack` into separate chunks; `build.sourcemap: true`, `outDir: dist`.
  - `frontend/package.json`: React 19.3.1, Vite 8.3.7, TanStack Query 5.107.1, echarts 6.1.0.
- **Method:** File size inspection (`Get-ChildItem`), `index.html`/source/config reads. No `frontend/dist` overwritten; no build run.
- **Limitations:** No browser transfer waterfall, parse/compile/execute timing, LCP/INP/CLS, long tasks, render counts, console errors. No precompressed asset inventory beyond observed dist files.
- **Acceptance criteria outcome:** Partially met (static/build evidence recorded); runtime trace-backed metrics blocked.

**WS0-05 — PARTIAL (static DB plans verified; runtime/SSE/process blocked)**
- **Problem:** Need SSE connection/message rates, reconnects/errors, render/update frequency, Python CPU/memory, SQLite query duration/plans, startup time, event-loop/thread-pool contention evidence.
- **Evidence (2026-11-14, static/read-only DB):**
  - No running app/process; SSE and Python process metrics unavailable.
  - SQLite DB present: `data/events.db` (80,310,272 bytes, last write 2026-11-14 13:23:52). Backups/test DBs observed but not queried. Read-only inspection via `python` + `sqlite3.connect("file:data/events.db?mode=ro", uri=True)`.
  - Tables (30): includes `market_alerts`, `news_items`, `news_articles`, `instruments`, `condition_alerts`, etc.
  - Counts: `market_alerts`=5, `news_items`=816, `news_articles`=0, `instruments`=151,485, `condition_alerts`=0.
  - Query plans: `SELECT * FROM market_alerts ORDER BY created_at DESC` → `SCAN market_alerts` + `USE TEMP B-TREE FOR ORDER BY`. `SELECT * FROM news_items ORDER BY published_at DESC LIMIT 200` → `SCAN news_items USING INDEX idx_news_items_published`. `news_articles` recent-by-`published_at` plan unavailable (`no such column: published_at`).
- **Method:** Read-only SQLite URI connection and `EXPLAIN QUERY PLAN`; no schema/index/data/WAL changes.
- **Limitations:** No query durations, no contention/lock evidence, no runtime SSE rates, no CPU/memory/startup measurements.
- **Acceptance criteria outcome:** Partially met (DB plans/counts recorded); runtime/realtime/resource baseline blocked.

**WS0-06 — PARTIAL (provisional targets recorded)**
- **Problem:** Finalize MarketHub-specific targets from collected baseline.
- **Evidence:** Static baselines recorded (bundle/preload/route chunking, DB plans/counts) and prior runtime samples retained; runtime/browser/SSE/API rerun unavailable.
- **Actual result:** Provisional targets added in §9 with rationale/method/status; per-endpoint/runtime targets remain deferred until safe fresh measurements.
- **Acceptance criteria outcome:** Partially met (targets recorded with rationale; finalization pending runtime evidence).

### Workstream 1 — WebUI and bundle optimization
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS1-01 | Route-level lazy loading for non-critical routes (keep shell small) | `VERIFIED` (Sprint 01) | WS0-04 | `frontend/src/routes/router.tsx`, feature entrypoints | Initial bundle/chunks reduced; routes load on demand; navigation tests pass |
| WS1-02 | Remove global echarts preload / isolate echarts to needed routes | `VERIFIED` (Sprint 01) | WS0-04, WS1-01 | `frontend/dist/index.html` (build output), `frontend/vite.config.ts`, `frontend/src/components/EChart.tsx`, chart/breadth/sector/fno consumers | Non-chart initial load excludes echarts preload; chart features intact |
| WS1-03 | Evaluate ECharts modular imports (`echarts/core`) with required registrations only | `VERIFIED` (Sprint 01) | WS1-02 | `frontend/src/components/EChart.tsx`, `features/breadth/breadthOption.ts`, `features/sector-heatmap/sectorHeatmapOption.ts`, `features/fno/fnoOption.ts`, `features/market-map/marketMapOption.ts` | Bundle reduced or justified no-change; theme/disposal/resize/interactions/tests preserved |
| WS1-04 | Dashboard shell vs secondary data separation; defer non-critical widgets | `NOT STARTED` | WS0-02, WS0-03 | `frontend/src/features/dashboard/DashboardView.tsx`, `useMarketQuotes.ts`, widget components | Faster first useful content; no harmful freshness loss |
| WS1-05 | Build/chunk-size budgets and verification | `PARTIAL` (Sprint 01: build verified, budgets not formally enforced) | WS1-01..04 | `frontend/vite.config.ts`, CI/build scripts (if approved) | Budgets recorded/enforced; build green |

### Workstream 2 — Backend and API performance
| Task ID | Task | Status | Dependencies | Key files/functions | Acceptance criteria |
|---|---|---|---|---|---|
| WS2-01 | Profile and optimize `/api/market/quotes` snapshot path (canonical parity) | `PARTIAL` (Sprint 02: SSE/single-instrument canonical lookup optimized with parity tests; snapshot full-build path unchanged; live endpoint latency unmeasured) | WS0-03, WS0-05 | `api/routes.py` (`_market_quotes`, `_quotes_snapshot`, `_market_stream`), `app/quote_service.py` (`QuoteService.quote`, `QuoteService.quotes`), `market/quote_coordinator.py` | Measured latency/timeout improvement; snapshot/SSE/reset/canonical parity tests pass |
| WS2-02 | Investigate/fix `/api/alerts` root cause (DB vs contention vs workload) | `PARTIAL` (Sprint 02: profiled with isolated temp DB; no code change justified beyond existing `to_thread` DB read; live contention unmeasured) | WS0-03, WS0-05 | `api/product_routes.py` (`build_alert_routes._list`), `core/persistence/store.py` (`list_market_alerts`), `core/persistence/modules/products.py` (`list_alerts`) | Root cause evidenced; fix measured if applied; contract preserved |
| WS2-03 | Profile/fix `/api/news` blocking I/O and processing | `PARTIAL` (Sprint 02: async store reads offloaded from event loop; isolated benchmark recorded; live endpoint latency unmeasured) | WS0-03, WS0-05 | `api/news_routes.py` (`_get_news`), `news/service.py` (`NewsService.news`, `_apply_filters`, `_dedup_items`), `core/persistence/modules/news.py` (`query_news_items`) | Measured improvement; filtering/ordering/dedup/response contract preserved; thread-safety understood |
| WS2-04 | F&O coverage plan reuse/cache/single-flight for `/api/fno/coverage` and `/api/fno/catalog` | `VERIFIED` (Sprint 01 + Sprint 02 correctness hardening) | WS0-03 | `api/fno_coverage_routes.py` (`_coverage`, `_catalog`, `_plan_payload`), `app/fno_coverage.py` (`FnoCoverageService.plan`, `RoutingPlan.to_dict`) | Reduced recomputation/latency; correct invalidation; no stale dynamic capacity |
| WS2-05 | `/api/futures/heatmap` plan reuse and board efficiency | `PARTIAL` (Sprint 01/02: plan skeleton reuse + cache correctness hardening benefit heatmap; board TTL/single-flight already present, expired-cache pruning added; board computation logic unchanged) | WS2-04, WS0-03 | `api/futures_heatmap.py`, `app/fno_coverage.py`, `market/futures_board.py` | Measured improvement; OI/buildup/coverage semantics preserved |
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
| 2026-11-14 | Completed comprehensive read-only audit; produced 6 audit docs | audit docs in `docs/performance/2026-11-14-*`, commit `d0a8b0b4fb94855738603cdfeffd16291b3fdbb8` | Consolidated evidence/roadmap/baseline/bloat/deps/opportunities |
| 2026-11-14 | Consolidated all findings into this canonical master plan; removed unsafe broad staging instruction from legacy task text | commit `81610ccc092f1b4f15bea6f2547adc3ad410a522` | Single source of truth for planning/execution tracking |
| 2026-11-14 | Verified publication: `d0a8b0b` and `81610cc` both published on `origin/develop` (remote/local in sync) | `git fetch`, `git ls-remote origin refs/heads/develop`, `git rev-parse HEAD`, worktree/status inspection | Corrected prior local-only/unpublished bookkeeping; no force-push/rewrite |
| 2026-11-14 | Executed WS0 measurement-only pass with safety constraints (no app start, no broker/config/DB changes) | static build/route/config/version inspection, read-only SQLite `EXPLAIN QUERY PLAN` on `data/events.db` | WS0-01 verified; WS0-02/03 blocked; WS0-04/05 partial; WS0-06 provisional targets recorded |
| 2026-11-14 | Corrected frontend baseline metric labeling (initial JS vs lazy ChartView) | commit `2bb215ffafa6d19f023489c4b51a7488fe7ad24f` | Clarified uncompressed asset-size baseline; no behavior change |
| 2026-11-14 | Implemented Sprint 01 bounded performance improvements (F&O plan skeleton cache + frontend route lazy-loading/modular ECharts) | commits `0fc3e585dc4b4ee111e62bd8553f5e737a07e2ec`, `d7ccfa59f61983cacef94a2565c00501a96c6104` | Measured backend recomputation reduction (microbenchmark/tests) and major initial frontend bundle/preload reduction (isolated build comparison); no API contract/canonical/auth/broker/config/DB changes |
| 2026-11-14 | Sprint 02: optimized quote single-instrument canonical lookup (SSE hot path) via direct store-key reads with full-scan fallback | commit `4213ece2ac168aa6f969257bb5b718faf2fc210d`, `app/quote_service.py` | Eliminates O(N) full-quote scans per lookup when storage keys resolve; parity tests passed; live endpoint/SSE rates unmeasured |
| 2026-11-14 | Sprint 02: offloaded synchronous news store reads from async request path | commit `a4d4d4b6f3a788cfae79222145495282bbf10e3b`, `news/service.py` | Prevents event-loop blocking on DB reads; isolated benchmark recorded; response/filter/sort/dedup contract preserved |
| 2026-11-14 | Sprint 02: hardened F&O coverage skeleton cache correctness (date rollover, enabled-provider set, unreliable fingerprint no-cache, cache growth bound) and pruned expired futures-board cache entries | commit `fb6231b7c1e87f6dff9ba2fe6c202dbdd6688e1b`, `app/fno_coverage.py`, `market/futures_board.py`, `test/test_fno_coverage_plan_cache.py` | Prevents stale expiry/universe reuse and unbounded cache growth; dynamic provider state remains fresh; regression tests added |

## 13. Progress log

| Date | Update | Commit | Notes |
|---|---|---|---|
| 2026-11-13 | Initial plan created | (see git history) | WebUI load/perf plan |
| 2026-11-13 | Plan updated with live inspection and TBT review | (see git history) | Evidence-backed priorities retained |
| 2026-11-14 | 6 audit docs committed and published | `d0a8b0b4fb94855738603cdfeffd16291b3fdbb8` | Exact files only; published on `origin/develop` |
| 2026-11-14 | Master plan consolidated and published | `81610ccc092f1b4f15bea6f2547adc3ad410a522` | Planning-only; no app/config changes; published on `origin/develop` |
| 2026-11-14 | Corrected publication/progress records and added WS0 baseline evidence | `b73249f8263f94b463fe830c2ecd608273410500` | Measurement/documentation only; runtime/browser/API/SSE/process measurements blocked by no-safe-running-instance constraint |
| 2026-11-14 | Corrected frontend initial asset baseline metrics | `2bb215ffafa6d19f023489c4b51a7488fe7ad24f` | Documentation-only correction |
| 2026-11-14 | Sprint 01 backend: F&O coverage plan skeleton cache/single-flight with fresh dynamic routing | `0fc3e585dc4b4ee111e62bd8553f5e737a07e2ec` | `app/fno_coverage.py`, `test/test_fno_coverage_plan_cache.py`; parity/invalidation/freshness/concurrency tests + microbenchmark |
| 2026-11-14 | Sprint 01 frontend: route lazy-loading + modular ECharts isolation | `d7ccfa59f61983cacef94a2565c00501a96c6104` | Router/EChart/option builders/tests updated; isolated build shows initial preload excludes echarts and initial JS+CSS reduced |
| 2026-11-14 | Sprint 02 F&O cache correctness hardening + futures-board cache pruning | `fb6231b7c1e87f6dff9ba2fe6c202dbdd6688e1b` | Date rollover/enabled-provider/unreliable-fingerprint/growth fixes with regression tests; futures board expired-entry pruning |
| 2026-11-14 | Sprint 02 quotes: direct single-instrument canonical lookup optimization | `4213ece2ac168aa6f969257bb5b718faf2fc210d` | `app/quote_service.py`; avoids full quote scans on SSE per-event path when keys resolve; quote/SSE parity tests passed |
| 2026-11-14 | Sprint 02 news: async DB read offload | `a4d4d4b6f3a788cfae79222145495282bbf10e3b` | `news/service.py`; store reads moved off event loop via `asyncio.to_thread`; news tests passed |

## 15. Sprint 01 implementation results (2026-11-14)

### Scope and safety
- **Authorized bounded changes only:** frontend route-loading/ECharts area and F&O coverage/heatmap planning area.
- **Untouched:** `config.json`, credentials/auth/session persistence, startup restoration, broker login/feed lifecycle, FYERS TBT, canonical market identity/quote semantics, production DB contents/schema, unrelated features. No full app launched; no broker connections triggered.
- **HEAD at start:** `2bb215ffafa6d19f023489c4b51a7488fe7ad24f` (`develop`).

### Workstream A — Backend F&O performance (WS2-04 verified, WS2-05 partial)

**Problem/evidence:** `FnoCoverageService.plan()` rebuilt catalog-derived futures/universe/spot-key structures on every call; `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap` all invoke `coverage.plan()`.

**Implementation:** Added catalog-derived skeleton caching in `app/fno_coverage.py` keyed by requested expiry + relevant config + catalog sync fingerprint, with single-flight build protection and explicit `invalidate_plan_cache()`. Skeleton caches immutable/catalog-derived inputs (universe symbols/index set, futures groups/selected expiries, equity/index spot-key maps). **Dynamic provider state is always computed fresh per call** (provider status/availability, subscribed keys, budget usage/capacity, routing/state decisions), preserving dynamic capacity freshness and existing behavior/contracts.

**Exact changed files:** `app/fno_coverage.py`, `test/test_fno_coverage_plan_cache.py` (new).

**Tests/verification:**
- `python -m pytest test/test_fno_coverage.py test/test_futures_heatmap_api.py test/test_fno_coverage_plan_cache.py -q -s` → **24 passed** (includes parity, skeleton reuse, catalog-sync invalidation, explicit invalidation, dynamic-state freshness with cached skeleton, single-flight concurrency, microbenchmark).
- `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
- `python -m ruff check app/fno_coverage.py api/fno_coverage_routes.py api/futures_heatmap.py test/test_fno_coverage_plan_cache.py` → clean.

**Benchmark (repeatable microbenchmark, isolated test fixtures/data, not live endpoint latency):**
- `test_fno_coverage_plan_cache.py::test_plan_microbenchmark_repeated_calls`
- **Before (uncached catalog-derived rebuild per call, implied):** each `plan()` would perform catalog futures index reads (NSE+BSE) and equity universe reads per provider.
- **After (warm skeleton cache):** 50 repeated `plan()` calls, `elapsed_s=0.002747`, `per_call_ms=0.055`, `catalog_future_reads=2`, `equity_reads=2` (no additional catalog reads after initial skeleton build). Single-flight test confirmed one skeleton build under 8 concurrent callers.
- **Dynamic freshness verified:** provider status/subscription changes reflected in routing/state on subsequent calls while skeleton/catalog reads remained cached.

**Risks/limitations:** Catalog fingerprint uses `catalog.sync_state()` best-effort; if sync state is unavailable, cache key falls back conservatively. No live endpoint p50/p95 measured (no safe running app). `api/fno_coverage_routes.py`/`api/futures_heatmap.py` unchanged functionally but benefit from cheaper `coverage.plan()` calls.

**Commit:** `0fc3e585dc4b4ee111e62bd8553f5e737a07e2ec` — `perf(fno): cache catalog-derived coverage plan skeleton with fresh dynamic routing`

### Workstream B — Frontend initial-load performance (WS1-01/02/03 verified, WS1-05 partial)

**Problem/evidence:** Eager route graph and full `echarts` import/global preload on initial page; baseline initial JS+CSS (entry/preload graph, uncompressed assets) 1,807,783 B with ECharts preload 1,116,589 B.

**Implementation:**
- Lazy-loaded non-critical/heavy routes in `frontend/src/routes/router.tsx` with `React.lazy`/`Suspense` fallbacks (dashboard kept eager as initial shell route; chart and chart-heavy/feature routes loaded on demand).
- Switched `frontend/src/components/EChart.tsx` to modular `echarts/core` with required registrations only (`BarChart`, `TreemapChart`, `GridComponent`, `TooltipComponent`, `LegendComponent`, `CanvasRenderer`) and resilient init/resize/update/dispose handling.
- Updated chart option builder type imports to `echarts/core` (`breadthOption.ts`, `sectorHeatmapOption.ts`, `fnoOption.ts`, `marketMapOption.ts`).
- Updated affected frontend tests/mocks for modular echarts (`analyticsOptions.test.ts`, `breadth.test.tsx`, `sectorHeatmap.test.tsx`).

**Exact changed files:** `frontend/src/routes/router.tsx`, `frontend/src/components/EChart.tsx`, `frontend/src/features/breadth/breadthOption.ts`, `frontend/src/features/fno/fnoOption.ts`, `frontend/src/features/market-map/marketMapOption.ts`, `frontend/src/features/sector-heatmap/sectorHeatmapOption.ts`, `frontend/src/__tests__/analyticsOptions.test.ts`, `frontend/src/__tests__/breadth.test.tsx`, `frontend/src/__tests__/sectorHeatmap.test.tsx`.

**Build comparison (isolated output `--outDir ../.tmp-frontend-dist-sprint01`, temp dir removed after measurement; existing `frontend/dist` not overwritten):**

| Metric | Baseline (recorded) | Sprint 01 build | Delta |
|---|---:|---:|---:|
| Initial JS resources (entry/preload graph, uncompressed) | 1,737,246 B | 436,387 B (`index` 142,288 + `react` 250,006 + `query` 43,367 + runtime 726) | **-1,300,859 B (-74.9%)** |
| Initial CSS (uncompressed) | 70,537 B | 70,537 B | 0 B |
| Combined initial JS+CSS (entry/preload graph) | 1,807,783 B | 506,924 B | **-1,300,859 B (-72.0%)** |
| ECharts global preload on initial page | 1,116,589 B (preloaded) | **0 B (not preloaded)** | **removed from initial load** |
| ECharts chunk (lazy, modular) | 1,116,589 B (full, preloaded) | 545,838 B (`echarts-DBNUSy3J.js`, on-demand) | **-570,751 B (-51.1%)** |
| ChartView chunk (lazy) | 1,613,661 B | 1,613,703 B | ~same |
| Initial `index.html` modulepreloads | runtime, query, react, echarts | runtime, query, react | echarts removed |

**Route/chunk behavior:** non-critical routes emitted as separate lazy chunks (e.g. `BreadthView`, `SectorHeatmapView`, `MarketMapView`, `FuturesHeatmapView`, `FnoWorkspaceView`, `OptionChainView`, settings/news/alerts/etc.). Dashboard remains eager.

**Tests/verification:**
- `bun run typecheck` (frontend) → **pass**.
- `bun run build -- --outDir ../.tmp-frontend-dist-sprint01` → **pass** (isolated; temp output removed).
- Targeted tests: `src/__tests__/analyticsOptions.test.ts`, `src/__tests__/breadth.test.tsx`, `src/__tests__/sectorHeatmap.test.tsx`, `src/__tests__/futuresHeatmap.test.tsx`, `src/__tests__/fnoFormat.test.ts` → **28 passed** across 5 files.
- Chart functionality/themes/resize/disposal preserved by existing `EChart` lifecycle contract; modular registrations cover bar/treemap/grid/tooltip/legend/canvas used by current chart options.

**Risks/limitations:** No browser DevTools trace/LCP/INP/CLS/navigation timing captured (no safe running app). Dashboard widget polling/fan-out unchanged (WS1-04 not addressed). Build chunk-size budgets not formally enforced in CI/config (WS1-05 partial). Vite still warns about large lazy chunks (`ChartView`, `echarts`) which are expected on-demand chunks.

**Commit:** `d7ccfa59f61983cacef94a2565c00501a96c6104` — `perf(frontend): lazy-load routes and isolate modular echarts from initial bundle`

## 16. Sprint 02 implementation results (2026-11-14)

### Scope and safety
- **Authorized bounded changes only:** market quote read/SSE path, news service async DB access, alerts profiling (no schema/index/prod-data changes), F&O coverage cache correctness hardening, futures board cache pruning, relevant tests, and this master plan.
- **Untouched:** `config.json`, credentials/auth/session persistence, startup restoration, broker login/feed lifecycle/subscription reconciliation, FYERS TBT, canonical market identity/quote semantics, production DB contents/schema, frontend UI, unrelated features. No full app launched; no broker connections triggered; no production DB writes.
- **HEAD at start:** `c99404f1ce7a3500dc43e06c6c7c95a441d0f349` (`develop`, in sync with `origin/develop`).

### Workstream A — Market quote snapshot and SSE performance (WS2-01 PARTIAL)

**Problem/evidence (historical 2026-11-13):** `/api/market/quotes` timed out ~20s. Source inspection showed `QuoteService.quote()` scanned the full quote collection per lookup (`await self._quotes()` + candidate scan), and `api/routes.py` `_market_stream` re-selected canonical quote per live event via `quote_service.quote(ref)`.

**Implementation:** Optimized `QuoteService.quote()` in `app/quote_service.py` to resolve provider storage keys and read candidates directly from `market_service.get_quote_now()` (O(k) per instrument, k = provider keys/candidates) before falling back to the previous full-scan behavior for correctness in unusual identity mappings. Preserved canonical identity resolution, provider ownership/priority, freshness/stale semantics, fallback behavior, snapshot/SSE consistency intent, ordering/duplicate handling, and API response structure.

**Exact changed files:** `app/quote_service.py`.

**Before/after (isolated service microbenchmark, synthetic in-memory `MarketService`, 3000 instruments × 2 providers = 6000 quotes, 2026-11-14):**
| Path | Iterations | p50 | p95 | Notes |
|---|---:|---:|---:|---|
| Full canonical-id scan over all quotes (pre-optimization algorithmic cost) | 1000 | 0.196 ms | 0.306 ms | `O(N)` scan per lookup |
| Direct `get_quote_now(exchange, token)` store-key read (optimized primitive) | 20000 | 0.0001 ms | 0.0002 ms | `O(1)` per key; optimized `quote()` uses direct candidate reads + selection |

**Interpretation:** For SSE per-event canonical reselection, avoiding full-universe scans removes repeated O(N) work per update when storage keys resolve (typical path). This is a repeatable isolated microbenchmark, **not** a live `/api/market/quotes` or live SSE latency measurement.

**Tests/verification:** `python -m pytest test/test_quote_service_parity.py test/test_quote_coordinator.py test/test_quote_canonical_parity.py test/test_market_sse.py test/test_sse_stream.py -q` → **25 passed, 1 skipped**. `python -m ruff check app/quote_service.py` → clean.

**Risks/limitations:** Snapshot endpoint still builds full canonical payload (`quote_service.quotes()`/coordinator snapshot) and remains cached for 1s in route; no live endpoint/browser/SSE/process measurements. Full-scan fallback retained for correctness.

**Commit:** `4213ece2ac168aa6f969257bb5b718faf2fc210d` — `perf(quotes): avoid full quote scans for single-instrument canonical lookups`

### Workstream B — News API performance (WS2-03 PARTIAL)

**Problem/evidence (historical 2026-11-13):** `/api/news` ~9.0s. `NewsService.news()` (async) performed synchronous store reads (`list_sources`/`query_news_items`) and Python-side filtering/sorting/dedup in the request path.

**Implementation:** Offloaded synchronous store reads in `news/service.py` to `asyncio.to_thread` for `news()`, `get_item()`, `freshness()`, `last_refresh_at()`, and sentiment source resolution/persistence (`_resolve_sources`, `update_news_sentiments`) while preserving search/source/symbol/sentiment/sort/dedup/pagination/limits/response behavior. Store opens per-call SQLite connections (`EventStore._open`), so threaded reads are compatible with existing store usage.

**Exact changed files:** `news/service.py`.

**Before/after (isolated temp DB, `EventStore` + `NewsService`, 3 sources, 2000 `news_items`, `NewsFilter(limit=50)`, 30 iterations, warm, 2026-11-14):**
| Stage/metric | Measurement |
|---|---:|
| Total `await svc.news(filter)` (after offload) | p50 5.154 ms, p95 6.096 ms, max 6.724 ms |
| Rows fetched (query over-fetch headroom) | up to 300 rows for limit 50 (`max(limit*5, limit+50)`) |
| DB/query + Python processing split | Not separately instrumented in this pass; DB read now non-blocking to event loop |

**Interpretation:** Isolated request/service processing is fast on 2k rows; historical 9s not reproduced in isolated environment. Primary justified fix applied: remove synchronous DB blocking from async path. **Not** a live `/api/news` latency measurement.

**Tests/verification:** `python -m pytest test/test_news_tools.py test/test_l1_logging_news.py -q` → **48 passed**. `python -m ruff check news/service.py` → clean.

**Risks/limitations:** No live endpoint p50/p95; no schema/index/prod-data changes; over-fetch/filter/sort/dedup semantics preserved (no DB-side semantic rewrite without stronger evidence).

**Commit:** `a4d4d4b6f3a788cfae79222145495282bbf10e3b` — `perf(news): offload synchronous store reads from async request path`

### Workstream C — Alerts API performance (WS2-02 PARTIAL)

**Problem/evidence (historical 2026-11-13):** `/api/alerts` ~5.9s. Prior read-only plan showed `SCAN market_alerts` + temp B-tree sort on 5 rows (not causal). Route already used `asyncio.to_thread(store.list_market_alerts)`.

**Profiling (isolated temp DB, 500 `market_alerts`, 50 iterations, 2026-11-14):**
| Component | Measurement | Notes |
|---|---:|---|
| `await asyncio.to_thread(store.list_market_alerts)` | p50 2.281 ms, p95 3.140 ms, max 3.537 ms | DB query + row conversion in thread |
| `engine.recent_notifications(10)` | in-memory slice under lock (`app/alerts.py`) | no DB/I/O observed |
| Route serialization | not separately timed; payload is small JSON for tested sizes | contract preserved |

**Root-cause assessment:** No evidence in isolated profiling that DB query/serialization/notification retrieval dominates at realistic small-medium row counts; route already avoids blocking DB on event loop. Historical 5.9s remains **unreproduced/unexplained** without live contention/process/thread-pool/runtime measurements.

**Implementation:** No application-code change justified by measurements (avoided speculative changes). No schema/index/prod-data changes.

**Tests/verification:** `python -m pytest test/test_alert_boundary.py test/test_alert_history.py test/test_alert_monitoring.py test/test_alert_reliability.py test/test_alert_trigger_events.py test/test_market_alert_concurrency.py -q` → **69 passed**.

**Risks/limitations:** Live thread-pool contention, concurrent workload, and runtime environment effects not measured (no safe running app).

**Commit:** none (evidence-only; no justified code change).

### Workstream D — Futures heatmap and F&O cache hardening (WS2-04 VERIFIED hardening, WS2-05 PARTIAL)

**D1 correctness hardening (`app/fno_coverage.py`, `test/test_fno_coverage_plan_cache.py`):**
- **Date rollover:** skeleton key now includes `date.today().isoformat()` because expiry selection depends on current date; deterministic rollover test added.
- **Provider enabled-state changes:** skeleton key now includes enabled provider set (`registry.enabled_ids()` snapshot) because universe selection depends on enabled providers; regression test added.
- **Missing/unreliable catalog fingerprint:** `sync_state()` missing/empty/exception/unparseable now treated as unreliable and **not cached** (correctness over indefinite reuse); regression tests added.
- **Cache growth:** skeleton cache bounded (`_MAX_SKELETON_CACHE=32`) with oldest-entry eviction; build locks retained (not pruned while in-flight) to preserve single-flight correctness.
- **Dynamic freshness preserved:** provider status/availability/subscribed/capacity/routing still computed fresh per `plan()` call.

**D2 futures heatmap/board (`market/futures_board.py`, `api/futures_heatmap.py` unchanged):**
- `get_futures_board()` already had TTL cache + single-flight; added expired-entry pruning on access to prevent stale cached-entry accumulation. Build locks retained to avoid breaking single-flight.
- Heatmap path benefits from Sprint 01/02 cheaper `coverage.plan()` and existing board cache; board computation/OI/buildup/ordering logic unchanged.

**Exact changed files:** `app/fno_coverage.py`, `market/futures_board.py`, `test/test_fno_coverage_plan_cache.py`.

**Tests/verification:**
- `python -m pytest test/test_fno_coverage.py test/test_futures_heatmap_api.py test/test_fno_coverage_plan_cache.py test/test_futures_board.py -q` → **38 passed** (includes date rollover, enabled-provider change, missing/unparseable fingerprint no-cache, cache bound, existing parity/freshness/single-flight/heatmap/board tests).
- `python -m ruff check app/fno_coverage.py market/futures_board.py test/test_fno_coverage_plan_cache.py` → clean.

**Risks/limitations:** No live `/api/futures/heatmap` endpoint latency measured. Build-lock maps can still retain keys for distinct historical cache keys in pathological churn, but skeleton cache is bounded and futures-board expired entries are pruned; correctness prioritized over aggressive lock pruning.

**Commit:** `fb6231b7c1e87f6dff9ba2fe6c202dbdd6688e1b` — `perf(fno): harden coverage plan cache for rollover, provider changes, and growth`

### Sprint 02 verification summary
- **Targeted relevant tests:** 180 passed, 1 skipped (`quote/market/sse/news/alerts/fno/futures/heatmap` suites listed above).
- **Architecture boundaries:** `python -m pytest test/test_architecture_boundaries.py -q` → **17 passed**.
- **Ruff (changed backend/test files):** `python -m ruff check app/fno_coverage.py app/quote_service.py market/futures_board.py news/service.py test/test_fno_coverage_plan_cache.py` → clean.
- **Frontend:** not modified; typecheck/build not rerun (no shared frontend code changed).

## 14. Remaining unmeasured / conflicts / open evidence

- **Unmeasured (blocked):** live API rerun on current HEAD/runtime (p50/p95/max/status/size/timeouts/errors, cold/warm) for `/api/market/quotes`, `/api/news`, `/api/alerts`, `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap`, `/api/market/breadth`, `/api/options/chain/view`, `/api/market/fno/universe`; browser DevTools traces (LCP/INP/CLS/long tasks/JS parse-execute/memory, cold/warm navigation, dashboard first useful content, request waterfall/duplicates/console errors); SSE connection/message/reconnect/error rates and frontend update frequency; sustained Python CPU/memory, startup time, event-loop/thread-pool contention; SQLite query durations/contention under load.
- **Partially measured:** frontend bundle/preload/route chunking and installed versions (static + isolated build comparison); read-only DB table counts and `EXPLAIN QUERY PLAN` for selected alerts/news queries; F&O plan recomputation microbenchmark (test fixtures, not live endpoint latency); quote lookup microbenchmark (synthetic in-memory, 6000 quotes); news service isolated temp-DB benchmark (2000 rows); alerts isolated temp-DB query benchmark (500 rows).
- **Publication:** `d0a8b0b`, `81610cc`, `b73249f`, `2bb215f`, `0fc3e58`, `d7ccfa5`, `c99404f` published on `origin/develop`. Sprint 02 commits `fb6231b`, `4213ece`, `a4d4d4b` and this plan-update commit require safe publication after commit.
- **Proof gaps:** route helper body-level duplication, identity consolidation safety, broker feed lifecycle extraction, legacy branch retirement preconditions, and any "unused" claims require deeper evidence before action.
- **No known conflicting evidence** against top priorities (backend stalls + frontend initial payload/fan-out), but all fixes remain measurement-gated.

---
**End of canonical master plan. Sprint 02 bounded implementation completed with repeatable isolated evidence and passing regression checks; live runtime verification remains blocked by safe-running-instance constraints.**
