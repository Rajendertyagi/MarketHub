# MarketHub — Fyers/Upstox Dual-Provider Market Data Audit (Full Report)

**Date:** 2026-10-18 (observations 2026-10-17)
**Branch:** `fno-provider-agnostic` · **HEAD:** `3f4b8d9`
**Mode:** Audit + planning only — **no code, config, DB, or file modifications were made.**

---

## 1. Objective

Determine how to make MarketHub reliably support the complete intended market-data scope (NSE indices, NSE stock F&O, BSE indices including SENSEX) across **Fyers-only, Upstox-only, and dual-provider** operation. Independently verify the prior audit's claims against the actual working tree.

---

## 2. Baseline & Repository State (Preserved)

| Item | State |
|---|---|
| Branch | `fno-provider-agnostic` |
| HEAD | `3f4b8d9` (also `origin/b8-production-hardening`, `b8-production-hardening`) |
| vs `origin/main` | 0 ahead / **161 behind** |
| Working tree | **Authoritative state** — ~45 modified files, ~40 staged new files, 3 untracked. `git HEAD` does NOT reflect current work. |
| Untracked | `brokers/fyers/rest.py`, `docs/plans/2026-10-17-fno-full-trading-plan.md`, `preview-tailwind-shadcn.html` |
| DB | `data_test/events.db` — schema **v23**, `instruments` table = **0 rows** |
| Subscriptions DB | 9 index + 2 stock rows in `md_subscriptions`; 0 derivative rules |

**Nothing was modified during this audit.** No `git reset/clean/checkout/restore/stash` run. No config, credential, or DB writes.

---

## 3. Executive Summary

### Verified blockers (evidence, not assumption)

1. **Empty instrument catalog (Critical, Verified).**
   `data_test/events.db` schema v23 has zero `instruments` rows. All catalog-driven F&O discovery (universe, futures index, option strikes, routing) returns nothing. `FnoCoverageService.plan()` reports `desired: 0` + a catalog read error. *No catalog = no F&O coverage at all.*

2. **`brokers/fyers/rest.py` untracked (Critical, Verified).**
   `git ls-files --error-unmatch` fails for this file, yet `app/server.py:695-698` imports it at module scope. A clean checkout crashes at startup with `ModuleNotFoundError`, taking down the whole Fyers REST path (history/option-chain/depth/market-status).

3. **Fyers HSM index ticks dropped unconditionally (High, Verified).**
   `brokers/fyers/feed.py:937-941` (`_emit_tick`): `if snap["topic"].startswith("if"): return`. In Fyers-only mode **no live index quote ever reaches `MarketService`** through streaming. `resolve_spot` can only fall back to a Fyers quote already in the store — which never exists because the ticks are dropped.

4. **`IndicesWidget` / quote-store key divergence (High, Verified).**
   `MarketService` keys quotes by `(exchange, instrument_token)`. Fyers writes `("NSE","NSE:NIFTY50-INDEX")` (colon); Upstox writes `("NSE","NSE_INDEX|Nifty 50")` (pipe). Same real instrument = **two storage slots**. `get_quote_now` (`market/service.py:207-210`) does a raw dict lookup, no identity resolver. `preferences()` returns `"key": entry["upstox_key"]` (`app/subscriptions/service.py:231`); `IndicesWidget.tsx:31,61` joins on `q.instrument_token` → in Fyers-only mode the join misses and all index cards render "—".

5. **F&O requires manual apply (Medium, Verified).**
   `api/fno_coverage_routes.py:112-144` `POST /api/fno/apply` → `set_derivatives_universe` + `reconcile`. The derivatives-universe owner is ephemeral (`app/subscriptions/service.py:92`, "NEVER persisted") and **not restored at startup**. Without a manual apply, F&O contracts never get subscribed; in Fyers-only mode the HSM feed only has the two configured index keys and no F&O data flows.

### Architectural weaknesses

- **No health-based provider fallback.** `FnoCoverageService._route_one` uses static preference order + `reg.is_enabled` + `_available` (state in `{streaming,connecting,reconnecting}`). No quote-freshness signal; a stale-but-streaming Upstox feed still wins routing.
- **Provider key-format heuristics scattered** across `app/subscriptions/service.py::_provider_of` (pipe→upstox, colon→fyers), `api/routes.py::_prov_of`, `providers/registry.py::_STYLE_MATCHERS`, and `frontend/.../types.ts::quoteKey`. Adding a provider requires touching all of them.
- **Two separate identity resolvers, only one wired into the quote store.** `InstrumentIdentityRegistry` (config-key → storage-key) is injected into `MarketService` (`app/server.py:590`); `MarketInstrumentIdentityResolver` (→ canonical ID, `app/market_identity.py`) is wired only into the condition-alert engine (`app/server.py:645,657`). `MarketService._merge_quote` computes `Quote.canonical_id` (`market/service.py:251-257`) but the **storage key stays provider-native** — canonical identity enriches the object without unifying the slot.

---

## 4. Coverage Matrix

