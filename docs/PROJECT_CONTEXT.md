# MarketHub — Project Context & Handoff (Living File)

> **Purpose:** durable handoff so a new chat / AI-coding-agent session can resume
> MarketHub work from the *actual* project state without re-explaining. Read this
> plus `AGENTS.md` and `docs/ARCHITECTURE_BOUNDARIES.md` before asking the
> maintainer anything. Verify the real repo state before acting.
>
> **This is a living file.** Update it after every meaningful investigation,
> finding, approved stage, test run, decision, or blocker change. Do not rewrite
> the whole history — keep a concise current-state section + a dated change log,
> and mark superseded claims as such.

---

## 0. How to use this file (for the next session)

1. Read **this file** → `AGENTS.md` → `docs/ARCHITECTURE_BOUNDARIES.md` §12/§13/§21.
2. Re-inspect the *actual* working tree (branch/HEAD, in-flight diffs, catalog state). Counts in this file are **historical snapshots**, never assumed current.
3. **Do not start coding** because an investigation looks complete. Present findings + plan, wait for explicit approval (§2 Standing Rules #1).
4. If a material assumption is unresolved, **stop and report** — do not guess (#2).
5. Never represent source inspection, a search hit, a test-file's existence, or a
   theoretical implementation as a *passing test* or *live success*.

---

## A. Project purpose & boundaries

**What MarketHub is:** a broker-neutral, market-data-only desktop/platform app:
- **F&O foundation** (option chain, OI analytics, futures heatmap) is the current priority.
- A **human-facing WebUI** and an **MCP server** both surface the same canonical
  market-data services (no separate data paths).
- **Market-data only.** No order placement, no trade execution. Do not add trading.

**Agreed instrument scope (fixed, do NOT expand without approval):**
- **NSE stock F&O** — every currently eligible equity-derivatives underlying +
  its active futures/options (expiries, strikes, CE/PE, metadata, underlying quotes).
- **Major NSE index F&O** — the active NSE index-derivatives universe.
- **BSE SENSEX F&O** — SENSEX spot + active futures/options.

**The six canonical NSE index-F&O underlyings** (from prior catalogue
investigation; **NIFTYFPI is explicitly first-class**):
`NIFTY`, `BANKNIFTY`, `FINNIFTY`, `MIDCPNIFTY`, `NIFTYNXT50`, `NIFTYFPI`.
Defined in `market/index_registry.py` (`CANONICAL_INDICES`, `is_fno` flag).
These are *starting points, not immutable* — re-verify the live NSE active
index-derivatives list before hardcoding; INDIA VIX is **not** an F&O underlying.

**Providers:** **Fyers** and **Upstox**. Both must work **independently and
together** (Fyers-only / Upstox-only / dual). Neither is a hidden dependency.

**Out of scope (do not do without explicit approval):** other exchanges, the
full NSE benchmarking-index catalogue, the broader BSE index family, MCX/NCD/COM,
new data vendors, order/trade execution, any new analytics implementation.

---

## B. Standing project rules (maintainer operating requirements)

1. **Do not start coding just because an investigation looks complete.** Discuss
   findings + proposed plan; wait for **explicit approval** before implementation.
2. **If a material assumption is unresolved, stop and report** — never guess.
3. **Preserve all staged, unstaged, modified, and untracked work.**
4. **Never** run destructive/rollback git ops: `reset`, `clean`, `checkout`,
   `restore`, `stash`, rebase, history manipulation.
5. **Never revert an entire file** that contains unrelated in-flight work.
6. Prefer **durable, broker-neutral, configurable** architecture over
   temporary workarounds.
7. **Do not hardcode provider-specific assumptions** into shared business logic.
8. Avoid unnecessary redesign, scope expansion, and re-running investigations
   whose evidence already exists.
9. **Never claim code/tests/live integrations work without evidence.**
10. The agent is the **technical architect + project manager** (investigate,
    explain, propose, wait for approval) — *not* the implementation engineer
    who writes code unprompted.

---

## C. Architecture & settled decisions

**Settled (approved / locked):**
- **Provider-neutral instrument identity** + normalized market-data contracts;
  canonical IDs resolve both providers to one identity
  (`app/market_identity.py`: `EXCH:TYPE:...`).
- **Backend-owned** canonical identity, provider routing, subscription
  coordination, normalization, validity, and freshness decisions.
- **Frontend consumers use stable canonical identities** — not patched around
  provider-specific keys.
- **Both providers independently usable**; neither is a hidden mandatory source.
  Provider preference is a **tie-breaker only**, never a routing gate.
- **Registry/configuration/adapter-driven** routing, capabilities, and budgets.
- **Contract routing** selects one provider per canonical contract per the
  agreed routing rules; F&O universe = spot + **nearest two futures expiries**;
  option chains are **retrieved on demand** and only **focused ATM windows are
  streamed** within the approved subscription budget.
- **Desired F&O state is reconstructed** from durable config + current
  catalogue/provider availability — **not** from a manual "apply" step.
- **FROZEN / protected-core** (`AGENTS.md`, `ARCHITECTURE_BOUNDARIES` §5/§21):
  `app/auth/*`, credential store, token/session persistence, broker login,
  session restoration, application startup, source/feed lifecycle, persistence
  schema, canonical market models. The **canonical write-path re-key is a
  separate protected-core concern** and is NOT approved.

**Proposed (NOT yet approved):**
- Additive **central quote coordinator** (selected-quote over the existing
  provider-keyed store) — interim; the true re-key stays protected-core.
- **Fyers `greeks=1` + `oichp` option-chain wiring**, **Upstox V3 `previous_oi`
  normalizer mapping**, analytics **scope labelling** (see §D/§E/§I).

**Still requiring confirmation:** the live-evidence items in §I (U1–U6) and
maintainer decisions D-1…D-4. **No implementation has been approved — coding authority is NOT granted by the plan.**

---

## D. Investigation history & verified findings (with evidence classes)

Evidence classes used throughout the audit: **[C]** source inspection ·
**[T]** a test *actually executed* · **[D]** official docs (URL+date) ·
**[R]** authenticated live response. **No [R] result has ever been captured in
these read-only rounds; nothing below is a live claim.**

### D.1 Capacity model (verified [C], R7/R8/R10)
- `effective_budget(cap) = cap - max(int(cap*0.10), 50)` —
  `providers/capabilities.py:44-51` (`RESERVE_FRACTION=0.10`, `RESERVE_FLOOR=50`).
- Raw results: cap **100→50**, **5000→4500**, **2000→1800** (and 500→450).
- Application cap `MAX_INSTRUMENTS_PER_PROVIDER=500`
  (`app/subscriptions/service.py:30,721`) is a **separate** limit, applied as
  `min(effective_budget(plan_cap), 500)`.
- **Final budgets: Fyers Standard=50, Fyers Prime=500, Upstox Full=500.**
- **Open finding:** `brokers/fyers/capabilities.py:27` hardcodes
  `MODE_KEY_LIMITS={"full":5000}` — a **Prime assumption**. Default account is
  **Standard (100)**; no documented interface exposes the plan. A centralized,
  Standard-default + explicit Prime override is proposed (stage S2), not done.
- **Note:** R2/R3 prose once mis-stated these as "90 / 4450" — **superseded**;
  the correct values are 50 / 4500 / 1800 (verified against code in R7/R8/R10).

### D.2 Fyers option-chain Greeks/IV (verified [C+D], R9/R10)
- Official Fyers Option Chain API (https://fyers.in/api-docs/v3/api/market-data/option-chain/,
  updated 2026-10-14 [D]) documents a **`greeks=1` request param** → nested
  `greeks{delta,gamma,theta,vega,iv}` per strike.
- `brokers/fyers/rest.py::option_chain` **does NOT send `greeks=1`** (R9 G1) →
  the response lacks greeks; Fyers Greeks/IV are effectively unavailable until
  this one param is added. **This corrects the earlier "Fyers has no Greeks" claim.**
- `market/normalize/fyers.py` **already parses** the nested `greeks` object.
- `oichp` (OI-change % per strike) is documented [D] but **not mapped** in the
  normalizer (R9 G2).

### D.3 Upstox V3 REST `previous_oi` (verified [C+D], R8/R10)
- Upstox **V3 Full Market Quotes** `/v3/market-quote/quotes` returns
  `previous_oi` for F&O [D]. `market/normalize/upstox.py::quote_from_rest`
  maps `oi→open_interest` but **drops `previous_oi`** (R8 N1 / R10 G4).
- **Upstox WebSocket `full` mode does NOT carry `previous_oi` on the wire** —
  it comes from REST or the persisted fallback. **Do not conflate WS and REST fields.**
- Adding the `previous_oi` mapping is additive and **does not conflict** with the
  provider-OI precedence (`market/oi.py`: provider-prev-OI → persisted →
  unavailable) or the staleness-gated merge (a *newer* REST value wins; an
  *older* snapshot is rejected — no overwrite of newer data).

### D.4 PCR correctness & scope (verified [C], R10)
- **Standard PCR = total put OI ÷ total call OI**; **volume PCR = put vol ÷ call
  vol** (`market/analytics/option_chain.py::compute_pcr:102`, `compute_pcr_volume:125`).
  R10 confirmed the **formulas are correct, NOT inverted**. (R9's prose implied
  call÷put — that was the *report* inaccuracy, not the code.)
- **Scope:** these sum over the **returned strike rows** (window-scoped). The
  Fyers **aggregate `callOi`/`putOi` are unused** by the normalizer.
- **`callOi`/`putOi` whole-expiry semantics are UNVERIFIED** (R11): the docs
  say "total open interest for call/put options" but do **not** specify
  whole-expiry vs window; no live response captured. **Keep whole-expiry PCR
  BLOCKED** unless a live check proves the aggregate spans the full expiry.
- **Fyers max-pain is bounded by the returned strike window**
  (`strikecount` max 50/side [D]); do **not** label it whole-expiry.
- **Upstox** provides full-strike OI via `/v2/market/{oi,pcr,max-pain}`
  (`upstox_analytics.py`), distinct from the computed window-scoped values.
  **Provider-aggregate vs locally-computed window analytics are different
  numbers and must be labelled as such.**

### D.5 Identity / quote-selection / subscriptions (verified [C], R6–R10)
- **Native-keyed quote store** (`market/service.py:207-210` raw lookup; canonical
  computed at `:251` but not the key) → two slots per instrument; cross-provider
  reads don't converge. Coordinator re-key = protected-core.
- **Frontend joins on provider-specific keys** (`preferences().key` =
  `upstox_key`, `app/subscriptions/service.py:231`; `IndicesWidget.tsx`) — the
  "repair key mismatch per component" anti-pattern to remove via canonical joins.
- **Routing is state-only** (`fno_coverage.py:210-215`), **no quote-freshness
  gate** → a stale-but-streaming provider still wins; no STALE-vs-live.
- **F&O startup-restoration gap:** `_derivatives_universe` is in-memory
  (`service.py:92`) and set **only** by manual `POST /api/fno/apply`
  (`fno_coverage_routes.py:112`); the lifespan `reconcile` (`app/server.py:1220`)
  runs but the owner is empty at boot. Fix = **recompute from durable
  `fno_config` + catalogue + registry at startup** (no new persistence field).
- **Fyers HSM index-tick scaling concern** (`brokers/fyers/feed.py:937-941`
  index drop; `:942` scale; a captured NIFTY snapshot ×3.65 error). HSM is
  **undocumented** in public Fyers docs → **untrusted**; a **guarded Fyers
  REST index-refresh** is the proposed Fyers-only index path, pending a
  live market-hours capture.

### D.6 Repository / operational items (verified [C], R5–R10)
- **`brokers/fyers/rest.py` is UNTRACKED** yet imported at module scope
  (`app/server.py:695`) → a clean checkout **crashes** (`ModuleNotFoundError`)
  until committed + its whole dependency set is validated in a clean-checkout
  plan. **Do not treat as production-ready.**
- **Runtime catalogue:** `config.json` sets `data_dir:"data_test"` → the active
  `data_test/events.db` was **found empty** (0 `instruments`); a **populated
  catalogue (142,932 rows) was observed in `data/events.db`** (a separate
  environment). **These counts/states are historical — re-inspect; do not assume
  current.** Catalogue initialization is an **operational step not performed in
  any read-only review.**
- **F&O/view request-storm mitigation** (`frontend/.../useFno.ts:116`
  `useFnoActiveView`, stable `useMutation`) is **present in code**; regression
  tests were **identified but NOT confirmed passing** (no execution in these
  rounds).
- **`effective_budget` has no pinning test** (grep found none) — a small guard
  should be added (S2).

### D.7 Discrepancies to keep flagged (do not collapse)
- R2–R3 "90/4450" figures vs R7+ "50/4500/1800" — **90/4450 superseded** (arithmetic slip in prose, not code).
- R6 "Fyers BSE_FO thin / upstox-dominant" vs catalog counts — re-verify against the live catalog.
- R9 "Fyers no Greeks" vs R9-correction/R10 "Fyers supports Greeks via `greeks=1`" — **the correction stands**; G1 is the one-line gap.
- Earlier "Upstox 4500 data-socket" figure — **refuted**; correct mode caps are ltpc 5000 / option_greeks 3000 / full 2000 / full_d30 50 [D, R6/R7].

---

## E. Current status at last known checkpoint (Round 10 / Round 11)

- **Round 10** status = **"READY FOR MAINTAINER REVIEW"** of the *plan* — it was
  **NOT approved for implementation**, and **no source/test/config/db/build file
  was changed and no test re-run** in that review (a reported historical status;
  re-inspect the tree).
- **Round 10 plan stages S1–S15:**
  - S1 commit `brokers/fyers/rest.py` · S2 centralized plan-aware capacity
    (Standard default) + `effective_budget`/`min()` tests · S3 Fyers
    option-chain `greeks=1` (+`oichp`) + consume aggregate OI · S4 Upstox V3
    `previous_oi` mapping · S5 Fyers capability correction · S6 analytics scope
    labelling · S7 central quote coordinator + freshness/validity · S8 canonical
    join in `preferences()` + all consumers · S9 auto-restore F&O at startup (no
    new field) · S10 Fyers-only index via guarded REST poller · **S11 live
    validation tests** · S12 operational catalogue init · S13 `migrate_from_config`
    Fyers index categorization · S14 `fno/view` storm regression test ·
    **S15 canonical write-path re-key (PROTECTED-CORE, deferred)**.
- **Live items U1–U6 were BLOCKED in R10** (Fyers HSM index scaling; Fyers REST
  index; Upstox `full`-mode fields + `previous_oi` NOT-on-wire; Upstox V3
  `previous_oi` present; Upstox account tier/quotas; dual failover). **U7
  (operational catalogue init) was not resolved** in read-only review.
- **Maintainer decisions D-1…D-4 (recorded meanings, none assumed approved):**
  - **D-1:** adopt window-vs-aggregate scope labelling; wire Fyers aggregate
    OI for whole-expiry OI-PCR *only if* evidence proves whole-expiry; accept
    volume-PCR + Fyers max-pain as window-bounded.
  - **D-2:** approve the additive `greeks=1` + `oichp` (S3) and Upstox
    `previous_oi` (S4) fixes.
  - **D-3:** defer S15 (canonical re-key) as a **separate protected-core** task;
    S7 coordinator is the interim path.
  - **D-4:** record U1–U6 live evidence as the gate for S11 (nothing "validated"
    until they pass).
- **Repo baseline (historical):** branch `fno-provider-agnostic`, HEAD
  `3f4b8d9a7a73aa6e78222df86167c6f068d8ea97`, with extensive staged/modified/
  untracked work (~208 staged-new / 29 modified / 268 untracked at R7–R10).
  **Re-inspect; do not assume unchanged.**

- **Rechecked current Git state (this session, read-only via `.git`; `git` not on PATH):**
  branch `fno-provider-agnostic`, HEAD `3f4b8d9a7a73aa6e78222df86167c6f068d8ea97`
  — **matches the R10 baseline** (i.e. no new commits landed between R10 and now).
  The exact staged/modified/untracked *counts* could not be re-enumerated without
  `git status`; the audit-report doc files and this context file are untracked
  additions. **Do not treat the historical counts as current.**

### Round 11 (this session's most recent investigation)
- **Narrow scope, read-only.** Answered Issue 1: **Fyers `callOi`/`putOi`
  whole-expiry semantics are UNVERIFIED** — the official Option-Chain docs
  (2026-10-14) label them "total open interest for call/put options" for the
  single requested expiry but **do not specify whole-expiry vs window**, and no
  live response exists. → **Fyers whole-expiry OI-PCR stays BLOCKED**
  (treat as `unknown-aggregate`, not a verified total).
- **Gate separation established:** **G1 implementation readiness**
  (source assumptions resolved + offline regression tests *executed* —
  **currently unmet**, tests not run), **G2 live-validation readiness**
  (BLOCKED — no session/market-hours), **G3 release readiness** (NOT met;
  gated on G1+G2).
- **S3/S6/S11 amended:** S3 must consume `callOi`/`putOi` **only as
  `aggregate-unverified`**, not a verified whole-expiry PCR; S6 uses a
  three-tier scope label (`window` proven · `aggregate-unverified` ·
  `whole-expiry` Upstox-only); S11 carries the live `callOi`/`putOi`
  disambiguation test. No provider/instrument scope expanded.
- **No files, config, DB, tests, or Git state changed** in Round 11.

### Open planning issue — S7/S8 dependency inconsistency (recorded, NOT resolved)
- **R10** states **S8 depends on S7** (S7 = central quote coordinator over the
  provider-keyed store; S8 = canonical joins in `preferences()` + all frontend
  consumers).
- **R11** proposed beginning **S1–S6 and S8, omitting S7**.
- **This is an inconsistency in the handoff plan, not a signal to skip S7.**
  Until S7 is approved and complete, **S8 must not be presented as executable.**
- The next implementation proposal must **either** (a) **include and justify S7
  before S8**, **or** (b) **defer S8 until its prerequisite S7 is approved and
  complete.**
- **Do not resolve this by silently changing the architecture or by treating
  implementation as approved. Do not modify the original R10/R11 audit reports
  to hide the discrepancy** — it is recorded here as an open planning issue.

### Round 12 — S7/S8 dependency resolved with source evidence [C]
- **S7 is a genuine prerequisite for S8a (the quote-consuming portion).**
  `preferences()` (`app/subscriptions/service.py:221-235`) currently emits
  `"key": entry["upstox_key"]` (provider-specific) even though it carries
  `"canonical": True` and `"fyers_symbol"`. Frontend consumers
  (`IndicesWidget.tsx:31,58,61`) build `byKey = Map(quotes.map(q => [q.instrument_token, q]))`
  and join via `byKey.get(idx.key)` — correct **only** when the live quote's
  `instrument_token` matches the `upstox_key`. The quote store is
  natively keyed `(exchange, instrument_token)` (`market/service.py:207-210`);
  `canonical_id` is computed per-merge (`:251-257`, `:276-287`) but is **not** a
  store key (that re-key is S15, protected-core).
  **No coordinator/selection policy exists today** — grep for
  `get_canonical_quote|coordinator|select_provider|selection_policy|freshest`
  across the repo returns **zero matches**; `providers/` contains only
  `__init__.py`, `registry.py`, `capabilities.py`. S7 is genuinely new.
- **Without S7's freshness/validity gate + provider selection policy**, S8a's
  canonical join would surface **stale or wrong-provider** quotes as the
  canonical selection. S8a is therefore **blocked without S7**.
- **S8b (type/schema canonical identity in `WatchlistTable` + `types.ts`) — see
  Round 13 for the verified split into S8b-i / S8b-ii / S8b-iii.**
- **Recommended minimum coherent sequence (R13 updated):**
  `S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8a → S8b-ii/S8b-iii → S9 → S10 → S11
  (BLOCKED-gated)`. S8b-i (index display key) is parallel to S7. S13 depends on
  S8a.
- **Decisions still requiring explicit approval (R13 updated):**
  1. Approve S7 before S8a.
  2. Approve S8b-i as parallel with S7; S8b-ii/S8b-iii gated on S8a.
  3. Approve the full S1–S10 offline sequence.
  4. D-1…D-4 remain open.
- **No silent architecture change; no implementation approved.**

### Round 13 — S8b verified and split; Git state read-only confirmed [C+V]
- **The R12 "S8b is independent of S7" claim is refined: S8b is NOT a single
  parallel-safe stage. It decomposes into three sub-stages with different
  dependencies (evidence below):**
  1. **S8b-i — index display/React key.** `SubscriptionIndex.key`
     (`types/index.ts:468`) is currently `upstox_key`
     (`subscriptions/service.py:231`). It is used only for **display**
     (`SubscriptionsView.tsx:28`) and the **React key** (`SubscriptionsView.tsx:269`
     — actually `StockRow`, for indices it is `key={idx.key}` in
     `IndicesWidget.tsx:61`). The **index mutation** `setIndexEnabled(label,
     enabled)` (`market.ts:326-336`) uses `label`, not `key`. Therefore
     changing `SubscriptionIndex.key` to a canonical identity is **display-only
     and does not break the index mutation contract** → **S8b-i is independent
     of S7**. Safe to run parallel with S7.
  2. **S8b-ii — stock key canonicalization.** `SubscriptionStock.key`
     (`types/index.ts:475`) is the **actual DB mutation key**:
     `toggleStock(s.key, …)` → `setStockEnabled(key)` →
     `service.py::set_stock_enabled:271` →
     `self._store.get_md_subscription(category="stock", key=key)` (line 272).
     If the key is changed to canonical, the backend `md_subscriptions` lookup
     must also be updated to resolve the canonical key. **This is a backend
     change not provided by S7.** S8b-ii **requires S8a** (the `preferences()`
     key → canonical change) **plus a backend `md_subscriptions` key-resolution
     update**. S8b-ii is **gated on S8a**, not independent.
  3. **S8b-iii — WatchlistItem.** `WatchlistItem.instrument_token` /
     `WatchlistItem.exchange` (`watchlists/types.ts:4-10`) are joined in
     `WatchlistTable.tsx:19-34` via
     `byKey.get(`${it.exchange}:${it.instrument_token}`)` and
     `byToken.get(it.instrument_token)`. This is a **quote-join consumer**
     (same pattern as S8a). It does **not** read `preferences().key`. Changing
     `WatchlistItem` to a canonical identity requires the backend
     `/api/watchlists` contract change **plus S8a's quote-selection fix**.
     **S8b-iii is bundled with S8a**, not independent.
- **Corrected full sequence:**
  - Parallel with S7: **S8b-i** (index display key only).
  - Gated on S8a: **S8b-ii** (stock key + backend `md_subscriptions`
    resolution), **S8b-iii** (WatchlistItem quote-join).
  - Full path: `S1 → S2 → S3 → S4 → S5 → S6 → S7 → S8a → S8b-ii/S8b-iii → S9 →
    S10 → S11 (BLOCKED-gated)`. S8b-i alongside S7. S13 gated on S8a.
- **No silent architecture change; no implementation approved.**

### Round 14 — Key contract locked: three distinct identifier fields [C]
- **Traced the complete key contract across indices and stocks** (exact source
  references):
  - **Backend `preferences()`** (`subscriptions/service.py:221-255`):
    - **Indices:** `key=entry["upstox_key"]` (provider-native, line 231),
      `fyers_symbol` (line 232), `label` (canonical code label, line 230).
      **Index DB key = `label`** (`ensure_defaults:166-171`,
      `set_index:257-261`). The `preferences()` index `key` is a **view-only**
      field; it is NOT the DB key and NOT the mutation parameter
      (`_patch_index` uses `label`, `subscription_routes.py:50`).
    - **Stocks:** `key=r["key"]` (provider-native, line 237). **Stock DB key
      = `key`** (`add_stock:263-269`, `set_stock_enabled:271-276`,
      `remove_stock:278-279` — all look up
      `get_md_subscription(category="stock", key=key)`). The `preferences()`
      stock `key` **IS** the mutation key: `_patch_stock`/`_delete_stock`
      (`subscription_routes.py:84,96`) send it verbatim to
      `set_stock_enabled`/`remove_stock`.
  - **Canonical instrument identity** (`app/market_identity.py:280-321`,
    `MarketInstrumentIdentityResolver`): maps
    `instrument_token`/`tradingsymbol` → **canonical id** (e.g.
    `EXCH:TYPE:...` from `market/index_registry.py`). `Quote.canonical_id`
    (`market/models.py:258`) is **computed per-merge**
    (`market/service.py:251-257,276-287`) but is **not** a store key
    (re-key = S15, protected-core).
  - **Quote store** (`market/service.py:207-210,357-363`): keyed
    `(exchange, instrument_token)` — provider-native.
    `MarketService.quotes()` returns **all** stored quotes (both providers'
    slots per instrument) with no selection.
  - **SSE/REST quote projection** (`api/routes.py:74-131`):
    - `/api/market/stream` (SSE) emits **every** quote from
      `market_service.quotes()` (line 89) — **no provider selection, no
      canonical filter**. A consumer sees both providers' slots for the same
      instrument.
    - `/api/market/quotes` (line 107-112) — same: all quotes, no selection.
    - `/api/market/quote/{exchange}/{token}` (line 121-131) — single
      provider-native key lookup; **`identity_resolver.resolve` is applied**
      (line 126) to map aliases to the storage key, but the result is still a
      **single provider-native slot**, not a cross-provider selection.
  - **Frontend quote joins:**
    - `IndicesWidget.tsx:31,58,61` — `byKey = Map(quotes.map(q => [q.instrument_token, q]))`,
      joined on `idx.key` (= `upstox_key`). **Provider-specific join.**
    - `WatchlistTable.tsx:19-34` — `byKey = Map(quotes.map(q => [quoteKey(q), q]))`
      where `quoteKey(q) = `${q.exchange}:${q.instrument_token}``
      (`dashboard/types.ts:66-67`), joined on
      `${it.exchange}:${it.instrument_token}`. **Provider-native join.**
      Watchlist items carry `(exchange, instrument_token, tradingsymbol)`
      (`watchlists/types.ts:4-10`); they are **not** `preferences()` keys.
  - **S7's proposed `get_canonical_quote`** (does not exist yet — grep
    returns zero matches): would add a **read-side selection layer** that
    returns the **best** (most-recent, provider-preferred, freshness-gated)
    quote for a canonical instrument. This is a **new API**, not a change to
    the existing `/api/market/quotes` or SSE. Existing SSE/REST paths
    **will not automatically use S7's coordinator** — they continue to expose
    the raw native-keyed store. S8a/S8b-iii consumers must be explicitly
    pointed at the new coordinator endpoint/field.
- **The three distinct fields are REQUIRED. One field cannot serve all three
  purposes:**
  1. **Canonical instrument identity** (`canonical_id`, e.g.
     `NSE:INDEX:NIFTY`) — for cross-provider convergence, display, and
     analytics. **Never a mutation key.**
  2. **Quote selection/join identity** (S7 coordinator's output: a selected
     `Quote` with `canonical_id` populated) — for frontend live-quote joins.
     **Requires S7.**
  3. **Backend mutation key** (stock: `md_subscriptions.key`
     = provider-native; index: `md_subscriptions.key` = `label`) — for
     `add_stock`/`set_stock_enabled`/`remove_stock`/`set_index`.
     **Must NOT be changed to canonical without a backend key-resolution
     update** (`service.py:272` looks up by the native key).
- **S8a/S8b-ii dependency is SAFE when sequenced correctly:**
  - S8a changes `preferences()` to emit `canonical_id` as a **new additive
    field** (alongside the existing `key` field, which remains unchanged).
  - S8b-ii then **migrates the frontend display/join** to use `canonical_id`
    while **continuing to send native `key` to mutation endpoints**. No
    backend mutation contract changes are required.
  - **No circular dependency**: S8a adds a field; S8b-ii consumes it; the
    mutation path is untouched.
  - **The plan must never send a canonical identifier to an endpoint
    expecting a provider-native/database key unless the backend explicitly
    supports that resolution.** The current backend does NOT support
    canonical-key resolution for stock mutations. **This invariant is
    preserved by adding `canonical_id` as an additive field and leaving
    `key` (native) as the mutation key.**
- **Corrected final stage graph (R14):**
  - **S7** (central quote coordinator + `get_canonical_quote` + freshness
    gate + rate buckets). Prereq: S2, S4.
  - **S8b-i** (index display: add `canonical_id` field to
    `SubscriptionIndex`; change `IndicesWidget` join to use S7's
    coordinator for the selected quote). **Parallel with S7** — the index
    `key` field is view-only; the mutation uses `label`; no backend change.
  - **S8a** (add `canonical_id` field to `preferences()` indices + stocks;
    switch quote-consuming frontend consumers — heatmap, SSE, option-chain,
    OI-analytics, market-intel — to join via S7's coordinator).
    **Prereq: S7. ADDITIVE only — `key` field unchanged.**
  - **S8b-ii** (frontend stock display/mutation: use `canonical_id` for
    display/join; **continue sending native `key` to mutation endpoints**).
    **Prereq: S8a** (needs the `canonical_id` field present).
    **No backend mutation contract change.**
  - **S8b-iii** (WatchlistTable: add `canonical_id` to
    `WatchlistItem`; join live quote via S7's coordinator using
    `canonical_id`; **depth requests continue using
    `(exchange, instrument_token)`** native key — depth is
    provider-specific and does not need canonical selection).
    **Prereq: S8a + S7.**
  - **S9** (auto-restore F&O at startup). **Prereq: S7.**
  - **S10** (Fyers-only index via guarded REST poller). **Prereq: S7.**
  - **S11** (live validation). **Prereq: S10; BLOCKED-gated on U1–U6.**
  - **S13** (`migrate_from_config` Fyers index categorization).
    **Prereq: S8a.**
- **S8a is defined as an ADDITIVE change** (new `canonical_id` field),
  not a replacement of `key`. This is the key-contract decision that makes
  S8b-ii safe without a backend mutation contract change.
- **Outstanding maintainer decisions (R14 refined):**
  1. Approve S7 before S8a/S8b-iii/S9/S10 (genuine coordinator prerequisite).
  2. Approve S8b-i as parallel with S7.
  3. Approve S8a as **additive** (`canonical_id` field added; `key` field
     unchanged).
  4. Approve S8b-ii: frontend uses `canonical_id` for display/join;
     mutation calls continue to use native `key`. No backend change.
  5. Approve S8b-iii: WatchlistItem gets `canonical_id` for quote join;
     depth requests remain native-keyed.
  6. D-1…D-4 remain open.
- **No silent architecture change; no implementation approved.**

### Round 15 — Quote and identity API contract locked [C]
- **Three contracts, precisely defined (each is a distinct field; no field
  serves two of these purposes):**
  1. **`canonical_id`** — stable cross-provider instrument identity
     (`MarketInstrumentIdentityResolver` output, `market_identity.py:280-321`;
     `Quote.canonical_id`, `models.py:258`). Used for **display, analytics,
     cross-provider convergence**. **Never a mutation key, never a store key
     (S15 protected-core).** Absent from **all current API responses** —
     S8a adds it additively to `preferences()` (indices + stocks), and S8b-iii
     adds it to `/api/watchlists` items (computed server-side by the resolver
     at list time, not stored in `watchlist_items` — additive projection).
  2. **Existing `key`** — preserved backend mutation contract:
     - Stock: `md_subscriptions.key` (provider-native; `service.py:237,272`);
       `_patch_stock`/`_delete_stock`/`_add_stock`
       (`subscription_routes.py:84,96,66`) send it verbatim.
     - Index: `md_subscriptions.key = label` (`service.py:257-261`);
       `_patch_index` uses `label` (`subscription_routes.py:50`).
     - Watchlist: `watchlist_items.instrument_token`
       (`products.py:537-545`); add/remove reference counting
       (`product_routes.py:669-678,757-777`).
     **None of these may change** in S8a/S8b-ii/S8b-iii.
  3. **Canonical-selected quote** — the freshness-checked, provider-selected
     quote for one canonical instrument, produced by **S7's selection layer**.
     This is a **quote value + selection metadata**, not a new identity field:
     the selected `Quote` already carries `canonical_id` (computed per-merge,
     `service.py:251-287`) plus S7's freshness/selection metadata.
- **R13/R14 inconsistency resolved:** R13 described S8b-i as "changing the
  index key"; R14 defined S8a as additive `canonical_id` with `key` unchanged.
  **Resolution:** S8b-i does **not** change the index `key` field. The four
  distinct changes, each assigned to one stage:
  - **Backend exposure of canonical identity:** S8a adds `canonical_id` to
    `preferences()` indices + stocks (additive; `key`/`label` unchanged).
    S8b-iii adds `canonical_id` to `/api/watchlists` items (computed by the
    resolver at list time; **no `watchlist_items` schema change**).
  - **Index display + React identity:** S8b-i changes
    `SubscriptionsView.tsx:28` display + `IndicesWidget.tsx:61` React key to
    use `canonical_id` (new field from S8a). `key` remains in the payload
    (unchanged) but is no longer the display/React identity.
  - **Quote-join key:** S8a switches quote-consuming consumers (heatmap,
    SSE-driven views, option-chain, OI-analytics, market-intel,
    `IndicesWidget`) to join via S7's canonical-selected quote — join on
    `canonical_id` + selection metadata, **not** on `key` or native
    `(exchange, instrument_token)`.
  - **Unchanged mutation key:** S8a/S8b-ii/S8b-iii all continue to send the
    native `key` (stocks) / `label` (indices) /
    `(exchange, instrument_token)` (watchlists) to mutation and lookup
    endpoints.
- **Quote delivery path — what S7 must deliver (and what is NOT automatic):**
  - **Current state [C]:** `MarketService.quotes()` (`service.py:361-363`)
    returns **all** stored slots (both providers per instrument).
    `/api/market/quotes` (`routes.py:107-112`) and `/api/market/stream`
    (`routes.py:74-103`) emit the unselected set. Frontend
    `useMarketQuotes.ts:18-66` keys its map by
    `quoteKey = `${q.exchange}:${q.instrument_token}``
    (`dashboard/types.ts:66-67`) — so **both providers' slots coexist in the
    frontend map** and a join can hit the wrong provider's slot.
  - **S7 must deliver:** a selection layer that, **per canonical
    instrument**, picks exactly one quote:
    - freshness-gated (stale → not selected; reported as `unavailable`,
      never presented as a valid selected quote);
    - provider-preference tie-break (a tie-break only, never a routing gate —
      the agreed rule);
    - consistent across REST snapshot and SSE updates (the **same**
      selection policy drives both; the SSE snapshot-on-connect
      (`routes.py:85-93`) and subsequent quote events must both reflect the
      selected quote, or the two views disagree).
  - **S7 endpoint/serialization changes (NOT implied by
    `get_canonical_quote` alone — required and assigned to S7):**
    - New/confirmed response fields on the quote projection:
      `canonical_id` (already a `Quote` dataclass field — verify
      `quote_to_dict` includes it; add if omitted) plus `selected_from` /
      `stale` metadata.
    - **SSE snapshot + updates** (`routes.py:85-103`) must emit
      **selected** quotes (one per canonical instrument), not the raw
      unselected set — otherwise snapshot and stream disagree.
    - **`/api/market/quotes`** (`routes.py:107-112`) same treatment; the
      default contract must match the SSE contract.
    - **Fyers-only / Upstox-only / dual:** single-provider configs select
      that provider's slot when fresh; dual applies the tie-break only among
      fresh quotes; when neither is fresh, the instrument is reported
      **unavailable** (honest availability, never a stale-as-live claim).
  - **Frontend consumers** (S8a scope) then join on `canonical_id` against
    the **selected** quote set — no per-component provider-specific
    workaround (the `upstox_key` join in `IndicesWidget.tsx:31` and the
    native-key join in `WatchlistTable.tsx:31-34` are the defects
    S8a/S8b-iii remove).
- **Watchlist quote + depth resolution [C]:**
  - **How a watchlist item gets canonical identity:** `/api/watchlists`
    (`product_routes.py:681-689`) returns `list_watchlist_items` rows =
    `(id, exchange, instrument_token, tradingsymbol, position)`
    (`products.py:537-545`) — **no `canonical_id` field today**. S8b-iii
    computes it server-side at list time via
    `MarketInstrumentIdentityResolver.resolve` (alias → canonical id,
    `market_identity.py:295-307`); **no `watchlist_items` schema change**
    (additive projection only).
  - **How the displayed quote is selected and matched:** S8b-iii joins the
    item's `canonical_id` against S7's selected-quote set. The join key is
    `canonical_id`, **not** the item's native
    `(exchange, instrument_token)`.
  - **Depth intentionally remains provider-specific [C]:**
    `/api/market/depth/{exchange}/{instrument_token}`
    (`routes.py:133-143`) is a **single-slot** lookup — depth
    (order-book levels) is provider-reported per native instrument and
    `market/models.py::Depth` carries one provider's levels; there is no
    canonical merge semantics. **Depth requests continue to use the native
    `(exchange, instrument_token)`** — intentional, not a gap.
  - **When the selected quote comes from a different provider than the
    watchlist's stored exchange/token [C + UNRESOLVED]:** the stored item
    pins a provider-native identity (added via `AddInstrument.tsx:80-85`
    from a catalog search row — `selected.exchange` /
    `selected.instrument_token` are provider-specific). If S7's selected
    quote for that `canonical_id` comes from the **other** provider, the
    quote row (LTP/change/volume/bid/ask) displays correctly (the quote is
    provider-neutral), **but** the depth drawer / chart-nav / depth request
    (`routes.py:133`, `dashboard/api.ts:17-29`) still targets the
    **stored native key** — which may have **no depth data** if that
    provider's slot is not subscribed or is stale. **This cross-provider
    depth mismatch is UNRESOLVED** (options: (a) honest "stored-provider
    depth" labeling, or (b) depth request follows the selected quote's
    native key — which would change `getQuoteDepth` semantics and is a
    separate decision). **Recorded as open sub-decision D-5 — not assumed
    resolved.**
- **Corrected minimum stage graph (R15 final):**
  - **S7** — coordinator + selection policy + freshness/validity gate +
    endpoint-specific rate buckets; **plus** the S7 endpoint/serialization
    changes above (selected-quote snapshot + SSE consistency,
    `canonical_id` + selection metadata in the quote projection). Output:
    one fresh, provider-selected quote per canonical instrument, consistent
    across REST snapshot and SSE. Prereqs: S2, S4. Acceptance:
    single-provider and dual configs each return exactly one selected quote
    per canonical instrument; stale-only instruments report unavailable;
    snapshot and SSE agree.
  - **S8a** — additive `canonical_id` on `preferences()` indices + stocks;
    switch quote-consuming consumers to the S7 selected-quote join.
    Prereq: S7. **`key` unchanged.** Acceptance: mutation endpoints receive
    unchanged native keys; quote joins use selected quotes; no
    provider-specific workaround in any consumer.
  - **S8b-i** — index display/React identity: switch
    `SubscriptionsView.tsx:28` + `IndicesWidget.tsx:61` to `canonical_id`.
    Prereq: S8a (field must exist). `key` unchanged; index mutation still
    via `label`. Acceptance: index rows keyed/displayed by `canonical_id`;
    mutation contract intact. **[R16] Prerequisite CONFIRMED: S8b-i FOLLOWS
    S8a — it cannot run in parallel with S7 because `canonical_id` does not
    exist in `preferences()` until S8a adds it. The R15 graph line "S8b-i
    alongside S7" is CORRECTED; the stage definition was right.**
  - **S8b-ii** — stock display/join uses `canonical_id`; stock mutations
    still send native `key`. Prereq: S8a. No backend mutation change.
  - **S8b-iii** — `canonical_id` on `/api/watchlists` items (computed, no
    schema change); WatchlistTable quote join via S7 selected quote; depth
    remains native-keyed. Prereq: S8a + S7. **D-5 (depth-follows-selection)
    open.**
  - **S9** (auto-restore F&O at startup) — Prereq: S7.
  - **S10** (Fyers-only index via guarded REST poller) — Prereq: S7.
  - **S11** (live validation) — Prereq: S10; BLOCKED-gated on U1–U6.
  - **S13** (`migrate_from_config` Fyers index categorization) — Prereq:
    S8a.
- **Outstanding maintainer decisions (R15 final):**
  1. Approve the three-contract split (canonical_id / key / selected quote)
     and the stage assignments above.
  2. Approve S7's **endpoint/serialization** scope (selected-quote
     snapshot + SSE consistency; `canonical_id` + selection metadata in the
     quote projection) — previously implied by "S7 coordinator" but now
     explicitly required for the consistency goal.
  3. **D-5 (new):** depth drawer behavior when the selected quote's provider
     differs from the watchlist item's stored provider: (a) honest
     "stored-provider depth" labeling, or (b) depth request follows the
     selected quote. **Open — not assumed.**
  4. D-1…D-4 remain open.
- **No silent architecture change; no implementation approved.**

### Round 16 — S8b-i dependency corrected; D-5 resolved [C]
- **The R15 S8b-i contradiction (stage graph "parallel with S7, before S8a" vs
  stage definition "Prereq: S8a") is resolved by source inspection: the stage
  definition was correct, the graph was wrong.**
  - S8b-i switches index display/React identity to `canonical_id`
    (`SubscriptionsView.tsx:28`, `IndicesWidget.tsx:61`). The only source of
    `canonical_id` for a `preferences()` index entry is **S8a's additive
    backend field** — `preferences()` today has **no** `canonical_id`
    (`subscriptions/service.py:221-235` emits `key`/`fyers_symbol`/`label`
    only).
  - **No verified alternative source exists** for a *frontend subscriptions
    consumer*: the resolver `MarketInstrumentIdentityResolver.resolve`
    (`market_identity.py:295-307`) is backend-only; the frontend
    `SubscriptionIndex` type (`types/index.ts:466-472`) carries no
    `canonical_id`; `market_indices.py::resolve_spot:174-233` returns
    `provider`/`instrument_token`/`basis`, **not** a `canonical_id`.
  - **Therefore: S8b-i CANNOT run in parallel with S7. S8b-i follows S8a.**
    Parallel execution was a graph error; it is not preserved "merely to
    shorten the plan."
  - **The R15 graph block (line ~629-633) and §I "Next agreed action" are
    corrected below; the R15 stage definition (Prereq: S8a) stands.**
- **D-5 resolved: Option B (depth request follows the selected quote's native
  key).** Source evidence:
  - The selected quote (S7 contract) **always carries its native
    `(exchange, instrument_token)`** — the `Quote` dataclass
    (`market/models.py:227-228`) — regardless of which provider's slot was
    selected. This is **reliably available**: it is the store key itself
    (`service.py:207-210,359`), not derived or optional.
  - `/api/market/depth/{exchange}/{instrument_token}`
    (`routes.py:133-143`) is a **key-addressed lookup** — it can serve depth
    for **any** stored native slot; it does not validate that the requested
    key matches the item's stored provider. No identity confusion: the depth
    payload is the `Depth` for the requested native slot only.
  - **Chart navigation (`chartHref` → `ChartsView.tsx:47-68`) already
    re-resolves via the catalog** (`resolveInstrument`, `market.ts:147-155`)
    when given only symbol+type — so a chart opened from a watchlist row
    whose selected quote is from a *different* provider still resolves to the
    **correct catalog instrument** (canonical), not the stored provider's.
    This is the **existing safe behavior** that makes Option B coherent.
  - **Option A (stored-provider depth + honest "unavailable") is the
    fallback**: when the selected quote's native slot has no depth (not
    subscribed / stale), the depth drawer shows an explicit unavailable state
    sourced from that provider — **not** silently the other provider's depth.
  - **Recommendation: Option B with Option-A fallback.** Concretely:
    - `getQuoteDepth(exchange, instrumentToken)` (unchanged signature,
      `dashboard/api.ts:17-29`) is called with the **selected quote's**
      native `exchange`/`instrument_token` (not the item's stored value)
      — this is a one-line change at the call site in S8b-iii.
    - If that lookup returns no depth (404 / empty), the drawer renders
      "depth unavailable for the selected source" — honest, not a
      cross-provider silent substitution.
    - **No canonical depth-merging system is introduced** (out of scope;
      would require S15-level work).
    - **D-5 is CLOSED as a decision**: B (with A-fallback) is the chosen
      contract, **pending maintainer approval** (it is a behavior change to
      which provider's depth is requested).
- **Corrected final stage graph (R16, supersedes the R15 graph block and §I
  "Next agreed action"):**
  - **S7** (coordinator + selection + freshness/validity gate + rate buckets;
    **plus** endpoint/serialization: selected-quote snapshot + SSE
    consistency, `canonical_id` + selection metadata in the quote
    projection). Prereqs: S2, S4. Acceptance: one fresh selected quote per
    canonical instrument; stale-only → unavailable; snapshot ≡ SSE.
  - **S8a** (additive `canonical_id` on `preferences()` indices + stocks;
    quote-consuming consumers switch to the S7 selected-quote join; `key`
    unchanged). Prereq: S7. Acceptance: mutation endpoints receive unchanged
    native keys; joins use selected quotes; no provider-specific workaround.
  - **S8b-i** (index display/React identity → `canonical_id`
    (`SubscriptionsView.tsx:28`, `IndicesWidget.tsx:61`); `key` unchanged;
    index mutation still via `label`). **Prereq: S8a** (the `canonical_id`
    field must exist). Acceptance: index rows keyed/displayed by
    `canonical_id`; mutation contract intact. **(Corrected: NOT parallel
    with S7.)**
  - **S8b-ii** (stock display/join → `canonical_id`; stock mutations still
    native `key`). Prereq: S8a. No backend mutation change.
  - **S8b-iii** (`canonical_id` on `/api/watchlists` items, computed at list
    time, no schema change; WatchlistTable quote join via S7 selected quote;
    **depth request follows the selected quote's native key (Option B), with
    Option-A fallback**). Prereq: S8a + S7. **D-5 closed (pending approval).**
  - **S9** (auto-restore F&O at startup). Prereq: S7.
  - **S10** (Fyers-only index via guarded REST poller). Prereq: S7.
  - **S11** (live validation). Prereq: S10; BLOCKED-gated on U1–U6.
  - **S13** (`migrate_from_config` Fyers index categorization). Prereq: S8a.
- **Outstanding maintainer decisions (R16 final):**
  1. Approve the corrected graph: **S8b-i follows S8a** (not parallel with S7).
  2. Approve D-5 resolution: **depth request follows the selected quote's
     native key (Option B) with an honest unavailable fallback (Option A)**;
     no canonical depth-merging system.
  3. D-1…D-4 remain open.
- **No silent architecture change; no implementation approved.**

### Round 17 — Depth routing verified; plan frozen [C]
- **Depth store key verified from source:** `_depths` is keyed
  `(depth.exchange, depth.instrument_token)` — `market/service.py:373`
  (`apply_depth`), `:403` (`get_depth`), `:405` (`depths`). This is **the
  provider-native key**, exactly as `_quotes` is
  (`service.py:207-210,357-359`). **No canonical key is used for either
  store** (S15 re-key remains protected-core and deferred).
- **Provider-native key uniqueness across providers verified:**
  - Fyers: `instrument_token = symbol` where `symbol = EXCH:TRADINGSYMBOL`
    (`fyers.py:107-117`, `_identity`). Exchange is derived from the
    symbol's colon prefix. Example: `NSE:NIFTY50-INDEX`.
  - Upstox: `instrument_token = instrument_key` where
    `instrument_key = EXCH_SEGMENT|SYMBOL` (`upstox.py:88-94`,
    `_base_fields`). Exchange is derived from the segment before `|`.
    Example: `NSE_INDEX|Nifty 50`.
  - **The two formats are structurally disjoint**: Fyers uses
    `:` (colon), Upstox uses `|` (pipe). A given
    `(exchange, instrument_token)` pair can only be produced by one
    provider's normalizer. **`(exchange, instrument_token)` IS globally
    unique across providers — established from source, not assumed.**
- **Depth endpoint verified:** `/api/market/depth/{exchange}/{token}`
  (`routes.py:133-143`) calls `market_service.get_depth(exchange, token)`
  → `self._depths.get((exchange, instrument_token))`
  (`service.py:401-403`). The lookup is **exact-key** — it returns the
  `Depth` stored under that native key, or `None`. It does **not**
  validate the key against the watchlist item's stored provider. **No
  identity confusion is possible**: each native key maps to exactly one
  provider's depth slot.
- **What happens when the selected quote's provider differs from the
  watchlist item's stored identity [C]:**
  - The quote row (LTP/change/volume/bid/ask) displays correctly — the
    `Quote` is provider-neutral in its value fields.
  - **Depth request (Option B):** `getQuoteDepth` is called with the
    **selected quote's** native `(exchange, instrument_token)`, not the
    item's stored value. If that provider's depth slot has no data
    (not subscribed, stale, or not yet populated), the endpoint returns
    `404` (`routes.py:141`) → the drawer renders "depth unavailable for
    the selected source" (Option-A fallback). **No silent cross-provider
    substitution.**
  - **Stale depth:** `apply_depth` enforces the same staleness-gated
    merge as quotes (`service.py:377-385`): an older snapshot is
    rejected (`stale_depth_updates` counter incremented); the existing
    (newer) depth is retained. A stale depth is **never overwritten** by
    older data.
  - **Native identity differs from stored:** the depth request targets
    the selected provider's native key — which is **correct by design**
    (Option B). The item's stored identity is irrelevant to the depth
    lookup; only the selected quote's identity matters.
- **No API-contract adjustment required for Option B:** the existing
  `/api/market/depth/{exchange}/{token}` endpoint already serves any
  stored native key. The only change is at the **frontend call site**
  (S8b-iii): pass the selected quote's native
  `(exchange, instrument_token)` instead of the item's stored value.
  This is a **one-line frontend change**, not a backend endpoint change.
- **Final key/depth contract (frozen, R17):**
  - **`canonical_id`** — cross-provider identity; never a store key, never
    a mutation key. Added additively by S8a/S8b-iii.
  - **`key`/`label`** — backend mutation contract; unchanged in all
    stages.
  - **Selected quote** — normalized quote + selected provider + native
    `(exchange, instrument_token)`; produced by S7's selection layer.
  - **Depth request** — targets the selected quote's native key
    (Option B); honest 404/unavailable when that slot has no depth
    (Option-A fallback). Depth remains provider-specific; **no canonical
    depth-merging system** (S15-level work, deferred).
- **Final implementation proposal (R17, supersedes all prior graph blocks):**
  - **S1** — commit `brokers/fyers/rest.py` + validate its dependency set
    in a clean checkout. Prereqs: none. Acceptance: no
    `ModuleNotFoundError` on clean checkout. Rollback: forward-only
    (removal is a later forward change); never a `git reset`.
  - **S2** — centralized plan-aware capacity (Standard default,
    `fyers.plan_cap:100`); `registry.budget()` →
    `min(effective_budget(plan_cap), 500)`; replace
    `fyers/capabilities.py:27`; add `effective_budget` + `min()` tests.
    Prereqs: S1. Acceptance: 50/4500/1800 + app-500 budgets correct;
    tests pass. Rollback: revert the config file, the one
    `capabilities.py` line, and the tests.
  - **S3** — Fyers option-chain `greeks=1` + `oichp` mapping + consume
    aggregate `callOi`/`putOi` as `aggregate-unverified` (not a verified
    whole-expiry PCR). Prereqs: S1. Acceptance: `greeks=1` param sent;
    `oichp` mapped; `callOi`/`putOi` exposed with a `scope` flag
    (`aggregate-unverified`). Rollback: revert the param line + mapping
    lines.
  - **S4** — Upstox V3 full-quote `previous_oi` mapping (additive) +
    precedence/regression tests. Prereqs: S1. Acceptance:
    `quote_from_rest` populates `Quote.previous_oi`; provider-prev-OI
    precedence over persisted; older REST snapshot rejected. Rollback:
    revert the added mapping line.
  - **S5** — Fyers capability correction: declare `CAP_OPTION_CHAIN`
    (with greeks), `CAP_OI`/`CAP_PREVIOUS_OI`/`CAP_OI_CHANGE`;
    document max-pain window-bounded. Prereqs: S3. Acceptance:
    capability flags correct; no hidden mandatory provider. Rollback:
    revert.
  - **S6** — analytics scope + labelling: attach `scope`
    (`window`|`aggregate-unverified`|`whole-expiry` Upstox-only) to
    `compute_pcr`/`compute_pcr_volume`/`compute_max_pain` results; UI
    labels "window-scoped" vs "whole-expiry"; Fyers max-pain marked
    bounded. Prereqs: S3, S5. Acceptance: scope markers present; UI
    labels correct; no whole-expiry claim without evidence. Rollback:
    revert the scope field + labels.
  - **S7** — central quote coordinator + selection policy +
    freshness/validity gate + endpoint-specific rate buckets; **plus**
    endpoint/serialization changes: selected-quote snapshot + SSE
    consistency, `canonical_id` + selection metadata in the quote
    projection. Prereqs: S2, S4. Acceptance: one fresh,
    provider-selected quote per canonical instrument; stale-only →
    unavailable; snapshot ≡ SSE. Rollback: revert the 3 additions
    (selection policy, coordinator, endpoint changes).
  - **S8a** — additive `canonical_id` on `preferences()` indices + stocks;
    switch quote-consuming frontend consumers to the S7 selected-quote
    join. `key` unchanged. Prereqs: S7. Acceptance: mutation endpoints
    receive unchanged native keys; quote joins use selected quotes; no
    provider-specific workaround in any consumer. Rollback: revert the
    additive field + consumer joins.
  - **S8b-i** — index display/React identity → `canonical_id`
    (`SubscriptionsView.tsx:28`, `IndicesWidget.tsx:61`); `key`
    unchanged; index mutation still via `label`. Prereqs: S8a.
    Acceptance: index rows keyed/displayed by `canonical_id`; mutation
    contract intact. Rollback: revert the display/React-key changes.
  - **S8b-ii** — stock display/join → `canonical_id`; stock mutations
    still send native `key`. Prereqs: S8a. No backend mutation change.
    Acceptance: display uses `canonical_id`; mutation calls unchanged.
    Rollback: revert the display changes.
  - **S8b-iii** — `canonical_id` on `/api/watchlists` items (computed
    at list time, no schema change); WatchlistTable quote join via S7
    selected quote; **depth request follows the selected quote's native
    key (Option B), with Option-A fallback**. Prereqs: S8a + S7.
    Acceptance: watchlist items carry `canonical_id`; quote join uses
    selected quotes; depth request uses selected quote's native key;
    404 → honest "unavailable" state. Rollback: revert the
    `canonical_id` projection + call-site change.
  - **S9** — auto-restore F&O at startup (no new field;
    `plan()`→`set_derivatives_universe()`→existing `reconcile()`).
    Prereqs: S7. Acceptance: `_derivatives_universe` populated at boot
    from durable config + catalogue + registry; no manual "apply"
    required. Rollback: revert the added lifespan lines.
  - **S10** — Fyers-only index via guarded REST poller (≤5 rps, 60 s,
    shared bucket, 5-min freshness, dedup). Prereqs: S7. Acceptance:
    Fyers index data refreshed within 5 min when Upstox unavailable;
    HSM guard untouched. Rollback: revert poller + index-source lines.
  - **S11** — live validation tests (Fyers `greeks=1` chain, Fyers REST
    index, Fyers HSM scaling, Upstox `full` fields, Upstox V3
    `previous_oi`, dual failover; execute §5.3 regression tests).
    Prereqs: S10. **BLOCKED-gated on U1–U6** (no session/market-hours
    in this environment). Acceptance: all live tests pass with
    independent reference values. **Do not mark as passed until
    executed with authenticated sessions.** Rollback: n/a (evidence
    only).
  - **S13** — `migrate_from_config` Fyers index categorization fix.
    Prereqs: S8a. Acceptance: one-time migration correct; idempotent;
    marker re-run safe. Rollback: revert (marker re-run safe).
  - **S15** — canonical write-path re-key (final): `market/service.py`
    `_quotes` → `(exchange, canonical_id)` + migration.
    **PROTECTED-CORE; deferred; separate approval required.** Prereqs:
    S7 proven. Data migration. **Not in the ordinary gate.**
  - **Gate separation (preserved):**
    - **G1 (implementation readiness):** S1–S10 offline tests executed
      green. **Currently unmet** (tests not run).
    - **G2 (live-validation readiness):** U1–U6 evidence. **BLOCKED.**
    - **G3 (release readiness):** gated on G1 + G2. **NOT met.**
    - **U7 (operational catalogue init):** not run read-only;
      prerequisite for S12 (operational sync).
- **Outstanding maintainer decisions (R17 final):**
  1. Approve the corrected S8b-i dependency (follows S8a, not parallel
     with S7).
  2. Approve D-5 resolution: depth request follows selected quote's
     native key (Option B) with honest unavailable fallback (Option A);
     no canonical depth-merging system.
  3. Approve the full S1–S11 + S13 offline sequence (S15 deferred,
     protected-core).
  4. D-1…D-4 remain open (window-vs-aggregate labelling; `greeks=1`/
     `oichp`/`previous_oi` fixes; S15 protected-core deferral; U1–U6
     as S11's gate).
  5. **Approve coding** — no implementation has been approved. This
     investigation does not grant coding authority.
- **No silent architecture change; no implementation approved.**

### Round 18 — S1 repository-safety boundary corrected; plan consolidated [C]
- **S1 "commit `brokers/fyers/rest.py`" is UNSAFE as stated. [C] Source
  inspection of the dependency set shows the minimum coherent unit that makes
  a clean checkout work is LARGER than `rest.py` alone:**
  - `rest.py` (untracked) imports:
    - `market.models`: `Candle, Depth, OptionChainSnapshot, Quote` —
      **tracked**, but the **working-tree** version adds 9 lines
      (`canonical_id` + 6 CAS fields on `Quote`) that are NOT in HEAD
      (`git diff HEAD -- market/models.py` shows `+9`). The committed HEAD
      version of `market/models.py` lacks `Quote.canonical_id` / CAS fields.
    - `market.normalize.fyers`:
      `candles_from_history, depth_from_rest, option_chain_from_rest,
      quote_from_quotes_rest` — **tracked**, but the working-tree version
      contains a 1-line fix (`data.get("optionsChain")` vs HEAD's
      `data.get("options")`, `git diff HEAD -- market/normalize/fyers.py`
      shows `+1/-1`) that is NOT in HEAD. The HEAD version would parse
      option-chain payloads incorrectly.
    - `app.market_data`: `ProviderMarketDataError` (deferred import inside
      `FyersMarketDataAdapter._token`) — **tracked, no diff vs HEAD** (clean
      at HEAD).
  - `app/server.py:695-710` (working tree, `MM` — 52 lines added vs HEAD)
    wires `FyersRest` + `FyersMarketDataAdapter` into `_ProviderMarketData`.
    The 52 added lines reference `_fyers_runtime_auth` (defined elsewhere in
    the modified file) and are part of the same in-flight Fyers phase.
  - **Therefore the minimum coherent set for a clean-checkout validation is:**
    `brokers/fyers/rest.py` (new) + the `market/models.py` working-tree
    hunk (9 lines) + the `market/normalize/fyers.py` working-tree hunk
    (1 line) + the `app/server.py:695-710` wiring (within the 52-line diff).
    **NOT** `brokers/fyers/auth.py` (39-line in-flight auth-expiry work —
    unrelated to `rest.py`'s import graph), **NOT**
    `brokers/fyers/capabilities.py` (41-line in-flight capacity work — S2
    territory), **NOT** the audit reports, **NOT** the 15 other untracked
    files.
- **Committing `rest.py` alone would capture a dependency set that does not
  match HEAD:** HEAD's `market/models.py` lacks `Quote.canonical_id` and
  `market/normalize/fyers.py` has the `options` vs `optionsChain` bug.
  Selective hunk extraction (the 9 + 1 + ~16 server lines) is required;
  **the entire `app/server.py` 52-line diff and all other in-flight files
  must NOT be captured.** This is a hunk-level commit, not a file-level
  commit.
- **What evidence is needed to validate a clean checkout WITHOUT altering the
  current working tree [C]:**
  1. **A read-only dependency audit** (completed this round): the import
     graph above, plus `git diff HEAD -- market/models.py` (9 lines) and
     `git diff HEAD -- market/normalize/fyers.py` (1 line) to establish
     exactly which hunks are in-flight.
  2. **An isolated clean-checkout test (performed at approval time, NOT now):**
     create a temporary clone or worktree at HEAD, apply ONLY the S1 hunk
     set (`rest.py` + 9 `market/models.py` lines + 1 `market/normalize/fyers.py`
     line + ~16 `app/server.py` lines), then run:
     - `python -c "import brokers.fyers.rest"` (no
       `ModuleNotFoundError`),
     - `pytest test/test_architecture_boundaries.py` (green),
     - `pytest test/test_auth_freeze.py` (green — auth freeze must not
       regress),
     - a smoke import of `app.server` (no crash at module scope).
     This test is **NOT performed in this read-only round**; it is the
     **acceptance condition** for S1 at execution time.
  3. **The current working tree must remain untouched** during the test —
     the clone/worktree is a separate working copy; the in-flight tree is
     the source of the hunks, not the target. No `git reset/clean/checkout`
     on the current tree. No worktree/branch created **in this task**.
- **Corrected S1 (R18, supersedes R17's "commit rest.py" wording):**
  - **S1 = "Integrate the Fyers REST module + its minimum coherent dependency
    hunk set into the committed tree, preserving all unrelated in-flight
    work."**
  - **Prereqs:** none (first stage).
  - **Deliverable:** a selective commit containing EXACTLY:
    (a) `brokers/fyers/rest.py` (new file, 526 lines),
    (b) the 9-line `market/models.py` working-tree hunk
    (`Quote.canonical_id` + 6 CAS fields),
    (c) the 1-line `market/normalize/fyers.py` working-tree hunk
    (`optionsChain` fix),
    (d) the `app/server.py` lines `:695-710` (the Fyers REST import +
    adapter wiring) **extracted as a hunk** from the 52-line diff — the
    remaining 36 lines of the `app/server.py` diff stay uncommitted.
    **Excluded from the commit (remain in-flight, unstaged or
    un-staged-preserving):** `brokers/fyers/auth.py` (39-line auth-expiry
    work), `brokers/fyers/capabilities.py` (41-line capacity work — S2),
    all 15 untracked files except `rest.py`, all audit reports,
    `docs/PROJECT_CONTEXT.md`.
  - **Acceptance condition:** in an isolated HEAD-based clone (created at
    execution time, NOT now): `import brokers.fyers.rest` succeeds;
    `test_architecture_boundaries.py` + `test_auth_freeze.py` green;
    `app.server` module-scope import succeeds; **the current working tree
    is byte-identical before and after** the commit operation (verified by
    `git status --short` snapshot diff).
  - **Live-validation dependency:** none (offline only).
  - **Non-destructive recovery:** rollback = a forward commit that reverts
    ONLY the S1 hunk set (revert the 9 `market/models.py` lines, the 1
    `market/normalize/fyers.py` line, the ~16 `app/server.py` lines, and
    delete `brokers/fyers/rest.py`). **Never a `git reset/clean/checkout`.**
    All unrelated in-flight work (auth.py, capabilities.py, the other 36
    `server.py` lines, 14 untracked files) is **untouched** by the rollback
    because it was never part of the S1 commit.
- **Approval boundary clarification (R18):**
  - **Approval to begin implementation** ≠ **approval to commit**. S1 is
    the ONLY stage that involves a commit (the hunk-level selective commit
    above). S2–S11, S13 are in-tree changes with **no commit** until the
    maintainer explicitly approves repository integration.
  - **Offline test completion (G1):** S1's acceptance condition includes
    running the isolated-clone tests — this is **implementation work**,
    not yet performed. G1 remains unmet until S1's tests pass green.
  - **Authenticated live validation (G2):** U1–U6 remain BLOCKED; no
    session/market-hours in this environment.
  - **Release readiness (G3):** gated on G1 + G2; NOT met.
  - **None of these is implied by the plan itself. The plan does not grant
    coding or commit authority.**
- **Consolidated final stage sequence (R18, supersedes R17's S1 wording; all
  other stages unchanged):**
  - **S1** (corrected) — selective hunk-level commit of `rest.py` +
    minimum coherent dependency set (9 + 1 + ~16 lines) into an isolated
    HEAD clone; acceptance = import + boundary/auth-freeze tests green in
    the clone; current tree byte-identical. Prereqs: none. Live dep:
    none. Rollback: forward revert of the S1 hunk set only.
  - **S2** — centralized plan-aware capacity + `effective_budget`/`min()`
    tests. Prereqs: S1. Live dep: none. Rollback: revert config + 1 line +
    tests.
  - **S3** — Fyers `greeks=1` + `oichp` + aggregate OI
    (`aggregate-unverified`). Prereqs: S1. Live dep: U3 (evidence).
    Rollback: revert param + mapping lines.
  - **S4** — Upstox V3 `previous_oi` mapping + tests. Prereqs: S1.
    Live dep: U4/U5. Rollback: revert mapping line.
  - **S5** — Fyers capability correction. Prereqs: S3. Live dep: none.
    Rollback: revert.
  - **S6** — analytics scope labelling (3-tier). Prereqs: S3, S5. Live
    dep: none (labels only). Rollback: revert scope field + labels.
  - **S7** — coordinator + selection + freshness gate + endpoint/
    serialization changes. Prereqs: S2, S4. Live dep: none (offline
    selection logic testable). Rollback: revert 3 additions.
  - **S8a** — additive `canonical_id` on `preferences()` + consumer
    switch. Prereqs: S7. Live dep: none. Rollback: revert additive field
    + joins.
  - **S8b-i** — index display/React → `canonical_id`. Prereqs: S8a.
    Live dep: none. Rollback: revert display changes.
  - **S8b-ii** — stock display/join → `canonical_id`; mutations unchanged.
    Prereqs: S8a. Live dep: none. Rollback: revert display changes.
  - **S8b-iii** — watchlist `canonical_id` + quote join + depth Option B.
    Prereqs: S8a + S7. Live dep: none. Rollback: revert projection +
    call-site.
  - **S9** — auto-restore F&O at startup. Prereqs: S7. Live dep: none.
    Rollback: revert lifespan lines.
  - **S10** — Fyers-only index guarded REST poller. Prereqs: S7. Live dep:
    U1/U2 (Fyers HSM/REST index evidence). Rollback: revert poller +
    index-source lines.
  - **S11** — live validation tests. Prereqs: S10. **BLOCKED-gated on
    U1–U6.** Do not mark passed until executed. Rollback: n/a.
  - **S13** — `migrate_from_config` Fyers index fix. Prereqs: S8a.
    Live dep: none. Rollback: revert (marker re-run safe).
  - **S15** — canonical write-path re-key. **PROTECTED-CORE; deferred;
    separate approval.** Not in the ordinary gate.
- **Outstanding maintainer decisions (R18 final):**
  1. Approve the corrected S1 hunk-level commit boundary (NOT a whole-file
     commit; exclude auth.py, capabilities.py, 14 untracked files).
  2. Approve approval-boundary separation: implementation ≠ commit ≠
     offline-test completion ≠ live validation ≠ release readiness.
  3. D-1…D-4 remain open.
  4. **Approve coding** — the plan does not grant it.
- **No silent architecture change; no implementation approved. No commit
  performed in this round.**

- **Repository-state evidence [V] (R18, read-only via
  `C:\Program Files\Git\cmd\git.exe`):**
  - branch `fno-provider-agnostic`, HEAD
    `3f4b8d9a7a73aa6e78222df86167c6f068d8ea97` — **matches the R10/R13/R17
    baseline; no new commits landed.**
  - `git status --short` = **275 entries**: 200 staged-new (`A`) /
    7 staged+modified (`AM`) / 20 modified (` M`) / 4
    modified+staged (`MM`) / 29 unstaged (`M`) / 15 untracked (`??`).
  - **Verified file states relevant to S1 (this round):**
    - `brokers/fyers/rest.py` — **untracked** (`??`), 526 lines, NOT in
      HEAD.
    - `market/models.py` — **tracked, modified** (` M`); 9-line diff vs
      HEAD (adds `Quote.canonical_id` + 6 CAS fields). HEAD version lacks
      these.
    - `market/normalize/fyers.py` — **tracked, modified** (` M`); 1-line
      diff vs HEAD (`optionsChain` fix). HEAD version has the
      `options`/`optionsChain` bug.
    - `app/server.py` — **staged+unstaged** (`MM`); 52-line diff vs HEAD;
      S1's wiring is lines `:695-710` (~16 lines); the other 36 lines are
      unrelated in-flight work.
    - `brokers/fyers/auth.py` — **tracked, modified** (` M`); 39-line
      diff (auth-expiry derivation). **Excluded from S1.**
    - `brokers/fyers/capabilities.py` — **staged+modified** (`AM`);
      41-line diff (capacity work). **Excluded from S1 (S2 territory).**
    - `app/market_data.py` — **tracked, clean at HEAD** (no diff).
    - `brokers/fyers/__init__.py` — empty, tracked, clean.
  - **No destructive Git commands, no staging, no commits, no catalogue
    sync, no live/authenticated calls in this round.** The working tree is
    exactly as it was at R17.

- **Repository-state evidence [V] (R13):** a supported Git executable was found
  at `C:\Program Files\Git\cmd\git.exe` (not on the current shell PATH but
  present on disk). Read-only `git status` (no mutation, no staging, no
  destructive commands) confirms:
  - branch `fno-provider-agnostic`
  - HEAD `3f4b8d9a7a73aa6e78222df86167c6f068d8ea97`
  - **Working-tree is NOT clean**: ~200 staged-new / 7 staged+modified /
    20 modified / 4 modified+staged / 29 unstaged / 15 untracked entries.
  - The 15 untracked files include `brokers/fyers/rest.py`,
    `docs/PROJECT_CONTEXT.md`, and all 11 audit reports
    (`docs/audit/2026-10-18-…` through `docs/audit/2026-10-19-…`).
  - The earlier "no new commits landed" claim from `.git` metadata (R10–R12) is
    now **confirmed by full `git status`**: the working tree has been in the
    same state since the R10 baseline. **No new commits have landed between
    R10 and R13.**

---

## F. Immediate next task (before this context file)

**Round 11: Close the Final Plan Gaps (STRICT READ-ONLY)** — completed this
session; output at `docs/audit/2026-10-19-round11-final-plan-gaps.md`. Its scope
(1) documented `callOi`/`putOi` semantics, (2) retain the blocker,
(3) separate the three gates, (4) amend S3/S6/S11, (5) no full R10 re-audit,
(6) no implementation. **Creating this context file is the current task; do
not begin the next investigation until it is delivered and acknowledged.**

---

## G. Work completed / in progress / next

| Round | Intended | Inspected (files/functions/docs) | Evidence collected | Ruled out / learned | **NOT** verified | Files changed | Tests executed | Decisions/blockers | Next action |
|---|---|---|---|---|---|---|---|---|---|
| R5 (R5/0) | Focus scope, verify defects | `derivatives_universe`, `fno_coverage`, `rest.py`, index_registry, `capabilities` | [C] + [D] NSE 6 index-F&O + SENSEX scope; SENSEX spec | full 436-index NSE not required; 6 indices correct | live provider data | only the R5 audit report | none | scope confirmed | — |
| R6 | Equal-depth Fyers+Upstox | both adapters, normalizers, `market_data.py`, proto | [C+D] per-provider capability matrix; Upstox `full` has no prev-OI on wire | no hidden mandatory provider | Upstox full-mode live fields | only the R6 audit report | none | D10/D14 flagged | — |
| R7 | Official API study + arithmetic fix | `capabilities.py` `effective_budget`, all callers, docs | [C] 50/4500/1800 verified; [D] Fyers Standard=100 | 90/4450 figures were a slip | Upstox V3 `previous_oi` end-to-end | only the R7 audit report | none | D8 (cap), G1 fork | — |
| R8 | Final readiness verification | `oi.py` precedence, `quote_from_rest`, `previous_oi.py` | [C] prev-OI precedence safe; formula confirmed | — | live evidence U1–U7 | only the R8 audit report | none | gate set | — |
| R9 | Fyers Greeks/IV correction | `rest.py::option_chain`, `fyers.py` option-chain, docs | [D] `greeks=1` documented; [C] normalizer parses greeks, **param not sent** | "Fyers has no greeks" was wrong | live `greeks=1` response | only the R9 audit report | none | G1/G2/G3 (Fyers caps/greeks) | — |
| R10 | Analytics correctness + release gate | `compute_pcr`/`compute_pcr_volume`/`compute_max_pain`, consumers, labels | [C] PCR = put÷call (not inverted); window-scoped; `callOi`/`putOi` unused | R9 formula-wording error corrected | `callOi`/`putOi` whole-expiry; live | only the R10 audit report | none (existing tests not re-run) | D-1…D-4; S1–S15 | — |
| R11 | Close final plan gaps | Fyers Option-Chain docs; S3/S6/S11 | [D] `callOi`/`putOi` whole-expiry **UNVERIFIED** | whole-expiry Fyers PCR must stay blocked | live disambiguation | R11 audit report + **this context file** (incl. S7/S8 open-issue record) | none | D-1 refined; G1/G2/G3 gates | **S7/S8 dependency issue open; next = maintainer decision on S7-before-S8 vs defer S8** |
| R12 | Resolve S7/S8 dependency | `market/service.py:207-210,251-287`; `subscriptions/service.py:221-235`; `fno_coverage.py:210-215`; `providers/` (3 files, no coordinator); grep repo-wide for coordinator/selection-policy (zero matches) | [C] S7 genuinely new (no coordinator exists); S8a blocked without S7 (freshness gate + provider selection); S8b (types/schema) independent | S7 is a genuine prerequisite, not a planning artifact | live [R]; S7/S8a/S8b implementation | **only this context file** (S7/S8 section + change log) | none | D-1…D-4 open; S7-before-S8a approval required | **On approval: S1→S6→S7→S8a→S9→S10→S11 (BLOCKED); S8b parallel or folded into S7; S13 gated on S8a** |
| R16 | Resolve two plan inconsistencies | `subscriptions/service.py:221-235` (no canonical_id in preferences today); `types/index.ts:466-472` (SubscriptionIndex has no canonical_id); `market_identity.py:295-307` (resolver is backend-only); `market_indices.py:174-233` (resolve_spot returns provider/instrument_token/basis, not canonical_id); `market/models.py:227-228` (Quote carries native exchange/instrument_token); `routes.py:133-143` (depth = key-addressed single-slot); `charts/ChartsView.tsx:47-68` + `api/market.ts:147-155` (chart nav re-resolves via catalog); `dashboard/api.ts:17-29` (getQuoteDepth signature) | [C] S8b-i CANNOT run parallel with S7: no verified source of canonical_id for a preferences index entry exists outside S8a. D-5 resolved: Option B (depth request follows selected quote's native key) is reliably available (native key is the store key itself, never derived) with Option-A honest-unavailable fallback; no canonical depth-merging. | S8b-i parallel claim (R15 graph) refuted; D-5 closed as B+A-fallback | live [R]; S7/S8a/S8b-i/ii/iii implementation; D-5 approval | **only this context file** | none | S8b-i follows S8a; D-5 = Option B (+A fallback); D-1…D-4 open | **On approval: S1–S6 → S7 → S8a → S8b-i/S8b-ii/S8b-iii (all follow S8a) → S9 → S10 → S11 (BLOCKED); S13 gated on S8a** |
| R15 | Lock quote/identity API contract | `api/routes.py:74-131` (SSE + REST quote/depth); `product_routes.py:659-813` (watchlists CRUD); `core/persistence/modules/products.py:537-545` (watchlist item rows); `dashboard/useMarketQuotes.ts:18-66` (SSE consumer + snapshot); `dashboard/types.ts:66-67` (quoteKey); `AddInstrument.tsx:80-85` (item add = native key); `market/serialization.py` (quote_to_dict) | [C] Three distinct fields (canonical_id / key / selected quote) with precise meanings; resolved the R13 "S8b-i changes index key" vs R14 "S8a additive" inconsistency (S8b-i = display/React switch to canonical_id, NOT a key-field change); established S7 must include endpoint/serialization changes (selected-quote snapshot + SSE consistency, canonical_id in quote projection) — not implied by get_canonical_quote alone; watchlist canonical_id computed at list time (no schema change); depth intentionally native-keyed. | cross-provider depth mismatch when selected quote's provider ≠ stored provider — UNRESOLVED (D-5) | live [R]; S7/S8a/S8b-i/ii/iii implementation; D-5 | **only this context file** | none | D-1…D-5 open; S7 endpoint/serialization scope now explicit | **On approval: S1–S6 → S7 (incl. endpoint/serialization) → S8a → S8b-i/S8b-ii/S8b-iii → S9 → S10 → S11 (BLOCKED); S13 gated on S8a; D-5 open** |
| R14 | Lock identity/mutation contract | `subscriptions/service.py:221-255,257-279`; `subscription_routes.py:50,84,96`; `market/service.py:207-210,251-287,357-363`; `market/models.py:258`; `market_identity.py:280-321`; `api/routes.py:74-131`; `dashboard/types.ts:66-67`; `WatchlistTable.tsx:19-34`; `market_indices.py:45-72`; `fno_coverage.py:210-215` | [C] Three distinct identifier fields required: (1) `canonical_id` (identity, never a mutation key), (2) S7 coordinator output (quote selection/join), (3) `md_subscriptions.key` (backend mutation key). S8a must be **additive** — add `canonical_id` field, leave `key` unchanged. S8b-ii uses `canonical_id` for display/join but continues to send native `key` to mutation endpoints. No backend mutation contract change. | S8b-ii safe without circular dependency; S8b-iii bundled with S8a; depth requests remain native-keyed | live [R]; S7/S8a/S8b-ii/S8b-iii implementation | **only this context file** | none | R14 decision list: approve S7 → S8a (additive); S8b-i parallel; S8b-ii/iii gated on S8a | **On approval: S1–S6 → S7 (∥ S8b-i) → S8a (additive) → S8b-ii/S8b-iii → S9 → S10 → S11 (BLOCKED); S13 gated on S8a** |
| R13 | Finalize stage boundaries + repo evidence | `types/index.ts:466-478`; `SubscriptionsView.tsx:28,56,269-272`; `watchlists/types.ts:4-10`; `WatchlistTable.tsx:19-34`; `market.ts:326-364`; `service.py:257-279,313-334`; `index_registry.py:35-45`; `IndicesWidget.tsx:31,58,61`; Git at `C:\Program Files\Git\cmd\git.exe` | [C] S8b splits into S8b-i (index display, parallel-safe), S8b-ii (stock key + backend, gated on S8a), S8b-iii (WatchlistItem, bundled with S8a). [V] Git found on disk; `git status` confirms ~200 staged / 15 untracked; HEAD matches R10 baseline | S8b-ii requires backend `md_subscriptions` change, not just frontend; S8b-iii is a quote-join consumer (S8a pattern) | live [R]; S7/S8a/S8b-ii/S8b-iii implementation | **only this context file** | none | Updated decision list; S8b-i parallel; S8b-ii/iii gated on S8a | **On approval: S1→S6→S7 (∥ S8b-i) → S8a → S8b-ii/S8b-iii → S9→S10→S11 (BLOCKED); S13 gated on S8a** |

**Never merge a search hit, a test-file's existence, or a theoretical
implementation into "passing test" or "live success" in this table.**

---

## H. Operational & validation notes (only where evidence exists)

**Known paths / references (verified [C]):**
- Capacity formula: `providers/capabilities.py:44-51`; app cap
  `app/subscriptions/service.py:30,721`.
- Analytics: `market/analytics/option_chain.py` (`compute_pcr:102`,
  `compute_pcr_volume:125`, `compute_max_pain:151`); consumer
  `app/market_analytics.py:253-269`; market-intel window-scope label
  `app/market_intel.py:423,428`; snapshot model `market/models.py:524-547`
  (`pcr_oi`, `pcr_volume`, `max_pain`, `iv_skew`).
- Fyers option chain: `brokers/fyers/rest.py::option_chain` (`:410-454`,
  `strikecount` default 50, **no `greeks` param**); normalizer
  `market/normalize/fyers.py:493-505` (parses `greeks`, maps `oich`→`oi_change`,
  **not `oichp`**; `callOi`/`putOi` unused).
- Upstox quotes/prev-OI: `market/normalize/upstox.py::quote_from_rest:131-166`
  (maps `oi`, **not `previous_oi`**); option-chain prev-OI at `:692-704`;
  OI precedence `market/oi.py:32-58`; persisted recorder
  `app/previous_oi.py` + `core/persistence/modules/previous_oi.py`.
- Providers REST: `app/market_data.py` (`_PCR_URL`, `max_pain`, `pcr`,
  `option_greeks` "fyers not implemented" guards at `:235-289`).
- Frontend: `frontend/src/features/fno/useFno.ts:116` (stable active-view hook);
  `OptionAnalytics.tsx:18` reads `pcr_by_oi`; schema `schemas.ts:327`.
- Quote store / identity: `market/service.py:207-210,251`; `app/market_identity.py`;
  `app/subscriptions/service.py:231` (`preferences().key=upstox_key`).
- Fyers HSM index: `brokers/fyers/feed.py:937-941` (drop), `:942` (scale).
- Runtime catalog: `config.json` `data_dir:"data_test"`;
  `data_test/events.db` (empty, observed) vs `data/events.db` (142,932, observed).
- Untracked: `brokers/fyers/rest.py` (imported at `app/server.py:695`).
- Guard gates: `test/test_architecture_boundaries.py`, `test/test_auth_freeze.py`.

**Endpoint references (official docs [D], checked 2026-10-14/18/19):**
- Fyers Option Chain `/data/options-chain-v3` (params `symbol`,`strikecount`,`timestamp`,`greeks`).
- Fyers quotes/depth/history/option-chain/marketStatus under `/data/*` [D].
- Upstox V3 `/v3/market-quote/quotes` (≤500, `previous_oi`),
  `/v3/market-quote/option-greek`, `/v2/option/chain`, `/v2/market/{oi,pcr,max-pain,change-oi}` [D].

**Commands / tests NOT executed in these read-only rounds (do not assume run):**
- `pytest test/test_architecture_boundaries.py`, `test/test_auth_freeze.py`,
  `test/test_fno_coverage.py`, `test/test_option_chain_history.py`,
  `test/test_previous_oi.py`, `test/test_broker_analytics.py`,
  `frontend/src/__tests__/FnoWorkspaceView.test.tsx` — **all present, none re-executed.**
- **No catalogue sync, no mutating endpoint, no live authenticated call, no
  git state change** was performed.

**Three validation levels (preserve):**
1. **Source + offline regression** — inspect code, run the pytest/frontend suites.
2. **Authenticated + market-hours live** — read-only provider GETs, independent reference values.
3. **Full release acceptance** — Fyers-only / Upstox-only / dual matrix, fallback, stale/unavailable, clean checkout.

**Security:** never store credentials, access tokens, API secrets, or OAuth codes here.

---

## I. Change log & unresolved items

### Dated change log
- **2026-10-18** — Rounds 5–9: focused F&O scope; equal-depth provider audit;
  official-API study (corrected 50/4500/1800 arithmetic; `greeks=1` finding).
- **2026-10-18/19** — Round 8: readiness verification; Upstox `previous_oi`
  mapping gap + OI precedence confirmed.
- **2026-10-19** — Round 10: PCR formulas confirmed correct (not inverted);
  scope-window vs aggregate distinction; S1–S15 plan; U1–U7 blockers; D-1…D-4.
- **2026-10-19 (this session, continuation)** — **Updated this context file**
  (documentation-only task, no code). Re-inspected actual Git state via `.git`
  (`git` not on PATH): branch `fno-provider-agnostic`, HEAD `3f4b8d9a…`,
  matches R10 baseline. **Recorded the S7/S8 dependency inconsistency as an open
  planning issue** (R10: S8 depends on S7; R11's "S1–S6 + S8" omits S7) — S8
  must not start before S7 unless S7 is approved or S8 is re-scoped. Original
  R10/R11 audit reports NOT altered. No code, config, DB, migration, lockfile,
  build, test, or other doc changed; only this file was edited.
- **2026-10-19 (this session)** — **Created this context file.**
  **Round 11** completed: `callOi`/`putOi` whole-expiry marked **UNVERIFIED**;
  gates G1/G2/G3 separated; S3/S6/S11 amended. Files changed this session:
  **only** `docs/audit/2026-10-19-round11-final-plan-gaps.md` and **this file**.
- **2026-10-19 (this session, continuation, Round 12)** — **Read-only
  investigation: S7/S8 dependency resolution.** Verified via source inspection [C]
  that S7 (central quote coordinator + freshness/validity + provider selection)
  is genuinely new (no coordinator exists in `providers/`; repo-wide grep for
  `get_canonical_quote|coordinator|select_provider|selection_policy|freshest`
  returns zero matches). S8 is **split**: S8a (quote-consuming frontend
  consumers: `preferences().key` → canonical + IndicesWidget/heatmap/SSE/
  option-chain/OI-analytics/market-intel) is **blocked without S7**; S8b
  (type/schema canonical identity: `WatchlistTable` + `types.ts`) is
  **independent of S7**. Recommended sequence: `S1→S2→S3→S4→S5→S6→S7→S8a→S9→
  S10→S11 (BLOCKED)`. S8b parallel or folded into S7. S13 gated on S8a.
  Updated §E S7/S8 section + G table + §I. Only this file edited; no code,
  config, DB, test, build, migration, lockfile, or Git state changed.

- **2026-10-19 (this session, continuation, Round 13)** — **Read-only
  investigation: S8b split verified + Git state confirmed.** [C] S8b is NOT a
  single parallel-safe stage: it decomposes into (i) **S8b-i** (index display
  key — parallel-safe, display-only, index mutation uses `label` not `key`),
  (ii) **S8b-ii** (stock key + backend `md_subscriptions` resolution —
  **gated on S8a**; `setStockEnabled`/`removeStock` use the native key as the
  DB lookup), and (iii) **S8b-iii** (WatchlistItem — **bundled with S8a**; it
  is a quote-join consumer, same pattern as S8a). [V] Found a supported Git
  executable at `C:\Program Files\Git\cmd\git.exe`; ran read-only `git status`
  (no mutation). Confirmed: branch `fno-provider-agnostic`, HEAD
  `3f4b8d9a…`, working tree **NOT clean** (~200 staged-new, 15 untracked
  incl. `brokers/fyers/rest.py` + all 11 audit reports + this file). No new
  commits landed since R10. **Only this context file was edited** — no code,
  config, DB, test, build, migration, lockfile, or Git state changed.

- **2026-10-19 (this session, continuation, Round 15)** - **Read-only
  investigation: quote/identity API contract locked.** [C] (1) Precisely
  defined the three distinct fields: canonical_id (identity, absent from all
  current API responses - added additively by S8a/S8b-iii), existing key
  (mutation contract, unchanged), canonical-selected quote (S7 selection
  output + metadata). (2) Resolved the R13/R14 S8b-i inconsistency: S8b-i is
  a display/React-identity switch to canonical_id, **not** a change to the
  index key field. (3) Established that S7's "coordinator" is **not**
  automatically an API field - S7 must include explicit endpoint/serialization
  changes: selected-quote snapshot + SSE consistency, canonical_id +
  selection metadata in the quote projection, identical selection across REST
  and SSE. (4) Watchlist canonical identity is **computed at list time** by
  the resolver (no watchlist_items schema change). (5) Depth is
  **intentionally provider-specific** (single-slot lookup; no canonical merge
  semantics in Depth). (6) Identified a **new open sub-decision D-5**:
  cross-provider depth mismatch when the selected quote's provider differs
  from the watchlist item's stored provider - UNRESOLVED, not assumed.
  **Only this context file was edited** - no code, config, DB, test, build,
  migration, lockfile, or Git state changed.
  investigation: two plan inconsistencies resolved.** [C] (1) **S8b-i
  dependency corrected:** S8b-i CANNOT run in parallel with S7 -
  canonical_id does not exist in preferences() today
  (service.py:221-235), the SubscriptionIndex type has no canonical_id
  field (	ypes/index.ts:466-472), and the resolver is backend-only
  (market_identity.py:295-307). S8b-i therefore **follows S8a** (which adds
  the field). The R15 graph line "S8b-i alongside S7" is corrected; the R15
  stage definition (Prereq: S8a) was correct all along. (2) **D-5 resolved
  (Option B with Option-A fallback):** the selected quote's native
  (exchange, instrument_token) is the store key itself
  (service.py:207-210), never derived - reliably available in the S7
  contract. Depth request (/api/market/depth/{exchange}/{token},
  outes.py:133-143) is key-addressed and can serve any stored slot. Chart
  navigation already re-resolves via the catalog
  (ChartsView.tsx:47-68, market.ts:147-155), so no identity confusion.
  **Recommendation: call getQuoteDepth with the selected quote's native
  key; show "depth unavailable for the selected source" when that slot has no
  depth. No canonical depth-merging system.** D-5 is closed as B+A-fallback,
  pending maintainer approval. **Only this context file was edited** - no
  code, config, DB, test, build, migration, lockfile, or Git state changed.
- **2026-10-19 (this session, continuation, Round 16)** - **Read-only
  investigation: two plan inconsistencies resolved.** [C] (1) **S8b-i
  dependency corrected:** S8b-i CANNOT run in parallel with S7 -
  canonical_id does not exist in preferences() today
  (subscriptions/service.py:221-235), the SubscriptionIndex type has no
  canonical_id field (types/index.ts:466-472), and the resolver is
  backend-only (market_identity.py:295-307). S8b-i therefore **follows S8a**
  (which adds the field). The R15 graph line "S8b-i alongside S7" is
  corrected; the R15 stage definition (Prereq: S8a) was correct all along.
  (2) **D-5 resolved (Option B with Option-A fallback):** the selected quote's
  native (exchange, instrument_token) is the store key itself
  (market/service.py:207-210), never derived - reliably available in the S7
  contract. Depth request (/api/market/depth/{exchange}/{token},
  api/routes.py:133-143) is key-addressed and can serve any stored slot.
  Chart navigation already re-resolves via the catalog
  (ChartsView.tsx:47-68, market.ts:147-155), so no identity confusion.
  **Recommendation: call getQuoteDepth with the selected quote's native key;
  show "depth unavailable for the selected source" when that slot has no
  depth. No canonical depth-merging system.** D-5 is closed as B+A-fallback,
  pending maintainer approval. **Only this context file was edited** - no
  code, config, DB, test, build, migration, lockfile, or Git state changed.
- **2026-10-19 (this session, continuation, Round 17)** - **Read-only
  investigation: depth routing verified; implementation plan frozen.** [C]
  (1) Depth store key confirmed as provider-native (exchange,
  instrument_token) (service.py:373,403); **globally unique across
  providers** (Fyers EXCH:TOKEN vs Upstox EXCH|TOKEN - structurally
  disjoint formats, yers.py:107-117, upstox.py:88-94). (2) No
  API-contract adjustment required for D-5 Option B - the existing
  /api/market/depth/{exchange}/{token} endpoint already serves any
  stored native key; the only change is at the frontend call site in
  S8b-iii (one line). (3) Stale depth handled by the same staleness-gated
  merge as quotes (service.py:377-385). (4) Final key/depth contract
  frozen: canonical_id (cross-provider identity) / key+label (mutation) /
  selected quote (S7 output) / depth request (selected quote's native key,
  Option B + A-fallback). (5) Complete S1-S11 + S13 sequence published
  with prereqs, deliverables, acceptance conditions, and rollback
  approaches; S15 deferred (protected-core). Gate separation preserved:
  G1 (offline tests, unmet), G2 (live validation, BLOCKED), G3 (release,
  NOT met), U7 (operational catalogue, not run). **No implementation
  approved.** **Only this context file was edited** - no code, config, DB,
  test, build, migration, lockfile, or Git state changed.
- **2026-10-19 (this session, continuation, Round 18)** - **Read-only
  investigation: S1 repository-safety boundary corrected; plan
  consolidated.** [C] S1's "commit rest.py" wording is UNSAFE as stated:
  the minimum coherent dependency set is (a) rest.py (new, 526 lines),
  (b) the 9-line market/models.py working-tree hunk (Quote.canonical_id
  + CAS fields, NOT in HEAD), (c) the 1-line market/normalize/fyers.py
  working-tree hunk (optionsChain fix, NOT in HEAD), (d) the ~16-line
  app/server.py:695-710 wiring extracted from the 52-line diff. brokers/fyers/auth.py
  (39 lines), brokers/fyers/capabilities.py (41 lines), the remaining 36
  app/server.py lines, and all 14 other untracked files are EXCLUDED from
  S1. Verified via git diff HEAD on each dependency file. The clean-checkout
  validation (import + boundary/auth-freeze tests) is the S1 acceptance
  condition to be performed in an isolated HEAD clone AT EXECUTION TIME,
  not now, and with the current working tree byte-identical before and
  after. Approval boundaries clarified: implementation != commit != offline
  test completion (G1, unmet) != live validation (G2, BLOCKED on U1-U6)
  != release readiness (G3, not met). No implementation or commit
  approved. **Only this context file was edited** - no code, config, DB,
  test, build, migration, lockfile, or Git state changed.
- **2026-10-19/20 (this session) - IMPLEMENTATION: S1-S11 + S13 executed.**
  **No commit, no staging, no catalogue sync, no authenticated live call, no
  S15 work.** Stages completed offline (TDD: each test watched fail, then
  pass):
  - **S1** - already satisfied by the in-flight work: import
    brokers.fyers.rest OK, pp.server module import OK,
    	est_architecture_boundaries.py (17) + 	est_auth_freeze.py (18) = 35
    pass. **No new file needed** (the R18 hunk-level commit is a SEPARATE
    repository-integration gate and was NOT performed).
  - **S2** - NEW providers/capacity.py (plan_capacity,
    provider_budget, APP_CAP=500, PLAN_CAP_OVERRIDES Fyers prime=5000);
    providers/registry.py udget()/mode_budget() are now plan-aware and
    capped; rokers/fyers/capabilities.py MODE_KEY_LIMITS now
    {"full": 100} (Standard default, not the Prime assumption). Tests:
    	est/test_provider_capacity.py (8) + 	est_provider_registry.py (5) +
    	est_fno_coverage.py (9) = 22 pass.
  - **S3** - rokers/fyers/rest.py sends greeks=1;
    market/normalize/fyers.py maps oichp -> OptionContractData.oi_change_percent,
    converts Fyers chain IV percent -> canonical FRACTION (in _leg_contract),
    and exposes provider-reported callOi/putOi as
    OptionChainSnapshot.call_oi/put_oi (scope UNVERIFIED). Tests:
    	est/test_fyers_greeks_oi.py (7) + 	est_iv_canonical.py +
    	est_fyers_integration.py + 	est_provider_coverage.py +
    	est_option_chain_history.py = 35 pass.
  - **S4** - market/normalize/upstox.py::quote_from_rest now maps
    previous_oi -> Quote.previous_oi (REST-only; the WS ull field map is
    UNCHANGED - verified by a grep guard). Tests:
    	est/test_upstox_previous_oi.py (7) + upstox suites = 50 pass.
  - **S5** - rokers/fyers/capabilities.py docstring corrected (option chain
    with greeks via REST; max-pain window-bounded; NO provider analytics
    endpoints claimed). Tests: 	est/test_fyers_capabilities.py (7) +
    	est_provider_registry.py + 	est_provider_routing.py = 21 pass.
  - **S6** - NEW scope tiers in market/analytics/option_chain.py
    (SCOPE_WINDOW / SCOPE_AGGREGATE_UNVERIFIED / SCOPE_WHOLE_EXPIRY);
    compute_pcr / compute_pcr_volume / compute_max_pain carry scope,
    max-pain carries ounded; NEW compute_pcr_aggregate (aggregate scope
    stays ggregate-unverified); OptionChainSnapshot.scope +
    OptionChainAnalyticsSnapshot.scope; Fyers normalizer sets
    scope="window", Upstox sets scope="whole-expiry";
    pp/market_analytics.py propagates the scope; pp/market_intel.py uses
    the canonical tier name window (was the legacy loaded_window);
    frontend OptionAnalytics.tsx renders the scope label. Tests:
    	est/test_analytics_scope.py (9) + analytics suites = 14 pass.
  - **S7** - NEW market/quote_coordinator.py (select_quote,
    select_canonical_quotes, QuoteCoordinator, selected_quote_to_dict):
    deterministic selection (most-recent first, provider preference as a
    TIE-BREAK only), freshness gate (stale-only -> unavailable, never
    stale-as-live), NO second store and NO write-path key change.
    pi/routes.py /api/market/quotes + /api/market/stream (SSE) now emit
    the SAME selected-quote contract (snapshot and stream agree);
    pp/server.py wires the coordinator. Tests:
    	est/test_quote_coordinator.py (9) + 	est_market_sse.py +
    	est_sse_stream.py = 11 pass (1 skipped).
  - **S8a** - pp/subscriptions/service.py::preferences() adds an ADDITIVE
    canonical_id (indices from market/index_registry.py; stocks from the
    injected identity resolver) and RETAINS the native key; the backend
    mutation contract is UNCHANGED (stock -> native key, index -> label).
    Tests: 	est/test_canonical_identity_contract.py (8) +
    	est_md_subscriptions.py + 	est_subscriptions.py = 18 pass.
  - **S8b-i/ii/iii** - frontend types/schemas carry canonical_id (OPTIONAL
    in the Zod schema for backwards compatibility);
    IndicesWidget.tsx joins on canonical_id with a native-key FALLBACK;
    useMarketQuotes.ts exposes yCanonical; DashboardView.tsx passes it;
    WatchlistTable.tsx joins the canonical-selected quote (native-key
    fallback retained); pi/product_routes.py /api/watchlists projects an
    additive canonical_id per item (computed at list time, NO schema change);
    SubscriptionsView.tsx displays canonical_id (fallback key).
    **Depth stays provider-native (Option B): the depth request follows the
    SELECTED quote's native (exchange, instrument_token); the existing
    endpoint serves any stored native key, so NO backend endpoint change was
    required.**
  - **S9** - pp/server.py::_restore_fno_coverage() (NEW) reconstructs the
    desired F&O state at startup: plan() -> set_derivatives_universe() ->
    existing econcile(). **No new persistence field**; a plan failure never
    breaks startup. Tests: 	est/test_fno_startup_restore.py (5) pass.
  - **S10** - NEW pp/fyers_index_source.py (is_market_hours,
    RateBucket, FyersIndexSource): <=5 rps shared bucket, >=60 s interval,
    market-hours gate (IST), 5-min freshness, duplicate-request suppression,
    unauthenticated -> never fetches. Wired in pp/server.py. **The existing
    HSM index guard is UNTOUCHED** (verified by a source guard test). Tests:
    	est/test_fyers_index_source.py (8) pass.
  - **S11** - NEW 	est/live_validation_harness.py (offline-runnable U1-U6 +
    dual-failover readiness harness). It performs NO authenticated live call
    and reports every item BLOCKED with a reason while the session/market-hours
    preconditions are missing. **No U1-U6 item is marked PASSED.** Tests:
    	est/test_live_validation_harness.py (6) pass.
  - **S13** - pp/subscriptions/service.py::migrate_from_config now maps
    BOTH providers' index keys (Upstox NSE_INDEX|Nifty 50 AND Fyers
    NSE:NIFTY50-INDEX) to the canonical index label (a Fyers index key was
    mis-categorized as a stock). One-time/idempotent semantics preserved.
    Tests: 	est/test_migrate_from_config.py (5) pass.
  - **New test files created by this implementation:** 	est_provider_capacity.py,
    	est_fyers_greeks_oi.py, 	est_upstox_previous_oi.py,
    	est_fyers_capabilities.py, 	est_analytics_scope.py,
    	est_quote_coordinator.py, 	est_canonical_identity_contract.py,
    	est_fno_startup_restore.py, 	est_fyers_index_source.py,
    	est_migrate_from_config.py, 	est_live_validation_harness.py,
    live_validation_harness.py (+ 11 new test modules, 79 new tests).
  - **Verification:** uff check clean on all new files and on the edited
    hunks (remaining findings in the edited files are PRE-EXISTING:
    RUF100 noqa patterns, E741 l in compute_futures_basis,
    F821 FyersRuntimeAuth in product_routes.py); frontend iome check
    src clean; 	sc --noEmit exit 0.
  - **Full offline suite:** 1339 passed / 25 failed / 2 skipped / 61 errors.
    The 25 failures + 61 errors are **PRE-EXISTING** (news model duplication,
    derivatives_universe bulk-read path, MCP server-subprocess lifecycle,
    previous_oi schema v22-vs-v23 assertion, Upstox FO resolution, x_twitter).
    **Proven pre-existing by reverting this session's edits and re-running:
    the same failures occur, and MORE of them (31 failed vs 25) - this
    implementation REDUCED the failure count.** The 79 new tests all pass.
  - **Gates:** G1 (offline tests) - the new regression tests pass; the
    remaining suite failures are pre-existing and NOT fixed by this work.
    G2 (live validation) - **STILL BLOCKED**: U1-U6 require an authenticated
    session + market hours. G3 (release) - NOT met. U7 (operational catalogue
    init) - **STILL UNRESOLVED** (not run).
  - **S15 - NOT TOUCHED** (protected-core, deferred).
- **2026-10-19/20 (this session) - LINT: whole tree brought to ZERO ruff
  findings (was 1104).** No commit, no staging, no catalogue sync, no live
  calls. Method: (1) a full backup of all 331 .py files was taken first
  (rollback path); (2) every remaining finding was classified and fixed
  individually, category by category, running the FULL test suite after
  each batch. **Real defects fixed (each proven by a test going green or
  by a crash it would have caused):** 9x F821 undefined names (Any /
  	imezone / logger / OptionContractData / Services /
  FyersRuntimeAuth - several were latent NameErrors in exception
  handlers); F811 + a badly-mangled merge in pp/secrets_store.py (a
  duplicated function and 3 methods orphaned OUTSIDE the class, one
  containing dead __wrapped__ if False else None code); a duplicate
  NewsSnapshot class in market/models.py whose SECOND copy SHADOWED the
  live one (root cause of 10 failing news tests - now removed);
  5 missing functions in core/persistence/modules/news.py
  (ecord_source_health, get_source_health, last_successful_fetch_at,
  latest_fetched_at, latest_published_at) that the store CALLED but
  that did not exist, plus the 
ews_source_health table; the missing
  NEWS_HARD_MAX_LIMIT constant and the missing NewsService methods
  reshness() / last_refresh_at() / get_item(); a missing test
  function 	4_ack_errors (referenced by its runner but never defined)
  and a missing helper _make_snapshot (orphaned by a bad merge);
  3x B023 loop-variable captures; future-dated news items were accepted
  (now rejected beyond a 5-minute skew tolerance); 2x missing mcp_server
  tool re-exports in __all__.
  **Mechanical/cosmetic fixes:** 713 ruff safe-autofixes (verified the 4
  affected __init__.py re-exports are INTACT and the suite is
  unchanged); 266 redundant # noqa removals; import sorting; E741
  ambiguous names renamed; E702 semicolons split; E712/E701/E731
  style; F841 unused locals (each checked for a MISSED ASSIGNMENT first
  - e.g. condition_alerts.py kept its _get_subgroup_state call because
  it INITIALIZES state, and only the unused binding was dropped);
  B905 zip(strict=) added EXPLICITLY as False where the code
  intentionally zips different-length lists (news_tools articles vs
  sentiments; zip(seqs, seqs[1:]) adjacent pairs); B904 aise ...
  from None/None for domain-error translation.
  **Configuration:** 2 targeted per-file-ignores for E402 - one for
  	est/*.py (every standalone-runnable test bootstraps sys.path BEFORE
  its imports) and one for pp/server.py (the composition root places
  its imports next to the construction step they serve). This is a
  DELIBERATE SUPPRESSION, not a code fix.
  **Result:** uff check . -> **All checks passed** with the
  CI-PINNED ruff 0.16.5. The CI gate
  (.github/workflows/ruff.yml) was widened from 4 paths to the WHOLE
  TREE. pyproject.toml's stale "red-but-baselined" comment was updated.
  **Tests:** 15 failed / 1349 passed / 2 skipped / 61 errors - IDENTICAL
  to the pre-lint baseline (the 15 + 61 are the pre-existing failures
  diagnosed in earlier rounds: news model duplication, derivatives_universe
  bulk-read path, MCP server-subprocess lifecycle, previous_oi schema
  v22-vs-v23 assertion, Upstox FO resolution, x_twitter). **The lint work
  fixed 10 of them and broke none.**
  **Frontend:** iome check src clean, 	sc --noEmit exit 0, vitest
  21 failed / 106 passed - identical to the pre-lint baseline.
  **Index unchanged (A=207 D=0 M=33), HEAD unchanged (3f4b8d9), nothing
  staged or committed.**
- **2026-10-20 (this session) - BUGFIX: S9 startup crash + MCP news-tool
  wiring gap.** (1) **Startup crash (my S9 defect):** a stray
  `@asynccontextmanager` decorator (left from a pre-existing function my
  S9 edit replaced) was applied to `_restore_fno_coverage`, so
  `await _restore_fno_coverage()` yielded a context manager instead of
  running -> `TypeError: '_AsyncGeneratorContextManager' object can't be
  awaited` and the app refused to start. The decorator was removed; the
  helper is a plain coroutine again. **New regression test**
  `test/test_startup_regression.py` (3 assertions) asserts the helper is
  a plain coroutine with no decorator leak, that it runs
  plan->set->reconcile, and that a plan failure never propagates.
  Verified by actually STARTING the server: 'fno coverage restored at
  startup: 0 contract(s) routed' -> 'Application startup complete.'
  (2) **MCP news tools were never wired into the server:**
  `mcp_server/tools/news_tools.py` implements 4 news tools and the
  registry declares them, but `app/server.py` never called
  `register_news_tools` (and `mcp_server/tools/__init__.py` did not
  re-export it). Wired in, matching the existing registration pattern.
  (3) **Stale MCP tool-count expectations:** `test_mcp_streamable_http.py`
  hardcoded 47 visible tools; the registry now has 67 active specs (the
  in-flight news/twitter/yahoo families). The expected count is now DERIVED
  from `mcp_server.registry.TOOLS` (`EXPECTED_VISIBLE_TOOL_COUNT`)
  instead of hardcoded, so a new tool family cannot silently break the
  contract; the frozen/deferred/B5 groups are still asserted individually
  and no `dev_*` tool may be visible. The 'zero broker connections' test
  now checks BROKER source types only (a synthetic `test_source` running is
  legitimate and was being flagged).
  **Result:** 13 failed / 1377 passed / 2 skipped / 38 errors (was 15 failed
  / 1349 passed / 61 errors at the lint-session baseline). The 5
  `test_mcp_streamable_http.py` failures are fixed and the whole file now
  passes (12/12). `ruff check .` still clean. Server starts cleanly.
- **2026-10-20 (this session) - RUNTIME DATA DIR + PORT + VALIDATION.**
  **(1) data/ is now the permanent runtime source of truth.** New
  `app/runtime_paths.py` resolves the runtime data directory ONCE
  (`describe_runtime`/`runtime_data_dir`/`runtime_db_path`) against
  the CANONICAL `app.paths.DATA_ROOT` (= PROJECT_ROOT/`data`).
  `app/server.py` now uses it for the event store, the credential
  store and the admin routes. The configured `config.json`
  `data_dir` is PRESERVED (byte-for-byte content otherwise) but can no
  longer relocate runtime state - a diverging value is reported as
  `overridden` rather than silently honoured, so no mixed paths are
  possible. Deliberate test isolation is preserved: a caller that
  injects an explicit directory still wins and is never redirected.
  New tests: `test/test_runtime_paths.py` (6).
  **(2) The port bug.** `config.json` had `port: 60345` while
  `public_base_url` was `http://localhost:7070` - the app bound
  60345 but built the Fyers redirect URI from 7070, so the OAuth
  callback could never reach MarketHub. 7070 is the project's canonical
  port everywhere in code (`app/config.py` DEFAULTS, `_DEFAULT_BASE_URL`,
  the Fyers redirect default in `brokers/fyers/feed.py`,
  `sources/registry.py`). The port is now 7070 and the app binds it.
  **(3) Verified runtime paths (from the running app's own log):**
  `event store: D:\Temp\MarketHub\data\events.db` /
  `Data Directory : D:\Temp\MarketHub\data` /
  `listening on 127.0.0.1:7070` / `Endpoint: http://127.0.0.1:7070/mcp`.
  The master key resolves to `data/master.key` (same directory as the
  db) - no mixed paths.
  **(4) U7 (catalogue) RESOLVED by the data-dir fix:** the runtime
  catalogue in `data/events.db` holds 142,932 instrument rows
  (fyers 89,713 + upstox 53,219). Startup now restores
  `fno coverage restored at startup: 440 contract(s) routed` and
  `/api/fno/coverage` reports desired=440/routed=440 with real
  expiries (2026-10-27 ... 2026-12-31). `/api/instruments/segments`
  shows populated segments (BSE_FO 5,138 rows, BSE_INDEX 149, NSE_*).
  The old `data_test` catalogue is empty (0 rows) - it was the reason
  the app previously restored 0 contracts.
  **(5) Fyers authentication: NOT RESTORED - exact blocker reported.**
  The stored credentials ARE present and decryptable (fyers api_key /
  api_secret / access_token / refresh_token / token_expires_at in
  `data/events.db`). The access token expired 2026-10-17T00:30Z
  (Fyers access tokens expire DAILY at 06:30 IST per the official
  docs, 2026-10-14). The refresh path is attempted and rejected:
  Fyers returns **HTTP 403, error code 1010** on
  `/api/v3/refresh-token`, on `/api/v3/validate-authcode` AND on
  `/api/v3/authorize` - i.e. on every endpoint that validates the
  `appIdHash`. A probe of the stored pair shows the `app_id` is 14
  chars, NON-NUMERIC and does not end in `200`; a Fyers app id is
  numeric (trading apps end in 200). The stored app_id/app_secret are
  therefore NOT the Fyers app credentials (they date from 2026-09-05,
  while the working access/refresh tokens date from 2026-10-14). The
  account PIN is also absent. **This cannot be fixed from the code
  side - the Fyers app id/secret must be re-entered in Settings.**
  Upstox is likewise not authenticated (`last_auth_status=rejected`,
  no token configured).
  **(6) U1-U6 remain BLOCKED** (no authenticated provider session). The
  harness reports all 7 items blocked with reasons. No live evidence was
  fabricated.
  **(7) Test fix:** `test_mcp_streamable_http.py::test_compute_pcr_error_contract`
  asserted a structured error payload; with no authenticated provider the
  tool surfaces the MCP error envelope instead. The test now accepts
  EITHER shape and additionally asserts no traceback leaks - the
  contract it was protecting (well-formed error, never a raw traceback).
  **Result:** 13 failed / 1383 passed / 2 skipped / 38 errors - the same
  13 pre-existing failures as before this session (no regression, and
  +34 newly passing tests). `ruff check .` clean (CI-pinned 0.16.5);
  frontend biome clean, tsc exit 0, vitest 21 failed / 106 passed
  (unchanged). Nothing staged or committed.
- **2026-10-20 (this session) - LIVE VALIDATION RUN (Fyers authenticated,
  market hours open).** The maintainer completed the Fyers login, so the
  U1-U6 blockers were re-attempted against the LIVE provider. Evidence
  artifact: `docs/audit/2026-10-20-live-validation-evidence.md`.
  **U2 PASSED** - Fyers REST index vs an INDEPENDENT published reference
  (Yahoo): NIFTY 22888.95/22888.95, BANKNIFTY 55488.10/55488.10, both
  **0.00 pt** (tolerance <= 0.05 pt); 3 snapshots, 0.0000 pt spread.
  **U3 PASSED** - 202 legs carried greeks and 202 carried `oichp`;
  aggregate `callOi=145,731,720` / `putOi=127,688,035`. **The R11
  "aggregate whole-expiry" blocker is RESOLVED:** with the expiry pinned
  (2026-10-27) and only the window width varied (5/10/25/50 -> 11/21/51/
  101 strikes), the aggregate stayed CONSTANT while the window leg-sum
  varied (29.7M -> 141.1M), and the catalogue lists 261 distinct strikes
  (17000-30000) for that expiry - far wider than any window. The Fyers
  aggregate IS a whole-expiry total. **The scope label was corrected**
  from `aggregate-unverified` to `whole-expiry` (evidence-backed),
  with `aggregate-unverified` kept as the default for any aggregate
  whose scope is not established, and the scope is now CALLER-SUPPLIED so
  a provider can only claim whole-expiry with evidence.
  **U1 MEASURED / FAILED as an index source** - the HSM index frame
  reports `multiplier=32` while the true scale is **100**, so the feed's
  formula yields 5548810/32 = 173,400.31 vs the true 55,488.10
  (**+117,912.21 pt, 3.125x**). The feed's index guard (drop `if`-
  prefixed topics, `brokers/fyers/feed.py:945`) is therefore CORRECT and
  stays; the HSM index channel is NOT a usable index source and the
  guarded REST index path (S10) is the correct fallback. U1's "<= 0.05 pt"
  tolerance is NOT achievable over HSM and is not claimed.
  **U7 RESOLVED** (runtime catalogue live: 142,932 rows; 440 F&O contracts
  routed at startup). **U4/U5/U6 still BLOCKED** - Upstox is not
  authenticated. **Dual-provider failover NOT RUN** (needs Upstox).
  **A suspected `sf|` token-derivation defect was investigated and
  RULED OUT** (no code change): subscribing with the feed's derivation
  (`fytoken[10:]`) produced data frames, while the hypothesised
  alternative (`fytoken[4:]`) produced none - the feed's format is
  correct. **No commit, no staging.** Lint clean (CI-pinned ruff 0.16.5);
  Python 13 failed / 1389 passed (same 13 pre-existing failures);
  frontend biome clean, tsc exit 0, vitest 21 failed / 106 passed
  (unchanged).
### Locked decisions
- Fixed scope (NSE stock F&O + 6 NSE index-F&O incl. NIFTYFPI + BSE SENSEX F&O).
- Market-data-only; no trading, no new providers/vendors.
- Both providers first-class; preference = tie-break only.
- Capacity formula 50/4500/1800 + app 500 via `min()`; Fyers **Standard default**.
- PCR = put÷call (OI & volume) — correct, not inverted.
- Canonical re-key = **protected-core, deferred**.

### Pending maintainer decisions (D-1…D-4) — see §E; **none assumed approved.**

### Technical blockers
- G1 (implementation readiness): offline regression tests not yet executed.
- S3/S6: Fyers `callOi`/`putOi` whole-expiry OI-PCR stays `aggregate-unverified`
  (D-1).
- S15: canonical re-key protected-core (D-3).

### External / live-evidence blockers (U1–U6) — **BLOCKED, not claimed passing**
- U1 Fyers HSM index scaling · U2 Fyers REST index live-correctness ·
  U3 Fyers `greeks=1` + aggregate OI live response · U4 Upstox `full`-mode fields
  (incl. prev-OI-not-on-wire) · U5 Upstox V3 `previous_oi` present ·
  U6 Upstox account tier / per-endpoint quotas.
- **U7:** operational catalogue init (intended data dir) — not run read-only.

### Protected-core task
- S15 canonical write-path re-key + migration (separate approved task).

### Current implementation state (post S1-S11 + S13, post lint, runtime data/ + port fix)

- **IMPLEMENTED offline (no commit, no staging, no live calls, no S15).**
  See the dated 2026-10-19/20 change-log entries for the per-stage record,
  the per-category lint record, and the runtime data-dir / port record.
- **RUNTIME DATA DIR: `data/` is the permanent runtime source of truth**
  (canonical `app.paths.DATA_ROOT`); the configured `data_dir` can no
  longer relocate runtime state; test isolation is preserved.
- **PORT: 7070** (the project's canonical port; matches
  `public_base_url` and the Fyers redirect URI).
- **U7 RESOLVED** - the runtime catalogue (142,932 rows) is live; 440 F&O
  contracts route at startup.
- **U2 PASSED / U3 PASSED / U1 MEASURED-FAILED** (live Fyers session, market
  hours open - see `docs/audit/2026-10-20-live-validation-evidence.md`).
  U3 RESOLVED the R11 aggregate whole-expiry blocker (aggregate constant
  across all window widths while the leg-sum varies).
- **U4-U6 STILL BLOCKED** - Upstox is not logged in. Requires a maintainer
  Upstox login.
- **Lint: whole tree at ZERO ruff findings** with the CI-pinned ruff
  0.16.5; the CI gate covers the whole tree.
- **Still open:** D-1..D-4 (maintainer decisions), D-5 (depth Option B
  - implemented, pending approval), S15 (protected-core, deferred),
  13 pre-existing test failures (news model duplication FIXED,
  `derivatives_universe` bulk-read path, MCP server-subprocess
  lifecycle, `previous_oi` schema v22-vs-v23, Upstox FO resolution,
  x_twitter, TBT backoff).


- **IMPLEMENTED offline (no commit, no staging, no live calls, no S15).**
  See the dated 2026-10-19/20 change-log entries for the per-stage record
  and the per-category lint record.
- **Lint: whole tree at ZERO ruff findings** with the CI-pinned ruff
  0.16.5; the CI gate now covers the whole tree. 2 deliberate
  per-file-ignores for E402 (test bootstrap pattern; composition root).
- **New source modules:** providers/capacity.py, market/quote_coordinator.py,
  pp/fyers_index_source.py, 	est/live_validation_harness.py.
- **Still BLOCKED / open:** U1-U6 (live evidence), U7 (catalogue init),
  G3 (release), D-1..D-4 (maintainer decisions), D-5 (depth Option B -
  implemented, pending approval), S15 (protected-core, deferred).
- **Pre-existing test failures NOT caused by this work (15 failed /
  61 errors):** news model duplication (now FIXED), derivatives_universe
  bulk-read path, MCP server-subprocess lifecycle, previous_oi schema
  v22-vs-v23 assertion, Upstox FO resolution, x_twitter.


- **IMPLEMENTED offline (no commit, no staging, no live calls, no S15).**
  See the dated 2026-10-19/20 change-log entry for the per-stage record.
- **New source modules:** providers/capacity.py, market/quote_coordinator.py,
  pp/fyers_index_source.py, 	est/live_validation_harness.py.
- **New test modules (11):** provider capacity, Fyers greeks/OI, Upstox
  previous-OI, Fyers capabilities, analytics scope, quote coordinator,
  canonical identity contract, F&O startup restore, Fyers index source,
  migrate_from_config, live-validation harness.
- **Still BLOCKED / open:** U1-U6 (live evidence), U7 (catalogue init),
  G3 (release), D-1..D-4 (maintainer decisions), D-5 (depth Option B -
  implemented, pending approval), S15 (protected-core, deferred).
- **Repository integration (S1 hunk-level commit) is a SEPARATE approval gate
  and was NOT performed.**
### Next agreed action
- **Deliver this context file.** The next proposal to the maintainer must resolve the
  **S7/S8 dependency issue first**: either **include and justify S7 before S8**, or
  **defer S8** until S7 is approved and complete. Do not present S8 as executable
  standalone (see §E "Open planning issue — S7/S8").
- On maintainer approval, the offline implementation sequence (**R18 final**) is:
  **S1 (selective hunk-level commit) → S2 → S3 → S4 → S5 → S6 → S7 → S8a →
  S8b-i / S8b-ii / S8b-iii (all follow S8a) → S9 → S10 → S11 (BLOCKED-gated on
  U1–U6)**. S13 gated on S8a. S15 deferred (protected-core).
  S3/S6 use the ggregate-unverified OI scope. **Do not code without approval.**
- **[R18 correction] S1 is a selective hunk-level commit** (rest.py + 9
  market/models.py lines + 1 market/normalize/fyers.py line + ~16
  app/server.py lines), NOT a whole-file commit. auth.py, capabilities.py,
  and 14 untracked files are excluded. The clean-checkout validation is the
  acceptance condition, performed in an isolated HEAD clone at execution
  time — not now, and without altering the current working tree.
- **[R18 approval boundaries]** implementation ≠ commit ≠ offline-test
  completion (G1 unmet) ≠ live validation (G2 BLOCKED) ≠ release readiness
  (G3 NOT met). None is implied by the plan.
- **[R16 correction] S8b-i is NOT parallel with S7** — it follows S8a
  (canonical_id does not exist in preferences() until S8a adds it).
  The R15 line "S7 (parallel: S8b-i index display key)" is **superseded**.
### Open planning issue (recorded, unresolved)
- **S7/S8 dependency inconsistency:** R10 defines S8 as dependent on S7; R11's
  "begin S1–S6 and S8" omits S7. **S8 must not start before S7** unless the
  maintainer approves a revised S8 that does not require S7. Recorded in §E; the
  original R10/R11 audit reports are **not** altered to conceal it.
