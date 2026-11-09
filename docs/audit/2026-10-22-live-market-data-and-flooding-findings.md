# Live market data and runtime flooding — findings and fixes (2026-10-22)

Scope: the two runtime defects reported from the live run of
2026-10-22 00:35–00:48 IST — *no market data reaches the app* and *the app
floods requests and errors* — plus the minimum Upstox authentication repair
that the same run exposed.

Everything below was measured on the real application against the real
catalogue, the real credential store and the real live database
(`data/events.db`, schema v23). No broker credentials were printed, and the
live database was never reset, cleaned or rewritten.

---

## 1. Verdict

| # | Reported symptom | Root cause | State |
|---|---|---|---|
| A | No market data ever reaches the app | `SubscriptionManager.apply_demand()` **did not exist**; the two startup call sites raised `AttributeError`, were swallowed by a broad `except Exception`, and the demand never became leases | **Fixed** — the demand is applied at startup: `fno coverage restored at startup: 662 contract(s) routed` (was `3 contract(s) routed` + `fno coverage restore failed`) |
| B | The demand was unusable even with `apply_demand` | The demand records carried **no canonical identity**, and the catalogue had **no canonical-id read model**, so the manager could not see the second provider's offer for the same contract and could not fail over | **Fixed** — catalogue `find_canonical_rows()` + manager-side canonical resolution; the demand now routes to the provider that *can* serve it (99 FYERS instruments applied at startup, Upstox has no session) |
| C | FYERS symbol rejected by the broker (`NSE:BANKNIFTY-INDEX`) | The live source config carried a **non-catalogue symbol**; the catalogue-exact FYERS symbol is `NSE:NIFTYBANK-INDEX` | **Fixed at the source** — the subscribed set is now a function of the demand (catalogue-exact symbols), not of the stale config list |
| D | 9 failed `news_source_health` writes per refresh cycle | Schema drift: the live table has `updated_at TEXT NOT NULL` (house style) but the module's DDL omitted the column and the INSERT never supplied it | **Fixed** — DDL + INSERT aligned, plus a guarded self-heal for tables created by the regressed DDL |
| E | `POST /api/market/fno/view` storm (~100 requests in 4 s) | The **served bundle was stale** (built 2026-10-20 01:09; the effect fix was written 10:30) **and the build was blocked** by 4 TypeScript errors in the new test files | **Fixed** — separate app/test typecheck projects, test doubles corrected, `frontend/dist` rebuilt; measured **1 POST** for the same interaction |
| F | Upstox: routine status polling could destroy a live session | The frozen contract was broken by an in-flight change that made `status()` a pure projection (4 frozen tests failed), and the legacy `broker_rejected_token` signal was dropped | **Fixed** — the frozen contract is restored (a genuine rejection still rolls the session back) but the invalidation is **token-scoped and idempotent** |
| G | Upstox quota accounting | The bucket map was keyed to **non-existent endpoints** (`/v2/authorization/*`, `/v2/feed/authorize`), so every real call fell into the default bucket and the strict login row protected nothing | **Fixed** — the map now uses the real endpoints and the documented per-API scopes |

**Still blocking live quotes** (environment/credential, not code): Upstox has
no stored session at all, and the stored FYERS session is now refused by the
broker (401). See §6.

### 1.1 Live verification after the operator's FYERS login (2026-10-22 11:33–11:45 IST)

The operator logged in through FYERS; the session was then verified against the
**running** application (not against a fixture).

| stage | evidence |
|---|---|
| session | `fyers access token restored (restored_access_token)`; `/api/diagnostics` → `auth.fyers.auth_state = authenticated`, `access_token_active = true`, `login_required = false` |
| running process uses the NEW session | the feed's symbol-token call 401'd at startup with the *previous* token and succeeded only after the login-triggered restart: `11:18:10 symbol-token auth failed (401)` → `11:19:04 resolved 98 instrument(s) to HSM wire topics` |
| symbol resolution | `resolved 8 … (0 unresolved)` → `resolved 88 … (0 unresolved)`; **0 unresolved** after the config correction (below) — the broker is never asked for a symbol it does not have |
| subscription | FYERS feed `state = streaming`, `configured_instruments = 98` (the demand-derived set, not the 2-key config seed) |
| tick → decoder | `/api/market/quotes` → 100 quotes, **all `selected_from: fyers`**, e.g. `NSE:NIFTY26OCTFUT` ltp 22 789.3, OI 15 318 645, `received_ts 2026-10-22T05:55:33Z`; `NSE:NIFTYBANK-INDEX` ltp 54 831.4 |
| quote store | canonical identity present (`NSE_FO|48704`, `NSE_EQ|INE003A01024`, `NSE:NIFTYBANK-INDEX`), `stale = false` |
| REST | `GET /api/market/quotes` (200), `GET /api/market/indices` (200, every index with a live `quote` block) |
| SSE | `GET /api/market/stream` → 200 `text/event-stream`, live `event: quote` frames with fresh `received_ts` |
| WebUI | the running dashboard shows **● Live** with the FYERS values (NIFTY26OCTFUT 22 760.00 +16.70, NBCC-EQ 70.81, …) **and the index cards** (NIFTY 22 728.45 −0.36 %, BANKNIFTY 54 854.65 −0.22 %, INDIA VIX 15.96 +1.79 %) |

