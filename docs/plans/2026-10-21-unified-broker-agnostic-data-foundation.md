# MarketHub — Unified, Broker-Agnostic Data Foundation

| | |
|---|---|
| **Plan ID** | `MH-UDF-2026-10-21` |
| **Status** | Approved (P1 protected edit approved with strict scope; capacity = limit not target; demand-driven subscriptions) - **not started** |
| **Repo copy** | `docs/plans/2026-10-21-unified-broker-agnostic-data-foundation.md` |
| **Branch** | `fno-provider-agnostic` (HEAD `3f4b8d9`) |
| **Prepared** | 2026-10-21 |
| **Verified against** | the live working tree (file inventory in §0.10) and the official Upstox / FYERS API docs (§0.8) |

Implementation plan (authored after reading the current tree, not from the audit docs).
Target branch: `fno-provider-agnostic`. Working tree already contains extensive uncommitted
work from earlier sessions — that work is PRESERVED, never reset/stashed/reverted, and never
included in my commits.

---

## 0a. APPROVED DECISIONS (owner, 2026-10-21)

**P1 — `app/server.py` protected edit: APPROVED, STRICT SCOPE.**
Composition and dependency injection only: construct + inject the subscription manager, the
shared quote service and the alert↔lease integration. No auth / login / restore / credential /
source-wiring changes. `app/server.py` stays a thin composition root — all implementation lives
in focused, separately-owned modules (no giant modules; provider adapters, capacity, leases,
canonical identity, quote selection and alert evaluation each keep their own module).

**Reserve policy: NO ARBITRARY RESERVE.**
- Capacity is a **LIMIT, not a target**: the verified account/feed-mode limits are hard upper
  bounds (FYERS Standard ≤ 100, Upstox Full ≤ 2,000), clamped to the account's actual
  entitlement and the documented constraints. Limits are enforced centrally, each with the scope
  the provider documents (FYERS socket symbols: **per user**; Upstox: **per user**, with
  individual + combined category rules; REST: per API / per user). The broker's real limit is
  never exceeded, not even temporarily.
- **Subscriptions are demand-driven**: only the agreed baseline coverage, active workspaces,
  focused option windows and alert dependencies are subscribed. Nothing is pre-subscribed because
  capacity is free - no extra strikes, no extra expiries, no "fill the socket". Unused capacity
  is acceptable and expected.
- **No duplicate contract across providers** unless an explicit operational requirement
  justifies it: each canonical contract is routed through **one provider at a time**, with
  automatic failover/rerouting when that provider is unavailable.
- Both providers run **concurrently to maximise useful coverage**, not to maximise subscription
  counts.
- When demand exceeds a provider's verified capacity: allocate by priority and **report what
  cannot be served**. Never exceed the actual limit.
- No arbitrary reserves, no arbitrary application caps, no unnecessary background polling.
  Rate limits are applied only where the relevant API requires them.
- **Acceptance criterion:** MarketHub subscribes to everything required by the agreed scope that
  can safely be served, and nothing extra merely to consume available capacity.
- The current `effective_budget = cap - max(10 % cap, 50)` (and any replacement percentage/fixed
  reserve) is **removed**: it silently halves Fyers coverage without evidence.
- A provider-specific headroom may exist **only with documented evidence** that the provider
  requires it, and must be configurable. No blanket percentage, no fixed reserve.
- When capacity is unavailable, the affected instruments and the reason are reported explicitly —
  never a silent drop, never a live-coverage claim that does not exist.

---

## 0. Verified current state (evidence, file:line)

Read-only inspection on 2026-10-21. Live DB inspected with `sqlite3 ... ?mode=ro` only.

### 0.1 Test/runtime data-path isolation DEFECT (real, blocking)

- `app/runtime_paths.py:36-56` — `resolve_runtime_data_dir()` **always** returns the canonical
  `DATA_ROOT`; a configured `data_dir` is only *labelled* `"overridden"`. The module docstring
  claims "a caller that injects a directory explicitly (isolated tests) is never redirected"
  — the code does the opposite.
- Consequence: `app/server.py:185` opens `_runtime_db_path(...)` = **`data/events.db`** and
  `app/server.py:835` builds the credential store on the **live `data/master.key`**, even
  though `config.json` sets `"data_dir": "data_test"` and `test/helpers/lifecycle.py:240,402`
  start servers with `data_dir="data_test"`.
- Tests that `import app.server` therefore open/migrate the **live** DB during pytest:
  `test/test_market_sse.py:50`, `test/test_x_route_wiring.py:25`, `test/test_fyers_pin.py:53`,
  `test/test_options_analytics.py:283`, `test/test_release_hardening.py:500`,
  `test/test_fyers_config_architecture.py:201`, `test/test_consumer_completents.py:249`,
  `test/test_unit_sources.py:321`, `test/test_startup_regression.py:29`.
- Live DB today: `data/events.db` = 73,543,680 bytes, mtime 2026-10-21 18:27;
  `instruments` = 142,932 rows (fyers 89,713 / upstox 53,219). **Not to be deleted, reset or rewritten.**

### 0.2 Catalogue sync is not fail-safe

- `core/persistence/modules/products.py:283-320` `replace_provider_instruments()` is
  transactional (`BEGIN IMMEDIATE` / rollback) but **unconditionally** `DELETE FROM instruments
  WHERE provider = ?` before inserting.
- `app/instruments.py:344-361` `sync_upstox()` — no completeness gate: a master that parses to
  zero rows wipes the Upstox catalogue.
- `app/instruments.py:363-392` `sync_fyers()` — per-segment download failures are swallowed
  (`except InstrumentSyncError: continue`, line 378) and the **whole provider set is still
  replaced**: rows for the failed segments are deleted. All segments failing ⇒ `kept == []` ⇒
  every Fyers row deleted.
- Identity registries are refreshed only at startup (`app/server.py:651-675`) → after a sync
  the canonical identity maps are stale until restart.

### 0.3 FYERS dynamic subscription DEFECT (confirmed)

- `brokers/fyers/feed.py::_mutation_frame` → `hsm = [self._hsm_symbols.get(s, s) for s in symbols]`
  → a key with no resolved wire token is sent **as the raw API symbol** on the HSM wire
  (e.g. `NSE_EQ|INE002A01018`), which the socket cannot resolve ⇒ silent no-data subscription.
- `_resolve_hsm_symbols()` returns early when `self._hsm_symbols` is non-empty ⇒ keys added later
  are never resolved, and a reconnect re-uses the stale map.
- `brokers/fyers/feed.py::_INDEX_DICT` has **no NIFTYFPI entry** ⇒ fallback emits `if|nse_cm|NIFTYFPI`
  (invalid topic). The official SDK `map.json` has no NIFTYFPI entry either, so the fix is **not**
  to invent a topic name: FYERS index symbols leave the HSM socket subscription set entirely
  (see §0.9 / Phase B) and FYERS indices are served over the guarded REST path with
  catalogue-verified symbols.

### 0.4 NIFTYFPI mapping is wrong (catalogue evidence, read-only)

Live catalogue rows for the same index:

| provider | instrument key / token | tradingsymbol | name |
|---|---|---|---|
| fyers  | `101000000026137` | `NSE:NIFTYFPI150-INDEX` | INDEX |
| upstox | `NSE_INDEX\|Nifty FPI 150` | `NIFTYFPI` | Nifty FPI 150 |

Current code (`app/market_indices.py:62-63`) says `fyers_symbol="NSE:NIFTYFPI-INDEX"`,
`upstox_key="NSE_INDEX|Nifty FPI"` — **neither string exists in the catalogue** (exact-match
query returned zero rows). Every other index entry (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY,
NIFTYNXT50, INDIA VIX, SENSEX, BANKEX) matches the catalogue exactly.

### 0.5 Arbitrary caps and a split capacity model

- `providers/capacity.py:25` `APP_CAP = 500` and `app/subscriptions/service.py:31`
  `MAX_INSTRUMENTS_PER_PROVIDER = 500` — undocumented global caps.
- `app/subscriptions/service.py:996-1000` — exceeding 500 raises `SubscriptionLimitError` for
  the **whole plan**, before any provider is touched ⇒ one oversized provider kills the pass.
