# X/Twitter Upstream Watch — twitter_search outage

## Status: waiting on upstream (no action until a release ships)

`twitter_search` fails for every query with X API HTTP 404 on the
`SearchTimeline` endpoint. Root cause is upstream, not in this repo:

- X's x-web frontend rollout removed the `ondemand.s` bundle tree.
- `xclienttransaction` 1.0.3 locates that bundle with
  `ON_DEMAND_FILE_REGEX` → `None.group(1)` → `AttributeError`, so the
  `x-client-transaction-id` header is never generated.
- `SearchTimeline` is the only endpoint enforcing that header → 404.
  (`feed`, `tweet`, `user-posts`, `user`, `bookmarks`, `status` work
  because their endpoints don't require it.)

References: `public-clis/twitter-cli` issues #78 / #88
(`iSarabjitDhiman/XClientTransaction` #41 / #42 / #43).

## Pins (verified 2026-09-23 — both are PyPI-latest, no fix exists)

- `twitter-cli == 0.8.5` (enforced via `PIN_VERSION` in `x_twitter/cli.py`)
- `xclienttransaction == 1.0.3`

## Trigger

Either of these becoming true on PyPI:

- `twitter-cli` > 0.8.5
- `xclienttransaction` > 1.0.3

Manual check: `python test/check_x_upstream.py` (read-only, hits the
PyPI JSON API, changes nothing).

## Action checklist (when triggered)

1. Install the candidate in an isolated check (do NOT upgrade the server env yet).
2. Live probe with valid cookies:
   `twitter search "Nifty" --type latest --max 1 --json` must return
   results with NO `Failed to init ClientTransaction` warning.
3. If green: bump `PIN_VERSION` in `x_twitter/cli.py`, update the pin
   check test, run the full X suite strict audit
   (`test/test_x_twitter.py`), plus MCP contract/registry suites,
   `ruff check` on owned paths, `bun run typecheck`/`lint`.
4. Revisit `search_unavailable`: if search works again, the
   `XNotFound → search_unavailable` mapping in `x_twitter/cli.py::search`
   stays as a safety net (a future rotation 404 still maps honestly),
   but confirm the steady-state path returns results.
5. Update this file with the new pins + verification date.

## Steady state until then

- `twitter_search` returns `{status: "error", code: "search_unavailable"}`
  with an honest reason — by design, not a bug.
- Do NOT vendor/patch the CLI locally, do NOT forward full browser
  cookie jars, do NOT chase alternative queryIds (all ruled out;
  fragile and outside the pin discipline).
