# EventHub — MCP Event Server

Generic self-hosted MCP event server foundation (`python -m app.server`).

## Layout

```
app/      application composition: Starlette+Uvicorn host, entrypoint,
          lifespan presentation helpers, canonical config + paths
core/     generic services: events, alerts, runtime/background tasks,
          SSE broker, persistence (core/persistence/store.py + modules/)
mcp_server/  MCP surface: contract identifiers, tools/, resources,
          services bundle, metrics   (named mcp_server so it can never
          shadow the installed `mcp` SDK distribution)
sources/  external source subsystem (registry, HTTP poller, test source)
test/     feature-focused regression suite (see below)
```

## Layout

```
test/
  run_all.py            # regression driver: isolated subprocess per file, hard 300s/file timeout
  helpers/              # lifecycle, mcp, wait, mock_http, runner — shared test support
  mcp_result.py         # SDK result normalization + safe teardown
  test_*.py             # one feature per file (the units of the suite)
  config.json           # base server config (overwritten per-test, restored after)
TEST_RUNTIME_MAP.md     # per-file server usage, harness behavior, run guidance
```

Production source under `test/` is **frozen** — do not edit `server.py`, `events.py`,
`store.py`, `runtime.py`, `errors.py`, `client.py`, `config.json`, `requirements.txt`,
or `sources/*` as part of test work.

## Run

```bash
# Focused (preferred while debugging one feature):
python test/test_acknowledgement.py
python test/run_all.py --group fast

# Full regression (CI / release only — slow path):
python test/run_all.py
python test/run_all.py --timeout 300
```

See `TEST_RUNTIME_MAP.md` for the server-usage matrix, the bounded-wait strategy, and
why you should run focused, not full, during development.

## Principles

- **No server unless the test needs the MCP/HTTP boundary.** Application logic
  (store, events, routing, checkpoint/ack, background tasks, source lifecycle, dedup)
  is tested directly against the real objects with an in-memory stub bus.
- **One server per D-level file** (exceptions: restart tests in `test_errors.py` /
  `test_reconnect.py`, and `test_subscriptions.py` whose tests need different source
  configs).
- **Bounded waits, no long fixed sleeps.** Readiness/counts are polled with timeouts.
- **Hard timeout (300 s/file) + parent-owned cleanup** — the runner terminates the child's
  process group, so a hung file fails fast (reported as **TIMEOUT**) and any `server.py`
  it spawned is killed with it. No reliance on the child's `atexit` (which can't run after
  a hard kill).

## Deleted (do not recreate)

`test/test_phase8.py`, `test/integrate_test.py` — replaced by the per-feature files above.

## Quick Start (daily routine)

1. Double-click `start_market_hub.bat` → open http://localhost:7070/ui/
2. **First time only:** Settings → save broker App credentials
   (Upstox and/or Fyers), then click Login and approve on the broker page.
3. Instruments page → Sync Upstox / Sync Fyers (one-time catalog load).
4. Every trading day: Settings → **Login with Upstox** → password + OTP.
   Feed flips to Streaming automatically.

## MarketHub Product Features (2026-08)

- **Live Upstox feed** — OAuth login from the Web UI (Settings → Login with
  Upstox), runtime subscription updates, automatic reconnect/resubscribe.
- **Encrypted credentials** — API key/secret stored Fernet-encrypted in the
  app SQLite DB (`data/events.db`, table `secrets`); master key at
  `data/master.key` (outside the DB, gitignored). Daily access token stays
  memory-only and expires at 03:30 IST.
- **Canonical data** — all provider payloads normalize into neutral models
  (`market/models.py`); REST/SSE/MCP/UI consume only canonical state.
- **Instruments catalog** — manual sync of official Upstox/Fyers masters;
  search at Instruments page.
- **Watchlists** — persistent, multi-list; adding an item joins the live
  desired-subscription set.
- **Option chain** — Upstox chain via authenticated REST; catalog-driven
  underlying/expiry selection.
- **History & charts** — Upstox candles into self-hosted ECharts
  candlestick+volume view.
- **Alerts** — threshold + crossing rules over canonical quotes; persisted,
  re-armable, in-app notifications.

### Data directory

    data/events.db        application SQLite (events, secrets, instruments,
                          watchlists, market_alerts)
    data/master.key       Fernet key for credential decryption (NEVER commit)
    data/backups/         database backups (ciphertext only)

### Limitations

- Fyers live WebSocket feed not yet implemented (auth + normalizers ready).
- Upstox exact-0.0 greeks unrepresentable over its websocket (wire format).
- Option-chain underlying picker requires a synced instrument catalog.

### More limitations

- Fyers live WebSocket protocol constants are implemented per official docs
  but need one real-session confirmation (requires Fyers app credentials).
- Option-chain underlying picker requires a synced instrument catalog.
- Market status outside session hours is inferred from IST time, clearly
  labelled — not broker-confirmed.
- Quotes older than 5 minutes are marked [STALE] in the UI.