- `providers/capabilities.py:44-51` `effective_budget = cap - max(10% cap, 50)` ⇒ Fyers Standard
  (100) yields **50** usable slots (50 % of the account) and Upstox full (2000) yields 1800.
  The reserve is unexplained and silently halves Fyers coverage.
- `providers/capacity.py:51-58` `PLAN_CAP_OVERRIDES {"fyers": {"prime": 5000}}` — an explicit
  Prime override exists (good), but `brokers/fyers/capabilities.py` declares Standard 100 as the
  default tier (good). No documented profile field exposes the tier (see 0.8).
- Two conflicting budget owners: `app/fno_coverage.py:361,515,574` uses `reg.budget(pid)`;
  `app/subscriptions/service.py` uses its own constant. No lease/ref-count mechanism exists
  anywhere (grep `lease|refcount` → no implementation).

### 0.6 Quote selection / identity

- `market/quote_coordinator.py` already selects one **fresh** quote per canonical id
  (most-recent wins, provider preference = tie-break) — good core. But:
  - stale-only instruments are **dropped** (no last-known value + stale flag + `as_of`);
  - `provider_of_key()` (lines 34-40) infers the provider from the **key shape**
    (`"|"` ⇒ upstox) — the exact "provider-native storage key mistaken for identity" pattern;
  - it is wired **only** in `api/routes.py:95-150` (SSE snapshot + bulk REST).
    Not used by `api/product_routes.py` (market-data routes), `mcp_server/tools/market.py:144`
    (`svc.get_quote(exchange, token)`), `mcp_server/tools/options_analytics_tools.py:244`,
    `app/market_indices.py` (`get_quote_now`), `app/market_analytics.py`, or the alert engine.
- `app/instrument_identity.py::resolve_quote()` resolves a quote's `instrument_token`
  (a **storage key**) to a canonical id ⇒ storage key used as an identity alias.

### 0.7 Alerts

- `app/condition_alerts.py` freshness is **dependency-driven** (a leaf is "fresh" when its own
  tick arrives) — there is **no absolute freshness policy** anywhere (grep `stale|max_age`
  finds only dependency-driven logic) ⇒ a late/stale quote can trigger an alert.
- **No leases**: nothing subscribes an alert's dependencies, so an alert can report monitoring
  while nothing is subscribed for its instrument.
- `reload()` (lines 176-247) rebuilds `_state` wholesale and clears `self._last_values` ⇒ a
  create/update/delete of one alert can reset other alerts' in-memory values.
- Persistence is already sufficient for expiry/dependencies: the condition tree (incl.
  `instrument.expiry`) lives in `condition_json`, and `metadata_json` is a free-form column
  (`core/persistence/modules/condition_alerts.py:75-90`) ⇒ **no schema migration required**.

### 0.8 Provider limits - re-verified against the OFFICIAL docs (2026-10-21)

Sources: Upstox Developer API (`upstox.com/developer/api-documentation/v3/get-market-data-feed/`,
`/rate-limiting/`, `/instruments/`, `/get-full-market-quote-v3/`) and FYERS API v3 docs
(`fyers.in/api-docs/v3/getting-started/plans-and-limits`, `/api/websockets/data-socket/...`,
`/api/market-data/option-chain/`, both "Last updated on October 14, 2026"), plus the official
FYERS SDK asset `FyersWebsocket/map.json` (the file the SDK itself uses for HSM symbols).

| limit | value (official) | scope - **this is the part that matters** |
|---|---|---|
| FYERS Data socket symbols | Standard **100**, Prime **5,000** | "counted **per user, across all your connections** to that socket, **not per connection**" (Data Socket Overview) |
| FYERS Data socket instances | "You can create only **one** Data Socket connection instance at a time" | ⇒ FYERS capacity is an **account-wide** number; there is no per-connection budget to multiply |
| FYERS TBT socket | 1 (Std) / 15 (Prime) | Prime = 15 total: up to 3 connections x 5 symbols |
| FYERS Data APIs (REST) | Std 5 rps / 50 rmin / **5,000 per day**; Prime 10 / 500 / 500,000 | "exceeding the **per-minute** limit more than **3 times in a day** blocks the user for the rest of the day" |
| FYERS Quotes API | 50 symbols/request (Std), 1,000 (Prime) | batch size |
| FYERS Option chain | `strikecount` **max 50** (each side of ATM); `greeks=1` adds delta/gamma/theta/vega/iv | window size is broker-bounded |
| FYERS index updates (`if`) | fields: symbol, ltp, prev_close_price, high/low/open, ch, chp | **no volume, no bid/ask** - matches our `_INDEX_VAL` |
| Upstox V3 feed - connections | 2 per user (5 with Upstox Plus) | |
| Upstox V3 feed - subscriptions | LTPC 5000 / Option Greeks 3000 / **Full 2000** / Full D30 50 (individual) | **combined** limits when several categories are used: LTPC 2000, Option Greeks 2000, **Full 1500** |
| Upstox REST (Other Standard APIs) | per API, per user: 50 rps / 500 rmin / **2,000 per 30 min** | the 30-minute window is the binding constraint |
| Upstox market-quote REST | up to **500** instrument keys per request (quotes / ltp / ohlc / option-greek) | batch size |
| Upstox instrument files | `complete.json.gz` (what we already use) + per-exchange NSE/BSE/MCX; refreshed daily ~6 AM | "use `instrument_key`... `exchange_token` may be reused after expiry" |
| Plan detection | **no documented profile field** exposes Standard/Prime | tier must be observed or configured |

Corrections to the earlier (audit-derived) numbers in this plan:

- Upstox `full` is **2000 individual** but **1500 combined** when other categories are subscribed
  alongside it. MarketHub uses one mode, so 2,000 applies - but the manager must model the
  combined rule so a future second category cannot silently exceed it.
- FYERS socket capacity is **per user**, not per connection (our earlier text implied a
  per-connection budget, and FYERS allows only one Data Socket instance anyway).

### 0.9 New defects found while verifying the official docs

1. **FYERS `map.json` (official SDK asset) has NO NIFTYFPI entry.** `index_dict` contains
   `NSE:NIFTY50-INDEX -> "Nifty 50"`, `NSE:NIFTYBANK-INDEX -> "Nifty Bank"`,
   `NSE:MIDCPNIFTY-INDEX -> "NIFTY MID SELECT"`, `NSE:FINNIFTY-INDEX -> "Nifty Fin Service"`,
   `NSE:NIFTYNEXT50-INDEX -> "Nifty Next 50"`, `BSE:SENSEX-INDEX -> "SENSEX"`, `BSE:BANKEX-INDEX`
   ... and **no** NIFTYFPI entry. The SDK's own fallback for a missing entry derives
   `"NIFTYFPI150"` from the symbol - an **unverified** topic name. ⇒ The plan must NOT hardcode
   an HSM index topic for NIFTYFPI.
   The `exch_seg_dict` in that file matches our `_EXCH_SEG` exactly (1010 nse_cm, 1011 nse_fo,
   1120 mcx_fo, 1210 bse_cm, 1012 cde_fo, 1211 bse_fo, 1212 bcs_fo, 1020 nse_com) - our segment
   map is confirmed correct.
2. **FYERS index symbols should not occupy HSM slots at all.** Their ticks are already dropped
   (measured multiplier 32 vs 100), so every subscribed index burns one of only 100 Standard
   symbol slots for data we discard. ⇒ FYERS indices are served by the guarded REST poller
   (catalogue-verified symbols), and the FYERS HSM socket carries only equity/option contracts.
   Upstox remains the streaming index source (`indexFF`).
3. **The FYERS catalogue sync misses `BSE_FO`.** `app/instruments.py:33-38` downloads
   `NSE_CM`, `NSE_FO`, `BSE_CM`, `MCX_COM` - the official master set is
   `NSE_CM, NSE_FO, NSE_CD, NSE_COM, BSE_CM, BSE_FO, MCX_COM`. `BSE_FO` (SENSEX options/futures)
   is **missing**, so FYERS cannot serve the BSE F&O leg of the scope; `MCX_COM` is downloaded
   but out of scope. Live catalogue confirms it: fyers `BSE_FO` rows = **0**, upstox `BSE_FO` =
   5,138. ⇒ add `BSE_FO`, drop `MCX_COM` (scope-driven, not a limit).
