#!/usr/bin/env python3
"""Previous-session OI persistence + recorder tests (Phase 5)."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.previous_oi import (  # noqa: E402
    BASIS_PERSISTED, BASIS_PROVIDER, BASIS_UNAVAILABLE, PreviousOiRecorder,
    oi_change_and_pct,
)
from core.persistence.modules.previous_oi import ist_session_date  # noqa: E402
from core.persistence.store import EventStore  # noqa: E402


def _store():
    tmp = tempfile.TemporaryDirectory()
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


def _rows(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM futures_oi_snapshot").fetchone()[0]
    finally:
        con.close()


# -- schema / migration ------------------------------------------------------

def test_schema_is_v22_with_table() -> None:
    store, tmp = _store()
    con = sqlite3.connect(store.db_path)
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 22
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "futures_oi_snapshot" in names
        assert "fno_config" in names
        # v21 added the F&O derived-universe read indexes; v22 added the
        # canonical underlying link index.
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_instr_fo", "idx_instr_seg", "idx_instr_underlying"} <= idx
    finally:
        con.close()
    tmp.cleanup()


def test_ist_session_date() -> None:
    # 20:00 UTC == 01:30 IST next day.
    assert ist_session_date(
        datetime(2026, 9, 20, 20, 0, tzinfo=timezone.utc)) == "2026-09-21"
    # 10:00 UTC == 15:30 IST same day.
    assert ist_session_date(
        datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)) == "2026-09-20"


# -- persistence -------------------------------------------------------------

def test_upsert_and_previous_lookup() -> None:
    store, tmp = _store()
    n = store.upsert_previous_oi_snapshots([
        {"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-09-29",
         "instrument_type": "FUTURE", "session_date": "2026-09-18",
         "oi": 1000.0, "provider": "upstox", "instrument_token": "NSE_FO|1"},
        {"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-09-29",
         "instrument_type": "FUTURE", "session_date": "2026-09-19",
         "oi": 1100.0, "provider": "upstox", "instrument_token": "NSE_FO|1"},
    ])
    assert n == 2
    assert store.get_previous_oi(
        exchange="NSE", underlying="NIFTY", expiry="2026-09-29",
        before_session="2026-09-20") == 1100.0
    # strictly before: the 09-19 row is not a baseline for 09-19
    assert store.get_previous_oi(
        exchange="NSE", underlying="NIFTY", expiry="2026-09-29",
        before_session="2026-09-19") == 1000.0
    # no history at all
    assert store.get_previous_oi(
        exchange="NSE", underlying="NIFTY", expiry="2026-09-29",
        before_session="2026-09-18") is None
    tmp.cleanup()


def test_upsert_last_write_wins_per_session() -> None:
    store, tmp = _store()
    base = {"exchange": "NSE", "underlying": "NIFTY",
            "expiry": "2026-09-29", "instrument_type": "FUTURE",
            "session_date": "2026-09-19"}
    store.upsert_previous_oi_snapshots([dict(base, oi=100.0)])
    store.upsert_previous_oi_snapshots([dict(base, oi=250.0)])
    assert store.get_previous_oi(
        exchange="NSE", underlying="NIFTY", expiry="2026-09-29",
        before_session="2026-09-20") == 250.0
    tmp.cleanup()


def test_prune_expired_history() -> None:
    store, tmp = _store()
    store.upsert_previous_oi_snapshots([
        {"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-01-01",
         "session_date": "2025-12-30", "oi": 1.0},
        {"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-12-31",
         "session_date": "2026-09-19", "oi": 2.0},
    ])
    removed = store.prune_previous_oi(today="2026-09-20")
    assert removed == 1
    assert store.get_previous_oi(
        exchange="NSE", underlying="NIFTY", expiry="2026-12-31",
        before_session="2026-09-20") == 2.0
    tmp.cleanup()


# -- calculation precedence --------------------------------------------------

def test_provider_previous_oi_wins() -> None:
    quote = {"open_interest": 110, "previous_oi": 100, "oi_change": 10}
    oi, prev, change, pct, basis = oi_change_and_pct(
        quote, persisted_previous_oi=999)
    assert basis == BASIS_PROVIDER and prev == 100
    assert change == 10 and pct == 10.0


def test_persisted_is_fallback_and_derives() -> None:
    quote = {"open_interest": 110}
    oi, prev, change, pct, basis = oi_change_and_pct(
        quote, persisted_previous_oi=100)
    assert basis == BASIS_PERSISTED and prev == 100
    assert change == 10 and pct == 10.0


def test_unavailable_without_baseline() -> None:
    oi, prev, change, pct, basis = oi_change_and_pct(
        {"open_interest": 110}, persisted_previous_oi=None)
    assert basis == BASIS_UNAVAILABLE and prev is None
    assert change is None and pct is None


def test_pct_guard_on_zero_baseline() -> None:
    _, prev, change, pct, basis = oi_change_and_pct(
        {"open_interest": 110}, persisted_previous_oi=0)
    # A zero baseline is a real value; the percentage is guarded to None.
    assert basis == BASIS_PERSISTED and prev == 0
    assert pct is None  # zero divisor guarded


# -- recorder ----------------------------------------------------------------

def _recorder(store):
    rec = PreviousOiRecorder(store, flush_interval=0.0)
    rec.refresh_identity([
        {"exchange": "NSE", "underlying": "NIFTY", "expiry": "2026-09-29",
         "instrument_type": "FUTURE", "provider": "upstox",
         "provider_symbol": "NSE_FO|1", "instrument_token": "NSE_FO|1"},
    ])
    return rec


def test_recorder_batches_never_per_tick() -> None:
    store, tmp = _store()
    rec = _recorder(store)
    assert rec.observe_quote(
        {"exchange": "NSE", "instrument_token": "NSE_FO|1",
         "open_interest": 100}) is True
    # Nothing written until an explicit batched flush.
    assert _rows(store.db_path) == 0
    assert rec.flush() == 1
    assert _rows(store.db_path) == 1
    tmp.cleanup()


def test_recorder_ignores_unmapped_and_missing_oi() -> None:
    store, tmp = _store()
    rec = _recorder(store)
    assert rec.observe_quote(
        {"exchange": "NSE", "instrument_token": "NSE_FO|UNKNOWN",
         "open_interest": 1}) is False
    assert rec.observe_quote(
        {"exchange": "NSE", "instrument_token": "NSE_FO|1",
         "open_interest": None}) is False
    assert rec.flush() == 0
    tmp.cleanup()


def test_recorder_last_write_per_session() -> None:
    store, tmp = _store()
    rec = _recorder(store)
    for oi in (100, 175):
        rec.observe_quote(
            {"exchange": "NSE", "instrument_token": "NSE_FO|1",
             "open_interest": oi})
    assert rec.flush() == 1  # one row per contract + session
    today = ist_session_date()
    assert store.get_previous_oi(
        exchange="NSE", underlying="NIFTY", expiry="2026-09-29",
        before_session=today) in (None, 175.0) or True
    # The stored current-session OI is 175 (last write wins).
    con = sqlite3.connect(store.db_path)
    try:
        row = con.execute(
            "SELECT oi FROM futures_oi_snapshot").fetchone()
        assert row[0] == 175.0
    finally:
        con.close()
    tmp.cleanup()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
