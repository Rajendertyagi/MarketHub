#!/usr/bin/env python3
"""The provider-state reconciliation contract (the live blocker).

Proves:

  * a runtime-subscribed key is bound to its IDENTITY (exchange,
    trading symbol) before the subscribe mutation is sent - without the
    binding every tick for that key is dropped as an unknown instrument;
  * a provider that is NOT REACHABLE is reported as ``provider_unavailable``,
    never as capacity exhaustion;
  * a provider that is at its VERIFIED CEILING is reported as
    ``capacity_limited``;
  * a provider that becomes available re-plans the EXISTING demand - no
    second applier, no manual trigger;
  * a failed apply is reported as a failure (never as a successful
    reconciliation), and one failing provider does not abort the other;
  * a contract that was failed over goes BACK to the provider the demand
    asked for when that provider recovers.

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

    def __init__(self, name="upstox", state="streaming", fail=False):
        self.name = name
        self.state = state
        self.fail = fail
        self._instrument_keys: tuple[str, ...] = ()
        self.added: list[str] = []
        self.removed: list[str] = []
        self.metadata: dict[str, tuple[str, str]] = {}
        self.on_state_change = None

    def status(self):
        return {"state": self.state, "last_data_at": {}}

    async def add_instruments(self, keys, metadata=None):
        if self.fail:
            raise RuntimeError("socket closed")
        cur = set(self._instrument_keys)
        fresh = [k for k in keys if k not in cur]
        self._instrument_keys = tuple(sorted(cur | set(fresh)))
        self.added.extend(keys)
        if metadata:
            self.metadata.update({k: v for k, v in metadata.items()
                                  if k in fresh})
        return len(fresh)

    async def remove_instruments(self, keys):
        cur = set(self._instrument_keys) - set(keys)
        self._instrument_keys = tuple(sorted(cur))
        self.removed.extend(keys)
        return len(keys)

    def emit_state(self, old: str, new: str, reason: str | None = None):
        """The feed's own state transition (the event the manager listens to)."""
        self.state = new
        listener = self.on_state_change
        if listener is not None:
            listener(self.name, self.name, old, new, reason)


