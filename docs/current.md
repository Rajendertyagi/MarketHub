# MarketHub — Current State

_Last updated: 2026-09-21_

Living status document. Captures what is done, what is pending, key findings, and
the outstanding verification work. Update this file as items are closed.

---

## 1. Snapshot (as of this writing)

| Item | State |
|---|---|
| Backend server | **not running** (nothing listening on `:7070`) |
| Catalog DB schema | **v20 on disk** — the `v20 → v21` migration has **not** run yet |
| Git | **nothing committed** — HEAD is `3e99513`; 64 modified + 42 untracked files |
| Config | `config.json` intact (`port=7070`, `data_dir=data`) |
| Subscriptions | 9 indices / 2 derivative rules (`NIFTY`, `BANKNIFTY`) — unchanged throughout |

> Restarting the server applies the `v20 → v21` migration (builds two indexes over
> ~130k rows, one-off, a few seconds) and loads the F&O performance fixes.

---

## 2. Completed work

### 2.1 Broker-neutral F&O foundation (Phases 1–7)
- Canonical 6-index registry (`market/index_registry.py`) + `NIFTYNXT50`/`NIFTYFPI` fixes.
- Provider registry (`providers/`) with adapter-declared capabilities/capacity.
- Canonical F&O universe (`market/derivatives_universe.py`), bulk `list_future_contracts`,
  `fno_universe` read model.
- Schema v19 `fno_config`; coverage/routing (`app/fno_coverage.py`, `api/fno_coverage_routes.py`);
  unified Market Data page.
- Schema v20 `futures_oi_snapshot` + `PreviousOiRecorder`.
- `market/futures_board.py` (TTL cache + single-flight) and the rewritten
  `api/futures_heatmap.py` over board + coverage.

### 2.2 Phase 8 — Trendlyne-style Futures Board
- Full-page uniform card grid (native React + CSS grid, no library).
- `FuturesHeatmapView.tsx`, `FuturesBoardGrid.tsx`, `components/HeatmapTile.tsx`,
  `styles/heatmap.css`, `styles/features/futures-heatmap.css`.
- Filters kept (Grid/Table, buildup, Price %/OI %, expiry, coverage, sort, search);
  collapsible coverage panel; honest `NO DATA` tiles; native `title` tooltips.
- ECharts treemap retired (`futuresHeatmapOption.ts` deleted).
- **Status: built and live-verified; awaiting formal approval.**

### 2.3 Frontend mutation-loop defect (FIXED)
- `FnoWorkspaceView.tsx` depended on the whole `useMutation` result object, which is a
  **new object every render** (`@tanstack/react-query@5.102.8`, `useMutation.js:17-21`),
  causing an infinite `POST /api/market/fno/view` loop (~1,500 req/s measured;
  187,813 browser console entries; `net::ERR_INSUFFICIENT_RESOURCES`).
- Fix: depend on the **stable `mutate` callback** (`activeView.mutate`), keeping the
  result object only for `isPending`.
- Permanent regression test in `frontend/src/__tests__/FnoWorkspaceView.test.tsx`
  ("does not loop the active-view mutation while params stay unchanged").
  Observed: `idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`.

### 2.4 F&O performance defects (FIXED)
Confirmed by profiling (py-spy, 25 s / 7,337 samples) and `EXPLAIN QUERY PLAN`:

- **Index-correct access.** F&O callers now supply `exchange` (leading column of the
  existing `idx_instr_lookup`). Catalog semantics verified first: `exchange` is the base
  exchange (`NSE`/`BSE`/`MCX`), `segment` is finer; the canonical F&O universe is
  explicitly NSE-scoped.
- **Workspace consumes the canonical read model.** `workspace_contracts` / `resolve` no
  longer issue per-underlying / per-expiry `search()` calls.
- **Cache/coalescing at the canonical boundary** (`market/derivatives_universe.py`):
  bulk futures index + expiries + underlying enumeration, keyed by **catalog epoch**,
  invalidated from the catalog-write boundary (`replace_provider_instruments` →
  `_bump_catalog_epoch`), single-flight per key (pattern adapted from
  `market/futures_board.py`). Uncached when no epoch exists (test doubles).

**Measured (one `workspace_contracts`, warm DB copy):**

| Metric | Before | After |
|---|---|---|
| latency | 0.277 s | **0.013 s** (~21×) |
| catalog calls / request | 8 | **3** |
| `derivative_expiries` calls | 3 | **0** (memoized) |
| futures `search` calls | 2 | **0** (bulk index) |

**Live server:** CPU with F&O workspace + heatmap open **198% → 1–4% of one core**;
`/health` **4.8 s → 0.087 s**.

### 2.5 Remaining two read-path items (schema v21)
- `idx_instr_seg(segment, instrument_type, tradingsymbol)` →
  `equity_universe` **0.0770 s → 0.0008 s** (~96×, covering seek).
