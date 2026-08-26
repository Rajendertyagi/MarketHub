# Fyers Live Acceptance — Operator Checklist

Do this once, in order. Expected result is noted for every step.
Total time: ~10 minutes. Do NOT proceed past a step that mismatches.

## Preparation (before clicking anything)

1. Your Fyers developer app must have this exact Redirect URL registered:
   `http://localhost:7070/auth/fyers/callback`
   (If you open MarketHub from a different address/port, set Settings →
   Application → Public Base URL to that address first, restart, and use
   the callback shown there instead.)

## Steps

| # | Action | Expected |
|---|--------|----------|
| 1 | Start MarketHub (`start_market_hub.bat`), open `/ui/` | Dashboard loads, no errors |
| 2 | Settings → Fyers panel | App Credentials = "Not configured", Daily Login = "Credentials Required" |
| 3 | Enter App ID + Secret Key → Save | Message "Fyers credentials saved."; App Credentials chip → "Configured"; Daily Login → "Login Required" |
| 4 | Check displayed Fyers Callback URL | Identical to the URL registered in the Fyers console (step 0) |
| 5 | Click **Login with Fyers** | Browser redirects to Fyers authorize page with your App ID |
| 6 | Complete Fyers authorization | Redirect back to MarketHub; Settings shows "Fyers authentication successful." |
| 7 | Settings → Fyers Daily Login chip | "Daily Login Active" |
| 8 | Sources page → fyers source state | "Streaming" within ~10 s |
| 9 | Add one NSE instrument (e.g. NIFTY 50) to a watchlist | Quote appears; LTP updates live |
| 10 | REST check: `GET /api/market/quote/NSE/<token>` | JSON quote with finite values |
| 11 | SSE check: open `GET /api/market/stream` | `quote:` frames arriving |
| 12 | Depth where supported | Bid/ask levels render, or honest "unavailable" |
| 13 | Sources → Stop (fyers) | State "Stopped"; quotes freeze |
| 14 | Sources → Start (fyers) — WITHOUT logging in again | "Streaming" again (token still in memory) |
| 15 | Restart MarketHub (close window, relaunch) | Startup log shows refresh-token restore; fyers → Streaming without manual login |
| 16 | `data/logs/markethub.log` review | Restore outcome logged; NO app id/secret/token values anywhere |

## Failure classification (what you should see if something fails)

- Wrong App ID/Secret at step 5/6: login page error or callback message
  "Fyers authentication failed…" — fix credentials, repeat from step 3.
- Redirect mismatch: Fyers shows its own redirect-uri error — re-check
  step 0 character-for-character.
- Refresh rejected at step 15: Daily Login chip shows **Login Required**
  (not a crash) — click Login with Fyers once; this is expected after
  token revocation/password change only.
- Feed stuck "Connecting": check log for classification line; network or
  session issue — do not retry more than twice before investigating.

## After passing

Record: date/time, HEAD commit, and "FYERS LIVE VERIFIED" in the release
gate checklist (`docs/RELEASE_GATE.md`), then run final CI, merge, tag RC.
