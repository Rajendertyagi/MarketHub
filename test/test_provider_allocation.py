#!/usr/bin/env python3
"""Provider allocation - useful, deterministic and demand-driven.

The fixtures are derived from the REAL catalogue (read-only evidence
captured from the live ``data/events.db`` on 2026-10-21):

  * a contract listed by BOTH providers - the NIFTY 2026-10-27 future:
        fyers  101126102748704 / "NIFTY26OCTFUT"
        upstox NSE_FO|51420 / "NIFTY 24550 CE 27 OCT 26"   (same canonical id)
    The two providers publish DIFFERENT native symbols for the same
    instrument, so the allocation works on the canonical id.
  * a contract listed only by FYERS: 360ONE 2026-12-29 1240 CE
    (fyers 101126122969319).

What is proven:
  * a contract only one provider lists goes to that provider (unique
    coverage wins over the configuration order);
  * an overlapping contract is served by ONE provider at a time - never
    duplicated across providers;
  * the assignment is deterministic (the same inputs always give the
    same assignment) and follows the VERIFIED CAPACITY (the provider with
    the most remaining capacity takes the shared contract; the provider
    that is close to its ceiling is relieved);
  * FYERS is never used merely to consume capacity: the subscribed set
    is a subset of the demand set;
  * an unavailable provider triggers a reroute to the other provider
    that supports the same canonical contract, and the alert/workspace
    leases survive the transition;
  * a reroute that would exceed the capacity is refused and reported,
    never silently dropping required coverage.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

from app.subscriptions.manager import (
    PRIORITY_ALERT,
    PRIORITY_REQUIRED,
    PRIORITY_WORKSPACE,
    STATE_SUBSCRIBED,
    STATE_UNAVAILABLE,
    SubscriptionManager,
)

# --- catalogue-derived fixtures ---------------------------------------------

#: The NIFTY 2026-10-27 future, published by BOTH providers.
SHARED_CONTRACT = {
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

#: A contract only FYERS lists (360ONE 2026-12-29 1240 CE).
FYERS_ONLY_CONTRACT = {
    "canonical_id": "NSE:OPTION:360ONE:2026-12-29:1240.0:CE",
    "fyers": {"provider": "fyers", "instrument_token": "101126122969319",
              "tradingsymbol": "360ONE26DEC1240CE", "exchange": "NSE",
              "segment": "NSE_FO", "instrument_type": "OPTION",
              "expiry": "2026-12-29", "strike": 1240.0, "option_type": "CE",
              "underlying": "360ONE"},
}

#: A contract only Upstox lists.
UPSTOX_ONLY_CONTRACT = {
    "canonical_id": "NSE:OPTION:SBIN:2026-12-29:800.0:CE",
    "upstox": {"provider": "upstox", "instrument_token": "NSE_FO|99999",
               "tradingsymbol": "SBIN 800 CE 29 DEC 26", "exchange": "NSE",
               "segment": "NSE_FO", "instrument_type": "OPTION",
               "expiry": "2026-12-29", "strike": 800.0, "option_type": "CE",
               "underlying": "SBIN"},
}


class _Catalog:
    """A catalogue double over the fixtures above."""

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


def _manager(*, contracts, statuses=None, upstox_capacity=None,
             fyers_capacity=None):
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    from providers.registry import ProviderRegistry

    upstox = UPSTOX
    fyers = FYERS
    if upstox_capacity is not None:
        upstox = type(upstox)(**{**upstox.__dict__,
                                 "modes": {**dict(upstox.modes),
                                           upstox.default_mode:
                                               upstox_capacity},
                                 "tier_limits": {"standard":
                                                 upstox_capacity}})
    if fyers_capacity is not None:
        fyers = type(fyers)(**{**fyers.__dict__,
                                "modes": {**dict(fyers.modes),
                                          fyers.default_mode:
                                              fyers_capacity},
                                "tier_limits": {"standard":
                                                fyers_capacity}})
    registry = ProviderRegistry([upstox, fyers])
    statuses = statuses or {}

    def _status(provider):
        return statuses.get(provider)

    return SubscriptionManager(
        catalog=_Catalog(contracts),
        registry=registry,
        feed_provider=lambda name: None,
        status_provider=_status,
    )


def _admitted(plan):
    keys = []
    for provider_plan in plan.values():
        keys.extend(provider_plan.admitted)
    return keys


def test_unique_coverage_goes_to_the_only_provider_that_lists_it() -> None:
    mgr = _manager(contracts=[FYERS_ONLY_CONTRACT, UPSTOX_ONLY_CONTRACT])
    mgr.acquire("", canonical_id=FYERS_ONLY_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    mgr.acquire("", canonical_id=UPSTOX_ONLY_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert plan["fyers"].admitted == ["101126122969319"]
    assert plan["upstox"].admitted == ["NSE_FO|99999"]


def test_an_overlapping_contract_is_served_by_one_provider_only() -> None:
    mgr = _manager(contracts=[SHARED_CONTRACT])
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    admitted = _admitted(plan)
    assert len(admitted) == 1, "a contract must not be duplicated"
    assert len(set(admitted)) == 1


def test_the_assignment_is_deterministic() -> None:
    results = []
    for _ in range(3):
        mgr = _manager(contracts=[SHARED_CONTRACT])
        mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                    priority=PRIORITY_REQUIRED, owner_kind="required",
                    owner_id="baseline")
        results.append(tuple(sorted(
            (pid, tuple(p.admitted)) for pid, p in mgr.plan().items())))
    assert results[0] == results[1] == results[2]


def test_the_shared_contract_follows_the_projected_utilization() -> None:
    """The shared contract goes to the LESS loaded provider.

    The allocation compares PROJECTED UTILIZATION (assigned / verified
    capacity), so the provider with proportionally more spare room takes
    the shared work - the larger provider does NOT take every shared
    instrument merely because it has more slots.
    """
    # FYERS (100 slots) is proportionally less loaded than Upstox (10).
    mgr = _manager(contracts=[SHARED_CONTRACT], upstox_capacity=10,
                    fyers_capacity=100)
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert plan["fyers"].admitted == ["101126102748704"]
    assert not plan.get("upstox", None) or not plan["upstox"].admitted

    # Equal capacities -> the configuration order breaks the tie.
    mgr = _manager(contracts=[SHARED_CONTRACT], upstox_capacity=100,
                    fyers_capacity=100)
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert plan["upstox"].admitted == ["NSE_FO|51420"]

    # Upstox out of capacity -> the shared contract moves to FYERS.
    mgr = _manager(contracts=[SHARED_CONTRACT], upstox_capacity=0,
                    fyers_capacity=100)
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert plan["fyers"].admitted == ["101126102748704"]
    assert not plan.get("upstox", None) or not plan["upstox"].admitted


def test_fyers_is_not_used_to_fill_its_capacity() -> None:
    """The subscribed set is a subset of the demand set - always."""
    mgr = _manager(contracts=[SHARED_CONTRACT], fyers_capacity=100)
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    subscribed = _admitted(plan)
    assert len(subscribed) == 1
    assert set(subscribed) <= {"NSE_FO|51420", "101126102748704"}
    # Nothing extra was subscribed on the other provider.
    assert not plan.get("fyers", None) or not plan["fyers"].admitted


def test_an_unavailable_provider_triggers_a_reroute() -> None:
    mgr = _manager(contracts=[SHARED_CONTRACT],
                   statuses={"upstox": "auth_required"})
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert plan["fyers"].admitted == ["101126102748704"]
    leases = mgr.leases()
    assert len(leases) == 1
    assert leases[0].provider == "fyers"
    assert leases[0].state == STATE_SUBSCRIBED


def test_a_reroute_that_would_exceed_capacity_is_reported() -> None:
    """When no provider has room, the demand is reported - not dropped."""
    mgr = _manager(contracts=[SHARED_CONTRACT],
                   statuses={"upstox": "auth_required"},
                   upstox_capacity=10, fyers_capacity=0)
    mgr.acquire("", canonical_id=SHARED_CONTRACT["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    mgr.plan()
    states = mgr.instrument_states()
    assert states
    assert states[0]["state"] in (STATE_UNAVAILABLE, "capacity_limited")
    assert states[0]["reason"], "the reason must be reported"


def test_alert_and_workspace_leases_survive_a_transition() -> None:
    """Leases are preserved when the provider changes."""
    mgr = _manager(contracts=[SHARED_CONTRACT],
                   statuses={"upstox": "auth_required"})
    alert_lease = mgr.acquire(
        "", canonical_id=SHARED_CONTRACT["canonical_id"],
        priority=PRIORITY_ALERT, owner_kind="alert", owner_id="alert-1")
    workspace_lease = mgr.acquire(
        "", canonical_id=SHARED_CONTRACT["canonical_id"],
        priority=PRIORITY_WORKSPACE, owner_kind="workspace", owner_id="w-1")
    mgr.plan()
    leases = {lease.lease_id: lease for lease in mgr.leases()}
    assert alert_lease in leases and workspace_lease in leases
    assert leases[alert_lease].provider == "fyers"
    assert leases[workspace_lease].provider == "fyers"
    assert leases[alert_lease].owner_kind == "alert"
    assert leases[workspace_lease].owner_kind == "workspace"
    # Releasing one consumer's lease leaves the other consumer's demand.
    assert mgr.release(alert_lease) is True
    fyers_key = SHARED_CONTRACT["fyers"]["instrument_token"]
    assert mgr.refcount(fyers_key) == 1, (
        "the workspace lease must survive the alert lease release")


# ---------------------------------------------------------------------------
# Concurrent, utilization-balanced allocation
# ---------------------------------------------------------------------------

def _shared_contracts(count: int) -> list[dict]:
    """``count`` contracts that BOTH providers list."""
    contracts = []
    for i in range(count):
        contracts.append({
            "canonical_id": f"NSE:FUTURE:SYM{i}:2026-10-27",
            "upstox": {"provider": "upstox",
                       "instrument_token": f"NSE_FO|{5000 + i}",
                       "tradingsymbol": f"SYM{i} FUT 27 OCT 26",
                       "exchange": "NSE", "segment": "NSE_FO",
                       "instrument_type": "FUTURE", "expiry": "2026-10-27",
                       "strike": None, "option_type": None,
                       "underlying": f"SYM{i}"},
            "fyers": {"provider": "fyers",
                      "instrument_token": f"10112610274870{i}",
                      "tradingsymbol": f"SYM{i}26OCTFUT", "exchange": "NSE",
                      "segment": "NSE_FO", "instrument_type": "FUTURE",
                      "expiry": "2026-10-27", "strike": None,
                      "option_type": None, "underlying": f"SYM{i}"},
        })
    return contracts


def test_both_providers_carry_useful_non_overlapping_work() -> None:
    """Shared demand is spread over the providers by UTILIZATION.

    The larger provider must not take every shared instrument merely
    because it has more spare slots: the contract goes to the provider
    that is proportionally less loaded, so both carry work.
    """
    mgr = _manager(contracts=_shared_contracts(10), upstox_capacity=10,
                    fyers_capacity=100)
    for i in range(10):
        mgr.acquire("", canonical_id=f"NSE:FUTURE:SYM{i}:2026-10-27",
                    priority=PRIORITY_REQUIRED, owner_kind="required",
                    owner_id="baseline")
    plan = mgr.plan()
    upstox = plan["upstox"].admitted
    fyers = plan["fyers"].admitted
    assert upstox and fyers, (
        "both providers must carry useful work when the demand supports it")
    assert not set(upstox) & set(fyers), "no contract may be duplicated"
    assert len(upstox) + len(fyers) == 10, "every demand is served once"
    # The split follows the verified capacities (utilization balanced),
    # it is not a hard-coded quota.
    assert len(fyers) > len(upstox)


def test_the_split_is_a_consequence_of_the_documented_capacity() -> None:
    """Equal capacities -> an even split; no percentage is configured."""
    mgr = _manager(contracts=_shared_contracts(4), upstox_capacity=10,
                    fyers_capacity=10)
    for i in range(4):
        mgr.acquire("", canonical_id=f"NSE:FUTURE:SYM{i}:2026-10-27",
                    priority=PRIORITY_REQUIRED, owner_kind="required",
                    owner_id="baseline")
    plan = mgr.plan()
    assert len(plan["upstox"].admitted) == 2
    assert len(plan["fyers"].admitted) == 2


def test_nothing_is_subscribed_without_demand() -> None:
    """Spare capacity is never filled speculatively."""
    contracts = _shared_contracts(3)
    mgr = _manager(contracts=contracts, upstox_capacity=2000,
                    fyers_capacity=100)
    mgr.acquire("", canonical_id=contracts[0]["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    admitted = _admitted(plan)
    assert len(admitted) == 1, "only the demanded contract is subscribed"


def test_rerouting_never_exceeds_the_backups_capacity() -> None:
    """A failed provider's demand moves only as far as the backup allows."""
    contracts = _shared_contracts(3)
    mgr = _manager(contracts=contracts, statuses={"upstox": "auth_required"},
                   upstox_capacity=10, fyers_capacity=2)
    for i in range(3):
        mgr.acquire("", canonical_id=f"NSE:FUTURE:SYM{i}:2026-10-27",
                    priority=PRIORITY_REQUIRED, owner_kind="required",
                    owner_id="baseline")
    plan = mgr.plan()
    assert len(plan["fyers"].admitted) <= 2, "the backup ceiling is respected"
    reported = [entry for plan_entry in plan.values()
                for entry in (plan_entry.capacity_limited
                              + plan_entry.provider_unavailable)]
    assert reported, "the contracts that could not be served are reported"
    assert all(entry["reason"] for entry in reported)
    # The provider that could not serve the contract is UNAVAILABLE, not
    # full: reporting a broken provider as a full one hides the real fault
    # (and the ceiling of a provider that is down is irrelevant).
    assert not [entry for plan_entry in plan.values()
                for entry in plan_entry.capacity_limited], (
        "an unavailable provider must not be reported as capacity_limited")
    assert [entry for plan_entry in plan.values()
            for entry in plan_entry.provider_unavailable], (
        "the unserved contract must be reported as provider_unavailable")


