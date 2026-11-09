"""Scanner API: offload, bounded concurrency, compatibility, and cache safety.

Phase 1 stabilization tests. Uses the repository's existing test conventions
(Starlette TestClient / asyncio.run, fake dependencies, no new dependencies).
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from api.routes import build_market_routes
from market.scanner import ScanResult, ScannerEngine

EXPECTED_SCANNERS = [d.name for d in ScannerEngine._scanner_defs()]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Catalog:
    """Minimal catalog: enough for route construction and list endpoint."""

    def catalog_epoch(self):
        return 1


class _MarketService:
    def get_quote_now(self, exchange, token):
        return None


class _SlowEngine:
    """Fake ScannerEngine that records concurrency and can block."""

    active = 0
    max_active = 0
    lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def __init__(self, catalog):
        self.catalog = catalog

    @classmethod
    def reset(cls):
        cls.active = 0
        cls.max_active = 0
        cls.started = threading.Event()
        cls.release = threading.Event()
        cls.calls = 0

    def list_scanners(self):
        return ScannerEngine(self.catalog).list_scanners()

    def scan(self, name, universe, reader, **kwargs):
        with _SlowEngine.lock:
            _SlowEngine.active += 1
            _SlowEngine.calls += 1
            _SlowEngine.max_active = max(_SlowEngine.max_active, _SlowEngine.active)
        _SlowEngine.started.set()
        try:
            _SlowEngine.release.wait(timeout=5)
            return ScanResult(
                scanner=name, universe=universe, as_of=None,
                eligible=0, quoted=0, matched=0, rows=[],
            )
        finally:
            with _SlowEngine.lock:
                _SlowEngine.active -= 1


class _Row:
    def to_dict(self):
        return {"symbol": "X", "rank": 1}


class _FastEngine(_SlowEngine):
    def scan(self, name, universe, reader, **kwargs):
        with _SlowEngine.lock:
            _SlowEngine.calls += 1
        return ScanResult(
            scanner=name, universe=universe, as_of="2026-01-01T00:00:00Z",
            eligible=1, quoted=1, matched=1,
            rows=[_Row()],
        )


class _ErrorEngine(_SlowEngine):
    def scan(self, name, universe, reader, **kwargs):
        with _SlowEngine.lock:
            _SlowEngine.calls += 1
        raise ValueError("unknown scanner: nope")


class _BoomEngine(_SlowEngine):
    def scan(self, name, universe, reader, **kwargs):
        with _SlowEngine.lock:
            _SlowEngine.calls += 1
        raise RuntimeError("boom")


@contextmanager
def _app(*, catalog=_Catalog(), market=_MarketService(), engine_cls=_FastEngine):
    import api.routes as routes_mod
    orig = routes_mod.ScannerEngine
    routes_mod.ScannerEngine = engine_cls
    try:
        routes = build_market_routes(
            market_broker=None, market_service=market,
            index_catalog=catalog,
        )
        yield Starlette(routes=routes)
    finally:
        routes_mod.ScannerEngine = orig


def _get_sem(app):
    # The semaphore lives in the scanner route closure; find it via the route.
    for r in app.routes:
        if getattr(r, "path", "") == "/api/market/scanner/{name}":
            fn = r.endpoint
            for cell in fn.__closure__ or ():
                try:
                    val = cell.cell_contents
                except ValueError:
                    continue
                if isinstance(val, asyncio.Semaphore):
                    return val
    return None


# ---------------------------------------------------------------------------
# API compatibility / service-unavailable / 400
# ---------------------------------------------------------------------------


def test_scanners_list_returns_all_nine_definitions():
    _SlowEngine.reset()
    with _app(engine_cls=_FastEngine) as app, TestClient(app) as c:
        r = c.get("/api/market/scanners")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    names = [s["name"] for s in body["scanners"]]
    assert names == EXPECTED_SCANNERS
    assert len(names) == 9


def test_scanners_list_503_when_catalog_unavailable():
    with _app(catalog=None) as app, TestClient(app) as c:
        r = c.get("/api/market/scanners")
    assert r.status_code == 503
    assert r.json() == {"error": "instrument catalog unavailable"}


def test_scanner_run_503_when_catalog_unavailable():
    with _app(catalog=None) as app, TestClient(app) as c:
        r = c.get("/api/market/scanner/gainers")
    assert r.status_code == 503
    assert r.json() == {"error": "instrument catalog unavailable"}


def test_scanner_run_503_when_market_service_unavailable():
    with _app(market=None) as app, TestClient(app) as c:
        r = c.get("/api/market/scanner/gainers")
    assert r.status_code == 503
    assert r.json() == {"error": "market service unavailable"}


def test_scanner_run_400_preserves_value_error():
    _SlowEngine.reset()
    with _app(engine_cls=_ErrorEngine) as app, TestClient(app) as c:
        r = c.get("/api/market/scanner/nope")
    assert r.status_code == 400
    assert r.json() == {"error": "unknown scanner: nope"}


def test_scanner_run_success_response_shape_preserved():
    _SlowEngine.reset()
    with _app(engine_cls=_FastEngine) as app, TestClient(app) as c:
        r = c.get("/api/market/scanner/gainers?universe=FNO&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["scanner"] == "gainers"
    assert body["universe"] == "FNO"
    assert body["eligible"] == 1
    assert body["quoted"] == 1
    assert body["matched"] == 1
    assert "rows" in body
    assert body["rows"][0]["symbol"] == "X"
    # Exact ScanResult.to_dict() keys preserved (plus status).
    for key in ("scanner", "universe", "as_of", "eligible", "quoted", "matched", "rows"):
        assert key in body


def test_scanner_run_releases_permit_after_success():
    _SlowEngine.reset()
    with _app(engine_cls=_FastEngine) as app:
        sem = _get_sem(app)
        assert sem is not None
        with TestClient(app) as c:
            r = c.get("/api/market/scanner/gainers")
        assert r.status_code == 200
        assert sem._value == 2


def test_scanner_run_releases_permit_after_value_error():
    _SlowEngine.reset()
    with _app(engine_cls=_ErrorEngine) as app:
        sem = _get_sem(app)
        with TestClient(app) as c:
            r = c.get("/api/market/scanner/nope")
        assert r.status_code == 400
        assert sem._value == 2


def test_scanner_run_releases_permit_after_unexpected_error():
    _SlowEngine.reset()
    with _app(engine_cls=_BoomEngine) as app:
        sem = _get_sem(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/api/market/scanner/gainers")
        assert r.status_code == 500
        assert sem._value == 2


# ---------------------------------------------------------------------------
# Concurrency limit / event-loop responsiveness / cancellation
# ---------------------------------------------------------------------------


def test_concurrent_requests_respect_limit_of_two():
    """At most 2 scans run concurrently; third waits; all complete."""

    async def _run():
        _SlowEngine.reset()
        _SlowEngine.release.clear()
        with _app(engine_cls=_SlowEngine) as app:
            sem = _get_sem(app)
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                tasks = [asyncio.create_task(ac.get("/api/market/scanner/gainers")) for _ in range(3)]
                # Wait until 2 have started (limit reached).
                deadline = time.time() + 5
                while time.time() < deadline:
                    with _SlowEngine.lock:
                        if _SlowEngine.active >= 2:
                            break
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.05)
                with _SlowEngine.lock:
                    assert _SlowEngine.max_active <= 2
                    assert _SlowEngine.active == 2  # third waiting on semaphore
                assert sem._value == 0
                _SlowEngine.release.set()
                resps = await asyncio.gather(*tasks)
            assert [r.status_code for r in resps] == [200, 200, 200]
            assert sem._value == 2

    asyncio.run(_run())


def test_event_loop_responsive_during_slow_scan():
    """A slow scanner (offloaded to thread) must not block the event loop."""

    async def _run():
        _SlowEngine.reset()
        _SlowEngine.release.clear()
        with _app(engine_cls=_SlowEngine) as app:
            from httpx import ASGITransport, AsyncClient

            ticks = 0

            async def ticker():
                nonlocal ticks
                for _ in range(30):
                    await asyncio.sleep(0.01)
                    ticks += 1

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                scan_task = asyncio.create_task(ac.get("/api/market/scanner/gainers"))
                tick_task = asyncio.create_task(ticker())
                # Let scan start in worker.
                for _ in range(100):
                    if _SlowEngine.started.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert _SlowEngine.started.is_set(), "scan did not start"
                # While scan is blocked in worker, event loop should tick.
                await asyncio.sleep(0.08)
                assert ticks >= 3, f"event loop appeared blocked, ticks={ticks}"
                _SlowEngine.release.set()
                resp = await scan_task
                await tick_task
            assert resp.status_code == 200
            assert ticks >= 3

    asyncio.run(_run())


def test_cancellation_keeps_permit_until_worker_finishes():
    """If the request is cancelled, permit stays held until worker completes."""

    async def _run():
        _SlowEngine.reset()
        _SlowEngine.release.clear()
        with _app(engine_cls=_SlowEngine) as app:
            sem = _get_sem(app)
            from httpx import ASGITransport, AsyncClient

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
                task = asyncio.create_task(ac.get("/api/market/scanner/gainers"))
                for _ in range(100):
                    if _SlowEngine.started.is_set():
                        break
                    await asyncio.sleep(0.01)
                assert _SlowEngine.started.is_set()
                # Worker is running; permit is held.
                assert sem._value == 1
                task.cancel()
                with pytest.raises((asyncio.CancelledError, Exception)):
                    await task
                # Worker still blocked -> permit must still be held.
                await asyncio.sleep(0.05)
                with _SlowEngine.lock:
                    still_active = _SlowEngine.active
                assert still_active == 1
                assert sem._value == 1
                # Let worker finish -> permit released via callback.
                _SlowEngine.release.set()
                deadline = time.time() + 3
                while time.time() < deadline and sem._value != 2:
                    await asyncio.sleep(0.01)
                assert sem._value == 2

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# QuoteService cache safety under concurrent scanner-like access
# ---------------------------------------------------------------------------


def test_quote_service_cache_safe_under_concurrent_access():
    """Narrow cache lock must prevent races during concurrent quote_now calls."""
    from app.quote_service import QuoteService

    class _Cat:
        def catalog_epoch(self):
            return 1

        def get_by_key(self, ref):
            return {"exchange": "NSE", "instrument_token": ref,
                    "tradingsymbol": ref, "provider": "upstox",
                    "instrument_type": "EQUITY"}

        def find_canonical_rows(self, cid):
            return []

        def search(self, **kw):
            return []

    class _Mkt:
        def get_quote_now(self, exchange, token):
            return SimpleNamespace(
                instrument_token=token, exchange=exchange,
                tradingsymbol=token, ltp=100.0, close=99.0,
                volume=1, oi=None, change_percent=1.0,
                received_ts=None, exchange_ts=None,
                canonical_id=None,
            )

    svc = QuoteService(market_service=_Mkt(), catalog=_Cat())
    errors = []
    refs = [f"TOK{i}" for i in range(50)]

    def worker():
        try:
            for _ in range(40):
                for ref in refs:
                    svc.quote_now(ref, "NSE")
                    svc.storage_keys(ref)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not errors, errors
    # Sanity: still returns a quote.
    assert svc.quote_now("TOK1", "NSE") is not None
