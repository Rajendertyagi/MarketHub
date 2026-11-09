# MarketHub — Round 7: Official API Study, Provider Parity & Final Implementation Audit

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints, no `git reset/clean/checkout/restore/stash/rebase`. All in-flight work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Date:** 2026-10-18
**Repo state (§10):** 208 staged-new + 29 modified + 268 untracked. In-flight provider files: `brokers/{fyers,upstox}/capabilities.py` (AM), `brokers/upstox/feed.py|feed_processing.py|proto` (M), `brokers/fyers/auth.py` (M) + `rest.py` (untracked), `market/normalize/{upstox,fyers}.py` (M), `market/service.py` (M), `app/{fno_coverage,subscriptions,market_indices,market_data}.py`, `providers/*` (new).

**Evidence classes:** **[C]** code read from the active tree · **[T]** an existing test actually executed · **[D]** official docs (URL + date) · **[R]** authenticated live response (**none captured this round — no live claim is made**) · **[A]** reasoned assumption.

**Two headline corrections this round (both flagged by the maintainer, both confirmed):**
1. **The capacity arithmetic was mis-stated in prior rounds.** The real `effective_budget` formula yields **50 / 4,500 / 1,800**, *not* 90 / 4,450. Details §4.
2. **Upstox V3 Full Market Quotes supplies `previous_oi` — and MarketHub's REST quote normalizer does NOT consume it.** The persisted-previous-OI fallback for Upstox is avoidable for the full-quote path. Details §3/§5 (finding N1).

---

## 1. Official API facts (verified, URL + date checked 2026-10-18)

### 1.1 FYERS [D]
| Fact | Value | Source |
|---|---|---|
| Data-socket symbols | **Standard = 100**, **Prime = 5,000** | support.fyers.in "rate-limits for Standard and Prime plans" (pub. 2026-10-05) |
| Quotes symbols/request | **50 (Standard)**, 1,000 (Prime) | same |
| Data-REST rate (Standard) | **5 rps / 50 rmin / 5,000 per day** | same |
| Data-REST rate (Prime) | 10 rps / 500 rmin / 500,000/day | same |
| TBT socket | 1 sym (Std) / 15 (Prime) | same |
| **OI in quotes** | **NOT in `/data/quotes` response** — OI delivered via **Market Depth** (`/data/depth`: `oi`, `pdoi`=previous-day OI, `oipercent`=OI-change%) | "market-depth API" KB + "quotes API" KB |
| HSM binary socket | **undocumented in the public API KB** (only the JSON *data-websocket* is documented); HSM index scaling is **not** documented | "data-websocket" KB; repo comment `feed.py:937` |
| Plan detection | **No documented profile field** exposes Standard/Prime | `/api/v3/profile` returns name/email/display_name only |

### 1.2 Upstox [D]
| Fact | Value | Source |
|---|---|---|
| V3 WS mode caps | **ltpc 5000 / option_greeks 3000 / full 2000 / full_d30 50** (individual); combined 2000/2000/1500 when multi-category; 2 conn (5 w/ Plus) | upstox.com V3 Market Data Feed |
| **Full Market Quotes V3** | **`/v3/market-quote/quotes`** ≤500 instruments; response adds **`prev_close_price`, `year_high/low`, and — for F&O — `previous_oi`**, plus `ohlc.volume/ts` and live CAS fields | "Full Market Quotes V3 API" |
| **LTP V3** | `/v3/market-quote/ltp` | OpenAPI `MarketQuoteV3Api` |
| **Option Greeks V3** | `/v3/market-quote/greeks` (≤500) — note repo uses `/v3/market-quote/option-greek` [C]; confirm exact path [A] | "Option Greek" / OpenAPI |
| **Option chain** | `/v2/option/chain` — response `market_data` **includes `prev_oi`** and `option_greeks` (vega/theta/gamma/delta/**iv**/**pop**) | "Put/Call Option Chain" |
| **OI / Change-OI** | `/v2/market/oi`, `/v2/market/change-oi` (per-strike call/put OI; spot close) | "Get OI" |
| **History V3** | `/v3/historical-candle` (day/week/month) + `/v3/intra-day-candle` (**1-minute & 30-minute only**) | "Historical Candle V3" / "Intraday Candle V3" |
| **REST rate limits** | **per API + per user:** Standard APIs **50 rps / 500 rmin / 2,000 per 30-min**; order-placement 10 rps (or 50 SEBI-registered); 429 + `UDAPI10005` | "Rate Limits" (circular 5-May-2025) |
| Access-token lifecycle | daily; token valid ~24 h, refreshed via auth-code | "Access-token request" |

