# MarketHub

Self-hosted market data desktop for Indian markets: live Upstox and Fyers
feeds, watchlists, option chain, charts, alerts with durable trigger
history, and a read-only MCP tool surface. Windows-first; runs entirely on
your machine.

- **Read-only.** MarketHub never places orders. There are no trading tools.
- **Secrets stay local.** Broker credentials are encrypted (Fernet) inside
  the application database; access tokens live only in process memory.

## Requirements

- Windows 10/11
- Python 3.12+ on PATH (`python --version`)
- A free port (default 7070)
- An Upstox and/or Fyers developer app (API key + secret)

## Start

Double-click `start_market_hub.bat`, then open <http://localhost:7070/ui/>.

The script starts the server from its own folder; press Ctrl+C (or close the
window) to stop. Logs are written to `data/logs/markethub.log`.

## First Run

On a fresh install there is no database, no credentials, no catalogs:

1. Open **Settings** → save your broker App credentials (encrypted).
2. Open **Instruments** → run the catalog sync for your provider(s).
3. Create a **Watchlist** and add instruments.
4. Click **Login** for your provider to start the daily session.

## Upstox Setup

1. Settings → Upstox → enter API Key + Secret → Save.
2. Check that the Redirect URL in your Upstox developer app is EXACTLY the
   callback shown in Settings (default `http://localhost:7070/auth/upstox/callback`).
3. Click **Login with Upstox** and approve on the Upstox page.

Upstox sessions are **daily**: the access token is memory-only, so after a
MarketHub restart you log in again. Credentials themselves persist encrypted.

## Fyers Setup

1. Settings → Fyers → enter App ID + Secret Key → Save.
2. Register the exact redirect URL shown in Settings in your Fyers developer
   console (default `http://localhost:7070/auth/fyers/callback`). If you open
   MarketHub from another address, set it under Settings → Application →
   Public Base URL (restart required).
3. Click **Login with Fyers** and approve.

Fyers provides a refresh token: MarketHub stores it encrypted and uses it at
startup to restore the session automatically when possible. If the refresh
is rejected you get a clear *Login Required* state instead of an error.

## Daily Use

- Markets page: live quotes/depth for your watchlists.
- Option Chain / Charts: pick an underlying or instrument from the synced
  catalog.
- Alerts: threshold and crossing rules; every firing is recorded in Trigger
  History even across restarts.

## Sources Controls

The Sources page shows each feed's state (Streaming / Connecting / Stopped /
Login Required), frame counters, reconnect counts, and recent transitions.
Start / Stop / Restart buttons control each source independently. A stopped
feed with valid auth can be restarted without logging in again.

## Instrument Sync

Instruments → Sync loads the official instrument master for a provider.
Syncs are manual and safe: a failed or interrupted sync leaves your existing
catalog untouched.

## Watchlists

Multiple lists, reorder, rename, import/export (JSON). An instrument in two
lists stays subscribed until removed from both.

## Alerts & Trigger History

Rules evaluate against canonical quotes; one crossing fires once until
re-armed. Every firing is appended to `alert_trigger_history` (survives
restarts, included in backups). Deleting an alert does not erase its past
trigger records.

## Backup / Recovery

Settings → **Create Backup** writes a timestamped copy of the database to
`data/backups/` (contains ciphertext only). To restore manually:

1. Stop MarketHub.
2. Copy the backup file to `data/events.db`.
3. Ensure `data/master.key` matches the one used when the backup was made —
   without the matching key, stored credentials cannot be decrypted (the app
   reports them as unconfigured rather than crashing).

There is intentionally no destructive one-click restore.

## Logs

`data/logs/markethub.log`, rotating at 10 MiB with 5 backups, UTF-8.
Lifecycle events, OAuth outcomes, and source transitions are logged;
credentials, tokens, and authorized URLs are never written.

## MCP

MarketHub exposes ~27 read-only MCP tools (quotes, depth, market status,
instrument search, watchlists, option chain, history, alerts, sources).
No order or trading tools exist. Point an MCP client at `http://<host>:7070`
(streamable HTTP).

## Known Limitations

- **Fyers production session pending live validation.** The implementation
  is complete offline-tested (login URL construction, callback state
  handling, encrypted store, refresh restore); one real login is required to
  confirm end-to-end.
- **Upstox restart requires daily login** (access token is runtime-only by
  design; Upstox offers no refresh flow in this integration).
- Whole-market scanner deferred pending a curated-universe and provider-limit
  policy.
- Market status outside session hours is inferred from IST time and clearly
  labelled — not broker-confirmed.
- Quotes older than 5 minutes are marked [STALE] in the UI.
