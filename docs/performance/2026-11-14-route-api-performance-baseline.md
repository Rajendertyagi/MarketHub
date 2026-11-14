# MarketHub — Route/API Performance Baseline (2026-11-14)

**Date:** 2026-11-14  
**Branch / HEAD:** `develop` @ `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Environment (audit session):** Windows 11 Pro 10.0.26300, 12th Gen Intel Core i3-12100 (4C/8T), ~32 GB RAM, Intel UHD 730 (from prior audit context; not re-profiled).  
**Server state during this audit:** No MarketHub server listening on `127.0.0.1:7070` at capture time (**[V]** checked). Live endpoint timings were **not rerun** in this session to avoid side effects in a read-only audit.

## 1. Baseline status

| Category | Status | Notes |
|---|---|---|
| Cold initial WebUI load | **[U] not measured now** | Requires running server + browser cold-cache trace. Prior 2026-11-13 samples showed `/ui/` variability and 6s main JS asset sample in one batch. |
| Warm route navigation | **[U]** | Requires browser interaction traces. |
| Dashboard initial content time | **[U]** | Depends on `/api/market/quotes`, SSE, and widget APIs. |
| Major slow routes (UI) | **[U]** | No DevTools traces captured. |
| API median/p95 latency | **[U] current; [V prior samples]** | Prior single-run samples recorded 2026-11-13 (below). |
| API timeout/error rates | **[U] current; [V prior samples]** | `/api/market/quotes` timeout observed 2026-11-13. |
| JS transfer/execution costs | **[V] static sizes; [U] runtime** | Dist sizes/preloads known; parse/execute not profiled. |
| Browser long tasks | **[U]** | No Performance trace. |
| SSE connection/message rates | **[U]** | No runtime capture. |
| Python CPU/memory | **[U] current** | No live process profiled in this session. |
| DB query latency | **[U]** | No query timing/plan capture. |
| Startup time | **[U]** | Not safely/measurably rerun in this audit. |

## 2. Historical live API measurements (2026-11-13, prior inspection)

> Source: `docs/performance/2026-11-13-webui-live-performance-inspection.md`. Single-run `Invoke-WebRequest` timings against `http://127.0.0.1:7070` (PID 4352 at that time). **Not current-session measurements; rerun required for before/after comparison.**

| Endpoint | Result | Time (ms) | Size (B) | Classification |
|---|---:|---:|---:|---|
| `/api/market/quotes` | timeout | 20063 | - | **P0 severe** |
| `/api/alerts` | 200 | 5883 | 1391 | **P0 severe** |
| `/api/news` | 200 | 9001 | 37052 | **P0 severe** |
| `/api/fno/coverage` | 200 | 3697 | 159773 | **P0 high** |
| `/api/fno/catalog` | 200 | 3934 | 378 | **P0 high** |
| `/api/futures/heatmap?expiry=latest&buildup=all` | 200 | 3408 | 165573 | **P0 high** |
| `/api/market/breadth?index=NIFTY50` | 200 | 682 | 13226 | **P1 moderate** |
| `/api/options/chain/view?underlying=NIFTY&window=10` | 200 | 620 | 23007 | **P1 moderate** |
| `/api/market/fno/universe` | 200 | 213–3735 | 3354–58123 | **P1 variable** |
| `/api/market/sector-heatmap?index=NIFTY50` | 200 | 120 | 16401 | fast/ok |
| `/api/market/map?index=NIFTY50` | 200 | 32 | 52964 | fast |
| `/api/market/scanners` | 200 | 7 | 2192 | fast |
| `/api/market/scanner/gainers?universe=FNO&limit=5` | 200 | 11 | 1760 | fast |
| `/api/alerts/history?limit=20` | 200 | 13 | 1803 | fast |
| `/api/news/sources` | 200 | 8–11 | 2601 | fast |
| `/api/ai-alerts` | 200 | 11 | 23 | fast |
| `/api/ai-alerts/events` | 200 | 42 | 70754 | moderate payload, fast |
| `/api/ai-alerts/consumers` | 200 | 32 | 33607 | moderate payload, fast |
| `/api/logs?limit=20` | 200 | 11 | 2782 | fast |
| `/api/diagnostics` | 200 | 38 | 1646 | fast |
| `/api/diagnostics/checks` | 200 | 9 | 4104 | fast |
| `/api/watchlists` | 200 | 18 | 17 | fast |
| `/api/subscriptions` | 200 | 11 | 1914 | fast |
| `/api/instruments/sync-state` | 200 | 107 | 192 | moderate |
| `/api/options/index-underlyings` | 200 | 9 | 245 | fast |
| `/api/options/expiries?underlying=NIFTY` | 200 | 16 | 282 | fast |

### Historical UI/asset samples (2026-11-13)
| URL | Result | Time (ms) | Size (B) |
|---|---:|---:|---:|
| `/ui/` | 200 / timeout sample observed | variable | 783 |
| `/ui/assets/index-CJsytrag.js` | 200 | 6015 | 326081 |
| `/ui/assets/index-ni0kl9kI.css` | 200 | 45 | 70537 |
| `/ui/assets/react-DviEPBuS.js` | 200 | 118 | 250002 |
| `/ui/assets/query-B0JyiKZ_.js` | 200 | 28 | 43367 |
| `/ui/assets/echarts-BBcsMa3e.js` | 200 | 553 | 1115921 |
| `/ui/assets/ChartView-BGB_DAs1.js` | 200 | 817 | 1613159 |