Two further defects were found and fixed during this verification:

* **the FYERS symbol rejection** — the live source config carried
  `NSE:BANKNIFTY-INDEX`, which is not a FYERS symbol (the catalogue-exact
  identifier is `NSE:NIFTYBANK-INDEX`); the broker rejected it on every
  reconnect (`1 instrument(s) could not be resolved: NSE:BANKNIFTY-INDEX`).
  Corrected in `config.json` (SETTINGS/CONFIG zone) → **0 unresolved** now.
* **the index cards showed `—`** although live index quotes existed: the
  quote store keys a quote by the **serving provider's native token**
  (`NSE:NIFTY50-INDEX`) while the dashboard card joined by the *subscription*
  key (`NSE_INDEX|Nifty 50`) and by the canonical id
  (`NSE:INDEX:NIFTY`), which the REST index poller does not publish. The
  card now falls back to the other provider's native identifier
  (`frontend/src/features/dashboard/components/IndicesWidget.tsx`).
* **two appliers fought each other**: the API-triggered apply
  (`POST /api/market/fno/view` → `subscriptions.reconcile()`) had its own
  view of the demand (configuration order → Upstox) and therefore
  *unsubscribed* the live FYERS instruments (98 → 2) and pushed 704 keys
  into a feed with no session. The subscribed set now has ONE owner: the
  lease manager. The service exposes the demand to it and applies nothing
  itself (`app/subscriptions/service.py` → `app/subscriptions/manager.py`).
  Measured after the fix: the FYERS feed keeps 97–98 instruments, the
  Upstox feed keeps its own 2, and the apply reports the truth
  (`fyers: desired 100, added 44, removed 45, budget 100,
  capacity_limited 45`; `upstox: desired 0, capacity_limited 574`).
* **the F&O option chain failed with `ProviderMarketDataError`** because the
  chain was addressed to Upstox (configuration order) although only FYERS
  has a session. The chain is now served by the provider that can
  (`app/market_data.py`): the 97 `fno chain snapshot unavailable` lines of
  the previous run are gone.

---

## 2. What was run

* `python -m app.server` on `127.0.0.1:7099` against the live data directory
  (2026-10-22 09:12–09:21 IST), plus the live WebUI in a browser.
* `python -m pytest test -q` (full suite), focused suites for every touched
  area, `ruff check` on the touched paths, `bun run typecheck` / `bun run build`
  in `frontend/`.
* Direct, credential-safe probes of the two FYERS data endpoints (symbol-token,
  quotes) and the FYERS refresh endpoint.

---

## 3. Root causes, with the evidence that proves each

### R1 — The demand never became leases (the first broken stage)

`app/server.py` calls the demand in two places at startup:

```
1380:  _subscription_manager.apply_demand(_subscription_service.lease_requests())
1526:  _subscription_manager.apply_demand(_subscription_service.lease_requests())
```

`SubscriptionManager` (`app/subscriptions/manager.py`) had `plan()`, `acquire()`,
`reconcile()` and `release_owner()` — but **no `apply_demand`**. Every call site
raised `AttributeError`, which the surrounding `except Exception` turned into
the log lines that dominated the live run:

```
2026-10-22 00:35..00:48  WARNING event_server: subscription reconcile failed  (x87)
2026-10-22 00:35..00:48  WARNING event_server: fno coverage restore failed
```

`test/test_startup_regression.py` passed anyway because its fake manager
*defines* `apply_demand` — the test asserted a fiction, not the contract.

**After the fix** (same call site, live run):

