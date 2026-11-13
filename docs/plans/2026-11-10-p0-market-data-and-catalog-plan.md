# P0 Plan: Live Market-Data Reliability and Canonical Instrument Catalog

**Date:** 2026-11-10  
**Repository:** `Rajendertyagi/MarketHub`  
**Branch:** `review/scanner-phase1`  
**Baseline HEAD:** `a93653b0fd01daacac856d3a79e79b883c9a4592`  
**Status:** Documentation and source-reviewed implementation record (tests/runtime verification deferred)

## A. Product objective

Live market-data reliability and canonical instrument identity are foundational to MarketHub because every user-facing and machine-facing market capability depends on them:

- **Correctness of decisions and displays:** Quotes, charts, scanners, F&O views, analytics, alerts, and MCP tools must refer to the same real instrument and the same current/stale state. If identity is ambiguous or quotes are stale/misattributed, downstream outputs become misleading even when UI or analytics logic is correct.
- **Single source of truth across surfaces:** WebUI, REST, SSE, and MCP must consume the same canonical market-data/catalog services and state so users and agents do not see conflicting values for the same instrument.
- **Provider independence:** MarketHub supports multiple providers and provider-specific tokens/keys. A stable canonical identity and correct provider-to-canonical mapping allow subscriptions, routing, failover, and reads to work without leaking provider-specific assumptions into consumers.
- **Operational trust:** Reliable connection status, freshness/staleness signaling, reconnect/recovery, and subscription ownership/cleanup are required so the system never silently presents unavailable or stale data as live.
- **Safe feature growth:** Scanners, option chains, breadth/heatmaps, and future analytics can only be dependable if the catalog defines supported contracts exactly and the live quote pipeline delivers consistent, attributable data.

This plan covers two connected P0 workstreams: **P0-A Live market-data reliability** and **P0-B Canonical instrument catalog**.

## B. Workstream P0-A: Live market-data reliability

### Scope and intent
Ensure provider connectivity, quote ingestion/delivery, timestamps/freshness, reconnect/recovery, routing/failover, subscription ownership/cleanup, and consistent quote state across REST/SSE/WebUI/MCP using existing canonical services (`MarketService`, `QuoteService`, subscription/catalog/identity services). No parallel quote store or subscription system; no auth/login/OAuth/credential/session-restoration changes.

### 1. Provider connection and availability

- **Current implementation and relevant source files (inspected):**
  - `app/server.py`: composition/wiring of source manager, feeds, runtime auth/status providers, source control routes, and lifecycle hooks.
  - `sources/registry.py`, `core/runtime.py`: source registration/lifecycle management and runtime task ownership.
  - `brokers/upstox/feed.py`, `brokers/fyers/feed.py`: feed state machines, readiness/auth-required states, connection attempts, reconnect counters, last error/exit reasons, liveness/data-flow indicators, and status reporting.
  - `api/routes.py` (`/api/sources/status`, source control routes): exposes source/runtime status.
  - `app/subscriptions/routing.py`, `app/subscriptions/manager.py`: provider eligibility/capacity/routing signals based on runtime status and catalog offers.
- **What was verified by source inspection:**
  - Feeds track explicit lifecycle states (including auth-required/reconnecting/streaming/stopped/failed patterns), connection/reconnect counters, last errors, and liveness/data-flow evidence rather than relying solely on socket openness.
  - Source lifecycle is managed through the existing source manager/runtime wiring; provider availability is surfaced through status/readiness signals used by routing/subscription logic.
- **What was changed in this implementation:**
  - No direct changes to provider connection/auth/lifecycle internals in this pass.
- **What remains unverified:**
  - Live provider connect/disconnect behavior, auth-required transitions under real sessions, and runtime status accuracy during actual broker outages/restarts.
- **What future work is required:**
  - Runtime validation of provider availability/status reporting under real Upstox/Fyers sessions, network failures, and restart scenarios; fix only concrete defects found, without altering protected auth/session restoration behavior.

### 2. Feed ingestion and quote normalization