4. **FYERS index REST symbol for NIFTYFPI** is `NSE:NIFTYFPI150-INDEX` (catalogue row
   `101000000026137`); the code's `NSE:NIFTYFPI-INDEX` does not exist in the FYERS master.

### 0.10 Final on-disk verification (2026-10-21, before saving this plan)

Re-checked every file this plan depends on. **No drift**: no file was modified after the
investigation, HEAD is unchanged, and the pre-existing dirty tree is untouched.

| file | bytes | mtime | sha256[0:12] |
|---|---|---|---|
| app/runtime_paths.py | 3,512 | 2026-10-20 09:05 | 1A5AAF950D74 |
| app/instruments.py | 24,483 | 2026-10-20 13:19 | 32A4EB98E104 |
| app/market_indices.py | 16,580 | 2026-10-20 13:19 | C7D14B3AFEBF |
| app/subscriptions/service.py | 51,253 | 2026-10-20 12:42 | 206CE88AA405 |
| app/condition_alerts.py | 45,703 | 2026-10-20 02:48 | 3D6EF73F25FA |
| app/instrument_identity.py | 10,727 | 2026-10-20 13:01 | 7060500F144E |
| app/canonical_identity.py | 20,822 | 2026-10-20 12:27 | D8CCFA9C72B5 |
| app/fyers_index_source.py | 8,812 | 2026-10-20 12:12 | 29F2E76AD2C2 |
| app/server.py | 69,543 | 2026-10-20 12:26 | C432D8D6A4B8 |
| providers/capacity.py | 2,330 | 2026-10-20 01:22 | ED1AB86F8758 |
| providers/capabilities.py | 3,025 | 2026-10-20 13:17 | 5E4474AA7018 |
| providers/registry.py | 7,980 | 2026-10-20 11:48 | 72A9E9AB397D |
| brokers/fyers/feed.py | 43,743 | 2026-10-20 02:53 | 8DC4C209E5E7 |
| market/quote_coordinator.py | 6,301 | 2026-10-20 01:22 | 84447135CFD5 |
| core/persistence/modules/products.py | 36,531 | 2026-10-20 11:56 | EE9C62FAB66A |
| api/routes.py | 53,005 | 2026-10-20 02:17 | 135EADA3F8AD |
| api/product_routes.py | 74,404 | 2026-10-20 12:13 | 4B5B60385A43 |
| mcp_server/services.py | 1,525 | 2026-10-14 02:02 | 14324E07480C |
| mcp_server/tools/market.py | 11,324 | 2026-10-20 01:46 | 20B6C2CAE917 |

Git: branch `fno-provider-agnostic`, HEAD `3f4b8d9`, 505 changed paths in the working tree
(pre-existing in-flight work - preserved, not touched, not committed by this plan).
The new modules this plan builds on already exist on disk and are **untracked**:
`providers/capacity.py`, `providers/capabilities.py`, `providers/registry.py`,
`market/quote_coordinator.py`, `app/canonical_identity.py`, `app/runtime_paths.py`,
`brokers/fyers/capabilities.py`, `brokers/upstox/capabilities.py`, `brokers/fyers/rest.py`.

Defect re-confirmations on disk (line numbers as of the hashes above):

| defect | location |
|---|---|
| runtime path ignores the configured dir | `app/runtime_paths.py:60` (`return base, "overridden"`) |
| arbitrary app cap | `app/subscriptions/service.py:31` (`MAX_INSTRUMENTS_PER_PROVIDER = 500`) |
| arbitrary provider cap | `providers/capacity.py:25` (`APP_CAP = 500`) |
| raw symbol on the HSM wire | `brokers/fyers/feed.py:470` (`self._hsm_symbols.get(s, s)`) |
| no re-resolution on reconnect | `brokers/fyers/feed.py:781` (`if self._hsm_symbols: return`) |
| NIFTYFPI static keys wrong | `app/market_indices.py:62-63` |
| FYERS `BSE_FO` master missing | `app/instruments.py:33-38` |
| no lease implementation anywhere | grep `lease` = 11 hits, all comments/docstrings |
| quote coordinator used only by the SSE/REST market routes | `api/routes.py` + `app/server.py` only |

**Refinement discovered during this check (it changes Phase B):** the NIFTYFPI defect is **not**
in the index-listing path - `app/market_indices.py` already resolves index provider keys from
the **catalogue** (`_catalog_index_row`, `index_provider_keys`, lines 168-245), and its own
comments (lines 118-120, 385-386) already record that NIFTYFPI is catalogued as
`NSE:NIFTYFPI150-INDEX`. `app/fno_coverage.py:296-303` also uses the catalogue first and falls
back to the static table. The defect is that **`app/subscriptions/service.py` still trusts the
static table** for the keys it asks the feeds to subscribe:
`:211` (migration aliases), `:326-327` (`_index_keys`), `:424` and `:430` (workspace futures /
options), `:689` (ATM spot lookup) - so it would subscribe `NSE_INDEX|Nifty FPI` and
`NSE:NIFTYFPI-INDEX`, neither of which exists in the catalogue.
⇒ Phase B therefore fixes the **subscription key resolution** (catalogue-first, static table only
as a documented fallback) *and* corrects the static entry, instead of hand-patching one string.

### 0.11 What is already good (do not rewrite)

`providers/{capabilities,registry,capacity}.py` (broker-neutral registry + adapter-declared
capabilities), `market/quote_coordinator.py` (freshness-gated selection),
`app/canonical_identity.py` + `app/instrument_identity.py` (canonical identity),
`app/market_indices.py` (catalogue-first index resolution - the pattern Phase B reuses),
`app/fno_coverage.py` (provider-routed coverage plan with `CAPACITY_LIMITED` states, and the
catalogue-first index key lookup at `:296-303`),
`app/fyers_index_source.py` (guarded Fyers REST index fallback), `market/index_registry.py`
(canonical index list).

---

## 1. Boundaries and protected-core change request

Per `AGENTS.md` / `docs/ARCHITECTURE_BOUNDARIES.md` §5, this task touches one protected file.
**Approved by the owner on 2026-10-21 — composition and dependency injection only (§0a).**

| # | Protected file | Why it is required | Dependency forcing it | Files needed | Regression risk | Required verification |
|---|---|---|---|---|---|---|
| P1 | `app/server.py` (STARTUP / composition root) | The new subscription manager, the canonical quote service, and the alert↔lease link must be constructed and injected. `app/server.py` is the single composition root (`_SubscriptionService` at :1120, `_ConditionAlertEngine` at :700, `_QuoteCoordinator` at :607, MCP services at :1540-1584). | `providers.registry` / `market.quote_coordinator` are libraries; nothing else constructs the singletons. | `app/server.py` only — composition lines (construct + inject), no logic | Startup/login regression | `test/test_auth_startup.py`, `test/test_auth_freeze.py`, `test/test_auth_foundation.py`, `test/test_lifespan.py`, `test/test_startup_regression.py`, `test/test_architecture_boundaries.py` |

Explicitly **NOT** touched (protected or red-zone): `app/secrets_store.py`, `brokers/*/auth.py`,
`api/routes.py::build_auth_routes`, `app/auth/*`, `market/models.py`, `market/service.py`,
`market/serialization.py`, `core/persistence/store.py`, `core/persistence/modules/schema.py`,
`sources/registry.py`, `core/runtime.py`, `api/routes.py` auth/settings portions,
`api/product_routes.py` non-market portions, `config.json` contents.

`api/routes.py` (market routes only) and `api/product_routes.py` (market-data routes only) are
red-zone ROUTER edits — verified per affected endpoint only.

Zone declarations per phase are listed in §7.

---

## 2. Phase A — Isolate tests; make catalogue sync safe

### A1. Runtime data-path isolation (fixes the blocking defect)

- `app/runtime_paths.py`
  - Add an explicit per-run override: environment variable `MARKETHUB_DATA_DIR`.
    Precedence: **explicit override > canonical `DATA_ROOT` > configured `data_dir`** (configured
    stays reported as `"overridden"`). The override is reported as source `"override"`.
  - `resolve_runtime_data_dir(configured, *, override=None)` keeps the pure signature for tests.
  - `is_isolated_override()` recognises the override path.
  - `app/server.py` needs **no change**: it already calls `_runtime_db_path(...)` /
    `_runtime_data_dir(...)`, which now honour the env var.
