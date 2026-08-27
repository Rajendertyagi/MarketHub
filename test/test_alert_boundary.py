#!/usr/bin/env python3
"""MCP-2B.3A alert tool boundary tests.

Direct MCP/tool-boundary coverage for all 5 generic alert tools
(alert_create / alert_list / alert_get / alert_enable / alert_disable)
and all 5 market-alert tools
(market_alert_create / market_alert_list / market_alert_enable /
 market_alert_disable / market_alert_delete), plus:

  * generic/market table isolation regression
  * EventStore facade signature regression (guards the shadowing fix)

NO LIVE BROKER. Synthetic store + deterministic MarketIntel fixture only.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
from typing import Any

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R  # noqa: E402

from core.errors import (  # noqa: E402
    AlertNotFoundError,
    ConsumerNotFoundError,
    ValidationError,
)
from core.persistence.store import EventStore  # noqa: E402


# ── Shared fixtures ───────────────────────────────────────────────────────────

class _FakeMCP:
    """Minimal MCP server mock that captures registered tools."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, name=None, **kw):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


class _Services:
    """Minimal services namespace backed by a real EventStore."""

    def __init__(self, store: EventStore) -> None:
        self.store = store
        self.market_intel = None
        self.alert_engine = None
        self.replay_cfg = {"max_limit": 100}


class _FakeIntel:
    """Deterministic MarketIntel fixture for market-alert resolution."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results

    def search(self, q, limit=5):
        return {"count": len(self._results), "results": self._results}


def _mk_store():
    """Return (tmp, store). Caller must keep tmp alive for the store's lifetime."""
    tmp = tempfile.TemporaryDirectory()
    return tmp, EventStore(os.path.join(tmp.name, "t.db"))


def _mk_generic_env():
    """Real store + registered generic alert tools."""
    tmp, store = _mk_store()
    fake = _FakeMCP()
    services = _Services(store)
    from mcp_server.tools.alerts import register_alert_tools
    register_alert_tools(fake, services)
    return tmp, store, fake, services


def _mk_market_env():
    """Real store + registered market-alert tools + deterministic intel."""
    tmp, store = _mk_store()
    fake = _FakeMCP()
    services = _Services(store)
    services.market_intel = _FakeIntel([{
        "instrument_key": "NSE:RELIANCE",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "instrument_token": "RELIANCE",
    }])
    from mcp_server.tools.market_alerts import register_market_alert_tools
    register_market_alert_tools(fake, services)
    return tmp, store, fake, services


async def _create_generic(fake, consumer_id: str = "c1", **overrides) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        consumer_id=consumer_id,
        source="market",
        field_path="ltp",
        operator="gt",
        value=100.0,
        name="test alert",
    )
    kwargs.update(overrides)
    return await fake.tools["alert_create"](**kwargs)


async def _create_market(fake, **overrides) -> dict[str, Any]:
    kwargs: dict[str, Any] = dict(
        instrument_query="RELIANCE", operator="lt", threshold=1400.0)
    kwargs.update(overrides)
    return await fake.tools["market_alert_create"](**kwargs)


