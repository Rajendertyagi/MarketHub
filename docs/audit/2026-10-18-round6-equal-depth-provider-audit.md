# MarketHub — Round 6: Equal-Depth Fyers & Upstox Audit (READ-ONLY)

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints. No `git reset/clean/checkout/restore/stash/rebase`. All in-flight work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Date:** 2026-10-18
**Repo state recorded (§6):** 208 staged-new + 29 modified + 268 untracked. In-flight provider files: `brokers/{fyers,upstox}/capabilities.py` (AM), `brokers/upstox/feed.py|feed_processing.py|proto` (M), `brokers/fyers/auth.py|rest.py` (M/??), `market/normalize/{upstox,fyers}.py` (M), `market/service.py` (M), `app/{fno_coverage,subscriptions/service,market_indices,market_data}.py`, `providers/*` (new). **Neither provider is a hidden mandatory dependency in the code — the defects are below.**
**Scope (fixed, §1):** NSE stock F&O + 6 major NSE index-F&O underlyings + BSE SENSEX F&O. **Out of scope:** full NSE benchmarking-index universe, broader BSE index family, BANKEX/SENSEX-50 derivatives, BSE single-stock F&O, MCX/NCD/COM, trading/order execution, new data vendors.

**Key framing correction from prior rounds:** the codebase is *already* provider-balanced more than previously credited. `app/market_data.py` (`ProviderMarketData`) routes history, option-chain, OI/PCR/max-pain, option-greeks, and analytics **per-provider** (`_resolve(provider)`), and Fyers has explicit "not implemented for fyers" guards where Upstox has an API (e.g. OI analytics). Both adapters share the same `UpstoxRest`-mirrored REST pattern. The Round-6 audit confirms **where each provider's path is complete vs. where it is declared-but-unwired**, so no provider defect defers the other.

**Evidence classes used throughout:**
- **[C] code** — read directly from the active working tree (staged+unstaged).
- **[T] test** — demonstrated by an existing automated test.
- **[D] docs** — official provider/exchange documentation (URL + check date).
- **[R] response** — actual authenticated provider response (**none available in this read-only round** — every live claim is explicitly marked *requires live test*).
- **[A] assumption** — reasoned but unverified.

---

## 1. Provider comparison matrix (separate evidence for each)

### 1.1 Instrument catalogue & coverage

| Aspect | **Fyers** | **Upstox** |
|---|---|---|
| Master download/parse | `public.fyers.in/sym_details/<SEG>_sym_master.json` (JSON object keyed `EXCH:SYMBOL`), `app/instruments.py::fyers_master_records` [C] | `assets.upstox.com/market-quote/instruments/exchange/complete.json.gz` (JSON array), `app/instruments.py` upstox parser [C] |
| Canonical fields per row | fyToken, exchange, segment, exInstType, expiryDate (epoch), optType, strikePrice, minLotSize, tickSize, isin, underSym → normalized [C `instruments.py:214-233`] | instrument_key, exchange, segment, instrument_type, expiry, strike_price, lot_size, tick_size, isin, underlying_symbol → normalized [C `instruments.py:150-165`] |
| NSE stock F&O underlyings | `exInstType` 11/13 (futures), 14/15 (options); equity-underlying F&O discovered via `underlying`/`underSym` [C] | segment `NSE_FO`; equity-underlying F&O via `underlying_symbol` [C] |
| Major NSE index-F&O (6) | `exInstType` 10/11/14; index symbols `NSE:*-INDEX` [C] | segment `NSE_INDEX`/`NSE_FO` (index futures/options) [C] |
| BSE SENSEX F&O | segment `BSE_FO`/`BSE_INDEX` [C; `DEFAULT_SEGMENTS` `instruments.py:252-254` includes BSE_FO+BSE_INDEX] | segment `BSE_FO`/`BSE_INDEX` [C] |
| Expiries/strikes/CE-PE/lot/tick | All present in master (Fyers `expiryDate` epoch→ISO `:190-199`; strike/optType/minLotSize/tickSize) [C] | All present (Upstox `expiry`, `strike_price`, `CE/PE` from `instrument_type`, `lot_size`, `tick_size`) [C] |
| Removed/expired/renamed/changed | **Catalogue re-sync** (epoch bump invalidates `futures_index`/`fno_underlying_rows` caches) + `_choose_expiry` filters `expiry>=today` [C `derivatives_universe.py:58,209`] | Same mechanism (catalog-epoch driven) [C] |
| Duplicated/unsupported/mis-mapped | Fyers index rows live *inside* the Fyers cash master; `_derive_segment` (`instruments.py:262-272`) separates index from equity per row — no conflation [C] | Upstox index rows are a distinct segment; clean [C] |
| **Active runtime catalog** | `data_test/events.db` **instruments = 0 rows** (active env per `config.json data_dir:"data_test"`); populated `data/events.db` = 142,932 (separate env) [C] | **Same** — the empty active runtime applies to *both* providers (one shared `instruments` table) [C] |