- **Current implementation and relevant source files (inspected):**
  - `brokers/upstox/feed.py`, `brokers/upstox/feed_processing.py`, `brokers/upstox/feed_protocol.py`: frame receive/decode/normalization, per-instrument metadata, patch/depth application, malformed-frame isolation, counters.
  - `brokers/fyers/feed.py`, `brokers/fyers/tbt/feed.py` (inventory/inspection context): Fyers HSM/TBT ingestion paths and normalization/application into shared market state.
  - `market/normalize/*` (referenced by feeds/processing): provider-to-canonical normalization.
  - `market/service.py`: canonical quote/depth merge/apply, ordering/staleness handling, immutable snapshots, post-merge callback.
- **What was verified by source inspection:**
  - Feeds normalize provider data before applying to `MarketService`; binary/frame processing isolates malformed/unknown instrument cases and applies quote/depth patches through the shared store.
  - `MarketService` is the single in-process canonical quote/depth state owner (merge semantics, stale/out-of-order handling, snapshot reads, callback after commit).
- **What was changed in this implementation:**
  - No changes to feed decoding/normalization logic or `MarketService` merge semantics.
- **What remains unverified:**
  - Correctness of normalization and ingestion under live provider payloads, burst load, malformed frames, and multi-provider concurrent updates.
- **What future work is required:**
  - Live/fixture-backed verification of ingestion correctness and per-instrument update behavior; targeted fixes only for evidenced normalization/ingestion defects.

### 3. Subscription ownership and provider routing

- **Current implementation and relevant source files (inspected):**
  - `app/subscriptions/service.py`: DB-backed preferences, resolution to concrete provider keys, desired-set ownership (active/analytics/derivatives), reconciliation/apply orchestration.
  - `app/subscriptions/manager.py`: lease-based ownership, desired/allocation/runtime state, priority, reconciliation, failover handling, explicit unrouted/capacity-limited states.
  - `app/subscriptions/routing.py`: deterministic provider offer selection (catalog/capability/capacity aware), single-provider routing with failover targets.
  - `app/server.py`: wiring of subscription service/manager, feed providers, F&O coverage/analytics ownership integration.
  - `api/routes.py`, `api/fno_routes.py`, `api/fno_coverage_routes.py`: analytics/F&O view application and reconciliation entry points.
- **What was verified by source inspection:**
  - Subscription architecture is lease/demand-driven with explicit ownership and reconciliation through existing services (no parallel subscription system observed).
  - Routing is deterministic and catalog/capability/capacity-aware; manager tracks runtime states and failover/reallocation concepts.
- **What was changed in this implementation:**
  - No direct changes to subscription ownership, routing, reconciliation, or failover logic.
- **What remains unverified:**
  - Correct runtime ownership/cleanup across view switches, reconnects, provider unavailability, and capacity pressure; absence of duplicate/conflicting subscriptions in live operation.
- **What future work is required:**
  - Runtime inspection/tests for lease cleanup, reconciliation correctness, and failover ownership under real provider conditions.

### 4. Quote delivery through MarketService and QuoteService

- **Current implementation and relevant source files (inspected):**
  - `market/service.py`: stores canonical merged quotes/depths and invokes post-merge quote callback.
  - `app/server.py`: `_on_market_quote_update` broadcasts canonical quote envelopes to market SSE broker and feeds alert evaluation.
  - `app/quote_service.py`: shared canonical read service (identity resolution, provider provenance from catalog/registry, freshness verdicts, stale-last-known behavior, storage-key lookup).
  - `market/quote_coordinator.py`: freshness-gated canonical selection over existing store.
  - `api/routes.py`: market REST snapshot/quote/depth endpoints and SSE stream use `QuoteService`/coordinator when available.
  - `mcp_server/tools/market.py`: MCP quote/depth/status/history tools prefer `services.quote_service`.
- **What was verified by source inspection:**
  - A shared canonical read path (`QuoteService`) exists and is used by REST and MCP; SSE snapshot uses canonical selection. Live updates are broadcast from `MarketService` callback and consumed by SSE subscribers.
  - `QuoteService` keeps last known stale values marked stale with provenance/as-of rather than dropping them or presenting them as live.