def _raises(runner: R, name: str, exc_type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        runner.ok(name)
        return True
    except Exception as exc:  # noqa: BLE001
        runner.fail(name, f"expected {exc_type.__name__}, got "
                          f"{type(exc).__name__}: {exc}")
        return False
    runner.fail(name, f"expected {exc_type.__name__}, no exception raised")
    return False


async def _raises_async(runner: R, name: str, exc_type, fn, *args, **kwargs) -> bool:
    try:
        await fn(*args, **kwargs)
    except exc_type:
        runner.ok(name)
        return True
    except Exception as exc:  # noqa: BLE001
        runner.fail(name, f"expected {exc_type.__name__}, got "
                          f"{type(exc).__name__}: {exc}")
        return False
    runner.fail(name, f"expected {exc_type.__name__}, no exception raised")
    return False


# ── Generic alert tools (alert_create/list/get/enable/disable) ───────────────

async def test_generic_create_and_list(runner: R) -> None:
    """GA-A/B: register consumer, create generic alert, list returns it."""
    _tmp, store, fake, _services = _mk_generic_env()
    store.register_consumer("c1")

    result = await _create_generic(fake)
    runner.assert_eq("GA-create-status", result["status"], "created")
    alert = result["alert"]
    runner.assert_true("GA-create-has-id", bool(alert.get("alert_id")))
    runner.assert_eq("GA-create-consumer", alert["consumer_id"], "c1")
    runner.assert_eq("GA-create-operator", alert["operator"], "gt")

    listed = fake.tools["alert_list"](consumer_id="c1")
    runner.assert_eq("GA-list-returned", listed["returned"], 1)
    runner.assert_eq("GA-list-id", listed["alerts"][0]["alert_id"],
                     alert["alert_id"])


async def test_generic_get(runner: R) -> None:
    """GA-C: alert_get returns the created alert."""
    _tmp, store, fake, _services = _mk_generic_env()
    store.register_consumer("c1")
    alert = (await _create_generic(fake))["alert"]

    got = fake.tools["alert_get"](consumer_id="c1", alert_id=alert["alert_id"])
    runner.assert_eq("GA-get-id", got["alert"]["alert_id"], alert["alert_id"])
    runner.assert_eq("GA-get-source", got["alert"]["source"], "market")


async def test_generic_disable_enable(runner: R) -> None:
    """GA-D/E/F: disable changes state, second disable is a no-op, enable restores."""
    _tmp, store, fake, _services = _mk_generic_env()
    store.register_consumer("c1")
    alert = (await _create_generic(fake))["alert"]
    alert_id = alert["alert_id"]

    d1 = fake.tools["alert_disable"](consumer_id="c1", alert_id=alert_id)
    runner.assert_eq("GA-disable-changed", d1["changed"], True)
    d2 = fake.tools["alert_disable"](consumer_id="c1", alert_id=alert_id)
    runner.assert_eq("GA-disable-idempotent", d2["changed"], False)

    disabled = fake.tools["alert_list"](consumer_id="c1", enabled=False)
    enabled = fake.tools["alert_list"](consumer_id="c1", enabled=True)
    runner.assert_eq("GA-list-disabled-one", disabled["returned"], 1)
    runner.assert_eq("GA-list-enabled-empty", enabled["returned"], 0)

    e1 = fake.tools["alert_enable"](consumer_id="c1", alert_id=alert_id)
    runner.assert_eq("GA-enable-changed", e1["changed"], True)
    enabled = fake.tools["alert_list"](consumer_id="c1", enabled=True)
    runner.assert_eq("GA-list-enabled-one", enabled["returned"], 1)


async def test_generic_ownership(runner: R) -> None:
    """GA-G: wrong consumer cannot access another consumer's alert."""
    _tmp, store, fake, _services = _mk_generic_env()
    store.register_consumer("c1")
    store.register_consumer("c2")
    alert = (await _create_generic(fake, consumer_id="c1"))["alert"]
    alert_id = alert["alert_id"]

    _raises(runner, "GA-ownership-get", AlertNotFoundError,
            fake.tools["alert_get"], consumer_id="c2", alert_id=alert_id)
    _raises(runner, "GA-ownership-disable", AlertNotFoundError,
            fake.tools["alert_disable"], consumer_id="c2", alert_id=alert_id)
    _raises(runner, "GA-ownership-enable", AlertNotFoundError,
            fake.tools["alert_enable"], consumer_id="c2", alert_id=alert_id)

    listed = fake.tools["alert_list"](consumer_id="c2")
    runner.assert_eq("GA-ownership-list-empty", listed["returned"], 0)


async def test_generic_invalid_consumer(runner: R) -> None:
    """GA-H: unregistered consumer handled through shared domain error."""
    _tmp, store, fake, _services = _mk_generic_env()
    # c1 is NOT registered.
    await _raises_async(runner, "GA-invalid-consumer", ConsumerNotFoundError,
                        _create_generic, fake, consumer_id="ghost")


async def test_generic_invalid_operator_value(runner: R) -> None:
    """GA-I: invalid operator/value validation still works."""
    _tmp, store, fake, _services = _mk_generic_env()
    store.register_consumer("c1")

    await _raises_async(runner, "GA-invalid-operator", ValidationError,
                        _create_generic, fake, operator="bogus")
    await _raises_async(runner, "GA-invalid-value", ValidationError,
                        _create_generic, fake, value="high")
    await _raises_async(runner, "GA-invalid-field-path", ValidationError,
                        _create_generic, fake, field_path="")


# ── Market alert tools (market_alert_create/list/enable/disable/delete) ──────

async def test_market_create_list(runner: R) -> None:
    """MA-A/B: create a market alert, list returns it."""
    _tmp, _store, fake, _services = _mk_market_env()

    result = await _create_market(fake)
    runner.assert_eq("MA-create-status", result["status"], "created")
    alert = result["alert"]
    runner.assert_true("MA-create-id-int", isinstance(alert["id"], int))
    runner.assert_eq("MA-create-symbol", alert["tradingsymbol"], "RELIANCE")
    runner.assert_eq("MA-create-operator", alert["operator"], "lt")

    listed = await fake.tools["market_alert_list"]()
    runner.assert_eq("MA-list-count", listed["count"], 1)
    runner.assert_eq("MA-list-id", listed["alerts"][0]["id"], alert["id"])


async def test_market_disable_enable(runner: R) -> None:
    """MA-C/D: disable works, enable restores."""
    _tmp, _store, fake, _services = _mk_market_env()
    alert = (await _create_market(fake))["alert"]
    alert_id = alert["id"]

    d = await fake.tools["market_alert_disable"](alert_id=alert_id)
    runner.assert_eq("MA-disable-status", d["status"], "disabled")
    listed = await fake.tools["market_alert_list"]()
    runner.assert_eq("MA-disable-state", listed["alerts"][0]["enabled"], False)

    e = await fake.tools["market_alert_enable"](alert_id=alert_id)
    runner.assert_eq("MA-enable-status", e["status"], "enabled")
    listed = await fake.tools["market_alert_list"]()
    runner.assert_eq("MA-enable-state", listed["alerts"][0]["enabled"], True)


async def test_market_delete(runner: R) -> None:
    """MA-E/F: delete works; deleted alert no longer appears/addressable."""
    _tmp, _store, fake, _services = _mk_market_env()
    alert = (await _create_market(fake))["alert"]
    alert_id = alert["id"]

    d = await fake.tools["market_alert_delete"](alert_id=alert_id)
    runner.assert_eq("MA-delete-status", d["status"], "deleted")

    listed = await fake.tools["market_alert_list"]()
    runner.assert_eq("MA-delete-gone", listed["count"], 0)

    await _raises_async(runner, "MA-delete-not-addressable", AlertNotFoundError,
                        fake.tools["market_alert_enable"], alert_id=alert_id)


async def test_market_invalid_alert_id(runner: R) -> None:
    """MA-G: invalid alert_id raises the shared AlertNotFoundError."""
    _tmp, _store, fake, _services = _mk_market_env()

    await _raises_async(runner, "MA-invalid-enable", AlertNotFoundError,
                        fake.tools["market_alert_enable"], alert_id=999999)
    await _raises_async(runner, "MA-invalid-disable", AlertNotFoundError,
                        fake.tools["market_alert_disable"], alert_id=999999)
    await _raises_async(runner, "MA-invalid-delete", AlertNotFoundError,
                        fake.tools["market_alert_delete"], alert_id=999999)


# ── Table isolation regression ───────────────────────────────────────────────

def test_table_isolation(runner: R) -> None:
    """Generic and market alerts live in different tables/facade methods."""
    _tmp, store = _mk_store()
    store.register_consumer("c1")
    store.create_alert(
        alert_id="g1", consumer_id="c1", name=None, source="market",
        event_type=None, field_path="ltp", operator="gt", value=100.0,
        one_shot=True)
    store.create_market_alert(
        exchange="NSE", instrument_token="T1", tradingsymbol="AAA",
        field="ltp", operator="gt", threshold=100.0)

    generic = store.list_alerts("c1", None)
    market = store.list_market_alerts()

    runner.assert_eq("ISO-generic-count", len(generic), 1)
    runner.assert_eq("ISO-market-count", len(market), 1)
    runner.assert_eq("ISO-generic-id", generic[0]["alert_id"], "g1")
    runner.assert_true("ISO-market-id-int", isinstance(market[0]["id"], int))
    # Generic rows carry alert_id/consumer_id; market rows carry id.
    runner.assert_true("ISO-no-cross-contamination",
                       all("alert_id" in a for a in generic)
                       and all("id" in a for a in market))


# ── Facade signature regression ──────────────────────────────────────────────

def test_facade_signatures(runner: R) -> None:
    """EventStore facade signatures must not collide (shadowing guard)."""
    sig_create = inspect.signature(EventStore.create_alert)
    params_create = list(sig_create.parameters)
    runner.assert_eq(
        "FAC-create-params", params_create,
        ["self", "alert_id", "consumer_id", "name", "source", "event_type",
         "field_path", "operator", "value", "one_shot"])

    sig_list = inspect.signature(EventStore.list_alerts)
    runner.assert_eq("FAC-list-params", list(sig_list.parameters),
                     ["self", "consumer_id", "enabled"])

    sig_mcreate = inspect.signature(EventStore.create_market_alert)
    runner.assert_eq("FAC-mcreate-params", list(sig_mcreate.parameters),
                     ["self", "kw"])

    sig_mlist = inspect.signature(EventStore.list_market_alerts)
    runner.assert_eq("FAC-mlist-params", list(sig_mlist.parameters), ["self"])


def test_facade_functional(runner: R) -> None:
    """All four facade methods behave correctly end-to-end."""
    _tmp, store = _mk_store()
    store.register_consumer("c1")

    generic = store.create_alert(
        alert_id="a1", consumer_id="c1", name=None, source="market",
        event_type=None, field_path="ltp", operator="gt", value=100.0,
        one_shot=True)
    runner.assert_eq("FAC-generic-create", generic["alert_id"], "a1")
    listed = store.list_alerts("c1", None)
    runner.assert_eq("FAC-generic-list", len(listed), 1)
    runner.assert_eq("FAC-generic-list-id", listed[0]["alert_id"], "a1")

    market = store.create_market_alert(
        exchange="NSE", instrument_token="T1", tradingsymbol="AAA",
        field="ltp", operator="gt", threshold=100.0)
    runner.assert_true("FAC-market-create", market["id"] > 0)
    mlist = store.list_market_alerts()
    runner.assert_eq("FAC-market-list", len(mlist), 1)
    runner.assert_eq("FAC-market-list-id", mlist[0]["id"], market["id"])


# ── main ─────────────────────────────────────────────────────────────────────

async def main() -> bool:
    runner = R()

    await test_generic_create_and_list(runner)
    await test_generic_get(runner)
    await test_generic_disable_enable(runner)
    await test_generic_ownership(runner)
    await test_generic_invalid_consumer(runner)
    await test_generic_invalid_operator_value(runner)

    await test_market_create_list(runner)
    await test_market_disable_enable(runner)
    await test_market_delete(runner)
    await test_market_invalid_alert_id(runner)

    test_table_isolation(runner)
    test_facade_signatures(runner)
    test_facade_functional(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)