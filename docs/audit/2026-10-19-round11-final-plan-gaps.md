# MarketHub — Round 11: Close the Final Plan Gaps (READ-ONLY, focused)

**Scope of this round:** only the three Round 10 open items — (1) Fyers aggregate-PCR evidence, (2) gate separation, (3) S3/S6/S11 dependency correction. No re-audit of other findings. No files, config, DB, tests, Git state, or in-flight work modified. No mutating endpoints, no catalogue sync.

---

## 1. Fyers aggregate PCR — `callOi`/`putOi` evidence

### 1.1 What the official documentation actually says
Source: **Fyers API v3 Option Chain** (https://fyers.in/api-docs/v3/api/market-data/option-chain/, "Last updated October 14, 2026") [D].

- Request attributes: `symbol`, `strikecount` (**max 50**, "number of strikes on each side of ATM"), `timestamp`, `greeks`.
- Response `callOi`/`putOi`: **"Total open interest for call/put options"** — *one* top-level integer each, **returned for the single expiry** of the request (the response carries exactly one `expiry` + `date`, not a per-expiry breakdown; `expiryData[]` is only the *list* of available expiries, not OI).
- Per-strike: `oi`, `oich`, `oichp`, `prev_oi` — these belong to the **returned strikes only** (the `strikecount` window).

### 1.2 The decisive semantic question: whole-expiry, or window-only?
**The documentation does NOT state that `callOi`/`putOi` sum over all strikes of the expiry.** It labels them "total open interest for call/put options" without qualifying the strike range. Two readings are both textually consistent:
- **(a)** the *whole-expiry* aggregate OI for that side (independent of the `strikecount` window), and
- **(b)** the aggregate over the *returned* strikes only.

**The documentation is therefore insufficient to establish which.** The example values cannot disambiguate: the sample response shows `callOi=10,038,175` / `putOi=3,875,200` with **only ~5 returned strikes per side** (`strikecount` small), where the sum of the visible per-strike `oi` values is far below those totals — *which is consistent with reading (a), whole-expiry*, **but the sample is illustrative, not a specification, and no live response was captured to confirm it.**

### 1.3 Evidence class and conclusion (mandated: do not infer live behavior from docs)
- **`callOi`/`putOi` are whole-expiry OI totals: UNVERIFIED.** The docs say "total … for call/put options" for a single expiry but **do not specify whole-expiry vs. window**, and **no live response exists** to settle it. The sample magnitude hints at whole-expiry, but that is inference, not proof.
- **Consequence (per the instruction "keep whole-expiry PCR blocked rather than treating the fields as verified totals"):** any Fyers OI-PCR computed from `callOi`/`putOi` **must be marked `scope:"unknown-aggregate"` / `bounded` until a live check confirms the aggregate is whole-expiry.** It must **not** be presented as a verified whole-expiry PCR.
- **The only fully-proven whole-expiry OI-PCR source in the fixed provider set remains Upstox `/v2/market/{oi,pcr}`** (per-strike `call_put_oi_data_list` + `total_put_oi`/`total_call_oi`, and a provider-computed `pcr`). Fyers *window-scoped* OI-PCR (leg-sum, already implemented `compute_pcr`) is safe; Fyers *whole-expiry* OI-PCR from `callOi`/`putOi` is **blocked on live evidence**.
- **`oichp` (OI-change %) is documented per-strike** [D] — this is independent of the aggregate question and *is* usable once mapped (S3 sub-item).

**Issue 1 answer (evidence-backed):** `callOi`/`putOi` are documented as "total open interest for call/put options" for the requested expiry, but the **docs do not specify whole-expiry vs. window**, and **no live response was captured** → the semantics are **UNVERIFIED**. **Fyers whole-expiry OI-PCR stays BLOCKED** (treated as `unknown-aggregate`, not a verified total). The exact evidence to close it: an authenticated `/data/options-chain-v3?symbol=<NSE-F&O>&strikecount=1&greeks=1` response, compared against the *known* whole-expiry call/put OI (independently, e.g. Upstox `/v2/market/oi` for the same expiry, or NSE OI reports) — if `callOi`/`putOi` match the whole-expiry totals, the field is a whole-expiry aggregate; if they equal the strike-window leg-sum, it is window-scoped.

---

## 2. Gate separation (three distinct readiness states)

| Gate | Definition | Current status |
|---|---|---|
| **G1 Implementation readiness** | Source-level assumptions resolved **and** the applicable *offline* regression tests pass (executed). | **PARTIALLY MET.** Assumptions resolved for S2/S4 (capacity math confirmed; Upstox `previous_oi` additive-safe) and for `compute_pcr` (put÷call confirmed, §10). **But no offline regression test has been executed this round** (read-only; §5.3 tests not run). G1 is **not yet satisfied** until the §5.3 analytics/capacity/normalizer tests are authored *and* executed green. |
| **G2 Live-validation readiness** | An authenticated Fyers/Upstox session **and** open market hours are available for read-only GETs. | **NOT MET / BLOCKED.** No reachable session in this environment; market-hours unconfirmed; app/DB undisturbed. Every [R] item (Fyers `greeks=1` response, Fyers REST index, HSM index scaling, Upstox `full` fields, Upstox V3 `previous_oi`, dual failover, **and the new `callOi`/`putOi` whole-expiry disambiguation**) is **BLOCKED — LIVE EVIDENCE REQUIRED**. **None is claimed passing.** |
| **G3 Release readiness** | All required acceptance gates pass (G1 + G2 + the three-config matrix + no-request-storm + clean-checkout). | **NOT MET.** Gated on G2 (live) and the S11 test execution. **The plan is not releasable until G2's blocked tests produce recorded evidence.** |

**Explicit rule honored:** a missing live session is **recorded as a block, never silently treated as a correctness claim.** No [R] test is described as passing. The plan may be *approved* for implementation of the offline stages while G2 items remain gated.

---

## 3. Precise plan inconsistency in S3 / S6 / S11 (and minimum amendments)

**The inconsistency:** Round 10 **S3** says *"consume aggregate `callOi`/`putOi` for OI-PCR (A1)"* and Round 10 §5.4/§6 present Fyers OI-PCR as *upgradable to whole-expiry* by wiring `callOi`/`putOi`. **That overstates the evidence.** §1 above proves the whole-expiry semantics of `callOi`/`putOi` are **UNVERIFIED** — so S3 must not *promote* Fyers OI-PCR to a "whole-expiry" value on documentation alone, and S6's "aggregate" scope label must not claim verified whole-expiry. S11's live list did not include the disambiguation check. This is a **scope-labelling / gating inconsistency**, not a scope *expansion* (providers and instrument scope are unchanged).

**Preserved scope (unchanged):** providers = **Fyers + Upstox**; instruments = **NSE stock F&O, major NSE index F&O, BSE SENSEX F&O**. No provider or instrument scope added.

### Minimum amendments (affecting only S3, S6, S11)

**S3 (Fyers option-chain param + fields):** keep `greeks=1` + `oichp→oi_change_percent` (both [C+D]-supported). **Amend the `callOi`/`putOi` sub-item:** consume `callOi`/`putOi` **only as `oi_aggregate_call`/`oi_aggregate_put` with `scope:"unknown-aggregate"`** — *do not* emit a verified whole-expiry PCR. The OI-PCR derived from them is labelled **`bounded`/`unverified-whole-expiry`** until S11 disambiguates. (The proven window-scoped `compute_pcr` leg-sum remains the safe OI-PCR for Fyers in the interim.)

**S6 (analytics scope + labelling):** amend so the scope vocabulary is: `window` (leg-sum, proven) · `aggregate-unverified` (`callOi`/`putOi`, semantics unconfirmed) · `whole-expiry` (Upstox `/v2/market/*`, proven). **Fyers whole-expiry OI-PCR is NOT labelled `whole-expiry`** unless S11 confirms; it is `aggregate-unverified`. Upstox OI-PCR is `whole-expiry`. Volume-PCR and Fyers max-pain stay `window`-bounded (no aggregate volume; `strikecount` cap).

**S11 (live validation):** add the **missing live check** — an authenticated Fyers `/data/options-chain-v3?strikecount=1&greeks=1` response, **disambiguating `callOi`/`putOi`** against an independent whole-expiry OI reference (Upstox `/v2/market/oi` for the same expiry, or NSE OI report). Outcome **upgrades** `aggregate-unverified → whole-expiry` (and unblocks a verified Fyers whole-expiry OI-PCR) **or keeps it `aggregate-unverified/bounded`**. This test gates S3/S6's OI-PCR scope label.

**No other stage changes.** S1, S2, S4, S5, S7–S10, S12–S15 are unaffected (S4 Upstox `previous_oi` remains a proven additive mapping; S7–S10 unchanged).

---

## 4. Final status

**NOT READY FOR MAINTAINER APPROVAL as a *release* — READY for *implementation approval of the offline stages*.** Precisely:

- **The plan is internally consistent once §3's amendments are applied** (S3 no longer overstates `callOi`/`putOi`; S6 uses a three-tier scope label; S11 carries the disambiguating live test). No provider/instrument scope was expanded.
- **Implementation gate (G1):** satisfiable offline — author + execute §5.3 regression tests (PCR put÷call, scope flags, `greeks=1`, `oichp`, Upstox `previous_oi`, `effective_budget`). **Currently unmet (tests not yet run this read-only round).**
- **Live-validation gate (G2):** **BLOCKED.** No authenticated session / open market in this environment. The Fyers `callOi`/`putOi` whole-expiry question, Fyers `greeks=1`, Fyers REST index, HSM index scaling, Upstox `full`/`previous_oi`, and dual failover are all **open, recorded as blocked, and not claimed passing.**
- **Release gate (G3):** **NOT met** — it depends on G1 (offline tests green) **and** G2 (live evidence recorded).

**Therefore:** the plan *can* be presented to the maintainer for **approval to begin the offline implementation stages (S1–S6, S8)** with S3/S6/S11 amended as above, **while the whole-expiry Fyers OI-PCR, Fyers index live-correctness, HSM scaling, Upstox field delivery, and dual-failover acceptance criteria remain explicitly gated on S11's blocked live tests.** Nothing is declared validated; no live result is fabricated; no file, test, config, DB, Git state, or in-flight work was touched this round. **Stop.**