> **Environment separation (mandated §2A):** all "catalog has the instruments" evidence above is from the **populated `data/events.db`** environment. The **active `data_test/events.db` is empty** for both providers, so in the live runtime *neither* provider can discover F&O/index/SENSEX contracts until a catalogue sync runs (a future approved operational step, §5/§7 — **not done here**).

### 1.2 Live-feed implementation

| Aspect | **Fyers (HSM)** | **Upstox (V3 WS)** |
|---|---|---|
| Authorization | `broker/fyers/auth.py` OAuth+PIN; HSM socket auth via `hsm_key` in the access-token JWT (`feed.py:148-163`) [C] | `authorize_market_feed` → one-time WSS code (`rest.py:280-300`), never stored/logged [C `feed.py:14-17`] |
| Lifecycle | connect→subscribe(full mode marker `0x46` / lite `0x4c`)→recv (`feed.py:555-660`) [C] | authorize→connect→subscribe→recv; reconnect with **fresh** authorization, resubscribe full desired set (`feed.py:566-639`) [C] |
| Modes used by MarketHub | `full` only (`capabilities.py MODE_KEY_LIMITS={"full":5000}` — **Prime assumption, refuted in R5**) [C] | `full` default (`DEFAULT_MODE="full"`), `SUPPORTED_MODES=("ltpc","option_greeks","full","full_d30")` [C `feed.py:64-70`] |
| Message decode | Binary HSM frames (resp_type 6), ported from official SDK (`feed.py:851-930`) [C] | **Protobuf V3** `MarketDataFeed` (`feed_protocol.py`, `proto/MarketDataFeed_pb2.py`); `process_binary_frame` [C `feed_processing.py:222-279`] |
| V3 message format verified | — | **Yes.** Decoded `initial_feed`/`live_feed`/`market_info`; per-instrument routing: `ltpc` → `quote_fields_from_ws_ltpc`; `fullFeed.marketFF`/`indexFF` → `quote_fields_from_ws_full` (+`marketLevel`→`depth_from_ws`); `firstLevelWithGreeks` → greeks normalizer [C `feed_processing.py:164-196`] |
| Fields actually received under `full` | HSM `sf|` stock/future ticks: LTP, OHLC, OI, high/low, volume, depth (`_DATA_VAL`) [C] | `full` mode = **LTPC + D5 marketLevel + marketOHLC + OI + IV + greeks + aTP/vtt/tbq/tsq** [D Upstox V3 docs; C proto]. **Note: `full` does NOT carry previous-OI/OI-change** (R5 finding stands). |
| Index spot | **HSM `if|` index ticks DROPPED** (`feed.py:937-941`), guard reason = "Upstox is authoritative index source" [C] | **Upstox `indexFF` in `full` mode is the working index path** (`feed_processing.py:185-188`) [C] |
| Timestamps | `received_ts` from socket; exchange_ts optional [C] | `ltt` (LTP timestamp) + `received_ts` [C `proto`] |
| Reconnect/recovery | exponential+jitter backoff `_next_backoff` (`feed.py:455,577-672`); resubscribe full desired set on reconnect [C] | same backoff model (`feed.py:384,601-672`); **reconnect always re-authorizes** (one-time code is single-use) [C] |
| Subscribes OK but no ticks | Not explicitly distinguished [A] | `streaming` state + per-instrument counts; unknown keys dropped as `unknown_instrument` (`feed_processing.py:261-265`) — a successful sub that never yields usable ticks surfaces as absence, not error [C] |
| Provider assumptions in shared services | `_infer_provider`/`_provider_of` use key-format heuristics (`market_data.py:90-106`, `subscriptions/service.py:41-52`) — **scattered, both providers present** [C] | same |