- `test/conftest.py`
  - Session-scoped autouse fixture: create a temp dir
    (`tempfile.mkdtemp(prefix="markethub-test-")`), export `MARKETHUB_DATA_DIR`, and delete it at
    session end. The env var is inherited by the MCP server subprocesses
    (`test/helpers/lifecycle.py` `Popen` inherits `os.environ`).
  - **Guard**: patch `core.persistence.store.EventStore.__init__` so that opening the
    **production** `data/events.db` raises `RuntimeError`. Any test that legitimately needs it
    can opt out explicitly. This makes "tests never touch the live DB" mechanically enforced,
    mirroring the existing fake-token guard in `app/auth/storage.py`.
- `test/test_runtime_paths.py` — update to the new contract (override wins; canonical still the
  default) instead of the current "configured dir is never honoured" assertion.
- New `test/test_data_isolation.py`
  - override honoured; DB + master key land in the isolated dir;
  - the production-DB guard fires when something tries to open `data/events.db`;
  - the live DB file (size + mtime) is unchanged across the session.
- **The live DB is never deleted, reset or rewritten.** Existing rows stay as they are.

### A2. Fail-safe catalogue synchronisation

- `app/instruments.py`
  - `sync_upstox()` / `sync_fyers()`: build the candidate set, then validate **before** any write:
    - parse error / download error ⇒ abort with an explicit error, **no write**;
    - per-segment download failure in `sync_fyers` ⇒ keep the previously stored rows for the
      failed segments (segment-scoped replace instead of provider-wide replace) and report
      `partial: true` with the failed segment names;
    - empty candidate set ⇒ abort (`empty_catalogue`), **no delete**;
    - suspicious shrinkage (candidate rows < X % of the currently stored rows for that provider,
      X configurable, default 50 %) ⇒ abort with `catalogue_shrank` — no silent mass delete.
  - Return a structured result: `{provider, records, parsed, kept, filtered, segments, aborted,
    reason, partial}`.
- `core/persistence/modules/products.py` — add
  `replace_provider_instruments_segmented(conn, provider, records, segments)` (same transaction
  semantics; deletes only the rows of the segments being replaced). No schema change.
- **FYERS master coverage (official master set: `NSE_CM, NSE_FO, NSE_CD, NSE_COM, BSE_CM,
  BSE_FO, MCX_COM`)**: `app/instruments.py:33-38` currently downloads `NSE_CM, NSE_FO, BSE_CM,
  MCX_COM` — `BSE_FO` is **missing** (so FYERS cannot serve the SENSEX BSE F&O leg of the
  scope; live catalogue: fyers `BSE_FO` = 0 rows, upstox = 5,138) and `MCX_COM` is out of
  scope. ⇒ add `BSE_FO`, drop `MCX_COM` (scope-driven). The Upstox master URL already used
  (`assets.upstox.com/market-quote/instruments/exchange/complete.json.gz`) is the official
  "Complete" BOD file.
- Identity refresh after a successful sync: `app/instruments.py` gets an optional
  `identity_registries` hook; `app/server.py` (P1) registers the catalogue + identity registries
  so a successful sync re-binds canonical ids and invalidates the derived read-model caches
  (`market.derivatives_universe` is already epoch-invalidated by `catalog_epoch()`).
- New `test/test_catalogue_sync_safety.py`: partial download, empty response, parse failure,
  rollback (no partial rows), shrink guard, segmented replace keeps other segments, successful
  sync refreshes identity, provider failure does not affect the other provider.

---

## 3. Phase B — Repair FYERS dynamic subscriptions + NIFTYFPI

- `brokers/fyers/feed.py`
  - Split symbol resolution from the connect path:
    `_resolve_wire_symbols(keys, token) -> (resolved: dict[key, wire], unresolved: list[key])`
    calling `POST /data/symbol-token` for **any** subset, caching into `_hsm_symbols`.
  - `add_instruments()` resolves the wire symbols for the new keys **before** sending the frame.
    If a key cannot be resolved it is **not subscribed** and is reported
    (`status()["unresolved_symbols"]`, and the return value stays `int` for compatibility —
    a new `add_instruments_detailed()` returns the full result).
  - `_mutation_frame()` **never** falls back to the raw key: an unresolved symbol raises
    internally, is counted, and is dropped from the frame (no invalid symbol on the wire).
  - Reconnect: `_run_session()` re-resolves the **full** desired set (remove the early
    `if self._hsm_symbols: return`) so newly discovered contracts work after a reconnect.
  - `_emit_tick()` keeps dropping `if|` topics (measured: Fyers HSM index multiplier is 32, not
    100 ⇒ 3.125× error; `docs/audit/2026-10-20-live-validation-evidence.md`). Fyers index
    quotes stay on the guarded REST path.
  - Per-instrument **data-received** evidence: track `last_data_at` per resolved wire symbol so
    the runtime can distinguish "subscribed" from "live data flowing" (feeds must never claim
    liveness from a socket that only connected).
  - Index topics: **no new static symbol is invented.** The official FYERS SDK `map.json`
    (`index_dict`) has **no NIFTYFPI entry** - only the SDK's derived fallback
    `"NIFTYFPI150"`, which is unverified. Since FYERS index ticks are dropped anyway
    (measured multiplier 32 vs 100 => 3.125x error), the correct fix is to **stop subscribing
    index symbols on the FYERS HSM socket**: each one burns a scarce Standard slot (100 per
    user) for data we discard. FYERS indices are served by the guarded REST poller with the
    catalogue-verified symbols; Upstox stays the streaming index source.
    Every other `_INDEX_DICT` entry is confirmed against the official `map.json`
    (Nifty 50 / Nifty Bank / NIFTY MID SELECT / Nifty Fin Service / Nifty Next 50 / SENSEX /
    BANKEX ...), and `exch_seg_dict` matches our `_EXCH_SEG` exactly.
- `app/market_indices.py:62-63` — `fyers_symbol="NSE:NIFTYFPI150-INDEX"`,
  `upstox_key="NSE_INDEX|Nifty FPI 150"` (catalogue-verified; neither current string exists in
  the catalogue). The static table stays a **fallback** only.
- `app/subscriptions/service.py` — **the actual NIFTYFPI defect**: index/workspace keys are
  built from the static table (`:211`, `:326-327`, `:424`, `:430`, `:689`), so the service asks
  the feeds for keys that exist nowhere in the catalogue. Resolution becomes
  **catalogue-first** (`market.index_registry.index_provider_keys(catalog, label)`, the pattern
  `app/fno_coverage.py:296-303` already uses), with the static table only as a documented
  fallback for an index the catalogue does not list. The same catalogue-first rule applies to
  the FYERS index REST symbols (so the poller asks for `NSE:NIFTYFPI150-INDEX`, not
  `NSE:NIFTYFPI-INDEX`).
- New `test/test_fyers_dynamic_subscriptions.py` — asserts the **serialized bytes** of the
  subscribe frame contain `sf|nse_fo|<fyToken>` (and never the raw `NSE_EQ|...` key); asserts
  an unresolvable key is not put on the wire and is reported; asserts reconnect re-resolves.
- New `test/test_index_mapping_catalogue.py` — asserts every canonical index's provider keys
  match catalogue rows (fixture captured read-only from the live catalogue; the fixture is
  committed as data, the live DB is not touched by the test).

---

## 4. Phase C — One capacity-aware subscription manager

### C1. Verified capacity model (`providers/capacity.py`, `providers/capabilities.py`)

- Remove `APP_CAP = 500` and `app/subscriptions/service.py::MAX_INSTRUMENTS_PER_PROVIDER = 500`.
  The application-level cap becomes the **verified provider limit**, not a constant.
- Adapter-declared documented limits stay the source of truth
  (`brokers/upstox/capabilities.py`, `brokers/fyers/capabilities.py`), plus a declared
  `rest_limits` block per provider (FYERS: 5 rps / 50 rmin / 5,000 per day, 50 symbols per
  quotes request, 50 strikes per side per option-chain request; Upstox: 50 rps / 500 rmin /
  2,000 per 30 min per API, 500 instrument keys per quotes/ltp/ohlc/option-greek request).
