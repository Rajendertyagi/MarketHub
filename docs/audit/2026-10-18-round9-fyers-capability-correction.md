# MarketHub — Round 9: FYERS Capability Correction & Final Provider Audit (READ-ONLY)

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints, no auth changes, no credential output, no feed disturbance. All in-flight work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Date:** 2026-10-18
**Evidence classes:** **[C]** source inspection · **[T]** a test *actually executed* (none re-run this round — see §7) · **[D]** official docs (URL + date) · **[R]** authenticated live response (**none available in this read-only environment → BLOCKED**).

## 0. Headline correction (this round's purpose)

**Round 8's claim that "Fyers has no Greeks/IV" was WRONG.** The official Fyers Option Chain API (https://fyers.in/api-docs/v3/api/market-data/option-chain/, **last updated 2026-10-14**) documents a **`greeks` request parameter**: *"Set `greeks` to 1 to receive `delta`, `gamma`, `theta`, `vega` and `iv`. Each strike then carries them in a nested `greeks` object."* [D]

MarketHub's Fyers option-chain **normalizer already parses that nested `greeks` object** (`market/normalize/fyers.py:493-503`). The **sole defect** is that **`brokers/fyers/rest.py::option_chain` does not send `greeks=1`** (`rest.py:428-433` sends only `symbol`,`strikecount`,`timestamp`). **Classification: "Supported by FYERS but not requested by MarketHub" (Class 2 in §2) — a one-line parameter omission, NOT a broker limitation.** This materially changes the Fyers-only capability story and the PCR/max-pain feasibility (§3).

---

## 1. Fixed scope (unchanged)
NSE stock F&O (all eligible underlyings + active futures/options) · 6 major NSE index-F&O underlyings (spot + futures + options) · BSE SENSEX F&O (spot + active futures/options). Fyers-only / Upstox-only / dual. **No manual broker choice, instrument mapping, per-contract selection, or F&O apply in normal use.** No unrelated markets/vendors/trading.

---

## 2. FYERS Greeks & IV — verified, corrected

### 2.1 What the official API supports [D option-chain, 2026-10-14]
- Request param **`greeks`** (string "1") → each `optionsChain` leg carries a nested **`greeks:{delta,gamma,theta,vega,iv}`**.
- Same response also carries, **per strike**: `oi`, **`oich`** (OI change), **`oichp`** (OI change %), **`prev_oi`** (previous OI), `volume`, `ltp`, `ltpch`/`ltpchp`, `bid`, `ask`.
- **Top-level**: `callOi` (total call OI), `putOi` (total put OI), `expiryData[]` (available expiries), `indiavixData` (India VIX), and the first leg = the underlying (`strike_price:-1`, empty `option_type`, with `fp`/`fpch`/`fpchp` future price).
- **Strike window: `strikecount` MAX = 50 per side of ATM.**

### 2.2 What MarketHub does / misses (exact classification)

| Item | Classification | Evidence |
|---|---|---|
| Fyers option-chain greeks+IV | **Class 2 — supported, NOT requested.** `rest.py:428-433` omits `greeks=1` → response lacks the `greeks` object → normalizer's `leg.get("greeks")` returns `{}` → greeks/IV = None. | [C `rest.py`, `fyers.py:493`] |
| Fyers option-chain normalizer parsing greeks/IV | **Class 1 — correctly implemented** (given a `greeks` object). `fyers.py:493-503` maps `delta/gamma/theta/vega/iv` → `OptionContractData`. | [C] |
| Fyers option-chain previous-OI / OI-change | **Class 1 — implemented.** `fyers.py:498-499` `prev_oi→previous_oi`, `oich→oi_change`. **Note:** `oichp` (OI-change %) is documented but **not mapped** — a sub-gap. | [C] |
| Fyers option-chain IV | **Class 1 — mapped** (`iv` fraction, `fyers.py:501`) once `greeks=1` is sent. | [C] |
| Fyers capability declaration | **Defect:** `brokers/fyers/capabilities.py` declares **no** `CAP_OI_ANALYTICS/MAX_PAIN/PCR/GREEKS` — its docstring claims "analytics endpoints not implemented." This is now **inaccurate** once `greeks=1` is sent + the existing analytics compute from the chain (§3). | [C `fyers/capabilities.py:5`] |
| Fyers live-WS greeks/IV | **Class 5 — genuinely unavailable** on the HSM/WS wire (Fyers HSM has no greeks). Greeks/IV for Fyers are **REST-only** (option-chain `greeks=1`). | [D HSM undocumented; C] |

