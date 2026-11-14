# MarketHub — Project Context & Handoff (Living File)

> **Purpose:** durable handoff so a new chat / AI-coding-agent session can resume
> MarketHub work from the *actual* project state without re-explaining. Read this
> plus `AGENTS.md`, `docs/ARCHITECTURE_BOUNDARIES.md`, and
> `docs/PROJECT_MANAGER_PROTOCOL.md` before acting. **Verify the real repo state before
> acting** — counts/SHAs here are snapshots, not assumptions.
>
> **Living file.** Update after meaningful investigations, approved changes, test
> runs, decisions, or blocker changes. Keep a concise current-state section + a
> short dated change log; mark superseded claims as such.

---

## 1. How to resume

1. Read **this file** → `AGENTS.md` → `docs/ARCHITECTURE_BOUNDARIES.md` (zones, protected core, §21 auth/startup frozen) → `docs/PROJECT_MANAGER_PROTOCOL.md`.
2. Re-inspect actual state: `git status`, branch/HEAD, `git log -1`, and run relevant checks. Do not assume baselines are current.
3. **Do not start coding** because an investigation looks complete. Present findings + plan and wait for explicit approval (see `docs/PROJECT_MANAGER_PROTOCOL.md` / `AGENTS.md`).
4. **No destructive Git ops** (`reset`/`clean`/force push/history rewrite/amend) and **no live service restart or live DB modifications** without explicit authorization. Preserve unrelated working-tree changes.

## 2. Current repo state (verified 2026-11-14, after Sprint 04 correction publication)

| Item | Value |
|---|---|
| Repo | `D:\Temp\MarketHub` / `Rajendertyagi/MarketHub` |
| Current branch | `develop` |
| `develop` / `origin/develop` | `501edeb8d3bee33e606a75a00e4c4f734558775f` — `fix(dashboard): stabilize deferred widget observers and placeholders` (Sprint 04 follow-up corrections published; in sync) |
| `main` / `origin/main` (default, stable) | `4d0ceb558238ef00173fc5475b245b72a86e50d0` — `fix(alerts): restore baseline-first crossing semantics` (**untouched**) |
| Permanent branches | **Exactly 2:** `main`, `develop` |
| Protections | `main`: PR required (0 approvals) + required status check `lint` (non-strict), no force/delete. `develop`: no force/delete (practical; no PR requirement / no required status checks on push). Default branch `main`. |
| Tags/releases (intact) | Tags: `exe-35064251523`, `exe-35072882240`, `exe-35082963187`, `exe-35229434409`, `latest-exe`, `v0.3.0-rc.1`. Existing GitHub releases/assets preserved. |
| Preserved untracked files (do not touch) | `config.json.pre_tbt_activation_20261111_191221.bak`, `docs/performance/2026-11-10-backend-optimization-simplification-architecture-review.md`, `docs/performance/2026-11-10-backend-performance-audit.md`, `docs/performance/2026-11-10-full-stack-performance-reliability-audit.md`, `docs/performance/2026-11-10-scanner-phase1-implementation-plan.md`, `docs/performance/2026-11-10-webui-system-performance-audit.md`, `docs/performance/2026-11-13-webui-live-performance-inspection.md` |

## 3. Verified baselines (point-in-time; re-verify before relying)

- **2026-11-10 (historical):** Backend `1754 passed, 4 skipped, 0 failed`; frontend `35 files / 199 tests passed, 0 failed`; frontend typecheck/build pass; architecture/auth guardrails green (targeted 49 passed). **Login must not regress.**
- **2026-11-14 Sprint 04 WS1-04 (published `37c76d3`):** targeted dashboard tests `2 files / 7 tests passed` (`dashboardLive.test.tsx`, `dashboardDeferred.test.tsx`); full frontend suite `35 files passed, 1 failed; 199 tests passed, 2 failed` — `src/__tests__/EChart.test.tsx` (2 tests) failed identically on parent `ff6ccea` in isolated temp-worktree comparison (pre-Sprint 04), not modified; frontend typecheck pass; Biome pass on changed dashboard/test paths (full `biome check src` has pre-existing unrelated formatting drift in `src/app/routes.tsx`, `src/features/dashboard/components/IndicesWidget.tsx`); isolated production build pass. Backend/architecture guardrails not rerun in this frontend-only sprint.
- **2026-11-14 Sprint 04 follow-up corrections (published `501edeb`):** stabilized `IntersectionObserver` options/effect deps to prevent observer churn on dashboard rerenders, added lightweight reserved placeholder/layout space for deferred secondary slots, strengthened deferral regression tests (IO fallback, cleanup, rerenders, no duplicate requests, single activation per widget, critical requests immediate), and corrected stale documentation. Verification: dashboard tests `2 files / 10 tests passed` (`dashboardLive.test.tsx`, `dashboardDeferred.test.tsx`); full frontend suite `35 files passed, 1 failed; 202 tests passed, 2 failed` — `src/__tests__/EChart.test.tsx` (2 tests) failed identically on parent `ff6ccea` in isolated temp-worktree comparison (pre-Sprint 04), not modified; typecheck pass; Biome pass on changed dashboard/test/style paths; isolated production build pass (`vite build --outDir %TEMP%\markethub-sprint04-fix-dist`, `✓ built`, existing large lazy-chunk warnings unchanged). GitHub Actions `ruff` run https://github.com/Rajendertyagi/MarketHub/actions/runs/43620202777 — **success**. Backend/architecture guardrails not rerun in this frontend-only pass.