- **What was changed in this implementation:**
  - `api/routes.py` (market SSE live stream): improved canonical re-selection for live updates by attempting resolution using `canonical_id`, then `instrument_token`, then `tradingsymbol` (when present) via `QuoteService`/`QuoteCoordinator` before emitting. Canonical payloads are emitted when resolvable; raw broker envelope remains a last-resort fallback only when canonical selection cannot be produced.
- **What remains unverified:**
  - End-to-end live delivery consistency under reconnects, multi-provider updates, and unresolved instruments; behavior of last-resort raw SSE fallback in production traffic.
- **What future work is required:**
  - Runtime validation that REST/SSE/MCP consumers observe consistent selected values/freshness/provenance for the same instrument; evaluate whether unresolved-instrument fallback should be further canonicalized without dropping delivery.

### 5. Stale quote detection and freshness

- **Current implementation and relevant source files (inspected):**
  - `app/quote_service.py`: per-kind freshness windows/policy, freshness verdicts, stale-last-known payloads (`stale`, `as_of`, provenance), liveness derived from data recency.
  - `market/quote_coordinator.py`: freshness-gated selection (stale-only not selected as live).
  - `brokers/upstox/feed.py`, `brokers/fyers/feed.py`: feed-level liveness/data-flow/stale indicators based on frame/data recency.
  - `api/routes.py`, `mcp_server/tools/market.py`: consumers prefer canonical quote service payloads/verdicts.
- **What was verified by source inspection:**
  - Freshness is modeled as data-recency policy (not socket-connected state) in canonical read services; stale data can be retained but marked stale.
  - Feeds expose awaiting-data/stale/liveness signals distinct from connection state.
- **What was changed in this implementation:**
  - No direct freshness-policy changes; SSE live path now prefers canonical payloads (including freshness/provenance verdicts) when resolvable.
- **What remains unverified:**
  - Real-world stale detection timing, stale-to-live recovery, and absence of stale-as-fresh presentation across all UI/MCP surfaces during outages.
- **What future work is required:**
  - Runtime tests/observation for stale marking, recovery after reconnect, and consumer rendering/handling of stale payloads.

### 6. Reconnect and subscription restoration

- **Current implementation and relevant source files (inspected):**
  - `brokers/upstox/feed.py`: reconnect loop/backoff, session re-authorize/connect, resubscribe desired instrument set on (re)connect, socket retirement/mutation safety.
  - `brokers/fyers/feed.py`: reconnect/session handling and desired-set resubscription behavior (inspected in part).
  - `app/subscriptions/manager.py`, `app/subscriptions/service.py`: reconciliation/apply of desired leases/keys to provider feeds.
  - `app/server.py`: startup restoration/reconciliation hooks for subscriptions/F&O coverage (composition-level, inspected in part).
- **What was verified by source inspection:**
  - Feeds maintain desired subscription sets and resubscribe on reconnect paths; subscription services/managers reconcile desired state to live feeds.
- **What was changed in this implementation:**
  - No reconnect/subscription-restoration logic changes.
- **What remains unverified:**
  - Actual restoration completeness after disconnect/restart, timing/races, and whether all intended subscriptions resume without duplicates or stale ownership.
- **What future work is required:**
  - Live reconnect drills and reconciliation verification per provider; targeted fixes only for evidenced restoration/cleanup defects.

### 7. Provider capacity and failover

- **Current implementation and relevant source files (inspected):**
  - `app/subscriptions/routing.py`: capacity-aware deterministic routing and failover target selection.
  - `app/subscriptions/manager.py`: allocation against provider ceilings, priority handling, explicit capacity-limited/unrouted states, failover/re-routing and consumer notification mechanisms.
  - `app/subscriptions/service.py`: demand/lease construction and reconciliation integration.
- **What was verified by source inspection:**
  - Capacity and failover concepts are implemented in the existing subscription manager/routing layer (no parallel system introduced).
- **What was changed in this implementation:**
  - No capacity/failover logic changes.