**V2 vs V3 (mandated §4):** Upstox runs **both**: V2 endpoints for option-chain, OI, change-OI, max-pain, PCR, news; V3 for full-market-quotes, option-greeks, historical-candle, intra-day. V3 full-market-quotes is a *superset* of V2 `market-quote/quotes` (adds `previous_oi`, CAS, year high/low) **but does not replace** the V2 option-chain/OI endpoints (those remain V2). See N1.

---

## 2. Provider capability & endpoint matrix (exact F&O scope)

Legend: **L** live feed · **R** documented REST · **S** specialized endpoint · **P** persisted/cached only · **X** unsupported/unverified · **[✓C]** wired in code · **[gap]** declared-not-wired or absent.

| Field / capability | **FYERS** | **UPSTOX** |
|---|---|---|
| LTP + prev close + change | L (HSM `sf|`) + R (`/data/quotes`) ✓C | L (`ltpc`) + R (`/v3/market-quote/quotes`) ✓C |
| OHLC + volume + ts | L + R (`/data/history`) ✓C | L (`marketOHLC` interval candles) + R V3 (`/v3/historical-candle`,`intra-day`) ✓C — **per-tick OHLC is interval candles, not tick** [A] |
| Open interest (current) | L (HSM `OI`) + **R via Depth `/data/depth`** ✓C (`fyers.py:89,258`) | L (`full.oi`) + R/S (`/v2/market/oi`) ✓C |
| **Previous OI** | **R via Depth only** (`pdoi`) ✓C (`fyers.py:265`) | **V3 Full-Quote `previous_oi` — NOT consumed by `quote_from_rest` [gap N1]**; option-chain `prev_oi` ✓C (`upstox.py:692`); persisted fallback otherwise |
| OI change / OI-change % | **R via Depth** (`oipercent`; derived `oi_change`) ✓C (`fyers.py:263,277`) | **R/S** (`/v2/market/change-oi`) ✓C |
| Greeks + IV | **X** — not in `fyers/capabilities.py`; option-chain has no greeks/iv [gap] | **L** (`full.optionGreeks`,`iv`) + **R/S** (`/v3/market-quote/greeks`, option-chain `option_greeks`) ✓C |
| Bid/ask + depth (D5/D30) | L (HSM depth tolerated) + **R `/data/depth`** ✓C | L (`marketLevel` D5) + R (V3 quotes `depth`) ✓C |
| Option chain | **R** (`/data/options-chain-v3`) — in **untracked** `rest.py` [gap D1] | **R/S** (`/v2/option/chain`, returns last-session even closed) ✓C |
| Historical candles | **R** (`/data/history`) [gap D1 untracked] | **R V3** (day/week/month + 1/30-min intraday) ✓C |
| Market status | **R** (`/data/marketStatus`) [gap D1] | **R** (`/v2/market/status`,`/timings`,`/holidays`) ✓C |
| OI analytics (max-pain/PCR) | **X — not implemented** (`market_data.py:235-289` raise "fyers not yet implemented") [gap D11] | **R/S** (`/v2/market/max-pain`,`/pcr`,`/oi`) ✓C |
| Auth/401 | typed `FyersAuthError` ✓C | typed `UpstoxAuthError` ✓C |
| 429 / retry-after | `FyersRateLimitError` ✓C | `UpstoxRateLimitError` + `_parse_retry_after` ✓C |