```
2026-10-22 09:12:59,571 INFO event_server: fno coverage restored at startup: 662 contract(s) routed
```

no `fno coverage restore failed`, and the FYERS feed's desired set became the
demand-derived one:

```
/api/diagnostics -> sources[fyers].configured_instruments = 99   (was 2: the stale config pair)
```

### R2 — The demand could not be routed (two broken links in one hop)

`SubscriptionService.lease_requests()` emits `canonical_id: contract.get("canonical_id")`,
but the resolved contracts never carried one — every one of the 12 demand
records read `"canonical_id": null` at runtime.

`app/subscriptions/routing.py::catalogue_offers()` looks the contract up with
`catalog.find_canonical_rows(canonical_id)` and falls back to
`catalog.search(canonical_id=...)`. `InstrumentCatalog` had **no
`find_canonical_rows`**, and `search_instruments()` has no `canonical_id`
parameter, so the fallback raised `TypeError`, was swallowed, and returned
`[]` — the manager therefore had **no offers at all** and could never fail
over to a provider that can serve the contract.

**Fixed** by:

* `app/instruments.py` — new `InstrumentCatalog.find_canonical_rows(canonical_id)`:
  the canonical id is composed of provider-independent parts
  (`EXCHANGE:EQUITY:ISIN`, `EXCHANGE:INDEX:SYMBOL`,
  `EXCHANGE:FUTURE:U:EXPIRY`, `EXCHANGE:OPTION:U:EXPIRY:STRIKE:TYPE`); the
  parts are matched exactly and each candidate row is kept only when its
  *derived* canonical id equals the requested one, so a near-miss can never be
  routed as the same contract.
* `core/persistence/modules/products.py` — `search_instruments()` gained an
  `isin` filter (the canonical identity of a cash instrument).
* `app/subscriptions/manager.py` — `apply_demand()` resolves the canonical id
  of a provider-native key through the catalogue, so a demand that names no
  canonical id is still routable.

### R3 — The demand pointed at a provider that cannot serve it

The service's provider choice follows the *configured* order (`upstox`,
`fyers`), not runtime availability, so all 12 demand records were routed to
Upstox — which has no session. The manager's availability check and failover
then had nothing to fail over to (R2). With R2 fixed, the manager fails over
and the demand lands on FYERS, the only provider with a session:

```
plan[fyers].admitted  = 99 instruments   (entitlement 100, standard tier)
plan[upstox]          = provider_unavailable (no session) - reported, not dropped
```

### R4 — The FYERS symbol rejection

The live source config listed `NSE:BANKNIFTY-INDEX`; the catalogue (and
`brokers/fyers/hsm_symbols.py::INDEX_TOPICS`) use `NSE:NIFTYBANK-INDEX`. The
broker correctly rejected the non-existent symbol:

```
2026-10-22 00:42:14,469 WARNING fyers feed fyers: 1 instrument(s) could not be resolved:
                                NSE:BANKNIFTY-INDEX (broker rejected the symbol)
```

The demand path now supplies catalogue-exact symbols, so the stale key is no
longer part of the subscribed set. (The stale key still sits in the live
`config.json`; the durable fix is that the subscribed set is demand-derived,
not config-derived. `app/server.py` — which owns the source-config merge — is
a protected file and was not touched.)

### R5 — The `news_source_health` write failures

The live table (schema v23) is `news_source_health(source_id, last_attempt_at,
last_success_at, last_error, items_seen, items_added, updated_at NOT NULL)`.
The working-tree module created the table **without** `updated_at` and its
INSERT never supplied it, so every write failed:

```
sqlite3.IntegrityError: NOT NULL constraint failed: news_source_health.updated_at   (x9 per cycle)
```

**Fixed** in `core/persistence/modules/news.py`: the DDL now matches the house
shape (`updated_at TEXT NOT NULL`), the INSERT supplies it, and
`ensure_source_health_schema()` repairs a table created by the regressed DDL
(`ALTER TABLE ... ADD COLUMN` + back-fill) so an existing installation heals
itself instead of failing every cycle.

**After the fix** (live run, `POST /api/news/refresh` → 9 sources, 57 items):

```
reddit_dalalstreettalks | attempt: 2026-10-22T03:45:32.308388+00:00 | updated_at: 2026-10-22T03:45:32.308388+00:00
... (9/9 rows now carry a fresh updated_at; no IntegrityError in the run)
```

### R6 — The request storm: a stale bundle behind a broken build

