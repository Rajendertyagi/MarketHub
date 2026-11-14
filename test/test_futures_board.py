#!/usr/bin/env python3
"""Futures Board projection tests (Phase 6)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from datetime import datetime, timezone

from market.coverage_states import (
    CAPACITY_LIMITED,
    LIVE,
    NO_DATA,
    PROVIDER_UNAVAILABLE,
    STALE,
    UNCOVERED,
    UNSUBSCRIBED,
)
from market.futures_board import (
    BoardInput,
    clear_board_cache,
    compute_futures_board,
    filter_board,
    get_futures_board,
)
from market.oi import (
    BASIS_PERSISTED,
    BASIS_PROVIDER,
    BASIS_UNAVAILABLE,
)

NOW = datetime.now(timezone.utc).isoformat()


def _future(underlying="NIFTY", *, provider="upstox", token="NSE_FO|1",
            kind="index", coverage=UNSUBSCRIBED):
    return BoardInput(underlying=underlying, kind=kind, exchange="NSE",
                      expiry="2026-09-29", instrument_type="FUTURE",
                      provider=provider, provider_token=token,
                      coverage_state=coverage)


def _spot(underlying="NIFTY", *, provider="upstox", token="NSE_INDEX|Nifty 50"):
    return BoardInput(underlying=underlying, kind="index", exchange="NSE",
                      expiry="", instrument_type="SPOT", provider=provider,
                      provider_token=token)


def _quotes(mapping):
    def reader(exchange, token):
        return mapping.get(token)
    return reader


def _board(inputs, quotes, *, prev=None, expiry="2026-09-29"):
    return compute_futures_board(
        inputs, expiry=expiry, quote_reader=_quotes(quotes),
        previous_oi_reader=prev)


# -- tiles -------------------------------------------------------------------

def test_index_and_equity_tiles_with_basis() -> None:
    inputs = [
        _future("NIFTY", token="NSE_FO|1"),
        _spot("NIFTY", token="NSE_INDEX|Nifty 50"),
        _future("RELIANCE", token="NSE_FO|2", kind="equity"),
        _spot("RELIANCE", token="NSE_EQ|INE002A01018"),
    ]
    quotes = {
        "NSE_FO|1": {"ltp": 23400.0, "close": 23300.0, "change": 100.0,
                     "change_percent": 0.43,
                     "open_interest": 1000, "previous_oi": 900,
                     "received_ts": NOW},
        "NSE_INDEX|Nifty 50": {"ltp": 23350.0, "received_ts": NOW},
        "NSE_FO|2": {"ltp": 1500.0, "close": 1490.0, "change": 10.0,
                     "open_interest": 500, "previous_oi": 550,
                     "received_ts": NOW},
        "NSE_EQ|INE002A01018": {"ltp": 1495.0, "received_ts": NOW},
    }
    snap = _board(inputs, quotes)
    assert snap.eligible == 2 and snap.quoted == 2
    by = {t.underlying: t for t in snap.items}
    assert by["NIFTY"].basis == 23400.0 - 23350.0
    assert by["RELIANCE"].basis == 1500.0 - 1495.0
    assert by["NIFTY"].price_pct is not None
    assert by["NIFTY"].coverage == LIVE


def test_buildup_classes_from_oi_and_price() -> None:
    inputs = [
        _future("LONGB", token="A"),
        _future("SHORTB", token="B"),
        _future("SC", token="C"),
        _future("LU", token="D"),
    ]
    quotes = {
        # oi up, price up -> Long Buildup
        "A": {"ltp": 110, "change": 10, "open_interest": 1200,
              "previous_oi": 1000, "received_ts": NOW},
        # oi up, price down -> Short Buildup
        "B": {"ltp": 90, "change": -10, "open_interest": 1200,
              "previous_oi": 1000, "received_ts": NOW},
        # oi down, price up -> Short Covering
        "C": {"ltp": 110, "change": 10, "open_interest": 800,
              "previous_oi": 1000, "received_ts": NOW},
        # oi down, price down -> Long Unwinding
        "D": {"ltp": 90, "change": -10, "open_interest": 800,
              "previous_oi": 1000, "received_ts": NOW},
    }
    snap = _board(inputs, quotes)
    by = {t.underlying: t.buildup for t in snap.items}
    assert by["LONGB"] == "Long Buildup"
    assert by["SHORTB"] == "Short Buildup"
    assert by["SC"] == "Short Covering"
    assert by["LU"] == "Long Unwinding"
    assert snap.class_counts.get("Long Buildup") == 1


def test_oi_basis_provider_wins_then_persisted_then_unavailable() -> None:
    inputs = [_future("NIFTY", token="A"), _future("RELIANCE", token="B"),
              _future("TCS", token="C", kind="equity")]
    quotes = {
        "A": {"ltp": 100, "open_interest": 110, "previous_oi": 100,
              "received_ts": NOW},
        "B": {"ltp": 100, "open_interest": 110, "received_ts": NOW},
        "C": {"ltp": 100, "open_interest": 110, "received_ts": NOW},
    }
    persisted = {"RELIANCE": 100.0}  # only RELIANCE has a baseline

    def prev_reader(exchange, underlying, expiry, itype):
        return persisted.get(underlying)

    snap = compute_futures_board(inputs, expiry="2026-09-29",
                                 quote_reader=_quotes(quotes),
                                 previous_oi_reader=prev_reader)
    by = {t.underlying: t for t in snap.items}
    assert by["NIFTY"].oi_basis == BASIS_PROVIDER
    assert by["RELIANCE"].oi_basis == BASIS_PERSISTED
    assert by["RELIANCE"].oi_change == 10.0 and by["RELIANCE"].oi_pct == 10.0
    assert by["TCS"].oi_basis == BASIS_UNAVAILABLE


def test_coverage_states() -> None:
    inputs = [
        _future("UNCOV", token="A", coverage=UNCOVERED),
        _future("CAP", token="B", coverage=CAPACITY_LIMITED),
        _future("UNAVAIL", token="C", coverage=PROVIDER_UNAVAILABLE),
        _future("NODATA", token="D"),
        _future("NOSUB", token="", coverage=UNSUBSCRIBED),
        _future("STALET", token="E"),
    ]
    quotes = {
        "A": {"ltp": 1, "received_ts": NOW},
        "B": {"ltp": 1, "received_ts": NOW},
        "C": {"ltp": 1, "received_ts": NOW},
        "E": {"ltp": 1, "received_ts": "2020-01-01T00:00:00+00:00"},
    }
    snap = _board(inputs, quotes)
    by = {t.underlying: t.coverage for t in snap.items}
    assert by["UNCOV"] == UNCOVERED
    assert by["CAP"] == CAPACITY_LIMITED
    assert by["UNAVAIL"] == PROVIDER_UNAVAILABLE
    assert by["NODATA"] == NO_DATA
    assert by["NOSUB"] == UNSUBSCRIBED
    assert by["STALET"] == STALE
    assert snap.data_status == "stale"  # newest tile is old


def test_empty_board_is_valid() -> None:
    snap = _board([_spot("NIFTY")], {})
    assert snap.eligible == 0 and snap.items == []
    assert snap.data_status == "live"


def test_filter_board_is_pure_projection() -> None:
    inputs = [_future("LONGB", token="A"), _future("SHORTB", token="B")]
    quotes = {
        "A": {"ltp": 110, "change": 10, "open_interest": 1200,
              "previous_oi": 1000, "received_ts": NOW},
        "B": {"ltp": 90, "change": -10, "open_interest": 1200,
              "previous_oi": 1000, "received_ts": NOW},
    }
    snap = _board(inputs, quotes)
    assert len(snap.items) == 2  # unfiltered
    only_long = filter_board(snap, buildup="long")
    assert [t.underlying for t in only_long.items] == ["LONGB"]
    only_short = filter_board(snap, buildup="short")
    assert [t.underlying for t in only_short.items] == ["SHORTB"]
    by_symbol = filter_board(snap, symbols=["shortb"])
    assert [t.underlying for t in by_symbol.items] == ["SHORTB"]


# -- cache / single-flight ---------------------------------------------------

def test_ttl_cache_reuses_snapshot() -> None:
    clear_board_cache()
    calls = [0]

    def reader(exchange, token):
        calls[0] += 1
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}

    inputs = [_future("NIFTY", token="A")]
    get_futures_board(inputs, expiry="E", quote_reader=reader)
    get_futures_board(inputs, expiry="E", quote_reader=reader)
    assert calls[0] == 1  # second call served from cache


def test_single_flight_one_build_per_key() -> None:
    clear_board_cache()
    calls = [0]

    def reader(exchange, token):
        calls[0] += 1
        time.sleep(0.05)
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}

    inputs = [_future("NIFTY", token="A"), _spot("NIFTY", token="S")]
    results = []

    def worker():
        results.append(get_futures_board(
            inputs, expiry="E2", quote_reader=reader))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 6 concurrent callers -> ONE build (2 quote reads), not 6 builds (12).
    assert calls[0] == 2
    assert len(results) == 6


def test_build_lock_cleanup_after_sequential_distinct_keys() -> None:
    import market.futures_board as fb
    clear_board_cache()

    def reader(exchange, token):
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}

    # At least 100 distinct keys sequentially.
    for i in range(110):
        inputs = [_future(f"SYM{i}", token=f"T{i}")]
        get_futures_board(inputs, expiry=f"E{i}", quote_reader=reader, ttl=0.0)

    # Expired entries pruned on access; unused build-lock entries cleaned up.
    assert len(fb._cache) == 0
    assert len(fb._build_locks) == 0


def test_concurrent_identical_requests_single_build_and_lock_cleanup() -> None:
    import market.futures_board as fb
    clear_board_cache()
    calls = [0]

    def reader(exchange, token):
        calls[0] += 1
        time.sleep(0.02)
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}

    inputs = [_future("NIFTY", token="A"), _spot("NIFTY", token="S")]
    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(get_futures_board(inputs, expiry="EID", quote_reader=reader))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert len(results) == 8
    assert calls[0] == 2  # one build (future + spot)
    assert len(fb._build_locks) == 0


def test_concurrent_distinct_requests_no_cross_key_blocking() -> None:
    import market.futures_board as fb
    clear_board_cache()
    started = {"a": threading.Event(), "b": threading.Event()}
    release = threading.Event()

    def reader(exchange, token):
        if token == "TA":
            started["a"].set()
            release.wait(timeout=5)
        elif token == "TB":
            started["b"].set()
            release.wait(timeout=5)
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}

    errors = []
    results = {}

    def worker(name, token, expiry):
        try:
            inputs = [_future(name, token=token)]
            results[name] = get_futures_board(inputs, expiry=expiry, quote_reader=reader)
        except Exception as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("A", "TA", "EA"))
    t2 = threading.Thread(target=worker, args=("B", "TB", "EB"))
    t1.start()
    t2.start()
    assert started["a"].wait(timeout=5)
    assert started["b"].wait(timeout=5), "distinct keys must not block each other"
    release.set()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors
    assert "A" in results and "B" in results
    assert len(fb._build_locks) == 0


def test_build_exception_cleans_up_lock() -> None:
    import market.futures_board as fb
    clear_board_cache()

    def boom(exchange, token):
        raise RuntimeError("quote failed hard")

    # compute_futures_board catches quote reader exceptions per tile, so force
    # failure at compute level to exercise get_futures_board cleanup.
    orig = fb.compute_futures_board

    def bad_compute(*a, **k):
        raise RuntimeError("build failed")

    fb.compute_futures_board = bad_compute  # type: ignore[assignment]
    try:
        inputs = [_future("NIFTY", token="A")]
        try:
            get_futures_board(inputs, expiry="EX", quote_reader=boom)
            raise AssertionError("expected exception")
        except RuntimeError:
            pass
        assert len(fb._build_locks) == 0
    finally:
        fb.compute_futures_board = orig  # type: ignore[assignment]

    # Subsequent call succeeds.
    def reader(exchange, token):
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}
    snap = get_futures_board([_future("NIFTY", token="A")], expiry="EX2", quote_reader=reader)
    assert snap.eligible == 1
    assert len(fb._build_locks) == 0


def test_invalidation_while_active_and_no_removal_while_waiter() -> None:
    import market.futures_board as fb
    clear_board_cache()
    build_started = threading.Event()
    allow_finish = threading.Event()
    orig = fb.compute_futures_board
    calls = {"n": 0}

    def slow_compute(*a, **k):
        calls["n"] += 1
        build_started.set()
        allow_finish.wait(timeout=5)
        return orig(*a, **k)

    fb.compute_futures_board = slow_compute  # type: ignore[assignment]
    errors = []
    results = []

    def reader(exchange, token):
        return {"ltp": 100, "open_interest": 10, "received_ts": NOW}

    inputs = [_future("NIFTY", token="A")]

    def worker():
        try:
            results.append(get_futures_board(inputs, expiry="EINV", quote_reader=reader))
        except Exception as exc:
            errors.append(exc)

    try:
        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        assert build_started.wait(timeout=5)
        t2.start()
        time.sleep(0.05)
        # Explicit invalidation while callers active must be safe.
        clear_board_cache()
        assert len(fb._build_locks) >= 1, "lock must not be removed while waiter/active caller depends on it"
        allow_finish.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not errors
        assert len(results) == 2
        assert calls["n"] == 1, "single-flight must hold while waiter/active"
        assert len(fb._build_locks) == 0
    finally:
        fb.compute_futures_board = orig  # type: ignore[assignment]


# -- boundary ----------------------------------------------------------------

def test_board_is_broker_and_app_free() -> None:
    code = (
        "import sys\n"
        "import market.futures_board\n"
        "bad = [m for m in sys.modules "
        "if m.startswith('app') or m.startswith('brokers')]\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=_PROJECT_DIR,
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