### 1.3 REST APIs + field-level capability matrix

**Legend:** **L** = provider live feed · **R** = REST · **P** = cached/persisted only · **X** = unsupported/unverified.

| Field | **Fyers** | **Upstox** | Evidence |
|---|---|---|---|
| LTP + previous close | L (HSM) + R (`/data/quotes`) | L (`ltpc.ltp`/`cp`) + R (`/v2/market-quote`) | [C+D] |
| OHLC + volume + ts | L + R (`/data/history`) | L (`marketOHLC`) + R (`/v2/historical-candle[intraday]`) | [C+D] |
| Open interest (current) | L (HSM `OI`) + R | L (`full.oi`) + R (`/v2/market/oi`) | [C+D] |
| Previous OI | **R only** (`depth` `pdoi`) [D] | **P** — not on wire; persisted previous-session OI is the fallback [C `upstox/capabilities.py:4-6`] | [C+D] |
| OI change / OI-change % | **R only** (`depth` `oipercent`) [D] | **R** (`/v2/market/change-oi`) | [C+D] |
| Option Greeks + IV | **X** (not declared in `fyers/capabilities.py`) [C] | **L** (`full.optionGreeks`/`iv`) + **R** (`/v3/market-quote/option-greek`, ≤50/req) | [C+D] |
| Bid/ask + depth (D5) | L (HSM depth, tolerated) + R (`/data/depth`) | L (`marketLevel` D5) + R | [C+D] |
| Option chain | **R** (`/data/options-chain-v3`) — *untracked file `brokers/fyers/rest.py`* [C] | **R** (`/v2/option/chain`, returns last-session even closed) [C `market_data.py:218-220`] | [C+D] |
| Historical data | **R** (`/data/history`) | **R** (`/v2/historical-candle` + `intraday`, day/week/month + 1min/30min only) [C `market_data.py:43-48`] | [C+D] |
| OI analytics (max-pain/PCR) | **X** — `market_data.py:235,250,264,279` raise "fyers … not yet implemented" [C] | **R** (`/v2/market/max-pain`,`/pcr`,`/oi`) [C] | [C] |
| Market status | **R** (`/data/marketStatus`) [C] | **R** (`/v2/market/timings`+`holidays`) [C] | [C+D] |
| Failure responses | typed `FyersAuthError`/`FyersRateLimitError`/`FyersRestError` (429) [C] | typed `UpstoxAuthError`/`UpstoxRateLimitError`(Retry-After)/`UpstoxRestError` [C] | [C] |

**Critical honesty note (§2C):** a *declared* capability ≠ a working end-to-end path.
- **Fyers:** `CAP_OHLC/CAP_HISTORY/CAP_OPTION_CHAIN` are declared **and** implemented in the **untracked** `brokers/fyers/rest.py` [C]. OI/prev-OI/OI-change% come from REST depth only [D]. **No OI-analyis, no greeks, no IV** [C]. The `fiures` option chain works only once `rest.py` is committed (R5 defect V1).
- **Upstox:** OI analytics, greeks, PCR, max-pain, option-chain, history are **all wired** through `ProviderMarketData` → `UpstoxRest.authenticated_request` → dedicated normalizers [C]. **However `CAP_OHLC` is declared but `full` mode's `marketOHLC` delivers candle *intervals* (1/30-min/day), not per-tick OHLC — verify the UI's "OHLC" expectation matches** [A]. Previous-OI is **persisted, not live** [C].

### 1.4 Limits & quotas (current official docs, checked 2026-10-18)

| | **Fyers** | **Upstox** |
|---|---|---|
| Data socket symbols | **Standard=100** (default), Prime=5,000 [D KB 2026-10-05] | ltpc 5000 / option_greeks 3000 / **full 2000** / full_d30 50 (Plus); combined 2000/2000/1500 [D V3 docs] |
| Effective budget (`cap−max(10%,50)`) | **90 (Standard)** / 4,450 (Prime) | **1,800 (full)** |
| REST rate (data) | **Standard 5 rps / 50 rmin / 5,000/day**; Prime 10/500/500,000 [D] | **Standard APIs 50 rps / 500 rmin / 2,000 per 30-min** [D rate-limit page] |
| App cap | `MAX_INSTRUMENTS_PER_PROVIDER=500` [C] | same shared cap [C] |
| REST 429 handling | typed `FyersRateLimitError` [C] | typed `UpstoxRateLimitError` + `_parse_retry_after` [C `rest.py:174`] |