- **What remains unverified:**
  - Correct failover behavior under real provider degradation/capacity exhaustion, and absence of duplicate/conflicting quote ownership after failover.
- **What future work is required:**
  - Runtime/fault-injection verification of routing, failover, and ownership transitions; fix evidenced issues within existing boundaries.

### 8. REST, SSE, WebUI and MCP consistency

- **Current implementation and relevant source files (inspected):**
  - `api/routes.py`: REST market snapshot/quote/depth/indices and SSE stream.
  - `app/quote_service.py`, `market/quote_coordinator.py`: shared canonical selection/freshness/provenance.
  - `mcp_server/tools/market.py`: MCP market tools use shared quote service when available.
  - `app/server.py`: shared market service/broker wiring; WebUI consumes backend APIs/SSE (no separate WebUI market-data state introduced).
- **What was verified by source inspection:**
  - REST and MCP prefer the same `QuoteService` read path; SSE snapshot is canonical-selected. Live SSE now attempts canonical re-selection more robustly (by canonical id/token/symbol) before emission.
- **What was changed in this implementation:**
  - `api/routes.py`: live SSE canonical re-selection fallback improved to reduce raw-envelope bypass when canonical services can resolve the update.
- **What remains unverified:**
  - Full runtime parity across REST/SSE/WebUI/MCP for all instruments/providers, especially unresolved instruments and reconnect windows.
- **What future work is required:**
  - End-to-end parity checks (same instrument, same timestamp/provenance/freshness semantics) across all surfaces; address any remaining raw-fallback inconsistencies with minimal safe changes.

## C. Workstream P0-B: Canonical instrument catalog

### Scope and intent
Provide complete, exact, and stable canonical instrument identity and provider mappings for the supported NSE/BSE universe; ensure consistent resolution and discovery across catalog, scanners, charts, F&O, REST, and MCP. Exclude unsupported BSE derivatives from supported discovery.

### 1. Supported NSE and BSE instrument universe

- **Current implementation and relevant source files (inspected):**
  - `app/instruments.py`, `app/catalog_sync.py`: catalog sync/filter/validation/write orchestration and supported segment handling.
  - `core/persistence/modules/products.py`: supported-product SQL scope, search/browse/read models, F&O universe queries.
  - `api/fno_routes.py`, `api/product_routes.py`: F&O/catalog discovery endpoints.
  - `market/derivatives_universe.py`, `app/market_indices.py`, `market/index_registry.py`: universe/index definitions and consumers.
- **What was verified by source inspection:**
  - Supported scope is encoded in catalog/read-model filters and sync policies (NSE segments and BSE SENSEX F&O scope handling present in persistence/catalog logic).
  - F&O universe read models support provider-neutral membership (`provider=None`) and NSE_FO-focused derivation.
- **What was changed in this implementation:**
  - `api/fno_routes.py`: F&O universe endpoint now requests provider-neutral universe (`provider=None`) instead of hardcoded `provider="upstox"`.
  - `core/persistence/modules/products.py`: `browse_instruments` now applies `_SUPPORTED_PRODUCT_SQL` to exclude out-of-scope BSE derivatives from browse/discovery.
- **What remains unverified:**
  - Actual catalog completeness against live provider masters for all supported NSE and BSE SENSEX F&O contracts; runtime discovery parity across all surfaces.
- **What future work is required:**
  - Catalog sync/audit against official provider masters for supported scope; verify no supported contracts missing and no unsupported BSE derivatives exposed.

### 2. Canonical identity rules

- **Current implementation and relevant source files (inspected):**
  - `app/canonical_identity.py`: canonical ID derivation, provider identifier roles (feed/rest/storage), alias/reference helpers.
  - `app/instrument_identity.py`: alias-to-storage-key registry and catalog-row registration.
  - `app/market_identity.py`: condition-alert/canonical resolver view over canonical derivation.
  - `market/models.py`, `market/service.py`: canonical quote/depth identity fields and storage-key behavior (inspected; not modified).
- **What was verified by source inspection:**
  - Canonical identity derivation is centralized and provider-neutral; provider-native keys/tokens are treated as identifiers/mappings, not canonical identity.
  - Registries map aliases/canonical IDs to storage keys for consistent quote-state lookup.