**Parity statement (mandated §11.9):** **Do NOT claim complete provider parity.** Real asymmetries: (a) **Fyers OI-analytics (max-pain/PCR), Greeks and IV are absent** — a genuine *broker* limitation (Fyers exposes no greeks/IV endpoint), not just a missing integration → in Fyers-only mode those features are **unavailable** and must say so. (b) **Fyers OI/prev-OI/OI% come from Depth, not Quotes** — the code is correct (`fyers.py:89,258,263,265`), confirmed against the docs. (c) **Upstox previous-OI is available via V3 full-quote but the normalizer drops it** (N1) — a fixable gap, not a broker limit.

---

## 3. Code-level findings (both providers; verified / partial / unverified / refuted)

| # | Finding | Status | Evidence |
|---|---|---|---|
| N1 | **Upstox V3 Full-Quote `previous_oi` not consumed by `quote_from_rest`.** `market/normalize/upstox.py:139-145` maps `oi→open_interest` but **not** `previous_oi→previous_oi`; the only `prev_oi` consumption is the option-chain path (`upstox.py:692`). Consequence: a *spot/full-quote* for an F&O instrument shows **`previous_oi=None`**, forcing the *persisted* previous-session fallback (per `upstox/capabilities.py:4-6`), even though the REST endpoint already returns it. | **Verified [C + D]** | `upstox.py:139-145` vs V3 docs `previous_oi` field |
| N2 | **Capacity formula mis-stated in prior rounds.** `effective_budget = cap − max(10%×cap, 50)` → **Fyers Standard: 50** (reserve = max(10,50)=50), **Fyers Prime: 4,500**, **Upstox full: 1,800**. Prior rounds reported 90/4,450 — **refuted**. | **Verified [C]** | `providers/capabilities.py:44-51`; computed: `eb(100)=50, eb(5000)=4500, eb(2000)=1800, eb(500)=450` |
| N3 | **Fyers capability hardcodes Prime (5000).** `brokers/fyers/capabilities.py` `MODE_KEY_LIMITS={"full":5000}` → budget 4,500. Default account is **Standard (100 → budget 50)**. The app cap `MAX_INSTRUMENTS_PER_PROVIDER=500` **exceeds** the Standard broker cap. | **Verified [C vs D]** | `fyers/capabilities.py`; Standard=100 [D] |
| N4 | `brokers/fyers/rest.py` untracked; imported at `app/server.py:695` | **Verified [C]** | `git ls-files` no-match; module-scope import |
| N5 | Fyers HSM index ticks dropped (`if`-prefix guard) | **Verified [C]** | `fyers/feed.py:937-941` |
| N6 | Fyers HSM index scaling unverified (captured ×3.65 error) | **Verified-unverified [C + captured]** | `fyers/feed.py:937-942`; **HSM is undocumented in public Fyers API docs [D]** → cannot be proved without a live capture |
| N7 | Quote store keyed on native token; canonical computed but not the key | **Verified [C]** | `market/service.py:207-210,251` |
| N8 | `preferences().key` = `upstox_key`; frontend joins on it | **Verified [C]** | `subscriptions/service.py:231`; `IndicesWidget.tsx` |
| N9 | F&O universe not auto-restored at startup | **Verified [C]** | `_derivatives_universe` in-memory (`service.py:92`); manual `POST /api/fno/apply` (`fno_coverage_routes.py:112`) |
| N10 | Routing state-only, no quote freshness | **Verified [C]** | `fno_coverage._available:210-215` |
| N11 | Fyers OI-analytics/Greeks/IV absent (Upstox-only today) | **Verified [C]** | `market_data.py:235-289` "fyers not yet implemented" |
| N12 | Active runtime catalog empty (both providers) | **Verified [C]** | `config.json data_dir:"data_test"`; `data_test/events.db` instruments=0 |
| N13 | `migrate_from_config` mis-categorizes Fyers index keys as `stock` | **Verified [C]** | `service.py:187`; `data_test` rows |
| N14 | `fno/view` storm mitigation present; regression test **absent** | **Partially verified [C]** | `FnoWorkspaceView.tsx:38,67` + `useFno.ts:116` (stable hook) present; **`FnoWorkspaceView.test.tsx` does not exist** |
| N15 | Upstox `full`-mode fields actually delivered (OI, IV, greeks) | **Unverified [A]** — requires live capture | `feed_processing.py` + proto; **no [R] evidence this round** |