**Safe defaults (no Prime / no Plus assumed):** Fyers plans for **Standard 90**; Upstox plans for **full 1,800** (standard tier). Account-plan/entitlement is **not reliably exposed by any documented profile field** (Fyers profile = name/email only [D]; Upstox Plus is a paid tier) → **state the limitation and default to the lower cap; make overrides centralized + explicitly explained (Round-4/5 model).**

---

## 2. End-to-end data-flow map (both providers, canonical identity → consumers)

```
PROVIDER MASTERS
  Fyers: public.fyers.in/sym_details/<SEG>_sym_master.json ── fyers_master_records()
  Upstox: assets.upstox.com/.../complete.json.gz ──────── upstox parser()
                                   │  sync (manual, epoch-bumped) — NOT run in this audit
                                   ▼
INSTRUMENT CATALOG  SQLite `instruments`   ← EMPTY in active data_test/events.db
  │  (provider-native: Fyers fyToken / colon; Upstox pipe keys)
  ▼
CANONICAL IDENTITY  app/market_identity.py → {EXCH}:{TYPE}:{…}  (shared by BOTH)
  │   Fyers NSE:NIFTY50-INDEX ≡ Upstox NSE_INDEX|Nifty 50 ≡ NSE:INDEX:NIFTY  [C]
  ▼
LIVE FEEDS
  Fyers HSM  ─┐  (if| index ticks DROPPED at feed.py:937)   sf| stock/future ticks delivered
  Upstox V3  ─┴─► QuotePatch (per provider, native token + source + ts)
                                   │
                                   ▼
MARKET SERVICE  market/service.py   _quotes[(exchange, native_token)]   ← TWO slots per instrument [C]
   • get_quote_now = raw native-key lookup (no canonical resolver) [C]
   • Quote.canonical_id IS computed in _merge_quote (:251) but NOT the storage key [C]
   • _on_market_quote_update → SSE /api/market/stream
                                   ▼
QUOTED API + CONSUMERS (per-provider where applicable)
  ProviderMarketData (app/market_data.py): history/chain/OI/greeks/max-pain/PCR/IV — _resolve(provider) [C]
  FnoCoverageService (app/fno_coverage.py): plan() routes NSE+BSE futures+options, bulk catalog read [C]
  SubscriptionService (app/subscriptions/service.py): resolve() desired keys; reconcile() add/remove [C]
  Consumers: IndicesWidget.tsx (joins on upstox_key [C, defect]), WatchlistTable, futures heatmap
   (/api/futures/heatmap), option chain (/api/options/chain/view), market intel, SSE [C]
```

**Shared-service provider assumptions (both providers, §2D defect):** `_infer_provider`/`_provider_of`/`_STYLE_MATCHERS` infer provider from key format (pipe→upstox, colon/numeric→fyers). This is **symmetric** (not Upstox-only), but it is the scattered-provider-identifier pattern to remove.

---

## 3. Verified defect list (both providers, exact evidence)

