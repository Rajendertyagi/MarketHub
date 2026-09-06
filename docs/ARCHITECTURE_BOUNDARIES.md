# MarketHub — Architecture Boundaries & Change-Isolation Contract

> Purpose: make MarketHub **safe to keep developing**. A change to one feature
> must not be able to break an unrelated feature unless the two genuinely
> depend on each other.
>
> This document is the authoritative boundary map. It was produced from a
> read-only audit of the repository (no code was moved/rewritten during the
> audit). Implementation of large moves is **deferred**; this document defines
> the target and the guardrails that already protect it.

---

## 1. Subsystem Map

| Subsystem | Key modules | Owns |
|---|---|---|
| **AUTH / SESSION** | `app/secrets_store.py`, `brokers/upstox/auth.py`, `brokers/fyers/auth.py`, `api/routes.py::build_auth_routes`, `api/product_routes.py::build_fyers_auth_routes` | encrypted credential store, OAuth/token/PIN exchange, session persistence, restart-safe restore |
| **BROKER RUNTIME** | `sources/registry.py`, `sources/__init__.py`, `core/runtime.py`, `app/server.py` (composition) | source registration, source/feed lifecycle, reconnect, runtime status |
| **UPSTOX ADAPTER** | `brokers/upstox/{auth,rest,feed,feed_processing,feed_protocol,errors}.py`, `brokers/upstox/proto/` | Upstox REST/feed/auth/proto translation |
| **FYERS ADAPTER** | `brokers/fyers/{auth,feed}.py`, `brokers/fyers/tbt/{feed,normalizer,order_book,proto}` | Fyers REST/TBT/feed/auth translation |
| **CANONICAL MARKET DATA** | `market/service.py`, `market/models.py`, `market/serialization.py`, `market/normalize/*`, `app/market_data.py`, `app/market_identity.py`, `core/sse_broker.py` | canonical quote store, quote/depth normalization, market SSE broker |
| **INSTRUMENT / CATALOG** | `app/instruments.py`, `app/instrument_identity.py`, `core/persistence/modules/products.py` | canonical identity, catalog, provider→canonical mappings |
| **OPTIONS** | `market/analytics/option_chain.py`, `market/analytics/strategies.py`, `mcp_server/tools/options_analytics_tools.py` | expiries, option contracts, chain, greeks/OI analytics |
| **ALERTS** | `app/alerts.py`, `app/condition_alerts.py`, `app/market_analytics.py`, `core/alerts.py`, `core/persistence/modules/{alerts,condition_alerts,delivery,consumers}.py` | condition engine, persistence, delivery/ACK |
| **NEWS** | `news/service.py`, `news/adapters/{base,rss,reddit}.py`, `api/news_routes.py`, `web/ui/js/{news,sentiment}.js`, `core/persistence/modules/news.py` | sources, ingestion, sentiment |
| **MCP** | `mcp_server/{contract,metrics,registry,resources,services}.py`, `mcp_server/tools/*` | MCP surface over canonical services |
| **REST API** | `api/routes.py`, `api/product_routes.py`, `api/{chat,ai_alert,log,news}_routes.py` | HTTP route registration (composition-injected) |
| **SSE** | `core/sse_broker.py`, `api/routes.py::_market_stream`, `app/server.py::_event_stream` | live event/quote fan-out |
| **APPLICATION STARTUP** | `app/server.py` | composes all services, wires transports, runs restore |
| **WEBUI SHELL** | `web/ui/js/{app,shell,router}.js`, `web/ui/index.html`, `web/ui/css/{shell,style,base,components,tokens,app}.css` | bootstrap, router, shell chrome |
| **WEBUI FEATURES** | `web/ui/js/{option-chain,news,sentiment,alerts,ai-alerts,charts,watchlists,instruments,market,market-sources,sources,quotes,logs,mcp-tools,auth}.js`, `web/ui/js/features/settings/*` | per-feature state/DOM/listeners/timers |
| **SETTINGS / CONFIG** | `app/config.py`, `config.json`, `api/routes.py::build_settings_routes`, `api/product_routes.py::build_app_settings_routes` | app/source config (non-secret) vs encrypted secrets |
| **PERSISTENCE** | `core/persistence/store.py`, `core/persistence/modules/*.py` | encrypted event/secret/source-state store, schema |