`frontend/src/features/fno/FnoWorkspaceView.tsx:69` already contained the fix
(depend on the stable `mutate`, not on the mutation result), but:

* the served `frontend/dist/assets/index-Bzx4Onqr.js` was built **2026-10-20
  01:09**, i.e. *before* the fix was written (10:30) — the running app served
  the old effect, so every render re-posted the active view;
* `bun run build` was blocked: `tsc --noEmit` failed with 4 errors in the new
  test files (Node built-ins without Node types; a spread of an untyped
  `vi.mock` factory result), and two of the doubles mocked **non-existent**
  API functions (`getFnoUnderlyings` instead of `getFnoUniverse`) and the
  wrong return shape for `getIndexOptionUnderlyings`.

**Fixed**: `frontend/tsconfig.json` (production program) now excludes
`src/__tests__`, `frontend/tsconfig.test.json` type-checks the tests with the
Node types they need, `package.json` runs both projects, and the two doubles
were corrected. The bundle was rebuilt.

**Measured, same interaction (select an underlying in the F&O workspace):**

| | before (live run 00:35–00:48) | after (live run 09:20 IST) |
|---|---|---|
| `POST /api/market/fno/view` | ~100 in 4 s | **1** |
| served bundle | `index-Bzx4Onqr.js` (2026-10-20 01:09) | `index-DnhRKFCz.js` (2026-10-22 08:21) |

The regression test `frontend/src/__tests__/fnoViewStorm.test.tsx` asserts the
"no re-post on every render" contract and now passes.

### R7 — Upstox: the frozen contract, and the quota map

* **Contract.** The frozen tests require that a status observation of a
  *genuine* rejection still rolls the session back
  (`test_upstox_rejection.py::test_genuine_401_latches_and_invalidates`,
  `test_oauth_login.py::test_upstox_status_clears_only_on_broker_rejection`).
  An in-flight change had made `status()` a pure projection, and the legacy
  `broker_rejected_token` signal had been dropped — 4 frozen tests failed.
  Restored, with two safety properties the earlier implementation lacked:
  * the invalidation is **token-scoped** — a session issued *after* the
    rejection is a different token and survives;
  * it is **idempotent** — the same rejection is acted on once, so a status
    poll can never wipe a freshly stored token (the login loop);
  * for a feed that does not publish a rejection timestamp, the moment the
    rejection was *first observed* is the correlation point.
  `test_auth_foundation.py`, `test_upstox_rejection.py`, `test_oauth_login.py`,
  `test_auth_freeze.py`, `test_auth_startup.py`: **69 passed**.
* **Quota map.** The map was keyed to endpoints that do not exist
  (`/v2/authorization/*`, `/v2/feed/authorize`). Verified against the live
  97-path spec, the real endpoints are
  `/v2/login/authorization/token` (OAuth code exchange),
  `/v2/totp-login/authorization/token` (TOTP) and `/v2/login/validate` (PIN),
  and the feed authorization is `/v3/feed/market-data-feed/authorize`. The map
  now uses the real endpoints, with each endpoint's **documented** scope:
  the interactive-login family on the strict TOTP row (1 rps / 10 min / 60 per
  30 min), the code exchange and the feed authorization on the standard row
  (50 rps / 500 min / 2 000 per 30 min) in their **own** budgets — the feed
  authorization never sits in the login quota, and the login quota is not
  merged into an unrelated market-data quota.

---

## 4. Files changed