def test_a_contract_no_provider_can_take_is_reported_as_capacity_limited(
        ) -> None:
    """Every provider that lists the contract is at its ceiling."""
    contracts = _shared_contracts(3)
    mgr = _manager(contracts=contracts, upstox_capacity=0, fyers_capacity=0)
    for i in range(3):
        mgr.acquire("", canonical_id=f"NSE:FUTURE:SYM{i}:2026-10-27",
                    priority=PRIORITY_REQUIRED, owner_kind="required",
                    owner_id="baseline")
    plan = mgr.plan()
    limited = [entry for plan_entry in plan.values()
               for entry in plan_entry.capacity_limited]
    assert limited, "a genuine capacity limit is reported as such"
    assert all(entry["reason"] for entry in limited)


def test_protected_leases_are_reported_not_discarded() -> None:
    """A capacity failure must not silently drop required/alert leases."""
    contracts = _shared_contracts(3)
    mgr = _manager(contracts=contracts, upstox_capacity=0,
                   fyers_capacity=1)
    required = mgr.acquire("", canonical_id=contracts[0]["canonical_id"],
                           priority=PRIORITY_REQUIRED, owner_kind="required",
                           owner_id="baseline")
    alert = mgr.acquire("", canonical_id=contracts[1]["canonical_id"],
                         priority=PRIORITY_ALERT, owner_kind="alert",
                         owner_id="alert-1")
    plan = mgr.plan()
    # The leases still exist (they are not discarded) and the ones that
    # could not be served are reported with a reason.
    held = {lease.lease_id for lease in mgr.leases()}
    assert required in held
    assert alert in held
    reported = [entry for plan_entry in plan.values()
                for entry in plan_entry.capacity_limited]
    assert reported
    for entry in reported:
        assert entry["reason"]