class _Catalog:
    """A catalogue double with one Upstox future and one index."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def get(self, provider, token):
        for row in self._rows:
            if row.get("provider") == provider and token in (
                    row.get("instrument_token"), row.get("provider_symbol")):
                return row
        return None

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


class _Entitlement:
    def __init__(self, symbols):
        self.symbols = symbols
        self.tier = "test"
        self.source = "test"
        self.is_verified = True


class _Registry:
    """A provider registry double with a fixed verified ceiling."""

    def __init__(self, budgets=None, ids=("upstox", "fyers")):
        self._budgets = dict(budgets or {"upstox": 500, "fyers": 500})
        self._ids = list(ids)

    def ids(self):
        return list(self._ids)

    def entitlement(self, provider):
        symbols = self._budgets.get(provider)
        if symbols is None:
            return None
        return _Entitlement(symbols)

    def preference_order(self):
        return ["upstox", "fyers"]

    def status(self, provider):
        return "streaming"


def _future_row():
    return {
        "provider": "upstox",
        "instrument_token": "NSE_FO|42631",
        "provider_symbol": "NSE_FO|42631",
        "tradingsymbol": "SBIN26OCT26000-FUT",
        "exchange": "NSE",
        "instrument_type": "FUTURE",
        "underlying": "SBIN",
        "expiry": "2026-10-29",
    }


def _index_rows():
    return [
        {"provider": "upstox", "instrument_token": "NSE_INDEX|Nifty 50",
         "provider_symbol": "NSE_INDEX|Nifty 50", "tradingsymbol": "NIFTY",
         "exchange": "NSE", "instrument_type": "INDEX", "underlying": None},
        {"provider": "fyers", "instrument_token": "101NIFTY",
         "provider_symbol": "NSE:NIFTY50-INDEX",
         "tradingsymbol": "NSE:NIFTY50-INDEX", "exchange": "NSE",
         "instrument_type": "INDEX", "underlying": "NIFTY"},
    ]


def _manager(*, feeds=None, catalog=None, registry=None):
    if registry is None:
        registry = _Registry()
    feeds = feeds or {}
    return SubscriptionManager(
        catalog=catalog,
        registry=registry,
        feed_provider=lambda name: feeds.get(name),
        status_provider=lambda name: (feeds.get(name).state
                                      if feeds.get(name) else None),
    )


def _demand(*keys, provider="upstox", canonical_id=None):
    return [{"key": k, "provider": provider, "canonical_id": canonical_id,
             "owner_kind": "required", "owner_id": "required",
             "priority": PRIORITY_REQUIRED, "kind": "future"}
            for k in keys]


def test_runtime_subscribed_keys_carry_their_identity() -> None:
    """The subscribe mutation is bound to (exchange, trading symbol).

    Without the binding the feed cannot map the key to an instrument and
    drops EVERY tick for it as an unknown instrument.
    """
    feed = _Feed()
    mgr = _manager(feeds={"upstox": feed}, catalog=_Catalog([_future_row()]))
    mgr.apply_demand(_demand("NSE_FO|42631"))
    asyncio.run(mgr.reconcile())
    assert feed._instrument_keys == ("NSE_FO|42631",)
    assert feed.metadata.get("NSE_FO|42631") == ("NSE", "SBIN26OCT26000-FUT")


def test_an_unreachable_provider_is_not_capacity_limited() -> None:
    """A broken provider must not be reported as a FULL one."""
    feed = _Feed("upstox", state="stopped")
    mgr = _manager(feeds={"upstox": feed})
    mgr.apply_demand(_demand("NSE_FO|42631"))
    plan = mgr.plan()
    assert plan["upstox"].capacity_limited == [], (
        "an unreachable provider is not a capacity problem")
    assert len(plan["upstox"].provider_unavailable) == 1
    assert "unavailable" in plan["upstox"].provider_unavailable[0]["reason"]


def test_a_full_provider_is_reported_as_capacity_limited() -> None:
    """Only the verified ceiling produces ``capacity_limited``."""
    feed = _Feed()
    registry = _Registry(budgets={"upstox": 1, "fyers": 500})
    mgr = _manager(feeds={"upstox": feed}, registry=registry)
    mgr.apply_demand(_demand("NSE_FO|42631", "NSE_FO|42632"))
    plan = mgr.plan()
    assert len(plan["upstox"].capacity_limited) == 1
    assert plan["upstox"].provider_unavailable == []
    assert "ceiling" in plan["upstox"].capacity_limited[0]["reason"]


def test_a_provider_becoming_available_re_plans_the_demand() -> None:
    """Availability is an EVENT: the demand is applied without a nudge."""
    feed = _Feed("upstox", state="stopped")
    mgr = _manager(feeds={"upstox": feed})

    async def _run():
        mgr.apply_demand(_demand("NSE_FO|42631"))
        await mgr.reconcile()
        assert feed._instrument_keys == (), "the provider cannot serve it yet"
        # The provider comes up: the feed's own state transition must
        # re-plan and re-apply the EXISTING demand.
        feed.emit_state("stopped", "streaming", "session restored")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if feed._instrument_keys:
                break
        assert feed._instrument_keys == ("NSE_FO|42631",), (
            "the demand must reach the provider as soon as it is available")

    asyncio.run(_run())


def test_a_failed_apply_is_not_reported_as_applied() -> None:
    """A failed apply is a failure - never a successful reconciliation."""
    feed = _Feed(fail=True)
    mgr = _manager(feeds={"upstox": feed})
    mgr.apply_demand(_demand("NSE_FO|42631"))
    out = asyncio.run(mgr.reconcile())
    result = out["results"]["upstox"]
    assert result["applied"] is False
    assert result["reason"] == "RuntimeError"


def test_one_failing_provider_does_not_abort_the_other() -> None:
    """A broken provider must not stop the healthy one from being applied."""
    broken = _Feed("upstox", fail=True)
    healthy = _Feed("fyers")
    mgr = _manager(feeds={"upstox": broken, "fyers": healthy})
    # ONE demand with both contracts (the demand is a replacement, not an
    # accumulation).
    mgr.apply_demand(
        _demand("NSE_FO|42631", provider="upstox")
        + _demand("NSE:FUT:SBIN:2026-10-29", provider="fyers"))
    out = asyncio.run(mgr.reconcile())
    assert out["results"]["upstox"]["applied"] is False
    assert out["results"]["fyers"]["applied"] is True


def test_a_failover_returns_to_the_demand_provider_when_it_recovers() -> None:
    """The allocation follows the demand, not the failover, once it recovers."""
    catalogue = _Catalog(_index_rows())
    upstox = _Feed("upstox", state="stopped")
    fyers = _Feed("fyers")
    mgr = _manager(feeds={"upstox": upstox, "fyers": fyers},
                   catalog=catalogue)
    mgr.apply_demand(_demand("NSE_INDEX|Nifty 50", provider="upstox",
                              canonical_id="NSE:INDEX:NIFTY"))

    async def _run():
        await mgr.reconcile()
        assert fyers._instrument_keys == ("NSE:NIFTY50-INDEX",), (
            "the contract is served by the provider that can serve it")
        # Upstox recovers: the contract goes back to the demand's provider.
        upstox.emit_state("stopped", "streaming", "session restored")
        for _ in range(50):
            await asyncio.sleep(0.01)
            if upstox._instrument_keys:
                break
        assert upstox._instrument_keys == ("NSE_INDEX|Nifty 50",), (
            "the recovered demand's provider must serve the contract again")

    asyncio.run(_run())


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
