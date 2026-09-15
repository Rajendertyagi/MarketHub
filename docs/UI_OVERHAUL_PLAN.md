# MarketHub Web UI Overhaul — Plan & Progress

> Living document. Updated after each phase. Scope: **WEBUI SHELL only**
> (see `docs/ARCHITECTURE_BOUNDARIES.md` §WEBUI SHELL, lines 31/266/319/394).
> **Frontend-only.** No P&L / positions / orders (frozen broker/auth zone).
> `test/test_architecture_boundaries.py` MUST stay GREEN after every phase.
> Build/verify: `bun run build` (npm not on PATH; bun is the verified tool) and
> `pytest test/test_architecture_boundaries.py`. App: `http://localhost:7070/ui`.

## Goal
Make the web UI modern, compact, and trader-relevant. Demote Chat from primary
nav. Reorganize and polish existing data; do **not** invent brokerage features.

## Design principles
- Dark-first OKLCH palette already in `tokens.css`; keep single source of truth.
- 13px base / 26–32px controls; tight but legible.
- Numerics: `font-variant-numeric: tabular-nums` + right-align in tables.
- Restrained `--accent`; semantic `--pos`/`--neg` for up/down.
- One table primitive (`.table` + `.table-compact`); no per-view table forks.
- Topbar = command center: session, market status, alerts, navigation.

## Phase status

| Phase | Name | Status |
|-------|------|--------|
| 0 | Baseline snapshot | DONE |
| 1 | Design-system cleanup (tokens, tables, icons, numerics) | DONE |
| 2 | Information architecture / nav regroup + landing | DONE |
| 3 | Dashboard command-center | DONE |
| 4 | Per-view polish (watchlists, alerts, charts, breadth, heatmap, map, settings, logs, diagnostics) | DONE |
| 5 | Responsive + final verification | DONE |
| 5 | Responsive + final verification | PENDING |

## Phase 0 — Baseline snapshot (DONE)
- CSS already layered: `index.css` imports `tokens, base, layout, components,
  utilities` + `features/*.css`. Layer order:
  `@layer tokens,base,layout,components,features,utilities;`
- Prior fixes already applied: `.panel` surface (components.css:89),
  `.settings-content` de-elevated, Markets dropdown overflow fixed
  (`.app-nav` overflow removed in layout.css).
- Known debt (targets for Phase 1):
  - `features/fno.css`: hardcoded px (gap:6px :8, margin-bottom:12px :9/38,
    gap:16px :14, font-size:18px :17, margin-top:12px :39, gap:12px :35,
    margin-left:6px :58).
  - `features/chat.css:27`: `color:#fff` hardcoded.
  - `components.css`: two table defs — `.table` (:120) and `.data-table` (:474).
  - `components/Icon.tsx`: only home/sun/moon/chevron-down.
  - No shared numeric/tabular utility.

## Phase 1 — Design-system cleanup (DONE)
- [x] Tokenize `features/fno.css` px → `--space-*` / `--font-size-xl`.
- [x] Fix `features/chat.css:27` `color:#fff` → `var(--text-inverse)`.
- [x] Unify `.table` (components.css:120) + `.data-table` (:474) into one
      primitive + `.table-compact`; route both class names to it.
- [x] Expand `components/Icon.tsx` with: arrow-up, arrow-down, star, bell,
      live-dot, search, settings, plus, refresh (SVG paths).
- [x] Add `utilities.css`: `.num` (tabular-nums + right-align), `.tnum`,
      `.text-muted` global utilities.
- [x] Verify `bun run build` clean (28.65 kB CSS) + architecture test 15/15 green.

## Phase 2 — Information architecture / nav (DONE)
- [x] Regroup `layouts/nav.ts`: My [Dashboard, Watchlists, Alerts]; Markets ▾
      [Market Map, Breadth, Sector Heatmap, Instruments]; Analyze [Charts,
      Scanners, F&O, Option Chain]; Insights [News, Sentiment, AI Alerts];
      More ▾ [Chat, Subscriptions, Test Center, Settings]. Chat demoted.
- [x] `routes/router.tsx`: `index` and catch-all `*` → `/dashboard`.
- [x] `StatusBar.tsx`: added inferred market session ("Open/Closed (inferred)")
      + alerts count (reuse `useAlerts`). Moved `inferredMarketOpen` →
      `utils/market.ts` (self-contained IST constants); `dashboard/format.ts`
      re-exports it, `MarketStatus.tsx` imports from utils.
