# Release Gate — MarketHub RC

Check every box before merge/tag. Nothing here is automated-destructive;
git operations are manual and deliberate.

## Preconditions

- [ ] Branch: `feature/market-phase-c-sse`
- [ ] Clean git working tree (`git status` empty)
- [ ] HEAD recorded: `________________`
- [ ] Pushed HEAD == local HEAD

## Local test groups (python test/run_all.py --group <name>)

- [ ] fast
- [ ] source
- [ ] lifecycle
- [ ] web_ui (covered by fast; run standalone if web changes)
- [ ] unit
- [ ] mcp
- [ ] consumer
- [ ] performance
- [ ] all (`--group all`)

## CI

- [ ] `full-regression` workflow green on the exact pushed HEAD
      Run ID: `________________`  URL: `________________`

## Security

- [ ] Repository secret scan clean (no API secrets, bearer/refresh tokens,
      auth codes, master.key, runtime DBs, WSS URLs in tracked files;
      clearly-synthetic test fixtures excepted)
- [ ] `config.example.json` contains no credential fields
- [ ] `master.key` not tracked by git

## Live acceptance

- [ ] Fyers live checklist (`docs/FYERS_LIVE_CHECKLIST.md`) completed
      Result: `________________`  Date: `________________`

## Finalize (only after ALL boxes above)

1. Merge `feature/market-phase-c-sse` → main
2. Tag `v0.3.0-rc` on main
3. Announce with RELEASE_NOTES.md
