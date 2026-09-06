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
