# MarketHub — Unified Catalog, Picker and Chart Plan

| | |
|---|---|
| **Plan ID** | `MH-UCP-2026-10-27` |
| **Status** | Phase 4 design proposal for owner review; no Phase 4 code authorized or implemented |
| **Prepared** | 2026-10-27 |
| **Revision** | v1.5 (merges the remote official SDK/API review into v1.4) |
| **Branch** | `fno-provider-agnostic` |
| **Scope** | Chart experience, bars backend, unified instrument catalog, unified indicator catalog |
| **Verified against** | Branch code and read-only catalog evidence recorded in v1.2; official Vela, Fyers and Upstox docs reviewed in v1.3; the Vela 0.9.0 documentation set and the installed `@luxalgo/vela` type definitions and bundle reviewed in v1.4 |

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
| The existing `/instruments` route is catalog **admin** (segment enablement + master re-sync), not a symbol browser | [frontend/src/features/instruments/InstrumentsView.tsx:59](file:///D:/Temp/MarketHub/frontend/src/features/instruments/InstrumentsView.tsx#L59); [router.tsx:59](file:///D:/Temp/MarketHub/frontend/src/routes/router.tsx#L59) |
| Navigation is data-driven, one line per entry | [frontend/src/layouts/nav.ts:14](file:///D:/Temp/MarketHub/frontend/src/layouts/nav.ts#L14) |
| Shared UI primitives exist (Button/Input/Select/Modal/Tabs) | [frontend/src/components/ui.tsx](file:///D:/Temp/MarketHub/frontend/src/components/ui.tsx) |
| Client search wrappers | `searchInstruments`, `resolveInstrument` — [frontend/src/api/market.ts:152](file:///D:/Temp/MarketHub/frontend/src/api/market.ts#L152) |
| Chart provider declares `enumerate:false`, `stream:false` | [frontend/src/features/chart/chartData.ts:141](file:///D:/Temp/MarketHub/frontend/src/features/chart/chartData.ts#L141) |
| Vela topbar configured without a symbol slot | [frontend/src/features/chart/VelaChart.tsx:51](file:///D:/Temp/MarketHub/frontend/src/features/chart/VelaChart.tsx#L51) |
| Only the left side is declared; the right side keeps Vela's defaults (`actions`, `alerts`, `panels`, `screenshot`) | same line; `TOPBAR_DEFAULT_RIGHT` — [statusline-model-C3yIY9WE.d.ts:231](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/statusline-model-C3yIY9WE.d.ts#L231) |
| The host does not handle Vela's typed-letter chord, so today a typed letter opens Vela's own symbol dialog with an empty pool | §2.4, "Typing chord" rows |
| Persistence keyed per instrument token | [frontend/src/features/chart/VelaChart.tsx:53](file:///D:/Temp/MarketHub/frontend/src/features/chart/VelaChart.tsx#L53) |
| Dependencies | React 19, TanStack Query, zod, Vela 0.9.0; no combobox/virtualization/fuzzy library |

### 2.4 Vela capabilities and limits

Verified against the Vela 0.9.0 documentation (`docs.luxalgo.com/vela`,
`architecture/modules`, `contributing/plugin-sdk`, `contributing/adding-a-data-provider`,
`user/workspace`, `user/data-providers`, `user/options`) and the installed
`frontend/node_modules/@luxalgo/vela` type definitions and bundle.

| Fact | Evidence |
|---|---|
| Native symbol index is built **once** at provider registration; no refresh API | `ProviderRegistry.buildIndex()` in `node_modules/@luxalgo/vela/dist/chunk-XIRSR3D6.js` |
| Native symbol slot cannot be overridden; `indicators` and `screenshot` can | `OVERRIDABLE_TOPBAR_IDS` — [contributions-BjefWpxQ.d.ts:2220](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2220) |
| Contributed topbar actions: `align`, `order`, `iconOnly`, `mobile`, a **static** `label`, `when(ctx)` | `WidgetActionDescriptor` — [contributions-BjefWpxQ.d.ts:2082](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2082); official SDK docs clarify `align`/`order` are suggestions and the host's topbar composition controls final placement |
| Topbar composition is a per-side **complete contract** and freezes that side; naming a contributed id pins it at that position (overriding `align`/`order`); unpinned actions need the `'actions'` flow slot | `TopbarComposition` / `resolveTopbarComposition` — [statusline-model-C3yIY9WE.d.ts:218](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/statusline-model-C3yIY9WE.d.ts#L218); shell option at `:273`; `user/workspace#composing-the-topbar` |
| Default composition | left `["symbol","timeframes","style","layout","indicators","actions","undo-redo"]`, right `["actions","alerts","panels","screenshot"]` — declared at [statusline-model-C3yIY9WE.d.ts:229](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/statusline-model-C3yIY9WE.d.ts#L229), values in `chunk-YRCL3WMD.js` |
| Overriding `'indicators'` also suppresses the native picker's construction and takes over its mobile stop and `/` chord | [contributions-BjefWpxQ.d.ts:2221](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2221) |
| Attachments mount **once per widget/workspace** at construction (late registrations on `refreshActions()`); `ctx.host` is the workspace root | `WidgetAttachment` — [contributions-BjefWpxQ.d.ts:2127](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2127); `mountAttachments()` in `chunk-4WAHYL7Y.js` |
| `WidgetContext` exposes live getters plus `openSymbolSearch`, `togglePanel`, `dockStrip`, `stateChanged`, `i18n`, `host`, `pointer` | [contributions-BjefWpxQ.d.ts:1986](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L1986) |
| **Typing chord is unconditional**: a letter or `0` opens the native symbol picker seeded with it; digits 1–9 open the timeframe entry. Topbar composition does **not** gate this chord (unlike `'screenshot'`) | `routeTyping` / `resolveTyping` in `chunk-4WAHYL7Y.js`; `user/workspace#keyboard` |
| The native picker's pool is the aggregated provider index (`feed.symbols()`); with `enumerate:false` the pool is empty | `this.symbolPicker.setSource(() => this.feed.symbols())`; `buildIndex` runs only with `listSymbols` |
| `registerSymbolRanking` only reorders/injects rows in that aggregated pool, cached per pool change — never per keystroke, never a remote query | `SymbolRankingHook` — [contributions-BjefWpxQ.d.ts:2401](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2401); `contributing/plugin-sdk#symbol-ranking` |
| Symbol resolution: an explicit `PROVIDER:` prefix must name a registered provider (or a declared descriptor `prefix`); bare tickers need an index | `ProviderRegistry.resolve` in `chunk-XIRSR3D6.js` |
| Our provider's tickers are composite (`NSE:RELIANCE`), wrapped as `markethub:NSE:RELIANCE`; they do not fit a native descriptor without a registered provider prefix | [frontend/src/features/chart/chartData.ts](file:///D:/Temp/MarketHub/frontend/src/features/chart/chartData.ts) `chartSymbol` |
| `registerStatePersistence`: `scope:'cell'` → per-chart `charts[i].ext`; `scope:'global'` → **document root** `state.ext`; only keys present in the document are restored; payloads are untrusted | [contributions-BjefWpxQ.d.ts:2364](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2364); `contributing/plugin-sdk#state-persistence` |
| Side panels are dockable contributed columns (`registerSidePanel`), toggled from `'panels'` and via `ctx.togglePanel`; width persists | [contributions-BjefWpxQ.d.ts:2172](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2172) |
| Optional provider ports: `listSymbols`, `getSymbolInfo`, `resolveSymbolIcon`, `subscribe`, `getCalendar`, `capabilitiesFor` | [DataProvider-BGlB1zSc.d.ts:145](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/DataProvider-BGlB1zSc.d.ts#L145) |
| Shell `timezone` accepts an IANA zone or `'exchange'` (default `Etc/UTC`) | [statusline-model-C3yIY9WE.d.ts:266](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/statusline-model-C3yIY9WE.d.ts#L266) |
| Kit components must receive `host: ctx.host`, or they portal to `<body>` outside the theme's CSS variables | `contributing/plugin-sdk` — "Two rules keep actions portable" |
| Extension seams are the plugin/contribution registries, the provider port, renderer features and the state document; core/render internals are not an extension surface | `architecture/modules`, `architecture/boundaries` — the plan uses only these seams |
| Vela ships Popover/Menu/Dialog/Tooltip/Select primitives | `node_modules/@luxalgo/vela/dist/ui.d.ts` |
| Indicator type metadata is enumerable | `nativeIndicatorDescriptors()`, `chart.availableNativeIndicators()` |
| Indicator slot can be replaced and indicators added through the shell | [contributions-BjefWpxQ.d.ts:2029](file:///D:/Temp/MarketHub/frontend/node_modules/@luxalgo/vela/dist/contributions-BjefWpxQ.d.ts#L2029) |

**Typing-chord defect (code-derived, browser check in Phase 2).** The chart hides
`'symbol'` from `topbar.left`, which removes the button and its mobile entry but **not**
the typing chord. `VelaWorkspace` attaches `routeTyping` to the root unconditionally,
and the native picker's source is the provider index, which is empty because our
provider declares `enumerate:false`. Consequence: with the chart focused, typing any
letter opens Vela's symbol dialog with no rows. This cannot be fixed by `registerSymbolRanking`
(which only reorders the same empty pool) or by `listSymbols` (one-shot 151k index;
composite tickers do not fit native descriptors). It is fixed in Phase 2 by bridging
the chord to the shared picker (W32), because "typing a letter opens symbol search" is
core Vela UX that should stay true — with MarketHub's picker behind it.

### 2.5 Backend indicators

There is **no** indicator concept in the backend today (no definitions, no evaluation,
no persistence). Indicators exist only as Vela natives in the browser.

### 2.6 Official API / SDK review (2026-10-27)

Reviewed official documentation before implementation. These checks refine the plan; they do not prove that MarketHub's adapters are currently correct or that live broker calls have been exercised.

| Area | Official documentation finding | Plan impact |
|---|---|---|
| Vela plugin SDK | `registerWidgetAction` contributes a topbar action; `registerWidgetAttachment` mounts once per widget and returns a disposer. `align`/`order` are suggestions: host `topbar.left` / `topbar.right` composition can pin the final position. Late registrations require `widget.refreshActions()`. | Keep the attachment + action design and mount the picker once. Do not assume `align: 'left'` alone fixes placement; configure the host slot/position and test desktop/mobile behavior. Register at module import time where possible. |
| Fyers historical data | Official v3 docs explicitly list 240-minute resolution (`resolution: "240"`) and document per-request history windows/rate-plan constraints. | The W4 statement that Fyers lacks 240-minute candles is incorrect as a provider API claim. First inspect MarketHub's adapter normalization and tests; fix adapter mapping if that is the actual gap. Do not add client-side aggregation until direct API behavior is verified. |
| Upstox historical candles | Official Historical Candle V3 supports minute intervals 1–300 and hour intervals 1–5, with interval-dependent history limits. V2 historical candles are marked deprecated in the official docs. | For Upstox, evaluate V3 in the existing adapter before implementing additional timeframe work. Respect provider-specific maximum ranges and response semantics. Do not assume identical interval support or history windows across providers. |
| Dependencies | The reviewed picker and chart integration APIs are already available through the existing React/Vela stack; the search plan's measured single-pass query does not justify FTS5 or a fuzzy-search package for v1. | No new package is approved by this review. Add one only if a documented, tested requirement cannot be met with the existing stack. |

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
| **D7** | Vela's typed-letter chord must open the **shared** picker, never Vela's native symbol dialog. The host bridges the chord; the native picker stays unused in every surface (no `listSymbols`, no `registerSymbolRanking`, no `ctx.openSymbolSearch`). Digits 1–9 stay Vela's timeframe entry. |

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
        ├── catalog page (/catalog, Phase 1.5 — first host)
        ├── chart  (pinned topbar action + typed-letter bridge, Phase 2)
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
| Topbar placement | Custom toolbar chrome | An id-pinned contributed action in `topbar.left` (the `'symbol'` slot cannot be overridden) |
| Typing-to-search | Keyboard shim library | One capture-phase listener in the chart attachment, mirroring Vela's own guard |
| Picker container | Docked side panel | Dialog mounted in `ctx.host` (side panel evaluated and deferred — §4.2 item 16) |
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
    mounts a contributed UI once per workspace/widget (at construction) and returns a
    disposer; the topbar action opens that mounted picker, not a rebuilt one.
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
15. **Do bridge the typed-letter chord; do not leave Vela's native picker reachable.**
    The chord is core Vela UX and cannot be disabled by composition. The attachment
    adds a capture-phase `keydown` listener on `ctx.host`, mirrors Vela's guard
    (no ctrl/meta/alt, not composing, not an editable target, no open dialog), matches
    Vela's letter/`0` predicate, then `preventDefault()` + `stopPropagation()` and
    opens the shared picker seeded uppercase. Digits 1–9 pass through untouched.
    Never call `ctx.openSymbolSearch()`.
16. **Do not dock the picker as a side panel in v1.** `registerSidePanel` works and
    is attractive for a future watchlist panel, but a permanently docked column
    costs plot width; the reference UX is a modal picker. The dialog is mounted in
    `ctx.host` so Vela theme variables apply (kit components portal to `<body>`
    without an explicit host).
17. **Do not persist app-owned picker state through `registerStatePersistence`.**
    `scope:'global'` writes into the **document** root, and the chart's document is
    keyed per instrument (`persist: markethub-chart:<token>`), so a hot set would be
    duplicated and diverge across instruments. Hot set, recents and filters stay in
    MarketHub's own `localStorage` key. A `scope:'cell'` handler is reserved for
    state that genuinely belongs to one chart cell.
18. **Do not build a dynamic topbar label in v1.** `WidgetActionDescriptor.label` is
    static; showing the ticker there needs re-registration plus
    `workspace.refreshActions()` on every switch. The statusline and watermark already
    show the current ticker, so the action stays "Symbol" (see open question 3).
19. **Do pin the action id explicitly.** The shell resolves composition at
    construction and freezes a declared side; naming our action id in `topbar.left`
    places it exactly and overrides `align`/`order`. `'indicators'` stays in the list
    for Phase 3, and the undeclared right side keeps Vela's defaults.
20. **Do not grow the admin `/instruments` page into the symbol browser.** That page
    owns segment enablement and master re-sync (a destructive action); a read-only
    browse surface there mixes audiences. The browser is its own `/catalog` route
    (Phase 1.5), and `/instruments` stays untouched.

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

### Phase 1.5 — Instrument catalog page (picker proving ground)

**Goal:** give the shared picker a real, app-wide home and prove it against the full
catalog before it enters Vela. This is deliberately thin: one page, one route, one
nav entry, no new backend.

**Changes**
- New `frontend/src/features/catalog/CatalogView.tsx` at `/catalog`, registered in
  `src/routes/router.tsx` and in `src/layouts/nav.ts` (one entry under "More",
  label "Catalog"; the owner can move that one line to "Markets").
- Uses the Phase 1 `InstrumentPicker` as its search surface (no second search
  implementation, no page-local debounce/abort) and consumes the same
  `GET /api/market/search` contract (plus `exact=1` for "Open chart"). The committed
  result renders in the page's own table from the compact fields (symbol, name, type,
  exchange, provider badge, derivative fields) at the picker's `limit <= 50`. Type and
  exchange filters come from the picker's controls; the F&O structured mode is the
  Phase 1 component, not a page-specific copy.
- "Open chart" navigates to the existing chart route with the exact identity from the
  result (bare `provider_token` + `provider`, per the Phase 1 contract trap); a
  "Details" surface shows only the compact fields — no new read model.
- Leave the existing `/instruments` admin page (segments + master sync) untouched; it
  is not a symbol browser and is not renamed.
- No virtualization or fuzzy library (D4); the contract caps result size.
- Empty state until a query is typed; the hot set (W29) may populate suggestions.

**Allowed surface**
`frontend/src/features/catalog/*` (new), `frontend/src/routes/router.tsx` (one
route), `frontend/src/layouts/nav.ts` (one entry), `frontend/src/styles/features/catalog.css`,
tests.

**Zone**
`WEBUI FEATURES` + `WEBUI SHELL` (route and nav registration only).

**Out of scope / protected**
No backend edits, no new API, no chart/Vela files, no watchlist mutation changes, no
persistence schema.

**Acceptance**
- `/catalog` exists and is reachable from the nav; `/instruments` still renders its
  admin controls unchanged.
- `REL`, `NIFTY`, `RELIANCE` behave as the Phase 0 acceptance (cash before option
  noise, index before derivatives, one deduped row with provider attribution).
- Keyboard: ArrowUp/Down, Enter, Escape; focus returns to the search input on close.
- "Open chart" lands on the exact instrument; no `results[0]` substitution.
- Requests abort on a new keystroke and on unmount; loading, empty and error states
  are visible and distinct.
- `vitest` covers result rendering and the navigate-with-exact-identity wiring;
  browser pass at 1440x900 and mobile width, zero console errors and failed requests.

---

### Phase 2 — Chart selector inside the chart layout

**Goal:** remove the host toolbar row; select symbols from the chart topbar.

**Changes**
- Reuse the picker proven in Phases 1 and 1.5 **unchanged**: only the mount container
  and trigger move. This phase adds no search code.
- One module (`frontend/src/features/chart/chartSymbolPicker.tsx`) registers, at
  import time:
  - a contributed topbar action with a stable exported id (`MARKETHUB_SYMBOL_ACTION_ID`,
    `align:'left'`, static label "Symbol") that calls the picker controller;
  - a widget **attachment** that mounts once per workspace into `ctx.host` and returns
    a disposer. It creates **one** React root hosting the shared picker (no root per
    click), passes `host` to Vela kit components, and exposes `open(seed?)` through the
    module controller. `ChartView` registers the `onSelect`/`onClose` handlers with that
    controller on mount, so URL updates and remounts stay in React. The controller is
    keyed by `ctx.host`, so a second mounted workspace cannot cross-open the wrong
    picker.
- Pin `MARKETHUB_SYMBOL_ACTION_ID` in `topbar.left` (for example after
  `'timeframes'`, before `'style'`) and keep `'indicators'` for Phase 3. The stateful
  `'symbol'` slot cannot be overridden; an id-pinned contributed action is the
  supported seam. The undeclared right side keeps its defaults.
- **Typing bridge (W32):** the attachment also adds a capture-phase `keydown`
  listener on `ctx.host`. It mirrors Vela's own guard (no ctrl/meta/alt, not
  composing, not an editable target, no open dialog), matches Vela's letter/`0`
  predicate, then `preventDefault()` + `stopPropagation()` and opens the shared
  picker seeded uppercase. Digits 1–9 stay Vela's timeframe entry. No call to
  `ctx.openSymbolSearch()`.
- On selection: update URL (`key/sym/ex/type`), remount the chart with the new
  per-instrument persistence key.
- Remove the `.chart-toolbar` row and its search state from `ChartView.tsx`; keep
  the error banner and empty state.
- Keep `capabilities.enumerate: false`; no `listSymbols()`, no
  `registerSymbolRanking`, no native symbol dialog in any path.

**Allowed surface**
`frontend/src/features/chart/*` (including the new attachment/bridge module),
`src/styles/features/chart.css`, `src/routes/*` only if the route contract changes,
chart tests.

**Zone**
`WEBUI FEATURES` + `WEBUI SHELL` (if the layout changes).

**Out of scope / protected**
Vela provider `getBars` contract, bars API, auth, feeds.

**Acceptance (real browser)**
- Picker opens from inside the chart topbar; host toolbar row is gone.
- Typing `r` on the chart opens the **shared** picker seeded `R`; Vela's native
  symbol dialog never appears (assert no `.vela-sp` dialog); typing `2` still opens
  Vela's timeframe entry.
- Selecting from the picker works with mouse and keyboard; Escape closes it.
- The picker is themed (mounted under `ctx.host`, not portaled to `<body>`), and
  exactly one React root exists for the workspace lifetime.
- Search → RELIANCE and INFY updates URL, chart and watermark; reload restores.
- Drawings/indicators persist per instrument across switches.
- The chart imports the shared picker; no search/debounce code remains in
  `frontend/src/features/chart/`.
- Mobile: the left-aligned action gets its bottom-bar stop; verify placement with
  `'symbol'` hidden.
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
  persistence stay correct. The override also owns the slot's mobile stop and the `/`
  chord, and stops the native picker from being constructed — so the catalog menu
  becomes the only indicator entry point. `'indicators'` must stay in `topbar.left`
  for the override to render.
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
- The `/` chord opens the catalog menu; Vela's native indicator dialog is never
  constructed.
- Tests cover catalog mapping and menu behavior.

---

### Phase 4 — App-wide indicator catalog (condition alerts first)

**Status:** design proposal for owner review; no implementation authorized by this section.

**Goal:** make one versioned catalog the authority for chart indicator identity and backend-evaluable indicator behavior, without making the database a definition store.

**Proposed owner decisions (pending explicit approval)**

- **Evaluator v1:** SMA, EMA and RSI, plus explicit cross-above / cross-below operators. MACD and Bollinger Bands remain chart-only until their own parity fixtures pass.
- **First consumer:** condition alerts. Scanner integration is deferred; scanners are universe-ranking screens and need a separate evaluation/cost design.
- **Catalog authority:** a checked-in JSON spec is the shared, versioned contract. The database stores user preferences only (favorites, defaults, last-used and templates), never indicator definitions or formulas.

**Changes**

1. Add a checked-in JSON catalog spec. Each indicator entry has a stable catalog ID and version, Vela native type, display metadata, category, parameter schema/defaults, required input series, warm-up/missing-value policy, and an evaluator key or an explicit `chartOnly` state. Cross operators are a separately typed, allow-listed operator set; they are not arbitrary formulas.
2. Validate the same JSON in TypeScript and Python:
   - TypeScript validates the schema and confirms every declared Vela native type exists in the installed Vela descriptor registry and is supported by the active chart.
   - Python validates the schema and confirms every non-chart-only evaluator key resolves to a registered evaluator with compatible parameters.
   - Invalid JSON, unknown native types, duplicate IDs, unknown evaluator keys, or invalid parameter defaults fail CI/startup clearly. A chart-only entry is valid without a backend evaluator.
3. Add a pure indicator math module under `core/` with no I/O, provider imports, adapters, or database access. Add an app-level adapter/registry that maps catalog IDs to the pure evaluator functions.
4. Add read-only `GET /api/indicators`, generated from the checked-in spec and merged with user preference state where that state is available. Never accept formulas, Python expressions, arbitrary evaluator names, or catalog definitions from API callers.
5. Add deterministic parity fixtures: fixed OHLCV inputs and parameter sets, Vela native outputs captured/verified against the same bars, Python outputs, declared warm-up behavior, and per-indicator numerical tolerances. Include flat, rising, falling, missing/short-history and boundary cases. Do not mark an evaluator backend-ready until its fixtures pass.
6. Integrate catalog IDs into condition-alert validation/evaluation. Reject chart-only indicators and unsupported parameters with a clear validation error before evaluation. Keep existing quote-only alert behavior backward compatible.
7. Store only user-specific preferences in the existing persistence mechanisms if a suitable supported surface exists. Any schema migration is a separate explicit approval; do not introduce a new table merely for catalog definitions.
8. Defer scanner integration. Revisit it after condition-alert semantics, evaluator cost, history requirements and universe-scale evaluation have been measured.

**Semantics that must be explicit before parity can pass**

- SMA/EMA/RSI input series and parameter defaults.
- EMA seed/warm-up behavior and RSI smoothing/initialization convention.
- Output alignment and how warm-up values are represented (null/absent, never silently coerced to zero).
- Missing bars, zero/invalid prices, insufficient history, and numeric tolerance per evaluator.
- Cross operators compare current and previous valid values and define behavior across missing values; they do not trigger on an uninitialized prior value.

**Allowed surface**

New versioned catalog JSON; a pure `core/` indicator module; app-level evaluator registry/adapter; read-only indicator route; TypeScript catalog validation; targeted tests and parity fixtures. Condition-alert integration is a separate subtask in its own zone.

**Zone**

New `core/` module (pure math) + app-level adapter + `TRANSPORT/API` + `WEBUI FEATURES`. `app/condition_alerts.py` is a red-zone file and requires a separate explicit task declaration/approval before it is changed. No scanner files in this phase.

**Prerequisites**

- Commit the verified chart work (shared picker/chart integration/indicator menu) as a scoped commit before implementation, so the Phase 4 diff is reviewable and does not mix unrelated changes.
- Confirm the exact installed Vela descriptor IDs and how parity fixtures can deterministically capture native outputs. Documentation and types alone are not evidence of numeric parity.
- Baseline the existing alert tests and architecture-boundary tests before editing.

**Acceptance**

- One checked-in JSON spec is consumed by both TS and Python; no DB-backed definition source exists.
- TS rejects catalog native types absent/unsupported in Vela; Python rejects unknown evaluator keys and invalid parameter definitions.
- `GET /api/indicators` is read-only and emits stable catalog IDs, metadata, parameters, and capability status.
- SMA, EMA, RSI and cross operators are enabled for backend use only after deterministic parity fixtures pass within their declared tolerances.
- Chart-only indicators are rejected by condition alerts with a clear message and no runtime formula execution.
- Existing quote-only alert behavior remains unchanged.
- Architecture-boundary tests, targeted backend/frontend tests, type checks and frontend build pass; login/auth smoke checks remain green.
- Scanner integration and all Phase 5 W1–W17 items remain deferred unless separately approved.

---

### Phase 5 — Chart and bars gaps (existing list)

Grouped for later scheduling; all were verified during the 2026-10-27 chart work.

| ID | Item | Area | Notes |
|---|---|---|---|
| W1 | Real market calendar / status badge | FE+BE | Vela `getCalendar`; currently "(inferred)" |
| W2 | Push updates (`subscribe`) | FE+BE | Currently 3 s polling, `limit:2` |
| W3 | Subscribe the charted instrument | BE | Feed lifecycle — protected, separate approval |
| W4 | Provider-aware timeframes | FE+BE | `240` is supported by official Fyers Historical Data docs; verify MarketHub adapter mapping and Upstox V3 interval/range limits before changing the normalized timeframe contract |
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
| W29 | Local hot set for instant suggestions | 1 | New (MarketHub `localStorage`, not Vela persist — §4.2 item 17) |
| W30 | Catalog hygiene: 7,121 NULL-type Upstox `NSE_EQ` rows | 0 (exclude) / later (classify) | New |
| W31 | Structured F&O selection mode | 1 | New |
| W32 | Bridge Vela's typed-letter chord to the shared picker (native dialog unused) | 2 | New (D7) |
| W33 | Instrument catalog page `/catalog` — shared picker's first host | 1.5 | New |

---

## 7. Boundary and risk register

| Risk | Mitigation |
|---|---|
| Search changes regress chart deep links | Exact mode (`find_by_exact_symbol`) is authoritative; router tests unchanged |
| Ranked cash-only search hides derivatives | Derivative intent still parsed (`nifty 25000 ce`, `reliance future`); tests keep those paths green; structured F&O mode covers discovery |
| Dedupe picks a provider that cannot serve bars | Rank only enabled providers; prefer a provider with an active session when tokens are absent (Fyers restore can fail); keep the provider token in the result; the picker passes `provider` to the bars API explicitly |
| Catalog page grows its own search | Phase 1.5 acceptance: no debounce/abort/fetch of its own; it imports the picker and the shared API wrappers |
| New read-model kwargs change existing callers | Defaults preserve alphabetical ordering and current filters; only the ranked mode opts in |
| Chart picker remount loses layout | Persistence key remains per instrument; acceptance test switches A→B→A |
| Overriding the indicator slot loses built-ins | Build menu from `nativeIndicatorDescriptors()`; test the full type list |
| Typing bridge depends on Vela's DOM listener ordering | Pin the exact `@luxalgo/vela` version; guard test asserts no native picker after a typed letter; if a future release exposes an option or capture hook, switch to it |
| Vela's native picker leaks back into the app | D7: no `listSymbols`, no `registerSymbolRanking`, no `ctx.openSymbolSearch`; browser assertion on `.vela-sp` |
| Static action label disappoints | Statusline and watermark already show the ticker; re-registration via `refreshActions()` is the documented escape hatch |
| Indicator evaluation diverges from Vela | Parity fixtures; chart-only flag until parity passes |
| Bar feed subscription change | Protected feed lifecycle; not part of this plan |
| Auth regression | No auth file in any allowed surface; login smoke test remains mandatory |

---

## 8. Open questions

1. Phase 0: freeze `/api/instruments/search` as legacy-only and deprecate it later,
   or keep it as the raw-row endpoint for non-picker consumers? Exact resolution now
   lives on `/api/market/search?exact=1`.
2. Phase 1: should the hot set be per-user (server) or per-browser (localStorage)?
   Evaluated: per-browser. Vela persistence cannot host it (global entries live in the
   per-instrument document), and a server store belongs with the future
   favorites/watchlist work, not v1.
3. Phase 2: topbar action label — static "Symbol" plus watermark, or a dynamic label
   that shows the current ticker? Verified: `label` is static, so a dynamic label
   requires re-registration plus `refreshActions()` on every switch. Recommendation:
   static label in v1; the statusline and watermark carry the ticker.
4. Phase 4 proposed evaluator set: SMA/EMA/RSI plus cross operators; MACD and
   Bollinger remain chart-only until parity fixtures pass. Pending explicit owner approval.
5. Phase 4 proposed first consumer: condition alerts; scanner integration is deferred.
   Pending explicit owner approval.
6. Phase 0/1: for short prefixes (`REL`), accept alphabetical tie-breaks
   (`RELAXO` before `RELIANCE`) or boost a curated index/underlying list in the
   client hot set? The server has no popularity signal.
7. Phase 2: with `'symbol'` hidden, a left-aligned contributed action takes the
   mobile bottom-bar stop. Confirm that is the desired mobile entry point, or move the
   action to the three-dots sheet with `mobile:'menu'`.

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
- 2026-10-28 (v1.6) — Expanded Phase 4 as a review-only design proposal: code-first JSON catalog, TS/Python validation, pure core math + app adapter, read-only API, parity semantics, proposed SMA/EMA/RSI + cross operators, condition-alert-first scope, explicit red-zone approval and chart-work commit prerequisite. No implementation authorized; owner decisions remain pending. Phase 5 W1–W17 remains deferred.
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
- 2026-10-27 (v1.3) — Vela full-docs and installed-API study:
  - verified the topbar contract: defaults, per-side completeness/freeze, id pinning
    for contributed actions, and the `'actions'` flow slot;
  - confirmed only `'indicators'`/`'screenshot'` are overridable, and that the
    `indicators` override also owns the mobile stop and `/` chord;
  - found the **typing-chord defect** (letters open Vela's native picker even with
    `'symbol'` hidden, and the pool is empty without `listSymbols`), and added D7 +
    W32: a capture-phase bridge to the shared picker, with a guard test;
  - rejected `listSymbols`/`registerSymbolRanking` as fixes (one-shot 151k index,
    composite tickers, pool-only reorder, no remote search);
  - added attachment facts (once per workspace, `ctx.host`, late `refreshActions()`);
  - resolved open question 2 (hot set stays in MarketHub localStorage, not Vela
    persistence) and documented the mechanism for question 3 (static label v1);
  - evaluated and deferred a docked side-panel picker; dialog stays;
  - recorded provider ports (`getCalendar`, `subscribe`, `resolveSymbolIcon`,
    `capabilitiesFor`) against W1/W2 and the `'exchange'` timezone option.
- 2026-10-27 (v1.4) — Catalog page added as Phase 1.5 (W33): the shared picker's first
  real host, placed after search correctness and before the Vela attachment work; a
  separate `/catalog` route because `/instruments` is catalog admin, not a browser.
  Phase 2 now builds on the picker proven on that page.
- 2026-10-27 (v1.3, remote `19868da`) — Official SDK/API review:
  - confirmed the Vela action/attachment lifecycle and that `align`/`order` are
    suggestions with the host's topbar composition controlling final placement;
  - corrected the Fyers 240-minute assumption: official v3 docs support
    `resolution: "240"`, so any gap is in adapter normalization, not the provider API;
  - documented Upstox Historical Candle V3 intervals (1–300 min, 1–5 h) and
    history-window limits, with V2 deprecated;
  - retained the no-new-dependency decision.
- 2026-10-27 (v1.5) — Merge of remote `19868da` into v1.4: added §2.6, reworded the
  front-matter evidence line and the `WidgetActionDescriptor` row, and re-scoped W4
  from "Fyers lacks 240" to adapter verification plus Upstox V3 limits.
