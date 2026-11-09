# Futures Heatmap — Implementation Plan (living document)

> Updated after each phase. Tests are deliberately deferred to Phase 4;
> Phases 1–3 are verified by manual smoke checks instead.

## Goal
New **Futures Heatmap** page (Trendlyne-style tile wall) over MarketHub's own
canonical data. Tiles = stock futures with price %, OI %, buildup class.
Five tabs, metric toggle, expiry picker, click-through to the exact future
in the F&O workspace.

## Decisions (locked)
- Renderer: **ECharts treemap** (copy `sectorHeatmapOption.ts` pattern; no custom layout code).
- Buildup views: `all` (no filter), `long`, `short`, `shortcovering`, `longunwinding`.
- Compute location: **backend endpoint** (not client-side).
- Buildup authority: `classify_buildup` in `market/analytics/option_chain.py` — reused, never duplicated.
- Expiry: backend-owned from catalog `derivative_expiries` via `MarketIntel.futures_contracts`; `latest` = `expiries[0]`; unknown expiry rejected.
- Polling: 5s UI poll against a lazy TTL-cached snapshot (~20s, keyed on expiry+buildup+metric).
- Snapshot: `instrument_key` per tile, `as_of` = **build time**, per-tile `received_ts`, `data_status` live/stale from newest tile age.
- Empty ≠ stale: empty → eligible=0, valid, never labeled stale. Closed market → last data + STALE.
- Pct formulas: `effective_change` for price; provider `oi_change_percent` first, guarded fallback mirroring the locked rule.
- Tile click → `/fno?sym=&kind=&tab=futures&expiry=` (exact future; never equity substitution).
- No scraping of Trendlyne. No touch to: auth, brokers, MarketService semantics, MarketMap internals, persistence schema, startup, alerts, existing /fno UI.

## Phase tracker

- [x] **Phase 1 — Backend** (`market/futures_heatmap.py` + `api/futures_heatmap.py` + wiring)
      Manual smoke only. Status: DONE 2026-09-20 (builder smoke OK, route smoke OK,
      boundaries 15/15 green; tests deferred to Phase 4).
- [x] **Phase 2 — Frontend** (`features/futures-heatmap/`: option builder + view + nav/route)
      Manual browser check only. Status: DONE 2026-09-20 (tsc clean, biome clean,
      builder fixture OK, vite build OK; browser check = Phase 3).
- [ ] **Phase 3 — Perfect-check** (manual end-to-end pass: tabs, metric, expiry, click, stale, empty)
      Status: NOT STARTED.
- [ ] **Phase 4 — Tests late** (pytest endpoint + vitest builder/view + gates)
      Status: NOT STARTED.

## Phase log
- 2026-09-20: plan saved to disk; implementation starts at Phase 1.
- 2026-09-20: Phase 1 DONE. Files: `market/futures_heatmap.py` (new),
  `api/futures_heatmap.py` (new), `app/server.py` (+3 lines: import + route).
  Deviation: snapshot dataclasses live in `market/futures_heatmap.py` (mirrors
  `market/market_map.py`) instead of `market/models.py` — smaller protected-surface touch.
  Smoke: 4 buildup classes incl. derived-OI fallback, filters, expiry ok/404,
  buildup 400, empty-valid-live, stale wall, TTL cache reuse, response shape,
  route 200/400/404/503. Boundaries green. No tests yet (Phase 4).
- 2026-09-20: Phase 2 DONE. Files: `api/schemas.ts` + `api/market.ts`
  (getFuturesHeatmap) + `types/index.ts` (FuturesHeatTile/Snapshot) +
  `features/futures-heatmap/futuresHeatmapOption.ts` (ECharts treemap) +
  `FuturesHeatmapView.tsx` (tabs/metric/size/expiry/search, click to /fno) +
  nav.ts + router.tsx (one link/route). Plus small Phase-1 addition:
  `expiries` union on the snapshot (dropdown source). Fixes during phase:
  Tabs generic typing, dataclass field order (expiries default), `button`
  resets for new toggle/dropdown CSS classes.
  Known limitation (by design, not a bug): wall shows tiles only for futures
  with canonical quotes present; no subscription redesign — uncovered
  underlyings render unavailable, honestly. Nothing committed.
