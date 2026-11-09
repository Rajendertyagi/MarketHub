#!/usr/bin/env python3
"""The demand -> lease -> subscription path (the startup data path).

Proves the contract that the live run violated:

  * ``apply_demand`` translates the resolved demand into leases;
  * the demand is a FUNCTION of the demand: a contract that is no longer
    demanded is unsubscribed, and re-applying the same demand does not
    duplicate leases (the lease set is reference-counted);
  * a demand whose provider cannot serve it is routed to a provider that
    CAN, using the canonical identity of the contract;
  * an instrument nobody demands is never subscribed;
  * a lease owned by somebody else (an alert's dependency) survives a
    demand re-apply.

No network, no broker, no live database: isolated doubles only.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from app.subscriptions.manager import (
    PRIORITY_REQUIRED,
    SubscriptionManager,
)


class _Feed:
    """A feed double that records what it was asked to subscribe."""

    def __init__(self, name="upstox", state="streaming"):
        self.name = name
        self.state = state
        self._instrument_keys: tuple[str, ...] = ()
        self.added: list[str] = []
        self.removed: list[str] = []

    def status(self):
        return {"state": self.state, "last_data_at": {}}

    async def add_instruments(self, keys, metadata=None):
        cur = set(self._instrument_keys)
        fresh = [k for k in keys if k not in cur]
        self._instrument_keys = tuple(sorted(cur | set(fresh)))
        self.added.extend(keys)
        return len(fresh)

    async def remove_instruments(self, keys):
        cur = set(self._instrument_keys) - set(keys)
        self._instrument_keys = tuple(sorted(cur))
        self.removed.extend(keys)
        return len(keys)


class _Catalog:
    """A catalogue double: one row per provider for one canonical contract.

    The rows look like real catalogue rows (provider, instrument token,
    provider symbol, tradingsymbol, exchange, instrument type) because the
    routing derives the provider's FEED key and the streamability from
    exactly those fields.
    """

    def __init__(self, rows: list[dict] | None = None):
        self._rows = list(rows or [])

    def get_by_key(self, key):
        for row in self._rows:
            if key in (row.get("instrument_token"),
                       row.get("provider_symbol")):
                return row
        return None

    def find_canonical_rows(self, canonical_id):
        from app.canonical_identity import canonical_id_for_row
        out = []
        for row in self._rows:
            try:
                if canonical_id_for_row(row) == canonical_id:
                    out.append(row)
            except Exception:
                continue
        return out


def _index_rows(canonical_id: str) -> list[dict]:
    """The two catalogue rows of one index (Upstox + Fyers)."""
    label = canonical_id.rsplit(":", 1)[-1]
    upstox_symbol = {
        "NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank",
        "SENSEX": "SENSEX", "BANKEX": "BANKEX",
    }.get(label, label)
    fyers_symbol = {
        "NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "SENSEX": "BSE:SENSEX-INDEX", "BANKEX": "BSE:BANKEX-INDEX",
    }.get(label, f"NSE:{label}-INDEX")
    exchange = "BSE" if label in ("SENSEX", "BANKEX") else "NSE"
    return [
        {"provider": "upstox", "instrument_token": f"{exchange}_INDEX|{upstox_symbol}",
         "provider_symbol": f"{exchange}_INDEX|{upstox_symbol}",
         "tradingsymbol": label, "exchange": exchange,
         "instrument_type": "INDEX", "underlying": None},
        {"provider": "fyers", "instrument_token": f"101{label}",
         "provider_symbol": fyers_symbol, "tradingsymbol": fyers_symbol,
         "exchange": exchange, "instrument_type": "INDEX",
         "underlying": label},
    ]


def _manager(*, feeds=None, catalog=None):
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    from providers.registry import ProviderRegistry

    registry = ProviderRegistry([UPSTOX, FYERS])
    feeds = feeds or {}
    return SubscriptionManager(
        catalog=catalog,
        registry=registry,
        feed_provider=lambda name: feeds.get(name),
        status_provider=lambda name: (feeds.get(name).state
                                     if feeds.get(name) else None),
    )


def _demand(*keys, provider="upstox", kind="index"):
    return [{"key": k, "provider": provider, "canonical_id": None,
             "owner_kind": "required", "owner_id": "required",
             "priority": PRIORITY_REQUIRED, "kind": kind}
            for k in keys]


def test_demand_becomes_leases_and_reaches_the_feed() -> None:
    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed})
    out = mgr.apply_demand(_demand("NSE_EQ|A"))
    assert out["requested"] == 1
    assert out["leases"] == 1
    assert not out["rejected"]
    assert mgr.refcount("NSE_EQ|A") == 1
    asyncio.run(mgr.reconcile())
    assert feed._instrument_keys == ("NSE_EQ|A",)


def test_reapplying_the_same_demand_does_not_duplicate_leases() -> None:
    mgr = _manager()
    mgr.apply_demand(_demand("NSE_EQ|A"))
    mgr.apply_demand(_demand("NSE_EQ|A"))
    assert mgr.refcount("NSE_EQ|A") == 1, (
        "the demand is a set, not a stream of acquisitions")


def test_a_contract_that_is_no_longer_demanded_is_unsubscribed() -> None:
    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed})
    mgr.apply_demand(_demand("NSE_EQ|A", "NSE_EQ|B"))
    asyncio.run(mgr.reconcile())
    assert feed._instrument_keys == ("NSE_EQ|A", "NSE_EQ|B")
    out = mgr.apply_demand(_demand("NSE_EQ|A"))
    assert out["released"] == 1
    asyncio.run(mgr.reconcile())
    assert feed._instrument_keys == ("NSE_EQ|A",)


def test_nothing_is_subscribed_without_demand() -> None:
    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed})
    asyncio.run(mgr.reconcile())
    assert feed._instrument_keys == ()


def test_demand_routes_to_a_provider_that_can_serve_it() -> None:
    """The demanded contract is served by the provider that can serve it.

    The demand names Upstox (the configured order), but Upstox cannot
    serve it right now. The manager must route the SAME contract to the
    provider that can, using the canonical identity - not drop it.
    """
    catalogue = _Catalog(_index_rows("NSE:INDEX:NIFTY"))
    upstox = _Feed("upstox", state="stopped")
    fyers = _Feed("fyers")
    mgr = _manager(feeds={"upstox": upstox, "fyers": fyers},
                   catalog=catalogue)
    mgr.apply_demand(_demand("NSE_INDEX|Nifty 50"))
    asyncio.run(mgr.reconcile())
    assert fyers._instrument_keys == ("NSE:NIFTY50-INDEX",), (
        "the contract must be served by the provider that can serve it")
    assert upstox._instrument_keys == ()


def test_an_unroutable_demand_is_reported_not_dropped() -> None:
    mgr = _manager()
    out = mgr.apply_demand(_demand("SOME:UNKNOWN", provider=None))
    assert out["leases"] == 1
    plan = mgr.plan()
    assert "unrouted" in plan
    assert plan["unrouted"].unreachable == ["SOME:UNKNOWN"]


def test_a_demand_record_without_an_instrument_is_reported() -> None:
    mgr = _manager()
    out = mgr.apply_demand([{"key": "", "provider": "upstox",
                             "owner_kind": "required", "owner_id": "required",
                             "priority": PRIORITY_REQUIRED, "kind": "index"}])
    assert out["leases"] == 0
    assert out["rejected"]
    assert out["rejected"][0]["reason"]


def test_demand_does_not_disturb_another_owner_s_lease() -> None:
    """An alert's dependency lease is not the demand's to release."""
    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed})
    mgr.acquire("NSE_EQ|ALERT", provider="upstox",
                priority=PRIORITY_REQUIRED, owner_kind="alert",
                owner_id="alert-1")
    mgr.apply_demand(_demand("NSE_EQ|A"))
    mgr.apply_demand(_demand("NSE_EQ|B"))   # A is no longer demanded
    assert mgr.refcount("NSE_EQ|ALERT") == 1
    assert mgr.refcount("NSE_EQ|A") == 0


def test_the_service_applies_the_demand_through_the_manager() -> None:
    """ONE owner applies the subscribed set.

    The service exposes the demand; the lease manager applies it (capacity,
    availability and failover aware). A second applier with its own view of
    the demand would unsubscribe what the other one subscribed.
    """
    import app.subscriptions.manager as mgr_mod

    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed})
    previous = mgr_mod.get_active_manager()
    mgr_mod._ACTIVE_MANAGER = mgr
    try:
        from app.subscriptions.service import SubscriptionService

        class _Svc(SubscriptionService):
            def __init__(self):
                self._last_apply: dict = {}
                self._leaser = mgr

            def resolve(self):
                return {"contracts": [{"key": "NSE_EQ|A", "provider": "upstox"}],
                        "by_provider": {"upstox": ["NSE_EQ|A"]},
                        "pending": [], "notes": []}

            def lease_requests(self):
                return _demand("NSE_EQ|A")

        svc = _Svc()
        out = asyncio.run(svc.reconcile(lambda name: feed))
        assert out["apply"]["results"]["upstox"]["applied"] is True
        assert out["apply"]["results"]["upstox"]["added"] == 1
        assert feed._instrument_keys == ("NSE_EQ|A",)
        # The manager is the single owner: it holds the lease.
        assert mgr.refcount("NSE_EQ|A") == 1
    finally:
        mgr_mod._ACTIVE_MANAGER = previous


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