- **What was changed in this implementation:**
  - No changes to canonical identity derivation rules or registries.
- **What remains unverified:**
  - Absence of ambiguous/duplicate canonical identities across all synced provider rows in production-like catalogs.
- **What future work is required:**
  - Data-level validation of canonical ID uniqueness/collision handling on full synced catalogs; targeted remediation if collisions/ambiguities are evidenced.

### 3. Provider-native token mappings

- **Current implementation and relevant source files (inspected):**
  - `app/canonical_identity.py`: provider identifier extraction/roles and quote-key selection.
  - `app/instrument_identity.py`, `app/quote_service.py`: catalog-backed mapping/lookup and storage-key resolution across providers.
  - `app/catalog_sync.py`, `app/instruments.py`, `core/persistence/modules/products.py`: persistence of provider tokens/symbols and read access.
- **What was verified by source inspection:**
  - Mapping logic distinguishes provider token/symbol/feed/rest keys and resolves references through catalog/registry-backed lookups rather than string-shape-only assumptions in primary paths.
- **What was changed in this implementation:**
  - No direct mapping logic changes.
- **What remains unverified:**
  - Correctness of all provider-token-to-canonical mappings for supported NSE/BSE SENSEX F&O contracts in live/synced data.
- **What future work is required:**
  - Mapping audits on synced catalogs and runtime spot checks (quote/chain/subscription resolution) for representative contracts.

### 4. Catalog ingestion and synchronization

- **Current implementation and relevant source files (inspected):**
  - `app/catalog_sync.py`: master validation, segment-scoped/atomic replacement behavior, per-master failure handling, BSE SENSEX scope/write-scope policies.
  - `app/instruments.py`: provider master fetch/parse/filter/sync orchestration and catalog service APIs.
  - `core/persistence/modules/products.py`: transactional replace helpers (segmented/scoped/atomic variants), canonicalization/migration helpers, indexes.
- **What was verified by source inspection:**
  - Sync paths include validation and scoped/transactional writes to avoid destructive partial updates; BSE SENSEX scope policies exist in sync/filter logic.
- **What was changed in this implementation:**
  - No sync/ingestion logic changes.
- **What remains unverified:**
  - Successful end-to-end sync completeness and freshness against current provider masters in this environment.
- **What future work is required:**
  - Run/verify catalog syncs (where permitted) and confirm segment/master completeness, especially NSE F&O and BSE SENSEX F&O.

### 5. Duplicate and ambiguous contract handling

- **Current implementation and relevant source files (inspected):**
  - `app/canonical_identity.py`, `app/instrument_identity.py`, `app/market_identity.py`: canonical derivation, alias registration, collision/rejection behavior and grouping strategies.
  - `core/persistence/modules/products.py`: deduped browse/pool read models and canonical-key SQL mirroring for discovery.
  - `app/catalog_sync.py`: validation checks (including duplicate token/symbol constraints in scoped policies where applicable).
- **What was verified by source inspection:**
  - Identity registries reject ambiguous alias re-pointing and aggregate collisions; discovery read models include canonical dedupe logic.
- **What was changed in this implementation:**
  - No duplicate/ambiguity resolution logic changes.
- **What remains unverified:**
  - Real-data duplicate/ambiguity rates and whether any supported contracts remain ambiguous or duplicated in user-facing discovery.
- **What future work is required:**
  - Catalog-level duplicate/ambiguity audit and targeted fixes if concrete collisions affect supported contracts.

### 6. Catalog browsing and search

- **Current implementation and relevant source files (inspected):**
  - `core/persistence/modules/products.py`: `search_instruments`, `browse_instruments`, `instrument_pool`, exact symbol lookups, derivative/option read models.
  - `api/product_routes.py` (inventory/inspection context): instrument/catalog endpoints consuming persistence/catalog services.
  - `app/instruments.py`: catalog service interface over store/read models.
- **What was verified by source inspection:**
  - Search/read models already applied supported-product scope in multiple paths; browse previously lacked this scope filter.
  - Browse/pool use canonical dedupe and keyset pagination patterns.
