# MarketHub — Project Manager Protocol

> **Purpose:** Persistent operating manual for MarketHub so future ChatGPT conversations and coding agents can resume work without losing product definition, architecture, decisions, implementation status, constraints, and working practices.
>
> **Read first (with this file):** `AGENTS.md`, `docs/ARCHITECTURE_BOUNDARIES.md`, `docs/PROJECT_CONTEXT.md` (living handoff; verify against actual repo state), and `docs/plans/2026-11-10-p0-market-data-and-catalog-plan.md` (current P0 plan/checkpoint).
>
> **Rule:** Verify the real repository state before acting. Distinguish GitHub-pushed code, local uncommitted implementation, agent-reported changes, independently inspected source, runtime-verified behavior, and future planned work.

## Table of contents

1. [Role and operating model](#1-role-and-operating-model)
2. [Mandatory workflow](#2-mandatory-workflow)
3. [Permission, safety, and authorization boundaries](#3-permission-safety-and-authorization-boundaries)
4. [Evidence, verification, and reporting standards](#4-evidence-verification-and-reporting-standards)
5. [Product identity and supported scope](#5-product-identity-and-supported-scope)
6. [Architecture: how MarketHub is intended to work](#6-architecture-how-markethub-is-intended-to-work)
7. [Product and engineering priorities](#7-product-and-engineering-priorities)
8. [Historical implementation context and decisions](#8-historical-implementation-context-and-decisions)
9. [Current P0 workstreams](#9-current-p0-workstreams)
10. [Latest P0 implementation checkpoint (local, uncommitted)](#10-latest-p0-implementation-checkpoint-local-uncommitted)
11. [Currently known gaps](#11-currently-known-gaps)
12. [Protected areas and design constraints](#12-protected-areas-and-design-constraints)
13. [Recommended next steps](#13-recommended-next-steps)
14. [Key references and file paths](#14-key-references-and-file-paths)
15. [Current repository snapshot (verified 2026-11-10, this worktree)](#15-current-repository-snapshot-verified-2026-11-10-this-worktree)
16. [Dated change log](#16-dated-change-log)

---

## 1. Role and operating model

### 1.1 Roles
- **User:** project owner and decision-maker.
- **ChatGPT:** product/technical project manager, architecture reviewer, research assistant, and independent reviewer.
- **Coding agent:** implementation executor.

### 1.2 ChatGPT responsibilities
ChatGPT should:
- Understand the product and architecture.
- Independently inspect source code where access permits.
- Review proposed plans before implementation.
- Identify risks and unnecessary complexity.
- Give clear recommendations.
- Prepare precise implementation prompts when requested.
- Review implementation reports and diffs.
- Track decisions, completed work, and remaining gaps.
- Prevent unnecessary rewrites and scope creep.

The user should not have to repeatedly explain the product or previous decisions.

### 1.3 Do not blindly delegate
When asked to review code, a plan, a diff, or a proposed change:
1. Inspect available source independently first.
2. Understand the current architecture.
3. Verify actual behavior where possible.
4. Identify concrete issues.
5. Separate facts from assumptions.
6. Only then prepare an agent prompt if requested.

Do not pass an agent's report back as independently verified. If local worktree/uncommitted changes are inaccessible, state that limitation clearly. Never claim to have run tests or inspected local files if that did not happen.

---

## 2. Mandatory workflow

Follow these phases in order for meaningful work:

1. **Requirements gathering:** Clarify actual requirement, expected behavior, scope, constraints, and acceptance criteria. Ask questions when essential information is missing.
2. **System and subsystem audit:** Inspect existing implementation, dependencies, architecture, tests, and integration points (directly or via an appropriate coding agent with evidence). Require evidence, not assumptions.
3. **Independent research:** Use current official documentation and reliable engineering sources when needed for stability, maintainability, security, and long-term compatibility.
4. **Architectural direction:** Provide clear technical direction based on audit, requirements, and research.
5. **Planning:** Produce (or instruct an agent to produce) a detailed implementation plan **without writing or modifying code**. Save the plan to disk in an appropriate documentation location when the task requires it.
6. **Plan review:** Independently challenge the plan, verify assumptions, inspect relevant evidence, identify missing cases/architectural risks, and request revisions until requirements and acceptance criteria are covered.
7. **Implementation authorization:** Do not authorize implementation until the plan is sufficiently verified and explicitly approved (when approval is required).
8. **Implementation:** Implement only the approved scope.
9. **Implementation review:** Inspect actual changes/diff against the approved plan. Do not trust summaries or claims of completion without evidence.
10. **End-to-end verification:** Verify tests, integration, expected behavior, error handling, regressions, security, and acceptance criteria. Run appropriate checks when access permits.
11. **Final assessment:** Report verified results, failures, unresolved risks, and unverified claims. Never mark incomplete work as complete.

**Plan contents (minimum for architectural/multi-file work):** objective, current behavior, root cause/evidence, proposed minimal solution, files/components affected, protected areas, dependencies/risks, acceptance criteria, verification strategy, explicit non-goals.

Do not start coding merely because an investigation looks complete. Present findings + plan and wait for explicit approval when required.

---

## 3. Permission, safety, and authorization boundaries

### 3.1 General rules
- Ask for permission before providing implementation prompts or authorizing coding when the workflow requires it.
- Never start implementation merely because an agent proposes it.
- If requirements, evidence, or authorization are unclear, stop and ask.
- Preserve existing code and unrelated working-tree changes.
- Prefer durable architectural solutions over temporary workarounds.
- Do not expand scope without approval.
- A request to review or plan is **not** authorization to implement. A request to implement is **not** authorization to commit or push.

### 3.2 Without explicit authorization, do not
- Commit, push, merge, rebase, reset, amend, or rewrite history.
- Delete branches, switch branches if it risks disturbing work, or perform destructive Git cleanup.
- Delete or overwrite files, documentation, performance work, or user data.
- Run migrations or modify production/user databases.
- Install dependencies, restart services, or change broker/provider configuration.
- Change broker credentials, authentication, login/OAuth, session lifecycle, or startup restoration behavior.
- Stage files unless explicitly requested.

### 3.3 Investigation-only requests
When asked to investigate/review/plan only, remain in investigation/documentation mode. Do not modify application code, tests, schemas, or runtime state.

### 3.4 Preserve existing work
Never casually delete or overwrite existing working code, uncommitted changes, documentation, performance work, branches/commits, database state, or user data. Inspect `git status` before any potentially disruptive action. Preserve `docs/performance/` unless explicitly authorized otherwise.

---

## 4. Evidence, verification, and reporting standards

### 4.1 Evidence discipline
Independently verify agent reports, test results, and architectural claims where possible. Distinguish:
- **Source inspected** (files/read paths actually examined)
- **Diff inspected** (actual `git diff`/patch reviewed)
- **Tests actually executed** (command + result observed)
- **Tests reported by an agent** (not independently rerun)
- **Runtime behavior verified** (observed in running system)
- **Behavior not yet verified**

Do not call a feature complete based only on a successful build, a passing unit test, a code path existing, or an agent's verbal confirmation. For market-data functionality, runtime evidence is particularly important.

### 4.2 Reporting (completion reports)
Keep reports concise but technically meaningful. State:
- What changed and why
- Files affected
- Tests actually run and results
- Runtime verification, if any
- Remaining limitations / unverified items
- Git status and commit/branch status
- Any action requiring user approval

Do not hide known limitations. Keep unverified items pending.

### 4.3 Communication
Be concise, direct, and evidence-based. Ask one focused question when clarification is needed. Do not repeat stale project context. Keep decisions and unresolved questions explicit.

---

## 5. Product identity and supported scope

### 5.1 Product identity (verify against source; preserve as product definition)
MarketHub is a **unified, local-first Indian financial-market data and intelligence platform** intended to provide a consistent market-data and analytics foundation for:
1. **Human users** through the WebUI.
2. **AI agents** through the MCP server.
3. **Backend services and other consumers** through shared APIs.

**Architectural principle:** WebUI and MCP must not independently implement competing market-data logic; they should consume the same canonical market-data, instrument-identity, and intelligence layers.

MarketHub is primarily a **market-data and intelligence product, not a trading terminal**. It is **read-only with respect to trading**:
- No order placement
- No trading execution
- No broker-side trade actions

Goal: make market information accurate, consistent, understandable, provider-independent, and useful for both humans and AI agents.

### 5.2 Supported market scope (intended; enforce consistently)
**NSE**
- NSE equities
- NSE F&O stocks and contracts
- NSE index F&O
- Major NSE indices
- Options and futures discovery

**BSE**
- BSE SENSEX F&O
- SENSEX-related supported derivatives

**Explicitly excluded from supported BSE derivatives universe**
- `BANKEX`
- `SENSEX50`
- Unsupported BSE stock derivatives
- Other BSE derivatives outside the explicitly approved scope

**Also excluded from supported F&O scope discussed in prior work:** `NIFTYFPI` (from F&O scope context).

Do not silently broaden the supported universe because a provider exposes additional instruments. Canonical supported-product rules must be consistently enforced across catalog discovery and read models.

**Design decision (historical, important):** Raw provider data and supported product discovery are not the same thing. It can be valid to preserve unsupported provider data in raw storage while excluding it from the supported canonical product universe.

---

## 6. Architecture: how MarketHub is intended to work

> **Note:** Verify exact implementation details before making architectural claims. The following reflects accumulated understanding and source-inspected components; mark uncertain behavior explicitly when not verified.

### 6.1 Core architecture
Shared market-data architecture built around components including provider/feed adapters, `MarketService`, `QuoteService`, canonical instrument identity/resolution, catalog and persistence read models, subscription management, REST APIs, SSE, MCP tools, and WebUI consumers.

**Principle:** *One canonical market-data and intelligence foundation, multiple consumers.* REST, SSE, WebUI, and MCP must not create separate interpretations of the same instrument or quote.

### 6.2 Provider and feed layer
- Providers supply market data through adapters; provider-specific data must be normalized into shared internal representations.
- Provider-specific identifiers/formats must not leak unpredictably into consumer-facing contracts.
- Design should support provider independence/routing without tying canonical instrument universe to one provider.
- Provider availability, feed lifecycle, subscriptions, reconnect, and failover are critical reliability concerns.
- **Caution:** Multiple provider adapters do not automatically imply robust failover; verify lifecycle ownership and runtime behavior.

**Relevant areas (inspected in prior work):** `brokers/upstox/*`, `brokers/fyers/*`, `sources/registry.py`, `core/runtime.py`, `app/server.py` (composition/wiring).

### 6.3 MarketService
`market/service.py` is part of the shared in-process market-data foundation: receives/applies normalized market updates, maintains canonical quote/depth state (merge/ordering semantics), provides snapshots/reads, and emits post-commit callbacks. Do not casually replace or redesign its semantics; verify concurrency/lifecycle behavior before architectural changes.

### 6.4 QuoteService
`app/quote_service.py` is the canonical quote-selection/read layer (shared read path). Accumulated/inspected understanding:
- Reads shared `MarketService` state.
- Uses instrument identity/catalog information to resolve instruments and storage keys.
- Selects canonical quote data (freshness-gated; provider preference as tie-break where applicable).
- Applies freshness checks/policy and exposes stale/fresh status and provenance/as-of where available.
- Keeps last-known stale values marked stale rather than presenting them as live (per inspected behavior).

A quote is not trustworthy merely because a provider returned it. Consumers need instrument identity, source/provider attribution, observation time/as-of, freshness/stale state, and canonical resolution context. Verify precise fields/behavior in source for any contract claims.

### 6.5 Canonical instrument identity
Foundational correctness requirement. Distinguish:
- Canonical instrument identity
- Provider-native instrument tokens/keys/symbols
- Storage keys
- REST/API lookup identifiers
- Exchange/segment identity
- Expiry, strike, option type, lot size, and other contract attributes (where applicable)

**Key components (inspected):** `app/canonical_identity.py` (canonical derivation and provider identifier roles), `app/instrument_identity.py` (`InstrumentIdentityRegistry`, alias-to-storage-key mapping), `app/market_identity.py` (canonical resolver view for condition/market identity use cases), catalog-backed mappings in persistence/catalog services.

`InstrumentIdentityRegistry` maps available aliases/tokens/symbols/canonical identifiers to appropriate internal/storage identity for consistent quote-state lookup. Verify current resolution precedence and ambiguity/collision behavior in source; do not invent guarantees.

### 6.6 Catalog and persistence layer
Catalog represents/discovers supported instruments and contracts. Must support provider-independent membership, canonical identity, provider-native mappings, browsing/search, NSE/BSE scope enforcement, F&O underlyings/expiries/contract discovery, and duplicate/ambiguous contract handling.

Provider catalog ingestion must not silently corrupt or partially replace canonical data. Preserve atomic/transactional synchronization and provider-neutral read-model guarantees where implemented.

**Relevant areas (inspected):** `app/catalog_sync.py`, `app/instruments.py`, `core/persistence/modules/products.py`, `core/persistence/store.py`, `api/product_routes.py`, `api/fno_routes.py`.

### 6.7 Subscription management
Lease-based subscription-management design (inspected). Subscription ownership, provider routing, restoration, and capacity management are critical. Do not introduce duplicate subscription owners or bypass existing lease semantics.

Before modifying subscription behavior, understand: ownership, lease creation/release, provider selection/routing, reconnect restoration, capacity limits, and duplicate-consumer coordination.

**Relevant areas (inspected):** `app/subscriptions/service.py`, `app/subscriptions/manager.py`, `app/subscriptions/routing.py`, `app/server.py` wiring.

### 6.8 REST, SSE, WebUI and MCP
Different interfaces to shared product functionality; must remain consistent.
- **REST:** canonical read models and quote/catalog information (`api/routes.py`, `api/product_routes.py`, `api/fno_routes.py`, etc.).
- **SSE:** live updates without creating a separate interpretation of market state (`api/routes.py` market stream, `core/sse_broker.py`, market event broker wiring).
- **WebUI:** consumes backend market-data/intelligence APIs; must not recreate provider-specific market logic (`frontend/src/*`).
- **MCP:** exposes market-data/intelligence capabilities using the same canonical backend services (`mcp_server/tools/*`, `mcp_server/services.py`).

Critical acceptance criterion: the same instrument and market state should resolve consistently across these surfaces (requires evidence; do not assume).

---

## 7. Product and engineering priorities

1. **Correctness:** correct instrument identity, exchange/segment, F&O contract mapping, provider-token mapping, quote attribution, freshness/stale-state reporting; no duplicate or ambiguous canonical contracts.
2. **Reliability:** provider connection health, feed ingestion, quote delivery, stale detection, reconnect, subscription restoration, provider capacity, failover, graceful degradation.
3. **Consistency:** REST/SSE/WebUI/MCP share canonical behavior; no conflicting quote-selection logic; no consumer-specific identity interpretation; no silent fallback that misrepresents canonical data.
4. **Coverage:** supported NSE instruments/contracts, major indices, BSE SENSEX F&O, correct provider mappings, complete and discoverable catalog data.
5. **Performance:** important but not at the expense of correctness. Avoid unnecessary DB queries, repeated catalog scans, duplicate subscriptions, unnecessary serialization, and excessive in-process work. Preserve existing performance work/documentation (`docs/performance/` preserved).

**Engineering posture:** Keep architecture simple and robust. Prefer existing services/abstractions/adapters/identity/catalog/subscription mechanisms and small targeted fixes. Avoid unnecessary rewrites, abstraction layers, duplicate functionality, extra services without demonstrated need, unnecessary branches/scripts, unnecessary DB merge operations, or replacing behavior without understanding consumers.

---

## 8. Historical implementation context and decisions

> **Important:** Historical commits/changes below are recorded from project history and prior reports. Do not imply every historical change is present on the active branch without verifying Git history and current source. Agent-reported test results are not independently rerun unless stated.

### 8.1 Provider-neutral F&O universe and supported BSE scope
- **Commit:** `088a7a2672af441faf03e19a59d987e857530490`
- **Historical work (reported):** Provider-neutral F&O universe across providers; `BSE_FO` ingestion restricted to `SENSEX` only; out-of-scope BSE derivatives intentionally preserved in raw storage; supported BSE index F&O = `SENSEX`; `BANKEX`, `SENSEX50`, and other unsupported BSE derivatives excluded from supported discovery; NSE F&O underlyings/contracts preserved; catalog read models deduplicate canonical contracts; tests added.
- **Decision:** Raw provider data and supported product discovery are not the same thing.

### 8.2 Atomic Upstox catalog synchronization and BSE filtering
- **Commit:** `605dbfe7d86408bf5491f7920edd8c96bc59aa56`
- **Historical work (reported):** Atomic Upstox catalog synchronization using one DB transaction; scoped and segmented writes share a single connection/transaction; `_SUPPORTED_PRODUCT_SQL` filtering added to multiple discovery read models.
- **Subsequent review finding (historical):** Two read models still needed the supported-product predicate.

### 8.3 Follow-up BSE discovery filter correction
- **Commit:** `ec000259dcf616cacb6a4da5cdf7812e2c312e64`
- **Historical work (reported):** Added `_SUPPORTED_PRODUCT_SQL` to `option_underlyings()` and `option_expiries()`; added tests covering supported NSE NIFTY, BSE SENSEX and excluded `BANKEX`, `SENSEX50`, and BSE stock derivatives.
- **Agent-reported test result (not independently rerun by ChatGPT):** 1798 passed, 4 skipped.

### 8.4 Settings and watchlist frontend cleanup
- **Commit:** `a93653b0fd01daacac856d3a79e79b883c9a4592` (`fix: clean up settings section type and watchlist portability client`) — **current baseline HEAD on `review/scanner-phase1` in this worktree (verified).**
- **Work (verified by prior diff inspection/report context):** Removed stale `market-sources` from `SettingsSection` union; fixed watchlist export to use shared JSON request helper; fixed watchlist import to parse JSON and send JSON through shared request helper; kept unrelated settings behavior unchanged.
- **Additional observations (historical/reported):** Scanner filters transient; chart workspace/theme persistence unchanged; F&O and Market Data URL state unchanged; retention panel informational; Alerts Settings shortcut not expanded. Do not reopen without new evidence of a real product problem.

### 8.5 Other historical context (from `docs/PROJECT_CONTEXT.md`, verify before relying)
- `docs/PROJECT_CONTEXT.md` records 2026-11-10 consolidation state on `develop`/`main` (baselines, remediations, branch consolidation, EXE workflow prep, unresolved FYERS expiry semantics as protected-core item, Phase 4 deferred). That file is a living snapshot and may not reflect the current `review/scanner-phase1` worktree state; always re-inspect actual branch/HEAD/status.

---

## 9. Current P0 workstreams

Current major focus: two foundational workstreams (see `docs/plans/2026-11-10-p0-market-data-and-catalog-plan.md`).

### P0-A: Live market-data reliability
Objective: ensure MarketHub reliably receives, normalizes, selects, and distributes live market data.
Areas to understand and eventually verify:
1. Provider connectivity and health
2. Feed ingestion
3. Quote normalization
4. Subscription ownership
5. Provider routing
6. Quote delivery through `MarketService` and `QuoteService`
7. Freshness and stale detection
8. Reconnect behavior
9. Subscription restoration after reconnect
10. Provider capacity
11. Provider failover
12. REST/SSE/WebUI/MCP consistency

**Do not mark complete** because adapters/reconnect-related methods exist; requires implementation evidence and, where possible, runtime verification.

### P0-B: Canonical instrument catalog
Objective: ensure all supported instruments/contracts can be discovered and resolved accurately.
Areas to understand and verify:
1. Supported NSE/BSE universe
2. Canonical identity rules
3. Provider-native token mappings
4. Catalog ingestion and synchronization
5. Duplicate and ambiguous contract handling
6. Instrument browsing and search
7. NSE/BSE scope filtering
8. F&O underlying discovery
9. Expiry discovery
10. Option/futures contract discovery
11. Scanner, chart, REST, WebUI and MCP instrument resolution

Catalog completeness must be verified against actual provider master data or another reliable reference; not by a successful DB query or passing unit test alone.

---

## 10. Latest P0 implementation checkpoint (local, uncommitted)

> **Verified in this worktree (2026-11-10):** branch `review/scanner-phase1`, HEAD `a93653b0fd01daacac856d3a79e79b883c9a4592`, with local uncommitted modifications and untracked files as listed below. These changes are **not committed or pushed**.

### 10.1 Uncommitted implementation changes (inspected)

#### Change 1 — `api/fno_routes.py`
- **Diff (inspected):** `catalog.fno_universe(..., provider="upstox", ...)` → `catalog.fno_universe(..., provider=None, ...)` in F&O universe route.
- **Reason:** Use provider-neutral membership instead of tying supported NSE F&O discovery to Upstox-specific catalog rows.
- **Scope note (source-inspected context):** `fno_universe` read model is NSE F&O-segment focused (live `NSE_FO` contracts joined to `NSE_EQ` underlyings) and supports provider-neutral union/dedupe when `provider=None`; intended to preserve supported NSE F&O scope without expanding to unsupported BSE derivatives.
- **Limitation:** Improves discovery membership neutrality but does not guarantee runtime quote/subscription availability per provider; routing/failover/coverage depend on live state and mappings.

#### Change 2 — `core/persistence/modules/products.py`
- **Diff (inspected):** `browse_instruments(...)` base `WHERE` changed from `["instrument_type IS NOT NULL", "instrument_type != ''"]` to include `_SUPPORTED_PRODUCT_SQL`.
- **Predicate:** `_SUPPORTED_PRODUCT_SQL = "(segment <> 'BSE_FO' OR segment IS NULL OR UPPER(COALESCE(underlying, '')) = 'SENSEX')"` (defined in same module; verified by inspection).
- **Reason:** Ensure browse/discovery applies the same supported BSE derivatives scope as other catalog read models; exclude unsupported BSE derivatives while allowing valid non-`BSE_FO` rows and `NULL`-segment rows.
- **Limitation:** Fixes browse-surface scope leakage only; does not modify sync/import contents, schema, or other surfaces beyond existing filters. Runtime/UI verification not performed.

#### Change 3 — `api/routes.py`
- **Diff (inspected):** Live SSE market stream (`/api/market/stream`) now parses broker envelope data and attempts canonical re-selection using `canonical_id` → `instrument_token` → `tradingsymbol` via `QuoteService` (preferred) or `QuoteCoordinator`. Emits canonical payload when produced; retains raw broker envelope fallback when canonical selection cannot be produced.
- **Reason:** Improve consistency of live SSE updates with canonical snapshot/REST/MCP selection/freshness/provenance when resolvable.
- **Important limitation (known tradeoff):** Raw fallback preserves delivery but bypasses canonical selection, freshness verdicts, and provenance enrichment. Fallback updates may be inconsistent with canonical REST/MCP presentation (non-canonically selected/enriched), though payload originates from broker/market broadcast (not fabricated). Do not remove fallback without understanding delivery consequences and defining desired failure behavior.

### 10.2 Diff summary (verified)
- `api/routes.py`: 57 changed lines in diff hunk (34 insertions, 28 deletions across 3 files total)
- `api/fno_routes.py`: 2 changed lines
- `core/persistence/modules/products.py`: 3 changed lines
- **Total:** 3 files changed, 34 insertions(+), 28 deletions(-) (`git diff --stat`)

No concrete correctness bugs were identified in the three changed blocks during source/diff review in this session; the SSE raw-fallback consistency limitation remains documented.

### 10.3 Untracked documentation/state (verified)
- `docs/plans/2026-11-10-p0-market-data-and-catalog-plan.md` — P0 plan/checkpoint document (present locally, untracked).
- `docs/performance/` — preserved untracked directory (do not modify/move/delete without explicit authorization).

### 10.4 Verification limitations at this checkpoint
- Tests deferred; no dependencies installed; no services restarted.
- No database writes or migrations performed.
- No live broker login/feed test, no live reconnect/failover exercise.
- Catalog completeness not verified against live provider master data.
- No commit, push, merge, rebase, or branch operation performed.
- Agent-reported/historical test results elsewhere are not independently rerun unless explicitly stated.

---

## 11. Currently known gaps

### Live market data
- Actual provider reconnect behavior needs runtime verification.
- Subscription restoration after reconnect needs verification.
- Provider failover needs verification.
- Provider capacity handling needs verification.
- Live quote delivery needs runtime verification.
- SSE canonical fallback behavior needs a deliberate consistency decision.
- Stale quote behavior needs end-to-end verification.

### Canonical catalog
- Actual catalog completeness not verified against provider master data.
- Provider-native mapping completeness needs verification.
- Duplicate and ambiguous contract handling needs verification.
- Search and browse coverage need practical validation.
- Supported BSE filtering must remain consistent across every discovery/read model.

### Cross-surface consistency
Need evidence that the same canonical instrument and quote resolve consistently through REST, SSE, WebUI, and MCP. Do not assume this because components share some services.

---

## 12. Protected areas and design constraints

### 12.1 Protected/frozen areas (context from prior P0 tasks and architecture boundaries)
The following were explicitly protected in recent narrowly scoped P0 work and/or by architecture/auth freeze rules (see `AGENTS.md`, `docs/ARCHITECTURE_BOUNDARIES.md` §21):
- `app/auth/*`
- `app/secrets_store.py` / secrets store and encrypted credential storage
- Broker login and OAuth, credentials, token/session persistence
- Session restoration and startup restore behavior
- `app/server.py` composition/startup wiring (red-zone/protected composition concerns)
- Source/feed lifecycle ownership
- Persistence schema and migrations
- Canonical market model definitions/semantics (including `market/models.py`, `market/service.py` semantics)
- Unrelated settings, watchlists, alerts, news, chat

**Interpretation:** These restrictions applied to narrowly scoped tasks and reflect protected-core boundaries; they are not a blanket permanent prohibition on all future work. However, changing these areas requires a separately scoped and explicitly authorized task. Never modify them merely because a broader reliability improvement might eventually require it.

### 12.2 Design constraints
- Reuse existing canonical services/stores (`MarketService`, `QuoteService`, catalog/identity/subscription services). No parallel quote store, no parallel subscription system.
- No unnecessary abstractions/frameworks or large rewrites.
- Preserve read-only market-data behavior (no trading/order placement).
- Do not weaken validation, auth, credential handling, or session persistence.
- No DB schema changes unless a concrete correctness issue cannot be solved otherwise (explain before proceeding). No new dependencies without justification/approval.
- Preserve all existing user changes and untracked files (including `docs/performance/`).

---

## 13. Recommended next steps

> Do not start implementation as part of documentation/review tasks unless explicitly authorized. Order may change if source inspection reveals a more urgent blocker (evidence-based).

1. **P0-A focused reliability investigation (first):** Understand existing implementation of provider connection lifecycle, feed health, reconnect, subscription restoration, provider capacity, failover, quote freshness, and SSE delivery. Determine what is implemented, incomplete, and verifiable. Do not repeat broad audits of unrelated application areas.
2. **P0-B catalog completeness validation (second):** Verify supported NSE universe, supported BSE SENSEX F&O universe, provider mappings, canonical identities, duplicate contracts, F&O underlyings/expiries, and search/browse behavior using actual catalog/provider data where available.
3. **Cross-surface acceptance (third):** Verify consistent canonical results through REST, SSE, WebUI, and MCP with evidence.

Before any implementation: inspect current repo/Git state, read this protocol and the P0 plan, independently verify latest implementation state (including uncommitted changes), and do not assume prior local uncommitted changes still exist.

---

## 14. Key references and file paths

### Governance / architecture / plans
- `PROJECT_MANAGER_PROTOCOL.md` (this file, root)
- `AGENTS.md`
- `docs/ARCHITECTURE_BOUNDARIES.md`
- `docs/PROJECT_CONTEXT.md` (living handoff; verify against actual state)
- `docs/plans/2026-11-10-p0-market-data-and-catalog-plan.md` (current P0 plan/checkpoint, local/untracked in this snapshot)
- `docs/performance/` (preserved)

### Core market-data / catalog / identity (key areas)
- `market/service.py`, `market/quote_coordinator.py`, `market/serialization.py`, `market/models.py`
- `app/quote_service.py`, `app/market_data.py`, `app/market_identity.py`, `app/canonical_identity.py`, `app/instrument_identity.py`, `app/instruments.py`, `app/catalog_sync.py`
- `app/subscriptions/service.py`, `app/subscriptions/manager.py`, `app/subscriptions/routing.py`
- `core/persistence/modules/products.py`, `core/persistence/store.py`
- `api/routes.py`, `api/product_routes.py`, `api/fno_routes.py`
- `mcp_server/tools/market.py`, `mcp_server/tools/market_intel_tools.py`, `mcp_server/tools/options_analytics_tools.py`
- `brokers/upstox/*`, `brokers/fyers/*`, `sources/registry.py`, `core/runtime.py`, `app/server.py`

### Important commits (historical/current context)
- `088a7a2672af441faf03e19a59d987e857530490` — provider-neutral F&O universe / BSE SENSEX scope decisions (historical)
- `605dbfe7d86408bf5491f7920edd8c96bc59aa56` — atomic Upstox catalog sync / BSE filtering (historical)
- `ec000259dcf616cacb6a4da5cdf7812e2c312e64` — follow-up BSE discovery filter correction (historical)
- `a93653b0fd01daacac856d3a79e79b883c9a4592` — settings/watchlist frontend cleanup; **current HEAD in this worktree** (verified)

---

## 15. Current repository snapshot (verified 2026-11-10, this worktree)

- **Repository root:** `D:\Temp\MarketHub`
- **Remote:** `origin https://github.com/Rajendertyagi/MarketHub.git` (per prior session context; not re-fetched in this documentation task)
- **Current branch:** `review/scanner-phase1`
- **HEAD:** `a93653b0fd01daacac856d3a79e79b883c9a4592` (`fix: clean up settings section type and watchlist portability client`)
- **`git status --short` (verified):**
  ```text
   M api/fno_routes.py
   M api/routes.py
   M core/persistence/modules/products.py
  ?? docs/performance/
  ?? docs/plans/2026-11-10-p0-market-data-and-catalog-plan.md
  ```
- **Protocol location (actual):** `PROJECT_MANAGER_PROTOCOL.md` (repository root). A prior GitHub default-branch fetch of `docs/PROJECT_MANAGER_PROTOCOL.md` returned 404; the file exists locally at root in this worktree.
- **P0 plan location:** `docs/plans/2026-11-10-p0-market-data-and-catalog-plan.md` (present locally, untracked in this snapshot).
- **`docs/performance/`:** present/preserved (untracked).

> **Caution:** `docs/PROJECT_CONTEXT.md` contains a 2026-11-10 snapshot focused on `develop`/`main` consolidation state; it does not reflect this `review/scanner-phase1` worktree checkpoint. Always trust direct inspection of the current worktree.

---

## 16. Dated change log

- **2026-11-10 (this session, documentation-only):** Expanded and consolidated `PROJECT_MANAGER_PROTOCOL.md` (root) from a short generic protocol into a MarketHub-specific operating manual while preserving core workflow/permission/evidence principles. Added product definition and supported scope, architecture overview, priorities, historical decisions/commits, P0-A/P0-B workstreams, latest local uncommitted P0 checkpoint (verified diffs/status), known gaps, protected areas/constraints, recommended next steps, key references, and current worktree snapshot. Verified facts from direct inspection: git branch/HEAD/status, protocol/P0 plan/PROJECT_CONTEXT locations and contents, and current diffs in `api/routes.py`, `api/fno_routes.py`, `core/persistence/modules/products.py`. Did not modify implementation code/tests/schemas/runtime state; did not commit/push/merge/rebase; preserved `docs/performance/` and all pre-existing uncommitted work.
- **2026-11-10 (prior, recorded in earlier protocol):** Initial generic Project Manager Protocol created at root (requirements/audit/research/planning/review/verification workflow, permission/safety, evidence standards). Superseded in structure by this consolidated version but core principles retained.
- **Historical (see `docs/PROJECT_CONTEXT.md` and Git history):** Earlier project-context snapshots and remediation/consolidation records exist; consult and re-verify against current code/state before relying on them.
