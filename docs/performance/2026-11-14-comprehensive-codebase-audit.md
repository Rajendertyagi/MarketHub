# MarketHub — Comprehensive Codebase, Architecture and Performance Audit

**Date:** 2026-11-14  
**Repository:** `Rajendertyagi/MarketHub`  
**Branch / HEAD (verified):** `develop` @ `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Scope:** Read-only engineering audit (code/build/config inspection, static tracing, prior live evidence review). **No application code, config, DB, schema, credentials, or runtime settings were modified.** No implementation performed.

## Evidence standard

- **[V] Confirmed:** directly observed in current code/build/config or in cited prior measurements/artifacts.
- **[S] Strongly suspected:** plausible from code structure/tracing but not profiled/measured in a controlled reproduction.
- **[U] Not yet measured:** requires runtime profiling/traces/benchmarks not captured in this audit.

This audit does **not** treat code-inspection-only findings as proven root causes.

---

## 1. Executive summary

### What is actually making MarketHub slow (current evidence)

1. **[V] Backend/API stalls on specific endpoints dominate user-visible delays.** Prior live inspection (2026-11-13, `docs/performance/2026-11-13-webui-live-performance-inspection.md`) measured: `/api/market/quotes` timeout (~20s), `/api/news` ~9.0s, `/api/alerts` ~5.9s, `/api/fno/coverage` ~3.7s, `/api/fno/catalog` ~3.9s, `/api/futures/heatmap` ~3.4s, `/api/market/breadth` ~682ms, `/api/options/chain/view` ~620ms. Static tracing confirms heavy work paths: full-universe canonical quote selection/snapshot build (`api/routes.py`, `app/quote_service.py`), DB over-fetch + in-Python filter/sort/dedup/sentiment work (`news/service.py`), full alert list DB read (`api/product_routes.py`, `core/persistence/store.py`), repeated full F&O routing plan computation including catalog scans/routing (`app/fno_coverage.py`, `api/fno_coverage_routes.py`), futures board build over routed contracts with quote/previous-OI reads (`market/futures_board.py`, `api/futures_heatmap.py`), per-member breadth computation (`market/breadth.py`), and per-contract option-chain catalog/quote enrichment (`app/market_intel.py`).
2. **[V] Initial frontend load is heavier than necessary.** `frontend/dist/index.html` modulepreloads `rolldown-runtime`, `query`, `react`, and **`echarts`** on every initial load. Dist assets (uncompressed): `index` 326,558 B, `react` 250,006 B, `query` 43,367 B, `echarts` 1,116,589 B, `ChartView` 1,613,661 B, CSS 70,537 B. Initial JS referenced by HTML (entry + modulepreloads) is ~1.66 MiB uncompressed; no precompressed `.gz/.br` assets found in `frontend/dist`. `frontend/src/routes/router.tsx` eagerly imports almost all views; only `/chart` is lazy.
3. **[V] Dashboard startup compounds backend and frontend cost.** `DashboardView` mounts market SSE + snapshot (`/api/market/stream`, `/api/market/quotes`) and renders widgets that fetch/poll subscriptions, sector heatmap (8s), breadth (8s), F&O universe (15s), and movers scanner (15s). `useMarketQuotes` flushes React state up to ~1 Hz when changed and rebuilds arrays/canonical maps.
4. **[S] Realtime/render churn can amplify CPU/memory under load.** Market SSE re-selects canonical quotes per live tick (`api/routes.py`), per changed quote hook serializes/broadcasts and evaluates alert engines sequentially (`app/server.py`), logs view uses raw `EventSource` with per-message prepend/slice (`frontend/src/features/logs/useLogs.ts`), and large non-virtualized tables/treemaps/charts exist in several views. Impact requires browser/runtime profiling.
5. **[V/S] SQLite/background architecture is correctness-first but write/read amplification exists.** Per-operation connections with WAL/busy_timeout, per-event persistence + consumer materialization + recent-journal append, retention/news/bar maintenance, and catalog/identity resolution caches with coarse eviction are confirmed patterns; contention/CPU impact is workload-dependent and unprofiled.

### What is not a confirmed root cause
- **[U]** Browser long tasks, JS parse/compile/main-thread attribution, GC/memory leaks, GPU/compositing issues, SSE tick/message rates, sustained Python/DB contention, and effective runtime `test_source` activity for the current process (config shows `test_source.enabled=false` [V], but runtime process state was not revalidated in this session because no server was running).

---

## 2. Review of existing performance plan and prior audits

### Documents reviewed
- `docs/superpowers/plans/2026-11-13-current-webui-load-performance-fix.md` (current plan, updated for 2026-11-13 TBT review)
- `docs/performance/2026-11-10-webui-system-performance-audit.md`
- `docs/performance/2026-11-13-webui-live-performance-inspection.md`

### Validation against current code/evidence (2026-11-14)

| Claimed bottleneck / finding | Status now | Evidence |
|---|---|---|
| Dashboard polling fan-out (sectors/breadth 8s, F&O/movers 15s) | **[V] Confirmed** | `frontend/src/features/dashboard/components/{SectorsWidget,BreadthWidget,FnoWidget,MoversWidget}.tsx`, `DashboardView.tsx` |
| `useMarketQuotes` SSE + snapshot + 1s flush + array/map rebuild | **[V] Confirmed** | `frontend/src/features/dashboard/useMarketQuotes.ts`, `constants.ts`, `api.ts` |
| Logs use raw `EventSource` per mount with per-message prepend/slice | **[V] Confirmed** | `frontend/src/features/logs/useLogs.ts` |
| Chart lazy-loaded but echarts preloaded globally / eager route graph | **[V] Confirmed** | `frontend/src/routes/router.tsx`, `frontend/dist/index.html`, `frontend/vite.config.ts` |
| `/api/market/quotes` expensive snapshot/canonical selection path | **[V] Confirmed path; [V prior] timeout observed** | `api/routes.py` (`_quotes_snapshot`, `_market_stream`), `app/quote_service.py`; 2026-11-13 timeout ~20s |
| `/api/news` slow due to query + over-fetch + filter/sort/dedup/sentiment work | **[V] Confirmed path; [V prior] ~9s** | `api/news_routes.py`, `news/service.py` |
| `/api/alerts` slow for small payload | **[V] Confirmed path (full DB list + notifications); [V prior] ~5.9s** | `api/product_routes.py`, `core/persistence/store.py`, `core/persistence/modules/products.py` |
| `/api/fno/coverage` and `/api/fno/catalog` recompute heavy routing plan | **[V] Confirmed path; [V prior] ~3.7s/~3.9s** | `api/fno_coverage_routes.py`, `app/fno_coverage.py` |
| `/api/futures/heatmap` computes plan + board (board cached, plan not cached at route) | **[V] Confirmed path; [V prior] ~3.4s** | `api/futures_heatmap.py`, `app/fno_coverage.py`, `market/futures_board.py` |
| `/api/market/breadth` moderate latency | **[V] Confirmed path; [V prior] ~682ms** | `api/routes.py`, `market/breadth.py`, `market/market_universe.py` |
| `/api/options/chain/view` moderate latency | **[V] Confirmed path; [V prior] ~620ms** | `api/product_routes.py`, `app/market_intel.py` |
| `test_source` enabled causing background noise | **[V] Not currently true in config** (`sources.test_source.enabled=false` in `config.json`); runtime process not revalidated [U] | `config.json` |
| Browser freeze/GPU/long-task/memory root cause | **[U] Not yet measured** | no DevTools traces in this audit |
| TBT/Fyers recent changes changed measured webui load bottlenecks | **[V] No evidence of change to measured bottlenecks** (settings/feed/status/runtime wiring changed; core slow paths unchanged) | git history `e8a9489..733f3ed`, perf plan review section |

**Conclusion:** The 2026-11-13 plan remains directionally valid for frontend payload/fan-out and for investigating the named slow endpoints, but it should be treated as **partially measured**: endpoint timings are prior samples (not re-run now), and browser/SSE/DB contention causes remain unconfirmed.

---

## 3. Full codebase simplification audit (selected findings)

> Detailed inventory: `docs/performance/2026-11-14-code-bloat-inventory.md`

### Confirmed simplification/complexity areas
- **Compatibility/delegation layers:** `app/market_identity.py` is an active compatibility shim over `app/canonical_identity.py` (runtime + tests). `app/previous_oi.py` re-exports `market.oi` symbols. Multiple legacy/backward-compat branches exist across auth/session, persistence, config/subscriptions, and broker adapters.
- **Large coupled modules/facades:** `core/persistence/store.py` is a broad pass-through facade over `core/persistence/modules/*` (intentional but high maintenance surface). `api/routes.py` and `api/product_routes.py` contain repeated helper-name patterns and mixed concerns (architecture debt already documented).
- **Repeated lifecycle/helper patterns:** broker feed lifecycle helpers show similar names/patterns across Upstox/Fyers/TBT but protocol-specific implementations (refactor risk high).

### Suspected simplification areas
- **Parallel identity abstractions:** `app/canonical_identity.py`, `app/instrument_identity.py`, and `app/market_identity.py` overlap conceptually (different targets/roles in part; consolidation requires careful design).
- **Broad dependency container:** `mcp_server/services.py` `Services` dataclass is a wide service locator (design smell, not dead).
- **Body-level helper duplication:** repeated route helper names in `api/routes.py`/`api/product_routes.py` indicate likely duplication; body equivalence must be proven before extraction.

### Not dead / not unused (important negatives)
- `sources/test_source.py` is runtime-registered (`sources/registry.py`) and referenced by settings/tests.
- `app/previous_oi.py`, `app/fyers_index_source.py`, `app/fyers_runtime_auth.py` are runtime-wired and tested.
- `yfinance` dependency and `yfinance_service/*` are active (MCP Yahoo research capability).

---

## 4. Frontend performance audit

### Confirmed
- **Bundle/load:** initial HTML preloads echarts globally; initial JS (entry + modulepreloads) ~1.66 MiB uncompressed; no dist compression artifacts; chart chunk ~1.61 MiB lazy but echarts vendor chunk still preloaded.
- **Routing:** only `/chart` lazy in `frontend/src/routes/router.tsx`; most views eagerly imported.
- **Dashboard:** SSE + snapshot + subscriptions query + 4 polling widgets (8s/8s/15s/15s); movers tab changes query key.
- **State/update patterns:** `useMarketQuotes` throttled flush (1s) but allocates new arrays and recomputes canonical map on flush; ticker/indices do linear work over quotes.
- **SSE:** market stream uses shared `SSEManager`; logs use raw `EventSource` per mount with per-message state prepend/slice and separate history query merge.
- **Heavy views:** market map polls 5s and recomputes squarified layout over visible stocks; scanners run first card by default and render tables without virtualization; F&O/option-chain/futures/logs/AI-alert tables can be large; no list virtualization library/pattern detected in `frontend/src`.
- **Query config:** global defaults `retry:1`, `refetchOnWindowFocus:false`, `staleTime:30_000`; per-query intervals used widely.

### Suspected / unmeasured
- Actual JS parse/compile/main-thread cost, React commit counts, GC pressure, canvas/WebGL/chart render cost, and memory growth over 10–15 min.
- Network transfer time with server compression (if enabled at runtime) vs uncompressed dist sizes.
- Real SSE tick/message rates and their UI impact.

---

## 5. Backend and API performance audit (slow endpoints traced)

| Endpoint | Confirmed call-path/cost drivers | Prior measurement | Status |
|---|---|---:|---|
| `/api/market/quotes` | `_quotes_snapshot` 1s TTL + lock; builds canonical-selected snapshot via `QuoteService.quotes()` (full quote grouping/canonical resolution/selection) or coordinator fallback; JSON (+ maybe gzip). | timeout ~20s (2026-11-13) | **[V] path, [V prior] latency, [U] current repro** |
| `/api/news` | `NewsService.news`: resolve sources, DB `query_news_items` over-fetch (`limit*5+50`), row mapping, in-Python keyword/symbol/age filtering, sort, dedup, serialize. No network fetch on GET. | ~9.0s | **[V] path, [V prior] latency** |
| `/api/alerts` | `asyncio.to_thread(store.list_market_alerts)` + `engine.recent_notifications(10)`; returns full alerts + notifications. | ~5.9s | **[V] path, [V prior] latency** |
| `/api/fno/coverage` | `asyncio.to_thread(coverage.plan)` full routing plan: universe/catalog reads, futures/expiry selection, spot/futures routing, capacity/state aggregation; large `plan.to_dict()` payload. No route cache/single-flight. | ~3.7s | **[V] path, [V prior] latency** |
| `/api/fno/catalog` | Catalog metadata reads + **full `coverage.plan()`** recomputation, then returns small summary. | ~3.9s | **[V] path, [V prior] latency** |
| `/api/futures/heatmap` | `coverage.plan()` (uncached at route) + `get_futures_board` (20s cache/single-flight) building tiles from quotes and possible previous-OI store reads; projection/serialization. | ~3.4s | **[V] path, [V prior] latency** |
| `/api/market/breadth` | Resolve universe + `compute_breadth` over all members with per-member quote reads and row assembly; no endpoint cache. | ~682ms | **[V] path, [V prior] latency** |
| `/api/options/chain/view` | `MarketIntel.option_chain`: catalog underlying/expiries/strikes, provider selection, spot resolution, per-contract live quote enrichment, analytics/window assembly. | ~620ms | **[V] path, [V prior] latency** |

**Fast comparison points (confirmed):** `/api/market/scanners` returns static metadata; `/api/market/sector-heatmap` and `/api/market/map` perform in-memory linear projections (map recomputes breadth for reconciliation) with index-universe caching benefits; prior timings showed map/sector/scanner fast.

---

## 6. Database and realtime audit

### Confirmed
- **SQLite:** per-operation connections (`EventStore._open`) with `foreign_keys=ON`, `WAL`, `synchronous=NORMAL`, `busy_timeout=5000`; schema v25 with migrations; explicit transactions in event save/materialization, replay/checkpoint, recent journal append/prune, bar upserts/prunes, retention, etc.
- **Write amplification:** persistent event publish can write `persistent_events`, materialize `consumer_event_state` by scanning consumers/topics, and append/prune `recent_events` in separate transaction; generic event path also maintains in-memory history and broadcasts SSE.
- **Read/query patterns:** event/replay queries indexed for common access; recent journal does count-on-append prune logic; news over-fetches for post-filtering; catalog browse/search use CTE/window/keyset patterns (complexity depends on scope).
- **Realtime:** separate generic event broker and market broker (bounded queues, drop-on-full). Market quote hook serializes/broadcasts per changed quote and evaluates alert engines sequentially. Market SSE endpoint re-selects canonical payload per live line for consistency.
- **Background/runtime:** source manager/lifespan tasks, retention prune loop when configured, analytics/Fyers index poller/previous-OI recorder wiring in server; `test_source` disabled in config [V], news refresh appears on-demand in inspected service path [S for absence of scheduler elsewhere].

### Suspected
- Single-writer contention under bursty writes, per-tick CPU/allocation amplification at high tick rates, cache eviction/relookup overhead (QuoteService/SubscriptionManager coarse clears), and drop-on-full SSE impact for slow clients.

---

## 7. Performance measurements (baseline status)

### Reproducible measurements captured in this audit session
- **[V] Static/build measurements:** dist asset sizes, initial HTML modulepreloads, route lazy/eager structure, config flags (`test_source` disabled), dependency manifests.
- **[V] Code-path tracing:** slow endpoint handlers/services and frontend data/render paths with file/symbol evidence.
- **[U] Live runtime measurements:** **not re-run in this session** because no MarketHub server was listening on `127.0.0.1:7070` at audit time and starting the app was avoided to prevent side effects in a read-only audit. Prior live HTTP timings from 2026-11-13 are retained as **historical samples** (same machine/environment family not guaranteed identical now).

### Baseline artifact
- `docs/performance/2026-11-14-route-api-performance-baseline.md` consolidates prior measured samples, current static/build facts, and required rerun protocol.

---

## 8. Protected boundaries and constraints respected

- **Not modified/inspected only:** `app/auth/**`, credentials/session persistence, startup restoration, broker login/feed lifecycle, canonical market semantics, production DB data/schema, `config.json`.
- **FYERS TBT functionality:** not changed; recent TBT commits reviewed for perf impact only.
- **No broad git staging:** only the six exact documentation deliverables are intended for commit (see final report instructions).
- **No dead-code claims from missing direct imports alone:** dynamic/runtime registration and config-driven loading considered.

---

## 9. Prioritized optimization roadmap (summary)

See `docs/performance/2026-11-14-prioritized-optimization-roadmap.md` for full P0–P3 findings with files/symbols, evidence, root cause hypotheses, solutions, benefits, risks, tests/benchmarks, complexity, and boundary notes.

**Highest-value first (risk-adjusted):**
1. **P0:** Diagnose/fix `/api/market/quotes` timeout and slow core APIs (`/api/alerts`, `/api/news`, `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap`) with request-path profiling and targeted caching/single-flight/query-shape fixes (boundary-safe).
2. **P1:** Remove global initial frontend cost (stop preloading echarts on non-chart routes, lazy-load heavy routes/features, split chunks) and reduce dashboard startup fan-out.
3. **P1:** Profile and optimize market SSE/logs render churn only where measured.
4. **P2:** Simplify proven duplication/compat layers (route helpers, identity shims) behind characterization tests.
5. **P3:** Low-risk cleanup after measurement.

---

## 10. Final deliverables produced

1. `docs/performance/2026-11-14-comprehensive-codebase-audit.md` (this document)
2. `docs/performance/2026-11-14-prioritized-optimization-roadmap.md`
3. `docs/performance/2026-11-14-route-api-performance-baseline.md`
4. `docs/performance/2026-11-14-code-bloat-inventory.md`
5. `docs/performance/2026-11-14-dependency-bundle-report.md`
6. `docs/performance/2026-11-14-performance-opportunities.md`

---

## 11. Measurement limitations

- No live server running during this audit session; prior endpoint timings are historical samples (2026-11-13) and must be rerun for current baseline/after comparisons.
- No browser DevTools Performance/Memory/Network traces, no LCP/INP/CLS/long-task attribution, no JS execution profiling.
- No SSE tick/message rate capture, no sustained CPU/GPU/memory timeline, no SQLite lock/contention/query-plan profiling.
- No safe verification of effective runtime config/data dir for a live process in this session.
- Some simplification candidates are name/pattern-level signals; body-level equivalence and runtime reachability require deeper proof before removal.

**Audit complete (read-only). No implementation performed.**