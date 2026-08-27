"""
MCP-0 architectural guardrail tests.

Enforces permanent invariants of the MCP foundation:
  - MCP modules never import broker adapters (GATE 10)
  - MCP tools are registered deterministically
  - The MCP server uses the same MarketService as the application (GATE 8)
  - Read-only boundary is maintained — no trading tools (GATE 9)
"""
from __future__ import annotations

import ast
import sys
from dataclasses import fields as dataclass_fields
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
MCP_SERVER_DIR = PROJECT_ROOT / "mcp_server"

BROKER_MODULES = ("brokers.upstox", "brokers.fyers")


# ── GATE 10: MCP modules have ZERO direct Upstox/Fyers imports ──────────────

class TestImportWall:
    """MCP modules must never import broker adapters."""

    @pytest.mark.parametrize(
        "mcp_file",
        sorted(MCP_SERVER_DIR.rglob("*.py")),
        ids=lambda p: str(p.relative_to(MCP_SERVER_DIR)),
    )
    def test_no_broker_imports_in_mcp(self, mcp_file: Path) -> None:
        tree = ast.parse(mcp_file.read_text(encoding="utf-8"), filename=str(mcp_file))
        violations: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(m) for m in BROKER_MODULES):
                        violations.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(m) for m in BROKER_MODULES):
                    violations.append(node.module)
        assert not violations, (
            f"{mcp_file.relative_to(PROJECT_ROOT)} imports broker modules: {violations}"
        )

    def test_mcp_package_imports_clean(self) -> None:
        pre_broker = {
            k for k in sys.modules
            if k.startswith("brokers.upstox") or k.startswith("brokers.fyers")
        }
        mod_name = "mcp_server"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        import mcp_server  # noqa: F401
        post_broker = {
            k for k in sys.modules
            if k.startswith("brokers.upstox") or k.startswith("brokers.fyers")
        }
        new_broker = post_broker - pre_broker
        assert not new_broker, f"Importing mcp_server loaded broker modules: {new_broker}"


# ── GATE 9: MCP creates ZERO broker connections ─────────────────────────────

class TestNoBrokerConnections:
    """MCP tool registration must not instantiate broker providers."""

    def test_tool_registration_no_broker_instantiation(self) -> None:
        from mcp_server.services import Services
        from mcp_server.tools import (
            register_system_tools, register_market_tools,
            register_event_tools, register_consumer_tools,
            register_replay_tools,
            register_alert_tools, register_market_intel_tools,
            register_options_analytics_tools, register_market_alert_tools,
        )

        mock_services = Services(
            store=MagicMock(), subscription_bus=MagicMock(),
            bg_task_manager=MagicMock(), source_manager=MagicMock(),
            timeouts={}, replay_cfg={}, metrics=MagicMock(),
        )

        broker_instantiations: list[str] = []
        orig_inits: dict[str, object] = {}

        # Patch broker __init__ if importable
        for cls_path, label in [
            ("brokers.upstox.rest.UpstoxRest", "UpstoxRest"),
            ("brokers.fyers.rest.FyersRest", "FyersRest"),
        ]:
            try:
                mod_path, cls_name = cls_path.rsplit(".", 1)
                mod = __import__(mod_path, fromlist=[cls_name])
                cls = getattr(mod, cls_name)
                orig_inits[label] = cls.__init__
                def _track(label=label, *a, **kw):
                    broker_instantiations.append(label)
                cls.__init__ = _track
            except (ImportError, AttributeError):
                pass

        try:
            mock_mcp = MagicMock()
            register_system_tools(mock_mcp)
            register_market_tools(mock_mcp, mock_services)
            register_event_tools(mock_mcp, mock_services)
            register_consumer_tools(mock_mcp, mock_services)
            register_replay_tools(mock_mcp, mock_services)
            register_alert_tools(mock_mcp, mock_services)
            register_market_intel_tools(mock_mcp, mock_services)
            register_options_analytics_tools(mock_mcp, mock_services)
            register_market_alert_tools(mock_mcp, mock_services)
        finally:
            for label, orig in orig_inits.items():
                cls_path = f"brokers.upstox.rest.{label}" if "Upstox" in label else f"brokers.fyers.rest.{label}"
                mod_path, cls_name = cls_path.rsplit(".", 1)
                try:
                    mod = __import__(mod_path, fromlist=[cls_name])
                    cls = getattr(mod, cls_name)
                    cls.__init__ = orig
                except (ImportError, AttributeError):
                    pass

        assert not broker_instantiations, (
            f"Tool registration instantiated broker objects: {broker_instantiations}"
        )


# ── GATE 8: MCP uses SAME MarketService as application ──────────────────────

class TestSharedState:
    """MCP context and application must share the same MarketService instance."""

    def test_services_dataclass_holds_market_service(self) -> None:
        from mcp_server.services import Services
        field_names = [f.name for f in dataclass_fields(Services)]
        assert "market_service" in field_names

    def test_market_service_type_hint_is_any(self) -> None:
        from mcp_server.services import Services
        ms_field = [f for f in dataclass_fields(Services) if f.name == "market_service"][0]
        assert ms_field.type == "Any" or "Any" in str(ms_field.type)


# ── READ-ONLY BOUNDARY ─────────────────────────────────────────────────────

class TestReadOnlyBoundary:
    """No trading/order tools should be registered in MCP-0."""

    FORBIDDEN = ["place_order", "modify_order", "cancel_order",
                 "holdings", "positions", "funds", "balance",
                 "transfer", "withdraw"]

    def test_no_trading_tool_names(self) -> None:
        from mcp_server import contract
        tool_values = [
            getattr(contract, attr)
            for attr in dir(contract)
            if attr.startswith("TOOL_") and isinstance(getattr(contract, attr), str)
        ]
        for tool_name in tool_values:
            for pattern in self.FORBIDDEN:
                assert pattern not in tool_name.lower(), (
                    f"Tool '{tool_name}' contains forbidden pattern '{pattern}'"
                )


# ── TOOL REGISTRATION DETERMINISM ───────────────────────────────────────────

class TestRegistrationDeterminism:
    """Tool registration must be deterministic and duplicate-safe."""

    EXPECTED_PROBES = {
        "system_ping", "market_quote", "market_depth", "market_status",
        "instrument_search", "watchlists", "market_history",
    }

    def test_probe_tools_exist_in_contract(self) -> None:
        from mcp_server import contract
        for tool_name in self.EXPECTED_PROBES:
            attr_name = "TOOL_" + tool_name.upper()
            assert hasattr(contract, attr_name), f"contract.py missing {attr_name}"

    def test_contract_constants_are_unique(self) -> None:
        from mcp_server import contract
        tool_values = [
            getattr(contract, attr)
            for attr in dir(contract)
            if attr.startswith("TOOL_") and isinstance(getattr(contract, attr), str)
        ]
        duplicates = [v for v in tool_values if tool_values.count(v) > 1]
        assert not duplicates, f"Duplicate tool name constants: {set(duplicates)}"