**Net Fyers-only result:** Greeks + IV **are obtainable** via the option-chain REST endpoint by adding `greeks=1`. Fyers OI / prev-OI / OI-change (and OI-change% with a small mapping addition) are **already** in the option-chain response. What is *not* available from a single Fyers endpoint is **aggregate max-pain/PCR across the full chain beyond the `strikecount` window** (§3).

---

## 3. PCR, max-pain, OI analytics — feasibility on Fyers data

### 3.1 What the existing analytics need
`market/analytics/option_chain.py`: `compute_pcr` (line 102, **sums call-leg OI / put-leg OI over all rows**), `compute_pcr_volume` (125), `compute_max_pain` (151, **OI-weighted payout minimization over every strike**). Consumed by `app/market_analytics.py:253-256`. Upstox feeds these **two different ways**: per-strike `call_put_oi_data_list` from `/v2/market/oi` (`upstox_analytics.py:44,89,119,146`) **and** the option-chain snapshot.

### 3.2 Feasibility from Fyers option-chain (`greeks=1`, `strikecount` ≤ 50)
- **PCR (OI):** `callOi`/`putOi` are **top-level totals in the Fyers response** → PCR-oi is directly computable from the Fyers chain (or by summing leg OI). **Feasible.**
- **Max-pain:** needs OI across **all** strikes; Fyers caps the window at **`strikecount` ≤ 50/side**. Within that window max-pain is **computable but bounded** — the true max-pain across a full NIFTY chain (hundreds of strikes) **cannot** be derived from a single 50-side Fyers call. **Limitation: bounded-strike-window.** The official API **does not** expose the full chain in one call for Fyers (that's Upstox's `/v2/market/oi` per-strike list).
- **OI / prev-OI / OI-change / OI-change%:** per-strike `oi`,`prev_oi`,`oich` present [C]; `oichp` present in the API [D] but **not mapped** [C] → small gap.

### 3.3 Classification
| Metric (Fyers-only) | Status |
|---|---|
| PCR (OI & volume) | **Feasible** from the Fyers chain (add `greeks=1` + compute) — **integration gap, not broker limit.** |
| Max-pain | **Partially feasible** — reliable only within the `strikecount` (≤50/side) window; full-chain max-pain **not obtainable from Fyers** (Upstox `/v2/market/oi` is the full-strike source). **Limitation must be surfaced, not silently under-sampled.** |
| OI / prev-OI / OI-change | **Available** (per-strike) [C]. |
| OI-change % | API `oichp` available [D]; **mapping gap** [C]. |
| Greeks / IV | **Available** with `greeks=1` (§2). |

**Do NOT invent values or borrow Upstox data in Fyers-only mode** — the plan's truthful behaviour: Fyers-only PCR = computed from the Fyers chain; max-pain = "bounded (strike window ≤50/side)" or `unavailable` if the window is too narrow; greeks/IV = populated when `greeks=1` is sent. **Upstox full-strike OI analytics remains the complete-data path.**

---

## 4. Upstox — independent audit (equal depth)

