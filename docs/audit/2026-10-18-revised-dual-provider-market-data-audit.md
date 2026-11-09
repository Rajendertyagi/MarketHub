# MarketHub — Revised Dual-Provider Market-Data Audit (Round 2)

**Mode:** READ-ONLY. No files staged/committed, no DB writes, no config changes, no catalogue sync, no mutating API calls, no repo reorganization. All prior staged/unstaged/untracked work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Observation date:** 2026-10-18
**Scope note:** Every claim re-verified against the live working tree; official provider docs re-checked. The prior report's stage sequence was **not** treated as approved. Several prior claims are **REFUTED or CORRECTED** by direct inspection — detailed in §1.

---

## 1. Confirmed findings vs. unresolved questions

### 1.1 Corrected / refuted claims from the prior report

| Prior claim | Status now | Evidence |
|---|---|---|
| D1 "catalogue empty (Critical)" | **PARTIALLY REFUTED — environment conflation.** The *main* `data/events.db` is **fully populated: 142,932 instruments**. Only `data_test/events.db` and `test/data_test/events.db` are empty (0 rows). "Empty catalogue" is a **test-fixture** condition, not the production database. | `data/events.db`→142,932; `data_test/events.db`→0; `test/data_test/events.db`→0 |
| Limits "Fyers 5000 / Upstox 4500" | **REFUTED — Upstox 4500 is wrong.** Prior numbers were inherited, not official. Actual: Fyers data-socket = **5000/connection**; Upstox = **mode-limited** (ltpc 5000, option_greeks 3000, **full 2000**, full_d30 50 [Plus]) with **combined-cap** reductions when multiple categories share a connection. The code's `brokers/upstox/capabilities.py` is **correct** (full=2000 default) — the prior audit's "4500" figure was the error. | §5 below |
| D2 "untracked rest.py will crash clean checkout" | **CONFIRMED, unchanged.** `git ls-files --error-unmatch brokers/fyers/rest.py` → no match; imported at `app/server.py:695-696`. | `git ls-files` |
| D3 "Fyers HSM index ticks dropped unconditionally" | **CONFIRMED, location precise.** `brokers/fyers/feed.py:937-941` `if snap["topic"].startswith("if"): return` — unconditional, with comment "Upstox is the authoritative index source." | `feed.py:937` |
| D4/D5 "provider-specific quote keys + UI join on `upstox_key`" | **CONFIRMED, refined.** `market/service.py:207-210` `get_quote_now` = raw `(exchange, instrument_token)` dict lookup, **no identity resolver consulted**. `app/subscriptions/service.py:231` `preferences().indices[].key = entry["upstox_key"]`. `Quote.canonical_id` *is* computed in `_merge_quote` (`service.py:251-257`) but is **not used as the storage key**. | `service.py:207`, `:251` |
| D6 "F&O manual apply, not restored at startup" | **CONFIRMED.** `SubscriptionService._derivatives_universe` is an in-memory dict (`service.py:92`), "NEVER persisted"; `app/server.py` lifespan reconciles only the *persistent* owners. No startup restore of the last-applied F&O plan. | `service.py:92` |
| D7 "Fyers index keys stored as `stock`" | **CONFIRMED, root cause exact.** `data_test/events.db::md_subscriptions` has `('stock','NSE:NIFTY50-INDEX')` and `('stock','NSE:BANKNIFTY-INDEX')`. `migrate_from_config` (`service.py:173-219`) matches only `by_key = {e["upstox_key"]: e}` — Fyers colon keys fall through to `category="stock"`. | `service.py:187` |
| D8 "no freshness-based fallback" | **CONFIRMED, refined.** `fno_coverage._available` (`:210-215`) checks only feed **state** ∈ `{streaming,connecting,reconnecting}` — **no quote-age check**. A stale-but-`streaming` Upstox feed still wins routing. | `fno_coverage.py:210` |
| D9 "MAJOR_INDICES hardcoded to 9" | **REFUTED as the prior count, CORRECTED.** `MAJOR_INDICES` has **9 entries** (`app/market_indices.py:45-73`), but the **catalog holds 268 NSE_INDEX distinct symbols (union of both providers)** and **149 BSE_INDEX** (incl. SENSEX, BANKEX). The *coverage gap* is real: 259 NSE + 140 BSE indices are in the catalogue but not in the hardcoded 9. | `market_indices.py:45`, DB counts |
| D10 "fno/view request storm; fix not in tree" | **CONFIRMED (partially verified).** `docs/current.md` §2.3 documents the stable-`mutate` fix + regression test; the working-tree `FnoWorkspaceView.tsx` does **not** carry it. | `docs/current.md §2.3` |

