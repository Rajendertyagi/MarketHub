# MARKETHUB — B8 PRODUCTION HARDENING REPORT

## 1. Starting / Final HEAD

| | SHA |
|---|---|
| Starting (origin/main) | `4d0ceb558238ef00173fc5475b245b72a86e50d0` |
| Final (b8-production-hardening) | `eb408eb6d729e213870275e156c5365fca2dc50d` |

## 2. Exact Production Files Changed

| File | Change |
|---|---|
| `app/condition_alerts.py` | FIX 1: Clean up `_alert_locks` in `reload()` for deleted alerts |
| `app/market_analytics.py` | FIX 2: Concurrent chain refresh with configurable bound |
| `core/persistence/modules/delivery.py` | FIX 3: Single relevance check replaces 3 SELECTs |
| `core/persistence/store.py` | FIX 3: Cached connection eliminates per-call open/close |
| `test/test_condition_alert_live_delivery.py` | Add `store.close()` before temp dir cleanup |

## 3. FIX 1 — `_alert_locks` Retention

**Problem:** `_alert_locks` grew unboundedly across create/delete cycles because `reload()` rebuilt `_alerts`, `_dep_index`, `_alert_deps`, `_state` but never cleaned up `_alert_locks`.

**Fix:** In `reload()`, after rebuilding state, iterate `_alert_locks` and remove entries for alerts not in the new `_alerts` set. Skip locks currently held by suspended evaluations (check `lock.locked()`).

**Race-safety proof:**
- `reload()` is synchronous and runs on the event loop thread
- `evaluate()` is async on the same thread — no true concurrency
- `evaluate()` reads `_dep_index` under `self._lock` (threading.Lock), then releases it before acquiring the asyncio lock
- If `reload()` runs while `evaluate()` is suspended mid-evaluation, the suspended coroutine holds the asyncio lock object (reference), which remains valid even if removed from `_alert_locks`
- `lock.locked()` prevents removing locks held by suspended evaluations
- Next `reload()` cleans up any remaining stale entries

**Tests (9/9 pass):**
- T1: Lock created on evaluate, removed on delete+reload
- T2: 3 cycles × 50 alerts — lock count returns to baseline (bounded)
- T3: Concurrent evaluate + delete — no exception, consistent state
- T4: Delete one alert — other alert's lock preserved
- T5: Fresh engine has zero locks

## 4. Scheduler Design & Concurrency Bound

**Design:** `_refresh_all_active()` uses `asyncio.Semaphore(max_concurrent_refreshes)` to bound concurrency. Each chain runs `_refresh_one()` which has its own per-chain `asyncio.Lock` for same-chain dedup.

**Bound:** Default `max_concurrent_refreshes=4` (configurable via constructor). This prevents unbounded REST call fan-out while allowing independent chains to refresh in parallel.

**Failure isolation:** `asyncio.gather(return_exceptions=True)` ensures one chain's failure does not cancel others. Each `_refresh_one()` catches its own exceptions and increments `_failure_count`.

## 5. Slow-Chain BEFORE/AFTER

**BEFORE (sequential):**
```
A=50ms, B=1000ms, C=50ms → total ≈ 1100ms
C waits ~1s behind B
```

**AFTER (concurrent, bound=4):**
```
A=50ms, B=1000ms, C=50ms → total ≈ 1050ms (dominated by B)
C starts within 0.3s of t=0, does NOT wait for B
```

**Test T5 proves:** C's start time < 0.3s (not 1.0s+).

## 6. Failure-Isolation Result

**Test T2:** B raises RuntimeError → A and C still succeed. B's snapshot is None. A and C have valid snapshots. Failure does not propagate to other chains.

## 7. Same-Chain Dedup Result

**Test T3:** Two alerts depend on same chain key → exactly 1 REST call (not 2). Per-chain lock in `_refresh_one` ensures dedup.

## 8. ACK Root Cause