> Observation date **2026-10-17**. The live `data_test/events.db` catalog is empty (0 rows), so counts come from the Sept 2026 sync recorded in `docs/plans/2026-10-17-fno-full-trading-plan.md` Phase 5. Official universes from nseindia.com / niftyindices.com / bse.co.in catalogs.

| Category | Official exchange universe | Fyers catalog | Upstox catalog | Live feed / field capability | MarketHub ingestion & storage | Subscription / API | Frontend | Auth / entitlement / limits | Gaps |
|---|---|---|---|---|---|---|---|---|---|
| **NSE indices** | ~70+ published NSE indices | 129 NSE_INDEX (Sept 2026) | 139 NSE_INDEX | Fyers HSM: index ticks **dropped** at feed boundary; REST quotes available (untracked file). Upstox V3: full index stream | HSM index not ingested (dropped); REST not committed | `MAJOR_INDICES` hardcoded to 9; `preferences()` returns Upstox key; `IndicesWidget` joins on `instrument_token` | Index strip shows max 9; F&O underlyings limited to `OPTION_CHAIN_INDICES` (6) | Fyers daily-login + index entitlement; Upstox market-data entitlement | 70+ NSE indices not in `MAJOR_INDICES`; no catalog-driven index discovery |
| **BSE indices** | SENSEX + BSE index family | 70 BSE_INDEX | 79 BSE_INDEX | Same HSM `if`-prefix drop for BSE index ticks; Upstox BSE_INDEX | HSM index dropped | `MAJOR_INDICES` includes SENSEX + BANKEX only | `IndicesWidget` shows SENSEX/BANKEX when subscribed | BSE entitlement per provider | 68+ BSE indices not in `MAJOR_INDICES` |
| **NSE stock F&O — underlyings** | ~250 eligible equities (changes monthly) | catalog-driven (`fno_underlying_rows`; 0 in test DB) | catalog-driven (0 in test DB) | Futures/options stream via HSM (Fyers) / V3 (Upstox) | Catalog-driven, 0 rows in test DB | `FnoCoverageService.plan()` discovers underlyings; `POST /api/fno/apply` subscribes | F&O workspace, futures heatmap, option chain | Daily login for both | No auto-apply; no automatic provider fallback |
| **NSE stock F&O — futures** | All active futures per underlying | NSE_FO (85,939 records Sept 2026) | NSE_FO (38,043) | HSM `sf` topic (Fyers) / V3 `full` (Upstox) | Catalog-driven, 0 rows in test DB | `futures_index` bulk read model; routed by `FnoCoverageService` | Futures heatmap, F&O workspace | Daily login; limits: Fyers 5000, Upstox 4500 | No auto-apply; routing doesn't auto-fallback |
| **NSE stock F&O — options** | All active CE/PE + strikes | NSE_FO CE/PE | NSE_FO CE/PE | Greeks via `option_greeks` mode (Upstox). Fyers option chain via REST only | Catalog-driven, 0 rows in test DB | `option_strikes` read model; ATM snapping in `SubscriptionService._resolve_options` | Option chain view, F&O analytics | Daily login; chain REST-on-demand (no WS keys) | No auto-apply; OI analytics (max-pain/PCR) not implemented for Fyers |
| **F&O fields (OI/depth/greeks)** | Exchange provides OI, depth, greeks | `CAP_OI,CAP_PREVIOUS_OI,CAP_OI_CHANGE,CAP_OI_CHANGE_PERCENT,CAP_DEPTH,CAP_OPTION_CHAIN,CAP_HISTORY` | `CAP_LTP,CAP_OHLC,CAP_OI,CAP_OI_CHANGE,CAP_DEPTH,CAP_HISTORY,CAP_OPTION_CHAIN,CAP_GREEKS` | Fyers OI via HSM + REST depth; Upstox OI via feed, greeks via `option_greeks` | `Quote` has `open_interest, previous_oi, oi_change, oi_change_percent, greeks` | `market/oi.py` single implementation; `market/analytics/option_chain.py` | Futures heatmap OI/buildup; F&O workspace greeks | Fyers OI analytics (max pain) not implemented | Fyers OI-change-percent only via REST depth, not HSM |

---

## 5. End-to-End Architecture Map

