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
- **Status:** the running server already mounts `/ui` from `frontend/dist` (cutover
  effectively live). A **read-only** `/legacy` mount (`web/ui`) was added
  alongside it purely as a side-by-side comparison aid — it does not delete or
  alter the legacy files and is not used in production. Remove the `/legacy`
  Mount from `app/server.py` once comparison is done.

`/ui` serves the React build; `/legacy` serves the legacy plain-JS app. Open
both in separate tabs to diff screens.

## Status legend

`LEGACY` (only legacy JS exists) · `IN_PROGRESS` · `REACT` (built) ·
`VERIFIED` (built + browser-verified)

## Screen tracking

| Screen | Status | Backend deps | SSE deps | Notes |
|---|---|---|---|---|
| Shell / Theme | REACT | — | — | AppShell, dark/light, HashRouter |
| Charts | REACT | `GET /api/market/history` | — | candlestick+volume+SMA20/50, chronological normalization |
| Scanners | REACT | `GET /api/market/scanners`, `GET /api/market/scanner/{name}` | — | metadata-driven controls, IV fraction→% |
| Market Map | REACT | `GET /api/market/map` | — | treemap; tile → Charts preserves exact identity |
| Sector Heatmap | REACT | `GET /api/market/sector-heatmap` | — | treemap; Unclassified preserved |
| Breadth | REACT | `GET /api/market/breadth` | — | stat grid + bar + table |
| F&O Workspace | VERIFIED | `GET /api/market/fno/universe`, `GET /api/market/fno/stock/{symbol}`, `POST /api/market/fno/view` | — | unified equity+index picker; index underlyings consumed from backend (`GET /api/options/index-underlyings`, no front-end hard-code); bounded active-view subscription (reuses owner); futures/option ladder; IV fraction→%; Option Chain also exposed as a dedicated `/option-chain` route (deep-links to the chain tab) |
| Option Chain | VERIFIED | `GET /api/options/chain/view`, `GET /api/futures` | — | CE/PE ladder shared model; ATM; greeks; OI analytics (PCR/straddle) EChart; exact identity → Charts |
| Instruments | VERIFIED | `GET /api/instruments/segments`, `PUT /api/instruments/segments`, `POST /api/instruments/sync`, `GET /api/instruments/sync-state` | — | catalog/source status; segment preferences (saved, not defaults); Save & Re-sync; backend owns master sync + Fyers segment semantics |
| Subscriptions | VERIFIED | `GET /api/subscriptions`, `PATCH /api/subscriptions/indices`, `POST/PATCH/DELETE /api/subscriptions/stocks`, `PUT/DELETE /api/subscriptions/rules`, `POST /api/subscriptions/apply` | — | DB-backed 8 canonical indices (backend-owned, never redefined in UI); stocks; derivative rules (current/next expiry, ATM range, CE/PE); Save & Apply reconciles live feed without restart |
| Settings | REACT | `GET/POST /api/settings/app`, `POST /api/admin/backup`, `GET/POST /api/settings/upstox[/*]`, `GET/POST /api/settings/fyers[/*]`, `GET/POST/DELETE /api/news/sources[/*]`, `GET/POST /api/sources/{name}/{action}`, `GET/POST /api/chat/config`+`/status` | — | panels: General, Brokers (Upstox+Fyers login UI), News Sources, Market Sources, AI/MCP, Backup. Data Retention / Logging / Alerts panels are informational (no backend endpoints). Frozen-zone UI migrated via authorized override; no auth/credential/startup logic changed. |
| Dashboard | REACT | `GET /api/market/stream` (SSE), `GET /api/market/quotes` | — | ticker strip + market cards + movers + markets table + inferred status + filter; live via shared `SSEManager` (named `quote` event + `reset`); no client-side price/state computation |
| Watchlists | REACT | `GET/POST /api/watchlists`, `PATCH/DELETE /api/watchlists/{id}`, `DELETE /api/watchlists/items/{id}`, `export/import` | — | picker + item table with live values resolved from the market quote stream; CRUD + export/import |
| Test Center | REACT | `GET /api/diagnostics`, `GET /api/diagnostics/checks`, `POST /api/diagnostics/run?mode=quick\|full&symbol=` | — | diagnostics + checks list + run results; backend owns all evaluation; React renders only |
| News | REACT | `GET /api/news`, `GET /api/news/sentiment`, `POST /api/news/refresh`, `GET/POST/DELETE /api/news/sources[/*]` | — | 3-column RSS reader: Sources+filters (left) · Article list w/ sentiment chips + keyboard nav (middle) · Reader (right); new-arrival pill; "Manage Sources" modal; backend-computed sentiment only. 2 `news.test.tsx` cases outstanding (filter query + source toggle) |
| Sentiment | REACT | `GET /api/news/sentiment`, `POST /api/news/refresh` | — | standalone `/sentiment` dashboard (overall sentiment, bull/bear/neutral distribution, source/category breakdowns, recent items) + per-article `SentimentPanel` inside News; backend-computed |
| Alerts | REACT | `GET /api/alerts`, `POST /api/alerts`, `DELETE /api/alerts/{id}`, `POST /api/alerts/{id}/rearm`, `POST /api/alerts/{id}/enabled`, `GET /api/alerts/history` | — | alert form + table + notifications + history; `enabled` normalized from SQLite 1/0; no client-side evaluation |
| AI Alerts | REACT | `GET /api/ai-alerts`, `GET /api/ai-alerts/events`, `GET /api/ai-alerts/consumers` | — | consumer cards + active alerts + triggered events; observability only |
| Sources / Market Sources | REACT | `GET /api/sources/status`, `GET/POST /api/sources/{name}/{action}`, `GET/POST/DELETE /api/news/sources[/*]` | — | status + start/stop/restart + news-source management; migrated under Settings panels |
| Logs | REACT | `GET /api/logs`, `GET /api/logs/stream` (SSE) | — | snapshot table + SSE live stream via shared `SSEManager`; records produced server-side |
| MCP Tools | REACT | `GET /api/mcp/tools` | — | tools table grouped by category; list-only |
| Auth | REACT | `GET /api/auth/upstox/status`, `POST /api/auth/upstox/pin`, `POST /api/auth/upstox/token`, `GET /api/auth/upstox/login` (OAuth redirect), `DELETE /api/auth/upstox/session`, Fyers equivalents | — | login UI only (Brokers panel); no credential/token/startup logic changed |
| Chat | REACT | `GET /api/chat/status`, `POST /api/chat` (SSE stream of `tool_start`/`delta`/`error`/`done` events) | — | message composer + streamed assistant responses; conversation history kept client-side; AI provider config remains in Settings → AI Provider |