---

## 2. Why Unrelated Changes Currently Affect Each Other (Fragility Causes)

1. **Single composition root with import-time side effects (`app/server.py`, 1322 lines).**
   Broker login/session restore (Upstox + Fyers), Fyers token refresh, Upstox
   session restore, source registration, config migration, identity registration,
   and route wiring all execute as **module import side effects**. One exception
   or bad import in any concern aborts process startup, taking login, feed
   restoration, and every other subsystem down together. There is no isolation
   boundary between "Fyers restore" and "Upstox login" — they share one module
   and one execution path.

2. **AUTH + SETTINGS + market + source-control routes share `api/routes.py`.**
   `build_settings_routes` directly mutates `config.json` (`_write_upstox_feed`,
   `_remove_upstox_feed`) and holds the shared mutable `oauth_ref` dict that
   `build_auth_routes` also reads. An edit to feed-config persistence sits in the
   same file as token/session endpoints. `build_auth_routes` imports
   `brokers.upstox.auth` and reaches into `feed._credentials` / `feed.status()`.

3. **`api/product_routes.py` (1556 lines) mixes many domains** (admin, alerts,
   instruments, intel, watchlists, market_data, app_settings, diagnostics,
   fyers_auth, chat, ai_alert). Editing one domain's `build_*_routes` risks the
   whole file, and it currently imports `brokers.fyers.auth` directly (a route
   module reaching into a broker adapter's auth internals).

4. **Source registry imports concrete broker adapters** (`sources/registry.py`
   imports `brokers.upstox.feed`, `brokers.fyers.auth`, `brokers.fyers.feed`,
   `brokers.upstox.rest`, `brokers.upstox.auth`). A breaking change in a broker
   adapter's import path can prevent the source runtime (and therefore feed
   startup) from loading at all.

5. **Minor frontend feature-to-feature imports.** `market-sources.js` imports
   `getAuthStatus` from `auth.js` and `renderMarketStatus/renderMovers` from
   `market.js`; `alerts.js` imports `pollSources` from `market-sources.js`.
   These are shared read/helper imports, not deep coupling, but they are the
   kind of edge that lets a feature change ripple sideways.

6. **Config is a partial dumping ground.** `config.json` legitimately holds
   non-secret source config, but feed enable/disable writes also happen there
   (`build_settings_routes`), and legacy plaintext Fyers credentials were
   historically migrated from `config.json` into the encrypted store at startup
   (`app/server.py::_inject_fyers_source_config`). Secrets must never live in
   `config.json`; this is already enforced by code, not yet by a guardrail.

---

## 3. Allowed Dependency Directions

```
WEBUI (shell + features)
        │  (HTTP/SSE only; stable REST/SSE contracts)
        ▼
REST API  ──►  canonical application services (MarketService, NewsService,
SSE            InstrumentCatalog, OptionsService, AlertEngine, ...)
MCP     ─────►  canonical application services

OPTIONS                ──►  canonical market data + instruments
MARKET DATA            ──►  broker interfaces / adapters (via normalization)
BROKER RUNTIME         ──►  broker adapters + AUTH interface
AUTH                   ──►  encrypted persistence (secrets_store)
NEWS                   ──►  canonical persistence + market models (read-only)
ALERTS                 ──►  canonical market data + persistence
PERSISTENCE            ──►  (lowest layer; nothing above depends on its internals)
```

Provider adapters may depend on **shared canonical models/interfaces**
(`market.models`, `market.serialization`), never the reverse.

---

## 4. Forbidden Dependencies

These edges are **not allowed** to be introduced (existing violations are
recorded as debt in §8 and must not be widened):

- Options → auth implementation / secrets store / `app.secrets_store`
- News → broker auth / `brokers.*.auth` / `app.secrets_store` / `app.config`
- WebUI → broker adapters / `brokers.*` / provider tokens / provider-specific
  normalization