- **Scope of each limit is modelled explicitly** (official wording):
  - FYERS Data-socket symbols are counted **per user across all connections to that socket**
    (and FYERS allows only **one** Data Socket instance) ⇒ the FYERS budget is an
    **account-wide** number; there is no per-connection budget to multiply.
  - Upstox subscription limits are per user, with an **individual** limit per category and a
    **combined** limit when several categories are used (Full: 2000 individual / 1500 combined).
    MarketHub uses one mode (`full`), so 2000 applies today, but the manager must model the
    combined rule so a second category cannot silently exceed it.
- **Rate-limit safety (FYERS):** exceeding the per-minute limit more than 3 times in a day
  blocks the account for the rest of the day ⇒ the central bucket must pace conservatively,
  treat 429 as a hard stop for that window, and the daily budget (5,000 requests on Standard)
  must be accounted, not assumed. The guarded index poller alone consumes ~1,440 requests/day
  at one batched call per 60 s, so the remaining daily budget is a real constraint that the
  option-chain / history paths must respect.
- **Entitlement** (`providers/capacity.py`): `ProviderEntitlement(provider, tier, source, verified_at)`
  with `source ∈ {verified, declared_default, operator_declared}`.
  - No documented field exposes the tier ⇒ the **declared default tier** is used
    (Fyers Standard 100, Upstox full 2,000) and labelled `declared_default`, never "Prime".
  - The tier may be raised only by (a) explicit operator configuration, or (b) observed broker
    behaviour (e.g. the socket accepts more than the Standard tier) — recorded as `verified`.
  - On a broker "limit exceeded" error the tier is **adapted down** and the downgrade is reported.
  - `Prime` is never assumed.
- **Capacity = a hard upper bound, never a target.** The provider budget is the **verified
  entitlement for the active feed mode** (FYERS Standard ≤ 100; Upstox Full ≤ 2,000). It is a
  ceiling the subscription set may never cross - not a quota to fill. The current
  `effective_budget = cap - max(10 % cap, 50)` (and any replacement percentage/fixed reserve)
  is **removed**: it silently halves Fyers coverage without evidence.
  - A provider-specific headroom is introduced **only** where there is documented evidence that
    the provider requires it; it is declared in the adapter's capability block with its
    justification and is configurable (`config.json`). Default: none.
  - The budget is always clamped to the verified tier: `budget ≤ verified_capacity(mode)`.
  - **The subscribed set is a function of DEMAND only.** No code path may add an instrument
    because capacity is available. The provider budget is consulted solely to decide what to
    *reject* when demand exceeds it.
- **Rate management**: one central token-bucket per provider per REST family
  (`providers/rate.py`), consumed by the provider REST adapters
  (`brokers/fyers/rest.py`, `brokers/upstox/rest.py`) - one budget, no per-module duplicates.
  This is a *request-rate* limit (documented: Fyers 5 rps shared pool; Upstox 50 rps per API per
  user), which is separate from the *subscription-count* limit and must not be conflated with it.
  Rate limiting is applied **only where the provider's API requires it**; no generic background
  polling is introduced by this work. The existing guarded FYERS REST index poller
  (`app/fyers_index_source.py`) keeps its documented 60 s minimum interval, 5 rps bucket and
  market-hours gate - it exists because the FYERS HSM index channel is measurably unusable,
  not as a capacity-filling mechanism.
- Tests: `test/test_provider_capacity.py` updated (it currently pins `APP_CAP == 500` - a stale
  test that asserted the cap we are removing); new `test/test_capacity_entitlement.py`
  (asserts the upper bounds 100 and 2,000, that no implicit reserve exists, and - per the
  acceptance criterion - that the **subscribed set never exceeds the demand set**).

### C2. Lease-based subscription manager (`app/subscriptions/manager.py`, new)

- Lease = `(lease_id, provider, key, canonical_id, priority, owner_kind, owner_id)`.
  `key → ref-count`; a key is subscribed while ref-count > 0.
- **The demand set is the union of the leases. Nothing else is ever subscribed.** There is no
  "fill to capacity" step, no speculative subscription, no background re-subscription of
  instruments nobody asked for.
- Priorities (explicit, ordered):
  1. `REQUIRED_COVERAGE` - the agreed baseline scope: the six NSE F&O index spots (NIFTY,
     BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI) + their nearest two futures,
     SENSEX + its relevant BSE contracts, and the eligible NSE F&O stock spots.
  2. `WORKSPACE` - active F&O workspace (spot + nearest two futures + the focused option window
     the workspace actually shows).
  3. `ALERT` - alert dependencies (the instruments an enabled alert actually evaluates).
  4. `OTHER` - analytics universe, derivatives universe, watchlists.
- **One provider at a time per canonical contract.** The manager routes each canonical contract
  to exactly one provider (first enabled provider by configuration order that can serve it, per
  the locked routing decisions). A second provider subscribes the same contract only if an
  explicit operational requirement is recorded - never by default, never "just in case".
- **A lease means a socket subscription.** Index leases route to the provider that *streams*
  them (Upstox `indexFF`); the FYERS index path is a guarded REST poll, not a subscription, so
  it consumes REST rate budget (and therefore FYERS socket capacity) only where it is the
  selected provider for that index.
- Allocation per provider: leases are admitted in priority order up to the **verified budget**
  (a ceiling, not a target). When the budget is exhausted:
  1. **evict first** - lower-priority leases (priority below the incoming one) are released
     safely and their subscriptions removed, *before* the new lease is admitted, so the broker's
     limit is never exceeded even transiently;
  2. if nothing can be evicted (every remaining lease is protected: required coverage,
     workspace, alert dependency), the incoming lease is **not** admitted and is reported as
     `capacity_limited` **with the exact reason** (budget, current use, what was protected);
  3. eviction never happens silently: the evicted lease's owner is notified through the
     manager's state projection (`capacity_limited` + reason), and the eviction is logged.
  A single oversized request never raises and never aborts the reconciliation of other
  providers.
- Eviction order is deterministic (lowest priority first, then oldest lease).
- Reconciliation: diff desired (leases) vs the feed's current set → batched add/remove.
  A provider that is unavailable keeps its leases in state `provider_unavailable`; the manager
  attempts **rerouting** to another enabled provider that lists the same canonical contract
  (catalogue-driven). If no provider can serve it ⇒ state `unrouted` + reason.
- Feed state transitions (connected / reconnecting / auth_required) trigger an automatic
  re-plan; the manager attaches to the feeds' existing `on_state_change` listener (no change to
  `sources/registry.py` / `core/runtime.py`).
- Per-instrument runtime state surfaced by the manager:
  `connected | subscribed | live | stale | unavailable | capacity_limited | unrouted`,
  each with `provider`, `since`, `last_data_at`, `reason`.
- `app/subscriptions/service.py` keeps ownership of **user preferences** (DB) and stops
  enforcing its own cap; it exposes the desired sets to the manager.
- `app/fno_coverage.py` switches from `reg.budget()` to the manager's allocation result, so the
  coverage view and the live subscriptions share ONE budget.

---

## 5. Phase D — Unify canonical identity and quote selection

- `market/quote_coordinator.py` (canonical layer, no app imports):
  - provider resolution becomes **injected** (`provider_of`), never key-shape guessing;
    the default stays a documented fallback only.
  - selection result gains `provider`, `as_of` (`received_ts`), `state`
    (`live|stale|unavailable`), `candidate_count`, `age_seconds`.
  - **stale-only instruments are retained**: `stale: true`, `live: false`, `as_of` present —
    never presented as live, never silently dropped.
  - `select_quote()` gains a `keep_stale` mode for display paths; the *live* selection path
    keeps excluding stale values.
- Canonical read service `app/quote_service.py` (new, app layer):
  wraps the coordinator with the **catalogue-backed** provider resolver
  (`registry.provider_for_key(key, catalog=...)` — catalogue first, key style only as fallback)
  and exposes: `get(canonical_id)`, `bulk(canonical_ids)`, `all()`, `depth(canonical_id)`,
  `indices()`, `status()`.
  Used by: REST single/bulk quote, REST index list, SSE market stream, MCP quote/depth,
  analytics reads and alert evaluation.