### 1.2 Outstanding critical uncertainties (must settle before coding)

| # | Uncertainty | Why it blocks | Safe resolution |
|---|---|---|---|
| U1 | **Fyers HSM index-tick price scaling is UNPROVEN.** Code applies `scale = (10**precision)*multiplier` (`feed.py:942`), but the comment at `:937-939` records a *captured* NIFTY snapshot returning **multiplier=28 / precision=0 → 87,205.71** when NIFTY was actually ~23,897. That is a **×3.65 error**, i.e. the HSM index numbers are unreliable **as observed**. | We cannot claim Fyers-only index data will be correct without live proof. | A **live-market HSM smoke test** during NSE trading hours: capture a `if\|nse_cm\|Nifty 50` snapshot, verify `ltp/scale` matches the NSE published index, and re-derive the exact `multiplier`/`precision` semantics from `map.json`. **Do NOT assume code inspection proves it.** |
| U2 | **Upstox "combined" vs "individual" mode cap interaction** when a connection subscribes ltpc+full+greeks simultaneously. Docs say multi-category → 2000 (ltpc/greeks) / 1500 (full). Code picks one `default_mode` per provider. | Determines the true usable streaming budget. | Confirm whether MarketHub subscribes **all** keys in one mode (current design: single `default_mode`="full" → 2000 cap → effective 1800 after reserve). If a future design mixes modes, recompute. |
| U3 | **Fyers daily-login (2FA) session lifetime** vs. "auto-restore at restart" goal. If a Fyers token expires during the day, "restore last plan" still can't stream without re-login. | Affects whether "no manual step" is truly achievable for Fyers. | Document: Fyers requires a **manual daily OAuth+PIN** (unavoidable provider constraint). "Automatic" applies to *subscription state*, not *authentication*. Upstox also has daily re-auth. Neither can be fully hands-free across calendar days — this is an entitlement/licensing boundary, not a code defect. |
| U4 | **Whether `data/` (populated) or `data_test/` (empty) is the runtime DB in a given environment.** | "Empty catalogue" only applies to the empty one. | Read-only evidence: `config.json` `data_dir` and which `events.db` the running server opens. In this tree `data/events.db` is the populated one; `data_test/` is the test harness fixture. |

---

## 2. Corrected coverage / capacity matrix

### 2.1 Catalogue reality (from `data/events.db`, the populated production DB, as of 2026-10-16 sync)

| Segment | upstox rows | fyers rows | Distinct symbols (union) |
|---|---|---|---|
| NSE_INDEX | 139 | 129 | **268** |
| BSE_INDEX | 79 | 70 | **149** |
| NSE_EQ | 9,820 (7,121 have `instrument_type` NULL = GOI strips/bonds) | 3,220 (EQUITY) + 355 (ETF) | — |
| NSE_FO futures | 653 | 653 | 219 distinct underlyings |
| NSE_FO options | 37,390 | 85,286 | 219 underlyings, 18 expiries (2026-10-19 → 2031-06-24) |
| BSE_FO | futures 12 + options 5,126 | (fyers BSE_FO rows present under BSE segments) | SENSEX/BANKEX options present |

> **Key correction:** NSE_FO equity-F&O underlyings = **219 distinct** in the catalogue (not "~250"; not 0). Both providers list the same 219. The top contract counts: NIFTY 5,995, BANKNIFTY 3,335, NIFTYNXT50 3,320, MIDCPNIFTY 2,536, NIFTYFPI 1,630, FINNIFTY 1,594, then single-stock underlyings ~700-870 each.

### 2.2 The five coverage layers (explicitly separated, as required)

1. **Complete instrument-catalogue coverage** — everything in `instruments`: 268 NSE index, 149 BSE index (incl. SENSEX/BANKEX), 219 NSE_FO underlyings with futures+options across 18 expiries, BSE_FO (SENSEX/BANKEX options). *This is the source of truth; coverage must be discovered from it, never from a hardcoded list.*