- Canonical market service → WebUI
- Broker feed → WebUI
- Auth → Option Chain internals
- Feature module → server-startup internals (`app.server` symbols)
- Route module → concrete broker adapter auth internals
  (exception: `api/routes.py::build_auth_routes` is the designated auth surface
  and may import `brokers.upstox.auth`; `api/product_routes.py` may NOT)
- `config.json` → any credential/secret value

---

## 5. Protected Core

A future agent may **READ** protected code but must **NOT MODIFY** it merely
because that looks like the quickest way to ship another feature.

| Protected subsystem | Files |
|---|---|
| Credential storage | `app/secrets_store.py`, `data/master.key`, encrypted secrets table |
| Token / session persistence | `app/secrets_store.py` (`*_session_token`, `*_auth_code`), `core/persistence/modules/secrets.py`, `core/persistence/modules/source_state.py` |
| Broker login (Upstox + Fyers) | `brokers/upstox/auth.py`, `brokers/fyers/auth.py`, `api/routes.py::build_auth_routes`, `api/product_routes.py::build_fyers_auth_routes` |
| Session restoration | `app/server.py` restore blocks (`_try_restore_fyers_token`, Upstox session restore), `build_auth_routes::_auth_status` |
| Application startup | `app/server.py` (composition root) |
| Source / feed lifecycle | `sources/registry.py`, `core/runtime.py`, `app/server.py` source wiring |
| Persistence schema / migrations | `core/persistence/store.py`, `core/persistence/modules/schema.py` |
| Canonical market models | `market/models.py`, `market/service.py`, `market/serialization.py` |

**Escalation rule:** if a task outside a protected subsystem genuinely requires
changing protected code, **STOP**. Report: (1) which protected subsystem, (2) why,
(3) the exact dependency forcing it, (4) files required, (5) regression risk.
Wait for a separate approved task. Do not silently cross the boundary.

---

## 6. Red-Zone Files

A change to a red-zone file requires extra verification matched to the boundary.

| File | Zone | Required verification after change |
|---|---|---|
| `app/secrets_store.py` | AUTH | login + restart restore + forget-session |
| `brokers/upstox/auth.py`, `brokers/fyers/auth.py` | AUTH | OAuth login + token/PIN + restart restore |
| `api/routes.py` (auth/settings portions) | AUTH | login + session persistence + feed enable/disable |
| `app/server.py` | STARTUP/RUNTIME | full startup + Upstox & Fyers feed start + restore |
| `sources/registry.py`, `core/runtime.py` | RUNTIME | source start/stop/restart + reconnect |
| `market/service.py`, `market/models.py` | MARKET | representative canonical quote + SSE reconciliation |
| `api/product_routes.py` | ROUTER | affected endpoints only |
| `web/ui/js/app.js`, `web/ui/js/shell.js`, `web/ui/js/router.js` | SHELL | all WebUI routes render + active-view-only |

Do **not** run the entire suite for every change; verify the touched zone.

---

## 7. Single Ownership Map

| Resource | Single owner |
|---|---|
| Broker session / credentials | `app/secrets_store.py` (encrypted) + `brokers/*/auth.py` |
| Upstox feed instance | `app/server.py` (`_feed_ref["feed"]`), lifecycle via `SourceManager` |
| Fyers runtime token | `app/server.py` (`_fyers_runtime_token`) — single source of truth |
| Feed task / source lifecycle | `sources/registry.py::SourceManager` (only owner) |
| Quote store | `market/service.py::MarketService` (only owner) |
| MarketService | constructed once in `app/server.py` |
| Options analytics | `market/analytics/option_chain.py` (pure over canonical models) |
| SSE broadcaster (market) | `app/server.py` (`_market_event_broker`) |
| SSE broadcaster (generic events) | `app/server.py` (`_event_broker`) |
| News service | `news/service.py::NewsService` |
| Alert engine | `app/alerts.py` + `app/condition_alerts.py` |
| OAuth mutable config | `_oauth_cfg_ref` dict in `app/server.py`, shared read-only with routes |

No module outside `SourceManager` may independently start/stop a feed.
No module outside `MarketService` may own the quote store.

---

