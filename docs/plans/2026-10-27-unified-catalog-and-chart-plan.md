# MarketHub — Unified Catalog, Picker and Chart Plan

| | |
|---|---|
| **Plan ID** | `MH-UCP-2026-10-27` |
| **Status** | Draft for owner review — collection complete, **no code implemented** |
| **Prepared** | 2026-10-27 |
| **Branch** | `fno-provider-agnostic` |
| **Scope** | Chart experience, bars backend, unified instrument catalog, unified indicator catalog |
| **Verified against** | The live working tree and a read-only query of the live catalog (`data/events.db`) |

This document is the persisted version of the change list collected on 2026-10-27. It
supersedes the chat-only list. Nothing here authorizes code changes; each phase below
carries its own zone declaration and must be approved separately.

---

## 0. Declaration required before implementation

Every phase is a separate task with its own declaration:

```
TASK ZONE:              <zone(s) from docs/ARCHITECTURE_BOUNDARIES.md §12>
ALLOWED CHANGE SURFACE: <files listed in the phase>
PROTECTED/OUT-OF-SCOPE ZONES: auth, startup, credential storage, broker login,
                        session restoration, feed/source lifecycle, canonical
                        market models, persistence schema (unless named)
```

**FROZEN:** `app/auth/*`, `CredentialStore`, broker login, session restoration,
startup restore blocks, rejection/expiry lifecycle, fake-token guards. None of the
phases below require touching them. **Login must not regress.**

---

## 1. Purpose and non-goals

**Purpose**

1. One instrument catalog and one chooser UI for the whole application, used by the
   chart as well (the picker must live inside the chart layout, not in a separate
   host toolbar row).
2. One indicator catalog, centrally defined, used by the chart now and by
   scanners/alerts later.
3. Keep the chart and bars foundation correct and observable while doing so.

**Non-goals**

- No change to auth, credential storage, broker login or session restoration.
- No replacement of the canonical market models (`market/models.py`,
  `market/service.py`, `market/serialization.py`).
- No provider-specific branching in the picker UI.
- No new runtime dependency unless a phase proves it necessary.

---

## 2. Evidence base (verified 2026-10-27)

### 2.1 Catalog

