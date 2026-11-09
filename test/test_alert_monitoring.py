#!/usr/bin/env python3
"""Alerts that genuinely monitor their dependencies.

Proves:
  * an enabled alert LEASES the instruments it evaluates (so its
    dependencies are actually subscribed);
  * deleting/disabling an alert RELEASES its leases;
  * a STALE quote never triggers an alert (the alert fails closed);
  * the monitoring state is honest: active / waiting / blocked, with the
    reason a dependency cannot be monitored;
  * creating/updating/deleting one alert does NOT reset the evaluation
    state of another alert.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from market.models import Quote


def _store():
    from core.persistence.store import EventStore
    tmp = tempfile.mkdtemp(prefix="alert-mon-")
    return EventStore(str(Path(tmp) / "events.db"))


class _Resolver:
    """A canonical identity resolver double."""

    def __init__(self, canonical_id="NSE:INDEX:NIFTY"):
        self.canonical_id = canonical_id

    def resolve_quote(self, quote):
        return self.canonical_id

    def resolve(self, reference):
        return self.canonical_id if reference else None

    def storage_key_for(self, canonical_id):
        return "NSE_INDEX|Nifty 50"

    def context_for(self, canonical_id):
        return {"canonical_id": canonical_id, "tradingsymbol": "NIFTY",
                "exchange": "NSE"}


class _Manager:
    """A lease registry double."""

    def __init__(self):
        self.leases = []
        self._n = 0

    def acquire(self, key="", *, provider=None, canonical_id=None,
                priority=20, owner_kind="alert", owner_id=""):
        self._n += 1
        lease = type("L", (), {"lease_id": f"lease-{self._n}",
                               "canonical_id": canonical_id})()
        self.leases.append(lease)
        return lease.lease_id

    def release(self, lease_id):
        before = len(self.leases)
        self.leases = [lease for lease in self.leases
                      if lease.lease_id != lease_id]
        return len(self.leases) < before


def _engine(store, resolver=None, manager=None):
    from app.condition_alerts import ConditionAlertEngine
    engine = ConditionAlertEngine(store, resolver=resolver or _Resolver())
    if manager is not None:
        engine.set_leaser(manager)
    return engine


def _create_alert(store, *, alert_id=None, canonical_id="NSE:INDEX:NIFTY"):
    consumer = "test-consumer"
    store.register_consumer(consumer)
    condition = {
        "condition_version": 1,
        "condition_id": alert_id or "c1",
        "instrument": {"canonical_id": canonical_id},
        "metric": "ltp",
        "operator": "gt",
        "value": 100.0,
    }
    return store.create_condition_alert(
        consumer_id=consumer,
        name="test",
        trigger_mode="repeat",
        condition_json=condition,
        metadata={"expiry": "2026-10-29"},
    )


def test_enabled_alert_leases_its_dependency() -> None:
    store = _store()
    manager = _Manager()
    _create_alert(store)
    _engine(store, manager=manager)
    assert manager.leases, "an enabled alert must lease its dependency"
    assert manager.leases[0].canonical_id == "NSE:INDEX:NIFTY"


def test_deleting_the_alert_releases_its_leases() -> None:
    store = _store()
    manager = _Manager()
    _create_alert(store)
    engine = _engine(store, manager=manager)
    assert len(manager.leases) == 1
    alert_id = list(engine._alerts)[0]
    engine.release_alert_leases(alert_id)
    assert manager.leases == []


def test_stale_quote_never_triggers_an_alert() -> None:
    store = _store()
    manager = _Manager()
    _create_alert(store)
    engine = _engine(store, manager=manager)
    stale = Quote(instrument_token="NSE_INDEX|Nifty 50", exchange="NSE",
                  tradingsymbol="NIFTY",
                  received_ts=datetime.now(timezone.utc) - timedelta(hours=2),
                  ltp=500.0)
    fired = asyncio.run(engine.evaluate(stale))
    assert fired == [], "a stale quote must never trigger an alert"
    assert engine._stale_evaluations >= 1


def test_fresh_quote_evaluates_normally() -> None:
    store = _store()
    manager = _Manager()
    _create_alert(store)
    engine = _engine(store, manager=manager)
    fresh = Quote(instrument_token="NSE_INDEX|Nifty 50", exchange="NSE",
                  tradingsymbol="NIFTY",
                  received_ts=datetime.now(timezone.utc), ltp=500.0)
    asyncio.run(engine.evaluate(fresh))
    assert engine._stale_evaluations == 0


def test_monitoring_state_reports_the_reason() -> None:
    store = _store()
    manager = _Manager()
    _create_alert(store)
    engine = _engine(store, manager=manager)
    alert_id = list(engine._alerts)[0]

    engine.set_dependency_state_fn(
        lambda cid: {"state": "capacity_limited",
                     "reason": "provider is at its verified ceiling"})
    state = engine.monitoring_state(alert_id)
    assert state["state"] == "blocked"
    assert "ceiling" in state["reason"]

    engine.set_dependency_state_fn(
        lambda cid: {"state": "subscribed", "reason": ""})
    assert engine.monitoring_state(alert_id)["state"] == "waiting"

    engine.set_dependency_state_fn(lambda cid: {"state": "live", "reason": ""})
    assert engine.monitoring_state(alert_id)["state"] == "active"


def test_one_alert_does_not_reset_another_alert_state() -> None:
    """Creating a second alert must not clear the first alert's values."""
    store = _store()
    manager = _Manager()
    _create_alert(store, alert_id="c1")
    engine = _engine(store, manager=manager)
    alert_id = list(engine._alerts)[0]
    engine._last_values[alert_id] = 42.0
    engine._state[alert_id] = {"leaves": {"c1": {"last_result": "true",
                                                 "crossing_side": "up"}},
                               "root": {"last_result": "true",
                                        "crossing_side": "up"}}
    # A second alert appears (and a reload happens).
    _create_alert(store, alert_id="c2")
    engine.reload()
    assert engine._last_values.get(alert_id) == 42.0, \
        "an unrelated alert must not reset the evaluation state"
    assert engine._state[alert_id]["root"]["last_result"] == "true"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
