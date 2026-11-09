# MarketHub — Project Context & Handoff (Living File)

> **Purpose:** durable handoff so a new chat / AI-coding-agent session can resume
> MarketHub work from the *actual* project state without re-explaining. Read this
> plus `AGENTS.md`, `docs/ARCHITECTURE_BOUNDARIES.md`, and
> `PROJECT_MANAGER_PROTOCOL.md` before acting. **Verify the real repo state before
> acting** — counts/SHAs here are snapshots, not assumptions.
>
> **Living file.** Update after meaningful investigations, approved changes, test
> runs, decisions, or blocker changes. Keep a concise current-state section + a
> short dated change log; mark superseded claims as such.

---

## 1. How to resume

1. Read **this file** → `AGENTS.md` → `docs/ARCHITECTURE_BOUNDARIES.md` (zones, protected core, §21 auth/startup frozen) → `PROJECT_MANAGER_PROTOCOL.md`.
2. Re-inspect actual state: `git status`, branch/HEAD, `git log -1`, and run relevant checks. Do not assume baselines are current.
3. **Do not start coding** because an investigation looks complete. Present findings + plan and wait for explicit approval (see `PROJECT_MANAGER_PROTOCOL.md` / `AGENTS.md`).
4. **No destructive Git ops** (`reset`/`clean`/force push/history rewrite/amend) and **no live service restart or live DB modifications** without explicit authorization. Preserve unrelated working-tree changes.

## 2. Current repo state (verified 2026-11-10)

| Item | Value |
|---|---|
| Repo | `D:\Temp\MarketHub` / `Rajendertyagi/MarketHub` |
| Current branch | `develop` (clean, up to date with `origin/develop`) |
| `develop` / `origin/develop` | `f4e65962b162fff7ac3d78f599f5b9ebc6b09f62` — `docs: add Project Manager Protocol` |
| `main` / `origin/main` (default, stable) | `4d0ceb558238ef00173fc5475b245b72a86e50d0` — `fix(alerts): restore baseline-first crossing semantics` (**unchanged** during 2026-11-10 consolidation) |
| Permanent branches | **Exactly 2:** `main`, `develop` (obsolete branches deleted with approval; see change log) |
| Protections | `main`: PR required (0 approvals) + required status check `lint` (non-strict), no force/delete. `develop`: no force/delete (practical; no PR requirement / no required status checks on push). Default branch `main`. |
| Tags/releases (intact) | Tags: `exe-35064251523`, `exe-35072882240`, `exe-35082963187`, `exe-35229434409`, `latest-exe`, `v0.3.0-rc.1`. Existing GitHub releases/assets preserved. |

## 3. Verified baselines (2026-11-10, this session)

- **Backend:** `1754 passed, 4 skipped, 0 failed`.
- **Frontend:** `35 files / 199 tests passed, 0 failed`.
- **Frontend quality gates:** typecheck and production build pass.
- **Architecture/auth guardrails:** `pytest test/test_architecture_boundaries.py` and auth/startup freeze/regression checks green (targeted run: 49 passed). **Login must not regress.**
- Re-verify before relying on these numbers; they are point-in-time evidence.

## 4. Recent completed work (2026-11-10, on `develop`)

Validated remediations and consolidation (evidence-based; no protected auth implementation changes; no live 7070/DB changes; no EXE build/publish):

- `686ee85` — Fyers restore/web auth tests aligned to current contracts.
- `58fb0be` — deterministic date pinning for F&O/scanner tests.
- `cfe8320` — F&O strike sort fix.
- `281302e` — News/Market Map tests aligned to current UI; `MarketMapView` `useElementSize` callback-ref fix.
- `3c88e5b` — Scanner/Sector Heatmap tests aligned to current UI.
- `541f16c` — Alerts tests aligned; Fyers TBT reconnect backoff made interruptible; `test_tbt24_shutdown_during_backoff` made deterministic.
- `e87d182` — repo-addressable warning/lifecycle fixes (`core/runtime.py` no-op coro close, awaited barrier, `inspect.iscoroutinefunction`, `sources/test_source.py __test__ = False`, `websockets.asyncio.client.connect`).
- `400e419` — server lifecycle/config: top-level Starlette lifespan `@asynccontextmanager` (startup/shutdown ordering preserved) + uvicorn `ws="websockets-sansio"`.
- `b90adce` — merged remote-only `e95ac21` (`docs(plan): draft Phase 4 indicator catalog scope`); conflict in `docs/plans/2026-10-27-unified-catalog-and-chart-plan.md` resolved preserving local status/decisions and incorporating the drafted proposal as **non-authorized/deferred**.
- `beb60ea`, `80ee786` — frontend lint/type cleanups (unused `useRef` / unused ResizeObserver stub param).
- `9877a21` — `.github/workflows/build-exe.yml` made safe/tag-driven (see §5).
- `f4e6596` — added `PROJECT_MANAGER_PROTOCOL.md`.