| # | Defect | Provider | Status | Evidence | Impact | Confidence |
|---|---|---|---|---|---|---|
| D1 | `brokers/fyers/rest.py` untracked; imported at module scope | Fyers | **Verified [C]** | `git ls-files` no-match; `app/server.py:695-696` | clean-checkout `ModuleNotFoundError`; Fyers history/chain/quotes/depth/marketStatus all unavailable | High |
| D2 | Fyers HSM index ticks dropped | Fyers | **Verified [C]** | `feed.py:937-941` `if topic.startswith("if"): return` | Fyers-only: no *streaming* index quote | High |
| D3 | Fyers HSM index-price scaling unverified | Fyers | **Verified-unverified [C+captured]** | `feed.py:942` scale; comment records NIFTY `mult=28/prec=0→87,205.71` vs true ~23,897 (×3.65) | HSM index LTP untrusted; guard (D2) must stay | High |
| D4 | Quote store keyed on native token, not canonical; read path doesn't consult canonical resolver | **Both** | **Verified [C]** | `market/service.py:207-210` raw lookup; `:251` canonical computed not used as key | two slots/instrument; cross-provider reads don't converge | High |
| D5 | `preferences().key` = `upstox_key`; frontend joins on it | **Both** (Upstox-native bias) | **Verified [C]** | `subscriptions/service.py:231`; `IndicesWidget.tsx`/`types.ts` | Fyers-only index cards show "—"; frontend repairs key mismatch per-component | High |
| D6 | F&O universe not auto-restored at startup (manual apply required) | **Both** | **Verified [C]** | `_derivatives_universe` in-memory (`service.py:92`); lifespan `reconcile` (`server.py:1220`) but only manual `POST /api/fno/apply` sets it | normal use requires a manual apply (violates §5) | High |
| D7 | Routing uses connection state only, not quote freshness | **Both** | **Verified [C]** | `fno_coverage._available` (:210-215) state-only | stale-but-streaming provider still wins; no STALE-vs-live | High |
| D8 | Fyers capability cap assumes Prime (5000/4450) | Fyers | **Verified [C vs D]** | `fyers/capabilities.py` `full:5000`; docs: Standard=100 | over-subscribes a Standard account; app cap 500 exceeds Standard 90 | High |
| D9 | Active runtime catalog empty (both providers) | **Both** | **Verified [C]** | `config.json data_dir:"data_test"`; `data_test/events.db` instruments=0 | no F&O/index/SENSEX discovery until sync (operational, not run here) | High |
| D10 | Upstox previous-OI / OI-change not on the wire | Upstox | **Verified [C]** | `upstox/capabilities.py:4-6` docstring; `full` proto has no prev-OI field [C proto] | OI% relies on persisted previous-session fallback; a fresh Upstox-only deploy shows OI-change as `P` not `L` | High |
| D11 | Fyers OI-analyis (max-pain/PCR/OI/IV/greeks) not implemented | Fyers | **Verified [C]** | `market_data.py:235-289` "fyers … not yet implemented" | Fyers-only: OI-analytics + greeks/IV features unavailable (Upstox-only capability) | High |
| D12 | `migrate_from_config` mis-categorizes Fyers index keys as `stock` | Fyers | **Verified [C]** | `data_test` `('stock','NSE:NIFTY50-INDEX')`; `service.py:187` matches upstox keys only | Fyers index subscriptions imported under wrong category | High |
| D13 | `fno/view` request-storm fix present but **untested** | both (frontend) | **Partially verified [C]** | `FnoWorkspaceView.tsx:38,67` uses stable `useFnoActiveView` (`useFno.ts:116`); **`frontend/src/__tests__/FnoWorkspaceView.test.tsx` does not exist** [C] | storm mitigation exists in code but has no regression test | High |
| D14 | `CAP_OHLC` declared for Upstox, but `full` delivers candle-interval OHLC, not per-tick | Upstox | **Unverified [A]** | `upstox/capabilities.py` declares `CAP_OHLC`; `MarketOHLC` proto = interval candles [C proto] | verify UI "OHLC" expectation matches interval candles; mark **verify** not assume | Medium |

**Provider-balance check (mandated §7):** Upstox defects D10/D14 and Fyers defects D1/D2/D3/D8/D11/D12 are recorded at equal depth; no provider's defect defers the other's investigation.

---

## 4. Three-mode test matrix (Fyers-only / Upstox-only / dual)

> **Every cell below states the *code path* and whether it is verified by [C]/[T] or **requires an authenticated market-hours test [R]**. No live response was captured in this read-only round; success is *not* claimed from code inspection alone.**