- **What was changed in this implementation:**
  - `core/persistence/modules/products.py::browse_instruments`: added `_SUPPORTED_PRODUCT_SQL` to base filters to exclude unsupported BSE derivatives from browse/discovery results.
- **What remains unverified:**
  - End-to-end browse/search behavior in WebUI/MCP for all supported instrument classes and edge cases (expired derivatives, option scoping, provider preference).
- **What future work is required:**
  - UI/API/MCP discovery verification against synced catalog; ensure no unsupported BSE derivatives appear and supported NSE/BSE SENSEX contracts are discoverable.

### 7. F&O universe and option contract discovery

- **Current implementation and relevant source files (inspected):**
  - `core/persistence/modules/products.py`: `fno_underlying_rows`, `fno_universe`, derivative/option read models.
  - `api/fno_routes.py`: F&O universe and workspace routes.
  - `app/subscriptions/service.py`, `app/fno_coverage.py` (inspected in part): F&O workspace/coverage resolution and subscription application.
  - `market/derivatives_universe.py`: derived universe helpers.
- **What was verified by source inspection:**
  - Persistence F&O universe read models support provider-neutral membership and canonical contract dedupe concepts; route previously forced Upstox-only universe.
  - Supported BSE scope is constrained by shared product-scope filters in relevant read models.
- **What was changed in this implementation:**
  - `api/fno_routes.py`: F&O universe endpoint now uses provider-neutral catalog membership (`provider=None`).
- **What remains unverified:**
  - Runtime correctness of F&O workspace contract resolution, option-chain enrichment, and subscription application across providers for supported NSE/BSE SENSEX contracts.
- **What future work is required:**
  - Runtime validation of F&O discovery/workspace flows and provider-neutral contract availability; fix evidenced resolution/routing issues minimally.

### 8. Scanner, chart, F&O, REST and MCP instrument resolution

- **Current implementation and relevant source files (inspected):**
  - `api/routes.py`: market/scanner/breadth/sector/map endpoints use shared readers/catalog/identity-aware resolution paths.
  - `api/fno_routes.py`: F&O workspace/chain enrichment uses shared services/catalog/quote paths.
  - `mcp_server/tools/market.py`, `mcp_server/tools/market_intel_tools.py`, `mcp_server/tools/options_analytics_tools.py`: MCP adapters over shared services/catalog/intel.
  - `app/quote_service.py`, `app/instrument_identity.py`, `app/canonical_identity.py`, `app/market_intel.py`: shared identity/mapping/quote resolution.
- **What was verified by source inspection:**
  - Major consumers are wired to shared canonical services/identity/catalog abstractions rather than separate market-data stores.
  - F&O universe endpoint now aligns better with provider-neutral catalog membership.
- **What was changed in this implementation:**
  - Provider-neutral F&O universe lookup and browse scope filtering improve consistency of discovery inputs; SSE live canonical re-selection improved for stream consumers.
- **What remains unverified:**
  - Cross-surface resolution parity for all supported instruments in live conditions (especially scanners/charts/F&O chains under provider failover or partial catalog state).
- **What future work is required:**
  - Targeted parity checks across scanner/chart/F&O/REST/MCP for representative NSE and BSE SENSEX contracts; resolve any mismatched identity/mapping behavior with minimal changes.

## D. Completed implementation

### 1. `api/fno_routes.py`: provider-neutral F&O universe lookup
- **Change:** `GET /api/market/fno/universe` now calls `catalog.fno_universe(..., provider=None, ...)` instead of `provider="upstox"`.
- **Reasoning:** The supported F&O universe should reflect provider-neutral canonical/catalog membership for supported contracts, not a single hardcoded provider. The persistence read model already supports provider-neutral union/dedupe semantics when `provider` is `None`.
- **Limitations:** This improves discovery membership neutrality but does not by itself guarantee runtime quote/subscription availability for every listed contract on every provider; runtime routing/failover and provider coverage still depend on live state and catalog mappings.

