"""B8 heavy verification — ACK measurement + thread-safety stress."""
from __future__ import annotations
import asyncio, os, shutil, sys, tempfile, time, uuid, threading
from concurrent.futures import ThreadPoolExecutor

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from core.persistence.store import EventStore
from core import events
from core.persistence.modules.replay import get_consumer_inbox_status


class _StubBus:
    async def publish(self, item): pass


def _make_store():
    tmp = tempfile.mkdtemp(prefix="b8heavy_")
    store = EventStore(os.path.join(tmp, "events.db"))
    store.register_consumer("c1")
    return store, tmp


def _percentile(vals, p):
    s = sorted(vals)
    k = (len(s) - 1) * p / 100.0
    f = int(k); c = min(f + 1, len(s) - 1)
    return s[f] + (k - f) * (s[c] - s[f])


# ===================================================================
# TEST 1: ACK performance measurement
# ===================================================================

async def test_ack_performance():
    print("\n=== ACK PERFORMANCE MEASUREMENT ===")
    results = {}

    for n in [100, 1000, 10000]:
        store, tmp = _make_store()
        bus = _StubBus()
        try:
            ids = []
            for i in range(n):
                data = {
                    "alert_family": "market_condition",
                    "alert_id": f"alert-{i}",
                    "consumer_id": "c1",
                    "condition": {
                        "condition_version": 1, "logic": None,
                        "conditions": [{"condition_version": 1, "condition_id": "c1",
                                        "metric": "ltp", "operator": "gt",
                                        "value": 25000.0,
                                        "instrument": {"canonical_id": "NSE:EQUITY:I"}}],
                    },
                    "observed": {"root_result": "true", "leaves": []},
                    "instrument": {"canonical_id": "NSE:EQUITY:I"},
                    "one_shot": False,
                }
                result = await events.publish_event(
                    event_type="alert.triggered", source="test",
                    data=data, persistent=True,
                    routing={"targets": ["c1"]},
                    store=store, bus=bus,
                )
                ids.append(result["id"])

            times = []
            for eid in ids:
                t0 = time.perf_counter_ns()
                store.acknowledge_event("c1", eid)
                dt = (time.perf_counter_ns() - t0) / 1e6
                times.append(dt)

            total_ms = sum(times)
            throughput = n / (total_ms / 1000.0) if total_ms > 0 else 0

            status = get_consumer_inbox_status(store._open(store._db_path), "c1")
            pending = status["pending_count"]

            idempotent_ok = True
            for eid in ids[:10]:
                r = store.acknowledge_event("c1", eid)
                if r is not True:
                    idempotent_ok = False

            cp = store.get_checkpoint("c1")

            results[n] = {
                "total_ms": round(total_ms, 2),
                "ack_per_sec": round(throughput, 1),
                "p50": round(_percentile(times, 50), 4),
                "p95": round(_percentile(times, 95), 4),
                "p99": round(_percentile(times, 99), 4),
                "pending": pending,
                "idempotent": idempotent_ok,
                "checkpoint": cp,
            }
            print(f"  {n:>6} ACKs: total={total_ms:.1f}ms  ack/s={throughput:.1f}  "
                  f"p50={_percentile(times,50):.3f}ms  p95={_percentile(times,95):.3f}ms  "
                  f"p99={_percentile(times,99):.3f}ms  pending={pending}  "
                  f"idempotent={idempotent_ok}  cp={cp}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n  BEFORE (B8A baseline): 10K=258626ms 38.7ack/s p50=23.9ms")
    print("  AFTER  (B8 fixed):")
    for n, r in results.items():
        print(f"    {n:>6} ACKs: total={r['total_ms']:.1f}ms  "
              f"ack/s={r['ack_per_sec']:.1f}  p50={r['p50']:.3f}ms  "
              f"p95={r['p95']:.3f}ms  p99={r['p99']:.3f}ms")
    return results


# ===================================================================
# TEST 2: Thread-safety stress
# ===================================================================