| Capability | **Fyers-only** | **Upstox-only** | **Dual** |
|---|---|---|---|
| In-scope index spot (NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/NXT50/FPI/SENSEX) | **HSM index dropped (D2)** → needs **REST `/data/quotes` index refresh [R-test]**; HSM index untrusted (D3) | **`indexFF` live in `full` mode [C+T]** → works; verify live [R] | freshest validated wins (central coordinator); Upstox live, Fyers REST fallback |
| NSE stock F&O contracts | HSM `sf|` futures/options ticks [C]; OI live [C]; greeks/IV **X** (D11) | `full` ltpc/oi/greeks/iv [C]; greeks/IV live [C] | converge canonical; per-field: OI both, greeks Upstox-only |
| Major NSE index futures/options | futures/options ticks [C]; options **REST on-demand** (`options-chain-v3`, D1-blocked until commit) | futures/options ticks [C]; chain REST [C] | same, provider-routed |
| BSE SENSEX futures/options | HSM BSE ticks [A — verify]; options REST [C] | `full` BSE ticks [C]; SENSEX chain REST [C] | Upstox authoritative for BSE options (fyers BSE thin) [C `data/events.db` counts] |
| Option-chain + ATM spot | **blocked on D1 commit + [R]** | works [C+T] | converges on canonical |
| Futures heatmap / OI analytics | heatmap ok; **OI-analytics unavailable (D11)** | full (OI/PCR/max-pain REST) [C] | Upstox for analytics, Fyers for OI ticks |
| SSE / REST quote / market-intel | uses native-key lookup (D4) → fix via canonical | same (D4) | same (D4) |
| **Failure/fallback** | feed `auth_required` → all in-scope `unavailable` (truthful); stale provider ≠ live (D7 fix) | symmetric | one provider down → other supplies; **both down → `unavailable`, never fabricated `live`** |
| **Verified by [T]?** | partial (`test_fyers_integration.py` [C present]) | `test_upstox_feed.py`/`test_upstox_wiring.py` [C present] | `test_fno_coverage.py` [C present] |
| **Requires [R] (authenticated market-hours)?** | **YES** (REST index correctness, HSM scaling D3) | **YES** (confirm `full` delivers required fields live, D14) | **YES** (cross-provider convergence, failover, both-down) |

**Stale/unavailable semantics (all modes):** `fresh`/`stale`/`unavailable` × `source` (WS/REST) × `validity` (validated/rejected). **Stale or rejected values are never shown as `live`.** Both-providers-down ⇒ truthful `unavailable`.

---

## 5. Canonical identity + automatic subscription (final design, both providers)

### 5.1 Canonical identity (symmetric, provider-balanced)
- **One canonical ID per real instrument** (`market_identity.py`); Fyers `NSE:NIFTY50-INDEX` ≡ Upstox `NSE_INDEX|Nifty 50` ≡ `NSE:INDEX:NIFTY`. **Both adapters deliver their observations** keyed by canonical ID + native token + source + ts + provenance. **Adapters never discard their own ticks to do cross-provider selection** (the D2 index-tick drop is the *one* place this violates the rule and is itself untrusted-scaling, D3 — see §7 S2/S6).
- **Central coordinator** (new, `providers` policy + `market/service.get_canonical_quote`) selects the exposed quote by `enabled∧authenticated`, `validity`, `freshness`, `entitlement/plan-cap`, with **preference order as a tie-breaker only** — **no hidden mandatory provider** (fixes D5/D7 and the "Upstox-first" assumption).
- **Both observations retained** (per-provider last-observation) so a failover to the other provider's data is always possible.
- **Frontend contract:** join on **canonical ID**; provider-native keys become diagnostics only (removes D5 from *every* consumer, not just `IndicesWidget`).

### 5.2 Automatic subscriptions (no manual provider/instrument/apply)
- **Single source of truth (recomputed, not manually stored):** `resolve(fno_config[declarative intent] × catalogue × active_views × enabled∧authenticated providers × plan caps)`. `fno_config` **already persists declarative intent** ("always resolved dynamically", `core/persistence/modules/fno_config.py:5-7`) → **no new DB field** (resolves the R3/R5 persistence contradiction; D6 fixed by re-deriving at startup through the existing `reconcile`).
- **Triggers (all automatic):** startup restore (D6), active-view change, provider reconnect/failover, expiry rollover (`_choose_expiry` filters `>= today`), catalogue epoch bump, Fyers-only⇄Upstox-only⇄dual transitions.
- **Capacity (from verified limits, no Prime/Plus assumed):** Fyers **Standard 90**; Upstox **full 1,800**; app cap 500. When exceeded: route by priority (live futures > index spot > option strikes), drop overflow by priority, mark `CAPACITY_LIMITED` — **never claim full live coverage.** The broader option-chain/contract tail is **REST on-demand** (both providers), not streamed.