### 4.1 WebSocket modes & actual fields [D V3 Market Data Feed, 2026-10-18]
- Modes: `ltpc`(LTP+close+IES), `option_greeks`(greeks only), `full`(LTPC + D5 `marketLevel` + `marketOHLC` interval candles + **`oi` + `iv` + `optionGreeks` + `atp/vtt/tbq/tsq` + CAS fields), `full_d30`(full + D30, Plus only).
- **`full` mode does NOT carry `previous_oi` on the wire** (proto `MarketFullFeed` has `oi`, no `previous_oi`) [C proto; D]. So **WS `oi` is current-session only**; previous-OI for Upstox comes from **REST** (§4.2) or the **persisted** fallback — never the WS wire. This matches the code's `upstox/capabilities.py:4-6` docstring.
- Caps (verified against code `feed.py:65-70`): ltpc 5000 / option_greeks 3000 / **full 2000** / full_d30 50; **combined** 2000/2000/1500 when multi-category on one connection. MarketHub uses a single `full` default → **individual 2000 cap** (combined only if a future design mixes modes).
- Reconnect = **re-authorize** (one-time WSS code) + resubscribe full desired set (`feed.py:566-639`); backoff exponential+jitter, resets only after ≥60 s stable (`feed.py:83-88`).

### 4.2 REST Full Market Quotes V3 + `previous_oi` [D Full Market Quotes V3, 2026-10-18]
- `/v3/market-quote/quotes` (≤500 instruments) returns **`previous_oi`** for F&O + `prev_close_price`,`year_high/low`,`ohlc.volume/ts`, live CAS.
- **MarketHub's REST full-quote normalizer `quote_from_rest` (`market/normalize/upstox.py:131-166`) maps `oi→open_interest` but DROPS `previous_oi`.** This is the **exact N1 defect** confirmed: **adding the field is a one-line additive mapping; it does NOT conflict with the provider-OI precedence** (`market/oi.py:32-58` = provider-prev-OI → persisted → unavailable) or the **presence-exact + staleness-gated merge** (`market/service.py:236-335`), because a *newer* REST quote that populates `previous_oi` **wins** and an *older* snapshot is rejected by the staleness gate — **no overwrite of newer data.**
- Note: MarketHub's live-quote path is the **WS `full` feed**, not the V3 REST full-quote; the V3 REST full-quote `previous_oi` is currently **unused by the app** (no call site in `market_data.py`).

### 4.3 Option-chain greeks/IV/prev-OI, OI & change-OI [D, 2026-10-18]
- `/v2/option/chain` response `market_data` **includes `prev_oi`** and per-contract `option_greeks{vega,theta,gamma,delta,iv,pop}` [D]; consumed by `upstox.py:692-703` (incl. `iv` percent→fraction). **Correctly implemented [C].**
- `/v2/market/oi` (per-strike `call_put_oi_data_list`) + `/v2/market/change-oi` → `oi_from_rest`/`oi_change_from_rest` [C `market_data.py:231-258`]; max-pain/PCR from `/v2/market/{max-pain,pcr}` [C].
- **Endpoint-specific quotas [D rate-limit, 2026-10-18]:** Standard APIs **50 rps / 500 rmin / 2,000 per 30-min, per API + per user**; 429 → `UDAPI10005` + `Retry-After`. **Not a universal number** — order-placement and Payout have separate envelopes. MarketHub has **no app-wide REST throttle yet** (only feed backoff).

### 4.4 Auth / depth / history / market-status
- Auth expiry → `UpstoxAuthError` → `auth_required` state, no retry loop (`feed.py:582-595`) [C]. Depth D5 in `full` [C]. History V3 `/v3/historical-candle` + `/intraday` (**1-min & 30-min only**) `market_data.py:43-48` [C+D]. Market status `/v2/market/{status,timings,holidays}` [C].

---

## 5. FYERS vs UPSTOX — field-by-field matrix (fixed scope)

Legend: **L**=live feed · **R**=REST · **S**=specialized endpoint · **P**=persisted fallback · **X**=unavailable/unverified. "Wired [C]" = end-to-end path confirmed in code; "Doc [D]" = documented, not proven end-to-end; "Gap" = declared/documented but not wired.