---

## 4. Correct capacity arithmetic + endpoint-specific rate policy

### 4.1 The single authoritative formula (verified against `providers/capabilities.py:48-51`)
```
effective_budget(cap) = cap - max(int(cap*0.10), 50)
```
| Capacity | Reserve | **Effective budget** |
|---|---|---|
| Fyers **Standard** (100) | 50 | **50** |
| Fyers **Prime** (5,000) | 500 | **4,500** |
| Upstox **full** (2,000) | 200 | **1,800** |
| Upstox ltpc (5,000) | 500 | 4,500 |
| App cap (`MAX_INSTRUMENTS_PER_PROVIDER`) | — | 500 (applied **min() after** provider budget) |

**Final binding streaming budget = `min(effective_budget(provider_cap), 500)`:**
- **Fyers Standard → 50** (app cap is not the limiter; the *broker* cap is).
- **Fyers Prime → 500** (app cap binds below 4,500).
- **Upstox full → 500** (app cap binds below 1,800).

**Reconciled, no conflicting numbers remain:** use **50 / 4,500 / 1,800** everywhere (code, config, docs, tests). Prior "90 / 4,450" figures are **withdrawn** (N2). The app's 500 cap is applied *explicitly and separately* as a `min()` — not folded into the reserve.

**Tests to pin (future S2):** `effective_budget(100)==50`, `(5000)==4500`, `(2000)==1800`, `(500)==450`; and the *final* policy `min(effective_budget, 500)` → Fyers-Standard=50, Fyers-Prime=500, Upstox=500. **A 100-slot Standard Fyers account can stream only ~50 instruments; the requirement's auto-subscription must scope the live stream to that and push the rest to REST (§8).**

### 4.2 Endpoint-specific rate control (do NOT use one universal number)
- **Fyers Data-REST (Standard):** shared pool **5 rps / 50 rmin / 5,000/day** across `/data/quotes`,`/data/depth`,`/data/history`,`/data/options-chain-v3`,`/data/marketStatus`. One token-bucket at ≤5 rps; quotes batched ≤50/request; 429 → backoff. [D]
- **Upstox REST:** **per-API + per-user** Standard **50 rps / 500 rmin / 2,000 per 30-min** [D]. MarketHub calls (from `market_data.py`): `/v3/market-quote/quotes`, `/v3/market-quote/greeks`, `/v3/historical-candle`,`intra-day`, `/v2/option/chain`, `/v2/market/{oi,change-oi,max-pain,pcr}`, `/v2/news`. **Each endpoint has its own envelope; the 2,000/30-min is the Standard-API aggregate, not a per-endpoint figure.** Add a **separate conservative app-wide throttle** on top of the broker limit (distinct, documented, not conflated with the broker quota).

---

## 5. Canonical identity + provider observations + selected-quote architecture

- **One canonical ID per real instrument/contract** (`app/market_identity.py`): Fyers `NSE:NIFTY50-INDEX` ≡ Upstox `NSE_INDEX|Nifty 50` ≡ `NSE:INDEX:NIFTY`; `BSE:SENSEX-INDEX` ≡ `BSE_INDEX|SENSEX` ≡ `BSE:INDEX:SENSEX`. F&O contracts: `{EXCH}:FUTURE:{und}:{expiry}`, `{EXCH}:OPTION:{und}:{expiry}:{strike}:{CE|PE}`.
- **Three distinct artefacts (never conflated):**
  1. **Provider-native observations** — each adapter writes its last *validated* observation, keyed by canonical ID, carrying `provider`, `provider_token` (native), `source` (ws/rest), `received_ts`/`exchange_ts`, `provenance`. **Adapters do not discard their own ticks for cross-provider selection** (the one exception — the Fyers HSM index guard, N5 — is justified by *data validity* (N6), not provider preference, and stays until a live capture proves scaling).
  2. **Canonical instrument identity** — the single real-instrument key.
  3. **Selected canonical quote** — exposed to consumers, chosen by the **central coordinator**: `enabled∧authenticated` ∧ `validity` ∧ `freshness` ∧ `entitlement/plan-cap`, **preference order only as a tie-breaker** (N10 fix; no hidden mandatory provider).
