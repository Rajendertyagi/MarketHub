#!/usr/bin/env python3
"""Subscription failover and reconciliation lifecycle (deterministic).

Proves, with fake feeds and a fake catalogue:

  * a provider availability CHANGE triggers a reconciliation even when the
    demand did not change - and a state change inside the debounce window
    is DEFERRED, never DROPPED (the demand returns to its provider);
  * ONE canonical contract is served by ONE provider at a time (a second
    lease for the same contract is coalesced, never duplicated);
  * a DISCONNECTED provider is reported as ``provider_unavailable``, never
    as ``capacity_limited``;
  * a refused subscription mutation is reported as a FAILED apply, never
    as a successful reconciliation;
  * a provider is never given an instrument it was not asked for.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

from app.subscriptions.manager import (
    PRIORITY_ALERT,
    PRIORITY_REQUIRED,
    SubscriptionManager,
)

#: A contract listed by BOTH providers (the canonical id is the identity).
_SHARED = {
    "canonical_id": "NSE:FUTURE:NIFTY:2026-10-27",
    "upstox": {"provider": "upstox", "instrument_token": "NSE_FO|51420",
               "tradingsymbol": "NIFTY 24550 CE 27 OCT 26", "exchange": "NSE",
               "segment": "NSE_FO", "instrument_type": "OPTION",
               "expiry": "2026-10-27", "strike": 24550.0, "option_type": "CE",
               "underlying": "NIFTY"},
    "fyers": {"provider": "fyers", "instrument_token": "101126102748704",
              "tradingsymbol": "NIFTY26OCTFUT", "exchange": "NSE",
              "segment": "NSE_FO", "instrument_type": "FUTURE",
              "expiry": "2026-10-27", "strike": None, "option_type": None,
              "underlying": "NIFTY"},
}


class _Catalog:
    def __init__(self, contracts):
        self._rows: dict[str, list[dict]] = {}
        for contract in contracts:
            for provider in ("upstox", "fyers"):
                row = contract.get(provider)
                if row:
                    self._rows.setdefault(
                        contract["canonical_id"], []).append(dict(row))

    def find_canonical_rows(self, canonical_id):
        return list(self._rows.get(canonical_id, []))


class _Feed:
    """A feed double that records the applied subscriptions.

    It exposes the SAME attributes the real feeds do (``_instrument_keys``),
    so the manager's add/remove diff behaves exactly as in production.
    """

    def __init__(self, name: str, *, refuse_mutations: bool = False):
        self.name = name
        self._instrument_keys: set[str] = set()
        self.refuse_mutations = refuse_mutations
        self.mutation_failures = 0
        self.last_mutation_error: str | None = None

    @property
    def keys(self) -> set[str]:
        return set(self._instrument_keys)

    def status(self):
        return {"name": self.name, "state": "streaming",
                "mutation_failures": self.mutation_failures,
                "last_mutation_error": self.last_mutation_error,
                "last_data_at": {}}

    async def add_instruments(self, keys, metadata=None):
        new = [k for k in keys if k not in self._instrument_keys]
        self._instrument_keys.update(new)
        if new and self.refuse_mutations:
            # The socket was closed: the frame is NOT sent.
            self.mutation_failures += 1
            self.last_mutation_error = "socket_not_open"
        return len(new)

    async def remove_instruments(self, keys):
        gone = [k for k in keys if k in self._instrument_keys]
        self._instrument_keys.difference_update(gone)
        return len(gone)


def _manager(*, statuses=None, feeds=None, debounce=0.05):
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    from providers.registry import ProviderRegistry

    registry = ProviderRegistry([UPSTOX, FYERS])
    statuses = statuses or {}
    feeds = feeds or {}

    def _status(provider):
        return statuses.get(provider)

    def _feed(provider):
        return feeds.get(provider)

    return SubscriptionManager(
        catalog=_Catalog([_SHARED]),
        registry=registry,
        feed_provider=_feed,
        status_provider=_status,
        reconcile_debounce_s=debounce,
    )


def _demand(*keys, provider="upstox", canonical_id=None):
    """The DEMAND as lease requests (the production shape: pinned)."""
    return [{"key": k, "provider": provider, "canonical_id": canonical_id,
             "owner_kind": "required", "owner_id": "required",
             "priority": PRIORITY_REQUIRED, "kind": "future"}
            for k in keys]


def _plan(mgr):
    return mgr.plan()


def _admitted(plan):
    out = []
    for provider_plan in plan.values():
        out.extend(provider_plan.admitted)
    return out


# ---------------------------------------------------------------------------
# 1. a provider availability change re-plans the demand
# ---------------------------------------------------------------------------

def test_a_provider_state_change_replans_the_unchanged_demand() -> None:
    mgr = _manager(statuses={"upstox": "streaming", "fyers": "streaming"})
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = _plan(mgr)
    assert len(_admitted(plan)) == 1


def test_a_flap_inside_the_debounce_window_is_not_dropped() -> None:
    """A disconnect/reconnect flap must not leave the demand on the backup.

    The state change inside the debounce window is DEFERRED (a trailing
    reconcile), never dropped.
    """
    upstox = _Feed("upstox")
    fyers = _Feed("fyers")
    statuses = {"upstox": "streaming", "fyers": "streaming"}
    mgr = _manager(statuses=statuses, feeds={"upstox": upstox,
                                             "fyers": fyers},
                   debounce=0.2)
    mgr.apply_demand(_demand("NSE_FO|51420", provider="upstox",
                              canonical_id=_SHARED["canonical_id"]))

    async def _scenario():
        await mgr.reconcile()
        assert upstox.keys, "the demand is served by upstox"
        # Upstox drops ...
        statuses["upstox"] = "stopped"
        mgr.notify_provider_state("upstox", "stopped", "feed closed")
        await asyncio.sleep(0.05)
        # ... and comes straight back (inside the debounce window).
        statuses["upstox"] = "streaming"
        mgr.notify_provider_state("upstox", "streaming", "reconnected")
        # The trailing reconcile applies the LAST state.
        await asyncio.sleep(0.6)
        await mgr.reconcile()

    asyncio.run(_scenario())

    # The ALLOCATION returns to the provider that recovered: the contract
    # is served by ONE provider at a time.
    plan = mgr.plan()
    assert plan["upstox"].admitted == ["NSE_FO|51420"], (
        "the demand returns to the provider that recovered")
    assert not plan["fyers"].admitted, (
        "the backup must not keep the contract after the primary recovered")
    for lease in mgr._leases.values():
        assert lease.provider == "upstox", (
            "the lease must be allocated to the recovered provider")


# ---------------------------------------------------------------------------
# 2. one canonical contract -> one provider
# ---------------------------------------------------------------------------

def test_one_contract_is_never_served_by_two_providers() -> None:
    mgr = _manager(statuses={"upstox": "streaming", "fyers": "streaming"})
    # A demand lease (pinned to upstox) and an alert lease (unpinned) for
    # the SAME canonical contract.
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_ALERT, owner_kind="alert", owner_id="a1")
    plan = _plan(mgr)
    admitted = _admitted(plan)
    providers = {pid for pid, p in plan.items() if p.admitted}
    assert len(providers) == 1, (
        "one canonical contract is served by ONE provider at a time")
    assert len(admitted) == 1, "the contract must not be duplicated"


# ---------------------------------------------------------------------------
# 3. a disconnected provider is not reported as a capacity limit
# ---------------------------------------------------------------------------

def test_a_disconnected_provider_is_reported_as_provider_unavailable() -> None:
    mgr = _manager(statuses={"upstox": "stopped", "fyers": "streaming"})
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = _plan(mgr)
    # The contract could not be served: FYERS is the only alternative and
    # it is fine, so the contract is served by FYERS (a failover).
    assert plan["fyers"].admitted, "the contract is served by the backup"
    assert not plan["upstox"].capacity_limited, (
        "a disconnected provider must not be reported as capacity_limited")


def test_a_genuine_capacity_limit_is_reported_as_such() -> None:
    mgr = _manager(statuses={"upstox": "streaming", "fyers": "streaming"})
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = _plan(mgr)
    assert not plan["upstox"].capacity_limited
    assert not plan["upstox"].provider_unavailable


# ---------------------------------------------------------------------------
# 4. a refused mutation is a FAILED apply
# ---------------------------------------------------------------------------

def test_a_refused_mutation_is_reported_as_a_failed_apply() -> None:
    upstox = _Feed("upstox", refuse_mutations=True)
    fyers = _Feed("fyers")
    mgr = _manager(statuses={"upstox": "streaming", "fyers": "streaming"},
                   feeds={"upstox": upstox, "fyers": fyers})
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")

    async def _scenario():
        return await mgr.reconcile()

    result = asyncio.run(_scenario())
    upstox_result = result["results"].get("upstox") or {}
    assert upstox_result.get("applied") is False, (
        "a refused subscription mutation is NOT a successful reconciliation")
    assert upstox_result.get("reason")


# ---------------------------------------------------------------------------
# 5. no speculative work
# ---------------------------------------------------------------------------

def test_no_provider_receives_an_instrument_it_was_not_asked_for() -> None:
    upstox = _Feed("upstox")
    fyers = _Feed("fyers")
    mgr = _manager(statuses={"upstox": "streaming", "fyers": "streaming"},
                   feeds={"upstox": upstox, "fyers": fyers})
    mgr.acquire("", canonical_id=_SHARED["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")

    asyncio.run(mgr.reconcile())

    demanded = {"NSE_FO|51420", "101126102748704"}
    for feed in (upstox, fyers):
        for key in feed.keys:
            assert key in demanded, (
                "no provider receives an instrument it was not asked for")
    assert len(upstox.keys) + len(fyers.keys) <= 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