- `idx_instr_fo(exchange, instrument_type, underlying, expiry)` →
  `derivative_expiries` **0.0118 s → 0.0000 s** (covering seek).
  Root cause: `idx_instr_lookup` puts `underlying` **last**, so with `expiry IS NOT NULL`
  the planner scanned the whole NSE option range (115k entries).
- `stock_underlyings()` memoized per (provider, day, epoch) so `plan()` stops repeating
  the `fno_universe` aggregation (~0.09 s, aggregation-bound, not index-bound).
- Migration verified on a copy of the real DB: `20 → 21`, both indexes created and used,
  re-open idempotent.

---

## 3. Pending

### A. Immediate / blocking
1. **Server is down.** Restart to apply the v21 migration and load the perf fixes.
2. **Verification not yet run** — see §5 test prompt.

### B. Awaiting decision
3. **Phase 8 approval** — built and live-verified, not formally approved.
4. **Phase 9** — not started; scope never defined.
5. **Nothing is committed.** All work is uncommitted (64 modified, 42 untracked).
   No branch, commit, or PR has been created.

### C. Known issues found but NOT fixed
6. **Latent catalog inconsistency (decision needed).** Fyers rows store the *raw numeric*
   `segment` (`'10'`/`'11'`); Upstox stores `NSE_EQ`/`NSE_FO`. Consequences:
   - `equity_universe` (`segment='NSE_EQ'`) → 2,668 rows, **excludes all 3,187 Fyers equities**
   - `fno_universe` (`segment='NSE_FO'`) → **returns nothing for `provider='fyers'`**

   Pre-existing behaviour; fixing it changes results (spot-key resolution, F&O universe),
   so it was left alone deliberately.
7. **Pre-existing test failures, never triaged:** backend `10 failed / 38 errors`;
   frontend `8 failed files / 21 failed tests / 3 errors` (incl. `fnoFormat.test.ts`).
8. **Test-harness bugs (flagged, unfixed):**
   - `test/helpers/runner.py` records FAILs without failing pytest → runner-style tests
     must be run directly (`python test/<file>.py`).
   - `test/helpers/lifecycle.py` rewrites the real gitignored `config.json`
     (`port`, `data_dir`) and its restore does not always run.
9. **Pre-existing lint:** 2 × `I001` (import ordering) in `core/persistence/store.py` and
   `app/fno_coverage.py` — present at `HEAD`, unrelated to recent edits.

### D. Deliberately not done (with evidence)
10. **SQLite connection pooling** — measured saving is only **2.4 ms/call**
    (connect alone 0.0000 s; connect + 4 PRAGMAs 0.0008 s). It would also break 7 test
    files that `os.unlink()` DB files, because Windows blocks deletion of open handles.
    **Not recommended.**
11. **`fno_universe` aggregation cost (~0.09 s)** — aggregation-bound (self-JOIN + 4×
    `count(DISTINCT)` temp B-trees); an index does not fix it. Cached at the canonical
    boundary instead; the query itself is unchanged.

### E. Blocked externally
12. **Fyers live index HSM smoke test** (`if|nse_cm|26137`, `if|nse_cm|26013`) —
    market hours only; must not be marked passed/failed.

### F. Optional polish
13. Phase 8 toolbar wraps heavily at narrow widths (615px → ~396px tall). Acceptable at
    desktop; responsive tuning possible.

---

## 4. Architecture (preserved and enforced)

```
Instrument Catalog
      ↓
Canonical F&O Universe / read model      (market/derivatives_universe.py)
      ↓
Coverage + provider routing              (app/fno_coverage.py)
      ↓
SubscriptionService                      (app/subscriptions/service.py)
      ↓
Feeds
```

- Provider-specific key/token handling stays below the canonical contract layer.
- No second F&O universe implementation.
- API contracts, broker config, provider budgets, live subscriptions, polling intervals,
  MCP contracts and the Phase 8 heatmap UI are unchanged.

---

## 5. Verification prompt

