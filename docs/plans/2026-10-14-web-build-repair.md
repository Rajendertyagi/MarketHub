# Web Build Repair — Frontend Upgrade + F&O Feature Completion

> Living document. Created 2026-10-14 after `bun run build` failed following the
> 2026-10-13 dependency bump. Phase tracker at the bottom; update after each phase.

## Problem

A major-version bump across every frontend dependency (React 18→19, zod 3→4,
TypeScript 5.5→7, Vite 5→8, Vitest 2→5, react-router 6→7, echarts 5→6,
jsdom 24→30) was applied to `frontend/package.json` without a follow-up migration.
`bun run build` fails with 35 TypeScript errors and `bun run test` cannot start a
single worker.

Investigation showed the upgrade is **not** the only breakage. A large body of
F&O coverage / Futures Board work (Phase 7–8, `docs/current.md` §2.1–2.2) exists
as new modules and new tests, but was **never integrated into existing production
files**. The same "written but not wired" pattern appears on both sides:

| Layer | What is missing |
|---|---|
| Backend | `FnoCoverageService` constructed nowhere; two route builders never mounted; `SubscriptionService` missing its `registry` kwarg and derivatives-universe owner |
| Frontend | `FuturesHeat*` types, 6 API functions, `embedded` props, router + nav entries |
| Upgrade | zod 4 `z.record` arity, TS 7 side-effect imports, `Object.entries` inference, jsdom 30 + Vitest 5 under Bun, Biome CRLF |

### Documentation drift (verified, do not trust these docs)

- `docs/current.md` §2.2 claims the Futures Board is *"built and live-verified"*.
  False on disk. It also cites HEAD `3e99513`; actual HEAD is `3f4b8d9`.
- `docs/plans/2026-09-20-futures-heatmap.md` describes the **retired** ECharts
  treemap (`futuresHeatmapOption.ts`, deleted). Phase 8 replaced it with the
  CSS-grid board wall. Superseded by `docs/current.md` §2.2.
- `docs/FRONTEND_MIGRATION.md` pins "Vite 5"; the tree is on Vite 8.

## Baseline (measured 2026-10-14)

```
pytest test/                          36 failed · 1272 passed · 2 skipped · 38 errors
bun x tsc --noEmit                    35 errors
bun run test                          27 files fail to boot, 0 tests run
bun run lint                          18 errors (17 CRLF + 1 organizeImports)
ruff check (new backend files)        24 errors (23 auto-fixable)
pytest test_architecture_boundaries.py test_auth_freeze.py   35 passed  ✓
```

### Failure triage

| Block | Count | Root cause | In scope |
|---|---|---|---|
| `test_l1_logging_news.py` | 38 errors | `fixture 'r' not found` — pre-existing harness bug (`docs/current.md` §3.C.8) | no |
| `test_fno_coverage` 5, `test_fno_read_paths` 3, `test_derivatives_universe` 2 | 10 | `SubscriptionService` missing `registry=` + derivatives owner | **yes** |
| `test_upstox_fo_resolution.py` | 4 | `KeyError: 'upstox'` via `providers.registry` → new `brokers/*/capabilities.py` | **yes** |
| `test_news_tools.py` | 9 | `ImportError: NEWS_HARD_MAX_LIMIT` from `news.service` | no — separate feature |
| `test_mcp_streamable_http` 3, `test_mcp_workflows` 2, `test_x_twitter` 1 | 6 | X/Twitter + MCP integration | no |
| `test_previous_oi.py` | 1 | stale assert; `SCHEMA_VERSION = 23` (v22→v23 = X/Twitter cache), test pins 22 | no — stale test only |

`core/persistence/modules/schema.py` is a §6 red zone. The v23 drift needs a
**test** edit only — no schema edit.

### Backend contracts to mirror in the frontend

Derived from `market/futures_board.py:99-137`, `app/fno_coverage.py:100-115`,
`api/futures_heatmap.py`, `api/fno_coverage_routes.py`:

| Endpoint | Method | Shape |
|---|---|---|
| `/api/futures/heatmap?expiry=&buildup=` | GET | `FuturesBoardSnapshot.to_dict()` + `buildup` + per-item `status` + `providers` + `coverage` |
| `/api/fno/catalog` | GET | `sync_state` + `segments` + `fno{}` |
| `/api/fno/universe?expiry=` | GET | `enabled`, `indices`, `stocks`, `expiries`, `expiry_resolved`, `errors` |
| `/api/fno/coverage?expiry=` | GET | `{status:"ok", **RoutingPlan.to_dict()}` |
| `/api/fno/preview?expiry=` | GET | subset of the plan |
| `/api/fno/apply` | POST | body `{expiry}` |
| `/api/fno/apply` | DELETE | no body |

`request()` lives in `src/api/client.ts` and takes paths **without** the `/api`
prefix → frontend paths are `/fno/coverage`, `/futures/heatmap`.

## Boundary declaration

```
TASK ZONE:              WEBUI FEATURES (frontend/src/features/*)
                        WEBUI SHELL (frontend/src/routes/router.tsx — §6/§12 red zone)
                        UNDECLARED GAP: app/subscriptions/service.py (assigned to no
                        zone in §1 or §12 — declared here explicitly; additive-only,
                        no persistence-schema change, no new feed/quote ownership)
ALLOWED CHANGE SURFACE: frontend/{package.json,bun.lock,biome.json,tsconfig.json,
                        vitest.config.ts,src/test/setup.ts}
                        frontend/src/{api/client.ts,api/market.ts,api/schemas.ts,
                        types/index.ts,main.tsx}
                        frontend/src/features/{futures-heatmap,market-data,
                        instruments,subscriptions}/
                        frontend/src/routes/router.tsx
                        frontend/src/layouts/nav.ts
                        app/subscriptions/service.py
                        api/fno_coverage_routes.py, api/futures_heatmap.py  (ruff only)
PROTECTED/OUT-OF-SCOPE: PROTECTED AUTH — app/auth/*, CredentialStore, credential
                        persistence, session restoration, rejection/expiry lifecycle
                        CONFIG/PERSISTENCE red zone — core/persistence/**
                        news/*, mcp_server/*, x_twitter/*, test/test_l1_logging_news.py
```

`app/server.py` (Phase D) is `PROTECTED STARTUP/RUNTIME` — §5 protected core, §6
red zone, and §13 line 304 requires escalation. **Approved by the user on
2026-10-14** as a separately-authorised task. It inherits §12/§15 verification:
`full startup + Upstox & Fyers feed start + restore` and
`backend startup + auth restoration + source/feed state`. The "login needs NO
full retest" carve-out does **not** apply.

No §5/§6/§21 protected file is touched outside that approved composition-root
wiring. Login must not regress.

## Phases

