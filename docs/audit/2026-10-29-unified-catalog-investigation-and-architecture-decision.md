# MarketHub Unified Instrument Catalog — Technical Investigation and Architecture Decision

| | |
|---|---|
| **Date** | 2026-10-29 |
| **Type** | Read-only investigation + architecture decision |
| **Status** | Report complete; implementation **not started**; Phase 1 awaiting approval |
| **Repository** | `D:\Temp\MarketHub` |
| **Branch / HEAD** | `fno-provider-agnostic` / `34a2f710f3b51374e15982a3b24dc21bdf028e98` |
| **Remote** | `origin/fno-provider-agnostic` — 0 behind / 6 ahead |
| **Working tree** | 554 porcelain entries (unrelated WIP, untouched) |
| **Backup (read-only)** | `E:\MarketHub-backups\2026-10-28-post-release` — verified present |
| **Vela probe (read-only)** | `D:\Temp\probe\vela-app` — verified present, unmodified |
| **Vela version** | `@luxalgo/vela@0.9.0` — `frontend/package.json:19`, `frontend/bun.lock:75` |

Evidence markers used throughout: **[V]** verified from code, database, official
documentation, or probe output. **[I]** inferred / estimated.

This document is a new artifact. It does not modify application code, tests,
configuration, the existing plan document
(`docs/plans/2026-10-27-unified-catalog-and-chart-plan.md`), the Vela probe, any
database, or any running service.

---

## 1. Executive decision

**Recommendation: Hybrid (Option C), with a hard split of responsibility.**

1. **Vela's native picker becomes the fast, bounded "navigation" surface.**
   Enumerate a curated pool of roughly 600–900 descriptors — every index, every
   live F&O underlying's cash instrument, and every ETF — through
   `listSymbols()`. This gives click-to-browse on the chart and makes the
   documented typing chord useful again.
2. **MarketHub owns the authoritative full catalog.** A server-side, paginated
   browse read model over all **65,840 live logical instruments** (4,186 cash +
   61,654 live F&O contracts), plus the existing ranked search and structured
   F&O mode. This is the only surface that can reach the long tail.
3. **The full catalog is reachable without typing from inside the native
   picker** via a pinned "Browse all instruments…" action row, and redundantly
   from an adjacent topbar action and the `/catalog` page.

### Why this supersedes the current design