## Recommended next migration batch

1. ~~**F&O Workspace** → Option Chain~~ — migrated (React `/fno`)
2. ~~**Subscriptions / Instruments**~~ — migrated (React `/subscriptions`, `/instruments`); DB-backed prefs + segment preferences, no legacy parsing in UI
3. ~~**News / Sentiment**~~ — migrated (React `/news`); 2 `news.test.tsx` cases outstanding
4. ~~**Settings / Test Center / Auth / Sources**~~ — migrated (React `/settings`); frozen-zone UI lifted via authorized override; no auth/credential/startup logic changed
5. ~~**Alerts / AI Alerts**~~ — migrated (React `/alerts`, `/ai-alerts`)
6. ~~**Logs / MCP Tools**~~ — migrated (React `/logs`, `/mcp`)
7. ~~**Dashboard / Markets / Watchlists**~~ — migrated (React `/dashboard`, `/watchlists`); live via shared `SSEManager`
7. **final legacy frontend removal** (`web/ui/js`, `web/ui/css`) once parity verified + `/ui` cutover approved
8. ~~**Chat**~~ — migrated (React `/chat`)

### Market analytics migration notes (Breadth / Sector Heatmap / Market Map)

Migrated in one batch. All three consume the **existing** canonical REST
endpoints and render only; no aggregation/breadth math, sector classification, or
market-map weighting is computed in React.

- **Breadth** (`GET /api/market/breadth`): summary stat grid (advances/declines/
  unchanged/unavailable/eligible/quoted/unclassified, A/D ratio, net advances,
  advance/decline %), a pure ECharts stacked bar, and a filterable/sortable
  constituent table.
- **Sector Heatmap** (`GET /api/market/sector-heatmap`): finviz-style ECharts
  treemap; one group per canonical sector, leaves colored by the backend Change %
  only; `Unclassified` preserved explicitly; unavailable stocks get a gray tile.
- **Market Map** (`GET /api/market/map`): finviz-style ECharts treemap; sectors as
  groups, stocks as leaves sized by equal area (or bounded volume weight) and
  colored by Change %; clicking a tile navigates to Charts preserving the exact
  instrument identity (never substituting futures for cash).
- **Analytics coverage**: all three reuse the existing `/market/analytics/coverage`
  owner (request on universe change, clear on unmount) — best-effort, non-fatal.
  No subscription architecture change, no direct broker calls from React.
- **Refresh**: TanStack Query with a 5s `refetchInterval` (no manual timers, no
  overlapping requests); cancellation via the query `signal`.
- **Universe set**: the single canonical `market_universe.UNIVERSE_NAMES`
  (FNO, NSE_EQ, NIFTY50, NIFTYNXT50, BANKNIFTY) — never a divergent frontend model.

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
