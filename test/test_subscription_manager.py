#!/usr/bin/env python3
"""Lease-based, demand-driven subscription manager.

Proves:
  * a lease is a consumer's need; shared subscriptions are
    reference-counted and one consumer cannot remove another's;
  * the SUBSCRIBED set is a subset of the DEMAND set - nothing is
    subscribed because capacity is free;
  * the verified provider ceiling is the bound, and demand above it is
    reported (capacity_limited) instead of silently dropped;
  * a higher-priority lease releases a lower-priority one before it is
    admitted (the broker limit is never exceeded, not even transiently);
  * a protected dependency is never displaced for optional demand;
  * a provider failure does not abort the other provider's reconciliation.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.subscriptions.manager import (
    PRIORITY_ALERT,
    PRIORITY_OTHER,
    PRIORITY_REQUIRED,
    PRIORITY_WORKSPACE,
    SubscriptionManager,
)


class _Feed:
    """A feed double that records what it was asked to subscribe."""

    def __init__(self, name="upstox", state="streaming"):
        self.name = name
        self._instrument_keys: tuple[str, ...] = ()
        self.state = state
        self.added: list[str] = []
        self.removed: list[str] = []

    def status(self):
        return {"state": self.state, "last_data_at": {}}

    async def add_instruments(self, keys, metadata=None):
        self.added.extend(keys)
        cur = set(self._instrument_keys)
        fresh = [k for k in keys if k not in cur]
        self._instrument_keys = tuple(sorted(cur | set(fresh)))
        return len(fresh)

    async def remove_instruments(self, keys):
        self.removed.extend(keys)
        cur = set(self._instrument_keys)
        cur -= set(keys)
        self._instrument_keys = tuple(sorted(cur))
        return len(keys)


def _manager(*, feeds=None, budgets=None):
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    from providers.registry import ProviderRegistry

    # A COPY of the adapter declarations: the shared capability objects
    # must never be mutated (it would leak into every other test).
    capabilities = []
    for caps in (UPSTOX, FYERS):
        ceiling = (budgets or {}).get(caps.provider)
        if ceiling is None:
            capabilities.append(caps)
            continue
        capabilities.append(type(caps)(**{
            **caps.__dict__,
            "tier_limits": {**dict(caps.tier_limits), "standard": ceiling},
            "modes": {**dict(caps.modes), caps.default_mode: ceiling},
        }))
    registry = ProviderRegistry(capabilities)
    feeds = feeds or {}
    return SubscriptionManager(
        registry=registry,
        feed_provider=lambda name: feeds.get(name),
        status_provider=lambda name: (feeds.get(name).state
                                      if feeds.get(name) else None),
    )


def test_shared_subscription_is_reference_counted() -> None:
    mgr = _manager()
    first = mgr.acquire("NSE_EQ|INE002A01018", provider="upstox",
                        priority=PRIORITY_WORKSPACE, owner_kind="workspace",
                        owner_id="w1")
    second = mgr.acquire("NSE_EQ|INE002A01018", provider="upstox",
                         priority=PRIORITY_WORKSPACE, owner_kind="workspace",
                         owner_id="w2")
    assert mgr.refcount("NSE_EQ|INE002A01018") == 2
    mgr.release(first)
    assert mgr.refcount("NSE_EQ|INE002A01018") == 1
    plan = mgr.plan()
    assert plan["upstox"].admitted == ["NSE_EQ|INE002A01018"]
    mgr.release(second)
    assert mgr.refcount("NSE_EQ|INE002A01018") == 0
    assert mgr.plan().get("upstox") is None or \
        mgr.plan()["upstox"].admitted == []


def test_subscribed_set_is_a_subset_of_the_demand_set() -> None:
    """Capacity stays unused unless the demand asks for it."""
    mgr = _manager()
    mgr.acquire("NSE_EQ|INE002A01018", provider="upstox",
                priority=PRIORITY_REQUIRED)
    plan = mgr.plan()
    admitted = set(plan["upstox"].admitted)
    demand = {"NSE_EQ|INE002A01018"}
    assert admitted <= demand
    assert plan["upstox"].budget == 2000, "the ceiling is not a target"


def test_demand_above_the_verified_ceiling_is_reported() -> None:
    mgr = _manager(budgets={"upstox": 3})
    for i in range(5):
        mgr.acquire(f"NSE_EQ|BULK{i}", provider="upstox",
                    priority=PRIORITY_REQUIRED)
    plan = mgr.plan()["upstox"]
    assert len(plan.admitted) == 3
    assert len(plan.capacity_limited) == 2
    assert plan.capacity_limited[0]["reason"]
    assert plan.budget == 3


def test_higher_priority_releases_a_lower_priority_lease() -> None:
    mgr = _manager(budgets={"upstox": 2})
    mgr.acquire("NSE_EQ|LOW", provider="upstox", priority=PRIORITY_OTHER)
    mgr.acquire("NSE_EQ|LOW2", provider="upstox", priority=PRIORITY_OTHER)
    mgr.acquire("NSE_EQ|HIGH", provider="upstox", priority=PRIORITY_REQUIRED)
    plan = mgr.plan()["upstox"]
    assert "NSE_EQ|HIGH" in plan.admitted
    assert len(plan.admitted) == 2, "the ceiling must never be exceeded"
    assert plan.capacity_limited, "the displaced lease must be reported"


def test_protected_dependency_is_never_displaced() -> None:
    mgr = _manager(budgets={"upstox": 1})
    mgr.acquire("NSE_EQ|REQUIRED", provider="upstox", priority=PRIORITY_REQUIRED)
    mgr.acquire("NSE_EQ|OPTIONAL", provider="upstox", priority=PRIORITY_OTHER)
    plan = mgr.plan()["upstox"]
    assert plan.admitted == ["NSE_EQ|REQUIRED"]
    limited = {entry["key"] for entry in plan.capacity_limited}
    assert "NSE_EQ|OPTIONAL" in limited
    assert "NSE_EQ|REQUIRED" not in limited


def test_reconcile_never_exceeds_the_ceiling() -> None:
    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed}, budgets={"upstox": 2})
    for i in range(4):
        mgr.acquire(f"NSE_EQ|BULK{i}", provider="upstox",
                    priority=PRIORITY_OTHER)
    out = asyncio.run(mgr.reconcile())
    assert out["results"]["upstox"]["applied"] is True
    assert len(feed._instrument_keys) <= 2


def test_one_provider_failure_does_not_abort_the_other() -> None:
    upstox = _Feed("upstox")
    mgr = _manager(feeds={"upstox": upstox})
    mgr.acquire("NSE_EQ|A", provider="upstox", priority=PRIORITY_REQUIRED)
    mgr.acquire("NSE:FY-EQ", provider="fyers", priority=PRIORITY_REQUIRED)
    out = asyncio.run(mgr.reconcile())
    assert out["results"]["fyers"]["applied"] is False
    assert out["results"]["fyers"]["reason"] == "feed unavailable"
    assert out["results"]["upstox"]["applied"] is True
    assert upstox._instrument_keys == ("NSE_EQ|A",)


def test_releasing_an_owner_releases_all_its_leases() -> None:
    mgr = _manager()
    mgr.acquire("NSE_EQ|A", provider="upstox", priority=PRIORITY_ALERT,
                owner_kind="alert", owner_id="alert-1")
    mgr.acquire("NSE_EQ|B", provider="upstox", priority=PRIORITY_ALERT,
                owner_kind="alert", owner_id="alert-1")
    mgr.acquire("NSE_EQ|C", provider="upstox", priority=PRIORITY_ALERT,
                owner_kind="alert", owner_id="alert-2")
    assert mgr.release_owner("alert", "alert-1") == 2
    assert mgr.refcount("NSE_EQ|A") == 0
    assert mgr.refcount("NSE_EQ|C") == 1


def test_unrouted_instrument_is_reported() -> None:
    mgr = _manager()
    mgr.acquire("SOME:UNKNOWN", canonical_id=None, provider=None,
                priority=PRIORITY_REQUIRED)
    plan = mgr.plan()
    assert "unrouted" in plan
    assert plan["unrouted"].unreachable == ["SOME:UNKNOWN"]


def test_instrument_states_expose_the_reason() -> None:
    feed = _Feed(state="auth_required")
    mgr = _manager(feeds={"upstox": feed})
    mgr.acquire("NSE_EQ|A", provider="upstox", priority=PRIORITY_REQUIRED)
    states = mgr.instrument_states()
    assert states[0]["state"] == "provider_unavailable"
    assert "auth_required" in states[0]["reason"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