| file | change |
|---|---|
| `app/subscriptions/manager.py` | **new** `apply_demand(requests)`: translates the demand into leases, replacing (never accumulating) the demand-owned leases; resolves the canonical id of a provider-native key through the catalogue so the demand can be routed and failed over |
| `app/instruments.py` | **new** `find_canonical_rows(canonical_id)`: the catalogue read model the subscription router already expected |
| `core/persistence/modules/products.py` | `search_instruments()` gained an `isin` filter (the canonical identity of a cash instrument) |
| `core/persistence/modules/news.py` | `news_source_health` DDL + INSERT aligned with the house schema; guarded self-heal (`ensure_source_health_schema`, run before the write) for tables created by the regressed DDL |
| `app/auth/upstox.py` | `status()` restores the frozen rollback, token-scoped and idempotent; the rejected projection now also carries the feed facts; `now_fn` injection for deterministic tests |
| `app/auth/upstox_session.py` | rejection evidence: the feed latch, plus the legacy `broker_rejected_token` signal; invalidation: token-scoped, idempotent, correlation-pointed |
| `brokers/upstox/rest.py`, `brokers/upstox/capabilities.py` | quota buckets mapped to the real endpoints with their documented scopes |
| `frontend/tsconfig.json`, `frontend/tsconfig.test.json` (new), `frontend/package.json` | production typecheck separated from the test typecheck; both run in `typecheck`/`build` |
| `frontend/src/__tests__/fnoViewStorm.test.tsx`, `fnoViewEffectDeps.test.ts` | the doubles mock the real API functions/shapes; the storm contract is asserted |
| `test/test_subscription_demand.py` (new) | the demand→lease→feed contract, including routing to a provider that can serve and idempotent re-apply |
| `test/test_news_source_health_schema.py` (new) | the source-health write path: house schema, attempt/success/update times, and the self-heal of an older table |
| `test/test_upstox_auth_lifecycle.py` | the auth-lifecycle regressions (deterministic clock, token-scoped invalidation) |
| `test/test_provider_capacity.py`, `test/test_provider_request_budget.py` | quota assertions updated to the verified endpoints/scopes |
| `config.json` (live source config, SETTINGS/CONFIG zone) | the FYERS instrument key corrected from the non-existent `NSE:BANKNIFTY-INDEX` to the catalogue-exact `NSE:NIFTYBANK-INDEX` — the broker is no longer asked for a symbol it rejects (0 unresolved) |
| `frontend/src/features/dashboard/components/IndicesWidget.tsx` | the index card joins the live quote by the serving provider's native token (the canonical id the REST index poller publishes does not match the subscription registry's id) |
| `docs/plans/2026-10-21-unified-broker-agnostic-data-foundation.md` | §14.2 quota table corrected (see §7) |
| `docs/plans/proposed-fno-remediation-plan-2026-10-20.md` | the implementation assumptions affected by this work are noted (see §7) |

`app/server.py`, `app/auth/fyers.py`, `app/secrets_store.py`,
`brokers/*/feed.py`, `sources/registry.py`, `core/runtime.py`,
`core/persistence/store.py` and `core/persistence/modules/schema.py` were **not**
modified (protected files).

---

## 5. Runtime evidence, before and after

| measurement | before | after |
|---|---|---|
| startup demand apply | `fno coverage restore failed` (×N) | `fno coverage restored at startup: 662 contract(s) routed` |
| FYERS feed desired set | 2 (a stale config pair, one of them broker-rejected) | 99 (demand-derived, catalogue-exact) |
| `news_source_health` writes | 9 × `IntegrityError` per cycle | 9/9 rows written with a fresh `updated_at` |
| `POST /api/market/fno/view` per selection | ~100 in 4 s | 1 |
| served WebUI bundle | `index-Bzx4Onqr.js` (2026-10-20 01:09, pre-fix) | `index-DnhRKFCz.js` (2026-10-22 08:21, post-fix) |
| Upstox frozen auth tests | 4 failing | 69 passed (5 files) |
| `ruff check` (touched paths) | 10 errors | clean |
| full `pytest test` | 1612 passed / 2 failed / 14 errors | 1616 passed / 2 failed / 12 errors (the 12 are pre-existing, §8) |
| `tsc` (app + test projects) | 4 errors | clean, `vite build` succeeds |

---

## 6. Remaining blockers (measured, not assumed)

These are the places where the live path still cannot deliver a quote. None of
them is a code defect in the scope of this task; each needs a credential or a
broker-side action.

1. **Upstox has no session at all.** The store holds `api_key`, `api_secret`
   and `last_auth_status = rejected`, but **no `access_token`**. The source
   therefore gates on the OAuth login (`upstox oauth configured from saved
   credentials`, `upstox access_token not configured - source will gate on
   OAuth login`). An interactive login is required; the OAuth code exchange
   (`/v2/login/authorization/token`) is the documented flow and is implemented.
   Upstox's code path is verified with isolated fixtures (the frozen auth
   suite: 69 passed) — the live limitation is the missing session only.
2. **The FYERS session is valid again.** After the operator's login the broker
   accepts it (symbol-token and quotes both succeed — see §1.1). The earlier
   401 was the *previous* day's token: FYERS access tokens are per trading
   day, and the app's restore trusts the **locally derived** expiry
   (`2026-10-22T10:00:00Z`) instead of the broker's verdict, so a dead token
   is restored as if it were live and the feed then reports
   `symbol-token auth failed (401)`. That mismatch is in the **protected**
   FYERS auth/session-restoration path and was not changed here; the
   documented recovery without a PIN does not exist (the refresh endpoint
   answers `{"code":-502,"message":"Please provide your valid pin"}` and no
   PIN is stored).