---

## 6. Recommended architecture (summary)
Shared canonical identity (§5.1) + per-provider observations + **central quote coordinator** + **automatic subscription reconciliation from durable declarative intent** (§5.2) + **stable frontend canonical contract** + centralized, plan-aware capacity config (Standard-default, Prime/Plus override) + truthful per-kind/per-provider coverage reporting. Provider-specific protocols/identifiers stay inside `brokers/{fyers,upstox}/`.

---

## 7. Staged implementation plan (non-destructive rollback; no coding this round)

> **Rollback rule:** NEVER `git reset/clean/checkout/restore/stash/rebase`. Rollback = selective reversal of *only* the lines a stage adds, after safeguarding pre-existing in-flight edits. **No whole-file revert on a file with unrelated in-flight changes.** No new persistence field; no protected-startup-composition change.

| # | Stage | Exact module boundary | Deps | Data/schema | Risk | Non-destructive rollback |
|---|---|---|---|---|---|---|
| S1 | Commit `brokers/fyers/rest.py` (D1) | `git add` one untracked file | — | none | none | *only* `git reset --soft HEAD~1` safe here (commit holds just this file); no `--hard`/`clean` |
| S2 | **Plan-aware capacity model** (D8): centralized `providers.capacity` (Fyers Standard=100 default, Prime override; Upstox full=2000/Plus note); adapters read `registry.budget` | `providers/capacity.py` (new) + `fyers/capabilities.py` (read config) | S1 | config (additive) | medium | revert the added config file + the one-line change in `capabilities.py`; consumers fall back |
| S3 | **Central quote coordinator + validity + freshness** (D4,D5,D7) | new `providers` selection policy + `market/service.get_canonical_quote` + `fno_coverage._available` (add quote-age) | S2 | none | medium-high (lock-free O(1)) | revert the 3 modules; native-key reads still work |
| S4 | **Frontend + `preferences()` on canonical ID** (D5) | `subscriptions/service.py::preferences` (`.key`→canonical) + `IndicesWidget.tsx` + `types.ts` + `WatchlistTable.tsx` | S3 | none | medium (additive API) | selective hunk reversal per file (all in-flight) |
| S5 | **Auto-restore F&O at startup, NO new field** (D6) | `app/server.py` lifespan: `plan()`→`set_derivatives_universe()`→existing `reconcile()` (~3 lines at `:1220`) | S3 | **none** | low-medium (calls existing method; does not touch frozen auth/credential baseline) | revert added lifespan lines only |
| S6 | **Symmetric index-tick handling** (D2/D3) | keep Fyers HSM index drop **until** the §7 test passes; Upstox `indexFF` is the index path; the coordinator labels unvalidated HSM index as `untrusted`, never `live` | S3 | none | medium | revert (guard already present) |
| S7 | **Authenticated market-hours validation tests** (closes D3, D14, and all [R] cells) | test harness: Fyers REST index (`NSE:NIFTY50-INDEX`,`BSE:SENSEX-INDEX`) + Fyers HSM scaling + Upstox `full` field capture | S6 | none (evidence) | n/a | n/a |
| S8 | Catalogue sync for the empty active env (D9) | operational `POST /api/instruments/sync` (future approved step) | S1 | populates `instruments` (target env) | low (transactional) | idempotent; `data_test` **not** touched in this review |
| S9 | `migrate_from_config` Fyers index categorization (D12) | `subscriptions/service.py::migrate_from_config` | S4 | one-time (idempotent marker) | low | revert (marker safe) |
| S10 | `fno/view` storm regression test (D13) | add `frontend/src/__tests__/FnoWorkspaceView.test.tsx` | — | none (new test) | low | delete new test file |
| S11 | **Fyers OI-analytics gap (D11)** — document as Upstox-only OR implement | `market_data.py` (document) / new Fyers OI path | S3 | none | medium | document-only stage has no rollback |
| S12 | **Canonical re-key (Option A)** ⚠ deferred, protected-core | `market/service.py` write path → `(exchange, canonical_id)` | S3 proven | data migration | high | **separate protected-core approval; not in this gate** |

