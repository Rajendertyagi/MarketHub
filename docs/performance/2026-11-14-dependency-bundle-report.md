# MarketHub — Dependency & Bundle-Size Report (2026-11-14)

**Date:** 2026-11-14  
**Branch / HEAD:** `develop` @ `08b3071f43b8d0875d3b2ada080cd668a37aaa56`  
**Scope:** Read-only inspection of `frontend/package.json`, `frontend/dist/*`, `frontend/vite.config.ts`, `frontend/src/routes/router.tsx`, `pyproject.toml`, `requirements.txt`, and usage evidence from codebase audit. No builds or dependency installs were run in this session.

## 1. Frontend bundle/build report (verified from `frontend/dist`)

### 1.1 Dist assets (uncompressed)

| File | Bytes | Role | Initial load? |
|---|---:|---|---|
| `index.html` | 783 | Entry HTML | Yes |
| `assets/index-CJsytrag.js` | 326,558 | App entry chunk | Yes (`<script type="module">`) |
| `assets/rolldown-runtime-XKyaHtl9.js` | 726 | Runtime chunk | Yes (modulepreload) |
| `assets/query-B0JyiKZ_.js` | 43,367 | TanStack Query vendor chunk | Yes (modulepreload) |
| `assets/react-DviEPBuS.js` | 250,006 | React/router vendor chunk | Yes (modulepreload) |
| `assets/echarts-BBcsMa3e.js` | 1,116,589 | ECharts/zrender vendor chunk | **Yes (modulepreload, global)** |
| `assets/index-ni0kl9kI.css` | 70,537 | App CSS | Yes |
| `assets/ChartView-BGB_DAs1.js` | 1,613,661 | Lazy chart route chunk | No (route-loaded) |
| `assets/index-CJsytrag.js.map` | 1,353,026 | Sourcemap | Not requested by HTML |
| `assets/query-B0JyiKZ_.js.map` | 269,233 | Sourcemap | Not requested by HTML |
| `assets/react-DviEPBuS.js.map` | 1,459,807 | Sourcemap | Not requested by HTML |
| `assets/echarts-BBcsMa3e.js.map` | 6,501,402 | Sourcemap | Not requested by HTML |
| `assets/ChartView-BGB_DAs1.js.map` | 5,097,674 | Sourcemap | Not requested by HTML |

**Totals (uncompressed):**
- **Initial JS referenced by `index.html` (entry + modulepreloads):** 1,737,246 B (~1.66 MiB)
- **Initial CSS:** 70,537 B (~68.9 KiB)
- **Lazy chart JS:** 1,613,661 B (~1.54 MiB)
- **All JS/CSS assets:** 3,421,444 B (~3.26 MiB)
- **Sourcemaps:** 14,681,142 B (~14.0 MiB) present in dist (not initial payload unless fetched)
- **Precompressed assets (`.gz/.br/.zst`):** none found in `frontend/dist`

### 1.2 Build/chunking observations
- **[V]** `frontend/dist/index.html` modulepreloads `echarts` globally, even though chart is the only lazy route.
- **[V]** `frontend/vite.config.ts` uses Rolldown `manualChunks` to split `echarts/zrender`, `react/react-dom/react-router*`, and `@tanstack/*` from `node_modules`; no app-feature chunking rules.
- **[V]** `frontend/src/routes/router.tsx` eagerly imports almost all feature views; only `ChartView` is lazy-loaded.
- **[V]** `sourcemap: true` in Vite build config; sourcemaps are emitted to dist.
- **[U]** Runtime transfer size depends on server/CDN compression (not observed in dist artifacts).

### 1.3 Bundle-size risks (prioritized)
| Risk | Evidence | Impact | Notes |
|---|---|---|---|
| Global echarts preload | `index.html` modulepreload + 1.12 MiB chunk | High initial parse/network/compile cost on non-chart routes | Highest frontend bundle win |
| Eager route graph | Most views statically imported in router | Larger initial app chunk / delayed readiness | Lazy-load heavy routes |
| Large chart stack | `ChartView` chunk 1.61 MiB + Vela/ECharts deps | High cost when chart opened | Acceptable if isolated and on-demand |
| No precompressed dist artifacts | No `.gz/.br` files | Transfer efficiency unknown/suboptimal if server compression absent | Verify runtime compression |
| Sourcemaps in dist | ~14 MiB maps | Storage/deploy noise; not user payload unless exposed | Keep out of prod artifacts if not needed |

## 2. Frontend dependencies (`frontend/package.json`)