### 2. `core/persistence/modules/products.py`: supported-product filtering in instrument browsing
- **Change:** `browse_instruments(...)` base `WHERE` filters now include `_SUPPORTED_PRODUCT_SQL` (in addition to existing non-null/non-empty instrument type filters).
- **Reasoning:** `_SUPPORTED_PRODUCT_SQL` enforces the supported discovery scope for BSE derivatives (`BSE_FO` allowed only for `SENSEX`), preventing unsupported BSE derivatives from leaking into browse/discovery results while preserving non-`BSE_FO` instruments (including valid NSE instruments).
- **Limitations:** This fixes browse-surface scope leakage only; it does not modify catalog sync/import contents, schema, or other discovery surfaces beyond existing filters already present elsewhere. Runtime/UI verification was not performed.

### 3. `api/routes.py`: canonical re-selection of live SSE quotes using available identifiers, retaining the raw fallback
- **Change:** In `/api/market/stream` live update handling, the server now parses the broker envelope data and attempts canonical re-selection using `canonical_id` or fallback identifiers (`instrument_token`, then `tradingsymbol`) via `QuoteService` (preferred) or `QuoteCoordinator`. If a canonical payload is produced, it is emitted in the same canonical envelope shape as snapshot/REST reads. If canonical selection cannot be produced, the raw broker envelope is still emitted as a last-resort fallback.
- **Reasoning:** Snapshot reads were canonical-selected, but live updates previously depended primarily on `canonical_id` presence and could bypass canonical selection/freshness/provenance. Using available identifiers improves consistency with REST/SSE snapshot/MCP canonical reads while preserving delivery for unresolved cases.
- **Limitations:** Last-resort raw fallback remains when canonical services cannot resolve/select the instrument; raw fallback may not carry the same canonical freshness/provenance presentation as `QuoteService` payloads. No runtime stream verification was performed.

## E. Remaining work and priorities

### Implemented and source-reviewed
- Provider-neutral F&O universe lookup in `api/fno_routes.py`.
- Supported-product scope filtering added to `browse_instruments` in `core/persistence/modules/products.py`.
- Improved canonical re-selection for live SSE quote updates in `api/routes.py` (with raw fallback retained).
- Source inspection of key P0-A/P0-B paths (feeds/market service/quote service/subscriptions/routing/catalog/identity/REST/SSE/MCP wiring) to identify concrete, low-risk fixes.

### Existing functionality not modified
- Feed protocol/normalization internals, reconnect/backoff algorithms, and provider adapter implementations.
- Subscription manager/routing/failover algorithms and lease reconciliation logic.
- `MarketService` merge/staleness semantics and canonical model definitions.
- Catalog sync/validation/write logic and identity derivation/registry implementations.
- Auth/login/OAuth/credential storage/session restoration/startup behavior (protected/frozen).
- Unrelated settings/watchlists/alerts/news/chat functionality.

### Not yet verified at runtime
- Live provider connectivity/status accuracy (Upstox/Fyers), feed ingestion under real traffic, and quote delivery continuity.
- Stale detection/recovery behavior and absence of stale-as-fresh presentation across WebUI/REST/SSE/MCP in live sessions.
- Reconnect and subscription restoration completeness, cleanup correctness, and absence of duplicate/conflicting ownership.
- Provider capacity handling and failover correctness under real degradation/capacity conditions.
- Catalog completeness/mapping correctness against current provider masters for supported NSE and BSE SENSEX F&O contracts.
- End-to-end cross-surface parity (catalog/scanners/charts/F&O/REST/MCP) for representative supported instruments.

### Future implementation candidates (prioritized, evidence-driven)
1. **Runtime parity and freshness validation harness/checklist execution** for REST/SSE/MCP/WebUI on representative NSE equity, NSE index, NSE F&O, and BSE SENSEX F&O instruments (including reconnect windows).
2. **SSE fallback hardening (if evidenced necessary):** reduce/standardize raw-envelope fallback behavior for unresolved instruments without dropping delivery or introducing a parallel state path.
3. **Catalog completeness/mapping audit:** verify supported NSE/BSE SENSEX F&O coverage, provider-token mappings, and duplicate/ambiguous contract handling using synced master data.
4. **Subscription/failover runtime fixes (only if concrete defects found):** address evidenced ownership/cleanup/reconciliation/failover issues within existing subscription architecture and protected boundaries.
5. **Discovery/read-model consistency sweep:** confirm all supported discovery surfaces (browse/search/F&O/scanners/MCP) enforce the same supported-universe and identity rules.