```
INSTRUMENT MASTERS (official)
  Upstox: assets.upstox.com/.../complete.json.gz
  Fyers:  public.fyers.in/sym_details/<SEG>_sym_master.json
     │  sync_upstox() / sync_fyers()  (manual, WebUI-triggered)
     ▼
INSTRUMENT CATALOG (SQLite: instruments table)   ← EMPTY in data_test/events.db
     ├──► market/derivatives_universe.py  (F&O read model: index_underlyings, stock_underlyings, futures_index — cached + single-flight, keyed by catalog epoch)
     ├──► market/index_registry.py         (canonical 9-index list — hardcoded)
     ▼  FnoCoverageService.plan()
PROVIDER ROUTING (app/fno_coverage.py)
  • routes each contract to a provider by capability + static preference order
  • computes coverage states (LIVE/UNSUBSCRIBED/UNCOVERED/PROVIDER_UNAVAILABLE)
  • keys_by_provider → SubscriptionService.set_derivatives_universe()
     │
     ▼  POST /api/fno/apply   (MANUAL — not automatic; not restored at startup)
SUBSCRIPTION RECONCILE (app/subscriptions/service.py)
  • resolve() → per-provider desired key set (pipe→upstox, colon→fyers)
  • reconcile(feed_provider) → add/remove on live feeds
     ▼
LIVE FEEDS
  UpstoxFeed (V3 WS) ─► QuotePatch ─► MarketService.apply_quote()
  FyersFeed (HSM WS) ─► QuotePatch ─► MarketService.apply_quote()
                                  │  Fyers HSM: index ticks DROPPED at _emit_tick:940
                                  │  Fyers HSM: stock/future ticks DELIVERED
                                  ▼
MARKET SERVICE (market/service.py)
  _quotes: dict[(exchange, instrument_token), Quote]
  • Fyers key: ("NSE","NSE:NIFTY50-INDEX")
  • Upstox key: ("NSE","NSE_INDEX|Nifty 50")
  • get_quote_now(exchange, token) → raw dict lookup (NO identity resolver consulted)
     ├──► _on_market_quote_update → SSE /api/market/stream
     ├──► /api/market/indices → resolve_indices() → resolve_spot() (Upstox-first, Fyers-fallback; Fyers HSM ticks dropped)
     ├──► /api/futures/heatmap → FnoCoverageService.plan() → futures_board
     ├──► /api/market/fno/workspace → SubscriptionService.workspace_contracts()
     └──► /api/options/chain/view → MarketIntel.option_chain()
     ▼
FRONTEND
  IndicesWidget.tsx: byKey = Map(quotes.map(q => [q.instrument_token, q])); idx.key = upstox_key → miss in Fyers-only
  useMarketQuotes.ts: quoteKey(q) = `${q.exchange}:${q.instrument_token}` (provider-native)
  FnoWorkspaceView.tsx: getFnoWorkspace → postFnoView (active-view mutation)
  FuturesHeatmapView.tsx: /api/futures/heatmap → board tiles with provider_token keys
```

**Provider-identifier leaks into shared layers:**

| Leak point | File | Detail |
|---|---|---|
| `InstrumentIdentityRegistry` maps config-key→storage-key but `get_quote_now` never consults it | `market/service.py:207-210` | Two providers store same instrument under different keys |
| `preferences()` returns `"key": entry["upstox_key"]` | `app/subscriptions/service.py:231` | UI joins on Upstox pipe key; Fyers colon key never matches |
| `IndicesWidget.byKey` keyed by `q.instrument_token` | `frontend/.../IndicesWidget.tsx:31` | Per-provider join, no canonical ID |
| `quoteKey()` = `${exchange}:${instrument_token}` | `frontend/.../types.ts:66-67` | Provider-native composite key |
| `_provider_of(key)` checks for `\|` or `:` | `app/subscriptions/service.py:41-52` | Heuristic provider detection from key format |
| `_route_one` routes by static `reg.preference_order()` | `app/fno_coverage.py:394-404` | No health/freshness-based fallback |
| `resolve_spot` tries Upstox key first, then Fyers candidates | `app/market_indices.py:199-233` | Fyers HSM index ticks dropped → Fyers candidates always `None` |

---

## 6. Verified Findings (Ranked by Severity)

### D1 — Critical — Empty Instrument Catalog in Test Database
- **Status: Verified.** `data_test/events.db` schema v23, `instruments` = 0 rows; `md_subscriptions` has 9 index + 2 stock rows (Fyers index keys `NSE:BANKNIFTY-INDEX` / `NSE:NIFTY50-INDEX` stored under the **stock** category — data-quality issue).
- **Impact:** No F&O universe, option chain, futures board, stock F&O coverage. `FnoCoverageService.plan()` → `errors: ["catalog future read failed"]`, `desired: 0`.
- **Root cause:** Catalog never synced into the test DB (or was wiped). Sync mechanism exists (`sync_upstox`/`sync_fyers`) but is manual.
- **Affected:** F&O workspace, futures heatmap, option chain, subscription resolution, market intelligence.
- **Nature:** Operational, not a code defect.

### D2 — Critical — `brokers/fyers/rest.py` Untracked
- **Status: Verified.** `git ls-files --error-unmatch brokers/fyers/rest.py` → no match; `app/server.py:695` imports it at module scope.
- **Impact:** Clean checkout → `ModuleNotFoundError` → server fails to start; Fyers REST adapter unavailable.
- **Root cause:** Created during F&O plan Phase 1, never committed.
- **Fix:** `git add brokers/fyers/rest.py` — no code change.