2. **Eligible-per-provider** — a contract is eligible for provider *P* only if P's catalogue has a row for it. NSE indices: both (129/139 fyers/upstox). BSE indices: both (70/79). NSE_FO: both carry the 219 underlyings but option counts differ (37,390 upstox vs 85,286 fyers — Fyers lists far more strikes/expiries). BSE_FO: dominated by upstox (fyers BSE_FO is thin).

3. **Continuously streamable under real limits** — bounded by mode caps after the app's safety reserve:
   - Fyers: `effective_budget(5000)=4450` keys.
   - Upstox (full): `effective_budget(2000)=1800` keys.
   - `MAX_INSTRUMENTS_PER_PROVIDER=500` is the *application* hard cap on resolved desired keys per provider (`service.py:721`), far below both broker caps. **So the app's real streaming ceiling today is 500/provider, not the broker cap.** Streaming *all* 219 underlyings × multi-expiry × multi-strike F&O (thousands of keys) is **infeasible** in one budget; the design must scope the live stream (futures + ATM option window) and defer the rest to REST on-demand.

4. **On-demand via supported REST** — beyond the stream budget:
   - Fyers: `/data/quotes` (≤50 symbols/request, Standard), `/data/option-chain-v3` (ATM±window), `/data/depth`, `/data/history`, `/data/marketStatus`. Rate: **Standard 5 rps / 50 rmin / 5,000/day**; Prime 10 rps / 500 rmin / 500,000/day.
   - Upstox: `/v2/option/chain`, `/v3/market-quote/option-greek` (≤50 keys/request), history V3 (OI). Full market-quote quotes ≤500.
   - The prior plan (Phase 2.3, `docs/plans/...`) already specifies **option chains are REST-on-demand, never WebSocket keys** — the live WS budget is reserved for futures + index + spot.

5. **Unavailable due to capability/auth/entitlement/licensing** —
   - **Fyers OI analytics (max-pain/PCR):** NOT declared (`brokers/fyers/capabilities.py` lacks `CAP_OI_ANALYTICS/CAP_MAX_PAIN/CAP_PCR`); OI-change% only via REST depth (`pdoi`/`oipercent`), not HSM.
   - **Upstox greeks:** via `option_greeks` mode or REST option-greek; **previous-day OI / OI-change are NOT on the Upstox wire** (`brokers/upstox/capabilities.py` docstring) — persisted previous-session OI is the fallback.
   - **Fyers HSM index LTP:** unreliable scaling (U1) → indices effectively Upstox-authoritative until U1 passes.
   - **Both brokers:** daily re-auth (2FA/PIN) — a *human* step at day boundary, outside "automatic subscription" scope (U3).

---

## 3. Target architecture for identity & quote selection

### 3.1 Three-layer canonical identity (target, unchanged from round 1 but now with evidence)
- **Canonical ID** (`app/market_identity.py`): `INDEX: {exch}:INDEX:{sym}`, `EQUITY: {exch}:EQUITY:{ISIN|sym}`, `FUTURE: {exch}:FUTURE:{und}:{expiry}`, `OPTION: {exch}:OPTION:{und}:{expiry}:{strike}:{CE|PE}`. The same real instrument converges from either provider. This is the **storage key target**.
- **Provider-native token** preserved as `Quote.provider_token` for feed subscribe/unsub + provenance; the wire still uses native keys.
- **Provider-specific observations kept separate** from the canonical quote: store per-provider last-observation (price, ts, provider, provenance) and let a **central selector** expose one canonical quote.

### 3.2 Read-through vs. canonical re-key (the genuine design fork — G1)

| | **Option B — read-through (additive)** | **Option A — canonical re-key (target)** |
|---|---|---|
| Change | `get_quote_now` falls back to `MarketInstrumentIdentityResolver` when direct key misses | `_quotes` keyed by `(exchange, canonical_id)`; every write path resolves canonical first |
| Slots | 2 per instrument (one per provider) | 1 per instrument |
| Consistency | Reads converge; writes still diverge (two slots, last-write-wins per provider) | Single slot; cross-provider merge under one key |
| Concurrency | Additive read path; write path untouched → **low risk** | Must re-key the write path + all consumers; **cross-cutting**, adjacent to the FROZEN startup baseline |
| Migration | Fully reversible (revert one method) | Requires data backfill of existing runs + a migration step; not reversible in place |
| Performance | Two lookups on miss | One lookup |
| Verdict | **Ship now** (unblocks Fyers-only display + cross-provider reads) | **Later, separate protected-core task** (re-keying the shared quote-store is the kind of boundary-crossing move that needs its own approval) |

