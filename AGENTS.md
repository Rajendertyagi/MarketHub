# Agent Rules — MarketHub

Read **`docs/ARCHITECTURE_BOUNDARIES.md`** before editing. It defines the
subsystem map, allowed/forbidden dependencies, the protected core, red-zone
files, and verification per zone.

## Before editing
Declare the subsystem and the exact files you intend to touch.

## During work
Do **not** cross into a protected subsystem (credential storage, token/session
persistence, broker login, session restoration, application startup, source/feed
lifecycle, persistence schema, canonical market models).

## If crossing is required
**STOP.** Do not edit. Report: (1) which protected subsystem, (2) why, (3) the
exact dependency forcing it, (4) files required, (5) regression risk. Wait for a
separate approved task.

## After work
Inspect `git diff` and verify only the affected boundary plus the protected
baseline. **Login must not regress.**

## Guardrails
Run `pytest test/test_architecture_boundaries.py` — it enforces import
boundaries (Options/News must not reach auth/secrets; WebUI must not import
broker code). Keep it green. Add new edges only when already clean.
