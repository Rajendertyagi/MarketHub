# MarketHub Frontend Migration Roadmap

**Goal:** Replace the legacy plain-JS WebUI (`web/ui/js`, `web/ui/css`) with a
single React + TypeScript + Vite + ECharts application served at **`/ui`**.

The backend (REST/SSE, MarketService, scanner engine, canonical models,
auth/startup) is **unchanged** and remains the source of truth. React is
presentation, interaction, routing, client lifecycle, and server-state
consumption only.

## Architecture decisions

| Concern | Decision | Why |
|---|---|---|
| Toolchain | Bun (package manager + runner + test) | Already available; no Node needed. |
| Build tool | Vite 5 | Build/dev tooling only. |
| Build output | `frontend/dist/` (isolated, gitignored) | Generated artifact is separate from tracked legacy `web/ui` source; a build never overwrites tracked files. |
| Routing | **HashRouter** | `StaticFiles` at `/ui` has no SPA fallback; HashRouter needs zero changes to frozen `app/server.py`. |
| Production serving | `bun run build` → `frontend/dist/`; the Python `/ui` Mount must point at `frontend/dist` (one-line protected change, pending approval) | No separate Node/Vite production server. Until that change lands, `/ui` still serves the legacy app. |
| Typed API | One `api/client.ts` (fetch wrapper) + Zod schemas | No scattered `fetch()`; predictable `ApiError` taxonomy. |
| Server state | TanStack Query | Dedupe, caching, cancellation, loading/error — no giant global client state. |
| Charts | Single `EChart` lifecycle wrapper | One `init`/`setOption`/`resize`/`dispose`; no leaked observers. |
| SSE | Single-owner `streams/SSEManager` | One `EventSource` per logical stream; native reconnect; cleanup on unmount. |

## Toolchain commands

```bash
cd frontend
bun install        # install dependencies
bun run dev        # Vite dev server (proxies /api -> MARKETHUB_API, default :7070)
bun run typecheck  # tsc --noEmit
bun run build      # tsc --noEmit + vite build -> frontend/dist (isolated, gitignored)
bun run test       # vitest run
```

Production: run `bun run build` (emits `frontend/dist`). The MarketHub Python
server's `/ui` Mount must be repointed from `web/ui` to `frontend/dist` (see
"CUTOVER" below) so the built React app is served at `/ui`. No separate
Vite/Node/Bun production server is required.

## Production `/ui` cutover (protected change — pending approval)

The existing server serves `/ui` via:

```python
# app/server.py (STARTUP/RUNTIME red-zone)
Mount("/ui", app=StaticFiles(directory=str(PROJECT_ROOT / "web" / "ui"), html=True), name="ui")
```

To serve the React build at `/ui` with the final architecture, the mount
directory must change from `web/ui` to `frontend/dist`:

```python
Mount("/ui", app=StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "dist"), html=True), name="ui")
```

- **Exact change:** one line, `app/server.py` line ~1280 (the `Mount("/ui", ...)` call).
- **Why necessary:** the existing `/ui` Mount root is the tracked legacy `web/ui`
  directory. The isolated React artifact lives in `frontend/dist`; StaticFiles
  serves the directory it is pointed at, so the mount directory must move.
- **Startup/auth risk:** low — `StaticFiles` is a pure static file server; this
  does not touch `app/auth/*`, credentials, broker lifecycle, or any composition
  logic. It only changes which directory `/ui` reads static files from.
- **Alternatives considered:** (a) build React into `web/ui` — rejected because
  it overwrites the tracked legacy `index.html`; (b) serve React at a subpath
  like `/ui/react` — rejected as a permanent hybrid; (c) a separate Vite/Node
  production server — rejected (task requires no separate server).
- **Status:** reported for explicit approval. NOT implemented in this task.

Until this change is applied, `/ui` continues to serve the legacy plain-JS app,
and the React app is available only via `bun run dev` (development).

## Status legend

`LEGACY` (only legacy JS exists) · `IN_PROGRESS` · `REACT` (built) ·
`VERIFIED` (built + browser-verified)

## Screen tracking

| Screen | Status | Backend deps | SSE deps | Notes |
|---|---|---|---|---|
| Shell / Theme | REACT | — | — | AppShell, dark/light, HashRouter |
| Charts | REACT | `GET /api/market/history` | — | candlestick+volume+SMA20/50, chronological normalization |
| Scanners | REACT | `GET /api/market/scanners`, `GET /api/market/scanner/{name}` | — | metadata-driven controls, IV fraction→% |
| Market Map | LEGACY | `GET /api/market/map` | — | next migration batch |
| Sector Heatmap | LEGACY | `GET /api/market/sector-heatmap` | — | |
| Breadth | LEGACY | `GET /api/market/breadth` | — | |
| F&O Workspace | LEGACY | `GET /api/options/*`, `/api/futures` | — | |
| Option Chain | LEGACY | `GET /api/options/chain` | — | |
| Instruments | LEGACY | `GET /api/instruments/search` | — | reused by Charts/Scanners resolve |
| Subscriptions | LEGACY | `GET /api/market/stream` (SSE) | yes | uses `streams/` owner |
| Settings | LEGACY | settings routes | — | |
| Test Center | LEGACY | diagnostics | — | |
| News | LEGACY | `GET /api/news/*` | — | |
| Sentiment | LEGACY | news sentiment | — | |
| Alerts | LEGACY | `GET /api/alerts` | — | |
| AI Alerts | LEGACY | `GET /api/ai-alerts` | — | |
| Sources / Market Sources | LEGACY | source status | — | |
| Logs | LEGACY | `GET /api/logs` | — | |
| MCP Tools | LEGACY | MCP surface | — | |
| Auth | LEGACY | auth routes | — | FROZEN — not part of React migration |

## Recommended next migration batch

1. **Market Map** (treemap, reuses instrument identity → Charts)
2. **Breadth**
3. **Sector Heatmap**
4. **F&O Workspace**
5. **Option Chain**
6. **Subscriptions / Instruments** (first real SSE consumer via `streams/`)
7. **News / Sentiment**
8. **Settings / Test Center**
9. **Alerts / AI Alerts**
10. **remaining views**
11. **final legacy frontend removal** (`web/ui/js`, `web/ui/css`) once parity verified

## Known blockers / debt (do NOT mix into migration)

- RELIANCE curated identity quirk (backend catalog).
- full-FNO Option IV catalog lookup performance.
- unrelated existing WIP (preserved as-is).
- technical indicators beyond SMA20/50.
- new market/news/alert features.

## Files eligible for removal after parity

Once a feature's React replacement is `VERIFIED`, its legacy
`web/ui/js/<feature>.js` and `web/ui/css/features/<feature>.css` may be deleted.
Do not delete until then. The legacy `web/ui/index.html` is superseded only
when the approved `/ui` Mount change (see CUTOVER) lands and the full React app
replaces `/ui` at the final step.

## Build/deploy boundary (verified)

- React source: `frontend/src/`
- Generated build: `frontend/dist/` (isolated, gitignored)
- `bun run build` writes ONLY to `frontend/dist/` and never touches tracked
  legacy `web/ui` files (verified across repeated builds).
- Generated artifacts are NOT committed; they are rebuilt at deploy.
- Legacy `web/ui` remains the current `/ui` source until the cutover is approved.