**Recommendation:** B now, A later. This matches the "provider adapters keep native keys; consumers read canonical" separation.

### 3.3 Central quote-source selection & fallback (not per-adapter tick suppression)
- **Do not** resolve provider priority or suppress ticks inside `brokers/*/feed.py` as the primary mechanism (prior Stage 3/9 did exactly that — that is the anti-pattern the requirements forbid). Both feeds deliver their ticks to `MarketService`; the **adapter must not drop a provider's own index ticks** as a substitute for policy.
- The **central selector** lives in `market/service.py` + a new `providers`-level policy:
  - Each provider writes its native-keyed observation.
  - `get_canonical_quote(canonical_id)` → gather all provider slots for that instrument → **pick by (enabled, freshness, provenance rank, provider preference order)** → return the winner + `basis` (`live`/`stale`/`unavailable`) + `source_provenance` (`hsm`/`rest`/`tbt`) + `provider`/`provider_token` for attribution.
  - **Health/freshness** is a first-class routing input (fixes D8): a provider whose last quote is older than the staleness threshold (5 min, `market_indices.STALE_MINUTES`) is *state-available* but *data-stale* → routed contracts are marked `STALE`, never silently `live`.
- **Validation:** every exposed quote carries `received_ts`, `provider`, `provider_token`, `canonical_id`, `basis`. No fabricated quotes; missing → `unavailable`.

### 3.4 Stable, provider-independent API contracts
All market-data endpoints return `canonical_id` + provider attribution + `basis` + `received_ts`. The frontend joins on `canonical_id`, never on `instrument_token` or `upstox_key`. `preferences().indices[].key` becomes the **canonical ID** (with `fyers_symbol`/`upstox_key` as separate provenance fields), so `IndicesWidget` works in Fyers-only mode.

---

## 4. Fully automatic subscription model (no manual provider/instrument/apply)

### 4.1 Discovery → compute → reconcile → restore
1. **Discover** from the catalogue (layer 1), not hardcoded lists: `discover_indices(catalog)` = all `instrument_type='INDEX'` rows (NSE_INDEX ∪ BSE_INDEX, 268+149). F&O universe = `fno_underlyings`/`futures_index` (219 NSE underlyings + BSE SENSEX/BANKEX).
2. **Compute required subscriptions** = **auto-selected scope**, NOT the whole catalogue:
   - Indices: featured + F&O underlyings in the main strip; full list discoverable/searchable (G4).
   - F&O live stream: **futures (current+next) + ATM ± bounded-strike option window** per active view (the existing `md_derivative_rules` + active-view mechanism). This stays *within* the 500/provider app cap.
   - Everything else (all strikes/expiries, chains, history) → **REST on-demand**, not streamed.
3. **Reconcile across eligible providers** via the central selector: route each contract by capability + freshness + budget; if capacity is exceeded, the **policy** picks automatically (per-kind priority: live futures > index > options; drop lowest-priority to fit; mark the dropped set `CAPACITY_LIMITED`) and **reports truthful coverage** — never claims full live coverage.
4. **Restore on restart:** persist the last-applied derivatives-universe owner (the *subscription state*, not auth) so a restart resubscribes the same contracts automatically. **This is the only persistence change, and it is the red-zone item (see §6).** Auth/session is **FROZEN** and out of scope (daily re-login is a provider constraint, not a bug).

### 4.2 Truthful status when capacity is exceeded
`FnoCoverageService.plan()` already emits states (`LIVE`/`UNSUBSCRIBED`/`UNCOVERED`/`PROVIDER_UNAVAILABLE`/`CAPACITY_LIMITED`/`STALE`). The fix is: (a) add **freshness** to `_available` so stale feeds don't masquerade as live; (b) make the covered/total counts **per-provider + per-kind** so the UI can show "N of M streamed, rest REST-on-demand" instead of implying full live coverage.

### 4.3 Verify-before-recommend: limits
- **Do NOT stream every catalogue contract** — infeasible (layer 3). 219 underlyings × multi-expiry × multi-strike ≫ any single connection's cap. The design explicitly scopes the live stream and uses REST for the tail.
- REST batching is bounded: Fyers quotes ≤50/request (Standard), Upstox option-greek ≤50/request, Upstox full-market-quote ≤500. Caching: option chains + futures index are already cached single-flight keyed by catalogue epoch + 300s TTL. Bounded polling: REST fallback only for instruments outside the stream budget, with per-provider token-bucket throttle (Fyers ~≤8 rps standard) and exponential backoff on 429.