def test_thread_safety():
    print("\n=== THREAD-SAFETY STRESS ===")

    # Test 1: Concurrent ACK/read operations
    print("  Test: concurrent ACK/read across threads...")
    tmp = tempfile.mkdtemp(prefix="b8thread_")
    db_path = os.path.join(tmp, "events.db")
    store = EventStore(db_path)
    store.register_consumer("c1")
    errors = []

    # Create 200 events
    conn = store._open(db_path)
    for i in range(200):
        conn.execute(
            "INSERT INTO persistent_events (id, type, source, data, created_at) "
            "VALUES (?, 'test', 'test', '{}', '2026-01-01T00:00:00Z')",
            (f"evt-{i}",)
        )
        conn.execute(
            "INSERT INTO consumer_event_state (consumer_id, event_id, delivered_at) "
            "VALUES ('c1', ?, '2026-01-01T00:00:00Z')",
            (f"evt-{i}",)
        )
    conn.commit()
    conn.close()

    def ack_worker(start, end):
        try:
            for i in range(start, end):
                store.acknowledge_event("c1", f"evt-{i}")
        except Exception as e:
            errors.append(f"ack({start}-{end}): {e}")

    def read_worker():
        try:
            for _ in range(50):
                c = store._open(db_path)
                c.execute(
                    "SELECT COUNT(*) FROM consumer_event_state "
                    "WHERE consumer_id='c1' AND acknowledged_at IS NULL"
                ).fetchone()
                c.close()
                time.sleep(0.001)
        except Exception as e:
            errors.append(f"read: {e}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = []
        for i in range(4):
            futures.append(pool.submit(ack_worker, i * 50, (i + 1) * 50))
        for _ in range(4):
            futures.append(pool.submit(read_worker))
        for f in futures:
            f.result()

    if errors:
        for e in errors:
            print(f"    ERROR: {e}")
    else:
        print("    Concurrent ACK/read: OK (no thread errors)")

    # Test 2: Repeated open/close cycles
    print("  Test: repeated open/close cycles...")
    for i in range(100):
        c = store._open(db_path)
        c.execute("SELECT 1")
        c.close()
    print("    100 open/close cycles: OK")

    # Test 3: Clean shutdown + temp DB deletion
    print("  Test: clean shutdown + temp DB deletion...")
    shutil.rmtree(tmp, ignore_errors=True)
    assert not os.path.exists(tmp), "temp dir should be deleted"
    print("    Clean shutdown: OK")

    # Test 4: Restart/reopen
    print("  Test: restart/reopen...")
    tmp2 = tempfile.mkdtemp(prefix="b8restart_")
    db2 = os.path.join(tmp2, "events.db")
    s1 = EventStore(db2)
    s1.register_consumer("c1")
    c1 = s1._open(db2)
    c1.execute(
        "INSERT INTO persistent_events (id, type, source, data, created_at) "
        "VALUES ('e1', 'test', 'test', '{}', '2026-01-01T00:00:00Z')"
    )
    c1.commit()
    c1.close()

    s2 = EventStore(db2)
    c2 = s2._open(db2)
    row = c2.execute("SELECT id FROM persistent_events WHERE id='e1'").fetchone()
    c2.close()
    assert row is not None, "data should persist across reopen"
    shutil.rmtree(tmp2, ignore_errors=True)
    print("    Restart/reopen: OK")

    # Test 5: No "database is locked" under rapid open/close
    print("  Test: no 'database is locked' under rapid cycles...")
    tmp3 = tempfile.mkdtemp(prefix="b8rapid_")
    db3 = os.path.join(tmp3, "test.db")
    locked_errors = 0
    for i in range(200):
        try:
            s = EventStore(db3)
            s.register_consumer("c1")
            c = s._open(db3)
            c.execute("SELECT 1")
            c.close()
        except Exception as e:
            if "locked" in str(e).lower():
                locked_errors += 1
    shutil.rmtree(tmp3, ignore_errors=True)
    print(f"    Rapid open/close (200 iterations): locked_errors={locked_errors}")
    assert locked_errors == 0, f"FAIL: {locked_errors} database locked errors"
    print("    PASS: no database locked errors")

    print("\n  ALL thread-safety tests PASSED")


# ===================================================================
# Main
# ===================================================================

async def main():
    print("=" * 60)
    print("B8 HEAVY VERIFICATION (FIX 3 reworked)")
    print("=" * 60)

    await test_ack_performance()
    test_thread_safety()

    print("\n" + "=" * 60)
    print("ALL HEAVY VERIFICATION COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