- [x] Added `.status-item` flex utility in `layout.css`.
- [x] Verify `bun run build` clean + architecture test 15/15 green.

## Phase 3 — Dashboard command-center (PENDING)
- [ ] `DashboardView.tsx`: market-status hero + watchlist-ticker + breadth
      summary + movers + heatmap/map previews in a compact grid.
- [ ] `MoversTable.tsx`: expand from 2/2/2 → configurable (default 5/5/5).
- [ ] `MarketCards.tsx` / `MarketStatus.tsx`: live status chips, tabular nums.
- [ ] `QuoteDrawer.tsx`: compact quote popover on row click.
- [ ] Verify in browser; architecture test green.

## Phase 4 — Per-view polish (DONE)
Delegated to 5 parallel agents (disjoint feature files + own feature CSS; no
shared-CSS edits; architecture test kept green).
- [x] Watchlists: compact `.table-compact` + `.num` numerics, symbol filter toolbar, watchlist picker.
- [x] Alerts: `AlertForm` instrument-token input now datalist-backed (static
      `NSE_SYMBOLS`, no broker calls); panels/tables compact; `.num` numerics.
- [x] AI Alerts: `.panel`/`.panel-header` layout, compact scrollable tables.
- [x] Charts: `.toolbar` + bounded `.chart-wrap`; Scanners: `.toolbar` + `.table-compact`;
      F&O: `.toolbar` + `.fno-table-card` (option-chain ladder untouched).
- [x] Breadth: `.panel` + stat cards + `.table-compact`/`.num` + legend; Sector
      Heatmap & Market Map: `color-mix` magnitude tiles + shared legend + responsive.
- [x] Renamed nav Markets ▾ entries to **Sector Analysis** (`/sector-heatmap`) and
      **Heatmap** (`/market-map`) to match the research360 references. Sector
      Analysis gained a **Sector Performance** table (avg chg%, adv/dec/unch,
      constituents, top gainer/loser) from `SectorRow` + sector filter. Heatmap
      gained a **Metric** toggle (Price % / Volume) via `volumeTileColor` +
      volume-intensity normalization + switchable legend; keeps universe/size/
      sector/movement/search filters. Both use ECharts treemaps (existing).
- [x] News/Sentiment/Chat: compact `.panel` cards, tabular numerics, bounded
      chat composer/messages scroll.
- [x] Settings/Logs/Diagnostics/Subscriptions/Instruments/MCP tools/Analytics:
      `.panel`/`.table-compact`/`.num`/`.toolbar` polish, responsive media queries.
- [x] Created missing feature CSS files (breadth, charts, market-map, scanners,
      sector-heatmap, watchlists, instruments, subscriptions, analytics) + wired
      `@import`s in `index.css` so agents never touched shared CSS.
- [x] Final: `bun run build` clean; `pytest test/test_architecture_boundaries.py` 15/15 green.

## Phase 5 — Responsive + verification (DONE)
- [x] ≤860px: Dashboard `dash-grid` collapses to 1 column; nav keeps
      `overflow-x:auto` scroll (verified at 820px); per-view media queries stack
      controls/tables.
- [x] Density verified at 820px (narrow) and 1920px; tabular numerics + compact
      rows hold.
- [x] Final: `bun run build` clean (CSS ~29kB) + `pytest test/test_architecture_boundaries.py` 15/15 green.
- [x] Added overhaul notes to `docs/FRONTEND_MIGRATION.md`.

## File map
- `frontend/src/styles/index.css` — layer entry
- `frontend/src/styles/{tokens,base,layout,components,utilities}.css`
- `frontend/src/styles/features/*.css`
- `frontend/src/layouts/{nav.ts,AppShell.tsx,StatusBar.tsx}`
- `frontend/src/routes/router.tsx`
- `frontend/src/features/dashboard/*`
- `frontend/src/features/watchlists/*`
- `frontend/src/features/alerts/*`
- `frontend/src/components/{Icon.tsx,ui.tsx,EChart.tsx}`
- `docs/ARCHITECTURE_BOUNDARIES.md`, `docs/FRONTEND_MIGRATION.md`
- `test/test_architecture_boundaries.py`