---

## 5. Current official provider limits, capabilities, URLs, check dates

**Checked 2026-10-18.**

### 5.1 Upstox (V3 WebSocket + REST) — https://upstox.com/developer/api-documentation/v3/get-market-data-feed/
| Mode | Individual cap | Combined cap (multi-category) | Fields |
|---|---|---|---|
| `ltpc` | **5000** | 2000 | LTP + close (+CAS IEP) |
| `option_greeks` | **3000** | 2000 | greeks only |
| `full` | **2000** | 1500 | LTPC + D5 + OHLC + OI + IV + greeks |
| `full_d30` (Plus only) | **50** | 1500 | full + D30 |
- Connections: 2/user (standard), 5/user (Plus).
- **Corrections to code:** `brokers/upstox/capabilities.py` `MODE_KEY_LIMITS={"ltpc":5000,"option_greeks":3000,"full":2000,"full_d30":50}`, `DEFAULT_MODE="full"` → **capacity = 2000**, `effective_budget(2000)=1800`. **This matches the official docs. The prior audit's "Upstox 4500" was wrong; the code is correct.**
- REST: option chain `/v2/option/chain`; option greek `/v3/market-quote/option-greek` (≤50 keys/request); full-market-quote `/v2/market-quote/quotes` (≤500); history V3 with OI.
- **Combined-limit nuance (U2):** if a single connection subscribes ltpc+greeks+full, each category is throttled to its *combined* cap (2000/2000/1500). Current MarketHub uses a single `default_mode`, so the individual cap applies per connection.

### 5.2 Fyers (v3 data socket + REST) — https://support.fyers.in/... (data-websocket, rate-limits)
| Item | Limit |
|---|---|
| Data WebSocket symbols | **5000 / connection** (full mode marker `0x46`=full, `0x4c`=lite) |
| TBT socket | 3 connections, 5 symbols/connection |
| REST data (Standard) | **5 rps / 50 rmin / 5,000 per day** |
| REST data (Prime) | **10 rps / 500 rmin / 500,000 per day** |
| Quotes per request | ≤50 (Standard), ≤1000 (Prime) |
| Transaction/User-info | 10 rps / 200 rmin / 1,00,000 per day |
- Code: `brokers/fyers/capabilities.py` `MODE_KEY_LIMITS={"full":5000}` → capacity 5000, `effective_budget(5000)=4450`. **Correct vs docs.**
- **Index-tick scaling (U1):** HSM `if|` topics use `_INDEX_VAL` (ltp, prev_close, exch_feed_time, high, low, open) scaled by `(10**precision)*multiplier`. A *captured* NIFTY snapshot gave multiplier=28/precision=0 → 87,205.71 vs true ~23,897 (×3.65 off). **Cannot be declared correct by code inspection — requires a live HSM market-hours capture.**

### 5.3 NSE / BSE official universes
- NSE indices: nseindia.com / niftyindices.com — 268 NSE_INDEX symbols in the synced catalogue.
- BSE: bseindia.com — 149 BSE_INDEX (incl. SENSEX, BANKEX, SENSEX50, BSE100/200/500, etc.).
- NSE F&O: 219 equity underlyings in the catalogue (live NSE F&O eligible equities change monthly — re-measure after each sync; do not hardcode).

---

## 6. Revised staged implementation plan (corrected)

> Every stage lists dependencies, module boundaries, acceptance tests, risks, data/schema impact, rollback. **Protected-core items are explicitly marked and require separate approval.** Nothing here touches `app/auth/*`, credential storage, session restoration, or startup restoration (FROZEN).

### Stage 0 — Record & re-baseline (read-only, already done in this round)
- **Boundary:** none (inspection only). **Risk:** none. **Rollback:** n/a.
- **Acceptance:** this report's §1/§2/§5 numbers are the new baseline. **Data impact:** none.

### Stage 1 — Commit `brokers/fyers/rest.py` (fixes D2; **no code change**)
- **Dep:** none. **Boundary:** add one untracked file `brokers/fyers/rest.py` (the REST client the composition root already imports at `app/server.py:695`). **Risks:** none (additive; the file is already written and used). **Data/schema:** none. **Acceptance:** on a clean checkout `python -c "from brokers.fyers.rest import FyersRest"` succeeds; `pytest test/test_provider_registry.py` green. **Rollback:** `git reset --soft HEAD~1`.