### D3 — High — Fyers HSM Index Ticks Dropped Unconditionally
- **Status: Verified.** `brokers/fyers/feed.py:937-941` `_emit_tick`: `if snap["topic"].startswith("if"): return` — unconditional drop; comment says "Upstox is the authoritative index source".
- **Impact:** Fyers-only mode → no live index quotes in `MarketService`; `resolve_spot` returns `{"quote": None, "basis": "unavailable"}`; index strip shows "—".
- **Root cause:** Design decision made when Upstox was always available; not conditional on Upstox health.
- **Affected:** Index strip, F&O spot resolution, option-chain ATM snapping, futures-board spot basis.
- **Fix options:** (a) make drop conditional on Upstox freshness, (b) unconditional delivery gated on verifying HSM index scaling, (c) Fyers REST index-quote backfill poller.

### D4 — High — `IndicesWidget` / Quote Store Join on Provider-Specific Keys
- **Status: Verified.** `IndicesWidget.tsx:31` `byKey = Map(quotes.map(q => [q.instrument_token, q]))`; `:61` `byKey.get(idx.key)` where `idx.key` = `preferences().indices[].key` = `entry["upstox_key"]` (pipe). Fyers-only: `q.instrument_token` = `"NSE:NIFTY50-INDEX"` ≠ `"NSE_INDEX|Nifty 50"` → miss.
- **Impact:** Dashboard index strip shows "—" in Fyers-only mode.
- **Root cause:** `preferences()` exposes only the Upstox key; no canonical join key.
- **Affected:** Dashboard index strip (Fyers-only), watchlist join (`WatchlistTable.tsx:23` uses the same `quoteKey`).

### D5 — High — Quote Store Keys Are Provider-Specific; Identity Resolver Not Consulted on Read
- **Status: Verified.** `market/service.py:207-210` `get_quote_now` → raw `self._quotes.get((exchange, instrument_token))`. `InstrumentIdentityRegistry` injected at `app/server.py:590` but used only in `_merge_quote` to set `Quote.canonical_id` (`market/service.py:251-257`) — **not** in `get_quote_now`.
- **Impact:** Dual-provider: two separate storage slots for one instrument; a consumer using one provider's key never sees the other provider's quote. `api/routes.py::_resolve_token` (line 65-70) resolves a config key to a storage key but cannot converge Fyers colon-keys and Upstox pipe-keys.
- **Root cause:** Phase B2 storage key was provider-native `(exchange, instrument_token)`; canonical identity added later without re-keying.
- **Affected:** All quote lookups: REST quote/depth, option-chain spot, futures board, market intelligence.

### D6 — Medium — F&O Subscription Requires Manual Apply
- **Status: Verified.** `api/fno_coverage_routes.py:112-144` `POST /api/fno/apply` → `set_derivatives_universe` + `reconcile`. The derivatives-universe owner is ephemeral (`app/subscriptions/service.py:92` `_derivatives_universe`, "NEVER persisted"). `app/server.py` lifespan calls `reconcile` only for persistent owners (indices/stocks/derivative rules), not the derivatives-universe owner.
- **Impact:** Fyers-only mode: F&O contracts routed to Fyers but never subscribed unless operator manually applies; futures heatmap shows all `UNSUBSCRIBED`.
- **Root cause:** Ephemeral owner + no startup restore.
- **Fix:** Persist the last-applied plan and auto-restore at startup, or auto-apply.

### D7 — Medium — `md_subscriptions` Stores Fyers Index Keys as "stock"
- **Status: Verified.** `data_test/events.db`: `('stock','NSE:BANKNIFTY-INDEX','BANKNIFTY',1)` and `('stock','NSE:NIFTY50-INDEX','NIFTY 50',1)`.
- **Impact:** `SubscriptionService._stock_keys()` routes them via `_provider_of` → Fyers; `preferences()` reports them as stocks, not indices; the index strip won't show them as indices.
- **Root cause:** `migrate_from_config` (`app/subscriptions/service.py:173-219`) matches config instruments against `MAJOR_INDICES` upstox keys only; Fyers colon keys fall to the `else` branch (`category="stock"`).
- **Fix:** Recognize Fyers index keys and import as `index` category with correct label.

### D8 — Medium — No Automatic Provider Health-Based Fallback
- **Status: Verified.** `FnoCoverageService._route_one` (line 448-493) iterates static `order` (preference), checks `reg.is_enabled`, `supports`, `by_prov`, `_available` (state in `{streaming,connecting,reconnecting}`), budget. Falls back to next provider only when the preferred is *state*-unavailable. No freshness/quote-validity signal; a stale-but-streaming Upstox feed still wins.
- **Impact:** No rapid switching (good) but no recovery from a degraded-but-available provider; duplicate-work / stale-data risk.
- **Fix:** Add a freshness check to `_available()` (last-quote age > threshold → treat as unavailable for routing).

### D9 — Low — `MAJOR_INDICES` Is a Hardcoded 9-Index Subset
- **Status: Verified.** `app/market_indices.py:45-73` = 9 indices; NSE publishes 70+, BSE has 70+ in the masters. Requirement: "Do not restrict coverage to a manually curated list."
- **Impact:** Index strip, F&O underlyings, option chain limited to 9.
- **Fix:** Discover indices from the catalog (`instrument_type='INDEX'`), keep `MAJOR_INDICES` as a featured subset.