**Branch consolidation (approved):** deleted local+remote `fno-provider-agnostic`, `feature/market-phase-c-sse`, `b8-production-hardening`, `audit/b8-performance-measurement` after containment/preservation checks. `main`/`develop` retained; no force push, no history rewrite, no tag/release deletion.

**B8A benchmark evidence:** preserved and verified (bundle + clean artifacts zip + README) outside Git tracking, then `D:\Temp\MarketHub\project-archive\` was deleted after verification. **No durable benchmark archive copy remains in the repo/workspace**; recover only from any external copy retained by the maintainer. Incompatible `bcd0074` non-benchmark hunks were excluded from integration by design.

## 5. EXE release workflow (current, prepared — not executed)

- File: `.github/workflows/build-exe.yml` (on `develop` @ `9877a21`).
- Triggers: **`v*` tag push** and **`workflow_dispatch`** with explicit `ref` input. **No automatic build on `develop`/`main` pushes.**
- Release builds use the tagged/explicit ref (not latest `develop`). Publishes **versioned** GitHub Releases for `v*` tags only; `prerelease` auto-detected (`rc/alpha/beta/pre`); **`make_latest=false`**, **`overwrite_files=false`**, `fail_on_unmatched_files=true`. Non-`v*` manual runs upload artifact only (no release publish).
- Build toolchain/steps unchanged in that prep (Windows, Bun 1.4.4, Python 3.14, Nuitka standalone, smoke probe). Floating dependency upgrades reduce reproducibility — consider pinning in a separate, approved change.
- **No EXE build or GitHub Release publishing was performed** as part of the prep/consolidation tasks.

## 6. Still-relevant unresolved / open items

- **FYERS expiry/refresh semantics (PROTECTED CORE — unresolved, no change made):** code derives ~15:30 IST when `expires_at` is absent (`brokers/fyers/auth.py`); restore treats missing expiry as still valid (`app/auth/fyers.py`); status treats unknown as `UNKNOWN`. A report notes official docs state 6:30 AM. **Do not change semantics** without a confirmed rendered official v3 auth contract and a **separate explicitly approved protected-core task**. Keep `test/test_architecture_boundaries.py`, `test_auth_freeze.py`, and auth/startup regression tests green. **Login must not regress.**
- **Phase 4 indicator catalog:** **deferred / design-only, non-authorized** (see `e95ac21` and `docs/plans/2026-10-27-unified-catalog-and-chart-plan.md`). No implementation approved by this handoff.
- **`main` promotion:** `main` is far behind `develop`; promote only via a separate reviewed PR/merge (respect protections). Do not rewrite history.
- **B8A benchmark archive:** not present in repo/workspace after approved deletion of `project-archive/`; any reintegration requires maintainer-provided evidence and adaptation/review against current `develop`.

## 7. Guardrails / key references

- `AGENTS.md` — task-zone declaration, no protected-subsystem crossing, stop/report if crossing required, verify `git diff` + affected boundary + protected baseline.
- `docs/ARCHITECTURE_BOUNDARIES.md` — subsystem map, allowed/forbidden deps, protected core, red-zone files, auth/startup frozen (§21).
- `PROJECT_MANAGER_PROTOCOL.md` — required workflow, approval, evidence, and safety rules.
- `test/test_architecture_boundaries.py` — import-boundary/auth-ownership guardrail (keep green). Ruff gate is pinned/minimal per `pyproject.toml` and CI config.
- **Security:** never store credentials, access tokens, API secrets, or OAuth codes in this file.

## 8. Dated change log

- **2026-11-10** — **Refreshed this handoff to current verified state** (doc-only). Updated repo/branch/protection/tag-release state (`develop`@`f4e6596`, `main`@`4d0ceb5`), verified baselines (backend 1754 passed/4 skipped; frontend 35/199 passed; typecheck/build/architecture+auth checks green), recent remediations, EXE `v*` tag workflow, and branch consolidation outcome. **Removed/superseded obsolete content:** stale `fno-provider-agnostic`@`3f4b8d9` primary state, R10–R18 S1–S15 planning/investigation detail and round tables presented as current, old “Next agreed action” (S7/S8/S1–S15 sequence), outdated test-failure baselines tied to the old in-flight tree, and completed cleanup items. Preserved unresolved protected-core item (FYERS expiry semantics) and Phase 4 deferred/design-only status with clear caveats. No code/config/DB/workflow logic/tests changed.
- **2026-10-18–2026-10-20 (historical, superseded detail removed)** — Prior R5–R18 investigations, S1–S13/S15 planning notes, lint/runtime/U1–U7 validation narratives, and old baselines existed in earlier revisions of this file; they reflected a different branch/working-tree state and are no longer maintained here. Consult Git history (`git log -- docs/PROJECT_CONTEXT.md`) only if that historical context is specifically needed, and re-verify against current code before relying on it.
