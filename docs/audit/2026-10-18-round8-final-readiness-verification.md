# MarketHub — Round 8: Final Readiness Verification (READ-ONLY)

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints, no authentication changes, no credential output, no feed termination, no DB mutation. All in-flight work preserved.
**Date:** 2026-10-18 · **Repo:** `fno-provider-agnostic` @ `3f4b8d9`
**Standing rule honored:** no coding while a *material architectural* uncertainty is open; live tests are gated, never guessed.

**Live-validation availability (mandated §5):** no authenticated Fyers/Upstox session is reachable from this read-only environment, no running app/DB was disturbed, and NSE market-hours data is **not** captured here. Therefore every live-endpoint claim below is explicitly marked **BLOCKED — LIVE EVIDENCE REQUIRED**; **no live result is fabricated or assumed.** No new data source was introduced.

---

## 1. Corrected capacity math — verified against the actual code

### 1.1 `effective_budget` (implementation-confirmed)
`providers/capabilities.py:44-51`:
```
RESERVE_FRACTION=0.10; RESERVE_FLOOR=50
effective_budget(cap) = cap - max(int(cap*0.10), 50)
```
| Input | reserve | **result** | matches spec? |
|---|---|---|---|
| 100 | 50 | **50** | ✅ Fyers Standard |
| 5000 | 500 | **4500** | ✅ Fyers Prime |
| 2000 | 200 | **1800** | ✅ Upstox full |
| 500 | 50 | **450** | (app cap input, informational) |

**Conclusion:** the reported values (50 / 4500 / 1800) **exactly match the implementation.** Any residual "90/4450" wording in earlier rounds was an arithmetic slip in the *prose*, **not** in code — there is **no conflicting number in code or tests.** Confirmed: **no test pins `effective_budget`** (`Get-ChildItem test | Select-String effective_budget` → 0 matches) — see §8 (a gap to close with tests, not a conflicting assumption).

