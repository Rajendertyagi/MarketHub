# Proposed F&O Remediation Plan — MarketHub

> **Status: PROPOSAL — NOT AN APPROVED ARCHITECTURE.**
> This document is a *proposal for maintainer review*. It does not supersede,
> amend or override any existing decision record. Where this document restates
> an earlier decision it cites the source; where it proposes something new it is
> marked **NEW** and requires an explicit maintainer decision.
>
> | | |
> |---|---|
> | Author | Independent architecture & remediation study |
> | Date | 2026-10-20 |
> | Branch / HEAD | `fno-provider-agnostic` @ `3f4b8d9` (working tree is dirty; see §4) |
> | Scope | MarketHub F&O (futures/options) subsystem — application layer only |
> | Method | Strict read-only. No code, test, config, schema, migration, runtime-data or Git state was modified. |
> | Mode | Planning only. **Nothing in this document has been implemented.** |
> | Version | V23 (see §11 revision log) |

---

## 1. Executive assessment

### 1.1 Bottom line

The F&O subsystem is **not releasable**, and the previous "completed" report is
**not a reliable basis for approval**. Four things block it, and V9's analysis
shows that the fix is a **capacity-architecture** fix, not a constant change.

1. **A real, reproducible runtime failure.** `POST /api/market/fno/view`
   returns HTTP 500. The coverage planner and the subscription resolver apply
   **two different, mutually incompatible capacity policies** to the same key
   set: the planner assigns **exactly 500 Upstox keys** (the whole of the
   implemented ceiling) and the resolver then unions in **16–56** more keys
   from the other five owners, producing **516–556** keys against that 500
   ceiling on the current tree, and **517** on the settled scope (§3.13). The
   resolver's only response is to `raise`, which leaves the subscription
   service **wedged** until it is cleared with `DELETE /api/fno/apply` or a
   restart (R-04).

2. **The capacity limit that binds is MarketHub's own, not the brokers'.**
   The intended coverage needs **659** contracts (**220** universe spots +
   **439** nearest-two futures; §3.12.1). Upstox's documented `full`
   entitlement is **2 000** keys per user and FYERS Data Socket Standard is
   **100** — so the coverage **fits**, with room. The **109** contracts marked
   `CAPACITY_LIMITED` today are a **budget artefact**, not a capacity limit: the
   planner already computes all 659 and assigns only 550 because its
   per-provider budget ran out. Under a capacity-first design the same
   catalogue yields **0 dropped**
   contracts (§3.7.2b, §3.10).

3. **The application ceiling is redundant — and it is what caused the
   failure.** The resolver enforced the **ceiling** (500) and not the
   **per-provider allocation**, so the FYERS feed was permitted 500 keys
   against an allocation of 50 while Upstox was stopped by a number that
   relates to no provider limit. No demonstrated failure mode requires a
   separate application-wide ceiling: the failure that *was* demonstrated is
   an **error-handling** defect (raising instead of degrading), fixed by the
   already-approved overflow policy P-5 and by atomic startup. §3.5b sets out
   the evidence for retiring it.

4. **The option-surface unit of work was wrong, and the scope is now confirmed (V17).**
   V15 priced the option surface **symbol by symbol** — 87 842 symbols, and
   5 934 of them as individual per-symbol REST depth calls. The unit is the
   **(underlying, expiry) cell**: **one chain response covers every symbol in
   that cell**, and a chain response carries price, volume, bid/ask, current
   OI, previous OI, IV **and** Greeks. On **2026-10-21** the maintainer set the
   scope — *"20 strikes on either side of ATM is enough for us"* (D-21) — and then
   **confirmed it**: **D-23** (±20 around the **true** ATM, **surface-wide**,
   across every underlying-expiry cell), **D-24** (multi-level depth is **not**
   required), **D-25** (the deep-ladder tail is **out of scope**) and **D-22**
   (DhanHQ is **excluded**). The class-C gap is **0**, the per-symbol tail is
   **out of scope**, and the required surface costs **one** chain request for the cell a user actually opens — the **689-request background sweep is withdrawn (V21)**, because it never existed in the code and it priced a cost the product does not incur (§3.18) — and **no** request is made for a cell nobody has opened.
   On the FYERS Standard pool the index poller is now a **conditional**
   consumer — **0** in normal operation because the fallback is idle while the
   primary is healthy, **77 per session** in failover and up to **770** under a
   sustained provider failure (§3.17.7) — leaving **5 000** for sweeps in
   normal operation ⇒ **≈ 7.46 sweeps/day** — spent **inside the market
   session**, because the market is open only **≈ 385 minutes** a day
   (NSE equity derivatives **09:15–15:40 IST**, w.e.f. 2026-08-03), so the
   surface refreshes **about once an hour while the market is open** and
   **nothing is refreshed outside it** (§3.16.5a). The other FYERS consumers,
   retries and actual runtime behaviour remain **unmeasured**, so this is a
   **target, not a SLA**. **M-15 and M-16 are closed**; **D-26** makes the
   refresh policy session-scoped and demand-driven.
5. **The streaming scope is settled, and the plan is written against it.**
   The maintainer stated the requirement on **2026-10-20** (D-1, D-4): the
   **six approved NSE F&O indices plus BSE SENSEX**, the **NSE F&O stock
   universe**, **all** contracts including option chains and futures, **NIFTY and
   BANKNIFTY** explicitly covered, **BANKEX excluded**. That **confirms** the
   position recorded in `docs/PROJECT_CONTEXT.md` §C (2026-10-19, *"Settled
   (approved / locked)"*) and the as-built implementation of 2026-09-21. R5's
   narrower *streaming* paragraph (2026-10-18) is retained in §2.4 as **labelled
   pre-decision history**; it is **not** an alternative product requirement, and
   **no acceptance criterion tests it as one**. **M-1 and M-4 are closed and
   gate nothing.**

**What is not wrong.** Substantial parts are correct and should be preserved:
the Fyers REST client and its `greeks=1` / `oichp` handling, the
adapter-declared capability model, the analytics scope tiers, the quote
coordinator's freshness-gated selection, the catalogue-backed canonical
identity derivation, the FYERS Data Socket protocol implementation and its
100-symbol capacity declaration (§3.3), and the Fyers REST index fallback.
**The failure is an integration-and-architecture failure, not a craftsmanship
failure** — which is why it survived a clean implementation, a green targeted
test run and a self-reported "complete".

### 1.2 What V9 changed, and why

**V9 reframes the capacity architecture around the product goal: deliver the
intended F&O coverage using the maximum safely usable market-data capacity
available from the configured brokers.** The consequence is that the question
"500 or 800?" disappears, because the ceiling is no longer the thing that
limits coverage.

| | V5–V8 | **V9** |
|---|---|---|
| The enforced limit | an application-wide ceiling (500, later 800) **plus** a per-provider allocation, with the wrong one enforced | **per-provider usable capacity** = entitlement − headroom, with **one** formula and **no** application-wide constant in the enforced path |
| Where the number comes from | a constant, twice | the **documented** limit for the actual socket/mode/tier, minus a **declared** headroom policy |
| The FYERS headroom | `max(10 %, 50)` → usable **50** of 100 | `max(10 %, 2)` → usable **90** of 100 (§3.6) — the floor was a zero-budget guard, not a margin |
| The desired set | truncated by the planner to 550 | **659 described**; the resolver fits it (0 dropped) |
| The overflow rule | P-5 (approved) — unimplemented | P-5 (approved) — **implemented and verified**; M-3 is no longer a design gate |
| The routing | planner assigns, resolver checks | one routing sequence (§6.3): identity → eligibility → one provider per contract by **remaining capacity** → single de-duplication → P-5 only if needed |
| The 800 decision | treated as the live question | **historical decision context**; the architectural finding is that the ceiling is redundant (M-2) |
| **V13/V14** | — | the **API claims were re-verified against the vendor documentation** (V13: C1–C8 applied, §3.14 source register) and the **capacity arithmetic was recomputed at option-symbol level** (V14: the FYERS Standard budget, the exact union, and the settled scope propagated through the active text) |
| The target universe | 221 spots · 441 futures · 662 contracts · 670 cells (pre-decision) | **220 spots · 439 futures · 659 contracts · 689 cells (670 NSE + 19 SENSEX)** — recomputed for the settled scope (§3.12.1) |
| The option surface | "on demand", never sized | **field-verified per endpoint**; **one chain response per (underlying, expiry) cell**; under the **±20 ATM scope (D-21)** the class-C gap is **0** — the 2 969-strike / 5 934-symbol gap belongs to the **full-ladder background tier** only (§3.16.2) |

**The single most important measured result** (§3.7.2b, §3.10): with the
artificial ceiling out of the enforced path, the **entire** intended coverage
fits inside the documented provider limits — **0 dropped** — and both providers
retain failover headroom. **The streaming scope is closed** (M-1, M-4 — the
maintainer's requirement of 2026-10-20: the six approved NSE indices plus
SENSEX, the NSE F&O stock universe, all contracts including option chains and
futures, BANKEX excluded). What remains genuinely undecided is the **headroom
policy** (M-5), the **application ceiling** (M-2), the **narrow IV/Greeks
trade-off** (M-12), the **BSE dependency record** (M-13) and the **account
tier** (G-T).

### 1.3 What this plan does

It proposes a **decision-gated, dependency-ordered remediation** that starts
from the *actual* current state, replaces the ceiling-driven capacity model
with a **provider-capacity-driven** one, restores the approved architecture,
and re-derives every capacity number from the **official broker
documentation** and **measured** catalogue data. It deliberately does **not**:

* invent a new architecture or reorder the approved stage list without
  evidence;
* choose the maintainer's headroom policy, ceiling disposition, the
  option-surface completeness trade-off, or the BSE dependency record for them
  — the **streaming scope is settled** (D-1, D-4) and is applied, not chosen
  here;
* introduce a new arbitrary fixed ceiling, derive one from the smallest
  provider allocation, or equate a REST batch limit with a WebSocket
  subscription limit;
* include the unrelated news / X-Twitter / Yahoo / heatmap / MCP work;
* touch the protected quote-store write path;
* recommend a wholesale revert, Git cleanup, staging or committing.

### 1.4 Status

**BLOCKED on the decisions and evidence listed in §2.3 — M-2 (application
ceiling), M-5 (headroom policy, account tier, failover trade-off), M-13 (BSE
dependency record), M-6/M-7/M-8/M-9/M-9b/M-10/M-11, and M-14 (optional) — on
one external fact (the account tier, gate G-T), and on the repository
integration gate (W-01).** **M-12 no longer blocks the required surface**: its
subject — the IV/Greeks gap on the deep wings — is out of scope (D-25), and it
reopens only if the tail returns. **M-15 and M-16 are CLOSED** by the
maintainer's decisions of 2026-10-21 (D-23, D-24), **DhanHQ is EXCLUDED**
(D-22), and the **refresh policy is session-scoped and demand-driven** (D-26).
One **document-recovery** blocker also remains open: **§4's V13 source could
not be accessed** in this environment, so §4 is an **unverified reconstruction**
until an external copy is supplied.** **M-14 (the optional FYERS futures-chain
check) is not on that list: it is an optional improvement that blocks
nothing.** **M-1 and M-4 are closed and gate
nothing.** The remediation sequence is
fully specified and dependency-correct; the capacity work (W-10 → W-07 → W-08)
is blocked only on the two capacity decisions (M-2, M-5) and does **not** wait
on the scope.

### 1.5 Corrections to V5–V8 (history, for auditability)

* **V5 → V6:** V5's R-05 (the FYERS socket was "not the documented Data
  Socket") was **wrong** and withdrawn; the endpoint **is** the FYERS v3 Data
  Socket, so the documented 100-symbol limit **applies** (§3.3).
* **V5 → V6:** V5's "≈ 52 keys" counted only two of six owners (§3.7).
* **V6 → V7:** V6 treated R5's REST-on-demand paragraph as the approved
  scope; V7 reconstructed the authority (§2.4) and found **no recorded
  amendment** of the "spot + nearest two futures" decision, so the scope is
  **BLOCKED** and R-02 became **conditional**.
* **V6 → V7:** V6's "derive the ceiling from the smallest allocation" was
  **incoherent** (it would cap Upstox at 50) and withdrawn.
* **V7 → V8:** V7 called the maintainer's 800 raise "unsupported"; **V8
  corrected that** — the PM context is the source, and what is missing is the
  *repository record* (§2.2 D-2). V8 also recomputed the union in four states
  and added the NIFTYFPI arithmetic (§3.9).
* **V10 → V11 (this pass):** the target universe was **recomputed from the catalogue
  for the settled scope** — V10's 221 spots / 441 futures / 662 contracts / **670**
  chain cells are **prior measurements, not the approved inventory**. The settled
  in-scope figures are **220 spots · 439 nearest-two futures · 689 chain cells
  (670 NSE + 19 SENSEX) · 87 842 option contracts (84 816 NSE + 3 026 BSE) ·
  43 973 distinct strikes**, and V10's "670" is reconciled (it was the
  **NSE-only** count; V10's prose "214 × 3 + 35 = 677" was wrong on both terms).
  The **field-by-field** capability of every endpoint was then verified against
  the official documentation, which **overturned V10's tail route**: FYERS
  `/data/quotes` carries **no `oi`, `prev_oi`, `iv` or `greeks`**, so the
  FYERS-only tail needs **`/data/market-depth`** for OI and previous OI — and
  the residual gap is narrowed, honestly, to **IV and Greeks on 2 969 strikes (6.75 %) — 5 934 option symbols**, for which every documented route was evaluated and fails (§3.12.3).
  The request budget is restated with the **total** call count and the existing
  index-poller consumption, and the per-tier **freshness** guarantee is derived
  (**required surface, ±20 ATM scope (D-23):** Upstox ≈ seconds per sweep — **21 calls**; FYERS ≈ **13.4 min** per sweep, and the index poller is **conditional** — **0** in normal operation, **77 per session** in failover, up to **770** under a sustained provider failure (§3.17.7) — so **5 000** of the pool is available for sweeps in normal operation ⇒ **≈ 7.46 sweeps/day**, spent **inside the ≈ 385-minute market session** (NSE equity derivatives **09:15–15:40 IST**) ⇒ **≈ once an hour while the market is open**, and **nothing outside it** — a **target, NOT a freshness SLA**: the other FYERS consumers, retries and actual runtime behaviour are unmeasured. **Re-entry tier (only if the out-of-scope tail returns, D-25):** FYERS ≈ **135 min** per pass and the daily pool does **not** fit a complete pass (7 011 > 5 000), so the tail would rotate over **≥ ≈1.7 days** — also a lower bound).
* **V9 → V10:** the **maintainer stated the product requirement**
  (2026-10-20): *all* F&O data for NSE major indices + NSE F&O stocks + BSE
  SENSEX, all contracts including option chains and futures, with NIFTY and
  BANKNIFTY explicitly covered. That **closes M-1 and M-4** and adds a surface
  the plan had never sized — the **complete option surface** (§3.11). The
  measured inventory (**670** chain cells, **84 816** option contracts,
  **665** futures) and the request budget (**670** chain calls, 13 % of the
  FYERS Standard daily pool) show the requirement is **affordable on REST**, and
  the plan now states explicitly that *all data available* is not *all data
  streamed*. Two new findings: **neither provider alone is complete** (R-26) and
  **BSE F&O is Upstox-only** because a published FYERS master is missing from
  the sync list (R-27).
* **V8 → V9:** the 500-vs-800 framing is replaced by a
  **capacity-first** model (§3.5); the application ceiling is found
  **redundant** and its retirement recommended (§3.5b); the **headroom floor**
  is re-derived and shown to be the real FYERS coverage cost (§3.6); the
  desired set and the capacity available to place it are measured for the
  first time (§3.10); the **routing sequence** is specified ahead of any
  dropping (§6.3); **M-3** is downgraded from a design gate to an
  implementation-authorisation matter; and the **P-5 kind-versus-role**
  conflict is surfaced as **M-11** rather than silently redefined.

---

## 2. Authoritative decision ledger

### 2.1 The decision chain (chronological)

| # | Date | Document | Role in the chain |
|---|---|---|---|
| — | 2026-10-17 | `docs/plans/2026-10-17-fno-full-trading-plan.md` | Original F&O plan (Phases 1–6: Fyers REST client, wiring, CAS proto, capability truthfulness, catalogue sync, read-path workstream). States *"No code was edited to produce this plan."* **Superseded in part** by R10/R11. |
| R3 | 2026-10-18 11:09 | `docs/audit/2026-10-18-round3-pre-implementation-gate.md` | Pre-implementation gate: capacity model, `CAPACITY_LIMITED` policy, streaming scope vs REST-on-demand. |
| R4 | 2026-10-18 11:35 | `docs/audit/2026-10-18-round4-final-amendment.md` | Binding-budget formula; Fyers plan-cap override model. |
| **R5** | 2026-10-18 11:50 | `docs/audit/2026-10-18-round5-focused-fno-audit-plan.md` | **The F&O-focused plan.** Universe (6 NSE indices), streaming scope, truthfulness states, limit handling. |
| R6 | 2026-10-18 12:27 | `docs/audit/2026-10-18-round6-equal-depth-provider-audit.md` | Equal-depth provider scope; in-scope index set (6 NSE + SENSEX); capacity re-statement. |
| R7 | 2026-10-18 13:12 | `docs/audit/2026-10-18-round7-official-api-study-final-audit.md` | Official-API capacity arithmetic correction (50 / 4500 / 1800); app cap 500 as an explicit separate `min()`. |
| R8 | 2026-10-18 23:40 | `docs/audit/2026-10-18-round8-final-readiness-verification.md` | Readiness verification; auto-trigger list; G-series gates. |
| R9 | 2026-10-18 23:54 | `docs/audit/2026-10-18-round9-fyers-capability-correction.md` | Fyers capability correction; G1–G13 defect list. |
| **R10** | 2026-10-19 00:05 | `docs/audit/2026-10-19-round10-analytics-correctness-release-gate.md` | **The implementation plan: stages S1–S15** (§5.4), gates G1/G2/G3, D-1…D-4. States *"Do not code without approval."* |
| **R11** | 2026-10-19 00:15 | `docs/audit/2026-10-19-round11-final-plan-gaps.md` | S3/S6/S11 amendments (aggregate OI scope gating); G1 not met, G2 blocked. |
| — | 2026-10-19 → 20 | `docs/PROJECT_CONTEXT.md` | The implementation agent's running handoff. **Not a plan and not an approval record.** Records the stage execution and the change log. |
| — | 2026-10-20 10:01 | `docs/audit/2026-10-20-live-validation-evidence.md` | Live evidence artifact (Fyers session, market hours). |
| **PM** | **PM context — the maintainer's conversation of record** (not a repository document) | Lists among the decisions to be checked against their authoritative records: the application ceiling **raised from 500 to 800**, six NSE index underlyings, spot + nearest-two-futures streaming, on-demand option chains, a ≈40-instrument ATM window, registry-driven spot resolution, adapter-declared capabilities, one provider per contract, broker neutrality, protected-core preservation, no silent universe expansion, and no unrelated-subystem edits. **Two of these entries are now known to conflict with the repository record** — the 800 ceiling (§2.2 D-2) and the streaming scope (§2.4). |

### 2.2 Decision ledger

Categories: **[A]** approved architectural requirement · **[M]** explicit
maintainer decision · **[P]** proposed, unapproved · **[S]** superseded ·
**[C]** genuine conflict needing maintainer resolution · **[U]** unsupported by
evidence.

| # | Decision | Original source | Later amendment | Approval evidence | Current status | Required action |
|---|---|---|---|---|---|---|
| D-1 | **Streaming scope — RESOLVED by the maintainer, 2026-10-20.** The requirement is now explicit: *"all the data for F&O trading — NSE major indices + NSE F&O stocks + BSE SENSEX — almost all stocks; NIFTY and BANKNIFTY covered; under NSE F&O stocks we need all, including options chain + future."* That is **position (a)** — the full universe's spot and its futures — **plus** the complete option surface, delivered **on demand** rather than streamed. | **PM context, 2026-10-20** (the maintainer's statement of requirement); consistent with P-1/P-2 and with the as-built implementation | The requirement also settles the delivery split: the *coverage* is everything; the *streaming* subset is what the provider entitlements allow (§3.11) | **[M] — a maintainer decision**, dated 2026-10-20 | **The implementation already realises (a) for spot and futures**; the **option surface** is not yet delivered as a complete, budgeted, cached service (§3.11, R-25) | **M-1 is now CLOSED by the maintainer's statement.** The remaining work is to implement the *data surface* it implies (W-24) and to keep the two scope-specific acceptance sets (S1/S2) until the implementation is measured. |
| D-2 | **Application ceiling — raised from 500 to 800 by a maintainer decision that the repository has not yet captured.** The earlier value (500) is the one recorded in the repository: R3 §178, R4 §118, R5 §100, R6 §181, R7 §109/160, implemented as `min(effective_budget(plan_cap), 500)`, asserted in code (`providers/capacity.py:25`), in the pre-existing constant (`app/subscriptions/service.py:31`) and in `test/test_md_subscriptions.py:484`. | **PM context — the maintainer's own instruction of record**, which lists the raise to 800 among the decisions that "must be checked against their authoritative records", and asks to "distinguish this application limit from each broker's documented or configured capacity". | **No repository amendment exists.** The decision is therefore **not integrated**: the code still computes 500 and a test still pins 500. | **[M] — a maintainer decision, recorded outside the repository.** Its *value* (800) and *intent* (a distinct application limit, separate from broker capacity) are stated in the PM context. Its **date, the artefact in which it was approved, and whether it was already applied to any branch or environment** are **not established** by available evidence. | **Ceiling 800 (per the decision); 500 in the code.** The divergence is itself a finding: the repository records a superseded value, and `test_md_subscriptions.py:484` now pins a **stale** number. **V9 adds the architectural finding: the ceiling is redundant** — it is bounded by the provider entitlement, the safety headroom and the provider feed's own mode guard, and it was the *cause* of the demonstrated failure because the resolver enforced it instead of the allocation (§3.5b). | **M-2** — decide whether to **retire** the application ceiling (recommended, §3.5b option A) or retain it as a guard that cannot bind below the provider's usable capacity (option B); **record the 800 decision as historical context** with its date and approving artefact, and update the stale test whichever way the decision goes. **The decision is not in doubt; its architectural necessity is, and its repository record is missing.** |
| D-3 | **Overflow policy**: drop by priority (live futures > index spot > option strikes), mark the dropped set `CAPACITY_LIMITED`, never claim full coverage | R3 §178, R4 §118, R5 §100, R6 §181, R7 §139, R8 §132 | — | **[A]** | **Half-implemented** — `FnoCoverageService` implements it correctly; `SubscriptionService.resolve()` does not (it raises) | **M-3** — confirm drop-and-report is the resolver's contract too (it is the only reading under which D-1 is implementable) |
| D-4 | **F&O index universe — RESOLVED by the maintainer, 2026-10-20: the six NSE indices + BSE SENSEX; BANKEX is NOT in scope.** The requirement names *"NSE major indices ... + BSE SENSEX"* and does not name BANKEX. | **PM context, 2026-10-20** | R5 §29 (six NSE) · R6 §155 (six NSE + SENSEX, no BANKEX) — the maintainer's statement matches R6 | **[M] — a maintainer decision**, dated 2026-10-20, aligning with R6 | The working tree's `is_fno=True` for **BANKEX** is therefore an **unapproved expansion** and must be reverted; `market/index_registry.py`'s docstring (which says SENSEX/BANKEX are *"never part of the F&O universe"*) must be reconciled with the code (SENSEX **is** in scope) | **M-4 is now CLOSED** (six NSE + SENSEX; BANKEX out) — the remaining work is to *apply* it, not to choose it (W-04) |
| D-5 | **Per-provider budget** = `min(plan_cap_effective, app_cap)`; the reserve is `max(10 % × cap, 50)` | R4 §118, R7 §10/75 | R10 S2 implements `providers/capacity.py` | **[A]** as arithmetic; the *role* of the reserve is not stated | **Active. Classification settled in V6 (§3.6):** the reserve **floor** is deliberate and necessary (it prevents a zero budget for tiny capacities) and the reserve is **not user-configurable** by design — but the **50 % of a 100-symbol entitlement that the floor produces is an artefact of applying an absolute floor to a percentage rule**, and was never a recorded product decision. R7 corrected the arithmetic to 50/4500/1800 and accepted the number without examining the floor's effect at small capacities. | **M-5** — what should the per-provider budget *mean*: a hard entitlement, a planning reserve, or a configurable margin? And what reserve rule applies at the FYERS tier? |
| D-6 | **Capabilities are adapter-declared**; the registry is broker-neutral; no hardcoded provider lists in feature code | R10 §2.2, §5.4 S2 | — | **[A]** | **Correct and verified** ✓ | none |
| D-7 | **One provider per canonical contract**; a provider's data is never attributed to another provider's contract | R10 §2.1, §5.4 S8 | — | **[A]** | **Implemented at (underlying, expiry, strike, option_type) granularity**, not at strike granularity → the frontend's per-strike grouping can cross a provider boundary (latent; 0 live instances found) | **M-6** — confirm the granularity of "one provider per contract" (per leg, or per strike pair) |
| D-8 | **Route one provider per contract; routing defaults to the first enabled provider in configuration order**; spot resolution is registry-driven | R10 §5.4 S7/S8 | — | **[A]** | **Partially implemented** — four divergent provider-inference implementations coexist; the quote coordinator hardcodes `("upstox","fyers")`; the coverage service receives **no** runtime status, so it routes to unauthenticated providers | **M-7** — confirm the status vocabulary the routing layer must consume |
| D-9 | **Broker-neutral and configurable; no provider-specific workarounds for individual symbols** | R10 §5.4, AGENTS.md | — | **[A]** | Mostly honoured. One exception: the Fyers TBT adapter hardcodes **Prime-tier** limits (3 connections × 5 symbols) sourced from a third-party SDK, while the official docs give **Standard = 1 symbol** | **M-8** — is the TBT depth feature in release scope? |
| D-10 | **Protected core**: `app/auth/*`, credential persistence, startup restoration, quote-store write path | AGENTS.md; R10 S15 (deferred) | — | **[A]** | **Respected** — `market/service.py`'s write path is unchanged; S15 correctly deferred. New writers (Fyers index poller) use the existing public `apply_quote` API. | none — but W-13 must not be allowed to re-key the store |
| D-11 | **Do not silently expand the universe with BSE underlyings** | R5 §29 (six) vs R6 §155 (+SENSEX) | — | **[A]** (prohibition) | **VIOLATED** (BANKEX) — see D-4 | **M-4** |
| D-12 | **Do not modify unrelated news / X-Twitter / authentication subsystems as part of F&O work** | `docs/plans/2026-10-17-fno-full-trading-plan.md` §5 | — | **[A]** (prohibition) | **Not satisfied** — the same session wired news MCP tools and edited `test_mcp_streamable_http.py`; the tree also carries X/Twitter, Yahoo, heatmap and MCP-registry work | **M-9** — approve or separate them (W-01) |
| D-13 | **Option chains are REST on-demand; no WebSocket keys for chains** | R10 §2.1; `2026-10-17` plan §2.1 item 7 | — | **[A]** | **Correct and verified** ✓ (Fyers `strikecount=50` REST; no option WS keys) | none |
| D-14 | **Focused ATM-window streaming bounded ≈ 40 instruments** | R5 §47/120 | — | **[A]** | **Implemented** — ATM ± 10 strikes × CE/PE = **42** legs, clamped ≤ 25 per side | none |
| D-15 | **Nearest-two futures per underlying** | R5, R6; `2026-10-17` plan | — | **[A]** | **Implemented**, but as an **unstaged** change that is the proximate trigger of the capacity failure | fold into W-09 with D-1 |
| D-16 | **Analytics scope labelling** — `window` / `aggregate-unverified` / `whole-expiry`; Fyers `callOi`/`putOi` may only be labelled `whole-expiry` with evidence | R10 §5.4 S3/S6; R11 §3 | R11 §3 amended S3/S6 after live evidence | **[A]** | **Implemented** ✓ — and the 2026-10-20 live run produced the evidence R11 required, so the scope label is now evidence-backed | none |
| D-17 | **Fyers `callOi`/`putOi` whole-expiry** | R11 §1 (UNVERIFIED) | live evidence 2026-10-20 §U3 (aggregate constant across window widths) | **[A]** — closed by evidence | **Resolved** ✓ | none |
| D-18 | **Upstox HSM/WS carries no `previous_oi`; persisted prior-session OI is the documented fallback** | R10 §3 G4; live evidence 2026-10-20 U4 (BLOCKED) | — | **[A]** (as design) | **The fallback is inert** — the recorder has no production observation path (R-07) | **M-9b** — is the previous-OI fallback in this release's scope, or formally excluded? |
| D-19 | **Stage order S1→S2→…→S11, S13 gated on S8a, S15 deferred** | R10 §5.4; PROJECT_CONTEXT §"Next agreed action" | — | **[A]** as a plan; **no approval artifact exists** | **Executed largely out of order and without the approval gate** (S1 never performed; S9 performed and broken) | **M-10** — record whether the stages were approved before coding, since no repository artifact says so |
| D-20 | **Implementation ≠ commit ≠ offline-test completion (G1) ≠ live validation (G2) ≠ release readiness (G3)** | R10 §5.4, §6.1; R11 §2 | — | **[A]** | **Correctly stated by the previous agent** ✓ but then **not honoured** — implementation proceeded with G1 unmet and G2 blocked | enforce via W-19/W-23 |
| D-21 | **The option-surface scope: *"20 strikes on either side of ATM is enough for us"*** | maintainer statement, **2026-10-21** | — | **[M]** | **Recorded** — the statement's reading and unit are resolved by D-23 | — |
| D-22 | **DhanHQ is EXCLUDED** — no credential, no probe, no integration, and no order routed through it | maintainer decision, **2026-10-21** | — | **[M]** | **Binding** — the DhanHQ evaluation of §3.16.7 is closed as **excluded**, not merely not-adopted | none — do not pursue |
| D-23 | **±20 strikes around the TRUE ATM define the required option surface, across EVERY underlying-expiry cell** — not merely the presentation layer | maintainer decision, **2026-10-21** | — | **[M]** | **Binding** — **resolves M-15**; the deep wings are outside the required surface | — |
| D-24 | **Multi-level depth is NOT required** — the chain's top-of-book bid/ask satisfies the current requirement | maintainer decision, **2026-10-21** | — | **[M]** | **Binding** — **resolves M-16**; per-symbol depth polling is **removed** from the proposed request budget | — |
| D-25 | **The deep-ladder tail beyond ±20 is OUT OF SCOPE for now** | maintainer decision, **2026-10-21** | — | **[M]** | **Binding** — the tail is neither served nor claimed as a required surface; its cost is retained only as the **re-entry cost** (§3.12.4) | reopens **M-12** only if the tail returns to scope |
| D-26 | **The application must *show what we need to see*, not synchronise everything continuously** — the refresh policy is **session-scoped and demand-driven** | maintainer statement, **2026-10-21** | — | **[M]** | **Binding** — the background sweep runs only **inside the market session** (§3.16.5a), the visible tier is refreshed on demand, and every displayed surface carries its **own age** and the **session it belongs to** | — |
| D-27 | **The data-refresh architecture is DEMAND-DRIVEN** — MarketHub fetches and updates **what the user needs**, not the whole option universe; the mandatory background chain sweep is **withdrawn and not replaced** by another blanket schedule | maintainer directive, **2026-10-21** | — | **[M]** | **Binding** — §3.16.4, §3.16.5, §3.16.5a, §3.18 | — |

### 2.3 Maintainer decisions this plan needs

| ID | Decision | Why it cannot be inferred | Blocks |
|---|---|---|---|
| **M-1** | **CLOSED — 2026-10-20.** The maintainer stated the requirement: *"all the data for F&O trading — NSE major indices + NSE F&O stocks + BSE SENSEX — almost all stocks; NIFTY and BANKNIFTY covered; under NSE F&O stocks we need all, including options chain + future."* The **coverage** scope is the full universe; the *streaming* subset is bounded by provider entitlement and the **option surface** is delivered on demand (§3.11). | — | — | none — W-09 and W-24 |
| **M-2** | **Does MarketHub keep a separate application-wide subscription ceiling?** The 500→800 raise is **historical decision context** showing the intent (more usable capacity than 500); the architectural analysis finds the ceiling **redundant** and the *recommendation* is to **retire it** in favour of per-provider usable capacity (§3.5b, option A). What remains undecided: retire vs retain-as-derived-guard, and the **provenance** of the 800 decision (date, approving artefact, whether any environment already runs 800) — which must be recorded either way, together with the stale `test_md_subscriptions.py:484` assertion. | The ceiling is **redundant**: it is bounded by the entitlement, the headroom and the provider feed's own mode guard, and it caused the demonstrated failure by being enforced instead of the allocation. | W-10 |
| **M-3** | **No longer a design blocker.** The overflow policy is **already approved** (P-5 — R3:178 / R4:118 / R5:100 / R6:181 / R7:139 / R8:132; never withdrawn). W-08 is its **implementation and verification**, not a new architecture. If separate *implementation* authorisation for the resolver's behavioural change is wanted, that is an **authorisation** matter, not a design gate — labelled here as such and **not** counted among the design decisions. | Keeping a design gate on an already-approved requirement would re-open settled ground. | W-08 (implementation only) |
| **M-4** | **CLOSED — 2026-10-20.** The universe is the **six NSE indices + SENSEX**; **BANKEX is out**. The working tree's `is_fno=True` for BANKEX must be reverted and the registry docstring reconciled. **No capacity consequence** (§3.8). | — | — | none — W-04 applies it |
| **M-12** | **The trade-off on the demonstrated data gap — narrowed by D-21, and its subject removed by D-25 (V17).** The gap exists **only in the deep-ladder tail**, which is **out of scope** (D-25) — so it is **not** a failure of the required surface and **no longer blocks it**. The gap itself remains a real limitation of the tail: for **2 969 strikes (6.75 %) carrying 5 934 CE/PE option symbols** outside the ±20 ATM window, the documented APIs expose **no source for IV and Greeks** (§3.12.3): FYERS' chain is capped at 50 strikes per side, FYERS' quotes carry no `oi`/`prev_oi`/`iv`/`greeks`, FYERS' depth carries OI but no IV/Greeks, and Upstox's chain and option-greek endpoints require Upstox-listed instrument keys. **Current OI and previous OI are separate claims (V14).** A documented Upstox route exists (`/v2/market/oi`, already implemented) whose strike coverage outside Upstox's master is **not documented**, so **current** OI there is *not established* rather than unavailable — and that endpoint carries **no previous-session OI**, so it **cannot** replace the FYERS depth cost. The decision is therefore narrow: report **IV and Greeks** as *provider-unavailable* per contract (the recommendation), or reduce the required field set — and treat the **current-OI probe** as a **separate evidence task**, not as a resolution of the IV/Greeks gap. | Every documented route has been evaluated and each fails, or is undocumented, for a stated reason (§3.12.3) — this is an **absence of a documented source among the evaluated endpoints of the configured providers**, not proof that no external source could exist. The requirement is **not** reopened. **With D-25 the affected symbols are out of the required surface, so this no longer blocks the required-surface acceptance criterion — it blocks only the re-entry of the tail.** | **W-25** (re-entry only) |
| **M-13** | **How the BSE SENSEX provider dependency is recorded — SENSEX stays in scope either way.** The catalogue holds **5 126** BSE option contracts and **12** BSE futures, all Upstox, because `FYERS_MASTER_URLS` omits `BSE_FO` (R-27). **V13 (C5): FYERS BSE Equity Derivatives is a *documented* capability** — the Exchange-Segment Mapping lists **BSE \| Equity Derivatives \| exchange 12 \| segment 11** **[FY-7]**, and the futures-chain documentation's own example uses **`BSE:SENSEX-INDEX`** **[FY-5]**. The single-provider dependency is therefore **provisional on a sync omission, not a provider limitation**. What remains to establish: (i) whether the specific SENSEX contracts are in FYERS' master for the account's market-data permission, and (ii) per-symbol response behaviour for FYERS quotes/depth on BSE F&O. | Two first-party sources now support FYERS BSE F&O; the plan's earlier framing understated this. The remaining questions are per-symbol and permission-bound, and SENSEX's inclusion is **not** reopened. | W-26 |
| **M-14** | **Should the FYERS futures-chain be used as a *currently-trading* check?** `/data/futures-chain` reports *"only currently available contracts"* **[FY-5]**, which the static instrument master cannot. It is **not adopted** and **changes no capacity figure** (§3.12.4b); the only candidate use is a *currently-trading* guard on the nearest-two-futures selection. | A small correctness refinement to expiry selection, not a capacity or architecture change. The plan's futures selection is unchanged until this is decided. | W-26 |
| **M-15** | **CLOSED 2026-10-21 (D-23)** — ±20 strikes around the **true** ATM, **surface-wide**, across every underlying-expiry cell | **closed** — the design basis of §3.16 is the confirmed requirement | — |
| **M-16** | **CLOSED 2026-10-21 (D-24)** — multi-level depth is **not** required; the chain's top-of-book bid/ask satisfies the requirement | **closed** — per-symbol depth polling is removed from the budget (§3.16.5) | — |
| **M-15** | **Is the ±20 ATM scope (D-21) surface-wide or presentation-only — and is it 20 strikes on each side?** **CLOSED 2026-10-21 by D-23 — the scope is surface-wide, measured in strikes on each side of the **true** ATM, across every underlying-expiry cell.** The design basis of §3.16 is therefore the confirmed requirement, not an assumption. | **CLOSED** — no open decision | — |
| **M-16** | **Is multi-level market depth (order-book levels) a required field?** **CLOSED 2026-10-21 by D-24 — multi-level depth is NOT required; the chain's top-of-book bid/ask satisfies the requirement.** Per-symbol depth polling is **removed** from the proposed request budget (§3.16.5). | **CLOSED** — no open decision | — |
| **M-5** | **The safety-headroom policy** — and, with it, the account tier. The reserve *fraction* (10 %) is already a deliberate, documented policy; the **floor** is the open parameter, and the plan recommends correcting it from 50 to **2** so that it serves its stated purpose (never a zero budget) instead of halving a 100-symbol entitlement (§3.6). Also: is the account Standard or Prime (gate **G-T**)? | This is now the **decisive** coverage lever on the FYERS side: with usable 50 the FYERS union drops 13–35 keys under Scope-S1; with usable 90 it drops none. It also decides whether the FYERS numbers in the plan are valid at all. | W-07, W-16, G-T |
| **M-6** | "One provider per contract" granularity: per leg, or per strike pair | Determines whether the frontend may group by strike | W-17 |
| **M-7** | Provider-availability vocabulary the routing layer consumes (`ready` vs `streaming`/`connecting`/`reconnecting`) | The existing runtime status function and the coverage service disagree; one of them must change | W-06 |
| **M-8** | FYERS TBT depth feature: in release scope or excluded (and under which plan tier) | The adapter assumes Prime limits; Standard permits 1 symbol | W-16 |
| **M-9** | Change-set integration: which of the 6 untracked modules and the mixed-scope edits are in the F&O release | Determines whether a clean checkout can be validated at all | W-01 |
| **M-9b** | Previous-OI fallback: in scope (needs an observation owner) or formally excluded | It is the documented Upstox OI-change fallback but is currently inert | W-14 |
| **M-10** | Whether stages S1–S15 were approved before coding (no repository artifact records it) | Affects whether the executed work needs re-approval or only correction | W-01 |
| **M-11** | **Overflow priority: kind or role?** P-5 drops *option strikes* first, which includes the **active view's focused ATM window** — the instrument set the user is actually looking at. The coverage goal would rather sacrifice a universe contract. P-5 does not distinguish *role* from *kind*. **Latent** (the capacity-first design drops 0), but real whenever capacity is exhausted. | A policy refinement, not a defect; it must be **surfaced, not silently redefined** (§6.3). | W-08 (application only) |

> **Note on M-10.** The absence of an approval artifact in the repository does not
> prove approval did not occur elsewhere (conversation, issue tracker, or a
> maintainer instruction outside the repository). It only means the repository
> cannot evidence it. This plan therefore treats the *stage outcomes* as
> reviewable technical work (which they are) and asks only whether re-approval
> is needed — it does not assume the work was unauthorised.

### 2.4 The streaming scope — authority table (V7)

**V6 treated R5's "REST-on-demand" paragraph as the approved scope and treated
the locked "spot + nearest two futures" decision as superseded. V7 found no
evidence of an explicit maintainer amendment, and therefore treats the scope as
UNRESOLVED.** The two positions are recorded below with their dates and
provenance.

| # | Position | Date | Source | Kind of document | Approval evidence | Status |
|---|---|---|---|---|---|---|
| **P-1** | *"F&O universe = spot + **nearest two futures expiries**; option chains are **retrieved on demand** and only **focused ATM windows are streamed** within the approved subscription budget."* | recorded **2026-10-19** | `docs/PROJECT_CONTEXT.md` §C, under the heading **"Settled (approved / locked):"** | Project handoff / context file (agent-authored summary of the round chain) | The document labels it *settled/approved/locked*. It is a **summary**, not a signed decision record. | **Live position of record.** No later document amends it. |
| **P-2** | The F&O foundation (Phases 1–7) was **built and live-verified** with a canonical F&O universe and coverage/routing; the coverage service routes **futures + spot** for the universe. | **2026-09-21** | `docs/current.md` §2.1 — *"Broker-neutral F&O foundation (Phases 1–7) … Canonical 6-index registry … Canonical F&O universe … Schema v19 `fno_config`; coverage/routing (`app/fno_coverage.py`, `api/fno_coverage_routes.py`)"* | Living status document, describing **as-built, live-verified** work | Describes shipped, verified behaviour — the strongest form of evidence for *what was decided and built*. | **As-built reality**; consistent with P-1. |
| **P-3** | *"The requirement is **NOT** 'subscribe to every contract simultaneously' … **Automatic subscriptions only for what active app features need, within real limits** … the live stream is *scoped*, not the whole universe."* | **2026-10-18 11:50** | `docs/audit/2026-10-18-round5-focused-fno-audit-plan.md` §2, items 1–5 | **Audit / implementation plan** (R5) | R5's *instrument scope* correction is explicitly **"maintainer-directed"** (R5 preamble) and its *limit handling* is a rule; but the §5 streaming-scope **paragraph** is a planning conclusion inside an audit, not a recorded decision. | **Conflict source.** |
| **P-4** | *"**Streaming scope (auto, in-cap):** live stream = **index spot (7) + active-view futures + bounded ATM option window + active featured indices**. … the full 219-underlying F&O universe is **REST/on-demand, not streamed**."* | **2026-10-18 11:50** | R5 §5, "Capacity plan" | Same | Same | **Conflict source.** Note the internal tension: R5 §4's limit rule prioritises *"live futures > index spot > option strikes"*, which would **keep** the universe's futures — contradicting P-4's own sentence. |
| **P-5** | *"if a provider's plan cap … or the app cap (500) is exceeded, route by priority (live futures > index spot > option strikes), drop the overflow by priority, and **mark the dropped set `CAPACITY_LIMITED`** — never claim full coverage."* | 2026-10-18 (R3:178) → 2026-10-18 (R5:100) → 2026-10-18 (R7:139) | R3 / R5 / R7 / R8 | Audit chain | Repeated verbatim across four consecutive rounds; never withdrawn | **Uncontested.** This is the *degradation* rule, and it is compatible with **both** P-1 and P-3/P-4. |
| **P-6** | The current implementation streams **221 universe spots + 441 nearest-two-futures = 662 contracts** | working tree | `app/fno_coverage.py:405-482` (measured) | Implementation | — | **Conforms to P-1 and P-2**; **diverges from P-3/P-4.** *V11: this figure still includes BANKEX (3 contracts). The settled target is **659** — 220 spots + 439 futures (§3.12.1).* |

**Reconciliation.**

* **P-1 and P-2 agree** with each other and with the **as-built implementation**
  (P-6). The universe's spot and nearest two futures are what the coverage
  service plans and streams; option chains are on demand; only focused ATM
  windows stream option legs.
* **P-3/P-4 disagree with P-1/P-2**, but they come from an **audit document**
  and are in tension with their own companion rule (P-5), which prioritises
  *live futures* — i.e. the universe's futures.
* **No document anywhere records an explicit maintainer decision superseding
  P-1.** R5's *instrument* scope correction **is** maintainer-directed; its
  *streaming-scope* paragraph is not recorded as a decision. The **latest
  dated artifact that states a settled position** (P-1, 2026-10-19) states
  **P-1**.
> **V11/V12 — THIS VERDICT IS SUPERSEDED. The scope was subsequently settled by
> the maintainer directly (2026-10-20), not by any repository document.** The
> reasoning below is retained as the *pre-decision* analysis: it is the reason
> the question was put to the maintainer, and it remains the correct statement
> of what the repository record alone could establish.

* **Therefore (as of 2026-10-18):** per the rule *"do not assume that a later
  repository document automatically supersedes a maintainer decision, and do not
  assume that an older decision remains current if explicit evidence shows it
  was amended"* — there was **no explicit amendment**, so the streaming scope
  was **BLOCKED** and put to the maintainer.

**Consequences, as they now stand:**

1. **The streaming scope is decided** — by the maintainer's requirement of
   2026-10-20 (D-1, D-4), which **confirms P-1/P-2** (the full universe's spot
   and nearest two futures, with the option surface served on demand) and
   **excludes BANKEX**. **M-1 and M-4 are CLOSED** and gate nothing. The
   maintainer's statement *is* the explicit amendment the rule above was
   waiting for.
2. **A final capacity requirement is now stated** — against the settled scope,
   in §3.12.1 and §3.13. §3.7's two scenarios survive only as **labelled
   history** (§3.7 banner), not as alternatives.
2. **No final capacity requirement is stated.** §3.7 gives **two clearly labelled
   scenarios** — **Scope-S1** (P-1/P-2/P-6: universe spot + nearest two futures)
   and **Scope-S2** (P-3/P-4: index spot + active view only) — with exact counts
   for each. Neither is presented as approved.
3. **R-02 is reclassified.** V6 asserted "streaming scope diverged from D-1"
   as a *proven* defect. It is not: under Scope-S1 the implementation is
   scope-conformant and the defect is purely the resolver's `raise` (R-01). R-02
   is now **conditional on M-1**.
4. **The degradation rule (P-5) is the mechanism that reconciles the two
   positions** — and it is **already approved** (stated in R3:178, R4:118,
   R5:100, R6:181, R7:139 and R8:132; never withdrawn, never amended).
   **V8 correction:** V7 instructed M-1 to "re-affirm or amend" P-5, which
   would have put an approved requirement back in play. It does not need to be
   re-affirmed, and **M-1 must decide the scope only**. The overflow policy
   stays exactly as approved: drop by priority, mark `CAPACITY_LIMITED`, never
   claim full coverage. **W-08 is therefore not blocked by M-1** and does not
   wait on it.
5. **M-4 (the index universe) is unaffected.** R5's *instrument* scope was
   maintainer-directed and explicitly places **BANKEX derivatives out of scope**
   *"flagged for approval, not assumed"* (R5 §1 C). The working tree's
   `is_fno=True` for BANKEX is an **unapproved expansion** regardless of how
   M-1 is decided.

---

## 3. Official provider capacity — sourced, version-specific

All facts below were read from the **official vendor documentation on
2026-10-20** and carry the URL and the document's own "last updated" date.

### 3.1 Upstox — Market Data Feed V3

Source: <https://upstox.com/developer/api-documentation/v3/get-market-data-feed/>
(checked 2026-10-20). Market Data Feed V3. "Normal Connection and Subscription
Limits" and "Connection and Subscription Limits under Upstox Plus".

| Mode | Normal — individual limit | Normal — combined limit | Plus — individual | Plus — combined |
|---|---|---|---|---|
| LTPC | 5 000 | 2 000 | — | — |
| Option Greeks | 3 000 | 2 000 | — | — |
| **Full** | **2 000** | **1 500** | — | — |
| Full D30 | — (Plus only) | — | **50** | 1 500 |

* **Connections:** 2 per user (Normal) · 5 per user (Plus).
* **Individual vs combined (verbatim):** *"The Individual Limit refers to the maximum
  number of instrument keys allowed when a user subscribes to a single category…
  The Combined Limit applies when subscriptions cover multiple categories."*
  → A client that subscribes **only** `full` is bounded by the **individual**
  limit of 2 000, not the combined 1 500.
* **Per user, not per connection:** the table is framed as *"2 connections per
  user"* and the key allowance is stated for *"a user"*. Upstox does not use the
  phrase "across all your connections" that FYERS uses, so the per-user
  reading is **well-supported but not stated as explicitly as FYERS'** →
  residual uncertainty (low).

Source: <https://upstox.com/developer/api-documentation/market-quote/>
(checked 2026-10-20) — **Full Market Quotes, Full Market Quotes V3, OHLC Quotes
V3, LTP Quotes V3, OHLC Quotes and LTP Quotes all accept "up to 500
instruments" per request.**

> **This is the origin of the confusion.** The number **500** in MarketHub's
> `MAX_INSTRUMENTS_PER_PROVIDER` is numerically identical to the **Upstox REST
> per-request batch limit**, and to nothing in the Upstox *feed* limit table.
> `app/subscriptions/service.py:31` uses it as an **application-wide streaming
> ceiling**. Two unrelated constraints share a number and no comment anywhere
> records which is which. See **§3.5** (the seven quantities, which keep the
> application ceiling separate from the provider limits and from the REST
> batching limit) and **§3.6** (why the per-provider allocation comes out at
> 50 for FYERS and 500 for Upstox).

Source: <https://upstox.com/developer/api-documentation/rate-limiting/>
(checked 2026-10-20) — "Other Standard APIs (holdings, positions, funds,
historical candles etc.)": **50 req/s · 500 req/min · 2 000 req per 30 min**,
enforced *"on a per-API, per-user basis"*. Exceeding *"might result in temporary
suspension of access"*.

### 3.2 FYERS — plans, data socket, TBT, REST

Source: <https://fyers.in/api-docs/v3/getting-started/plans-and-limits/> and
<https://fyers.in/api-docs/v3/api/websockets/websocket-overview/> (both checked
2026-10-20; both pages state *"Last updated on October 14, 2026"*).

| Dimension | Standard | Prime |
|---|---|---|
| Data APIs (REST) | 5 req/s · 50 req/min · **5 000 req/day** | 10 req/s · 500 req/min · 500 000 req/day |
| Transaction + User Info APIs | 10 req/s · 200 req/min · 100 000 req/day | 10 req/s · 500 req/min · 500 000 req/day |
| Quotes API batch | **50 symbols/request** | 1 000 symbols/request |
| **Data Socket symbols** | **100** | **5 000** |
| **TBT Socket symbols** | **1** | **15** (up to 3 connections × 5) |

* **Scope of the symbol limit (verbatim):** *"Symbol limits apply per user,
  across all your connections to that socket, not per connection."*
* **Connection instance:** *"For the Data Socket and General WebSocket, create
  only one WebSocket instance at a time."*
* **429 behaviour:** the API returns `-429` when a limit is exceeded; *"if the
  per-minute rate limit is exceeded more than 3 times in a day, the user is
  blocked for the rest of the day."* → the REST budget is a **shared daily
  pool**, and the Fyers index poller, the option chain and the history calls all
  draw from it.
* **Option chain** (<https://fyers.in/api-docs/v3/api/market-data/option-chain/>,
  checked 2026-10-20, "Last updated on October 14, 2026"): `strikecount` is
  *"the number of strikes on each side of ATM (maximum 50)"*; `greeks=1` returns
  a nested `greeks` object; `callOi`/`putOi` are *"total open interest for call
  / put options"* for the single requested expiry; per-strike `oi`, `oich`,
  `oichp`, `prev_oi`, `volume`; `expiryData[]` carries `date` (DD-MM-YYYY),
  epoch `expiry`, `expiry_flag`; `optionsChain[0]` is the underlying itself
  (`strike_price = -1`) with `fp`/`fpch`/`fpchp`.

### 3.3 The FYERS socket identity — **RESOLVED IN V6 (V5's R-05 was wrong)**

> **V6 CORRECTION.** V5 asserted that the socket MarketHub opens
> (`wss://socket.fyers.in/hsm/v1-5/prod`) was *not* the documented FYERS Data
> Socket, and that the Data Socket symbol limit therefore could not be applied
> to MarketHub's FYERS budget. **That inference was wrong.** It was drawn from
> the URL path segment alone — precisely the reasoning this study was told to
> avoid. The evidence below establishes that the endpoint **is** the Data
> Socket, so the documented Data Socket limits **do** apply. V5's R-05 is
> withdrawn and replaced by R-05a/R-05b.

**What MarketHub's adapter actually implements** (`brokers/fyers/feed.py`):

| Aspect | MarketHub implementation | Official FYERS v3 Data Socket description |
|---|---|---|
| Endpoint | `wss://socket.fyers.in/hsm/v1-5/prod` (`:41`) | the Data Socket (URL not printed on the docs page) |
| Control frames | Join `{"type": 1}` · Sub `{"type": 2, "data": {"symbols": [...], "subType": "SymbolUpdate"}}` · Ping `{"type": 3}` (`:6-11`) | *"Subscribe from the on-connect callback"*; `data_type` = `SymbolUpdate` / `DepthUpdate`; `connect` / `sub` / `unsub` |
| Payload types | `sf` / `dp` / `if` (`:11`) | *"Each message has a `type`: `cn` connection, `sub` subscribe, `sf` equity and option data, `if` index data, `dp` market depth"* |
| Auth | `hsm_key` claim extracted from the access-token JWT; binary auth + full-mode + channel-resume frames; channel 11 (`:149-181, 249-261`) | *"Pass the access token in the format `appid:accesstoken`"*; the reference SDK performs the same binary handshake |
| SDK class | — | `from fyers_apiv3.FyersWebsocket import data_ws` → **`data_ws.FyersDataSocket`** ("FyersDataSocket WebSocket") in FYERS' **own sample code** |

**Evidence chain for the endpoint → Data Socket mapping** (checked 2026-10-20):

1. **Official FYERS docs** — the Data Socket protocol is described with the
   exact message-type vocabulary and `SymbolUpdate` data type that MarketHub
   implements. <https://fyers.in/api-docs/v3/api/websockets/data-socket/overview/data-socket-connect-and-subscribe/>
2. **FYERS' own sample repository** (first-party, `FyersDev/fyers-api-sample-code`,
   branch `sample_v3`, `v3/python/websocket/data_socket/data_websocket_foreground.py`)
   — the *only* websocket sample for market data imports `data_ws.FyersDataSocket`.
   The docs page for Symbol Update links to this repository as
   *"Data WebSocket Sample Scripts"*.
3. **The reference SDK's own changelog** (`fyers-apiv3` on PyPI, published by
   Fyers-Tech / support@fyers.in) — *"**New Data Socket:** … Increased
   subscription capacity, accommodating tracking of 200 symbols."*
4. **A third-party SDK that mirrors the reference SDK byte-for-byte**
   (`fyers-rs`, docs.rs) — states that the binary protocol at
   `wss://socket.fyers.in/hsm/v1-5/prod` is *"the **documented Fyers V3 binary
   protocol**"*, names the service **DataSocketService** / `SocketKind::Data`,
   and states its builders *"mirror the reference Python SDK
   (`fyers_apiv3.FyersWebsocket.data_ws`) byte-for-byte"*.

**Conclusion:** the `hsm` path segment is a legacy URL artefact, **not a
different socket family**. The official Data Socket symbol limits —
**Standard 100 / Prime 5 000, counted per user across all connections** —
apply to the socket MarketHub uses. MarketHub's declared FYERS capacity of
**100** is therefore **correct in value**.

**Residual uncertainties (recorded, not dismissed):**

| # | Uncertainty | Status |
|---|---|---|
| U-1 | The official docs pages do not print the `wss://` URL; the mapping rests on the protocol-identity match plus the reference SDK's URL. | **Low.** Three independent chains agree. The first-party SDK source would settle it outright — a maintainer can confirm in one file read (`fyers_apiv3/FyersWebsocket/data_ws.py`). |
| U-2 | The reference SDK changelog says *"accommodating tracking of **200** symbols"*; the current docs say **100** (Standard) / **5 000** (Prime). | **Medium, dated.** The changelog entry belongs to the v3 SDK release era. The docs page (*"Last updated on October 14, 2026"*) is authoritative for the current limits. |
| U-3 | FYERS' own `FyersDev/fyers-skills` reference doc says *"**Limit: 5000 symbols per connection**"* for the data socket — conflicting with the docs page's *"per user, across all your connections"* and its 100-symbol Standard limit. | **Medium.** A first-party repository contradicting the first-party documentation. The **documentation page governs**; the repo phrasing is wrong. Recorded because it is the kind of contradiction that leads an implementer to the wrong number. MarketHub opens **one** FYERS socket, so the per-user vs per-connection distinction is currently moot — but it must be stated correctly in code. |

**What survives from V5's R-05, reclassified:**

* **R-05a (S4)** — `brokers/fyers/capabilities.py:35-38` documents the limit as
  *"100 symbols/**connection**"*; the official wording is *"per user, across
  all your connections, not per connection"*. A wrong scope in a comment that
  engineers will read when sizing a budget. (The **value** 100 is correct.)
* **R-05b (S2)** — the **100 is a plan-tier default, not a confirmed
  entitlement.** No account is authenticated on either broker (§3.4). The FYERS
  entitlement must be marked *unverified* and must not be presented as
  confirmed anywhere in the plan.
* **R-15 survives and is strengthened** — `brokers/fyers/tbt/feed.py:44-47`
  declares `MAX_CONNECTIONS_PER_USER = 3` / `SYMBOLS_PER_CONNECTION = 5` with
  the comment *"Official rate limits (verified from fyers-apiv3 implementation)"*
  — the **Prime** numbers, taken from an implementation rather than from the
  documentation. The official page gives **Standard = 1 symbol** and Prime = 15
  (3 connections × 5). TBT is opt-in (`tbt_enabled`, absent from the current
  `config.json`), so this is latent today — but a Prime-tier limit is embedded
  in an adapter whose comment claims official provenance.

### 3.4 Capacity matrix

| Dimension | Upstox | FYERS Standard | FYERS Prime | MarketHub implementation |
|---|---|---|---|---|
| **WebSocket mode actually requested** | `full` — `config.json:120,146`; default `sources/registry.py:123`; feed default `feed.py:161` is `ltpc` (mismatch with the registry's `DEFAULT_MODE="full"`) | **Data Socket** (endpoint `wss://socket.fyers.in/hsm/v1-5/prod` — see §3.3) | same | upstox `full`; fyers Data Socket (`data_ws.FyersDataSocket` protocol: Join/Sub/Ping, `sf`/`dp`/`if`, `hsm_key` auth, channel 11) |
| **Provider symbol limit (feed)** | `full`: **2 000** individual / 1 500 combined, **per user** (Normal, 2 connections/user) | **Data Socket 100**/user, across all connections | Data Socket **5 000**/user | upstox declares **2 000** ✓ (`brokers/upstox/capabilities.py:28`); fyers declares **100** ✓ **value correct** — but the code comment mis-states the scope as "per connection" (**R-05a**) and the tier is **not a confirmed entitlement** (**R-05b**) |
| **Combined / category limit** | LTPC 5 000→2 000 · OptionGreeks 3 000→2 000 · Full 2 000→1 500 · FullD30 50→1 500 (Plus) | n/a — single socket family | n/a | **not modelled** — only the individual limit is represented (`MODE_KEY_LIMITS`) |
| **Connection limit** | 2/user Normal · 5/user Plus | Data + General: 1 instance at a time; TBT has its own | same | 1 upstox feed + 1 fyers feed (+ optional TBT) — within limits |
| **REST request limits** | The documented bucket is *"Other Standard APIs (holdings, positions, funds, historical candles etc.)"*: 50 rps · 500 rpm · 2 000 / 30 min, per-API per-user **[UPX-7]**. **Applying that bucket to the market-data endpoints is an ASSUMPTION** — they are not enumerated and publish no limit of their own. **market-quote ≤ 500 instruments per request** **[UPX-2]** | Data APIs: 5 rps · 50 rpm · **5 000/day**; **Quotes ≤ 50 symbols/request** (Prime 1 000) **[FY-1]**, **[FY-2]** | 10 rps · 500 rpm · 500 000/day; Quotes ≤ 1 000 symbols/request **[FY-1]** | Fyers REST client exists (`brokers/fyers/rest.py`); the **daily 5 000 pool is shared** by index polling + chain + history and is **not budgeted anywhere** (R-08) |
| **Configured effective budget** | `effective_budget(2000) = 1800` → `min(1800, 500) =` **500** | `effective_budget(100) = 50` → `min(50, 500) =` **50** | plan override 5 000 → `effective_budget(5000) = 4500` → `min(4500, 500) =` **500** | `providers/capacity.py:59-61`; `providers/capabilities.py:48-62` |
| **Reserve / safety margin** | `max(10 % × 2000, 50) = 200` (10 %) | `max(10 % × 100, 50) = 50` (**50 %**) | `max(10 % × 5000, 50) = 500` (10 %) | `RESERVE_FRACTION=0.10`, `RESERVE_FLOOR=50` — a **fixed floor applied to every capacity**, so a 100-symbol entitlement is halved (R-06) |
| **Application-wide ceiling** | 500 (`app/subscriptions/service.py:31`, `providers/capacity.py:25`) | same 500 | same 500 | enforced **only** in `SubscriptionService.resolve()` |
| **Account entitlement confirmed?** | **NO — UNVERIFIED.** Upstox not authenticated (`last_auth_status=rejected`, PROJECT_CONTEXT:1628-1629) | **NO — UNVERIFIED.** Fyers tokens present but the stored `app_id` is non-numeric and rejected with HTTP 403 / code 1010 (PROJECT_CONTEXT:1612-1627) | **NO — UNVERIFIED** | Neither plan tier can be confirmed without a login. **The 100 / 5 000 figures are documented *plan tiers*, not this account's entitlement.** Every downstream number in this plan is therefore **conditional on the account's tier** and is presented that way. No live probe was performed. |
| **Evidence and remaining uncertainty** | Feed limits: official V3 feed doc, 2026-10-20 — **high confidence**. Per-user vs per-connection: well-supported, not stated as explicitly as FYERS' — **low residual**. Entitlement: **unverified**. | Symbol limit: official plans page + Data Socket Overview, 2026-10-20 — **high confidence**; the endpoint↔Data-Socket mapping is resolved (§3.3) with residual U-1/U-2/U-3. Entitlement: **unverified**. | same as Standard. | The application's declared values are **correctly derived from the official documentation**. Two defects survive: the comment's scope is wrong (R-05a) and the tier is assumed, not confirmed (R-05b). The reserve floor still distorts small entitlements (R-06) — that, not the entitlement, is now the FYERS capacity problem. |

### 3.5 The capacity model — driven by provider capacity, not by an application ceiling

**V9 reframes this section.** V5–V8 spent their effort on whether the
application ceiling should be 500 or 800. That was the wrong question: the
ceiling was never the thing that limited coverage, and it was the thing that
*caused* the failure. This section replaces the ceiling-driven model with a
**provider-capacity-driven** one, in which the binding number is what the
provider actually permits, and MarketHub's application-wide ceiling is
**redundant** (§3.5b).

#### 3.5a The seven concepts, and the one formula

| # | Concept | Definition | Value here | Status |
|---|---|---|---|---|
| **1** | **Documented provider limit** | the vendor's published limit **for the actual socket, subscription mode and plan tier** | Upstox `full`: **2 000** individual / **1 500** combined, per user, 2 connections/user (Normal) **[UPX-1]** · FYERS Data Socket: **100** (Standard) / **5 000** (Prime), per user across all connections **[FY-1]**, **[FY-6]** | **CONFIRMED documentation** (§3.1, §3.2, §3.14, checked 2026-10-20). **Caveat (C7):** Upstox `full` is *assumed* tier-independent — the Plus table adds only `full_d30` and does not restate `full` **[UPX-1]**, so that is an **assumption [A]**, not a documented fact |
| **2** | **Account entitlement** | what *this* authenticated account may actually use | **NOT ESTABLISHED** — no session on either broker; the FYERS stored credentials are rejected (403/1010) and Upstox is unauthenticated | **UNVERIFIED** — every FYERS figure below is conditional on it |
| **3** | **Safety headroom** | a deliberate margin over (1), with a stated rationale | `reserve = min( max( ceil(entitlement × 10 %), floor ), entitlement − 1 )`, `floor` = **2 recommended** (current code: 50) | **POLICY — M-5**; the *fraction* is deliberate, the *floor* is the open parameter (§3.6) |
| **4** | **Usable provider capacity** | what MarketHub is permitted and designed to hold: `entitlement − reserve` | Upstox **1 800** · FYERS **90** (Standard) / 4 500 (Prime) | **DERIVED** from (1)+(2)+(3) |
| **5** | **Desired contract set** | the keys the resolved product scope requires | **659** = 220 universe spots + 439 nearest-two futures (the *streamed* subset; the option surface — 87 842 contracts over 689 chain cells — is served on demand, §3.12) | **MEASURED** (§3.12.1, §3.13) |
| **6** | **Resolved union** | the exact deduplicated key set requested by all six owners | **settled scope:** current upstox **517** / fyers **85** · capacity-first upstox **623** / fyers **84** (§3.13) | **MEASURED** (current) / **PROJECTED** (capacity-first, §3.13) |
| **7** | **Applied and dropped sets** | what is actually streamed, and what cannot be | **settled scope:** current **109** contracts dropped as `CAPACITY_LIMITED` by the planner, then the resolver *raises* · capacity-first: **0 dropped** | **MEASURED** (current) / **PROJECTED** (capacity-first) |

**The one formula — no application-wide constant appears in it:**

```
usable_provider_capacity(provider) = entitlement(provider) − reserve(provider)

reserve(provider) = min( max( ceil(entitlement × 10%) , floor ) , entitlement − 1 )
```

* Upstox `full` (entitlement 2 000, floor 2): reserve 200 → **usable 1 800**
* FYERS Standard (entitlement 100, floor 2): reserve 10 → **usable 90**
* FYERS Prime (entitlement 5 000, floor 2): reserve 500 → **usable 4 500**

**The hard backstop is the provider's own guard, not a MarketHub constant.**
`brokers/upstox/feed.py:188-191` already refuses to subscribe more keys than
the mode's documented limit, and the FYERS adapter's capability declaration
carries the socket's documented tier. Those are the only limits that can
actually stop a subscription; a MarketHub-wide number in front of them is
redundant (§3.5b).

#### 3.5b Does MarketHub need a separate hard application ceiling?

**Independently determined: no.** The evidence for removing it:

1. **It is redundant.** The provider entitlement (1) and the provider feed's own
   mode guard already bound what can be sent. A second MarketHub number in
   front of them can only be *tighter* (which is what happened) or looser
   (which is pointless).
2. **It was the proximate cause of the demonstrated failure.** The resolver
   enforced the ceiling (500) and **not** the per-provider allocation — so the
   FYERS feed was permitted **500** keys against an allocation of **50**, while
   the Upstox side was stopped by a ceiling that had no relationship to any
   provider limit. Two competing numbers, the wrong one enforced: exactly the
   collision that produced the 500.
3. **It protected against no demonstrated failure mode.** The failure that was
   demonstrated — the resolver **raising** and wedging the subscription service
   — is an **error-handling** defect, not a capacity defect, and is fixed by
   the approved overflow policy (P-5) and by atomic startup (W-11). Nothing in
   the evidence shows a failure caused by "too many keys for the provider":
   the planner's 500 Upstox keys were well inside Upstox's 2 000.
4. **It arbitrarily restricted usable capacity.** At 500 (or 800) the ceiling
   sat *below* Upstox's usable 1 800 — so capacity that the broker explicitly
   permits could not be used, in a product whose goal is *maximum safely usable
   capacity*.

**What replaces it.** Not a new number:

* the **per-provider usable capacity** (4) becomes the single enforced limit;
* the **provider feed's own mode guard** remains the hard backstop (already in
  code, provider-specific, not a MarketHub constant);
* a **loud, non-fatal diagnostic** replaces the silent truncation: if the
  resolved union exceeds the usable capacity by more than a small tolerance,
  that is a *bug* and must be reported as such — not silently capped and not
  raised into the request path.

**The maintainer's 500 → 800 decision is recorded as historical decision
context** (§2.2 D-2): it shows the intent — *more usable capacity than 500* —
and the capacity-first design delivers that intent more completely (1 800 /
90) by removing the arbitrary limit rather than moving it. **Removing the
ceiling is a change to existing behaviour and therefore requires the
maintainer's confirmation (M-2); this plan recommends it and does not presume
it.**

**Two ways this can be implemented, and the trade-off:**

| Option | Effect | Cost | Recommendation |
|---|---|---|---|
| **A. Remove the application ceiling** from the enforced path; enforce `usable_provider_capacity` per provider; keep the provider feed's mode guard; add a diagnostic assertion | Upstox may use up to **1 800** (its usable capacity); FYERS up to **90** (Standard) | Requires a maintainer decision to retire an existing guard (M-2); the `MAX_INSTRUMENTS_PER_PROVIDER` name disappears from the resolver | **RECOMMENDED** — it is the only option that both maximises usable capacity and keeps the limit meaningful |
| **B. Retain the ceiling but raise it to a value that cannot bind below the provider's usable capacity** (e.g. 1 800 for Upstox) | same coverage as A for Upstox; FYERS still capped by its own allocation | Keeps a redundant second number that must be kept in step with the entitlement and the reserve — the exact coupling that produced the bug | Acceptable **only** if the maintainer wants a belt-and-braces guard; it must be **derived from the entitlement**, never chosen independently |
| **C. Keep the ceiling at 500 / 800** | caps Upstox below its usable capacity; FYERS unaffected (its allocation binds first) | contradicts the product goal | **Not recommended** |

### 3.6 The safety headroom — is the 50-key floor necessary?

**The arithmetic**, from `providers/capabilities.py:42-62` with the FYERS
Data Socket Standard tier of 100 (§3.2):

```
capacity            = 100                       (the FYERS Data Socket Standard tier, concept 1)
reserve_fraction    = 0.10
reserve_floor       = 50                        ← the parameter under review
reserve             = min(max(100 × 0.10, 50), 99) = min(max(10, 50), 99) = 50
effective_budget    = max(1, 100 − 50) = 50      (concept 5 → the per-provider allowance)
provider_budget     = min(50, APP_CAP)          (concept 4)
                    = min(50, 500) = 50        ← the ceiling as implemented today
                    = min(50, 800) = 50        ← the DECIDED ceiling: **unchanged**
```

**Is the floor genuinely necessary? Its stated purpose says no.**

| Question | Answer | Evidence |
|---|---|---|
| Is a reserve needed at all? | **Yes**, and it is deliberate — the *fraction* is a policy: *"Application safety reserve — backend-controlled, NEVER user-editable. effective_budget = broker_capacity − max(10 % of capacity, 50)"* | `providers/capabilities.py:42-45` |
| What is the floor **for**? | *"Without this floor a capacity of 2 produced a budget of 0 and every contract was silently dropped."* Its job is to stop a **tiny** entitlement producing a **zero** budget. | `providers/capabilities.py:51-55` (docstring) |
| Does a floor of 50 serve that purpose? | **No.** Any floor ≥ 2 serves it. A floor of 50 is **50 %** of a 100-symbol entitlement and **2.5 %** of Upstox's 2 000 — it is a *margin* masquerading as a guard, and it was never recorded as a margin decision. | R3–R11 record no such decision; R7 recomputed the arithmetic as 50/4500/1800 and **accepted the number without asking what the floor does at small capacities** |
| What does the floor cost? | **FYERS coverage.** With usable 50, the FYERS union (63–85 under Scope-S1) **drops 13–35 keys**; with usable 90 it **drops none** and leaves 5–27 keys of failover headroom. | measured, §3.7.2 |

**Quantified comparison of headroom policies** (FYERS Standard entitlement 100):

| Policy | reserve | FYERS usable | FYERS keys dropped under Scope-S1 | FYERS failover headroom | Upstox usable (2 000) |
|---|---|---|---|---|---|
| current: 10 % with floor **50** | 50 | **50** | **13 – 35** | 0 | 1 800 |
| **recommended: 10 % with floor 2** | 10 | **90** | **0** | 5 – 27 | 1 800 |
| 10 % with floor 10 | 10 | 90 | 0 | 5 – 27 | 1 800 |
| 5 % with floor 2 | 5 | 95 | 0 | 10 – 32 | 1 900 |
| no reserve (not recommended) | 0 | 100 | 0 | 15 – 37 | 2 000 |

**Why a non-zero reserve is still the right policy** (the rationale that was
never written down):
1. **Reconnect overlap** — the new subscription set is sent before the old one
   is unsubscribed, so the broker can briefly count both.
2. **Counting differences** — a symbol subscribed in two data types, or a
   symbol counted once per request, can differ from MarketHub's count by a
   key or two.
3. **Headroom absorbs a single extra key** without breaking an entire
   subscription.

**Recommendation (requires M-5 approval).** Keep the deliberate **10 %
fraction**; **correct the floor to its stated purpose** — a small absolute
minimum (**2**) so a tiny entitlement can never produce a zero budget — and
make the floor a **declared, named parameter** rather than a magic constant.
Effect: FYERS usable **50 → 90** (Standard), Upstox unchanged at **1 800**;
the FYERS dropped set under Scope-S1 goes from **13–35 to 0**.

**The account tier is still not verified.** The 90 above assumes the **Standard**
tier (100 symbols). If the account is Prime, FYERS usable is 4 500 and the
question is moot; if the tier cannot be established, the plan must plan for
the **lower** tier (Standard) and carry the uncertainty explicitly. **Which
conclusions depend on the tier:** the FYERS usable capacity (90 vs 4 500), the
FYERS headroom, and therefore whether the FYERS side of the desired set can be
streamed without dropping. **The Upstox side does not depend on it — but only
*assumed* so** (C7, §3.14): the Plus table adds only `full_d30` and does not
restate `full`, so tier-independence is an **assumption [A]**, not a documented
fact **[UPX-1]**. The 1 800 usable figure and the "all 659 fit on Upstox"
conclusion therefore hold *conditionally* (gate **G-T**).

### 3.7 The resolved union — exact, owner by owner — **PRE-DECISION MEASUREMENT (historical)**

> **V11: this section is the measurement taken *before* the scope was settled and
> is retained for traceability only.** Its figures (221 universe spots, 441
> futures, a 662-contract desired set, 670 NSE chain cells) were computed
> against the **pre-decision** universe and **must not be used as the target**.
> The **settled** inventory is §3.12.1 and the **settled** capacity-first union
> is **§3.13**. The owner-by-owner method, the overlap findings and the
> single-union rule remain valid and are reused there; only the counts change.
>
> | figure | this section (historical) | settled basis |
> |---|---|---|
> | universe spots | 221 | **220** (BANKEX removed) |
> | nearest-two futures | 441 | **439** (BANKEX removed) |
> | desired contracts (streamed subset) | 662 | **659** |
> | NSE chain cells | 670 | **670** (unchanged) |
> | in-scope chain cells incl. SENSEX | — | **689** |
> | owner overlaps ①∩⑥ / ③∩⑥ / ④∩⑥ / ③∩④ | 7 / 1 / 1 / 4 | re-measured in §3.13 |

**V7 replaces every band in this section with exact counts** obtained by
running the *real* `SubscriptionService` and `FnoCoverageService` against a
**copy** of the live catalogue (142 932 rows), resolving the six owners
separately and de-duplicating with the **actual key semantics** — the resolver
de-duplicates by **exact provider key string** per provider
(`app/subscriptions/service.py:990-993`).

Because the streaming scope is **UNRESOLVED** (§2.4), the union is computed for
**two clearly labelled scenarios** and neither is presented as approved.

#### 3.7.1 The six owners and their exact contributions

Current state: the live database (`data/events.db` copy) — 9 enabled index
subscriptions, 0 stock subscriptions, 2 derivative rules, 0 analytics
subscriptions. `docs/current.md` §1 records the same 9/2/0 shape as
*"unchanged throughout"*, so it is a long-standing configuration, not a
side-effect of the audit.

| Owner | Source | Exact contribution (upstox / fyers) | Nature of the input |
|---|---|---|---|
| **① Index quote subscriptions** | `_index_keys()` — `app/subscriptions/service.py:410-435`, driven by the **enabled rows of `md_subscriptions` where category='index'** | **9 / 9** | **MEASURED** from the DB. **General quote subscriptions — NOT conditional on the F&O universe.** They are the user's index watch-list (INDIA VIX, SENSEX and BANKEX are all present). See §3.8. |
| **② Persistent equities** | `_stock_keys()` — enabled `md_subscriptions` rows where category='stock' | **0 / 0** | **MEASURED** (0 rows). **User-configurable** — unbounded in principle. |
| **③ Derivative rules** | `list_md_derivative_rules()` → `_resolve_futures` / `_resolve_options` | **13 / 4** = **17** | **MEASURED.** NIFTY: 2 futures + 10 option legs → upstox. BANKNIFTY: 1 future → upstox, 4 option legs → **fyers** (those strikes are FYERS-only in the catalogue). |
| **④ Active view** | `workspace_contracts()` → `_keys_by_provider` | **3 / 22 = 25** (RELIANCE) · **44 / 0 = 44** (NIFTY) | **MEASURED** for two named underlyings. The total varies with the underlying's **strike-ladder density** and the ATM: RELIANCE's ladder near its ATM yields 11 strikes (22 legs); NIFTY's yields 21 strikes (42 legs). The **provider split** varies with which strikes each provider lists (0 cross-provider strikes exist in the catalogue, so a CE and a PE at one strike always come from the same provider — but *different* strikes can differ). |
| **⑤ Analytics universe** | `set_analytics_universe()` — breadth / sector / market-map equities | **0 / 0** | **MEASURED** (not selected). **User-configurable.** |
| **⑥ Coverage planner** | `FnoCoverageService.plan()` → `plan.keys_by_provider` | **Scope-S1: 500 / 50** · **Scope-S2: 6–8 / 0** | Scenario-dependent — see §3.7.2. **The plan's own allocation is what it is; the union is what matters.** |

**Owner ① is a general quote subscription set, not an F&O concept.** The nine
index keys exist because the user has those indices enabled for quotes; they
are *not* gated on `market/index_registry.is_fno`. This is the correction the
V7 audit demanded: V6's §3.8 treated the nine base index spots as if the
F&O index decision governed them. It does not.

#### 3.7.2 Scope-S1 / Scope-S2 — **historical analysis, no longer decision gates**

> **V11: these two scenarios are no longer alternatives to be chosen between.**
> The maintainer stated the requirement on 2026-10-20 (D-1, D-4): the coverage
> is the **full F&O universe** — six NSE indices + SENSEX, all NSE F&O stocks,
> all contracts including option chains and futures — and BANKEX is excluded.
> The figures below are retained because they are the *measured* basis of the
> capacity-first design, and because the two scopes still differ in what the
> **streamed** surface must carry. They are **not** open decision gates
> (G-S is satisfied) and the plan does **not** present Scope-S2 as an
> acceptance target.
>
> | | Scope-S1 (**historical label**) | Scope-S2 (**historical label**) |
> |---|---|---|
> | What it meant | universe spot + nearest-two futures **streamed**, tail on demand | index spot + active view only |
> | Standing now | **the coverage scope** (the requirement) | **the streamed subset** if the requirement were interpreted narrowly — superseded |



*Everything in this subsection is computed for **Scope-S1** (the locked
"spot + nearest two futures" position) unless a row says otherwise, because
that is the scope the current implementation realises. The Scope-S2 numbers
are the **hypothetical** alternative and stay labelled as such until M-1 is
resolved.*

| | **Scope-S1** (P-1 / P-2 / P-6 — the as-built and settled position) | **Scope-S2** (P-3 / P-4 — the R5 paragraph) |
|---|---|---|
| Coverage planner streams | the universe's **spot + nearest two futures** (221 + 441 = 662 contracts) | the **F&O index spots** only; the universe is REST-on-demand |
| Owner ⑥ contribution | upstox **500** (its allocation), fyers **50** | upstox **6–8** (all already present in owner ①), fyers **0** |
| **Union, active view = RELIANCE** | upstox **516–518** · fyers **85** | upstox **26** · fyers **35** |
| **Union, active view = NIFTY** | upstox **554–556** · fyers **63** | upstox **63** · fyers **13** |
| Against the **allocation** (upstox `min(1800, ceiling)`, fyers **50**) | upstox exceeds by **16–56**; fyers exceeds by **13–35** | upstox **26–63** (within); fyers **13–35** (within) |
| Against the **application ceiling** | **the 500 ceiling binds on Upstox** (overflow 16–56); it does not bind on FYERS, whose 50-key allocation binds first | neither binds |
| Against the FYERS entitlement (100, Standard — *unverified*) | 63–85 → within 100 | 13–35 → within 100 |

*Ranges are across the three index alternatives of §3.8, which change **which**
contracts are streamed, not how many. The **current working-tree** state
(alternative C) gives **516 / 85** (RELIANCE) and **554 / 63** (NIFTY); the
six-index alternative A gives **518 / 85** and **556 / 63**. The lower bound of
**16** (not 18) comes from alternative C: the earlier prose figure of "18–56"
took alternative A's RELIANCE value as the floor and was **wrong**.*

#### 3.7.2a The four states, separately computed

**Every figure below is measured; nothing is extrapolated.** "Excess" is
`union − allocation`; "dropped" is what the approved drop-by-priority rule
(P-5, W-08) would actually drop.

**Scope-S1 — Upstox**

| Index alternative | View | **1. Current** (working tree) | **2. After W-03** (NIFTYFPI canonical) | Excess over allocation @500 | Excess over allocation @**800** |
|---|---|---|---|---|---|
| A six NSE | RELIANCE | 518 | 517 | 18 → 17 | **0** |
| A six NSE | NIFTY | 556 | 555 | 56 → 55 | **0** |
| B +SENSEX | RELIANCE | 517 | 516 | 17 → 16 | **0** |
| B +SENSEX | NIFTY | 555 | 554 | 55 → 54 | **0** |
| C +BANKEX (current tree) | RELIANCE | 516 | 515 | 16 → 15 | **0** |
| C +BANKEX (current tree) | NIFTY | 554 | 553 | 54 → 53 | **0** |

⇒ current **516–556**, after W-03 **515–555**; excess over the 500 allocation
**16–56** (current) / **15–55** (post-W-03); excess over the **decided 800
ceiling: 0**.

**Scope-S1 — FYERS** (identical in every alternative, and unaffected by W-03)

| View | **1. Current** | **2. After W-03** | Excess over allocation (50) | Excess over entitlement (100) | Excess over ceiling (500 **or** 800) |
|---|---|---|---|---|---|
| RELIANCE | 85 | 85 | **35** | 0 | 0 — the 50-key allocation binds first |
| NIFTY | 63 | 63 | **13** | 0 | 0 |

**Scope-S2** (the R5 scope) — no overflow anywhere:

| View | Upstox current → after W-03 | FYERS current → after W-03 | Excess over allocation |
|---|---|---|---|
| RELIANCE | 26 → **25** | 35 → 35 | 0 |
| NIFTY | 63 → **62** | 13 → 13 | 0 |

#### 3.7.2b What the states mean, and what changes at each step

| State | How it is obtained | Upstox (Scope-S1, RELIANCE / NIFTY) | FYERS | Dropped |
|---|---|---|---|---|
| **1. Current** | the working tree: planner budget 500/50, application ceiling 500 | 516 / 554 | 85 / 63 | **112** marked `CAPACITY_LIMITED` by the planner, then the resolver **raises** |
| **2. Identity-corrected** (W-03 only) | owner ①'s NIFTYFPI key replaced by the catalogue key ⇒ the dead key disappears and the two owners' keys coincide | 515 / 553 (**−1**) | 85 / 63 (**unchanged** — the FYERS NIFTYFPI key is not in the plan) | as state 1 |
| **3. Capacity-first** (W-07 + W-08 + W-10; **PROJECTED**) | the planner describes the **full 662** desired contracts; the resolver routes them across the eligible providers subject to **usable capacity** (Upstox 1 800 · FYERS 90), then fits the union | **637 / 674** | **85 / 63** | **0** — 0 `CAPACITY_LIMITED` |
| **3′. Capacity-first with the current floor 50** | as state 3, but FYERS usable stays 50 | **672 / 687** | **50 / 50** | **0** — the 112 "dropped" contracts simply move to Upstox |
| **4. Hypothetical — Scope-S2** | if M-1 resolves to the R5 position | **25 / 62** | **35 / 13** | **0** |

**Four consequences, and they are the substance of the V9 audit.**

1. **The 112 `CAPACITY_LIMITED` contracts are a *budget artefact*, not a
   capacity limit.** The planner already describes all **662** desired
   contracts (`plan.desired = 662`); it assigns only 550 because its per-provider
   budget ran out. Upstox's usable capacity (**1 800**) would accommodate the
   entire universe on its own. Removing the artificial ceiling and letting the
   resolver route across both providers therefore **delivers full coverage**,
   and it does so **even with the current floor-50 reserve**.
2. **The FYERS side, not the Upstox side, is where the reserve policy bites.**
   With usable 50 the FYERS union (63–85) does not fit; with usable 90
   (floor 2) it fits with 5–27 keys of failover headroom. **The 50-key floor —
   not the application ceiling — is what costs FYERS coverage.**
3. **The application ceiling is not what limits coverage and is not what
   protects anything** (§3.5b). Removing it (option A) *is* what makes the
   Upstox headroom usable; retaining it as 500 or 800 keeps Upstox artificially
   capped below 1 800.
4. **The scope (M-1) still decides the FYERS outcome** — under Scope-S1 FYERS
   is at 63–85 keys, which needs usable ≥ 85 to fit without dropping; under
   Scope-S2 it is 13–35 and always fits. **The ceiling decision does not
   resolve the scope, and the scope decision does not resolve the reserve
   policy.**

**What is *not* claimed any more (V8 withdrawals).** V6's claim that "the 50-key
budget sits below the 35–43 demand band" was arithmetically wrong (50 > 43)
and is withdrawn; V6's "≈ 22–64 / 35–43" bands are withdrawn (incomplete owner
list, mis-stated provider split for owner ④, unresolved scope); and V6's
conclusion that *"the capacity failure is a scope defect, not a cap-value
defect"* is **withdrawn**: the failure has **two independent causes** — the
resolver's `raise` instead of graceful degradation (R-01), and a scope that is
unresolved (M-1) — and the application ceiling is a **third**, separate lever
that the maintainer has already moved.

**A "wedged" service is not a scope problem.** Under Scope-S1 the correct
behaviour is graceful degradation; under Scope-S2 the union fits. The
resolver must implement the former so that *either* answer works.

#### 3.7.3 Overlap between owners (why a single-owner budget is wrong)

Measured with the real key semantics, per provider, Scope-S1:

| Overlap | RELIANCE view | NIFTY view | Consequence |
|---|---|---|---|
| **① ∩ ⑥** — index spot subscribed by the general index subscription *and* routed by the coverage plan | **7** (of owner ①'s 9) | **7** | The plan's index spots are **already** subscribed by owner ①. Budgeting ⑥ as 500 *and* ① as 9 double-counts 7 keys. |
| **③ ∩ ⑥** — NIFTY futures resolved by the derivative rule *and* routed by the plan | **1** (`NSE_FO\|48699`) | **1** | The **same contract** is resolved independently by two owners. |
| **④ ∩ ⑥** — the workspace's equity spot, already in the universe | **1** (`NSE_EQ\|INE002A01018`) | 0 (no equity spot for an index workspace) | — |
| **③ ∩ ④** — the rule's contracts also inside the active view | 0 | **4** (2 futures + 2 ATM option legs) | The same contracts are requested by two owners with **different windows**. |
| **Total duplicate keys** | **9** (525 → 516) | **12** (566 → 554) | — |
| FYERS (any view) | **0** duplicates (85 keys) | **0** duplicates (63 keys) | The FYERS owners happen not to overlap today — **not** a structural guarantee. |

**A sharp consequence of the same measurement.** Owner ①'s nine index keys
include `NSE_INDEX|Nifty FPI`, and the plan does **not** contain it — the plan
uses the catalogue's `NSE_INDEX|Nifty FPI 150`. So NIFTYFPI is subscribed
under **two different keys** by two different owners, and only one of them
exists in the catalogue (R-10). The other two owner-① keys absent from the
plan are `NSE_INDEX|Nifty FPI` and `NSE_INDEX|India VIX` — the latter correctly
so, since INDIA VIX is not an F&O underlying (R5 §1 B).

**This is why the union must be computed once** and both the planner and the
resolver must read it (W-08). A per-owner budget is arithmetically wrong
because owners overlap by design — and the overlap is *not* uniform across
providers or views.

### 3.8 The index universe (M-4) — measured effect on capacity: **zero**

**V6 claimed the three index alternatives change the requirement by "± 1 key
per provider". That was wrong, twice over.**

| Alternative | Source | F&O index set | Plan's index contribution (upstox / fyers) | **Union, Scope-S1 (RELIANCE)** | **Union, Scope-S2 (RELIANCE)** |
|---|---|---|---|---|---|
| **A** — six NSE | R5 §29 *"exactly the six entries in FNO_INDEX_LABELS"* | NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI | 6 / 0 | upstox **518** · fyers **85** | upstox **26** · fyers **35** |
| **B** — six NSE + SENSEX | R6 §155 | + SENSEX | 7 / 0 | upstox **517** · fyers **85** | upstox **26** · fyers **35** |
| **C** — six NSE + SENSEX + BANKEX | current working tree (**unstaged**) | + SENSEX, BANKEX | 8 / 0 | upstox **516** · fyers **85** | upstox **26** · fyers **35** |

**The same table for the NIFTY view (active view = an index, hence no equity
spot and 42 option legs):** Scope-S1 → upstox **554 / 555 / 556**, fyers **63**
for alternatives A / B / C; Scope-S2 → upstox **63**, fyers **13** for all three.

**Findings.**

1. **Under Scope-S2 the union is identical across all three alternatives
   (26 / 35 for the RELIANCE view; 63 / 13 for the NIFTY view).** The F&O index spots are a **subset of owner ①'s nine general
   index quote subscriptions**, so the plan contributes **no new keys at all**
   and M-4 has **exactly zero** effect on the requirement.
2. **Under Scope-S1 the union moves by one key per alternative (518 → 517 →
   516)** — and the movement is *downward* as indices are added, because the
   plan's allocation is fixed at 500 and each added index consumes plan
   capacity that would otherwise have gone to a stock contract. **The total is
   pinned by the allocation, not by the index set.** M-4 changes *which*
   contracts are streamed, not *how many*.
3. **M-4 is therefore a scope-integrity decision with no capacity
   consequence**, and must not be argued as a capacity question.
4. **BANKEX is not approved by its presence in the working tree.** R5's
   maintainer-directed scope correction places **BANKEX derivatives explicitly
   out of scope** — *"flagged for approval, not assumed … if the maintainer
   wants them, a separate approval is needed"* (R5 §1 C). The working tree's
   `is_fno=True` for BANKEX is an **unapproved expansion** (R-21). The
   **registry flags, the module docstring and the authoritative decision all
   disagree**: the code says BANKEX is F&O, the docstring says SENSEX/BANKEX
   are *"never part of the F&O universe"*, and R5 says BANKEX is out of scope.
   All three are recorded; none is silently preferred.

### 3.9 NIFTYFPI and the index scope — what M-4 actually changes

V7 asserted that the Scope-S2 coverage keys are "already present" in the
general index-subscription owner, **and** that NIFTYFPI resolves to different
keys in the two owners. Both are true, and together they mean the Scope-S2
union is **not** simply "unchanged" — it is *unchanged for the wrong reason*.
This section makes the discrepancy explicit.

**The two owners, and the two NIFTYFPI keys (measured):**

| Owner | NIFTYFPI key it uses | Exists in the catalogue? |
|---|---|---|
| ① general index quote subscriptions (`_index_keys()` → `MAJOR_INDICES`) | `NSE_INDEX\|Nifty FPI` (upstox) · `NSE:NIFTYFPI-INDEX` (fyers) | **NO — 0 catalogue rows** |
| ⑥ coverage planner (`_index_spot_keys()` → catalogue) | `NSE_INDEX\|Nifty FPI 150` (upstox) · `NSE:NIFTYFPI150-INDEX` (fyers) | **YES — 1 catalogue row** |

**Consequence 1 — the Scope-S2 union currently carries a dead key.** Because
the two owners' NIFTYFPI keys are *different strings*, the resolver's
de-duplication (exact provider key string) does **not** merge them: the union
contains **both** keys. The owner-① key can never deliver data, and the
plan's key is subscribed twice-over in intent. This is R-10, and it is the
reason the Scope-S2 union is **25** after W-03 but **26** before it.

**Consequence 2 — W-03 changes the union by exactly −1 key per provider.**
Replacing owner ①'s NIFTYFPI key with the catalogue key makes the two owners'
keys coincide: the dead key disappears and the valid key is counted once.
Measured: upstox Scope-S2 RELIANCE **26 → 25**, NIFTY **63 → 62**; Scope-S1
**516 → 515** and **554 → 553**. Duplicates rise by exactly 1 per provider
(the two now-coincident keys). FYERS is **unchanged** (85 / 63 / 35 / 13)
because the FYERS NIFTYFPI key is not in the Scope-S2 plan's key set.

**Consequence 3 — M-4 changes four different things, and only one of them is
a count.**

| What M-4 changes | Effect | Measured |
|---|---|---|
| **Which underlyings are classified `is_fno`** | the registry flag; and therefore which indices the coverage planner treats as F&O | 6 / 7 / 8 indices |
| **Which instruments the plan routes for those indices** | the plan's index **spot** and the index's **nearest-two futures** | +1 index ⇒ +1 spot +2 futures (upstox) |
| **Which provider keys are streamed** | a different index set means a different mix of spot and future keys — *which* contracts, not *how many* | see below |
| **The number of unique keys in the resolved union** | **Scope-S2: 0** (the plan's index spots are a subset of owner ①'s nine keys, except NIFTYFPI where the two differ by one string, and that difference is a *defect*, not a scope effect). **Scope-S1: −1 per added index** — the union is *pinned by the allocation*, so each added index displaces one stock future inside the plan's 500-key budget. | Scope-S1 RELIANCE: 518 → 517 → 516 for A → B → C |

**So the honest statement of M-4's capacity effect is:** *in Scope-S2 the count
of unique keys in the resolved union is unchanged by the index set — the
coverage planner's index spots coincide with the general index subscriptions
that already exist; in Scope-S1 the count is likewise unchanged (it is pinned
by the allocation) but the **composition** changes.* The one count that M-4
*does* move is the **−1 per added index in Scope-S1**, and it moves in the
opposite direction to intuition: more F&O indices means *fewer* stock
contracts in the stream, because the plan's budget is fixed.

**What this means for the acceptance criteria (V8 correction).** The
**current** counts (516/554, 85/63) are *not* valid post-fix expectations,
because W-03 changes identity and de-duplication. The **post-fix** expectations
are the "after W-03" column of §3.7.2a, and the **post-capacity** expectations
are the "streamed / dropped" columns of §3.7.2b. §8 is corrected accordingly.


### 3.10 The desired contract set, and the capacity available to place it

Everything above measures the *union* MarketHub asks for today. This section
measures the **target** — what the intended coverage requires — and the
**capacity available** to place it, independently of any budget.

**The desired set (measured, 2026-10-20, against a copy of the live
catalogue).** *V11: the figures below are the pre-decision, NSE-only
measurement; the settled in-scope figures are in §3.12.1 and the settled
capacity-first union is in §3.13.*

| Component | Count | Source |
|---|---|---|
| Universe spots (8 F&O index + 213 stock underlyings) | **221** | the coverage planner's own universe |
| Nearest-two futures per (exchange, underlying) | **441** | 435 NSE + 6 BSE contract groups |
| **Desired contract set** | **662** | `plan.desired = 662` — the planner **already computes this**; it assigns only 550 of them |
| Of which listed by **both** providers | **650** | measured against the catalogue, independent of any budget |
| Of which listed by **one** provider only | **12** (8 FYERS-only, 4 Upstox-only) | — |
| **Unroutable** (no provider lists them) | **0** | every desired contract can be placed |

**The capacity available to place them (concept 4):**

| Provider | Documented limit (1) | Entitlement (2) | Headroom (3) | **Usable capacity (4)** | Desired set (5) | Fits? |
|---|---|---|---|---|---|---|
| Upstox `full` | 2 000 (individual) / 1 500 (combined) **[UPX-1]** | **assumed** tier-independent — the Plus table adds only `full_d30` and does not restate `full` (§3.14) | 200 (10 %) | **1 800** | 659 | **yes, ≈ 1 141 spare** |
| FYERS Data Socket (Standard) | 100 | **UNVERIFIED** (G-T) | 10 (10 %, floor 2) | **90** | routable subset | **yes** for 63–85, with headroom |
| FYERS Data Socket (Prime) | 5 000 | **UNVERIFIED** (G-T) | 500 | 4 500 | — | yes, if the account is Prime |

**Reading.** The desired set (662) is smaller than Upstox's usable capacity
(1 800) on its own, so **no contract needs to be dropped for capacity
reasons at all** once the artificial per-provider truncation is removed. The
**only** place capacity binds is the FYERS side, and only because the FYERS
entitlement is a hundred symbols — which is why the *reserve policy* (M-5) and
the *account tier* (G-T) are the two decisions that actually matter for
coverage, while the application ceiling is not.

**And the part that is a projection, not a measurement.** The settled-scope projection is in **§3.13** (upstox **623**, fyers **84**,
**0 dropped**); the older pre-settlement figures in §3.7.2b are retained only
as labelled history. That projection assumes three things that are **not
true of the current code** and that the work items must make true:

1. the **planner stops pre-truncating** at its per-provider budget and
   describes the whole desired set (it already computes 659 on the settled scope —
it merely assigns 550);
2. the **resolver routes** the desired set across the eligible providers
   subject to usable capacity, instead of the planner assigning and the
   resolver merely checking (the routing sequence is §6.4);
3. the **entitlement is as documented** — measured for Upstox, **unverified**
   for FYERS (G-T).

**Therefore no claim of full coverage is made from the current tree.** What is
claimed is: *the capacity needed to hold the entire intended coverage exists
within the documented provider limits, and the only reason MarketHub drops
112 contracts today is a MarketHub-side budget artefact.*

### 3.11 The complete F&O data surface (the maintainer's requirement, 2026-10-20) — **SUPERSEDED IN PART BY §3.12**

> **V11: the three-surface model and the request-budget *shape* below stand, but
> three things in this section are corrected by §3.12 and must not be read
> from here:** (i) the **inventory** was the pre-decision, NSE-only measurement
> (219 underlyings · 670 NSE cells · 84 816 NSE option contracts) — the settled
> in-scope figures are **220 underlyings · 689 cells (670 NSE + 19 SENSEX) ·
> 87 842 option contracts** (§3.12.1); (ii) the **route for the FYERS-only
> tail is wrong** — this section proposed FYERS *batched quotes*, whose
> documented fields carry **no OI, previous OI, IV or Greeks**; the route is
> FYERS **market depth** (`oi`, `pdoi`), one symbol per request (§3.12.2); and
> (iii) the **budget percentages** below are per-item and exclude the existing
> index-poller consumption and retries — the **totals** are in §3.12.4; and
> (iv) the **cell and underlying counts** below (670 cells · 219 underlyings) are
> the **NSE-only, pre-settlement** figures — the settled in-scope figures are
> **689** cells (670 NSE + 19 SENSEX) over **220** underlyings (§3.12.1).



**The requirement, as stated by the maintainer on 2026-10-20:**
*"we need all the data for F&O trading — NSE major indices + NSE F&O stocks +
BSE SENSEX — and almost all stocks; NIFTY and BANKNIFTY covered; under NSE F&O
stocks we need all, including options chain + future."*

This resolves **M-1** (the coverage scope) and **M-4** (the index universe) in
one stroke, and it **adds a surface the plan did not previously design for**:
the plan treated "the broader option-chain tail" as on-demand REST without
ever sizing it. This section sizes it.

#### 3.11.1 The requirement decomposes into three surfaces with three different budgets

| Surface | What it is | Delivered by | Budget | Can it be streamed? |
|---|---|---|---|---|
| **A — the contract universe** | every listed contract: underlying, expiry, strike, CE/PE, lot, tick, freeze quantity | the **instrument masters** (already synced) + Upstox `/v2/option/contract` for freshness | no market-data rate limit (static files) | n/a |
| **B — the live option surface** | LTP, OI, volume, bid/ask, prev-OI, IV and greeks for **every** option contract | **REST chains + batched quotes**, cached | Upstox 50 rps / 500 rpm / 2 000 per 30 min · FYERS 5 rps / 50 rpm / **5 000/day** | **NO** — see 3.11.3 |
| **C — the streamed surface** | spot, futures and the focused ATM window | the websocket | Upstox `full` 2 000 keys/user · FYERS Data Socket 100/user | yes, within entitlement |

**The central engineering fact:** *all F&O data available* ≠ *all F&O data
streamed*. The unexpired NSE option universe alone is **84 816 logical
contracts** (measured, §3.11.2) — 42× Upstox's `full` entitlement and 848×
FYERS Standard's. Streaming it is impossible by a wide margin. It must be
**served on demand from a cache**, and the plan must therefore budget a
**REST data surface** it previously had no line item for.

#### 3.11.2 The inventory, measured (2026-10-20, live catalogue copy)

| Quantity | Value | Notes |
|---|---|---|
| NSE F&O underlyings with unexpired **options** | **219** | catalogue-derived, not hand-picked |
| NSE F&O underlyings with unexpired **futures** | **223** | |
| Logical NSE **option contracts** (unexpired) | **84 816** | 37 182 listed by both providers · **47 634 FYERS-only** · **0 Upstox-only** |
| Logical NSE **future contracts** (unexpired) | **665** | |
| **(underlying, expiry) chain cells** | **670** | 214 stock underlyings × 3 expiries + the index expiries |
| Expiries per underlying | stocks **3** · NIFTY **17** · BANKNIFTY **6** · FINNIFTY/MIDCPNIFTY/NIFTYNXT50/NIFTYFPI **3** | |
| Strikes per chain cell | min 16 · median 55 · p95 103 · **max 387** | 47 cells exceed 100 strikes |
| **BSE** option contracts (unexpired) | **5 126** | SENSEX/BANKEX, 4 underlyings, 19 expiries — **Upstox only** (§3.11.4) |
| **BSE** future contracts (unexpired) | **12** | **Upstox only** |

**NIFTY and BANKNIFTY specifically** (the maintainer singled them out):
NIFTY **17 expiries / 1 828 strikes**, BANKNIFTY **6 expiries / 1 188 strikes**.
They are 23 of the 670 chain cells but carry the **deepest ladders** — and the
deepest ladders are exactly where the two providers differ (§3.11.3).

#### 3.11.3 Provider asymmetry — the decisive finding for completeness

| | Upstox | FYERS |
|---|---|---|
| Option **listing** breadth | 37 390 option rows — a **strict subset** of FYERS's (0 contracts that FYERS does not list) | 85 286 option rows — **the superset** (47 634 contracts Upstox does not list) |
| **Chain** coverage per expiry | `/v2/option/chain` — **documents no strike cap** ⇒ its full *listed* ladder, including 387-strike expiries **[UPX-3]** | `/data/options-chain-v3` — `strikecount` **max 50 per side** ⇒ at most 100 strikes per call |
| Chain cells FYERS cannot fully return | — | **40 of the 670** cells that hold a FYERS-only symbol (the deep index ladders: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI) — the other 630 are fully served by the FYERS chain, and the **19 SENSEX cells hold no FYERS-only symbol at all** |
| Static contract list, one call per underlying | `/v2/option/contract?instrument_key=` — **all strikes, all expiries**, optional `expiry_date` | the synced `NSE_FO_sym_master.json` |

**Consequence: neither provider alone delivers the complete option surface, and
they fail in opposite directions.**

* For the **40 deep-ladder cells** that hold FYERS-only symbols outside the
  window (NIFTY/BANKNIFTY and the other indices) the **complete** chain is only
  available from **Upstox**; FYERS' 50-per-side cap truncates it.
* For the **47 634 contracts that Upstox does not list at all**, the only
  source is **FYERS** — and the chain endpoint will not return **5 934** of them
  either (they are outside its ±50-strike window), so they must be fetched as
  **batched quotes** (`/data/quotes`, ≤ 50 symbols/request ⇒ **119** requests)
  **and** per-symbol **market depth** (`/data/market-depth`, **1 symbol per
  request** ⇒ **5 934** requests), because quotes alone carry no OI.

⇒ **A completeness policy is a maintainer decision (M-12):** which provider is
authoritative for the chain, and how the FYERS-only tail is served (batched
quotes vs accepted as unavailable). The plan does not pick one silently.

#### 3.11.4 The request budget for a *complete* sweep — **superseded by §3.12.4 / §3.15 (V15)**

> **V15 correction.** This table is the **V10-era** budget and its
> request-count column is **wrong**; it is retained for the reasoning it
> records, not as the budget. The authoritative budget is **§3.12.4**, derived
> from the exact **689-cell inventory in §3.15**: the Upstox chain needs **37**
> requests (not 689 — only the cells whose Upstox-listed symbols fall outside
> the FYERS window need it), the FYERS chain needs **670**, the FYERS quotes
> are needed only for the **5 934** class-C symbols (**119** requests, not 953),
> and the FYERS depth costs **5 934** requests — one per symbol. The **FYERS
> Standard daily pool does not fit a complete pass** (7 011 > 5 000); the tail
> rotates over **≥ ≈1.7 days**, a lower bound.

| Sweep | Calls | Against the documented limit | Verdict |
|---|---|---|---|
| Upstox `/v2/option/chain`, every cell whose Upstox-listed symbols fall outside the FYERS window | **37** | **assumed** 2 000 per 30 min ⇒ **0.02 of one window** **[UPX-7]** | **comfortable — on an assumed limit** |
| Upstox `/v2/option/contract`, every underlying | **220** | trivial | **the cheapest complete-inventory path — and it is not implemented in MarketHub today (R-25)** |
| FYERS `/data/options-chain-v3`, every cell holding a FYERS-only symbol | **670** | Standard 5 000/day ⇒ **13.4 % of the daily pool** for the chain calls alone **[FY-1]** | **comfortable**; Prime 500 000/day ⇒ negligible |
| FYERS `/data/quotes` for the class-C tail (5 934 symbols) | **119** (50/request) | adds ≈ 2.4 % of the Standard pool | **price, volume and bid/ask only** — OI and previous OI need the per-symbol depth below — the documented quote response carries **no `oi`/`prev_oi`/`iv`/`greeks**; OI and previous OI for this tail need FYERS **market depth** (1 symbol/request, **5 934 calls** — one per unreachable option symbol). **Corrected in §3.12.2-3.12.4.** |

**And SENSEX is currently an Upstox-only dependency (V15).** Two first-party
sources confirm that FYERS supports BSE Equity Derivatives **[FY-7]** and that a
BSE SENSEX symbol is an accepted futures-chain argument **[FY-5]** — but the
**catalogue holds no FYERS BSE derivative rows at all** (FYERS: **70**
`BSE_INDEX` rows, **0** `BSE_FO` rows; Upstox: **5 126** `BSE_FO` option rows,
12 futures rows). So the 19 SENSEX cells are served by the **Upstox** chain
alone, they contribute **zero FYERS calls** to the budget, and the coverage
state must report SENSEX as a **single-provider dependency** rather than as
complete. **M-13** would add the `BSE_FO` master to the sync list; after that,
FYERS-only SENSEX symbols would exist and — if any fell outside the FYERS ±50
window — would **create** new class-C symbols. **The direction of the
dependency is the opposite of what V14 implied.** **SENSEX remains in scope
either way** (§3.11.6).

**So the requirement is affordable on both providers** — provided the sweep is
**prioritised and cached** rather than executed per user request, and provided
the daily pool is accounted for (§3.11.5). The earlier plan's assumption that
"the universe is on-demand" was directionally right but **never sized**; it is
now.

#### 3.11.5 What the data surface therefore requires (and what it does not)

1. **A prioritised, cached sweep**, not per-request fan-out: the open
   workspace's chain first, then the nearest expiries, then the tail; results
   cached against the **catalogue epoch** and a TTL, so the same sweep serves
   every user.
2. **A shared daily-budget accounting** for the REST surface (the FYERS pool
   is 5 000/day *shared* by quotes, depth, history, option chains and the index
   poller **[FY-1]**) with the tail explicitly deprioritised — and the
   *"if the per-minute rate limit is exceeded more than 3 times in a day, the
   user is blocked for the rest of the day"* consequence made explicit.
3. **An honest completeness state** per (underlying, expiry): *complete*,
   *window-truncated* (FYERS ±50 strikes) or *unavailable*, never silently
   presented as a complete chain.
4. **The streamed surface stays as designed** — spot + futures + the focused
   ATM window — because it is the only part that fits an entitlement. The
   requirement adds **availability**, not **subscriptions**.

#### 3.11.6 The BSE SENSEX dependency, and why it is a single-provider risk today

The catalogue holds **5 126 BSE option contracts and 12 BSE futures — all
Upstox**, and **zero** FYERS BSE_FO rows, because `FYERS_MASTER_URLS`
(`app/instruments.py:34-37`) lists `NSE_CM`, `NSE_FO`, `BSE_CM` and `MCX_COM`
but **not `BSE_FO`**. A read-only check of the public master URL shows
FYERS **does publish** `https://public.fyers.in/sym_details/BSE_FO_sym_master.json`
(the request returns a payload larger than the 5 MB fetch limit — it exists and
is large, unlike a segment that returns a small 404). **Adding that segment to
the sync list is therefore the likely fix** — and it would give the BSE
requirement a second provider instead of a single-provider dependency. Whether
the *data* (chains, quotes) for BSE F&O is also available from FYERS must be
confirmed (W-26).

### 3.12 The settled universe, verified field by field (V11)

Everything below was **recomputed on 2026-10-20 against a read-only copy** of
the live catalogue with the **settled** universe: the **six NSE F&O indices**
(NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI) **plus BSE
SENSEX**, and the **NSE F&O stock universe** from the catalogue read model
(`NSE_FO` derivatives joined to an `NSE_EQ` cash row). **BANKEX is excluded**
even though its instruments remain in a provider master and in the general
quote subscriptions.

#### 3.12.1 The corrected inventory (MEASURED)

| Quantity | Value | Basis |
|---|---|---|
| NSE F&O **stock** underlyings | **213** | catalogue read model (NSE_FO ⋈ NSE_EQ) |
| NSE F&O **index** underlyings | **6** (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI) | the approved set |
| BSE **SENSEX** | **1** underlying, **19** expiries | catalogue |
| In-scope underlyings carrying options | **220** (219 NSE + 1 BSE) | catalogue |
| Expiries per underlying | stocks: 210×**3**, 2×**2**, 1×**1** · NIFTY **17** · BANKNIFTY **6** · FINNIFTY/MIDCPNIFTY/NIFTYNXT50/NIFTYFPI **3** each · SENSEX **19** | catalogue |
| **(underlying, expiry) chain cells** | **689** = NSE **670** + SENSEX **19** | catalogue |
| Option contracts (CE+PE) in scope | **87 842** = NSE **84 816** + BSE **3 026** | catalogue |
| Distinct option strikes in scope | **43 973** | catalogue |
| Nearest-**two** futures per in-scope underlying | **439** (437 listed by both providers, 2 Upstox-only) | catalogue |
| In-scope spot underlyings | **220** (213 stocks + 6 NSE indices + SENSEX) | catalogue |

**Reconciliation of V10's arithmetic (the 670 / 677 discrepancy).**

* V10 reported **670** chain cells and, in prose, described them as *"214 stock
  underlyings × 3 expiries plus the index expiries"* — which reads as
  **677**. Both numbers were wrong in different ways, and the true figure is
  **689**:
  * **670** was the **NSE-only** count (the earlier measurement filtered
    `exchange='NSE'`), and it **excluded SENSEX's 19 cells**;
  * the "214 × 3" description was wrong: there are **213** stock underlyings
    (210 with 3 expiries, 2 with 2, 1 with 1 ⇒ **635** cells), and the six NSE
    indices contribute **35** cells (17+6+3+3+3+3);
  * **635 (stocks) + 35 (NSE indices) + 19 (SENSEX) = 689**.
* The "214 with 3 expiries" group is **210 stocks + 4 indices**, not 214
  stocks.
* **The 670 figure must not be carried forward**; every number in this plan now
  uses the **689** (in-scope) total and its **670 NSE / 19 BSE** split.

#### 3.12.2 Field coverage matrix (official documentation, checked 2026-10-20)

| Required field | Upstox `/v2/option/contract` | Upstox `/v2/option/chain` | Upstox `/v3/market-quote/quotes` | Upstox `/v3/market-quote/option-greek` | FYERS `/data/quotes` | FYERS `/data/options-chain-v3` | FYERS `/data/market-depth` |
|---|---|---|---|---|---|---|---|
| **Static contract metadata** (expiry, strike, CE/PE, lot, tick, freeze qty) | **✓** (all strikes + all expiries, 1 call/underlying) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| **LTP** | ✗ | ✓ | ✓ | ✓ `last_price` | ✓ | ✓ | ✓ |
| **Current OI** | ✗ | ✓ `oi` | ✓ `oi` (F&O only) | ✓ `oi` | **✗ not documented** | ✓ `oi` | ✓ `oi` **only when `oiflag` is true** |
| **Previous OI** | ✗ | ✓ `prev_oi` | ✓ `previous_oi` (F&O only) | ✗ | **✗ not documented** | ✓ `prev_oi` | ✓ `pdoi` **only when `oiflag` is true** |
| **Volume** | ✗ | ✓ | ✓ | ✓ `volume` | ✓ | ✓ | ✓ |
| **Bid / ask** | ✗ | ✓ | ✓ 5-level `depth` | ✗ | ✓ | ✓ | ✓ 5-level |
| **IV** | ✗ | ✓ `iv` | ✗ | ✓ `iv` | **✗ not documented** | ✓ `iv` | ✗ |
| **Greeks** (delta, gamma, theta, vega) | ✗ | ✓ | ✗ | ✓ | **✗ not documented** | ✓ (`greeks=1`) | ✗ |
| **Per-expiry / per-strike** | all expiries, all strikes | one expiry, all its **listed** strikes (documents no cap) | 500 keys/request | ≤50 keys/request | 50 symbols/request (Prime 1 000) | one expiry, **≤50 strikes per side** | **1 symbol per request** |
| **Expiry constraint** | optional `expiry_date` (date or relative keyword) | **required** `expiry_date`; **not available for MCX** | n/a | n/a | n/a | `timestamp` = *"Options chain data at timestamp"*; used as the expiry selector by MarketHub **convention [E]**, not by documentation; `expiryData[]` lists expiries | n/a |

**Corrections applied in V13 (C3, C4, C6, C8), with sources.**

* **C3 — the FYERS depth OI fields are conditional.** The Market Depth
  documentation defines `oiflag` as *"a true or false flag for open-interest
  data"*, and its own equity example returns `oi: 0, oiflag: false` **[FY-3]**.
  OI and previous OI must therefore be read **only when `oiflag` is true**; the
  matrix above now says so, and §3.12.5 gains a *provider-conditional* state.
* **C4 — the Upstox option-greeks endpoint is more than a Greeks endpoint.**
  It returns `iv, vega, gamma, theta, delta` **plus `oi, last_price, volume,
  cp, ltq`**, in ≤ 50 instrument keys per request **[UPX-5]** — so it is a
  **second OI source** for Upstox-listed contracts, and the matrix above now
  shows its LTP / volume / OI fields.
* **C6 — "no documented strike cap" is not "no cap".** The chain documentation
  publishes no cap **[UPX-3]**; the matrix wording is now *"all its **listed**
  strikes (documents no cap)"*, and §3.12.3 no longer asserts the absence of a
  limit as a fact.
* **C8 — two documented caveats added.** The Upstox chain is *"currently not
  available for the MCX Exchange"* **[UPX-3]** — no effect on the NSE/BSE scope,
  but it is now carried. And the FYERS `timestamp` parameter is documented as
  *"Options chain data at timestamp"* **[FY-4]**; using it to select an expiry is
  a **MarketHub convention**, empirically validated on 2026-10-20 by the live
  evidence artifact but **not** documented as an expiry selector — class **[E]**.

**Additional documented endpoints the matrix does not cover (V13).**

| Endpoint | Documented fields | What it does and does not change |
|---|---|---|
| Upstox `GET /v2/market/oi` **[UPX-6]** | `total_calls`, `total_puts`, `spot_closing_price`, `expiry`, and `call_put_oi_data_list[]` = **per-strike** `call_oi`/`put_oi`/`strike_price`; requires `instrument_key` + `expiry` + **`date`** (YYYY-MM-DD) | A **whole-expiry per-strike OI source**, keyed by *underlying + expiry + date* — **not** by instrument key, and with **no documented restriction to Upstox-listed instruments**. It therefore **may** cover strikes Upstox does not list; whether it does is **not documented either way**. **MarketHub already implements it** (`app/market_data.py:19`, `market/normalize/upstox_analytics.py:27`), together with `/v2/market/change-oi`, `/v2/market/max-pain` and `/v2/market/pcr`. **This changes the OI conclusion for the 2 969 uncovered strikes — see §3.12.3.** It carries **no IV or Greeks**, so that conclusion is unaffected. |
| FYERS `GET /data/futures-chain` **[FY-5]** | per contract `symbol`, `fyToken`, `lp`, `ch`, `chp`, `expiry` (epoch; empty for the underlying), `volume`; 1 underlying per request; *"Only currently available contracts are listed"*; documented examples include **`BSE:SENSEX-INDEX`** | **No OI, no bid/ask, no IV/Greeks.** It is a *discovery + light-quote* endpoint. **Evaluated against the existing futures path (§3.12.4b): it does not materially change the capacity analysis, and it is not adopted.** |

**Two corrections to V10 follow directly from the documentation.**

**Two corrections to V10 follow directly from the documentation.**

1. **V10's proposed route for the FYERS-only tail was insufficient.** It used
   FYERS **batched quotes**, whose documented response fields are
   `lp / bid / ask / spread / open_price / high_price / low_price /
   prev_close_price / atp / volume / tt / fyToken` and the CAS fields — **no
   `oi`, no `prev_oi`, no `iv`, no `greeks`.** Quotes alone therefore supply
   **price and volume only**. The documented route to OI and previous-OI for
   *any* FYERS symbol is **`/data/market-depth`**, which carries `oi`, `pdoi`
   and `oipercent` — but it is **one symbol per request**.
2. **IV and Greeks have exactly two documented sources**: Upstox's chain and
   option-greek endpoints (Upstox-listed instruments only) and FYERS's chain
   (**capped at 50 strikes per side** of ATM). There is no batched IV/Greeks
   endpoint at either provider.

#### 3.12.3 Reachability for the complete required field set (MEASURED)

| Reachability class | Strikes | Share | How it is served |
|---|---|---|---|
| **Full field set available** (LTP, volume, bid/ask, current OI, previous OI, IV, Greeks) | **41 004** | **93.25 %** | Upstox chain (its whole listed ladder) ∪ FYERS chain (±50 strikes per side of FYERS's ATM) — counted **per option symbol**, not per strike |
| **IV and Greeks unavailable** (LTP, volume, bid/ask available; OI and previous OI per the notes below) | **2 969 strikes / 5 934 symbols** | **6.75 % of strikes** | FYERS quotes (price, volume, bid/ask) + FYERS depth (`oi`, `pdoi` — **only when `oiflag` is true**); **no documented IV/Greeks source** |
| **Total in-scope strikes** | **43 973** | **100 %** | 41 004 + 2 969 = 43 973 ✓ |

**Correction (V12) — *historical*: the classes it discusses are superseded by the V15 measurement below.** V11 presented three classes — 41 988, 3 613 and 1 985 — as
if they were mutually exclusive and exhaustive. They were not: they summed to
47 586 against a 43 973 total, and the percentages summed to 108 %. The three
figures were **nested**, not parallel. The correct reading:

* `3 613` = the FYERS-listed strikes that fall **outside FYERS' ±50-strike chain
  window** (`fy − fy_window`). That is an **intermediate** measure, not a
  reachability class.
* Of those 3 613, **1 628 are still fully served** — Upstox lists them and
  Upstox's chain **documents no strike cap**, so they fall in the 41 988 class.
* The remaining **3 613 − 1 628 = 1 985** are the only strikes with no
   full-field source, and they are exactly the class that cannot supply IV and
  Greeks.

**V15 correction — the partition is re-measured per option symbol, and the earlier
strike-level figure is withdrawn.** The **1 985 strikes (4.5 %)** recorded above
was a **strike-level** measurement and **could not be reproduced** against the
current catalogue: it counted a strike as *served* whenever the strike *value*
appeared in either provider's ladder, even when one of its two legs was
unserved. Re-measured **per option symbol** against the same catalogue and the
same settled scope:

| Class | Strikes | Share | How it is served |
|---|---|---|---|
| **Every leg served** (both CE and PE have a full-field source) | **41 004** | **93.25 %** | the Upstox chain or the FYERS chain |
| **At least one leg unserved** | **2 969** | **6.75 %** | 2 965 strikes have **both** legs unserved; 4 strikes have **one** |
| **Total in-scope strikes** | **43 973** | **100 %** | 41 004 + 2 969 = 43 973 ✓ |

| Class | Option symbols | How it is served |
|---|---|---|
| Served by a chain response (all seven fields) | **81 908** | Upstox chain or FYERS chain |
| Not served by any chain response (**class C**) | **5 934** | FYERS quotes + FYERS depth; **IV and Greeks unavailable** |
| **Total in-scope option symbols** | **87 842** | 81 908 + 5 934 = 87 842 ✓ |

The two partitions are consistent and **reconcile exactly**: 2 965 strikes ×
2 legs + 4 strikes × 1 leg = **5 934 symbols**; 41 004 + 2 965 = 43 969
fully-served strikes, plus the 4 partially-served ones = 43 973 ✓. **The
withdrawn figure was 1 985 strikes; the reproducible figure is 2 969 strikes
carrying 5 934 symbols — the symbol count was already correct in V14, the
strike count and the 4.5 % share were not.**

**What the uncovered strikes are, and why each route fails.** They are contracts that
**FYERS lists and Upstox does not**, sitting **outside FYERS' ±50-strike chain
window** around ATM, i.e. the deep wings of the widest expiries. Routes
evaluated:

| Route | Why it fails for these contracts |
|---|---|
| FYERS `/data/options-chain-v3` | `strikecount` is capped at **50 per side**; the wing strikes are outside the window |
| FYERS `/data/quotes` | documented response has **no `oi`, `prev_oi`, `iv`, `greeks`** |
| FYERS `/data/market-depth` | gives OI/prev-OI but **no IV/Greeks**, and 1 symbol/request |
| Upstox `/v2/option/chain` | Upstox **does not list these instruments** — it returns only its own ladder |
| Upstox `/v3/market-quote/quotes` | same: the instrument key does not exist in Upstox's master |
| Upstox `/v3/market-quote/option-greek` | same — and it is restricted to Upstox instrument keys **[UPX-5]** |
| Upstox `/v2/option/contract` | returns Upstox's own (smaller) contract list — it cannot invent the missing ones **[UPX-4]** |
| Upstox `/v2/market/oi` **[UPX-6]** | **does not obviously fail for *current* OI** — it is keyed by underlying + expiry + `date` and returns **per-strike** `call_oi`/`put_oi`, with no documented restriction to Upstox-listed instruments. Whether its strike series extends beyond Upstox's instrument master is **not documented either way**, so this route is **not established** for the affected contracts rather than excluded. It carries **no previous-session OI, no IV and no Greeks**, so it **cannot** replace the FYERS depth cost and **does not** close the IV/Greeks gap. |

**Scope boundary of this gap (V16).** The gap below is a property of the
**deep-ladder tail only**. Under the maintainer's **confirmed** scope (D-23:
±20 around the true ATM, surface-wide, every cell) the affected strikes are
**out of the required surface**, and under **D-25** the tail itself is **out of
scope**: the class-C count for the required surface is **0**, every in-scope
symbol is served by one chain response, and the **depth and quote costs
disappear** (§3.16.2). **M-15 and M-16 are closed.** The gap remains a real
limitation **of the tail** and reopens only with it.

**The limitation is narrow and demonstrable, and its boundary must be stated
precisely (V14).** For **2 969 strikes = 5 934 option symbols** (2 965 strikes with both legs unserved, 4 with one), the *implied volatility and the Greeks* have **no documented source
among the endpoints evaluated** for the configured providers. This is an
**absence of a documented source in the evaluated endpoint set**, **not** proof
that no external market-data source could exist. The requirement stays closed:
these two fields are reported per contract as `field-unavailable`, and the
full-data acceptance criterion **stays blocked** until the gap is either
technically resolved or explicitly recorded as an unavoidable external-source
limitation. The concentration is
exactly where the maintainer pointed — the deep index wings: BANKNIFTY 2026-11-23 (**219**),
BANKNIFTY 2026-12-29 (219), BANKNIFTY 2026-10-27 (213), NIFTYNXT50 2026-11-23 (192),
NIFTYNXT50 2026-12-29 (192), NIFTYNXT50 2026-10-27 (175), MIDCPNIFTY 2026-10-27
(137), MIDCPNIFTY 2026-11-23 (137), NIFTY 2026-12-29 (132).

**Current OI and previous OI are different claims, and neither is proven for
this class (V14).**

| Field | Candidate source for the affected contracts | Status |
|---|---|---|
| **Current OI** | Upstox `/v2/market/oi` **[UPX-6]** — per-strike `call_oi`/`put_oi` for an underlying + expiry + `date`, **no documented restriction to Upstox-listed instruments**, and **already implemented** in MarketHub | **UNESTABLISHED** — the strike coverage outside Upstox's master is not documented. The probe must establish *strike coverage and response semantics*, not merely that an HTTP call succeeds |
| **Previous OI** | FYERS `/data/market-depth` (`pdoi`) — **only when `oiflag` is true** **[FY-3]**. Upstox's OI endpoint documents **no** previous-session OI | **NO PROVEN SOURCE** other than FYERS depth ⇒ the **FYERS depth cost cannot be replaced by Upstox OI calls** |
| **IV, Greeks** | none among the evaluated endpoints | **UNAVAILABLE** |

**This is an absence of a documented source among the endpoints evaluated for
the configured providers — not proof that no external market-data source could
exist.** It is **not** a reason to reopen the requirement: SENSEX and every
other in-scope underlying stay in scope, the affected fields are reported
per contract as *field-unavailable* / *field-unestablished* and never as
complete, and the **full-data acceptance criterion stays blocked** until the
gap is either technically resolved or explicitly recorded as an unavoidable
external-source limitation.

#### 3.12.4 The request budget, per provider (V15 — reconciled against the 689-cell inventory)

> **V17 status — this is the RE-ENTRY COST, not the current budget.** The
> maintainer's decisions of **2026-10-21** settle the scope: **D-23** (±20
> around the true ATM, surface-wide, every cell), **D-24** (no multi-level
> depth) and **D-25** (the deep-ladder tail is **out of scope**). The
> **required** surface therefore costs **670** FYERS chain calls + **19**
> Upstox chain calls per sweep, with **0** quotes and **0** depth calls
> (§3.16.5), and the FYERS Standard pool **fits**. The figures below are
> what the deep-ladder tail would cost **if it is brought back into
> scope**, and they are retained for that purpose only.

**V14's "77 chain requests" was wrong, and so was its label.** The **40** it
reported as "FYERS chain cells" is the number of cells that contain
*uncovered* contracts — **not** the number of FYERS chain requests. The
**corrected** derivation, from the exact 689-cell inventory (§3.15):

| Derivation | Count |
|---|---|
| Cells in scope | **689** |
| Cells that can be covered with **no** chain request | **0** — every cell holds contracts whose IV/Greeks only a chain endpoint can supply |
| Cells needing a **FYERS** chain request (they hold FYERS-listed symbols the Upstox chain cannot serve) | **670** |
| Cells *additionally* needing an **Upstox** chain request (Upstox-listed symbols outside the FYERS ±50 window) | **37** |
| Cells needing **both** | **18** |
| **Total chain requests for a complete-surface pass** | **670 + 37 = 707** |
| Cells containing symbols **no** chain endpoint can cover (class C) | **40** |

**There is no batching across cells and none is assumed.** Both chain endpoints
take **one (underlying, expiry) per request** — the Upstox chain requires
`expiry_date` **[UPX-3]** and the FYERS chain is addressed by an underlying
plus an expiry timestamp **[FY-4]**. A cell is therefore covered by **one
request per endpoint that serves it**, and the counts above are **requests**,
not cells.

**Per-provider budget for one complete-surface pass:**

| | **Upstox** | **FYERS Standard** | **FYERS Prime** |
|---|---|---|---|
| Chain requests | **37** | **670** | 670 |
| Quote requests (50 symbols/request) | 2 (659 keys ÷ 500) | **119** (5 934 ÷ 50) | 119 |
| Depth requests (**1 symbol/request**) | 0 | **5 934** | 5 934 |
| Existing index poller | — | ≈ **75**/gated day | ≈ 75/gated day |
| **Total per pass** | **39** | **7 011** | 7 011 |
| Pool | *assumed* 2 000 per 30 min **[UPX-7]** | **5 000/day** **[FY-1]** | **500 000/day** **[FY-1]** |
| Rate limit | *assumed* 500 rpm | **50 rpm** | **50 rpm** |
| **Fits the pool?** | yes — and the limit it is measured against is an **assumption** | **NO — 7 011 > 5 000** | yes (1.4 % of the pool) |
| Pass duration at the rate limit | seconds | **≈ 134 min** of continuous calling | ≈ 134 min |
| Passes/day possible | many | **0** (the daily pool binds first) | ≈ **10** (the 50 rpm limit binds) |

**The two providers' loads are separate and are never summed**: the FYERS pool
is charged only with FYERS calls, the Upstox pool with Upstox calls. The
**39** Upstox calls are **1 %** of the total workload and are **not** charged to the FYERS pool.

**Workload shape (V15, measured against the catalogue):**

| Quantity | Value |
|---|---|
| In-scope option contracts (**unique option symbols**) | **87 842** |
| — listed by **Upstox** (the Upstox chain serves their whole ladder) | **40 208** |
| — listed by **FYERS** but not by Upstox | **47 634** |
| — of those, **inside** the FYERS ±50-strike window (full field set from the **FYERS chain**) | **41 700** |
| — of those, **outside** it (class C: quotes + depth only, **no IV/Greeks**) | **5 934** |
| — Upstox-listed symbols **outside** the FYERS window (a *subset* of the 40 208 above — the reason 37 cells also need an Upstox chain) | **4 315** |
| Reconciliation | 40 208 + 47 634 = 87 842 ✓ (unique option symbols, no strike double-count) · 41 700 + 5 934 = 47 634 ✓ |

**Symbols vs strikes vs cells vs provider copies.** The 87 842 figure is
a count of **unique option symbols** — one row per (exchange, underlying,
expiry, strike, option type) — never of strikes and never of cells. A strike
may carry two symbols (CE and PE); a cell carries many; and a symbol may exist
in **one** provider's catalogue (Upstox-listed, FYERS-only) or in **both**
(the 40 208 Upstox-listed symbols, of which 4 315 are outside the
FYERS window). The chain response for one cell yields each of its symbols
**once**; nothing is double-counted across the two providers because the
FYERS-covered and Upstox-covered sets are disjoint by construction (§3.15).

#### 3.12.4a The freshness schedule, with what is actually reserved

Because a complete pass **7 011 > 5 000** on FYERS Standard, a daily full
refresh **cannot be claimed**. The surface is tiered, with an explicit daily
reservation per tier against the 5 000/day pool. The *cell* tiers are assigned
by expiry distance: **220** cells are the nearest expiry of each of the
220 underlyings (tier 1 — visible), **469** are the later expiries
(tier 2 — universe); tier 0 is a **runtime overlay** on whichever tier-1 cell
the user currently has open, and it is a subset of tier 1.

| Tier | Content | Requests per refresh | Daily reservation (Standard) | Deferred when the pool is exhausted |
|---|---|---|---|---|
| **0 — active workspace** | the open underlying's chain cell + its ATM-window symbols (quotes + depth) | ≈ 3 chain + ≈ 42 depth + 1 quote (FYERS); ≈ 3 chain + 1 quote (Upstox) | ≈ 276 (FYERS), ≈ 12 (Upstox) | **nothing** — tier 0 has first call on the pool |
| **1 — visible underlyings** | the nearest-expiry chain cell of each visible underlying | 1 chain per cell | ≈ 60 (FYERS) | tier 2 and tier 3 are deferred |
| **2 — universe cells** | the 469 later-expiry cells | 1 FYERS chain per cell | ≈ 670 if every chain cell is refreshed daily | rotated over days |
| **3 — the option tail** | the **5 934** class-C symbols' OI and previous OI (FYERS depth), in 40 cells (1 876 of them in tier 1, 4 058 in tier 2) | 5 934 depth calls | the residual | rotated over days — **≈ 1.7 days is a LOWER BOUND** |
| **existing** | FYERS index poller | — | ≈ **75** | never deferred (it is the index source) |

**The rotation figure is a lower bound, not a guarantee.** It is computed as

```
available for depth  =  5 000 (pool) − 75 (poller) − 670 (all chain cells, if refreshed daily)
                       − 119 (quotes) − 276 (tier 0) − 60 (tier 1)
                    =  3587 / day
tail rotation        =  5934 / 3587  =  ≈ 1.7 days
```

and it **assumes that every other FYERS consumer costs nothing**: history,
depth for other purposes, **retries**, and any consumer not yet measured
(§4.1). None of those is measured, so the true interval is **≥ the lower bound
and could be materially longer**. **The plan therefore states a target
freshness per tier and a *calculated lower bound* on the tail's interval, and
does not promise a guaranteed refresh cadence.**

**On FYERS Prime** the daily pool is not the binding constraint — the **50 rpm**
limit is: one pass is ≈ 134 minutes of continuous calling, so ≈ 10 passes/day
are possible and a **daily** full refresh is achievable. The Upstox side is
unaffected by any of this (**39** calls) but its rate limit remains an
**assumption** **[UPX-7]**.

**What would change the picture (evidence, not assumption).** If the Upstox
current-OI probe (§3.12.3) establishes that `/v2/market/oi` covers the affected
strikes **and** a previous-session OI can be derived for them, the
**5 934** depth calls could fall by roughly that number, the FYERS pass would
drop below the pool, and a daily rotation would become feasible. **That is a
probe outcome, not a plan assumption** — and it does **not** close the
IV/Greeks gap.

#### 3.12.4b FYERS `/data/futures-chain` — evaluated against the existing futures path (V13, item 4)

**Documented capabilities [FY-5].** `GET /data/futures-chain?symbol=<underlying>`
— one underlying per request; the response lists the underlying and its
**currently available** futures contracts, each with `symbol`, `fyToken`, `lp`,
`ch`, `chp`, `expiry` (epoch; empty for the underlying) and `volume`. The
documentation's own examples include **`BSE:SENSEX-INDEX`**, and the endpoint
states *"Only currently available contracts are listed"*. It carries **no OI, no
bid/ask and no IV/Greeks**.

**Evaluated against the plan's existing futures path — the existing path is:**

| Need | How the plan serves it today | Cost |
|---|---|---|
| The contract list (expiry, lot, tick, provider identifiers) | the **synced instrument masters** (static files, no market-data rate limit) | **0** market-data calls |
| Prices for the futures contracts | Upstox `/v3/market-quote/quotes` (500 keys/request) and the **streamed** futures (WS `full` carries `oi`/`prev_oi`) | ≈ 1–2 calls |
| "Is this contract actually tradeable right now?" | **not currently answered** — the master lists contracts that may not be trading | — |

**Does it materially change the capacity analysis? No.**

* **Call count:** for bulk pricing it is *worse*, not better — 220 futures-chain
  calls (1 per underlying) against ≈ 1–2 batched quote calls for all 439 futures
  contracts. It would not reduce the sweep.
* **Fields:** it carries no OI, so it cannot serve the OI requirement, and it
  adds nothing the quote path does not already carry for price and volume.
* **Freshness:** it *is* better than the master on one point — it reports
  **currently available** contracts, which the static master cannot.

**Therefore it is NOT adopted, and no architecture change is made.** It is
recorded as an **evaluated candidate** with one narrow, optional use: a
*currently-trading* check for the nearest-two-futures selection, so a listed but
untradeable contract cannot be chosen. That use is **a maintainer decision**
(M-14), not a design change, and it is explicitly **out of the current
dependency chain** — the plan's futures selection is unchanged.

**One incidental finding worth keeping:** the endpoint's documented
`BSE:SENSEX-INDEX` example is a **second, independent confirmation** of FYERS
BSE support (with **[FY-7]**'s Exchange-Segment Mapping), reinforcing the C5
reclassification below.

#### 3.12.5 Honest per-contract / per-field states (what the API and UI must report)

| State | Meaning |
|---|---|
| `complete` | every required field present, from one provider, within the freshness window |
| `complete-stale` | every field present but the cell has not been refreshed inside the TTL |
| `provider-truncated` | the provider's own window cut the strike set (e.g. FYERS ±50) — the cell is **not** the full ladder |
| `provider-missing` | the contract is not listed by the provider that would carry its fields |
| `field-unavailable` | the contract exists but a specific field (IV, Greeks) has no documented source |
| `field-unestablished` | a documented route for the field exists but its coverage for **this** contract is not documented (e.g. current OI for a strike outside Upstox's master, via `/v2/market/oi` **[UPX-6]**) — the field must be **probed**, not declared present or absent |
| `provider-conditional` | the provider documents the field but gates it behind a condition (e.g. FYERS depth OI is present only when `oiflag` is true **[FY-3]**) — absent, but not unavailable |
| `unavailable` | no provider source at all |

**No partial field set may be labelled a complete chain**, and `field-unavailable`
must be visible per field, not implied by a missing value.

### 3.13 The resolved subscription union — recomputed from exact keys (V14)

Recomputed with the **real** `SubscriptionService` / `FnoCoverageService`
against a **copy** of the live catalogue, on the **settled** scope (six NSE
indices + SENSEX, BANKEX excluded), with the **canonical NIFTYFPI identity**,
and with the six subscription owners kept separate. Three states are reported
and **not** interchanged.

| State | Upstox union | FYERS union | Total | Label |
|---|---|---|---|---|
| **1. Current** (static NIFTYFPI key, planner pre-truncating at its own budget) | **517** | **85** | **602** | **MEASURED** |
| **2. Post-W-03** (canonical NIFTYFPI key, planner still pre-truncating) | **517** | **84** | **601** | **MEASURED** |
| **3. Capacity-first** (canonical NIFTYFPI, planner describes the whole set, resolver fits it) | **623** | **83** | **706** | **PROJECTED** — routing and capacity behaviour are not implemented, so this is a projection, not a measurement |

**What changes between states 1 and 2, and why the Upstox count does not move.**
The identity fix (W-03) replaces owner ①'s static `NSE_INDEX|Nifty FPI` with the
catalogue's `NSE_INDEX|Nifty FPI 150` (and `NSE:NIFTYFPI-INDEX` with
`NSE:NIFTYFPI150-INDEX`). Before the fix, owner ①'s key is a **key that does not
exist in the catalogue** — it can never deliver data — and it does **not**
coincide with the coverage plan's key, so it occupies a slot in the union
*without* being valid coverage. After the fix it coincides with the plan's key
and is counted once. Net effect: the **FYERS** union loses the dead key
(**85 → 84**), the **Upstox** count is unchanged (**517**) but one of its keys
changes from *invalid* to *valid* — which is precisely the distinction the
capacity reporting must make.

#### 3.13.1 The exact composition of the post-W-03 capacity-first union (706)

**706 = 659 desired contracts + 47 other keys**, and the 47 decompose exactly:

| Class | Keys | Detail |
|---|---|---|
| **6 — desired contract set** (owner ⑥: the coverage plan) | **659** | 220 universe spots + 439 nearest-two futures. **Every one is a logical contract routed to exactly one provider** (604 Upstox + 55 FYERS) — no contract is assigned twice, and the resolver de-duplicates by exact provider key, so no provider key appears twice. |
| **3 — derivative rules' option legs** (owner ③) | **14** (10 Upstox + 4 FYERS) | *streamed option* instruments — they belong to the **option surface** (§3.12), not to the 659-contract set |
| **4 — active view's option legs** (owner ④) | **22** (FYERS) | the focused ATM window's CE/PE legs — same |
| **1 — second-provider copies of the F&O index spots** (owner ①) | **7** (Upstox) | the same canonical index, the *other* provider's key. `_index_keys()` deliberately emits **both** providers' keys for every enabled canonical index, so a canonical index can hold one key per provider. **This is a watch-list subscription, not a second owner of the same contract.** |
| **1 — general quote subscriptions outside the F&O universe** (owner ①) | **4** (INDIA VIX and BANKEX, one per provider) | **not F&O coverage at all** — they are general index quotes the user has enabled, and they are **not** in the F&O universe, so they must never be counted towards F&O coverage. |
| **Total others** | **47** | 14 + 22 + 7 + 4 = **47** ✓ · 659 + 47 = **706** ✓ |

**The "ten additional index keys" (V13's wording) decomposed.** V13 reported
"10 additional index keys" without classifying them. Exactly: **7** second-provider
copies of the F&O index spots (Upstox) + **2** general quote subscriptions
outside the F&O universe (Upstox: INDIA VIX, BANKEX) + **1** the dead FYERS
NIFTYFPI key (pre-fix only) = **10** pre-fix. Post-fix the dead key is gone,
leaving **9** index keys outside the plan, of which **4 are general quotes that
are not F&O coverage** and **5 are second-provider F&O index copies**.

**Reporting rule this imposes.** A F&O coverage figure must count **unique
valid provider keys** and must **exclude** (a) keys absent from the provider's
catalogue and (b) general quote subscriptions outside the F&O universe. The
current code can do neither, which is why the coverage API and W-03/W-08
must carry the distinction.

**Failover (R-30) is unchanged by this correction.** Upstox carries **623** of
the 659 desired contracts; the FYERS allocation is **90** in total, so on the
FYERS Standard tier **streaming the full universe and surviving an Upstox outage
remain mutually exclusive** — that is a property of the entitlement, not of the
identity fix.

---

### 3.14 API source register (V13)

Every API claim in this plan is traceable to one of these official sources.
**All were read on 2026-10-20**; the FYERS pages state *"Last updated on
October 14, 2026"*. Evidence classes: **[D]** documented fact · **[E]**
empirical convention, validated by the 2026-10-20 live evidence artifact but
**not** documented as such · **[A]** assumption · **[M]** measured against the
local catalogue.

| Key | Official source (read 2026-10-20) | Establishes | Class |
|---|---|---|---|
| **[UPX-1]** | [Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/) | WS limits: `full` 2 000 individual / 1 500 combined; `ltpc` 5 000/2 000; `option_greeks` 3 000/2 000; `full_d30` 50/1 500 (Plus); 2 connections/user (Normal), 5 (Plus); *"Individual Limit"* = single category, *"Combined Limit"* = mixed categories; CAS fields | **[D]** |
| **[UPX-2]** | [Full Market Quotes V3](https://upstox.com/developer/api-documentation/get-full-market-quote-v3/) | `GET /v3/market-quote/quotes`; **≤ 500** instrument keys; fields incl. `oi`, `previous_oi`, `depth` (5-level), `volume`, `ohlc`, `cas_*` | **[D]** |
| **[UPX-3]** | [Put/Call Option Chain](https://upstox.com/developer/api-documentation/get-pc-option-chain/) | `GET /v2/option/chain`; `expiry_date` **required** (date or relative keyword); per strike `market_data{ltp, close_price, volume, oi, bid/ask+qty, prev_oi}` + `option_greeks{vega, theta, gamma, delta, iv, pop}`; `pcr`; `underlying_spot_price`; **no strike cap documented**; **not available for MCX** | **[D]** |
| **[UPX-4]** | [Option Contracts](https://upstox.com/developer/api-documentation/get-option-contracts/) | `GET /v2/option/contract`; `instrument_key` required, `expiry_date` optional; **static only** (expiry, strike, CE/PE, lot, tick, freeze qty, weekly) | **[D]** |
| **[UPX-5]** | [Option Greek Fields](https://upstox.com/developer/api-documentation/option-greek/) | `GET /v3/market-quote/option-greek`; **≤ 50** instrument keys/request; fields `iv, vega, gamma, theta, delta` **plus `oi, last_price, volume, cp, ltq`** | **[D]** |
| **[UPX-6]** | [Get OI](https://upstox.com/developer/api-documentation/get-oi/) (and [Market Information](https://upstox.com/developer/api-documentation/market-information/)) | `GET /v2/market/oi`; `instrument_key` + `expiry` (required) + `date` (**required**, YYYY-MM-DD); returns `total_calls`, `total_puts`, `spot_closing_price`, `expiry`, and `call_put_oi_data_list[]` = **per-strike** `call_oi`/`put_oi`/`strike_price`. Siblings: `/v2/market/change-oi`, `/v2/market/max-pain`, `/v2/market/pcr` | **[D]** |
| **[UPX-7]** | [Rate Limits](https://upstox.com/developer/api-documentation/rate-limiting/) | *"Other Standard APIs (holdings, positions, funds, historical candles etc.)"*: **50 rps · 500 rpm · 2 000 per 30 min**, *per-API, per-user*. Market-data endpoints are **not enumerated**; their per-endpoint pages publish no limit of their own | **[D]** for the bucket; **[A]** for applying it to market data |
| **[UPX-8]** | [Exchange Status](https://upstox.com/developer/api-documentation/get-market-status) | `GET /v2/market/status/:exchange`; `data.status` (e.g. `NORMAL_OPEN`), `data.last_updated`; `cas_eligible_status` **omitted** for `NSE_FO` | **[D]** |
| **[UPX-9]** | [Market Timings](https://upstox.com/developer/api-documentation/get-market-timings) | `GET /v2/market/timings/:date`; per-exchange `start_time`/`end_time` — including **NFO** and **BFO** — for a given date | **[D]** |
| **[UPX-10]** | [Market Holidays](https://upstox.com/developer/api-documentation/get-market-holidays) | `GET /v2/market/holidays`; `holiday_type` ∈ `TRADING_HOLIDAY` / `SETTLEMENT_HOLIDAY` / `SPECIAL_TIMING`; per-date `closed_exchanges` and `open_exchanges[].start_time/end_time` | **[D]** |
| **[NSE-1]** | [NSE — Closing Auction Session](https://www.nseindia.com/static/products-services/closing-auction-session) | *"Continuous Trading for Non-CAS Securities: Session timings are 9:15 am to 3:30 pm. **Equity Derivatives Segment: Session timings are 9:15 am to 3:40 pm.**"* — effective **2026-08-03**; CAS 15:15–15:35, transition 15:35–15:50, post-close 15:50–16:00. *(Fetched 2026-10-21: the page timed out from this environment; the wording is quoted from the search index of that official page. The **runtime** source of truth is [UPX-9]/[UPX-10], which report the window per exchange and per date.)* | **[D]** |
| **[FY-1]** | [Plans & Limits](https://fyers.in/api-docs/v3/getting-started/plans-and-limits/) | Data APIs: Std **5 rps / 50 rpm / 5 000 per day**, Prime **10 / 500 / 500 000**; Transaction+User-Info Std 10/200/100 000, Prime 10/500/500 000; Quotes **50** symbols/request (Prime **1 000**); Data Socket **100** Std / **5 000** Prime; TBT **1** Std / **15** Prime; *"Symbol limits apply per user, across all your connections to that socket, not per connection"*; HTTP 429; *"if the per-minute rate limit is exceeded more than 3 times in a day, the user is blocked for the rest of the day"* | **[D]** |
| **[FY-2]** | [Quotes](https://fyers.in/api-docs/v3/api/market-data/quotes/) | `GET /data/quotes` (≤ 50 symbols) / `POST /data/quotes` (≤ 1 000 Prime); fields `lp, bid, ask, spread, ch, chp, open/high/low/prev_close_price, atp, volume, tt, fyToken`, CAS fields; `cmd` deprecated — **no `oi`, `prev_oi`, `iv` or `greeks`** | **[D]** |
| **[FY-3]** | [Market Depth](https://fyers.in/api-docs/v3/api/market-data/market-depth/) | `GET /data/market-depth`; **1 symbol per request**; `ohlcv_flag` required; fields `oi`, `oiflag` (*"true or false flag for open-interest data"*), `pdoi`, `oipercent`, 5-level `bids`/`ask`, `ltp/ltq/ltt`, `v`, `atp`, `o/h/l/c`, `ch/chp`, `tick_Size`, `lower_ckt`, `upper_ckt`, `expiry` | **[D]** |
| **[FY-4]** | [Option Chain](https://fyers.in/api-docs/v3/api/market-data/option-chain/) | `GET /data/options-chain-v3`; `symbol` required; `strikecount` = *"the number of strikes on each side of ATM (maximum 50)"*; `timestamp` = *"Options chain data at timestamp"*; `greeks=1` → nested `greeks{delta,gamma,theta,vega,iv}`; per leg `ltp, oi, oich, oichp, prev_oi, volume, bid, ask, iv`; `callOi`/`putOi` aggregates; `expiryData[]` (`date` DD-MM-YYYY, epoch `expiry`, `expiry_flag`); `indiavixData` | **[D]** |
| **[FY-5]** | [Futures Chain](https://fyers.in/api-docs/v3/api/market-data/futures-chain/) | `GET /data/futures-chain`; 1 underlying per request; per contract `symbol, fyToken, lp, ch, chp, expiry` (epoch, empty for the underlying), `volume`; *"Only currently available contracts are listed"*; examples include **`BSE:SENSEX-INDEX`**; **no OI, no bid/ask, no IV/Greeks** | **[D]** |
| **[FY-6]** | [WebSocket Overview](https://fyers.in/api-docs/v3/api/websockets/websocket-overview/) · [Data Socket Overview](https://fyers.in/api-docs/v3/api/websockets/data-socket/overview/) | Data Socket limits 100/5 000 per user across all connections; TBT 1/15; *"create only one WebSocket instance"*; message types `cn`, `sub`, `sf`, `if`, `dp`; `data_type` = `SymbolUpdate` / `DepthUpdate` | **[D]** |
| **[FY-7]** | [Exchange-Segment Mapping](https://fyers.in/api-docs/v3/api/appendix/available-exchange-segment-combinations/) · [Exchanges](https://fyers.in/api-docs/v3/api/appendix/exchanges/) | Supported pairs include **BSE \| Equity Derivatives \| exchange 12 \| segment 11**; exchange codes 10 NSE, 11 MCX, 12 BSE | **[D]** |

**Three inferences this plan makes from the sources above, and their class:**

| Inference | Class | Note |
|---|---|---|
| The Upstox *"Other Standard APIs"* bucket (50 rps / 500 rpm / 2 000 per 30 min) applies to market-quote, option-chain, contract, option-greek and market-information | **[A] — ASSUMPTION** | The bucket names *"holdings, positions, funds, historical candles etc."*; market-data endpoints are not enumerated **[UPX-7]**. Every Upstox-side sweep and freshness number that depends on it is therefore **assumption-based**, not documented. |
| Upstox `full` = 2 000 applies on **both** Normal and Plus | **[A] — ASSUMPTION** | The Normal table states 2 000/1 500; the Plus table adds only `full_d30` and does not restate `full` **[UPX-1]**. The plan's "the Upstox side does not depend on the account tier" is therefore an **inference**, not a documented fact. |
| FYERS `timestamp` selects the expiry when passed an expiry epoch | **[E] — EMPIRICAL CONVENTION** | The docs say only *"Options chain data at timestamp"* **[FY-4]**; the expiry-epoch usage is MarketHub's convention, validated by the 2026-10-20 live evidence artifact, **not** documented as an expiry selector. |

---

### 3.15 The 689-cell chain inventory (V15 — exact, per cell)

This is the **exact** inventory the §3.12.4 budget is derived from. Every cell
is one **(exchange, canonical underlying, expiry)**; the option symbols inside
it are the *unique option symbols* (exchange, underlying, expiry, strike,
option type) that the catalogue lists for that cell, across both providers.

**Derivation (read-only, against a copy of the catalogue).** For each cell the
inventory computes the FYERS ATM over the **distinct** FYERS strikes, takes the
documented **±50 strikes per side** window **[FY-4]**, and classifies every
symbol:

| Class | Meaning | Endpoint that serves it |
|---|---|---|
| **Upstox-listed** | Upstox lists the symbol | the **Upstox chain** for that cell **[UPX-3]** — it returns Upstox's whole listed ladder, with no documented strike cap |
| **FYERS-only, in window** | FYERS lists it, Upstox does not, and it is within ±50 strikes of FYERS's ATM | the **FYERS chain** for that cell **[FY-4]** — price, volume, bid/ask, OI, previous OI, IV and Greeks |
| **class C** | FYERS-only and **outside** that window | **no chain endpoint covers it** → FYERS quotes (price, volume, bid/ask) + FYERS depth (OI, previous OI **only when `oiflag` is true**) **[FY-2] [FY-3]**; **IV and Greeks have no documented source** |

**Both chain endpoints take one (underlying, expiry) per request** — the
Upstox chain requires `expiry_date` **[UPX-3]**, the FYERS chain an underlying
plus an expiry timestamp **[FY-4]**. **No request covers more than one cell**,
and no cross-cell batching is assumed anywhere in this plan.

**Column legend.** `sym` = unique option symbols in the cell · `U` = listed by
Upstox · `F` = FYERS-only · `win` = FYERS-only inside the ±50 window ·
`C` = class C (no chain endpoint covers them) · `chain` = which endpoint's chain
request the cell needs (`FY`, `FY+UP`, `UP`, `—`) · `full` = symbols whose
**seven** required fields come from a chain response · `part` = symbols served
by quotes + depth (seven minus IV/Greeks; OI/previous OI **conditional**) ·
`tier` = the freshness tier the cell belongs to (§3.12.4a) · `note` = a
conditionality marker.

**Reading the table.** `U + F = sym` (unique symbols, no provider double
count) and `full + part = sym` (complete field set vs partial). Every row
satisfies both identities; the totals are **sym 87 842**, **full
81 908**, **part 5 934**.

**Cells needing no chain request: 0.** There is no documented reason for a cell to need none: every cell contains at least one symbol whose IV/Greeks are only obtainable from a chain response, so no cell can be served by quotes and depth alone. This is the answer to the V15 question — the count is zero, not merely small.

**Cells needing a FYERS chain request: 670** (they hold FYERS-only
symbols the Upstox chain cannot serve) · **cells *additionally* needing an
Upstox chain request: 37** (they hold Upstox-listed symbols outside
the FYERS window) · **cells needing both: 18**.
**Total chain requests for a complete-surface pass: 707** — not
the 689 that V14 implied by mis-labelling the 40
class-C cells as the FYERS-chain cells.

**Freshness tier.** **220** cells are the nearest expiry of each of the
220 in-scope underlyings (tier 1 — visible underlyings); **469** are
the later expiries (tier 2 — universe cells). Tier 0 is a **runtime overlay**
on whichever tier-1 cell the user currently has open, not a separate set of
requests. Tier 3 (the class-C depth pass) is a **field tier** attached to the
40 cells that contain class-C symbols.

**BSE — SENSEX is currently an Upstox-only dependency (V15).** The catalogue
holds **no FYERS BSE derivative rows at all**: FYERS has **70** `BSE_INDEX`
rows and **zero** `BSE_FO` rows, against Upstox's **5 126** `BSE_FO` option rows
and 12 futures rows. Consequently **all 19 SENSEX cells are the 19 `UP`-only
cells in the table** — they need an **Upstox** chain request and **no FYERS
chain request**, and SENSEX's **3 026** option symbols are entirely
Upstox-listed. **SENSEX therefore contributes zero FYERS calls to the budget,
and its coverage is a single-provider dependency that must not be reported as
complete.** The FYERS BSE route is *conditional* in the opposite direction:
M-13 would add the `BSE_FO` master to the sync list, after which FYERS would
list SENSEX symbols that Upstox does not, and — if any of them fell outside
the FYERS ±50 window — would **create** new class-C symbols. **That is the
direction of the dependency, and it is the opposite of what V14 implied.**

**How this table stays true.** It is a **point-in-time measurement**, not a
hand-maintained inventory. The implementation (W-19, W-24) must regenerate it
from the catalogue and assert: the cell count; `U + F = sym` and
`full + part = sym` per row; the 670 / 37 chain-request
counts; and the class-C count. A catalogue refresh that changes any of these is
a **measured change**, and the budget in §3.12.4 must be regenerated with it.

| # | underlying | expiry | sym | U | F | win | C | chain | full | part | tier | note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | SENSEX | 2026-10-22 | 372 | 372 | 0 | 0 | 0 | +UP | 372 | 0 | T1 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 2 | SENSEX | 2026-10-29 | 422 | 422 | 0 | 0 | 0 | +UP | 422 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 3 | SENSEX | 2026-11-05 | 334 | 334 | 0 | 0 | 0 | +UP | 334 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 4 | SENSEX | 2026-11-12 | 310 | 310 | 0 | 0 | 0 | +UP | 310 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 5 | SENSEX | 2026-11-19 | 286 | 286 | 0 | 0 | 0 | +UP | 286 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 6 | SENSEX | 2026-11-26 | 388 | 388 | 0 | 0 | 0 | +UP | 388 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 7 | SENSEX | 2026-12-03 | 202 | 202 | 0 | 0 | 0 | +UP | 202 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 8 | SENSEX | 2026-12-31 | 326 | 326 | 0 | 0 | 0 | +UP | 326 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 9 | SENSEX | 2027-03-25 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 10 | SENSEX | 2027-06-24 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 11 | SENSEX | 2027-09-30 | 26 | 26 | 0 | 0 | 0 | +UP | 26 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 12 | SENSEX | 2027-12-30 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 13 | SENSEX | 2028-06-29 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 14 | SENSEX | 2028-12-28 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 15 | SENSEX | 2029-06-28 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 16 | SENSEX | 2029-12-27 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 17 | SENSEX | 2030-06-27 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 18 | SENSEX | 2030-12-26 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 19 | SENSEX | 2031-06-26 | 36 | 36 | 0 | 0 | 0 | +UP | 36 | 0 | T2 | SENSEX·Upstox-only (no FYERS BSE_FO rows) |
| 20 | 360ONE | 2026-10-27 | 86 | 39 | 47 | 47 | 0 | FY | 86 | 0 | T1 |  |
| 21 | 360ONE | 2026-11-23 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 22 | 360ONE | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 23 | ABB | 2026-10-27 | 112 | 53 | 59 | 59 | 0 | FY | 112 | 0 | T1 |  |
| 24 | ABB | 2026-11-23 | 112 | 44 | 68 | 68 | 0 | FY | 112 | 0 | T2 |  |
| 25 | ABB | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 26 | ABCAPITAL | 2026-10-27 | 128 | 55 | 73 | 73 | 0 | FY | 128 | 0 | T1 |  |
| 27 | ABCAPITAL | 2026-11-23 | 128 | 49 | 79 | 79 | 0 | FY | 128 | 0 | T2 |  |
| 28 | ABCAPITAL | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 29 | ADANIENSOL | 2026-10-27 | 116 | 68 | 48 | 48 | 0 | FY | 116 | 0 | T1 |  |
| 30 | ADANIENSOL | 2026-11-23 | 116 | 56 | 60 | 60 | 0 | FY | 116 | 0 | T2 |  |
| 31 | ADANIENSOL | 2026-12-29 | 62 | 30 | 32 | 32 | 0 | FY | 62 | 0 | T2 |  |
| 32 | ADANIENT | 2026-10-27 | 98 | 56 | 42 | 42 | 0 | FY | 98 | 0 | T1 |  |
| 33 | ADANIENT | 2026-11-23 | 98 | 45 | 53 | 53 | 0 | FY | 98 | 0 | T2 |  |
| 34 | ADANIENT | 2026-12-29 | 52 | 24 | 28 | 28 | 0 | FY | 52 | 0 | T2 |  |
| 35 | ADANIGREEN | 2026-10-27 | 110 | 51 | 59 | 59 | 0 | FY | 110 | 0 | T1 |  |
| 36 | ADANIGREEN | 2026-11-23 | 110 | 48 | 62 | 62 | 0 | FY | 110 | 0 | T2 |  |
| 37 | ADANIGREEN | 2026-12-29 | 52 | 26 | 26 | 26 | 0 | FY | 52 | 0 | T2 |  |
| 38 | ADANIPORTS | 2026-10-27 | 138 | 57 | 81 | 81 | 0 | FY | 138 | 0 | T1 |  |
| 39 | ADANIPORTS | 2026-11-23 | 138 | 54 | 84 | 84 | 0 | FY | 138 | 0 | T2 |  |
| 40 | ADANIPORTS | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 41 | ADANIPOWER | 2026-10-27 | 138 | 66 | 72 | 72 | 0 | FY | 138 | 0 | T1 |  |
| 42 | ADANIPOWER | 2026-11-23 | 138 | 64 | 74 | 74 | 0 | FY | 138 | 0 | T2 |  |
| 43 | ADANIPOWER | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 44 | ALKEM | 2026-10-27 | 172 | 66 | 106 | 106 | 0 | FY | 172 | 0 | T1 |  |
| 45 | ALKEM | 2026-11-23 | 172 | 66 | 106 | 106 | 0 | FY | 172 | 0 | T2 |  |
| 46 | ALKEM | 2026-12-29 | 88 | 34 | 54 | 54 | 0 | FY | 88 | 0 | T2 |  |
| 47 | AMBER | 2026-10-27 | 116 | 55 | 61 | 61 | 0 | FY | 116 | 0 | T1 |  |
| 48 | AMBER | 2026-11-23 | 116 | 48 | 68 | 68 | 0 | FY | 116 | 0 | T2 |  |
| 49 | AMBER | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 50 | AMBUJACEM | 2026-10-27 | 136 | 73 | 63 | 63 | 0 | FY | 136 | 0 | T1 |  |
| 51 | AMBUJACEM | 2026-11-23 | 136 | 57 | 79 | 79 | 0 | FY | 136 | 0 | T2 |  |
| 52 | AMBUJACEM | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 53 | ANANDRATHI | 2026-10-27 | 178 | 68 | 110 | 110 | 0 | FY | 178 | 0 | T1 |  |
| 54 | ANANDRATHI | 2026-11-23 | 178 | 68 | 110 | 110 | 0 | FY | 178 | 0 | T2 |  |
| 55 | ANANDRATHI | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 56 | ANGELONE | 2026-10-27 | 116 | 52 | 64 | 64 | 0 | FY | 116 | 0 | T1 |  |
| 57 | ANGELONE | 2026-11-23 | 116 | 52 | 64 | 64 | 0 | FY | 116 | 0 | T2 |  |
| 58 | ANGELONE | 2026-12-29 | 52 | 24 | 28 | 28 | 0 | FY | 52 | 0 | T2 |  |
| 59 | APLAPOLLO | 2026-10-27 | 182 | 81 | 101 | 101 | 0 | FY | 182 | 0 | T1 |  |
| 60 | APLAPOLLO | 2026-11-23 | 182 | 76 | 106 | 106 | 0 | FY | 182 | 0 | T2 |  |
| 61 | APLAPOLLO | 2026-12-29 | 86 | 36 | 50 | 50 | 0 | FY | 86 | 0 | T2 |  |
| 62 | APOLLOHOSP | 2026-10-27 | 152 | 68 | 84 | 84 | 0 | FY | 152 | 0 | T1 |  |
| 63 | APOLLOHOSP | 2026-11-23 | 152 | 60 | 92 | 92 | 0 | FY | 152 | 0 | T2 |  |
| 64 | APOLLOHOSP | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 65 | ASHOKLEY | 2026-10-27 | 106 | 61 | 45 | 45 | 0 | FY | 106 | 0 | T1 |  |
| 66 | ASHOKLEY | 2026-11-23 | 106 | 44 | 62 | 62 | 0 | FY | 106 | 0 | T2 |  |
| 67 | ASHOKLEY | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 68 | ASIANPAINT | 2026-10-27 | 194 | 90 | 104 | 104 | 0 | FY | 194 | 0 | T1 |  |
| 69 | ASIANPAINT | 2026-11-23 | 194 | 74 | 120 | 120 | 0 | FY | 194 | 0 | T2 |  |
| 70 | ASIANPAINT | 2026-12-29 | 104 | 40 | 64 | 64 | 0 | FY | 104 | 0 | T2 |  |
| 71 | ASTRAL | 2026-10-27 | 114 | 51 | 63 | 63 | 0 | FY | 114 | 0 | T1 |  |
| 72 | ASTRAL | 2026-11-23 | 114 | 46 | 68 | 68 | 0 | FY | 114 | 0 | T2 |  |
| 73 | ASTRAL | 2026-12-29 | 62 | 26 | 36 | 36 | 0 | FY | 62 | 0 | T2 |  |
| 74 | ATHERENERG | 2026-10-27 | 134 | 67 | 67 | 67 | 0 | FY | 134 | 0 | T1 |  |
| 75 | ATHERENERG | 2026-11-23 | 134 | 57 | 77 | 77 | 0 | FY | 134 | 0 | T2 |  |
| 76 | ATHERENERG | 2026-12-29 | 68 | 30 | 38 | 38 | 0 | FY | 68 | 0 | T2 |  |
| 77 | AUBANK | 2026-10-27 | 168 | 70 | 98 | 98 | 0 | FY | 168 | 0 | T1 |  |
| 78 | AUBANK | 2026-11-23 | 168 | 64 | 104 | 104 | 0 | FY | 168 | 0 | T2 |  |
| 79 | AUBANK | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 80 | AUROPHARMA | 2026-10-27 | 130 | 51 | 79 | 79 | 0 | FY | 130 | 0 | T1 |  |
| 81 | AUROPHARMA | 2026-11-23 | 130 | 50 | 80 | 80 | 0 | FY | 130 | 0 | T2 |  |
| 82 | AUROPHARMA | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 83 | AXISBANK | 2026-10-27 | 198 | 81 | 117 | 117 | 0 | FY | 198 | 0 | T1 |  |
| 84 | AXISBANK | 2026-11-23 | 198 | 77 | 121 | 121 | 0 | FY | 198 | 0 | T2 |  |
| 85 | AXISBANK | 2026-12-29 | 108 | 42 | 66 | 66 | 0 | FY | 108 | 0 | T2 |  |
| 86 | BAJAJ-AUTO | 2026-10-27 | 202 | 101 | 101 | 101 | 0 | FY | 202 | 0 | T1 |  |
| 87 | BAJAJ-AUTO | 2026-11-23 | 202 | 86 | 116 | 116 | 0 | FY | 202 | 0 | T2 |  |
| 88 | BAJAJ-AUTO | 2026-12-29 | 98 | 42 | 56 | 56 | 0 | FY | 98 | 0 | T2 |  |
| 89 | BAJAJFINSV | 2026-10-27 | 146 | 73 | 73 | 73 | 0 | FY | 146 | 0 | T1 |  |
| 90 | BAJAJFINSV | 2026-11-23 | 146 | 55 | 91 | 91 | 0 | FY | 146 | 0 | T2 |  |
| 91 | BAJAJFINSV | 2026-12-29 | 72 | 28 | 44 | 44 | 0 | FY | 72 | 0 | T2 |  |
| 92 | BAJAJHLDNG | 2026-10-27 | 178 | 72 | 106 | 106 | 0 | FY | 178 | 0 | T1 |  |
| 93 | BAJFINANCE | 2026-10-27 | 166 | 78 | 88 | 88 | 0 | FY | 166 | 0 | T1 |  |
| 94 | BAJFINANCE | 2026-11-23 | 166 | 63 | 103 | 103 | 0 | FY | 166 | 0 | T2 |  |
| 95 | BAJFINANCE | 2026-12-29 | 84 | 32 | 52 | 52 | 0 | FY | 84 | 0 | T2 |  |
| 96 | BANDHANBNK | 2026-10-27 | 124 | 64 | 60 | 60 | 0 | FY | 124 | 0 | T1 |  |
| 97 | BANDHANBNK | 2026-11-23 | 124 | 60 | 64 | 64 | 0 | FY | 124 | 0 | T2 |  |
| 98 | BANDHANBNK | 2026-12-29 | 66 | 32 | 34 | 34 | 0 | FY | 66 | 0 | T2 |  |
| 99 | BANKBARODA | 2026-10-27 | 148 | 60 | 88 | 88 | 0 | FY | 148 | 0 | T1 |  |
| 100 | BANKBARODA | 2026-11-23 | 148 | 57 | 91 | 91 | 0 | FY | 148 | 0 | T2 |  |
| 101 | BANKBARODA | 2026-12-29 | 74 | 30 | 44 | 44 | 0 | FY | 74 | 0 | T2 |  |
| 102 | BANKINDIA | 2026-10-27 | 90 | 41 | 49 | 49 | 0 | FY | 90 | 0 | T1 |  |
| 103 | BANKINDIA | 2026-11-23 | 90 | 34 | 56 | 56 | 0 | FY | 90 | 0 | T2 |  |
| 104 | BANKINDIA | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 105 | BANKNIFTY | 2026-10-27 | 740 | 314 | 426 | 0 | 426 | FY+UP | 314 | 426 | T1 |  |
| 106 | BANKNIFTY | 2026-11-23 | 736 | 298 | 438 | 0 | 438 | FY+UP | 298 | 438 | T2 |  |
| 107 | BANKNIFTY | 2026-12-29 | 700 | 261 | 439 | 0 | 439 | FY+UP | 261 | 439 | T2 |  |
| 108 | BANKNIFTY | 2027-03-30 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 109 | BANKNIFTY | 2027-06-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 110 | BANKNIFTY | 2027-09-28 | 64 | 24 | 40 | 40 | 0 | FY | 64 | 0 | T2 |  |
| 111 | BDL | 2026-10-27 | 94 | 62 | 32 | 32 | 0 | FY | 94 | 0 | T1 |  |
| 112 | BDL | 2026-11-23 | 94 | 41 | 53 | 53 | 0 | FY | 94 | 0 | T2 |  |
| 113 | BDL | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 114 | BEL | 2026-10-27 | 132 | 62 | 70 | 70 | 0 | FY | 132 | 0 | T1 |  |
| 115 | BEL | 2026-11-23 | 132 | 54 | 78 | 78 | 0 | FY | 132 | 0 | T2 |  |
| 116 | BEL | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 117 | BHARATFORG | 2026-10-27 | 154 | 76 | 78 | 78 | 0 | FY | 154 | 0 | T1 |  |
| 118 | BHARATFORG | 2026-11-23 | 154 | 62 | 92 | 92 | 0 | FY | 154 | 0 | T2 |  |
| 119 | BHARATFORG | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 120 | BHARTIARTL | 2026-10-27 | 156 | 64 | 92 | 92 | 0 | FY | 156 | 0 | T1 |  |
| 121 | BHARTIARTL | 2026-11-23 | 156 | 59 | 97 | 97 | 0 | FY | 156 | 0 | T2 |  |
| 122 | BHARTIARTL | 2026-12-29 | 78 | 30 | 48 | 48 | 0 | FY | 78 | 0 | T2 |  |
| 123 | BHEL | 2026-10-27 | 89 | 55 | 34 | 34 | 0 | FY | 89 | 0 | T1 |  |
| 124 | BHEL | 2026-11-23 | 70 | 32 | 38 | 38 | 0 | FY | 70 | 0 | T2 |  |
| 125 | BHEL | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 126 | BIOCON | 2026-10-27 | 132 | 68 | 64 | 64 | 0 | FY | 132 | 0 | T1 |  |
| 127 | BIOCON | 2026-11-23 | 132 | 53 | 79 | 79 | 0 | FY | 132 | 0 | T2 |  |
| 128 | BIOCON | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 129 | BLUESTARCO | 2026-10-27 | 132 | 59 | 73 | 73 | 0 | FY | 132 | 0 | T1 |  |
| 130 | BLUESTARCO | 2026-11-23 | 132 | 56 | 76 | 76 | 0 | FY | 132 | 0 | T2 |  |
| 131 | BLUESTARCO | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 132 | BOSCHLTD | 2026-10-27 | 158 | 68 | 90 | 90 | 0 | FY | 158 | 0 | T1 |  |
| 133 | BOSCHLTD | 2026-11-23 | 158 | 66 | 92 | 92 | 0 | FY | 158 | 0 | T2 |  |
| 134 | BOSCHLTD | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 135 | BPCL | 2026-10-27 | 108 | 52 | 56 | 56 | 0 | FY | 108 | 0 | T1 |  |
| 136 | BPCL | 2026-11-23 | 108 | 44 | 64 | 64 | 0 | FY | 108 | 0 | T2 |  |
| 137 | BPCL | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 138 | BRITANNIA | 2026-10-27 | 156 | 70 | 86 | 86 | 0 | FY | 156 | 0 | T1 |  |
| 139 | BRITANNIA | 2026-11-23 | 156 | 60 | 96 | 96 | 0 | FY | 156 | 0 | T2 |  |
| 140 | BRITANNIA | 2026-12-29 | 84 | 32 | 52 | 52 | 0 | FY | 84 | 0 | T2 |  |
| 141 | BSE | 2026-10-27 | 120 | 55 | 65 | 65 | 0 | FY | 120 | 0 | T1 |  |
| 142 | BSE | 2026-11-23 | 120 | 55 | 65 | 65 | 0 | FY | 120 | 0 | T2 |  |
| 143 | BSE | 2026-12-29 | 54 | 26 | 28 | 28 | 0 | FY | 54 | 0 | T2 |  |
| 144 | CAMS | 2026-10-27 | 114 | 59 | 55 | 55 | 0 | FY | 114 | 0 | T1 |  |
| 145 | CAMS | 2026-11-23 | 114 | 46 | 68 | 68 | 0 | FY | 114 | 0 | T2 |  |
| 146 | CAMS | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 147 | CANBK | 2026-10-27 | 212 | 100 | 112 | 100 | 12 | FY | 200 | 12 | T1 |  |
| 148 | CANBK | 2026-11-23 | 198 | 82 | 116 | 116 | 0 | FY | 198 | 0 | T2 |  |
| 149 | CANBK | 2026-12-29 | 106 | 44 | 62 | 62 | 0 | FY | 106 | 0 | T2 |  |
| 150 | CDSL | 2026-10-27 | 110 | 55 | 55 | 55 | 0 | FY | 110 | 0 | T1 |  |
| 151 | CDSL | 2026-11-23 | 110 | 42 | 68 | 68 | 0 | FY | 110 | 0 | T2 |  |
| 152 | CDSL | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 153 | CGPOWER | 2026-10-27 | 148 | 69 | 79 | 79 | 0 | FY | 148 | 0 | T1 |  |
| 154 | CGPOWER | 2026-11-23 | 148 | 68 | 80 | 80 | 0 | FY | 148 | 0 | T2 |  |
| 155 | CGPOWER | 2026-12-29 | 74 | 36 | 38 | 38 | 0 | FY | 74 | 0 | T2 |  |
| 156 | CHOLAFIN | 2026-10-27 | 148 | 72 | 76 | 76 | 0 | FY | 148 | 0 | T1 |  |
| 157 | CHOLAFIN | 2026-11-23 | 148 | 56 | 92 | 92 | 0 | FY | 148 | 0 | T2 |  |
| 158 | CHOLAFIN | 2026-12-29 | 72 | 28 | 44 | 44 | 0 | FY | 72 | 0 | T2 |  |
| 159 | CIPLA | 2026-10-27 | 204 | 87 | 117 | 115 | 2 | FY | 202 | 2 | T1 |  |
| 160 | CIPLA | 2026-11-23 | 204 | 76 | 128 | 126 | 2 | FY | 202 | 2 | T2 |  |
| 161 | CIPLA | 2026-12-29 | 104 | 40 | 64 | 64 | 0 | FY | 104 | 0 | T2 |  |
| 162 | COALINDIA | 2026-10-27 | 134 | 58 | 76 | 76 | 0 | FY | 134 | 0 | T1 |  |
| 163 | COALINDIA | 2026-11-23 | 134 | 54 | 80 | 80 | 0 | FY | 134 | 0 | T2 |  |
| 164 | COALINDIA | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 165 | COCHINSHIP | 2026-10-27 | 118 | 70 | 48 | 48 | 0 | FY | 118 | 0 | T1 |  |
| 166 | COCHINSHIP | 2026-11-23 | 118 | 50 | 68 | 68 | 0 | FY | 118 | 0 | T2 |  |
| 167 | COCHINSHIP | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 168 | COFORGE | 2026-10-27 | 162 | 74 | 88 | 88 | 0 | FY | 162 | 0 | T1 |  |
| 169 | COFORGE | 2026-11-23 | 162 | 70 | 92 | 92 | 0 | FY | 162 | 0 | T2 |  |
| 170 | COFORGE | 2026-12-29 | 72 | 34 | 38 | 38 | 0 | FY | 72 | 0 | T2 |  |
| 171 | COLPAL | 2026-10-27 | 150 | 69 | 81 | 81 | 0 | FY | 150 | 0 | T1 |  |
| 172 | COLPAL | 2026-11-23 | 150 | 62 | 88 | 88 | 0 | FY | 150 | 0 | T2 |  |
| 173 | COLPAL | 2026-12-29 | 74 | 30 | 44 | 44 | 0 | FY | 74 | 0 | T2 |  |
| 174 | CONCOR | 2026-10-27 | 154 | 77 | 77 | 77 | 0 | FY | 154 | 0 | T1 |  |
| 175 | CONCOR | 2026-11-23 | 154 | 58 | 96 | 96 | 0 | FY | 154 | 0 | T2 |  |
| 176 | CONCOR | 2026-12-29 | 82 | 30 | 52 | 52 | 0 | FY | 82 | 0 | T2 |  |
| 177 | CROMPTON | 2026-10-27 | 150 | 70 | 80 | 80 | 0 | FY | 150 | 0 | T1 |  |
| 178 | CROMPTON | 2026-11-23 | 150 | 58 | 92 | 92 | 0 | FY | 150 | 0 | T2 |  |
| 179 | CROMPTON | 2026-12-29 | 74 | 30 | 44 | 44 | 0 | FY | 74 | 0 | T2 |  |
| 180 | CUMMINSIND | 2026-10-27 | 158 | 68 | 90 | 90 | 0 | FY | 158 | 0 | T1 |  |
| 181 | CUMMINSIND | 2026-11-23 | 158 | 62 | 96 | 96 | 0 | FY | 158 | 0 | T2 |  |
| 182 | CUMMINSIND | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 183 | DABUR | 2026-10-27 | 130 | 54 | 76 | 76 | 0 | FY | 130 | 0 | T1 |  |
| 184 | DABUR | 2026-11-23 | 130 | 50 | 80 | 80 | 0 | FY | 130 | 0 | T2 |  |
| 185 | DABUR | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 186 | DELHIVERY | 2026-10-27 | 132 | 60 | 72 | 72 | 0 | FY | 132 | 0 | T1 |  |
| 187 | DELHIVERY | 2026-11-23 | 132 | 56 | 76 | 76 | 0 | FY | 132 | 0 | T2 |  |
| 188 | DELHIVERY | 2026-12-29 | 66 | 28 | 38 | 38 | 0 | FY | 66 | 0 | T2 |  |
| 189 | DIVISLAB | 2026-10-27 | 154 | 62 | 92 | 92 | 0 | FY | 154 | 0 | T1 |  |
| 190 | DIVISLAB | 2026-11-23 | 154 | 62 | 92 | 92 | 0 | FY | 154 | 0 | T2 |  |
| 191 | DIVISLAB | 2026-12-29 | 74 | 30 | 44 | 44 | 0 | FY | 74 | 0 | T2 |  |
| 192 | DIXON | 2026-10-27 | 86 | 43 | 43 | 43 | 0 | FY | 86 | 0 | T1 |  |
| 193 | DIXON | 2026-11-23 | 86 | 32 | 54 | 54 | 0 | FY | 86 | 0 | T2 |  |
| 194 | DIXON | 2026-12-29 | 48 | 18 | 30 | 30 | 0 | FY | 48 | 0 | T2 |  |
| 195 | DLF | 2026-10-27 | 106 | 47 | 59 | 59 | 0 | FY | 106 | 0 | T1 |  |
| 196 | DLF | 2026-11-23 | 106 | 42 | 64 | 64 | 0 | FY | 106 | 0 | T2 |  |
| 197 | DLF | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 198 | DMART | 2026-10-27 | 136 | 62 | 74 | 74 | 0 | FY | 136 | 0 | T1 |  |
| 199 | DMART | 2026-11-23 | 136 | 59 | 77 | 77 | 0 | FY | 136 | 0 | T2 |  |
| 200 | DMART | 2026-12-29 | 72 | 30 | 42 | 42 | 0 | FY | 72 | 0 | T2 |  |
| 201 | DRREDDY | 2026-10-27 | 194 | 79 | 115 | 115 | 0 | FY | 194 | 0 | T1 |  |
| 202 | DRREDDY | 2026-11-23 | 194 | 74 | 120 | 120 | 0 | FY | 194 | 0 | T2 |  |
| 203 | DRREDDY | 2026-12-29 | 92 | 36 | 56 | 56 | 0 | FY | 92 | 0 | T2 |  |
| 204 | EICHERMOT | 2026-10-27 | 132 | 60 | 72 | 72 | 0 | FY | 132 | 0 | T1 |  |
| 205 | EICHERMOT | 2026-11-23 | 132 | 50 | 82 | 82 | 0 | FY | 132 | 0 | T2 |  |
| 206 | EICHERMOT | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 207 | ENRIN | 2026-10-27 | 116 | 52 | 64 | 64 | 0 | FY | 116 | 0 | T1 |  |
| 208 | ENRIN | 2026-11-23 | 116 | 52 | 64 | 64 | 0 | FY | 116 | 0 | T2 |  |
| 209 | ENRIN | 2026-12-29 | 54 | 24 | 30 | 30 | 0 | FY | 54 | 0 | T2 |  |
| 210 | ETERNAL | 2026-10-27 | 106 | 43 | 63 | 63 | 0 | FY | 106 | 0 | T1 |  |
| 211 | ETERNAL | 2026-11-23 | 106 | 42 | 64 | 64 | 0 | FY | 106 | 0 | T2 |  |
| 212 | ETERNAL | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 213 | FEDERALBNK | 2026-10-27 | 114 | 51 | 63 | 63 | 0 | FY | 114 | 0 | T1 |  |
| 214 | FEDERALBNK | 2026-11-23 | 114 | 44 | 70 | 70 | 0 | FY | 114 | 0 | T2 |  |
| 215 | FEDERALBNK | 2026-12-29 | 62 | 26 | 36 | 36 | 0 | FY | 62 | 0 | T2 |  |
| 216 | FINNIFTY | 2026-10-27 | 508 | 238 | 270 | 14 | 256 | FY+UP | 252 | 256 | T1 |  |
| 217 | FINNIFTY | 2026-11-23 | 306 | 134 | 172 | 68 | 104 | FY | 202 | 104 | T2 |  |
| 218 | FINNIFTY | 2026-12-29 | 286 | 116 | 170 | 86 | 84 | FY | 202 | 84 | T2 |  |
| 219 | FORCEMOT | 2026-10-27 | 112 | 45 | 67 | 67 | 0 | FY | 112 | 0 | T1 |  |
| 220 | FORCEMOT | 2026-11-23 | 112 | 44 | 68 | 68 | 0 | FY | 112 | 0 | T2 |  |
| 221 | FORCEMOT | 2026-12-29 | 60 | 24 | 36 | 36 | 0 | FY | 60 | 0 | T2 |  |
| 222 | FORTIS | 2026-10-27 | 150 | 75 | 75 | 75 | 0 | FY | 150 | 0 | T1 |  |
| 223 | FORTIS | 2026-11-23 | 150 | 60 | 90 | 90 | 0 | FY | 150 | 0 | T2 |  |
| 224 | FORTIS | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 225 | GAIL | 2026-10-27 | 108 | 44 | 64 | 64 | 0 | FY | 108 | 0 | T1 |  |
| 226 | GAIL | 2026-11-23 | 108 | 41 | 67 | 67 | 0 | FY | 108 | 0 | T2 |  |
| 227 | GAIL | 2026-12-29 | 58 | 22 | 36 | 36 | 0 | FY | 58 | 0 | T2 |  |
| 228 | GLENMARK | 2026-10-27 | 196 | 85 | 111 | 111 | 0 | FY | 196 | 0 | T1 |  |
| 229 | GLENMARK | 2026-11-23 | 196 | 76 | 120 | 120 | 0 | FY | 196 | 0 | T2 |  |
| 230 | GLENMARK | 2026-12-29 | 94 | 38 | 56 | 56 | 0 | FY | 94 | 0 | T2 |  |
| 231 | GMRAIRPORT | 2026-10-27 | 156 | 73 | 83 | 83 | 0 | FY | 156 | 0 | T1 |  |
| 232 | GMRAIRPORT | 2026-11-23 | 156 | 67 | 89 | 89 | 0 | FY | 156 | 0 | T2 |  |
| 233 | GMRAIRPORT | 2026-12-29 | 78 | 34 | 44 | 44 | 0 | FY | 78 | 0 | T2 |  |
| 234 | GODFRYPHLP | 2026-10-27 | 169 | 76 | 93 | 93 | 0 | FY | 169 | 0 | T1 |  |
| 235 | GODFRYPHLP | 2026-11-23 | 162 | 66 | 96 | 96 | 0 | FY | 162 | 0 | T2 |  |
| 236 | GODFRYPHLP | 2026-12-29 | 90 | 38 | 52 | 52 | 0 | FY | 90 | 0 | T2 |  |
| 237 | GODREJCP | 2026-10-27 | 136 | 63 | 73 | 73 | 0 | FY | 136 | 0 | T1 |  |
| 238 | GODREJCP | 2026-11-23 | 136 | 52 | 84 | 84 | 0 | FY | 136 | 0 | T2 |  |
| 239 | GODREJCP | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 240 | GODREJPROP | 2026-10-27 | 135 | 85 | 50 | 50 | 0 | FY | 135 | 0 | T1 |  |
| 241 | GODREJPROP | 2026-11-23 | 134 | 54 | 80 | 80 | 0 | FY | 134 | 0 | T2 |  |
| 242 | GODREJPROP | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 243 | GRASIM | 2026-10-27 | 242 | 111 | 131 | 91 | 40 | FY | 202 | 40 | T1 |  |
| 244 | GRASIM | 2026-11-23 | 242 | 98 | 144 | 104 | 40 | FY | 202 | 40 | T2 |  |
| 245 | GRASIM | 2026-12-29 | 122 | 50 | 72 | 72 | 0 | FY | 122 | 0 | T2 |  |
| 246 | GVT&D | 2026-10-27 | 72 | 35 | 37 | 37 | 0 | FY | 72 | 0 | T1 |  |
| 247 | GVT&D | 2026-11-23 | 72 | 32 | 40 | 40 | 0 | FY | 72 | 0 | T2 |  |
| 248 | GVT&D | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 249 | HAL | 2026-10-27 | 158 | 64 | 94 | 94 | 0 | FY | 158 | 0 | T1 |  |
| 250 | HAL | 2026-11-23 | 158 | 62 | 96 | 96 | 0 | FY | 158 | 0 | T2 |  |
| 251 | HAL | 2026-12-29 | 90 | 36 | 54 | 54 | 0 | FY | 90 | 0 | T2 |  |
| 252 | HAVELLS | 2026-10-27 | 186 | 88 | 98 | 98 | 0 | FY | 186 | 0 | T1 |  |
| 253 | HAVELLS | 2026-11-23 | 186 | 78 | 108 | 108 | 0 | FY | 186 | 0 | T2 |  |
| 254 | HAVELLS | 2026-12-29 | 92 | 40 | 52 | 52 | 0 | FY | 92 | 0 | T2 |  |
| 255 | HCLTECH | 2026-10-27 | 153 | 87 | 66 | 66 | 0 | FY | 153 | 0 | T1 |  |
| 256 | HCLTECH | 2026-11-23 | 122 | 54 | 68 | 68 | 0 | FY | 122 | 0 | T2 |  |
| 257 | HCLTECH | 2026-12-29 | 54 | 26 | 28 | 28 | 0 | FY | 54 | 0 | T2 |  |
| 258 | HDFCAMC | 2026-10-27 | 215 | 107 | 108 | 94 | 14 | FY | 201 | 14 | T1 |  |
| 259 | HDFCAMC | 2026-11-23 | 200 | 84 | 116 | 116 | 0 | FY | 200 | 0 | T2 |  |
| 260 | HDFCAMC | 2026-12-29 | 106 | 44 | 62 | 62 | 0 | FY | 106 | 0 | T2 |  |
| 261 | HDFCBANK | 2026-10-27 | 110 | 52 | 58 | 58 | 0 | FY | 110 | 0 | T1 |  |
| 262 | HDFCBANK | 2026-11-23 | 110 | 43 | 67 | 67 | 0 | FY | 110 | 0 | T2 |  |
| 263 | HDFCBANK | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 264 | HDFCLIFE | 2026-10-27 | 174 | 77 | 97 | 97 | 0 | FY | 174 | 0 | T1 |  |
| 265 | HDFCLIFE | 2026-11-23 | 174 | 70 | 104 | 104 | 0 | FY | 174 | 0 | T2 |  |
| 266 | HDFCLIFE | 2026-12-29 | 90 | 38 | 52 | 52 | 0 | FY | 90 | 0 | T2 |  |
| 267 | HEROMOTOCO | 2026-10-27 | 174 | 74 | 100 | 100 | 0 | FY | 174 | 0 | T1 |  |
| 268 | HEROMOTOCO | 2026-11-23 | 174 | 70 | 104 | 104 | 0 | FY | 174 | 0 | T2 |  |
| 269 | HEROMOTOCO | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 270 | HINDALCO | 2026-10-27 | 158 | 73 | 85 | 85 | 0 | FY | 158 | 0 | T1 |  |
| 271 | HINDALCO | 2026-11-23 | 158 | 62 | 96 | 96 | 0 | FY | 158 | 0 | T2 |  |
| 272 | HINDALCO | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 273 | HINDPETRO | 2026-10-27 | 116 | 56 | 60 | 60 | 0 | FY | 116 | 0 | T1 |  |
| 274 | HINDPETRO | 2026-11-23 | 114 | 48 | 66 | 66 | 0 | FY | 114 | 0 | T2 |  |
| 275 | HINDPETRO | 2026-12-29 | 62 | 28 | 34 | 34 | 0 | FY | 62 | 0 | T2 |  |
| 276 | HINDUNILVR | 2026-10-27 | 154 | 75 | 79 | 79 | 0 | FY | 154 | 0 | T1 |  |
| 277 | HINDUNILVR | 2026-11-23 | 154 | 60 | 94 | 94 | 0 | FY | 154 | 0 | T2 |  |
| 278 | HINDUNILVR | 2026-12-29 | 84 | 32 | 52 | 52 | 0 | FY | 84 | 0 | T2 |  |
| 279 | HINDZINC | 2026-10-27 | 94 | 50 | 44 | 44 | 0 | FY | 94 | 0 | T1 |  |
| 280 | HINDZINC | 2026-11-23 | 94 | 40 | 54 | 54 | 0 | FY | 94 | 0 | T2 |  |
| 281 | HINDZINC | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 282 | HYUNDAI | 2026-10-27 | 178 | 77 | 101 | 101 | 0 | FY | 178 | 0 | T1 |  |
| 283 | HYUNDAI | 2026-11-23 | 178 | 70 | 108 | 108 | 0 | FY | 178 | 0 | T2 |  |
| 284 | HYUNDAI | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 285 | ICICIBANK | 2026-10-27 | 212 | 93 | 119 | 109 | 10 | FY | 202 | 10 | T1 |  |
| 286 | ICICIBANK | 2026-11-23 | 212 | 82 | 130 | 120 | 10 | FY | 202 | 10 | T2 |  |
| 287 | ICICIBANK | 2026-12-29 | 106 | 42 | 64 | 64 | 0 | FY | 106 | 0 | T2 |  |
| 288 | ICICIGI | 2026-10-27 | 122 | 64 | 58 | 58 | 0 | FY | 122 | 0 | T1 |  |
| 289 | ICICIGI | 2026-11-23 | 122 | 55 | 67 | 67 | 0 | FY | 122 | 0 | T2 |  |
| 290 | ICICIGI | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 291 | ICICIPRULI | 2026-10-27 | 148 | 72 | 76 | 76 | 0 | FY | 148 | 0 | T1 |  |
| 292 | ICICIPRULI | 2026-11-23 | 148 | 56 | 92 | 92 | 0 | FY | 148 | 0 | T2 |  |
| 293 | ICICIPRULI | 2026-12-29 | 74 | 30 | 44 | 44 | 0 | FY | 74 | 0 | T2 |  |
| 294 | IDEA | 2026-10-27 | 38 | 21 | 17 | 17 | 0 | FY | 38 | 0 | T1 |  |
| 295 | IDEA | 2026-11-23 | 38 | 19 | 19 | 19 | 0 | FY | 38 | 0 | T2 |  |
| 296 | IDEA | 2026-12-29 | 32 | 16 | 16 | 16 | 0 | FY | 32 | 0 | T2 |  |
| 297 | IDFCFIRSTB | 2026-10-27 | 138 | 63 | 75 | 75 | 0 | FY | 138 | 0 | T1 |  |
| 298 | IDFCFIRSTB | 2026-11-23 | 138 | 56 | 82 | 82 | 0 | FY | 138 | 0 | T2 |  |
| 299 | IDFCFIRSTB | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 300 | IEX | 2026-10-27 | 185 | 86 | 99 | 99 | 0 | FY | 185 | 0 | T1 |  |
| 301 | IEX | 2026-11-23 | 174 | 70 | 104 | 104 | 0 | FY | 174 | 0 | T2 |  |
| 302 | INDHOTEL | 2026-10-27 | 116 | 43 | 73 | 73 | 0 | FY | 116 | 0 | T1 |  |
| 303 | INDHOTEL | 2026-11-23 | 116 | 43 | 73 | 73 | 0 | FY | 116 | 0 | T2 |  |
| 304 | INDHOTEL | 2026-12-29 | 68 | 26 | 42 | 42 | 0 | FY | 68 | 0 | T2 |  |
| 305 | INDIANB | 2026-10-27 | 136 | 61 | 75 | 75 | 0 | FY | 136 | 0 | T1 |  |
| 306 | INDIANB | 2026-11-23 | 136 | 54 | 82 | 82 | 0 | FY | 136 | 0 | T2 |  |
| 307 | INDIANB | 2026-12-29 | 68 | 26 | 42 | 42 | 0 | FY | 68 | 0 | T2 |  |
| 308 | INDIGO | 2026-10-27 | 162 | 67 | 95 | 95 | 0 | FY | 162 | 0 | T1 |  |
| 309 | INDIGO | 2026-11-23 | 162 | 66 | 96 | 96 | 0 | FY | 162 | 0 | T2 |  |
| 310 | INDIGO | 2026-12-29 | 88 | 34 | 54 | 54 | 0 | FY | 88 | 0 | T2 |  |
| 311 | INDUSINDBK | 2026-10-27 | 158 | 75 | 83 | 83 | 0 | FY | 158 | 0 | T1 |  |
| 312 | INDUSINDBK | 2026-11-23 | 158 | 63 | 95 | 95 | 0 | FY | 158 | 0 | T2 |  |
| 313 | INDUSINDBK | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 314 | INDUSTOWER | 2026-10-27 | 130 | 52 | 78 | 78 | 0 | FY | 130 | 0 | T1 |  |
| 315 | INDUSTOWER | 2026-11-23 | 130 | 50 | 80 | 80 | 0 | FY | 130 | 0 | T2 |  |
| 316 | INDUSTOWER | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 317 | INFY | 2026-10-27 | 98 | 47 | 51 | 51 | 0 | FY | 98 | 0 | T1 |  |
| 318 | INFY | 2026-11-23 | 98 | 42 | 56 | 56 | 0 | FY | 98 | 0 | T2 |  |
| 319 | INFY | 2026-12-29 | 54 | 24 | 30 | 30 | 0 | FY | 54 | 0 | T2 |  |
| 320 | INOXWIND | 2026-10-27 | 146 | 74 | 72 | 72 | 0 | FY | 146 | 0 | T1 |  |
| 321 | INOXWIND | 2026-11-23 | 134 | 58 | 76 | 76 | 0 | FY | 134 | 0 | T2 |  |
| 322 | INOXWIND | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 323 | IOC | 2026-10-27 | 228 | 104 | 124 | 96 | 28 | FY | 200 | 28 | T1 |  |
| 324 | IOC | 2026-11-23 | 214 | 86 | 128 | 116 | 12 | FY | 202 | 12 | T2 |  |
| 325 | IOC | 2026-12-29 | 110 | 46 | 64 | 64 | 0 | FY | 110 | 0 | T2 |  |
| 326 | IREDA | 2026-10-27 | 198 | 98 | 100 | 100 | 0 | FY | 198 | 0 | T1 |  |
| 327 | IREDA | 2026-11-23 | 182 | 78 | 104 | 104 | 0 | FY | 182 | 0 | T2 |  |
| 328 | IRFC | 2026-10-27 | 134 | 66 | 68 | 68 | 0 | FY | 134 | 0 | T1 |  |
| 329 | IRFC | 2026-11-23 | 134 | 56 | 78 | 78 | 0 | FY | 134 | 0 | T2 |  |
| 330 | IRFC | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 331 | ITC | 2026-10-27 | 170 | 78 | 92 | 92 | 0 | FY | 170 | 0 | T1 |  |
| 332 | ITC | 2026-11-23 | 170 | 70 | 100 | 100 | 0 | FY | 170 | 0 | T2 |  |
| 333 | ITC | 2026-12-29 | 86 | 36 | 50 | 50 | 0 | FY | 86 | 0 | T2 |  |
| 334 | JINDALSTEL | 2026-10-27 | 100 | 50 | 50 | 50 | 0 | FY | 100 | 0 | T1 |  |
| 335 | JINDALSTEL | 2026-11-23 | 100 | 46 | 54 | 54 | 0 | FY | 100 | 0 | T2 |  |
| 336 | JINDALSTEL | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 337 | JIOFIN | 2026-10-27 | 150 | 81 | 69 | 69 | 0 | FY | 150 | 0 | T1 |  |
| 338 | JIOFIN | 2026-11-23 | 150 | 63 | 87 | 87 | 0 | FY | 150 | 0 | T2 |  |
| 339 | JIOFIN | 2026-12-29 | 74 | 30 | 44 | 44 | 0 | FY | 74 | 0 | T2 |  |
| 340 | JSWENERGY | 2026-10-27 | 88 | 44 | 44 | 44 | 0 | FY | 88 | 0 | T1 |  |
| 341 | JSWENERGY | 2026-11-23 | 88 | 38 | 50 | 50 | 0 | FY | 88 | 0 | T2 |  |
| 342 | JSWENERGY | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 343 | JSWSTEEL | 2026-10-27 | 202 | 89 | 113 | 113 | 0 | FY | 202 | 0 | T1 |  |
| 344 | JSWSTEEL | 2026-11-23 | 202 | 82 | 120 | 120 | 0 | FY | 202 | 0 | T2 |  |
| 345 | JSWSTEEL | 2026-12-29 | 108 | 44 | 64 | 64 | 0 | FY | 108 | 0 | T2 |  |
| 346 | JUBLFOOD | 2026-10-27 | 114 | 68 | 46 | 46 | 0 | FY | 114 | 0 | T1 |  |
| 347 | JUBLFOOD | 2026-11-23 | 90 | 38 | 52 | 52 | 0 | FY | 90 | 0 | T2 |  |
| 348 | JUBLFOOD | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 349 | KALYANKJIL | 2026-10-27 | 110 | 56 | 54 | 54 | 0 | FY | 110 | 0 | T1 |  |
| 350 | KALYANKJIL | 2026-11-23 | 110 | 51 | 59 | 59 | 0 | FY | 110 | 0 | T2 |  |
| 351 | KALYANKJIL | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 352 | KAYNES | 2026-10-27 | 118 | 56 | 62 | 62 | 0 | FY | 118 | 0 | T1 |  |
| 353 | KAYNES | 2026-11-23 | 118 | 50 | 68 | 68 | 0 | FY | 118 | 0 | T2 |  |
| 354 | KAYNES | 2026-12-29 | 64 | 28 | 36 | 36 | 0 | FY | 64 | 0 | T2 |  |
| 355 | KEI | 2026-10-27 | 72 | 43 | 29 | 29 | 0 | FY | 72 | 0 | T1 |  |
| 356 | KEI | 2026-11-23 | 72 | 28 | 44 | 44 | 0 | FY | 72 | 0 | T2 |  |
| 357 | KEI | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 358 | KFINTECH | 2026-10-27 | 68 | 36 | 32 | 32 | 0 | FY | 68 | 0 | T1 |  |
| 359 | KFINTECH | 2026-11-23 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 360 | KFINTECH | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 361 | KOTAKBANK | 2026-10-27 | 136 | 61 | 75 | 75 | 0 | FY | 136 | 0 | T1 |  |
| 362 | KOTAKBANK | 2026-11-23 | 136 | 58 | 78 | 78 | 0 | FY | 136 | 0 | T2 |  |
| 363 | KOTAKBANK | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 364 | KPITTECH | 2026-10-27 | 104 | 61 | 43 | 43 | 0 | FY | 104 | 0 | T1 |  |
| 365 | KPITTECH | 2026-11-23 | 104 | 44 | 60 | 60 | 0 | FY | 104 | 0 | T2 |  |
| 366 | KPITTECH | 2026-12-29 | 54 | 22 | 32 | 32 | 0 | FY | 54 | 0 | T2 |  |
| 367 | LAURUSLABS | 2026-10-27 | 158 | 68 | 90 | 90 | 0 | FY | 158 | 0 | T1 |  |
| 368 | LAURUSLABS | 2026-11-23 | 158 | 63 | 95 | 95 | 0 | FY | 158 | 0 | T2 |  |
| 369 | LAURUSLABS | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 370 | LICHSGFIN | 2026-10-27 | 194 | 90 | 104 | 104 | 0 | FY | 194 | 0 | T1 |  |
| 371 | LICHSGFIN | 2026-11-23 | 194 | 90 | 104 | 104 | 0 | FY | 194 | 0 | T2 |  |
| 372 | LICHSGFIN | 2026-12-29 | 94 | 44 | 50 | 50 | 0 | FY | 94 | 0 | T2 |  |
| 373 | LICI | 2026-10-27 | 130 | 55 | 75 | 75 | 0 | FY | 130 | 0 | T1 |  |
| 374 | LICI | 2026-11-23 | 130 | 51 | 79 | 79 | 0 | FY | 130 | 0 | T2 |  |
| 375 | LICI | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 376 | LODHA | 2026-10-27 | 88 | 46 | 42 | 42 | 0 | FY | 88 | 0 | T1 |  |
| 377 | LODHA | 2026-11-23 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 378 | LODHA | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 379 | LT | 2026-10-27 | 130 | 58 | 72 | 72 | 0 | FY | 130 | 0 | T1 |  |
| 380 | LT | 2026-11-23 | 130 | 50 | 80 | 80 | 0 | FY | 130 | 0 | T2 |  |
| 381 | LT | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 382 | LTF | 2026-10-27 | 94 | 55 | 39 | 39 | 0 | FY | 94 | 0 | T1 |  |
| 383 | LTF | 2026-11-23 | 94 | 39 | 55 | 55 | 0 | FY | 94 | 0 | T2 |  |
| 384 | LTF | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 385 | LTM | 2026-10-27 | 97 | 63 | 34 | 34 | 0 | FY | 97 | 0 | T1 |  |
| 386 | LTM | 2026-11-23 | 72 | 28 | 44 | 44 | 0 | FY | 72 | 0 | T2 |  |
| 387 | LTM | 2026-12-29 | 50 | 20 | 30 | 30 | 0 | FY | 50 | 0 | T2 |  |
| 388 | LUPIN | 2026-10-27 | 170 | 82 | 88 | 88 | 0 | FY | 170 | 0 | T1 |  |
| 389 | LUPIN | 2026-11-23 | 170 | 67 | 103 | 103 | 0 | FY | 170 | 0 | T2 |  |
| 390 | LUPIN | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 391 | M&M | 2026-10-27 | 108 | 60 | 48 | 48 | 0 | FY | 108 | 0 | T1 |  |
| 392 | M&M | 2026-11-23 | 108 | 44 | 64 | 64 | 0 | FY | 108 | 0 | T2 |  |
| 393 | M&M | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 394 | MAHABANK | 2026-10-27 | 132 | 62 | 70 | 70 | 0 | FY | 132 | 0 | T1 |  |
| 395 | MAHABANK | 2026-11-23 | 132 | 56 | 76 | 76 | 0 | FY | 132 | 0 | T2 |  |
| 396 | MAHABANK | 2026-12-29 | 66 | 28 | 38 | 38 | 0 | FY | 66 | 0 | T2 |  |
| 397 | MANAPPURAM | 2026-10-27 | 110 | 48 | 62 | 62 | 0 | FY | 110 | 0 | T1 |  |
| 398 | MANAPPURAM | 2026-11-23 | 110 | 46 | 64 | 64 | 0 | FY | 110 | 0 | T2 |  |
| 399 | MANAPPURAM | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 400 | MANKIND | 2026-10-27 | 202 | 89 | 113 | 113 | 0 | FY | 202 | 0 | T1 |  |
| 401 | MANKIND | 2026-11-23 | 202 | 86 | 116 | 116 | 0 | FY | 202 | 0 | T2 |  |
| 402 | MANKIND | 2026-12-29 | 102 | 44 | 58 | 58 | 0 | FY | 102 | 0 | T2 |  |
| 403 | MARICO | 2026-10-27 | 126 | 50 | 76 | 76 | 0 | FY | 126 | 0 | T1 |  |
| 404 | MARICO | 2026-11-23 | 126 | 46 | 80 | 80 | 0 | FY | 126 | 0 | T2 |  |
| 405 | MARICO | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 406 | MARUTI | 2026-10-27 | 198 | 107 | 91 | 91 | 0 | FY | 198 | 0 | T1 |  |
| 407 | MARUTI | 2026-11-23 | 198 | 82 | 116 | 116 | 0 | FY | 198 | 0 | T2 |  |
| 408 | MARUTI | 2026-12-29 | 106 | 44 | 62 | 62 | 0 | FY | 106 | 0 | T2 |  |
| 409 | MAXHEALTH | 2026-10-27 | 178 | 93 | 85 | 85 | 0 | FY | 178 | 0 | T1 |  |
| 410 | MAXHEALTH | 2026-11-23 | 178 | 72 | 106 | 106 | 0 | FY | 178 | 0 | T2 |  |
| 411 | MAXHEALTH | 2026-12-29 | 92 | 38 | 54 | 54 | 0 | FY | 92 | 0 | T2 |  |
| 412 | MAZDOCK | 2026-10-27 | 194 | 97 | 97 | 97 | 0 | FY | 194 | 0 | T1 |  |
| 413 | MAZDOCK | 2026-11-23 | 178 | 70 | 108 | 108 | 0 | FY | 178 | 0 | T2 |  |
| 414 | MAZDOCK | 2026-12-29 | 94 | 38 | 56 | 56 | 0 | FY | 94 | 0 | T2 |  |
| 415 | MCX | 2026-10-27 | 108 | 46 | 62 | 62 | 0 | FY | 108 | 0 | T1 |  |
| 416 | MCX | 2026-11-23 | 108 | 42 | 66 | 66 | 0 | FY | 108 | 0 | T2 |  |
| 417 | MCX | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 418 | MFSL | 2026-10-27 | 118 | 61 | 57 | 57 | 0 | FY | 118 | 0 | T1 |  |
| 419 | MFSL | 2026-11-23 | 118 | 51 | 67 | 67 | 0 | FY | 118 | 0 | T2 |  |
| 420 | MFSL | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 421 | MIDCPNIFTY | 2026-10-27 | 590 | 316 | 274 | 0 | 274 | FY+UP | 316 | 274 | T1 |  |
| 422 | MIDCPNIFTY | 2026-11-23 | 586 | 312 | 274 | 0 | 274 | FY+UP | 312 | 274 | T2 |  |
| 423 | MIDCPNIFTY | 2026-12-29 | 490 | 236 | 254 | 0 | 254 | FY+UP | 236 | 254 | T2 |  |
| 424 | MOTHERSON | 2026-10-27 | 108 | 47 | 61 | 61 | 0 | FY | 108 | 0 | T1 |  |
| 425 | MOTHERSON | 2026-11-23 | 108 | 46 | 62 | 62 | 0 | FY | 108 | 0 | T2 |  |
| 426 | MOTHERSON | 2026-12-29 | 48 | 22 | 26 | 26 | 0 | FY | 48 | 0 | T2 |  |
| 427 | MOTILALOFS | 2026-10-27 | 90 | 42 | 48 | 48 | 0 | FY | 90 | 0 | T1 |  |
| 428 | MOTILALOFS | 2026-11-23 | 90 | 40 | 50 | 50 | 0 | FY | 90 | 0 | T2 |  |
| 429 | MOTILALOFS | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 430 | MPHASIS | 2026-10-27 | 94 | 42 | 52 | 52 | 0 | FY | 94 | 0 | T1 |  |
| 431 | MPHASIS | 2026-11-23 | 94 | 40 | 54 | 54 | 0 | FY | 94 | 0 | T2 |  |
| 432 | MPHASIS | 2026-12-29 | 52 | 22 | 30 | 30 | 0 | FY | 52 | 0 | T2 |  |
| 433 | MUTHOOTFIN | 2026-10-27 | 86 | 49 | 37 | 37 | 0 | FY | 86 | 0 | T1 |  |
| 434 | MUTHOOTFIN | 2026-11-23 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 435 | MUTHOOTFIN | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 436 | NAM-INDIA | 2026-10-27 | 90 | 44 | 46 | 46 | 0 | FY | 90 | 0 | T1 |  |
| 437 | NAM-INDIA | 2026-11-23 | 90 | 34 | 56 | 56 | 0 | FY | 90 | 0 | T2 |  |
| 438 | NAM-INDIA | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 439 | NATIONALUM | 2026-10-27 | 118 | 62 | 56 | 56 | 0 | FY | 118 | 0 | T1 |  |
| 440 | NATIONALUM | 2026-11-23 | 118 | 51 | 67 | 67 | 0 | FY | 118 | 0 | T2 |  |
| 441 | NATIONALUM | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 442 | NAUKRI | 2026-10-27 | 108 | 45 | 63 | 63 | 0 | FY | 108 | 0 | T1 |  |
| 443 | NAUKRI | 2026-11-23 | 108 | 40 | 68 | 68 | 0 | FY | 108 | 0 | T2 |  |
| 444 | NAUKRI | 2026-12-29 | 50 | 20 | 30 | 30 | 0 | FY | 50 | 0 | T2 |  |
| 445 | NBCC | 2026-10-27 | 145 | 70 | 75 | 75 | 0 | FY | 145 | 0 | T1 |  |
| 446 | NBCC | 2026-11-23 | 132 | 53 | 79 | 79 | 0 | FY | 132 | 0 | T2 |  |
| 447 | NBCC | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 448 | NESTLEIND | 2026-10-27 | 218 | 102 | 116 | 100 | 16 | FY | 202 | 16 | T1 |  |
| 449 | NESTLEIND | 2026-11-23 | 218 | 92 | 126 | 110 | 16 | FY | 202 | 16 | T2 |  |
| 450 | NESTLEIND | 2026-12-29 | 112 | 48 | 64 | 64 | 0 | FY | 112 | 0 | T2 |  |
| 451 | NHPC | 2026-10-27 | 128 | 51 | 77 | 77 | 0 | FY | 128 | 0 | T1 |  |
| 452 | NHPC | 2026-11-23 | 128 | 46 | 82 | 82 | 0 | FY | 128 | 0 | T2 |  |
| 453 | NHPC | 2026-12-29 | 64 | 24 | 40 | 40 | 0 | FY | 64 | 0 | T2 |  |
| 454 | NIFTY | 2026-10-27 | 522 | 288 | 234 | 0 | 234 | FY+UP | 288 | 234 | T1 |  |
| 455 | NIFTY | 2026-11-03 | 470 | 208 | 262 | 6 | 256 | FY+UP | 214 | 256 | T2 |  |
| 456 | NIFTY | 2026-11-09 | 458 | 208 | 250 | 0 | 250 | FY+UP | 208 | 250 | T2 |  |
| 457 | NIFTY | 2026-11-17 | 444 | 176 | 268 | 26 | 242 | FY | 202 | 242 | T2 |  |
| 458 | NIFTY | 2026-11-23 | 506 | 244 | 262 | 0 | 262 | FY+UP | 244 | 262 | T2 |  |
| 459 | NIFTY | 2026-12-29 | 515 | 246 | 269 | 2 | 267 | FY+UP | 248 | 267 | T2 |  |
| 460 | NIFTY | 2027-03-30 | 64 | 26 | 38 | 38 | 0 | FY | 64 | 0 | T2 |  |
| 461 | NIFTY | 2027-06-29 | 66 | 28 | 38 | 38 | 0 | FY | 66 | 0 | T2 |  |
| 462 | NIFTY | 2027-09-28 | 60 | 22 | 38 | 38 | 0 | FY | 60 | 0 | T2 |  |
| 463 | NIFTY | 2027-12-28 | 73 | 35 | 38 | 38 | 0 | FY | 73 | 0 | T2 |  |
| 464 | NIFTY | 2028-06-27 | 65 | 27 | 38 | 38 | 0 | FY | 65 | 0 | T2 |  |
| 465 | NIFTY | 2028-12-26 | 70 | 32 | 38 | 38 | 0 | FY | 70 | 0 | T2 |  |
| 466 | NIFTY | 2029-06-26 | 65 | 27 | 38 | 38 | 0 | FY | 65 | 0 | T2 |  |
| 467 | NIFTY | 2029-12-24 | 67 | 29 | 38 | 38 | 0 | FY | 67 | 0 | T2 |  |
| 468 | NIFTY | 2030-06-25 | 64 | 26 | 38 | 38 | 0 | FY | 64 | 0 | T2 |  |
| 469 | NIFTY | 2030-12-31 | 64 | 26 | 38 | 38 | 0 | FY | 64 | 0 | T2 |  |
| 470 | NIFTY | 2031-06-24 | 64 | 26 | 38 | 38 | 0 | FY | 64 | 0 | T2 |  |
| 471 | NIFTYFPI | 2026-10-27 | 388 | 182 | 206 | 34 | 172 | FY+UP | 216 | 172 | T1 |  |
| 472 | NIFTYFPI | 2026-11-23 | 386 | 180 | 206 | 34 | 172 | FY+UP | 214 | 172 | T2 |  |
| 473 | NIFTYFPI | 2026-12-29 | 350 | 138 | 212 | 68 | 144 | FY+UP | 206 | 144 | T2 |  |
| 474 | NIFTYNXT50 | 2026-10-27 | 774 | 424 | 350 | 0 | 350 | FY+UP | 424 | 350 | T1 |  |
| 475 | NIFTYNXT50 | 2026-11-23 | 766 | 382 | 384 | 0 | 384 | FY+UP | 382 | 384 | T2 |  |
| 476 | NIFTYNXT50 | 2026-12-29 | 676 | 292 | 384 | 0 | 384 | FY+UP | 292 | 384 | T2 |  |
| 477 | NMDC | 2026-10-27 | 136 | 77 | 59 | 59 | 0 | FY | 136 | 0 | T1 |  |
| 478 | NMDC | 2026-11-23 | 136 | 58 | 78 | 78 | 0 | FY | 136 | 0 | T2 |  |
| 479 | NMDC | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 480 | NTPC | 2026-10-27 | 206 | 82 | 124 | 120 | 4 | FY | 202 | 4 | T1 |  |
| 481 | NTPC | 2026-11-23 | 206 | 80 | 126 | 122 | 4 | FY | 202 | 4 | T2 |  |
| 482 | NTPC | 2026-12-29 | 104 | 40 | 64 | 64 | 0 | FY | 104 | 0 | T2 |  |
| 483 | NYKAA | 2026-10-27 | 116 | 48 | 68 | 68 | 0 | FY | 116 | 0 | T1 |  |
| 484 | NYKAA | 2026-11-23 | 116 | 48 | 68 | 68 | 0 | FY | 116 | 0 | T2 |  |
| 485 | NYKAA | 2026-12-29 | 62 | 28 | 34 | 34 | 0 | FY | 62 | 0 | T2 |  |
| 486 | OBEROIRLTY | 2026-10-27 | 132 | 62 | 70 | 70 | 0 | FY | 132 | 0 | T1 |  |
| 487 | OBEROIRLTY | 2026-11-23 | 132 | 53 | 79 | 79 | 0 | FY | 132 | 0 | T2 |  |
| 488 | OBEROIRLTY | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 489 | OFSS | 2026-10-27 | 137 | 87 | 50 | 50 | 0 | FY | 137 | 0 | T1 |  |
| 490 | OFSS | 2026-11-23 | 83 | 31 | 52 | 52 | 0 | FY | 83 | 0 | T2 |  |
| 491 | OFSS | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 492 | OIL | 2026-10-27 | 152 | 64 | 88 | 88 | 0 | FY | 152 | 0 | T1 |  |
| 493 | OIL | 2026-11-23 | 152 | 56 | 96 | 96 | 0 | FY | 152 | 0 | T2 |  |
| 494 | OIL | 2026-12-29 | 84 | 32 | 52 | 52 | 0 | FY | 84 | 0 | T2 |  |
| 495 | ONGC | 2026-10-27 | 146 | 64 | 82 | 82 | 0 | FY | 146 | 0 | T1 |  |
| 496 | ONGC | 2026-11-23 | 146 | 55 | 91 | 91 | 0 | FY | 146 | 0 | T2 |  |
| 497 | ONGC | 2026-12-29 | 84 | 32 | 52 | 52 | 0 | FY | 84 | 0 | T2 |  |
| 498 | PAGEIND | 2026-10-27 | 114 | 48 | 66 | 66 | 0 | FY | 114 | 0 | T1 |  |
| 499 | PAGEIND | 2026-11-23 | 114 | 44 | 70 | 70 | 0 | FY | 114 | 0 | T2 |  |
| 500 | PAGEIND | 2026-12-29 | 62 | 26 | 36 | 36 | 0 | FY | 62 | 0 | T2 |  |
| 501 | PATANJALI | 2026-10-27 | 110 | 60 | 50 | 50 | 0 | FY | 110 | 0 | T1 |  |
| 502 | PATANJALI | 2026-11-23 | 110 | 42 | 68 | 68 | 0 | FY | 110 | 0 | T2 |  |
| 503 | PATANJALI | 2026-12-29 | 58 | 22 | 36 | 36 | 0 | FY | 58 | 0 | T2 |  |
| 504 | PAYTM | 2026-10-27 | 150 | 71 | 79 | 79 | 0 | FY | 150 | 0 | T1 |  |
| 505 | PAYTM | 2026-11-23 | 150 | 62 | 88 | 88 | 0 | FY | 150 | 0 | T2 |  |
| 506 | PAYTM | 2026-12-29 | 70 | 32 | 38 | 38 | 0 | FY | 70 | 0 | T2 |  |
| 507 | PERSISTENT | 2026-10-27 | 102 | 46 | 56 | 56 | 0 | FY | 102 | 0 | T1 |  |
| 508 | PERSISTENT | 2026-11-23 | 102 | 46 | 56 | 56 | 0 | FY | 102 | 0 | T2 |  |
| 509 | PERSISTENT | 2026-12-29 | 54 | 26 | 28 | 28 | 0 | FY | 54 | 0 | T2 |  |
| 510 | PETRONET | 2026-10-27 | 188 | 72 | 116 | 116 | 0 | FY | 188 | 0 | T1 |  |
| 511 | PETRONET | 2026-11-23 | 188 | 72 | 116 | 116 | 0 | FY | 188 | 0 | T2 |  |
| 512 | PETRONET | 2026-12-29 | 94 | 38 | 56 | 56 | 0 | FY | 94 | 0 | T2 |  |
| 513 | PFC | 2026-10-27 | 114 | 57 | 57 | 57 | 0 | FY | 114 | 0 | T1 |  |
| 514 | PFC | 2026-11-23 | 114 | 48 | 66 | 66 | 0 | FY | 114 | 0 | T2 |  |
| 515 | PFC | 2026-12-29 | 62 | 26 | 36 | 36 | 0 | FY | 62 | 0 | T2 |  |
| 516 | PGEL | 2026-10-27 | 90 | 47 | 43 | 43 | 0 | FY | 90 | 0 | T1 |  |
| 517 | PGEL | 2026-11-23 | 90 | 38 | 52 | 52 | 0 | FY | 90 | 0 | T2 |  |
| 518 | PGEL | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 519 | PHOENIXLTD | 2026-10-27 | 156 | 67 | 89 | 89 | 0 | FY | 156 | 0 | T1 |  |
| 520 | PHOENIXLTD | 2026-11-23 | 156 | 64 | 92 | 92 | 0 | FY | 156 | 0 | T2 |  |
| 521 | PHOENIXLTD | 2026-12-29 | 78 | 34 | 44 | 44 | 0 | FY | 78 | 0 | T2 |  |
| 522 | PIDILITIND | 2026-10-27 | 128 | 58 | 70 | 70 | 0 | FY | 128 | 0 | T1 |  |
| 523 | PIDILITIND | 2026-11-23 | 128 | 48 | 80 | 80 | 0 | FY | 128 | 0 | T2 |  |
| 524 | PIDILITIND | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 525 | PIIND | 2026-10-27 | 202 | 102 | 100 | 98 | 2 | FY | 200 | 2 | T1 |  |
| 526 | PIIND | 2026-11-23 | 186 | 78 | 108 | 108 | 0 | FY | 186 | 0 | T2 |  |
| 527 | PIIND | 2026-12-29 | 98 | 42 | 56 | 56 | 0 | FY | 98 | 0 | T2 |  |
| 528 | PNB | 2026-10-27 | 200 | 80 | 120 | 120 | 0 | FY | 200 | 0 | T1 |  |
| 529 | PNB | 2026-11-23 | 200 | 80 | 120 | 120 | 0 | FY | 200 | 0 | T2 |  |
| 530 | PNB | 2026-12-29 | 98 | 42 | 56 | 56 | 0 | FY | 98 | 0 | T2 |  |
| 531 | PNBHOUSING | 2026-10-27 | 102 | 46 | 56 | 56 | 0 | FY | 102 | 0 | T1 |  |
| 532 | PNBHOUSING | 2026-11-23 | 102 | 46 | 56 | 56 | 0 | FY | 102 | 0 | T2 |  |
| 533 | PNBHOUSING | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 534 | POLICYBZR | 2026-10-27 | 177 | 127 | 50 | 50 | 0 | FY | 177 | 0 | T1 |  |
| 535 | POLICYBZR | 2026-11-23 | 154 | 71 | 83 | 83 | 0 | FY | 154 | 0 | T2 |  |
| 536 | POLICYBZR | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 537 | POLYCAB | 2026-10-27 | 130 | 70 | 60 | 60 | 0 | FY | 130 | 0 | T1 |  |
| 538 | POLYCAB | 2026-11-23 | 130 | 57 | 73 | 73 | 0 | FY | 130 | 0 | T2 |  |
| 539 | POLYCAB | 2026-12-29 | 66 | 28 | 38 | 38 | 0 | FY | 66 | 0 | T2 |  |
| 540 | POWERGRID | 2026-10-27 | 178 | 77 | 101 | 101 | 0 | FY | 178 | 0 | T1 |  |
| 541 | POWERGRID | 2026-11-23 | 178 | 70 | 108 | 108 | 0 | FY | 178 | 0 | T2 |  |
| 542 | POWERGRID | 2026-12-29 | 88 | 36 | 52 | 52 | 0 | FY | 88 | 0 | T2 |  |
| 543 | POWERINDIA | 2026-10-27 | 110 | 53 | 57 | 57 | 0 | FY | 110 | 0 | T1 |  |
| 544 | POWERINDIA | 2026-11-23 | 110 | 50 | 60 | 60 | 0 | FY | 110 | 0 | T2 |  |
| 545 | POWERINDIA | 2026-12-29 | 52 | 26 | 26 | 26 | 0 | FY | 52 | 0 | T2 |  |
| 546 | PREMIERENE | 2026-10-27 | 86 | 43 | 43 | 43 | 0 | FY | 86 | 0 | T1 |  |
| 547 | PREMIERENE | 2026-11-23 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 548 | PREMIERENE | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 549 | PRESTIGE | 2026-10-27 | 114 | 58 | 56 | 56 | 0 | FY | 114 | 0 | T1 |  |
| 550 | PRESTIGE | 2026-11-23 | 114 | 46 | 68 | 68 | 0 | FY | 114 | 0 | T2 |  |
| 551 | PRESTIGE | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 552 | RADICO | 2026-10-27 | 154 | 64 | 90 | 90 | 0 | FY | 154 | 0 | T1 |  |
| 553 | RADICO | 2026-11-23 | 154 | 64 | 90 | 90 | 0 | FY | 154 | 0 | T2 |  |
| 554 | RADICO | 2026-12-29 | 70 | 32 | 38 | 38 | 0 | FY | 70 | 0 | T2 |  |
| 555 | RBLBANK | 2026-10-27 | 142 | 67 | 75 | 75 | 0 | FY | 142 | 0 | T1 |  |
| 556 | RBLBANK | 2026-11-23 | 142 | 62 | 80 | 80 | 0 | FY | 142 | 0 | T2 |  |
| 557 | RBLBANK | 2026-12-29 | 72 | 32 | 40 | 40 | 0 | FY | 72 | 0 | T2 |  |
| 558 | RECLTD | 2026-10-27 | 106 | 52 | 54 | 54 | 0 | FY | 106 | 0 | T1 |  |
| 559 | RECLTD | 2026-11-23 | 106 | 43 | 63 | 63 | 0 | FY | 106 | 0 | T2 |  |
| 560 | RECLTD | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 561 | RELIANCE | 2026-10-27 | 194 | 96 | 98 | 98 | 0 | FY | 194 | 0 | T1 |  |
| 562 | RELIANCE | 2026-11-23 | 194 | 79 | 115 | 115 | 0 | FY | 194 | 0 | T2 |  |
| 563 | RELIANCE | 2026-12-29 | 104 | 40 | 64 | 64 | 0 | FY | 104 | 0 | T2 |  |
| 564 | RVNL | 2026-10-27 | 136 | 68 | 68 | 68 | 0 | FY | 136 | 0 | T1 |  |
| 565 | RVNL | 2026-11-23 | 136 | 60 | 76 | 76 | 0 | FY | 136 | 0 | T2 |  |
| 566 | RVNL | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 567 | SAGILITY | 2026-10-27 | 76 | 33 | 43 | 43 | 0 | FY | 76 | 0 | T1 |  |
| 568 | SAGILITY | 2026-11-23 | 76 | 32 | 44 | 44 | 0 | FY | 76 | 0 | T2 |  |
| 569 | SAGILITY | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 570 | SAIL | 2026-10-27 | 116 | 50 | 66 | 66 | 0 | FY | 116 | 0 | T1 |  |
| 571 | SAIL | 2026-11-23 | 116 | 50 | 66 | 66 | 0 | FY | 116 | 0 | T2 |  |
| 572 | SAIL | 2026-12-29 | 62 | 26 | 36 | 36 | 0 | FY | 62 | 0 | T2 |  |
| 573 | SBICARD | 2026-10-27 | 106 | 64 | 42 | 42 | 0 | FY | 106 | 0 | T1 |  |
| 574 | SBICARD | 2026-11-23 | 106 | 44 | 62 | 62 | 0 | FY | 106 | 0 | T2 |  |
| 575 | SBICARD | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 576 | SBILIFE | 2026-10-27 | 134 | 57 | 77 | 77 | 0 | FY | 134 | 0 | T1 |  |
| 577 | SBILIFE | 2026-11-23 | 134 | 54 | 80 | 80 | 0 | FY | 134 | 0 | T2 |  |
| 578 | SBILIFE | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 579 | SBIN | 2026-10-27 | 150 | 76 | 74 | 74 | 0 | FY | 150 | 0 | T1 |  |
| 580 | SBIN | 2026-11-23 | 150 | 57 | 93 | 93 | 0 | FY | 150 | 0 | T2 |  |
| 581 | SBIN | 2026-12-29 | 82 | 30 | 52 | 52 | 0 | FY | 82 | 0 | T2 |  |
| 582 | SHREECEM | 2026-10-27 | 150 | 65 | 85 | 85 | 0 | FY | 150 | 0 | T1 |  |
| 583 | SHREECEM | 2026-11-23 | 150 | 57 | 93 | 93 | 0 | FY | 150 | 0 | T2 |  |
| 584 | SHREECEM | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 585 | SHRIRAMFIN | 2026-10-27 | 88 | 47 | 41 | 41 | 0 | FY | 88 | 0 | T1 |  |
| 586 | SHRIRAMFIN | 2026-11-23 | 88 | 39 | 49 | 49 | 0 | FY | 88 | 0 | T2 |  |
| 587 | SHRIRAMFIN | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 588 | SIEMENS | 2026-10-27 | 134 | 58 | 76 | 76 | 0 | FY | 134 | 0 | T1 |  |
| 589 | SIEMENS | 2026-11-23 | 134 | 54 | 80 | 80 | 0 | FY | 134 | 0 | T2 |  |
| 590 | SIEMENS | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 591 | SOLARINDS | 2026-10-27 | 130 | 59 | 71 | 71 | 0 | FY | 130 | 0 | T1 |  |
| 592 | SOLARINDS | 2026-11-23 | 130 | 51 | 79 | 79 | 0 | FY | 130 | 0 | T2 |  |
| 593 | SOLARINDS | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 594 | SONACOMS | 2026-10-27 | 134 | 58 | 76 | 76 | 0 | FY | 134 | 0 | T1 |  |
| 595 | SONACOMS | 2026-11-23 | 134 | 58 | 76 | 76 | 0 | FY | 134 | 0 | T2 |  |
| 596 | SONACOMS | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 597 | SRF | 2026-10-27 | 198 | 85 | 113 | 113 | 0 | FY | 198 | 0 | T1 |  |
| 598 | SRF | 2026-11-23 | 188 | 70 | 118 | 118 | 0 | FY | 188 | 0 | T2 |  |
| 599 | SRF | 2026-12-29 | 100 | 36 | 64 | 64 | 0 | FY | 100 | 0 | T2 |  |
| 600 | SUNPHARMA | 2026-10-27 | 146 | 63 | 83 | 83 | 0 | FY | 146 | 0 | T1 |  |
| 601 | SUNPHARMA | 2026-11-23 | 146 | 54 | 92 | 92 | 0 | FY | 146 | 0 | T2 |  |
| 602 | SUNPHARMA | 2026-12-29 | 72 | 28 | 44 | 44 | 0 | FY | 72 | 0 | T2 |  |
| 603 | SUPREMEIND | 2026-10-27 | 114 | 48 | 66 | 66 | 0 | FY | 114 | 0 | T1 |  |
| 604 | SUPREMEIND | 2026-11-23 | 114 | 46 | 68 | 68 | 0 | FY | 114 | 0 | T2 |  |
| 605 | SUPREMEIND | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 606 | SUZLON | 2026-10-27 | 74 | 52 | 22 | 22 | 0 | FY | 74 | 0 | T1 |  |
| 607 | SUZLON | 2026-11-23 | 74 | 42 | 32 | 32 | 0 | FY | 74 | 0 | T2 |  |
| 608 | SUZLON | 2026-12-29 | 52 | 22 | 30 | 30 | 0 | FY | 52 | 0 | T2 |  |
| 609 | SWIGGY | 2026-10-27 | 94 | 53 | 41 | 41 | 0 | FY | 94 | 0 | T1 |  |
| 610 | SWIGGY | 2026-11-23 | 94 | 43 | 51 | 51 | 0 | FY | 94 | 0 | T2 |  |
| 611 | SWIGGY | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 612 | TATACONSUM | 2026-10-27 | 158 | 69 | 89 | 89 | 0 | FY | 158 | 0 | T1 |  |
| 613 | TATACONSUM | 2026-11-23 | 158 | 63 | 95 | 95 | 0 | FY | 158 | 0 | T2 |  |
| 614 | TATACONSUM | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 615 | TATAELXSI | 2026-10-27 | 140 | 68 | 72 | 72 | 0 | FY | 140 | 0 | T1 |  |
| 616 | TATAELXSI | 2026-11-23 | 140 | 66 | 74 | 74 | 0 | FY | 140 | 0 | T2 |  |
| 617 | TATAELXSI | 2026-12-29 | 74 | 38 | 36 | 36 | 0 | FY | 74 | 0 | T2 |  |
| 618 | TATAPOWER | 2026-10-27 | 114 | 55 | 59 | 59 | 0 | FY | 114 | 0 | T1 |  |
| 619 | TATAPOWER | 2026-11-23 | 114 | 48 | 66 | 66 | 0 | FY | 114 | 0 | T2 |  |
| 620 | TATAPOWER | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 621 | TATASTEEL | 2026-10-27 | 116 | 54 | 62 | 62 | 0 | FY | 116 | 0 | T1 |  |
| 622 | TATASTEEL | 2026-11-23 | 116 | 50 | 66 | 66 | 0 | FY | 116 | 0 | T2 |  |
| 623 | TATASTEEL | 2026-12-29 | 68 | 28 | 40 | 40 | 0 | FY | 68 | 0 | T2 |  |
| 624 | TCS | 2026-10-27 | 206 | 94 | 112 | 108 | 4 | FY | 202 | 4 | T1 |  |
| 625 | TCS | 2026-11-23 | 206 | 90 | 116 | 112 | 4 | FY | 202 | 4 | T2 |  |
| 626 | TCS | 2026-12-29 | 108 | 46 | 62 | 62 | 0 | FY | 108 | 0 | T2 |  |
| 627 | TECHM | 2026-10-27 | 154 | 60 | 94 | 94 | 0 | FY | 154 | 0 | T1 |  |
| 628 | TECHM | 2026-11-23 | 154 | 60 | 94 | 94 | 0 | FY | 154 | 0 | T2 |  |
| 629 | TECHM | 2026-12-29 | 80 | 34 | 46 | 46 | 0 | FY | 80 | 0 | T2 |  |
| 630 | TIINDIA | 2026-10-27 | 92 | 54 | 38 | 38 | 0 | FY | 92 | 0 | T1 |  |
| 631 | TIINDIA | 2026-11-23 | 92 | 42 | 50 | 50 | 0 | FY | 92 | 0 | T2 |  |
| 632 | TIINDIA | 2026-12-29 | 50 | 22 | 28 | 28 | 0 | FY | 50 | 0 | T2 |  |
| 633 | TITAN | 2026-10-27 | 156 | 74 | 82 | 82 | 0 | FY | 156 | 0 | T1 |  |
| 634 | TITAN | 2026-11-23 | 156 | 60 | 96 | 96 | 0 | FY | 156 | 0 | T2 |  |
| 635 | TITAN | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 636 | TMPV | 2026-10-27 | 106 | 61 | 45 | 45 | 0 | FY | 106 | 0 | T1 |  |
| 637 | TMPV | 2026-11-23 | 106 | 46 | 60 | 60 | 0 | FY | 106 | 0 | T2 |  |
| 638 | TMPV | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 639 | TORNTPHARM | 2026-10-27 | 156 | 64 | 92 | 92 | 0 | FY | 156 | 0 | T1 |  |
| 640 | TORNTPHARM | 2026-11-23 | 156 | 58 | 98 | 98 | 0 | FY | 156 | 0 | T2 |  |
| 641 | TORNTPHARM | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 642 | TRENT | 2026-10-27 | 102 | 46 | 56 | 56 | 0 | FY | 102 | 0 | T1 |  |
| 643 | TRENT | 2026-11-23 | 102 | 46 | 56 | 56 | 0 | FY | 102 | 0 | T2 |  |
| 644 | TRENT | 2026-12-29 | 54 | 26 | 28 | 28 | 0 | FY | 54 | 0 | T2 |  |
| 645 | TVSMOTOR | 2026-10-27 | 134 | 60 | 74 | 74 | 0 | FY | 134 | 0 | T1 |  |
| 646 | TVSMOTOR | 2026-11-23 | 134 | 54 | 80 | 80 | 0 | FY | 134 | 0 | T2 |  |
| 647 | TVSMOTOR | 2026-12-29 | 70 | 30 | 40 | 40 | 0 | FY | 70 | 0 | T2 |  |
| 648 | UJJIVANSFB | 2026-10-27 | 118 | 52 | 66 | 66 | 0 | FY | 118 | 0 | T1 |  |
| 649 | UJJIVANSFB | 2026-11-23 | 118 | 52 | 66 | 66 | 0 | FY | 118 | 0 | T2 |  |
| 650 | UJJIVANSFB | 2026-12-29 | 52 | 26 | 26 | 26 | 0 | FY | 52 | 0 | T2 |  |
| 651 | ULTRACEMCO | 2026-10-27 | 178 | 76 | 102 | 102 | 0 | FY | 178 | 0 | T1 |  |
| 652 | ULTRACEMCO | 2026-11-23 | 178 | 70 | 108 | 108 | 0 | FY | 178 | 0 | T2 |  |
| 653 | ULTRACEMCO | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 654 | UNIONBANK | 2026-10-27 | 110 | 51 | 59 | 59 | 0 | FY | 110 | 0 | T1 |  |
| 655 | UNIONBANK | 2026-11-23 | 110 | 43 | 67 | 67 | 0 | FY | 110 | 0 | T2 |  |
| 656 | UNIONBANK | 2026-12-29 | 64 | 24 | 40 | 40 | 0 | FY | 64 | 0 | T2 |  |
| 657 | UNITDSPR | 2026-10-27 | 112 | 58 | 54 | 54 | 0 | FY | 112 | 0 | T1 |  |
| 658 | UNITDSPR | 2026-11-23 | 112 | 44 | 68 | 68 | 0 | FY | 112 | 0 | T2 |  |
| 659 | UNITDSPR | 2026-12-29 | 60 | 24 | 36 | 36 | 0 | FY | 60 | 0 | T2 |  |
| 660 | UNOMINDA | 2026-10-27 | 96 | 45 | 51 | 51 | 0 | FY | 96 | 0 | T1 |  |
| 661 | UNOMINDA | 2026-11-23 | 96 | 42 | 54 | 54 | 0 | FY | 96 | 0 | T2 |  |
| 662 | UNOMINDA | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 663 | UPL | 2026-10-27 | 190 | 92 | 98 | 98 | 0 | FY | 190 | 0 | T1 |  |
| 664 | UPL | 2026-11-23 | 190 | 82 | 108 | 108 | 0 | FY | 190 | 0 | T2 |  |
| 665 | UPL | 2026-12-29 | 98 | 42 | 56 | 56 | 0 | FY | 98 | 0 | T2 |  |
| 666 | VBL | 2026-10-27 | 130 | 56 | 74 | 74 | 0 | FY | 130 | 0 | T1 |  |
| 667 | VBL | 2026-11-23 | 130 | 50 | 80 | 80 | 0 | FY | 130 | 0 | T2 |  |
| 668 | VBL | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 669 | VEDL | 2026-10-27 | 88 | 48 | 40 | 40 | 0 | FY | 88 | 0 | T1 |  |
| 670 | VEDL | 2026-11-23 | 88 | 37 | 51 | 51 | 0 | FY | 88 | 0 | T2 |  |
| 671 | VEDL | 2026-12-29 | 48 | 20 | 28 | 28 | 0 | FY | 48 | 0 | T2 |  |
| 672 | VMM | 2026-10-27 | 169 | 83 | 86 | 86 | 0 | FY | 169 | 0 | T1 |  |
| 673 | VMM | 2026-11-23 | 158 | 63 | 95 | 95 | 0 | FY | 158 | 0 | T2 |  |
| 674 | VMM | 2026-12-29 | 86 | 34 | 52 | 52 | 0 | FY | 86 | 0 | T2 |  |
| 675 | VOLTAS | 2026-10-27 | 92 | 52 | 40 | 40 | 0 | FY | 92 | 0 | T1 |  |
| 676 | VOLTAS | 2026-11-23 | 92 | 36 | 56 | 56 | 0 | FY | 92 | 0 | T2 |  |
| 677 | VOLTAS | 2026-12-29 | 46 | 18 | 28 | 28 | 0 | FY | 46 | 0 | T2 |  |
| 678 | WAAREEENER | 2026-10-27 | 229 | 105 | 124 | 92 | 32 | FY | 197 | 32 | T1 |  |
| 679 | WAAREEENER | 2026-11-23 | 218 | 88 | 130 | 114 | 16 | FY | 202 | 16 | T2 |  |
| 680 | WAAREEENER | 2026-12-29 | 110 | 44 | 66 | 66 | 0 | FY | 110 | 0 | T2 |  |
| 681 | WIPRO | 2026-10-27 | 126 | 60 | 66 | 66 | 0 | FY | 126 | 0 | T1 |  |
| 682 | WIPRO | 2026-11-23 | 126 | 52 | 74 | 74 | 0 | FY | 126 | 0 | T2 |  |
| 683 | WIPRO | 2026-12-29 | 66 | 26 | 40 | 40 | 0 | FY | 66 | 0 | T2 |  |
| 684 | YESBANK | 2026-10-27 | 52 | 28 | 24 | 24 | 0 | FY | 52 | 0 | T1 |  |
| 685 | YESBANK | 2026-11-23 | 52 | 26 | 26 | 26 | 0 | FY | 52 | 0 | T2 |  |
| 686 | YESBANK | 2026-12-29 | 40 | 18 | 22 | 22 | 0 | FY | 40 | 0 | T2 |  |
| 687 | ZYDUSLIFE | 2026-10-27 | 174 | 72 | 102 | 102 | 0 | FY | 174 | 0 | T1 |  |
| 688 | ZYDUSLIFE | 2026-11-23 | 174 | 67 | 107 | 107 | 0 | FY | 174 | 0 | T2 |  |
| 689 | ZYDUSLIFE | 2026-12-29 | 90 | 34 | 56 | 56 | 0 | FY | 90 | 0 | T2 |  |

### 3.16 The option-surface architecture correction (V16)

**What changed.** V15 still treated the option surface as **87 842 individual
symbols**, and priced the 5 934-symbol deep-ladder tail as **per-symbol REST
work** (5 934 depth calls + 119 quote calls). That was the wrong unit of
work. The unit is the **(underlying, expiry) cell**: **one chain response
covers every symbol in that cell**, at one request per cell. On 2026-10-21 the
maintainer stated the scope that makes this explicit — *"20 strikes on either
side of ATM is enough for us"* — and under that scope the per-symbol tail
leaves the required surface entirely.

#### 3.16.1 The scope input, and the decisions that resolved it

| | |
|---|---|
| **D-21** (maintainer, **2026-10-21**) | *"20 strikes on either side of ATM is enough for us."* |

The sentence admitted two readings. **The maintainer has now settled it**
(**2026-10-21**), and both resulting decisions are recorded in the ledger
(§2.2):

| Decision | Content | Effect |
|---|---|---|
| **D-22** | **DhanHQ is EXCLUDED** — no credential, no probe, no integration, no order routed through it | the §3.16.7 evaluation is **closed as excluded**, not merely not-adopted |
| **D-23** | **±20 strikes around the TRUE ATM define the required option surface, across EVERY underlying-expiry cell** — **not** merely the presentation layer | **M-15 is CLOSED.** The design basis of §3.16 is now the confirmed requirement, and the deep wings are outside the required surface |
| **D-24** | **Multi-level depth is NOT required** — the chain's top-of-book bid/ask satisfies the current requirement | **M-16 is CLOSED.** Per-symbol depth polling is **removed** from the proposed request budget |
| **D-25** | **The deep-ladder tail beyond ±20 is OUT OF SCOPE for now** | the tail is neither served nor claimed as a required surface; its cost is retained only as the **re-entry cost** (§3.12.4) |
| **D-26** | **Show what we need to see — do not synchronise everything all the time.** The refresh policy is **session-scoped and demand-driven** | the background sweep runs only **inside the market session**, the visible tier is refreshed **on demand**, and every displayed surface carries its **own age** and the **session it belongs to** (§3.16.5a) |

**What this removes.** The "reading (b)" branch — the full-ladder background
tier with its **5 934 depth + 119 quote** calls — is **no longer an
alternative design**: D-23 and D-25 settle the scope, and D-24 settles the
depth question. **M-12's subject (the IV/Greeks gap on the deep wings) leaves
the required surface with D-25**, so it no longer blocks the required-surface
acceptance criterion; it reopens only if the tail returns to scope.

#### 3.16.2 The measured consequence of the confirmed scope

| Quantity | Full ladder (the old unit of work) | **±20 ATM scope (D-23, confirmed)** | Change |
|---|---|---|---|
| In-scope option symbols | 87 842 | **9 087** (10.3 % of the ladder; a proxy-measured **lower bound** — §3.16.3) | −78 755 |
| In-scope strikes | 43 973 | **4 550** | −39 423 |
| Symbols with **no** chain source (the class-C gap) | 5 934 | **0** — and the gap's own symbols are **out of scope** (D-25) | closed |
| **Chain requests** for a complete sweep | 707 | **689 = exactly one per cell** (670 FYERS + 19 Upstox) | −18 |
| **Quote** requests | 119 | **0** (D-24) | −119 |
| **Depth** requests | 5 934 | **0** (D-24) | **−5 934** |
| FYERS calls per sweep | 670 chain + 119 quote + 5 934 depth | **670 chain** | −6 053 |
| FYERS calls per day | 7 011 (of 5 000 — did not fit) | **0 when idle; 1 per opened cell; 0 outside the session** — the 689-request sweep is **withdrawn** (§3.16.4, §3.18) | fits |
| Upstox calls per sweep | 39 | **21** | −18 |
| Fits the FYERS Standard 5 000/day pool? | **no** | **yes** | **changed** |

**The class-C row is the substantive change.** The 5 934 symbols that no chain
endpoint could serve live **entirely outside the ±20 window** — they are the
deep wings of the widest index expiries. Under D-23 they are **not**
unavailable; they are **out of scope** (D-25). The IV/Greeks gap, the
current-vs-previous-OI distinction, the 5 934 depth calls and the 119 quote
calls all follow from that. **None of them was a provider limitation that a
better provider would have fixed** — they were a consequence of pricing the
whole ladder symbol by symbol and of a scope the maintainer has now narrowed.

#### 3.16.3 The ATM cannot be taken from the catalogue — but the budget does not depend on it

The ±20 window is defined against the **ATM**, and the catalogue carries **no
prices**, so the ATM cannot be computed offline. Measured against the best
offline proxy (the median of the cell's distinct strikes — the centre FYERS
itself uses for its ±50 window), the window captures a **median of 3 strikes
per cell**, against a theoretical maximum of 41:

| | value |
|---|---|
| Ladder strikes per cell | mean **63.8**, median 55, min 13, max 387 |
| Scope strikes per cell (ladder-median proxy ATM) | mean **6.6**, median **3**, min 1, max 48 |
| Measured scope symbols (proxy ATM) | **9 087** (17.4 % of the theoretical maximum) |
| Theoretical maximum (41 strikes × 2 legs × 689 cells, capped by ladder size) | **52 250** — reconciled below |

**The maximum, reconciled exactly (V18).**

| | Value |
|---|---|
| Cells in scope | **689** |
| Window | **41 strikes** (ATM + 20 below + 20 above) |
| Legs per strike | **2** (CE + PE) |
| **Naive product** — 689 × 41 × 2 | **56 498** |
| **Measured cap** — Σ over cells of 2 × min(41, distinct strikes in the cell) | **52 250** |
| **Difference** | **4 248** = 2 × Σ max(0, 41 − ladder strikes) over the **191 short cells** |
| **Eligible cells** (ladder ≥ 41 — can hold a full 41-strike window) | **498** — 469 NSE F&O stock · 21 NSE index · 8 SENSEX → **40 836** symbols |
| **Short cells** (ladder < 41 — the window is truncated by the cell's own ladder) | **191** — 166 NSE F&O stock (ladders 16–40) · 14 NSE index (30–39) · 11 SENSEX (13–18) → **11 414** symbols |
| One-leg strikes in the ladders (CE or PE absent) | **104** — a window containing one of them contributes one symbol fewer |
| **Refined bound** | **52 146 ≤ true maximum ≤ 52 250** |

**The formula.** `max_symbols = Σ over the 689 cells of 2 × min(41, |distinct strikes in the cell|)`, less the one-leg strikes falling inside the chosen window.

**No cell is excluded from the scope** — all **689** are required, and all 689 get a chain request. What differs is a cell's **capacity to fill a 41-strike window**: a cell's listed ladder must contain at least **41** distinct strikes. **191 cells cannot**, because their ladder is shorter than the window, so their ±20 window is truncated at the ladder edge. They are the **shallow weekly ladders of the NSE F&O stock underlyings** (166 cells, ladders 16–40), the **shallower NSE index expiries** (14 cells, 30–39) and the **SENSEX weekly expiries** (11 cells, 13–18). That last group is also why the ±20 proxy window caught only **1 strike** in each SENSEX cell above: a 13–18-strike ladder cannot present a 41-strike window at all.

**The 9 087 figure stays a proxy.** It is the ±20 window measured against the **ladder-median** ATM, which is not the money strike — the median of a lumpy ladder is nowhere near the ATM (SENSEX 2026-11-26: a 194-strike ladder spanning 65 000–84 300, median 74 700 → **1** strike caught). The true scope lies between **9 087** (proxy) and **52 250** (theoretical maximum) and is **not known until live prices and the true ATM windows are verified** (probes P-1…P-3, §3.16.9). **The request budget depends on neither number:** it is one request per cell.

The proxy is visibly wrong in the **19 SENSEX cells**, which have no FYERS
rows and whose Upstox ladder is lumpy — e.g. SENSEX 2026-11-26 has a
**194-strike ladder spanning 65 000–84 300** whose median (74 700) is far from
the money, so the proxy window captures **1 strike** instead of ~41.

**This does not affect the request budget.** The budget is **per cell, not per
symbol**: one chain request per (underlying, expiry), whatever the window
contains. So:

* the **one-request-per-cell** rule and the **0 depth / 0 quote** result are
  **exact**; the **689-request-per-sweep total has been withdrawn** (V21) —
  the cells are fetched **on demand**, so there is no per-cycle sweep to count;
* the **scope size** (9 087 symbols) is a **lower bound measured against a
  proxy**, and the true figure is between 9 087 and 52 250, to be measured
  with the underlying price (see §3.16.9).

**The architecture answers the ATM problem with two phases and no extra
request.** A chain response carries the underlying's price alongside the
ladder (FYERS `underlying_value`, Upstox `underlying_price` — and DhanHQ's
`data.last_price`, recorded for completeness although the provider is **excluded**
(D-22)). The implementation therefore **fetches the cell first,
reads the underlying price from that same response, computes the true ATM, and
then selects the ±20 window from the ladder it already holds.** No second
request, and no dependence on a proxy.

#### 3.16.4 The demand-driven operating model (V21 — the background sweep is withdrawn)

**The 670-cell FYERS + 19-cell Upstox background chain sweep is withdrawn.** It
was a **plan proposal** (V16–V20), never an implementation (§3.18.1), and it
contradicted the maintainer's requirement that the application *"show what we
need to see"* rather than continuously synchronise the universe (**D-26**).

| Tier | What | Transport | REST requests | Freshness |
|---|---|---|---|---|
| **T0 — focused streaming** | the **visible** chain's ±20 window + the desired set, live | provider **WebSocket** | **0 REST** — a tick is not a request | tick-level for what is on screen |
| **T1 — on-demand chain fetch** | the cell the user opens | REST chain | **1 per opened cell** | immediate, on request |
| **T2 — visible-window maintenance** | subscribe / unsubscribe as the user changes underlying or expiry | WebSocket | **0 REST** (subscribe and unsubscribe are not REST calls) | continuous while viewed |
| **T3 — narrowly scoped refresh** | the **active** cell only, when the live feed cannot supply a required field | REST chain | **1 per refresh cycle, active cell only** | bounded, and visible to the user |
| **T4 — index fallback** | index quotes, only when the primary feed is down | REST quotes | **0** normal; **77/session** in failover (§3.17.7) | per the V20 lifecycle |
| **— closed session** | nothing | — | **0** | the last snapshot is retained, labelled with its timestamp and session |

**The catalog's size is not the request count.** 689 cells exist; the target
design makes **one** request for the cell a user actually opens, and **none**
for the rest.

#### 3.16.5 The request budget under the demand-driven model (V21)

**The per-sweep budget is withdrawn with the sweep it priced.** What remains is
a budget of **one request per user action**, plus explicitly justified recovery.

| Consumer | Normal (idle) | A chain is open | Provider outage | Market closed |
|---|---|---|---|---|
| **Instrument-master / expiry sync** | **0** in-cycle; a **bounded** sync at startup, expiry rollover or catalogue change | 0 | 0 | 0 |
| **Futures Heatmap** | **0 REST** — it reads the **quote store**; the quotes arrive on the WebSocket | 0 REST | 0 REST | 0 REST |
| **Chain fetch — opened cell** (or alert-registered cell) | 0 | **1 per cell**, shared with the alert path and **single-flighted** (§3.19.4) — opening a cell an alert already maintains costs **0** | **1**, from the alternate provider **where one exists** (§3.19.7) | **0** — the cached snapshot is shown |
| **Visible-window subscription change** | 0 | **0 REST** — a subscribe/unsubscribe frame is not a REST request | 0 REST | 0 |
| **Reconnect / stale-cache recovery** | 0 | **1** — re-fetch **only** the active cell before restoring its live window | 1 | 0 |
| **Narrowly scoped refresh** (live feed cannot supply a field) | 0 | **1 per refresh cycle, active cell only** | 1 | 0 |
| **Analytics chain scheduler** (alert-registered chains) | **1 per registered chain per 60 s**, and **0 outside the session** (W-29) | same | same | 0 |
| **Index fallback** | **0** | **0** | **77/session**; **≤ 770** under a sustained provider failure (§3.17.7) | **0** |
| **Total REST in the steady state** | **0** | **≈ 1 per user action** | **≈ 1 per action, plus the fallback** | **0** |

**What this replaces.** The withdrawn design spent **689 requests per sweep**
(**670** FYERS + **19** Upstox) to keep the whole surface "fresh" whether or
not anyone was looking at it. The target design spends **0** when idle and
**≈ 1** when the user opens something. **The FYERS Standard 5 000/day pool is
no longer the binding constraint on this surface at all** — which means the
earlier arithmetic about how many sweeps fit in a session (§3.16.5a) describes a
design that no longer exists, and is retained only as the **re-entry cost** if
the deep-ladder tail is ever brought back into scope (§3.12.4).

**What is still unmeasured, and is not claimed otherwise:** the FYERS
**history** and **retry** consumers, the other shared-pool consumers, the
**Upstox market-data quota** **[UPX-7]**, and the real runtime behaviour of the
demand-driven path. **No quota, SLA or budget guarantee is offered** — the
figures above are *request counts by design*, not verified throughput.

#### 3.16.5a Session-scoped behaviour — what happens when the market is shut

**Outside the applicable exchange session, MarketHub stops refreshing live
market data.** The last snapshot is **retained** and labelled with its
**timestamp and the session it belongs to**, so the UI shows the previous close
rather than implying a live price.

| Consumer | In session | Out of session |
|---|---|---|
| Chain fetch (opened cell) | 1 per opened cell | **0** — the cached snapshot is served, labelled |
| Narrowly scoped refresh | 1 per cycle, active cell | **0** |
| Analytics chain scheduler | 1 per registered chain per 60 s | **0** — a session gate is added (W-29) |
| Index fallback | per the V20 lifecycle | **0** |
| Heatmap | quote store | quote store (unchanged) |

**The session is provider-driven and exchange-specific** (§3.17.3, §3.17.6):
`/v2/market/timings/{date}` **[UPX-9]**, `/v2/market/holidays` **[UPX-10]**,
`/v2/market/status/{exchange}` **[UPX-8]** — with holidays and **special
sessions** honoured, and **no hard-coded 15:30** and **no single window for
NSE and BSE**. The **7 NSE + 2 BSE** symbols the index fallback covers need
**two** windows.

**A snapshot's session identity is part of the data, not a comment.** Each
cached surface carries: its **provider**, the **canonical underlying and
expiry**, the **session date**, and its **last-update timestamp** — which is
what lets the UI distinguish *live* from *previous close* without a second
request, and what the freshness rules in `market/quote_coordinator.py`
(`received_ts` age against `stale_after_seconds`) already consume.

#### 3.16.6 Provider capability matrix — documented capability vs runtime-verified coverage

| | **Upstox `/v2/option/chain`** | **FYERS `/data/options-chain-v3`** | **FYERS `/data/market-depth`** | **DhanHQ `/v2/optionchain`** |
|---|---|---|---|---|
| **Addressed by** | one underlying + `expiry_date` | one underlying + expiry timestamp | **one symbol** | one underlying + expiry |
| **Strikes returned** | Upstox's whole listed ladder (documents no cap) | **±50 per side** of FYERS's ATM (`strikecount` ≤ 50) | n/a — single symbol | **all strikes** |
| **Price / volume / bid-ask** | yes | yes | yes (5 levels) | yes (top bid/ask + qty) |
| **Current OI** | yes | yes | yes (`oi`, conditional on `oiflag`) | yes (`oi`) |
| **Previous OI** | yes | yes | yes (`pdoi`, conditional on `oiflag`) | yes (`previous_oi`) |
| **IV** | yes | yes | **no** | yes (`implied_volatility`) |
| **Greeks** | yes (delta/gamma/theta/vega/rho) | yes | **no** | yes (delta/gamma/theta/vega) |
| **Authentication** | access token | access token + app key | same | **JWT `access-token` + `client-id`**, 24 h validity |
| **Extra entitlement** | market-data feed | data plan (Standard / Prime) | data plan, **shared pool** | **Data API subscription** (`dataPlan`, `dataValidity`) |
| **Throttle** | *assumed* 2 000/30 min, 500 rpm | **50 rpm, 5 000/day** | **50 rpm, shared pool** | **1 unique request / 3 s** |
| **Exchanges** | NSE, BSE | NSE (BSE derivatives documented **[FY-7]**) | same | **NSE, BSE, MCX** — *documented, but the provider is excluded (D-22)* |
| **Runtime-verified in this environment** | **NO — no credentials** | **NO — no credentials** | **NO** | **NO — and EXCLUDED (D-22); no credential sought, no probe, no request, no integration, no order routing** |

**The only capability difference that matters for the required surface is the
strike cap.** All three live endpoints carry the seven required fields for the
symbols they return, and the fourth is **excluded** (D-22). FYERS's chain is the only one with a **documented** strike cap (±50),
which is why the FYERS chain is the primary source under D-21's ±20 window;
Upstox's chain is uncapped in practice. (DhanHQ's chain is documented as
uncapped, but the provider is **excluded** — D-22 — so it is not an option.) Depth is
the odd one out: it is the **only** endpoint that serves a single symbol, and
the **only** one that carries **no IV and no Greeks**.

#### 3.16.7 DhanHQ — evaluated, and then **EXCLUDED** (D-22)

**The status is now exclusion, not merely non-adoption.** The maintainer
decided on **2026-10-21** (D-22): **DhanHQ is excluded — do not pursue
credentials, probes, or integration.** Accordingly: **no credential was
sought, no request was made, no integration was started, and no order is or
would be routed through it.** The evaluation below is retained **as the record
of what was assessed and why the provider was excluded** — it is not a
workstream.

| Question | Documented answer (read 2026-10-21) |
|---|---|
| **Authentication** | A **JWT access token** in the `access-token` header plus `client-id`. Individual traders get a token from `web.dhan.co` with **24-hour** validity, or generate one via `auth.dhan.co/app/generateAccessToken` with PIN + **TOTP**, or via the 3-step OAuth consent flow (app id/secret → browser login → consume consent). `POST /v2/RenewToken` extends a token another 24 h **only for web-generated tokens**. |
| **Account / permission** | Trading APIs are free, but **"for Data APIs, there are additional charges"** — a **Data API subscription** is required (`/v2/profile` exposes `dataPlan` and `dataValidity`). The account's `activeSegment` must include the relevant segment (e.g. Derivative). **Static-IP whitelisting is mandatory for order placement but explicitly not required for data fetching.** |
| **Identifier mapping** | Identifiers are **integer security IDs**, not exchange symbols. The chain is addressed by the **underlying's** `UnderlyingScrip` + `UnderlyingSeg` + `Expiry`; each returned leg carries its own `security_id`. The master (`api-scrip-master.csv` / `-detailed.csv`, or `GET /v2/instrument/{exchangeSegment}`) provides `EXCH_ID`, `SEGMENT`, `UNDERLYING_SECURITY_ID`, `UNDERLYING_SYMBOL`, `SM_EXPIRY_DATE`, `STRIKE_PRICE`, `OPTION_TYPE`. **The exchange trading symbol column is documented as removed** — so a mapping to MarketHub's canonical identity would have to be built on a **six-field composite**, not on a symbol string. |
| **Request throttling** | The option chain is limited to **one unique request every 3 seconds** — documented with the reason: *"OI data gets updated slow, compared to LTP or other data parameter."* That is ≈ 20/min and ≈ 1 200/h. **No daily cap is documented**; the dedicated rate-limits page returns **404**, so any daily ceiling is **not established**. |
| **NSE / BSE coverage** | The endpoint is documented as covering *"NSE, BSE and MCX traded options"*, and the master carries `EXCH_ID` ∈ {NSE, BSE, MCX} with `SEGMENT` = D for derivatives. The `UnderlyingSeg` enum is defined in the Annexure's exchange-segment table; the documented example is `IDX_I`. |
| **Why it would not have helped** | On the required surface it adds **nothing**: its chain fields are a subset of what the two configured providers' chain endpoints already supply, and the ±20 scope is served by a single chain response per cell regardless of provider. It would also have introduced a **third identifier form** (integer security IDs with a six-field composite master) into a broker-neutral architecture whose purpose is one canonical identity, plus a **paid Data API entitlement** and a **24-hour token lifecycle** — for no coverage gain. |
| **Status** | **EXCLUDED (D-22).** Documented capability only; nothing runtime-verified, nothing pursued. |

#### 3.16.8 Canonical-key reconciliation, and the catalogue compared with what a chain returns

**Three identifier forms, one canonical identity.** MarketHub already carries
two, and the reconciliation is the reason the canonical identity module exists
(`app/canonical_identity.py`):

| Provider | Native key form | Example |
|---|---|---|
| Upstox | `SEGMENT` + separator + `instrument_token` — opaque, token-based | `BSE_FO` `1100720` |
| FYERS | a **composed symbol string** (plus a separate numeric token) | `NSE:PAGEIND26OCT44000PE` |
| DhanHQ (**excluded**, D-22) | a **bare integer security ID** + a six-field master | `42528` |

A cell's identity is therefore **(exchange, canonical underlying, expiry)**, and
the **provider key is derived from the cell + strike + option type** — the key
is never the identity. That is what lets the ±20 window be selected from a
single chain response without knowing the provider's key format in advance.

**What the catalogue says, compared with what a chain would return.** Measured
offline against the catalogue (the runtime comparison is §3.16.9):

| Finding | Count | Detail |
|---|---|---|
| **Strikes listed with only one leg** in the full ladder | **104** | e.g. CANBK 107.5 PE, IEX 102.5 PE / 127.5 CE, INOXWIND 62.5 PE / 87.5 PE, IOC 117.5 PE, NBCC 72.5 PE, VMM 87.5 PE / 92.5 PE / 112.5 CE |
| …of which fall **inside the ±20 window** | **13** | the 7 above plus NIFTY 2027-12-28, 2028-12-26 and 2029-12-24 — each a **PE-only ATM strike** |
| **Cells with no FYERS rows at all** | **19** | all of them **SENSEX** — the catalogue has **0** FYERS `BSE_FO` rows (§3.15) |
| **Underlyings whose expiry sets differ between providers** | **1** | **SENSEX** — its 19 expiries exist in the Upstox master only |
| **Scope symbols FYERS lists and Upstox does not** | **994** | the reason 670 cells need the FYERS chain; Upstox lists **0** that FYERS does not |
| **Scope symbols listed by neither** | **0** | every in-scope symbol has a chain source |

**The 13 one-leg strikes are the catalogue's own asymmetry, not a provider
gap**: a chain response returns *both* legs for the strikes it carries, and
Upstox's own master is a strict subset of FYERS's for the full ladder. What
the comparison establishes is that **the catalogue and the chain responses can
disagree at the leg level**, so the implementation must reconcile
**per (cell, strike, option type)** and report a missing leg as a
*provider-missing* state rather than assuming the catalogue is right.

#### 3.16.9 The runtime comparison is **PENDING** — no credentials exist here

**No Upstox or FYERS credentials exist in this environment**: no credential
environment variables, and no credential store holding broker slots. **DhanHQ
is excluded (D-22) and was not looked for.** **No runtime probe was performed,
and no live market-data request was made.** The probes below are therefore
specified **pending** authorisation and credentials. They are the **required
runtime validation** for the confirmed scope (D-23) — nothing here reopens a
decision.

**The probes (Upstox and FYERS only — read-only market data, no orders)**

| # | Probe | What it establishes | Pass condition |
|---|---|---|---|
| P-1 | **Upstox** `/v2/option/chain` for **3 cells** — NIFTY nearest expiry, a stock's weekly expiry, and **SENSEX** nearest expiry | the **exact returned option keys**; whether the response carries the underlying price; the **true ATM**; the real scope size | the returned key set matches the catalogue for that cell, and `underlying_price` is present |
| P-2 | **FYERS** `/data/options-chain-v3` for the same 3 cells | the same, plus whether the ±50 window is centred on the true ATM | key set matches; `underlying_value` present; the **±20** window lies inside the returned window |
| P-3 | **both** providers, **all 689 cells** — one request each | the **complete** returned-key set vs the catalogue: missing legs, extra legs, missing/extra **expiries**, and the **SENSEX** cells | every catalogue symbol for the cell appears in the response; every response symbol appears in the catalogue; discrepancies are classified, not silently dropped |
| P-4 | the same cells, **one symbol each** from the deep-wing set | whether the chains really return nothing beyond ±50 (the finding that closed the required-surface gap) | the response is empty or documented as truncated — **the finding is recorded either way**; this now concerns only the **out-of-scope** tail (D-25) |
| P-5 | a **1,000-call budget probe** | the real Upstox market-data rate bucket **[UPX-7]** | the observed throttle is recorded; the **assumed** bucket is replaced by a measured one, or confirmed as un-measurable |

**Until P-1…P-3 run, the following remain unverified and are labelled so: the
scope size (9 087 is a proxy-measured lower bound against a theoretical maximum
of 52 250), the true ATM, the per-leg reconciliation, and every claim about
what a chain actually returns.** The **request budget** does not depend on any
of them — it is one request per cell either way — and the **≈ 3.4-hour average
cycle** does not depend on them either: it is a **theoretical maximum** derived
from the pool arithmetic, not a promise about runtime behaviour.

**The current-OI probe (`/v2/market/oi`) is no longer part of the required
validation.** It was scoped to the deep wings, which D-25 puts **out of scope**;
it is retained only as a re-entry condition should the tail return.

### 3.17 The index poller — measured scheduler behaviour (V19)

The plan had carried the poller's cost as **≈ 288 calls/day** since V10. That
figure was **never traced to the code**. It is wrong, and this section replaces
it with the measured behaviour.

#### 3.17.1 The implementation, traced

| Element | Measured value | Where |
|---|---|---|
| Module | `app/fyers_index_source.py` — `FyersIndexSource` + `run_fyers_index_poll_loop` | untracked working-tree module |
| Owner / lifecycle | started **unconditionally** in the app lifespan (`await _start_fyers_index_poller()`), alongside the previous-OI recorder — *"never constructed-and-idle"* | `app/server.py` |
| Role | **FYERS-only fallback** for index quotes when Upstox is unavailable — but started **regardless of Upstox's health** | `app/server.py`, module docstring |
| Endpoint | `GET {_DATA_HOST}/quotes?symbols=a,b,…` — **one request for all due symbols** (≤ 50 symbols/request **[FY-2]**) | `brokers/fyers/rest.py` `quotes()` |
| Symbols | **9** — `default_index_symbols(_MAJOR_INDICES)`: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI, INDIA VIX, SENSEX, BANKEX | `app/market_indices.py` |
| Freshness gate | **300 s** per symbol (`FRESHNESS_SECONDS`); all due symbols are marked together, so they stay in lockstep | `should_fetch()` / `mark_fetched()` |
| Loop cadence | **30 s** wake (`interval_seconds=30.0`) | `app/server.py` |
| Rate bucket | ≤ **5 rps** shared sliding window | `RateBucket` |
| `_interval` = 60 s | **stored and reported in diagnostics but never used as a guard** — the 300 s freshness gate is what actually bounds the fetch | `__init__` / `snapshot_state()` |
| Session gate | `is_market_hours()` — a **hard-coded** **09:15–15:30 IST** window, clock-only | `_MARKET_OPEN_IST` / `_MARKET_CLOSE_IST` |

#### 3.17.2 The measured request consumption: **75 per gated day**, not 288

Because all due symbols are marked at the same moment, the source makes **one
request every 300 s** for the whole set. The gated window is 375 minutes
(09:15–15:30 IST), so:

```
requests per gated day  =  375 min ÷ 5 min  =  75
```

**The plan's 288 was wrong on both counts.** It assumed (a) **one request per
symbol** — the implementation batches every due symbol into a single quotes
call — and (b) **24-hour operation** — the poller has been clock-gated to the
session window all along. The correct figure is **75**, which is **3.8× less**
than the plan assumed and frees **213 requests/day** of the shared pool.

#### 3.17.3 Reconciliation with D-26 — what is right, what is not

**Already right:** the poller does **not** poll overnight — the clock gate
stops it at 15:30 IST, and it publishes nothing outside that window. The
duplicate-request suppression and the rate bucket are real.

**Not right, and each is a concrete defect against D-26** (*"no unnecessary
live-price polling when the relevant exchange is closed"*):

| # | Finding | Consequence |
|---|---|---|
| **1** | **The gate is clock-only — no exchange-status, holiday or special-session awareness.** It never consults `/v2/market/status/{exchange}` **[UPX-8]**, `/v2/market/timings/{date}` **[UPX-9]** or `/v2/market/holidays`** **[UPX-10]** | **75 requests on every Saturday, every Sunday and every exchange holiday** — for values that cannot change. **150 requests/week (28.6 %)** are spent on non-trading days; ≈ **8 550/year (31 %)** on the current gate |
| **2** | **The hard-coded close is 15:30; NSE equity derivatives now close at 15:40** (w.e.f. 2026-08-03) **[NSE-1]** | the poller **stops 10 minutes before the F&O session ends**, so the final index values are pre-close |
| **3** | **No per-exchange distinction** — one IST window is applied to the NSE and the BSE symbols alike | the BSE window is assumed to equal NSE's, although both are **provider-supplied per date** and can differ |
| **4** | **The fallback runs even when the primary is healthy** — it is started unconditionally, not on Upstox failure | the FYERS pool is spent on a *fallback* while the Upstox WebSocket is the live index source |
| **5** | **A failed request is retried on the next 30 s wake with no backoff** — the symbols are not marked fetched, so they stay due | a persistent provider failure burns **up to 2 requests/minute (1 200/hour)** — roughly **24 % of the entire daily pool in one hour** |
| **6** | **The 60 s interval guard is inert** (stored, never enforced) | the real bound is the 300 s freshness gate; the documented "≥ 60 s between polls" is true only as a consequence of the other two bounds |

**Is there a concrete reason the poller must run outside market hours? — No.**
For live prices, the exchange is the only source of change, and while it is
closed the last value *is* the session close; re-fetching it cannot change
anything. The out-of-session work that genuinely exists — the **instrument
master / expiry roll** and the **previous-session OI snapshot** — belongs to
the **catalogue sync** and the **previous-OI recorder**, neither of which is
this poller (`PreviousOiRecorder` writes to the local event store and makes
**no provider calls**). The only legitimate post-close action is **one final
snapshot**, and the last in-session poll already provides it. **So the
concrete reason for out-of-hours polling is: none.**

#### 3.17.4 The corrected FYERS daily budget, by cost type

| Cost type | Per gated day (today) | Per trading day (D-26 applied) |
|---|---|---|
| **Chain sweep** (T2, one request per cell) | **670** per sweep | **670** per sweep |
| **Index poller** — *measured* | **75** | **75** |
| Poller outside the session | **75/day on every weekend and holiday** | **0** |
| **History** (`/data/history`, user-driven on demand) | **not measured** | not measured |
| **Retries** | **not measured**; worst case **2/min** on a persistent failure (§3.17.3 #5) | same |
| **Standard daily pool** | **5 000** | **5 000** |
| **Left for sweeps** | **5 000** ⇒ **≈ 7.46 sweeps/day** (normal) | **4 923** in failover · **4 230** under a sustained provider failure — §3.17.7 |
| **Session interval at 7.35 sweeps** | **≈ 52 min** (385 ÷ 7.35) | **≈ 52 min** |
| Whole pool on a **non-trading day** | spent on a poller that cannot see a change | **0** — no sweep, no poll, no request |

**What this changes.** The poller was never the problem the plan made it out
to be: at **75/day** it is **1.5 %** of the FYERS Standard pool, not 5.8 %.
The plan's own arithmetic was over-conservative, and the real defects are the
**six in §3.17.3** — especially the **weekend/holiday polling** and the
**retry storm**, both of which are *scheduling* faults rather than capacity
faults.

#### 3.17.5 Upstox's 21 requests per cycle — reconciled

| # | What | Tier | Basis |
|---|---|---|---|
| **19** | **Chain requests for the 19 SENSEX cells** — the only cells Upstox serves alone; the other 670 are served by the FYERS chain | **T2 background sweep** | measured from the 689-cell inventory (§3.15) |
| **2** | `GET /v3/market-quote/quotes` for the **659 desired contracts** (220 spots + 439 nearest-two futures) at **≤ 500 instrument keys per request** **[UPX-2]** — 659 ÷ 500 = 1.32 ⇒ **2** | **T1 / fallback**, *not* the background sweep | proposed REST path |

**Two qualifications.** First, the **2 quote calls are not an implemented path
today** — the desired set is carried by the **WebSocket**, and
`app/market_data.py` exposes no quotes call site; they are the **proposed**
REST fallback/analytics path, they belong to **T1**, and they must **not** be
double-counted against the streamed surface. Second, **the count 21 is right
but it spans two tiers**: 19 (T2) + 2 (T1). **The Upstox market-data quota
remains unverified** **[UPX-7]** — the documented bucket names only holdings,
positions, funds and historical candles — so neither figure may be presented
as a provider guarantee.


#### 3.17.6 The lifecycle: FYERS is a fallback, not an always-on duplicate

**What the audit found.** The FYERS index path is **not** a fallback in the
scheduler's sense. `app/server.py` constructs `_FyersIndexSource` and calls
`await _start_fyers_index_poller()` **unconditionally** at startup, and
**nothing in the poller consults the Upstox feed's state** — it runs in
parallel with the primary, not after it. Meanwhile the *intended* fallback
semantics live in the **read path**: `app/market_indices.py` resolves a FYERS
candidate when Upstox has no quote, so that an index is *"stale" rather than
'stale' rather than 'unavailable'"*. **So the design intent is fallback; the
scheduler implements a duplicate.**

**The health vocabulary already exists and must be reused, not invented.**
`brokers/upstox/feed.py` `status()` publishes: `state`
(`stopped` · `connecting` · `authorizing` · `reconnecting` · `streaming` ·
`failed` · `auth_required`), `state_updated_at`, `last_connected_at`,
**`last_message_at`**, `last_error`, `last_exit_reason`, `reconnect_count`,
`connect_attempts`, `not_ready_reason` and `recent_transitions`;
`subscribed_instruments` is `None` unless the state is `streaming`.

**The specified lifecycle.**

| State | Condition | FYERS index requests |
|---|---|---|
| **PRIMARY_HEALTHY** *(default)* | the primary feed is healthy | **0** |
| **PRIMARY_UNHEALTHY** | the primary is not delivering | **0** — the activation conditions are not yet met |
| **FALLBACK_ACTIVE** | the primary is judged dead **and** the symbol's own exchange session is open | 1 per 300 s per due-symbol set |
| **FALLBACK_COOLDOWN** | the primary recovered; the fallback is parked | **0** |

**Health predicate.** The primary is healthy **iff both** hold:
`state == "streaming"` **and** `now − last_message_at ≤ STALE_THRESHOLD`.

**Stale threshold = 600 s (10 minutes)** — twice the poller's own 300 s
freshness. The fallback must not start while the primary could still deliver a
value fresher than the fallback's own cache; and ten minutes of silence from a
feed that ticks every few seconds inside a session is a **fault**, not a quiet
market. This is deliberately **not** the display threshold:
`app/market_indices.py`'s `STALE_MINUTES = 5` answers *"is this quote fresh
enough to **show** as live?"* (a UI question, kept in parity with the WebUI's
`shell.js STALE_MIN`); the activation threshold answers *"is the primary dead
enough to be worth duplicating?"* (a fault question). **Conflating them would
arm the fallback on every quiet minute of the session.**

**Activation — all three conditions must hold:**
1. the **symbol's own exchange** session is open (§3.17.3);
2. the primary has been **continuously unhealthy for ≥ 60 s** (two 30 s wake
   cycles) — `unhealthy_since` is stamped on the first unhealthy evaluation;
3. the primary has **not** been continuously healthy for the last **15
   minutes** (the recovery hold-off).

**Deactivation.** The moment the primary is healthy again the fallback stops.
Its cached values stay in the store — they are the last-session values, which
is exactly the "stale rather than unavailable" behaviour the read path wants
— and the next primary message overwrites them.

**Anti-flap.** The two directions use **different time constants**: **60 s to
arm**, **15 min to re-arm after a recovery**. A feed flapping between
`streaming` and `reconnecting` therefore produces **at most one** fallback
start per 15-minute window, and the fallback never stops and restarts on every
transition. The 30 s wake gives the resolution; the 300 s freshness window
means the fallback's own data is never newer than the primary's last message
by more than one cycle.

**Session eligibility — exchange-specific and provider-timing-driven.** The 9
poller symbols split **7 NSE + 2 BSE** (`NSE:*` for NIFTY, BANKNIFTY, FINNIFTY,
MIDCPNIFTY, NIFTYNXT50, NIFTYFPI, INDIA VIX; `BSE:*` for SENSEX, BANKEX), so
**two** windows are needed, not one:

* each symbol's exchange window for **today** comes from
  `/v2/market/timings/{date}` **[UPX-9]** — which returns **NSE, NFO, BSE,
  BFO**, MCX, CDS and BCD — with the calendar and **special timings** from
  `/v2/market/holidays` **[UPX-10]** and the live state from
  `/v2/market/status/{exchange}` **[UPX-8]**;
* a symbol is eligible only when **its own** exchange is open: a BSE-only
  special session polls the **2 BSE** symbols and not the 7 NSE ones; an NSE
  holiday leaves the 7 NSE symbols idle while BSE continues;
* **no hard-coded 15:30** and **no single window** for NSE and BSE.

**What the fallback does *not* cover — stated so the gap is not hidden.** It
covers **index quotes only**. Under an Upstox outage the **19 SENSEX chain
cells have no source at all**, because the catalogue holds **0** FYERS
`BSE_FO` rows (M-13). The index fallback therefore **does not** close the
option-surface gap, and the SENSEX single-provider dependency (**M-13**) is
unchanged by this work.

#### 3.17.7 The corrected request accounting — normal operation and failover, separately

| Mode | FYERS index requests | Basis |
|---|---|---|
| **Normal operation** (primary healthy) | **0** | the fallback is idle by design (§3.17.6) |
| **Failover, provider healthy** (primary dead, session open) | **1 per 300 s** per due-symbol set ⇒ **≈ 77 per session** (385 min ÷ 5 min) | arithmetic |
| **Failover, provider failing** (continuous failure) | **1 per 30 s** ⇒ **120 per hour**, and **≈ 770 attempts across a 385-minute session** | arithmetic |
| Outside the session | **0** | no session ⇒ no request |
| Weekend / exchange holiday | **0** | the provider calendar closes the exchange |

**The two modes are alternatives, not a single blended number** — and the
failover cost is **additional to** the chain sweep, not instead of it. Against
the FYERS Standard **5 000/day** pool, **77** is 1.5 % and **770** is 15.4 %:
**stated as arithmetic only.** **No budget guarantee is offered here** —
history, retries, the other shared-pool consumers and the Upstox quota are
**unmeasured** (§4.1), the 770 case is a *failure* path whose duration is
unknown, and the pool is shared with every other consumer.

**What this does to the sweep arithmetic** (all three rows in §3.16.5):

| Mode | Pool left for sweeps | Sweeps/day | Interval inside the session |
|---|---|---|---|
| **Normal** | **5 000** | **≈ 7.46** | **≈ 52 min** |
| **Failover, provider healthy** | **4 923** | **≈ 7.35** | **≈ 52 min** |
| **Failover, provider failing** | **4 230** | **≈ 6.31** | **≈ 61 min** |

**And the change in the poller's own cost.** The current code spends **75/day
on an always-on duplicate** (§3.17.2). Under the specified lifecycle the
*normal* cost becomes **0**, and the **77/session** is spent **only when the
primary has actually failed** — the same order of magnitude, but paid only
when it buys something.


### 3.18 The demand-driven data architecture (V21 — audit, target model, lifecycle)

#### 3.18.1 What the code actually does (code-observed)

Every path below was read in the working tree on **2026-10-21**. **Nothing
here is inferred from the plan.**

| # | Path | Observed behaviour | Anchor |
|---|---|---|---|
| 1 | **Analytics chain scheduler** | A background loop refreshes **only the chains registered by enabled analytics alerts** — `self._dependents` — every **60 s** (`MIN` 30), concurrency **4**, per-chain locks, retry 5→60 s, `stale_after` **300 s** (an older snapshot is UNKNOWN and cannot trigger). Each refresh is **one REST chain request**. **There is no exchange-session gate.** | `app/market_analytics.py:10, 88-101, 196-237, 300` |
| 2 | **On-demand chain fetch** | `option_chain()` → `GET /v2/option/chain?instrument_key=…&expiry_date=…` — **one request per (underlying, expiry)**. The request returns the ladder *and* the underlying price. | `app/market_data.py:244-273` |
| 3 | **Futures Heatmap** | A **thin transport**: API → `FnoCoverageService` (routing) → `market.futures_board` → **the quote store**. *"Expiries are catalog-derived; **no per-underlying catalog/REST loops**."* **It fetches no option chain at all.** | `api/futures_heatmap.py:1-13` |
| 4 | **Heatmap caching** | `get_futures_board` is **TTL-cached with single-flight** (one build per key); every tile carries `received_ts`. | `market/futures_board.py:185, 306-355` |
| 5 | **WebSocket subscriptions** | `self._instrument_keys` is a **mutable tuple**; keys are added/removed, and — per the code's own comment — a change *"grows and the next authorize/resubscribe cycle picks it up"*, i.e. **a subscription change is applied at the next authorize/re-subscribe cycle**, not immediately. On reconnect the feed **re-subscribes the whole configured key set** with fresh authorization. | `brokers/upstox/feed.py:8, 194, 473, 484-519` |
| 6 | **Subscription owners** | Six owners; owner ⑥ (`set_derivatives_universe`) carries the **659** desired contracts; `MAX_INSTRUMENTS_PER_PROVIDER = 500`; the resolver **raises** `SubscriptionLimitError` when the union exceeds it. | `app/subscriptions/service.py:31, 157, 410, 587, 614, 996-1000, 1033` |
| 7 | **Quote cache and freshness** | Freshness is `received_ts` age vs `stale_after_seconds`; **stale-only instruments are UNAVAILABLE**, never served as live; `provider_of_key(instrument_token)` attributes a quote to its provider. | `market/quote_coordinator.py:8-9, 35, 54-124` |
| 8 | **Instrument master** | `sync_upstox()` / `sync_fyers()` fetch the static masters on demand; `catalog_epoch()` versions the catalogue. | `app/instruments.py:344, 363, 402` |
| 9 | **FYERS index poller** | The always-on duplicate of V19/V20. | `app/fyers_index_source.py` |
| 10 | **Other periodic loops — not market data** | retention prune (`core/runtime.py:226-239`), SSE broker (`core/sse_broker.py:121`), news and X/Twitter pollers (`core/persistence/modules/news.py:496`, `x_twitter.py:180` — explicitly out of scope), the FYERS socket **ping** (`brokers/fyers/feed.py:709-710`) and the Upstox recv loop. | — |

#### 3.18.2 Which paths cause full-surface or background REST polling — the answer

**No code path performs a full-universe option-chain sweep.** The **689-request
sweep (670 FYERS + 19 Upstox) existed only in this plan** (V16–V20) and was
never implemented. The tree contains **exactly two** recurring market-data REST
fetchers, and neither is universe-driven:

1. the **alert-driven** analytics chain scheduler — one request per
   *registered* chain per 60 s, and **no session gate**;
2. the **always-on** FYERS index poller — 75 per gated day (V19), now
   specified to be idle unless the primary fails (V20).

Everything else is on-demand (a user opening a chain), a static catalogue
fetch, or a **WebSocket** — and **a WebSocket tick is not a REST request**.

**So the plan was proposing a cost the code never incurred, and the real
full-surface risk in the tree is the *unbounded* alert-driven scheduler** — a
user with alerts on many underlyings multiplies one chain request per minute
per chain, with no session gate and no aggregate bound. That is the defect the
target model must bound.

#### 3.18.3 The target operating model

| Area | Target behaviour | How it maps onto the code |
|---|---|---|
| **A. Instrument universe** | The canonical catalogue and expiry metadata are maintained and **synchronised when needed** — startup, expiry rollover, catalogue change — **not** repeatedly during market operation. | `InstrumentCatalog.sync_upstox/sync_fyers` + `catalog_epoch` become an explicit, bounded, event-driven sync (W-29) |
| **B. Futures Heatmap** | The settled scope — required index spots and nearest-two futures across the F&O universe — served from the **configured providers' live subscriptions** during the relevant sessions, respecting entitlements and the shared subscription owners. **No option chain is fetched for the heatmap.** | Already the case: the heatmap reads the quote store. **V22 record:** the **alert-driven scheduler** was traced and bounded: chains are registered from **enabled analytics alerts** (`pcr_oi`, `pcr_volume`, `max_pain`, `iv_skew`) into a **set of alert ids per chain key**, so several alerts on one cell already share one request — but the registry has **no session gate, no aggregate cap**, and two key spellings (`analytics:<id>:<expiry>` and `<id>:<expiry>`) let **the same cell be fetched twice per tick**. The worst case was computed for **N** registered chains: **N** requests per minute, **385 N** per trading session — the FYERS Standard pool is exhausted at **N = 13**, and the catalogue ceiling would be **265 265** requests per session (≈ **53×** the pool). The design now normalises the registry key, gates the tick on the chain's **own** exchange window (date-specific, holidays, special sessions), **shares one cached chain per (underlying, expiry, provider)** between the visible path and the alert path, admits every request — analytics, visible refreshes, reconnect recovery, index polling, retries — through **one per-provider rate and daily-budget control**, and bounds retries with backoff **plus** admission; a deferred, stale or unavailable chain evaluates **UNKNOWN** and **cannot fire**. **V23 record:** V22's exposure arithmetic was **corrected**: the current scheduler has **no session gate**, so the exposure is **1 440 × N** requests per calendar day (not 385 N, which applies only after the gate) — **4 FYERS-routed chains already require 5 760/day against the 5 000/day Standard pool**, and **11 520/day** at the 30 s minimum. **Routing was traced and measured**: the scheduler names no provider, the **catalogue decides**, its read model returns one row per provider with `ORDER BY provider` — so **"fyers" wins** — and **all 220 in-scope underlyings resolve to the FYERS row**, which the registry confirms. The duplicate-key risk was reconciled too: the prefixed and unprefixed spellings both parse to the same canonical cell while remaining two registry entries, so the worst case is **2 × the distinct cells**; the target keeps **one entry per canonical cell**. What is added is the **session gate** on the underlying subscriptions and the entitlement check |
| **C. Option chain — on demand** | On opening an underlying + expiry: resolve the canonical identity and the provider-supported identifiers → fetch **that** chain → take the **underlying price from the same response** → compute the **true ATM** → display the **±20 window** bounded by the ladder → **cache with provider, expiry, session and last-update timestamp**. | `MarketDataService.option_chain` (already one request per cell) + the new chain cache (W-29) |
| **D. Keep the visible chain updated** | Subscribe the visible window's option contracts on the WebSocket and update its cache while it is viewed; on underlying/expiry change, **update and release** the no-longer-needed subscriptions safely; on **reconnect or a stale cache**, re-fetch **only the active cell** before restoring its live window. If the live feed cannot deliver a required field, use a **narrowly scoped refresh of the visible chain** — never a whole-universe job. | `brokers/upstox/feed.py` key add/remove + the subscribe-cycle latency; `market/quote_coordinator.py` for freshness; a scoped refresh path (W-29) |
| **E. Background and closed-session behaviour** | **Remove** the mandatory 670-cell + 19-cell background chain sweep. Outside the session, **stop live refreshes**, retain the last snapshot **labelled with its timestamp and session**, and let the UI show the previous close. Use provider-supplied status, timings and holiday/special-session data. The **index fallback stays separate** and runs only under the V20 conditions. | The sweep never existed to remove; what is added is the **session gate** on the alert-driven scheduler and the **snapshot labelling** (W-29) |

**Why the ±20 requirement is not weakened.** The window applies to **every
eligible cell when it is requested**. It never required the 689 cells to be
downloaded in advance, and it is not weakened by not downloading them: a cell
the user opens is served correctly, at the same ±20 width, with the same seven
fields.

#### 3.18.4 The lifecycle

```
   user opens underlying+expiry
            │
            ▼
   resolve canonical identity ──► provider-supported identifiers (provider-neutral)
            │
            ▼
   fetch THAT cell's chain ──► 1 REST request ──► underlying price from the SAME response
            │                                                 │
            ▼                                                 ▼
   compute TRUE ATM ──► select ±20 window (bounded by the ladder)   │
            │                                                         │
            ▼                                                         │
   cache: provider · canonical underlying · expiry · session · last-update ts
            │
            ▼
   subscribe the window's option contracts on the WS ──► 0 REST
            │
   ┌────────┴─────────┐
   │ underlying/expiry │──► unsubscribe the released keys, subscribe the new ones
   │ changes          │      (applied at the next authorize/re-subscribe cycle)
   └────────┬─────────┘
            │
   ┌────────┴─────────┐
   │ reconnect / stale │──► re-fetch ONLY the active cell (1 REST), then restore the window
   └────────┬─────────┘
            │
   ┌────────┴─────────┐
   │ exchange closed  │──► 0 REST; keep the snapshot, labelled with its session
   └──────────────────┘
```

**A missing field in the live window** takes the **narrowly scoped refresh**
path: one request for the **active cell only**, on a bounded cadence, with the
field state reported as *refreshed* rather than silently substituted by a
universe-wide job.

#### 3.18.5 Provider capability gaps and runtime evidence still required

| # | Gap | Status | Consequence |
|---|---|---|---|
| 1 | **The visible window is ~82 option keys** (41 strikes × CE/PE) against Upstox's `full` entitlement of **2 000** **[UPX-1]** — comfortable — but against the plan's recorded FYERS socket figure of **100 sockets/user** it would nearly exhaust the entitlement before the desired set is added | **Unverified for FYERS** — the figure is the plan's reading of the documented socket limits **[FY-6]** | The live window must be **sized per provider** (configuration-driven, provider-neutral): a provider whose entitlement cannot carry the window uses the **narrowly scoped refresh** path for the visible chain |
| 2 | **A subscription change is applied at the next authorize/re-subscribe cycle** (`brokers/upstox/feed.py:473`), not immediately | **Code-observed** | The UI must reflect the *pending* state, and the release of no-longer-needed keys is **deferred but guaranteed** — it is not a leak, it is a cycle boundary |
| 3 | **Does the market feed carry IV and Greeks for option contract keys?** | **Unverified** — the chain response does; the WS feed's field set for option keys is not established here | If it does not, the **narrowly scoped refresh** becomes **required** for the visible chain, not optional |
| 4 | **Under an Upstox outage the 19 SENSEX cells have no alternative source** (0 FYERS `BSE_FO` rows) | **M-13, unchanged** | The demand-driven design makes the gap **visible at the moment of use** instead of hiding it behind a sweep that would have failed anyway |
| 5 | **The alert-driven scheduler's aggregate bound** | **Not implemented** | It is the one real full-surface risk in the tree: it must be session-gated **and** given an explicit aggregate cap (W-29) |
| 6 | **The Upstox market-data quota**, and the real behaviour of the demand-driven path | **Unverified** — probes **P-1…P-5** are **PENDING** (no credentials) | No quota, throughput or latency claim is made anywhere in this plan |
| 7 | **Whether the providers' chain responses carry the underlying price** | **Documented** (Upstox `underlying_spot_price` **[UPX-3]**; FYERS `underlying_value` **[FY-4]**); **runtime unverified** | The true-ATM step depends on it — which is exactly what P-1/P-2 must confirm |

#### 3.18.6 Evidence separation

| Class | Content |
|---|---|
| **Code-observed** | Everything in §3.18.1 and §3.18.2 — read from the working tree on 2026-10-21, with file and line anchors |
| **Officially documented** | The provider endpoints and entitlements in §3.14, read 2026-10-20/21: Upstox chain **[UPX-3]**, feed limits **[UPX-1]**, market status **[UPX-8]**, timings **[UPX-9]**, holidays **[UPX-10]**; FYERS chain **[FY-4]**, quotes **[FY-2]**, socket limits **[FY-6]**; NSE session timings **[NSE-1]** |
| **Architectural recommendation** | §3.16.4, §3.16.5, §3.16.5a, §3.18.3, §3.18.4, §3.18.5 — the demand-driven model, the lifecycle and the budget, proposed by this plan and **not implemented** |
| **Unverified at runtime** | The probes **P-1…P-5**; the Upstox market-data quota **[UPX-7]**; the FYERS socket entitlement for an 82-key window; whether the live feed carries IV/Greeks for option keys; the real latency and throughput of the demand-driven path. **No live probe was performed and no result is invented.** |


### 3.19 The alert-driven refresh: current behaviour, bounds and budget (V22)

#### 3.19.1 Current behaviour (code-observed)

| Element | Observed | Anchor |
|---|---|---|
| **Registry** | `_dependents: dict[chain_key, set[alert_id]]` — a **set of alert ids per chain key**, so **several alerts on one cell share one key and therefore one request per tick** | `app/market_analytics.py:106, 137-140` |
| **How chains are registered** | `reconstruct_from_alerts(load_enabled)` runs at startup over the **enabled** alerts; each alert's condition tree is walked by `_extract_analytics_keys`, and every key found is registered with that alert's id | `:358-384, 386-407` |
| **Which metrics need a chain** | `pcr_oi`, `pcr_volume`, `max_pain`, `iv_skew` (condition v1); condition v2 recurses into child conditions | `:392, 404-406` |
| **Key format** | `analytics:<canonical_id>:<expiry>`; `parse_chain_key` strips the prefix **if present** and splits on the **LAST** `:` (a canonical id itself contains colons) | `:58-77` |
| **The duplicate hazard** | `_extract_analytics_keys` prefers `condition["_dependency_key"]` (which **is** prefixed) and otherwise builds `f"{canonical_id}:{expiry}"` — **unprefixed**. Both parse to the same `(canonical_id, expiry)`, but they are **different dictionary keys**, so **the same cell can occupy two registry entries and be fetched twice per tick** | `:395-403` vs `:69-71` |
| **Per-tick work** | `keys = list(self._dependents.keys())` → **one REST chain request per registered key**, concurrency 4, per-chain lock | `:210-237` |
| **Cadence** | `DEFAULT_REFRESH_INTERVAL_SECONDS = 60`, `MIN_REFRESH_INTERVAL_SECONDS = 30` | `:88, 98-99` |
| **Failure handling** | per-chain `_failure_count`, retry backoff `INITIAL_RETRY_DELAY = 5` → `MAX_RETRY_DELAY = 60`; a failed chain is retried on the next tick | `:50-52, 226-237` |
| **Staleness semantics** | a snapshot older than `stale_after_seconds` (**300 s** default) is **UNKNOWN** and **cannot cause a trigger** | `:16-18, 120-121` |
| **Routing of each chain request** | The scheduler calls `option_chain(instrument_key=…, exchange=…, tradingsymbol=…, expiry=…)` **without naming a provider**, so the **catalogue decides**: `ProviderMarketData._provider_for` asks the registry, which is *“the CATALOGUE is authoritative”*, and the read model `find_by_exact_symbol` returns **one row per provider** with `ORDER BY provider` — so **"fyers" sorts first and its row wins**. **Measured on the live catalogue: all 220 in-scope underlyings resolve to the FYERS row**, and `provider_for_key` confirms the key belongs to FYERS. **So the analytics chain requests are FYERS-routed** — but by an **alphabetical tie-break**, not by policy. | `app/market_data.py:244-260`; `core/persistence/modules/products.py` (`ORDER BY provider`); `app/instruments.py` `find_index`/`find_equity` (`rows[0]`) |
| **A routing consequence for SENSEX** | SENSEX resolves to the FYERS **index** row (`BSE:SENSEX-INDEX`), but FYERS lists **no BSE_FO instruments** — so an analytics chain request for SENSEX is routed to the provider that **cannot serve the chain**, and fails on every tick. | measured |
| **What is missing** | **no** exchange-session gate, **no** aggregate cap, **no** shared cache with the visible-chain path, and the registry key is **not normalised** | — |

**Two things this establishes.** The scheduler is **demand-driven** (alerts, not
the universe) — that part is right and the full-surface sweep stays withdrawn.
But it is **unbounded in the number of registered chains**, **ungated by the
trading session**, and it can **fetch the same cell twice** when the two key
spellings diverge.

#### 3.19.2 Current exposure versus target budget — corrected

**The correction.** V22 presented **385 N** as the exposure. That figure
applies **only after the session gate exists**. **Today** the scheduler has no
gate, so it refreshes every registered chain **every 60 seconds, all day**:
the current exposure is **1 440 × N** requests per calendar day, not 385 × N.

**The formulas (N = registered chains, each issued once per tick):**

| | Current (no session gate) | Target (session-gated) |
|---|---|---|
| Requests per **minute** | **N** | **N** while the session is open, **0** outside it |
| Requests per **trading session** (385 min) | **1 440 N** (the gate does not exist yet) | **385 N** |
| Requests per **calendar day**, trading day | **1 440 N** | **385 N** |
| Requests per **calendar day**, non-trading day | **1 440 N** | **0** |
| At the configured **30 s** minimum | **2 880 N** per day | **770 N** per session |

**Four FYERS-routed chains, at the 60 s interval:**

```
current   4 x 1 440 =  5 760 requests/day   >  5 000/day Standard pool   (over by 760, before any other consumer)
target    4 x   385 =  1 540 requests/session  <  5 000/day Standard pool  (≈ 3 460 left for everything else)
```

**At the configured 30 s minimum:** current **4 × 2 880 = 11 520 requests/day**
— **2.3× the pool** — against a target of **4 × 770 = 3 080 per session**.

**The pool is exceeded at N = 4, not at N = 13.** The earlier "N = 13" figure
came from dividing the pool by the *session* length; the scheduler currently
runs for the **whole day**, so the divisor is **1 440**.

**Routing, measured (§3.19.1):** all **220** in-scope underlyings resolve to
the **FYERS** row, and the registry confirms the key belongs to FYERS — so
**the analytics chain requests are FYERS-routed** and are charged to the FYERS
pool. **No Upstox request is charged to the FYERS pool, and none is assumed to
be**: the split is a *measured* property of the catalogue's read model, and it
would change if the ordering changed. The **Upstox** side is unaffected by
this path today, and its market-data quota remains **unverified** **[UPX-7]**.

**Retries, concurrency and the other consumers — measured where available,
otherwise explicitly unmeasured:**

| Item | Value | Status |
|---|---|---|
| **Attempts per tick** | **N** — a failed chain consumes the **same** per-tick slot, so a failure does **not** add a request; the retry **is** the next tick's attempt | **Measured** (`:210-237`) |
| **Retry backoff** | 5 s → 60 s, per-chain failure count retained | **Measured** (`:50-52`) |
| **Concurrency 4** | spreads the requests in **time**; it does **not** reduce the **count**. It is not a budget control | **Measured** (`:219-224`) |
| **Other FYERS pool consumers** (history, depth for other purposes) | **unmeasured** | **§4.1** |
| **Index poller** | **≈75 per gated day** today, **0** in the target's normal operation (§3.17.7) | **Measured** (§3.17.2) |
| **The Upstox market-data quota** | **unverified** — no aggregate is claimed | **[UPX-7]** |

**So the honest current figure is: 1 440 N requests per calendar day, of which
the *whole* FYERS Standard pool is at risk from N = 4 chains onward, before
retries (already inside that count), before the other consumers (unmeasured),
and before the index poller.**

#### 3.19.3 The session gate, and what a price-based alert does while the market is closed

**The gate is exchange-specific, date-specific and provider-supplied.** A chain
is refreshed only while **its own exchange** is open, using the provider's
timings for **that date** — `/v2/market/timings/{date}` **[UPX-9]**,
`/v2/market/holidays` **[UPX-10]** (holidays **and special timings**),
`/v2/market/status/{exchange}` **[UPX-8]**. An NSE cell is gated on the NSE
window, a BSE cell on the BSE window; a BSE-only special session leaves the
NSE chains idle and vice versa. **No hard-coded 15:30, and no single window
for NSE and BSE.**

**No routine live-price chain refresh happens outside the relevant session.**

**What price-based alerts do while the market is closed** — stated, not left to
inference, using the vocabulary the service already has:

| Alert kind | While the exchange is closed | At the next session open |
|---|---|---|
| **Live-move** (PCR crosses, IV skew breaks, max-pain moves) | The condition evaluates against the **last snapshot**, which is **stale** — therefore **UNKNOWN**, and it **cannot fire**. No trigger is fabricated from a closed-market price. | Re-armed; the first fresh snapshot resumes evaluation |
| **Session-close / derived** (a metric meaningful only at the close) | Evaluated **once** against the **labelled session-close snapshot** — the day's last refresh, carrying its **session** and timestamp — and then not again | Evaluated once per session, at the close |
| **Chain unavailable** (no provider lists the cell, or the provider is down) | **UNKNOWN**; the alert does **not** fire, and the reason is visible in diagnostics | Re-armed when a source returns |

**A deferred, stale or unavailable chain must never produce a trigger.** The
service already encodes that (`stale_after_seconds` → UNKNOWN → no trigger);
the design keeps it and makes the *cause* explicit: **deferred by budget**,
**stale by age**, and **unavailable by provider** are three different states,
and only the first two are recoverable by waiting.

#### 3.19.4 One cached chain per (canonical underlying, expiry, provider)

The **visible-chain** path and the **alert** path must read the **same** entry.

| Property | Design |
|---|---|
| **Key** | `(canonical underlying, expiry, provider)` — the provider is part of the key because the two providers list different contracts for the same cell |
| **Single-flight** | one in-flight request per key; a second reader of the same key waits for it rather than issuing a second request (the pattern `market/futures_board.get_futures_board` already uses) |
| **Contents** | the chain snapshot plus **provider**, **canonical underlying**, **expiry**, **session** and **last-update timestamp** |
| **Sharing** | a user opening a cell an alert already maintains issues **no** request; a visible refresh **updates the entry the alert reads**, so the alert's next evaluation uses the fresher data |
| **Derivation** | the analytics layer keeps only the **derived** values (PCR, max pain, IV skew) plus the chain's timestamp and session — not a second copy of the chain |
| **Duplicate-key reconciliation** | The registry is keyed by the **string**; the canonical cell is the **parsed** `(canonical_id, expiry)`. `_extract_analytics_keys` yields the **prefixed** `analytics:<id>:<expiry>` from `condition["_dependency_key"]` and the **unprefixed** `<id>:<expiry>` from the fallback. `parse_chain_key` strips the prefix **if present** and then splits on the last colon, so **both spellings parse to the same canonical cell** while remaining **two different registry entries** ⇒ **one cell, two entries, two fetches per tick**. One alert yields one spelling, so the two shapes can only coexist when **two alerts on the same cell were persisted in different shapes** (e.g. one written before the dependency key existed and one after) — and the registry is rebuilt from **persisted** alerts at startup, so both shapes meet in the same registry. The condition-v2 recursion is **safe**: the same key twice under the same alert id is collapsed by the `set`. **Worst case: registry entries = 2 × distinct canonical cells**, doubling the fetch count. **Target: one entry per canonical cell**, because the key is the normalised `(exchange, underlying, expiry)` — so the fetch count equals the cell count, whatever the persisted spelling was. |

#### 3.19.5 One shared per-provider budget, with admission

Every market-data request in the process — **analytics refreshes, visible-chain
refreshes, reconnect recovery, index polling and retries** — passes the **same**
per-provider admission control.

| Rule | Detail |
|---|---|
| **Rate admission** | a request is admitted only if the provider's per-minute budget allows it; otherwise it is **deferred** to the next slot, never dropped |
| **Daily admission** | a request is admitted only if the provider's **documented** daily pool has room; otherwise it is **deferred** and the deferral is reported |
| **Reservation** | a configurable share of the documented pool is reserved for the chain path, so history, depth and other consumers cannot starve it — and the **residue is reported**, not assumed |
| **FYERS** | the **Standard** (5 000/day, 50 rpm) and **Prime** (500 000/day, 50 rpm) pools are **documented** **[FY-1]** and may be used as admission limits |
| **Upstox** | the market-data bucket is **unverified** **[UPX-7]** ⇒ **no claimed aggregate quota** is invented. The Upstox side gets only a **configured local admission cap** (concurrency and a per-minute ceiling) that is explicitly **not** a quota claim, and the residue is reported as unknown |
| **Unknown shared consumption** | because the other consumers of the FYERS pool are **unmeasured** (§4.1), the admission limits are stated as **reservation shares of a documented pool**, not as a promise that the whole pool is available |

#### 3.19.6 Bounded retries, and the preservation of alert correctness

| Rule | Detail |
|---|---|
| **Backoff** | retained — 5 s → 60 s — and the **per-chain failure count** is kept |
| **Budget admission on retry** | a retry must also pass the admission control; a retry into a full pool is **deferred**, not spent |
| **Failure ceiling** | after a configured number of consecutive failures a chain is marked **unavailable** rather than retried indefinitely |
| **Correctness** | an **unavailable**, **deferred** or **stale** chain evaluates **UNKNOWN** and **cannot fire**; the alert is re-armed when a fresh snapshot arrives. **A trigger is never produced from data the system knows to be stale or absent** |
| **Visibility** | the state — fresh / stale / deferred / unavailable — is reported per chain in diagnostics, alongside the session and the last-update timestamp |

#### 3.19.7 Fallback availability is exchange- and provider-specific

An alternate source for a cell is determined by **the catalogue** (which
provider lists that cell's contracts) **and** the provider's own exchange
coverage — never by a general "there are two providers" assumption.

| Cell group | Alternate under an Upstox outage | Basis |
|---|---|---|
| The **670** NSE cells whose contracts both providers list | **FYERS** | the catalogue lists both |
| The **19** SENSEX cells | **NONE** — the catalogue holds **0** FYERS `BSE_FO` rows | **M-13**; FYERS BSE chain support is **not** verified |

**So SENSEX chains are `unavailable` under an Upstox outage, and the plan must
not promise otherwise** until FYERS BSE chain support is verified and the
`BSE_FO` master is synced. The demand-driven design makes the gap **visible at
the moment of use** instead of hiding it behind a sweep that would have failed
anyway.

#### 3.19.8 Evidence still required

| # | Gap | Status |
|---|---|---|
| 1 | The **date-specific** session windows, holidays and special timings, and whether the two providers agree on them | **Documented** for Upstox **[UPX-8] [UPX-9] [UPX-10]**; **not probed** here (P-1…P-5 **PENDING**, no credentials) |
| 2 | The **FYERS** session semantics — a FYERS-side timing/status equivalent of the Upstox endpoints is **not** documented here | **Unverified** — the gate is designed provider-agnostic, but the FYERS side needs its own source |
| 3 | The **Upstox market-data quota** | **Unverified** **[UPX-7]** — no aggregate is claimed |
| 4 | The other consumers of the FYERS pool (history, depth, retries) | **Unmeasured** (§4.1) — the admission limits are reservation shares, not guarantees |
| 5 | Whether the **live feed** carries IV and Greeks for option keys (§3.18.5 #3) | **Unverified** — decides whether the narrowly scoped refresh is optional or required |
| 6 | The real **latency and failure profile** of the demand-driven path | **Unverified** — no live probe was performed and none is invented |
| 7 | **Whether the analytics chain should route to FYERS at all.** The routing is a **catalogue read-model artefact** (`ORDER BY provider`, so "fyers" wins), not a policy; and for **SENSEX** it selects the provider that **cannot serve the chain** (no FYERS `BSE_FO` instruments) | **Measured** (§3.19.1) — the *decision* is **open**, and it belongs to the provider-neutral routing work, not to this plan's scope |
| 8 | Whether the **Upstox chain** would be a better destination for analytics chains | **Unverified** — the Upstox market-data quota is **unverified** **[UPX-7]**, so the comparison cannot be made on cost today |


---

## 4. Current-state and change-scope inventory — **UNVERIFIED RECONSTRUCTION, NOT A RESTORATION (V18)**

> **V18 recovery blocker — the V13 source could not be accessed, and no second
> reconstruction has been produced.**
>
> The instruction was to restore §4 **from the earlier V13 plan copy**. That
> copy **does not exist in this working environment**, and it was searched for
> exhaustively on 2026-10-21:
>
> | Where | Result |
> |---|---|
> | **Git object store** — `git fsck --no-reflogs --unreachable`: all **85** unreachable blobs, every unreachable commit and its trees | **No blob of the plan exists.** The file was **never staged and never committed** — it is untracked, so there is no object to restore from. The unreachable commits contain only the *other* `docs/plans/` files |
> | **All branches, remotes, stash, reflog, worktrees** | no copy; a single worktree; no stash entries |
> | **The index** (240 staged files) | the plan is not among them |
> | **Editor local history** (`AppData\Roaming\Code` / `Cursor\User\History`) | absent — no editor history directory exists |
> | **17 442 text files** across `D:\Temp`, the user `Temp`, `Roaming`, `Documents`, `Desktop`, `Downloads` | the only hits are the **live plan file** and **this session's own scratch scripts**, which contain the *heading* only — they used it as an insertion anchor and never held the body |
> | **This session's scratch scripts** (`v7`…`v18` under `%TEMP%\opencode`) | contain the heading string as an anchor; **no §4 body text** anywhere |
>
> **§4 has therefore not been restored, and per the instruction no second
> reconstruction was produced.** The body below is the **V17 reconstruction**,
> retained only because §3.12.4 and §12 item 14 cite §4.1 and §4.2, and a
> dangling reference would be worse than a labelled one. **It is not the V13
> text and must not be treated as recovered.** Every figure in it is either
> re-measured here (2026-10-21), carried from another section of this plan, or
> a statement of scope.
>
> **What closes this blocker:** an **external copy of the V13 plan file** — from
> the maintainer, a prior session's artefact store, or a backup outside this
> machine — **or** committing the plan to Git so a later version cannot be lost
> again. **The original wording is not recoverable from this repository.**

### 4.1 The current state, and what is measured versus unmeasured

**The working tree, re-measured 2026-10-21:**

| | Value |
|---|---|
| **HEAD** | `3f4b8d9` — *"fix(server): wire X/Twitter REST routes and MCP tools into production app"* |
| **Tracked changes** | **292** files, **+14 586 / −1 804** lines, over `app/ api/ market/ providers/ brokers/ core/ mcp_server/ test/ frontend/src/ docs/` |
| **Staged** | **240** files |
| **Untracked modules imported at module scope** | `app/canonical_identity.py` · `app/fyers_index_source.py` · `app/runtime_paths.py` · `brokers/fyers/rest.py` · `market/quote_coordinator.py` · `providers/capacity.py` |
| **Consequence** | a **clean checkout cannot import** those modules ⇒ **W-01** is a hard integration blocker, not a formality, and it gates every acceptance test |

**What is NOT measured — the honest list.** Every budget, interval or
freshness figure in this plan that is *not* re-measured against the catalogue
appears in this list, and each carries its label wherever it is used:

| Unmeasured input | Where it bites | Label used at the point of use |
|---|---|---|
| **The account tier** (FYERS Standard vs Prime) | the FYERS usable capacity (90 vs 4 500) and every pool arithmetic | **UNVERIFIED** — gate **G-T** |
| **The Upstox market-data rate bucket** | the Upstox side of every budget | **assumption [A]** **[UPX-7]** |
| **The other FYERS pool consumers** — history, depth for other purposes, retries | the residual of the **5 000/day** pool (less any failover poller), and therefore the **≈ 52-minute in-session cycle** | **theoretical maximum, NOT a freshness SLA** (§3.16.5) |
| **Actual runtime behaviour** — latency, throttling responses, partial responses, per-request failures | every "per sweep" and "per refresh" figure | **projected** |
| **What a chain actually returns** — the exact option keys, the underlying price, the true ATM | the scope **size** (9 087 symbols is a proxy-measured **lower bound** against a theoretical maximum of 52 250) | **PENDING** — probes **P-1…P-5** (§3.16.9) |
| **The per-leg reconciliation** against the catalogue | **13** one-leg strikes inside the ±20 window, **104** in the full ladder | **PENDING** (P-3) |

### 4.2 Change scope and attribution

**In scope for this plan** — the F&O subscription-capacity failure and the
option/data surface that follows from it: the capacity model (§3.5a–§3.6), the
shared union and capacity-aware routing (§3.13), the index-key source of
truth, the **cell-addressed chain surface** (§3.16), the field-level
completeness reporting, and the verification chain (§8, §10).

**Explicitly out of scope, with the reason** (mirrored in §9): **X/Twitter,
Yahoo, news MCP tools, the futures heatmap, the MCP registry, the non-F&O
hunks of `api/product_routes.py`, and any change to `app/auth/*`.** The only
reason any of them appear at all is **W-01** (separation) and **W-21**
(baseline attribution).

**Protected by ownership, not by this plan.** The **auth/startup foundation**
(`app/auth/*`, credential persistence, startup restoration, the
rejection/expiry lifecycle, the fake-token guards), the **quote-store write
path**, and the **canonical market models** are **read-only** for this work.
No work item in §7 edits them; `test/test_architecture_boundaries.py` and
`test/test_auth_freeze.py` enforce that mechanically. **Login must not
regress.**

**Deferred, deliberately.** **S15** stays deferred. The **option-key streaming
prohibition (D-13)** stays in force: W-24/W-25/W-26 add a REST-backed,
cached, budgeted chain surface and add **no** WebSocket option keys, change
**no** streamed surface, re-key **no** quote store, and alter **no**
authentication, credential or startup behaviour.

---

## 5. Root-cause register

Severity: **S1** = blocks release · **S2** = materially wrong behaviour ·
**S3** = correctness/robustness gap · **S4** = hygiene.

| ID | Sev | Finding | Evidence | Impact | Reproducible | Dependency | Coverage today |
|---|---|---|---|---|---|---|---|
| **R-01** | S1 | **Two incompatible capacity policies.** The planner allocates up to its budget (500) and reports `CAPACITY_LIMITED`; the resolver unions the plan with the base, active-view and analytics owners and then **raises** if the total exceeds 500. `app/fno_coverage.py:515-517` (budget check) with drop sites at `:527-529` (spot) and `:593-595` (futures) — vs `app/subscriptions/service.py:995-1000` (raise) | 500 on every F&O view; startup restore fails; the subscription service is **wedged until cleared** (R-04) | **Yes** — measured **516–556** vs 500 against the live catalogue (DB copy, real modules); **517** on the settled scope | Root cause of R-03 and R-04; a **co-cause** of R-02 under position (b) | **None** — the only cap test (`test_md_subscriptions.py:481`) uses a synthetic store; `test_fno_startup_restore.py:46` **stubs** the subscription service |
| **R-02** | **CONDITIONAL** (S1 if M-1 resolves to Scope-S2; **not a defect** if M-1 resolves to Scope-S1) | **Streaming scope — unresolved, not "divergent".** V6 asserted this as a *proven* violation, on the strength of R5 §120. V7's authority review (§2.4) found **no recorded maintainer amendment** of the "spot + nearest two futures" decision, and the as-built implementation conforms to it. The defect is therefore **conditional**: if the maintainer confirms Scope-S1, the implementation is **scope-correct** and the failure is R-01 alone; if the maintainer confirms Scope-S2, the planner is over-streaming and must be rescoped. | §2.4 authority table; `app/fno_coverage.py:405-482`; measured `plan.desired=662, routed=550, capacity_limited=112` | Under Scope-S2: the planner streams 441 futures that should be REST-on-demand. Under Scope-S1: none — but 112 contracts are still silently `CAPACITY_LIMITED` with no drop-priority reporting | **Yes** (the 662/550/112 measurement) — the *interpretation* depends on M-1 | **Blocked by M-1.** Its mitigation is identical either way: the drop-by-priority rule (P-5) in W-08 | **None** — coverage tests use a toy catalogue |
| **R-24** | S2 | **The decision record contains two contradictory streaming-scope statements and no recorded reconciliation.** P-1/P-2 (2026-09-21 / 2026-10-19) say the universe's spot + nearest two futures are streamed; P-3/P-4 (2026-10-18) say the universe is REST-on-demand. Both are in the record; neither amends the other; the implementation was built against P-1/P-2. | §2.4 authority table | Any future implementer reading only R5 would conclude the code is wrong; one reading only PROJECT_CONTEXT §C would conclude R5 was wrong. The ambiguity is what allowed an unreviewed scope to ship. | **Yes** (documentary) | **Upstream of R-02, M-1** | **None** |
| **R-25** | S1 | **There is no complete F&O data surface — only a per-request chain fetch.** The maintainer requires the *complete* option surface for all NSE F&O stocks and the indices. MarketHub has `/v2/option/chain` (Upstox) and `/data/options-chain-v3` (FYERS) behind single on-demand calls, **no** `/v2/option/contract` implementation (the one-call-per-underlying *complete* contract list), **no** prioritised sweep, **no** cross-request cache, and **no** daily-budget accounting for the REST surface. | `app/market_data.py:18, 244-276`; `brokers/fyers/rest.py::option_chain`; absence of any `option/contract` call; §3.11.1-3.11.5 | The requirement is not deliverable today: 219 underlyings, 670 chain cells and 84 816 contracts have no retrieval path other than a user request at a time, so the tail is effectively unavailable and every request competes for the same daily pool. | **Yes** — measured; a full sweep costs **689** chain calls (≈ 14 % of the FYERS Standard daily pool) and **220** contract-list calls | Feeds M-12; gates W-24 | **None** — the existing F&O tests exercise a single chain request, never a sweep or a budget |
| **R-26** | S2 | **Neither provider alone can deliver the complete option surface, and they fail in opposite directions.** FYERS lists **47 634** unexpired option contracts that Upstox does not list at all (Upstox's option listing is a strict subset: **0** Upstox-only). FYERS' `strikecount` is capped at **50 per side**, so **47 of the 670** chain cells — the deep index ladders, including **NIFTY (max 387 strikes)** and **BANKNIFTY (max 370)** — cannot be fully returned by FYERS, while Upstox's `/v2/option/chain` has **no documented strike cap**. | measured against the catalogue (§3.11.2) and the official docs (Upstox `/v2/option/chain`; FYERS `strikecount` max 50 per side) | A completeness policy is required and does not exist: today a chain can silently be **truncated** (FYERS) or **missing contracts** (Upstox) with no state saying so. | **Yes** | Feeds M-12, W-25 | **None** — nothing asserts chain completeness or truncation |
| **R-27** | S2 | **BSE F&O is a single-provider dependency — and the cause is a missing sync entry, not a provider limitation (V13, C5: the provider limitation is now largely ruled out).** The catalogue holds **5 126 BSE option contracts and 12 BSE futures — all Upstox, zero FYERS** — because `FYERS_MASTER_URLS` (`app/instruments.py:34-37`) lists `NSE_CM`, `NSE_FO`, `BSE_CM` and `MCX_COM` but **not `BSE_FO`**; a read-only check shows FYERS **does publish** `BSE_FO_sym_master.json`. **Two official sources now confirm FYERS BSE support:** the Exchange-Segment Mapping lists **BSE \| Equity Derivatives \| exchange 12 \| segment 11** **[FY-7]**, and the futures-chain documentation's own example uses **`BSE:SENSEX-INDEX`** **[FY-5]**. | `app/instruments.py:34-37`; catalogue counts; §3.11.6; **[FY-5]**, **[FY-7]** | The maintainer named **BSE SENSEX** as a deliverable, and it currently depends on a single broker. With FYERS BSE F&O documented as supported, the dependency is **provisional on the sync omission**, not a provider limitation. | **Yes** | Feeds M-13, W-26 | **None** — no test asserts that a named in-scope segment has more than one provider |
| **R-28** | S2 | **The route V10 proposed for the FYERS-only tail cannot supply the required fields — and the documented alternative is conditional (V13, C2/C3).** V10 proposed FYERS **batched quotes** for the 47 634 contracts Upstox does not list. The documented FYERS quote response carries `lp / bid / ask / spread / open_price / high_price / low_price / prev_close_price / atp / volume / tt / fyToken` and the CAS fields — **no `oi`, no `prev_oi`, no `iv`, no `greeks`** **[FY-2]**. The documented route to OI and previous OI for any FYERS symbol is **`/data/market-depth`** (`oi`, `pdoi`, `oipercent`) — **one symbol per request** **[FY-3]** — and its OI fields are present **only when `oiflag` is true** (the doc's own equity example returns `oi: 0, oiflag: false`). A second route exists for OI: Upstox **`/v2/market/oi`** returns **per-strike** OI for an underlying + expiry + `date` with no documented restriction to Upstox-listed instruments **[UPX-6]** — **already implemented** in MarketHub (`app/market_data.py:19`, `market/normalize/upstox_analytics.py:27`) — but its strike coverage outside Upstox's master is **not documented**. | FYERS Quotes **[FY-2]**; FYERS Market Depth **[FY-3]**; Upstox Get OI **[UPX-6]** | A tail served on quotes alone would present **price without open interest** — the most-watched F&O field. The depth route is 1 symbol/request and its OI is conditional; the Upstox OI route is per-strike and **unverified** for unlisted strikes. | **Yes** | Feeds M-12, W-25 | **None** — no test asserts which fields a surface actually returns |
| **R-29** | S3 — **the affected symbols are now out of the required surface (D-25)**, so this is a limitation of the **out-of-scope tail**, not a failure of the required surface | **A narrow, demonstrated data gap: no documented source for IV and Greeks on the 2 969 in-scope strikes that carry 5 934 option symbols (V13, C2: the OI half is weaker than V12 stated).** These are FYERS-listed contracts outside FYERS' ±50-strike chain window and absent from Upstox's master. Price, volume, bid/ask, previous OI and depth are available; **IV and Greeks are not**. | §3.12.3 (every route evaluated, with the reason each fails); **[UPX-3]**, **[UPX-5]**, **[UPX-6]**, **[FY-2]**, **[FY-3]**, **[FY-4]** | Without an explicit state, the API would present these contracts as if complete. The gap is concentrated in the deep index wings — BANKNIFTY 2026-12-29 (**160** strikes), 2026-11-23 (157), 2026-10-27 (155), NIFTYNXT50 2026-11-23 (140) / 2026-12-29 (132), MIDCPNIFTY 2026-10-27 (88), NIFTY 2026-12-29 (74). | **Yes** | Feeds M-12, W-25 | **None** |
| **R-30** | S2 | **Provider failover at full coverage is not achievable on the FYERS Standard tier.** The settled desired set is **659** contracts; the capacity-first routing places **623** on Upstox and **55** on FYERS, because the FYERS usable capacity is **90** in total (entitlement 100 less a 10 % reserve). If Upstox becomes unavailable, FYERS can absorb at most ~55 of the plan's contracts, so the *coverage* degrades sharply even though the *subscription* stays within budget. Measured in §3.13 against a copy of the live catalogue. | The entitlement is a broker limit; the reserve is MarketHub's. **This is a property of the entitlement, not a defect**, but it must be stated rather than discovered: *streaming the full universe* and *surviving a provider outage* are mutually exclusive at the FYERS Standard tier. It is the missing half of the M-5 discussion. | **Yes** | Feeds M-5 | **None** — no test or projection states the failover capacity |
| **R-31** | S1 | **The option surface was priced symbol by symbol instead of cell by cell (V16).** The planner treated every one of the **87 842** option symbols as its own unit of work, and the **5 934** deep-ladder symbols as **5 934 individual per-symbol REST depth calls** — when the unit of work is the **(underlying, expiry) cell**, one chain response covers every symbol in that cell and carries all seven required fields. The cost of the mistake was a **per-symbol budget of 7 011 FYERS calls** against a 5 000/day pool (a surface that appeared undeliverable) and a **class-C "provider limitation"** that was in fact a scope artefact. **Corrected in V16** (§3.16): the required surface is **670 + 19** chain calls with **0** quotes and **0** depth calls *(that per-sweep figure is **superseded by R-34 / V21** — the sweep is withdrawn and cells are fetched on demand)*. | **W-24** (the cell-addressed surface), **W-25** (the field surface) |
| **R-32** | S2 | **The index poller's schedule is clock-only, not session-aware, and its fallback always runs (V19).** `app/fyers_index_source.py` gates on a **hard-coded 09:15–15:30 IST** window with **no** exchange-status, holiday, special-session or per-exchange awareness, so it polls **75 requests on every weekend and every exchange holiday** for values that cannot change; it is **started unconditionally** even when the Upstox WebSocket is the healthy primary index source; it stops **10 minutes before the current 15:40 F&O close**; and a failed request is retried on the next 30 s wake with **no backoff**, up to **2 requests/minute**. The plan had carried this cost as **288/day** — **3.8× the measured 75/day**. | **75 requests on each non-trading day** (≈8 550/year, **31 %** of the poller's annual spend) and a **retry storm** worth up to **24 % of the daily pool in one hour**; and the plan's own budget was overstated in both directions | **Corrected in V19** (§3.17): the measured cost is **75/gated day**, and the six defects are turned into **W-27** | **W-27** (session-bound the poller), **W-24** (the budget it feeds) |
| **R-33** | S2 | **The FYERS index path is an always-on duplicate, not a fallback (V20).** `app/server.py` starts the poller **unconditionally** and **nothing in it consults the Upstox feed's state** — so the FYERS index source runs in parallel with the primary WebSocket rather than after it, and the FYERS REST pool is spent even while Upstox is healthy. The *intended* fallback semantics exist only in the **read path** (`app/market_indices.py` resolves a FYERS candidate when Upstox has no quote, so an index is "stale rather than unavailable"). The Upstox feed already publishes everything a health decision needs — `state` (`streaming`/`reconnecting`/`failed`/…), **`last_message_at`**, `last_exit_reason`, `reconnect_count` — but the poller ignores all of it. | **75 FYERS requests/day spent on a duplicate** while the primary is healthy, and **no failover behaviour at all** — the fallback cannot be *switched on* because it is never switched off. The health decision also cannot be made today, because the poller has no notion of the primary's state. | **Corrected in V20** (§3.17.6): the lifecycle is specified — idle while the primary is healthy, armed on a **600 s** staleness plus **60 s** dwell with a **15 min** recovery hold-off, and **exchange-specific provider-driven** session eligibility. | **W-28** (the failover lifecycle), **W-27** (the session binding) |
| **R-34** | S1 | **The plan proposed a full-surface background chain sweep the code never had, and the one path that can multiply requests without bound went unexamined (V21).** V16–V20 specified a mandatory **670-cell FYERS + 19-cell Upstox** background chain sweep — **689 REST requests per cycle** to keep the whole option universe "fresh" whether or not anyone was looking at it. The audit found **no such path in the working tree**: the sweep was **never implemented**, and the only recurring market-data fetchers are the **alert-driven** analytics chain scheduler (**one request per registered chain per 60 s**, with **no exchange-session gate and no aggregate bound**) and the **always-on** FYERS index poller. | the plan's cost model, its work items and its whole budget rested on a cost the product does not incur, while the path that **can** grow without limit — one chain request per minute per alert-registered chain, overnight, for an unbounded number of chains — was never identified, bounded or gated | **Corrected in V21** (§3.18): the sweep is **withdrawn**; the target model fetches a chain **on demand** (one request for the cell the user opens), keeps the **visible window** live over the WebSocket, refreshes **only the active cell** on reconnect or a field gap, and **stops outside the session**, retaining the last snapshot labelled with its session. The alert-driven scheduler gains a session gate and an explicit aggregate cap. | **W-29** |
| **R-35** | S1 | **The alert-driven chain scheduler is unbounded in the number of registered chains, is not session-gated, and can register the same cell twice (V22).** The registry is `_dependents: dict[chain_key, set[alert_id]]`, populated from **enabled analytics alerts** whose metrics need a chain (`pcr_oi`, `pcr_volume`, `max_pain`, `iv_skew`); each tick refreshes **every** registered chain with **one REST chain request each**. The tick interval is **60 s** (minimum 30) and the concurrency bound of **4** spreads the requests in *time* without reducing their *count*. Two spellings of the same cell are possible — `_dependency_key` yields `analytics:<canonical_id>:<expiry>` while the fallback yields `<canonical_id>:<expiry>` — and `parse_chain_key` accepts both, so the same cell can occupy **two** registry entries and be fetched **twice** per tick. There is **no** aggregate cap and **no** session gate. | the request count is a function of the user's alert configuration, not of anything bounded: with **N** registered chains the scheduler issues **N** requests per minute and **1 440 N** per **calendar day** — because it has **no session gate** — and the FYERS Standard pool (5 000/day) is **exceeded at N = 4** (5 760/day) before any other consumer; the catalogue's **220** FYERS-routed underlyings bound it at **316 800** requests per day, ≈ **63×** the pool. The **385 N** figure applies only **after** the session gate of W-30 exists| **Corrected in V22** (§3.19): the registry is keyed on a **normalised canonical (underlying, expiry)** so a cell is fetched once; the tick is **session-gated** per exchange; every request passes a **shared per-provider rate and daily-budget admission**; retries are **bounded and admitted**; and an alert whose chain is deferred, stale or unavailable evaluates **UNKNOWN** and **cannot fire falsely**. | **W-30** |

| **R-03** | S1 | **`POST /api/fno/apply` error path is broken by construction.** The `except` handler imports `SubscriptionBudgetError`, which does not exist. | `api/fno_coverage_routes.py:130-131`; `SubscriptionLimitError` is the only class in `app/subscriptions/service.py:42` | ImportError inside the handler → 500 with a raw traceback instead of a controlled response | **Yes** (static) | — | **None** — the error path is untested |
| **R-04** | S1 | **Startup restore leaves the subscription service in a wedged state until it is explicitly cleared.** `set_derivatives_universe()` runs *before* the failing `reconcile()`; the exception is caught and logged as a warning, but the 500-key union remains installed. | `app/server.py:1304-1312`; `app/subscriptions/service.py:157-175` | **What stays installed:** the `_derivatives_universe` owner (500 upstox + 50 fyers keys) — an **in-memory** attribute, so it does not survive a restart. **What keeps failing:** every `resolve()` → `SubscriptionLimitError` → every `reconcile()` fails → `POST /api/market/fno/view` → 500, `POST /api/subscriptions/apply` → 400, `POST /api/fno/apply` → 500 (ImportError, R-03), and `GET /api/subscriptions/status` → `resolved_count: null`. **What still works:** the coverage is simply **not applied** — 0 F&O contracts are streamed; the rest of the market-data path is unaffected. **What restores service:** `DELETE /api/fno/apply` (`clear_derivatives_universe()` then `reconcile()`, `api/fno_coverage_routes.py:146-156`), or a process restart. **Not permanent** — but not self-healing either, and the failure is invisible in the UI (R-13). | **Yes** | Consequence of R-01 | **None** |
| **R-05a** | S4 | **The FYERS capability comment mis-states the limit's scope.** The declaration's **value (100) is correct** — it is the official Data Socket Standard-tier limit and the socket MarketHub opens **is** the Data Socket (§3.3). But the comment says *"100 symbols/**connection**"* whereas the official wording is *"per user, across all your connections, not per connection"*. | `brokers/fyers/capabilities.py:35-38` vs the official Data Socket Overview (checked 2026-10-20) | An engineer sizing a budget from this comment would size per connection; FYERS' own `FyersDev/fyers-skills` doc repeats the same wrong phrasing (*"5000 symbols per connection"*), so the risk of picking up the wrong scope is real | **Yes** (static + doc comparison) | Feeds W-07 | **None** — `test_fyers_capabilities.py` asserts the declaration against itself, not against the vendor wording |
| **R-05b** | S2 | **The FYERS 100 is a plan-tier default, not a confirmed entitlement.** No account is authenticated on either broker, so the tier that actually applies to this deployment is unknown. The plan's `PLAN_CAP_OVERRIDES` already anticipates a Prime tier (5 000) — an assumption the code is entitled to make *only if the tier is known*. | `brokers/fyers/capabilities.py:35-38`, `providers/capacity.py:34-36`; PROJECT_CONTEXT:1612-1629 (both brokers unauthenticated) | Any capacity conclusion drawn from the 100 is conditional. If the account is not Standard-tier the whole FYERS arithmetic shifts. | **Yes** (static) | Feeds M-5, M-8, W-07 | **None** — nothing records the account's plan tier or its verification status |
| ~~**R-05**~~ | — | **WITHDRAWN IN V6.** V5 claimed the FYERS 100-symbol Data Socket limit could not be applied because MarketHub opens `wss://socket.fyers.in/hsm/v1-5/prod`. That inference came from the URL path alone and is **contradicted by the evidence in §3.3**: the endpoint *is* the Data Socket, the official sample code uses `data_ws.FyersDataSocket`, and the reference SDK connects to that URL while calling it the documented v3 Data Socket protocol. The *limit value* is correct and applies. | §3.3 evidence chain | — | — | — | — |
| **R-06** | S2 | **The reserve floor distorts small entitlements.** `reserve = min(max(10 % × cap, 50), cap−1)` makes a 100-symbol entitlement yield **50** — a 50 % reserve — while a 2 000-symbol entitlement yields a 10 % reserve. The floor is *deliberate* (it prevents a zero budget for tiny capacities) but its **percentage effect at small entitlements is an artefact**, not a recorded product decision — see the full derivation and classification in §3.6. | `providers/capacity.py:42-62`; R7 (arithmetic accepted, effect not examined) | FYERS Standard is budgeted at half its documented entitlement. **Measured effect (V9, §3.6/§3.7.2b):** under Scope-S1 the FYERS union is **85** (RELIANCE view) or **63** (NIFTY view) — above the current 50-key allocation but **below** the 100-symbol entitlement, and **inside** the 90-key usable capacity the corrected floor would produce. So the floor is the constraint that costs FYERS coverage: with usable 50 the FYERS side drops **13–35** keys, with usable 90 it drops **none**. Under Scope-S2 the union is 35 or 13 and the floor is irrelevant. The *reason* the allowance is 50 is a floor artefact rather than a decision. | **Yes** | Feeds M-5, W-07 | `test_provider_capacity.py:27` pins `effective_budget(2000)==1800` only — the small-capacity case is unpinned |
| **R-07** | S2 | **The previous-OI recorder is inert.** `observe_quote()` has **no production call site** (tests only). The recorder starts, builds an identity map, and periodically flushes an always-empty set. | repo-wide grep: `observe_quote` appears only in `app/previous_oi.py` and two test files; `app/server.py:1355-1385` starts it but nothing feeds it | The documented Upstox OI-change fallback can never be populated; the lifecycle snapshot reports `running: true` — a **false health signal** | **Yes** | Feeds M-9b | `test_previous_oi.py` proves the recorder works, not that it is wired |
| **R-08** | S2 | **The FYERS REST daily pool is unbudgeted.** Standard allows **5 000 Data-API requests/day** across quotes, depth, history and option chain, and *"if the per-minute rate limit is exceeded more than 3 times in a day, the user is blocked for the rest of the day"*. The index poller, the option-chain requests and the history calls all draw from that one pool, and **no request counter, daily budget or guard exists anywhere**. | official plans page, 2026-10-20; `app/fyers_index_source.py:31-33` (`MAX_RPS=5`, `MIN_INTERVAL_SECONDS=60`, `FRESHNESS_SECONDS=300`); `app/server.py:774` (30 s loop tick) | A mis-tuned or bursty REST path can hard-block the account for the day. The *current* index-poller consumption is modest (one batched request per 300 s freshness window ≈ **75 per gated day** for **9** symbols), so this is a **missing guard**, not a present outage — but the chain and history paths are uncounted. | **Yes** (static) | Feeds W-15 | **None** |
| **R-09** | S2 | **Four divergent provider-inference implementations**, one hardcoding `("upstox","fyers")` as the preference and one falling back to a guessed provider. | `providers/registry.py:121-160`; `market/quote_coordinator.py:32,35-41`; `app/subscriptions/service.py:50-61`; `app/market_data.py:91` | Routing is not registry-driven (D-8); the same instrument can resolve to different providers in different subsystems | **Yes** | Feeds W-05, W-17 | `test_provider_routing.py` covers the registry only |
| **R-10** | S2 | **NIFTYFPI has two non-existent subscription keys.** `MAJOR_INDICES` hardcodes `NSE_INDEX\|Nifty FPI` and `NSE:NIFTYFPI-INDEX`; the catalogue holds `NSE_INDEX\|Nifty FPI 150` and `NSE:NIFTYFPI150-INDEX`. Both static keys have **0 rows** in the catalogue. | catalogue queries: 0 rows for the static keys, 1 row for the catalogue keys; `app/subscriptions/service.py:424-434` (static) vs `app/fno_coverage.py:285-305` (catalogue) | Two bogus keys subscribed on every reconcile (they can never deliver data); one canonical index subscribed under **two different keys** by two owners | **Yes** | Feeds W-03 | **None** — no test asserts the static index keys exist in the catalogue |
| **R-11** | S2 | **The coverage planner routes to unauthenticated providers.** `FnoCoverageService` is constructed **without** `status_provider`; `_available()` therefore returns `True` for every provider. The runtime status function that *does* exist returns `"ready"`, which is **not** in `_AVAILABLE_STATES = {streaming, connecting, reconnecting}` — so injecting it as-is would mark everything `PROVIDER_UNAVAILABLE`. | `app/server.py:1151-1152`; `app/server.py:1100-1117`; `app/fno_coverage.py:54, 268-273` | Contracts are routed to a provider that cannot serve them; a truthful routing layer would report `PROVIDER_UNAVAILABLE` but cannot | **Yes** | Feeds W-06, M-7 | **None** |
| **R-12** | S2 | **The live-validation evidence describes a different build.** The *staged* planner selects **one** nearest expiry → `desired = 442` (≈ the reported **440**); the *unstaged* planner selects **two** → `desired = 662`. | `git show :app/fno_coverage.py` (no `_nearest_expiries`) vs the worktree (`_nearest_expiries(..., 2)`) at `app/fno_coverage.py:229-257, 410`; arithmetic reproduced in SQL against the live catalogue | The G2 live evidence cannot be claimed for the tree under review; the "440 contracts routed" line is not reproducible | **Yes** | Feeds W-23 | N/A |
| **R-13** | S2 | **Inconsistent error contracts across the three surfaces that call the same service.** `api/subscription_routes.py:144-151,165-173` → 400 with `detail`; `api/fno_routes.py:227-230` → 500 generic; `api/fno_coverage_routes.py:128-136` → ImportError → 500. The F&O frontend mutation has **no `onError`** and never renders the failure. | as cited; `frontend/src/features/fno/useFno.ts:113-120`; `FnoWorkspaceView.tsx:326` | The same underlying condition presents as 400, 500 and a raw traceback; the user sees **nothing** | **Yes** | Feeds W-02, W-12, W-13 | **None** |
| **R-14** | S3 | **Frontend groups option rows by strike and discards provider attribution**, while the backend selects one provider per **(strike, option_type)**. | `frontend/src/features/fno/useFno.ts:134-156`; `app/subscriptions/service.py:556-559` | A CE and PE at the same strike may come from different providers and be shown as one row without attribution. **Currently 0 live instances**: across 121 998 unexpired NSE option rows, 0 strikes have CE/PE from different providers, and all 47 634 provider-exclusive contracts are FYERS-only | **Yes** (0 instances; risk is structural, not active) | Feeds M-6, W-17 | `fnoFormat.test.ts` covers the normaliser, not the provider boundary |
| **R-15** | S2 | **The FYERS TBT adapter hardcodes Prime-tier limits** (3 connections × 5 symbols) with a comment claiming *"Official rate limits (verified from fyers-apiv3 implementation)"* — an **implementation**, not the documentation. The official TBT limits are **Standard 1 symbol** and **Prime 15** (up to 3 connections × 5). TBT is opt-in (`tbt_enabled`, absent from the current `config.json`), so this is latent today, but a Prime entitlement is embedded in an adapter that presents itself as officially sourced. | `brokers/fyers/tbt/feed.py:44-47`; official Plans & Limits + WebSocket Overview, checked 2026-10-20 (TBT: Standard 1 / Prime 15) | Enabling TBT on a Standard account would exceed the entitlement from the first symbol; on Prime the declared 15 is correct, so the defect is the *unverified tier assumption*, not the number | **Yes** (static) | Feeds M-8, W-16 | `test_fyers_tbt.py` does not assert the entitlement against the vendor limit |
| **R-16** | S3 | **The FYERS index poller ticks every 30 s while its own declared minimum interval is 60 s**; only the 300 s freshness gate actually bounds fetches. | `app/server.py:774` vs `app/fyers_index_source.py:32` | Cosmetic inconsistency, but the declared bound is not the enforced one | **Yes** | Feeds W-15 | `test_fyers_index_source.py` covers the guards, not the loop cadence |
| **R-17** | S3 | **The FYERS index poller writes to the quote store on a second cadence from the REST daily budget** with no request counter. | `app/fyers_index_source.py:134-152` | Contributes to R-08 | **Yes** | Feeds R-08, W-15 | **None** |
| **R-18** | S4 | **Stale schema-version assertion.** `test_previous_oi.py:47` expects `user_version == 22`; the store is 23 at HEAD (the X/Twitter v23 step), and the F&O work added a table without a version bump. | `git show HEAD:core/persistence/modules/schema.py` → `SCHEMA_VERSION = 23`; `test_previous_oi.py:47` | One permanently red test; misattributes a version drift to the F&O feature | **Yes** — reproduced | — | the test itself |
| **R-19** | S4 | **The registry's `DEFAULT_MODE` ("full") disagrees with the feed's own default (`ltpc`)**, while `config.json` sets `full` for both providers. | `brokers/upstox/capabilities.py:30` vs `brokers/upstox/feed.py:161`; `config.json:120,146` | The declared capacity (2 000 for `full`) only holds if `full` is actually requested; `ltpc` would be 5 000 individual / 2 000 combined. A config drift silently changes the applicable limit. | **Yes** | Feeds W-07 | **None** |
| **R-20** | S4 | **The runtime configuration is git-ignored and has drifted** (`port: 52904` vs `public_base_url …:7070`; `app/config.py` default 7070 is overridden). | `.gitignore:37`; `config.json`; `app/config.py:20,186` | The reported live evidence (bound to 7070) cannot be reproduced from the repository; a reviewer sees a different runtime from the one that was validated | **Yes** | Feeds W-22, W-23 | N/A |
| **R-21** | S4 | **The F&O index set includes BANKEX with no supporting evidence**, and `market/index_registry.py`'s docstring contradicts its own `is_fno` flags. | `git diff -- market/index_registry.py` (unstaged flip); `market/index_registry.py:9-14` vs `:35-45`; R5 §29, R6 §155 | An unapproved universe expansion, and documentation that cannot be trusted | **Yes** | Feeds M-4, W-04 | **None** |
| **R-22** | S4 | **The "F&O coverage restored at startup" log line reports `plan.desired` (contracts *planned*) while its wording says "routed"**, and is only emitted on success. | `app/server.py:1308-1310` | The log can be read as "N contracts are live" when N is the plan size, not the routed or subscribed count | **Yes** | Feeds W-11 | N/A |
| **R-23** | S3 | **A documented provider limit is not enforced at the adapter boundary.** `FyersRest.quotes()` documents *"Fetch quotes for up to 50 symbols"* (the official Standard batch limit) but **neither validates nor chunks** — it joins every symbol into one comma-separated query string. The only production caller passes the index set (7–9), so the limit holds **by accident of the current caller**, not by construction. | `brokers/fyers/rest.py:361-374`; official plans page (Quotes API: Standard 50 symbols/request) | A future caller, a larger index set, or a change to the poller's symbol source silently exceeds the documented batch limit and produces a provider error that is swallowed by the poller's `except Exception: return []` (`app/fyers_index_source.py:148-149`) | **Yes** (static) | Feeds W-15 | **None** |

---

## 6. Architecture and dependency map

### 6.1 The failing path

```
POST /api/market/fno/view
 └─ api/fno_routes.py:219  workspace_contracts()      ← reads catalogue, 1 provider per (strike,type)
 └─ api/fno_routes.py:225  set_active_view(keys)      ← owner #4 (ephemeral)
 └─ api/fno_routes.py:226  reconcile(feed)
      └─ app/subscriptions/service.py:1043  resolve()
           ├─ owner #1  _index_keys()            9 Upstox + 9 FYERS   (static MAJOR_INDICES keys → R-10)
           ├─ owner #2  _stock_keys()            user-persisted equities
           ├─ owner #3  derivative rules         NIFTY/BANKNIFTY futures + option legs
           ├─ owner #4  active view             equity + futures + ≤42 option legs
           ├─ owner #5  analytics universe      breadth/heatmap/map equities
           ├─ owner #6  derivatives universe    ← the F&O coverage plan: 500 Upstox + 50 FYERS
           └─ :995  if len(keys) > 500: raise SubscriptionLimitError
 └─ api/fno_routes.py:227  except Exception → HTTP 500
```

### 6.2 The two planners

| | Coverage planner | Subscription resolver |
|---|---|---|
| Owner | `app/fno_coverage.py` | `app/subscriptions/service.py` |
| Input | catalogue + registry + persisted `fno_config` | DB preferences + 6 ephemeral owners |
| Scope | **whole F&O universe** — conforms to D-1 position (a); would diverge under position (b) | union of all owners |
| Overflow policy | drop by priority, mark `CAPACITY_LIMITED` | **raise** |
| Budget awareness | per-provider budget | **app cap only** |
| Availability | none (no `status_provider`) | n/a |
| Runs | at startup + on demand | on every reconcile |

The planner is *permissive in scope* and *graceful in overflow*; the resolver is
*strict in overflow* and *blind in scope*. Because the resolver runs **last**,
over the **union**, its strictness wins and the planner's graceful degradation
is discarded.

### 6.3 The capacity-first routing sequence

**The first response to constrained capacity must be efficient *routing*, not
arbitrary dropping.** This is the sequence the implementation must follow
(W-08). It is a description of a contract, not code.

| Step | Rule | Why it is there | Owner |
|---|---|---|---|
| **R-1** | **Resolve canonical identity** for every desired contract through the registry — never by string shape. | The union is deduplicated by *provider key*; if the identity is wrong, the same contract is counted twice or under the wrong key. | W-03, W-05 |
| **R-2** | **Consider only eligible providers**: enabled in configuration **and** runtime-eligible (state supports routing) **and** capability-suitable (the provider can supply what the contract needs). | Routing to a provider that is logged out or lacks the capability produces a contract that can never deliver. | W-06 |
| **R-3** | **Route each canonical contract to exactly one** eligible provider — broker-neutral, one-provider-per-contract, no hardcoded provider preference, no per-symbol workaround. | A contract must never be half-owned by two providers: that is how mixed-provider data corruption happens. | W-05, W-17 |
| **R-4** | **Choose among the eligible providers by remaining usable capacity**, with the configured preference order as the **tie-break only** (never a gate). | The product goal is to use the maximum safely usable capacity **across** the configured brokers; a first-come-first-served router strands capacity on one provider while the other idles — and strands failover headroom. | W-07, W-08 |
| **R-5** | **Deduplicate the complete union of all six owners exactly once**, by exact provider key. | Owners overlap **by design** (①∩⑥=7, ③∩⑥=1, ④∩⑥=1, ③∩④=4 measured). Per-owner budgeting double-counts. | W-08 |
| **R-6** | **If the usable capacity is still insufficient**, apply the **already-approved** overflow policy P-5 (drop by priority; mark the dropped set `CAPACITY_LIMITED`) and **expose the missing coverage** — never claim it is streamed. | The plan already says what to drop and in what order; the resolver simply does not implement it. | W-08, W-09 |
| **R-7** | **Never crash, never poison, never lie.** An over-budget request must return a controlled response, must not leave stale state installed, must not wedge the subscription service, and must not be reported as successfully applied. | This is the failure that was actually demonstrated. | W-08, W-11, W-12 |

**A conflict to surface for maintainer review — not to be silently resolved.**
P-5 ranks by instrument **kind**: *"live futures > index spot > option strikes"*. Under pressure it therefore drops **option strikes first** — and the *focused ATM-window option legs* are option strikes, i.e. exactly the instrument set the user is looking at in the open workspace. The capacity-first goal is to preserve **full universe coverage**, and the coverage goal would rather sacrifice the focused window than a universe contract. **P-5 does not distinguish *role* (active view vs universe) from *kind*.** Two things follow:

* Under the capacity-first design this conflict **does not arise in practice** —
  the measured state is **0 dropped** (§3.7.2b) — so it is a **latent**
  policy question, not a present defect.
* When capacity *is* exhausted, whether the active view should outrank the
  universe is a **product decision that the current policy does not express**.
  It is raised as **M-11** for maintainer review; **P-5 is not reopened or
  redefined by this plan.**

### 6.4 Dependency map (what must be true before what)

```
M-1 and M-4 are CLOSED (the maintainer's requirement of 2026-10-20).
They are applied by W-04 and W-09 and gate NOTHING.
Open decisions: M-2, M-5, M-6, M-7, M-8, M-9, M-9b, M-10, M-11, M-12, M-13
(M-14 is OPTIONAL and gates nothing)

   ├── M-9  ──► W-01 change-set integration ──► clean checkout ──► every later acceptance test
   │
   ├── (closed M-4) ──► W-04 apply the index set  ──┐
   ├── (settled D-6/D-8) ──► W-03 index key source ┤
   │                                                  ▼
   │                              W-05 provider inference unified (registry-driven)
   │                                          │
   ├── M-7  ──► W-06 provider availability ◄───┘
   │
   ├── M-2  ──► W-10 application ceiling: retire, or retain as a derived guard
   │                 │
   │                 ▼
   ├── M-5  ──► W-07 provider-capacity model (7 concepts; one formula;
   │                 reserve floor; explicit account tier)
   │                 │
   │                 ▼
   │              W-08 shared union + capacity-aware routing + P-5 overflow
   │                 │
   ├── (closed M-1) ──► W-09 apply the settled scope
   ▼
W-02 dead import ──► W-11 startup atomicity ──► W-12 uniform error contract ──► W-13 UI surfacing
   │
   ▼
W-14 previous-OI flow (M-9b)  ·  W-15 index source budget/cadence  ·  W-16 TBT entitlement (M-8)
   │
   ▼
W-17 chain/analytics provider-consistency end-to-end (M-6)
W-18 REST chain failure + expiry fallback
   │
   ▼
W-19 integration harness ──► W-20 unit/regression per defect ──► W-21 baseline discipline
   │
   ▼
W-22 clean-checkout + startup readiness ──► W-23 live re-validation (G2) ──► G3 release gate
```

**Why this order.** M-1 and M-4 no longer appear as gates: the scope is
settled, so W-04 and W-09 *apply* it. W-01 must precede everything because no
acceptance test can run against a tree that cannot be imported. W-03/W-04/W-05/W-06 precede the
capacity work because the capacity model is computed over *routed, deduplicated
keys* — if identity and routing are inconsistent, every capacity number is
meaningless. W-07/W-08/W-09/W-10 form one inseparable cluster: the scope
decides how many keys are needed, the budget decides how many are available,
the overflow policy decides what happens when the two disagree, and the
application ceiling is the **output** of that chain, never an input chosen
first. W-11–W-13 follow because truthful failure reporting is only meaningful
once failure is well-behaved. W-17 follows the identity work because the
frontend can only carry provider attribution if the backend's provider
selection is settled. The verification cluster comes last because every
earlier acceptance criterion is what the tests assert.

---

## 7. Dependency-ordered remediation plan

Each item states: defect addressed · evidence · desired behaviour and acceptance
criteria · affected components · dependencies · downstream consumers · tests
required · failure/recovery · observability · risk & regression surface ·
rollback/containment · approval required · evidence to declare complete.

Severity legend: **S1** blocks release · **S2** materially wrong ·
**S3** robustness · **S4** hygiene.

---

### PHASE 0 — Decision gates (no code)

| ID | Sev | Addresses | Work | Acceptance | Approval |
|---|---|---|---|---|---|
| **W-00** | S1 | D-2, D-3, D-4, D-5, D-6, D-7, D-8, D-10, **R-24** | **Record the decisions that are still open** — M-2 (application ceiling), M-5 (headroom floor, account tier, failover trade-off), M-6, M-7, M-8, M-9, M-9b, M-10, M-11, M-12, M-13 — in a dated decision record **in the repository** (a new decision file, not an edit to R3–R11 or PROJECT_CONTEXT). For each: the decision, the rationale, the derived numbers, and the artifacts it supersedes. **M-1 and M-4 are CLOSED** (the maintainer's requirement of 2026-10-20) and are **recorded as closed decisions, not re-opened**; the record must state that they confirm P-1/P-2 and exclude BANKEX. **M-3 is not a design decision** — the overflow policy (P-5) is already approved; if separate implementation authorisation is wanted it is recorded as an authorisation. | Every open M-* has a dated, attributable decision entry, and the two closed decisions are recorded as such. Decisions that change earlier rounds are marked as *amendments*, leaving the originals intact. | **Maintainer — blocking** |

**Why first:** W-07…W-10 are arithmetic over M-1…M-5; W-03/W-04 are over M-4/M-6; W-01 is over M-9; W-17 is over M-6. Starting earlier produces a fourth guess.

**Rollback/containment:** n/a — documentation only.

---

### PHASE 1 — Repository integration prerequisites

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-01** | S1 | R-10's S1 acceptance; D-12 | Integrate or remove the 6 untracked modules; **separate the mixed-scope edits** (news MCP wiring + `test_mcp_streamable_http.py` change; X/Twitter, Yahoo, heatmap) from the F&O change set. Produce an explicit F&O file list. **Do not revert, do not re-stage wholesale, do not clean.** | (a) A clean clone of the integration branch imports `app.server` with no `ModuleNotFoundError`; (b) `pytest test/test_architecture_boundaries.py` green; (c) the F&O file list is reviewable in isolation; (d) no unrelated feature is inside the F&O diff. | W-00 (M-9) | **Maintainer** for the scope split; the integration itself is a separate, explicitly-approved Git operation |
| **W-02** | S1 | R-03 | Remove the dead `SubscriptionBudgetError` import from `api/fno_coverage_routes.py`. If a dedicated budget-error type is wanted, **define it deliberately** with the fields the route already assumes (`.violations`) — otherwise translate the existing `SubscriptionLimitError`. | `POST /api/fno/apply` on an over-budget resolution returns a **controlled** 4xx with a machine-readable body; no `ImportError`; no traceback in the response; the response is covered by a test. | W-01 (so the route is importable in a clean checkout) | M-3 (which error shape) |

**Downstream consumers of W-02:** `frontend/src/features/subscriptions/*`, `api/fno_routes.py` (error contract alignment in W-12).

---

### PHASE 2 — Identity, index mapping, routing, availability

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-03** | S2 | R-10 | Make the **catalogue / canonical identity** the single source of index feed keys. `app/market_indices.py::MAJOR_INDICES` must not carry a key that the catalogue does not contain. Repair NIFTYFPI (`NSE_INDEX\|Nifty FPI 150`, `NSE:NIFTYFPI150-INDEX`). Decide whether the static table is reduced to *labels only* and the keys are resolved per provider from the catalogue. **Note (V7):** this is a *technical* consequence of the already-locked "registry/adapter-driven routing and identity" decision (D-6/D-8) and of the catalogue being the authority for provider identifiers — **it is not a new maintainer decision** and does not wait on M-6. | (a) A test asserts **every** provider index key the application can emit exists in the catalogue (fails today for NIFTYFPI); (b) after the change, the resolved Upstox and FYERS key sets contain **no** key absent from the catalogue; (c) NIFTYFPI resolves to exactly **one** key per provider across all owners; (d) the **nine** general index quote subscriptions (owner ①) and the **F&O index universe** (M-4) remain **separate concepts** and are not conflated in the code or in the reporting. | W-01 | none — the authority of the catalogue is already settled (D-6, D-8) |
| **W-04** | S2 | R-10, R-21, D-4, D-11 | **Apply the closed index decision** — the F&O index universe is the **six NSE indices plus SENSEX; BANKEX is excluded.** Revert the working tree's `is_fno=True` for BANKEX, and reconcile `market/index_registry.py`'s docstring (which says SENSEX/BANKEX are *"never part of the F&O universe"*) with the code, which must say SENSEX **is** in scope and BANKEX is not. This is **application of a decided position, not a decision gate** — M-4 is closed. | (a) A test pins the F&O index set to exactly the six NSE indices + SENSEX; (b) the docstring and the flags agree; (c) BANKEX contributes **no** contract to the coverage plan, the universe enumeration or the chain-cell count; (d) the registry change is recorded as an amendment, not an edit to R5/R6. | W-01 | none — M-4 is **closed** by the maintainer's statement of 2026-10-20 |
| **W-05** | S2 | R-09, D-8 | Collapse provider inference to **one** implementation — the registry, which resolves catalogue-first and falls back to adapter-declared key styles. Remove the shape-guessing copies; replace the coordinator's hardcoded `("upstox","fyers")` with the registry's configured preference order. **V7:** this is a *technical* consequence of the already-settled "registry/adapter-driven routing" decision (D-8) — it needs **no** new maintainer decision, and it is **not** the same subject as M-6 (which is the granularity of "one provider per contract", owned by W-17). | (a) One implementation remains; a grep for the removed shape heuristics returns nothing outside the registry; (b) the coordinator's preference order equals `registry.preference_order()`; (c) an instrument unknown to the catalogue resolves via the declared key style, and the fallback is documented. | W-03 | none — D-8 already settles registry-driven routing |
| **W-06** | S2 | R-11, M-7 | Give the coverage planner a runtime-availability source, and reconcile the status vocabulary. Decide the canonical vocabulary (e.g. `ready` vs `streaming`/`connecting`/`reconnecting`) and normalise the runtime status function to it. | (a) With a provider reported unavailable, the plan marks its contracts `PROVIDER_UNAVAILABLE` and routes elsewhere where a capable, available provider exists; (b) with no availability injected (tests), behaviour is unchanged and explicit; (c) no code path treats `"ready"` as "not available" or vice versa. | W-05 | **M-7** |

**Failure/recovery:** W-06's planner must never treat *unknown* status as *unavailable* (that would silently empty the plan) nor as *available* for routing to a logged-out provider. The "unknown → eligible, but never counted as live" convention already used in `SubscriptionService._equity_provider_candidates` is the reference behaviour.

---

### PHASE 3 — Capacity and subscription accounting

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-07** | S1 | R-05a, R-05b, R-06, R-19, D-2 | **Implement the provider-capacity model of §3.5.** One formula, no application-wide constant in the enforced path: `usable_provider_capacity = entitlement - reserve`, `reserve = min(max(ceil(entitlement x 10%), floor), entitlement - 1)`. The **entitlement** is the documented limit *for the actual socket, mode and plan tier* (Upstox `full` 2 000 individual / 1 500 combined per user; FYERS Data Socket 100 Standard / 5 000 Prime, per user across connections) and the **account tier** is an explicit input, **UNVERIFIED** until gate G-T, defaulting to the **lower** tier. The **floor** becomes a declared parameter (recommended **2**, per §3.6) rather than the magic 50, which is what actually cost FYERS its coverage. The duplicated `APP_CAP` literal is removed. The **combined-category** limit is represented even though MarketHub uses one mode, because the mode is configurable (R-19). | (a) The seven concepts of §3.5a each have a single owner and a stated verification status; (b) a test pins the reserve behaviour at both a large entitlement (2 000 to 1 800) and a small one (100 to **90**; the old floor gave 50, so the change is visible); (c) the FYERS entitlement entry names the socket, states the **per-user** scope, and carries an *unverified-tier* flag (R-05a/R-05b); (d) the Upstox `full` vs `ltpc` distinction is represented and the effective mode is read from the same place the feed reads it; (e) **no application-wide ceiling appears in the enforced path** - the only remaining hard bound is the provider feed's own mode guard, which already exists (`brokers/upstox/feed.py:188-191`); (f) the reserve policy is a **named, declared parameter** with the rationale of §3.6 attached. | W-10, W-00 (M-5) | **M-5** (reserve floor); M-2 only via W-10 |
| **W-08** | S1 | R-01, R-24, D-3, P-5 | **Implement the approved overflow policy (P-5) and the capacity-first routing sequence (§6.3) in the resolver.** The resolver becomes the **single** place the union is computed and the **single** place the desired set is fitted: it applies the routing sequence R-1..R-7 - resolve canonical identity; consider only enabled, runtime-eligible, capability-suitable providers; route each canonical contract to exactly one eligible provider, choosing by **remaining usable capacity** with the configured preference order as the **tie-break only**; deduplicate the six owners' union **once**, by exact provider key; and only then, if the usable capacity is genuinely insufficient, apply the **already-approved** P-5 (drop by priority, mark `CAPACITY_LIMITED`) and **expose the missing coverage**. The coverage planner must **describe the whole desired set** instead of pre-truncating at its own budget. **No hardcoded provider preference and no per-symbol workaround.** | (a) An over-budget resolution returns a controlled result with `CAPACITY_LIMITED` entries and **no exception**; (b) the dropped set is exactly the P-5 class and is reported per provider; (c) the resolved union never exceeds `usable_provider_capacity` **including all six owners' contributions**, asserted with the **exact** owner figures of §3.7.1 and the measured overlaps 1n6=7, 3n6=1, 4n6=1, 3n4=4; (d) the union is computed once and the planner reads the same accounting; (e) the routing uses **available eligible capacity** before dropping; (f) the coverage status is **truthful** - the API reports planned, routed, applied and dropped separately, and a partial apply is never reported as applied; (g) the service stays usable after exhaustion: no exception escapes, no stale owner stays installed, no wedged state. | W-07 | **Implementation authorisation only** (M-3) - the policy itself is already approved (P-5) |
| **W-09** | S1 | R-02, R-24, D-1 | **Apply the closed scope.** The maintainer's requirement of 2026-10-20 fixes the coverage scope: the **six NSE indices + SENSEX**, the **NSE F&O stock universe** (213 catalogue-derived underlyings), **all** contracts including option chains and futures, with NIFTY and BANKNIFTY explicitly in scope. The planner must **describe** that whole desired set - **220 in-scope underlyings, 220 spots, 439 nearest-two futures, 689 (underlying, expiry) chain cells, 87 842 option contracts (84 816 NSE + 3 026 BSE)** - and the **resolver** decides what is applied, within the usable capacity. The planner must not truncate the *description* at its own budget. | (a) The planner's desired set equals the settled inventory of §3.12.1 (689 cells, 87 842 contracts, 439 futures, 220 spots) and contains no BANKEX contract; (b) the on-demand remainder is reported explicitly - the contracts that exist but are not streamed, and why; (c) the union across all six owners is computed once and both the planner and the resolver read it; (d) the **streamed** subset is the entitlement-bounded one (spot + futures + the focused ATM window) and the **option** surface is served on demand (W-24) - the two are reported separately and never conflated. | W-08 | none - M-1 is **closed** by the maintainer's statement of 2026-10-20 |
| **W-10** | S1 | D-2, R-01 | **Decide and implement the application ceiling: retire it, or retain it as a guard that cannot bind below the provider's usable capacity.** The analysis in §3.5b finds the ceiling **redundant** (bounded by the entitlement, the headroom and the provider feed's own mode guard) and the **cause** of the demonstrated failure (the resolver enforced it instead of the allocation). The maintainer's 500-to-800 raise is **historical decision context** and is recorded as such, with its date and approving artefact. If the ceiling is retired, the duplicated literal and the stale `test_md_subscriptions.py:484` assertion go with it; if it is retained (option B), it must be **derived from the entitlement** and must never bind below `usable_provider_capacity`. | (a) The decision is recorded in the repository with its provenance, and the historical 800 context is recorded without being treated as a current instruction; (b) whichever way the decision goes, there is **exactly one** declared limit in the enforced path - no duplicated literal; (c) a test asserts the invariant that **no enforced limit is tighter than any provider's usable capacity**; (d) the stale assertion in `test_md_subscriptions.py:484` is updated to the decided state and noted as an approved-value change, not a weakened test; (e) the ceiling's removal does not weaken the provider feed's own mode guard, which remains the hard backstop. | W-00 (**M-2**) | **M-2** |

**Risk & regression surface (Phase 3):** W-08 changes the resolver's public behaviour — `api/subscription_routes.py` currently maps `SubscriptionLimitError` to 400, and that mapping becomes obsolete for over-budget resolutions. W-09 changes what the feed receives — existing stock/index/subscription behaviour must be unchanged, and the F&O coverage API's payload shape changes (adds the on-demand remainder). W-10 changes a constant asserted by `test/test_md_subscriptions.py:484`.

---

### PHASE 4 — Lifecycle, error handling, observability

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-11** | S1 | R-04, R-22 | Make the startup restore **atomic - no wedged state**: compute the plan, install the derivatives owner, and reconcile - such that a failure leaves the service in a **consistent, usable** state and the failure is **loud** (distinct log level / diagnostic field / lifecycle status), not an anonymous warning. Distinguish "planned", "routed", "applied" and "dropped" counts in the log and in the status projection. **V7:** this is deliberately **not** dependent on W-09 - an over-budget plan exists under *either* scope, and the atomicity requirement is the same. | (a) With an over-budget plan, startup completes and the service remains **usable** (a subsequent `resolve()` does not raise) **without needing `DELETE /api/fno/apply` or a restart**; (b) the coverage is reported as **not applied** with the reason, and a **partial** apply is never reported as applied; (c) the log/diagnostics distinguish planned vs routed vs applied vs dropped; (d) a repeat startup and a repeat reconcile are idempotent. | W-08 | none |
| **W-12** | S2 | R-13 | **One error contract** for every surface that triggers a subscription apply: the same exception → the same HTTP status and the same body shape (machine-readable code + human detail), across `api/subscription_routes.py`, `api/fno_routes.py` and `api/fno_coverage_routes.py`. Over-budget is a **controlled 4xx**, not a 500. | (a) A table-driven test asserts the same condition yields the same status + body on all three routes; (b) no route returns a bare traceback; (c) the error code is stable and documented. | W-02, W-08 | none |
| **W-13** | S2 | R-13, R-14, D-10 | **Surface the failure and the attribution in the UI**: render the F&O apply error (currently swallowed by the mutation) and carry the provider through the chain rows to the UI so a mixed-provider row is visible rather than implicit. Must **not** re-key the quote store. | (a) A failed F&O view shows an actionable message naming the capacity reason; (b) the frontend does not present a strike row whose CE and PE come from different providers without saying so; (c) the quote-store write path is byte-unchanged. | W-12, W-17 | **M-6** (granularity) |

**Observability required (Phase 4):** a per-provider counter of *planned / routed / dropped-by-CAPACITY_LIMITED / applied / provider-unavailable* keys, surfaced in the existing diagnostics projection (`app/server.py` lifecycle snapshot) and in the F&O coverage API. This is the evidence that will make the next failure visible instead of silent.

> **Implementation note (2026-10-22 — the demand→lease→apply chain now exists).**
> The chain that W-08 and W-11 depend on was **missing at runtime** and has
> now been implemented and measured (see
> `docs/audit/2026-10-22-live-market-data-and-flooding-findings.md`):
> `SubscriptionManager.apply_demand()` translates the resolved demand into
> leases, and the leases are planned and applied to the feeds. Two
> implementation assumptions in this plan are affected:
> (i) **the routing inside the chain is availability-aware** — a contract whose
> preferred provider has no session is routed to the provider that can serve it
> (live run: 99 instruments planned for FYERS, the provider that had a
> session, and the contracts with no second provider are *reported*, not
> dropped); and
> (ii) **the identity used for that routing is the canonical id**, which
> required a catalogue read model by canonical id
> (`InstrumentCatalog.find_canonical_rows`) that this plan assumed but that did
> not exist. The remaining W-08/W-11 items (the single computed union, the
> planned/routed/applied/dropped counters and the overflow policy) are
> unchanged and still open.

---

### PHASE 5 — Data-flow completion and provider-socket correctness

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-14** | S2 | R-07, D-18 | Either **wire** the previous-OI recorder to a production observation path (the futures quotes already flowing through the market service are the natural source), or **formally exclude** the feature from this release and remove the misleading "running" health signal. A recorder that reports `running: true` while observing nothing is a false health claim. | (a) Either: an end-to-end test shows a live futures quote → recorder → persisted `futures_oi_snapshot` row → OI-change computed with `basis == persisted`; or: the feature is removed from the release scope, the status reports it as absent, and the OI-change path states that Upstox previous-OI is unavailable. | W-00 (M-9b), W-11 | **M-9b** |
| **W-15** | S2 | R-08, R-16, R-17, **R-23** | **Budget the FYERS REST daily pool** (5 000/day on Standard) across the index poller, the option chain and history; align the poller's tick with its declared interval; expose the consumption counter; and **enforce the documented per-request batch limits at the adapter boundary** (Quotes ≤ 50 symbols/request on Standard) so an over-limit request is rejected or chunked rather than sent and silently swallowed. | (a) A test proves the poller honours its declared minimum interval and that the freshness gate is the *secondary* bound; (b) a counter of FYERS Data-API requests per session is exposed in diagnostics; (c) the projected daily consumption under the default configuration is below the Standard budget with a stated margin; (d) `FyersRest.quotes()` above the documented batch limit is rejected or chunked, and the rejection is not swallowed into an empty result. | W-07 | none |
| **W-24** | S1 | R-25, R-28, R-29, D-1, **D-21…D-25** | **Build the option surface as a **cell-addressed, chain-served** layer — not as per-symbol REST work (V16).** The unit is the **(underlying, expiry) cell**: **one chain response covers every symbol in the cell** and carries price, volume, bid/ask, current OI, previous OI, IV and Greeks. The work item must implement: (i) the **complete cell inventory** (689 cells; §3.15) and a **shared, cached sweep** so one sweep serves every user; (ii) the **±20 ATM window selection (D-21)** as a **second phase of the same response** — read the underlying price the chain already returns, compute the true ATM, then select the window — with **no extra request**; (iii) **per-cell completeness states** and a **per-leg reconciliation** against the catalogue (§3.16.8), reporting a missing leg as *provider-missing* rather than assuming the catalogue is right; (iv) the **four tiers** of §3.16.4 — focused streaming, active/visible chain refresh, background full-surface refresh, and the deep-ladder tail — each with its own budget and freshness statement; (v) **shared daily-budget accounting** per provider, including the existing index poller, the visible refresh and retries, with the FYERS pool's *"three per-minute breaches block the account for the day"* consequence surfaced **[FY-1]**; (vi) every Upstox-side rate figure marked **assumption-based** where the documented bucket does not cover market data **[UPX-7]**. | (a) All **689** cells are reachable and report their completeness state; (b) a complete sweep costs **670** FYERS chain calls + **19** Upstox chain calls with **0** quotes and **0** depth calls, and the implementation's accounting reproduces it; (c) the surface is served from cache after one sweep, not per request; (d) the **±20 window** is derived from the chain response's own underlying price, and the **measured** scope size is recorded against the proxy lower bound; (e) a truncated chain, a missing leg, a conditional field and a missing field are labelled as such, never as complete; (f) the **tiered freshness** is implemented and reported per tier, and **no daily full-refresh claim is made**; (g) a degraded provider degrades the background tier and never the active view. | W-07 (budget model), W-15 (REST budget), W-25 (field completeness), W-19 (integration) | none — the requirement is stated (D-1) and its scope is stated (D-21); **M-15** fixes the reading, **M-16** fixes the depth field |
| **W-25** | S2 | R-26, R-28, R-29, **M-12 (re-entry only)** | **Make the required surface field-complete where the providers allow it, and honest where they do not — with current OI and previous OI treated as separate claims.** For the **required surface** (±20 around the true ATM, D-23) the field set is served entirely by **one chain response per cell**: the **Upstox chain** for its listed ladder (the only source with IV+Greeks and **no documented** strike cap) **[UPX-3]**; the **FYERS chain** within ±50 strikes per side of ATM **[FY-4]**; the chain's **top-of-book bid/ask** satisfies the bid/ask requirement (D-24). **Multi-level depth is NOT required (D-24), so no per-symbol depth polling is in the budget.** The **deep-ladder tail is out of scope (D-25)**, so the 5 934 class-C symbols carry no requirement: if the tail is ever re-entered, they must be reported `field-unavailable` for IV and Greeks, their **current OI** is `field-unestablished` and their **previous OI** has **no proven source** other than FYERS depth. The providers' roles must be **explicit in the code and in the API**, with no per-symbol workaround. | (a) Every in-scope contract has a per-field source or an explicit `field-unavailable` / `field-unestablished` state; (b) the required surface's contracts report **all seven fields**, sourced from a chain response; (c) the **current-OI probe** is **not** required for the required surface — it is a re-entry condition only; (d) the completeness state distinguishes *provider-truncated*, *provider-missing*, *field-unavailable*, *field-unestablished* and *provider-conditional*; (e) no provider preference is hardcoded per symbol; (f) the out-of-scope tail is **not** reported as a failure of the required surface. | W-24 | **none open** — M-12 applies only if the tail is re-entered |
| **W-26** | S2 | R-27, M-13, M-14 | **Establish and record the BSE SENSEX provider coverage — SENSEX stays in scope.** Three claims must be kept apart: (i) FYERS **documents BSE Equity Derivatives support** (Exchange-Segment Mapping: BSE \| Equity Derivatives \| exchange 12 \| segment 11) **[FY-7]**; (ii) FYERS **documents a futures-chain example using `BSE:SENSEX-INDEX`** **[FY-5]**; (iii) the specific in-scope SENSEX **option-chain, quote and depth** requests have **not** been verified against FYERS. Until (iii) is established, the **SENSEX portion of the FYERS chain/quote/depth calls and the coverage that depends on them are CONDITIONAL**, and the coverage state must report SENSEX as a **single-provider dependency** rather than as complete. The work item must: add the FYERS `BSE_FO` master to the sync list (`app/instruments.py:34-37`), verify per-symbol FYERS responses for SENSEX, and record the result. **M-14 (the optional FYERS futures-chain *currently-trading* check, §3.12.4b) is OPTIONAL and gates nothing** — it is not required by the product goal, it changes no capacity figure, and W-26 must not wait for it. | (a) the SENSEX option/quote/depth requests are either verified against FYERS — in which case the conditional flag is cleared and the budget is confirmed — **or** recorded as a single-provider dependency in the decision record and reported by the API; (b) a catalogue-sync test asserts that every **in-scope segment** has more than one provider, or an explicit exception is recorded; (c) the BSE inventory (SENSEX: **19** chain cells, **3 026** option contracts, **2** nearest-two futures) is measured and asserted; (d) the request budget records that SENSEX currently needs **no** FYERS calls (all 19 cells are Upstox-only), and that adding the FYERS `BSE_FO` master would create FYERS-only SENSEX symbols whose class-C share must be re-measured. | W-01, W-24 | **M-13**. **M-14 is optional and is not a dependency of this item.** |
| **W-27** | S2 | R-32, **D-26** | **Bind the index poller to the exchange session instead of the wall clock.** Replace `is_market_hours()`'s hard-coded 09:15–15:30 IST window with a **provider-supplied, per-exchange, per-date** session: `/v2/market/status/{exchange}` for the live state **[UPX-8]**, `/v2/market/timings/{date}` for the window **[UPX-9]**, `/v2/market/holidays` for the calendar and **special timings** **[UPX-10]**. The poller must (i) **not** request when the relevant exchange is closed — weekends, holidays and outside the session; (ii) use the **per-exchange** window (NSE vs BSE) and the current **15:40** F&O close **[NSE-1]**; (iii) start **only when the fallback is actually needed** (Upstox unavailable), not unconditionally; and (iv) apply a **bounded retry** (backoff and a daily retry ceiling) instead of retrying every 30 s. **No concrete reason exists for polling outside market hours** — the last in-session value *is* the session close (§3.17.3). | (a) The poller issues **0 requests** on a weekend, an exchange holiday and outside the session; (b) it honours the **provider-supplied** per-exchange window, including **special timings**, and never a hard-coded clock; (c) it starts **only** when the primary index source is unavailable; (d) a failing provider produces a **bounded** retry pattern, and the worst case is **measured and reported** rather than unbounded; (e) the **measured** daily consumption (≈75/gated day) is asserted by a test and reported in diagnostics, replacing the plan's historical 288. | W-24 (the budget it feeds), W-15 (the shared pool) | none — the requirement is stated (**D-26**); the behaviour is a **defect fix** (R-32) |
| **W-28** | S2 | R-33, **D-26** | **Make the FYERS index path a genuine fallback, driven by the primary feed's health.** The poller must be **idle whenever the primary is healthy** and must consult the primary's published state — `brokers/upstox/feed.py` `status()`: `state` (`streaming` · `reconnecting` · `failed` · `auth_required` · …) and **`last_message_at`** — rather than running in parallel. The lifecycle to implement (§3.17.6): **PRIMARY_HEALTHY → 0 requests**; **PRIMARY_UNHEALTHY → 0 requests** until the activation conditions are met; **FALLBACK_ACTIVE** when the symbol's own exchange session is open **and** the primary has been continuously unhealthy for **≥ 60 s** **and** the primary has not been continuously healthy for the last **15 min**; **FALLBACK_COOLDOWN** after recovery, with the fallback's cached values left in place (they are the last-session values, which is what the read path wants). The **stale threshold is 600 s** — twice the poller's own freshness — and is deliberately **not** the UI's display threshold (`market_indices.STALE_MINUTES = 5`). Session eligibility is **exchange-specific and provider-driven** (7 NSE + 2 BSE symbols ⇒ two windows from `/v2/market/timings/{date}`, `/v2/market/holidays`, `/v2/market/status/{exchange}`), never a hard-coded clock. | (a) With the primary healthy the poller issues **0** requests; (b) it arms only on the specified condition and disarms on recovery, with the **60 s / 15 min** hysteresis measured to produce **at most one** arm per 15-minute window; (c) a symbol is polled only when **its own** exchange is open, including **holidays** and **special sessions**; (d) the **600 s** threshold is asserted by a test and is distinct from the display threshold; (e) the failover cost is **reported** as **77 requests per session** in normal failover and **up to 770** under a sustained provider failure, and the **60 s** retry bound is implemented; (f) the coverage boundary is stated: the fallback covers **index quotes only** — the **19 SENSEX chain cells still have no source** while Upstox is down (M-13). | W-27 (the session binding), W-24 (the budget it feeds), W-15 (the shared pool) | none — the requirement is stated (**D-26**); the behaviour is a **defect fix** (R-33) |
| **W-29** | S1 | R-34, **D-26, D-27** | **Replace the full-surface sync with a demand-driven data layer.** (i) **Chain fetch on demand** — the opened cell only: **one** request per (underlying, expiry), the **underlying price taken from the same response** to compute the **true ATM**, and the **±20 window** selected from that ladder and bounded by it. (ii) **Visible-window streaming** — subscribe the window's option contracts on the provider's WebSocket; on underlying/expiry change, **release the no-longer-needed keys and subscribe the new ones**, acknowledging that a change is applied at the **next authorize/re-subscribe cycle**; on **reconnect or a stale cache**, re-fetch **only the active cell** before restoring its live window. (iii) **Narrowly scoped refresh** — if the live feed cannot supply a required field for the active window, refresh **that cell only** on a bounded cadence and report the field state; **never** substitute a universe-wide job. (iv) **Session gating** — outside the applicable exchange session, **stop live refreshes** and **retain the last snapshot labelled with its timestamp and session**, so the UI shows the previous close; the session is provider-supplied, exchange-specific and holiday/special-session aware. (v) **Bound the alert-driven scheduler** — session-gate it and give it an explicit **aggregate cap**. (vi) **Catalogue sync on demand only** — startup, expiry rollover, catalogue change. | (a) Opening an underlying+expiry issues **exactly one** chain request and nothing else; (b) the window is ±20 around the **true** ATM derived from the response's own underlying price, bounded by the ladder; (c) the cache carries **provider, canonical underlying, expiry, session and last-update timestamp**, and the UI can distinguish *live* from *previous close* without another request; (d) a chain change releases the old keys and subscribes the new ones, and a reconnect re-fetches **only** the active cell; (e) **no** request is issued for a cell nobody has opened, and **no** code path fetches all 689 cells; (f) outside the session, **0** market-data REST requests are made; (g) the alert-driven scheduler is session-gated and aggregate-capped; (h) the **live window's key count is sized per provider** from the configured entitlement, with the narrowly scoped refresh as the fallback where the entitlement cannot carry it. | W-24 (the cell inventory it consumes), W-27 / W-28 (the index fallback), W-15 (the shared pool) | none — the requirement is stated (**D-26**, **D-27**); the behaviour is a **design correction** (R-34) |
| **W-30** | S1 | R-35, **D-28** | **Make the alert-driven refresh bounded, session-gated and shared.** (i) **Normalise the registry key** to a canonical **(exchange, underlying, expiry)** so one cell is registered once, however the alert spelled it, and the same cell is never fetched twice per tick. (ii) **Session-gate the tick** against the chain's **own** exchange window, taken from the provider's **date-specific** timings, holidays and **special sessions** — no routine live-price chain refresh outside that window. (iii) **Share one cached chain per (canonical underlying, expiry, provider)** between the visible-chain path and the alert path, with **single-flight** and a labelled snapshot (provider, underlying, expiry, **session**, last-update timestamp), so opening a cell an alert already maintains costs **no** request. (iv) **Admit every request through a shared per-provider rate and daily budget** — analytics, visible-chain refreshes, reconnect recovery, index polling and retries all draw on it; a request that does not fit is **deferred**, not dropped. (v) **Bound the retries** with backoff **and** budget admission, plus a per-chain failure ceiling after which the chain is **unavailable** rather than retried indefinitely. (vi) **Preserve alert correctness**: a chain that is deferred, stale or unavailable evaluates **UNKNOWN** and **cannot fire**; live-move alerts are suspended outside the session and re-arm on the first fresh snapshot, while **session-close** metrics are evaluated once against the labelled close snapshot. | (a) A cell is fetched **once** per tick regardless of how many alerts reference it, and the two key spellings cannot produce two entries; (b) **0** live-price chain refreshes are issued outside the chain's exchange session, including on holidays and under special timings; (c) opening a cell an alert already maintains issues **no** new request; (d) every market-data request in the process passes the **same** per-provider admission, and a deferred request is reported as deferred; (e) the worst case for **N** registered chains is bounded and **asserted** in a test, and the FYERS Standard pool cannot be exceeded; (f) an alert whose chain is deferred, stale or unavailable **never fires** on stale data, and its UNKNOWN state is visible in diagnostics; (g) the **Upstox** side is **not** given a claimed quota — only a configured local admission cap, with the limit labelled **unverified** **[UPX-7]**. | W-29 (the demand-driven data layer), W-27 / W-28 (the index fallback), W-15 (the shared pool) | none — the requirement is stated (**D-26**, **D-27**, **D-28**); the behaviour is a **defect fix** (R-35) |
| **W-16** | S2 | R-15, R-05b, D-9 | Align the FYERS TBT adapter with the **official** entitlement (Standard **1** symbol; Prime **15** = 3 × 5), source the numbers from the official documentation rather than an implementation, and make the **account tier an explicit, verifiable input** rather than an assumption. | (a) The declared TBT limits match the official page, with the URL and date recorded; (b) a Standard-tier configuration cannot subscribe more than 1 TBT symbol and says so; (c) the tier used is read from one place, is marked *unverified* until confirmed, and defaults to the **lower** entitlement; (d) the "Prime override" path in `providers/capacity.py:34-36` is gated on the same tier input. | W-00 (M-8), W-07 | **M-8** |

---

### PHASE 6 — Chain, analytics and provider-consistency correctness

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-17** | S2 | R-14, R-09, D-7 | Make contract identity and provider attribution **consistent end-to-end**: backend selection (one provider per logical contract at the decided granularity) → chain snapshot merge (already provider-filtered) → API payload (provider present) → frontend grouping (must not merge across providers silently) → analytics (must compute over one provider's chain). | (a) A test builds a workspace whose CE and PE at one strike come from different providers and asserts the API and the UI represent that truthfully; (b) the chain snapshot is never merged into a leg owned by another provider; (c) analytics are computed over a single provider's chain, and the scope/provenance marker reflects it; (d) the current catalogue (0 cross-provider strikes) is asserted as a *present* property, not a *guarantee*. | W-05, W-00 (M-6) | **M-6** |
| **W-18** | S3 | — | Harden the REST option-chain path: expiry fallback when the requested expiry is absent, provider-specific error surfacing, and a bounded retry that respects the FYERS daily budget. | (a) An unavailable expiry yields a controlled response naming the available expiries; (b) a provider error does not degrade into a silent empty chain; (c) retries are bounded and counted. | W-08, W-15 | none |

---

### PHASE 7 — Verification and release readiness

| ID | Sev | Addresses | Work | Acceptance | Depends | Approval |
|---|---|---|---|---|---|---|
| **W-19** | S1 | R-01, R-02 | **Build the missing integration harness** - the single most important test in this plan. It must combine the **real** `FnoCoverageService`, the **real** `SubscriptionService`, **all six owners**, provider **routing**, **capacity allocation**, **de-duplication** and **overflow**, against a **realistic catalogue** (the live catalogue, read-only, via a fixture or a DB copy - never the runtime DB). **V7:** the harness itself is **not** blocked by M-1 - it reproduces today's failure immediately; only the *scope-specific expectations* are added once M-1 is recorded. | (a) The harness reproduces today's failure (pre-fix) and passes post-fix; (b) it asserts the resolved set per provider, the applied set, the dropped set and the `CAPACITY_LIMITED` set, using the **exact** owner figures of §3.7.1 and the **post-fix** expectations of §3.7.2b - **not** the current-state counts, which a planned fix changes (identity, de-duplication, allocation); (c) it covers overlapping and non-overlapping owner contributions, including the measured overlaps 1n6=7, 3n6=1, 4n6=1, 3n4=4; (d) it exercises the **routing sequence** of §6.3 - eligibility filtering, one-provider-per-contract, capacity-aware choice with preference as tie-break, single de-duplication, and the P-5 overflow path; (e) it asserts the **capacity-first** result: the whole desired set is described by the planner, **0 dropped** where the usable capacity permits, and the P-5 path is exercised separately with a deliberately reduced capacity; (f) it never writes to the runtime data directory. | W-01, W-08 | none - the scope-specific expectations arrive with M-1 |
| **W-20** | S1 | every R-* | Per-defect unit and regression tests (§8.2) — **one per root cause**, each failing before the fix. **V9 additions:** the capacity-model tests (reserve at 2 000 and at 100; usable-capacity derivation; the *no enforced limit is tighter than any provider's usable capacity* invariant; the routing sequence of §6.3; and the capacity-first result of **0 dropped**). | Every R-01…R-24 has at least one test that fails on the current tree and passes after its work item, and the capacity model has its own failing-before tests. | each work item | none |
| **W-21** | S2 | baseline discipline | **Baseline discipline**: record, before the first fix, a machine-readable baseline of the test suite (counts + the exact failing test IDs, with the environment noted). A failure may be labelled *pre-existing* **only** if it appears in that baseline **and** its cause is demonstrated to be outside the F&O change set. | (a) A dated baseline file lists every failing test ID; (b) post-fix runs are diffed against it; (c) the two failures reproduced during this study are correctly attributed — `test_x_twitter.py::test_xt2_string_operators` (cause outside F&O: `core/alerts.py` `SUPPORTED_OPERATORS` is identical at HEAD) and `test_previous_oi.py::test_schema_is_v22_with_table` (cause outside F&O: `SCHEMA_VERSION = 23` at HEAD, bumped by the X/Twitter step — the F&O work added a table without a version bump, so the *test* is stale but the *cause* is not the F&O work). | W-01 | none |
| **W-22** | S1 | R-10's S1 acceptance, R-20 | **Clean-checkout and startup readiness**: a clean clone imports, the architecture-boundary test is green, and the app starts with the F&O subsystem enabled and reports its coverage state truthfully (planned vs routed vs subscribed, and any `CAPACITY_LIMITED` set). | (a) Clean-clone import succeeds; (b) startup reaches a *truthful* coverage state; (c) the runtime configuration used is captured in the evidence artifact (the git-ignored `config.json` drift, R-20, must not silently change behaviour between runs). | W-01, W-11, W-19 | none |
| **W-23** | S1 | R-12, R-10's G2/G3 | **Re-run live validation** against the exact integrated version, producing a fresh evidence artifact (replacing, not editing, the 2026-10-20 artifact), and re-evaluate G1/G2/G3. | (a) The new artifact states the exact commit/tree it describes; (b) the streamed set and the coverage state match the offline plan; (c) G1 (offline) is met, G2's live items are either passed with evidence or still explicitly blocked, G3 is not claimed unless all gates are met. | W-19, W-20, W-22, and an authenticated session | Maintainer must supply the session; **no probe may be performed without it** |

---

## 8. Per-stage acceptance criteria and regression coverage

### 8.1 Validation matrix (mapped to the required test list)

| Required validation | Work item | Test type | Fixture / realism | Notes |
|---|---|---|---|---|
| Provider subscription capacity under each mode/entitlement | W-07 | unit | declared capabilities | The **seven concepts** of §3.5a are each asserted separately: documented limit · account entitlement (flagged *unverified*) · safety headroom · **usable provider capacity** · desired set · resolved union · applied/dropped sets. Upstox `full` vs `ltpc`; FYERS Standard vs Prime; the reserve at both a large (2 000 → 1 800) and a small (100 → 90) entitlement |
| Multiple owners, overlapping and non-overlapping | W-19 | **integration** | real planner + real resolver + real catalogue | The specific combination that failed; **all six owners** accounted for, with the **exact** owner figures of §3.7 (9/9 · 0/0 · 13/4 · 3/22 or 44/0 · 0/0 · scenario-dependent) and the **measured overlaps** ①∩⑥=7, ③∩⑥=1, ④∩⑥=1, ③∩④=4 |
| Deduplication without losing provider attribution | W-05, W-17 | unit + integration | real catalogue | Overlap across owners; per-provider attribution preserved |
| Capacity overflow and explicit `CAPACITY_LIMITED` | W-08 | integration | real planner + real resolver | Over-budget ⇒ **no exception**, dropped set reported, and the bound enforced is **`usable_provider_capacity`** (Upstox 1 800 · FYERS 90) — **not** an application-wide constant. The P-5 drop class is applied and the missing coverage exposed |
| Priority preservation (index spot, active view) | W-08, W-09 | integration | real planner | Spot and active-view keys are never the dropped class |
| Full-universe vs active-view streaming scope | W-09 | integration | real catalogue | **M-1 is closed**; the scope is the settled one (six NSE indices + SENSEX, the NSE F&O stock universe, all contracts including option chains and futures, BANKEX excluded). The assertions use the **settled** post-fix counts of §3.13: desired **659**, applied upstox **623** / fyers **84**, **0 dropped**; the Scope-S2 figures (**25–62** / **13–35**) are retained as history only. The test names the scope it assumes. |
| Nearest-two futures per underlying/expiry | W-09 | unit + integration | real catalogue | Per (exchange, underlying) — BSE and NSE expiries must not cross |
| All approved index mappings resolve to real catalogue instruments | W-03 | unit | real catalogue | Fails today for NIFTYFPI (2 keys, 0 rows) |
| No accidental universe expansion | W-04 | unit | registry | Pinned F&O index set; BANKEX absent unless M-4 says otherwise |
| Unavailable-provider routing and failover | W-06 | unit | injected status | `PROVIDER_UNAVAILABLE`; failover to a capable available provider |
| Startup restore, repeated reconcile, release, unsubscribe | W-11 | integration | real service | Idempotence; **no poisoning**; release restores usability |
| REST option-chain failures and expiry fallback | W-18 | unit | injected transport | No silent empty chain; bounded retries |
| No mixed-provider option rows misrepresenting a chain | W-17 | unit + integration | **synthetic mixed-provider fixture** | The live catalogue has 0 such rows — the fixture must manufacture one |
| Provider attribution and errors in the user-visible UI | W-13 | frontend | vitest | The apply error renders; a mixed row is labelled |
| Previous-OI persistence fed by a production path | W-14 | integration | real service + recorder | Live quote → persisted row → `basis == persisted` |
| Clean-checkout import and startup readiness | W-22 | CI | clean clone | No `ModuleNotFoundError`; truthful startup state |
| Analytics correctness and scope/provenance | W-17 | unit | snapshot fixtures | put÷call; scope tiers; `bounded` flag; single-provider provenance |
| Regressions in stock / index / MCP / subscription paths | W-20, W-21 | regression | full suite vs baseline | Machine-readable baseline diff |

### 8.2 Root-cause → test mapping (each test fails before its fix)

| Root cause | Minimum failing-before test |
|---|---|
| R-01 two capacity policies | Integration: real planner + real resolver + 6 owners ⇒ today raises; after W-08 ⇒ returns `CAPACITY_LIMITED` and enforces **`usable_provider_capacity`** (Upstox 1 800 · FYERS 90): **0 dropped** under the capacity-first design — **post-fix** expectations, not the current counts |
| R-02 scope (M-1 closed) | Integration: the planner **describes** the whole settled desired set (**659**) and the resolver fits it — applied upstox **623** / fyers **84**, **0 dropped** (§3.13). The Scope-S2 figures (**25–62** / **13–35**) are historical only. The test names its scenario. |
| R-03 dead import | Route test: over-budget `POST /api/fno/apply` ⇒ today `ImportError`; after W-02 ⇒ 4xx |
| R-04 startup wedged state | Integration: after a failed restore, a later `resolve()` does not raise, and the service is usable without a restart |
| R-05a FYERS limit scope wording | Unit: the FYERS declaration states the **per-user, across-all-connections** scope, not "per connection" |
| R-05b unverified account tier | Unit: the account tier is an explicit input, marked *unverified* until confirmed, and every derived number carries that status; the Prime override is gated on it |
| R-06 reserve floor | Unit: the reserve rule is explicit about its behaviour at small entitlements, and the small-capacity case is pinned (today: a 100-symbol entitlement yields a 50 % reserve — the artefact under review) |
| R-07 inert recorder | Integration: a live futures quote reaches the recorder and persists a row |
| R-08 unbudgeted REST pool | Unit: the poller's daily consumption is projected and bounded; a counter exists |
| R-09 divergent inference | Unit: one implementation; the coordinator's preference equals the registry's |
| R-10 NIFTYFPI keys | Unit: every emittable index key exists in the catalogue |
| R-11 unavailable-provider routing | Unit: an unavailable provider yields `PROVIDER_UNAVAILABLE` and failover |
| R-12 stale evidence | Release: the evidence artifact names the exact tree it describes |
| R-13 error-contract drift | Table-driven: the same condition ⇒ the same status + body on all three routes |
| R-14 frontend strike grouping | Frontend: a mixed-provider strike row is labelled, not merged |
| R-15 TBT Prime assumption | Unit: the declared TBT limits match the official page; Standard ⇒ 1 symbol |
| R-16 poller cadence | Unit: the poller honours its declared minimum interval |
| R-17 poller budget | Unit: a request counter is exposed and bounded |
| R-18 stale assertion | The existing assertion, updated to the real version (the plan explicitly permits fixing stale assertions) |
| R-19 mode mismatch | Unit: the effective mode is read from one place and drives the entitlement lookup |
| R-20 config drift | Release: the runtime configuration is captured with the evidence artifact |
| R-21 BANKEX / docstring | Unit: the F&O index set is pinned; docstring and flags agree |
| R-22 log semantics | Unit: the log/diagnostics distinguish planned vs routed vs subscribed |
| R-23 unenforced provider batch limit | Unit: `FyersRest.quotes()` rejects or chunks a request above the documented 50-symbol batch limit, and the failure is not silently swallowed |
| R-24 contradictory scope decisions in the record | Decision record: M-1's entry names which of P-1…P-6 it resolves and states whether it amends or confirms the earlier position; a dated decision entry exists |
| **capacity model** (V9) | Unit: the reserve yields usable **1 800** at entitlement 2 000 and **90** at entitlement 100 (and **50** under the old floor, so the change is visible); **no application-wide constant appears in the enforced path**; the invariant *no enforced limit is tighter than any provider's usable capacity* holds; the account tier is an explicit, *unverified* input defaulting to the lower tier |
| **routing sequence** (V9) | Integration: an ineligible provider is never chosen; each contract has exactly one owner; the choice follows remaining usable capacity with preference as tie-break only; the union is de-duplicated once; the P-5 path is exercised with a deliberately reduced capacity and reports `CAPACITY_LIMITED` |
| **capacity-first coverage** (V13) | Integration: the planner describes the **settled** desired set (**659**) and the resolver applies **all** of them with **0 dropped** — union upstox **623** / fyers **84** (§3.13) — both providers retaining failover headroom. Asserted against the **post-fix** expectations, never against current-state counts |
| **complete data surface** (V11) | Integration: all **689** (underlying, expiry) chain cells - **670 NSE + 19 SENSEX** - are reachable and each reports its completeness state (complete / complete-stale / provider-truncated / provider-missing / field-unavailable / unavailable); a full pass costs **220** contract-list + **689** chain calls; the tail is served from cache after one sweep, not per request; a degraded provider degrades the tail and never the active workspace |
| **field-level completeness** (V13) | Integration: every in-scope contract resolves **per field** against the endpoint that documents it - price/volume/bid-ask, current OI, previous OI, IV, Greeks - and the **2 969** strikes carrying **5 934** option symbols with no documented IV/Greeks source report `field-unavailable` for exactly those two fields while the rest remain present; the **current OI** of those strikes is **probed** (via Upstox `/v2/market/oi`, already implemented) and reported `field-unestablished` until established; a **conditional** field (FYERS depth OI, present only when `oiflag` is true) is distinguished from an absent one; a partial field set is never labelled a complete chain |
| **request budget** (V16) | Unit/integration: the sweep's total call count and its apportionment against the documented pools, **including** the existing index-poller consumption, retries and refreshes, stay within budget. Under the **±20 ATM scope (D-21)** the required surface is **670** FYERS chain calls + **19** Upstox chain calls per sweep, **0** quotes and **0** depth calls; the FYERS index poller is **conditional** — **0** in normal operation, **77 per session** in failover, up to **770** under a sustained provider failure (§3.17.7) — so **5 000** is available for chain sweeps in normal operation ⇒ **≈ 7.46 sweeps/day**, spent **inside the ≈ 385-minute market session** ⇒ **≈ once an hour while the market is open**, and **nothing outside it** (670 requests per sweep, **13.4 min** at 50 rpm) — a **theoretical maximum, not a guaranteed SLA**, because the other pool consumers, retries and runtime behaviour are unmeasured; and Upstox uses **21** calls per sweep (**39** with the optional redundancy sweep) against an **assumed** bucket, in a **separate** provider budget. The deep-ladder tail is **out of scope** (D-25); its re-entry cost is **7 011** FYERS calls per sweep, at which point the daily pool **does not fit**. The per-tier refresh cadence is asserted **per tier**, never as a global daily claim; **every Upstox-side rate figure used by the implementation is labelled assumption-based** where the documented bucket does not cover market data |
| **Upstox OI route probe** (V13) | Integration: the per-strike OI returned by `/v2/market/oi` for a cell whose strikes are outside Upstox's instrument master is recorded either way, and the field state for those contracts follows the result |
| **BSE coverage** (V11) | Unit/integration: BSE F&O contracts are present for **both** providers, or the single-provider dependency is recorded and reported by the API; the SENSEX inventory (**19** chain cells, **3 026** option contracts, **2** nearest-two futures) is measured and asserted; every in-scope segment has more than one provider, or an explicit exception exists |

---

## 9. Approval and maintainer-decision gates

| Gate | Content | Owner | Blocking |
|---|---|---|---|
| **G-D** | The **open** decisions recorded: **M-2, M-5, M-6, M-7, M-8, M-9, M-9b, M-10, M-11, M-13** (§2.3). **M-12 is a re-entry condition only** — its subject, the IV/Greeks gap on the deep wings, is out of scope (D-25). **M-1, M-4, M-15 and M-16 are CLOSED** (M-1/M-4 by the maintainer's requirement of 2026-10-20; **M-15/M-16 by the decisions of 2026-10-21 — D-23 and D-24**) by the maintainer's requirement of 2026-10-20 and are **recorded as closed decisions, not re-opened** — they confirm P-1/P-2 (the full universe's spot and nearest two futures, option surface on demand) and exclude BANKEX. **M-3 is not a design gate** — the overflow policy (P-5) is already approved; if separate implementation authorisation is wanted it is recorded as an authorisation, not a decision | Maintainer | **All coding** |
| **G-S** | **SATISFIED — 2026-10-20 (M-1 and M-4 closed).** The maintainer's requirement statement resolves the streaming scope: the coverage is the full F&O universe (NSE major indices + NSE F&O stocks + BSE SENSEX, all contracts including option chains and futures) and the index universe is the six NSE indices + SENSEX, BANKEX out. The *delivery* split (streamed vs on-demand) is §3.11. The overflow policy is **not** re-opened (P-5, R3:178 / R4:118 / R5:100 / R6:181 / R7:139 / R8:132). | Maintainer (satisfied) | none — W-09 and W-24 proceed on the stated scope |
| **G-T** | The **account's plan tier** confirmed for each provider — or, if it cannot be confirmed without a session, explicitly recorded as **UNVERIFIED** with the **lower** tier assumed as the planning default and the consequence stated. This is R-05b and it is *not* satisfied by the tier being documented; only the account's own tier satisfies it. | Maintainer (session) or Maintainer (written confirmation) | W-07, W-16, and any capacity conclusion that assumes a tier — concretely: the **FYERS** usable capacity (90 Standard vs 4 500 Prime), the FYERS headroom, and therefore whether the FYERS side of the desired set can be streamed without dropping. **The Upstox side does not depend on it — *assumed* so (C7: `full` is not
restated under Plus, so tier-independence is an assumption [A], §3.14)**. |
| **G-01** | W-01 change-set integration executed and the F&O diff isolated | Maintainer (Git operation) | All acceptance testing |
| **G-02** | W-02…W-21 and **W-24…W-26** complete, each with its failing-before test. **W-21 (the baseline) must be captured *before* the first fix** and diffed against every later run | Maintainer (review) | Release readiness |
| **G-03** | W-22 clean-checkout + startup readiness | Maintainer | Release readiness |
| **G-04** | W-23 live re-validation evidence recorded for the exact integrated tree | Maintainer (session) + auditor | **G3 release gate** |
| **G-05** | R10's G1 (offline regression green), G2 (live evidence), G3 (all acceptance gates) re-evaluated | Maintainer | Release |

**Protected-core boundaries restated for this plan:** no work item modifies
`app/auth/*`, credential persistence, session restoration, application startup
composition semantics, the persistence schema, or the **quote-store write path**
(`market/service.py`). W-13 explicitly must not re-key the store; S15 (canonical
write-path re-key) remains deferred as a separate protected-core task. W-22
touches startup only to verify truthful reporting, not to change composition
semantics.

**Explicitly excluded from this plan** (and why): X/Twitter, Yahoo, news MCP
tools, futures heatmap, MCP registry, `api/product_routes.py` non-F&O hunks,
and any change to `app/auth/*`. The only reason any of these appear is W-01
(separation) and W-21 (baseline attribution).

**Added in V10 (the complete data surface), and what it does *not* touch.**
W-24/W-25/W-26 add a REST-backed, cached, budgeted *option-chain* surface. They
do **not** add WebSocket option keys (that stays prohibited, D-13), do not
change the streamed surface, do not re-key the quote store, and do not change
any authentication, credential or startup behaviour. The only write path they
add is the existing catalogue (via the instrument-master sync) and the existing
quote-store read path.

---

## 10. Verification and release-readiness strategy

### 10.1 Baseline discipline (W-21)

1. Before the first fix, run the suite once with `-p no:cacheprovider` and
   `PYTHONDONTWRITEBYTECODE=1`, capture the **failing test IDs** (not just the
   count), and store them with the environment (Python version, commit, working
   tree state) as a dated baseline artifact.
2. Every subsequent run is diffed against that baseline.
3. A failure is *pre-existing* only if (a) it is in the baseline **and** (b) its
   cause is demonstrated to lie outside the F&O change set. Two such
   demonstrations already exist and are recorded in §8.2 (R-18 and the
   `test_x_twitter` case). All other failures are **new** until proven otherwise.
4. This study's own observation: the targeted F&O test set
   (`test_fno_read_paths`, `test_fno_coverage`, `test_fno_lifecycle`,
   `test_fno_routing_acceptance`, `test_fno_startup_restore`,
   `test_provider_capacity`, `test_canonical_identity`, `test_option_chain_routing`,
   `test_architecture_boundaries`) passes **85/85** on the current tree — which
   is exactly why the defect escaped: the tests exercise each component
   *separately* and never the **interaction** that fails. The W-19 integration
   harness is the direct answer.

### 10.2 Release-readiness gates (re-derived from R10 §5.4)

| Gate | Definition | State today | Target after this plan |
|---|---|---|---|
| **G1** offline implementation readiness | source assumptions resolved **and** the applicable offline regression tests executed green | **Not met** — the interaction tests do not exist; G1's analytics tests were never executed as a gate | Met after W-19/W-20 |
| **G2** live-validation readiness | an authenticated session and open market hours, with read-only GETs | **Blocked** — no Upstox session; the FYERS app credentials are rejected (403 / 1010) | Requires maintainer-provided session; re-run under W-23 |
| **G3** release readiness | all acceptance gates pass, plus a clean checkout and no request storm | **Not met** | After W-22, W-23 |

### 10.3 What this plan deliberately does not claim

* It does not claim the application ceiling is *wrong* — it claims the
  ceiling is **redundant**, and that retiring it is the recommended direction
  (§3.5b), subject to M-2. The maintainer's 800 raise is recorded as
  **historical decision context**, not as a current instruction, and not as the
  architecture.
* It does not claim **full coverage** from the current tree. The capacity-first
  result (**0 dropped**) is a **projection** of §3.7.2c / §3.10, and it
  assumes three things that are not yet true: the planner stops pre-truncating,
  the resolver routes capacity-aware, and the entitlement is as documented
  (**unverified** for FYERS).
* It does not claim the **reserve floor** should be 2 as a settled fact — that
  is a **recommendation** with the quantified coverage effect stated
  (§3.6), and the final value is **M-5**.
* It does not claim a specific new cap value — that is M-2, whose default
  presumption is that 500 **stands**.
* It does not claim the account's **plan tier** is known for either broker —
   both are **UNVERIFIED**, and every FYERS figure in this document is
   explicitly conditional on the Standard tier (§3.5 quantity 2, §3.7).
* It does not claim the **limit scope** ("per user across all connections")
   is ambiguous for FYERS: the official documentation states it, and
   FYERS' own `fyers-skills` repository contradicts it — the documentation
   page governs (§3.3, U-3). The endpoint↔Data-Socket mapping is **resolved**
   in V6; what remains open is the account's tier, not the limit.
* It does not claim any **band** in §3.7/§3.8 is "the approved requirement".
   Each is conditional on an unresolved decision (M-1, M-4), an unverified
   fact (the account tier) and two user-configurable owners.
* It does not claim the **reserve formula** should be changed. V6 classifies it
   (§3.6): the floor is deliberate; its percentage effect at small
   entitlements is an artefact; the remedy belongs to M-5.
* It does not claim the previous agent's work was unauthorised — only that
  approval cannot be evidenced from the repository.
* **It does not claim any defect is fixed.** Every item is a proposal.

---

## 11. Iteration / revision history

| Version | Pass | What changed | Why |
|---|---|---|---|
| **V1** | 1 — evidence-backed plan | Full plan: decision ledger (§2), official capacity matrix (§3), change inventory (§4), root-cause register (§5), dependency map (§6), 20 work items in 8 phases (§7), validation matrix (§8), gates (§9), verification strategy (§10). | First synthesis of repository evidence + official documentation. |
| **V2** | 2 — adversarial architecture review | (a) Added **R-05**: the FYERS capacity declaration is sourced from the **Data Socket** while the adapter opens the **HSM** socket, and the code comment mis-states a per-user limit as per-connection — an unsupported-limit class the prior audits missed. (b) Added **R-06**: the reserve floor `max(10 %, 50)` halves a 100-symbol entitlement, so the approved ≈ 52-key scope does not fit the 50-key FYERS budget — the prior audits accepted this arithmetic without questioning the floor. (c) Added **R-19**: the registry's `DEFAULT_MODE="full"` disagrees with the feed's own `ltpc` default, and the *combined* Upstox limit (1 500) is unmodelled. (d) Added **R-20**: `config.json` is git-ignored and drifted, so the live evidence is not reproducible from the repository. (e) Added the Upstox **REST 500-per-request** fact and showed the app ceiling of 500 is a *different* constraint that merely shares the number. (f) Reordered W-10 to be **derived** from W-07/W-09 rather than chosen independently. (g) Split the cap question into M-2 (value) and made M-3 (overflow contract) the gating decision. | V1 treated the app ceiling as a value to be re-chosen and the provider budgets as settled. Both assumptions failed the review: the ceiling must be an output of scope+budget, and one provider budget rests on an unverified socket. |
| **V3** | 3 — independent completeness review (provider/API · architect · QA · release owner) | (a) **Provider/API**: added the FYERS *Quotes API* batch limit (Standard 50 symbols/request) and the **shared 5 000/day** pool with the "blocked for the day after 3 per-minute breaches" rule → became **R-08** and **W-15**. (b) **Provider/API**: added the FYERS **TBT** entitlement (Standard **1** symbol) against the adapter's Prime assumption → **R-15**, **W-16**. (c) **Architect**: made the coverage planner *consume* the resolver's accounting rather than re-derive it (W-08 acceptance (d)) — V2 still permitted two planners. (d) **Architect**: added the "unknown status → eligible but never counted as live" rule to W-06. (e) **QA**: added the requirement that the mixed-provider test use a **synthetic fixture**, because the live catalogue contains zero such rows and would give false confidence. (f) **Release owner**: added the separation of the mixed-scope edits into W-01 and the protected-core restatement in §9. | The completeness pass found that V2's "provider capacity" section covered only the *stream* path, not the REST pool or the TBT socket, and that V2's W-09 still allowed two independent planners. |
| **V4** | 4 — dependency and failure-path audit | (a) Added §3.5 — the **decisive capacity calculation** (approved scope ≈ 52 keys vs 500 ceiling vs 100 entitlement) and the conclusion that the capacity problem is *not* a cap-value problem; this reframes the whole plan and is the explicit answer to "do not automatically increase it to 800". (b) Added **R-22** (log semantics: planned vs routed vs subscribed) and the corresponding acceptance criterion in W-11. (c) Added the **failure-path walk** of every phase: W-01 must precede every test; W-03→W-05→W-06 precede the capacity cluster; W-08 must precede W-11; W-17 must precede W-13's attribution requirement. (d) Added the explicit "no work item may re-key the quote store" clause to W-13 and §9. (e) Added the **approval-gate table G-D…G-05** and the §9 protected-core restatement. (f) Added the "what this plan deliberately does not claim" subsection (§10.3) to prevent a proposal being read as a result. (g) Corrected the §1.1 phrasing so the failure is described as *measured*, and added the R-12 → W-23 link so the stale-evidence defect has an owner. | The failure-path walk showed V3 had no ordering rationale for W-10, no owner for R-12/R-20/R-22, and no gate table binding the phases to the release decision. |
| **V5** | 5 — final citation-and-claim verification pass | (a) Corrected the R-08 citation: the 45 s figure belongs to `app/previous_oi.py` (the previous-OI flush), not to the FYERS index source; the index source's real bounds are `MAX_RPS=5`, `MIN_INTERVAL_SECONDS=60`, `FRESHNESS_SECONDS=300` (`app/fyers_index_source.py:31-33`). (b) Restated R-08's impact: the *current* poller consumption is modest (one batched request per 300 s window ≈ 288/day for 7 symbols), so the defect is a **missing guard**, not a present outage — the earlier wording overstated it. (c) Corrected the R-01 citation to name the two real drop sites (`app/fno_coverage.py:527-529` spot, `:593-595` futures) and the budget check (`:515-517`). (d) **Added R-23**: `FyersRest.quotes()` documents *"up to 50 symbols"* — the official Standard Quotes-API batch limit — but neither validates nor chunks; the limit currently holds only because the sole production caller passes the 7–9 index symbols. This is the same class as R-05 (a documented provider limit with no enforcement at the adapter boundary) and is now owned by W-15 with its own failing-before test. (e) Header version and the root-cause count updated 22 → 23. | A verification pass over every file:line citation in §5 found two mis-citations and one overstated impact statement, and re-reading the FYERS Quotes contract surfaced R-23, which none of the four earlier passes had found. |
| **V6** | 6 — API re-verification, capacity re-derivation, and a five-perspective adversarial review | **(a) Broker API / entitlement.** V5's R-05 was **mis-sourced and is withdrawn**: the endpoint `wss://socket.fyers.in/hsm/v1-5/prod` **is** the FYERS v3 Data Socket (official sample code uses `data_ws.FyersDataSocket`; the reference SDK connects to that URL calling it *"the documented Fyers V3 binary protocol"*; MarketHub implements that protocol's `sf`/`if`/`dp` types and `hsm_key` handshake). The declared **100** is therefore correct and applies. Replaced by **R-05a** (the code comment mis-states the scope as "per connection" — official: *per user across all connections*) and **R-05b** (the 100 is a plan-tier default, **not** a confirmed entitlement). Three residuals typed: **U-1** the docs page does not print the URL; **U-2** the reference SDK's changelog still says 200 symbols; **U-3** FYERS' own `fyers-skills` repo contradicts the docs page's scope wording — the docs page governs. **(b) Capacity / lifecycle.** §3.5 replaces V5's five concepts with **seven named quantities**; §3.6 derives the FYERS **50** and classifies the reserve (**the floor is deliberate and non-configurable; its 50 % effect at a 100-symbol entitlement is an artefact**); §3.7 replaces V5's erroneous "≈ 52" — which counted only two of six owners — with a **per-owner band** (upstox ≈ 22–64, fyers ≈ 35–43); §3.8 costs the three index alternatives separately and shows they differ by **± 1 key per provider**, i.e. **M-4 is not a capacity argument**. A **double-counting hazard** between owners ③ and ⑥ was surfaced (the same index futures resolved twice) — a second, independent reason for W-08's single-union rule. **(c) Identity / routing.** Confirmed the index base owner carries **9** keys including **INDIA VIX**, which is not an F&O underlying, and NIFTYFPI's two non-existent keys (R-10) — so the index owner is not neutral with respect to the universe decision. **(d) Integration / regression.** W-09's acceptance changed from a single number to the **band**; §8.1 updated to match; the harness is required to assert the **union**, not one owner; the synthetic mixed-provider fixture requirement (the live catalogue has 0 such rows) is retained. **(e) Maintainer approval / scope control.** The exit checklist was **overstating**: item 3 marked isolation as resolution, item 8 marked the unresolved universe PASS on the strength of the erroneous "≈ 52", and item 18 said "one declared block" where there are **eleven** decisions. All three corrected — item 18 is now **BLOCKED**, not PASS. The "nine decisions" count in §1.4/§13 was wrong (**eleven**: M-1…M-10 plus M-9b). The **account tier** was added as a blocking item in its own right (R-05b) rather than folded into M-5. Confirmed no work item touches protected core. | The five perspectives each found something V5 had wrong: a mis-sourced provider limit, an incomplete union arithmetic, an unclassified reserve rule, a double-counting hazard, and three false PASS verdicts in the exit checklist. Two of V5's own conclusions were overturned rather than defended. |
| **V7** | 7 — authority reconciliation, exact union recomputation, and a six-perspective independent review | **(a) Maintainer-decision authority.** The streaming scope is **not** a settled design choice: `docs/PROJECT_CONTEXT.md` §C (2026-10-19) lists *"F&O universe = spot + nearest two futures expiries"* under **"Settled (approved / locked)"**, and `docs/current.md` §2.1 (2026-09-21) records the F&O foundation as **built and live-verified** with universe coverage/routing; R5 §2/§5 (2026-10-18) says the universe is **REST-on-demand**. **No document records an explicit maintainer amendment** of the earlier position, and R5's own limit rule (prioritise *live futures*) contradicts R5 §5's sentence. **New §2.4 authority table** (P-1…P-6, dated, with document kind and approval evidence). **The scope is therefore BLOCKED (M-1)**, and no final capacity requirement is stated. **(b) Scope of R-02 changed from a proven defect to a CONDITIONAL one** — under Scope-S1 the implementation is scope-conformant and R-01 alone explains the failure. New **R-24** records the contradictory decision record itself. **(c) Union recomputed from exact keys.** V6's bands (22–64 / 35–43) and its §3.8 "± 1 key" claim are **withdrawn**. The six owners were resolved separately against a copy of the live catalogue and de-duplicated with the resolver's real key semantics: ① **9/9** (general index *quote* subscriptions — **not** F&O-gated), ② 0/0, ③ **13/4**, ④ **3/22** (RELIANCE) or **44/0** (NIFTY), ⑤ 0/0, ⑥ scenario-dependent. Union: **Scope-S1 → 516–518/85 (RELIANCE) or 554–556/63 (NIFTY)**; **Scope-S2 → 26/35 or 63/13**. Overlaps ①∩⑥=7, ③∩⑥=1, ④∩⑥=1, ③∩④=4 (NIFTY view) measured. **(d) M-4's capacity effect is ZERO** under Scope-S2 (the F&O index spots are a subset of owner ①) and the union is *pinned by the allocation* under Scope-S1 — so the index universe is a scope-integrity decision, not a sizing one. **(e) Ceiling model corrected.** V6's "derive the ceiling from the smallest allocation" is **withdrawn as incoherent** (it would cap Upstox at 50). New §3.5a states what each limit protects, where it is enforced and how they interact: `effective_limit = min(allocation, ceiling)`; the **allocation binds** (Upstox 500, FYERS 50); the ceiling is an independent backstop and the **approved 500 is preserved**; W-10 is rescoped accordingly. **(f) Identifiers audited.** W-03 no longer depends on M-6 (M-6 is the provider-per-contract granularity; W-03's dependency was a reuse error) — the catalogue's authority over provider identifiers is already settled by D-6/D-8. M-6 is now referenced only by W-13 and W-17. R-04's text was corrected to describe the *actual* wedged state and its recovery path, and a duplicate R-22 created during that edit was removed. **(g) Convergence claim retracted.** V6 asserted convergence; this pass found substantive contradictions, so the claim is withdrawn (see the note below). | The six-perspective review found that **two of V6's own conclusions were wrong** (the scope premise and the ceiling-derivation proposal) and that **four exit-checklist verdicts were false** (items 3, 7, 8, 17). The V5/V6 revision-log rows were also found **out of chronological order**. **(h) Second-order contradictions the revision itself introduced, found by the final review and fixed:** the §2.2 **D-1** ledger row still asserted the R5 scope as *approved* and the code as *violating* it — directly contradicting the new §2.4; it is now restated as a **conflict** with both positions dated. **§3.7.3's overlap figures were wrong** (asserted ①∩⑥=8, ③∩⑥=3, ③∩④=2 against a measured total of 9) — recomputed pairwise: **①∩⑥=7, ③∩⑥=1, ④∩⑥=1, ③∩④=4 (NIFTY view)**, total duplicates 9 (RELIANCE) / 12 (NIFTY). **W-11 and W-19 were made to depend on W-09**, which would have put the startup-atomicity work and the integration harness behind M-1 — both now depend on W-08 only, since the degradation rule is uncontested and the harness reproduces today's failure without a scope. **G-02** now includes W-21 and states that the baseline is captured *before* the first fix. **§3.7.2's scenario totals were quoted as single values** (518/85) when they are ranges across the index alternatives (516–518/85) — corrected in every occurrence. |
| **V8** | 8 — decision-authority reconciliation (the 800 ceiling), four-state recalculation, and a whole-document consistency pass | **(a) The 800 decision.** V7 recorded the maintainer's 800 raise as **"unsupported — no evidence anywhere"**. That was wrong: the **PM context — the maintainer's own conversation of record — is the source**, and it names the raise explicitly while asking to "distinguish this application limit from each broker's documented or configured capacity". V8 therefore **reclassifies 800 from an unsupported premise to a decided value with a missing repository record**: D-2 rewritten, M-2 rescoped to *provenance* (date, approving artefact, whether any environment already runs 800) rather than *whether to change the value*, and **R3–R7 preserved as history** with an amendment proposed to be **added**. The **consequence is recalculated**: at ceiling 800 the Upstox allocation becomes 800 and the measured Upstox union (516–556) **no longer overflows at all**, while the **FYERS allocation stays 50** (`min(50,800)`) and still drops 13–35 keys. **The ceiling raise resolves Upstox and does not touch FYERS.** **(b) Four states.** §3.7.2a/b now separates **1. current**, **2. after W-03**, **3. after the capacity+overflow changes** (and 3′ with the ceiling left at 500), and **4. the Scope-S2 hypothetical**, with every excess, drop and `CAPACITY_LIMITED` count computed. **(c) Stale conclusions removed.** The obsolete 514–556 range (it came from a run in which the derivative rules' option legs were unresolved), the "18–56" excess figure (the floor is 16, not 18), the duplicated 516–518 range, the "the 500 ceiling does not bind under every scope" claim, the "the FYERS 100-symbol entitlement is the binding constraint" claim, and the "the capacity failure is a scope defect, not a cap-value defect" headline are all withdrawn or corrected, with the historical revision rows left intact and explicitly marked as history. **(d) §3.9 added** — the NIFTYFPI and index-scope arithmetic: the two owners' NIFTYFPI keys are *different strings*, so the union currently carries a **dead key**; W-03 removes it (−1 key per provider); and M-4 changes four distinct things, only one of which is a count. **(e) Approval logic.** V7 instructed M-1 to "re-affirm or amend" the drop-by-priority rule, which would have put an **already-approved** requirement back in play; P-5 is approved (R3:178 / R4:118 / R5:100 / R6:181 / R7:139 / R8:132, never withdrawn), so **M-1 decides the scope only** and W-00/G-S no longer re-open it. **(f) Acceptance criteria** in §8 now use **post-fix** values, not the current ones. | This pass overturned **V7's own central premise about the 800 ceiling** — a conclusion the previous three passes had each asserted without questioning — and found five further defects in V7's own arithmetic and approval logic. |
| **V9** | 9 — capacity-first architecture and consistency closure | **(a) The framing is replaced.** V5–V8 spent their effort on whether `MAX_INSTRUMENTS_PER_PROVIDER` should be 500 or 800. V9 asks the prior question instead — *does MarketHub need a separate application ceiling at all?* — and answers it from evidence: **no**. The ceiling is bounded by the provider entitlement, the safety headroom and the provider feed's own mode guard (`brokers/upstox/feed.py:188-191`), and it was the **proximate cause** of the demonstrated failure, because the resolver enforced the ceiling instead of the per-provider allocation (500 FYERS keys against an allocation of 50). **No demonstrated failure mode requires it.** The maintainer's 500→800 raise is recorded as **historical decision context**; the recommendation is to **retire** the ceiling (option A of §3.5b), with the alternative — retain it as a guard that can never bind below the provider's usable capacity — stated and costed. **(b) One model, seven concepts.** §3.5a replaces the ceiling-driven model with `usable_provider_capacity = entitlement − reserve`, over: documented provider limit · account entitlement (UNVERIFIED) · safety headroom · usable capacity · desired contract set · resolved union · applied/dropped sets. **No application-wide constant appears in the enforced path.** **(c) The headroom policy is re-derived, and it turns out to be the real FYERS cost.** The `max(10 %, 50)` floor was written to stop a *tiny* entitlement yielding a *zero* budget, but it halves a 100-symbol entitlement; correcting the floor to its stated purpose (**2**) gives FYERS usable **90** instead of 50 and turns *13–35 keys dropped* into **0**. §3.6 quantifies five policies against coverage and states the rationale for a non-zero reserve (reconnect overlap, counting differences, absorbing a single extra key). **(d) The desired set is measured for the first time** (§3.10): **662** contracts (221 universe spots + 441 nearest-two futures), **0 unroutable**, 650 listable by both providers, 12 by exactly one — against usable capacity 1 800 / 90. The **112** contracts dropped today are therefore a **budget artefact, not a capacity limit**. **(e) Four states, clearly separated** (§3.7.2a/b): current (measured), identity-corrected (measured), capacity-first (**projected**, with the three assumptions that make it a projection stated), and Scope-S2 (hypothetical). The capacity-first state is **not** claimed as a measurement. **(f) Routing before dropping** (§6.3): the sequence identity → eligibility → one provider per contract chosen by *remaining usable capacity* with preference as tie-break only → single de-duplication → P-5 only if needed → never crash, never poison, never report a partial apply as applied. The **P-5 kind-versus-role gap** (it would drop the *active view's* option legs first) is **surfaced as M-11**, not silently redefined. **(g) M-3 is downgraded** from a design gate to an implementation-authorisation matter, because P-5 is already approved; **M-2** is rescoped to *retire or retain* the ceiling; **M-5** becomes the headroom policy; **M-11** is added. **(h) The work plan is re-sequenced to follow the architecture, not the order of changing a constant:** W-10 (ceiling) → W-07 (capacity model) → W-08 (union + routing + overflow) → W-09 (scope) → W-11 → W-12 → W-13, with W-19/W-20/W-22 as the verification chain; W-08/W-10/W-11 explicitly do **not** wait on M-1. **(i) §1, §12 and §13 are rewritten from the corrected body** rather than copied forward, and the exit checklist gains item 19 on the capacity architecture. | This pass replaced the plan's central question rather than refining its answer, and found that the question itself had been wrong for four versions. The 500-vs-800 debate was a proxy for a decision nobody had asked: whether a second, application-wide limit should exist at all. |
| **V10** | 10 — the maintainer's product requirement, the complete F&O data surface, and provider-completeness analysis | **(a) The requirement is now stated** (maintainer, 2026-10-20): *all* F&O data for NSE major indices + NSE F&O stocks + BSE SENSEX, almost all stocks, NIFTY and BANKNIFTY explicitly, all contracts including option chains and futures. **M-1 and M-4 are closed by it**; the working tree's `is_fno=True` for BANKEX is confirmed as an unapproved expansion to be reverted. **(b) New §3.11** decomposes the requirement into three surfaces with three different budgets — the contract universe (instrument masters, no rate limit), the live option surface (REST chains and batched quotes, a shared daily pool) and the streamed surface (entitlement-bounded) — and states the central fact plainly: *all data available is not all data streamed*. **(c) The inventory is measured** for the first time: **219** NSE F&O underlyings with options, **223** with futures, **670** (underlying, expiry) chain cells, **84 816** logical option contracts, **665** futures, **5 126** BSE options; NIFTY 17 expiries / 1 828 strikes, BANKNIFTY 6 / 1 188. **(d) The requirement is shown to be affordable on REST** — a full sweep is **670** chain calls (**13 %** of the FYERS Standard daily pool) plus **219** `option/contract` calls — so the plan's earlier *"the universe is on-demand"* assumption, never sized, is now sized. **(e) Two findings the earlier versions could not see:** **provider asymmetry** (FYERS lists **47 634** contracts Upstox does not, and FYERS' 50-per-side `strikecount` truncates **47 of 670** cells including NIFTY and BANKNIFTY — so neither provider alone is complete, and they fail in opposite directions) and **BSE F&O being Upstox-only** because FYERS *publishes* `BSE_FO_sym_master.json` while `app/instruments.py:34-37` omits the segment. **(f) New work items W-24 (complete data surface: contract list, prioritised cached sweep, shared daily-budget accounting, honest completeness states), W-25 (provider asymmetry, per M-12) and W-26 (BSE coverage, per M-13)**, with new failing-before test rows in §8.2, new root causes R-25/R-26/R-27, and new decisions M-12/M-13. **(g) MarketHub has no `/v2/option/contract` implementation** — the cheapest complete-inventory path (219 calls) is simply not built (R-25). | This pass closed the scope question and immediately exposed that the plan had never sized the thing the scope implies: a 670-cell, 84 816-contract option surface with no sweep, no cache and no request budget. It also revealed that 'use the providers' full capacity' is not a single-provider question — the two providers are individually incomplete in opposite directions. |
| **V11** | 11 — scope reconciliation and complete data-surface verification | **(a) The closed scope is propagated.** M-1 and M-4 are closed by the maintainer's statement of 2026-10-20; every active reference now names the **six NSE indices + SENSEX** with **BANKEX excluded**, and **Scope-S1/Scope-S2 survive only as clearly labelled historical analysis** (§3.7 banner) — they are no longer decision gates and Scope-S2 is not an acceptance target. W-04 is reclassified as *applying* a decided position, and G-S is recorded as **satisfied**. **(b) The target universe is recomputed** for the settled scope: **220** underlyings · **220** spots · **439** nearest-two futures · **659** desired contracts · **689** chain cells (670 NSE + 19 SENSEX) · **87 842** option contracts · **43 973** strikes. **V10's 221 / 441 / 662 / 670 are prior measurements, marked as such**, and the 670-vs-677 discrepancy is **reconciled, not guessed**: 670 was NSE-only; the stock count is 213 (not 214); 635 + 35 + 19 = 689. **(c) Complete data coverage is verified field by field** against the official documentation (§3.12.2) for Upstox option-contracts, option-chain, full-quote and option-greek, and FYERS masters, quotes, option-chain and market-depth. **This overturned V10's tail route**: FYERS `/data/quotes` documents **no `oi`, `prev_oi`, `iv` or `greeks`** — the route is FYERS **depth** (`oi`, `pdoi`), one symbol per request. The residual gap is narrowed and demonstrated: **1 985 strikes (4.5 %)** have **no documented IV/Greeks source**, with every route evaluated and each failure explained (§3.12.3). **(d) The budget is restated as a total** with the existing index-poller consumption included (Upstox ≈ 911 calls/pass ≈ 1.8 min; FYERS ≈ 3 627 + ≈ 288 ≈ 3 915 ≈ 78 % of the 5 000/day Standard pool ⇒ roughly one full refresh per day), and per-tier freshness guarantees are derived. **(e) M-12 is reframed** from 'may missing data be accepted as an equal alternative' to a **narrow trade-off** on the two proven-unavailable fields, and **M-13** keeps SENSEX in scope while deciding how its provider dependency is recorded. **(f) The capacity-first projection is recomputed on the settled scope** (§3.13): desired 659, union **623/84**, **0 dropped** — and **R-30** is added, the risk that **full-universe streaming and Upstox-outage survival are mutually exclusive at the FYERS Standard tier** (623 on Upstox vs a 90-key FYERS allocation). | This pass showed that the previous version's headline numbers were pre-decision measurements presented as current, and that the option-surface route rested on an endpoint field list that does not contain the fields the requirement needs. Both are now corrected against measurement and documentation rather than inference. |
| **V12** | 12 — correction of four defects found by an independent review of V11 | **(1) M-1 was simultaneously closed and open.** §1.2 still called the streaming scope undecided, §2.4 retained the *"no explicit amendment ⇒ BLOCKED"* verdict as a live conclusion, and Phase 0's **W-00** still required M-1 to resolve the scope conflict. **Corrected:** §1.2 and §1.4 now list only the open decisions; §2.4's verdict is explicitly marked as **superseded pre-decision analysis** (retained because it is the reason the question was put to the maintainer) and its consequence list is updated; **W-00** now requires the *open* decisions and records M-1/M-4 as closed. **(2) The field-coverage arithmetic did not reconcile.** §3.12.3 listed 41 988, 3 613 and 1 985 as parallel classes, totalling **47 586** against a stated **43 973** strikes (108 % of the total). The three figures were **nested**: 3 613 = *FYERS-listed but outside FYERS' ±50-strike chain window*, of which **1 628 are still fully served by Upstox's chain** (no documented strike cap), leaving exactly **1 985** without a full-field source. **Corrected** to a two-class, mutually exclusive, exhaustive partition — **41 988 (95.5 %) + 1 985 (4.5 %) = 43 973 (100 %)** — with the intermediate measure retained and explained. **(3) The provider allocation was unexplained.** The capacity-first projection reported **623** Upstox + **84** FYERS = **707** against a **659**-contract desired set, with no account of the 48. **Corrected** by measurement (§3.13.1): **659 + 48 = 707 exactly**, and the 48 decompose as **36 streamed option legs** (owners ③ and ④ — a different instrument class, belonging to the option surface), **10 second-provider index spot keys** (owner ① deliberately subscribes every enabled canonical index on **both** providers, by design), and **2 dead NIFTYFPI keys** (the known **R-10** identity defect, removed by W-03). **No canonical contract is assigned to two providers and no provider key is duplicated** — the excess is reconciled, not explained away. **(4) The approval gate was internally stale.** G-D now lists only the open decisions and records M-1/M-4 as closed; G-S is marked **satisfied**; the Phase-0 decision requirements were rewritten so no closed decision is presented as a design gate. The exit checklist was corrected to state that **V11's own consistency verdict was wrong**, and the four findings are recorded in the plan's own revision history rather than quietly removed. | This pass did not extend the analysis — it corrected it. Three of the four defects were internal inconsistencies in V11's own numbers and identifiers, which is precisely what an independent consistency check is for; the plan's substance was sound but its arithmetic and its gate bookkeeping were not. |
| **V13** | 13 — application of an independent API verification (C1–C8) to the V12 plan | **(a) C1 — the Upstox REST rate bucket was mis-attributed as documented.** The official Rate Limits page defines the bucket as *"Other Standard APIs (holdings, positions, funds, historical candles etc.)"* **[UPX-7]**; market-data endpoints are **not enumerated** and their per-endpoint pages publish no limit. **Every Upstox-side sweep and freshness figure is now labelled an assumption [A]** (§3.5a, §3.10, §3.11.4, §3.12.4), not a documented fact. **(b) C2 — the omitted Upstox `/v2/market/oi` route, and an over-strong OI claim.** `GET /v2/market/oi` **[UPX-6]** returns **per-strike** OI for an underlying + expiry + `date`, with no documented restriction to Upstox-listed instruments — and **MarketHub already implements it** (`app/market_data.py:19`, `market/normalize/upstox_analytics.py:27`, with `/v2/market/change-oi`, `/v2/market/max-pain`, `/v2/market/pcr` alongside). The endpoint is now in the field matrix; the conclusion for the 1 985 strikes is weakened from *"current OI unavailable"* to ***"not established"***, the field state `field-unestablished` was added, and the endpoint is recorded as a **route to be probed** (§3.12.2, §3.12.3, §3.12.5, §5 R-28/R-29, §7 W-25, §8.2). The **IV/Greeks** conclusion is unaffected and stands. **(c) C3 — FYERS depth OI is conditional.** `oiflag` is documented as *"a true or false flag for open-interest data"*, and the doc's own equity example returns `oi: 0, oiflag: false` **[FY-3]**; the matrix now says the OI fields apply *only when `oiflag` is true*, and a `provider-conditional` field state was added (§3.12.2, §3.12.5, §5 R-28, §7 W-25). **(d) C4 — the Upstox option-greeks endpoint carries more than Greeks.** It returns `iv, vega, gamma, theta, delta` **plus `oi, last_price, volume, cp, ltq`**, ≤ 50 keys/request **[UPX-5]** — so it is a second OI source for Upstox-listed contracts; the matrix was completed and W-25 now uses it (§3.12.2, §7 W-25). **(e) C5 — FYERS BSE Equity Derivatives is a documented capability.** The Exchange-Segment Mapping lists **BSE \| Equity Derivatives \| exchange 12 \| segment 11** **[FY-7]**, and the futures-chain documentation's own example uses **`BSE:SENSEX-INDEX`** **[FY-5]**. The single-provider dependency is therefore reclassified as **provisional on a sync omission, not a provider limitation**; M-13 narrows to per-symbol availability and the account's market-data permission, and **SENSEX stays in scope** (§2.3, §3.11.6, §5 R-27, §7 W-26, §12, §13). **(f) C6 — "no strike cap" is now "documents no cap"** **[UPX-3]** at the two load-bearing sites (§3.11.2, §3.12.3). **(g) C7 — Upstox `full`'s tier-independence is an assumption [A]**, not a documented fact: the Plus table adds only `full_d30` and does not restate `full` **[UPX-1]**. The claim *"the Upstox side does not depend on the account tier"* is now explicitly conditional (§3.5a, §3.10, §3.12.4, §9 G-T, §13). **(h) C8 — the Upstox chain's MCX exclusion** **[UPX-3]** **and the FYERS `timestamp` semantics** (*"Options chain data at timestamp"* **[FY-4]**, used as the expiry selector by **convention [E]**, not by documentation) are now recorded (§3.12.2). **(i) Item 4 — FYERS `/data/futures-chain` evaluated, not adopted** (§3.12.4b): documented **[FY-5]** — one underlying per request, `symbol/fyToken/lp/ch/chp/expiry/volume`, *"only currently available contracts are listed"*, example **`BSE:SENSEX-INDEX`** — with **no OI, no bid/ask, no IV/Greeks**. Against the existing futures path it is **call-inefficient** for bulk pricing (220 calls against ≈1–2 batched quote calls) and adds no field the quote path lacks, so **it changes no capacity figure and is not adopted**. Its one candidate use — a *currently-trading* guard on nearest-two-futures selection — is recorded as a new maintainer decision **M-14**, with the plan's futures selection unchanged. **(j) §3.14 — a new API source register** gives the URL, the date read (2026-10-20) and the evidence class **[D] / [E] / [A] / [M]** for every API claim, and the three inferences the plan makes (the Upstox market-data rate bucket, Upstox tier-independence, the FYERS expiry-epoch convention) are listed explicitly as assumptions. **Cross-checks:** §3.5a quantities 6 and 7 carried stale pre-settlement figures (**516–556 / 637–674 / 112**) and now carry the settled **517 / 85 · 623 / 84 / 109**; §3.11.4's table carried the pre-settlement **670 / 219** and now carries **689 / 220**; §3.7's G-T wording and §13's blocking list were re-aligned; M-12, M-13 and the new M-14 were re-worded; and the exit checklist records the V13 findings. | This pass changed no measured number except by removing stale ones, but it changed **eight claims' evidential status** — several of which the plan had presented as documented facts. The most consequential is C2: an endpoint that MarketHub **already implements** was missing from the field matrix, and it weakens the plan's central 'no source' claim for open interest. |
| **V14** | 14 — final consistency, request-budget and post-fix verification | **(a) Scope statements repaired in the active text.** §1.1 asserted the scope was unresolved, §1.2 carried the pre-decision 662/221/441 figures, §1.3 listed the streaming scope as a decision to be made, and §6.4 still showed M-1 and M-4 as blocking dependencies. All are **rewritten to the settled scope** (§1.1, §1.2, §1.3, §1.4, §6.4); M-1 and M-4 are removed as gates from the ledger, the dependency map and the work items, and remain recorded as **closed decisions** that W-04 and W-09 apply; the R5 streaming paragraph stays in §2.4 as **labelled pre-decision history**, and no acceptance criterion tests it. **(b) The FYERS budget recomputed at option-symbol level** (§3.12.4). V13's budget was wrong twice over: it charged **one depth request per strike** where the endpoint takes **one symbol per request** and each strike carries CE **and** PE, and it carried **953** quote calls for the whole FYERS-only tail although the FYERS chain already serves the in-window tail. The contract-level classification is now **A 40 208** (Upstox chain) + **B 41 700** (FYERS chain) + **C 5 934** (quotes + depth) = **87 842** ✓, giving a minimum pass of **≈6 420 calls** — which **does not fit** the FYERS Standard 5 000/day pool. The plan now states a **tiered freshness schedule** and **no daily full-refresh claim** on that tier, and marks the SENSEX portion **conditional**. **(c) Current OI separated from previous OI** (§3.12.2, §3.12.3, §5, §7 W-25, §8.2): `/v2/market/oi` is a candidate for **current** per-strike OI only, is **not** a source of previous OI, and therefore **cannot** replace the FYERS depth cost; the field states `field-unestablished` and `field-unavailable` are distinct, and the probe is a **separate evidence task** that does **not** resolve the IV/Greeks gap. **(d) The union recomputed from exact keys** with the canonical NIFTYFPI identity (§3.13, §3.13.1): **current 517/85 · post-W-03 517/84 · capacity-first 623/83 = 706 = 659 + 47**, with the 47 decomposed (36 streamed option legs · 7 second-provider F&O index copies · 4 general quote subscriptions **outside** the F&O universe) and the V13 "ten additional index keys" classified. **(e) The IV/Greeks gap bounded honestly** — *an absence of a documented source among the evaluated endpoints of the configured providers*, not proof of impossibility; the requirement is not redefined and the full-data acceptance criterion **stays blocked**. **(f) M-14 made consistent** — optional, outside the dependency chain, blocking neither W-26 nor the core data-surface work. | This pass did not extend the analysis; it **reconciled** it. Two of its findings changed the plan's conclusions rather than its wording: the corrected request budget shows the FYERS Standard tier **cannot** carry a full daily refresh, and the current-OI/previous-OI conflation would have produced a budget that counted the wrong thing. |
| **V15** | 15 — targeted request-budget and chain-coverage reconciliation | **(a) The 689 cells reconciled.** An exact per-cell inventory was built from the catalogue (§3.15): every cell's canonical underlying and expiry, the endpoint that serves it, its tier, the symbols it yields, and the identities `U + F = sym` and `full + part = sym` that hold on every row. **(b) The chain-request count corrected.** V14's "40 FYERS chain cells" was the class-C cell count mislabelled as a request count. The reconciliation is **670** FYERS chain requests + **37** Upstox chain requests (18 cells need both) = **707**, with **0** cells needing none — and no cross-cell batching is assumed, because both chain endpoints take one (underlying, expiry) per request. **(c) The budget split per provider.** **7 011 FYERS calls** (670 chain + 119 quote + 5 934 depth + ≈288 poller) and **39 Upstox calls** (37 chain + 2 quote) — separate pools, never summed; the 39 Upstox calls are **not** charged to FYERS. **(d) The freshness claim corrected.** The FYERS Standard pool does not fit a complete pass (7 011 > 5 000); the tail rotation is **≥ ≈1.7 days** and is now stated as a **lower bound**, because the other pool consumers are unmeasured; the tiered schedule reserves the poller, tier 0 and tier 1 explicitly, and names what is deferred when the pool is exhausted. **(e) The reachability partition re-measured per option symbol.** The unreproducible **1 985 strikes / 4.5 %** is withdrawn in favour of **41 004 strikes fully served (93.25 %)** and **2 969 strikes (6.75 %)** carrying **5 934** option symbols — symbol count and strike count now agree, and the concentration list is re-measured. **(f) Nothing settled was reopened** — scope, BSE conditionality, the IV/Greeks boundary, the current-vs-previous OI distinction and every open decision are unchanged. | This pass did not extend the analysis; it **re-derived** it from the primary data. Two of its findings changed conclusions rather than wording: the chain-request count was understated by an order of magnitude (77 → 707), and the strike-level reachability figure was overstated as coverage. |
| **V16** | 16 — option-surface architecture correction (provider-capability investigation) | **(a) The unit of work corrected.** The option surface was priced symbol by symbol (87 842 symbols, and 5 934 of them as individual per-symbol REST depth calls) when the unit is the **(underlying, expiry) cell**: one chain response covers every symbol in the cell and carries all seven required fields. **(b) The scope input applied.** The maintainer's instruction of 2026-10-21 — *"20 strikes on either side of ATM is enough for us"* (D-21) — takes the deep-ladder tail out of the required surface: the class-C gap becomes **0**, and the required surface costs **670** FYERS chain calls + **19** Upstox chain calls with **0** quotes and **0** depth calls. **(c) The budget now fits and the freshness becomes a guarantee.** FYERS Standard: **958** calls/day of 5 000 → **5.22 sweeps/day** → **all 689 cells refresh every ≈ 4.6 hours** (≈ 13 min per sweep at 50 rpm); Prime is rate-bound, not pool-bound. **(d) Four tiers separated** (§3.16.4): focused WebSocket streaming, active/visible chain refresh, background full-surface refresh, deep-ladder tail (out of scope under D-21). **(e) The ATM problem answered without an extra request** — a chain response carries the underlying price, so the ±20 window is a second phase of the same response; the scope **size** is a proxy lower bound, the **budget** is exact either way. **(f) The provider capability matrix** separates documented capability from runtime-verified coverage for Upstox's chain, FYERS's chain, FYERS's depth and DhanHQ's chain; the only difference that matters for the required surface is FYERS's documented ±50 strike cap. **(g) DhanHQ evaluated from documentation only and NOT adopted** — JWT + a paid Data API plan, integer security IDs with no exchange trading symbol, 1 request/3 s with no documented daily cap, NSE/BSE/MCX documented; no credential sought, no request made, no order routed. **(h) The runtime comparison is PENDING** — no Upstox or FYERS credentials exist here, so probes P-1…P-5 are specified and every runtime claim is labelled unverified. **(i) Two decisions opened** — M-15 (the ±20 reading) and M-16 (is multi-level depth required?). | This pass did not extend the analysis; it **changed the architecture**, and the budget fell by an order of magnitude because the unit of work was wrong rather than because a provider was better. It also withdrew a **"provider limitation"** that was in fact a scope artefact. |
| **V17** | 17 — maintainer decisions recorded, M-15/M-16 closed, budget arithmetic corrected | **(a) Four decisions recorded** (§2.2, D-21…D-25): **D-22** DhanHQ **excluded** (no credential, no probe, no integration, no order routing); **D-23** ±20 strikes around the **true** ATM, **surface-wide**, every underlying-expiry cell; **D-24** multi-level depth **not** required — per-symbol depth polling removed from the budget; **D-25** the deep-ladder tail **out of scope**. **(b) M-15 and M-16 closed** in the ledger, the gate table and the work items; **M-12** stops blocking the required surface (its subject is out of scope) and reopens only with the tail. **(c) The budget arithmetic corrected.** The index poller's **≈288** calls/day are a **fixed daily** cost, not a per-sweep one: **670** requests per sweep, **≈288** poller, **5 000/day** pool, **4 712/day** remaining, **4 712 ÷ 670 ≈ 7.03 sweeps/day**, **one sweep every ≈ 3.4 hours on average**, **13.4 min** per sweep at 50 rpm. V16's "670 + 288 = 958 per sweep ⇒ 5.22 sweeps/day ⇒ ≈ 4.6 h" is withdrawn. **(d) 3.4 hours is explicitly NOT a freshness SLA** — it is the theoretical maximum, and the plan says so wherever the figure appears: the other FYERS consumers, retries and runtime behaviour are unmeasured, and the near-real-time guarantee lives in the active/visible tier. **(e) The Upstox side stays a separate provider budget** — the **19** SENSEX cells (**21** calls per sweep, **691** with the optional redundancy sweep) — with its market-data quota **unverified**, and it is excluded from the 3.4-hour figure. **(f) The pending runtime validation is preserved** (P-1…P-5, Upstox and FYERS only, no credentials here); the current-OI probe is now a re-entry condition only. **(g) DhanHQ's evaluation is retained as the record of an excluded provider** — no credential was sought, no request made, nothing integrated. | This pass changed no analysis and no scope: it **recorded four decisions**, **closed two open gates**, and **corrected an arithmetic error in the budget** that V16 had carried — a per-sweep charge for a fixed daily cost, which had understated the sweep rate by a third. |
| **V18** | 18 — §4 recovery attempt, ±20 maximum reconciliation, session-scoped refresh model | **(a) The §4 recovery was attempted and the blocker is reported, not papered over.** The V13 plan copy **cannot be accessed** in this environment — the file was never staged or committed (no unreachable blob holds it), there is no branch, stash, editor history or external copy, and a 17 442-file scan found only the live plan and this session's scratch scripts (heading only). **No second reconstruction was produced**; §4 is relabelled an **unverified reconstruction** and the blocker is carried in §4, §12 item 17 and §13. **(b) The ±20 maximum reconciled exactly**: naive **689 × 41 × 2 = 56 498** vs the measured cap **52 250**; the **4 248** difference is **191 short cells** (166 NSE F&O stock, 14 NSE index, 11 SENSEX) whose own ladder is shorter than the window; refined bound **52 146 ≤ max ≤ 52 250** after 104 one-leg strikes; **9 087 stays a proxy**. **(c) The refresh model re-based on the trading session** — the material correction: the market is open **≈385 min** a day (NSE equity derivatives **09:15–15:40 IST**), the window is **read from the provider per date** (**[UPX-8] [UPX-9] [UPX-10] [NSE-1]**), the ≈7 sweeps/day are spent **inside** the session, giving **≈ once an hour while the market is open** and **nothing outside it**; the V17 *≈3.4 h* frame is withdrawn because it divided by 24 hours. **(d) D-26** records the maintainer's *show what we need to see* requirement as a session-scoped, demand-driven refresh policy. **(e) The locked decisions and the provider/cost-type budget separation are preserved unchanged**, and the runtime probes stay **pending**. | This pass corrected the plan's **time model**, not its scope: the refresh interval had been expressed against a 24-hour day for a market that trades about six and a half, which produced a number that described nothing a user would see. It also produced the first honest account of a document loss and its recovery blocker. |
| **V19** | 19 — session-bound poller and request-budget audit | **(a) The index poller was traced to the code for the first time** (§3.17): `app/fyers_index_source.py` + `app/server.py`; one `GET {DATA}/quotes` request for all due symbols every 300 s inside a hard-coded 09:15–15:30 IST gate; **9** symbols; 30 s loop; 5 rps bucket. **(b) Its measured cost is 75 requests per gated day, not the 288/day the plan carried since V10** — the code batches all symbols into one request and has always been clock-gated to the session window. **(c) Six scheduling defects against D-26** found and turned into **W-27**: weekend/holiday/special-session polling (75/day wasted, ≈8 550/year), a hard-coded close 10 minutes short of the current 15:40 F&O close, no per-exchange window, a fallback that runs even when Upstox is healthy, an unbounded 30 s retry, and an inert interval guard. **V20 record:** the **Upstox feed's health state** and the **poller's lifecycle** were audited together, and the finding is that **FYERS is not a fallback — it is an always-on duplicate**: the poller is started unconditionally and never consults the primary's `state` / `last_message_at`, while the intended fallback semantics live only in the **read path**. The lifecycle is now specified — idle while the primary is healthy (**0 requests**), armed on a **600 s** staleness with a **60 s** dwell and a **15 min** recovery hold-off, and **exchange-specific provider-driven** session eligibility (7 NSE + 2 BSE symbols ⇒ **two** windows, no hard-coded clock) — and the request accounting is split into **normal** (**0**) and **failover** (**77 per session**; **120/hour** and **≈ 770 attempts** under a sustained provider failure), with the chain sweep and the other consumers kept separate and **no budget guarantee offered**. **V21 record:** the code was audited before the design was changed, and the audit **contradicted the plan**: the **689-request background chain sweep was never implemented** — it was a V16–V20 proposal — while the one path that *can* multiply requests without bound, the **alert-driven analytics chain scheduler** (one chain request per minute per alert-registered chain, with **no session gate**), was never identified or bounded. The sweep is therefore **withdrawn and not replaced**; the target model fetches a chain **on demand** (one request for the cell the user opens), keeps the **visible window** live over the WebSocket, refreshes **only the active cell** on reconnect or a field gap, and **stops outside the session**, retaining the last snapshot labelled with its timestamp and session. The **Future Heatmap** was confirmed to make **no option-chain request** at all — it reads the quote store. **(d) No concrete reason exists to poll outside market hours** — the out-of-session work belongs to the catalogue sync and the previous-OI recorder, not the poller. **(e) The FYERS budget re-derived from the measured scheduler and kept separated by cost type**: chain sweep 670 · poller ≈75 · history unmeasured · retries unmeasured · pool 5 000 ⇒ **4 925** ⇒ **≈ 7.35 sweeps/day** ⇒ **≈ 52 min inside the session** — a target, not an SLA. **(f) Upstox's 21 reconciled** as **19** SENSEX chain calls (T2) + **2** proposed quotes calls (T1), quota still unverified. **(g) The session model, every locked decision and §4's unverified-reconstruction label are preserved**; the probes stay pending. | This pass removed the last figure in the budget that had never been measured, and in doing so showed that the poller was never the capacity problem — it is a **scheduling** problem: a few dozen wasted requests on non-trading days and an unbounded retry, against a budget that had been overstated by 3.8×. |
| **V20** | 20 — close the index-poller scheduling defect (audit + specification) | **(a) The primary's health state was traced.** `brokers/upstox/feed.py` `status()` already publishes `state` (7 values), **`last_message_at`**, `last_connected_at`, `last_exit_reason`, `reconnect_count`, `not_ready_reason` and `recent_transitions` — everything a health decision needs. **(b) FYERS is not a fallback but an always-on duplicate:** the poller is started unconditionally and never consults the primary's state; the fallback semantics exist only in the read path (`market_indices` resolves a FYERS candidate when Upstox has no quote). **(c) The lifecycle is specified:** idle while the primary is healthy (**0 requests**), armed on a **600 s** staleness with a **60 s** dwell and a **15 min** recovery hold-off, and **exchange-specific provider-driven** session eligibility — 7 NSE + 2 BSE symbols, **two** windows from `/v2/market/timings/{date}`, `/v2/market/holidays`, `/v2/market/status/{exchange}`, no hard-coded clock. **(d) Request accounting split into normal and failover:** **normal 0**; **failover 77 per session**; **sustained failure 120/hour and ≈ 770 attempts per session**; pool left for sweeps **5 000 / 4 923 / 4 230** ⇒ **≈ 7.46 / 7.35 / 6.31 sweeps/day** ⇒ **≈ 52 / 52 / 61 min** in-session — **arithmetic, not guarantees**. **(e) The coverage boundary is stated:** the fallback covers **index quotes only**; the 19 SENSEX chain cells still have no source under an Upstox outage (M-13). **(f) W-27 and W-28** carry the two defects; the locked decisions, §4's unverified-reconstruction label and the pending probes are unchanged. | This pass closed the last contradiction between what the code says it does ("a fallback") and what it does (a duplicate), and produced the first **normal-versus-failover** budget — the distinction that decides whether the FYERS pool is spent at all. |
| **V21** | 21 — replace full-surface sync with demand-driven data | **(a) The code was audited before the design was changed**, and the audit **overturned the plan**: the **689-request background chain sweep (670 FYERS + 19 Upstox) was never implemented** — it was a V16–V20 proposal — while the one path that can multiply requests without bound, the **alert-driven analytics chain scheduler** (one chain request per minute per alert-registered chain, **no session gate, no aggregate cap**), was never identified. **(b) The Heatmap was confirmed to fetch no option chain** — it reads the quote store, TTL-cached with single-flight. **(c) The target model (D-27):** chain **on demand** (one request for the opened cell; the underlying price from that same response gives the **true ATM** and the **±20 window** at no extra cost), **visible-window WebSocket** maintenance, **release** of no-longer-needed keys at the next authorize/re-subscribe cycle, **re-fetch only the active cell** on reconnect or stale cache, a **narrowly scoped refresh** when the live feed lacks a field, and **0 requests outside the session** with the last snapshot retained **labelled with its timestamp and session**. **(d) The accounting (§3.16.5):** **0** when idle, **≈ 1 per user action**, **0** closed-session, index fallback separate and conditional — a tick is not a REST request, and one chain response covers many strikes. **(e) Capability gaps recorded** (§3.18.5): the ~82-key visible window against a per-provider entitlement, IV/Greeks on the live feed **unverified**, the 19 SENSEX cells without an alternate source (M-13), and the Upstox quota **unverified** (P-1…P-5 **PENDING**). **(f) W-29** carries the work; the locked decisions, §4's label and the field requirements are unchanged. | This pass did the thing the previous thirteen had not: it went to the code before changing the plan, and the code said the plan's biggest cost was imaginary. The remaining budget is not a smaller version of the old one — it is a different design, and the one path that could still grow without bound is now named. |
| **V22** | 22 — bound the alert scheduler; preserve demand-driven fetching | **(a) The scheduler was traced**: chains are registered from **enabled analytics alerts** into a **set of alert ids per chain key** (so several alerts on one cell already share one request), at a **60 s** tick, concurrency 4, with backoff and a 300 s staleness that already makes a stale snapshot UNKNOWN. **(b) Two defects found**: **no session gate and no aggregate cap**, and **two key spellings** (`analytics:<id>:<expiry>` vs `<id>:<expiry>`) that let the **same cell be fetched twice per tick**. **(c) The worst case for N registered chains was computed**: **N** per minute, **385 N** per session, **1 440 N** per ungated day — the **FYERS Standard pool is exhausted at N = 13**, and the catalogue ceiling would be **265 265** per session (≈ **53×** the pool). **(d) The design (D-28):** an **exchange-specific, date-specific, provider-supplied** session gate (timings, holidays, special timings — no hard-coded 15:30); **price-based alerts while closed are stated** — live-move alerts are UNKNOWN and cannot fire, session-close metrics are evaluated once against the labelled close snapshot; **one shared cached chain per (underlying, expiry, provider)** with single-flight, so opening an alert-maintained cell costs **0**; **one shared per-provider rate and daily-budget admission** across analytics, visible refreshes, reconnect recovery, index polling and retries, with deferral rather than dropping, the FYERS pools used as documented limits and the **Upstox quota left unverified**; **bounded retries** with backoff and admission; and **exchange- and provider-specific fallback**, so the 19 SENSEX cells are **unavailable** under an Upstox outage rather than promised to FYERS. **(e) W-30** carries the work; the sweep stays withdrawn and every locked decision is preserved. | This pass found the one remaining path whose cost is a function of the *user* rather than of the system — and showed that **thirteen** alerts would have exhausted the FYERS daily pool with nothing in the design to notice. It also fixed the duplication hazard that would have made a shared cache silently double-fetch the same cell. |
| **V23** | 23 — correct current exposure versus target budget | **(a) The exposure arithmetic was corrected.** V22's **385 N** applies only *after* the session gate; **today** the scheduler has none, so the exposure is **1 440 × N** per calendar day (and **2 880 × N** at the 30 s minimum). **Four FYERS-routed chains already require 5 760/day** against the **5 000/day** Standard pool, and **11 520/day** at 30 s — exceeded **before** any other consumer. The target is **385 N** per session and **0** on non-trading days. **(b) Routing was traced and measured**: the scheduler names no provider, the **catalogue decides**, and its read model orders by provider — so **"fyers" wins**; **all 220 in-scope underlyings resolve to the FYERS row**. No Upstox request is charged to the FYERS pool, and none is assumed to be. A consequence: SENSEX routes to a provider that lists **no** BSE_FO instruments. **(c) The duplicate-key risk was reconciled**: both key spellings parse to the same canonical cell while remaining two registry entries, so the worst case is **2 × the distinct cells**; the target keeps **one entry per canonical cell**. **(d) Retries, concurrency and the other consumers are separated**: a failed chain consumes the same per-tick slot, so **N** attempts per tick already include retries; **concurrency 4 is not a budget control**; the other FYERS consumers are **unmeasured**; the Upstox quota stays **unverified**. **(e) Nothing else was reopened** — the sweep stays withdrawn and every locked decision is preserved. | This pass corrected the plan's own headline number in the direction that makes the problem *smaller* than stated for the target and *larger* than stated for today — and it did so by tracing **where the request actually goes** rather than assuming. The number that matters is now unambiguous: **four chains is too many**. |

**Passes 1–23 completed — and the convergence claim remains WITHDRAWN.** V6
asserted that the review cycle had converged; passes 7–23 each found
substantive new material — the scope premise, the ceiling derivation, the
identifier reuse, the band arithmetic, the treatment of the 800 decision, the
framing itself, an unsized product requirement, a pre-decision inventory
presented as current, a data route built on an endpoint that does not carry the
required fields, four internal inconsistencies, eight defects in the plan's own
API claims, a request budget that was wrong in two directions, a
current-vs-previous OI conflation, a union that had never been recomputed from
exact keys, active scope statements still contradicting a closed decision, a
chain-request count understated by an order of magnitude, a reachability
figure that could not be reproduced, the unit of work itself misidentified, an
arithmetic error that charged a fixed daily cost per sweep, a refresh model
expressed against a 24-hour day for a market that trades six and a half, a
budget line never traced to the code, a "fallback" that was an always-on
duplicate, a cost model describing a sweep the code never had, an alert
scheduler with no bound — and in V23, **the plan's own exposure number being
wrong in the wrong direction**: it quoted the *post-gate* figure for a
*pre-gate* system, and it had not checked **which provider** the requests
actually reached.

**Honest statement of where this stands.** The plan is **structurally complete
but not converged**. The open items are (i) **decisions** only the maintainer
can make (M-2 the ceiling; M-5 the headroom, the account tier and the failover
trade-off; M-13 the BSE dependency record; M-14, optional; **M-12** as a
re-entry condition only — **M-15 and M-16 are closed**), (ii) **evidence**
that requires a session or a probe (the **chain-key probes P-1…P-5** against
Upstox and FYERS, the account tier, a **FYERS-side session/timing source**,
the **per-provider live-window entitlement**, whether the feed carries **IV and
Greeks** for option keys, and a **measurement of the remaining FYERS pool
consumers** — without which no quota, SLA or budget guarantee is offered),
(iii) a **document-recovery blocker** (§4's V13 source is not accessible), and
(iv) a set of **work items** that cannot begin before those are discharged. **The
scope is closed and gates nothing.** Seventeen consecutive passes have
overturned or materially extended their predecessor, and the last four have all
come from reading the code or re-deriving the arithmetic rather than from the
prose. **The plan is therefore BLOCKED, and it is blocked on decisions, evidence
and one recoverable document — not on unfinished analysis — but it must not be
described as converged.**

---

## 12. Exit checklist

Each criterion is marked **PASS**, **BLOCKED** or **PASS (design) — not
implemented** as appropriate. A *design* verdict means the analysis is complete
and internally consistent; it does **not** mean the behaviour has been built or
measured at runtime. Items 17 and 18 are deliberately **BLOCKED**.

| # | Criterion | Result | Explanation |
|---|---|---|---|
| 1 | Official provider limits correctly sourced, version-specific, distinguished from application limits | **PASS** | §3 cites the official pages with the check date (2026-10-20) and the FYERS pages' own "last updated 2026-10-14". The Upstox feed limit (2 000 individual / 1 500 combined, per user, 2 connections/user), the Upstox REST 500-per-request limit, the FYERS Data-Socket 100/5 000 and TBT 1/15 (per user, across connections), and the REST pools are all separated from MarketHub's own 500 ceiling. **V6 additionally resolved the endpoint↔Data-Socket identity** (§3.3) and records three residual uncertainties (U-1 URL not printed on the docs page; U-2 an older SDK-changelog figure of 200; U-3 FYERS' own `fyers-skills` repo contradicting the docs page's scope wording — the docs page governs). |
| 2 | Actual account entitlements verified or explicitly marked unverified | **PASS — declared UNVERIFIED, not confirmed** | §3.5 quantity 2 and §3.4 record **both** accounts as unverified (Upstox unauthenticated; FYERS credentials rejected 403/1010). No live probe was performed. **Every** FYERS figure in this plan is explicitly conditional on the Standard tier (§3.7, §3.8, §10.3) and the tier is carried as an open item (M-5, R-05b). V5's wording — that the FYERS figure "does not even apply to the socket the adapter opens" — was **wrong** and has been removed. |
| 3 | Every conflicting plan/decision reconciled or isolated as a maintainer gate | **PASS for isolation; the decisions themselves remain OPEN** | §2.2 classifies D-1…D-20 as approved / amended / proposed / superseded / conflicting; §2.3 isolates **sixteen** decisions (M-1…M-10, M-9b, M-11…M-16), of which **M-1, M-4, M-15 and M-16 are closed** and **M-12 is a re-entry condition only**; **§2.4** isolates the streaming-scope conflict as an authority question with a dated table (P-1…P-6); §2.1 records the PM context as a source. The 800 ceiling is **not** recorded as unsupported — it is a maintainer decision whose *repository record* is missing (M-2). **M-3 is no longer a design gate** (P-5 is approved); **M-11** isolates the kind-versus-role conflict in the overflow priority. **Isolation is not resolution** — §13 remains **BLOCKED**. |
| 4 | No historical approval silently overridden | **PASS** | §2.2 cites every source and dates it; the plan proposes *amendments* recorded as new dated entries, not edits to R3–R11. No existing document is modified by this plan. V5's own R-05 is shown **withdrawn in the open**, with its replacement, rather than quietly deleted; and the **approved application ceiling of 500 is preserved by default** (§3.5a, W-10) rather than re-derived. |
| 5 | Every known runtime defect covered or justified out of scope | **PASS** | §5 registers **36 open** root causes (R-01…R-04, R-05a, R-05b, R-06…R-35) plus **1 withdrawn** (R-05, §3.3). Each open one maps to a work item in §7 and a failing-before test in §8.2; **V9** added the capacity-model, routing-sequence and capacity-first-coverage rows, **V10** the complete-data-surface, provider-asymmetry and BSE-coverage rows. Out-of-scope items are listed explicitly in §9. |
| 6 | All subscription owners and their interactions accounted for | **PASS** | §6.1 enumerates all six owners; **§3.7.1 gives each owner's exact contribution** (① 9/9 · ② 0/0 · ③ 13/4 · ④ 3/22 or 44/0 · ⑤ 0/0 · ⑥ scenario-dependent) and **§3.7.3 the measured overlaps** (①∩⑥=7, ③∩⑥=1, ④∩⑥=1, ③∩④=4); §3.10 measures the **desired set** (662, 0 unroutable, 650 listable by both) independently of any budget; §6.3 specifies the routing sequence that consumes all of it; §7 W-08/W-09/W-19 require the union to be computed once, with the planner consuming it rather than budgeting in isolation. |
| 7 | The subscription-capacity failure has a complete causal model and acceptance criteria | **PASS (model) — the numbers were recomputed independently in V14 and again in V15** | The causal chain (scope → the planner fills its own budget → the resolver unions the other owners → no headroom → `raise` → wedged-until-cleared) is in §6.1/§6.2; the limits model §3.5a/§3.5b; the headroom derivation §3.6; the **option-symbol-level** field coverage §3.12.2/§3.12.3; the request budget and freshness schedule §3.12.4; the exact union and its three states §3.13/§3.13.1. V14 independently recomputed the union from exact provider keys (**current 517/85; post-W-03 517/84; capacity-first 623/83 = 706 = 659 + 47**) and the option-surface classification (**A 40 208 + B 41 700 + C 5 934 = 87 842 ✓**, measured per option symbol — the **full-ladder** view, retained for the out-of-scope tail).
**V15 re-derived the budget from an exact 689-cell inventory (§3.15)**: **670** FYERS chain cells + **37** Upstox chain cells = **707** chain requests (**0** cells need none), giving **7 011** FYERS and **39** Upstox calls per complete pass, and it withdrew the earlier **1 985-strike / 4.5 %** reachability figure in favour of the reproducible **2 969 strikes / 6.75 % carrying 5 934 symbols**.
**V17 recorded the maintainer's confirmed scope and corrected the pool arithmetic**: the required surface is the **±20 window** (D-23), served by **670 + 19 = 689** chain requests — **one per cell** — with **0** quotes and **0** depth calls (D-24); the FYERS pool leaves **4 925/day** after the **≈75**-call poller (measured, §3.17) ⇒ **≈ 7.35 sweeps/day ≈ one sweep every ≈ 52 minutes inside the session**, a **target and not a freshness SLA**.
The **acceptance criteria use post-fix values**, never current-state counts. **The scope is settled** (M-1, M-4 closed); the *design* is complete, the *runtime behaviour* is not — W-08/W-19 are what turn these projections into measurements. |
| 8 | The approved universe and streaming model are explicit | **PASS — stated by the maintainer, 2026-10-20** | **M-1 and M-4 are closed** by the maintainer's statement: the coverage scope is the full F&O universe (NSE major indices + NSE F&O stocks + BSE SENSEX, all contracts including option chains and futures), the index universe is the **six NSE indices + SENSEX with BANKEX out**, and the *delivery* split is now explicit (§3.11): the **streamed** surface is spot + futures + the focused ATM window (bounded by entitlement), the **option surface** is served on demand from a budgeted cache, and the two are reported separately. **The acceptance set is the settled scope only** — the S1/S2 scenario sets survive in §3.7 and §2.4 as **labelled pre-decision history** and are **not** acceptance targets. **This item is a statement of design completeness, not of verified runtime behaviour:** the *scope* is explicit, but the implementation that must realise it (W-08, W-24) has not begun, and what is not yet decided is the completeness policy (M-12) and the BSE provider dependency (M-13). |
| 9 | Identity, routing, provider availability and provider attribution addressed coherently | **PASS** | W-03 (index keys) → W-05 (single inference) → W-06 (availability) → W-17 (attribution end-to-end), with the granularity question isolated as M-6. |
| 10 | Dependencies valid and remediation order justified | **PASS** | §6.3 gives the dependency graph; §7 states per-item dependencies; the cluster W-07→W-08→W-09→W-10 is justified in the Phase-3 risk note (scope → budget → policy → ceiling as an output). |
| 11 | Integration, regression and release tests cover real component interactions | **PASS** | W-19 is the explicit integration harness; §8.1 marks which validations must be integration-level; §8.2 requires every root cause to have a failing-before test. |
| 12 | Startup, recovery, error reporting and observability included | **PASS** | W-11 (atomic startup, no poisoning), W-12 (one error contract), W-13 (UI surfacing), and the per-provider counter requirement in Phase 4's observability note. |
| 13 | Clean-checkout and change-set integration blockers handled | **PASS** | W-01 (integration + mixed-scope separation) and W-22 (clean-clone import + startup readiness). |
| 14 | Unrelated work and protected-core changes kept out unless separately approved | **PASS** | §4.2 attributes and §9 lists the explicit exclusions; W-13 and §9 forbid re-keying the quote store; S15 stays deferred. |
| 15 | Every work item has evidence-based completion criteria | **PASS** | Every item in §7 has an *Acceptance* clause, a *Depends* clause and an *Approval* column; §8.2 maps them to failing-before tests. |
| 16 | No fix described as complete merely because it has been proposed | **PASS** | §1.3, §7's framing and §10.3 state explicitly that nothing here is implemented or validated. **Every work item is a proposal**: W-01…W-23 describe *desired* behaviour and *acceptance criteria*, not work performed. No defect in §5 is marked fixed, and no test in §8.2 is marked passing — each is stated as "fails before the fix". |
| 17 | No ungrounded assumptions, unexplained contradictions or duplicated remediation | **BLOCKED** — *and V11's verdict on this item was wrong* | Every uncertainty is typed and labelled: the account tier **UNVERIFIED** (§3.5a concept 2, R-05b, gate G-T); the API residuals U-1…U-3 (§3.3); the 800 decision's **repository record** missing (§2.2 D-2, M-2); the headroom floor analysed and the value left to M-5 (§3.6); the scope conflict explained (§2.4, R-24); the P-5 kind-versus-role conflict surfaced rather than redefined (§6.3, M-11); and the capacity-first result labelled a **projection**, not a measurement (§3.7.2c). **But a contradiction remains in the underlying decision record** (P-1 vs P-3/P-4) and it is **unresolved** — only the maintainer can close it (M-1 / G-S). **And §4 is an unverified reconstruction**: its V13 source could not be accessed in this environment (the file was never staged or committed and no backup exists), so no §4 claim can be certified. This item is therefore **not** marked PASS. |
| 18 | An independent final review finds no remaining structural gaps | **BLOCKED** | Passes 2–12 closed 35+ gaps. **Pass 7** overturned two of V6's own conclusions; **pass 8** overturned **V7's own central premise** about the 800 ceiling; **pass 9** replaced the 500-vs-800 framing with the capacity-first model of §3.5; **pass 10** closed the scope and added the data surface; **pass 11** recomputed the target universe and verified the field matrix; **pass 12** corrected **four internal defects in V11's own arithmetic and gate bookkeeping** that this checklist had claimed to be resolved — **(a)** M-1 was simultaneously *closed* and *open* across §1.2, §1.4, §2.4, W-00 and §9; **(b)** the §3.12.3 reachability classes were **nested, not parallel** and summed to **47 586** against a **43 973** total (108 %); **(c)** the capacity-first projection reported **623 + 84 = 707** against a **659**-contract desired set with no account of the **48**; **(d)** the approval gate still carried the closed decisions. **All four are corrected in V12** (§1.2, §1.4, §2.4, §3.12.3, §3.13.1, §7 W-00, §9), and the corrections are recorded in the revision history rather than quietly applied. **V13 record:** an independent re-read of the official FYERS and Upstox documentation found **eight further defects** — C1 the Upstox REST rate bucket mis-attributed as documented, C2 the omitted `/v2/market/oi` route and an over-strong "no source" claim for OI, C3 the conditional FYERS depth OI, C4 the understated Upstox option-greeks field set, C5 the understated FYERS BSE support, C6 "no strike cap" asserted where only "documents no cap" is true, C7 the tier-independence assumption stated as fact, and C8 the MCX exclusion and FYERS `timestamp` semantics missing — **all applied in V13**. **V14 record:** a further audit found (a) **active scope statements still contradicting the closed decision** (§1.1, §1.2, §1.3, §6.4) — **all repaired in V14**; (b) the **FYERS budget was wrong in two directions** — V13 budgeted **one depth request per strike** (1 985) where the endpoint takes **one symbol per request** and each strike carries CE **and** PE, and carried **953** quote calls for the whole tail although the FYERS chain already serves the in-window tail — the corrected minimum pass is **≈6 420 calls**, which **does not fit** the FYERS Standard daily pool, so the plan now states a **tiered freshness schedule** and makes **no daily full-refresh claim**; (c) **current OI and previous OI were conflated** — `/v2/market/oi` is a candidate for **current** per-strike OI only and is **not** a source of previous OI, so it **cannot** replace the FYERS depth cost; (d) the **union was not recomputed from exact keys** with the canonical NIFTYFPI identity — the three states are now **517/85 · 517/84 · 623/83 = 706**, with the 47 extra keys decomposed; (e) the **IV/Greeks gap** is now bounded as *an absence of a documented source among the evaluated endpoints*, with the full-data acceptance criterion **left blocked**; and (f) **M-14** was listed as optional while W-26 still depended on it — now consistent. **All are applied in V14**. **V15 record:** the plan's own arithmetic was re-derived from an exact **689-cell inventory** (§3.15) and the **chain-request count** was corrected (**707**, not the mislabelled 77) and the **reachability partition** re-measured per option symbol. **V16 record:** the **unit of work** was corrected from the symbol to the **(underlying, expiry) cell**, which changed the architecture and cut the budget by an order of magnitude, and **DhanHQ was evaluated from documentation only**. **V17 record:** the maintainer's decisions of **2026-10-21** were recorded (**D-21…D-25**), **M-15 and M-16 were closed**, **DhanHQ was excluded**, the **index-poller cost was corrected** from per-sweep to fixed-daily (so the achievable rate is **≈ 7.03 sweeps/day ≈ one sweep every ≈ 3.4 hours on average** *(as stated in V17; **corrected by V19** — the poller costs **75 per gated day**, not 288, so the rate is **≈ 7.35 sweeps/day ≈ 52 minutes inside the session**)*, a **theoretical maximum and explicitly not a freshness SLA**), the **19 Upstox cells were kept in a separate provider budget** with an **unverified** quota, the **pending runtime validation was preserved** (P-1…P-5), and — recorded in the section itself — **§4 was lost to an editing error and rebuilt from the plan's own cross-references and a fresh measurement of the working tree**, because no copy existed on disk or in Git. **V18 record:** the **§4 recovery was attempted and the blocker is now documented precisely** — the V13 source **cannot be accessed** (never staged or committed; no unreachable blob, branch, stash, editor history or external copy), so **no second reconstruction was produced** and §4 is labelled an **unverified reconstruction**; the **52 250 maximum was reconciled exactly** against the naive 689 × 41 × 2 = **56 498**, the **4 248** difference being **191 short cells** (refined bound 52 146–52 250) and the **9 087** figure remaining a **proxy**; and the **refresh model was re-based on the trading session** after the maintainer pointed out that the market is not open 24 hours. **V19 record:** the index poller was **traced to the code** for the first time (§3.17) — its measured cost is **75 requests per gated day**, **not** the 288/day this plan had carried since V10, because it makes **one** request for all **9** due symbols every 300 s inside a 375-minute clock gate; the FYERS pool therefore leaves **4 925** for sweeps ⇒ **≈ 7.35 sweeps/day ≈ 52 minutes inside the session**. Six scheduling defects were found against D-26 — **no holiday/weekend/special-session awareness** (75 wasted requests on every non-trading day), a **hard-coded 15:30** close that is **10 minutes short** of the current 15:40 F&O close, **no per-exchange** distinction, a **fallback that runs even when Upstox is healthy**, an **unbounded 30 s retry** on failure, and an **inert 60 s interval guard** — and are turned into **W-27**; the **Upstox 21** requests were reconciled as **19** SENSEX chain calls (T2) + **2** proposed quotes calls for the 659 desired contracts (T1, not an implemented path), with its quota still **unverified** — NSE equity derivatives run **09:15–15:40 IST** (≈385 min), the session window is now **read from the provider per date**, and the ≈7 sweeps/day are spent **inside the session**, giving **≈ once an hour while the market is open** and **nothing outside it** (D-26). **V13 record:** an independent re-read of the official FYERS and Upstox documentation against this plan found **eight further defects** — C1 the Upstox REST rate bucket mis-attributed as documented, C2 the omitted `/v2/market/oi` route and an over-strong "no source" claim for OI, C3 the conditional FYERS depth OI, C4 the understated Upstox option-greeks field set, C5 the understated FYERS BSE support, C6 "no strike cap" asserted where only "documents no cap" is true, C7 the tier-independence assumption stated as fact, and C8 the MCX exclusion and FYERS <code>timestamp</code> semantics missing — **all applied in V13** (§2.3, §3.5a, §3.7, §3.10, §3.11.4, §3.12.2, §3.12.3, §3.12.4, §3.12.4b, §3.12.5, §3.14, §5, §7, §8.2, §9, §12, §13). **A plan with an unresolved blocker does not pass its own exit criteria**, and the remaining blockers are: the **capacity decisions (M-2, M-5)**, the **narrow IV/Greeks trade-off (M-12)**, the **BSE dependency record (M-13)**, the **account tier (G-T)** and the **integration gate (W-01)**. The scope is **no longer** a blocker (M-1, M-4 closed 2026-10-20). **The plan is not described as converged.** |
| 19 | The capacity architecture maximises usable provider capacity rather than an arbitrary ceiling | **PASS (design) — not implemented** | §3.5a defines one formula over the seven concepts; §3.5b determines that a separate application-wide ceiling is **redundant** (bounded by the entitlement, the headroom and the provider feed's own mode guard) and was the **cause** of the demonstrated failure; §3.6 re-derives the headroom and quantifies the cost of the old floor (FYERS usable 50 → 90, dropped 13–35 → 0); §3.12.1 and §3.13 measure the desired set (**659** contracts, 0 unroutable) against the usable capacity (**1 800 / 90**); §3.16.5 and §3.15 size the *option* surface per provider for the **confirmed** scope (**670** FYERS chain calls + **19** Upstox chain calls per sweep, **0** quotes, **0** depth calls; **4 712** of the 5 000/day pool left after the **≈288**-call poller ⇒ **≈ 7.03 sweeps/day ≈ one sweep every ≈ 3.4 hours on average**, a **theoretical maximum and not a freshness SLA**); §3.12.4 retains the **re-entry cost** of the out-of-scope tail (**7 011** FYERS calls per sweep); §6.3 specifies routing **before** dropping. The implementation is W-10 → W-07 → W-08 and **has not begun**; the 500-vs-800 question is closed as *historical context* plus one open decision (M-2: retire or retain-as-derived-guard). **Design completeness only — the runtime union is not yet measured.** |
| 20 | The complete F&O data surface required by the maintainer is **designed, field-verified and sized** | **PASS (design) — not implemented** | **V16 re-based the design on the cell as the unit of work** — one chain response per (underlying, expiry) covers every symbol in that cell, with all seven required fields — and **V17 recorded the maintainer's confirmation** (D-23 surface-wide, D-24 no multi-level depth, D-25 tail out of scope). Under the **confirmed** scope the class-C gap for the required surface is **0**, the per-symbol tail is **out of scope**, and the budget is **one** chain request **per cell the user opens** — the background sweep is withdrawn (V21) — with **0** quotes and **0** depth calls. The full-ladder budget (§3.12.4) is retained as the **re-entry cost**. The **±20 maximum** is reconciled exactly in §3.16.3: **689** cells × **41** strikes × **2** legs = **56 498** naively, against a measured cap of **52 250** — the **4 248** difference is **191 short cells** whose own ladder is shorter than the window (166 NSE F&O stock, 14 NSE index, 11 SENSEX), with a refined bound of **52 146 ≤ max ≤ 52 250** after the 104 one-leg strikes. The **9 087** scope figure remains a **proxy** until live prices and the true ATM windows are verified.  §3.12 recomputes the settled inventory (**220** underlyings, **220** spots, **439** nearest-two futures, **689** chain cells = 670 NSE + 19 SENSEX, **87 842** option contracts, **43 973** strikes). §3.12.2 is a field-by-field matrix of every relevant endpoint against the official documentation, and it **overturned V10's tail route** (FYERS quotes carry no OI/prev-OI/IV/Greeks; the route is FYERS **depth**, one symbol per request, OI only when `oiflag` is true). §3.12.3 measures reachability as a two-class partition — **41 004** strikes (93.25 %) fully served and **2 969** strikes (6.75 %) carrying **5 934** option symbols with no documented IV/Greeks source — and now states the **current-OI vs previous-OI** distinction explicitly. §3.12.4b records the FYERS `/data/futures-chain` evaluation: documented, BSE-SENSEX-capable, **changes no capacity figure**, **not adopted**, its one candidate use is the optional **M-14**. The implementation is **W-24 → W-25 → W-26** and **has not begun**. |
| 21 | The request budget and freshness claims are stated per tier, without over-claiming | **PASS (analysis) — the design is not implemented** | **V21 replaces the sweep with demand.** The steady-state REST cost is **0 when idle** and **≈ 1 request per user action**: **1** chain request for the cell the user opens (the underlying price comes from that same response, so the true ATM and the ±20 window cost no extra call), **1** re-fetch of the **active cell only** on reconnect or a stale cache, and **1 per refresh cycle, active cell only**, for a narrowly scoped refresh. The visible window is maintained over the **WebSocket** — a tick is **not** a REST request, and a subscribe/unsubscribe frame is not either. The **Heatmap makes no chain request** (it reads the quote store); the **catalogue syncs on demand** (startup, expiry rollover, catalogue change); the **index fallback stays separate** and conditional (**0** normal, **77/session** in failover, up to **770** sustained — §3.17.7). Outside the session: **0** market-data REST requests, and the last snapshot is retained **labelled with its timestamp and session**. The alert-driven chain scheduler keeps its **one request per registered chain per 60 s** but gains a **session gate**, a **normalised registry key**, a **shared chain cache** with the visible path, and a **shared per-provider admission control** (W-29, W-30); its **current** worst case for **N** registered chains is **1 440 N** requests per calendar day (there is **no session gate**) — the FYERS Standard pool is exceeded at **N = 4** — and the **target**, once the gate of W-30 exists, is **385 N** per session; both are asserted rather than assumed, and the routing is **measured** (all 220 in-scope underlyings resolve to the FYERS row), so the FYERS and Upstox pools are never conflated. **History, retries, the other shared-pool consumers and the Upstox quota remain unmeasured, and no quota, SLA or budget guarantee is offered.** **V18 corrected the frame again**: the market is open only **≈ 385 minutes** a day (NSE equity derivatives **09:15–15:40 IST**, w.e.f. 2026-08-03), so those sweeps are spent **inside the session** — the surface refreshes **about once an hour while the market is open**, and **nothing is refreshed outside it**. The session window is **read from the provider per date** (`/v2/market/timings/{date}`, `/v2/market/status/{exchange}`, `/v2/market/holidays`), never hard-coded. It remains a **target, not a SLA**: the other FYERS consumers, retries and runtime behaviour are unmeasured, and any visible refresh, redundancy sweep or retry comes out of the same 4 925. The tiers are **focused WebSocket streaming**, **active/visible chain refresh**, **background full-surface refresh** (≈ once an hour **inside the session**, a target and not a SLA) and the **deep-ladder tail** (**out of scope**, D-25). The **19 Upstox cells sit in a separate provider budget** whose market-data quota is **unverified**. The scope **size** remains a proxy-measured lower bound because the true ATM needs the underlying price — a value the chain response itself carries, so no extra request is needed.  V14 recomputed the budget at **option-symbol level**: the complete-surface pass is **670** FYERS chain calls + **119** FYERS quote calls + **5 934** FYERS depth calls + the **existing ≈288/day** index poller ⇒ **7 011 FYERS calls**, and separately **37** Upstox chain calls + **2** Upstox quote calls ⇒ **39 Upstox calls** charged to the Upstox pool. The FYERS side against the **Standard 5 000/day** pool **does not fit**. The plan therefore states a **tiered** schedule (active workspace → visible underlyings → universe cells → a **≥ ≈1.7-day rotation** for the tail's OI/previous-OI — a **lower bound**, since the other pool consumers are unmeasured) and makes **no daily full-refresh claim** on that tier; on **Prime** the same pass is ~1.3 % of the pool, bounded by 50 rpm. Upstox's rate remains an **assumption [A]** **[UPX-7]**. The FYERS **daily pool is shared** with quotes, depth, history and the index poller, and the *"three per-minute breaches block the account for the day"* consequence is recorded **[FY-1]**. **SENSEX contributes no FYERS calls today** (the catalogue has zero FYERS `BSE_FO` rows, so all 19 SENSEX cells are Upstox-only) and is a **single-provider dependency** until M-13 establishes the FYERS BSE route |

---

## 13. Final status

# ⛔ BLOCKED — READY FOR MAINTAINER REVIEW, NOT READY FOR IMPLEMENTATION

**The plan is structurally complete and blocked on decisions and evidence, not
on unfinished work. It is not described as converged, and no figure in it is
claimed as a measured runtime behaviour.**

**The requirement, as the maintainer stated it (2026-10-20) — closed, not
reopenable.** All F&O data for the **six approved NSE F&O indices plus BSE
SENSEX** (NIFTY and BANKNIFTY explicitly), the **NSE F&O stock universe**,
**all** contracts including option chains and futures, with **BANKEX excluded**;
maximum useful coverage within genuine provider limits; and honest reporting of
any genuine availability, freshness or completeness gap.

**What the verification established (V11 — with the V12, V14 and V15 corrections applied; the records below carry the history).**

* **The target universe is now exact** (§3.12.1): **220** in-scope underlyings
  (213 stocks + 6 NSE indices + SENSEX) · **220** spot contracts · **439**
  nearest-two futures · **689** chain cells (670 NSE + 19 SENSEX) · **87 842**
  option contracts (84 816 NSE + 3 026 BSE) over **43 973** strikes. V10's
  221 / 441 / 662 / 670 were **pre-decision measurements**; the "670 vs 677"
  discrepancy is reconciled (670 was NSE-only; the stock count is 213, and
  635 + 35 + 19 = 689).
* **The capacity-first result is now measured on the settled scope** (§3.13):
  the **whole 659-contract desired set is placed with 0 dropped** — Upstox union
  **623** of 1 800 usable, FYERS union **83** of 90 (post-W-03; **84** before
  the identity fix — §3.13) — against the current
  behaviour, where the planner's own budget drops **109** contracts and the
  resolver then raises. The **application ceiling is redundant** (§3.5b) and the
  **headroom floor** — not the ceiling — is what costs FYERS coverage (§3.6).
* **The option surface is field-verified against the official documentation**
  (§3.12.2), and this **overturned V10's route**: FYERS `/data/quotes` carries
  **no `oi`, `prev_oi`, `iv` or `greeks`**, so the FYERS-only tail needs
  **`/data/market-depth`** for OI and previous OI — one symbol per request.
* **One genuine data gap remains, and it is narrow and demonstrated**
  (§3.12.3): **2 969 strikes (6.75 %)**, carrying **5 934** option symbols — the
  deep wings of the widest index expiries, including **BANKNIFTY** (213–219
  strikes per expiry) — have **no documented source for IV and Greeks**; every
  route was evaluated and each fails for a stated reason. Price, volume, OI,
  previous OI and depth remain available for all of them (OI and previous OI
  **conditionally**, on `oiflag`), and the gap is reported per contract, never
  as a complete chain. **The requirement is not reopened for it.**
* **The request budget is now a total, not a percentage, and it is split per
  provider** — and, as of V16, split per **tier** as well. **Required surface
  (±20 ATM scope, D-21):** **Upstox 21** calls per sweep (19 chain + 2 quote)
  and **FYERS 670** chain calls + the **≈ 288** existing index-poller =
  **958** calls/day of 5 000 ⇒ **5.22 sweeps/day** ⇒ **all 689 cells refresh
  every ≈ 4.6 hours** (≈ 13.4 min per sweep at 50 rpm), with **0 quotes and
  0 depth calls**. **Background tier (reading (b), full ladder):** Upstox
  **39**; FYERS **670** chain + **119** quote + **5 934** depth + ≈ 288
  poller = **7 011** per pass, which **does not fit** the Standard pool ⇒ the
  tail rotates over **≥ ≈1.7 days**, a **lower bound**. On Prime the daily
  pool is not the constraint — the **50 rpm** limit is.
* **One risk the maintainer must see** (R-30): the capacity-first routing puts
  **623** contracts on Upstox and **55** on FYERS. At the FYERS Standard tier
  the FYERS allocation is **90 in total**, so **streaming the full universe and
  surviving an Upstox outage are mutually exclusive**. That is a property of the
  entitlement, and it belongs in the M-5 decision.

**V12 record — the V11 review found four defects in this plan, and all four
are corrected here.**

1. **M-1 was closed and open at the same time.** §1.2, §1.4, §2.4's verdict and
   the Phase-0 decision requirements still treated the streaming scope as
   undecided. **Corrected:** the scope is closed by the maintainer's requirement
   of 2026-10-20; §2.4's "no explicit amendment ⇒ BLOCKED" reasoning is retained
   only as **labelled pre-decision history**; **W-00** and **G-D** now list only
   the *open* decisions and record M-1/M-4 as closed.
2. **The field-coverage arithmetic did not reconcile.** 41 988 + 3 613 + 1 985
   = 47 586 against a 43 973 total, and the percentages summed to 108 %. The
   three figures were **nested, not parallel**: 3 613 is the *intermediate*
   measure "FYERS-listed but outside FYERS' ±50-strike window", of which 1 628
   are still fully served by Upstox's chain, leaving exactly **1 985** without a
   full-field source. **Corrected** to a two-class partition that sums to
   43 973 and 100 %.
3. **The provider allocation was unexplained.** 623 + 84 = 707 against a
   659-contract desired set. **Corrected and reconciled** (§3.13.1): 659 + 48 =
   707 exactly, where the 48 are **36 streamed option legs** (a different
   instrument class), **10 second-provider index keys** (owner ① deliberately
   subscribes every canonical index on both providers) and **2 dead NIFTYFPI
   keys** (the known R-10 defect). No canonical contract is assigned to two
   providers and no provider key is duplicated.
4. **The approval gate was internally stale.** **Corrected:** G-D lists only the
   open decisions, G-S is marked satisfied, and W-00 no longer asks for a closed
   decision to be resolved.

**V23 record — the current exposure was corrected against the target budget,
and the routing was traced and measured.**

1. **The exposure arithmetic was wrong in V22, and is corrected.** The
   **385 N** figure applies **only after the session gate exists**. **Today**
   the scheduler refreshes every registered chain **every 60 seconds with no
   session gate**, so the exposure is **1 440 × N** requests per calendar day.
   At the 60 s interval, **4 FYERS-routed chains require 5 760 requests/day**,
   **exceeding the FYERS Standard 5 000/day pool before any other consumer**;
   at the configured **30 s minimum** the same four require **11 520/day**
   (2.3× the pool). The target, with the gate, is **385 N** per session
   (**1 540** for four chains) and **0** on non-trading days.
2. **Routing was traced and measured (§3.19.1).** The scheduler passes **no
   provider**, so the **catalogue decides**: `_provider_for` consults the
   registry, which is *"the CATALOGUE is authoritative"*, and the read model
   `find_by_exact_symbol` returns **one row per provider** with
   `ORDER BY provider` — so **"fyers" sorts first and wins**. Measured on the
   live catalogue: **all 220 in-scope underlyings resolve to the FYERS row**,
   and `provider_for_key` confirms it. **The analytics chain requests are
   FYERS-routed** — by an alphabetical tie-break, not by policy — so they are
   charged to the FYERS pool. **No Upstox request is charged to the FYERS pool,
   and none is assumed to be.** A consequence: **SENSEX routes to FYERS, which
   lists no BSE_FO instruments**, so those requests cannot succeed.
3. **The duplicate-key risk was reconciled.** The registry is keyed by the
   **string**; the canonical cell is the **parsed** `(canonical_id, expiry)`.
   The prefixed `analytics:<id>:<expiry>` and the unprefixed `<id>:<expiry>`
   **both parse to the same cell** while remaining **two registry entries** ⇒
   one cell, two fetches per tick. The two shapes can coexist because the
   registry is rebuilt from **persisted** alerts written in different eras; the
   condition-v2 recursion is **safe** (the `set` collapses a repeated key under
   the same alert id). **Worst case: registry entries = 2 × distinct canonical
   cells.** The **target keeps one entry per canonical cell**, because the key
   is the normalised `(exchange, underlying, expiry)`.
4. **Retries, concurrency and the other consumers are separated (V22's
   treatment refined).** A failed chain consumes the **same per-tick slot**, so
   attempts per tick are **N** and retries are **inside** that count — the
   1 440 N figure therefore already includes retries. **Concurrency 4 spreads
   requests in time and does not reduce the count** — it is not a budget
   control. The other FYERS pool consumers are **unmeasured**; the index
   poller is **≈75/gated day** today and **0** in the target's normal
   operation; the **Upstox quota is unverified** and no aggregate is claimed
   for it.
5. **Unchanged:** the full-surface sweep stays **withdrawn**; DhanHQ excluded;
   ±20 around the **true** ATM when a cell is requested; multi-level depth and
   the deep tail excluded; **M-15/M-16 closed**; **§4 unverified**; and no
   unrelated decision was reopened.

**V22 record — the alert-driven refresh was traced, bounded, and given a
shared budget with the rest of the request paths.**

1. **Current behaviour (code-observed, §3.19.1).** Chains are registered from
   **enabled analytics alerts** whose metric needs one (`pcr_oi`,
   `pcr_volume`, `max_pain`, `iv_skew`) into
   `_dependents: dict[chain_key, set[alert_id]]` — a **set per key**, so
   several alerts on one cell already share one request per tick. The tick
   interval is **60 s** (minimum 30), concurrency 4, per-chain lock, retry
   5→60 s, `stale_after` 300 s.
2. **The two defects.** (a) **No session gate and no aggregate cap** — the
   request count is a function of the user's alert configuration. (b) **The
   same cell can be fetched twice**: `_extract_analytics_keys` yields
   `analytics:<id>:<expiry>` from `_dependency_key` and `<id>:<expiry>` from
   the fallback, both of which `parse_chain_key` accepts, but they are
   **different dictionary keys**.
3. **The worst case, for N registered chains (§3.19.2):** **N** requests per
   minute; **385 N** per trading session (385-min NFO session); **1 440 N** per
   calendar day if ungated. The **FYERS Standard pool is exhausted at N = 13**
   (385 × 13 = 5 005); the rate limit (50 rpm) binds only at N = 50, so **the
   daily pool binds first**; the catalogue ceiling of 689 cells would be
   **265 265** requests per session ≈ **53×** the pool; and with the duplicate
   hazard the effective N is up to **2×** the distinct cells.
4. **Session gating (D-28)** is **exchange-specific, date-specific and
   provider-supplied** — `/v2/market/timings/{date}` **[UPX-9]**,
   `/v2/market/holidays` **[UPX-10]** (holidays and **special timings**),
   `/v2/market/status/{exchange}` **[UPX-8]** — with **no routine live-price
   chain refresh outside the relevant session**.
5. **Price-based alerts while the market is closed** are stated, not implied:
   **live-move** alerts evaluate against the last snapshot, which is **stale**
   ⇒ **UNKNOWN** ⇒ they **cannot fire**, and re-arm on the first fresh
   snapshot; **session-close/derived** metrics are evaluated **once** against
   the labelled close snapshot; an **unavailable** chain leaves the alert
   UNKNOWN with the reason visible.
6. **One shared cached chain per (canonical underlying, expiry, provider)**
   with **single-flight**, so opening a cell an alert already maintains
   issues **no** request and a visible refresh updates the entry the alert
   reads.
7. **One shared per-provider budget with admission** (§3.19.5): analytics,
   visible-chain refreshes, reconnect recovery, index polling and retries all
   draw on it; a request that does not fit is **deferred**, not dropped. The
   **FYERS** Standard/Prime pools are **documented** and may be used as
   admission limits; the **Upstox** market-data quota is **unverified**
   **[UPX-7]**, so **no aggregate is claimed** there — only a configured local
   cap, and the residue is reported as unknown. Retries are **bounded by
   backoff *and* admission**, with a per-chain failure ceiling, and a
   deferred/stale/unavailable chain **never fires**.
8. **Fallback availability is exchange- and provider-specific (§3.19.7):** the
   **19 SENSEX cells have no alternate source** under an Upstox outage (0
   FYERS `BSE_FO` rows, **M-13**), and the plan **does not promise one**
   until FYERS BSE chain support is verified.
9. **Preserved:** the full-surface sweep stays **withdrawn**; DhanHQ excluded;
   ±20 around the **true** ATM when a cell is requested; multi-level depth
   excluded; the deep tail excluded; **M-15/M-16 closed**; and **§4 remains an
   unverified reconstruction**.

**V21 record — the data-refresh architecture was audited in the code first,
and the audit overturned the plan's own cost model before the design changed.**

1. **The 689-request background chain sweep was never implemented.** V16–V20
   specified a mandatory **670-cell FYERS + 19-cell Upstox** sweep; the working
   tree contains **no such path** (§3.18.1–§3.18.2, read 2026-10-21). The plan's
   cost model, its work items and its whole budget rested on a cost the
   product does not incur.
2. **The one path that can multiply requests without bound was not the one the
   plan named**: the **alert-driven analytics chain scheduler** — **one chain
   request per minute per alert-registered chain**, with **no exchange-session
   gate and no aggregate bound** (§3.18.2). It is now given both (W-29).
3. **The Futures Heatmap was confirmed to make no option-chain request** — it
   is a thin transport over `market.futures_board`, which reads the **quote
   store** with a TTL cache and single-flight; *"no per-underlying catalog/REST
   loops"* (§3.18.1, items 3–4).
4. **The target model (D-27)**: fetch a chain **on demand** (one request for the
   cell the user opens; the **underlying price from that same response** gives
   the **true ATM** and the **±20 window** at no extra cost), keep the visible
   window live over the **WebSocket**, **release** no-longer-needed keys on a
   change (applied at the **next authorize/re-subscribe cycle** — a
   code-observed fact), re-fetch **only the active cell** on reconnect or a
   stale cache, use a **narrowly scoped refresh** when the live feed cannot
   supply a field, and **stop outside the session**, retaining the last
   snapshot **labelled with its timestamp and session**.
5. **The corrected accounting (§3.16.5)**: **0** REST when idle · **≈ 1 per
   user action** · **0** outside the session · the index fallback **separate**
   and conditional (0 / 77 per session / ≤ 770 sustained). **A WebSocket tick
   is not a REST request, and one chain response covers many strikes.** The
   689 cells in the catalogue are **not** 689 requests per cycle.
6. **Capability gaps recorded, not assumed away** (§3.18.5): the visible window
   is **~82 option keys**, comfortable against Upstox's **2 000** **[UPX-1]**
   but unverified against the FYERS socket figure, so the window must be
   **sized per provider** from the configured entitlement; whether the live feed
   carries **IV and Greeks** for option keys is **unverified**, which would make
   the narrowly scoped refresh **required** rather than optional; the **19
   SENSEX cells have no alternate source** under an Upstox outage (M-13); and
   the **Upstox quota** and the demand-driven path's real behaviour are
   **unverified** — probes **P-1…P-5** are **PENDING** and **no live probe
   was performed**.
7. **All locked decisions are preserved** (D-22…D-27), **§4 remains an
   unverified reconstruction**, and the field requirements are **not**
   weakened to make the budget look feasible.

**V20 record — the primary feed's health state and the poller's lifecycle were
audited together, and the fallback was found to be an always-on duplicate.**

1. **FYERS is not a fallback — it is an always-on duplicate.**
   `app/server.py` constructs `_FyersIndexSource` and calls
   `await _start_fyers_index_poller()` **unconditionally** at startup, and
   **nothing in the poller consults the Upstox feed's state**: it runs in
   parallel with the primary WebSocket. The *intended* fallback semantics live
   only in the **read path** — `app/market_indices.py` resolves a FYERS
   candidate when Upstox has no quote, so an index is *"stale rather than
   unavailable"*. **So the design intent is a fallback and the scheduler
   implements a duplicate** (R-33).
2. **The health vocabulary already exists and is reused, not invented.**
   `brokers/upstox/feed.py` `status()` publishes `state` (`stopped` ·
   `connecting` · `authorizing` · `reconnecting` · `streaming` · `failed` ·
   `auth_required`), `state_updated_at`, `last_connected_at`,
   **`last_message_at`**, `last_error`, `last_exit_reason`, `reconnect_count`,
   `connect_attempts`, `not_ready_reason`, `recent_transitions` — and
   `subscribed_instruments` is `None` unless `state == "streaming"`.
3. **The specified lifecycle** (§3.17.6): **PRIMARY_HEALTHY → 0 requests**;
   **PRIMARY_UNHEALTHY → 0 requests** until the conditions are met;
   **FALLBACK_ACTIVE** when the symbol's own exchange session is open **and**
   the primary has been continuously unhealthy for **≥ 60 s** **and** has not
   been continuously healthy for the last **15 min**; **FALLBACK_COOLDOWN**
   after recovery, with the cached values left in place. The **stale threshold
   is 600 s** — twice the poller's own freshness — and is deliberately **not**
   the display threshold (`market_indices.STALE_MINUTES = 5` answers a UI
   question, not a fault question).
4. **Session eligibility is exchange-specific and provider-driven.** The 9
   symbols split **7 NSE + 2 BSE**, so **two** windows are required:
   `/v2/market/timings/{date}` **[UPX-9]** (NSE, NFO, BSE, BFO, MCX, CDS,
   BCD), `/v2/market/holidays` **[UPX-10]** (calendar + special timings),
   `/v2/market/status/{exchange}` **[UPX-8]**. **No hard-coded 15:30, and no
   single window for NSE and BSE.**
5. **Corrected request accounting, normal and failover separately** (§3.17.7):
   **normal = 0**; **failover = 1 per 300 s ⇒ ≈ 77 per session** (385 min ÷
   5 min); **sustained provider failure = 1 per 30 s ⇒ 120/hour and ≈ 770
   attempts across a 385-minute session**. The pool left for sweeps is
   **5 000** normal / **4 923** failover / **4 230** sustained failure ⇒
   **≈ 7.46 / 7.35 / 6.31 sweeps/day** ⇒ **≈ 52 / 52 / 61 minutes** inside
   the session. **These are arithmetic, not guarantees** — history, retries,
   the other shared-pool consumers and the Upstox quota are **unmeasured**,
   and the 770 case is a failure path of unknown duration.
6. **The coverage boundary is stated, not hidden:** the fallback covers **index
   quotes only** — under an Upstox outage the **19 SENSEX chain cells still
   have no source**, because the catalogue holds **0** FYERS `BSE_FO` rows
   (M-13). The index fallback does **not** close the option-surface gap.
7. **The two new work items are W-27** (session binding) and **W-28** (the
   failover lifecycle). **All locked scope decisions are preserved**, **§4
   remains an unverified reconstruction**, and the runtime probes stay
   **PENDING**.

**V19 record — the index poller was traced to the code, and the budget was
re-derived from the measured scheduler.**

1. **The poller's cost was never traced.** §3.17 measures it from the code:
   `app/fyers_index_source.py`, `FyersIndexSource` + `run_fyers_index_poll_loop`,
   wired unconditionally in `app/server.py`. Endpoint `GET {DATA}/quotes` with
   **one request for all due symbols** (≤ 50 symbols **[FY-2]**); **9** symbols
   (`default_index_symbols(_MAJOR_INDICES)`); **300 s** freshness; **30 s** loop
   wake; **5 rps** bucket; and a **hard-coded 09:15–15:30 IST** clock gate.
2. **The measured cost is 75 requests per gated day — not 288.** All due symbols
   are marked together, so the source makes **one** request per 300 s across a
   375-minute window: 375 ÷ 5 = **75**. The plan's 288 assumed **one request
   per symbol** *and* **24-hour operation**; both are wrong. The poller is
   **1.5 %** of the FYERS Standard pool, not 5.8 %.
3. **Six scheduling defects against D-26** (§3.17.3): **no** exchange-status,
   holiday, special-session or per-exchange awareness (**75 wasted requests on
   every weekend and holiday** ≈ **8 550/year, 31 %**); a hard-coded close
   **10 minutes short** of the current **15:40** F&O close **[NSE-1]**; **no
   per-exchange** window for the BSE symbols; a **fallback that runs even when
   the Upstox WebSocket is the healthy primary**; an **unbounded 30 s retry**
   after a failure (up to **2 requests/minute**, ≈ **24 % of the daily pool in
   one hour**); and an **inert 60 s interval guard**. They become **W-27**.
4. **Is there a concrete reason to poll outside market hours? — No.** For live
   prices the exchange is the only source of change, and while it is closed the
   last value *is* the session close. The out-of-session work that genuinely
   exists — the **instrument-master/expiry roll** and the **previous-session OI
   snapshot** — belongs to the catalogue sync and the previous-OI recorder
   (`PreviousOiRecorder` makes **no provider calls**), not to this poller.
5. **The corrected FYERS budget, by cost type:** chain sweep **670** per
   sweep · index poller **≈ 75** per gated day (**0** on non-trading days once
   D-26 is applied) · **history** (user-driven) **not measured** · **retries**
   **not measured** · pool **5 000/day** ⇒ **4 925** for sweeps ⇒ **≈ 7.35
   sweeps/day** ⇒ **≈ 52 minutes** between full-surface sweeps **inside the
   session** — a **target, not an SLA**.
6. **Upstox's 21 requests reconciled**: **19** = the chain requests for the
   **19 SENSEX cells** (T2); **2** = `GET /v3/market-quote/quotes` for the **659
   desired contracts** at ≤ 500 keys per request **[UPX-2]** — **proposed**, not
   an implemented path (the desired set is streamed; `app/market_data.py`
   exposes no quotes call site), belonging to **T1** and not to the background
   sweep. The count is right; the two tiers are not the same tier. **The
   Upstox market-data quota remains unverified** **[UPX-7]**.
7. **The session model and every locked decision are preserved unchanged**
   (D-22…D-26), **§4 remains an unverified reconstruction**, and the runtime
   probes stay **PENDING**.

**V18 record — the §4 source could not be recovered, the ±20 maximum was
reconciled, and the refresh model was re-based on the trading session.**

1. **§4 was NOT restored, and the blocker is documented precisely.** The
   instruction was to restore §4 from the earlier **V13** plan copy. That copy
   **does not exist in this environment**. Searched on 2026-10-21: the **Git
   object store** (`git fsck --unreachable` — all 85 unreachable blobs and
   every unreachable commit) — **no blob of the plan exists**, because the file
   was **never staged and never committed**; **all branches, remotes, stash,
   reflog and worktrees** — no copy; the **index** — not among the 240 staged
   files; **editor local history** — no such directory; and **17 442 text
   files** across `D:\Temp`, the user `Temp`, `Roaming`, `Documents`, `Desktop`
   and `Downloads` — the only hits are the live plan and this session's own
   scratch scripts, which hold the **heading** only, as an insertion anchor.
   **Per the instruction, no second reconstruction was produced.** §4 is
   relabelled **"UNVERIFIED RECONSTRUCTION, NOT A RESTORATION"**, the body is
   the V17 one, and the blocker is recorded in §4, §12 item 17 and the blocker
   list below. **Closing it needs an external V13 copy — or the plan committed
   to Git so it cannot be lost again.**
2. **The ±20 maximum was reconciled exactly.** The naive product
   **689 × 41 × 2 = 56 498** overstates it because **191 cells** have a listed
   ladder shorter than the 41-strike window: the measured cap is
   **Σ 2 × min(41, ladder strikes) = 52 250**, the **4 248** difference being
   **2 × Σ max(0, 41 − ladder)** over those cells — 166 NSE F&O stock
   (ladders 16–40), 14 NSE index (30–39) and 11 SENSEX (13–18). After the
   **104** one-leg strikes the refined bound is **52 146 ≤ maximum ≤ 52 250**.
   **No cell is excluded from the scope** — all 689 get a chain request; what
   differs is each cell's capacity to fill the window. The **9 087** figure
   stays a **proxy** (ladder-median ATM) until live prices and the true ATM
   windows are verified.
3. **The refresh model was re-based on the trading session** — the correction
   that matters most, because the market is **not** open 24 hours. NSE equity
   derivatives run **09:15–15:40 IST** (**≈385 min**, extended from 15:30 w.e.f.
   2026-08-03) **[NSE-1]**; F&O has **no** CAS session (`cas_eligible_status`
   is omitted for `NSE_FO`) **[UPX-8]**. The session window is **read from the
   provider per date and per exchange** — `/v2/market/timings/{date}` **[UPX-9]**,
   `/v2/market/holidays` **[UPX-10]**, `/v2/market/status/{exchange}` **[UPX-8]**
   — and is **never hard-coded**. The ≈7.35 sweeps a day are therefore spent
   **inside the session**: the required surface refreshes **about once an hour
   while the market is open** (≈52 min per cycle), and **nothing is refreshed outside it**, because
   the values are the session close. The V17 "**≈ 3.4 h average**" is
   **withdrawn as the operating statement** — it divided the sweep rate by 24
   hours, spreading seven sweeps across the seventeen hours when nothing
   changes.
4. **D-26** records the maintainer's requirement that the application *show
   what we need to see* rather than synchronise everything all the time: the
   visible tier is refreshed **on demand**, the background tier is a **slow
   safety net** (so a cell nobody has opened is at most ~an hour old when
   someone does), and **every displayed surface carries its own age and the
   session it belongs to**.
5. **The locked decisions are preserved unchanged**: DhanHQ **excluded**
   (D-22), ±20 around the **true** ATM **surface-wide across every cell**
   (D-23), multi-level depth **excluded** (D-24), deep tail **out of scope**
   (D-25). **M-15 and M-16 remain closed**; **M-12** remains a re-entry
   condition only.
6. **The budget remains separated by provider and cost type**: FYERS
   **670** chain requests per sweep + the **fixed ≈288/day** poller against
   the **5 000/day** Standard pool (**4 712** remaining); Upstox **19** cells
   (**21** calls per sweep) in a **separate** budget whose market-data quota
   remains **unverified**. The interval stays a **target, not a SLA** — the
   other FYERS consumers, the retries and the actual runtime behaviour are
   unmeasured.
7. **Runtime validation stays pending**: probes **P-1…P-5** against
   **Upstox and FYERS only** — no credentials exist in this environment.

**V17 record — the maintainer's decisions of 2026-10-21 were recorded, two
decisions were closed, and the request budget's arithmetic was corrected.**

1. **Four decisions recorded** (§2.2, D-21…D-25): **D-22** DhanHQ is
   **excluded** — no credential, no probe, no integration, no order routing;
   **D-23** ±20 strikes around the **true** ATM define the required option
   surface, **surface-wide**, across **every** underlying-expiry cell;
   **D-24** multi-level depth is **not** required — the chain's top-of-book
   bid/ask satisfies the requirement, and per-symbol depth polling is
   **removed** from the proposed budget; **D-25** the deep-ladder tail beyond
   ±20 is **out of scope for now**.
2. **M-15 and M-16 are CLOSED** — in the ledger (§2.3), in the approval-gate
   table (§9) and in the work items. **M-12** is no longer a blocker of the
   required surface: its subject — the IV/Greeks gap on the deep wings — is
   out of scope (D-25), and it reopens only if the tail returns.
3. **The budget arithmetic was corrected.** V16 charged the index poller
   **per sweep** ("670 + 288 = 958 per sweep ⇒ 5.22 sweeps/day ⇒ ≈ 4.6 h").
   The poller runs ≈ 288 times a day whatever the sweep count, so it is a
   **fixed daily** cost. The correct figures are: **670** requests per sweep,
   **≈ 288** poller calls/day, a **5 000/day** pool, **4 712/day** remaining,
   **4 712 ÷ 670 ≈ 7.03 sweeps/day** *(as stated in V17; **corrected by V19**)*, and **one sweep every ≈ 3.4 hours on
   average**, with each sweep taking **13.4 min** at 50 rpm.
4. **3.4 hours is NOT a freshness SLA**, and the plan no longer presents it as
   one. It is the **theoretical maximum** for the background tier: it assumes
   the whole of the 4 712 goes to the background sweep, that the documented
   50 rpm applies to the aggregate workload as modelled, and that the other
   FYERS consumers, the retries and the actual runtime behaviour cost
   nothing — none of which is measured. **The near-real-time guarantee lives
   in the active/visible tier (T1)**, refreshed on demand from the same cached
   sweep; the background tier reports the **age of the sweep it was served
   from**.
5. **The Upstox side is a separate budget and its quota stays unverified.** The
   **19** SENSEX cells cost **19** chain requests per sweep (**21** with the
   spot/futures quotes); the optional redundancy sweep is **670** more
   (**691** total) and is **not** counted in any freshness statement. The
   ≈ 3.4-hour figure is a **FYERS-pool statement only**.
6. **The deep-ladder tail is out of scope**, so it costs **0** requests; its
   cost is retained in §3.12.4 as the **re-entry cost** (5 934 depth + 119
   quote per sweep, at which point the Standard pool no longer fits).
7. **The pending runtime validation is preserved**: probes **P-1…P-5** against
   **Upstox and FYERS only** remain **PENDING** — no credentials exist in this
   environment — and every claim about what a chain actually returns stays
   labelled unverified. The **current-OI probe is no longer required** for the
   required surface; it is a re-entry condition only.

**V16 record — the option surface was re-based on the correct unit of work,
and the request budget fell by an order of magnitude because of it.**

1. **The unit of work was wrong.** V15 priced the option surface symbol by
   symbol (87 842 symbols) and priced the **5 934** deep-ladder symbols as
   **5 934 individual per-symbol REST depth calls**. The unit is the
   **(underlying, expiry) cell**: **one chain response covers every symbol in
   that cell**, carrying price, volume, bid/ask, current OI, previous OI, IV
   **and** Greeks.
2. **The maintainer set the scope on 2026-10-21** (D-21): *"20 strikes on
   either side of ATM is enough for us."* Under it the deep-ladder tail leaves
   the required surface, the **class-C gap is 0**, and the required surface
   costs **670** FYERS chain calls + **19** Upstox chain calls with **0**
   quotes and **0** depth calls (§3.16.2).
3. **The budget now fits and the freshness becomes a real guarantee.** FYERS
   Standard uses **958** calls/day of 5 000 → **5.22 sweeps/day** → **all 689
   cells refresh every ≈ 4.6 hours** (≈ 13 min per sweep at 50 rpm). On Prime
   the rate limit binds, not the pool. The Upstox side is **21** calls per
   sweep against an **assumed** bucket.
4. **The four tiers are separated** (§3.16.4): focused **WebSocket**
   streaming (0 REST), **active/visible chain refresh** (1 request per cell),
   **background full-surface refresh** (1 request per cell, all 689), and the
   **deep-ladder tail** (out of scope under D-21; the V15 budget applies under
   reading (b)).
5. **The ATM problem is answered without an extra request**: a chain response
   carries the underlying price, so the true ATM and the ±20 window are a
   **second phase of the same response**. The **scope size** is therefore a
   proxy-measured lower bound (9 087 symbols) against a theoretical maximum of
   52 250 — while the **request budget is exact** either way, because it is
   per cell.
6. **The provider capability matrix is now explicit** (§3.16.6), separating
   **documented capability** from **runtime-verified coverage** for Upstox's
   chain, FYERS's chain, FYERS's depth and DhanHQ's chain. The only capability
   difference that matters for the required surface is FYERS's **documented
   ±50 strike cap**.
7. **DhanHQ was evaluated from documentation only and is NOT adopted** (§3.16.7):
   a JWT access token (24 h) plus a **paid Data API subscription**; **integer
   security IDs** with a six-field composite master and **no exchange trading
   symbol**; a **1-request-per-3-seconds** throttle with no documented daily
   cap; documented **NSE, BSE and MCX** option coverage. It adds no coverage
   the two configured providers' chain endpoints do not already supply, and it
   would add a third identifier form to a broker-neutral architecture.
   **No credential was sought, no request was made, and no order is routed
   through it.**
8. **The runtime comparison is PENDING**: no Upstox or FYERS credentials exist
   in this environment, so probes P-1…P-5 are specified (§3.16.9) and every
   claim about what a chain actually returns is labelled unverified.
9. **Two decisions are open**: **M-15** (is ±20 surface-wide or
   presentation-only?) and **M-16** (is multi-level market depth required?).
   Neither reopens the settled scope, and neither is assumed away.

**V15 record — the request budget and the chain coverage were reconciled
against an exact 689-cell inventory, and the plan's own arithmetic was
re-derived rather than trusted.**

1. **The "77 chain requests" figure was wrong, and so was its label.** V14's
   "**40** FYERS chain cells" was the number of cells containing *uncovered*
   contracts — the class-C cells — **not** the number of FYERS chain requests.
   From the exact 689-cell inventory (§3.15): **670** cells need a FYERS chain
   request, **37** additionally need an Upstox chain request, **18** need both,
   and **0** cells need no chain request — every cell holds symbols whose
   IV/Greeks only a chain response can supply. The **complete-surface pass is
   707 chain requests**, and the per-provider budget is **7 011 FYERS calls**
   (670 chain + 119 quote + 5 934 depth + ≈288 poller) and **39 Upstox calls**
   (37 chain + 2 quote) — **separate pools, never summed**.
2. **The freshness claim was over-stated.** The FYERS Standard pool still does
   not fit a complete pass (7 011 > 5 000), but the **≈1.3-day** tail rotation
   is withdrawn: the arithmetic on the corrected figures gives
   **≥ ≈1.7 days**, and that is now stated as a **lower bound** on a rotation
   that assumes *every other FYERS consumer costs nothing* — history, other
   depth, retries and the unmeasured consumers are not priced, so the true
   interval may be materially longer. **No guaranteed refresh cadence is
   claimed.**
3. **The reachability partition was wrong at strike level and is
   re-measured per option symbol.** The **1 985 strikes (4.5 %)** figure could
   not be reproduced: it counted a strike as served whenever the strike
   *value* appeared in either provider's ladder, even when one of its two legs
   was unserved. The reproducible partition is **41 004 strikes fully served
   (93.25 %)** and **2 969 strikes (6.75 %)** carrying **5 934** option symbols
   with no documented IV/Greeks source — the symbol count was already right in
   V14, the strike count and the share were not.
4. **Every cell is now enumerated** (§3.15) with its canonical underlying and
   expiry, the endpoint that serves it, its tier, the symbols it yields and
   the identities that must hold per row, so the budget is a **derived** number
   rather than an asserted one. The plan states the **regeneration** obligation
   (W-19, W-24) so a catalogue change re-derives the budget instead of
   silently invalidating it.
5. **The per-provider separation is enforced in the text**: the 39 Upstox calls
   are never charged to the FYERS pool, and Upstox's rate limit remains an
   **assumption [A] [UPX-7]**, not a provider guarantee.
6. **SENSEX's provider dependency was reconciled and its direction corrected.**
   The catalogue holds **no FYERS BSE derivative rows**, so the 19 SENSEX cells
   need **only** an Upstox chain request and contribute **zero** FYERS calls;
   SENSEX is an **Upstox-only dependency** today. V14 implied the opposite —
   that FYERS calls for SENSEX were conditional — and M-13's effect is now
   stated correctly: adding the FYERS `BSE_FO` master would *create* FYERS-only
   SENSEX symbols, and any that fell outside the FYERS ±50 window would
   *create* class-C symbols.
7. **No settled decision was reopened and no scope changed.** The IV/Greeks
   boundary, the current-vs-previous OI distinction, M-2, M-5, M-12, M-13 and
   the optional M-14 are unchanged.

**V14 record — the plan's own consistency, arithmetic and dependencies were
reconciled, and six classes of defect were found and repaired.**

1. **Active scope statements still contradicted the closed decision** (§1.1,
   §1.2, §1.3, §6.4). The scope was re-asserted as settled in the
   **active text** — not merely annotated — and **M-1/M-4 were removed as
   gates** from the decision ledger, the dependency map and the work items;
   they remain recorded as **closed decisions** that W-04 and W-09 *apply*.
2. **The FYERS request budget was wrong in two directions** and is withdrawn.
   V13 budgeted **one depth request per strike** (1 985) although the endpoint
   takes **one symbol per request** and each strike carries a CE **and** a PE
   contract, and it carried **953** quote calls for the whole FYERS-only tail
   although the FYERS chain already serves the in-window tail. Recomputed at
   **option-symbol level** (§3.12.4): the minimum complete-surface pass is
   **40** FYERS chain cells + **37** Upstox chain cells + **119** FYERS quote
   calls + **5 934** FYERS depth calls + **2** Upstox quote calls + the
   **existing ≈288/day** index poller ⇒ **≈6 420 calls** — which **does not fit**
   the FYERS Standard 5 000/day pool. The plan now states a **tiered freshness
   schedule** (active workspace → visible underlyings → universe cells → a
   **≈1.3-day rotation** for the tail's OI/previous OI) and **makes no daily
   full-refresh claim** on that tier; on Prime the same pass is ≈1.3 % of the
   pool, bounded by 50 rpm.
3. **Current OI and previous OI were conflated.** Upstox `/v2/market/oi` is a
   candidate for **current** per-strike OI on contracts outside Upstox's master
   — **unestablished** until a probe establishes its strike coverage *and
   response semantics* — and it documents **no previous-session OI**, so it
   **cannot** replace the FYERS depth cost. FYERS depth supplies OI and
   previous OI **only when `oiflag` is true**. The field states
   `field-unestablished` (current OI) and `field-unavailable` (IV, Greeks) are
   now distinct, and the **probe is a separate evidence task** that does not
   resolve the IV/Greeks gap.
4. **The union was recomputed from exact provider keys** with the canonical
   NIFTYFPI identity (§3.13): **current 517/85**, **post-W-03 517/84**,
   **capacity-first 623/83 = 706 = 659 + 47**, with the 47 decomposed exactly
   (36 streamed option legs · 7 second-provider F&O index copies · **4 general
   quote subscriptions outside the F&O universe**). The "ten additional index
   keys" is now classified, and **invalid keys are excluded from valid
   coverage**.
5. **The IV/Greeks gap is bounded honestly**: it is *an absence of a documented
   source among the evaluated endpoints of the configured providers*, **not**
   proof that no external source could exist. The requirement is not redefined;
   the full-data acceptance criterion **stays blocked**.
6. **M-14 is now consistent**: optional, outside the dependency chain, and it
   blocks neither W-26 nor the core data-surface work. *(The SENSEX sentence
   here is superseded by the V15 record above, which shows the dependency runs
   in the opposite direction.)*

**V13 record — an independent re-read of the official FYERS and Upstox
documentation found eight further defects in this plan, all now applied.** C1
the Upstox REST rate bucket was presented as a documented market-data limit when
the documented bucket names only *"holdings, positions, funds, historical
candles etc."* **[UPX-7]** — every Upstox-side sweep and freshness figure is now
labelled **assumption-based**; C2 the plan omitted Upstox's **`/v2/market/oi`**
**[UPX-6]** — a per-strike OI endpoint **already implemented** in MarketHub —
so the OI conclusion for the 1 985 strikes is weakened from *"unavailable"* to
***"not established"*** and becomes a route to **probe**; C3 FYERS depth's OI is
**conditional** on `oiflag` **[FY-3]**; C4 Upstox's option-greeks endpoint also
carries `oi`, `last_price`, `volume`, `cp`, `ltq` **[UPX-5]**; C5 **FYERS BSE
Equity Derivatives is a documented capability** **[FY-7]**, **[FY-5]**, so the
SENSEX single-provider dependency is *provisional on a sync omission*; C6
"no strike cap" is now "documents no cap" **[UPX-3]**; C7 Upstox `full`'s
tier-independence is an **assumption [A]** **[UPX-1]**, so "the Upstox side does
not depend on G-T" is now conditional; C8 the Upstox chain's **MCX exclusion**
**[UPX-3]** and the FYERS `timestamp` semantics (**[E]** convention, not
documentation) **[FY-4]** are recorded. §3.14 now carries a **source register**
with the URL, the date read and the evidence class for every API claim, and
§3.12.4b records the evaluation of FYERS **`/data/futures-chain`**: documented,
BSE-SENSEX-capable, but it **changes no capacity figure** and is **not
adopted**; its one candidate use (a *currently-trading* check) is a new
maintainer decision (**M-14**) and changes no architecture.

**What is blocked, and on what.**

1. **M-2** — the application ceiling: retire it (recommended) or retain it as a
   guard that cannot bind below the provider's usable capacity; plus the 800
   decision's date, approving artefact, and whether any environment already runs
   800.
2. **M-5** — the headroom floor (2 recommended), the account tier (gate
   **G-T**), and — with R-30 — the failover trade-off.
3. **M-12** — the **narrow** IV/Greeks trade-off on the **2 969** strikes that
   carry **5 934** option symbols. Asked only because the limitation is now
   proven, not as a licence to downgrade the requirement. The **current-OI
   probe** is a **separate evidence task** and does **not** resolve it.
4. **M-13** — how the BSE SENSEX single-provider dependency is recorded.
   **SENSEX stays in scope either way**; FYERS BSE Equity Derivatives is now
   documented, so the remaining questions are per-symbol and permission-bound.
5. **M-15** — **CLOSED 2026-10-21 (D-23)**: ±20 strikes around the **true**
   ATM, **surface-wide**, across every underlying-expiry cell. The design basis
   of §3.16 is the confirmed requirement.
6. **M-16** — **CLOSED 2026-10-21 (D-24)**: multi-level depth is **not**
   required; the chain's top-of-book bid/ask satisfies it, and per-symbol depth
   polling is removed from the budget.
7. **M-14** — whether FYERS `/data/futures-chain` is used as a
   *currently-trading* check for nearest-two-futures selection. **Optional**:
   it changes no capacity figure and **gates nothing** (it is not a dependency
   of W-26 or of the core data-surface work).
8. **M-6 / M-7** — the granularity of "one provider per contract" and the
   provider-availability vocabulary.
9. **M-8 / M-9 / M-9b / M-10** — TBT scope, change-set integration, the
   previous-OI fallback, and whether the stages were approved before coding.
10. **M-11** — the overflow priority's *kind-versus-role* gap.
11. **The account tier (R-05b, gate **G-T**)** — the one external fact still
    missing; it determines the FYERS usable capacity (90 Standard vs 4 500
    Prime) and, under reading (b), the FYERS tail's refresh cadence. **The
    Upstox side does not depend on it — but only *assumed* so** (C7), so that
    claim is now conditional too.
12. **Evidence tasks that are not decisions** — (a) the **chain-key probes
    P-1…P-5** against **Upstox and FYERS only** (§3.16.9): the exact returned
    option keys vs the catalogue, the underlying price, the true ATM, the
    measured scope size, and the real Upstox market-data rate bucket — **no
    credentials exist in this environment, so these are PENDING**; (b) the
    **current-OI probe** for Upstox `/v2/market/oi` (strike coverage *and*
    response semantics), now scoped to the **background tier**; (c) the
    **per-symbol FYERS BSE SENSEX** verification (M-13) — SENSEX is an
    **Upstox-only dependency** today, and M-13 would *add* a FYERS route whose
    class-C share must then be re-measured; (d) the **account tier** (G-T);
    (e) **the remaining FYERS pool consumers** — history, depth for other
    purposes and retries — which are **unmeasured**, and without which the
    background tier's **≥ ≈1.7-day** rotation stays a **lower bound** and no
    guaranteed cadence can be claimed for it. **DhanHQ is not among them**:
    it was evaluated from documentation only, is **not adopted**, and no
    credential was sought, no request made and no order routed through it.
13. **W-01** — the change-set integration gate.
14. **The §4 source recovery** — the **V13** plan copy **could not be
    accessed** in this environment: the plan file was never staged or
    committed, no unreachable Git object holds it, and no backup or editor
    history exists (§4). **§4 therefore remains an unverified reconstruction**,
    and no §4 claim can be certified until an **external copy** is supplied.
    **The plan file should also be committed**, so a future revision cannot be
    lost again.

**Order of work once the gates are discharged.** W-10 (ceiling) → W-07 (capacity
model) → W-08 (shared union, capacity-aware routing, P-5 overflow, truthful
coverage) → **W-09** (apply the settled scope) → **W-24 → W-25 → W-26** (the
complete data surface) → **W-27** (session-bind the index poller) → **W-29** (the demand-driven data layer) → W-11 → W-12 → W-13, with W-19/W-20/W-22 as the
verification chain. **M-1 and M-4 are closed and gate nothing.**

**Status.** Not converged — the open decisions, the external facts, the
integration gate and the §4 document-recovery blocker listed above remain — but the analysis is structurally
complete, every figure is either measured, documented, derived from the
689-cell inventory, or explicitly labelled as projected or as a lower bound,
and the recommended direction is unambiguous: **drive coverage from the providers' real capacity
with an explicit, justified headroom; deliver the complete option surface from a
prioritised, budgeted, field-aware cache; and report the one proven gap
honestly rather than hiding it behind a "complete" label.**

> **No implementation step in this document has been performed.** No source,
> test, configuration, schema, migration, frontend file or runtime data was
> created, modified or deleted. The runtime database was never altered: all
> measurements were made against a **copy** in a temporary directory. No Git
> state was changed. No live market-data probe, authentication operation,
> catalogue sync, migration or application-startup experiment was run; the only
> outbound requests were read-only fetches of **public, unauthenticated**
> documentation and instrument-master URLs. **Awaiting explicit maintainer
> approval.**