### Phase A — Toolchain
- A1 `.gitattributes` (`* text=auto eol=lf`) + `biome.json` `lineEnding: "lf"`.
  **Correction:** the initially-researched `lineEnding: "auto"` (Biome RFC #7598 /
  PR #7599) was **wrong for this repo** — the tree is LF-dominant, so `auto`
  demands CRLF on Windows and took lint from 18 → **204** errors. The correct
  long-term fix is to enforce LF at the git layer and leave Biome at `lf`.
  272 tracked files were normalised CRLF → LF in the working tree.
- A2 `tsconfig.json` → add `"vite/client"` to `types`; restore trailing newline
- A3 zod 4: 18 × `z.record(V)` → `z.record(z.string(), V)` across 6 files
- A4 jsdom → happy-dom 21.0.1 (`environment: "happy-dom"`). happy-dom is
  first-class in both Vitest and Bun and removes the jsdom 30 WebIDL wrapper
  conflict (`vitest` patches `EventTarget.prototype.addEventListener`; jsdom 30's
  `requireImplForWrapper` rejects the patched call). Suite went from *0 tests
  runnable* to the documented pre-upgrade baseline exactly.
- A5 delete stray `frontend/tsconfig - Copy.json`
- A6 **(discovered during verification)** Vite 8 bundles with **rolldown**, which
  requires the *function* form of `manualChunks`. The object form Rollup accepted
  failed the build. Converted to a function; chunk split (echarts/react/query)
  preserved.
- A7 **(discovered during verification)** lightningcss rejected
  `@container tile (max-height: 28px), tile (max-width: 40px)`. Comma-separated
  query lists are valid in `@media`/`@supports` but **not** `@container`;
  alternatives must use `or`. Rewritten as
  `@container tile ((max-height: 28px) or (max-width: 40px))`.

### Phase B — SubscriptionService
- B1 `registry=None` constructor kwarg (provider capacity/budget lookups)
- B2 `set_derivatives_universe` / `clear_derivatives_universe`, mirroring the
  existing `_analytics_universe` owner (`service.py:98-111`)
- B3 third union block in `resolve()` with `kind: "derivatives"`, mirroring the
  analytics block (`service.py:620-635`). Additive only — never touches another
  owner, never persisted.
- B4 ruff-clean the 24 errors in the new backend files

### Phase C — Frontend completion
- C1 zod schemas in `src/api/schemas.ts` (dict-valued fields use the 2-arg
  `z.record` form)
- C2 hand-written interfaces in `src/types/index.ts` — **not** `z.infer`
  (the 11 `*Parsed` exports are dead code; types/index.ts imports no zod)
- C3 six API functions in `src/api/market.ts` at `/fno/*` + `/futures/heatmap`
- C4 `embedded` prop on `InstrumentsView` + `SubscriptionsView`. Per the CSS
  contract in `docs/FRONTEND_MIGRATION.md`, `.panel` is a page-root elevated
  surface and must not nest inside another elevated surface — so `embedded`
  strips the `.panel` wrapper **and** the page header. `SubscriptionsView` also
  suppresses its inner `<Tabs>` + "Save & Apply" row, because `MarketDataView`
  is already the tab container and nested tab bars are wrong.
- C5 router (`/market-data`, `/futures-heatmap`) + nav entries
- C6 annotate the two `Object.entries` sites where TS 7 infers `unknown`

### Phase D — Composition wiring (approved escalation)
- D1 construct `FnoCoverageService(_instrument_catalog)` — registry/callables
  default internally, so no auth dependency is introduced
- D2 mount `build_fno_coverage_routes(catalog, subscriptions, coverage, feed_provider)`
- D3 mount `build_futures_heatmap_routes(coverage, market_service, store)`

## Verification

```
cd frontend && bun run typecheck && bun run lint && bun run test && bun run build
python -m pytest test/test_architecture_boundaries.py test/test_auth_freeze.py -q
python -m pytest test/test_fno_coverage.py test/test_fno_read_paths.py \
  test/test_derivatives_universe.py test/test_futures_board.py \
  test/test_futures_heatmap_api.py test/test_upstox_fo_resolution.py \
  test/test_provider_registry.py -q
python -m ruff check app/subscriptions/service.py app/fno_coverage.py \
  api/fno_coverage_routes.py api/futures_heatmap.py market/futures_board.py providers/
```
Phase D additionally requires live startup smoke: server boots, Upstox + Fyers feed
start, session restore intact, `/health` fast, `/api/fno/coverage` and
`/api/futures/heatmap` return 200, no request storm.

## Phase tracker

- [x] **Phase A — Toolchain** (incl. late-found A6 rolldown `manualChunks`, A7 `@container` CSS)
- [x] **Phase B — SubscriptionService**
- [x] **Phase C — Frontend completion**
- [x] **Phase D — Composition wiring** (approved escalation)

## Results

| Gate | Before | After |
|---|---|---|
| `bun run build` | **FAIL** (35 tsc + rolldown + CSS) | **PASS** — 930 modules, 1.8 s |
| `bun run typecheck` | 35 errors | **0** |
| `bun run lint` | 18 errors | **0** |
| `bun run test` | 0 files runnable (27 boot failures) | 8 failed files / 21 failed tests / 106 passed / 3 errors — exactly the documented pre-upgrade baseline (`docs/current.md` §3.C.7) |
| `pytest test/` (full) | 36 failed · 1272 passed · 38 errors | **32 failed · 1276 passed · 38 errors** |
| `test_fno_coverage.py` | 5 failed | **11 passed / 1 failed** |
| `test_futures_heatmap_api.py` + `test_futures_board.py` | 15 passed | 15 passed |
| boundaries + auth freeze | 35 passed | **35 passed** |

The 38 errors are unchanged and entirely pre-existing
(`test_l1_logging_news.py`, `fixture 'r' not found`). The −4 / +4 delta is
exactly the `test_fno_coverage.py` fixes; **no test regressed**, confirmed
failure-by-failure against the baseline breakdown.

### Regression caught during verification

An edit to `app/server.py` mistyped `from api.x_routes import` as
`from app.x_routes import`, which broke every test that boots the real server
(`test_sdk_alignment.py` and others) — the full suite briefly read
47 failed / 61 errors. Caught by the startup gate, fixed, and a static check
was added confirming **all 75 module references in `app/server.py` resolve**.
Lesson recorded: on a file whose import-time failure is only observable by
booting the server, verify with a module-resolution pass, not just `ast.parse`.

Two other self-inflicted issues, both caught and recovered:
- `biome check --write` corrupted `src/api/market.ts` into 13927 NUL bytes.
  Restored from the git index and the edits re-applied. Whole tree scanned;
  that file was the only casualty.
- Two full pytest runs overlapped, producing a false +11 regression before the
  clean re-run. `test/helpers/lifecycle.py` mutates the real `config.json` and
  starts servers on real ports, so concurrent runs contaminate results.

Two name collisions were found and resolved while wiring: `FnoUniverseResponse`
and `fnoUniverseSchema` already existed for the *old* `/api/market/fno/universe`
contract, so the new `/api/fno/universe` ones were renamed
`FnoCoverageUniverseResponse` / `fnoCoverageUniverseSchema`.

### Out of scope — found, not fixed

- **F&O read-path workstream (10 failures).** `market/derivatives_universe.py`
  (canonical cached read model, epoch invalidation, single-flight) exists but is
  imported by nothing except one function in `api/routes.py`. Wiring it into
  `app/subscriptions/service.py` + `app/fno_coverage.py` also requires
  `exchange` pass-throughs and a catalog epoch on `core/persistence/store.py` +
  `app/instruments.py`. `store.py` is a **§6 red zone (CONFIG/PERSISTENCE)**,
  so this is a separate approved task. This is the documented F&O performance
  work in `docs/current.md` §2.4/§2.5/§6 — written but never integrated.
  Affected: `test_fno_read_paths` (3), `test_upstox_fo_resolution` (4),
  `test_derivatives_universe` (2, incl. `app.market_indices.FNO_INDEX_LABELS`
  missing), `test_fno_universe` (1), `test_fno_coverage` (1, `NIFTYFPI` missing
  from the routed universe) — 11 total.
- **News MCP tools (9) + `test_broker_analytics` (1).**
  `mcp_server/tools/news_tools.py` imports `NEWS_HARD_MAX_LIMIT` from
  `news.service`, which does not define it; `NewsSnapshot` has drifted
  (`total_records` kwarg missing).
- **MCP / X-Twitter (9).** `test_mcp_streamable_http` (5),
  `test_mcp_workflows` (3), `test_x_twitter` (1).
- **`test_previous_oi.py`** — stale assert; `SCHEMA_VERSION` is 23, test pins
  22. Test-only fix; the schema red zone stays untouched.
- **`test_fyers_tbt.py`** — async feed lifecycle assertion (`starting` vs
  `stopped`); looks like a timing flake, needs triage.
- **`test_l1_logging_news.py` (38 errors)** — pre-existing harness bug
  (`fixture 'r' not found`), logged in `docs/current.md` §3.C.8.

### Stale docs to correct

- `docs/current.md` §2.2 claims the Futures Board is *"built and live-verified"*.
  It was not wired until this task. It also cites HEAD `3e99513` vs actual `3f4b8d9`.
- `docs/plans/2026-09-20-futures-heatmap.md` documents the retired ECharts
  treemap (`futuresHeatmapOption.ts`, deleted in Phase 8).
- `docs/FRONTEND_MIGRATION.md` pins "Vite 5"; the tree is on Vite 8.

## Phase log

- 2026-10-14: plan saved; Phase A starts.
- 2026-10-14: Phase A done. `lineEnding: "auto"` tried and **reverted** (18 → 204
  errors); replaced with `.gitattributes` + 272-file LF normalisation → lint 0.
- 2026-10-14: `biome check --write` corrupted `src/api/market.ts` (file became
  13927 NUL bytes). Recovered from the git index and re-applied the edits.
  Scanned the whole tree — that file was the only casualty. `check --write` was
  not used again; only read-only `biome check` from that point on.
- 2026-10-14: Phases B, C, D done. Two further upgrade breakages surfaced only
  when `vite build` actually ran (A6 rolldown `manualChunks`, A7 `@container` CSS)
  — `tsc` alone could not have caught either.