- **Selected quote carries:** value, `source` (ws/rest), `provider`, `provider_token`, `received_ts`, `basis` (`live`/`stale`/`unavailable`), `validity` (`validated`/`rejected`). **Stale or rejected is never `live`.**
- **Central projection, not a destructive re-key:** the coordinator can be built **additively** (new `get_canonical_quote` over the existing store; native-keyed observations retained) so **no immediate storage re-key or data migration is required.** A true write-path re-key of `MarketService._quotes` to `(exchange, canonical_id)` is a **separate protected-core decision** (S-last) — recommended *after* the additive coordinator is proven, not now.
- **All API + frontend consumers** join on the **canonical ID**; provider-native keys become diagnostics (removes N8 across *every* consumer, not just `IndicesWidget`).

---

## 6. Automatic subscription restoration + fallback (no manual step)

- **Single source of truth = recomputed, not stored:** `desired = resolve( fno_config[declarative intent] × catalogue × active_views × enabled∧authenticated providers × plan caps (§4.1) )`. `fno_config` **already persists declarative intent** (`index_labels`, `stock_mode`/`stock_symbols`, `expiry_policy`, `preferred/fallback_provider`) and states the universe is "always resolved dynamically" (`fno_config.py:5-7`).
- **Why the in-memory derivatives owner is not restored today (N9):** `set_derivatives_universe` is only called by the **manual** `POST /api/fno/apply`; the lifespan `reconcile` (`server.py:1220`) runs but the owner is empty at boot. **Fix = recompute at startup** (`plan()` → `set_derivatives_universe(plan.keys_by_provider)` → existing `reconcile()`) — **no new persistence, no protected-startup change**, reusing durable intent.
- **Automatic triggers:** startup restore, active-view change, provider reconnect/failover, expiry rollover (`_choose_expiry` filters `>= today`), catalogue epoch bump, Fyers-only⇄Upstox-only⇄dual transition.
- **Capacity exceeded:** route by priority (live futures > index spot > option strikes), drop overflow by priority, mark `CAPACITY_LIMITED`; the rest is **REST on-demand**. **Never report the whole universe as live when only a subset streams.**
- **Normal use requires no manual provider selection, no per-instrument mapping, and no manual F&O apply.**

---

## 7. Coverage + acceptance tests (all three configurations)

> Each row states the **code path** + evidence class. **No [R] live success is claimed** — [R] rows are *the* tests to run with authenticated market access.

