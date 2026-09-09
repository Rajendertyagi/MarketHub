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
| Routing | **HashRouter** | `StaticFiles` at `/ui` has no SPA fallback; HashRouter needs zero changes to frozen `app/server.py`. |
| Production serving | `bun run build` → `web/ui/` (index.html + assets) served by existing Python `/ui` mount | No separate Node/Vite production server; no backend runtime change. |
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
bun run build      # tsc --noEmit + vite build -> ../web/ui
bun run test       # vitest run
```

Production: run `bun run build`, then start the MarketHub Python server — the
built app is served at `/ui`.

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
when the full React app replaces `/ui` at the final step.
