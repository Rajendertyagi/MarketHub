# MarketHub 0.3.0-rc.1 — Release Notes (Release Candidate)

Status: **RELEASE CANDIDATE — READY FOR FINAL FYERS LIVE ACCEPTANCE.**
Merge/tag happens only after the live Fyers acceptance passes.

## Core

- Canonical shared market state: one normalized quote/depth model consumed
  by REST, SSE, WebUI, and MCP.
- Live Upstox market feed: OAuth daily login, runtime subscriptions,
  automatic reconnect with resubscribe, honest `auth_required` lifecycle.
- Fyers feed implementation: secure composition (credentials never in
  config), OAuth login flow, refresh-token restore at startup.
- Source lifecycle: Start / Stop / Restart per source, transition history,
  task-liveness forensics, operator-stop suppression.
- Watchlists (multi-list, refcounted subscriptions, import/export),
  instrument catalog sync, option chain, history charts.
- Alerts: threshold + crossing rules, re-arm, in-app notifications, and a
  durable per-firing trigger history that survives restarts and backups.
- Sources observability page; rotating persistent logs; database backup;
  ~27 read-only MCP tools.

## Security

- Broker credentials stored Fernet-encrypted in the app SQLite DB; master
  key outside the DB (`data/master.key`), created once, never regenerated
  over existing ciphertext, never committed or logged.
- Daily access tokens are runtime-only (never persisted).
- `config.json` contains no secret fields; example config ships clean.
- OAuth callback URLs derive from the explicit `public_base_url` setting —
  never from request Host headers.
- Log redaction verified adversarially against dummy secret-like values.
- Read-only support snapshot at `GET /api/diagnostics` (strict whitelist,
  no tokens/URLs/secrets).

## Known Limitations

- Fyers production session pending live operator validation (tomorrow's
  acceptance); offline-tested end-to-end otherwise.
- Upstox process restart requires daily login (no refresh flow integrated).
- Whole-market scanner intentionally deferred (needs curated-universe +
  provider-limit policy).
- Market status outside session hours is inferred (labelled as such).