| Scenario | Fyers-only | Upstox-only | Dual | Evidence |
|---|---|---|---|---|
| Index spot (6 NSE + SENSEX) | HSM index untrusted (N6) → **REST `/data/quotes` refresh [R]** | `indexFF` live [C+T] → **[R] confirm** | freshest validated (coordinator) | [R] |
| NSE stock F&O futures/options | HSM `sf|` [C] | `full` ltpc/oi/greeks/iv [C] → [R] | converge canonical | [C+T / R] |
| Major NSE index futures/options | HSM futures [C] + **options REST (N4-blocked)** | live + chain REST [C] | routed | [R] |
| BSE SENSEX futures/options | [A — verify] + options REST | `full` + SENSEX chain REST [C] | Upstox authoritative for BSE options [C counts] | [R] |
| Option-chain + ATM spot | **blocked on N4 commit + [R]** | works [C+T] | converges | [R] |
| Futures heatmap / OI analytics | heatmap ✓; **OI-analytics UNAVAILABLE (N11, broker limit)** | full [C] | Upstox analytics, Fyers OI ticks | [C / R] |
| One provider stale/disconnected | — | — | coordinator falls back; **stale provider never `live`** | [R] |
| **Both providers unavailable** | `unavailable` (truthful) | `unavailable` | **both-down → `unavailable`, never fabricated `live`** | [R] |
| Unsupported field/instrument | `unavailable`/`UNCOVERED` + reason | same | same | [C] |
| Data via REST not WS | Fyers index/chain | Upstox prev-oi (N1) | — | [R] |
| Provider capacity exceeded | Fyers-Standard **50** (N2) | app 500 | `CAPACITY_LIMITED` overflow | [C + S2 tests] |
| Auth expired | `auth_required`, truthful | `auth_required` | symmetric | [C] |
| Startup / catalog unavailable | F&O `desired:0` until sync (N12) | same | same | [C] |
| No request storm | `FnoWorkspaceView` idle counts (N14 — **add regression test, S-future**) | — | — | [C present; test missing] |

---

## 8. Fyers-only index data without assuming Upstox

- **Keep HSM index untrusted (N6)** until a live market-hours capture proves scaling; the HSM socket is **undocumented in public Fyers API docs [D]**, so it cannot be validated from docs.
- **REST index path (valid, provider-bounded):** Fyers `/data/quotes` batched (≤50/request) for the 7 in-scope index symbols (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI + SENSEX). **Bounded refresh** (60 s, inside the 5-min `STALE_MINUTES`), **market-hours-aware** (off-hours → serve last close as `stale`, don't poll), **dedup** (coalesce same-symbol within window), **shared per-endpoint token bucket** (≤5 rps; index + F&O chain + history share the 5,000/day pool). Budget: 1 batched call/60 s ≈ 1,440/day + bounded chain polling ≈ 150–300/day → **~1,600–1,750/day < 5,000**, far under 5 rps. **Feasible on Standard for the *index* scope** (index quotes do not consume the 50-slot data-socket budget — they're REST, not WS).
- **Live-correctness is NOT claimed** (§11: U2 requires one authenticated spot check: `/data/quotes?symbols=NSE:NIFTY50-INDEX,BSE:SENSEX-INDEX` vs NSE/BSE published values, ≤0.05-pt, record ts/source/budget).
- **Same index-spot contract** is fulfilled by **Upstox `indexFF` in Upstox-only** and by **the freshest validated source in dual** — **Upstox is never a hidden mandatory source**; the coordinator picks by availability×freshness×validity×entitlement, preference order only as tie-break.

---

## 9. Staged implementation plan (non-destructive rollback; no coding this round)

> **Rollback rule (mandated §10/§11.8):** NEVER `git reset/clean/checkout/restore/stash/rebase`. Rollback = selective reversal of *only* the lines a stage adds, after safeguarding pre-existing in-flight edits. **No whole-file revert on a file with unrelated in-flight changes.** No new persistence field; no protected-startup-composition change.