| Field | **FYERS** | **UPSTOX** |
|---|---|---|
| LTP + prev close + change | L(HSM) + R(`/data/quotes`) [C] | L(`ltpc`) + R(`V3 quotes`) [C] |
| OHLC + volume + ts | L + R(`/data/history`) [C] | L(`marketOHLC` interval) + R V3 (day/week/month + 1/30-min) [C] |
| Current OI | L(HSM `OI`) + R(Depth) [C] | L(`full.oi`) + R/S(`/v2/market/oi`) [C] |
| **Previous OI** | **R option-chain `prev_oi` [C]** + R Depth `pdoi` [C] | **R V3 full-quote `previous_oi` — DROPPED by `quote_from_rest` (N1 gap)**; R option-chain `prev_oi` [C]; **P persisted** otherwise |
| OI change / OI-change % | **R option-chain `oich` [C]; `oichp` documented [D] but UNMAPPED (gap)**; Depth `oipercent` [C] | R/S(`/v2/market/change-oi`) [C] |
| **Greeks + IV** | **R option-chain `greeks=1` — NOT requested (Class-2 gap)**; normalizer parses it [C]; **WS: X** | L(`full.optionGreeks`/`iv`) + R(S)/option-chain [C] |
| Bid/ask + depth | L(HSM depth tolerated) + R(`/data/depth`) [C] | L(`marketLevel` D5) + R V3 `depth` [C] |
| Option chain | R(`/data/options-chain-v3`, untracked `rest.py`) [C] | R(`/v2/option/chain`) [C] |
| **PCR** | **computable** from Fyers chain (`callOi`/`putOi`) — integration, not broker [C §3] | R/S(`/v2/market/pcr`) + chain [C] |
| **Max-pain** | **bounded by `strikecount`≤50/side** — full-chain not Fyers-available [D] | R/S(`/v2/market/max-pain`) full-strike [C] |
| Historical candles | R(`/data/history`) [C] | R V3 (1/30-min intraday + day/week/month) [C] |
| Market status | R(`/data/marketStatus`) [C] | R(`/v2/market/status`,`timings`,`holidays`) [C] |
| Auth 401 | `FyersAuthError` [C] | `UpstoxAuthError` [C] |
| 429 / retry-after | `FyersRateLimitError` [C] | `UpstoxRateLimitError`+`Retry-After` [C] |

**Parity statement (mandated §9):** **Do NOT claim complete parity.** Genuine differences: (a) Fyers Greeks/IV = **REST-only** (one-line `greeks=1` gap to close); Upstox = live + REST. (b) **Fyers max-pain is strike-window-bounded**; Upstox has full-strike OI. (c) Upstox's **V3 full-quote `previous_oi` is dropped by the normalizer (N1)**; Fyers exposes prev-OI in option-chain+depth. (d) Fyers HSM index scaling unverified; Upstox `indexFF` is the working index path.

---

## 6. Verified defects (exact evidence, confidence)