### 2.1 Runtime dependencies
| Package | Version | Observed purpose/use | Audit note |
|---|---|---|---|
| `@luxalgo/vela` | `0.10.2` | Chart workspace/provider/UI (`features/chart/*`) | Heavy; keep lazy/on chart route only |
| `@tanstack/react-query` | `^5.107.1` | Server-state fetching/caching/polling across features | Core; review polling/refetch behavior |
| `echarts` | `^6.1.0` | Charts/treemaps/options (`components/EChart.tsx`, breadth/sector/fno/market-map option builders) | Heavy; currently preloaded globally in build output |
| `react` | `^19.3.1` | UI runtime | Core |
| `react-dom` | `^19.3.1` | DOM renderer | Core |
| `react-router-dom` | `^7.18.5` | Hash routing/navigation | Core |
| `zod` | `^4.6.7` | API/schema validation (`api/schemas`, feature schemas) | Core for contract safety |

### 2.2 Dev dependencies
| Package | Version | Purpose | Audit note |
|---|---|---|---|
| `@biomejs/biome` | `^2.6.6` | Lint/format | OK |
| `@playwright/test` | `^1.65.0` | E2E/smoke tests | Useful for perf regression checks |
| `@testing-library/jest-dom` | `^7.0.1` | DOM assertions | OK |
| `@testing-library/react` | `^16.3.3` | Component tests | OK |
| `@testing-library/user-event` | `^14.6.8` | Interaction tests | OK |
| `@types/react` | `^19.3.0` | Types | OK |
| `@types/react-dom` | `^19.3.0` | Types | OK |
| `@vitejs/plugin-react` | `^6.3.0` | Vite React integration | OK |
| `happy-dom` | `^21.0.4` | Test DOM env | OK |
| `typescript` | `^7.0.2` | Type checking | OK |
| `vite` | `^8.3.7` | Build/dev server | OK |
| `vitest` | `^5.1.0` | Unit/component tests | OK |

**Unused frontend dependency findings:** **None confirmed** in this static audit. A definitive unused-dep pass requires import graph + production bundle analysis + test/build verification.

## 3. Backend/Python dependencies

### 3.1 Declared dependencies (`pyproject.toml` / `requirements.txt`)

| Package | Declared in | Purpose (evidenced) | Audit note |
|---|---|---|---|
| `mcp>=2.0.0,<3.0.0` | both | MCP server/SDK integration (`mcp_server/*`, `app/server.py`) | Core |
| `starlette>=0.48.0,<2.0.0` | both | HTTP app/routes/SSE responses | Core |
| `uvicorn>=0.31.1,<1.0.0` | both | ASGI server runtime | Core |
| `sse-starlette>=3.0.0,<4.0.0` | both | `EventSourceResponse` for SSE streams | Core |
| `protobuf>=6.31.1,<7` | requirements | Upstox vendored proto bindings runtime | Broker/feed dependency; protected-adjacent usage |
| `websockets>=17.0,<18.0` | requirements | Upstox WebSocket feed adapter | Broker/feed dependency |
| `httpx>=0.27.0,<1.0.0` | requirements | Fyers TBT dynamic WS URL fetch / HTTP clients | Used in broker/news paths as applicable |
| `feedparser>=6.0.0,<7.0.0` | requirements | RSS/Atom parsing for news | News dependency |
| `cryptography>=44.0,<51` | requirements | Fernet encryption for secrets store | **Protected/security-critical** |
| `yfinance>=0.2.54,<2.0` | both | Yahoo Finance AI research capability (`yfinance_service/*`, MCP Yahoo tools) | **Used, not removable** without feature removal |
| `pytest>=8.0.0` | requirements | Tests | Dev/test |
| `pytest-asyncio>=1.0.0` | requirements | Async tests | Dev/test |

### 3.2 Dependency observations
- **[V]** `yfinance` is intentionally used (server init, MCP services/tools/registry/contract, tests/docs); not bloat.
- **[V]** `cryptography` is security-sensitive and tied to credential storage (protected boundary).
- **[V]** Broker/feed dependencies (`protobuf`, `websockets`, `httpx`) support protected broker/feed functionality; removal/swap is out of scope for perf cleanup.
- **[S]** Transitive dependency cost (especially `yfinance` -> pandas/numpy/requests stack) may affect install/startup/memory footprint, but no runtime profiling was performed.

## 4. Recommendations (evidence-backed)

1. **Frontend (high impact):** remove global `echarts` modulepreload from initial HTML path and lazy-load non-chart routes/features; re-measure initial JS transfer/parse.
2. **Frontend (medium):** review `manualChunks` and route-level splitting to keep dashboard shell minimal; consider separate chunks for heavy feature groups.
3. **Build hygiene:** verify runtime compression (gzip/brotli) and avoid shipping sourcemaps in production artifacts unless required.
4. **Dependencies:** no removals recommended without proof; keep `yfinance` and broker/security deps as-is unless product scope changes.
5. **Measurement:** capture compressed network transfer sizes and JS execution/long-task metrics in browser traces before/after bundle changes.