- `api/routes.py` (market routes only): the single-quote route and the depth route switch from
  `market_service.get_quote()` to the quote service (canonical selection + stale metadata).
- `app/market_indices.py`: `resolve_indices()` reads through the quote service
  (it currently uses `market_service.get_quote_now`).
- `mcp_server/services.py` + `mcp_server/tools/market.py`: the Services container gains a
  `quote_service` field; `market_quote` / `market_depth` resolve the canonical instrument via
  the identity registry and return the **same** payload shape as REST
  (provider, as_of, stale/live, provenance).
- `app/instrument_identity.py`: the canonical registry stops treating the provider-native
  **storage key** as an identity alias for resolution purposes (storage key stays the store key;
  canonical id is the only identity). Canonical reference parsing
  (`app/canonical_identity.py::resolve_provider_reference`) is repaired using registry-backed
  resolution — no fabricated provider keys.

---

## 6. Phase E — Alerts that genuinely monitor their dependencies

- `app/condition_alerts.py` (ALERTS zone, generic engine preserved):
  - **Leases**: on load/reload, each enabled alert's dependency keys
    (`quote:<canonical_id>`, `analytics:<canonical_id>:<expiry>`) are resolved to provider keys
    and leased through the manager (owner `alert:<alert_id>`). On delete/disable the leases are
    released and alert-owned analytics dependencies unregistered.
  - **Absolute freshness policy**: `max_quote_age_seconds` (configurable, default 120 s) — a
    quote older than the policy never triggers; the leaf result becomes UNKNOWN, not TRUE.
  - **Monitoring truth**: an alert reports `active` only when its dependencies are subscribed
    **and** fresh data has arrived for them; otherwise `waiting` (data not yet flowing) or
    `blocked` (route/feed/capacity unavailable) with the reason. The alert list/detail surfaces
    that state (WebUI + MCP).
  - **No cross-alert state resets**: `reload()` rebuilds only the alerts that changed; other
    alerts' `_state` / `_last_values` / `_dep_last_values` are preserved.
  - **Analytics-only alerts**: verified to run; if the analytics chain cannot be refreshed the
    alert reports `waiting`/`blocked`, never a trigger.
  - **Persistence**: the condition tree (with `instrument.expiry`) is already persisted;
    dependency + expiry metadata is written into the existing `metadata_json`
    (no schema migration, no `core/persistence/store.py` change) and re-read on restart.
- `api/ai_alert_routes.py`, `api/product_routes.py` (alert routes only),
  `mcp_server/tools/condition_alerts.py`: expose the monitoring state; replace fabricated
  broker-native instrument keys with registry-resolved canonical instruments.

---

## 7. Phase F — WebUI / MCP parity

- Both interfaces read through the **same** `app/quote_service.py` and the same identity
  registry; the MCP container gets the quote service (P1 wiring).
- Every payload carries: `canonical_id`, `provider`, `as_of`, `age_seconds`,
  `state (live|stale|unavailable)`, `source (provider)`.
- Backend "live" is derived from **feed state + data recency**, never from an open SSE
  connection. The SSE handler stops implying liveness by connection.
- SSE payloads are validated against a schema before broadcast (frontend
  `frontend/src/api/schemas.ts` gains the matching fields; the SSE consumer validates too).
- `frontend/src/features/...` shows stale/last-known values with `as_of` and marks
  capacity-limited instruments explicitly.

---

## 8. Phase G — Proof (isolated tests)

New test files (all isolated, deterministic provider fixtures — no live broker):

1. `test/test_data_isolation.py` — no test can open the production DB; live DB byte-identical
   after the session.
2. `test/test_catalogue_sync_safety.py` — partial / empty / parse failure / rollback / shrink
   guard / segmented replace / successful sync + identity refresh.
3. `test/test_fyers_dynamic_subscriptions.py` — **serialized frame bytes** for a dynamically
   added instrument; no raw symbol on the wire; reconnect re-resolution.
4. `test/test_index_mapping_catalogue.py` — index keys match catalogue evidence (incl. NIFTYFPI)
   and the official FYERS SDK `map.json` entries; the FYERS REST index symbol exists in the
   FYERS master; **no invented HSM index topic** is introduced; the **subscription** path
   resolves index keys from the catalogue (never from a static string that the catalogue does
   not contain).
5. `test/test_capacity_entitlement.py` — documented limits as **upper bounds**, tier resolution,
   no implicit reserve, removal of the 500 caps, adaptive downgrade, and the acceptance
   criterion: **the subscribed set is a subset of the demand set** (no instrument subscribed
   merely because capacity was free). Also pins the official scoping: FYERS symbol budget is
   **per user** (not per connection), Upstox `full` = 2000 individual / 1500 combined.
6. `test/test_subscription_manager.py` — lease ref-counts, priority allocation, capacity
   exhaustion reporting, eviction-before-admission, provider disconnect/reconnect/reroute,
   shared leases, release, and **one provider at a time per canonical contract**.
7. `test/test_concurrent_providers.py` — both providers live with *different* coverage (no
   duplicated contract); failover reroutes a canonical contract and the selection follows.
8. `test/test_provider_request_budget.py` — REST batching respects the official batch sizes
   (FYERS quotes ≤ 50 symbols, Upstox quotes ≤ 500 keys), the FYERS per-second/minute/day
   buckets are shared across all Data-API callers, and a 429 stops further calls in that window
   (the "3 per-minute breaches = blocked for the day" rule is never approached).
9. `test/test_fyers_symbol_wire.py` — the FYERS HSM subscribe frame contains **no index
   topic** (indices are not socket subscriptions) and **no raw API symbol**, only
   `sf|<segment>|<exchange_token>` topics resolved from the symbol-token API.
10. `test/test_quote_selection_parity.py` — identical canonical result from the REST route, the
    MCP tool and the coordinator; stale-only instruments visible but never "live".
11. `test/test_alert_monitoring.py` — stale-only instrument never triggers; blocked/waiting
    states; lease release on delete; restart reload; analytics-only alert.
12. `test/test_catalogue_master_coverage.py` — the FYERS master set includes `BSE_FO` and the
    segment filter keeps SENSEX BSE contracts; a failed segment never deletes the other
    segments' rows.

### Commands

```
python -m pytest test/test_data_isolation.py -q
python -m pytest test/test_catalogue_sync_safety.py test/test_fyers_dynamic_subscriptions.py -q
python -m pytest test/test_capacity_entitlement.py test/test_subscription_manager.py -q
python -m pytest test/test_quote_selection_parity.py test/test_alert_monitoring.py -q
python -m pytest test/test_architecture_boundaries.py test/test_auth_freeze.py test/test_auth_startup.py -q
python -m pytest -q --tb=short            # full suite
python test/run_all.py --group all --timeout 600
python -m ruff check .
```

---

## 9. Zone declarations (AGENTS.md §"mandatory declaration")

| Phase | TASK ZONE | Allowed change surface | Must not touch |
|---|---|---|---|
| A | INSTRUMENT/CATALOG + PERSISTENCE(products) | `app/runtime_paths.py`, `app/instruments.py`, `core/persistence/modules/products.py`, `test/conftest.py`, `test/helpers/lifecycle.py`, `test/test_runtime_paths.py`, new test files | `core/persistence/store.py`, `core/persistence/modules/schema.py`, `app/secrets_store.py` |
| B | FYERS ADAPTER | `brokers/fyers/feed.py`, `app/market_indices.py`, new test files | `brokers/fyers/auth.py`, `market/models.py`, `market/service.py` |
| C | PROVIDER/CAPACITY + BROKER RUNTIME (adapter only) | `providers/*`, `brokers/*/capabilities.py`, `brokers/*/rest.py` (rate bucket only), `app/subscriptions/*`, `app/fno_coverage.py`, new test files | `sources/registry.py`, `core/runtime.py`, `app/server.py` (except P1) |
| D | CANONICAL MARKET DATA (read side) | `market/quote_coordinator.py`, `app/quote_service.py`, `app/instrument_identity.py`, `app/canonical_identity.py`, `api/routes.py` (market routes), `mcp_server/services.py`, `mcp_server/tools/market.py`, `app/market_indices.py` | `market/models.py`, `market/service.py`, `market/serialization.py` |
| E | ALERTS | `app/condition_alerts.py`, `core/persistence/modules/condition_alerts.py` (metadata only), `api/ai_alert_routes.py`, `mcp_server/tools/condition_alerts.py` | `core/persistence/store.py`, `core/persistence/modules/schema.py` |
| F | REST API / MCP / WEBUI | `api/routes.py` (market), `api/product_routes.py` (market-data + alert routes), `mcp_server/*`, `frontend/src/**` | `api/routes.py` auth/settings sections, `api/product_routes.py::build_fyers_auth_routes` |
| G | TEST | `test/**` | — |
| all | **STARTUP (P1 — APPROVED, composition/injection only)** | `app/server.py` construction + injection lines only; all logic lives in focused modules (`app/subscriptions/*`, `providers/*`, `market/quote_coordinator.py`, `app/quote_service.py`, `app/condition_alerts.py`) | auth/login/restore blocks, credential handling, source wiring, `market/models.py`, `market/service.py`, `core/persistence/store.py`, `core/persistence/modules/schema.py` |