| # | Defect | Provider | Status | Evidence | Confidence |
|---|---|---|---|---|---|
| G1 | **Fyers option-chain omits `greeks=1`** → greeks/IV absent in Fyers option-chain responses | Fyers | **Verified [C+D]** | `rest.py:428-433` (no `greeks` param); docs require `greeks:"1"`; `fyers.py:493` parses a `greeks` object that never arrives | High |
| G2 | **Fyers `oichp` (OI-change%) documented but unmapped** | Fyers | **Verified [C+D]** | `fyers.py:499` maps `oich` only; docs list `oichp` | High |
| G3 | **Fyers capabilities docstring claims "OI-analytics not implemented"** — now inaccurate re: greeks/IV/PCR (all chain-derived) | Fyers | **Verified [C vs D]** | `fyers/capabilities.py:5`; §2/§3 show chain supports it | High |
| G4 | **Upstox V3 full-quote `previous_oi` dropped by `quote_from_rest`** (N1, from R8) | Upstox | **Verified [C+D]** | `upstox.py:139-145` maps `oi` not `previous_oi`; docs confirm the field | High |
| G5 | `brokers/fyers/rest.py` untracked; imported at module scope (`app/server.py:695`) | Fyers | **Verified [C]** | `git ls-files` no-match | High |
| G6 | Fyers HSM index ticks dropped; scaling unverified (×3.65 captured error) | Fyers | **Verified [C]** | `fyers/feed.py:937-941,942`; **HSM undocumented in public Fyers docs [D]** | High |
| G7 | Fyers cap hardcoded 5000 (Prime); Standard=100 is the real default | Fyers | **Verified [C vs D]** | `fyers/capabilities.py:27`; Fyers KB 2026-10-05 | High |
| G8 | Quote store native-keyed; canonical computed not used as key | both | **Verified [C]** | `market/service.py:207-210,251` | High |
| G9 | `preferences().key`=upstox_key; frontend joins on it | both | **Verified [C]** | `subscriptions/service.py:231` | High |
| G10 | F&O universe not auto-restored at startup | both | **Verified [C]** | `service.py:92`; manual `POST /api/fno/apply` | High |
| G11 | Routing state-only, no quote freshness | both | **Verified [C]** | `fno_coverage.py:210-215` | High |
| G12 | Active runtime catalog empty (both providers) | both | **Verified [C]** | `config.json data_dir:"data_test"`; instruments=0 | High |
| G13 | `fno/view` storm mitigation present, regression test absent | frontend | **Partially [C]** | `useFno.ts:116` present; **test file does not exist** | High |

---

## 7. Live validation — BLOCKED (no fabricated results)

**No authenticated Fyers/Upstox session is reachable in this read-only environment; NSE/BSE market hours not confirmed; the running app/DB were not disturbed.** Therefore every [R] item below is **BLOCKED — LIVE EVIDENCE REQUIRED**, with the exact procedure + acceptance:

| Test | Procedure | Acceptance | Status |
|---|---|---|---|
| **Fyers option-chain greeks/IV** | `GET /data/options-chain-v3?symbol=NSE:NIFTY50-INDEX&greeks=1&strikecount=2` (read-only) | each CE/PE leg carries `greeks{delta,gamma,theta,vega,iv}` populated (iv 0–200, delta −1..1) | **BLOCKED [R]** |
| **Fyers REST index correctness** | `GET /data/quotes?symbols=NSE:NIFTY50-INDEX,BSE:SENSEX-INDEX` | LTP within **≤0.05 index pts** of NSE/BSE published value at the same minute; record ts/source/budget | **BLOCKED [R]** |
| **Fyers HSM index scaling** | subscribe `if\|nse_cm\|Nifty 50`; capture snapshot raw `ltp`,`multiplier`,`precision`,`exch_feed_time` | `ltp/(10**precision*multiplier)` within ≤0.05 pts of NSE published, 3 snapshots; vs Upstox `indexFF` as independent ref | **BLOCKED [R]** |
| **Upstox `full`-mode field delivery** | capture one `full` WS frame for an in-scope F&O contract | `ltpc`,`marketLevel`(D5),`marketOHLC`(day),`oi`,`iv`,`optionGreeks`,`atp/vtt/tbq/tsq` populate; **confirm `previous_oi` is NOT on the WS wire** | **BLOCKED [R]** |
| **Upstox V3 full-quote `previous_oi`** | `GET /v3/market-quote/quotes?instrument_key=<F&O key>` | `previous_oi` present for an F&O instrument (validates G4 fix target) | **BLOCKED [R]** |
| **Dual-provider selection / both-down** | kill one feed → the other supplies a canonical quote; kill both → `unavailable` | selected quote retains source+ts+provider; stale/invalid never `live`; both-down = `unavailable`, not fabricated | **BLOCKED [R]** |
| **Tests re-execution** | run `test_upstox_feed`, `test_fno_coverage`, `test_fyers_integration`, `test_option_chain_history`, `test_previous_oi` | record pass/fail | **NOT executed this round** (read-only) — mark "present, not re-run" [T] |

