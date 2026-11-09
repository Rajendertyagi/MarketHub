# MarketHub — Round 3: Final Pre-Implementation Gate (READ-ONLY)

**Mode:** READ-ONLY review. No source/test/config/DB/migration/lockfile/build files modified. No `git add`/commit/checkout/reset/clean/stash/rebase. No catalogue sync, no mutating endpoints, no credential writes. All in-flight staged/unstaged/untracked work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Observation date:** 2026-10-18
**Runtime DB for THIS tree:** `config.json` → `"data_dir": "data_test"` ⇒ the running server opens `data_test/events.db` (**instruments = 0 rows**). The populated `data/events.db` (142,932 rows) belongs to a different environment and is **not** the active runtime here. This distinction (flagged as U4 in Round 2) is now settled by reading `config.json` directly.

---

## 1. Corrected findings (each marked verified / partially verified / unverified / refuted)

### 1.1 Fyers capacity model — **REFUTED (Round 2 was wrong)**
- **Round 2 claim:** "Fyers 5,000 data-socket symbols, 4,450 effective budget." **REFUTED.**
- **Correct official model** (https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans, published 2026-10-05, checked 2026-10-18):
  | Socket | Standard (default plan) | Prime |
  |---|---|---|
  | **Data socket symbols** | **100** | **5,000** |
  | TBT socket | 1 symbol | 15 symbols |
- Standard is the default; Prime is a paid subscription (~₹590/mo, launched Jul 2026) that unlocks 5,000. **An account is Standard unless it explicitly subscribes to Prime.**
- The code declares `brokers/fyers/capabilities.py` `MODE_KEY_LIMITS={"full":5000}` → `effective_budget(5000)=4450`. **This silently assumes Prime.** For a Standard account the true ceiling is **`effective_budget(100)=90`**, i.e. the application's own `MAX_INSTRUMENTS_PER_PROVIDER=500` cap already **exceeds** the Standard broker cap. **This is a real defect, not a theoretical one.**
- **Status:** verified via official docs; the code assumption is **refuted** against current docs.

### 1.2 Fyers plan (Standard/Prime) detection — **UNVERIFIED (no supported API field)**
- `GET /api/v3/profile` returns only `name/image/display_name/email_id` — **no plan/entitlement field is documented** in the official profile response. There is **no supported, documented interface to reliably detect Prime**.
- Implication: MarketHub **cannot auto-detect** whether the connected account has 100 or 5,000 slots. **Status: unverified → requires a design decision (see §3, §6).**

### 1.3 Upstox capacity model — **verified correct in code**
- `brokers/upstox/capabilities.py` `MODE_KEY_LIMITS={"ltpc":5000,"option_greeks":3000,"full":2000,"full_d30":50}`, `DEFAULT_MODE="full"` → capacity 2000, `effective_budget(2000)=1800`. Matches the official Upstox V3 docs (checked 2026-10-18): individual caps ltpc 5000 / option_greeks 3000 / full 2000 / full_d30 50 (Plus); combined caps 2000/2000/1500 when multiple categories share a connection.
- **The application cap `MAX_INSTRUMENTS_PER_PROVIDER=500` is the binding constraint for Upstox, not the broker cap.**

### 1.4 Fyers HSM index-tick price scaling — **UNVERIFIED (cannot be proven from code)**
- `brokers/fyers/feed.py:942` applies `scale=(10**precision)*multiplier` to `_INDEX_VAL` fields. The in-code comment (`:937-939`) records a *captured* NIFTY index snapshot with `multiplier=28 / precision=0 → 87,205.71` vs the true NIFTY (~23,897) — a **×3.65 error**. That captured evidence **contradicts** the scaling being correct.
- **Status: unverified.** A live market-hours HSM capture is required (§4). Until then the guard at `feed.py:937-941` (drop all `if|` index ticks) is the **safe** behavior and must NOT be removed.