## 4. Recent completed work (selected, on `develop`)

- **2026-11-10 consolidation/remediations (historical):** `686ee85`, `58fb0be`, `cfe8320`, `281302e`, `3c88e5b`, `541f16c`, `e87d182`, `400e419`, `b90adce`, `beb60ea`, `80ee786`, `9877a21`, `f4e6596` (protocol later relocated to `docs/PROJECT_MANAGER_PROTOCOL.md`). Branch consolidation completed (obsolete branches deleted with approval); `main`/`develop` retained. B8A benchmark evidence not durably present in repo/workspace after approved deletion of `project-archive/`.
- **2026-11-14 performance/protocol work (published on `origin/develop`):** Sprint 01 `0fc3e58`, `d7ccfa5`, `c99404f`; Sprint 02 `fb6231b`, `4213ece`, `a4d4d4b`, `f7281db`; Sprint 03 `75b9ae8`; protocol updates `e0dc8cc`, `b00877a`, `ff6ccea`. See `docs/superpowers/plans/2026-11-13-current-webui-load-performance-fix.md` for evidence, measurements, and limitations.
- **2026-11-14 Sprint 04 WS1-04 (published):** `37c76d3f1e87c5011fa66e9abe4603f7a6a5c6ee` — dashboard shell vs secondary data separation: deferred visibility-aware mount/fetch for Sectors, Breadth, F&O, Movers in `frontend/src/features/dashboard/DashboardView.tsx` via `frontend/src/features/dashboard/useInViewport.ts`; critical `useMarketQuotes`/SSE/ticker/indices remain immediate; widget query keys, polling intervals, freshness, and functionality preserved once visible. Added `frontend/src/__tests__/dashboardDeferred.test.tsx`. EChart failures confirmed pre-Sprint 04 vs `ff6ccea` (isolated temp worktree comparison).
- **2026-11-14 Sprint 04 follow-up corrections (published):** `501edeb8d3bee33e606a75a00e4c4f734558775f` — independent-review fixes on top of `37c76d3`: stable observer options/deps, reserved placeholder/layout space for deferred slots, strengthened regression tests, and documentation corrections. EChart failures confirmed pre-Sprint 04 vs `ff6ccea` (isolated temp worktree comparison). GitHub Actions `ruff` https://github.com/Rajendertyagi/MarketHub/actions/runs/43620202777 — **success**. See canonical plan for evidence.

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
- `docs/PROJECT_MANAGER_PROTOCOL.md` — required workflow, approval, evidence, and safety rules.
- `test/test_architecture_boundaries.py` — import-boundary/auth-ownership guardrail (keep green). Ruff gate is pinned/minimal per `pyproject.toml` and CI config.
- **Security:** never store credentials, access tokens, API secrets, or OAuth codes in this file.

## 8. Dated change log

- **2026-11-10** — **Refreshed this handoff to current verified state** (doc-only). Updated repo/branch/protection/tag-release state (`develop`@`f4e6596`, `main`@`4d0ceb5`), verified baselines (backend 1754 passed/4 skipped; frontend 35/199 passed; typecheck/build/architecture+auth checks green), recent remediations, EXE `v*` tag workflow, and branch consolidation outcome. **Removed/superseded obsolete content:** stale `fno-provider-agnostic`@`3f4b8d9` primary state, R10–R18 S1–S15 planning/investigation detail and round tables presented as current, old “Next agreed action” (S7/S8/S1–S15 sequence), outdated test-failure baselines tied to the old in-flight tree, and completed cleanup items. Preserved unresolved protected-core item (FYERS expiry semantics) and Phase 4 deferred/design-only status with clear caveats. No code/config/DB/workflow logic/tests changed.
- **2026-10-18–2026-10-20 (historical, superseded detail removed)** — Prior R5–R18 investigations, S1–S13/S15 planning notes, lint/runtime/U1–U7 validation narratives, and old baselines existed in earlier revisions of this file; they reflected a different branch/working-tree state and are no longer maintained here. Consult Git history (`git log -- docs/PROJECT_CONTEXT.md`) only if that historical context is specifically needed, and re-verify against current code before relying on it.