3. **FYERS index quotes do not come from the HSM socket.** The 2026-10-20
   live validation measured the FYERS index channel's scaling as wrong
   (multiplier 32 instead of 100), so the feed's guard drops `if|` index topics
   by design; FYERS index quotes are served by the guarded REST index poller,
   which only runs inside the IST session (09:15–15:30). The index values
   shown in §1.1 come from that poller.
4. **Breadth, sector heatmap and the scanners still read the quote store under
   the wrong key** (evidenced, not assumed): the breadth members name the
   *market-identity* token (`NSE_EQ|INE040A01034`) while the quote store holds
   the FYERS quote under the provider's API symbol (`NSE:HDFCBANK-EQ`), so
   `/api/market/breadth` reports `quoted: 0, unavailable: 50` although the
   same quotes are present (and each quote's `canonical_id` **does** equal the
   member token). The fix is a canonical-id fallback in the breadth/sector/
   scanner quote reader; it was not attempted here because the reader's only
   owner is the protected quote store (`market/service.py`), and the dashboard
   join was fixed on the WebUI side instead.
5. Everything else in the chain is verified live: startup demand apply,
   provider routing, symbol resolution, subscription, tick decode, quote store,
   REST, SSE and the WebUI.

---

## 7. Plan updates (verified corrections only)

* `docs/plans/2026-10-21-unified-broker-agnostic-data-foundation.md` §14.2 —
  the Upstox quota rows were corrected to the endpoints that exist and to
  their documented scopes (`auth` = the interactive-login family on the strict
  TOTP row, `auth_code` = the OAuth code exchange on the standard row,
  `feed_authorize` = the feed authorization on the standard row, each in its
  own budget). The previous row billed two non-existent paths to the strict
  login bucket. No other quota figure changed.
* `docs/plans/proposed-fno-remediation-plan-2026-10-20.md` — the assumptions
  that this work changed are noted in place (W-08/W-11): the demand→lease→
  apply chain now exists at runtime, and the provider choice inside it is
  availability-aware, so a contract whose preferred provider has no session is
  routed to the provider that can serve it instead of being dropped.

---

## 8. Verification summary

* `python -m pytest test -q` → **1616 passed, 2 failed, 12 errors, 4 skipped**.
  The 2 failures and 12 errors are **pre-existing** and outside this task's
  scope; each was reproduced with this work's changes reverted (e.g.
  `test_web_auth.py` fails identically with the HEAD version of
  `app/auth/upstox.py`; `test_upstox_feed_processing.py` fails identically with
  the HEAD version of `brokers/upstox/rest.py`). They live in prior-change-set
  test files for FYERS TBT, X/Twitter, the FYERS PIN/config-architecture
  paths, the Upstox feed's per-instrument error field, and the web-auth route
  injection (the route uses the composition-root auth service, while the test
  expects the route to use its own feed reference).
* `python -m pytest test/test_architecture_boundaries.py test/test_auth_freeze.py`
  → green (the protected-core and auth-ownership gates).
* `ruff check` on every touched path → clean.
* `bun run typecheck` and `bun run build` in `frontend/` → clean; the rebuilt
  bundle is the one the running app serves.
* Live database: `data/events.db` was only written by the application's own
  normal operation (news health rows, news items, recent events); it was never
  reset, cleaned, vacuumed or rewritten. No token, secret or PIN was printed
  at any point.

---

## 9. Deliberately not done

* `app/server.py` (protected startup/runtime) — the startup composition was
  left alone; the missing contract was implemented in the subscription module
  the call sites already use.
* The FYERS session-restoration semantics (protected auth): the restore still
  trusts the locally derived expiry, and a PIN login is still required. Both
  are reported in §6 as the remaining blockers rather than changed here.
* No PIN/TOTP login-flow redesign and no invented refresh behaviour.
* No change to the Upstox rate-limit *figures* — only the endpoint→scope
  mapping was corrected.
* No unrelated refactoring of the two appliers of the subscription set (the
  service's own `by_provider` view is still configuration-ordered; the
  manager's plan is the availability-aware one). The service's apply path
  leaves the manager's applied set untouched (verified: the 99 FYERS
  instruments survived `POST /api/subscriptions/apply`).
