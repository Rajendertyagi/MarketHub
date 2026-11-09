# Live-validation evidence — 2026-10-20 (Fyers, market hours)

Captured with an authenticated Fyers session during the IST session
(09:38–10:05 IST, 2026-10-20). The runtime data directory was `data/`
and the app bound `127.0.0.1:7070`.

Environment facts (verified, not assumed):

| Item | Value |
|---|---|
| Runtime DB | `D:\Temp\MarketHub\data\events.db` |
| Runtime master key | `D:\Temp\MarketHub\data\master.key` |
| Runtime catalogue | 142,932 instrument rows (fyers 89,713 + upstox 53,219) |
| F&O restore at startup | `fno coverage restored at startup: 440 contract(s) routed` |
| Fyers auth state | `authenticated`, `login_required=false`, `last_auth_status=restored_access_token` |
| Fyers feed state | `streaming` |
| Market hours | open (IST 09:15–15:30) |

---

## U2 — Fyers REST index live-correctness: **PASSED**

Three REST snapshots, then compared against an INDEPENDENT published
reference (Yahoo Finance chart API — neither Fyers nor Upstox).

| Instrument | Fyers REST | Published reference | Difference |
|---|---|---|---|
| NIFTY 50 | 22888.95 | 22888.95 | **0.00 pt** |
| BANKNIFTY | 55488.10 | 55488.10 | **0.00 pt** |

Spread across the 3 snapshots: 0.0000 pt (stable). Tolerance ≤ 0.05 pt — **met**.

## U3 — Fyers option-chain greeks + OI fields: **PASSED** (and it RESOLVED the
## R11 "aggregate whole-expiry" blocker)

Request: `GET /data/options-chain-v3?symbol=NSE:NIFTY50-INDEX&strikecount=50&greeks=1`

- **202 legs carried greeks** (`iv`/`delta`/`gamma`/`theta`/`vega`) — the
  `greeks=1` request parameter works.
- **202 legs carried `oichp`** → mapped to `OptionContractData.oi_change_percent`.
- **Aggregate OI observed:** `callOi = 145,731,720`, `putOi = 127,688,035`.

### The aggregate scope question (previously UNVERIFIED)

Method: hold the expiry fixed (`2026-10-27`) and vary only the requested
window width, then compare the provider aggregate against the sum of the
returned legs.

| `strikecount` | strikes returned | window range | aggregate `callOi` | window leg-sum |
|---|---|---|---|---|
| 5  | 11  | 22650–23150 | 145731720 | 29,680,760 |
| 10 | 21  | 22400–23400 | 145731720 | 54,812,935 |
| 25 | 51  | 21650–24150 | 145731720 | 108,544,235 |
| 50 | 101 | 20400–25400 | 145731720 | 141,069,725 |

- The aggregate is **CONSTANT** across every window width.
- The window leg-sum **varies** with the width.
- The aggregate **exceeds** the widest window.
- The catalogue lists **261 distinct strikes** (17000–30000) for that expiry —
  far wider than any requested window.

**Conclusion: the Fyers `callOi`/`putOi` aggregate IS a whole-expiry total**,
not a window sum. The scope label was changed from `aggregate-unverified` to
`whole-expiry` (evidence-backed), with `aggregate-unverified` retained as the
default for any aggregate whose scope has not been established.

Window-scoped analytics remain window-scoped and are labelled as such:
`compute_pcr` → `pcr=0.8339, scope=window`;
`compute_max_pain` → `max_pain=23000, scope=window, bounded=true`;
`compute_pcr_aggregate` → `pcr=0.8762, scope=whole-expiry`.

## U1 — Fyers HSM index scaling: **MEASURED (defect confirmed; guard is correct)**

The feed's OWN frame builders and decoder were used against the live HSM
websocket (`wss://socket.fyers.in/hsm/v1-5/prod`):

- AUTH frame → **accepted** (`auth_ok=True`).
- Market-data frames (`resp_type 6`) **are** delivered.
- Decoded index snapshot:

| Field | Value |
|---|---|
| topic | `if\|nse_cm\|Nifty Bank` |
| multiplier | **32** |
| precision | 0 |
| raw `ltp` | 5548810 |
| raw `prev_close_price` | 5584400 |

- Feed's scale formula `(10**precision) * multiplier` = **32** → 5548810/32 =
  **173,400.31** (error **+117,912.21 pt**, ratio **3.1250×**).
- Correct scale **100** → 5548810/100 = **55,488.10** → matches the
  authoritative REST value **exactly (0.00 pt)**, and prev_close
  5584400/100 = 55,844.00 also matches.

**Conclusion: the HSM index `multiplier` is wrong (32 instead of 100), so the
feed's index guard (drop `if`-prefixed topics, `brokers/fyers/feed.py:945`)
is CORRECT and must stay.** The Fyers HSM index channel is therefore NOT a
usable index source; the guarded REST index path (S10) is the correct
fallback. U1's "≤0.05 pt" tolerance is **not achievable** over the HSM
index channel and **must not** be claimed.

## U4 / U5 / U6 — Upstox `full`-mode fields, V3 `previous_oi`, account tier: **BLOCKED**

Upstox is not authenticated (`last_auth_status=rejected`, no token
configured). These require an Upstox session.

## U7 — Catalogue prerequisite: **RESOLVED**

Resolved by the runtime data-directory fix: the active catalogue is
`data/events.db` (142,932 rows). The previous `data_test/events.db` was
empty (0 rows), which is why the app restored 0 F&O contracts before.
`/api/fno/coverage` now reports `desired=440, routed=440` with real
expiries (2026-10-27 … 2026-12-31).

## Dual-provider failover: **NOT RUN**

Upstox is unauthenticated, so a dual-provider failover scenario could not be
exercised.