> Validate the F&O performance work on `D:\Temp\MarketHub`. Do **not** change production
> code; report findings only.
>
> **1. Backend suites**
> ```
> python -m pytest test/test_fno_read_paths.py test/test_derivatives_universe.py test/test_fno_universe.py test/test_fno_coverage.py test/test_futures_board.py test/test_futures_heatmap_api.py test/test_md_subscriptions.py -q
> python -m pytest test/ -q
> ```
> Expect: focused set fully green; full suite **10 failed / 38 errors** baseline unchanged
> (record the passed count).
>
> **2. Migration v20 → v21**
> Copy `data/events.db` (+ `-wal`/`-shm`) to a temp dir, open with `EventStore`, then assert
> `PRAGMA user_version == 21`; indexes `idx_instr_fo`, `idx_instr_seg` exist; re-open is
> idempotent.
>
> **3. Query plans (read-only, real catalog)**
> `EXPLAIN QUERY PLAN` must show `idx_instr_fo` for `derivative_expiries` and
> `idx_instr_seg` for `equity_universe`, with **no `SCAN instruments`**. Re-measure both.
>
> **4. Before/after workspace latency**
> Against a warm DB copy, compare the pre-fix call pattern (no `exchange`, per-expiry
> futures `search`) with `SubscriptionService.workspace_contracts("HDFCBANK")`. Report
> latency, catalog calls/request, `derivative_expiries` calls.
> Expected ≈ **20× faster, 8 → 3 calls, 0 duplicate expiry reads**.
>
> **5. Coalescing**
> 8 threads hitting `derivatives_universe.futures_index` simultaneously must trigger exactly
> **1** `list_future_contracts` build. Bumping the catalog epoch forces exactly 1 rebuild.
>
> **6. Frontend**
> ```
> cd frontend
> bunx vitest run src/__tests__/FnoWorkspaceView.test.tsx src/__tests__/futuresHeatmap.test.tsx
> bun run typecheck
> bunx biome check src/features/fno/FnoWorkspaceView.tsx src/__tests__/FnoWorkspaceView.test.tsx
> ```
> Expect 13/13; mutation-loop line `idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`;
> tsc exit 0; Biome clean.
>
> **7. Runtime smoke (restart the server first so the migration runs)**
> Verify `/health` < 0.2 s; F&O workspace (equity) loads; changing symbol **and** expiry
> works; `/api/fno/coverage`; `/api/futures/heatmap`; **no request storm** (server CPU
> < 10% with F&O + heatmap open); `md_subscriptions` = 9 and `md_derivative_rules` = 2
> **unchanged**; no broker errors in API responses; `config.json` still `port=7070`,
> `data_dir=data`.
>
> **8. Report** any deviation, plus the new `user_version`, index list, and measured
> before/after numbers.

---

## 6. Files touched (F&O performance work)

**Production**
| File | Change |
|---|---|
| `core/persistence/modules/products.py` | `exchange` on `derivative_expiries` / `option_strikes` / `list_future_contracts`; `create_instrument_read_indexes()`; `migrate_v20_to_v21()` |
| `core/persistence/modules/schema.py` | `SCHEMA_VERSION` 20 → 21 |
| `core/persistence/store.py` | `exchange` pass-throughs; catalog epoch; v20→v21 wiring |
| `app/instruments.py` | `exchange` pass-throughs; `catalog_epoch()` |
| `market/derivatives_universe.py` | canonical cached read model (futures index, expiries, underlyings) + single-flight + epoch invalidation |
| `app/subscriptions/service.py` | workspace/resolution consume the canonical model; `exchange=NSE` on hot lookups |
| `app/fno_coverage.py` | `plan()` shares the canonical futures index |

**Frontend**
| File | Change |
|---|---|
| `frontend/src/features/fno/FnoWorkspaceView.tsx` | stable `mutate` dependency (loop fix) |
| `frontend/src/features/futures-heatmap/*`, `frontend/src/components/HeatmapTile.tsx`, `frontend/src/styles/heatmap.css`, `frontend/src/styles/features/futures-heatmap.css` | Phase 8 board |

**Tests**
| File | Change |
|---|---|
| `test/test_fno_read_paths.py` | new — 11 regression tests (cache, single-flight, epoch invalidation, no per-expiry search, exchange passed, index plans) |
| `test/test_md_subscriptions.py` | fake catalog extended with the canonical bulk read + `exchange` kwargs |
| `test/test_previous_oi.py` | `test_schema_is_v21_with_table` — asserts `user_version == 21` and that `idx_instr_fo` / `idx_instr_seg` exist (was v20) |
| `frontend/src/__tests__/FnoWorkspaceView.test.tsx` | mutation-loop regression test |

**Index-plan tests.** With the v21 indexes present the planner serves all four hot F&O
queries from **`idx_instr_fo`** (verified: `derivative_expiries` → covering seek on
`exchange, instrument_type, underlying, expiry`; `option_strikes`, `list_future_contracts`,
`search_instruments` likewise). `test_hot_lookups_use_idx_instr_fo` asserts that index,
and `test_equity_universe_uses_idx_instr_seg` asserts `idx_instr_seg` for the NSE_EQ
universe read. Both also assert no `SCAN instruments`.

---

## 7. Environment notes

- Server: `python _run_server.py` (config `config.json`, port `7070`, `data_dir=data`).
- Catalog: 130,576 rows (`fyers` 84,611 / `upstox` 45,965; OPTION 115,732; FUTURE 1,294;
  all `exchange='NSE'`).
- Frontend: `@tanstack/react-query@5.102.8`, React 18, Vitest + jsdom, Biome.
- Probe the UI at `http://localhost:7070/ui/` (Markets ▾ → Futures Heatmap; F&O → workspace).
