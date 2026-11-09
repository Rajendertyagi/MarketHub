# MarketHub — Round 4: Final Architectural Contradictions (READ-ONLY Amendment)

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints. No `git add/commit/reset/clean/checkout/restore/stash/rebase`. All in-flight staged/unstaged/untracked work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Date:** 2026-10-18
**This document is an amendment to Round 3.** It resolves the four contradictions the maintainer flagged: (1) silently narrowed index scope, (2) the F&O persistence contradiction, (3) unvalidated Fyers REST index path, (4) plan-cap configuration + destructive rollbacks.

---

## 1. Corrected market scope and coverage gaps (no silent narrowing)

### 1.1 Three separate inventories (requested)

**Inventory A — Complete official NSE index universe + published data availability.**
- **Official count: 436+ Nifty indices** (NSE Indices Ltd, "over 436 indices… as of 31 Jul 2026", https://www.nseindia.com/static/nse-indices, checked 2026-10-18).
- **Published data availability is tiered, and NOT every index is live-streamed:**
  - **Live-streamed by NSE itself: ~139** (NSE live index watch / live-feed catalogue count).
  - **Factsheet / EOD / historical only:** the remainder (~297) — NSE publishes factsheets, monthly weightage, and EOD values for all 436, but **does not real-time-stream all of them**.
  - **Index funds / derivatives benchmarks:** 239 ETFs + 279 index funds track Nifty indices (official factsheet).
- **Status labels (used consistently below):** `DISCOVERED` (in a catalog), `LIVE-STREAMABLE` (provider pushes it over WS), `REST-QUERYABLE` (provider can fetch on demand), `FACTSHEET/EOD-ONLY` (published but not live), `UNAVAILABLE/UNVERIFIED` (no provider data or unconfirmed).
- **Explicitly: the official NSE universe is NOT all live-streamable.** Neither Fyers nor Upstox streams all 436; both stream a **subset** of the ~139 NSE-live indices. Coverage of the full 436 universe is therefore **catalogue + factsheet/EOD (REST), not real-time streaming** — and that is the truthful state to display, not a claim of full live coverage.

**Inventory B — NSE index instruments exposed by Fyers & Upstox, mapped to canonical IDs.**
From the populated `data/events.db` (the 142,932-row environment; **not** the active `data_test` runtime here):
| Provider | NSE_INDEX rows | BSE_INDEX rows | Native key shape |
|---|---|---|---|
| Fyers | 129 | 70 | `NSE:<SYMBOL>-INDEX` (e.g. `NSE:NIFTY50-INDEX`) |
| Upstox | 139 | 79 | `NSE_INDEX\|<Name>` (e.g. `NSE_INDEX\|Nifty 50`) |
- **Deduplication is evidence-based**, via `app/market_identity.py` normalized-symbol canonical IDs (`NSE:INDEX:NIFTY` ≡ both `NSE:NIFTY50-INDEX` and `NSE_INDEX|Nifty 50`; `BSE:INDEX:SENSEX` ≡ `BSE:SENSEX-INDEX` and `BSE_INDEX|SENSEX`). Different provider tokens are **not** assumed to be different instruments; the Fyers `_INDEX_DICT` (`brokers/fyers/feed.py:86-120`) is a curated alias table, not fuzzy matching.
- **Deliverable mapping report (offline, reproducible):** for each canonical NSE/BSE index, a row `canonical_id | fyers_key? | upstox_key? | fyers_stream? | upstox_stream? | fyers_entitled? | upstox_entitled? | status`. *Stream?* for Fyers indices is **`UNVERIFIED`** pending the HSM test (§3); *entitled?* depends on plan (§4). Missing = present in only one provider; deprecated/unavailable = catalog row present but no live/REST source.

**Inventory C — BSE SENSEX-related indices (proposed scope, clearly documented).**
- **Proposed SENSEX-related scope (documented, for maintainer sign-off):** `SENSEX`, `SENSEX 50`, `SENSEX NEXT 50`, `SENSEX NEXT 30`, `SENSEX SIXTY`, `BANKEX`, `BSE 100`, `BSE 200`, `BSE 500`, `BSE INDIA 150`, `BSE 1000`, `BHARAT 22`. (BSE live Index Watch + BSE Index Services factsheets, checked 2026-10-18.)
- Of these, **both providers expose SENSEX and BANKEX** (Fyers `BSE:SENSEX-INDEX`/`BSE:BANKEX-INDEX`; Upstox `BSE_INDEX|SENSEX`/`BSE_INDEX|BANKEX`). SENSEX F&O (futures + options) is present in `data/events.db` BSE_FO (upstox-dominant). **SENSEX 50 / NEXT 50 / NEXT 30 / SIXTY live data is not confirmed on either broker's streaming feed** — mark `UNVERIFIED`, display truthful availability, do **not** claim live.
- **Licensing/entitlement:** a symbol's presence in a public catalog does **not** grant data entitlement; both brokers gate index data on account plan/entitlement. BSE index data on these brokers is subject to the same plan caps as NSE. **No new data vendor is introduced** — full 436-NSE / full-BSE-coverage beyond the two brokers' entitlements is explicitly **out of scope** unless the maintainer approves a vendor.

### 1.2 "Keep the complete intended universe discoverable" — mechanism
- `discover_indices(catalog)` (Stage S8) returns **all** catalog `instrument_type='INDEX'` rows (NSE_INDEX ∪ BSE_INDEX), **not** the 9-item `MAJOR_INDICES`. The full intended universe stays discoverable/searchable.
- For each discovered index, the UI shows **truthful availability**: `LIVE-STREAMABLE` / `REST-QUERYABLE` / `FACTSHEET-ONLY` / `UNAVAILABLE`, derived from (provider rows in catalog) × (plan caps §4) × (stream-validity §3). An index with no live source is **rendered as unavailable/stale, never fabricated live.**
- **Unresolved scope decision (maintainer):** "all NSE indices" = (a) all 436 official (discover + factsheet/EOD, live where providers stream), vs (b) the 139 NSE-live subset only. **This report does not pick silently; both are supported by the same discovery + truthful-availability design.** Default: (a) discover-all, (b) live-subset marked.

---

## 2. F&O persistence contradiction — RESOLVED (no new field needed)

### 2.1 What already persists (inspected, not assumed)
`core/persistence/modules/fno_config.py` (schema v19) **already stores declarative intent** in a single row: `enabled`, `index_labels` (`[]` = all F&O indices, else explicit), `stock_mode` (`all`/`selected`) + `stock_symbols`, `preferred_provider`, `fallback_provider`, `expiry_policy` (`nearest`/`selected`) + `selected_expiry`. Its own docstring: *"the actual universe, indices and expiries are **always resolved dynamically** from the neutral registry + catalog."* `fno_coverage._config()` defaults mirror this.

**Therefore the desired F&O contract set is already fully reconstructable from durable state:**
```
desired_fno = resolve( fno_config(intent), catalogue, active_views,
                       enabled∧authenticated_providers, plan_caps )
```
The *derived* `keys_by_provider` is **ephemeral and redundant** — it is a pure function of (intent × catalogue × providers × caps). Storing a second copy (the last-applied plan) would be **duplicated semantics** that can drift from the recomputed one (catalogue sync or provider failover would make the stored plan stale). **The audit proves the new field is NOT required.**

### 2.2 The actual gap (small, and already half-built)
`_derivatives_universe` (in-memory owner, `service.py:92`) is **never re-applied at startup.** The fix is **not** a persistence change — it is: at startup, **recompute the desired F&O set from the existing durable `fno_config` + catalogue + registry**, inject it via the *existing* `set_derivatives_universe(...)`, and let the **existing** `reconcile()` (`service.py:758`, already called at lifespan `app/server.py:1220`) apply it. This reuses a frozen-boundary-safe path: it writes no new table, changes no schema, and touches startup **composition only by calling an existing service method** — not by altering the frozen auth/credential/session-restore baseline.

### 2.3 Precise startup sequence + dynamic triggers
1. **Boot:** `ensure_defaults()` + `migrate_from_config()` (existing, idempotent) → seed index subscriptions.
2. **F&O reconstruct:** `plan = FnoCoverageService.plan()` (reads `fno_config` + catalogue + registry + plan caps) → `subscriptions.set_derivatives_universe(plan.keys_by_provider)`.
3. **Reconcile:** existing `reconcile(feed_provider)` diffs desired vs live feeds, applies add/remove; a failed feed records `applied=False` and the durable preference stands (existing semantics, `service.py:764-766`).
4. **Expiry rollover:** `_choose_expiry` filters `expiry >= today`; on a new day the coordinator re-resolves the nearest/selected expiry and re-reconciles. No manual step.
5. **Provider failover / reconnect:** on feed-state change, recompute availability + route each contract to a still-available provider; losers marked `STALE`/`UNCOVERED`, never silently `live`.
6. **Catalogue change (sync):** epoch bump → recompute; expired contracts drop, new ones enter; reconciled automatically.
7. **Fyers-only ⇄ Upstox-only ⇄ dual:** recompute `enabled∧authenticated` provider set → re-route. No operator input.
- **No manual F&O apply** in normal operation. The `POST /api/fno/apply` route remains only as an explicit *override* (e.g. pick a specific `expiry`), not a required step.
- **If a schema/startup change were ever truly required:** the only candidate was the "last-applied plan" field — and **the audit proves it is not needed**, so **no persistence or protected-startup change is made or recommended.** Persist *declarative intent only* (already true).

---

## 3. Fyers-only index strategy — REST path validated in scope; live test dependency isolated

### 3.1 What the existing REST adapter can do (inspected `brokers/fyers/rest.py`)
- `FyersRest.quotes(access_token, symbols)` → **batch ≤50 symbols** (Standard) → `list[Quote]` via `quote_from_quotes_rest`. **Established by docs + code:** the endpoint, the batch size, and the envelope (`{"s":"ok","d":[…]}`) are documented. **NOT established:** that a *specific* NSE/BSE index symbol returns a correct, live LTP *at market hours* — **that requires a real authenticated market-hours test.** No live response was validated in this read-only round; **do not claim live correctness.**
- `FyersRest.depth`, `.history`, `.option_chain` (`options-chain-v3`), `.market_status` — documented endpoints; per-symbol/underlying REST.
- **Failure responses:** `FyersAuthError` (401/-16), `FyersRateLimitError` (429), `FyersRestError` (other). Standard REST caps: **5 rps / 50 rmin / 5,000/day.**
- **Which symbols are REST-queryable:** every catalog Fyers index row's *native* `NSE:<SYMBOL>-INDEX` / `BSE:<SYMBOL>-INDEX` symbol is queryable via `/data/quotes`/`/data/depth`; whether the account is *entitled* is plan-gated (§4). Fyers **HSM index streaming stays disabled/untrusted** (U1).

### 3.2 Bounded, market-hours-aware refresh (specification, not implementation)
- **Deduplication:** coalesce requests for the same index within its freshness window; one batched `/data/quotes` call per cycle (≤50 symbols) instead of N calls.
- **Rate limiting:** a shared **per-provider token bucket** (Fyers Standard: 5 rps, 50 rmin) used by *all* Fyers REST (index refresh + F&O option chains + history) so the tail work shares the 5,000/day pool; exponential backoff on 429 with `Retry-After` honored.
- **Market-hours awareness:** refresh only during NSE/BSE trading windows (per `/data/marketStatus`); outside hours the last close is served as `STALE`, not re-polled (saves quota, avoids meaningless churn).
- **Caching:** index REST quotes cached with a 60 s in-memory TTL (well inside the 5-min `STALE_MINUTES`); option chains cached single-flight by catalogue epoch + 300 s TTL (existing `futures_board`/`derivatives_universe` pattern).
- **Budget check (§3 below):** ~11 index instruments → **1 batched quotes call/60 s ≈ 1,440/day** + bounded option-chain polling ≈ 150–300/day → **~1,700/day, inside 5,000/day Standard with ~3× headroom**, and far below 5 rps.

### 3.3 Non-conflated status semantics (three independent axes)
| Axis | Values | Meaning |
|---|---|---|
| **Source** | `websocket` / `rest` | where the observation came from (identifiable; never masked) |
| **Freshness** | `fresh` (≤5 min) / `stale` / `unavailable` | age-based |
| **Validity** | `validated` / `rejected` | passed central data checks |
- A quote is **exposed as `live` only if `fresh ∧ validated ∧ (source=websocket OR source=rest)`**; a **fresh REST snapshot is a valid "live" source** but its `source:"rest"` label is retained. **Stale or rejected values are never represented as live.** Invalid data is **rejected by the central coordinator even if freshest.**
- **HSM index** remains `source=websocket` but classified **untrusted/unvalidated** until §3's market-hours HSM test passes; the REST path is the authoritative index source in Fyers-only mode. **Upstox is not a hidden mandatory provider** — selection is by `availability ∧ freshness ∧ validity ∧ entitlement`, across *all enabled* providers, tie-broken by preference order only.

### 3.4 Remaining live-test dependency
- **HSM index LTP scaling (U1)** — BLOCKING for any *streaming* Fyers index feature; **does NOT block the REST path.**
- **Fyers REST index "live correctness"** — requires one **authenticated market-hours spot check**: fetch `NSE:NIFTY50-INDEX` via `/data/quotes`, compare to NSE published NIFTY value, tolerance ≤0.05 pts. **Documented as required evidence; not claimed passed.**

---

## 4. Provider-capacity configuration (Standard default; Prime override centralized)

### 4.1 Fyers (Standard = safe default)
- **Default plan-cap = Standard = 100 data-socket symbols** (effective `90` after the app's `max(10%,50)` reserve). The official profile API exposes **no plan field**, so the system **plans for Standard unless explicitly told otherwise** — never assume Prime.
- **Centralized settings model (proposed, not yet coded):** a single `providers.capacity` config block:
  ```
  providers:
    fyers:
      data_socket_symbols: 100   # Standard default; change to 5000 for Prime
      plan_override: standard    # {standard|prime}; prime requires explicit opt-in
    upstox:
      data_socket_symbols: 2000  # 'full' mode individual cap (combined 1500 if multi-category)
  ```
  - All plan numbers live in **this one model**; adapters read the *effective* value via `registry.budget(provider)` — **no plan-specific numbers scattered across the codebase.** `brokers/fyers/capabilities.py` currently hardcodes `5000` (a Prime assumption) → **to be replaced by reading the centralized value.**
  - **WebUI exposure (proposed):** Settings → Providers → Fyers "Data-socket capacity" with a plain-language consequence note: *"Standard plan streams 100 symbols; raising this to 5,000 requires Fyers Prime. If your plan is Standard and MarketHub plans more than 100 Fyers symbols, the excess is reported as `CAPACITY_LIMITED` and NOT streamed."* Changing it is a user choice with explained consequences, not a hidden assumption.

### 4.2 Reconcile caps ↔ application limit ↔ truthful reporting
- Binding Fyers streaming budget = `min(plan_cap_effective, MAX_INSTRUMENTS_PER_PROVIDER)` → **Standard: 90**, Prime: 4,450 (still clamped by the app 500 unless the app cap is raised intentionally). The coordinator **routes by priority** (live futures > index > option strikes), **drops the overflow by priority**, and marks the dropped set `CAPACITY_LIMITED` — **never claims full live coverage.** Automatic routing requires **no manual instrument selection.**

### 4.3 Upstox REST quotas / entitlements (confirmed against official docs, 2026-10-18)
- Streaming: individual caps ltpc 5000 / option_greeks 3000 / **full 2000** / full_d30 50 (Plus); **combined 2000/2000/1500** when a connection spans multiple categories. `full_d30` + 5-connection access = **Upstox Plus** tier (entitlement).
- REST: option-greek ≤50 keys/request; full-market-quote ≤500; option chain per underlying; history V3 with OI. **Residual to confirm against the connected account:** the exact Upstox *account-tier* rate limits (not captured here) — flagged as U3, does not block the Fyers-only path.

---

## 5. Final identity + quote-selection architecture (transitional vs durable)

### 5.1 Is read-through a useful stage? — **decision: SKIP it as a standalone step**
- A pure read-through fallback (`get_quote_now` → identity-resolver on miss) creates **duplicate semantics**: two storage slots per instrument coexist while reads converge, and it is easy to leave in place, becoming permanent debt.
- **Better: implement the central coordinator directly (Section 5.2), and make "read-through" a *behavior of the coordinator*, not a separate transitional stage.** The coordinator *is* the canonical read path; there is no intermediate half-state. This removes scattered provider-key assumptions in one move and avoids the duplicate-semantics trap.
- **Removal criteria for any read-through, if it were introduced:** (a) all quote consumers read via `get_canonical_quote`; (b) no consumer references `instrument_token`/`upstox_key` as a join; (c) coordinator returns the freshest *validated* observation by canonical ID. Once met, the native-key lookup is read-only provenance, and the canonical re-key (5.2) subsumes and deletes it.

### 5.2 Durable target
- **Storage key = `(exchange, canonical_id)`; provider-native token = `Quote.provider_token` (provenance).** Single slot per instrument → true cross-provider convergence, one lookup.
- **Central coordinator** (new `providers` policy + `market/service.get_canonical_quote`): gather each provider's *validated* observation for a canonical ID → select by `enabled∧authenticated`, `freshness`, `data-validity`, `provider entitlement`, preference-order (tie-break only). **Reject invalid data even if freshest.** Adapters never suppress their own ticks as selection policy; central *validity* gates are the only suppression.
- **Backend + frontend** join on `canonical_id`; provider keys are diagnostics. `preferences().indices[].key` becomes the canonical ID (`fyers_symbol`/`upstox_key` retained as provenance fields).
- **Migration/rollback:** canonical keying is the one irreversible step; the coordinator is additive/reversible. Native-key reads remain as provenance until the re-key lands.

---

## 6. Reconciled implementation sequence — safe, NON-DESTRUCTIVE rollback

> **Rollback rule (applies to every stage):** never use `git reset`, `git clean`, `checkout`, `restore`, `stash`, or `rebase`. Rollback = **selective, file-level reversal of only the lines this stage added**, after first identifying and safeguarding any *pre-existing* modifications in the same file. Pre-existing work is preserved; unrelated in-flight changes are untouched. "Revert file X to HEAD" is **forbidden** when X already has unrelated in-flight edits; instead, reverse the specific hunk.

| # | Stage | Module boundary (exact) | Deps | Data/schema impact | Safe rollback (non-destructive) |
|---|---|---|---|---|---|
| S1 | Commit untracked `brokers/fyers/rest.py` | add 1 file (already written; imported at `app/server.py:695`) | — | none | *Only* `git reset --soft HEAD~1` is safe **here** (the commit contains just this file; nothing else touched) — or leave staged. **No `--hard`/`clean`.** |
| S2 | **Plan-aware Fyers cap (Standard default)** | `brokers/fyers/capabilities.py` reads `providers.capacity` (replace hardcoded 5000); add `providers/capacity.py` settings model | S1 | config (additive) | reverse the two files' added lines; existing `MODE_KEY_LIMITS` consumers fall back safely |
| S3 | **Centralized capacity config + WebUI** | `providers/` capacity model + Settings UI (explained consequence) | S2 | config | revert UI + model; Standard default already safe |
| S4 | **Central quote coordinator + validity + freshness** | new `providers` policy + `market/service.get_canonical_quote` + `fno_coverage._available` (add quote-age) | S2 | none | revert the 3 modules; native-key reads still work (no data migration done yet) |
| S5 | `preferences()` + frontend join on canonical ID | `app/subscriptions/service.py::preferences` + `IndicesWidget.tsx` + `types.ts` + `WatchlistTable.tsx` | S4 | none | selective hunk reversal per file (these files have in-flight edits — **do not revert whole files**) |
| S6 | **Automatic F&O reconstruct-at-startup (NO new field)** | `app/server.py` lifespan calls existing `FnoCoverageService.plan()` → `set_derivatives_universe()` → existing `reconcile()` | S4 | **none (reuses durable `fno_config`)** | revert the added lifespan lines only; `fno_config` and `reconcile` untouched |
| S7 | Catalogue-driven index discovery (remove hardcoded 9) | `app/market_indices.py::discover_indices` + `market/index_registry.py` + `IndicesWidget.tsx` | S1, S4, S5 | none | selective hunk reversal; `MAJOR_INDICES` retained as featured subset |
| S8 | **Fyers-only index via REST** (fallback source) | `brokers/fyers/rest.py::quotes` (exists) + coordinator index source + bounded poller (≤5 rps, 5-min, market-hours, shared token-bucket) | S4 | none | revert poller + coordinator index-source lines; HSM guard stays untouched |
| S9 | **HSM index scaling market-hours test** (BLOCKING for HSM index) | test harness only (no prod code) | S8 | none | n/a (evidence artifact) |
| S10 | `migrate_from_config` Fyers index categorization | `app/subscriptions/service.py::migrate_from_config` | S5 | one-time data correction (idempotent marker) | revert (marker makes re-run safe); idempotent |
| S11 | Frontend request-storm fix | `frontend/src/features/fno/FnoWorkspaceView.tsx` | — | none | selective hunk reversal (file may have in-flight edits) |
| S12 | Operational: catalogue sync (empty env only) | API `POST /api/instruments/sync` | S1 | populates `instruments` | transactional (failed sync leaves prior catalog); not run in read-only round |
| S13 | **Canonical re-key (Option A)** ⚠️ *deferred, protected-core* | `market/service.py` write path → `(exchange, canonical_id)` | S4 proven | data migration | **separate protected-core approval; not part of this plan's ordinary coding gate** |

**Critical path for safe Fyers-only operation:** S1 → S2/S3 (plan-aware Standard cap) → S4 (coordinator) → S5 → **S8** (REST index, no HSM dependency) → S6 (auto-restore, no new field). S9 gates HSM-index. S13 is protected-core.

### 6.1 Frozen-boundary check before recommending persistence/startup changes
- Inspected `AGENTS.md` FROZEN rules + `app/server.py` lifespan: the **only** startup change in this plan (S6) is *calling an existing service method* (`plan()` → `set_derivatives_universe()` → `reconcile()`) within the lifespan — it adds no new persistence table, changes no schema, and does **not** touch credential storage, token/session persistence, broker login, or session restoration. **No new persistence field is introduced** (Section 2). S13 is the sole protected-core item and is **deferred**, not included in the ordinary coding gate.

---

## 7. Unresolved critical issues (if any)

1. **U1 — Fyers HSM index LTP scaling.** Still unproven; **blocks only HSM-index streaming (S9-gated), not the REST path (S8).** Market-hours evidence required (§3.4).
2. **U2 — Fyers account plan (Standard vs Prime).** No documented API field exposes it. **Resolved by design** in §4: **Standard (100/90) is the safe default; Prime is an explicit, explained opt-in via centralized `providers.capacity`.** No residual blocker for *planning*; a residual remains only for *runtime auto-detection* (accept the default+override model).
3. **U3 — Upstox account tier / REST rate limits.** Not captured; **residual to confirm against the connected account** before relying on Upstox REST on-demand at scale. Does not block Fyers-only.
4. **U4 (settled):** active runtime DB here is `data_test` (empty) per `config.json data_dir`; F&O/index live behavior in *this* tree is blocked on S12 (catalogue sync), an operational step **not run** in read-only mode.
5. **Scope decision (maintainer):** "all NSE indices" = full 436 (discover-all + factsheet/EOD, live where streamed) vs 139-live subset. **Both supported by the same design; maintainer picks the default display.** Also: BSE SENSEX-related scope (Inventory C list) needs confirmation.
6. **G1 identity fork:** confirmed **skip standalone read-through; implement the coordinator (5.2); defer canonical re-key (S13) as protected-core.** Needs maintainer sign-off on deferring S13.
7. **Licensing/entitlement:** the *delivered* index set per plan (Standard 100-slot) must be confirmed against the connected account; catalog membership ≠ entitlement.

---

## 8. Final status

**READY FOR MAINTAINER REVIEW — of the *design and plan*.** The four contradictions are resolved and the proposal is now internally consistent:

1. **Corrected scope:** full NSE universe (436, discover-all; ~139 live), BSE SENSEX-family (Inventory C, documented), provider-mapped with canonical dedup; truthful availability for anything not live; **no silent narrowing; no new vendor.**
2. **Validated capacities/budgets:** Fyers **Standard default (100/90) with centralized Prime override**; Upstox full=2000 (effective 1800); Fyers-only REST index refresh ~1,700 calls/day within 5,000/day Standard, shared token-bucket ≤5 rps.
3. **Fyers-only index strategy:** **REST `/data/quotes` (batched, market-hours, cached, shared rate limit), non-conflated source/freshness/validity status; HSM index untrusted (U1-gated); Upstox not a hidden mandatory provider.**
4. **Automatic subscription/restoration:** **reconstruct desired F&O from existing durable `fno_config` + catalogue + registry + caps; re-apply via the existing `reconcile()` at startup; NO new DB field, NO manual apply.**
5. **Identity/quote-selection:** **skip standalone read-through; central coordinator selecting canonical quotes, rejecting invalid data; canonical re-key deferred as protected-core (S13).**
6. **Sequence:** S1–S13 reconciled with **safe, file-level, non-destructive rollback** (no `git reset/clean/checkout/restore/stash/rebase` for in-flight files).
7. **Unresolved:** U1 (HSM, non-blocking for REST), U3 (Upstox tier, residual), scope-default + G1 deferral decisions.

**Coding remains NOT authorized** until the maintainer explicitly approves: (a) the scope default (full-436 vs 139-live) and BSE SENSEX scope, (b) the Standard-default + Prime-override capacity model, (c) deferring the canonical re-key (S13) as protected-core, and (d) implementation of S1–S12. **Stop.** No stage is implemented; no files, DB, config, or build artifacts modified; all in-flight work preserved.