### D10 — Low — `fno/view` Request Storm (Previously Identified; Fix Not Yet in Working Tree)
- **Status: Partially Verified.** `smoke2.out`/`smoke3.out` (~1,500 req/s, ~187,813 console entries). `docs/current.md` §2.3 documents the fix (depend on stable `mutate` callback, not the per-render `useMutation` result object) + regression test `frontend/src/__tests__/FnoWorkspaceView.test.tsx`. The fix is **not** reflected in the current working-tree `FnoWorkspaceView.tsx`.
- **Impact:** Uncontrolled polling / browser resource exhaustion when data unavailable.

---

## 7. Recommended Architecture

### 7.1 Canonical Instrument Identity (three layers)
- **Layer 1 — Canonical ID (extend existing):** `MarketInstrumentIdentityResolver` (`app/market_identity.py`) already maps every catalog row to a provider-neutral ID: `INDEX: {exchange}:INDEX:{sym}`, `EQUITY: {exchange}:EQUITY:{ISIN|sym}`, `FUTURE: {exchange}:FUTURE:{underlying}:{expiry}`, `OPTION: {exchange}:OPTION:{underlying}:{expiry}:{strike}:{CE|PE}`. This is correct and should become the **storage key**.
- **Layer 2 — Provider Adapter (change):** Feeds/REST write `QuotePatch` with the canonical ID as storage key; the provider-native token is preserved as `Quote.provider_token` for feed ops + provenance. The feed still subscribes with the native key on the wire.
- **Layer 3 — UI (change):** Frontend receives quotes with `canonical_id` as the primary join key; `instrument_token` demoted to provenance. `IndicesWidget`/`quoteKey` join on `canonical_id`.

### 7.2 Provider Adapter Contract
Each adapter owns: capabilities declaration (`brokers/{name}/capabilities.py` — done), feed lifecycle (`brokers/{name}/feed.py` — done), REST client (`brokers/{name}/rest.py` — Fyers needs committing), normalizer (`market/normalize/{name}.py` — done, pure), key style (`STYLE_COLON/PIPE/NUMERIC`). Must expose `subscribe/unsubscribe(provider_keys)` and `quote_to_canonical(provider_key, exchange) -> canonical_id`.

### 7.3 Automatic Provider Selection + Fallback
`ProviderRegistry` exposes a routing decision: `route(contract, health_map) -> (provider, provider_token) | None`, iterating preference order and gating on enabled, capability, catalog row, **freshness**, and budget. `health_map` is populated by feed state + last-quote age + subscription success rate. This is a **central policy** consumed by `FnoCoverageService`, not per-feature.

### 7.4 Normalized Quote Storage + Provenance
`Quote` gains `canonical_id` (stable key), `provider`, `provider_token` (native), `source_provenance` (`hsm`/`rest`/`tbt`). `MarketService._quotes` keyed by `(exchange, canonical_id)`.

### 7.5 Catalogue Refresh & Subscription Reconciliation
- Catalogue refresh stays **manual** (large downloads; auto-refresh would hammer provider APIs). Catalogue-epoch invalidation of derived read models already works.
- Auto-apply the derivatives-universe owner at startup (restore last-applied plan).
- Expiry cleanup already correct: `fno_coverage._choose_expiry`, `market_intel.option_chain`, `subscriptions._unexpired_expiries` all filter `expiry >= today`.
- Stale-contract handling: `futures_index` cache keyed by catalogue epoch + 300s TTL; epoch bump on sync invalidates. Correct.

### 7.6 Stable API Contracts
All market-data responses include `canonical_id`, `provider` + `provider_token`, `basis` (`live`/`stale`/`unavailable`/`fallback_mid_strike`), `received_ts`. The frontend never needs to know which provider supplied a quote to display it correctly; provider attribution is a diagnostic overlay.

### 7.7 Centralized, Maintainable Configuration
| Policy | Location | Default |
|---|---|---|
| Provider preference order | `fno_config` (persisted) | `["upstox","fyers"]` |
| Staleness threshold | `fno_config` | 5 min |
| F&O auto-apply on startup | `fno_config` | true |
| Per-provider budget | `providers/registry.py` (adapter-declared) | Fyers 5000, Upstox 4500 |
| Analytics universe cap | `api/routes.py::ANALYTICS_CAP` | 400 |
| Max instruments per provider | `subscriptions/service.py::MAX_INSTRUMENTS_PER_PROVIDER` | 500 |

Changeable operational policies live centrally; user-adjustable ones exposed in WebUI; stable invariants (expiry filtering, canonical key schema) stay in code, not config.

---

## 8. Staged Implementation Plan (Dependency-Ordered)

> Each stage is independently verifiable. **No stage touches the frozen auth/startup subsystem.** Schema/persistence/red-zone changes are flagged for explicit maintainer review.

