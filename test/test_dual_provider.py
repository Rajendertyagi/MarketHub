#!/usr/bin/env python3
"""Dual-provider operation: different contracts, no routine duplicates.

Proves:
  * a canonical contract is routed to ONE provider at a time (no routine
    duplication across Upstox and FYERS);
  * a contract that only ONE provider lists goes to that provider
    (unique coverage wins over the configuration order);
  * both providers serve DIFFERENT canonical contracts concurrently;
  * when the selected provider becomes unavailable the contract is
    REROUTED to the other enabled provider (the lease is preserved);
  * when no provider can serve it, the failure is reported - never
    hidden and never presented as live coverage.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.subscriptions.manager import (
    PRIORITY_REQUIRED,
    STATE_SUBSCRIBED,
    STATE_UNAVAILABLE,
    SubscriptionManager,
)


class _Catalog:
    """A catalogue double: which provider lists which canonical contract."""

    def __init__(self, mapping):
        # canonical id -> {provider: (key, segment)}
        self._mapping = dict(mapping)

    def find_canonical_rows(self, canonical_id):
        rows = []
        for provider, (key, segment) in self._mapping.get(
                canonical_id, {}).items():
            rows.append({"provider": provider, "instrument_token": key,
                         "tradingsymbol": key, "exchange": segment,
                         "segment": segment, "instrument_type": "FUTURE",
                         "underlying": canonical_id})
        return rows


def _manager(*, statuses=None, feeds=None):
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    from providers.registry import ProviderRegistry

    registry = ProviderRegistry([UPSTOX, FYERS])
    statuses = statuses or {}
    feeds = feeds or {}

    def _status(provider):
        return statuses.get(provider)

    return SubscriptionManager(
        catalog=_Catalog({
            "NSE:FUTURE:NIFTY:2026-10-29": {
                "upstox": ("NSE_FO|101", "NSE"),
                "fyers": ("NSE:NIFTY26OCTFUT", "NSE"),
            },
            "NSE:FUTURE:RELIANCE:2026-10-29": {
                "upstox": ("NSE_FO|202", "NSE"),
                "fyers": ("NSE:RELIANCE26OCTFUT", "NSE"),
            },
            "NSE:FUTURE:ZEBRA:2026-10-29": {
                # ONLY FYERS lists this contract (unique coverage).
                "fyers": ("NSE:ZEBRA26OCTFUT", "NSE"),
            },
        }),
        registry=registry,
        feed_provider=lambda name: feeds.get(name),
        status_provider=_status,
    )


def test_a_contract_is_routed_to_one_provider_only() -> None:
    mgr = _manager()
    mgr.acquire("", canonical_id="NSE:FUTURE:NIFTY:2026-10-29",
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    routed = [pid for pid, p in plan.items() if p.admitted]
    total = sum(len(p.admitted) for p in plan.values())
    assert total == 1, plan
    assert routed == ["upstox"], plan          # configuration order wins
    assert len(set(total_keys(plan))) == total


def total_keys(plan):
    keys = []
    for p in plan.values():
        keys.extend(p.admitted)
    return keys


def test_unique_coverage_goes_to_the_only_provider_that_lists_it() -> None:
    mgr = _manager()
    mgr.acquire("", canonical_id="NSE:FUTURE:ZEBRA:2026-10-29",
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert "fyers" in plan
    assert plan["fyers"].admitted == ["NSE:ZEBRA26OCTFUT"]
    assert "upstox" not in plan or not plan["upstox"].admitted


def test_both_providers_serve_different_contracts_concurrently() -> None:
    mgr = _manager()
    mgr.acquire("", canonical_id="NSE:FUTURE:NIFTY:2026-10-29",
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    mgr.acquire("", canonical_id="NSE:FUTURE:ZEBRA:2026-10-29",
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    upstox_keys = plan.get("upstox").admitted if "upstox" in plan else []
    fyers_keys = plan.get("fyers").admitted if "fyers" in plan else []
    assert upstox_keys and fyers_keys, plan
    # No contract is served by both providers.
    assert not set(upstox_keys) & set(fyers_keys)


def test_unavailable_provider_triggers_a_reroute() -> None:
    """The contract moves to the other provider; the lease is preserved."""
    mgr = _manager(statuses={"upstox": "auth_required"})
    mgr.acquire("", canonical_id="NSE:FUTURE:NIFTY:2026-10-29",
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    plan = mgr.plan()
    assert "fyers" in plan
    assert plan["fyers"].admitted == ["NSE:NIFTY26OCTFUT"], plan
    # The lease survived the reroute.
    leases = mgr.leases()
    assert len(leases) == 1
    assert leases[0].provider == "fyers"
    assert leases[0].state == STATE_SUBSCRIBED


def test_no_alternative_provider_reports_the_failure_honestly() -> None:
    mgr = _manager(statuses={"fyers": "auth_required"})
    mgr.acquire("", canonical_id="NSE:FUTURE:ZEBRA:2026-10-29",
                priority=PRIORITY_REQUIRED, owner_kind="required",
                owner_id="baseline")
    mgr.plan()
    states = mgr.instrument_states()
    assert states
    assert states[0]["state"] == STATE_UNAVAILABLE
    assert "fyers" in states[0]["reason"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