## 8. Known Debt (documented, deferred — do NOT silently fix)

- **`api/product_routes.py` imports `brokers.fyers.auth`** — a route module
  reaching into a broker adapter's auth internals. Target: extract Fyers auth
  into a service that the route calls. **Deferred** (risky to move near login).
- **`app/server.py` restore/source/config-migration logic is import-time** —
  target: move into explicit, individually-try/excepted lifecycle functions so a
  Fyers-restore failure cannot abort Upstox login. **Deferred** (high risk;
  must preserve exact login/restore behavior).
- **`api/routes.py` mixes auth + settings + market + source control** — target:
  split `build_auth_routes`+`build_settings_routes` into `api/auth_routes.py`
  and `api/settings_routes.py` (URLs/contracts unchanged). **Deferred** (auth is
  protected; document boundary, do not move yet).
- **Minor frontend feature-to-feature imports** (`market-sources.js` → `auth.js`
  `getAuthStatus`; `alerts.js` → `market-sources.js` `pollSources`) — acceptable
  as stable read/helper APIs; keep them narrow.

---

## 9. Change-Surface Examples

| Task | Allowed areas | NOT allowed |
|---|---|---|
| Option Chain visual layout | `web/ui/js/option-chain.js`, `web/ui/css/features/option-chain.css` | auth, secrets, broker login, feed lifecycle, `MarketService`, MCP |
| Breadth backend | `market/analytics/*` (new module), `market/models.py` (add model) | auth, `brokers/*`, WebUI, `config.json` secrets |
| Breadth WebUI | `web/ui/js/breadth.js` (new), `web/ui/css/features/breadth.css` | `brokers/*`, `app.secrets_store`, login |
| News change | `news/*`, `api/news_routes.py`, `web/ui/js/news.js` | broker auth, option-chain internals, `config.json` secrets |
| Broker login change | `brokers/*/auth.py`, `app/secrets_store.py`, auth routes | WebUI features, `MarketService`, news, options |
| SSE change | `core/sse_broker.py`, `_market_stream`/`_event_stream` | `brokers/*` internals, auth, persistence schema |

---

## 10. Guardrails (enforced)

See `test/test_architecture_boundaries.py`. It is a **static import-boundary**
check (AST-based, no runtime), low-maintenance, and currently green. It forbids:

- `market/analytics/` (OPTIONS) importing `app.secrets_store`, `brokers.*.auth`,
  `api.routes`, `app.config`.
- `news/` importing `brokers.*.auth`, `app.secrets_store`, `api.routes`,
  `app.config`.
- Any `web/ui/js` module importing broker/provider code (`brokers`).

Add new forbidden edges there as boundaries harden — but only edges that are
**already clean today**, so the guard stays green and protective rather than a
failing gate.

---

## 11. Agent Rule (survives future conversations)

Before editing: declare the subsystem + intended files.
During work: do not cross into protected zones (§5).
If crossing is required: STOP and report instead of editing (§5 escalation).
After work: inspect `git diff` and verify only the affected boundary + protected
baseline. Login must not regress.

---

## 12. Change Zones (practical, file-level)

A zone is a set of files a task is allowed to touch. Ownership is explicit; a
file being *shared* does **not** grant every task permission to modify it (§14).

