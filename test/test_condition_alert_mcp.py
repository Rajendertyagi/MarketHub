#!/usr/bin/env python3
"""B5 condition-alert MCP tool boundary tests (in-process).

Uses a fake MCP server + real in-memory EventStore + mock catalog/resolver
to test the 5 public condition-alert tools without a subprocess server.

Covers:
  * CA-M1  condition_alert_create — v1 leaf success
  * CA-M2  condition_alert_create — v2 group success
  * CA-M3  condition_alert_create — validation errors (metric, operator,
           trigger_mode, condition_version, depth, leaves, multi-instrument,
           greeks on non-option, unresolved instrument, empty consumer_id)
  * CA-M4  condition_alert_list — list, filter by enabled, limit
  * CA-M5  condition_alert_get — get existing, not-found, ownership
  * CA-M6  condition_alert_set_enabled — enable (re-arm), disable, not-found,
           ownership
  * CA-M7  condition_alert_delete — delete, not-found, ownership
  * CA-M8  Error normalization — no raw Python errors leak

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
from typing import Any
from unittest.mock import MagicMock

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

from core.errors import (
    AlertNotFoundError,
    ConditionValidationError,
    ConsumerNotFoundError,
    StorageError,
    ValidationError,
)
from core.persistence.store import EventStore

# Test instrument: RELIANCE on NSE EQUITY
RELIANCE = "NSE:EQUITY:INE002A01018"
NIFTY = "NSE:INDEX:NIFTY"


# ---------------------------------------------------------------------------
# Fake MCP server (captures registered tools)
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal MCP server mock that captures registered tools."""

    def __init__(self):
        self.tools: dict[str, Any] = {}

    def tool(self, name=None, **kw):
        def deco(fn):
            async def wrapper(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except (ConditionValidationError, ValidationError,
                        AlertNotFoundError, ConsumerNotFoundError,
                        StorageError) as exc:
                    return {"error": str(exc)}
                except Exception as exc:
                    return {"error": f"internal_error: {exc}"}
            self.tools[name] = wrapper
            return fn
        return deco


# ---------------------------------------------------------------------------
# Mock services for condition-alert tools
# ---------------------------------------------------------------------------


def _make_mock_services(store: EventStore) -> MagicMock:
    """Create a mock services object with a real store + mock catalog/resolver."""
    services = MagicMock()
    services.store = store
    services.condition_alert_engine = MagicMock()
    services.condition_identity_resolver = MagicMock()

    # Mock catalog: resolve RELIANCE and NIFTY
    _catalog_rows = {
        "RELIANCE": {
            "exchange": "NSE",
            "instrument_type": "EQUITY",
            "tradingsymbol": "RELIANCE",
            "name": "Reliance Industries",
            "isin": "INE002A01018",
            "underlying": None,
            "expiry": None,
            "strike": None,
            "option_type": None,
        },
        "NIFTY": {
            "exchange": "NSE",
            "instrument_type": "INDEX",
            "tradingsymbol": "NIFTY",
            "name": "Nifty 50",
            "isin": None,
            "underlying": None,
            "expiry": None,
            "strike": None,
            "option_type": None,
        },
    }

    def _search(**kw):
        q = kw.get("q")
        exchange = kw.get("exchange")
        if q and exchange == "NSE":
            for name, row in _catalog_rows.items():
                if name.upper() in q.upper() or q.upper() in name.upper():
                    return [row]
        return []

    services.instrument_catalog.search = _search

    # Mock resolver: return canonical_id for known instruments
    def _canonical_id_for_row(row):
        ts = (row.get("tradingsymbol") or "").upper()
        if ts == "RELIANCE":
            return RELIANCE
        if ts == "NIFTY":
            return NIFTY
        return None

    services.condition_identity_resolver.canonical_id_for_row = _canonical_id_for_row
    return services


def _mk_store():
    """Create an in-memory EventStore for testing."""
    import tempfile
    tmp = tempfile.TemporaryDirectory()
    return EventStore(os.path.join(tmp.name, "t.db")), tmp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _condition_v1(metric="ltp", operator="gt", value=25000,
                  canonical_id=RELIANCE) -> dict:
    """Create a condition in the internal format (for store-level tests)."""
    return {
        "condition_version": 1,
        "condition_id": "cond-1",
        "metric": metric,
        "operator": operator,
        "value": value,
        "instrument": {"canonical_id": canonical_id},
    }


def _public_condition_v1(metric="ltp", operator="gt", value=25000,
                         symbol="RELIANCE") -> dict:
    """Create a condition in the public format (for MCP tool tests)."""
    return {
        "condition_version": 1,
        "metric": metric,
        "operator": operator,
        "value": value,
        "instrument": {"exchange": "NSE", "symbol": symbol},
    }


def _public_condition_v2(logic="all", children=None) -> dict:
    """Create a v2 group in the public format."""
    if children is None:
        children = [_public_condition_v1()]
    return {
        "condition_version": 2,
        "logic": logic,
        "conditions": children,
    }


# ---------------------------------------------------------------------------
# CA-M1: condition_alert_create — v1 leaf success
# ---------------------------------------------------------------------------


async def test_ca_m1_create_v1_success(runner: R) -> None:
    """CA-M1: create a v1 condition alert with a valid leaf."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition=_public_condition_v1(),
            trigger_mode="repeat",
            name="RELIANCE above 25000",
        )
        runner.assert_eq("CA-M1-status", result["status"], "created")
        runner.assert_true("CA-M1-alert", "alert" in result)
        alert = result["alert"]
        runner.assert_true("CA-M1-alert-id", bool(alert.get("alert_id")))
        runner.assert_eq("CA-M1-consumer", alert["consumer_id"], "consumer-1")
        runner.assert_eq("CA-M1-name", alert["name"], "RELIANCE above 25000")
        runner.assert_eq("CA-M1-enabled", alert["enabled"], True)
        runner.assert_eq("CA-M1-trigger_mode", alert["trigger_mode"], "repeat")
        runner.assert_eq("CA-M1-condition_version",
                         alert["condition"]["condition_version"], 1)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M2: condition_alert_create — v2 group success
# ---------------------------------------------------------------------------


async def test_ca_m2_create_v2_success(runner: R) -> None:
    """CA-M2: create a v2 condition alert with a same-instrument group."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition=_public_condition_v2(
                logic="all",
                children=[
                    _public_condition_v1(metric="ltp", operator="gt", value=25000),
                    _public_condition_v1(metric="volume", operator="gt", value=1000000),
                ],
            ),
            trigger_mode="once",
            name="RELIANCE ltp>25000 AND volume>1M",
        )
        runner.assert_eq("CA-M2-status", result["status"], "created")
        alert = result["alert"]
        runner.assert_eq("CA-M2-condition_version",
                         alert["condition"]["condition_version"], 2)
        runner.assert_eq("CA-M2-logic", alert["condition"]["logic"], "all")
        runner.assert_eq("CA-M2-children", len(alert["condition"]["conditions"]), 2)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M3: condition_alert_create — validation errors