## 3. Current static/build baseline (2026-11-14, verified)

| Metric | Value | Evidence |
|---|---:|---|
| Initial HTML modulepreloads | `rolldown-runtime`, `query`, `react`, `echarts` | `frontend/dist/index.html` |
| Initial JS referenced by HTML (entry + modulepreloads) | 1,737,246 B (~1.66 MiB) uncompressed | dist asset sizes |
| `index` chunk | 326,558 B | `frontend/dist/assets/index-CJsytrag.js` |
| `react` chunk | 250,006 B | `react-DviEPBuS.js` |
| `query` chunk | 43,367 B | `query-B0JyiKZ_.js` |
| `echarts` chunk (preloaded globally) | 1,116,589 B | `echarts-BBcsMa3e.js` |
| `ChartView` lazy chunk | 1,613,661 B | `ChartView-BGB_DAs1.js` |
| CSS | 70,537 B | `index-ni0kl9kI.css` |
| Precompressed assets in dist (`.gz/.br/.zst`) | none found | dist listing |
| Lazy routes | only `/chart` | `frontend/src/routes/router.tsx` |
| Dashboard polling intervals | sectors 8s, breadth 8s, F&O universe 15s, movers 15s | dashboard widget components |
| Market quote flush interval | 1000 ms when changed/visible | `frontend/src/features/dashboard/constants.ts`, `useMarketQuotes.ts` |
| `test_source` enabled in config | false | `config.json` |

## 4. Reproducible measurement protocol (for rerun/before-after)

Use the **same machine, same branch/build, same backend data/config state, same browser/profile settings**, and record timestamps.

### 4.1 Backend/API
1. Start MarketHub in the target environment without modifying `config.json`, DB, credentials, or protected runtime behavior. Record PID, startup time, effective data dir, and whether `test_source`/news polling/background tasks are active.
2. Warm-up: hit `/ui/` and key APIs once (discard or label separately).
3. For each endpoint, run N>=20 sequential requests and N>=20 concurrent requests (e.g. 5-10 workers) capturing: status, TTFB, total time, payload bytes, errors/timeouts. Compute min/median/p95/p99/max.
4. Target endpoints: `/api/market/quotes`, `/api/alerts`, `/api/alerts/history?limit=20`, `/api/news`, `/api/news/sources`, `/api/fno/coverage`, `/api/fno/catalog`, `/api/futures/heatmap?expiry=latest&buildup=all`, `/api/market/breadth?index=NIFTY50`, `/api/market/sector-heatmap?index=NIFTY50`, `/api/market/map?index=NIFTY50`, `/api/market/scanners`, `/api/market/scanner/gainers?universe=FNO&limit=5`, `/api/market/fno/universe`, `/api/options/chain/view?underlying=NIFTY&window=10`, `/api/subscriptions`, `/api/watchlists`, `/api/logs?limit=20`, `/api/diagnostics`.
5. Capture Python CPU%, RSS/working set, thread count, and DB/query timings (if instrumentation available) during runs.

### 4.2 Frontend/UI
1. Build frontend (`cd frontend && bun run build`) and record chunk sizes/compressed transfer sizes.
2. Browser cold load: clear cache/storage, open `/ui#/dashboard`, capture DevTools Performance + Network (TTFB, FCP, LCP, INP proxy interactions, long tasks, JS transfer/execute).
3. Warm navigation: dashboard -> watchlists/alerts/chart/scanners/fno/option-chain/breadth/sector-heatmap/market-map/futures-heatmap/news/sentiment/settings/logs/diagnostics; record route transition and data-ready times.
4. Live session: 10-15 min on dashboard and heavy pages; capture memory snapshots, long tasks, SSE message counts/rates, request counts.
5. Record browser version, hardware acceleration state, and tab/process attribution.

### 4.3 SSE/realtime
1. Measure `/api/market/stream` and `/api/logs/stream` connection count, message rate, payload size, reconnects, and dropped/lag behavior.
2. Correlate tick bursts with Python CPU, event-loop lag (if instrumented), and UI long tasks.

## 5. Comparison template (before/after)

| Scenario | Metric | Before | After | Delta | Notes |
|---|---|---:|---:|---:|---|
| `/api/market/quotes` | median / p95 / timeout % |  |  |  |  |
| `/api/alerts` | median / p95 |  |  |  |  |
| `/api/news` | median / p95 |  |  |  |  |
| `/api/fno/coverage` | median / p95 |  |  |  |  |
| `/api/fno/catalog` | median / p95 |  |  |  |  |
| `/api/futures/heatmap` | median / p95 |  |  |  |  |
| Dashboard cold load | FCP / LCP / long tasks |  |  |  |  |
| Initial JS transfer | compressed bytes |  |  |  |  |
| Dashboard request count (first 30s) | count |  |  |  |  |
| SSE market | msgs/s, reconnects |  |  |  |  |

**Note:** Do not claim millisecond-level improvement without rerunning this protocol under comparable conditions.