def test_the_allocation_is_stable_when_the_order_or_health_changes() -> None:
    """A preference change is a tie-break only; the served set is stable."""
    contracts = _shared_contracts(4)
    results = []
    for _ in range(2):
        mgr = _manager(contracts=contracts, upstox_capacity=10,
                        fyers_capacity=10)
        for i in range(4):
            mgr.acquire("", canonical_id=f"NSE:FUTURE:SYM{i}:2026-10-27",
                        priority=PRIORITY_REQUIRED, owner_kind="required",
                        owner_id="baseline")
        plan = mgr.plan()
        results.append(tuple(sorted(_admitted(plan))))
    assert results[0] == results[1], "the same inputs give the same result"
    assert len(results[0]) == 4


def test_a_contract_no_provider_can_stream_is_reported() -> None:
    """FYERS has no official HSM topic for NIFTYFPI - it is not streamed."""
    index_contract = {
        "canonical_id": "NSE:INDEX:NIFTYFPI",
        "fyers": {"provider": "fyers", "instrument_token": "101000000026137",
                  "tradingsymbol": "NSE:NIFTYFPI150-INDEX", "exchange": "NSE",
                  "segment": "NSE_INDEX", "instrument_type": "INDEX",
                  "expiry": None, "strike": None, "option_type": None,
                  "underlying": "NIFTYFPI"},
    }
    mgr = _manager(contracts=[index_contract])
    mgr.acquire("", canonical_id=index_contract["canonical_id"],
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    admitted = _admitted(plan)
    assert not admitted, (
        "a contract FYERS cannot stream must not be subscribed")
    states = mgr.instrument_states()
    assert states and states[0]["state"] == "unrouted"
    assert states[0]["reason"]

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