### Stage 1 — Commit `brokers/fyers/rest.py` (D2)
- **Objective:** Make the Fyers REST client available in clean checkouts.
- **Files:** `git add brokers/fyers/rest.py` (no code change); verify `app/server.py` import resolves.
- **Addresses:** D2. **Dependencies:** none. **Risks:** none.
- **Acceptance:** `python -c "from brokers.fyers.rest import FyersRest"` on a clean checkout; `pytest test/test_provider_registry.py -q` green.
- **Rollback:** `git reset --soft HEAD~1`. **Preserves in-flight work:** yes (adds a file only).

### Stage 2 — Canonical-Identity Read-Through in `MarketService` (D5)
- **Objective:** `get_quote_now`/`get_depth_now` fall back to the identity resolver when the direct `(exchange, token)` lookup misses, converging cross-provider reads.
- **Files:** `market/service.py`.
- **Addresses:** D5. **Dependencies:** Stage 1. **Risks:** low — additive read path; write path unchanged.
- **Acceptance:** unit test — two providers write the same instrument under different keys; `get_quote_now` with either key returns the same quote.
- **Rollback:** revert `market/service.py`. **Preserves:** yes (additive; file is in the modified list).

### Stage 3 — Conditional Fyers Index-Tick Drop (D3)
- **Objective:** Drop Fyers HSM index ticks only when a fresh Upstox quote exists for the same canonical index; otherwise deliver.
- **Files:** `brokers/fyers/feed.py::_emit_tick`.
- **Addresses:** D3. **Dependencies:** Stage 2 (canonical lookup). **Risks:** medium — feed must call lock-free `get_quote_now` for the Upstox key; keep it fast.
- **Acceptance:** Upstox fresh → Fyers index tick dropped; Upstox stale/absent → Fyers index tick delivered.
- **Rollback:** revert. **Preserves:** yes (feed.py committed, not in modified list).

### Stage 4 — Fix `preferences()` + Frontend Join to Provider-Agnostic Keys (D4, D7)
- **Objective:** `preferences().indices[].key` returns the canonical ID (add `fyers_symbol`/`upstox_key` as separate fields); `IndicesWidget`/`quoteKey` join on canonical ID. Fix `migrate_from_config` to categorize Fyers index keys as `index`.
- **Files:** `app/subscriptions/service.py`, `frontend/src/features/dashboard/components/IndicesWidget.tsx`, `frontend/src/features/dashboard/types.ts`, `frontend/src/features/watchlists/components/WatchlistTable.tsx`.
- **Addresses:** D4, D7. **Dependencies:** Stage 2. **Risks:** medium — API response shape changes additively; frontend consumers must use the new key field.
- **Acceptance:** `IndicesWidget` renders index cards with Fyers-only quotes; `GET /api/subscriptions` returns `canonical_id` per index; Fyers index config keys import as `index` category.
- **Rollback:** revert. **Preserves:** `app/subscriptions/service.py` is in the modified list — careful merge.

### Stage 5 — Auto-Apply / Restore Derivatives Universe at Startup (D6)
- **Objective:** Persist the last-applied F&O routing plan; restore it at startup so F&O contracts are subscribed without manual action.
- **Files:** `core/persistence/modules/fno_config.py` (new persistence field), `app/fno_coverage.py`, `app/server.py` lifespan.
- **Addresses:** D6. **Dependencies:** Stage 2. **Risks:** **high — touches persistence (red zone per ARCHITECTURE_BOUNDARIES); requires separate protected-core approval.**
- **Acceptance:** server boots with the derivatives-universe owner populated and F&O contracts subscribed.
- **Schema implication:** new persistence field. **Rollback:** revert to current staged `fno_config`.

### Stage 6 — Catalogue-Driven Index Discovery (D9)
- **Objective:** Discover all available indices from the catalog instead of the hardcoded 9; keep `MAJOR_INDICES` as a featured subset.
- **Files:** `app/market_indices.py` (add `discover_indices(catalog)`), `market/index_registry.py`, `IndicesWidget.tsx`.
- **Addresses:** D9. **Dependencies:** Stage 1 (catalogue synced), Stage 4 (canonical join). **Risks:** medium — resolver must handle all index symbols, not just 9.
- **Acceptance:** after sync, `discover_indices` returns all NSE_INDEX + BSE_INDEX records; frontend renders the full list.

### Stage 7 — Provider Health-Based Fallback (D8)
- **Objective:** Add freshness-aware provider availability to routing; a provider whose last quote is older than the staleness threshold is treated as unavailable.
- **Files:** `app/fno_coverage.py::_available`, `providers/registry.py` (add `health_fn`).
- **Addresses:** D8. **Dependencies:** Stage 2. **Risks:** medium — health check must be lock-free and fast (called per contract in `plan()`).
- **Acceptance:** provider with stale quotes → marked unavailable → routing falls back to next provider.