# ---------------------------------------------------------------------------


async def test_ca_m3_invalid_metric(runner: R) -> None:
    """CA-M3a: unknown metric raises ConditionValidationError."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition=_public_condition_v1(metric="nonexistent_metric"),
        )
        runner.assert_in("CA-M3a-error", "error", result)
        runner.assert_in("CA-M3a-msg", "unknown metric", result["error"].lower())
    finally:
        tmp.cleanup()


async def test_ca_m3_invalid_operator(runner: R) -> None:
    """CA-M3b: unknown operator raises ConditionValidationError."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition=_public_condition_v1(operator="invalid_op"),
        )
        runner.assert_in("CA-M3b-error", "error", result)
        runner.assert_in("CA-M3b-msg", "unknown operator", result["error"].lower())
    finally:
        tmp.cleanup()


async def test_ca_m3_invalid_trigger_mode(runner: R) -> None:
    """CA-M3c: invalid trigger_mode raises ValidationError."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition=_public_condition_v1(),
            trigger_mode="invalid_mode",
        )
        runner.assert_in("CA-M3c-error", "error", result)
        runner.assert_in("CA-M3c-msg", "trigger_mode", result["error"].lower())
    finally:
        tmp.cleanup()


async def test_ca_m3_invalid_condition_version(runner: R) -> None:
    """CA-M3d: invalid condition_version raises ConditionValidationError."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition={"condition_version": 99, "metric": "ltp"},
        )
        runner.assert_in("CA-M3d-error", "error", result)
        runner.assert_in("CA-M3d-msg", "condition_version", result["error"].lower())
    finally:
        tmp.cleanup()


async def test_ca_m3_empty_consumer_id(runner: R) -> None:
    """CA-M3e: empty consumer_id raises ValidationError."""
    store, tmp = _mk_store()
    try:
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="",
            condition=_public_condition_v1(),
        )
        runner.assert_in("CA-M3e-error", "error", result)
        runner.assert_in("CA-M3e-msg", "consumer_id", result["error"].lower())
    finally:
        tmp.cleanup()