**Modularity requirement (owner):** no giant modules. Ownership stays separated —
provider adapters (`brokers/*`), capacity/entitlement (`providers/capacity.py`,
`providers/rate.py`), subscription leases (`app/subscriptions/manager.py`),
canonical identity (`app/canonical_identity.py`, `app/instrument_identity.py`),
quote selection (`market/quote_coordinator.py`, `app/quote_service.py`),
alert evaluation (`app/condition_alerts.py`). `app/server.py` only wires them together.

---

## 10. Honest limits of this plan (stated up front)

1. **Full F&O coverage on FYERS Standard is arithmetically impossible.** The official FYERS
   Data-socket limit is 100 symbols on the Standard plan, counted **per user across all
   connections** to that socket (and FYERS allows only one Data Socket instance at a time).
   "All eligible NSE F&O stocks and their relevant contracts" plus option windows is orders of
   magnitude above that. Because subscriptions are **demand-driven**, the practical consequence
   is narrower and must be stated plainly: the baseline scope that can be served by FYERS
   Standard is small (index spots + nearest futures + a limited set of stock contracts), the
   remainder of the demand set is carried by Upstox (<= 2,000 in `full` mode, 1500 if other
   categories are ever combined), and any demand that still does not fit a provider's verified
   capacity is reported as **capacity-limited with the exact reason** - never silently dropped,
   never labelled live. **Unused capacity on either provider is an accepted outcome**, not a
   defect: the goal is correct coverage, fresh data and reliable failover, not
   subscription-count utilisation. If the account is later verified as Prime (5,000), the same
   code path uses the verified tier.
2. **Entitlement cannot be read from any documented API.** It is therefore *verified* by
   observation (broker error / accepted volume) or operator configuration, and the plan reports
   `declared_default` vs `verified` honestly. The Prime tier will **not** be claimed.
3. **FYERS index streaming stays off, and FYERS indices are not socket subscriptions.** The
   measured HSM index multiplier (32, not 100) makes the HSM index channel unusable, and the
   official SDK `map.json` has no NIFTYFPI index topic at all. FYERS index quotes therefore come
   from the guarded REST poller (60 s, market-hours aware, 5 rps shared bucket) using
   catalogue-verified symbols; Upstox remains the streaming index source. Index symbols are
   removed from the FYERS socket subscription set (they would waste scarce Standard slots for
   data we discard).
4. **FYERS REST budget is a real, daily constraint.** Standard allows 5,000 Data-API requests
   per day and blocks the account for the rest of the day after 3 per-minute breaches. One
   batched index poll per 60 s already costs ~1,440/day, so option-chain and history refreshes
   must share that budget through the central bucket - and a 429 must stop traffic for that
   window rather than retrying into a block.
5. **FYERS BSE F&O leg needs the `BSE_FO` master.** Until that master is synced, the SENSEX
   F&O leg of the scope is served by Upstox only (catalogue evidence: 0 FYERS `BSE_FO` rows vs
   5,138 Upstox rows today).
6. **Simulated failover is not live failover.** All failover/dual-provider evidence will be
   produced with deterministic provider fixtures. Live-broker failover is only claimed if it is
   actually performed and evidenced.
7. The live DB keeps whatever previous test runs wrote into it; nothing is deleted without
   owner authorization. The isolation fix prevents *further* accidental writes.
8. **No contract is subscribed on two providers at once.** Concurrent operation means the two
   providers serve *different* canonical contracts simultaneously; a contract is duplicated
   across providers only if an explicit operational requirement is recorded.

---

## 11. Sequencing / commits

One commit per phase, each containing **only** the files that phase touches (the pre-existing
dirty tree stays out of my commits):

1. `fix(test): isolate runtime data path from the production database`
2. `fix(catalog): fail-safe, segment-scoped instrument synchronisation (+ BSE_FO master)`
3. `fix(fyers): resolve wire symbols for dynamically added instruments`
4. `fix(indices): correct the NIFTYFPI provider mapping; keep FYERS indices off the socket`
5. `feat(providers): verified capacity + entitlement model, no arbitrary caps, official limits`
6. `feat(subscriptions): lease-based, priority-aware subscription manager`
7. `feat(market): unified canonical quote service with provenance and staleness`
8. `feat(alerts): dependency leases, absolute freshness, truthful monitoring state`
9. `feat(api,mcp): WebUI/MCP parity on the shared quote service`
10. `test: isolated coverage for catalogue sync, FYERS dynamic subs, capacity, failover, alerts`

### Sources consulted (2026-10-21, official)

- Upstox: Market Data Feed V3 (limits, modes), Rate Limits, Instruments (BOD files),
  Full Market Quotes V3 / LTP / OHLC / Option Greeks (500-key batch), Market Data Feed V3 proto.
- FYERS: Plans & Limits, Data Socket Overview, Indices Update, Option Chain, plus the official
  SDK asset `FyersWebsocket/map.json` (`index_dict`, `exch_seg_dict`, field orders).


---

## 12. Implementation status (2026-10-21)

Implemented on `fno-provider-agnostic` (HEAD `3f4b8d9`). Changes are **uncommitted**:
the tree already carried ~500 modified paths from earlier sessions, and several of the files
this work touches (`app/server.py`, `app/condition_alerts.py`, `app/instruments.py`,
`api/routes.py`, ...) were already modified, so a phase-only commit could not be made
without sweeping unrelated work in. Nothing was reset, stashed or reverted.

### New modules

| module | responsibility |
|---|---|
| `app/catalog_sync.py` | fail-safe, segment-scoped catalogue synchronisation + completeness invariants |
| `app/quote_service.py` | the single canonical quote read path (selection, freshness, provenance) |
| `app/subscriptions/manager.py` | lease registry, allocation against the verified ceiling, runtime states, failover |
| `app/subscriptions/routing.py` | deterministic provider routing (unique coverage first, then configuration order) |
| `brokers/fyers/hsm_symbols.py` | FYERS symbol -> HSM wire topic, generated from the official SDK assets |
| `providers/rate.py` | central per-provider request budgets (per second / minute / day) |

### Root causes fixed

1. **Test/runtime data isolation** - `app/runtime_paths.py` ignored the configured
   `data_dir`; the suite opened the live `data/events.db`. Now `MARKETHUB_DATA_DIR`
   overrides the runtime and a guard makes opening the production DB raise.
2. **Catalogue sync could destroy valid data** - an empty/partial master deleted the
   provider's rows. Now validated (per-segment completeness + family invariants), written
   segment-scoped, and a failed master keeps its previous rows.
3. **FYERS HSM decoding was off by two bytes** - the official SDK skips two bytes before
   the `multiplier`; MarketHub did not, so every scale was wrong (the "index multiplier 32"
   symptom). Fixed for all topic types.
4. **FYERS dynamic subscriptions sent raw API identifiers** on the wire; now every symbol is
   resolved to a valid HSM topic before subscribing, unresolved instruments are reported and
   never sent, and a reconnect re-resolves.
5. **Arbitrary capacity caps** - `APP_CAP = 500`, `MAX_INSTRUMENTS_PER_PROVIDER = 500` and the
   `max(10%, 50)` reserve are gone; the budget is the provider's verified tier limit.