### Stage 8 — Fix `migrate_from_config` Fyers Index Categorization (D7)
- **Objective:** Fyers index keys in config.json import as `index` with correct label.
- **Files:** `app/subscriptions/service.py::migrate_from_config`.
- **Addresses:** D7. **Dependencies:** Stage 4. **Risks:** low — one-time idempotent migration (marker in `source_state`).
- **Acceptance:** config with Fyers index keys produces `index` subscriptions.

### Stage 9 — Fyers HSM Index Tick: Unconditional Delivery + Staleness Label (D3 alternate)
- **Objective:** Remove the index-tick drop entirely; deliver all Fyers HSM index ticks. `resolve_spot`'s Upstox-first ordering already prefers Upstox; Fyers ticks serve as fallback.
- **Files:** `brokers/fyers/feed.py::_emit_tick`.
- **Addresses:** D3 (alternate). **Dependencies:** Stage 2. **Risks:** medium — original concern was "unreliable multiplier/precision (e.g. NIFTY reported as ~87,000)". The HSM `_INDEX_VAL` scaling (`_emit_tick` line 942: `scale = (10 ** precision) * multiplier`) must be verified correct **before** this is safe.
- **Acceptance:** live HSM smoke test during market hours — NIFTY index tick produces a correct LTP.
- **Recommendation:** Prefer Stage 3 (conditional) as the lower-risk default; use Stage 9 only if HSM index scaling is verified correct.

### Stage 10 — Frontend Request-Storm Fix (D10)
- **Objective:** Apply the documented mutation-loop fix in `FnoWorkspaceView.tsx`.
- **Files:** `frontend/src/features/fno/FnoWorkspaceView.tsx`.
- **Addresses:** D10. **Dependencies:** none. **Risks:** low — documented + tested.
- **Acceptance:** `frontend/src/__tests__/FnoWorkspaceView.test.tsx` passes (`idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`).

### Stage 11 — Operational: Sync the Instrument Catalogue (D1)
- **Objective:** Trigger a catalogue sync so `instruments` is populated.
- **Files:** none (operational — WebUI/API `POST /api/instruments/sync`).
- **Addresses:** D1. **Dependencies:** Stage 1. **Risks:** low — sync mechanism exists and is transactional (failed download leaves previous catalogue intact).
- **Acceptance:** `SELECT COUNT(*) FROM instruments` > 0; `FnoCoverageService.plan()` returns `desired > 0`.

---

## 9. Approval Decisions (genuine forks needing maintainer sign-off)

| # | Decision | Alternatives | Recommended default |
|---|---|---|---|
| **G1** | Canonical ID as storage key | (A) re-key `_quotes` to `(exchange, canonical_id)` — clean, single slot, but cross-cutting write-path change; (B) read-through fallback in `get_quote_now` — additive, two slots persist, no write-path change | **B now, A later as a separate approved task** (re-keying is protected-core territory) |
| **G2** | Fyers HSM index ticks | (A) unconditional delivery (Stage 9), gated on HSM scaling verification; (B) conditional delivery only when Upstox stale (Stage 3) | **B (Stage 3)** as the lower-risk default; A only after live HSM scaling check passes |
| **G3** | F&O universe at startup | (A) auto-apply full universe every boot — seamless but budget-exhaustion risk; (B) restore last-applied plan, require explicit apply for new plans — safer | **B** (Stage 5): restore last-applied, operator intent preserved |
| **G4** | Index strip scope | (A) all catalogue-discovered indices (70+ NSE, 70+ BSE) — complete but clutter + budget; (B) featured `MAJOR_INDICES` + F&O underlyings in the main strip, full catalogue in a searchable panel | **B** (Stage 6) |

---

## 10. Measurable Acceptance Criteria

### Provider configurations
1. **Fyers-only:** Upstox disabled, Fyers HSM `streaming` → index strip shows live NIFTY/BANKNIFTY; `GET /api/market/indices` returns `provider:"fyers"`, `basis:"live"`; `resolve_spot("NIFTY")` → Fyers quote, `basis:"live"`.
2. **Upstox-only:** Fyers disabled, Upstox V3 `streaming` → index strip live; `provider:"upstox"`, `basis:"live"`; no Fyers code path triggered.
3. **Both:** both feeds `streaming` → index strip live; `GET /api/market/quotes` contains both providers' quotes; each index entry carries a `provider` field; no duplicate *logical* instrument (or both slots converge via canonical ID after Stage 2).

### Failure / fallback
4. Upstox `auth_required` → Fyers-only routing auto; Fyers `auth_required` → Upstox-only; both `auth_required` → all indices `basis:"unavailable"`; feed task killed → reconnects within backoff, quotes resume; no retry storm (exponential+jitter backoff in `brokers/fyers/feed.py::_next_backoff`).

### Identity / freshness
5. `resolve("NSE:NIFTY50-INDEX") == resolve("NSE_INDEX|Nifty 50") == resolve("Nifty 50")` → same canonical ID; `Quote.canonical_id` populated for both providers; `get_quote_now("NSE","NSE:INDEX:NIFTY")` returns the same quote regardless of provider (post-Stage 2).
6. Quote older than 5 min → `basis:"stale"`, never `live`; no provider data → `basis:"unavailable"`; `source_provenance` distinguishes `hsm`/`rest`/`tbt`.

