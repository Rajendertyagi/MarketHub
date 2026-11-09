# Data-foundation blockers — implementation report (2026-10-22)

Scope: provider-state reconciliation, canonical quote lookup, FYERS reconnect
delivery, freshness/last-known handling, FYERS BSE F&O catalogue coverage,
alert/MCP/analytics parity and the remaining error flooding.

Branch `fno-provider-agnostic` @ `3f4b8d9`. Live run: `python -m app.server`
(pid 3872 → restarted after the final edits), port 7070.

## Files changed

| File | Change |
| --- | --- |
| `app/subscriptions/manager.py` | identity metadata on subscribe, availability-driven reconcile, `provider_unavailable` vs `capacity_limited`, failover reversion, truthful per-provider apply reporting, catalogue read-model cache, reconcile debounce + transient-state filter |
| `app/quote_service.py` | canonical resolution of any known identifier, per-kind freshness policy, `enrich()`, sync canonical reader `quote_now()`, catalogue/epoch caches |
| `api/routes.py` | builds the shared quote service when the composition root does not inject it; canonical reader for breadth/sector/scanner/market-map; SSE snapshot + live frames through the shared service |
| `api/fno_routes.py` | F&O chain quotes read through the shared service (canonical id + freshness) |
| `app/condition_alerts.py` | alert freshness window aligned with the shared policy (300 s → 120 s) |
| `app/catalog_sync.py` | a master that parses but contributes nothing is logged VISIBLY |
| `frontend/src/features/dashboard/format.ts` | the UI trusts the backend's `stale` verdict |
| `frontend/src/features/dashboard/components/TickerStrip.tsx` | stale tooltip wording |
| `frontend/dist/**` | rebuilt bundle |
| `test/test_subscription_reconcile_lifecycle.py` | new: reconciliation lifecycle regression tests |
| `test/test_quote_canonical_parity.py` | new: canonical lookup parity tests |
| `test/test_fyers_bse_fo_catalogue.py` | new: FYERS BSE F&O segment coverage (isolated DB) |
| `test/test_quote_coordinator.py` | updated: a stale-only quote stays visible as last known |

## Root causes fixed (live evidence)

1. **No identity on runtime-subscribed keys.** `SubscriptionManager.reconcile()`
   called `feed.add_instruments(keys)` without the `(exchange, tradingsymbol)`
   binding, so the Upstox feed dropped every tick for a runtime key as an
   unknown instrument. Now bound from the catalogue → the store went from
   11 to 663 quotes (`upstox quote_updates=11,316`, `subscribed_instruments=663`).
2. **Availability never re-planned the demand.** The manager now chains into
   the feeds' own `on_state_change` listener (`brokers/upstox/feed.py:340`,
   `brokers/fyers/feed.py:346`) and re-plans the existing demand on a
   significant state change: `reconcile applied for upstox=651 added` with no
   API click.
3. **A failed-over lease never came back.** The lease remembers its origin
   and returns to the demand's provider when that provider recovers.
4. **An unreachable provider was reported as capacity exhaustion.** 574
   contracts moved from `capacity_limited` to `provider_unavailable`;
   capacity is only reported when capacity actually prevented the allocation.
5. **A failed apply was logged as a success.** Per-provider failures are now
   `applied: false` with the reason; one failing provider no longer aborts
   the other.
6. **Three identity conventions.** The store's `canonical_id` is the
   provider-native storage key (`market/service.py`), the registry publishes
   the provider-neutral id, and the REST bulk route bypassed the shared quote
   service entirely (`build_market_routes` was called without
   `quote_service`). The shared service now resolves any known identifier
   (canonical id, storage key, market identity, ISIN), selects the best
   candidate across providers and reports the provider-neutral id plus
   `storage_key`, `provider`, `as_of`, `age_seconds`, `stale`/`live`.
   Live: the ISIN form resolves (`NSE:EQUITY:INE040A01034`), single and bulk
   reads agree exactly, breadth went from `quoted: 0 / unavailable: 50` to
   `quoted: 46 / unavailable: 4`, sector heatmap `quoted=46`, the F&O chain
   reports `NSE:FUTURE:HDFCBANK:2026-10-27`.
7. **A blanket 5-minute freshness window.** Freshness is now judged per
   instrument kind (INDEX/EQUITY/ETF 60 s, FUTURE/OPTION 120 s, default
   120 s); a stale-only quote is kept with truthful `stale`/`live`/`as_of`
   provenance, and the alert window follows the same policy.
8. **FYERS BSE F&O catalogue gap.** The master URL, payload, parser and
   segment mapping are correct (read-only probe: 7,929 records → segment
   `BSE_FO`; the app's own fetch+parse works for all four masters). The live
   gap is a transient download failure at 09:26-09:29 ("download failed:
   OSError" / "parsed to zero records"); the fail-safe kept the previous
   rows, so no FYERS `BSE_FO` row was ever written. The refresh is an
   operator action (the app does not sync at startup) and was NOT run
   against the production database.
9. **Error flooding.** The live run shows no `socket.send()` exceptions, no
   `subscription reconcile failed` and no `fno coverage restore failed`; the
   reconcile log no longer repeats identical lines and a flapping socket no
   longer triggers a reconcile storm (transient states are ignored, the
   catalogue read models are cached per epoch).

## Protected edits still required (not applied)

* **`brokers/fyers/feed.py` (red zone)** — (a) `_send_mutation`
  (line 508) must refuse a socket that is not OPEN; (b) the session must
  clear `self._live_ws` UNDER `self._sub_lock` BEFORE closing the socket
  (lines 644 / 653 / 665 / 684 / 696 / 709) so a mutation can never race a
  close; (c) `status()` must expose `awaiting_data` while `streaming` and
  `last_data_at` is empty.
* **`brokers/upstox/feed.py` (red zone)** — the same open-socket guard in
  `_send_mutation` (line ~440).
* **`market/service.py` (protected canonical models)** — optional: resolve
  the stored `canonical_id` with the provider-neutral registry so the store
  itself carries one identity (the shared service already normalizes it).
* **`app/server.py` (protected startup)** — optional: inject
  `quote_service=_quote_service` into `build_market_routes` /
  `build_fno_routes` (the routes already construct the service themselves).

## Preservation

Production DB, credentials and the unrelated worktree were preserved:
`data/events.db` 73,543,680 B, `master.key` intact, 15 secret rows, branch
`fno-provider-agnostic` @ `3f4b8d9`, 539 worktree paths (no reset/stash/
clean). The catalogue was not re-synced; the only catalogue rows in the
live DB that are not provider rows are two fixture rows
(`fyers / NSE_EQ / NSE:SBIN-EQ` and `fyers / BSE_INDEX / BSE:SENSEX-INDEX`)
written by an earlier direct (non-pytest) test-script run — a live FYERS
catalogue sync would replace them.