| Zone | Owned files | Allowed deps | Protected deps (read-only / never modify) | Red-zone files | Verify if touched |
|---|---|---|---|---|---|
| **PROTECTED AUTH** | `app/secrets_store.py`, `brokers/upstox/auth.py`, `brokers/fyers/auth.py`, `api/routes.py::build_auth_routes`, `api/product_routes.py::build_fyers_auth_routes` | encrypted store, broker adapters | — | `app/secrets_store.py`, `brokers/*/auth.py`, auth route funcs | login + restart restore + forget-session |
| **PROTECTED STARTUP/RUNTIME** | `app/server.py`, `sources/registry.py`, `core/runtime.py` | services it composes | AUTH internals (call, don't rewrite) | `app/server.py`, `sources/registry.py` | full startup + Upstox & Fyers feed start + restore |
| **BROKER ADAPTERS** | `brokers/upstox/*`, `brokers/fyers/*` | canonical models, `market/normalize` | AUTH store (via injected store) | `brokers/upstox/feed.py`, `brokers/fyers/feed.py` | representative feed connect/stream |
| **CANONICAL MARKET DATA** | `market/service.py`, `market/models.py`, `market/serialization.py`, `market/normalize/*`, `app/market_data.py`, `app/market_identity.py` | `core/sse_broker.py` | WebUI/API (never import) | `market/service.py`, `market/models.py` | representative canonical quote + SSE reconcile |
| **OPTIONS** | `market/analytics/option_chain.py`, `market/analytics/strategies.py`, `mcp_server/tools/options_analytics_tools.py` | canonical market, instruments | auth/secrets/adapters | (none red — feature-local) | Option Chain only |
| **NEWS** | `news/*`, `api/news_routes.py`, `web/ui/js/news.js`, `web/ui/js/sentiment.js` | canonical models, persistence | broker auth/secrets/config | (none red — feature-local) | News reader + sentiment |
| **ALERTS** | `app/alerts.py`, `app/condition_alerts.py`, `core/alerts.py`, `core/persistence/modules/{alerts,condition_alerts,delivery,consumers}.py` | market data, persistence | auth/secrets | `app/condition_alerts.py` | alert trigger + ACK |
| **TRANSPORT/API** | `api/routes.py`, `api/product_routes.py`, `api/{chat,ai_alert,log,news}_routes.py`, `core/sse_broker.py` | canonical services (injected) | broker adapter internals (except designated auth surface) | `api/routes.py`, `api/product_routes.py` | affected endpoints only |
| **WEBUI SHELL** | `web/ui/js/{app,shell,router}.js`, `web/ui/index.html`, `web/ui/css/{shell,style,base,components,tokens,app}.css` | feature modules (composition), REST/SSE | broker adapters, secrets | `web/ui/js/app.js`, `web/ui/js/router.js`, `web/ui/js/shell.js` | all routes render + active-view-only |
| **WEBUI FEATURES** | `web/ui/js/{option-chain,news,sentiment,alerts,ai-alerts,charts,watchlists,instruments,market,market-sources,sources,quotes,logs,mcp-tools,auth}.js`, `web/ui/js/features/settings/*`, `web/ui/css/features/*` | utils, api, sibling read-helpers | auth internals (beyond stable `getAuthStatus`/`pollAuthStatus` read) | (feature-local) | feature only |
| **CONFIG/PERSISTENCE** | `app/config.py`, `config.json`, `core/persistence/*` | stdlib (config), store API (persistence) | secrets must never enter `config.json` | `core/persistence/store.py`, `core/persistence/modules/schema.py` | config loads; secrets unchanged |

---

## 13. Protected-Core Rule (mandatory declaration)

Every future task MUST begin by declaring:

```
TASK ZONE:            <one zone from §12>
ALLOWED CHANGE SURFACE: <specific files>
PROTECTED/OUT-OF-SCOPE ZONES: <zones this task must NOT touch>
```

If completing the task *requires* modifying a protected/out-of-scope zone,
**STOP**. Do not edit. Report:

1. required file
2. owning subsystem
3. why the current task needs it
4. the exact dependency forcing the need
5. expected regression risk

Then wait for a separate approved task. This applies even when the protected
file *looks* like the quickest fix.

Example:

```
TASK: Option Chain CSS layout
TASK ZONE: WEBUI FEATURES
ALLOWED: web/ui/js/option-chain.js, web/ui/css/features/option-chain.css
PROTECTED: auth, secrets, startup, broker lifecycle, MarketService, config,
           unrelated WebUI features, router/shell unless nav behavior changes
```

If the agent thinks `app/server.py` must change → STOP and escalate, not edit.

---

## 14. Shared File ≠ Permission

A file being shared does **not** mean every task may modify it.

- `api/routes.py` contains auth + settings + market + source-control. An Option
  Chain task does **not** gain permission to modify its auth/settings portions.
- `app/server.py` composes everything. A News task does **not** gain permission
  to change startup/restore logic.
- `config.json` is shared. A feature task must not casually write credentials or
  feed config there.
- `web/ui/js/app.js` imports all features. A feature task edits its *own* module,
  not the bootstrap, unless navigation behavior changes (then WEBUI SHELL).

---

## 15. Minimum Verification Matrix (targeted, not whole-suite)

| Red zone changed | Minimum verification |
|---|---|
| AUTH | credential/session save + login + restart restore + forget-session |
| STARTUP/RUNTIME | backend startup + auth restoration + source/feed state |
| BROKER RUNTIME | source start/stop/restart + reconnect + representative feed |
| CANONICAL MARKET | canonical identity + representative quote + SSE reconcile |
| SSE | framing + reconnect/reset + representative event |
| MCP | canonical-service parity (tool contracts) |
| WEBUI SHELL | route/view lifecycle (all views reachable, inactive hidden) |
| OPTION CHAIN FEATURE | Option Chain only — login needs NO full retest because auth/startup were not touched |
| NEWS FEATURE | News reader + sentiment only |

Isolation first; targeted verification second.

---

## 16. Future Task Change-Surface Examples

For each: ALLOWED / PROTECTED / RED-ZONE ESCALATION / MINIMUM VERIFICATION.

**A. Option Chain CSS/layout**
- ALLOWED: `web/ui/js/option-chain.js`, `web/ui/css/features/option-chain.css`
- PROTECTED: auth, secrets, startup, broker lifecycle, MarketService, config,
  unrelated features, global CSS
- ESCALATION: none unless global CSS or router must change
- VERIFY: Option Chain renders/updates

**B. Option Chain backend calculation**
- ALLOWED: `market/analytics/option_chain.py`, `market/analytics/strategies.py`
- PROTECTED: auth/secrets/adapters (guard-enforced)
- ESCALATION: STOP if it needs broker auth or config secrets
- VERIFY: representative chain snapshot + greeks

**C. Breadth backend**
- ALLOWED: new `market/analytics/breadth.py`, `market/models.py` (add model)
- PROTECTED: auth, `brokers/*`, WebUI, `config.json` secrets
- ESCALATION: STOP if it needs login/session
- VERIFY: canonical identity + representative breadth value

**D. Breadth WebUI**
- ALLOWED: new `web/ui/js/breadth.js`, `web/ui/css/features/breadth.css`
- PROTECTED: `brokers/*`, `app.secrets_store`, login, `app/server.py`
- ESCALATION: STOP if it needs broker tokens
- VERIFY: breadth view renders

**E. News reader change**
- ALLOWED: `news/*`, `api/news_routes.py`, `web/ui/js/news.js`
- PROTECTED: broker auth, option-chain internals, `config.json` secrets
- ESCALATION: STOP if it needs broker credentials
- VERIFY: news ingestion + UI list

**F. Upstox authentication change**
- ALLOWED: `brokers/upstox/auth.py`, `app/secrets_store.py`, auth routes
- PROTECTED: WebUI features, `MarketService`, news, options
- ESCALATION: n/a (this IS the protected zone; needs its own approved task)
- VERIFY: OAuth login + token/PIN + restart restore

**G. Fyers feed change**
- ALLOWED: `brokers/fyers/feed.py`, `brokers/fyers/tbt/*`
- PROTECTED: auth store internals (use injected store), WebUI
- ESCALATION: STOP if it needs to change credential encryption
- VERIFY: feed connect/stream + representative quote

**H. SSE transport change**
- ALLOWED: `core/sse_broker.py`, `_market_stream`/`_event_stream`
- PROTECTED: `brokers/*` internals, auth, persistence schema
- ESCALATION: STOP if it needs to change event schema
- VERIFY: framing + reconnect/reset + representative event

**I. WebUI router/shell change**
- ALLOWED: `web/ui/js/{app,shell,router}.js`, shell/global CSS
- PROTECTED: feature module internals, broker adapters, login
- ESCALATION: STOP if feature modules must be rewritten
- VERIFY: all routes reachable, inactive views hidden

**J. Canonical identity change**
- ALLOWED: `market/models.py`, `app/instrument_identity.py`,
  `app/market_identity.py`
- PROTECTED: auth/secrets, `brokers/*` internals
- ESCALATION: STOP if it needs provider credentials
- VERIFY: representative quote resolves across feed/REST/intel/option-chain

---

## 17. Transitive Dependency Risks (discovered)

- **Options → helper → routes → auth**: currently clean — `market/analytics`
  imports only `market.models`; no helper reaches `api.routes` or auth. Guarded.
- **News → shared module → startup/runtime**: `news/` imports only
  `market.models` + store (injected). No path to `app.server`/`app.config`.
  Guarded.
- **Fyers runtime token is mutated in TWO places** (duplicate ownership, see §18):
  `app/server.py` (restore) and `api/product_routes.py` (Fyers login route).
  This is a transitive path by which a Fyers *auth-route* change can alter the
  runtime token the *feed* reads. Not auto-guardable at import level; recorded
  as debt + explicit change-review rule.
- **`api/routes.py` settings routes hold the shared `_oauth_cfg_ref`** also
  mutated by `app/server.py`. Intended runtime credential enablement, but it is
  cross-module mutable shared state.

---

## 18. Single-Owner Validation (Phase 2)

Phase 1 claimed single ownership; Phase 2 validated and found one duplicate:

| Resource | Owner (primary) | Duplicate / secondary mutation | Status |
|---|---|---|---|
| Broker auth/session | `app/secrets_store.py` + `brokers/*/auth.py` | — | OK |
| Upstox runtime token | `app/server.py` (`_feed_ref`) | — | OK |
| **Fyers runtime token** | `app/server.py` (`_fyers_runtime_token`) | **`api/product_routes.py` writes it on login (L1253/L1276)** | DUPLICATE — debt |
| SourceManager | `sources/registry.py` | — | OK |
| Feed tasks | `sources/registry.py` | — | OK |
| MarketService / quote store | `market/service.py` | — | OK |
| Market SSE broker | `app/server.py` (`_market_event_broker`) | — | OK |
| News service | `news/service.py` | — | OK |
| Option Chain service | `market/analytics/option_chain.py` | — | OK |
| OAuth config | `_oauth_cfg_ref` in `app/server.py` | `api/routes.py` settings routes mutate it | Shared-by-design |
| CredentialStore | `app/server.py` (`_credential_store`) | `api/routes.py` builds a fallback via `build_default_store()` when none injected | Test/isolation path |

**No fix applied in this phase** (would touch protected startup/auth surface).
Recorded for the controlled Fyers-token-isolation refactor.

---

## 19. Automated vs Explicit Rules

**Enforced automatically** (`test/test_architecture_boundaries.py`):
- Options analytics → no auth/secrets/broker/config/api import
- News → no broker auth/secrets/config/server/api import
- `app/config.py` → no secrets/broker import
- `market/` → no web/api/app/brokers import
- `core/` → no `app` import
- WebUI → no `brokers` import

**Cannot be reliably auto-enforced — explicit change-review rules instead:**
- A feature task must not modify a shared file's out-of-scope portion
  (e.g. Option Chain touching `api/routes.py` auth).
- WebUI feature modules must not depend on `auth.js` internals beyond the
  stable `getAuthStatus`/`pollAuthStatus` read (`market-sources.js` currently
  does; keep it narrow).
- `api/product_routes.py` must not widen its `brokers.fyers.auth` import or
  mutate `_fyers_runtime_token` beyond login (duplicate-ownership debt).
- `config.json` must never contain credentials/secrets.
- No module outside `SourceManager` may start/stop a feed; no module outside
  `MarketService` may own the quote store.

---

## 20. Pre-existing Debt Requiring Controlled Refactor (deferred)

1. `app/server.py` import-time restore/source/config-migration logic (highest).
2. `api/routes.py` auth/settings coupling + `config.json` writes.
3. `api/product_routes.py` size + `brokers.fyers.auth` import + Fyers-token
   duplicate mutation.
4. `sources/registry.py` concrete broker-adapter imports.

Each needs its own approved, login-safe task behind the §13 declaration and
the §15 verification matrix.