### Coverage
7. After catalog sync, `stock_underlyings(catalog, provider="upstox", today=...)` returns current NSE F&O equity underlyings; `futures_index(catalog, exchange="NSE")` returns all active futures; `FnoCoverageService.plan()` routes contracts; `POST /api/fno/apply` subscribes them; `GET /api/fno/coverage` shows `states:{LIVE:N, UNSUBSCRIBED:0}`.
8. `discover_indices(catalog)` returns all NSE_INDEX + BSE_INDEX records; `SENSEX`/`BANKEX` appear when subscribed; BSE futures/options routable.
9. Unsupported instrument → `basis:"unavailable"`, no fabricated quote; provider lacking capability → `UNCOVERED` + reason; expired contract (expiry < today) filtered at boundary.

### Behavioral
10. No uncontrolled polling/retry storm: `FnoWorkspaceView` `idle=1 afterRenders=1 atUnmount=1 afterUnmount=1`; bounded polling when data unavailable; server CPU < 10% with F&O + heatmap open.
11. Subscription limits: `resolve()` raises `SubscriptionLimitError` when a provider exceeds `MAX_INSTRUMENTS_PER_PROVIDER`; `reconcile()` applies add/remove without restart; `ANALYTICS_CAP=400` enforced; provider budgets respected in `_route_one`.
12. Normal use without manual broker/instrument selection: new user with both providers authenticated sees live index strip, auto-selected F&O underlyings, populated futures heatmap; no "Select Provider" / "Add Instrument" step; last-applied F&O plan restored at startup.

### Clean checkout / CI
13. `git clone` + `pip install -r requirements.txt` + `python -m app.server` succeeds (no `ModuleNotFoundError` for `brokers/fyers/rest.py`); `pytest test/ -q` passes (record current baseline); `cd frontend && bun run build` succeeds; `pytest test/test_architecture_boundaries.py -q` green; no untracked Python file imported by `app/server.py`.

### Negative tests
- Fyers-only with no valid Fyers session → feed `auth_required`, index strip shows `basis:"unavailable"` (never `live`), no fabricated quote.
- Provider with a stale-but-streaming feed → routed contracts marked `STALE`/`PROVIDER_UNAVAILABLE`, not silently shown as live.
- Unknown instrument in search → `unknown-underlying` basis, no invented data.

---

## 11. Source / Research Verification (URLs, checked 2026-10-17)

- **NSE index catalogue** — https://www.nseindia.com/static/products-services/indices-sectoral (70+ sectoral/broad/theme indices), https://www.niftyindices.com (index universe). NSE homepage shows NIFTY 50, NEXT 50, BANK, FIN SERVICE, MID SELECT, FPI 150, 100, MIDCAP 100, SMLCAP 100, IT, etc.
- **BSE index catalogue** — https://www.bseindia.com (SENSEX + BSE index family).
- **Upstox instrument master** — https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz ; segments include `NSE_EQ, NSE_INDEX, NSE_FO, BSE_EQ, BSE_INDEX, BSE_FO, MCX_FO, NCD_FO, BCD_FO, NSE_COM`. V3 feed modes `ltpc / option_greeks / full / full_d30`. Option chain `/v2/option/chain`; history `/v3` with OI; market-quote quotes ≤500.
- **Fyers v3 API** — `https://api-t1.fyers.in/data/{quotes,depth,history,options-chain-v3,marketStatus}`; auth header `Authorization: <app_id>:<access_token>`; envelope `{"s":"ok"/"error","code":<int>,"message"}`; option chain `optionsChain` array, `strikecount` + `timestamp` (expiry epoch); depth carries `pdoi`/`oipercent` (REST only); HSM WS `wss://socket.fyers.in/hsm/v1-5/prod` binary, symbol-token API `https://api-t1.fyers.in/data/symbol-token`. No third-party data provider introduced; Fyers/Upstox entitlement/licensing is the only constraint (no order placement added).

---

## 12. What This Is / Is Not

- This is **audit + plan only**. No source, test, config, DB, migration, lockfile, or build file was modified. No patch produced.
- A passing test, a live connection, or a visible dashboard card is **not** proof of complete coverage — the empty catalogue (D1) and dropped index ticks (D3) show that live display and true universe coverage are independent concerns.
- Stages 5 and 8 touch the persistence red-zone (`core/persistence`) and `app/subscriptions/service.py` (frozen per `AGENTS.md`); if in scope they require separate protected-core approval and are flagged, not folded into the ordinary plan.

---

## 13. Bottom Line & Next Step

- **Stages 1–2 + 9 (critical path for Fyers-only display) and Stage 11 (catalogue sync) are the smallest set that makes Fyers-only actually work end-to-end.** Stages 4–8 and 10 are separable follow-ups.
- Awaiting maintainer review and explicit approval of **G1–G4** and the stage sequence before any code changes.
