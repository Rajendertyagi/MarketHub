#!/usr/bin/env python3
"""F&O coverage plan caching/single-flight/freshness tests and microbenchmark."""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import date

import pytest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.fno_coverage import (
    LIVE,
    UNSUBSCRIBED,
    FnoCoverageService,
)
from providers.capabilities import (
    CAP_LTP,
    CAP_OI,
    CAP_PREVIOUS_OI,
    STYLE_COLON,
    STYLE_NUMERIC,
    STYLE_PIPE,
    ProviderCapabilities,
)
from providers.registry import ProviderRegistry


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 1)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    import app.fno_coverage as fno_cov

    monkeypatch.setattr(fno_cov, "date", _FixedDate)


def _registry(up=2000, fy=5000):
    upc = ProviderCapabilities(
        provider="upstox",
        label="Upstox",
        capabilities=frozenset({CAP_LTP, CAP_OI}),
        modes={"full": up},
        default_mode="full",
        key_styles=(STYLE_PIPE,),
    )
    fyc = ProviderCapabilities(
        provider="fyers",
        label="Fyers",
        capabilities=frozenset({CAP_LTP, CAP_OI, CAP_PREVIOUS_OI}),
        modes={"full": fy},
        default_mode="full",
        key_styles=(STYLE_COLON, STYLE_NUMERIC),
    )
    return ProviderRegistry([upc, fyc])


class _CountingCat:
    def __init__(self, sync_version: int = 1):
        self.sync_version = sync_version
        self.future_reads = 0
        self.equity_reads = 0
        self.fno_universe_reads = 0
        self.lock = threading.Lock()

    def sync_state(self):
        return [{"provider": "upstox", "last_sync": f"v{self.sync_version}", "instruments": 10}]

    def fno_universe(self, *, provider, today, limit):
        with self.lock:
            self.fno_universe_reads += 1
        return [{"symbol": "RELIANCE"}]

    def equity_universe(self, *, provider=None, limit=5000):
        with self.lock:
            self.equity_reads += 1
        p = provider or "upstox"
        return [
            {
                "tradingsymbol": "RELIANCE",
                "provider": p,
                "provider_symbol": f"EQ-{p}",
                "instrument_token": f"EQ-{p}",
            }
        ]

    def list_future_contracts(self, *, expiries=None, **kw):
        # futures_index is used by service; keep for compatibility if called
        rows = []
        for u in ("NIFTY", "RELIANCE"):
            rows.append(
                {
                    "exchange": "NSE",
                    "underlying": u,
                    "expiry": "2026-10-27",
                    "instrument_type": "FUTURE",
                    "provider": "upstox",
                    "provider_symbol": f"NSE_FO|U-{u}",
                    "instrument_token": f"NSE_FO|U-{u}",
                    "tradingsymbol": f"{u}FUT",
                    "lot_size": 1,
                }
            )
            rows.append(
                {
                    "exchange": "NSE",
                    "underlying": u,
                    "expiry": "2026-10-27",
                    "instrument_type": "FUTURE",
                    "provider": "fyers",
                    "provider_symbol": f"NSE:{u}FUT",
                    "instrument_token": f"F-{u}",
                    "tradingsymbol": f"{u}FUT",
                    "lot_size": 1,
                }
            )
        if expiries:
            rows = [r for r in rows if r["expiry"] in expiries]
        return rows


@pytest.fixture
def patch_futures_index(monkeypatch):
    import app.fno_coverage as fno_cov

    def install(cat: _CountingCat):
        def fake_futures_index(catalog, exchange="NSE"):
            assert catalog is cat
            with cat.lock:
                cat.future_reads += 1
            rows = cat.list_future_contracts()
            rows = [r for r in rows if r.get("exchange") == exchange]
            out = {}
            for r in rows:
                out.setdefault(r["underlying"], []).append(r)
            return out

        monkeypatch.setattr(fno_cov, "futures_index", fake_futures_index)

    return install