| # | Stage | Exact module boundary | Deps | Data/schema | Risk | Non-destructive rollback |
|---|---|---|---|---|---|---|
| S1 | Commit `brokers/fyers/rest.py` (N4) | `git add` one untracked file | — | none | none (additive; already imported) | *only* `git reset --soft HEAD~1` safe here (commit = just this file); no `--hard`/`clean` |
| S2 | **Correct capacity model (N2/N3)** | `providers/capacity.py` (new): Fyers **Standard=100 default, Prime=5000 explicit override**; adapters read `registry.budget`; fix `fyers/capabilities.py` hardcoded 5000; add **mathematical tests** pinning `eb(100)=50,eb(5000)=4500,eb(2000)=1800` + final `min(eb,500)` | S1 | config (additive) | medium | revert the new config file + the one `capabilities.py` line + tests; consumers fall back |
| S3 | **Consume Upstox V3 `previous_oi` (N1)** | `market/normalize/upstox.py::quote_from_rest` add `previous_oi→previous_oi` (+ derived `oi_change`), matching the option-chain path | — | none | low (additive normalizer field) | revert the added `set_reported` lines |
| S4 | **Central quote coordinator + validity + freshness (N7,N8,N10)** | new `providers` selection policy + `market/service.get_canonical_quote` (additive) + `fno_coverage._available` (add quote-age) + endpoint-specific rate buckets | S2,S3 | none | medium-high (lock-free O(1)) | revert the 3 modules; native-key reads still work |
| S5 | **Frontend + `preferences()` on canonical ID (N8)** | `subscriptions/service.py::preferences` (`.key`→canonical, keep native as provenance) + `IndicesWidget.tsx` + `types.ts` + `WatchlistTable.tsx` | S4 | none | medium (additive API shape) | selective hunk reversal **per file** (all in-flight) |
| S6 | **Auto-restore F&O at startup, NO new field (N9)** | `app/server.py` lifespan: `plan()`→`set_derivatives_universe()`→existing `reconcile()` (~3 lines at `:1220`) | S4 | **none** | low-medium (calls an existing method; does not touch frozen auth/credential/session baseline) | revert added lifespan lines only |
| S7 | **Fyers-only index via REST (§8)** | `fyers/rest.py::quotes` (exists) + coordinator index source + bounded market-hours poller (≤5 rps, 60 s, shared bucket) | S4 | none | medium | revert poller + index-source lines; HSM guard (N5) untouched |
| S8 | **Market-hours validation tests (U1–U4)** | harness: Fyers REST index, Fyers HSM scaling, Upstox `full` field capture, both-down failover | S7 | none (evidence) | n/a | n/a — **do not claim passed** |
| S9 | Catalogue sync for the empty active env (N12) | operational `POST /api/instruments/sync` (future approved step) | S1 | populates `instruments` (target env) | low (transactional) | idempotent; `data_test` **not** touched in this review |
| S10 | `migrate_from_config` Fyers index categorization (N13) | `subscriptions/service.py::migrate_from_config` | S5 | one-time (idempotent marker) | low | revert (marker safe) |
| S11 | `fno/view` storm regression test (N14) | add `frontend/src/__tests__/FnoWorkspaceView.test.tsx` | — | none (new test) | low | delete new test file |
| S12 | Fyers OI-analytics/Greeks/IV gap (N11) | **document as broker limitation** (Fyers-only: features unavailable) OR implement a Fyers OI path — maintainer decision | S4 | none | medium | document-only stage: no rollback |
| S13 | **Canonical write-path re-key (Option A)** ⚠ *protected-core, deferred* | `market/service.py` `_quotes` → `(exchange, canonical_id)` | S4 proven | data migration | high | **separate protected-core approval; not in this gate** |

**Critical path (safe all-three-config operation):** S1 → S2 (correct caps) → S3 (prev-oi) → S4 (coordinator) → S5 (canonical join) → S6 (auto-restore, no new field) → S7 (Fyers REST index) → S8 (live evidence) → S9 (sync). S12 documents the genuine Fyers gap. S13 deferred.

---

## 10. Repository preservation

Recorded pre-inspection: branch `fno-provider-agnostic`, HEAD `3f4b8d9`, 208 staged-new / 29 modified / 268 untracked. **No** files staged/committed/altered; no `git` mutator run; no catalogue synced; no DB/config/build file touched; no mutating API called. The only new artefact this round is this report file (untracked).

---

## 11. Unresolved critical uncertainties (evidence required to close)