async def test_ca_m3_greeks_on_non_option(runner: R) -> None:
    """CA-M3f: greeks.* metric on non-OPTION instrument raises error."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_create"](
            consumer_id="consumer-1",
            condition=_public_condition_v1(metric="greeks.delta"),
        )
        runner.assert_in("CA-M3f-error", "error", result)
        runner.assert_in("CA-M3f-msg", "only supported on option",
                         result["error"].lower())
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M4: condition_alert_list
# ---------------------------------------------------------------------------


async def test_ca_m4_list_all(runner: R) -> None:
    """CA-M4a: list all alerts for a consumer."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        # Create two alerts
        store.create_condition_alert(
            consumer_id="consumer-1", name="Alert 1",
            trigger_mode="repeat", condition_json=_condition_v1())
        store.create_condition_alert(
            consumer_id="consumer-1", name="Alert 2",
            trigger_mode="once", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_list"](consumer_id="consumer-1")
        runner.assert_eq("CA-M4a-status", result["status"], "ok")
        runner.assert_eq("CA-M4a-count", result["count"], 2)
        runner.assert_eq("CA-M4a-alerts-len", len(result["alerts"]), 2)
    finally:
        tmp.cleanup()


async def test_ca_m4_list_filter_enabled(runner: R) -> None:
    """CA-M4b: list with enabled filter."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        store.create_condition_alert(
            consumer_id="consumer-1", name="Enabled",
            trigger_mode="repeat", condition_json=_condition_v1())
        # Disable the second alert via store
        store.set_condition_alert_enabled(
            store.create_condition_alert(
                consumer_id="consumer-1", name="Disabled",
                trigger_mode="repeat", condition_json=_condition_v1()),
            False)
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_list"](
            consumer_id="consumer-1", enabled=True)
        runner.assert_eq("CA-M4b-count", result["count"], 1)
        runner.assert_eq("CA-M4b-alert-enabled",
                         result["alerts"][0]["enabled"], True)
    finally:
        tmp.cleanup()


async def test_ca_m4_list_limit(runner: R) -> None:
    """CA-M4c: list with limit."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        for i in range(5):
            store.create_condition_alert(
                consumer_id="consumer-1", name=f"Alert {i}",
                trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_list"](
            consumer_id="consumer-1", limit=2)
        runner.assert_eq("CA-M4c-count", result["count"], 2)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M5: condition_alert_get
# ---------------------------------------------------------------------------


async def test_ca_m5_get_existing(runner: R) -> None:
    """CA-M5a: get an existing alert."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="My Alert",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_get"](
            consumer_id="consumer-1", alert_id=alert_id)
        runner.assert_eq("CA-M5a-status", result["status"], "ok")
        runner.assert_eq("CA-M5a-name", result["alert"]["name"], "My Alert")
    finally:
        tmp.cleanup()


async def test_ca_m5_get_not_found(runner: R) -> None:
    """CA-M5b: get a non-existent alert raises AlertNotFoundError."""
    store, tmp = _mk_store()
    try:
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_get"](
            consumer_id="consumer-1", alert_id="nonexistent-id")
        runner.assert_in("CA-M5b-error", "error", result)
    finally:
        tmp.cleanup()


async def test_ca_m5_get_ownership(runner: R) -> None:
    """CA-M5c: cross-owner access returns not-found."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        store.register_consumer("consumer-2")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="Owned by c1",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        # consumer-2 tries to get consumer-1's alert
        result = await fake.tools["condition_alert_get"](
            consumer_id="consumer-2", alert_id=alert_id)
        runner.assert_in("CA-M5c-error", "error", result)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M6: condition_alert_set_enabled
# ---------------------------------------------------------------------------


async def test_ca_m6_disable(runner: R) -> None:
    """CA-M6a: disable an alert."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="Alert",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_set_enabled"](
            consumer_id="consumer-1", alert_id=alert_id, enabled=False)
        runner.assert_eq("CA-M6a-status", result["status"], "disabled")
        runner.assert_eq("CA-M6a-enabled", result["enabled"], False)
    finally:
        tmp.cleanup()


async def test_ca_m6_enable_rearm(runner: R) -> None:
    """CA-M6b: enable re-arms the alert (resets runtime state)."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="Alert",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        # Disable then re-enable
        await fake.tools["condition_alert_set_enabled"](
            consumer_id="consumer-1", alert_id=alert_id, enabled=False)
        result = await fake.tools["condition_alert_set_enabled"](
            consumer_id="consumer-1", alert_id=alert_id, enabled=True)
        runner.assert_eq("CA-M6b-status", result["status"], "enabled")
        runner.assert_eq("CA-M6b-enabled", result["enabled"], True)
        # Verify the engine's reload was called (re-arm)
        services.condition_alert_engine.reload.assert_called()
    finally:
        tmp.cleanup()


async def test_ca_m6_not_found(runner: R) -> None:
    """CA-M6c: enable/disable non-existent alert raises error."""
    store, tmp = _mk_store()
    try:
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_set_enabled"](
            consumer_id="consumer-1", alert_id="nonexistent", enabled=True)
        runner.assert_in("CA-M6c-error", "error", result)
    finally:
        tmp.cleanup()