6. **Every canonical index was subscribed on BOTH providers**; now one provider per contract.
7. **The MCP/REST/UI read paths diverged**; one quote service now serves all of them.
8. **Alerts could trigger on stale data and had no dependencies**; absolute freshness, leases
   and honest `active/waiting/blocked` states are in place.

### Verified provider limits (official, 2026-10-21)

* Upstox V3 feed: LTPC 5,000 / Option Greeks 3,000 / **Full 2,000** / Full D30 50
  (individual); combined 2,000 / 2,000 / **1,500** / 1,500; 2 connections per user (5 with
  Plus); REST 50 rps / 500 rmin / 2,000 per 30 min per API; 500 instrument keys per request.
* FYERS: Data socket **100** symbols (Standard) / 5,000 (Prime), counted per user across
  connections, one Data Socket instance at a time; TBT 1 / 15; Data APIs 5 rps / 50 rmin /
  5,000 per day (Standard); 50 symbols per quotes request; option chain at most 50 strikes
  per side.
* The account tier is **not verifiable** from any documented field: the default tier is used
  and reported as `declared_default`; Prime is never assumed.


---

## 13. Remediation status (2026-10-21, post-review)

Corrections made after an independent review of this plan:

1. **The FYERS decode IS now market-verified (live capture).** The
   earlier "55,488.10 matches the published NIFTY value" claim was
   withdrawn as circular (the scale had been inferred from the number
   looking plausible). A live capture then closed it: the raw HSM bytes
   and the FYERS REST Quotes value for the SAME instrument, seconds
   apart, are recorded in `test/fixtures/` and replayed by
   `test/test_fyers_decoder.py`:

   | instrument | HSM topic | skipped bytes | multiplier/precision | decoded | reference | delta |
   |---|---|---|---|---|---|---|
   | NSE:NIFTY50-INDEX | `if\|nse_cm\|Nifty 50` | `0x001c` (28) | 1 / 2 | 22,811.35 | 22,811.35 | **0.0000** |
   | NSE:SBIN-EQ | `sf\|nse_cm\|3045` | `0x001b` (27) | 1 / 2 | 963.00 | 963.00 | **0.0000** |
   | NSE:NIFTY26OCT19500CE | `sf\|nse_fo\|51190` | `0x0026` (38) | 1 / 2 | 3,411.90 | 3,411.90 | **0.0000** |

   The skipped bytes differ per frame (28/27/38) and are NOT the
   multiplier - reading them as one is exactly the defect that produced
   the old wrong scale. The decoder's byte layout was additionally
   verified against an independent re-implementation of the official SDK
   algorithm for the `sf`, `if` and `dp` families.
2. **The jump circuit breaker is a runaway guard, not evidence.** It
   cannot validate the first tick of an instrument, and it is documented
   as such in the feed module.
3. **`core/persistence/store.py` is no longer modified by this work.**
   The segment-scoped catalogue write moved into the permitted
   persistence module (`core/persistence/modules/products.py`,
   `replace_provider_instruments_segmented_at`, addressed through the
   store's PUBLIC `db_path`); the catalogue's own revision counter
   invalidates the derived read models.
4. **The REST request budgets are now enforced on the real call
   paths** (FYERS quotes/depth/history/option-chain/market-status, the
   symbol-token resolution, Upstox market data/history/analytics), with
   the documented batch sizes (FYERS 50 symbols per quotes request),
   a shared per-provider accounting state and a 429 that stops the
   provider instead of retrying into a restriction.
5. **Provider allocation is capacity-aware and demand-driven**: unique
   coverage wins, otherwise the provider with the most remaining
   verified capacity takes the shared contract (ties: configuration
   order), one provider serves a contract at a time, and an unavailable
   or full provider triggers a reroute that preserves the leases. With
   the current entitlement Upstox carries the shared baseline and FYERS
   is **standby** for it (plus the contracts only FYERS lists) - that is
   standby, not ordinary concurrent coverage.


---

## 14. Final allocation policy and REST quota scopes (2026-10-21)

### 14.1 Allocation objective

**Objective:** both providers must carry *useful* work when the demand,
the instruments they list, their verified capacity and their runtime
health justify it - and neither may be given an instrument nobody asked
for.

The previous policy ("the provider with the most remaining capacity wins")
systematically handed every shared instrument to the larger provider:
with Upstox Full (2,000) and FYERS Standard (100) the FYERS socket stayed
idle, so the second broker contributed nothing in normal operation.

The policy is now **projected-utilization balanced**:

1. a provider must LIST the contract (catalogue) and be able to STREAM it
   (adapter capability - e.g. FYERS has no official HSM topic for
   NIFTYFPI, so that index is not subscribed through the socket);
2. provider-EXCLUSIVE contracts go to that provider;
3. a shared contract goes to the candidate with the lowest PROJECTED
   UTILIZATION (assigned / verified capacity); ties break by the
   configuration order;
4. a contract is served by ONE provider at a time - never duplicated;
5. only demanded instruments are ever subscribed (the subscribed set is a
   subset of the demand set);
6. priority is preserved: required baseline -> workspace -> alert ->
   optional, and the higher priority is admitted first;
7. an unavailable or full provider hands the contract to an eligible
   provider with room; if there is none, the demand is REPORTED
   (capacity_limited / provider_unavailable), never silently dropped.

Why this is not a quota: the split is a *consequence* of the documented
capacities and the actual demand. With equal capacities the providers are
used equally; with 2,000 vs 100 slots the larger one takes proportionally
more. No percentage, no reservation, no speculative subscription is
configured anywhere.

### 14.2 REST quota scopes

| provider | bucket (documented scope) | documented limit | window | scope | callers covered |
|---|---|---|---|---|---|
| FYERS | `data` | 5 rps / 50 min / 5,000 day | 1 s / 60 s / 24 h | shared Data-API pool per user | quotes, depth, history, option chain, market status, symbol-token resolution, the index fallback |
| FYERS | `transaction` | 10 rps / 200 min / 100,000 day | 1 s / 60 s / 24 h | separate documented pool | (not used by MarketHub) |
| Upstox | `market_quote` | 50 rps / 500 min / 2,000 | 1 s / 60 s / 30 min | per API, per user | /v3/market-quote/* (quotes, ltp, ohlc, option-greek) |
| Upstox | `historical` | 50 rps / 500 min / 2,000 | 1 s / 60 s / 30 min | per API, per user | /v3/historical-candle/* |
| Upstox | `option_chain` | 50 rps / 500 min / 2,000 | 1 s / 60 s / 30 min | per API, per user | /v2/option/chain |
| Upstox | `market_info` | 50 rps / 500 min / 2,000 | 1 s / 60 s / 30 min | per API, per user | /v2/market/* (OI, PCR, FII/DII, news) |
| Upstox | `auth` | 1 rps / 10 min / 60 | 1 s / 60 s / 30 min | per API, per user (the stricter TOTP row) | /v2/totp-login/authorization/token, /v2/login/validate |
| Upstox | `auth_code` | 50 rps / 500 min / 2,000 | 1 s / 60 s / 30 min | per API, per user (the OAuth code exchange is its own documented family) | /v2/login/authorization/token |
| Upstox | `feed_authorize` | 50 rps / 500 min / 2,000 | 1 s / 60 s / 30 min | per API, per user (the feed authorization is its own API, not the login quota) | /v3/feed/market-data-feed/authorize |

> **Correction (2026-10-22, verified against the live 97-path API spec).**
> The previous row billed `/v2/authorization/*` and `/v2/feed/authorize` to
> the strict `auth` bucket. Neither path exists: the token endpoints are
> `/v2/login/authorization/token` (OAuth code exchange),
> `/v2/totp-login/authorization/token` (TOTP) and `/v2/login/validate`
> (PIN), and the feed authorization is
> `/v3/feed/market-data-feed/authorize`. The strict row therefore covers
> the interactive-login family only; the code exchange and the feed
> authorization each carry the standard row in their own budget, so the
> feed authorization never sits in the login quota and the login quota is
> never bypassed by switching endpoints.

When a bucket's window is exhausted the request is refused BEFORE it
reaches the network and surfaces as the provider's rate-limit error; a
429 response blocks that bucket (FYERS: the rest of the day after three
per-minute breaches) so the client never produces a retry burst.