1. **U1 — Fyers HSM index LTP scaling (N6).** HSM is undocumented in public Fyers docs [D]; blocks Fyers *streaming* index only (not the REST path S7). **Evidence [R]:** market-hours `if|nse_cm|Nifty 50` snapshot — raw `ltp`,`multiplier`,`precision`,`exch_feed_time` vs NSE published NIFTY, 3 snapshots, ≤0.05-pt. **Cannot close by inspection.**
2. **U2 — Fyers REST index live-correctness (§8).** **Evidence [R]:** `/data/quotes?symbols=NSE:NIFTY50-INDEX,BSE:SENSEX-INDEX` vs NSE/BSE published values, same minute, ≤0.05-pt, record ts/source/budget. **Not claimed passed.**
3. **U3 — Upstox account tier / per-endpoint quotas.** Standard = 50 rps/500 rmin/2,000 per 30-min [D]; Plus (5 conn, `full_d30`50) entitlement **not established for this account** → default to Standard; confirm against the connected account [R].
4. **U4 — Upstox `full`-mode live field delivery (N15, N14-OHLC).** **Evidence [R]:** capture a `full`-mode frame; confirm `ltpc`,`marketLevel`(D5),`marketOHLC`,`oi`,`iv`,`optionGreeks`,`atp/vtt/tbq/tsq` populate for an in-scope contract; confirm **previous-OI is NOT on the `full` WS wire** (it *is* on V3 REST full-quote + option-chain — N1).
5. **U5 — Fyers OI-analytics/Greeks/IV (N11).** Genuinely absent from Fyers (broker limitation, not just integration). **Maintainer decision:** acceptable as Fyers-only-unavailable, or build a Fyers OI path.
6. **U6 — Active runtime catalog empty (N12).** Both providers; closed only by S9 (operational sync), not in read-only mode.
7. **U7 — G1 fork + S13 deferral.** Confirm the additive central-coordinator projection (S4) is the interim path and the canonical write-path re-key (S13) is deferred as a **protected-core** task.

**Distinguish clearly (mandated §9):** [C]-proven defects (N1,N2,N3,N4,N5,N7,N8,N9,N10,N11,N12,N13,N14), [T]-demonstrated paths (existing `test_upstox_feed/wiring`, `test_fno_coverage`, `test_fyers_integration` — present but *not re-executed* in this read-only round), [D]-verified official facts (§1), [R]-required live tests (U1–U4,U7-scenario), [A]-assumed (N15 full-mode field population, Fyers BSE tick delivery). **No live provider response was captured; none is invented.**

---

## 12. Final status

**NOT READY FOR MAINTAINER REVIEW — for *implementation coding*.**

The **design is complete, provider-balanced, and internally consistent**, with two material corrections this round: **(1)** the capacity formula is **50 / 4,500 / 1,800** (prior 90/4,450 withdrawn — N2) with the 500 app cap applied as an explicit `min()`; **(2)** **Upstox V3 Full-Quote already returns `previous_oi` but the normalizer drops it (N1)** — a small, contained fix that removes a needless persisted-OI fallback. The genuine **Fyers capability gap** (OI-analytics/Greeks/IV absent — N11) is now classified as a *broker* limitation, not a missing integration, and must be surfaced as "unavailable" in Fyers-only mode.

Coding is blocked on:
- **U4 + U7 live evidence** (dominant gate): an authenticated market-hours session to prove Upstox `full`-mode field delivery (incl. prev-OI-on-wire vs REST), Fyers REST index correctness (U2), and dual-provider failover / both-down-`unavailable`.
- **U1** (Fyers HSM scaling — non-blocking for the REST index path).
- **U3** (Upstox account tier; default Standard until verified).
- **U5 decision** (accept Fyers OI-analytics/IV as unavailable, or build a Fyers OI path).
- **U7 sign-off** (additive coordinator now; canonical re-key S13 as protected-core).
- **Scope confirmation** (§1 fixed scope: NSE stock F&O + 6 index-F&O + SENSEX F&O; BANKEX/SENSEX-50/BSE single-stock/MCX/new-vendors out).

**No code written; no source/test/config/DB/migration/lockfile/build file modified; no catalogue synced; no mutating API; no destructive git; all in-flight work preserved.** Stopped — coding remains prohibited until the maintainer reviews this plan (including the live-test evidence U1–U7) and explicitly authorizes implementation of S1–S12 (S13 as a separate protected-core task).