The plan's locked decisions **D3** ("Vela's native symbol index is not enabled")
and **D7** ("the native picker stays unused in every surface; the host bridges
the typed-letter chord") were correct for a 151k-row eager index, but are wrong
for a curated ~800-row pool.

The existing probe proves:

- Ranking injection alone renders rows but **never triggers `getBars`**.
- `enumerate:true` + `listSymbols()` makes native picks load candles.
- The native picker's search filters only its enumerated pool.
- Indices have no native category tab.
- A picked ticker arrives **bare** (no exchange).

The hybrid keeps the curated pool strictly a *navigation convenience*: every
row it contains is also in the canonical catalog, carrying the same `provider`
+ `provider_token` identity. The pool is derived from the catalog, never a
second source of truth.

### Why not the alternatives

- **Native only (A):** the picker's search is a local array filter
  (`filterSymbols`), there is no remote query, tabs are a fixed seven-string
  vocabulary with no Index or Options tab, and full enumeration would ship
  ~144k descriptors (~6.6 MB of raw text plus object/index overhead) to every
  browser. It cannot satisfy "browse the full catalog".
- **Catalog only (B):** it discards the Vela-native quick-switch the owner
  explicitly asked for, keeps the fragile capture-phase chord bridge, and
  leaves the chart's own symbol affordance empty.

---

## 2. Current-state findings

### 2.1 Canonical schema and tables — supports the required browsing; no migration needed [V]

`instruments` (schema v24; `SCHEMA_VERSION = 24` at
`core/persistence/modules/schema.py:15`) has primary key
`(provider, instrument_token)` and every field the brief asks for.

Field completeness (read-only query against the live `data/events.db`,
151,485 rows):

| Field | Present | Notes |
|---|---:|---|
| `exchange` | 151,485 | NSE 138,353 / BSE 13,132 |
| `segment` | 151,485 | NSE_FO 124,698 · NSE_EQ 13,387 · BSE_FO 12,983 · NSE_INDEX 268 · BSE_INDEX 149 |
| `instrument_type` | 144,364 | OPTION 136,312 · NULL 7,121 · EQUITY 5,911 · FUTURE 1,369 · INDEX 417 · ETF 355 |
| `underlying` | 144,146 | 223 distinct; 222 live |
| `expiry` | 137,681 | 39 distinct total; 36 live |
| `strike` | 136,312 | options only |
| `option_type` | 136,312 | CE/PE, options only |
| `isin` | 13,387 | NSE_EQ cash only |
| `name` | 151,485 | — |
| `provider_symbol` | 151,485 | — |
| `lot_size` | 151,231 | — |
| `tick_size` | 151,267 | — |

Provider split: **fyers 98,266 / upstox 53,219**.

Indexes (authoritative `sqlite_master`):

| Index | Columns |
|---|---|
| `idx_instr_lookup` | exchange, instrument_type, expiry, underlying |
| `idx_instr_fo` | exchange, instrument_type, underlying, expiry |
| `idx_instr_seg` | segment, instrument_type, tradingsymbol |
| `idx_instr_underlying` | underlying, provider, segment |
| `idx_instr_symbol` | tradingsymbol |
| `idx_instr_name` | name |
| (autoindex) | provider, instrument_token (PK) |

**The 151,485 rows are not 151,485 instruments.** After canonical dedupe:

| Class | Raw rows | Logical instruments |
|---|---:|---:|
| Cash (INDEX + EQUITY + ETF) | 6,683 | **4,186** |
| Live F&O (expiry ≥ 2026-10-29) | 88,022 | **61,654** (488 futures · 61,166 options) |
| Expired contract rows | 49,659 | excluded by default |
| Untyped rows | 7,121 | all Upstox `NSE_EQ` (bonds/SDLs such as `749RJ35`, `KCK`) — never equity |
| **Total browsable live universe** | | **65,840** |

Live F&O detail: 222 live underlyings, 36 live expiries.

**Duplicate and ambiguity findings (important):**

- Provider duplicates are **not confined to cash**. At the *logical-contract*
  level, NIFTY has **1,386 live contracts listed by both Fyers and Upstox**
  (display symbols differ: `NIFTY26N0325000CE` vs `NIFTY 25000 CE 03 NOV 26`).
  SENSEX is likewise dual-listed (fyers 3,200 / upstox 2,654 live option rows).
  Any browse list must dedupe by canonical contract, not by display symbol.
- String-level duplicate groups `(exchange, tradingsymbol, instrument_type)`:
  **2,344 EQUITY + 1 untyped**; extra rows removed: 2,344.
- Cross-exchange bare-ticker collisions: **4 cash symbols** (`ENERGY`, `INFRA`,
  `METAL`, `MID150`, where EQUITY/INDEX/ETF meanings differ by exchange) and
  **2,040 F&O symbols**. A bare ticker is therefore **not** a safe identity key.

Top F&O underlyings by row count: SENSEX 6,232 · NIFTY 5,951 · BANKNIFTY 3,335 ·
NIFTYNXT50 3,320 · MIDCPNIFTY 2,536 · FOCIT 1,998 · BANKEX 1,866 · NIFTYFPI 1,630 ·
FINNIFTY 1,594 · TCS 1,009.

Distinct symbol counts: INDEX 417 (NSE 268 / BSE 149) · ETF 355 · EQUITY 3,567 ·
F&O underlyings 223 (FUTURE underlyings: NSE 219 / BSE 18).

`market_bars` holds 11,395 rows; `PRAGMA user_version` = 24.

### 2.2 Ingestion and provider-specific identifiers [V]

`app/instruments.py`:

| Function | Line | Responsibility |
|---|---:|---|
| `upstox_master_records` | 128 | Parses official Upstox `complete.json(.gz)`; `instrument_key` (`NSE_EQ\|INE002A01018`) → `instrument_token`; `underlying_symbol` → `underlying`; epoch-ms expiry → ISO |
| `fyers_master_records` | 251 | Parses Fyers `<SEGMENT>_sym_master.json`; `fyToken` → `instrument_token`; `symTicker` / `EXCH:SYMBOL` → `tradingsymbol` / `provider_symbol`; `exInstType` → canonical type |
| `_derive_segment` | 317 | Canonical segment via the shared rule in the catalog layer |
| `InstrumentCatalog` | 517 | Sync orchestration, segment enablement, read-model delegation |

Both parsers pass through `canonicalize_record`; `provider` is persisted per
row. Identity is preserved end-to-end as `(provider, instrument_token)`.

### 2.3 Search endpoint, ranking, dedupe, exact resolution [V]

| Layer | Location | Behaviour |
|---|---|---|
| Read model | `search_instruments` — `core/persistence/modules/products.py:543` | `LIKE` on symbol/name (+underlying when ranked); ranked `CASE` exact-symbol → exact-underlying → prefix → contains → name; NSE preference; length/alphabet tie-break; cap 50 ranked / 100 plain |
| Exact read model | `find_by_exact_symbol` — `products.py:637` | `UPPER` equality on symbol **or** underlying; excludes NULL type; cash-before-derivatives ordering; cap 20 |
| Derivative expiries | `derivative_expiries` — `products.py:682` | Distinct sorted expiries; `exchange` recommended for `idx_instr_lookup` |
| Option strikes | `option_strikes` — `products.py:712` | All contracts for underlying+expiry, strike-sorted |
| Equity universe | `equity_universe` — `products.py:745` | NSE_EQ + EQUITY, cap 20,000 |
| Coverage audit | `segment_underlying_summary` — `products.py:799` | Grouped per-underlying counts in one provider segment |
| Semantic search | `MarketIntel.search` — `app/market_intel.py:345` | Parses type words / CE-PE / numeric strike; one ranked cash pass; **applies `_dedupe_rows` at line 427** |
| Exact resolution | `MarketIntel.search_exact` — `market_intel.py:432` | Exact rows only, canonical types, dedupe, cap 20 |
| Dedupe | `_dedupe_rows` — `market_intel.py:268`; `_select_one_provider` — `:245` | Provider-neutral `canonical_id_for_row`; registry preference order; disabled providers rank last |
| Underlying resolution | `_find_underlying` — `market_intel.py:200` | INDEX before EQUITY; targeted queries then normalized scan |
| Compact output | `_compact` — `market_intel.py:305` | `instrument_key`, `provider`, `provider_token`, `native_key`, `canonical_id`, human fields, F&O fields, `lot_size` |
| HTTP | `GET /api/market/search`, handler `_search` — `api/product_routes.py:568` | **`q` mandatory** (400 otherwise); ≤64 chars; `exact=1` → `search_exact` (ignores `types`/`limit`); limit clamped to 50 |

Store wrappers: `core/persistence/store.py` — `search_instruments(**kw)` at 756,
`derivative_expiries` 775, `option_strikes` 786, `equity_universe` 731,
`get_instrument` 796, `instruments_sync_state` 804.

**Plan correction:** the plan's evidence table line 84 says "Generic search does
**not** dedupe". That is **stale** — `MarketIntel.search` now calls
`_dedupe_rows` (line 427). Verified in code.

**Limitations:** no cursor/offset anywhere; `q` required means there is no
"browse" query; `search_exact` caps at 20 and filters canonical types only.

### 2.4 `/catalog` page [V]

`frontend/src/features/catalog/CatalogView.tsx` is a single picker plus a detail
table.

- Passes `limit={50}` and `autoFocus` but **no `hot` rows** (lines 72–83).
- Before typing it renders the empty state at line 125: *"Type a symbol to
  browse the canonical catalog, or pick one from the results."* — precisely the
  owner's complaint.
- "Open chart" correctly carries `provider_token` + `prov` (`chartHref`, line 15).
- Detail fields (lines 36–48): symbol, name, type, exchange, provider,
  underlying, expiry, strike, option type, lot size, display key.

### 2.5 Shared `InstrumentPicker` [V]

`frontend/src/components/InstrumentPicker.tsx`:

- 250 ms debounce; abortable fetch (`AbortController`, line 144).
- `role=combobox` (315), `role=listbox` (487), `role=option` (495).
- Filters: type and venue; structured F&O mode underlying → expiry → strike →
  CE/PE (mode toggle at 341).
- `hot` prop declared at line 52 but **every caller omits it**; `shownRows`
  falls back to `hot` only when the query is empty (line 120).
- It is the right component to grow a browse mode; it simply has no data source
  for the empty state.

### 2.6 `VelaChart` and `MarketHubDataProvider` [V]

`frontend/src/features/chart/VelaChart.tsx`:

- Hides Vela's symbol slot and substitutes `MARKETHUB_SYMBOL_ACTION_ID`
  (topbar composition, line 60).
- `providers: { [VELA_PROVIDER]: () => provider }` (line 56).
- `timeframe:"15"`, `live:true`, `timezone:"Asia/Kolkata"`, `autofocus:true`,
  per-token persistence `markethub-chart:<token>`.

`frontend/src/features/chart/chartData.ts`:

| Member | Line | Behaviour |
|---|---:|---|
| `VELA_PROVIDER` | 17 | `"markethub"` |
| `tickerFor` / `chartSymbol` | 33 / 39 | `NSE:RELIANCE` / `markethub:NSE:RELIANCE` |
| `splitTicker` | 43 | Splits `EXCHANGE:SYMBOL` |
| `known` map | 76 | Seeded from picker/deep link |
| `resolve(ticker)` | 96 | `known` first, then `splitTicker` + `resolveMarketInstrument(symbol, {exchange})` |
| `getBars` | 108 | `/api/market/bars` with `instrument_key = provider_token` and explicit `provider` |
| `getSymbolInfo` | 127 | Per-symbol metadata |
| `info()` | 141 | `{enumerate:false, stream:false, symbolInfo:true}` |

**Critical gap for the hybrid:** Vela delivers a native pick as a **bare
ticker** (probe log: `getBars(NIFTY 50)`). `resolve()` then has no exchange, so
for the 4 cash and 2,040 F&O colliding symbols it can land on the wrong
instrument. The fix is to pre-seed `known` from the enumerated pool (exact
`ticker → Instrument`), which the provider can do at construction because the
pool is bounded.

### 2.7 Bars API and provider resolution [V]

- `GET /api/market/bars` — `api/market_bars_routes.py:39`: requires
  `instrument_key`; optional `provider`, `timeframe`, `from`, `to`, `limit`;
  `BarsUnavailable` → 400.
- `BarService._provider_for` — `app/market_bars.py:152`: asks
  `ProviderRegistry.provider_for_key(..., catalog=...)`.
- `ProviderRegistry.provider_for_key` — `providers/registry.py:148`: **the
  catalog is authoritative**; adapter key-shape inference is only a fallback.

A catalog selection is therefore already identity-safe; only native bare
tickers need the pool seed.

### 2.8 F&O workflow [V]

- `MarketIntel.futures_contracts` — `app/market_intel.py:479`.
- `MarketIntel.option_expiries` — `:508`; `option_chain` — `:567`.
- `_derivative_exchange` — `:465`: derives the listing exchange from the
  underlying's own row (SENSEX → BSE, NIFTY → NSE); this is what makes the BSE
  leg resolvable.
- Structured picker mode in `InstrumentPicker.tsx`.
- **Expired contracts are not filtered by default** — the browse/F&O layer must
  add that.

### 2.9 Navigation and instrument-selection surfaces [V]

- Routes: `/chart` (`frontend/src/routes/router.tsx:37`), `/catalog` (`:61`),
  `/instruments` (`:60`) — the last is catalog **admin** (segment enablement +
  master re-sync), not a symbol browser.
- `Catalog` sits under **More** in `frontend/src/layouts/nav.ts:55`.
- Picker hosts: chart empty state (`ChartView.tsx:73`), in-chart Vela overlay
  (`chartSymbolPicker.tsx:174`), catalog page, watchlist add
  (`features/watchlists/components/AddInstrument.tsx:72`). Scanners use
  `resolveInstrument` (`api/market.ts:220`).
- Chord bridge: capture-phase `keydown` at `chartSymbolPicker.tsx:227`; action
  id `markethub-symbol` at `:17`.

### 2.10 Backlog [V]

`docs/plans/2026-10-27-unified-catalog-and-chart-plan.md`:

- Phases 0–1 committed; 1.5–3 implemented; Phase 4 (backend indicators)
  deferred 2026-10-28.
- **W1–W17** (lines 712–735): chart/bars gaps — calendar (W1), streaming (W2),
  feed subscription (W3, protected), provider-aware timeframes (W4), drawings
  (W5), indicator coverage (W6), range semantics (W7), bars errors (W8), layouts
  (W9), corporate actions (W10), retention (W11), backfill budget (W12),
  **F&O bars through the chart (W13, unverified)**, bar quality (W14), session
  axis (W15), live verification (W16), bundle size (W17).
- **W18–W33** (lines 738–759): catalog/picker wishes.
- D3/D7 (lines 186–198) and open question 7 (mobile stop) must be revised by the
  hybrid.

---

## 3. Vela 0.9.0 — authoritative contracts and probe evidence

### 3.1 Sources

- Installed package `@luxalgo/vela@0.9.0` (`frontend/package.json:19`,
  `frontend/bun.lock:75`, integrity
  `sha512-OOGEPhW+m4pOw2+Jg0uxGUPZA7Gt4Ih/L6r6eUQ24hJww4Jhhan3Feiavs33EKoKZK19zQi03SrYgmklzW5DBg==`).
- Official prose documentation:
  - `https://docs.luxalgo.com/vela/user/data-providers`
  - `https://docs.luxalgo.com/vela/contributing/adding-a-data-provider`
  - `https://docs.luxalgo.com/vela/user/workspace`
- **No version-pinned 0.9.0 API reference page was found.** Exact signatures
  below come from the installed `.d.ts` files and bundle, cross-checked against
  the prose docs. `https://docs.luxalgo.com/vela/user/symbol-search` returns 404.

### 3.2 Contracts

| Item | Contract | Source |
|---|---|---|
| `listSymbols()` | `() => Promise<SymbolDescriptor[]>`; called **once** at provider registration to build the eager index | `DataProvider-BGlB1zSc.d.ts:179`; `chunk-XIRSR3D6.js:800` **[V]**; docs confirm **[V]** |
| `SymbolDescriptor` | `{ticker, description?, type?, group?, default?, market?, prefix?, provider?}`; `prefix` is the **listing venue**, displayed by pickers and used for `PREFIX:TICKER` resolution | `DataProvider-BGlB1zSc.d.ts:76` **[V]** |
| `ProviderCapabilities` | `{enumerate, stream, symbolInfo}`; `enumerate:true` means `listSymbols()` drives index + autocomplete | `:117–118` **[V]** |
| `registerSymbolRanking(hook)` | `(pool) => pool \| Promise<pool>`; runs when the **pool fingerprint changes**, cached — never per keystroke, never a remote query. Registering it also disables Vela's crypto `TOP_TICKERS` default order | `contributions-BjefWpxQ.d.ts:2401/2404`; `chunk-4WAHYL7Y.js:3568`, `:3598` **[V]** |
| Native picker tabs | Fixed: All, Stocks, ETFs, Crypto, Futures, Forex, Commodities. Row `type` must be `stock`/`etf`/`futures`/`root`/`crypto`/`forex`/`commodity` to appear in a non-All tab. **No Index or Options tab exists.** | `chunk-4WAHYL7Y.js:3365`, `:3371` **[V]** |
| Picker search | `filterSymbols(pool, query, limit=100, top)` — a **local** prefix/substring/description filter; no remote query | `widget.d.ts:410`; `chunk-4WAHYL7Y.js:3219` **[V]**; docs: "filters an enumerated pool" **[V]** |
| Pool source | `this.symbolPicker.setSource(() => this.feed.symbols())` — the aggregated provider index; empty with `enumerate:false` | `chunk-4WAHYL7Y.js:9568` **[V]** |
| Selection → `getBars` | Picker calls `onSelect(`${prefix ?? provider}:${ticker}`)`; registry `resolve()` maps a declared `prefix` back to the declaring provider, then **`getBars(ticker, …)` receives the ticker without any prefix** | `chunk-4WAHYL7Y.js:3530`, `:3218`; `chunk-XIRSR3D6.js:853`; docs state this explicitly **[V]** |
| Duplicate/ambiguous | Bare resolution picks the first indexed provider containing the ticker; exchange is not part of the ticker; ambiguity must be resolved by the provider | `chunk-XIRSR3D6.js:853` **[V]** |
| Mobile / keyboard | Picker opens on typing a letter or `0`; on mobile it is a full-screen search in the bottom bar; hiding the button is done by omitting `'symbol'` from `topbar.left`. The chord is **not** gated by topbar composition (code-level finding) | docs **[V]**; `routeTyping` in `chunk-4WAHYL7Y.js` **[V]** |
| Remote search | **Not supported** | code + docs **[V]** |
| `venueOf` | `s.prefix ?? s.provider` — the badge/venue shown on a row | `chunk-4WAHYL7Y.js:3218` **[V]** |
| `TOP_TICKERS` | Crypto defaults (`BTCUSDT`, `ETHUSDT`, …); replaced by `false` when a ranking hook is registered | `chunk-4WAHYL7Y.js:3205`, `:3598` **[V]** |
| Picker pagination | `PAGE = 100`; `visible` grows on scroll, but the whole pool is in memory | `chunk-4WAHYL7Y.js:3382` **[V]** |

### 3.3 Probe evidence (existing probe, unmodified) [V]

Probe: `D:\Temp\probe\vela-app`, three modes via `?mode=inject|pool|both`,
curated 29 descriptors (7 indices, 20 F&O underlyings, 2 ETFs), synthetic bars,
inspector panel, `window.__probe` / `window.__ws`.

| Mode | Observed | Evidence |
|---|---|---|
| `inject` (`enumerate:false` + ranking) | `pool=0`; `ranking(pool=0)` injected 31 rows; picker renders rows with NSE/BSE badges; clicking a row produces **no `getBars`**; chart title unchanged | `shots/probe-inject.png`, `shots/probe-inject-picked.png`, `probe3.mjs` |
| `pool` (`enumerate:true` + 29 descriptors) | `listSymbols() -> 29`; typing filters the pool; selecting `NIFTY 50` logs **`getBars(NIFTY 50)`** and candles render | `shots/probe2-pool.png`, `probe2.mjs` |
| Tabs | All / Stocks / ETFs / Crypto / Futures / Forex / Commodities; indices appear only under All | screenshots |
| Identity | Selected ticker arrives **bare** (no exchange); venue badge is display-only | probe log |

### 3.4 Not established / not generalizable

- The probe used **29** symbols; it does not measure 800, 10k, or 151k
  enumeration cost. **[I]** Estimate for the full set: 144,364 enumerable rows,
  6,630,427 bytes of raw symbol/name/type/exchange text, plus object and index
  overhead — the reason the hybrid caps the pool.
- Sentinel-row interception of a picker selection is **not** documented
  behaviour; it is a host-side pattern that must be proven in Phase 3 before
  committing to it. If it proves janky, the fallback is the already-working
  adjacent topbar action, and the "Browse all" row is dropped.

---

## 4. Alternatives compared

| Criterion | A. Native only | B. MarketHub catalog only | **C. Hybrid (recommended)** |
|---|---|---|---|
| Completeness | Fails: pool filter cannot reach 65k contracts | Full | Full |
| Browse-before-typing | Yes, within pool | Yes (new default view) | Yes, both surfaces |
| Search long tail | No remote search | Yes (ranked) | Yes (catalog) |
| Identity | Bare ticker; needs seed | Explicit token | Explicit in catalog; seeded exact map in pool |
| Performance / memory | Bad if full enumeration | Good (paged) | Good (paged + ~800-row pool) |
| F&O workflow | Poor (no Options tab, flat rows) | Structured underlying→expiry→strike→CE/PE | Structured; pool exposes underlyings only |
| Mobile | Native full-screen picker | Existing overlay | Both |
| Integration complexity | Low, but insufficient | Medium (browse API + default view) | Medium-high (both) |
| Undocumented internals | Relies on chord/DOM semantics | Relies on chord bridge today | Removes the bridge; only pool + optional sentinel |
| Maintainability | One source but incomplete | One authoritative source | One source of truth; pool derived, never authoritative |

**Decision: C.** The pool is a *cache of navigable instruments derived from the
catalog*, not a second catalog. It is built by the server from the same
canonical rows and carries the same identity.

---

## 5. Target user experience

### 5.1 `/catalog` — authoritative browser

- Opens with data: category rail — **All · Indices · Equities · ETFs · F&O
  underlyings · Watchlist · Recent** — and the first page loaded without a
  query.
- Rows: symbol, display name, `NSE`/`BSE` badge, type, provider. Selecting a row
  opens the detail panel (existing table) with **Open chart** carrying
  `provider_token` + `provider`.
- **Equities** and **ETFs** paginate alphabetically by keyset; **Indices** shows
  deduped canonical indices; **F&O underlyings** lists the 222 live underlyings,
  drilling into the existing structured mode (expiry → strike → CE/PE);
  **Recent** is client-side (`localStorage`, ≤20); **Watchlist** reads the
  existing watchlist tables.
- Search box remains: non-empty query uses the ranked `/api/market/search`;
  empty query uses browse. No query is needed to see instruments.
- States: skeleton rows; "No instruments in this category"; error + retry;
  "No matches — clear filters"; expired contracts hidden by default with an
  explicit "include expired" toggle in F&O.
- Keyboard: ↑/↓/Enter/Esc, focus trap in the overlay, `aria-activedescendant`;
  mobile: full-screen panel with a sticky category rail.

### 5.2 Chart — Vela native

- Topbar left: `symbol` (native, visible), a contributed **"Browse all"**
  action, then timeframes / style / indicators / undo-redo.
- Typing a letter or `0` opens the **native** picker seeded with the letter and
  populated from the curated pool. The capture-phase chord bridge is removed.
- Native tabs work for Stocks / ETFs / Futures; **indices appear under All**
  (documented Vela limitation); options are intentionally absent from the
  native pool.
- First row: **"Browse all instruments…"** — selecting it opens the shared full
  picker instead of loading a chart (interception proven in Phase 3; the
  adjacent topbar action remains the guaranteed path).
- A native selection resolves to the exact instrument through the pre-seeded
  map and loads bars with the provider token. On mobile the native picker is
  the documented bottom-bar full-screen search; the "Browse all" action must be
  placed so it remains one tap away (plan open question 7).

### 5.3 Data honesty

No popularity ranking is invented: the database has no popularity signal. The
default view substitutes indices, live underlyings, ETFs, recents, and
watchlist.

---

## 6. API and data flow

### 6.1 New read model: `browse_instruments` (Phase 1)

Location: `core/persistence/modules/products.py`, alongside
`search_instruments`.

```
browse_instruments(conn, *, types, exchange=None, segment=None,
                   underlying=None, expiry=None, option_type=None,
                   strike=None, include_expired=False, q=None,
                   cursor=None, limit=50, provider_order=(...)) ->
    (rows, next_cursor, total)
```

**Dedupe (one query, window function):** partition by
`exchange || ':' || instrument_type || ':' || canonical_key`, where
`canonical_key` is:

| Type | Key expression |
|---|---|
| EQUITY / ETF | `COALESCE(NULLIF(isin,''), UPPER(tradingsymbol))` |
| INDEX | `UPPER(COALESCE(underlying, tradingsymbol))` |
| FUTURE | `UPPER(underlying) \|\| '\|' \|\| expiry` |
| OPTION | `UPPER(underlying) \|\| '\|' \|\| expiry \|\| '\|' \|\| strike \|\| '\|' \|\| option_type` |

These are the same rules as `canonical_id_for_row`
(`app/canonical_identity.py:198`).
`ROW_NUMBER() OVER (PARTITION BY … ORDER BY CASE provider WHEN … END, provider,
tradingsymbol, instrument_token)` keeps the routed provider's row.

**Keyset pagination:** cash ordered by `(tradingsymbol, provider,
instrument_token)`; derivatives by `(underlying, expiry, strike, option_type,
provider, instrument_token)`. Cursor is base64 JSON
`{last_key, filter_fingerprint}`; a cursor whose fingerprint does not match the
request filters → 400.

**Filters:** `types` (canonical list), `exchange`, `segment`, `underlying`,
`expiry`, `option_type`, `strike`, `include_expired` (default false →
`expiry >= today`). Untyped rows are always excluded. `q` is not used here —
search stays on the ranked endpoint.

**Limits:** `limit` 1–100, default 50; response `total` only on the first page
(count query measured 0.3 ms).

**Measured feasibility** (read-only, live DB):

| Query | Time |
|---|---:|
| Browse by type + exchange | 11.3 ms |
| Keyset page | 8.4 ms |
| Count | 0.3 ms |
| Options by underlying | 2.7 ms |
| Expiries | 3.9 ms |
| Exact symbol | 0.1 ms |
| Prefix LIKE | 7.4 ms |
| Deduped cash window (4,186 rows) | 56.7 ms |
| Deduped cash keyset page (26 rows) | 52.7 ms |

No new index in Phase 1; add a composite
`(instrument_type, tradingsymbol, provider, instrument_token)` only if the
realistic-size gate (p95 ≤ 150 ms for limit 50) fails. 150 ms is ~3× the worst
measurement and below the picker's existing 250 ms debounce.

### 6.2 Pool read model: `GET /api/instruments/pool`

- Returns
  `{items: [{ticker, description, type, prefix, provider, provider_token, canonical_id}]}`.
- Selection rule (server, data-derived): all deduped INDEX; one cash row for
  each live F&O underlying; all deduped ETFs; exclude untyped, expired-only, and
  disabled providers. Expected size ≈ 600–900.
- `ticker` is the display symbol and **must be unique within the returned
  pool**; the builder enforces this (for the 4 cash collisions it emits one
  routed row, with `prefix` naming its venue).
- Order: a fixed canonical index order for the index block (only those
  present), then underlyings alphabetically, then ETFs alphabetically. This is
  product configuration over real rows, not invented data.
- Served once per app shell and cached by TanStack Query; static for the
  workspace lifetime because Vela builds the index once.

### 6.3 HTTP surface

| Endpoint | Purpose | Compatibility |
|---|---|---|
| `GET /api/instruments/browse` | Paginated browse (§6.1) | New, additive |
| `GET /api/instruments/pool` | Curated descriptor pool (§6.2) | New, additive |
| `GET /api/market/search` | Ranked/exact search | **Unchanged**; `q` remains mandatory |
| `GET /api/futures`, `/api/options/*`, `/api/options/chain/view` | F&O | Unchanged |
| `GET /api/market/bars` | Chart bars by `instrument_key` | Unchanged |

Response item schema is the existing compact shape (`instrument_key`,
`provider`, `provider_token`, `native_key`, `canonical_id`, `symbol`,
`display_name`, `exchange`, `type`, optional F&O fields, `lot_size`) — one
contract for browse, search, and pool.

### 6.4 Identity flow

- **Catalog selection:** row → `provider` + `provider_token` → chart URL
  `key/sym/ex/type/prov` → `known` map →
  `/api/market/bars?instrument_key=<token>&provider=<prov>`. No bare-ticker
  guess.
- **Native selection:** descriptor → bare `ticker` → `MarketHubDataProvider`
  **pre-seeded `known` map** (ticker → exact `Instrument`) → same bars call. If
  a ticker is absent from the pool map, fall back to the exact resolver; if
  still ambiguous, show the error banner — never substitute.
- **F&O:** selection carries underlying, expiry, strike, option type, provider
  token; the bars request uses the contract token (W13 verification in Phase 4).

**No new table, no duplicate catalog, no schema migration.**

---

## 7. Implementation phases and acceptance criteria

### Phase 1 — Backend browse + pool read models (no schema change)

- **Files:** `core/persistence/modules/products.py`,
  `core/persistence/store.py` (passthrough wrapper), `app/instruments.py`
  (catalog wrapper), `app/market_intel.py` (`browse`, cursor validation, compact
  mapping), `api/product_routes.py` (two routes),
  `test/test_catalog_browse.py` (new), `test/test_market_intel.py`.
- **Out of scope:** frontend, schema, existing search semantics, F&O chain
  changes.

### Phase 2 — Shared picker browse mode and `/catalog` rework

- **Files:** `frontend/src/api/market.ts`,
  `frontend/src/components/InstrumentPicker.tsx`,
  `frontend/src/features/catalog/CatalogView.tsx`, picker/catalog styles,
  `frontend/src/__tests__/instrumentPicker.test.tsx`, `catalog.test.tsx`.
- **Out of scope:** Vela chart, native picker.

### Phase 3 — Vela native curated pool + identity seed

- **Files:** `frontend/src/features/chart/chartData.ts` (`listSymbols`,
  `enumerate:true`, seeded `known`),
  `frontend/src/features/chart/chartSymbolPicker.tsx` (remove chord bridge, add
  Browse-all handler), `frontend/src/features/chart/VelaChart.tsx` (topbar:
  `symbol` + Browse all; pass pool), a pool hook, `chartData.test.ts`,
  `chartSymbolPicker.test.tsx`.
- **Proof gate:** browser check that the typed chord opens a populated native
  picker, a pick loads candles, and a duplicate-symbol pick lands on the routed
  venue.
- **Out of scope:** changing pool composition beyond indices/underlyings/ETFs.

### Phase 4 — F&O chart verification (W13)

- **Files:** tests only unless a defect is found
  (`test_market_bars_service.py`, `chartData.test.ts`, chart browser check).
- Verify one future and one option (NSE and BSE) load bars and preserve
  identity.

### Phase 5 — States, accessibility, mobile, performance gates

- **Files:** picker/catalog components and tests; a realistic-size test seeded
  with a scrubbed copy (per the isolated-runtime rule).
- **Out of scope:** backend indicator work (deferred), scanners.

### Acceptance criteria (brief §7, mapped)

1. Opening `/catalog` shows browseable rows before typing. (Phase 2)
2. Pagination reaches the full live catalog (65,840 logical instruments), not
   the pool. (1–2)
3. Search finds instruments outside the default list. (existing search + 2)
4. Duplicate symbols across exchanges resolve to the intended instrument
   (4 cash + F&O coverage tests). (1–3)
5. Catalog selection loads the correct chart and candles. (2–3)
6. Vela-native selection loads the correct chart and candles. (3)
7. F&O selections preserve underlying/expiry/strike/option type/provider.
   (1, 4)
8. Existing search, chart navigation, and `/instruments` admin remain
   functional. (all)
9. No bulk catalog download: pool ≤ ~900 descriptors; browse pages ≤ 100.
   (1–3)
10. Loading/empty/error/no-results, keyboard, focus, and mobile states covered.
    (2, 5)
11. Realistic-size query tests demonstrate the stated 150 ms p95 gate. (1, 5)

---

## 8. Risks, assumptions, unresolved blockers

| Risk / assumption | Mitigation |
|---|---|
| Native search is local-only [V] | Pool is curated and bounded; full search lives in MarketHub |
| Bare ticker loses exchange [V] | Seed exact `known` map from the pool; enforce pool ticker uniqueness; never guess |
| 4 cash + 2,040 F&O cross-exchange collisions [V] | Explicit tokens in catalog; uniqueness guard in pool |
| Logical duplicates across providers (NIFTY 1,386 live) [V] | SQL window dedupe by canonical key, routed provider |
| 49,659 expired rows [V] | `include_expired=false` default |
| 7,121 untyped Upstox rows [V] | Exclude at the read model; classify later (W30) |
| No Index/Options tab [V] | Indices under All; options via F&O mode; documented |
| Sentinel-row interception is undocumented [I] | Prove in Phase 3; adjacent topbar action is the guaranteed path |
| Vela index is built once [V] | Pool static per session; no refresh assumption |
| F&O bars unverified (W13) [I] | Phase 4 gate before calling F&O charting complete |
| No popularity data [V] | Use indices/underlyings/ETFs/recents/watchlist only |
| Chord/DOM dependence | Hybrid **removes** the capture-phase bridge; pin Vela 0.9.0 |
| Auth/startup regression | No auth file in any allowed surface; login smoke test stays mandatory |

**Blockers:** none that require a product decision. The only technical
uncertainty is the native "Browse all" sentinel; the recommendation does not
depend on it (topbar action + `/catalog` remain).

---

## 9. Complexity by phase

| Phase | Size | Rationale |
|---|---|---|
| 1 — backend browse/pool | **M** (~1.5–2 days) | One windowed SQL read model, cursor codec, two routes, realistic-size tests; measured queries already fit |
| 2 — picker browse + `/catalog` | **M–L** (~2–3 days) | Default view, categories, infinite scroll, states, a11y, tests |
| 3 — native pool + identity | **M** (~1.5–2 days) | Provider `listSymbols`, seeding, topbar recomposition, removal of the bridge, browser proof; highest uncertainty is the sentinel |
| 4 — F&O bars verification | **S** (~0.5–1 day) | Mostly verification and tests |
| 5 — states/mobile/perf | **S** (~1 day) | Polish and gates |

Total ≈ **7–9 engineering days**, no schema migration, no new dependency.

---

## 10. Exact scope to approve next

**Approve Phase 1 only**, with this declaration:

```
TASK ZONE:              Canonical catalog read models + market API adapters
ALLOWED CHANGE SURFACE: core/persistence/modules/products.py,
                        core/persistence/store.py,
                        app/instruments.py,
                        app/market_intel.py,
                        api/product_routes.py (new browse/pool routes only),
                        test/test_catalog_browse.py (new),
                        test/test_market_intel.py
PROTECTED/OUT-OF-SCOPE: app/auth/*, credential/session persistence, startup,
                        feeds, schema/migrations, frontend, Vela probe,
                        plan document, existing per-application routes
```

**Phase 1 delivers:** `browse_instruments` with canonical SQL dedupe and keyset
pagination; `GET /api/instruments/browse`; `GET /api/instruments/pool`; tests
proving full-catalog reach, dedupe, expired exclusion, validation, and the
150 ms realistic-size gate.

**Recommended implementation sequence:** Phase 1 → Phase 2 → Phase 3 → Phase 4
→ Phase 5, with a browser proof at the end of Phase 3 and a plan revision
(D3/D7, open question 7) handled as a **separate docs-only task after Phase 3 is
proven** — the plan is intentionally untouched in this investigation.

---

## Appendix A — Verification commands and results

All database reads used `sqlite3.connect("file:...?mode=ro", uri=True)` against
`D:\Temp\MarketHub\data\events.db`. No write, no service start/stop.

| Check | Result |
|---|---|
| `git log -1 --format='%H %s'` | `34a2f710f3b51374e15982a3b24dc21bdf028e98 feat(chart): Vela chart, shared catalog page and picker wiring` |
| `git branch --show-current` | `fno-provider-agnostic` |
| `git status --porcelain \| wc -l` | 554 |
| Backup path | exists |
| Probe path | exists |
| Vela manifest / lockfile | `0.9.0` in both |
| `SELECT COUNT(*) FROM instruments` | 151,485 |
| `PRAGMA user_version` | 24 |
| `SELECT COUNT(*) FROM market_bars` | 11,395 |

## Appendix B — Probe artifacts (read-only)

`D:\Temp\probe\vela-app`:

- `src/main.tsx` — three modes, 29 curated descriptors, conditional
  `listSymbols`, `registerSymbolRanking`, synthetic bars, inspector.
- `probe2.mjs`, `probe3.mjs` — Playwright assertions (row counts, tab counts,
  filter results, pick → `getBars` log).
- `shots/probe-inject.png`, `shots/probe-inject-picked.png` — ranking-only:
  rows visible, no bar load.
- `shots/probe2-pool.png` — enumeration: `listSymbols() -> 29`,
  `getBars(NIFTY 50)`, candles rendered.
- Preview server was left running on `http://localhost:4174` (not started or
  stopped by this investigation).