| Fact | Evidence |
|---|---|
| 151,485 instrument rows | read-only `SELECT count(*) FROM instruments` on `data/events.db` |
| 136,312 OPTION, 5,911 EQUITY, 1,369 FUTURE, 417 INDEX, 355 ETF, 7,121 untyped | same query grouped by `instrument_type` |
| All 7,121 untyped rows are Upstox `NSE_EQ` | debt-like symbols (`749RJ35`, `645BR27`, `KCK`), not regular equities |
| 2,344 duplicate groups by `(exchange, tradingsymbol, instrument_type)` | all are EQUITY; e.g. `RELIANCE` exists once under `fyers` and once under `upstox`; no F&O duplicates |
| Current picker search | `LIKE '%q%'` on `tradingsymbol`/`name`, `ORDER BY tradingsymbol` — [core/persistence/modules/products.py:543](file:///D:/Temp/MarketHub/core/persistence/modules/products.py#L543) |
| Current picker route latency | 36–95 ms for limit 25 (`REL` 87 ms, `NIFTY` 36 ms, `RELIANCE` 95 ms; best of 5, read-only) |
| Ranked multi-probe fallback latency | **370–538 ms** for the same terms — `MarketIntel.search` issues two probes per type across five types |
| Single-pass ranked SQL latency | **34–53 ms** when scoped to cash types (`INDEX`,`EQUITY`,`ETF`) with a `CASE` score and `LIMIT 25` |
| Cash universe size | ~6,683 rows (417 INDEX + 5,911 EQUITY + 355 ETF); the other 136k are options |
| Current ranking is wrong for a picker | `REL` → `755TPREL40`; `NIFTY` → `ABSLBANETF`; `RELIANCE` → `RCOM` |
| No popularity signal exists in the catalog | ties on symbol prefix can only break by length/alphabet, so `REL` ranks `RELAXO` before `RELIANCE` |
| Exact resolver exists | [core/persistence/modules/products.py:595](file:///D:/Temp/MarketHub/core/persistence/modules/products.py#L595) |
| Catalog lookup index exists | `idx_instr_lookup (exchange, instrument_type, expiry, underlying)` |
| FTS5 is available | SQLite 3.53.4, `ENABLE_FTS5 = 1` |

### 2.2 Search and identity already implemented

| Capability | Location |
|---|---|
| Structured + ranked search (`reliance future`, `nifty 25000 ce`) | [app/market_intel.py:325](file:///D:/Temp/MarketHub/app/market_intel.py#L325) |
| Relevance score (underlying exact → prefix → contains → name) | [app/market_intel.py:200](file:///D:/Temp/MarketHub/app/market_intel.py#L200) |
| Underlying resolution (INDEX before EQUITY) | [app/market_intel.py:216](file:///D:/Temp/MarketHub/app/market_intel.py#L216) |
| Provider preference rank | [app/market_intel.py:239](file:///D:/Temp/MarketHub/app/market_intel.py#L239) |
| Compact canonical result (`canonical_id`, `native_key`, provider token) | [app/market_intel.py:285](file:///D:/Temp/MarketHub/app/market_intel.py#L285) |
| Generic search does **not** dedupe | `_select_one_provider` is called only by `futures_contracts` and `option_chain`; `search()` returns every provider's row |
| Exact resolver exists but is **internal only** | `find_by_exact_symbol` / `find_canonical_rows` — [app/instruments.py:792](file:///D:/Temp/MarketHub/app/instruments.py#L792), [app/instruments.py:860](file:///D:/Temp/MarketHub/app/instruments.py#L860); no HTTP route exposes them |
| The store forwards read-model kwargs | `store.search_instruments(**kw)` — [core/persistence/store.py:756](file:///D:/Temp/MarketHub/core/persistence/store.py#L756) — so new optional read-model kwargs need **no** red-zone store edit |
| Provider-neutral canonical id | [app/canonical_identity.py:198](file:///D:/Temp/MarketHub/app/canonical_identity.py#L198) |
| Structured search route | `GET /api/market/search` — [api/product_routes.py:567](file:///D:/Temp/MarketHub/api/product_routes.py#L567) |
| Plain catalog search route | `GET /api/instruments/search` — [api/product_routes.py:677](file:///D:/Temp/MarketHub/api/product_routes.py#L677) |
| F&O read models | `fno_universe`, `fno_underlying_rows`, `derivative_expiries`, `option_strikes`, `futures_contracts` |

### 2.3 Frontend

| Fact | Evidence |
|---|---|
| Chart picker is a host toolbar **above** the Vela workspace | [frontend/src/features/chart/ChartView.tsx:201](file:///D:/Temp/MarketHub/frontend/src/features/chart/ChartView.tsx#L201) |
| A second, visually different picker in watchlists | [frontend/src/features/watchlists/components/AddInstrument.tsx:42](file:///D:/Temp/MarketHub/frontend/src/features/watchlists/components/AddInstrument.tsx#L42) |
| A third, client-side list filter for F&O underlyings — a specialized universe table, not a generic symbol chooser | [frontend/src/features/fno/UnderlyingPicker.tsx](file:///D:/Temp/MarketHub/frontend/src/features/fno/UnderlyingPicker.tsx) |
| Shared UI primitives exist (Button/Input/Select/Modal/Tabs) | [frontend/src/components/ui.tsx](file:///D:/Temp/MarketHub/frontend/src/components/ui.tsx) |
| Client search wrappers | `searchInstruments`, `resolveInstrument` — [frontend/src/api/market.ts:152](file:///D:/Temp/MarketHub/frontend/src/api/market.ts#L152) |
| Chart provider declares `enumerate:false`, `stream:false` | [frontend/src/features/chart/chartData.ts:141](file:///D:/Temp/MarketHub/frontend/src/features/chart/chartData.ts#L141) |
| Vela topbar configured without a symbol slot | [frontend/src/features/chart/VelaChart.tsx:51](file:///D:/Temp/MarketHub/frontend/src/features/chart/VelaChart.tsx#L51) |
| Persistence keyed per instrument token | [frontend/src/features/chart/VelaChart.tsx:53](file:///D:/Temp/MarketHub/frontend/src/features/chart/VelaChart.tsx#L53) |
| Dependencies | React 19, TanStack Query, zod, Vela 0.9.0; no combobox/virtualization/fuzzy library |

### 2.4 Vela capabilities and limits

| Fact | Evidence |
|---|---|
| Native symbol index is built **once** at provider registration; no refresh API | `ProviderRegistry.buildIndex()` in `node_modules/@luxalgo/vela/dist/chunk-XIRSR3D6.js` |
| Native symbol slot cannot be overridden; `indicators` and `screenshot` can | `OVERRIDABLE_TOPBAR_IDS` — `node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts:2216` |
| Contributed topbar actions support `align:'left'`, `run(ctx)` | `WidgetActionDescriptor` — `contributions-BjefWpxQ.d.ts:2081` |
| Action context exposes `host`, `setSymbol`, `openSymbolSearch`, `addNativeIndicator`, `toast`, `i18n` | `WidgetContext` — `contributions-BjefWpxQ.d.ts:1986` |
| Vela ships Popover/Menu/Dialog/Tooltip/Select primitives | `node_modules/@luxalgo/vela/dist/ui.d.ts` |
| Indicator type metadata is enumerable | `nativeIndicatorDescriptors()`, `chart.availableNativeIndicators()` |
| Indicator slot can be replaced and indicators added through the shell | `contributions-BjefWpxQ.d.ts:2029` |

### 2.5 Backend indicators

There is **no** indicator concept in the backend today (no definitions, no evaluation,
no persistence). Indicators exist only as Vela natives in the browser.

---

## 3. Locked decisions (owner, 2026-10-27)

| ID | Decision |
|---|---|
| **D1** | One **code-first** indicator catalog. Definitions, categories, parameter schema and formulas live in versioned code; the database stores only per-user state (favorites, defaults, templates). |
| **D2** | The catalog must serve the **whole application** — chart first, then scanners and condition alerts; backend evaluation comes after the catalog exists. |
| **D3** | The chart symbol chooser moves **inside the Vela layout** as a contributed topbar action with remote search. Vela's native symbol index is not enabled (151k instruments, one-shot index). |
| **D4** | No new packages for v1. Reuse React, TanStack Query, zod, Vela's kit and the existing UI primitives. |
| **D5** | One picker component, one search contract, one identity/dedupe rule; no per-feature search implementations. |
| **D6** | The instrument picker searches everything (equity, index, ETF, F&O) with type filters; F&O uses a structured mode instead of a flat option list. |

---

## 4. Target architecture (streamlined)

```
SQLite instruments (151k canonical rows; ~6.7k cash, 136k options)
        │
        ▼
products.search_instruments  ── one ranked pass (optional rank/types args)
        │
        ▼
market_intel.search  ── parse + compact + canonical-id dedupe
        │                 (reuse _row_score/_provider_rank/_compact)
        ▼
GET /api/market/search  ── picker contract (limit <= 50)
        │
        ▼
InstrumentPicker (React, src/components)
        ├── chart  (inside Vela topbar, Phase 2)
        ├── watchlist add (replaces AddInstrument's private search)
        └── F&O structured mode (underlying → expiry → strike → CE/PE)

GET /api/market/search?exact=1 ── exact identity (deep links, scanners, chart hydrate)
F&O underlying universe table ── shared search input; backend read models; not a picker

Vela nativeIndicatorDescriptors()
        │
        ▼
indicatorCatalog (one module) ── categories, params, chart-only/backend-ready flags
        │
        ├── chart indicator menu (override the `indicators` topbar slot)
        └── GET /api/indicators (read-only, generated) ── scanners/alerts later
```

### 4.1 Build vs reuse

| Need | Do not build | Reuse |
|---|---|---|
| Relevance ranking | New scoring engine | `app/market_intel.py` `_row_score` / `_norm` |
| Dedupe across providers | New identity rules | `canonical_id_for_row` for the key + `_provider_rank` for the winner. `_select_one_provider` is contract-keyed and today only used by futures/option chains — the picker path needs an explicit dedupe step (see §4.2 item 2) |
| Provider choice | New preference config | `providers.registry.preference_order()` / `enabled_ids()` (already what `_provider_rank` reads) |
| Result contract | New DTO | `_compact()` shape (canonical id, provider, native key, type metadata) |
| Ranking query shape | New search service or FTS index | One `CASE`-scored SQL pass scoped to cash types, as an optional read-model kwarg (measured 34–53 ms) |
| Exact symbol / deep-link resolution | New matcher | `find_by_exact_symbol` + `find_canonical_rows` (already implemented; needs an HTTP surface) |
| F&O selection | New tables/queries | `fno_underlying_rows`, `derivative_expiries`, `option_strikes`, `futures_contracts` |
| Chart picker UI | New dropdown primitives | `src/components/ui.tsx` + Vela `Popover`/`Menu` + `ctx.host` |
| Indicator list | Hand-maintained list | `nativeIndicatorDescriptors()` + per-chart `availableNativeIndicators()` |
| Chart indicator add | Custom indicator creation | `ctx.addNativeIndicator(type)` (undo/redo + persistence aware) |
| Fuzzy matching | `fuse.js`/`cmdk` | Server-side ranked SQL; no fuzzy library and no FTS5 at current volumes (W28 dropped) |

### 4.2 Streamlining revisions made after reading the code

1. **Do not write a new search service.** `app/market_intel.search` already parses
   `"nifty 25000 ce"` and `"reliance future"`, ranks by relevance, prefers NSE, and
   emits canonical identities. The picker should call it; the plain
   `/instruments/search` endpoint stays only for backward compatibility and exact
   resolution.
2. **Do not build a new dedupe layer — but do apply it in the search path.** Today
   `_select_one_provider` is called only by `futures_contracts` / `option_chain`;
   the generic fallback in `search()` returns one row per provider, so `RELIANCE`
   comes back twice (upstox + fyers). Phase 0 adds a canonical-id dedupe step:
   key by `canonical_id_for_row`, keep the lowest `_provider_rank`, and keep rows
   whose canonical id cannot be derived only when the caller asked for them.
3. **Do not add a fuzzy-search library, and do not keep the multi-probe loop.**
   The current route is fast (36–95 ms) but mis-ranked; the ranked fallback is
   correct-ish but costs 370–538 ms because it probes every type twice. A single
   `CASE`-scored pass over cash types (6,683 rows) returns in 34–53 ms. So Phase 0
   replaces the probe loop with one ranked read-model query; no FTS5, no new
   package. Derivatives are reached through parsed intent or the structured mode.
4. **Do not enumerate symbols into Vela.** 151k rows and a one-shot index make that
   both slow and permanently stale.
5. **Do not build indicator metadata twice.** Vela already exposes descriptor
   metadata; the app catalog adds only product metadata and backend capability flags.
6. **Do not introduce a new picker per feature.** The real duplication is the
   search box itself: the chart toolbar's dropdown (`ChartView`) and the
   watchlist `AddInstrument` each re-implement debounce, abort, ranking and
   keyboard behavior. Both become `InstrumentPicker`. The F&O underlying table
   is not a picker duplicate (see item 8).
7. **Do not change chart identity persistence.** Keep `persist:
   markethub-chart:<instrument_token>` and the per-instrument remount; in-place
   `setSymbol` would write drawings under the previous symbol's key.
8. **Do not dissolve the F&O underlying table.** It is a purpose-built universe
   view (contract counts, equity/index kinds) fed by backend read models. It should
   share the search input and picker behavior, not become a generic symbol list.
9. **Do not keep fuzzy resolution in chart navigation — but note it needs a route.**
   `resolveInstrument` (used by scanners) falls back to `results[0]`, so a symbol can
   be substituted by another instrument class. The exact resolver exists in the
   catalog but is not exposed over HTTP, so Phase 0 adds `exact=1` to
   `/api/market/search`; Phase 1 switches the client to it.
10. **Do not create React roots per click.** Vela's `registerWidgetAttachment`
    mounts a contributed UI once per widget and returns a disposer; the topbar action
    should toggle that mounted picker, not rebuild it.
11. **Do not change what existing catalog readers get.** Add the ranked query as an
    optional read-model mode used by `/api/market/search`; leave the default
    `search_instruments` ordering (alphabetical) untouched for `find_canonical_rows`,
    F&O read models and every other existing caller.
12. **Do not classify the untyped rows in Phase 0.** The 7,121 NULL-type rows are
    Upstox `NSE_EQ` records whose raw master type is unmapped in
    `_UPSTOX_INST_TYPE` ([app/instruments.py:120](file:///D:/Temp/MarketHub/app/instruments.py#L120)).
    Mapping them is an ingestion change that needs a live master check. Phase 0
    only excludes NULL types from picker queries — the cash-type scope does that
    for free — and classification stays W30.
13. **Do not require popularity ranking in v1.** No popularity column exists, so
    prefix ties break by length then alphabet (`REL` → RELAXO before RELIANCE).
    The acceptance rule is "cash results before option noise", not a specific first
    row; the optional hot set (W29) can refine ordering client-side later.
14. **Do not widen Phase 0 into the store facade.** `store.search_instruments(**kw)`
    forwards kwargs to the read model, so new optional query arguments (for example
    a cash-type scope and a rank query) need no edit to the red-zone
    [core/persistence/store.py](file:///D:/Temp/MarketHub/core/persistence/store.py).

---

## 5. Phases

Each phase is independently approvable and verifiable.

### Phase 0 — Search correctness and catalog hygiene

**Goal:** make the existing search usable as the single picker contract.

**Changes**
- Adopt `GET /api/market/search` (the existing structured/ranked service) as the
  picker contract; `/api/instruments/search` stays as-is for legacy callers.
- Add one ranked read-model mode to `products.search_instruments`: optional
  `types` (multi-type scope) and `rank` (query to score). SQL shape: INDEX/EQUITY/ETF
  scope by default, `CASE` score (exact symbol → exact underlying → symbol prefix →
  symbol contains → name contains), then exchange NSE preference, then
  length/alphabet, `LIMIT` at most 50. No schema change.
- Replace `MarketIntel.search`'s per-type probe loop with that single call. Keep
  the parser and its option/type-word branches; only the generic fallback changes.
  Cash classes return first; derivatives are queried only on parsed intent or an
  explicit type filter.
- Dedupe the returned rows by `canonical_id_for_row`, keeping the lowest
  `_provider_rank`; the compact result keeps the winning provider's `provider_token`
  and `native_key`. (No duplicates exist outside cash equities today, but the rule
  is identity-based.)
- Expose exact resolution on the same contract: `GET /api/market/search?exact=1&q=SYMBOL`
  resolves through `find_by_exact_symbol`, merges provider duplicates by
  `_provider_rank`, and returns the compact shape (no substring guessing). Put it
  on `build_intel_routes`, which already has the market-intel service for provider
  preference; leave `/api/instruments/search` unchanged. Phase 1 switches the client.
- Exclusion, not classification: picker queries scope to canonical types, which
  drops the 7,121 NULL-type rows. W30 stays deferred.
- Keep HARD limits (`limit <= 50` for pickers) and the 64-character query cap.

**Allowed surface**
`core/persistence/modules/products.py`, `app/instruments.py`, `app/market_intel.py`,
`api/product_routes.py`, `test/test_*` for these paths.

**Zone**
`CONFIG/PERSISTENCE` + `TRANSPORT/API`. `api/product_routes.py` is a red-zone file:
only the `/api/market/search` handler is edited; the legacy instruments handler is
untouched and both endpoints are re-verified.
`core/persistence/store.py` is **not** touched (§4.2 item 14).

**Out of scope / protected**
No schema change, no migration, no auth, no ingestion/type-mapping change.

**Acceptance**
- `REL` returns cash results before option noise.
- `NIFTY` returns the INDEX row before derivative/ETF rows.
- `RELIANCE` returns one row, not one per provider, with provider attribution.
- Exact mode returns only exact symbol/underlying matches; no `results[0]` fallback.
- p95 search latency <= 120 ms at limit 25, measured on the new ranked path
  (single-pass measurement: 34–53 ms for `REL`/`NIFTY`/`RELIANCE`).
- Backend tests cover ranking, dedupe, NULL-type exclusion, exact mode, limit caps.

---

### Phase 1 — Shared instrument picker

**Goal:** one component, one behavior, used everywhere a symbol is chosen.

**Changes**
- Add `frontend/src/components/InstrumentPicker.tsx` (+ CSS in the existing style
  layer) with:
  - 250 ms debounce + abort, loading/empty/error states;
  - keyboard: ArrowUp/Down, Enter, Escape, Home/End; ARIA combobox/listbox;
  - type and exchange filters; provider badge; canonical identity in the result;
  - F&O structured mode: underlying → expiry → strike → CE/PE;
  - optional local hot set (indices, underlyings, recent selections) that never
    replaces server truth.
- Replace the chart toolbar picker and `AddInstrument`'s private search with this
  component. The F&O underlying view keeps its universe table (contract counts,
  equity/index kinds) but uses the same search input and selection behavior; its
  data continues to come from the backend F&O read models.
- Type the search result in `api/market.ts` / `types/index.ts` (canonical id,
  provider, token, symbol, venue, type, and derivative fields).
- **Contract trap:** the compact result's `instrument_key` is a composite
  (`EXCHANGE:TOKEN`), while the bars API expects the provider's **bare**
  `instrument_token` (e.g. `NSE_EQ|INE002A01018`). The picker must map
  `provider_token` to the URL `key`; never feed the composite key to the bars API.
  Carry the result's `provider` through `getMarketBars`, which already accepts it
  ([api/market_bars_routes.py:48](file:///D:/Temp/MarketHub/api/market_bars_routes.py#L48)),
  instead of relying on key-shape inference.
- Remove the `results[0]` fallback in `resolveInstrument`
  ([frontend/src/api/market.ts:178](file:///D:/Temp/MarketHub/frontend/src/api/market.ts#L178)):
  call the exact mode from Phase 0 and return `null` when nothing matches. A
  scanner row must never open a different instrument class.

**Allowed surface**
`frontend/src/components/InstrumentPicker.tsx`, `src/components/ui.tsx` (only if a
primitive is genuinely missing), `src/features/{chart,watchlists,fno,scanners,market-map}/*`,
`src/api/market.ts`, `src/types/index.ts`, feature CSS, related tests.

**Zone**
`WEBUI SHELL` (shared component) + `WEBUI FEATURES`.

**Out of scope / protected**
Broker adapters, auth, feed lifecycle, canonical market models.

**Acceptance**
- Exactly one search implementation remains in the frontend.
- `vitest`: keyboard selection, Escape, abort on unmount, error/empty states, F&O
  mode, duplicate suppression.
- No visual regression in watchlists; chart resolves deep links exactly.
- A scanner/market-map symbol link opens the exact instrument or shows a visible
  "not found" error — never a different class.

---

### Phase 2 — Chart selector inside the chart layout

**Goal:** remove the host toolbar row; select symbols from the chart topbar.

**Changes**
- Reuse the Phase 1 component **unchanged**: only the mount container and trigger
  move. This phase adds no search code.
- Register a widget **attachment** that mounts the shared picker once per widget into
  `ctx.host` and returns a disposer (no React root per click).
- Register a contributed topbar action (`align: 'left'`) whose `run(ctx)` toggles the
  mounted picker, anchored under the topbar. Contributed actions are resolved by the
  shell at construction, so both registrations happen at module import time.
- On selection: update URL (`key/sym/ex/type`), remount the chart with the new
  per-instrument persistence key.
- Remove the `.chart-toolbar` row and its search state from `ChartView.tsx`; keep
  the error banner and empty state.
- Place the action so the current symbol remains visible (watermark/legend);
  mobile uses the action's `mobile` behavior.
- Keep `capabilities.enumerate: false`; no `listSymbols()`.

**Allowed surface**
`frontend/src/features/chart/*`, `src/styles/features/chart.css`, `src/routes/*`
only if the route contract changes, chart tests.

**Zone**
`WEBUI FEATURES` + `WEBUI SHELL` (if the layout changes).

**Out of scope / protected**
Vela provider `getBars` contract, bars API, auth, feeds.

**Acceptance (real browser)**
- Picker opens from inside the chart topbar; host toolbar row is gone.
- Search → RELIANCE and INFY updates URL, chart and watermark; reload restores.
- Drawings/indicators persist per instrument across switches.
- The chart imports the shared picker; no search/debounce code remains in
  `frontend/src/features/chart/`.
- Zero console errors and zero failed requests.

---

### Phase 3 — Indicator catalog (chart)

**Goal:** one indicator catalog, one menu, no duplicated list.

**Changes**
- New `frontend/src/features/chart/indicatorCatalog.ts` that reads
  `nativeIndicatorDescriptors()` and adds product metadata: category, search aliases,
  display order, params/defaults, and `backendReady` flag.
- Override the `indicators` topbar slot with a catalog-driven picker (registered at
  import time, because the shell resolves slot overrides at construction); add
  selected types via `ctx.addNativeIndicator(type)` so undo/redo, indicator count and
  persistence stay correct.
- Mark catalog entries that are chart-only.

**Allowed surface**
`frontend/src/features/chart/*`, chart styles, chart tests.

**Zone**
`WEBUI FEATURES`.

**Out of scope / protected**
Backend evaluation (Phase 4), persistence schema.

**Acceptance**
- Menu entries come only from the catalog.
- Adding an indicator survives reload under the existing persistence document.
- Unknown/unsupported types are not offered; `isSupported` is honored.
- Tests cover catalog mapping and menu behavior.

---

### Phase 4 — App-wide indicator catalog (scanners/alerts)

**Goal:** the same catalog ids drive backend evaluation.

**Changes**
- Shared catalog spec (JSON) validated in TS and Python at startup.
- `GET /api/indicators` read-only projection of the spec.
- Backend evaluators keyed by catalog id; only entries with evaluators are
  selectable outside the chart.
- User preferences in the database (favorites, defaults, last used); never formulas.
- Parity fixtures: fixed bars → expected values, run against both the Vela native and
  the backend evaluator.
- Scanners/condition alerts integrate by catalog id only.

**Allowed surface**
New indicator module, indicator route, catalog spec, tests; scanners/alerts changes
in their own zones.

**Zone**
New module (to be assigned) + `TRANSPORT/API` + `ALERTS` (red-zone
`app/condition_alerts.py` requires its own approval) + `WEBUI FEATURES`.

**Out of scope / protected**
Chart-side math stays Vela; no formula execution from untrusted input.

**Acceptance**
- Catalog entry without an evaluator is rejected by scanners/alerts with a clear
  message.
- Parity fixtures pass within tolerance.
- No indicator name is accepted as free text by APIs.

---

### Phase 5 — Chart and bars gaps (existing list)

Grouped for later scheduling; all were verified during the 2026-10-27 chart work.

| ID | Item | Area | Notes |
|---|---|---|---|
| W1 | Real market calendar / status badge | FE+BE | Vela `getCalendar`; currently "(inferred)" |
| W2 | Push updates (`subscribe`) | FE+BE | Currently 3 s polling, `limit:2` |
| W3 | Subscribe the charted instrument | BE | Feed lifecycle — protected, separate approval |
| W4 | Provider-aware timeframes | FE+BE | `240` hardcoded; Fyers lacks it |
| W5 | Verify all drawing tools | FE | Only two-click trendline verified |
| W6 | Indicator coverage in the catalog | FE | Volume + Bollinger verified only |
| W7 | Range-button semantics | FE | Timeframe+limit switch, not a true window |
| W8 | Actionable bars errors | FE | Banner exists; wording not audited |
| W9 | Layouts / templates | FE | Single chart; per-token persistence only |
| W10 | Corporate-action policy | BE | No split/bonus adjustment |
| W11 | Retention configurability | BE | Fixed 30 IST sessions |
| W12 | Backfill budget / prefetch | BE | 6 REST requests per read |
| W13 | F&O bars through the chart | BE | Not verified for FUT/CE/PE |
| W14 | Bar data-quality checks | BE | Volume null/0, duplicates, I1 sanity |
| W15 | Session-aware chart axis | FE | Holiday/weekend gaps not audited |
| W16 | Live-market verification | OPS | Forming-bar merge only tested after hours |
| W17 | Chart bundle size | FE | 1,456 kB (395.8 kB gzip) |

---

## 6. Wishlist register — catalogs and pickers

| ID | Change | Phase | Status |
|---|---|---|---|
| W18 | One shared instrument picker component | 1 | Decided |
| W19 | Central instrument catalog contract (all classes) | 0/1 | Decided |
| W20 | Chart picker inside Vela topbar via contributed action + remote search | 2 | Decided |
| W21 | Central indicator catalog + catalog-driven chart menu | 3 | Decided |
| W22 | Catalog feeds scanners/alerts (backend evaluation) | 4 | Decided, phased last |
| W23 | Code-first catalog; DB for user state only | 3/4 | Decided |
| W24 | Shared picker behavior spec + tests | 1 | Decided |
| W25 | Indicator parity fixtures | 4 | Decided |
| W26 | Read-only `/api/indicators` projection | 4 | Decided |
| W27 | Fix search relevance (single-pass ranked read model + canonical-id dedupe) | 0 | New |
| W28 | Optional FTS5 trigram index | — | Dropped from v1: ranked cash-type query measures 34–53 ms |
| W29 | Local hot set for instant suggestions | 1 | New |
| W30 | Catalog hygiene: 7,121 NULL-type Upstox `NSE_EQ` rows | 0 (exclude) / later (classify) | New |
| W31 | Structured F&O selection mode | 1 | New |

---

## 7. Boundary and risk register

| Risk | Mitigation |
|---|---|
| Search changes regress chart deep links | Exact mode (`find_by_exact_symbol`) is authoritative; router tests unchanged |
| Ranked cash-only search hides derivatives | Derivative intent still parsed (`nifty 25000 ce`, `reliance future`); tests keep those paths green; structured F&O mode covers discovery |
| Dedupe picks a provider that cannot serve bars | Rank only enabled providers; prefer a provider with an active session when tokens are absent (Fyers restore can fail); keep the provider token in the result; the picker passes `provider` to the bars API explicitly |
| New read-model kwargs change existing callers | Defaults preserve alphabetical ordering and current filters; only the ranked mode opts in |
| Chart picker remount loses layout | Persistence key remains per instrument; acceptance test switches A→B→A |
| Overriding the indicator slot loses built-ins | Build menu from `nativeIndicatorDescriptors()`; test the full type list |
| Indicator evaluation diverges from Vela | Parity fixtures; chart-only flag until parity passes |
| Bar feed subscription change | Protected feed lifecycle; not part of this plan |
| Auth regression | No auth file in any allowed surface; login smoke test remains mandatory |

---

## 8. Open questions

1. Phase 0: freeze `/api/instruments/search` as legacy-only and deprecate it later,
   or keep it as the raw-row endpoint for non-picker consumers? Exact resolution now
   lives on `/api/market/search?exact=1`.
2. Phase 1: should the hot set be per-user (server) or per-browser (localStorage)?
3. Phase 2: topbar action label — static "Symbol" plus watermark, or a dynamic label
   that shows the current ticker (requires re-registration on symbol change)?
4. Phase 3: which indicator set ships in v1 of the catalog?
5. Phase 4: which surface gets backend indicators first — scanners or condition
   alerts?
6. Phase 0/1: for short prefixes (`REL`), accept alphabetical tie-breaks
   (`RELAXO` before `RELIANCE`) or boost a curated index/underlying list in the
   client hot set? The server has no popularity signal.

---

## 9. Verification gates (per phase)

```
Backend:  python -m ruff check <touched paths>
          python -m pytest -q <targeted tests> + test/test_architecture_boundaries.py
Frontend: bun x tsc --noEmit
          bun x tsc -p tsconfig.test.json --noEmit
          bun x vitest run <targeted tests>
          bun x vite build
Browser:  Playwright at 1440x900, zero console errors, zero failed requests
```

Baseline note: six frontend test files (alerts, fnoFormat, marketMap, news,
scanners, sectorHeatmap) fail before this work; they are out of scope and must not
be mistaken for regressions.

---

## 10. Changelog

- 2026-10-27 — Initial persisted plan; decisions D1–D6 locked; phases 0–5 defined;
  W1–W31 registered.
- 2026-10-27 (v1.1) — Code-read streamlining pass: adopt the existing ranked
  `/api/market/search` instead of writing a new search service; reuse canonical
  identity/provider preference for dedupe (all duplicates are cash equities);
  keep the F&O underlying table as a specialized universe view; use Vela's
  `registerWidgetAttachment` for the in-chart picker lifecycle; replace fuzzy
  scanner navigation with the exact resolver; classify the 7,121 untyped debt-like
  rows instead of treating them as equities.
- 2026-10-27 (v1.2) — Second code-read pass with measurements:
  - found that generic search does **not** dedupe (`_select_one_provider` is used
    only by futures/option chains), so Phase 0 adds an explicit canonical-id dedupe;
  - measured the multi-probe ranked fallback at 370–538 ms and a single-pass
    cash-type ranked query at 34–53 ms, so the probe loop is replaced rather than
    kept; FTS5 (W28) is dropped from v1;
  - `find_by_exact_symbol` is internal-only, so Phase 0 exposes an exact mode and
    Phase 1 switches `resolveInstrument` off its `results[0]` fallback;
  - `store.search_instruments(**kw)` forwards kwargs, so `core/persistence/store.py`
    is removed from Phase 0's surface;
  - untyped rows are excluded by type scope in Phase 0; classification (W30) is
    deferred until a live master check;
  - acceptance reworded to "cash before option noise" because the catalog has no
    popularity signal.
