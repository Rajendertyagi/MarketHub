# MarketHub — Round 10: Final Analytics Correctness & Release Gate (READ-ONLY)

**Mode:** STRICT READ-ONLY. No source/test/config/DB/migration/lockfile/build files modified. No catalogue sync, no mutating endpoints, no auth changes, no destructive git. All in-flight work preserved.
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9` · **Date:** 2026-10-19
**Evidence classes:** **[C]** source inspection (re-read this round) · **[T]** a test *actually executed* (none re-executed this read-only round — see §4) · **[D]** official docs (URL + date) · **[R]** authenticated live response (**none available → BLOCKED**).

---

## 1. PCR formula — VERIFIED CORRECT (Round 9 wording was inaccurate, not the code)

### 1.1 The actual implementation (`market/analytics/option_chain.py`)
```python
# compute_pcr  (line 102)
ce_oi = sum(r["call_leg"]["open_interest"] ...)   # total CALL OI
pe_oi = sum(r["put_leg"]["open_interest"]  ...)   # total PUT OI
if ce_oi == 0: return {"pcr": None, ...}          # zero-denominator → UNKNOWN
pcr = pe_oi / ce_oi                                # ← PUT / CALL
```
`_rows`/`_leg_dict` (lines 35-61) map `r.call` → `call_leg` and `r.put` → `put_leg` with correct CE/PE routing. So:
- **OI PCR = total put OI ÷ total call OI.** `>1` = put-heavy (bearish), `<=0.8`/`>=1.2` thresholds, "Balanced" in between. **This is the conventional PCR.**
- **`compute_pcr_volume` (line 125): PCR_volume = total put volume ÷ total call volume.** Also conventional.

### 1.2 Resolution of the R9 contradiction
- **R9 §5 stated "PCR is Fyers-computable from callOi/putOi" and §3 implied the formula sums call÷put.** The R9 prose was **imprecise/misleading; the code is correct.** The **implementation divides put by call** — the standard definition. **No inversion exists in `compute_pcr`/`compute_pcr_volume`.**
- **Round 9's own `market/oi.py`/docstrings are consistent:** `compute_pcr` docstring *"Put-Call Ratio from total open interest. >1 = put-heavy (bearish bias)."* matches the formula. **The metric is not inverted.**

### 1.3 Data-scope subtlety (the real correctness nuance, §2)
- `compute_pcr`/`compute_pcr_volume` sum **over the rows in the snapshot** (`_rows(snapshot)` = the *returned* strike rows). So the OI/volume PCR is **strike-window-scoped** (however many strikes the snapshot carries), **not** the exchange-wide aggregate.
- The Fyers **aggregate `callOi`/`putOi` fields in the option-chain response are NOT read by the normalizer** [C — confirmed: no `callOi`/`putOi`/`call_oi`/`put_oi` reference in `market/normalize/fyers.py`]. So **Fyers PCR is computed from the returned strike legs only**, not from the (possibly whole-expiry) `callOi`/`putOi` totals.
- **Upstox's REST `/v2/market/pcr`** (`pcr_from_rest`, `upstox_analytics.py:146`) returns the provider's **aggregate `pcr`/`total_put_oi`/`total_call_oi`** — a *different* (exchange-wide) value than the computed `compute_pcr`. MarketHub exposes **both**: the provider aggregate via `pcr_from_rest`, and the window-scoped value via `compute_pcr`. **They are not the same number; the UI must label which it shows.**

**Conclusion:** No formula inversion; **one real defect = Fyers option-chain PCR/OI is window-scoped while the aggregate `callOi`/`putOi` is available but unused**, plus a **labeling gap** (window vs. whole-expiry must be explicit). No correction made this round (per instruction); required tests listed in §4.

---

## 2. Full-chain vs. bounded-window analytics (exact coverage)

| Metric | Data coverage actually used | Can it be "full-chain"? | Condition to mark bounded/unavailable |
|---|---|---|---|
| **Fyers OI PCR** | **Returned strike legs only** (legs present in the response at `strikecount` default **50/side**); aggregate `callOi`/`putOi` **ignored** [C] | **No** — as wired. *Could* be whole-expiry if the normalizer consumed `callOi`/`putOi` (documented top-level aggregates). | When the window excludes material OI, the value is **window-scoped** → label `scope:"loaded_window"` (the market_intel path already does this, `market_intel.py:423,428`). |
| **Fyers volume PCR** | **Returned strike legs only** [C `compute_pcr_volume`] | No — no aggregate volume field exists in the Fyers chain. | Window-bounded; no full-volume source from Fyers. |
| **Fyers max-pain** | **OI across returned strikes only** [C `compute_max_pain:151`] | **No** — capped at `strikecount` (50/side) [D]. | Must be marked **bounded (≤50/side)**; full-strike max-pain is **not obtainable from a single Fyers call** → for a whole-expiry answer, Upstox `/v2/market/max-pain` (full-strike) is the source. |
| **Upstox OI PCR / max-pain** | **Provider aggregate** via `/v2/market/{oi,pcr,max-pain}` (`call_put_oi_data_list` per-strike, `total_put_oi`/`total_call_oi`) [C `upstox_analytics.py:44,89,119,146`] | **Yes** — these endpoints return the whole-expiry per-strike OI. | N/A (full). |
| **Repeat calls / wider window** | Fyers: increase `strikecount` (max 50/side) or iterate expiries; **no documented endpoint returns the full strike list for Fyers** [D]. | Partial only. | Wider coverage for Fyers requires **multiple `strikecount`/`expiry` calls** (budget-consuming) — still bounded by the 50/side cap per call. |

**Do not claim full-chain analytics from a partial window (mandated).** The **only** full-expiry OI/PCR/max-pain source in the fixed provider set is **Upstox's `/v2/market/*` OI endpoints**; Fyers provides **window-scoped** analytics unless its aggregate `callOi`/`putOi` are wired in (a normalizer change, not yet present).

---

## 3. Round 9 provider findings — re-verified independently

| R9 claim | Re-verified status | Evidence |
|---|---|---|
| **G1 Fyers option-chain omits `greeks=1`** | **CONFIRMED [C+D].** `brokers/fyers/rest.py:428-433` sends only `symbol`,`strikecount`,`timestamp`; **no `greeks` param**. Official docs [D option-chain, upd. 2026-10-14] require `greeks:"1"` for the nested greeks. Normalizer `fyers.py:493-503` *already parses* the `greeks` object (delta/gamma/theta/vega/iv) → **greeks/IV are Fyers-supported but never requested.** | [C `rest.py`, `fyers.py`; D] |
| **G2 Fyers `oichp` (OI-change%) unmapped** | **CONFIRMED [C+D].** Docs list per-strike `oichp`; `fyers.py:499` maps `oich→oi_change` only; **`oichp` has no mapping.** | [C; D] |
| **G4 Upstox V3 REST `previous_oi` dropped** | **CONFIRMED [C+D].** `quote_from_rest` (`upstox.py:139-145`) maps `oi→open_interest`, **not `previous_oi`**; docs [D Full Market Quotes V3] return `previous_oi` for F&O. Adding it is additive and does **not** conflict with the provider-OI precedence (`market/oi.py:32-58`: provider-prev-OI → persisted → unavailable) or the staleness-gated merge (`market/service.py`): a *newer* REST value wins, an *older* snapshot is rejected — **no overwrite of newer data.** | [C; D] |
| **WS vs REST fields (both)** | **CONFIRMED [C+D].** Upstox WS `full` = LTPC+D5+marketOHLC(interval)+`oi`+`iv`+greeks+CAS — **no `previous_oi` on the WS wire** [C proto; D]; `previous_oi` is a **V3 REST full-quote** field only. Fyers OI/prev-OI/OI% come from **Depth REST** (`pdoi`/`oipercent`, `fyers.py:258,263,265` [C]) + option-chain `prev_oi`/`oich` [C]; **Fyers HSM/WS carries no greeks/IV.** | [C; D] |
| **Capacity arithmetic** | **CONFIRMED unchanged [C].** `effective_budget=cap−max(10%×cap,50)` → **50 / 4500 / 1800** (`providers/capabilities.py:44-51`); app cap 500 applied **separately** (`subscriptions/service.py:30,721`), *not* folded into `effective_budget`. **No test pins `effective_budget`** [C — 0 matches]. Fyers cap hardcoded 5000 (Prime) [C] vs Standard=100 [D]. | [C; D] |
| **Native obs / canonical selection / freshness** | **CONFIRMED [C].** `market/service.py:207-210,251` (native-keyed store, canonical computed not used as key); `fno_coverage._available:210-215` state-only (no quote freshness). Coordinator re-key is a **separate protected-core** item. | [C] |
| **Automatic F&O reconstruction** | **CONFIRMED [C].** `fno_config` persists declarative intent ("always resolved dynamically", `core/persistence/modules/fno_config.py:5-7`); **no new field needed**; the gap is only that `_derivatives_universe` (`service.py:92`) is set solely by the manual `POST /api/fno/apply`. | [C] |
| **Untracked Fyers REST module / clean-checkout** | **CONFIRMED [C].** `git ls-files` no-match for `brokers/fyers/rest.py`; imported at module scope `app/server.py:695` → clean checkout **crashes** until committed. | [C] |

**No earlier claim was repeated without re-inspection; all above was re-read against the active tree this round.**

---

## 4. Test & repository safety (evidence discipline; no destructive rollback)

- **Source inspection [C]:** all §1–§3 findings.
- **Tests actually executed [T]:** **none re-run this round** (read-only). Existing tests (`test_upstox_feed`, `test_fno_coverage`, `test_fyers_integration`, `test_option_chain_history`, `test_previous_oi`, `test_broker_analytics`, `FnoWorkspaceView.test.tsx` **exists** with `pcr_by_oi` fixtures per a `fnoSchemas.test`/`FnoWorkspaceView.test.tsx` grep — **its assertions are NOT claimed passing** without execution).
- **Official docs [D]:** Fyers option-chain `greeks=1` (upd. 2026-10-14); Upstox V3 feed / full-quote V3 / option-chain / OI / rate-limit (2026-10-18/19).
- **Authenticated live [R]:** **BLOCKED** — no reachable session, market-hours unconfirmed, app/DB undisturbed. **No live result fabricated.**

**Repository protection (mandated §4/§8):**
- **All rollback instructions in R7–R9 that used `git reset --soft` (S1) are hereby removed** (R8 §7 already withdrew it; R9 kept a "forward-only" note; this round removes the *last* residual reference). **No stage recommends `git reset/clean/checkout/restore/stash/rebase` or any history operation.**
- Rollback = **selective reversal of only the stage's added lines, after safeguarding pre-existing in-flight edits; never a whole-file revert on a file with unrelated changes.**
- **The untracked `brokers/fyers/rest.py` is NOT treated as production-ready** until its inclusion **and** its dependent wiring (`app/server.py:695` import, `FyersMarketDataAdapter` consumers) are verified **together** in the clean-checkout plan (S1+S2 acceptance), not assumed.
- Pre-inspection state preserved: `fno-provider-agnostic` @ `3f4b8d9`; 208 staged-new / 29 modified / 268 untracked (R7/R8/R9 recorded). No files staged/committed/altered; no destructive git; no sync.

---

## 5. Final amendment

### 5.1 Verified PCR formulas + data scope
- **OI PCR = Σ put OI ÷ Σ call OI** (conventional; **not inverted**). **Vol PCR = Σ put vol ÷ Σ call vol.** Both are **strike-window-scoped** in `compute_pcr`/`compute_pcr_volume` (sum over returned rows). Zero-denominator → `None` (UNKNOWN, not 0). [C]
- **Fyers aggregate `callOi`/`putOi` are available but UNUSED** [C] → Fyers OI-PCR is currently window-scoped, not whole-expiry.
- **Upstox** has a *separate* provider-aggregate source (`/v2/market/pcr`, `total_put_oi`/`total_call_oi`) [C]. **The two PCR values are different; the UI must state which.**

### 5.2 Defects found (this round)
| # | Defect | Provider | Status | Evidence |
|---|---|---|---|---|
| A1 | **Fyers OI-PCR & volume-PCR are strike-window-scoped**; aggregate `callOi`/`putOi` (Fyers) unused → a "full-expiry PCR" is **not** produced from Fyers as wired | Fyers | **Verified [C]** | `compute_pcr:102` sums `_rows`; no `callOi`/`putOi` read in `fyers.py` |
| A2 | **Metric-labelling gap:** consumers can conflate window-scoped `compute_pcr` with the provider-aggregate Upstox PCR; Fyers max-pain is bounded (≤50/side) | both | **Verified [C]** | `market_intel.py:423` labels "loaded_window"; `product_routes._pcr_to_dict` (aggregate) vs `compute_pcr` (window) are different paths |
| A3 | **R9 wording defect (not code):** R9 §3/§5 implied call÷put and "full from Fyers chain"; code is put÷call and window-scoped | report | **Verified [C]** | this document §1 |
| G1 | Fyers option-chain **omits `greeks=1`** (greeks/IV unsupported-in-practice) | Fyers | **Verified [C+D]** | `rest.py:428-433`; docs upd. 2026-10-14 |
| G2 | Fyers **`oichp` (OI-change%) unmapped** | Fyers | **Verified [C+D]** | `fyers.py:499` vs docs |
| G4 | **Upstox V3 full-quote `previous_oi` dropped** by `quote_from_rest` | Upstox | **Verified [C+D]** | `upstox.py:139-145` vs docs |
| G5 | `brokers/fyers/rest.py` untracked; clean-checkout crash at `app/server.py:695` | Fyers | **Verified [C]** | `git ls-files` |
| G6 | Fyers HSM index ticks dropped; scaling unverified (undocumented HSM) | Fyers | **Verified [C+D]** | `fyers/feed.py:937-942` |
| G7 | Fyers cap hardcoded 5000 (Prime); Standard=100 default not applied | Fyers | **Verified [C vs D]** | `fyers/capabilities.py:27` |
| G8–G11 | native-keyed quote store; `preferences().key`=upstox_key; F&O not auto-restored; routing state-only (no freshness) | both | **Verified [C]** | R8/R9, re-confirmed |
| G13 | `fno/view` storm mitigation present; regression test **not verified passing** | frontend | **Partially [C]** | `useFno.ts:116`; test existence not execution |

### 5.3 Regression tests required for analytics correctness (future, not implemented)
1. **`compute_pcr` / `compute_pcr_volume`:** assert **put÷call** semantics (a snapshot with 2× put OI ⇒ pcr≈2.0, "put-heavy"); zero-call-OI ⇒ `None`.
2. **Scope test:** a snapshot with *fewer* strikes than the full chain ⇒ returned `total_put_oi`/`total_call_oi` equal the **sum of returned legs only** (documents window-scope); assert the result object carries a `scope` marker (window vs. aggregate).
3. **Fyers aggregate test (post-G1 wiring):** when the normalizer consumes `callOi`/`putOi`, assert the OI-PCR equals `putOi/callOi` (whole-expiry) and is **distinguished** from the leg-summed value.
4. **Max-pain window test:** a Fyers chain with `strikecount=50` ⇒ `max_pain` result carries a `bounded:true` / `window_size` flag; a full-strike Upstox OI payload ⇒ unbounded.
5. **Upstox V3 `previous_oi` test (G4):** `quote_from_rest` on a payload with `previous_oi` ⇒ `Quote.previous_oi` populated; provider-prev-OI precedence over persisted; older REST snapshot rejected by the staleness gate.
6. **Fyers `greeks=1` test (G1):** `FyersRest.option_chain` request URL contains `greeks=1`; response with nested `greeks` ⇒ `OptionContractData.iv/delta/...` populated.
7. **No-inversion guard:** a property test `compute_pcr(snapshot).pcr == total_put_oi/total_call_oi` over random snapshots.

### 5.4 Final staged implementation plan (non-destructive rollback; NO coding)
> Rollback for every stage = **selective reversal of only that stage's added lines after safeguarding in-flight edits; never a whole-file revert on a file with unrelated changes; no `git reset/clean/checkout/restore/stash/rebase`.** No new persistence field; no protected-startup change.

| # | Stage | Exact boundary | Deps | Data/schema | Risk | Non-destructive rollback |
|---|---|---|---|---|---|---|
| S1 | Make `brokers/fyers/rest.py` available (G5) — **verified *with* its `app/server.py:695` dependent wiring in a clean checkout** | additive file + wiring check | — | none | none | forward-only (removal is a later forward change); **never a `reset`** |
| S2 | Centralized plan-aware capacity (G7) | new `providers/capacity.py` (`fyers.plan_cap:100 default / 5000 Prime-override`); `registry.budget()`→`min(effective_budget(plan_cap),500)`; replace `fyers/capabilities.py:27`; **add `effective_budget` + `min()` tests** (50/4500/1800; app 500) | S1 | config (additive) + tests | medium | revert config file, the one `capabilities.py` line, and tests |
| S3 | Fyers option-chain **`greeks=1`** + **`oichp` mapping** (G1,G2) + **consume aggregate `callOi`/`putOi`** for OI-PCR (A1) | `rest.py::option_chain` add `params["greeks"]="1"`; `fyers.py` add `oichp→oi_change_percent` and (optional) `callOi`/`putOi`→whole-expiry OI totals, exposed with a `scope` flag | S1 | none | low–medium | revert the param line + the mapping lines |
| S4 | Upstox V3 full-quote **`previous_oi` mapping** (G4) + precedence/regression tests (§5.3.5) | `upstox.py::quote_from_rest` add `previous_oi` (additive) | S1 | none | low | revert the added mapping line |
| S5 | **Fyers capability correction** (R9 G3) | `fyers/capabilities.py`: declare `CAP_OPTION_CHAIN`(with greeks), `CAP_OI`/`CAP_PREVIOUS_OI`/`CAP_OI_CHANGE`(chain/depth), document **max-pain window-bounded** | S3 | none | low | revert |
| S6 | **Analytics scope + labelling** (A1,A2) | attach a `scope` (`window`|`aggregate`) to `compute_pcr`/`compute_pcr_volume`/`compute_max_pain` results; UI labels "window-scoped" vs "whole-expiry"; Fyers max-pain marked bounded | S3,S5 | none | low–medium | revert the scope field + labels |
| S7 | **Central quote coordinator + freshness/validity** (G8,G9,G11) | new `providers` selection policy + `market/service.get_canonical_quote` (additive, reads existing store) + `fno_coverage._available` quote-age gate + endpoint-specific rate buckets | S2,S4 | **none** | medium-high | revert the 3 additions |
| S8 | **Canonical join in `preferences()` + all consumers** (G9) | `preferences().key`→canonical; `IndicesWidget`,`types.ts`,`WatchlistTable`,heatmap/SSE/option-chain/OI-analytics/market-intel switched to the canonical contract | S7 | none | medium | selective hunk reversal per in-flight file |
| S9 | **Auto-restore F&O at startup, NO new field** (G10) | `app/server.py` lifespan: `plan()`→`set_derivatives_universe()`→existing `reconcile()` (~3 lines at `:1220`) | S7 | none | low–medium | revert the added lifespan lines |
| S10 | **Fyers-only index via REST** (G6-alt) | `rest.py::quotes` + coordinator index source + bounded market-hours poller (≤5 rps, 60 s, shared bucket, 5-min freshness, dedup) | S7 | none | medium | revert poller + index-source lines; HSM guard untouched |
| S11 | **Market-hours live validation** (all §3 [R]; §5.3 tests) | harness: Fyers `greeks=1` chain (incl. `callOi`/`putOi` + `oichp`), Fyers REST index, Fyers HSM scaling, Upstox `full` fields, Upstox V3 `previous_oi`, dual failover; **execute §5.3 regression tests** | S10 | none (evidence) | n/a | n/a — **do not claim passed** |
| S12 | Catalogue init (operational, approved) | `POST /api/instruments/sync` vs the *intended* data dir | S1 | populates `instruments` (target env; `data_test` untouched) | low | idempotent/transactional; **not run in this review** |
| S13 | `migrate_from_config` Fyers index categorization | `subscriptions/service.py::migrate_from_config` | S8 | one-time (idempotent) | low | revert (marker re-run safe) |
| S14 | `fno/view` storm regression test **execution** (G13) | run/verify `frontend/src/__tests__/FnoWorkspaceView.test.tsx` (idle/afterRenders/atUnmount/afterUnmount) | — | none | low | delete a *new* test file only (if created); do not alter in-flight work |
| S15 | **Canonical write-path re-key (final)** ⚠ protected-core | `market/service.py` `_quotes` → `(exchange, canonical_id)` + migration | S7 proven | data migration | high | **separate protected-core approval; not in the ordinary gate** |

**Critical path (safe all-three-config):** S1 → S2 → S3 (Fyers greeks/IV + aggregate OI) → S4 (Upstox prev-OI) → S5 → S6 (analytics scope) → S7 (coordinator) → S8 (canonical join) → S9 (auto-restore) → S10 (Fyers REST index) → S11 (live evidence, **BLOCKED-gated**). **S11 gates the *trust* of S3/S4/S10 field delivery — no stage is declared validated until its live tests pass and §5.3 regression tests execute green.**

**Acceptance tests (per stage + config):** Fyers-only / Upstox-only / dual × (index spot, stock F&O fut/opt, NSE index fut/opt, SENSEX fut/opt, OI/prev-OI/OI-change, greeks/IV, depth, option-chain+ATM, **PCR window-vs-aggregate labelling**, max-pain bounded flag, heatmap, OI-analytics, REST, SSE, frontend); stale/unavailable truthfulness; capacity `CAPACITY_LIMITED`; auth-expired `auth_required`; catalog-unavailable `desired:0`; no request storm. Clean checkout (S1): no `ModuleNotFoundError`; `test_architecture_boundaries.py` green.

---

## 6. Remaining critical uncertainties + exact evidence

1. **U1 Fyers HSM index scaling** (G6) — live `if\|` capture, ≤0.05-pt, 3 snapshots. **BLOCKED [R]**. Blocks Fyers *streaming* index only.
2. **U2 Fyers option-chain `greeks=1` + `callOi`/`putOi` + `oichp` live response** (S3/S11) — confirm nested greeks populate and aggregate OI fields are whole-expiry. **BLOCKED [R]**.
3. **U3 Fyers REST index live-correctness** (S10/S11) — `/data/quotes` NIFTY/SENSEX vs published, ≤0.05-pt. **BLOCKED [R]**.
4. **U4 Upstox `full`-mode live fields + `previous_oi` NOT on wire** (S4/S11) — capture a `full` frame. **BLOCKED [R]**.
5. **U5 Upstox V3 full-quote `previous_oi` present for F&O** (S4) — `GET /v3/market-quote/quotes`. **BLOCKED [R]**.
6. **U6 Upstox account tier / per-endpoint quotas** (50/500/2000-30min std vs Plus) — confirm vs connected account. **BLOCKED [R]** (residual; not Fyers-only-blocking).
7. **U7 Active runtime catalog empty (G12)** — closed by S12 (operational sync), not read-only.
8. **D-1 (analytics):** accept **window-scoped Fyers PCR/max-pain as the truthful Fyers-only state** (aggregate `callOi`/`putOi` wired in S3 to lift OI-PCR to whole-expiry; volume-PCR + max-pain remain window-bounded). **Maintainer decision.**
9. **D-2:** defer the canonical write-path re-key (S15) as **protected-core**; the additive coordinator (S7) is interim.

**Evidence classes:** [C] A1–A3, G1–G13 re-inspected this round · [D] Fyers option-chain (2026-10-14) + Upstox V3 set (2026-10-18/19) · [T] tests present, **not re-executed** · [R] U1–U6 **BLOCKED**.

---

## 7. Maintainer decisions that remain
1. **D-1 (A1/A2):** adopt window-vs-aggregate **scope labelling**; approve S3 (wire Fyers aggregate `callOi`/`putOi` → whole-expiry OI-PCR) and S6 (scope flags). Accept volume-PCR + Fyers max-pain as **window-bounded** (Upstox is the full-strike source).
2. **D-2:** confirm the `greeks=1` + `oichp` (S3) and Upstox `previous_oi` (S4) additive fixes.
3. **D-3:** defer S15 (canonical re-key) as a **separate protected-core** task; S7 coordinator is the interim path.
4. **D-4:** record **U1–U6 live evidence** as the gate for S11 (no stage is "validated" until those tests pass).

---

## 8. Status

**READY FOR MAINTAINER REVIEW** — of the *architecture and implementation plan*.

- **The analytics-correctness question is resolved with code evidence:** PCR is **put ÷ call** (conventional; **not inverted** — the R9 prose was the inaccuracy, not the code). The **real** defects are (a) Fyers OI-PCR/max-pain are **strike-window-scoped** while the aggregate `callOi`/`putOi` is available but unused (A1, fixable additively in S3), and (b) **labelling** must distinguish window-scoped from provider-aggregate PCR (A2, S6). No formula correction was made this round, per instruction.
- **All R9 provider findings re-verified independently** (G1 `greeks=1` omission, G2 `oichp`, G4 Upstox `previous_oi`, WS-vs-REST, capacity 50/4500/1800+app-500, untracked `rest.py`) — **no claim repeated without fresh inspection.**
- **Repository safety is clean:** the `git reset` rollback is **fully removed**; rollback = selective hunk reversal with in-flight work safeguarded; the untracked Fyers module is **not** assumed production-ready until its import wiring is verified in the clean-checkout plan.
- **Outstanding items are external live tests (U1–U6, U7 operational) + 4 bounded maintainer decisions (D-1…D-4)** — **none is an unresolved *architectural* question.** No live result fabricated; no stage declared validated until S11's tests pass and §5.3 regression tests run green.

**No code written; no source/test/config/DB/migration/lockfile/build file modified; no catalogue synced; no mutating API; no destructive git; all in-flight work preserved.** Stopped — coding prohibited until the maintainer reviews this plan (approving S1–S14, deferring S15 as protected-core, recording U1–U6 evidence, and deciding D-1…D-4).