**Root cause:** Each `acknowledge_event` call opened a fresh SQLite connection (4 PRAGMAs), ran 3 redundant SELECT validation queries + 1 UPDATE + commit, then closed the connection. For 10K ACKs: 10K connection open/close cycles + 30K redundant SELECTs.

## 9. ACK BEFORE/AFTER

**BEFORE (per-call connection + 3 SELECTs):**
```python
conn = self._open(self._db_path)  # new connection + 4 PRAGMAs
BEGIN IMMEDIATE
SELECT 1 FROM consumers WHERE consumer_id = ?        # redundant
SELECT 1 FROM persistent_events WHERE id = ?          # redundant
SELECT 1 FROM consumer_event_state WHERE ...           # relevance
UPDATE consumer_event_state SET acknowledged_at = ...
COMMIT
conn.close()
```

**AFTER (cached connection + 1 SELECT):**
```python
conn = self._get_conn()  # reused connection, no PRAGMAs
BEGIN IMMEDIATE
SELECT acknowledged_at FROM consumer_event_state WHERE consumer_id = ? AND event_id = ?
  → if row: UPDATE + COMMIT (happy path: 1 query)
  → if None: consumer check + event check + rollback (error path: 2 extra queries)
```

**Semantic preservation:**
- Durable delivery: ✓ (transaction + commit preserved)
- Per-consumer ownership: ✓ (consumer_id validated)
- Acknowledgement/checkpoint semantics: ✓ (idempotent CASE WHEN preserved)
- Idempotency: ✓ (first ack time preserved, re-ack returns True)
- Replay correctness: ✓ (acknowledged_at set, pending_count decrements)
- Error semantics: ✓ (ConsumerNotFoundError, EventNotFoundError, EventNotRelevantError preserved)

**Performance results (B8 tests):**
- ACK 100: p50 < 25ms ✓
- ACK 1000: p50 < 25ms ✓
- Correctness: pending=0 after ACK, idempotent re-ACK safe, event history intact

## 10. Focused Test Results

| Suite | Result |
|---|---|
| B8 FIX 1 (alert locks) | 9/9 pass |
| B8 FIX 2 (analytics scheduler) | 10/10 pass |
| B8 FIX 3 (ACK performance) | 17/17 pass |
| Condition alert stress | 17/17 pass |
| B7 multi-target | 33/33 pass |
| Condition groups | 20/20 pass |
| Acknowledgement | 16/16 pass |
| Alert trigger events | 12/12 pass |
| Product foundations | 7/7 pass |
| Alert reliability | 7/7 pass |
| Live delivery | 6/6 pass |

**Total B8 tests: 36/36 pass**

## 11. Full Regression Result

```
pytest -q: 715 passed, 5 failed, 2 skipped
```

**5 failures are ALL pre-existing on main:**
1. `test_broker_analytics.py::test_normalize_from_rest` — NewsSnapshot interface mismatch (upstox_news.py uses old flat fields)
2-5. `test_market_sse.py` (4 tests) — `NameError: name 'app' is not defined`

**0 new failures introduced by B8 changes.**

## 12. GitHub Run ID + Exact Head SHA

- Branch: `b8-production-hardening`
- Head SHA: `eb408eb6d729e213870275e156c5365fca2dc50d`
- Workflow: `.github/workflows/b8-production-hardening.yml` (manual dispatch)
- Note: Workflow requires push to default branch to be discoverable by GitHub Actions. All verification performed locally.

## 13. Unrelated Baseline Failures

5 pre-existing failures on main (see #11). These are NOT caused by B8 changes.

## 14. Audit Branch Not Merged

✅ Confirmed. `audit/b8-performance-measurement` exists locally but was NOT merged into `b8-production-hardening`.

## 15. `upstox_news.py` Not Carried

✅ Confirmed. `git diff origin/main...HEAD --name-only` shows no `upstox_news.py` changes. The audit-only NewsSnapshot fix is NOT included in B8.