**Critical path (safe Fyers-only + Upstox-only + dual):** S1 → S2 → S3 → S4 → S5 → S6 → S7 (evidence) → S8 (sync). S11 documents a real capability asymmetry. S12 deferred.

**Acceptance tests (three configs, auto subscribe/fallback, all in-scope categories, spot/chain/heatmap, fields, stale/unavailable, budgets, clean-checkout, no storm):** mirror §4 matrix + S10 (`idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`) + clean-checkout (`no ModuleNotFoundError` for `rest.py`; `test_architecture_boundaries.py` green; `bun run build` OK).

---

## 8. Unresolved provider gaps (exact evidence/test needed)

1. **U1 — Fyers HSM index LTP scaling (D3).** Blocks Fyers *streaming* index only (REST path S6 does not depend on it). **Evidence [R]:** market-hours `if|nse_cm|Nifty 50` snapshot — raw `ltp`,`multiplier`,`precision`,`exch_feed_time` vs NSE published NIFTY, 3 snapshots, ≤0.05-pt. **Cannot close by code inspection.**
2. **U2 — Fyers REST index live-correctness.** **Evidence [R]:** `/data/quotes?symbols=NSE:NIFTY50-INDEX,BSE:SENSEX-INDEX` vs NSE/BSE published values at the same minute, ≤0.05-pt, record ts/source/budget. **Not claimed passed.**
3. **U3 — Upstox account tier / REST quotas.** R5 flagged; confirmed **Standard = 50 rps / 500 rmin / 2,000 per 30-min** [D]. Plus-tier (5 conn, `full_d30`) entitlement **not established for this account** → default to standard tier; verify against the connected account [R].
4. **U4 — Upstox `full`-mode field delivery (D14).** **Evidence [R]:** capture a `full`-mode frame; confirm `ltpc`,`marketLevel`(D5),`marketOHLC`,`oi`,`iv`,`optionGreeks`,`atp/vtt/tbq/tsq` actually populate for an in-scope contract; confirm previous-OI is *not* on the wire (persisted fallback).
5. **U5 — Fyers OI-analytics/IV/Greeks (D11).** Upstox-only today; decide whether a Fyers OI path is required (a genuine capability gap, not a bug). Maintainer decision.
6. **U6 — Active runtime catalog empty (D9).** Both providers; closed only by S8 (operational sync), not in read-only mode.
7. **U7 — both-providers-down / failover live proof.** No live capture; §4 dual-row fallback + both-down-`unavailable` must be proven [R] (kill one feed, confirm the other supplies; kill both, confirm truthful `unavailable`).

**Distinguish clearly (mandated §7):** [C]-proven defects (D1,D2,D4,D5,D6,D7,D8,D9,D10,D11,D12,D13), [T]-demonstrated paths (existing `test_upstox_feed/wiring`, `test_fno_coverage`, `test_fyers_integration`), [R]-required live tests (U1,U2,U4,U7), [A]-assumed (D14 OHLC semantic, Fyers BSE tick delivery). No live provider response was captured this round.

---

## 9. Final status

**NOT READY FOR MAINTAINER REVIEW — for *implementation coding*.**

The **design is complete, provider-balanced, and internally consistent** and is ready for review; coding is blocked on:
- **U7/U3/U4 live evidence:** an authenticated market-hours session is required to prove the dual-provider matrix (§4), Upstox `full`-mode field delivery (D14), and Fyers REST index correctness (U2). This is the dominant gate and is *not* claimable from code inspection.
- **U5 decision:** whether the Fyers OI-analytics/IV/Greeks gap (D11, an Upstox-only capability today) is acceptable for Fyers-only mode or needs a Fyers OI path.
- **Scope sign-off (§1):** confirm the fixed scope (NSE stock F&O + 6 index-F&O + SENSEX F&O; BANKEX/SENSEX-50/BSE single-stock/MCX/new-vendors out).
- **G-deferral:** canonical re-key (S12) as protected-core; Standard-default/Prime-override capacity model (S2).

**No code written; no source/test/config/DB/migration/lockfile/build file modified; no catalogue synced; no mutating API called; no destructive git operation; all in-flight work preserved.** Stopped here — coding remains prohibited until the maintainer reviews this plan (including the live-test evidence U1–U7) and explicitly authorizes implementation of S1–S11 (S12 as a separate protected-core task).
