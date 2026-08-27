"""
MCP-1 tool contract tests.

For every retained public tool, verifies:
  - handler is callable with declared parameters
  - output matches expected shape
  - error path returns error dict (not exception)
  - no broker imports leak into tool modules
  - all TOOL_* constants have matching registrations
  - dev tools are excluded from the public contract
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
MCP_SERVER_DIR = PROJECT_ROOT / "mcp_server"

BROKER_MODULES = ("brokers.upstox", "brokers.fyers")


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeMCP:
    """Minimal MCP server mock that captures registered tools."""

    def __init__(self):
        self.tools: dict[str, Any] = {}

    def tool(self, name=None, **kw):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


class _MockServices:
    """Minimal services mock for tool contract tests."""

    def __init__(self):
        self.store = MagicMock()
        self.subscription_bus = MagicMock()
        self.bg_task_manager = MagicMock()
        self.source_manager = MagicMock()
        self.timeouts = {}
        self.replay_cfg = {}
        self.metrics = MagicMock()
        self.market_service = None
        self.alert_engine = None
        self.market_intel = None
        self.instrument_catalog = None
        self.provider_market_data = None


def _register_all(fake: _FakeMCP, services: _MockServices) -> None:
    """Register all MCP tool modules on the fake server."""
    from mcp_server.tools.system import register_system_tools
    from mcp_server.tools.events import register_event_tools
    from mcp_server.tools.consumers import register_consumer_tools
    from mcp_server.tools.replay import register_replay_tools
    from mcp_server.tools.alerts import register_alert_tools
    from mcp_server.tools.market import register_market_tools
    from mcp_server.tools.market_intel_tools import register_market_intel_tools
    from mcp_server.tools.market_alerts import register_market_alert_tools
    from mcp_server.tools.options_analytics_tools import register_options_analytics_tools

    register_system_tools(fake)
    register_event_tools(fake, services)
    register_consumer_tools(fake, services)
    register_replay_tools(fake, services)
    register_alert_tools(fake, services)
    register_market_tools(fake, services)
    register_market_intel_tools(fake, services)
    register_market_alert_tools(fake, services)
    register_options_analytics_tools(fake, services)


# ── Contract completeness ────────────────────────────────────────────────────

class TestContractCompleteness:
    """All TOOL_* constants in contract.py must be registered."""

    def test_all_tool_constants_have_registration(self) -> None:
        from mcp_server import contract
        tool_values = [
            getattr(contract, attr)
            for attr in dir(contract)
            if attr.startswith("TOOL_") and isinstance(getattr(contract, attr), str)
        ]
        fake = _FakeMCP()
        services = _MockServices()
        _register_all(fake, services)

        registered = set(fake.tools.keys())
        # event_publish was removed from public registration in MCP-2B.3D;
        # the constant remains for docs/test-audit but is intentionally unregistered.
        exempt = {"event_publish"}
        for tool_name in tool_values:
            if tool_name in exempt:
                continue
            assert tool_name in registered, (
                f"TOOL constant '{tool_name}' not registered in any tool module"
            )

    def test_no_dev_tools_registered(self) -> None:
        """Dev tools must not be in the public contract."""
        from mcp_server import contract
        dev_attrs = [attr for attr in dir(contract) if attr.startswith("TOOL_DEV_")]
        assert not dev_attrs, (
            f"Dev tool constants still present in contract.py: {dev_attrs}"
        )


# ---------------------------------------------------------------------------
# Exact tool-name snapshot tests (MCP-2B.3D)
# ---------------------------------------------------------------------------

_MCP1_FROZEN_TOOLS: set[str] = {
    "system_ping",
    "market_quote", "market_depth", "market_status",
    "instrument_search", "watchlists", "market_history",
    "option_chain", "futures_contracts",
    "compute_pcr", "compute_max_pain", "compute_top_oi_strikes",
    "compute_atm", "compute_iv_skew", "compute_oi_buildup",
    "compute_support_resistance", "compute_straddle", "compute_gex",
    "compute_futures_basis",
    "price_long_straddle", "price_long_strangle",
    "price_bull_call_spread", "price_bear_put_spread",
    "price_iron_condor", "price_long_butterfly",
    "analyze_option_chain",
}

_MCP2B_TOOLS: set[str] = {
    # 5 generic alerts
    "alert_create", "alert_list", "alert_get", "alert_enable", "alert_disable",
    # 1 event diagnostics tool
    "event_list",
    # 2 consumer management
    "consumer_register", "consumer_topic_add",
    # 3 replay/checkpoint
    "consumer_event_pending_list", "consumer_event_acknowledge",
    "consumer_checkpoint_get",
    # 5 market alerts
    "market_alert_create", "market_alert_list", "market_alert_enable",
    "market_alert_disable", "market_alert_delete",
}

EXPECTED_MCP_TOOL_NAMES: set[str] = _MCP1_FROZEN_TOOLS | _MCP2B_TOOLS


class TestMcpToolSnapshot:
    """Exact 42-tool name set + 16 MCP-2B subset + zero dev_* (MCP-2B.3D)."""

    def test_exact_42_tool_names(self) -> None:
        fake = _FakeMCP()
        services = _MockServices()
        _register_all(fake, services)
        registered = set(fake.tools.keys())
        assert registered == EXPECTED_MCP_TOOL_NAMES, (
            f"Tool name mismatch.\n"
            f"  Missing: {EXPECTED_MCP_TOOL_NAMES - registered}\n"
            f"  Extra:   {registered - EXPECTED_MCP_TOOL_NAMES}"
        )

    def test_mcp2b_subset_exact_16(self) -> None:
        fake = _FakeMCP()
        services = _MockServices()
        _register_all(fake, services)
        registered = set(fake.tools.keys())
        # Filter to MCP-2B subset from the full registration.
        mcp2b_registered = registered & _MCP2B_TOOLS
        assert mcp2b_registered == _MCP2B_TOOLS, (
            f"MCP-2B subset mismatch.\n"
            f"  Missing: {_MCP2B_TOOLS - mcp2b_registered}\n"
            f"  Extra:   {mcp2b_registered - _MCP2B_TOOLS}"
        )

    def test_no_dev_tools_in_snapshot(self) -> None:
        fake = _FakeMCP()
        services = _MockServices()
        _register_all(fake, services)
        dev = [n for n in fake.tools if n.startswith("dev_")]
        assert not dev, f"dev_* tools found: {dev}"

    def test_event_publish_not_registered(self) -> None:
        """event_publish must NOT appear in the public tool registration."""
        fake = _FakeMCP()
        services = _MockServices()
        _register_all(fake, services)
        assert "event_publish" not in fake.tools, (
            "event_publish is still registered as a public tool"
        )

    def test_consumer_event_list_not_registered(self) -> None:
        """consumer_event_list was removed in MCP-2B.3C; must not be registered."""
        fake = _FakeMCP()
        services = _MockServices()
        _register_all(fake, services)
        assert "consumer_event_list" not in fake.tools, (
            "consumer_event_list is still registered"
        )


# ── System tools ─────────────────────────────────────────────────────────────

class TestSystemPingContract:
    def test_ping_returns_ok(self) -> None:
        fake = _FakeMCP()
        register_system_tools = __import__(
            "mcp_server.tools.system", fromlist=["register_system_tools"]
        ).register_system_tools
        register_system_tools(fake)
        result = fake.tools["system_ping"]()
        assert result["status"] == "ok"
        assert "timestamp" in result


# ── Market tools ─────────────────────────────────────────────────────────────

class TestMarketQuoteContract:
    async def test_quote_missing_service(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_quote"](instrument_ref="RELIANCE")
        assert "error" in result

    async def test_quote_unresolvable_ref(self) -> None:
        from mcp_server.tools.market import register_market_tools
        from market.service import MarketService
        fake = _FakeMCP()
        services = _MockServices()
        services.market_service = MarketService()
        register_market_tools(fake, services)
        result = await fake.tools["market_quote"](instrument_ref="NONEXISTENT")
        assert "error" in result
        assert "could not resolve" in result["error"]

    async def test_quote_not_found(self) -> None:
        from mcp_server.tools.market import register_market_tools
        from market.service import MarketService
        fake = _FakeMCP()
        services = _MockServices()
        services.market_service = MarketService()

        class _Cat:
            def search(self, q, exchange=None, limit=10):
                return [{"exchange": "NSE", "instrument_token": "12345"}]
        services.instrument_catalog = _Cat()

        register_market_tools(fake, services)
        result = await fake.tools["market_quote"](instrument_ref="RELIANCE")
        assert "error" in result
        assert "not found" in result["error"]


class TestMarketDepthContract:
    async def test_depth_missing_service(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_depth"](instrument_ref="RELIANCE")
        assert "error" in result

    async def test_depth_not_found(self) -> None:
        from mcp_server.tools.market import register_market_tools
        from market.service import MarketService
        fake = _FakeMCP()
        services = _MockServices()
        services.market_service = MarketService()

        class _Cat:
            def search(self, q, exchange=None, limit=10):
                return [{"exchange": "NSE", "instrument_token": "12345"}]
        services.instrument_catalog = _Cat()

        register_market_tools(fake, services)
        result = await fake.tools["market_depth"](instrument_ref="RELIANCE")
        assert "error" in result


class TestMarketStatusContract:
    async def test_status_missing_service(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_status"]()
        assert "error" in result

    async def test_status_returns_ok(self) -> None:
        from mcp_server.tools.market import register_market_tools
        from market.service import MarketService
        fake = _FakeMCP()
        services = _MockServices()
        services.market_service = MarketService()
        register_market_tools(fake, services)
        result = await fake.tools["market_status"]()
        assert result["status"] == "ok"
        assert "service" in result


class TestInstrumentSearchContract:
    async def test_search_no_catalog(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["instrument_search"](q="RELIANCE")
        assert "error" in result

    async def test_search_empty_results(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()

        class _Intel:
            def search(self, q, types=None, exchange=None, expiry=None, limit=10):
                return {"count": 0, "results": []}
        services.market_intel = _Intel()

        register_market_tools(fake, services)
        result = await fake.tools["instrument_search"](q="NONEXISTENT")
        assert result["status"] == "ok"
        assert result["count"] == 0


class TestMarketHistoryContract:
    async def test_history_missing_service(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_history"](
            instrument_ref="RELIANCE", unit="days", interval=1,
            from_date="2026-01-01", to_date="2026-01-31")
        assert "error" in result

    async def test_history_unresolvable_ref(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        services.provider_market_data = MagicMock()
        register_market_tools(fake, services)
        result = await fake.tools["market_history"](
            instrument_ref="NONEXISTENT", unit="days", interval=1,
            from_date="2026-01-01", to_date="2026-01-31")
        assert "error" in result
        assert "could not resolve" in result["error"]


class TestWatchlistsContract:
    async def test_watchlists_no_store(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        services.store = None
        register_market_tools(fake, services)
        result = await fake.tools["watchlists"]()
        assert "error" in result


# ── Intel tools ──────────────────────────────────────────────────────────────

class TestOptionChainContract:
    async def test_option_chain_no_intel(self) -> None:
        from mcp_server.tools.market_intel_tools import register_market_intel_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_intel_tools(fake, services)
        result = await fake.tools["option_chain"](underlying="NIFTY")
        assert "error" in result


class TestFuturesContractsContract:
    async def test_futures_no_intel(self) -> None:
        from mcp_server.tools.market_intel_tools import register_market_intel_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_intel_tools(fake, services)
        result = await fake.tools["futures_contracts"](underlying="NIFTY")
        assert "error" in result


# ── Analytics tools ──────────────────────────────────────────────────────────

class TestAnalyticsContracts:
    """Each compute_* tool returns error when intel/md services are unavailable."""

    ANALYTICS_TOOLS = [
        ("compute_pcr", {"underlying": "NIFTY"}),
        ("compute_max_pain", {"underlying": "NIFTY"}),
        ("compute_top_oi_strikes", {"underlying": "NIFTY"}),
        ("compute_atm", {"underlying": "NIFTY"}),
        ("compute_iv_skew", {"underlying": "NIFTY"}),
        ("compute_oi_buildup", {"underlying": "NIFTY"}),
        ("compute_support_resistance", {"underlying": "NIFTY"}),
        ("compute_straddle", {"underlying": "NIFTY"}),
        ("compute_gex", {"underlying": "NIFTY"}),
        ("compute_futures_basis", {"underlying": "NIFTY"}),
    ]

    @pytest.mark.parametrize("tool_name,kwargs", ANALYTICS_TOOLS,
                             ids=[t[0] for t in ANALYTICS_TOOLS])
    async def test_analytics_no_service(self, tool_name: str, kwargs: dict) -> None:
        from mcp_server.tools.options_analytics_tools import register_options_analytics_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_options_analytics_tools(fake, services)
        result = await fake.tools[tool_name](**kwargs)
        assert "error" in result or result.get("status") == "error"


class TestStrategyContracts:
    """Each price_* tool returns error when services are unavailable."""

    STRATEGY_TOOLS = [
        ("price_long_straddle", {"underlying": "NIFTY"}),
        ("price_long_strangle", {"underlying": "NIFTY", "call_strike": 25200, "put_strike": 24800}),
        ("price_bull_call_spread", {"underlying": "NIFTY", "lower_strike": 24900, "higher_strike": 25100}),
        ("price_bear_put_spread", {"underlying": "NIFTY", "higher_strike": 25100, "lower_strike": 24900}),
        ("price_iron_condor", {"underlying": "NIFTY", "put_sell_strike": 24700, "put_buy_strike": 24600,
                               "call_buy_strike": 25300, "call_sell_strike": 25400}),
        ("price_long_butterfly", {"underlying": "NIFTY", "lower_strike": 24800, "middle_strike": 25000, "upper_strike": 25200}),
    ]

    @pytest.mark.parametrize("tool_name,kwargs", STRATEGY_TOOLS,
                             ids=[t[0] for t in STRATEGY_TOOLS])
    async def test_strategy_no_service(self, tool_name: str, kwargs: dict) -> None:
        from mcp_server.tools.options_analytics_tools import register_options_analytics_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_options_analytics_tools(fake, services)
        result = await fake.tools[tool_name](**kwargs)
        assert "error" in result or result.get("status") == "error"

    async def test_analyze_option_chain_no_service(self) -> None:
        from mcp_server.tools.options_analytics_tools import register_options_analytics_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_options_analytics_tools(fake, services)
        result = await fake.tools["analyze_option_chain"](underlying="NIFTY")
        assert "error" in result or result.get("status") == "error"


# ── Broker import wall ──────────────────────────────────────────────────────

class TestNoBrokerImportsInTools:
    """Tool modules must never import broker adapters."""

    @pytest.mark.parametrize(
        "tool_file",
        sorted((MCP_SERVER_DIR / "tools").glob("*.py")),
        ids=lambda p: p.name,
    )
    def test_no_broker_imports(self, tool_file: Path) -> None:
        tree = ast.parse(tool_file.read_text(encoding="utf-8"), filename=str(tool_file))
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
            f"{tool_file.name} imports broker modules: {violations}"
        )


# ── Error semantics ─────────────────────────────────────────────────────────

class TestErrorSemantics:
    """Tool errors must be dicts, not exceptions (SDK wraps them)."""

    async def test_market_quote_error_is_dict(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_quote"](instrument_ref="X")
        assert isinstance(result, dict)
        assert "error" in result

    async def test_market_depth_error_is_dict(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_depth"](instrument_ref="X")
        assert isinstance(result, dict)
        assert "error" in result

    async def test_market_history_error_is_dict(self) -> None:
        from mcp_server.tools.market import register_market_tools
        fake = _FakeMCP()
        services = _MockServices()
        register_market_tools(fake, services)
        result = await fake.tools["market_history"](
            instrument_ref="X", unit="days", interval=1,
            from_date="2026-01-01", to_date="2026-01-31")
        assert isinstance(result, dict)
        assert "error" in result