**No live result is claimed or invented.** The documented schema alone does not prove end-to-end delivery (§7 rule).

---

## 8. Provider-independent architecture (confirmed, unchanged from R8 §3)

- **Provider-native observations** (each adapter, keyed by canonical ID; `provider`,`provider_token`,`source` ws/rest,`received_ts`,`validity`) — adapters never suppress their own ticks for cross-provider selection (the Fyers HSM index guard is a *validity* guard, G6, not preference).
- **Canonical instrument identity** (`market_identity.py`); Fyers `NSE:NIFTY50-INDEX` ≡ Upstox `NSE_INDEX|Nifty 50` ≡ `NSE:INDEX:NIFTY`.
- **Centrally selected quote** = only thing consumers see; chosen by `enabled∧auth ∧ validity ∧ freshness ∧ entitlement/plan-cap`; **preference = tie-break only.**
- Exposed quote carries canonical_id, provider, native token, source, ts, `basis`(live/stale/unavailable), `validity`. Stale/rejected never `live`.
- **All consumers** (option chain, ATM spot, futures heatmap, OI analytics, market-intel, REST, SSE, dashboard, watchlists) join on the canonical ID — no key-format heuristics, no component repairs.
- **Transitional vs final:** additive **coordinator** reads the existing provider-keyed store (safe); the **write-path re-key to `(exchange, canonical_id)` remains a separate protected-core stage.**
- **Capacity policy (unchanged unless code disproves it — it does not):** `effective_budget = cap − max(10%×cap,50)` → **50 / 4500 / 1800**; app cap **500** applied as `min(...,500)`; **Fyers Standard=100 default, Prime=5000 centralized override.** No new inconsistent figures introduced.

---

## 9. Dependency-ordered implementation plan (non-destructive rollback; NO coding this round)

> Rollback = selective reversal of *only* the stage's added lines after safeguarding in-flight edits. **No whole-file revert; no `git reset/clean/checkout/restore/stash/rebase`.** No new persistence field; no protected-startup change.