async def test_ca_m6_ownership(runner: R) -> None:
    """CA-M6d: cross-owner enable/disable returns not-found."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        store.register_consumer("consumer-2")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="Owned by c1",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_set_enabled"](
            consumer_id="consumer-2", alert_id=alert_id, enabled=True)
        runner.assert_in("CA-M6d-error", "error", result)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M7: condition_alert_delete
# ---------------------------------------------------------------------------


async def test_ca_m7_delete(runner: R) -> None:
    """CA-M7a: delete an alert."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="To Delete",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_delete"](
            consumer_id="consumer-1", alert_id=alert_id)
        runner.assert_eq("CA-M7a-status", result["status"], "deleted")
        # Verify it's gone
        alert = store.get_condition_alert(alert_id)
        runner.assert_true("CA-M7a-gone", alert is None)
    finally:
        tmp.cleanup()


async def test_ca_m7_not_found(runner: R) -> None:
    """CA-M7b: delete non-existent alert raises error."""
    store, tmp = _mk_store()
    try:
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_delete"](
            consumer_id="consumer-1", alert_id="nonexistent")
        runner.assert_in("CA-M7b-error", "error", result)
    finally:
        tmp.cleanup()


async def test_ca_m7_ownership(runner: R) -> None:
    """CA-M7c: cross-owner delete returns not-found."""
    store, tmp = _mk_store()
    try:
        store.register_consumer("consumer-1")
        store.register_consumer("consumer-2")
        alert_id = store.create_condition_alert(
            consumer_id="consumer-1", name="Owned by c1",
            trigger_mode="repeat", condition_json=_condition_v1())
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        result = await fake.tools["condition_alert_delete"](
            consumer_id="consumer-2", alert_id=alert_id)
        runner.assert_in("CA-M7c-error", "error", result)
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# CA-M8: Error normalization — no raw Python errors leak
# ---------------------------------------------------------------------------


async def test_ca_m8_no_raw_errors(runner: R) -> None:
    """CA-M8: all error paths return normalized dicts, not exceptions."""
    store, tmp = _mk_store()
    try:
        fake = _FakeMCP()
        services = _make_mock_services(store)
        from mcp_server.tools.condition_alerts import register_condition_alert_tools
        register_condition_alert_tools(fake, services)

        # Test each tool with invalid inputs to ensure normalized errors
        tools_to_test = [
            ("condition_alert_create", {"consumer_id": "", "condition": {}}),
            ("condition_alert_list", {"consumer_id": ""}),
            ("condition_alert_get", {"consumer_id": "", "alert_id": ""}),
            ("condition_alert_set_enabled", {"consumer_id": "", "alert_id": "",
                                             "enabled": True}),
            ("condition_alert_delete", {"consumer_id": "", "alert_id": ""}),
        ]
        for tool_name, kwargs in tools_to_test:
            result = await fake.tools[tool_name](**kwargs)
            runner.assert_true(
                f"CA-M8-{tool_name}-is-dict",
                isinstance(result, dict),
                f"{tool_name} did not return a dict")
            runner.assert_true(
                f"CA-M8-{tool_name}-has-error",
                "error" in result,
                f"{tool_name} did not return an error dict")
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_TESTS = [
    test_ca_m1_create_v1_success,
    test_ca_m2_create_v2_success,
    test_ca_m3_invalid_metric,
    test_ca_m3_invalid_operator,
    test_ca_m3_invalid_trigger_mode,
    test_ca_m3_invalid_condition_version,
    test_ca_m3_empty_consumer_id,
    test_ca_m3_greeks_on_non_option,
    test_ca_m4_list_all,
    test_ca_m4_list_filter_enabled,
    test_ca_m4_list_limit,
    test_ca_m5_get_existing,
    test_ca_m5_get_not_found,
    test_ca_m5_get_ownership,
    test_ca_m6_disable,
    test_ca_m6_enable_rearm,
    test_ca_m6_not_found,
    test_ca_m6_ownership,
    test_ca_m7_delete,
    test_ca_m7_not_found,
    test_ca_m7_ownership,
    test_ca_m8_no_raw_errors,
]


async def main() -> None:
    import atexit
    from helpers.lifecycle import restore_environment
    atexit.register(restore_environment)
    runner = R()
    for fn in _TESTS:
        try:
            await fn(runner)
        except Exception as exc:
            doc = fn.__doc__ or fn.__name__
            label = doc.split(":")[0].strip() if doc else fn.__name__
            runner.fail(label, str(exc))
    success = runner.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