### 1.5 Index universe coverage — **REFUTED as stated, corrected**
- Round 2's "268 NSE + 149 BSE index symbols" were **broker-catalogue row counts**, and were (incorrectly) treated as proof of complete exchange coverage.
- **Correct framing (checked 2026-10-18):**
  - **Official NSE universe:** NSE Indices owns **over 436 Nifty indices** (as of 31 Jul 2026; https://www.nseindia.com/static/nse-indices). **Only 139 are live-streamed** in NSE's own live feed (NSE homepage / live index watch). The remaining ~297 are factsheet-only or derivative-underlying indices, not necessarily real-time-streamed.
  - **Official BSE universe:** BSE publishes a much larger family (Broad, Sector/Industry, Thematic, Strategy, Sustainability, Volatility, Government/Money-Market, Hybrid) — the live Index Watch alone lists ~30 equity indices (SENSEX, BANKEX, 100, 200, 500, 150 MIDCAP, 250 SMALLCAP, 250 LARGEMIDCAP, 400 MIDSMALLCAP, 1000, SENSEX 50, SENSEX NEXT 50, SENSEX NEXT 30, SENSEX SIXTY, Bharat 22, India 150, etc.) with more end-of-day. BSE has **no published single "total index count" equivalent to NSE's 436**; the SENSEX-related scope is defined explicitly in §2.
  - **Broker-catalogue subset:** Fyers 129 NSE_INDEX / 70 BSE_INDEX; Upstox 139 NSE_INDEX / 79 BSE_INDEX (from `data/events.db`, the populated env). These are **subsets** of the official universes, not the universes themselves.
- **Requirement target (re-stated):** complete discovery of **currently supported NSE indices** and the **intended BSE SENSEX-related scope** (SENSEX, SENSEX 50, SENSEX Next 50, SENSEX Next 30, SENSEX SIXTY, BANKEX, and their F&O where providers expose them). Full 436-index NSE real-time coverage is **NOT claimed** — it is not achievable through these two brokers and is not the requirement.

### 1.6 Manual F&O apply — **verified**
- `app/subscriptions/service.py:92` `_derivatives_universe` is an in-memory dict, "NEVER persisted"; `app/server.py` lifespan reconciles only the persistent owners. **No startup restore of the last-applied F&O plan.** Confirmed.

### 1.7 Provider-specific quote keys + UI join — **verified**
- `market/service.py:207-210` `get_quote_now` = raw `(exchange, instrument_token)` lookup, no identity resolver. `app/subscriptions/service.py:231` `preferences().indices[].key = entry["upstox_key"]`. `Quote.canonical_id` is computed in `_merge_quote` (`service.py:251-257`) but **not used as the storage key**. Confirmed.

### 1.8 Fyers index keys stored as `stock` in test DB — **verified**
- `data_test/events.db::md_subscriptions` has `('stock','NSE:NIFTY50-INDEX')` and `('stock','NSE:BANKNIFTY-INDEX')`. `migrate_from_config` (`service.py:173-219`) matches only `{e["upstox_key"]: e}` → Fyers colon keys fall through to `stock`. Confirmed.

### 1.9 No freshness-based fallback — **verified**
- `fno_coverage._available` (`:210-215`) checks only feed state ∈ `{streaming,connecting,reconnecting}` — no quote-age check. Confirmed.

### 1.10 Daily re-authentication — **verified against official docs**
- **Fyers:** daily OAuth (auth-code → access-token) **plus daily 2FA/PIN** for the day's session; token is single-day. **Upstox:** daily re-auth required for the V3 market-data feed; session tokens are not persistent across days. These are **provider authentication requirements**, distinct from (and not bypassable by) any manual broker/instrument *configuration* step. Neither broker exposes a "stay logged in forever" mode. **This is an unavoidable provider constraint, not a code defect** — see §6.

---

## 2. Official-universe → provider coverage matrix + canonical deduplication evidence

### 2.1 Universe hierarchy (do not conflate)
```
OFFICIAL NSE UNIVERSE (436 Nifty indices, NSE Indices Ltd, 31-Jul-2026)
  └─ NSE live-streamed subset (139, NSE live feed)
      ├─ Fyers NSE_INDEX (129 catalog rows)  → provider-native tokens "NSE:<SYMBOL>-INDEX"
      └─ Upstox NSE_INDEX (139 catalog rows) → provider-native keys "NSE_INDEX|<Name>"
OFFICIAL BSE UNIVERSE (SENSEX family + broader BSE index family)
  └─ BSE live Index Watch (~30 equity indices, incl. SENSEX, BANKEX, 100/200/500,
      150 MIDCAP, 250 SMALLCAP, 250 LARGEMIDCAP, 400 MIDSMALLCAP, 1000, SENSEX 50,
      SENSEX NEXT 50/30, SENSEX SIXTY, Bharat 22, India 150 …)
      ├─ Fyers BSE_INDEX (70 catalog rows)  → "BSE:<SYMBOL>-INDEX"
      └─ Upstox BSE_INDEX (79 catalog rows) → "BSE_INDEX|<Name>"
```
The **target coverage** = *currently supported NSE indices* (treatable as the NSE live-streamed 139, of which the F&O/option-chain underlyings are a smaller curated set) **∪** *intended BSE SENSEX-related scope*. The full 436-index NSE universe and the full BSE family are **explicitly out of scope** for guaranteed real-time coverage.

### 2.2 Canonical deduplication (evidence-based, NOT "different name ⇒ different instrument")
The catalog deliberately stores **one row per provider** (columns: `provider, instrument_token, exchange, tradingsymbol, name, instrument_type, segment, …`). Canonical identity is `app/market_identity.py`:
```
INDEX  {exchange}:INDEX:{normalized symbol}
EQUITY {exchange}:EQUITY:{ISIN | normalized symbol}
FUTURE {exchange}:FUTURE:{underlying}:{expiry}
OPTION {exchange}:OPTION:{underlying}:{expiry}:{strike}:{CE|PE}
```
Evidence that deduplication works by **normalized symbol**, not raw token:
- Fyers `NSE:NIFTY50-INDEX` and Upstox `NSE_INDEX|Nifty 50` both normalize to **`NSE:INDEX:NIFTY`** (via `_SYMBOL_ALIASES` `"nifty50index"/"nifty50"/"nifty"` → `"NIFTY"`).
- Fyers `BSE:SENSEX-INDEX` and Upstox `BSE_INDEX|SENSEX` both normalize to **`BSE:INDEX:SENSEX`** (`"sensex"` → `"SENSEX"`).
- The Fyers `_INDEX_DICT` in `brokers/fyers/feed.py:86-120` is an **explicit, curated alias table** (NIFTY50→"Nifty 50", NIFTYBANK→"Nifty Bank", etc.) — a *known-aliases* mapping, not fuzzy matching. This is the correct pattern and should be the basis for the dedup evidence.
- **Duplicate/alias evidence to record per index:** for every catalog NSE_INDEX/BSE_INDEX row, compute its canonical ID and group. A "duplicate" is two provider rows that map to the *same* canonical ID (expected, e.g. NIFTY present in both). A "missing" is a canonical ID present in only one provider. A "deprecated/unavailable" is a canonical ID whose provider row exists but the provider does not stream it (e.g. Fyers index ticks dropped, §4). **All of these are resolvable offline from the populated `data/events.db` catalog without live data** — the mapping table should be a reproducible report (canonical ID → {fyers_key?, upstox_key?, streaming?, entitlement}).

### 2.3 Licensing note
Presence of a symbol in a public catalog **does not itself grant entitlement to use its data.** Both brokers gate index data on the account's plan/entitlement (Fyers Standard=100 slots, Upstox per-mode caps). The system must therefore **never assume a catalog row is deliverable** — it must treat catalog *membership* as a superset of *actual entitlement*, and report the delivered subset truthfully (§4).

### 2.4 Coverage-matrix target (reproducible report)
For every canonical index in (NSE-supported ∪ BSE-SENSEX-scope), the deliverable is a row:
`canonical_id | fyers_native_key | upstox_native_key | fyers_streamable? | upstox_streamable? | fyers_entitled? | upstox_entitled? | status`
where `streamable` = provider's HSM/WS actually delivers it (Fyers indices: **unknown until §4 HSM test**), `entitled` = account plan covers a slot for it. Unresolved mappings are listed, not silently dropped.

---

## 3. Correct provider capacity + API-budget model (recalculated)

### 3.1 Streaming budgets (binding, after the app safety reserve `effective_budget = cap − max(10%,50)`)
| Provider | Plan/mode | Raw cap | Effective budget | Notes |
|---|---|---|---|---|
| Fyers | **Standard (default)** | **100** | **90** | Assumed unless Prime is proven |
| Fyers | Prime | 5,000 | 4,450 | Only if plan is confirmed Prime |
| Upstox | full (default) | 2,000 | 1,800 | Combined-cap 1,500 if multi-category |
| **Application** | `MAX_INSTRUMENTS_PER_PROVIDER` | **500** | **500** | Currently **exceeds** the Fyers-Standard broker cap (90) |

**Correction:** the application must **clamp the Fyers streaming set to the *detected* Fyers plan cap (90 Standard / 4,450 Prime)**, not to 500. Until plan detection exists (§1.2), the **safe default is to plan for the Standard 100-cap (90 effective)** and treat anything above as "would be dropped by the broker."

### 3.2 REST budgets (shared API quota; Standard vs Prime)
Fyers data-REST (Standard = default):
- Rate: **5 rps / 50 rmin / 5,000/day.**
- Quotes batch: **≤50 symbols/request** (Standard); ≤1,000 (Prime).
- Options chain: `options-chain-v3` per underlying (strikecount default 50); 1 request per underlying-expiry.
- Depth/history: per symbol.
- **Daily Fyers Standard budget = 5,000 requests total across quotes+depth+history+option-chain+marketStatus.** That shared pool is what the on-demand index/option tail must fit inside.

Upstox REST (official): option-greek ≤50 keys/request; full-market-quote ≤500 instruments; option chain per underlying; history V3 with OI. Upstox's per-account rate limits are plan-based (not captured here — flagged as a residual to confirm).

### 3.3 Concrete Fyers-only REST-refresh budget estimate (for index fallback, §4)
Assume Fyers-only, Standard plan, the intended index scope:
- **~9 featured NSE indices + 2 BSE SENSEX-family indices ≈ 11 index instruments** (the `MAJOR_INDICES` set), refreshed to keep them "live."
- Quotes batch = 50 symbols/request ⇒ **11 indices fit in a single quotes call** (`symbols=NSE:NIFTY50-INDEX,…`).
- Refresh cadence for a "live" label = **within 5 min** (matches `market_indices.STALE_MINUTES=5`). To stay safe, refresh **every 60 s** → 60 calls/hour ≈ **1,440 calls/day** for indices alone.
- Add F&O option chains on demand: ~6 option-chain underlyings (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX) × (chain fetch + 1 quotes batch) refreshed every 5 min → ~6×2×12/day = ~144/day, plus initial fetch.
- **Total ≈ 1,600–1,700 Fyers REST calls/day** for a Fyers-only Standard account doing index refresh + bounded option-chain polling. **This fits inside the 5,000/day Standard quota with ~3× headroom**, but leaves the per-second/minute caps (5 rps / 50 rmin) as the real constraint: the refresh loop must **throttle to ≤5 rps** and **batch the quotes call** so it does not spike. A single quotes call per cycle (11 symbols) is ~1 request/60 s → far under 5 rps. **Feasible, but only because the index scope is small and the quotes endpoint batches to 50.** This is the safe Fyers-only index strategy (§4).

---

## 4. Viable Fyers-only index-data strategy, or blocking uncertainty

### 4.1 Do NOT trust HSM index prices (yet)
- The HSM index-tick guard (`feed.py:937-941`) **stays**. Fyers HSM index LTP is **unproven** (§1.4; captured evidence shows a ×3.65 error). Do **not** remove the guard or expose HSM index prices as `live`.

### 4.2 Use Fyers **REST `/data/quotes`** as the index source (recommended)
- `/data/quotes` is a **REST snapshot** of the index LTP/OHLCV — **not** the HSM binary index path. It is the same endpoint already implemented in `brokers/fyers/rest.py::quotes()` and uses the *documented* Fyers symbol → LTP mapping, not the suspect HSM `multiplier/precision` scaling.
- This sidesteps U1 entirely: REST index quotes are validated against the exchange's published LTP and carry no multiplier ambiguity. **This is the safe Fyers-only index strategy.**
- **Central coordinator state machine** (proposed, in `market/service.py` + a `providers` policy, not in the adapter):
  - `LIVE` — a validated observation within 5 min, from any enabled provider (Upstox WS for indices if upstox is also enabled; Fyers REST for Fyers-only).
  - `REST_REFRESHED` — Fyers REST quote, within 5 min, labeled with `source_provenance:"rest"` and `provider:"fyers"` (distinct from HSM).
  - `STALE` — last observation older than 5 min; never `live`.
  - `UNAVAILABLE` — no observation from any enabled provider; no fabricated value.
  - The coordinator **rejects any observation that fails validation** (e.g. an HSM index LTP that deviates > tolerance from the provider's REST/index truth, or a non-numeric/absent LTP) even if it is the freshest tick. **Provider adapters never suppress their own ticks as the selection policy** — suppression, if any, is a *data-validity* decision made centrally, not a per-adapter drop.

### 4.3 HSM scaling market-hours test (required to ever enable HSM index delivery)
- **Exact test:** during NSE trading hours, on a Fyers data socket, subscribe `if|nse_cm|Nifty 50`. Capture the `snapshot` (data_type 83) fields: raw `ltp`, `multiplier`, `precision`, `exch_feed_time`.
- **Independent comparison:** fetch NIFTY 50 LTP from NSE's published index page (or Upstox's index feed if available) **at the same timestamp**.
- **Allowed tolerance:** computed `ltp/scale` must equal the independent value to **within 0.05 index points** (NIFTY's tick is 0.05) at capture time. **Pass** = within tolerance on 3 consecutive snapshots.
- **Acceptance:** record `multiplier`, `precision`, raw `ltp`, computed value, independent value, delta, and timestamp. **If this passes**, the HSM index path may be *optionally* enabled (as a cheaper alternative to REST for indices). **If it fails or is not run, the REST index path is the authoritative one and HSM index ticks remain dropped/labeled-unreliable.**
- **This test has NOT been run; it must not be claimed as passed.** Mark it **BLOCKING** for any HSM-index feature. It is **not** blocking for the REST-index Fyers-only strategy above.

### 4.4 Do NOT make Upstox a permanent hidden assumption
- The central coordinator picks the index source by **availability + freshness + plan entitlement**, across *all enabled providers*. If both are enabled, the freshest validated index observation wins (the two are the same exchange index, so they agree within tick). If Fyers-only, Fyers REST is the source. **Upstox is preferred only when it is a *live, validated, entitled* source — not by hard-coded order.** The `resolve_spot` Upstox-first ordering in `market_indices.py:199-233` should be **replaced by the central coordinator's availability-based selection** so no provider is a hidden permanent default.

### 4.5 Conclusion on Fyers-only indices
- **Viable now:** Fyers-only index data via **`/data/quotes` REST refresh (batched, ≤5 rps, 5-min freshness)** — no HSM dependency, no unproven scaling. Marked viable *provided* the index scope stays within the 5,000/day Standard REST quota and the 100-slot Standard streaming cap (indices via REST do not consume streaming slots).
- **Blocking uncertainty:** **HSM index LTP correctness (U1).** Any *streaming* index delivery from Fyers is blocked until the §4.3 market-hours test passes. This does **not** block the REST path.

---

## 5. Final automatic subscription + fallback design

### 5.1 Single source of truth for desired subscriptions
**The desired set is computed, not stored, except for two durable seeds:**
1. **Instrument catalogue** (`instruments` table) — the *universe* (what exists). Discovered, never hardcoded.
2. **`fno_config` + `md_derivative_rules` + `md_subscriptions` (persistent owners)** — the *operator intent* (which categories/underlyings/windows are wanted). These are already the persisted "seed" state.

The **live desired set** = `f(resolve(catalogue, fno_config, active_views, workspace_state, enabled∧authenticated providers, per-provider plan caps))`. This is computed at startup and on every trigger (§5.2), then reconciled to the feeds. **No manual "apply F&O" step** — apply is the automatic reconcile, not a user action.

### 5.2 Automatic triggers and reconcile
- **Startup:** load persisted seeds → compute desired → `reconcile(feed_provider)` on each enabled feed. **Restore the last desired set without a manual apply.** (This is the *only* persistence that genuinely needs to survive restart: the operator's F&O intent. It does NOT require touching frozen auth/startup-*composition* — it is a *subscription-state* restore consumed by the existing `reconcile` path.)
- **Provider failure / reconnect:** on feed state change (entering/exiting `streaming`), recompute availability and **re-route** contracts to a still-available provider (central selector), marking the loser `STALE`/`UNCOVERED`, not silently `live`.
- **Catalogue change (sync):** epoch bump → recompute desired; expired expiries (< today) are filtered at the boundary; new contracts enter, removed ones drop from the feed.
- **Expiry change / rollover:** `_choose_expiry` already filters `expiry >= today`; on expiry roll the coordinator re-selects the nearest/selected expiry and re-reconciles.
- **Transition Fyers-only ⇄ Upstox-only ⇄ dual:** recompute `enabled∧authenticated` provider set → re-route every contract by capability+freshness+budget. **No operator input required.**
- **Subscription-limit handling:** if a provider's plan cap (Fyers 90/4,450; Upstox 1,800) or the app cap (500) is exceeded, the coordinator **drops by priority** (live futures > index > option strikes) and reports the dropped set as `CAPACITY_LIMITED` — **never silently claims full coverage.**

### 5.3 What is NOT persisted
- Per-tick quote state, in-memory `active_view`/`analytics_universe`/`derivatives_universe` owners (ephemeral, re-derived), HSM/WS session state, and any auth/session token. **No persistence or startup code is modified in this review.** The one durable *subscription-intent* seed (last-applied F&O plan) is the only candidate for a new persisted field, and it is **flagged as the protected-persistence item that needs separate approval** — not touched now.

---

## 6. Identity + quote-selection target; authentication boundary

### 6.1 Read-through (transitional) vs canonical-storage (durable)
- **Transitional read-through (Option B):** `get_quote_now` falls back to the canonical resolver when the native key misses. *Limitation:* two storage slots per instrument persist; cross-provider *writes* still diverge (last-write-wins per provider); a *single* canonical read converges but the store is not truly unified. **Acceptance:** a read by either provider's key returns the freshest; a read by canonical ID returns the same. **Removal plan:** once the central coordinator is in place (§6.2), the read-through is subsumed and deleted.
- **Durable target (Option A):** key `MarketService._quotes` by `(exchange, canonical_id)`; every write path resolves canonical first; provider-native token is `Quote.provider_token` (provenance only). **Consequences:** single slot per instrument (true convergence), clean cross-provider merge, one lookup; but it is a **write-path + data-migration change** adjacent to the frozen startup baseline → **separate protected-core approval**, later.

### 6.2 Central coordinator (removes scattered provider-key assumptions)
- A `providers`-level **quote coordinator**: each adapter delivers its *validated* observation keyed by canonical ID + provenance; the coordinator exposes **one canonical quote** chosen by (enabled ∧ authenticated, freshness, data-validity, provider entitlement, then preference order as a *tie-breaker only*). **Invalid/unvalidated data is rejected even if freshest.** Adapters never drop ticks as the selection policy; central *validity* gates are the only suppression.
- **Backend:** all quote consumers (`market/indices`, `futures/heatmap`, option-chain, SSE) read the coordinator's canonical quote; provider keys are diagnostics only.
- **Frontend:** `quoteKey`/`IndicesWidget` join on **canonical ID**, not `instrument_token`/`upstox_key`. This removes the `preferences().key=upstox_key` assumption that breaks Fyers-only display.
- **Migration/rollback:** canonical keying (Option A) is the only irreversible step; read-through (B) is a pure revert. The coordinator is additive and reversible.

### 6.3 Automatic-authentication boundary (separate from manual config)
- **Unavoidable provider auth (NOT a defect, NOT to be bypassed):** Fyers daily OAuth+PIN; Upstox daily re-auth; both single-day session tokens; plan/entitlement gates (Standard 100 slots). These are **provider security/entitlement requirements** and are out of code scope.
- **What CAN be automatic (in-scope):** subscription *state* restore at startup (§5.2) — **no manual broker selection, no manual per-instrument selection, no manual F&O apply.** The operator's once-daily login (provider requirement) is the *only* human step, and it is outside what "automatic" is asked to remove.
- **Do not** attempt to bypass auth, provider access controls, or account-security requirements.

---

## 7. Dependency-ordered implementation plan (no coding this round)

> Module boundaries exact; risks/tests/rollback per stage. **No stage in this round modifies source.** Protected-core stages are explicitly gated.

| # | Stage | Module boundary | Deps | Risk | Acceptance test | Data/schema impact | Rollback |
|---|---|---|---|---|---|---|---|
| S1 | Commit untracked `brokers/fyers/rest.py` | add 1 file (already written; imported at `app/server.py:695`) | — | none (additive) | clean checkout: `from brokers.fyers.rest import FyersRest` succeeds; `pytest test/test_provider_registry.py` | none | `git reset --soft HEAD~1` |
| S2 | **Correct Fyers capability cap to plan-aware** | `brokers/fyers/capabilities.py` (+ a plan-cap resolver) | S1 | medium (changes `effective_budget`) | unit: Standard→90, Prime→4,450; `MAX_INSTRUMENTS_PER_PROVIDER` clamped to the detected Fyers cap | none | revert |
| S3 | **Fyers plan-cap detection / safe default** | `providers/registry.py` (add `plan_cap(provider)`) + config field `fyers_plan_cap` (default **Standard=100**) | S2 | medium (new config surface) | a Standard account plans ≤90 streaming keys; a Prime account (if declared) plans ≤4,450; no manual per-instrument selection | config field (additive) | revert |
| S4 | **Canonical read-through** in `MarketService` | `market/service.py` `get_quote_now`/`get_depth_now` | S1 | low (additive read) | read by either provider key / by canonical ID returns the freshest | none | revert `market/service.py` |
| S5 | **Central quote coordinator + validity gates + freshness** | new `providers` policy + `market/service.py` (`get_canonical_quote`) + `fno_coverage._available` (add quote-age) | S4 | medium-high (new selection policy) | stale provider ≠ `live`; invalid observation rejected even if freshest; no provider drops ticks centrally | none | revert the 3 modules |
| S6 | `preferences()` + frontend join on canonical ID | `app/subscriptions/service.py::preferences` + `IndicesWidget.tsx` + `types.ts` + `WatchlistTable.tsx` | S4/S5 | medium (API shape) | Fyers-only renders index cards; `GET /api/subscriptions` returns `canonical_id` | none | revert |
| S7 | **Automatic subscription restore (last-applied F&O)** ⚠️ *protected-core* | `core/persistence/modules/fno_config.py` (1 new field) + `app/subscriptions/service.py` load-on-startup + `app/server.py` lifespan | S5 | **high (persistence red-zone + startup)** | boots with derivatives-universe owner populated; no manual apply | **schema: new persisted field** | revert to staged `fno_config` — **requires separate protected-core approval** |
| S8 | Catalogue-driven index discovery (remove hardcoded 9) | `app/market_indices.py::discover_indices(catalog)` + `market/index_registry.py` + `IndicesWidget.tsx` | S1, S4, S6 | medium | `discover_indices` returns all *catalogue* NSE/BSE index rows; main strip = featured + F&O underlyings; full list searchable | none | revert |
| S9 | **Fyers-only index via REST** (fallback source) | `brokers/fyers/rest.py::quotes` (exists) + coordinator index source + bounded poller (≤5 rps, 5-min) | S5 | medium | Fyers-only: index cards `provider:"fyers", source:"rest", basis:"live"` within 5 min; no HSM index used; REST budget ≤5,000/day Standard | none | revert |
| S10 | **HSM index scaling market-hours test** (BLOCKING for HSM index) | a test harness (no prod code) | S9 | — (evidence only) | §4.3 3-snapshot, ≤0.05-pt tolerance vs NSE/Upstox independent LTP; record fields | none | — (evidence) |
| S11 | `migrate_from_config` Fyers index categorization | `app/subscriptions/service.py::migrate_from_config` | S6 | low (idempotent) | Fyers index config keys import as `index`; the 2 `('stock','NSE:*-INDEX')` rows corrected | one-time data correction | revert (marker makes re-run safe) |
| S12 | Frontend request-storm fix | `frontend/src/features/fno/FnoWorkspaceView.tsx` | — | low | `FnoWorkspaceView.test.tsx` idle/afterRenders/atUnmount/afterUnmount = 1 | none | revert |
| S13 | Operational: catalogue sync (for the *empty* env) | none (API `POST /api/instruments/sync`) | S1 | low | `SELECT COUNT(*) FROM instruments > 0` in the target DB; `plan().desired > 0` | populates `instruments` | transactional (failed sync leaves prior catalog) |
| S14 | **Canonical re-key (Option A)** ⚠️ *deferred, protected-core* | `market/service.py` write path → `(exchange, canonical_id)` | S5 proven | high | single slot per instrument; cross-provider merge; migration/backfill | data migration | separate protected-core task |

**Critical path for Fyers-only to *work safely*:** S1 → S2/S3 (plan-aware cap) → S4 → S5 → S6 → **S9** (REST index, no HSM dependency). S10 gates any HSM-index feature. S7 and S14 are protected-core.

---

## 8. Unresolved issues that still prevent coding

1. **U1 — Fyers HSM index LTP scaling (BLOCKING for S10/HSM index only).** Not proven. The REST index path (S9) does **not** depend on it. Required evidence: §4.3 market-hours 3-snapshot capture. **Cannot be closed by code inspection.**
2. **U2 — Fyers account plan (Standard vs Prime).** No documented API field exposes it (§1.2). **Design decision required:** safe default = plan for Standard (100/90) and make the cap *configurable* (`fyers_plan_cap`) so a Prime operator can raise it. Until decided, S3 ships the Standard-default. **This is the #1 fact that must be settled before finalizing the streaming budget.**
3. **U3 — Upstox account rate/entitlement tier** (which drives Upstox REST caps and whether `full_d30` is available). Not captured here. **Residual to confirm** against the connected account before trusting Upstox REST on-demand at scale.
4. **U4 (settled for this tree):** runtime `data_dir = "data_test"` (empty catalog). Confirmed by reading `config.json`. Any F&O/index *live* behavior in *this* tree is blocked on S13 (catalogue sync) — an operational step, **not** to be run in this read-only round.
5. **G1 storage-key fork (B now / A later):** needs maintainer sign-off that read-through is acceptable as the transitional mechanism and that the canonical re-key (S14) is deferred as a separate protected-core task.
6. **Protected-persistence approval:** S7 (persist last-applied F&O plan) and S14 both touch `core/persistence` / startup. **Not to be coded without a separate protected-core approval** per `AGENTS.md`.
7. **Licensing/entitlement confirmation:** the exact *delivered* index set per plan (Standard 100-slot) must be confirmed against the connected account; catalog membership ≠ entitlement (§2.3).

---

## 9. Conclusion

**NOT READY FOR MAINTAINER REVIEW (for *implementation* coding).**

Reasons, in order of gate-blocking:
- **U2 (Fyers plan: Standard=100 vs Prime=5,000) is unresolved and materially changes every budget/acceptance figure.** The Round-2 numbers (5,000/4,450) are **refuted**; the correct *default-safe* model is **Standard 100/90** with a **configurable Prime override**. This must be settled (or the config override adopted) before the streaming-budget acceptance tests can be written.
- **U1 (HSM index scaling) is unresolved and untestable in this read-only round.** It blocks *any* HSM-index feature, though **not** the REST-index Fyers-only path (S9).
- **Protected-core items (S7, S14) require separate approval** and are excluded from the ordinary coding gate.
- The **investigation** (capacity model, index coverage, identity design, automatic subscription, Fyers-only index strategy) is **complete and self-consistent**; what remains is **factual sign-off on U2/U3** and **approval of the plan shape (G1 fork + protected-core scope)**.

**Ready state for the next step:** the *design* is ready for maintainer review; **coding is prohibited** until (a) the Fyers plan-cap model (U2) is decided, (b) the HSM-scaling test (U1) is scheduled/accepted as a known block, (c) G1 + protected-core scope (S7/S14) is approved, and (d) the maintainer explicitly authorizes implementation. No code was written; no files, DB, config, or build artifacts were modified; all in-flight work is preserved.