## F. Verification and acceptance criteria

Tests are deferred for now. The following criteria should be verified in future implementation/validation passes (runtime and/or automated tests where appropriate):

### P0-A: Live market-data reliability
- **Provider availability/status:** Source status accurately reflects connected, reconnecting, auth-required, stopped, and failed states; no false "live" indication when data is not flowing.
- **Quote ingestion/delivery:** Canonical quotes/depth updates are ingested, merged, and delivered without silent drops; consumers receive updates for subscribed instruments.
- **Freshness/staleness:** Fresh quotes are marked/live only within policy windows; stale-only data is retained but explicitly marked stale with `as_of`/provenance and never presented as fresh.
- **Reconnect/recovery:** After disconnect/restart, feeds reconnect (where possible) and restore intended subscriptions; quote flow resumes without requiring manual reconfiguration.
- **Routing/failover/ownership:** Subscriptions are owned/cleaned up correctly; provider routing respects catalog/capability/capacity; failover does not create duplicate/conflicting ownership or silent gaps.
- **Cross-surface consistency:** REST snapshot/quote/depth, SSE snapshot/live updates, WebUI views, and MCP tools expose consistent selected values, identity, freshness, and provenance for the same instrument.

### P0-B: Canonical instrument catalog
- **Supported universe coverage:** Catalog/discovery includes supported NSE equities/F&O, NSE index F&O, NSE major indices, and BSE SENSEX F&O only; excludes `BANKEX`, `SENSEX50`, unsupported BSE stock derivatives, and `NIFTYFPI` from F&O scope.
- **Canonical identity stability:** Each supported instrument resolves to one stable canonical identity with correct exchange/type/underlying/expiry/strike/option metadata.
- **Provider mapping correctness:** Provider-native tokens/keys map to the correct canonical contracts; no cross-contract mis-mapping.
- **No duplicates/ambiguity in discovery:** Supported discovery surfaces do not show duplicate canonical contracts or ambiguous resolutions for the same real instrument.
- **Discovery and resolution parity:** Browse/search, F&O universe/option discovery, scanners, charts, REST, and MCP resolve the same canonical instruments consistently.
- **Sync integrity:** Catalog sync/refresh is complete and atomic/scoped per existing rules; failed/partial provider data does not corrupt previously valid supported catalog state.

## G. Change boundaries

- **Protected/frozen areas (must not be modified in ordinary P0 feature work):** `app/auth/*`, `app/secrets_store.py`, encrypted credential storage, broker login/OAuth flows, token/session persistence, session restoration/startup restore behavior, and fake-token guards. `app/server.py` composition/startup wiring and source/feed lifecycle ownership are protected/red-zone areas requiring separate approved tasks for structural changes.
- **Architecture constraints:** Reuse existing canonical services/stores (`MarketService`, `QuoteService`, catalog/identity/subscription services). No parallel quote store, no parallel subscription system, no unnecessary abstractions/frameworks, and no large rewrites.
- **Behavioral constraints:** Preserve read-only market-data behavior (no trading/order placement). Do not weaken validation, auth, credential handling, or session persistence. Do not modify unrelated settings/watchlists/alerts/news/chat functionality.
- **Data/schema constraints:** No DB schema changes unless a concrete correctness issue cannot be solved otherwise (must be explained before proceeding). No new dependencies.
- **Repository hygiene:** Preserve all existing user changes and untracked files (including `docs/performance/`). No branch creation/switching, commits, pushes, merges, rebases, resets, or destructive operations unless explicitly requested/approved.
- **Verification discipline:** Report only actually executed commands/results; do not claim runtime reliability, catalog completeness, or test success without evidence.