| # | Stage | Exact boundary | Deps | Data/schema | Risk | Non-destructive rollback |
|---|---|---|---|---|---|---|
| S1 | Make `brokers/fyers/rest.py` available (G5) | **additive:** leave file in tree (staged); a commit holding *only* this file is additive-safe | — | none | none | forward-only (removal is a later forward change, never a reset) |
| S2 | **Centralized plan-aware capacity** (G7) | new `providers/capacity.py` (`fyers.plan_cap:100 default / 5000 Prime-override`, `upstox.full`); `registry.budget()` → `min(effective_budget(plan_cap),500)`; replace hardcoded `fyers/capabilities.py:27`; **add tests** pinning 50/4500/1800 + the `min()` | S1 | config (additive) + tests | medium | revert the config file, the one `capabilities.py` line, and tests |
| S3 | **Fyers option-chain `greeks=1`** (G1) + **`oichp` mapping** (G2) | `fyers/rest.py::option_chain` add `params["greeks"]="1"`; `fyers.py:498-499` add `oichp→oi_change_percent` (mirror the option-chain path) | S1 | none | low | revert the one param line + the mapping line |
| S4 | **Upstox V3 full-quote `previous_oi` mapping** (G4) | `upstox.py::quote_from_rest` add `previous_oi` mapping (additive); regression: provider-prev-OI beats persisted; older REST snapshot rejected by staleness | S1 | none | low | revert the added mapping line |
| S5 | **Correct Fyers capability declarations** (G3) | `fyers/capabilities.py` add `CAP_OI_ANALYTICS`(chain-bounded), `CAP_OPTION_CHAIN`(with greeks) truthfully; document max-pain window limit | S3 | none | low | revert |
| S6 | **Central quote coordinator + freshness/validity** (G8,G9,G11) | new `providers` selection policy + `market/service.get_canonical_quote` (additive) + `fno_coverage._available` (add quote-age) + endpoint-specific rate buckets | S2,S4 | none | medium-high | revert the 3 additions |
| S7 | **Canonical join in `preferences()` + all consumers** (G9) | `preferences().key`→canonical; `IndicesWidget`,`types.ts`,`WatchlistTable`,heatmap/SSE/option-chain switch to the canonical contract | S6 | none | medium | selective hunk reversal per in-flight file |
| S8 | **Auto-restore F&O at startup, NO new field** (G10) | `app/server.py` lifespan: `plan()`→`set_derivatives_universe()`→existing `reconcile()` (~3 lines at `:1220`) | S6 | none | low-medium | revert the added lifespan lines |
| S9 | **Fyers-only index via REST** (G6-alt) | `fyers/rest.py::quotes` + coordinator index source + bounded market-hours poller (≤5 rps, 60 s, shared bucket, 5-min freshness, dedup) | S6 | none | medium | revert poller + index-source lines; HSM guard untouched |
| S10 | **Market-hours validation tests** (all §7 [R]) | harness: Fyers `greeks=1` chain, Fyers REST index, Fyers HSM scaling, Upstox `full` fields, Upstox V3 `previous_oi`, dual failover | S9 | none (evidence) | n/a | n/a — **do not claim passed** |
| S11 | Catalogue init (operational, approved) | `POST /api/instruments/sync` vs the *intended* data dir | S1 | populates `instruments` (target env; `data_test` untouched) | low | idempotent/transactional; **not run in this review** |
| S12 | `migrate_from_config` Fyers index categorization | `subscriptions/service.py::migrate_from_config` | S7 | one-time (idempotent) | low | revert |
| S13 | `fno/view` storm regression test (G13) | add `frontend/src/__tests__/FnoWorkspaceView.test.tsx` | — | none | low | delete new test |
| S14 | **Max-pain strike-window handling** | surface Fyers max-pain as "bounded (≤50/side)" vs Upstox full-strike; no fabricated full-chain value | S5,S6 | none | low | revert |
| S15 | **Canonical write-path re-key (final)** ⚠ protected-core | `market/service.py` `_quotes` → `(exchange, canonical_id)` + migration | S6 proven | data migration | high | **separate protected-core approval; not in the ordinary gate** |

**Critical path (safe all-three-config):** S1 → S2 → S3 (Fyers greeks/IV) → S4 (Upstox prev-OI) → S5 → S6 → S7 → S8 → S9 → S10 (live evidence, BLOCKED-gated) → S11. **S10 gates the *trust* of S9's index path and S3/S4 field delivery — no stage is declared validated until S10's live tests pass.**

**Acceptance tests (per stage + config):** Fyers-only / Upstox-only / dual × (index spot, stock F&O fut/opt, NSE index fut/opt, SENSEX fut/opt, prev-OI/OI-change, greeks/IV, depth, option-chain+ATM, heatmap, OI-analytics, REST, SSE, frontend); stale/unavailable truthfulness; capacity-exceeded `CAPACITY_LIMITED`; auth-expired `auth_required`; catalog-unavailable `desired:0`; no request storm (S13). Clean checkout (S1): no `ModuleNotFoundError`; `test_architecture_boundaries.py` green.

---

## 10. Remaining uncertainties + exact evidence

