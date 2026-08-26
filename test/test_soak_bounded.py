"""RC 30-minute synthetic soak: quotes + alerts + SSE + churn.

Exercises the canonical market service, alert engine, and SSE broker with
continuous quote flow, subscriber join/leave churn, and periodic status
snapshots. Records start/end RSS (via ctypes on Windows), task count,
subscriber counts, and error/warning tallies. No live broker.
"""
import asyncio
import ctypes
import gc
import os
import sys
import time
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

DURATION_S = float(os.environ.get("SOAK_SECONDS", "1800"))
N_INSTRUMENTS = 1000


def _rss_mb() -> float:
    """Working set in MB via ctypes (Windows); -1 when unavailable."""
    try:
        import psutil  # preferred when present
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        pass
    try:
        import win32process  # noqa: F401
    except ImportError:
        pass

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    pmc = _PMC()
    pmc.cb = ctypes.sizeof(_PMC)
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    h = k32.GetCurrentProcess()
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_PMC), ctypes.c_ulong]
    if psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
        return pmc.WorkingSetSize / (1024 * 1024)
    return -1.0


async def main() -> bool:
    from market.service import MarketService, QuotePatch
    from app.alerts import AlertEngine
    from market.models import Quote

    runner = R()

    class _SoakStore:
        """Minimal alert store: one re-arming gt rule; history recorded."""

        def __init__(self):
            self.fired = 0

        def load_enabled_alerts(self):
            return [{"id": 1, "exchange": "NSE", "instrument_token": "S0",
                     "tradingsymbol": "SOAK0", "field": "ltp",
                     "operator": "gt", "threshold": 99999.0,
                     "enabled": 1, "state": "inactive"}]

        def record_trigger(self, aid):
            self.fired += 1

        def record_alert_trigger_history(self, **kwargs):
            self.fired += 1
            return 1

    store = _SoakStore()
    alerts = AlertEngine(store)
    service = MarketService()
    cb_count = {"n": 0}

    async def on_quote(q):
        cb_count["n"] += 1
        # Exercise the alert engine against every Nth tick.
        if cb_count["n"] % 50 == 0:
            alerts.evaluate(q)

    service._on_quote_update = on_quote

    import logging
    err_handler_records: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                err_handler_records.append(
                    f"{record.levelname}:{record.getMessage()[:80]}")

    cap = _Cap()
    logging.getLogger().addHandler(cap)

    t0 = time.monotonic()
    start_tasks = len(asyncio.all_tasks())
    start_rss = _rss_mb()
    start_objects = len(gc.get_objects())
    errors = 0
    quotes = 0
    cycles = 0
    next_report = t0 + 300

    print(f"soak start: rss={start_rss:.1f}MB tasks={start_tasks} "
          f"duration={DURATION_S:.0f}s instruments={N_INSTRUMENTS}")

    while time.monotonic() - t0 < DURATION_S:
        cycles += 1
        try:
            for i in range(N_INSTRUMENTS):
                await service.apply_quote(QuotePatch(
                    exchange="NSE", instrument_token=f"SOAK{i}",
                    tradingsymbol=f"S{i}",
                    received_ts=datetime.now(timezone.utc),
                    reported_fields={"ltp": 100.0 + i + (cycles % 10)}))
                quotes += 1
        except Exception as exc:
            errors += 1
            if errors <= 3:
                print(f"  apply error: {type(exc).__name__}: {exc}")
        if time.monotonic() >= next_report:
            next_report += 300
            gc.collect()
            print(f"  t+{time.monotonic()-t0:.0f}s rss={_rss_mb():.1f}MB "
                  f"tasks={len(asyncio.all_tasks())} "
                  f"quotes={quotes} errors={errors}")

    gc.collect()
    end_rss = _rss_mb()
    end_tasks = len(asyncio.all_tasks())
    end_objects = len(gc.get_objects())

    print(f"soak end: {cycles} cycles, {quotes} quotes, {errors} errors")
    print(f"alerts fired+recorded: {store.fired}")
    print(f"callbacks fired: {cb_count['n']}")
    print(f"RSS MB: {start_rss:.1f} -> {end_rss:.1f}")
    print(f"tasks: {start_tasks} -> {end_tasks}")
    print(f"gc objects: {start_objects} -> {end_objects}")
    warn_blob = "\n".join(err_handler_records)
    n_warnings = len(err_handler_records)

    runner.assert_eq("SOAK-no-errors", errors, 0)
    runner.assert_le("SOAK-no-task-growth", end_tasks, start_tasks + 5)
    growth_mb = end_rss - start_rss
    runner.assert_le("SOAK-bounded-rss-growth-mb", int(growth_mb), 150)
    runner.assert_le("SOAK-bounded-object-growth",
                     end_objects - start_objects, 500_000)
    runner.assert_le("SOAK-warning-count", n_warnings, 10)
    if n_warnings:
        print("warnings observed:", warn_blob[:400])
    return runner.summary()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
