# MarketHub — Focused F&O Market-Data Audit & Implementation Plan (Round 5)

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints. No `git reset/clean/checkout/restore/stash/rebase`. All in-flight work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Date:** 2026-10-18
**Scope correction (maintainer-directed):** the target is **NSE stock F&O + major NSE index F&O + BSE SENSEX F&O only** — *not* the full 436-index NSE catalogue and *not* the whole BSE index family. This audit corrects the earlier over-scoped rounds.

**Repo state recorded (mandatory §8):** branch `fno-provider-agnostic`; HEAD `3f4b8d9`; `brokers/fyers/rest.py` **untracked** (`git ls-files --error-unmatch` → no match); working tree ≈ 209 staged-new + 29 modified + 267 untracked (incl. this audit's own file). Active runtime DB per `config.json` → `data_dir:"data_test"` ⇒ `data_test/events.db` (**instruments = 0 rows**). Populated `data/events.db` (142,932 rows) is a *separate environment*, not the active runtime; **neither is modified in this review.**

---

## 1. Scope confirmation

### A. NSE stock F&O — complete *active* universe
- **Not hardcoded; catalog-driven.** `market/derivatives_universe.py` derives underlyings from `catalog.fno_underlying_rows`/`fno_universe` (line 185–244), and contracts from `list_future_contracts` / `option_strikes` (catalog reads, lines 105–132, 268–306). Expiries/strikes are *never* hardcoded; `_choose_expiry` and `_unexpired_expiries` filter `expiry >= today`.
- **Official active count (checked 2026-10-18):** NSE "About Equity Derivatives" page: *"Futures and Options… on 6 major indices and more than 200 securities."* NSE circulars 2026 add/remove individual names (Apr 1 +6, Jul 23 +3, Sep 9 +3, Sep 23 exclude IEX/IREDA, Oct 28 +6) ⇒ **current ≈ 200–219 eligible stock F&O underlyings**, a moving quarterly list.
- **Required coverage:** every eligible underlying, all active futures, all active stock options across expiries/strikes/CE/PE, plus metadata (expiry, strike, option_type, underlying identity, lot_size, tick_size — all present in the catalog record, `app/instruments.py:214-233`/`150-165`), underlying stock quotes, and the fields for F&O workspace / futures heatmap / option chain / OI analytics / market-intel.
- **Refresh:** from official/provider masters (Fyers `NSE_FO_sym_master.json`; Upstox `complete.json.gz` NSE_FO). Eligibility changes, new listings, exclusions, and expiry rollover are handled by **catalogue re-sync** (epoch invalidation of `futures_index`/`fno_underlying_rows` caches) — not manual lists.

### B. Major NSE index F&O — *active index-derivatives* underlyings only
- **Official active index-derivatives list (checked 2026-10-18, https://www.nseindia.com/static/products-services/equity-derivatives-contract-specifications + equity-derivatives-watch):**
  | Underlying | NSE symbol | F&O since | Cycle |
  |---|---|---|---|
  | NIFTY 50 | NIFTY | 2000 | 4 weekly + 3 monthly + 3 quarterly + 8 half-yearly options |
  | NIFTY Bank | BANKNIFTY | 2002 | 3 monthly + 3 quarterly |
  | NIFTY Financial Services | FINNIFTY | Jan 2020 | 3 monthly |
  | NIFTY Midcap Select | MIDCPNIFTY | Jan 2022 | 3 monthly |
  | NIFTY Next 50 | NIFTYNXT50 | Apr 2024 | 3 monthly |
  | Nifty India FPI 150 | NIFTYFPI | **Aug 12 2026** | 3 monthly |
  - *Not* INDIA VIX (a volatility index, **not** an F&O underlying — correctly `is_fno=False` in `market/index_registry.py`). **This is exactly the six entries in `FNO_INDEX_LABELS`** — verified to match the current official active list; the list is a *registry constant to keep in sync with NSE*, not a hardcoded "six" assumption (the requirement is that we *don't* blindly hardcode if NSE changes the active set — the registry is the single source to update).
- **Coverage per index:** spot quote, active index futures, active index options (all expiries/strikes/CE/PE), option-chain data, ATM + underlying-price resolution, and quote/depth/OI/Greeks/history where the provider supports them.
- **Out of scope:** the other ~290+ benchmarking/factsheet NSE indices (no live derivatives, no requirement to stream them).

### C. BSE SENSEX F&O (in-scope; other BSE derivatives flagged, not expanded)
- **In scope:** **BSE SENSEX** underlying quote + active SENSEX futures + active SENSEX options (all expiries/strikes/CE/PE) + OI/prev-OI/OI-change/Greeks/depth/history where supported.
- **Official SENSEX F&O spec (checked 2026-10-18, BSE contract-specifications page):**
  - **SENSEX futures:** lot 20, tick ₹0.05, 3 serial monthly, cash-settled, last-Thursday expiry.
  - **SENSEX options:** lot 20, tick ₹0.05, **7 serial weekly + 3 monthly + 3 quarterly + 8 half-yearly**, European, **≥101 strikes** (50 ITM / 1 ATM / 50 OTM), strike interval 100 pts.
  - `data/events.db` BSE_FO confirms **SENSEX futures (3 contracts) + SENSEX options present** (upstox-dominant: 12 BSE futures + 5,126 BSE options).
- **Explicitly OUT of scope (flag for approval, not assumed):** BANKEX derivatives, BSE single-stock derivatives, SENSEX 50 / NEXT 50 / NEXT 30 / SIXTY derivatives, MCX/NCD/BCD/COM, and any new data vendor. These are **not** part of this requirement; if the maintainer wants them, a separate approval is needed.

---

## 2. Full contract availability vs. continuous streaming (separated)

The requirement is **NOT** "subscribe to every contract simultaneously." The system must provide:
1. **Complete current catalogue** of in-scope underlyings + active contracts per provider (the `instruments` table: ~219 NSE_FO underlyings × 18 expiries, SENSEX futures/options, index F&O). ✓ derived, not hardcoded.
2. **Automatic subscriptions only for what active app features need, within real limits** (futures + bounded ATM option window per active view; index spot; featured indices). The live stream is *scoped*, not the whole universe.
3. **On-demand REST** for the broader option-chain / contract tail (chain fetches, greeks, history) — never WebSocket keys for chains.
4. **Truthful states** when an instrument/field can't be supplied (`UNCOVERED`, `CAPACITY_LIMITED`, `PROVIDER_UNAVAILABLE`, `STALE`, `unavailable` — never a fabricated `live`).
5. **Automatic reconcile** on startup, active-view change, provider reconnect, contract expiry, and catalogue change.
6. **No manual broker/contract/key selection, no manual apply** in normal use.

**Never report the whole universe as live when only a subset is streamed** — coverage counts must be per-provider + per-kind, and the UI must show "N of M streamed; rest REST-on-demand."

---

## 3. Verified defects (root cause, paths, evidence, confidence, method)

| # | Finding | Status | Evidence (file:line) | Impact | Confidence |
|---|---|---|---|---|---|
| V1 | `brokers/fyers/rest.py` untracked | **Verified** | `git ls-files --error-unmatch` → "did not match"; imported at `app/server.py:695-696` | Clean checkout → `ModuleNotFoundError` at startup; Fyers REST (quotes/depth/history/option-chain/marketStatus) unavailable | High — `git ls-files` |
| V2 | Fyers HSM index ticks dropped | **Verified** | `brokers/fyers/feed.py:937-941` `if snap["topic"].startswith("if"): return` (comment: "Upstox is the authoritative index source") | Fyers-only: no *streaming* index quote reaches `MarketService` | High — code |
| V3 | Fyers HSM index-price scaling unverified | **Verified-unverified** | `feed.py:942` `scale=(10**precision)*multiplier`; in-code comment records a *captured* NIFTY snapshot `multiplier=28/precision=0 → 87,205.71` vs true ~23,897 (**×3.65 error**) | HSM index LTP **must not be trusted** as live; guard (V2) stays until a market-hours test passes | High — code + captured evidence |
| V4 | Fyers REST quote path can supply index/SENSEX data | **Partially verified** | `brokers/fyers/rest.py::quotes()` (≤50 symbols batch, `/data/quotes`); Fyers index symbols `NSE:NIFTY50-INDEX`, `BSE:SENSEX-INDEX` are catalog-queryable | REST index data is **documentation-established** (endpoint + envelope + batch), but **live-correctness at market hours NOT validated** (no authenticated response captured this round) | Medium — docs + code; live test needed (§6) |
| V5 | Quote storage/lookup use provider-native keys, not canonical | **Verified** | `market/service.py:207-210` `get_quote_now` = raw `(exchange, instrument_token)`; no resolver consulted; `Quote.canonical_id` computed in `_merge_quote` (`service.py:251-257`) but *not* the key | Two slots per instrument; cross-provider reads don't converge; consumers using one provider's key miss the other | High — code |
| V6 | Frontend/consumers join on Upstox-native keys | **Verified** | `app/subscriptions/service.py:231` `preferences().indices[].key = entry["upstox_key"]`; `IndicesWidget.tsx` + `types.ts::quoteKey` join on `instrument_token`/`upstox_key` | Fyers-only index cards show "—"; frontend "repairs" key mismatches per-component | High — code |
| V7 | F&O coverage planning discovers current eligible stock + index + SENSEX | **Partially verified** | `fno_coverage._universe_symbols` + `market/derivatives_universe.index_underlyings` (six F&O indices) + `stock_underlyings(catalog, provider)`; `plan()` bulk-reads `futures_index` for NSE **and BSE** (`fno_coverage.py:305-315`), so SENSEX futures/options *are* in scope; empty test-DB catalog → `desired:0` | Logic is correct **and** SENSEX included; but in the active `data_test` env the catalog is empty ⇒ nothing discovered until sync (V9) | High — code; data-dependent |
| V8 | F&O subscriptions auto-reconstruct without manual apply | **Refuted — gap** | `_derivatives_universe` (`service.py:92`) is in-memory, "NEVER persisted"; `app/server.py:1220` lifespan calls `reconcile()` but the derivatives owner is only set by the **manual** `POST /api/fno/apply` (`fno_coverage_routes.py:112-144`) | After restart, the F&O universe is **not** auto-restored → normal use *requires* a manual apply (violates the requirement) | High — code |
| V9 | Runtime catalogue empty; test fixtures separated | **Verified** | `config.json data_dir:"data_test"`; `data_test/events.db` instruments=0; `data/events.db` 142,932 (separate env) | Active runtime has no F&O/index contracts discoverable until a sync; do **not** modify the test DB in this audit | High — read-only inspection |
| V10 | Provider routing uses connection state only, not quote freshness | **Verified** | `fno_coverage._available` (`:210-215`) checks state ∈ `{streaming,connecting,reconnecting}` — no quote-age gate | A stale-but-streaming provider still wins routing; no `STALE`-vs-`live` distinction in routing | High — code |
| V11 | Option-chain spot / futures heatmap / SSE / market-intel quote semantics inconsistent | **Partially verified** | `resolve_spot` (`market_indices.py:199-233`) hardcodes **Upstox-first**; option-chain, heatmap, and SSE each read `MarketService`/`MarketIntel` through different paths; none use a single canonical selected quote with source/freshness/validity | Same instrument can render differently per feature; a provider key mismatch or stale tick surfaces unevenly; Upstox is a **hidden mandatory** index source | Medium — code cross-check |
| V12 | Frontend `fno/view` request-storm fix | **Partially verified (present in a different form)** | Working-tree `FnoWorkspaceView.tsx:38,67` uses a **stable `useFnoActiveView()` hook** (`useFno.ts:116-120`, a `useMutation` on `postFnoView`) whose result object is stable; the `useEffect` dep is `activeView` (the hook, not a per-render object) | The Round-1/2/3 "fix documented but not in tree" claim is **refuted for the working tree** — the stable-hook pattern *is* present. **However the regression test `frontend/src/__tests__/FnoWorkspaceView.test.tsx` does NOT exist** in this tree → the fix is **untested** | High for code presence; **test missing** |
| V13 | Fyers plan-cap assumption (5000/4450) | **Refuted** | `brokers/fyers/capabilities.py` `MODE_KEY_LIMITS={"full":5000}` assumes **Prime**; official docs (2026-10-05) set **Standard = 100 data-socket symbols** (Prime = 5,000), Standard is default | For a Standard account the true streaming cap is **effective 90**, and the app's `MAX_INSTRUMENTS_PER_PROVIDER=500` **exceeds it** — the plan silently over-subscribes | High — docs vs code |

**Method notes:** file/line evidence read directly from the active working tree (staged+unstaged). `data_test/events.db` inspected **read-only** (`mode=ro`). No catalogue was synced, no endpoint mutated, no DB/config/build file touched.

---

## 4. Target architecture

### 4.1 Canonical identity
- **One canonical ID per real instrument/contract** (`app/market_identity.py`): `INDEX:{exch}:INDEX:{sym}`, `EQUITY:{exch}:EQUITY:{ISIN|sym}`, `FUTURE:{exch}:FUTURE:{und}:{expiry}`, `OPTION:{exch}:OPTION:{und}:{expiry}:{strike}:{CE|PE}`. Fyers `NSE:NIFTY50-INDEX` ≡ Upstox `NSE_INDEX|Nifty 50` ≡ `NSE:INDEX:NIFTY` (evidence-based, not "different name = different").
- **Storage key = `(exchange, canonical_id)`**; provider-native token demoted to `Quote.provider_token` (provenance). This removes V5's two-slot divergence.

### 4.2 Provider observations + selected quote
- Each adapter delivers a **validated observation** keyed by canonical ID + `source` (websocket/rest/tbt) + `received_ts` + `provider` + `provider_token`. **Provider-specific protocols/identifiers stay inside `brokers/*/`**; they never leak into `market/`, `app/`, or the frontend.
- A **central coordinator** exposes **one selected quote per canonical instrument**, chosen by `enabled∧authenticated`, `validity`, `freshness`, `entitlement/plan-cap`, with **preference order as a tie-breaker only** (no hidden mandatory provider — resolves V11's Upstox-first assumption and the requirement that Upstox not be a hidden dependency).

### 4.3 Non-conflated status semantics (three independent axes)
| Axis | Values | Rule |
|---|---|---|
| **Source** | `websocket` / `rest` | where it came from; always labelled |
| **Freshness** | `fresh` (≤5 min) / `stale` / `unavailable` | age-based; `STALE_MINUTES=5` (`market_indices.py:41`) |
| **Validity** | `validated` / `rejected` | passed central checks (numeric LTP, plausibility, entitlement); **rejected even if freshest** |
- A quote is **`live` only if `fresh ∧ validated`** and its `source` is retained. **Stale or invalid values are never shown as live.** Fyers HSM index (V3) is **classified untrusted** until the §6 test passes; the REST index path is the authoritative Fyers-only index source.

### 4.4 Automatic subscription reconciliation (single source of truth)
- **Desired set = computed, not manually stored:** `resolve(fno_config[declarative intent] × catalogue × active_views × enabled∧authenticated providers × plan caps)`.
- **Triggers (all automatic, no manual apply):** startup restore (V8 fix, §5), active-view change, provider reconnect/failover, expiry rollover (`_choose_expiry` filters `>= today`), catalogue epoch bump, and Fyers-only ⇄ Upstox-only ⇄ dual transitions.
- **Limit handling:** if a provider's plan cap (Fyers 90 Standard / 4,450 Prime; Upstox 1,800 full) or app cap is exceeded, route by priority (live futures > index spot > option strikes), drop the overflow by priority, and **mark the dropped set `CAPACITY_LIMITED`** — never claim full coverage.
- **What persists:** only *declarative intent* (the existing `fno_config` row + `md_derivative_rules` + `md_subscriptions`). **No new derived-plan field is needed** (V8's fix re-derives the plan from durable intent at startup via the existing `reconcile()`; see §5).

---

## 5. Capacity plan (recalculated from the actual formula)

**Formula (verified in `providers/capabilities.py:48-51`):** `effective_budget = capacity − max(10%×capacity, 50)`.

| Provider/plan | Raw cap | Effective budget | Source (checked 2026-10-18) |
|---|---|---|---|
| **Fyers Standard (default)** | **100** | **90** | Fyers KB (2026-10-05): data socket 100 (Std) / 5,000 (Prime) |
| Fyers Prime | 5,000 | 4,450 | same |
| Upstox `full` (default) | 2,000 | 1,800 | Upstox V3 docs: individual full=2000, combined 1500; `ltpc` 5000/`option_greeks` 3000/`full_d30` 50 (Plus) |
| **App cap** `MAX_INSTRUMENTS_PER_PROVIDER` | 500 | 500 | `subscriptions/service.py:30` |

**Corrections to earlier rounds:** Fyers **Standard = 100/90** is the safe default (the code's `5000`/`4450` is a **Prime assumption, refuted** — V13). The binding Fyers streaming budget today is **90 (Standard)** or **500 (app cap)** — whichever is lower; for a Prime account it's the app cap (500).

**Fyers-only REST budget for in-scope index data (Standard):** ~7 index spot symbols (6 NSE F&O indices + SENSEX) fit in **one** `/data/quotes` batch call (≤50/request). Refreshed every 60 s ≈ **1,440 calls/day**; plus bounded SENSEX/NSE index option-chain polling (~6 underlyings × 2 × 12/day ≈ 150–300/day). **≈ 1,600–1,750/day < 5,000/day Standard, and far under 5 rps / 50 rmin** when batched + market-hours-gated. Feasible **without Prime**, for the *index* scope.

**Streaming scope (auto, in-cap):** live stream = **index spot (7) + active-view futures + bounded ATM option window + active featured indices**. This is well under 90 (Standard) for the *feature-driven* set; the full 219-underlying F&O universe is **REST/on-demand**, not streamed.

**On-demand retrieval (outside stream budget):** Fyers `/data/option-chain-v3` (per underlying/expiry), `/data/quotes` (batched), `/data/depth`, `/data/history`; Upstox option chain `/v2/option/chain`, option-greek `/v3/market-quote/option-greek` (≤50 keys/req), history V3 (OI). Shared per-provider token bucket (Fyers Standard ≤5 rps) + catalogue-epoch/TTL caching (existing `futures_board`/`derivatives_universe` single-flight pattern).

**Upstox REST quotas/entitlements (confirmed vs docs, residual U3):** full=2000 (combined 1500), full_d30=50 (Plus tier), option-greek ≤50/req, market-quote ≤500. Exact *account-tier* REST rate limits are **residual to confirm against the connected account** (not captured read-only).

---

## 6. Safe index + quote validation (Fyers-only; HSM untrusted)

### 6.1 Use the existing Fyers REST path as the index source while HSM is unverified
- `FyersRest.quotes()` (already implemented, V1) is the index/SENSEX data path: one batched `/data/quotes` call for the 7 in-scope index symbols (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI, SENSEX). Bounded refresh (60 s), market-hours-aware (skip off-hours; serve last close as `stale`), cached (60 s in-mem), deduplicated (coalesce same-index requests), **shared token bucket with all Fyers REST** (index + F&O chain + history share the 5,000/day Standard pool).
- **Documented vs. to-be-tested:** endpoint shape, batch limit, envelope, and rate caps are **documentation-established**. **Live-correctness of a specific index/SENSEX LTP at market hours is NOT established** — no authenticated response was captured this round. **Do not claim it passes.**

### 6.2 Required authenticated market-hours validation test (specification — NOT claimed done)
- **Symbols (representative):** NSE `NSE:NIFTY50-INDEX` + `NSE:NIFTYBANK-INDEX` (+ optionally one more, e.g. `NSE:FINNIFTY-INDEX`); BSE `BSE:SENSEX-INDEX`.
- **Method:** `/data/quotes?symbols=NSE:NIFTY50-INDEX,NSE:NIFTYBANK-INDEX,BSE:SENSEX-INDEX`. Record per symbol: returned LTP/OHLC, `received_ts`, and **timestamp**.
- **Independent reference:** NSE published index value (nseindia.com index watch) and BSE SENSEX value (bseindia.com) **at the same minute**.
- **Tolerance:** within **±0.05 index points** (NIFTY/SENSEX tick is 0.05) at capture time.
- **Budget impact:** 1 call (3 symbols) vs ~14 calls if unbatched ⇒ negligible; within 5 rps.
- **HSM index scaling (V3) — separate test (blocking only for HSM index):** during NSE trading hours subscribe `if|nse_cm|Nifty 50`; capture `snapshot` (data_type 83) raw `ltp`, `multiplier`, `precision`, `exch_feed_time`; compare `ltp/scale` to NSE published NIFTY, **3 consecutive snapshots, tolerance ≤0.05 pts**. **Pass ⇒ HSM index may be optionally enabled (cheaper than REST). Fail/not-run ⇒ REST index is authoritative; HSM index ticks remain dropped/untrusted.** **No claim of passing without this evidence.**
- **Separation:** source (WS/REST) × freshness (fresh/stale/unavailable) × validity (validated/rejected) × provenance+native token are all carried on the observation. A **fresh REST snapshot is a valid "live" source** but keeps its `rest` label. **Upstox is not a hidden mandatory provider** — the coordinator picks by availability×freshness×validity×entitlement; preference order is only a tie-break.

---

## 7. Configuration & maintainability (where each rule lives)

| Concern | Home | Rationale |
|---|---|---|
| Canonical index/F&O-underlying set | `market/index_registry.py` (stable code) | Single source; sync when NSE changes the active index-F&O set |
| Per-provider caps | **Centralized `providers.capacity` config** (one block; adapters read `registry.budget`) | No plan numbers scattered; **Fyers Standard=100 default, Prime=5000 as an explicit, explained WebUI override** |
| Provider capabilities (OI/Greeks/chain/depth) | `brokers/{name}/capabilities.py` (adapter-declared) | Adapter-owned truth; registry consumes |
| F&O scope, expiry policy, provider preference (tie-break), stock_mode | `fno_config` (declarative intent, **existing table** — no new field) | Resolved dynamically against catalog + registry |
| Eligible-stock / active-contract universe | **Catalogue (dynamic data)** `instruments` table | Never hardcoded; re-sync on exchange changes |
| Stale threshold (5 min), safety reserve (10%/50), app cap (500) | stable code constants (`STALE_MINUTES`, `effective_budget`, `MAX_INSTRUMENTS_PER_PROVIDER`) | Domain invariants, not user-tunable |
| No user-tunable setting | — | Do **not** add settings for immutable domain rules (e.g. expiry `>= today` filter, canonical key schema) |
| User-adjustable ops policies | WebUI (provider enabled, F&O index/stock/expiry selection, Plan-cap override) with auto defaults | Normal operation needs no manual tuning |

**Eliminate:** scattered provider-name checks (`_provider_of`, `_prov_of`, `_STYLE_MATCHERS` should all read from the registry), hardcoded capacities (`5000` in `fyers/capabilities.py` → centralized), and raw provider-key assumptions in the frontend (join on canonical ID).

---

## 8. Staged implementation plan (dependencies, boundaries, tests, risks, data impact, safe rollback)

> **Rollback rule (all stages):** NEVER `git reset/clean/checkout/restore/stash/rebase`. Rollback = **selective reversal of only the lines a stage adds**, *after* identifying and safeguarding pre-existing in-flight modifications in the affected file. Whole-file revert is forbidden where the file already has unrelated in-flight edits. No new persistence field or protected-startup composition change is introduced (the V8 fix reuses existing durable `fno_config` + the existing `reconcile()` path).

| # | Stage | Module boundary (exact) | Deps | Data/schema | Risk | Safe rollback |
|---|---|---|---|---|---|---|
| S1 | Commit `brokers/fyers/rest.py` (V1) | `git add` one untracked file | — | none | none (additive; already imported at `app/server.py:695`) | *only* `git reset --soft HEAD~1` is safe **here** (commit holds just this file); no `--hard`/`clean` |
| S2 | **Plan-aware Fyers cap, Standard default** (V13) | new `providers/capacity.py` settings model (`fyers.data_socket_symbols:100 default` / Prime override); `brokers/fyers/capabilities.py` reads it | S1 | config (additive) | medium | revert the two added files; consumers fall back to existing behavior |
| S3 | **Central quote coordinator + validity + freshness** (V5, V10, V11) | new `providers` selection policy + `market/service.get_canonical_quote` + `fno_coverage._available` (add quote-age gate); **do not** change `brokers/*/feed.py` tick delivery | S2 | none | medium-high (new policy; must be lock-free O(1)) | revert the 3 modules; native-key reads still work (no migration done yet) |
| S4 | **Frontend + `preferences()` join on canonical ID** (V6) | `subscriptions/service.py::preferences` (`.key`→canonical, keep `fyers_symbol`/`upstox_key` provenance) + `IndicesWidget.tsx` + `types.ts::quoteKey` + `WatchlistTable.tsx` | S3 | none | medium (additive API shape) | selective hunk reversal **per file** (all in-flight — do not revert whole files) |
| S5 | **Auto-restore F&O at startup (NO new field)** (V8) | `app/server.py` lifespan: call existing `FnoCoverageService.plan()` → `set_derivatives_universe(plan.keys_by_provider)` → existing `reconcile()` (add ~3 lines at `server.py:1220` area) | S3 | **none** (reuses durable `fno_config`) | low-medium (touches startup *composition only by calling an existing method*; does NOT alter frozen auth/credential/session baseline) | revert the added lifespan lines only; `fno_config`, `reconcile`, `_derivatives_universe` untouched |
| S6 | **Fyers-only index via REST** (V4, §6) | `brokers/fyers/rest.py::quotes` (exists) + coordinator index source + bounded market-hours poller (≤5 rps, 60 s, shared token bucket) | S3 | none | medium | revert poller + index-source lines; HSM guard (V2/V3) stays untouched |
| S7 | **HSM index scaling market-hours test** (V3 — BLOCKING for HSM index only) | test harness only (no prod code) | S6 | none (evidence artifact) | n/a | n/a — §6.2 evidence; **do not claim passed** |
| S8 | Catalogue sync for the *empty active env* (V9) | operational API `POST /api/instruments/sync` | S1 | populates `instruments` (target env only; `data_test` **not** touched in this review) | low (transactional; failed sync leaves prior catalog) | none needed (sync is idempotent/transactional) |
| S9 | **F&O view request-storm regression test** (V12 — fix present, test missing) | add `frontend/src/__tests__/FnoWorkspaceView.test.tsx` asserting `idle/afterRenders/atUnmount/afterUnmount` stability of the `useFnoActiveView` mutation loop | — | none (new test file) | low | delete the new test file (additive) |
| S10 | `migrate_from_config` Fyers index categorization | `subscriptions/service.py::migrate_from_config` (recognize Fyers colon index keys → `index`) | S4 | one-time data correction (idempotent marker) | low | revert (marker makes re-run safe) |
| S11 | **Canonical re-key (Option A)** ⚠️ *deferred, protected-core* | `market/service.py` write path → `(exchange, canonical_id)` | S3 proven | data migration | high | **separate protected-core approval; not in this gate** |

**Critical path for safe Fyers-only F&O operation:** S1 → S2 (Standard cap) → S3 (coordinator) → S4 (canonical join) → **S6 (REST index, no HSM dependency)** → S5 (auto-restore, no new field) → S8 (sync active env). S7 gates HSM index. S9 adds the missing regression test. S11 deferred.

**Acceptance tests (all three configs, automatic subscribe/fallback, in-scope categories, spot/chain/heatmap, fields, stale/unavailable, budgets, clean-checkout, no storm):**
- **Fyers-only / Upstox-only / dual:** each shows in-scope index spot + F&O with truthful `provider`+`source`+`basis`; dual shows no duplicate logical instrument (converged canonical).
- **Automatic:** boots with F&O universe restored (S5, no manual apply); active-view change / reconnect / expiry-rollover / catalogue-sync / provider-drop each auto-reconcile; `CAPACITY_LIMITED` reported when a plan cap is hit — **never a full-live claim.**
- **In-scope contracts:** 200–219 NSE stock F&O underlyings (catalog-derived), 6 NSE index F&O (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/NIFTYNXT50/NIFTYFPI), SENSEX futures+options — all with expiry/strike/CE/PE/lot/tick metadata; spot resolution, ATM snap, option chain, futures heatmap, OI/prev-OI/OI-change/Greeks where the provider supports them.
- **States:** `fresh`/`stale`/`unavailable` × `websocket`/`rest` × `validated`/`rejected`; stale or rejected never `live`; HSM index untrusted until S7 passes; Fyers REST index labelled `rest`.
- **Budgets:** Fyers Standard 90 streaming cap respected; REST ≤5 rps/50 rmin/5,000 day; Upstox full 1800 effective; `ANALYTICS_CAP`/`MAX_INSTRUMENTS_PER_PROVIDER` enforced.
- **Clean checkout:** `git clone`+`pip install`+`python -m app.server` no `ModuleNotFoundError` for `rest.py`; `pytest test/test_architecture_boundaries.py` green; `bun run build` OK; **no untracked Python file imported by `app/server.py`.**
- **No request storm:** S9 test passes (`idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`).

---

## 9. Unresolved critical uncertainties (evidence needed to close)

1. **U1 — Fyers HSM index LTP scaling (V3).** Blocks *streaming* Fyers index only; **does not block the REST path (S6).** Evidence: §6.2 market-hours 3-snapshot capture (multiplier/precision/raw ltp + independent NSE value + delta + timestamps). **Cannot be closed by code inspection.**
2. **U2 — Fyers account plan (Standard vs Prime).** No documented profile field exposes it. **Resolved by design** (§5/§7): plan for **Standard (90) by default**; Prime is an explicit, WebUI-explained, centrally-configured override. Residual: *runtime auto-detection* is out of scope; the default+override model closes it for planning.
3. **U3 — Upstox account tier / REST rate limits.** Residual to confirm against the connected account before trusting Upstox REST at scale; **does not block Fyers-only.**
4. **U4 (settled read-only):** active runtime DB is `data_test` (empty); F&O/index live behavior here is blocked on **S8 (catalogue sync)** — an operational step **not run** in this read-only round; `data_test/events.db` is deliberately **not modified**.
5. **Fyers REST index live-correctness (§6.2).** Requires one authenticated market-hours spot check (3 index symbols vs NSE/BSE published values, ≤0.05-pt). **Not claimed passed.**

---

## 10. Final status

**NOT READY FOR MAINTAINER REVIEW — for *implementation coding*.**

The **design is complete and internally consistent** and is ready for review; coding is blocked on:
- **U2 decision:** adopt the Standard-default (90) + centralized Prime-override model (§5/§7) — the single fact that fixes the refuted 5000/4450 figures.
- **U1 + §6.2:** accept that HSM index is **blocked** on market-hours evidence (non-blocking for the REST path S6); and acknowledge the Fyers REST index live-correctness test is **pending**, not passed.
- **G-scope sign-off:** (a) confirm the focused scope (NSE stock F&O + 6 index F&O + SENSEX F&O only, BANKEX/BSE single-stock/SENSEX-50/etc. **out**), and (b) defer the canonical re-key (S11) as protected-core.
- **S8:** the active `data_test` catalog must be synced (operational) before F&O/index live behavior can be exercised *in this environment*.

**No code was written; no source/test/config/DB/migration/lockfile/build file modified; no catalogue synced; no mutating API called; all in-flight work preserved.** Stopped here — coding remains prohibited until the maintainer reviews this plan and explicitly approves implementation (S1–S10; S11 as a separate protected-core task).