### 1.2 Every caller / config default
- `providers/registry.py:96` `budget()` → `effective_budget(p.capacity)`; `:100` `mode_budget()` → `effective_budget(p.mode_limit(mode))`.
- `fno_coverage.py:290,293,425,467` read `reg.budget(pid)` for the routing cap.
- **Fyers `MODE_KEY_LIMITS={"full":5000}`** (`brokers/fyers/capabilities.py:27`) → budget 4500 ⇒ **assumes Prime.** **No other capacity config exists** in the tree (no `providers.capacity` block yet).
- **The app cap `MAX_INSTRUMENTS_PER_PROVIDER=500`** (`subscriptions/service.py:30`) is applied **separately** (`raise` when a provider's resolved set > 500, `service.py:721`). It is **not** currently folded into `effective_budget` as a `min()`.

### 1.3 The one authoritative policy to adopt (future S2, not done now)
```
final_streaming_budget(provider) = min(effective_budget(plan_cap(provider)), 500)
  Fyers Standard (100)  → min(50, 500)   = 50
  Fyers Prime   (5000)  → min(4500,500)  = 500
  Upstox full   (2000)  → min(1800,500)  = 500
```
- **Safe default = Fyers Standard (budget 50)** unless a supported interface proves Prime. **No documented interface exposes the plan** (Fyers `/profile` returns name/email only) → **Standard is the safe default**; a **centralized, explicitly-explained Prime override** belongs in one config block (not scattered into `brokers/fyers/capabilities.py`), surfaced in the WebUI with a consequence note.
- **No conflicting numbers remain in code.** The only fix is (a) make the Fyers cap config-driven (Standard default), and (b) add the `min(...,500)` + a small test suite pinning the table in §1.1.

### 1.4 Upstox REST quotas — per-endpoint, verified by the actual calls
MarketHub calls (`app/market_data.py`): `/v3/market-quote/quotes`, `/v3/market-quote/option-greek`, `/v3/historical-candle` + `/intraday`, `/v2/option/chain`, `/v2/market/{oi,change-oi,max-pain,pcr}`, `/v2/news`, `/v2/market/{status,timings,holidays}`.
- **Provider-enforced (official, 2026-10-18):** Standard APIs **50 rps / 500 rmin / 2,000 per 30-min, per API + per user** (Upstox rate-limit page). This is a *broker* quota, **not** universal across every endpoint type (order-placement and Payout have their own envelopes).
- **Distinguish from app-wide throttle:** MarketHub has **no application-wide REST throttle today** (only the feed backoff in `brokers/upstox/feed.py`). The plan must add a **separate, documented, app-level token bucket** (per endpoint family) on top of the broker quota — **never conflated** with the 50/500/2000 broker numbers. **Residual:** exact per-endpoint account-tier limits require an account-specific check → **BLOCKED — LIVE EVIDENCE** (U3).

---

## 2. Upstox previous-OI — fully reconciled (N1, from R7)

### 2.1 Verified facts
- **Endpoint MarketHub uses for full quotes:** `app/market_data.py` does **not** currently call `/v3/market-quote/quotes` (Full Market Quotes V3) for the live quote path — live quotes come from the **V3 WebSocket `full` mode** (`quote_fields_from_ws_full`, `upstox.py:285`). The **REST full-quote** normalizer `quote_from_rest` (`upstox.py:131`) maps `oi→open_interest` but **does not map `previous_oi`**. The **official V3 REST response does include `previous_oi`** (F&O) [D Full Market Quotes V3, 2026-10-18].
- **Where previous-OI IS consumed today:** (a) **option-chain** `market_data.prev_oi` → `OptionContractData.previous_oi` + derived `oi_change` (`upstox.py:692-694`); (b) **Fyers** option-chain `prev_oi`→`previous_oi`, `oich`→`oi_change` (`fyers.py:498-499`) — **this corrects the R6/R7 "Fyers has no previous-OI" claim: Fyers option-chain *does* carry `prev_oi`/`oich`**; (c) the **live WS `full` feed does NOT carry previous-OI** (`MarketFullFeed` has `oi` but no `previous_oi` in the proto) — so the WS path legitimately falls to persisted OI.

### 2.2 Correct design (no overwrite of newer data with older REST)
The precedence is already correct in `market/oi.py:32-58` `oi_change_and_pct`:
```
provider-supplied previous_oi  (quote.previous_oi)   ← basis "provider"
  → else persisted prior-session OI                  ← basis "persisted"
  → else unavailable                                  ← basis "unavailable"  (never fabricated)
```
and `MarketService._merge_quote` is **presence-exact** (`market/service.py:236-272`): a reported field *replaces*; a `None` *inside* a snapshot *preserves*; an all-`None` report *clears*. So a REST full-quote that **populates `previous_oi`** will **win** (provider basis) over the older persisted value, and will **never be overwritten by an older REST snapshot** because the staleness gate (`_orders`) rejects out-of-order writes. **The design is sound; the only defect is the *mapping gap* in `quote_from_rest`.**

### 2.3 Exact future change + tests (not implemented this round)
- **Change:** in `market/normalize/upstox.py::quote_from_rest`, add `set_reported(fields, entry, "previous_oi", "previous_oi", to_float)` (V3 full-quote field), mirroring the `oi` mapping at line 145. This makes a REST full-quote feed `Quote.previous_oi` directly (provider basis), instead of forcing the persisted fallback.
- **Regression tests (future S3):** (a) `quote_from_rest` on a V3 payload with `previous_oi` set → `Quote.previous_oi` populated, `oi_change`/`oi_change_pct` derived; (b) precedence: provider `previous_oi` beats persisted; (c) a stale REST snapshot (older `received_ts`) does **not** overwrite a newer WS quote (staleness gate); (d) Fyers option-chain `prev_oi`/`oich` already correct (add assertion). **Do not implement now.**

---

## 3. Final provider-independent quote architecture

**Roles (fixed, no hidden Upstox-first):**
- **Provider-native observations** — each adapter delivers its last *validated* observation, keyed by **canonical ID**, carrying `provider`, `provider_token` (native), `source` (`ws`/`rest`), `received_ts`/`exchange_ts`, `provenance`, `validity`. **Adapters never discard their own ticks for cross-provider selection** (the single exception, the Fyers HSM index guard, is justified by *data validity* — unverified scaling — not by provider preference, and stays until a live capture proves scaling).
- **Canonical instrument identity** — one ID per real instrument/contract (`market_identity.py`); Fyers `NSE:NIFTY50-INDEX` ≡ Upstox `NSE_INDEX|Nifty 50` ≡ `NSE:INDEX:NIFTY`.
- **Centrally selected quote** — the **only** thing consumers see. Chosen by `enabled∧authenticated` ∧ `validity` ∧ `freshness` ∧ `entitlement/plan-cap`; **preference order is a tie-breaker only.**
- **Exposed quote contract:** value + `canonical_id` + `provider` + `provider_token` + `source` + `received_ts` + `basis` (`live`/`stale`/`unavailable`) + `validity` (`validated`/`rejected`). **Stale/rejected is never `live`.**
- **All consumers** (dashboard index spot, watchlists, futures heatmap, option-chain spot/ATM, F&O workspace, OI analytics, market-intel, SSE, REST lookup) join on the **canonical ID** — no key-format heuristics, no component-specific identity repair.

**Transitional vs final (explicit, per §4):**
- **Transitional (safe, additive):** a central **coordinator** (`providers` policy + `market/service.get_canonical_quote`) reads the **existing provider-keyed `_quotes` store** and *projects* one canonical quote (gather slots for the canonical ID across providers, apply validity/freshness/entitlement). **No storage re-key, no data migration** → it can safely run on the current provider-keyed storage.
- **Final:** write-path re-key of `_quotes` to `(exchange, canonical_id)` (single slot per instrument, clean cross-provider merge). **This is a protected-core write-path + migration change → separate approval stage, NOT part of the ordinary gate.**
- **Removal criteria for the transitional coordinator-as-projection:** (1) all consumers read via `get_canonical_quote`; (2) no consumer references `instrument_token`/`upstox_key` as a join; (3) coordinator enforces validity+freshness+entitlement. Met ⇒ the projection is the selected-quote path; the re-key (final) then subsumes it and native-key reads become provenance-only.

---

## 4. Provider evidence — in-scope behaviours (source vs test vs docs vs live)

> **None of the [R] rows below were captured this round (no authenticated session; market not confirmed open; DB/app undisturbed). They are BLOCKED, not assumed.**

| Behaviour | Fyers | Upstox | Evidence available now | Live test required (BLOCKED) |
|---|---|---|---|---|
| Index spot (6 NSE + SENSEX) | HSM index **untrusted** → REST `/data/quotes` | WS `indexFF` (`full`) | [C] path exists; [C] HSM guard | **R:** REST index LTP vs NSE/BSE published (≤0.05-pt, market-hours); **R:** Upstox `indexFF` delivers SENSEX |
| Stock futures/options | HSM `sf|` [C] | WS `full` ltpc/oi/iv/greeks [C] | [C] | **R:** confirm live fields populate; SENSEX BSE options delivery |
| Major NSE index fut/opt | HSM futures [C] + options **REST** (`options-chain-v3`, untracked `rest.py`) | WS + `/v2/option/chain` [C] | [C] | **R:** Fyers option-chain REST live (blocked on commit, N4) |
| BSE SENSEX fut/opt | [A] | WS + SENSEX chain REST [C] | [C] | **R:** SENSEX options live (Upstox authoritative) |
| Previous OI / OI change | **Depth** `pdoi`/`oipercent` [C `fyers.py:258,263,265`]; option-chain `prev_oi`/`oich` [C] | **V3 REST `previous_oi` (mapping gap N1)**; option-chain `prev_oi` [C]; WS no prev-OI [C] | [C] (gap identified, fix specified §2.3) | **R:** V3 full-quote returns `previous_oi` for an in-scope F&O contract |
| Greeks / IV | **X (broker limit)** — Fyers exposes no greeks/IV endpoint [D] | WS `optionGreeks`/`iv` + REST `/v3/market-quote/greeks` [C] | [C] | **R:** Upstox greeks live; Fyers confirmed-absent |
| Option chain + ATM spot | REST [C] | REST [C] | [C] | **R:** both chains live + ATM snap |
| Futures heatmap / OI analytics | heatmap ✓; **max-pain/PCR unavailable (N11)** | `/v2/market/{max-pain,pcr,oi}` [C] | [C] | **R:** Upstox OI analytics live |
| REST / SSE / frontend consumers | native-key join (**N8 defect**) | same | [C] | [C] fix is S5 (canonical join) |
| Auth expiry / reconnect / stale / fallback | typed 401, backoff [C] | typed 401, re-authorize+resubscribe [C] | [C] | **R:** kill one feed → other supplies; both down → `unavailable` |
| Provider capacity exceeded | **Standard 50** (§1.3) | 500 | [C] | [C] (test-pinned, S2) |
| Startup / catalog unavailable | `desired:0` until sync (N12) | same | [C] | **R:** post-sync discovery |

**FYERS HSM index scaling + REST index correctness — exact market-hours tests (BLOCKED):**
1. **HSM scaling:** during NSE hours subscribe `if|nse_cm|Nifty 50`; capture `snapshot`(data_type 83) raw `ltp`,`multiplier`,`precision`,`exch_feed_time`; compute `ltp/(10**precision*multiplier)`; compare to NSE published NIFTY **and** to Upstox `indexFF` (independent reference), **3 snapshots, tolerance ≤0.05 index points**, record timestamps + source. **HSM is undocumented in public Fyers API docs [D] — cannot be closed by inspection.**
2. **REST index correctness:** `GET /data/quotes?symbols=NSE:NIFTY50-INDEX,BSE:SENSEX-INDEX`; record LTP/OHLC + `received_ts`; compare to NSE/BSE published index at the same minute, **≤0.05-pt**; record source + API-budget cost (1 batched call vs ~14 unbatched). **Not claimed passed.**
3. **Upstox full-mode fields:** capture one `full`-mode frame for an in-scope F&O contract; confirm `ltpc`,`marketLevel`(D5),`marketOHLC`(day),`oi`,`iv`,`optionGreeks`,`atp/vtt/tbq/tsq` actually populate; confirm **previous-OI is NOT on the WS wire** (so REST `previous_oi` is the correct source, §2). **Documented schema alone does not prove end-to-end delivery [R].**

---

## 5. Fyers-only feature limitations — truth (provider vs integration)

| Feature (Fyers-only) | Cause | Truthful user-visible state | Maintainer decision? |
|---|---|---|---|
| Option Greeks / IV | **Genuine Fyers broker limit** (no greeks/IV data endpoint; option-chain `optionsChain` has no greeks) [D+C] | F&O workspace greeks/IV panels show **"unavailable on Fyers"**; never fabricated; **do not borrow Upstox data** when Upstox is disabled | No (honest state is correct) |
| OI analytics (max-pain, PCR) | **Genuine broker limit** — Fyers has no max-pain/PCR/OI-analytics API; `market_data.py:235-289` raise "fyers not yet implemented" [C] | OI-analytics features **unavailable in Fyers-only**; Fyers *does* supply OI/prev-OI/OI-change via **Depth** (so OI buildup/basis works) [C `fyers.py:258-278`] | Optional: build a Fyers OI-analytics path from Depth data (new work → **needs approval**, §6 rule "no new analytics without approval") |
| OI (current) | Available via HSM tick **and** Depth REST [C] | normal | No |
| Index spot | HSM untrusted (U1) → **REST `/data/quotes`** (§4 test 2) | `live` once REST validated; `unavailable` until then | No |

**No value is invented, no Upstox data is silently borrowed in Fyers-only mode, and no parity is claimed that does not exist.** OI-analytics-in-Fyers is the **one item that may warrant a maintainer decision** (accept "unavailable", or approve a new Depth-based Fyers OI analytics build).

---

## 6. Automatic F&O subscriptions — verified design (no manual apply)

- **Durable intent already exists:** `fno_config` (`core/persistence/modules/fno_config.py:5-7`) persists *declarative* selections (`index_labels`, `stock_mode`/`stock_symbols`, `expiry_policy`, `preferred/fallback_provider`) and states the universe is "always resolved dynamically." **No new persisted plan is needed** (resolves the R3/R5/R7 persistence contradiction).
- **The only defect (N9):** `_derivatives_universe` (`subscriptions/service.py:92`) is in-memory and set **only** by the manual `POST /api/fno/apply` (`fno_coverage_routes.py:112`); the lifespan `reconcile` (`server.py:1220`) runs but the owner is empty at boot.
- **Automatic reconstruction (no new schema, no protected-startup change):** at startup compute `plan = FnoCoverageService.plan()` (reads `fno_config` × catalogue × registry × plan-cap (§1.3)) → `subscriptions.set_derivatives_universe(plan.keys_by_provider)` → existing `reconcile(feed_provider)`. This is **~3 added lifespan lines calling existing methods** — it does **not** alter the frozen auth/credential/session-restore baseline (that stays read-only).
- **Automatic triggers, all without operator input:** startup/restart (above) · active-view change (`set_active_view`) · provider reconnect/failover (recompute availability, re-route; losers `STALE`/`UNCOVERED`) · expiry rollover (`_choose_expiry` filters `>= today`; re-select on a new day) · catalogue epoch bump (invalidate `futures_index`/`fno_underlying_rows` caches, re-derive) · Fyers-only⇄Upstox-only⇄dual (recompute `enabled∧authenticated`, re-route) · **capacity exhaustion** (route by priority live-futures > index-spot > option-strikes; drop overflow by priority; mark `CAPACITY_LIMITED`; push the tail to REST) · **unsupported fields** (per §5 truthful states).
- **Recomputation, not duplication:** desired state is always `f(intent, catalogue, providers, caps)`; the derived `keys_by_provider` is ephemeral and re-derived — **never a second stored copy** (which would drift on catalogue/provider change).
- **Catalogue initialization (the one operational step, NOT performed in this audit):** run `POST /api/instruments/sync` against the *intended runtime* data dir so `instruments` is populated. The active `data_test/events.db` is **empty and is deliberately preserved untouched**; the populated `data/events.db` (142,932 rows) belongs to a separate environment. This sync is a **future approved operational step** (§7), not part of read-only review.

---

## 7. Repository-preservation policy — corrected (removes the `git reset` contradiction)

**The R7 "S1 rollback = `git reset --soft HEAD~1`" recommendation is WITHDRAWN and removed entirely** (§8 rule). It conflicted with this round's own prohibition on history manipulation.

**Corrected rollback rule for every future stage:**
- Rollback reverses **only** the changes *that stage introduced*, **after first identifying and protecting all pre-existing staged/unstaged/untracked work** in any affected file.
- **No whole-file revert** on a file that contains unrelated in-flight edits (selective hunk reversal only).
- **No `git reset` (any form), `git clean`, `git checkout`, `git restore`, `git stash`, or rebase** is ever recommended or executed.
- For the **additive new-file** stage (commit `brokers/fyers/rest.py`): the safe revert is **leaving the file staged/untracked and simply not committing it** — not any history operation. If a commit was already made, undo by **authoring a reverse commit / `git rm --cached` is still a mutation and is OUT OF SCOPE** — instead, the new-file stage is *additive-only* (it adds a file; removing it later is a forward change, never a history rewrite).

**Recorded pre-inspection state (§8):** `fno-provider-agnostic` @ `3f4b8d9`; 208 staged-new / 29 modified / 268 untracked. No files staged/committed/altered/reorganized; no destructive git; no sync; no DB mutation.

---

## 8. Stages (exact boundaries; dependency-ordered; non-destructive rollback; no coding)

> Rollback for each = *selective reversal of the stage's own added lines after safeguarding in-flight edits; never whole-file revert; never `git reset`.*

| # | Stage | Exact module boundary | Deps | Data/schema | Risk | Non-destructive rollback |
|---|---|---|---|---|---|---|
| S1 | Make `brokers/fyers/rest.py` available (N4) | **additive:** leave the file in the tree (staged); optionally `git add` as the *only* content of a commit (additive). No history op. | — | none | none | forward-only (a file added; removal is a later forward change, never a reset) |
| S2 | **Centralized plan-aware capacity** (§1.3) | new `providers/capacity.py` (`fyers.plan_cap:100 default / 5000 Prime-override`, `upstox: full`); `registry.budget()` returns `min(effective_budget(plan_cap), 500)`; **replace** hardcoded `fyers/capabilities.py:27` 5000 with the config value; **add tests** pinning §1.1 table + the `min()` | S1 | config (additive) + new tests | medium | revert the new config file, the one `capabilities.py` line, and the new tests; consumers fall back to current 4500 |
| S3 | **Upstox V3 `previous_oi` mapping** (§2.3) | `market/normalize/upstox.py::quote_from_rest`: add `previous_oi` mapping + regression tests (precedence + staleness) | — | none | low (additive normalizer field) | revert the added `set_reported` line + tests |
| S4 | **Central quote coordinator + validity + freshness** (N7,N8,N10) | new `providers` selection policy + `market/service.get_canonical_quote` (**additive**, reads the existing provider-keyed store) + `fno_coverage._available` (add quote-age gate) + endpoint-specific rate buckets | S2,S3 | **none** (transitional projection, no re-key) | medium-high | revert the 3 additions; native-key reads still work |
| S5 | **Canonical join in `preferences()` + all frontend consumers** (N8) | `subscriptions/service.py::preferences` (`.key`→canonical, native as provenance) + `IndicesWidget.tsx`,`types.ts`,`WatchlistTable.tsx` + heatmap/SSE/option-chain consumers switched to the canonical contract | S4 | none | medium (additive API shape) | selective hunk reversal per in-flight file |
| S6 | **Auto-restore F&O at startup, NO new field** (N9, §6) | `app/server.py` lifespan: `plan()`→`set_derivatives_universe()`→existing `reconcile()` (~3 lines at `:1220`) | S4 | **none** | low-medium (calls existing methods; frozen auth/session baseline untouched) | revert added lifespan lines only |
| S7 | **Fyers-only index via REST** (§4 test 2, §8) | `fyers/rest.py::quotes` (exists) + coordinator index source + bounded market-hours poller (≤5 rps, 60 s, shared bucket, 5-min freshness, dedup) | S4 | none | medium | revert poller + index-source lines; HSM guard untouched |
| S8 | **Market-hours validation tests** (U1–U4) | test harness only: Fyers REST index, Fyers HSM scaling, Upstox `full` field capture, dual failover | S7 | none (evidence) | n/a | n/a — **do not claim passed** |
| S9 | **Catalogue initialization (operational, approved)** | `POST /api/instruments/sync` against the *intended* runtime data dir | S1 | populates `instruments` (target env; `data_test` untouched) | low (transactional; failed sync preserves prior catalog) | idempotent/transactional; **not run in this review** |
| S10 | `migrate_from_config` Fyers index categorization (N13) | `subscriptions/service.py::migrate_from_config` | S5 | one-time (idempotent marker) | low | revert (marker re-run safe) |
| S11 | `fno/view` storm regression test (N14) | add `frontend/src/__tests__/FnoWorkspaceView.test.tsx` | — | none (new test) | low | delete the new test file |
| S12 | Fyers OI-analytics gap (N11, §5) | **document** as broker limitation (Fyers-only = unavailable) **or** (only with explicit approval) build a Depth-based Fyers OI path | S4 | none | medium | document-only: no rollback |
| S13 | **Canonical write-path re-key** (final) ⚠ protected-core | `market/service.py` `_quotes` → `(exchange, canonical_id)` + migration | S4 proven | data migration | high | **separate protected-core approval; not in the ordinary gate** |

**Critical path (safe all-three-config operation):** S1 → S2 (correct caps) → S3 (prev-oi) → S4 (additive coordinator) → S5 (canonical join) → S6 (auto-restore, no new field) → S7 (Fyers REST index) → S8 (live evidence, BLOCKED-gated) → S9 (sync). S12 documents the Fyers limitation (approval-gated if building). **S13 deferred as protected-core.**

**Gating:** **S7's *trustworthiness* and S8 are blocked on the U1–U4 live tests.** Stages S1–S6 may proceed on code evidence; the *index-live* acceptance is explicitly held to S8 passing. **No stage is declared validated until its tests pass.**

---

## 9. Remaining critical uncertainties — exact evidence to close each

| # | Uncertainty | Evidence needed | Blocks |
|---|---|---|---|
| U1 | Fyers HSM index LTP scaling (undocumented HSM [D]) | market-hours `if\|nse_cm\|Nifty 50` snapshot: raw `ltp`,`multiplier`,`precision`,`exch_feed_time` vs NSE published + Upstox `indexFF`, 3 snapshots, ≤0.05-pt | S7 *HSM* path only (REST index path S7-alt does **not** depend on it) |
| U2 | Fyers REST index live-correctness | `/data/quotes?symbols=NSE:NIFTY50-INDEX,BSE:SENSEX-INDEX` vs NSE/BSE published same-minute, ≤0.05-pt, record ts/source/budget | S7 index-live acceptance |
| U3 | Upstox account tier / per-endpoint REST quotas | confirm Standard 50/500/2000-30min applies to *this* account; Plus (5 conn, `full_d30`50) entitlement | S2/S7 Upstox budget; not Fyers-only |
| U4 | Upstox `full`-mode live field delivery (incl. prev-OI-on-wire absence) | capture one `full` frame for an in-scope F&O contract; confirm `ltpc/marketLevel/marketOHLC/oi/iv/optionGreeks` populate and **previous-OI is not on the wire** (REST is the source) | S3/§2 prev-oi path; N15 |
| U5 | Fyers OI-analytics / Greeks / IV absence | confirm absent (broker limit) via Fyers docs; decide **accept-unavailable** vs **approve a Depth-based OI build** | S12 decision |
| U6 | Active runtime catalog empty (N12) | run S9 sync against intended data dir (approved operational step) | all in-scope discovery in the live env |
| U7 | G1 identity fork + S13 deferral | confirm **additive coordinator now** (S4) and **canonical re-key (S13) as separate protected-core** | S13 |
| U8 | `effective_budget` not test-pinned | add tests pinning §1.1 (50/4500/1800/450) + `min(...,500)` (no conflicting number exists in code, but the guard is absent) | S2 |

**Evidence classes:** [C] source-inspected (N1,N2,N4,N5,N7,N8,N9,N10,N11,N12,N13,N14) · [D] official docs verified (§1,§4 URLs 2026-10-18) · [T] tests *present* but **not re-executed** this read-only round (`test_upstox_feed/wiring`,`test_fno_coverage`,`test_fyers_integration`,`test_previous_oi`,`test_option_chain_history`) · [R] **BLOCKED — live evidence required** (U1–U4) · [A] assumed (N15 full-mode population, Fyers BSE tick delivery).

---

## 10. Final status

**READY FOR MAINTAINER REVIEW** — of the *architecture and implementation plan*.

Rationale:
- The **design is internally consistent** and provider-balanced: one authoritative capacity policy (§1, no conflicting numbers; U8 adds the missing test guard), the Upstox previous-OI mapping gap is precisely located and the precedence design already prevents older-REST-overwriting-newer-data (§2), the quote architecture separates **native observations / canonical identity / centrally-selected quote** with preference only as a tie-break (§3, no hidden Upstox dependency), automatic F&O reconstruction needs **no new persistence field** (§6), Fyers-only limitations are stated truthfully without fabrication or borrowed data (§5), and every rollback is **non-destructive with the `git reset` recommendation removed** (§7).
- **Outstanding items are external live tests (U1–U4, U6) and two maintainer decisions (U5 Fyers-OI-analytics, U7 re-key deferral), not unresolved *architectural* questions.** Each live test is an **explicit prerequisite that gates the affected stage** (S7 index-live, S3/§2 prev-oi, S8); **no stage is declared validated until its tests pass**, and none is claimed to pass.
- **A material architectural uncertainty does NOT block review** — the additive coordinator (S4) is safe on existing storage; the write-path re-key (S13) is explicitly deferred as protected-core; capacity is Standard-default with a documented Prime override.

**Coding remains prohibited.** Stopped after the report. Implementation proceeds only after the maintainer (a) approves this plan (including S1–S12 now and S13 as a separate protected-core task), (b) records the U1–U4 live test results as the gate for S7/S3, and (c) decides U5/U7. No stage is implemented.