### Stage 2 — Canonical **read-through** in `MarketService` (D5; G1=Option B)
- **Dep:** Stage 1. **Boundary:** `market/service.py` — `get_quote_now`/`get_depth_now` fall back to `MarketInstrumentIdentityResolver` when the native key misses; write path **unchanged**. **Risks:** low (additive read path). **Data/schema:** none (two slots persist). **Acceptance:** two providers write the same instrument under different native keys; a `get_quote_now` with *either* key returns the freshest; a new `canonical_id`-based read returns the same. **Rollback:** revert `market/service.py` (file is in the modified list — careful merge).

### Stage 3 — **Central** quote-source selection + freshness (D8; fixes the "suppression in adapter" anti-pattern)
- **Dep:** Stage 2. **Boundary:** `market/service.py` (new `get_canonical_quote`), `providers/registry.py` (add `health_fn`/`route()`), `app/fno_coverage.py::_available` (add quote-age gate). **Do NOT** change `brokers/*/feed.py` tick delivery. **Risks:** medium — health check must be lock-free and O(1). **Data/schema:** none. **Acceptance:** provider with a >5-min-stale quote is marked `STALE` for routing (not `live`); no provider drops its own ticks; a central selector returns one canonical quote with `provider`+`basis`+`provenance`. **Rollback:** revert the three modules.

### Stage 4 — `preferences()` + frontend join on canonical ID (D4/D7)
- **Dep:** Stage 2. **Boundary:** `app/subscriptions/service.py::preferences` (return `canonical_id` as `.key`; keep `fyers_symbol`/`upstox_key` as provenance), `frontend/.../IndicesWidget.tsx`, `frontend/.../types.ts` (`quoteKey`), `frontend/.../WatchlistTable.tsx`. **Risks:** medium — additive API shape; consumers must switch to the new key. **Data/schema:** none. **Acceptance:** Fyers-only mode renders index cards; `GET /api/subscriptions` returns `canonical_id` per index. **Rollback:** revert. `app/subscriptions/service.py` is modified — merge carefully.

### Stage 5 — **Restore last-applied F&O plan at startup** (D6; G3) ⚠️ **PROTECTED-CORE**
- **Dep:** Stage 2. **Boundary:** `core/persistence/modules/fno_config.py` (new field: persisted last-applied `keys_by_provider`), `app/subscriptions/service.py` (load on startup → `_derivatives_universe`), `app/server.py` lifespan. **Risks:** **high — touches persistence red-zone + startup composition.** **Data/schema:** NEW persistence field (schema change). **Acceptance:** server boots with the derivatives-universe owner populated; F&O contracts auto-subscribed; operator intent preserved (restore last-applied, not full auto-apply). **Rollback:** revert to current staged `fno_config`. **Approval required:** separate protected-core task (persistence + startup), per `AGENTS.md` FROZEN rules.

### Stage 6 — Catalogue-driven index discovery (D9; G4)
- **Dep:** Stage 1, Stage 2, Stage 4. **Boundary:** `app/market_indices.py` (add `discover_indices(catalog)`), `market/index_registry.py`, `IndicesWidget.tsx`. **Risks:** medium — resolver must handle all index symbols, not just 9. **Data/schema:** none. **Acceptance:** `discover_indices` returns all NSE_INDEX (268) + BSE_INDEX (149); SENSEX/BANKEX present; main strip = featured subset, full list searchable. **Rollback:** revert.