1. **U1 Fyers HSM index scaling** (G6) — live `if\|` capture, ≤0.05-pt, 3 snapshots; **BLOCKED [R]**. Blocks Fyers *streaming* index only (REST index S9 does not depend on it).
2. **U2 Fyers option-chain `greeks=1` live response** (S3/S10) — confirm nested `greeks` populates; **BLOCKED [R]**.
3. **U3 Fyers REST index live-correctness** (S9/S10) — `/data/quotes` NIFTY/SENSEX vs published, ≤0.05-pt; **BLOCKED [R]**.
4. **U4 Upstox `full`-mode live field delivery + `previous_oi` NOT on wire** (S4/S10) — capture a `full` frame; **BLOCKED [R]**.
5. **U5 Upstox V3 full-quote `previous_oi` present for F&O** (S4) — `GET /v3/market-quote/quotes`; **BLOCKED [R]**.
6. **U6 Upstox account tier / per-endpoint quotas** (50/500/2000-30min standard vs Plus) — confirm vs connected account; **BLOCKED [R]** (residual, not Fyers-only-blocking).
7. **U7 Active runtime catalog empty** (G12) — closed by S11 (operational sync), not read-only.
8. **U8 Max-pain strike-window** (S14) — decide: Fyers max-pain is "bounded ≤50/side" (documented) vs "unavailable" when window too narrow — a **presentation policy** decision.

**Evidence classes:** [C] G1–G13 source-inspected; [D] Fyers option-chain `greeks=1` + per-strike OI/prev-OI/oichp (2026-10-14), Upstox V3 feed/full-quote/option-chain/OI/rate-limit (2026-10-18); [T] tests **present but not re-executed** this round; [R] U1–U6 **BLOCKED**.

---

## 11. Maintainer decisions that genuinely remain

1. **D-1:** Approve S3 (`greeks=1` + `oichp`) and S5 (capability correction) — makes **Fyers Greeks/IV/PCR real** (turns a R8 "unavailable" into "available"). *(Recommended: approve — it's a one-line request + a small mapping.)*
2. **D-2 (U8):** Fyers max-pain **bounded-window** representation — accept "max-pain (window ≤50/side)" as the truthful Fyers-only state (full-strike remains Upstox-only).
3. **D-3:** Upstox V3 full-quote `previous_oi` mapping (S4/G4) — approve the additive normalizer fix.
4. **D-4:** Defer the canonical write-path re-key (S15) as a **separate protected-core** task; the additive coordinator (S6) is the interim path.
5. **D-5:** Fyers OI-analytics (max-pain) **as a new Depth/chain-based build** — **not required** for the fixed scope (Fyers chain covers PCR; max-pain is window-bounded); approve only if full Fyers max-pain is genuinely needed.

---

## 12. Status

**READY FOR MAINTAINER REVIEW** — of the *architecture and implementation plan*.

- The **material architectural question this round targeted (Fyers Greeks/IV) is now resolved with evidence:** Fyers **supports** greeks/IV via the option-chain `greeks=1` parameter [D, 2026-10-14], MarketHub **parses** the nested object [C], and the **only gap is the missing request parameter (G1)** — a one-line fix, not a broker limitation. PCR is Fyers-computable; max-pain is window-bounded (documented, D-2). The Upstox `previous_oi` normalizer drop (G4) and the Fyers HSM index scaling (G6) remain the two genuine data-validity items, both safely gated behind live tests (U1–U5), which are **explicit prerequisites, not claimed passes**.
- The provider-independent architecture (canonical identity + per-provider observations + central coordinator, preference as tie-break), the capacity policy (50/4500/1800, app 500, Fyers Standard default), and the automatic-subscription reconstruction (no new persistence) are internally consistent and non-conflicting.
- **Outstanding items are external live tests (U1–U6, U7 operational) and 5 bounded maintainer decisions (D-1…D-5)** — **none is an unresolved *architectural* question.** No live result was fabricated; no stage is declared validated until S10's tests pass.

**No code written; no source/test/config/DB/migration/lockfile/build file modified; no catalogue synced; no mutating API; no destructive git; all in-flight work preserved.** Stopped — coding prohibited until the maintainer reviews this plan (approving S1–S14, deferring S15 as protected-core, recording U1–U5 evidence as the gate, and deciding D-1…D-5).