def test_plan_reuses_catalog_skeleton_across_calls(patch_futures_index):
    cat = _CountingCat(sync_version=1)
    patch_futures_index(cat)
    svc = FnoCoverageService(cat, registry=_registry())

    p1 = svc.plan()
    p2 = svc.plan()
    assert p1.desired == p2.desired
    assert p1.routed == p2.routed
    assert p1.expiry_resolved == p2.expiry_resolved
    # NSE+BSE futures index reads happen once per skeleton build (2 calls), not per plan call.
    assert cat.future_reads == 2
    # equity_universe called once per enabled provider per skeleton build.
    assert cat.equity_reads == 2


def test_catalog_sync_change_invalidates_skeleton(patch_futures_index):
    cat = _CountingCat(sync_version=1)
    patch_futures_index(cat)
    svc = FnoCoverageService(cat, registry=_registry())

    svc.plan()
    assert cat.future_reads == 2
    svc.plan()
    assert cat.future_reads == 2

    cat.sync_version = 2
    svc.plan()
    assert cat.future_reads == 4


def test_explicit_invalidate_drops_skeleton(patch_futures_index):
    cat = _CountingCat(sync_version=1)
    patch_futures_index(cat)
    svc = FnoCoverageService(cat, registry=_registry())

    svc.plan()
    assert cat.future_reads == 2
    svc.invalidate_plan_cache()
    svc.plan()
    assert cat.future_reads == 4


def test_dynamic_provider_state_is_fresh_with_cached_skeleton(patch_futures_index):
    cat = _CountingCat(sync_version=1)
    patch_futures_index(cat)
    statuses = {"upstox": "streaming", "fyers": "streaming"}
    subscribed = {"upstox": set(), "fyers": set()}

    svc = FnoCoverageService(
        cat,
        registry=_registry(),
        status_provider=lambda p: statuses.get(p),
        subscribed_provider=lambda p: subscribed.get(p, set()),
    )

    p1 = svc.plan()
    fut1 = next(c for c in p1.contracts if c.underlying == "NIFTY" and c.instrument_type == "FUTURE")
    assert fut1.provider == "upstox"
    assert fut1.state == UNSUBSCRIBED
    assert cat.future_reads == 2

    # Dynamic changes: upstox unavailable, fyers has subscription -> routing/state must reflect fresh state.
    statuses["upstox"] = "auth_required"
    statuses["fyers"] = "streaming"
    subscribed["fyers"] = {fut1.provider_token} if fut1.provider == "fyers" else {"NSE:NIFTYFUT"}

    p2 = svc.plan()
    fut2 = next(c for c in p2.contracts if c.underlying == "NIFTY" and c.instrument_type == "FUTURE")
    assert fut2.provider == "fyers"
    assert fut2.state == LIVE
    # Skeleton reused (no extra catalog reads) despite dynamic changes.
    assert cat.future_reads == 2


def test_single_flight_skeleton_build(patch_futures_index):
    cat = _CountingCat(sync_version=1)
    patch_futures_index(cat)
    svc = FnoCoverageService(cat, registry=_registry())

    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(svc.plan())
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert len(results) == 8
    assert all(r.desired == results[0].desired for r in results)
    # Only one skeleton build despite concurrent callers.
    assert cat.future_reads == 2
    assert cat.equity_reads == 2


def test_plan_microbenchmark_repeated_calls(patch_futures_index):
    cat = _CountingCat(sync_version=1)
    patch_futures_index(cat)
    svc = FnoCoverageService(cat, registry=_registry())

    # Warm skeleton cache.
    svc.plan()
    baseline_reads = (cat.future_reads, cat.equity_reads)

    n = 50
    t0 = time.perf_counter()
    for _ in range(n):
        svc.plan()
    elapsed = time.perf_counter() - t0

    assert (cat.future_reads, cat.equity_reads) == baseline_reads
    # Sanity: repeated cached-skeleton calls should be fast in test environment.
    assert elapsed < 2.0, f"repeated plan calls too slow: {elapsed:.4f}s for {n}"
    print(f"BENCH fno_coverage.plan repeated={n} elapsed_s={elapsed:.6f} per_call_ms={(elapsed/n)*1000:.3f} catalog_future_reads={cat.future_reads} equity_reads={cat.equity_reads}")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "-s"]))