### Stage 7 — Fyers HSM index-tick delivery — **gated on U1** (D3)
- **Dep:** Stage 2; **hard prerequisite: U1 live HSM smoke test passes.** **Boundary:** `brokers/fyers/feed.py::_emit_tick` — **remove the `if`-prefix drop** so index ticks flow (do NOT keep it as a policy); the central selector (Stage 3) decides which index quote is authoritative. **Risks:** medium-high — *only safe after U1 proves the scaling is correct.* **Data/schema:** none. **Acceptance:** live HSM capture — NIFTY `if|` tick produces a correct LTP within 1 tick of NSE published value. **Rollback:** revert `feed.py` (it's committed, not in the modified list). **If U1 is inconclusive:** keep delivery *on* but label Fyers index quotes `stale`/`unreliable-scaled` and let the central selector prefer Upstox for indices — **never silently trust the Fyers number.**

### Stage 8 — `migrate_from_config` Fyers index categorization (D7)
- **Dep:** Stage 4. **Boundary:** `app/subscriptions/service.py::migrate_from_config` — recognize Fyers colon index keys → `index` category. **Risks:** low (one-time idempotent; marker in `source_state`). **Data/schema:** one-time data correction in `md_subscriptions`. **Acceptance:** a config carrying Fyers index keys produces `index` subscriptions; the two `('stock','NSE:*-INDEX')` rows are corrected. **Rollback:** revert (idempotent marker makes re-run safe).

### Stage 9 — Frontend request-storm fix (D10)
- **Dep:** none. **Boundary:** `frontend/src/features/fno/FnoWorkspaceView.tsx` (apply the documented stable-`mutate` fix). **Risks:** low (documented + tested in `docs/current.md` §2.3). **Data/schema:** none. **Acceptance:** `frontend/src/__tests__/FnoWorkspaceView.test.tsx` → `idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`. **Rollback:** revert.

### Stage 10 — Operational: catalogue sync (D1, *for the empty test DBs only*)
- **Dep:** Stage 1. **Boundary:** none (operational). **Risks:** low (transactional sync; failed download leaves prior catalogue intact). **Data/schema:** populates `instruments` in the target DB. **Acceptance:** `SELECT COUNT(*) FROM instruments > 0`; `FnoCoverageService.plan()` returns `desired > 0`. **Note:** `data/events.db` is already populated (142,932) — sync is only needed for `data_test/` + `test/data_test/` fixtures.

### Stage 11 (deferred, protected-core) — Canonical re-key (G1 Option A)
- **Dep:** Stages 2+3 proven stable. **Boundary:** `market/service.py` write path re-keyed to `(exchange, canonical_id)`. **Risks:** high (write-path + data migration, adjacent to FROZEN startup baseline). **Approval required:** separate protected-core task.

### Approval decisions (genuine forks, maintainer sign-off)
| # | Decision | Recommended |
|---|---|---|
| G1 | storage key | **Option B (read-through) now; Option A (re-key) later as protected-core** |
| G2 | Fyers index ticks | **Remove the drop (Stage 7) only AFTER U1 live HSM scaling passes; until then label Fyers index quotes unreliable and let the central selector prefer Upstox** |
| G3 | F&O at startup | **Restore last-applied (Stage 5, protected-core), not full auto-apply** |
| G4 | index strip scope | **Featured + F&O underlyings in main strip; full catalogue searchable** |

**Out-of-scope unless explicitly expanded:** BSE derivatives streaming beyond SENSEX/BANKEX option-chain-on-demand, MCX/NCD/BCD/COM segments, new third-party data providers, any order/trading functionality.

---

## 7. Required acceptance tests — all three provider configurations

**Fyers-only** (Upstox disabled, Fyers HSM `streaming`, valid Fyers session):
1. Index strip shows live NIFTY/BANKNIFTY **only if U1 passes**; otherwise Fyers index cards show `basis:"unavailable/stale"` — **never a fabricated `live` value**.
2. `GET /api/market/indices` → `provider:"fyers"`, `basis:"live"` for the featured indices (post-U1); `resolve_spot("NIFTY")` → Fyers quote.
3. F&O futures/ATM-options stream; `GET /api/fno/coverage` → `states` honest (non-streamed = `CAPACITY_LIMITED`/REST, not `UNCOVERED`); **no full-coverage claim**.
4. No Fyers code path in Upstox-only; and vice-versa.

**Upstox-only** (Fyers disabled, Upstox V3 `streaming`):
- Index strip live; `provider:"upstox"`, `basis:"live"`; F&O routed to upstox; SENSEX/BANKEX option chain on-demand. No Fyers trigger.

**Dual:**
- Both feeds `streaming`; `GET /api/market/quotes` carries both providers' observations; each index entry has `provider` + `canonical_id`; **no duplicate logical instrument** (converged after Stage 2/3). Selector picks freshest; the losing provider is still visible as a secondary observation.

**Failure/fallback:**
- Upstox `auth_required` → routing auto-falls to Fyers (if Fyers streaming); Fyers `auth_required` → auto-falls to Upstox; **both** `auth_required` → all indices `basis:"unavailable"` (never `live`).
- A provider that is `streaming` but quote-stale >5 min → routed contracts `STALE`, not `live`.
- Feed task killed → reconnects within backoff; no retry storm (`_next_backoff` exponential+jitter, both `brokers/*/feed.py`).

**Identity/freshness:**
- `resolve("NSE:NIFTY50-INDEX") == resolve("NSE_INDEX|Nifty 50") == resolve("Nifty 50")` → same canonical ID. `Quote.canonical_id` populated for both providers. `get_canonical_quote` returns one quote regardless of which provider's key is asked.
- No provider data → `basis:"unavailable"`; `source_provenance` ∈ {hsm, rest, tbt}.

**Coverage (after catalogue sync into the target DB):**
- `stock_underlyings(catalog, provider, today)` → current NSE F&O equity underlyings; `futures_index(catalog, "NSE")` → all active futures; `discover_indices` → 268 NSE + 149 BSE; SENSEX/BANKEX appear; BSE F&O (SENSEX/BANKEX options) on-demand.
- Unsupported instrument → `unavailable`, no fabrication; provider without capability → `UNCOVERED` + reason; expired contract (expiry < today) filtered at the boundary.

**Behavioral:**
- No polling storm (`FnoWorkspaceView` idle counts as in Stage 9); server CPU <10% with F&O + heatmap open.
- Limits: `resolve()` raises `SubscriptionLimitError` at 500/provider; `reconcile()` add/remove without restart; `ANALYTICS_CAP=400` enforced; broker budgets (fyers 4450 / upstox 1800 effective) respected by the central selector.
- **No manual "Select Provider" / "Add Instrument" / "Apply F&O" step in normal use** (auto discovery + auto restore; G3).

**Clean checkout / CI:**
- `git clone` + `pip install` + `python -m app.server` succeeds (no `ModuleNotFoundError` for `brokers/fyers/rest.py`); `pytest test/ -q` (record baseline); `cd frontend && bun run build`; `pytest test/test_architecture_boundaries.py -q` green; **no untracked Python file imported by `app/server.py`.**

**Negative:**
- Fyers-only, no valid Fyers session → `auth_required`, index strip `unavailable`, no fabricated quote.
- Stale-but-streaming provider → `STALE`/`PROVIDER_UNAVAILABLE`, never `live`.
- Unknown instrument → `unknown-underlying`, no invented data.

---

## 8. Unresolved critical uncertainties (settle before coding)

1. **U1 — Fyers HSM index LTP scaling (BLOCKING for Stage 7).** A captured NIFTY index snapshot gave ×3.65 error (multiplier=28/precision=0 → 87,205.71 vs true ~23,897). **Cannot be resolved by reading code.** Required: a **live HSM capture during NSE trading hours** of `if|nse_cm|Nifty 50`, cross-checked against NSE's published index, to re-derive the exact `multiplier`/`precision` semantics (and whether index topics use a different field map than `_INDEX_VAL`). Until it passes, Fyers index quotes must be treated as **unreliable** and the central selector must prefer Upstox for indices.
2. **U2 — Upstox combined-mode cap interaction** if/when the app mixes ltpc+greeks+full on one connection (2000/2000/1500 combined). Confirm current single-`default_mode` design keeps the 2000 (full) cap → 1800 effective.
3. **U3 — Provider day-boundary re-auth.** "Automatic subscription" is about *state*; Fyers daily OAuth+PIN and Upstox daily re-auth are **provider entitlement constraints**, not defects. Confirm the requirement tolerates a once-daily human login for each enabled broker (this is the only true manual step that remains, and it's outside code scope).
4. **U4 — Which `events.db` is the runtime DB per environment** (`data/` populated vs `data_test/` empty). Confirm per-environment `data_dir` so "empty catalogue" findings are scoped to the right database.

---

## What I did NOT do
- No source/test/config/DB/migration/lockfile/build changes. No `git add`/commit. No catalogue sync. No mutating API. No repo reorganization. No code written.
- Scratch inspection scripts (`_audit_ro_*.py`) were created read-only and **deleted**; `git status` shows no new untracked files from me.

**Bottom line:** Stages 1–4 + 10 are the low-risk path that makes Fyers-only and dual-provider *read* convergence work. Stage 7 is **blocked on U1** (live HSM scaling). Stage 5 is the **only persistence red-zone change** (last-applied F&O plan restore) and needs separate protected-core approval. Stages 8–9 are small, isolated fixes.

**Awaiting maintainer approval of G1–G4 + U1–U4 resolutions before any code changes.**
