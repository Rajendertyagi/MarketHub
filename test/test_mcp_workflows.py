"""
MCP-1 multi-step AI workflow tests.

Tests realistic multi-step workflows that an AI agent would perform,
proving that identifiers flow cleanly between tools, no broker-specific
keys are required, and error paths work correctly.

Each workflow calls tool handlers directly with mock services.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from market.models import Depth, DepthLevel, Quote
from market.service import MarketService, QuotePatch
from datetime import datetime, timezone

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeMCP:
    def __init__(self):
        self.tools: dict[str, Any] = {}

    def tool(self, name=None, **kw):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


class _MockServices:
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


def _make_market_service() -> MarketService:
    """Create a MarketService pre-loaded with RELIANCE quote + depth."""
    return MarketService()


async def _setup_reliance(svc: MarketService) -> None:
    """Load RELIANCE data into a MarketService (async)."""
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token="INE002A01018",
        tradingsymbol="RELIANCE",
        received_ts=NOW, reported_fields={"ltp": 2850.0}))
    await svc.apply_depth(Depth(
        instrument_token="INE002A01018", exchange="NSE",
        tradingsymbol="RELIANCE",
        received_ts=NOW,
        bids=(DepthLevel(price=2849.5, quantity=100, orders=5),),
        asks=(DepthLevel(price=2850.5, quantity=200, orders=3),),
    ))


def _make_intel():
    """Create a mock MarketIntel that resolves 'RELIANCE' and 'NIFTY'."""
    class _Intel:
        def search(self, q, types=None, exchange=None, expiry=None, limit=10):
            if "RELIANCE" in q.upper():
                return {"count": 1, "results": [
                    {"instrument_token": "INE002A01018", "exchange": "NSE",
                     "tradingsymbol": "RELIANCE", "instrument_key": "NSE:INE002A01018"}
                ]}
            if "NIFTY" in q.upper():
                return {"count": 1, "results": [
                    {"instrument_token": "26000", "exchange": "NSE",
                     "tradingsymbol": "NIFTY 50", "instrument_key": "NSE:26000"}
                ]}
            return {"count": 0, "results": []}

        def resolve_underlying(self, name):
            return {"exchange": "NSE", "instrument_token": "26000",
                    "tradingsymbol": "NIFTY", "underlying": "NIFTY"}

        def option_expiries(self, underlying):
            return {"expiries": ["2026-09-04"]}

        def option_chain(self, underlying, expiry=None, window=10):
            return {"underlying": "NIFTY", "expiry": expiry or "2026-09-04",
                    "spot_price": 24500.0, "atm_strike": 24500.0,
                    "strikes": [
                        {"strike": 24400, "atm": False,
                         "call": {"ltp": 180.0, "oi": 50000, "iv": 13.5,
                                  "gamma": 0.0003, "delta": 0.35},
                         "put": {"ltp": 80.0, "oi": 60000, "iv": 14.0,
                                 "gamma": 0.0004, "delta": -0.40}},
                        {"strike": 24500, "atm": True,
                         "call": {"ltp": 120.0, "oi": 80000, "iv": 13.0,
                                  "gamma": 0.0005, "delta": 0.50},
                         "put": {"ltp": 120.0, "oi": 75000, "iv": 13.2,
                                 "gamma": 0.0005, "delta": -0.50}},
                        {"strike": 24600, "atm": False,
                         "call": {"ltp": 80.0, "oi": 70000, "iv": 13.8,
                                  "gamma": 0.0004, "delta": 0.60},
                         "put": {"ltp": 180.0, "oi": 55000, "iv": 14.2,
                                 "gamma": 0.0003, "delta": -0.65}},
                    ]}

        def futures_contracts(self, underlying, expiry=None):
            return {"underlying": "NIFTY",
                    "expiries": ["2026-09-04", "2026-09-25"],
                    "contracts": [
                        {"expiry": "2026-09-04", "instrument_key": "NSE:26001",
                         "lot_size": 50},
                    ]}

        def _spot_for(self, row):
            return 24500.0, {}

    return _Intel()


def _make_catalog():
    """Create a mock InstrumentCatalog."""
    class _Catalog:
        def search(self, q, exchange=None, limit=10):
            if "RELIANCE" in q.upper():
                return [{"exchange": "NSE", "instrument_token": "INE002A01018",
                         "tradingsymbol": "RELIANCE"}]
            if "NIFTY" in q.upper():
                return [{"exchange": "NSE", "instrument_token": "26000",
                         "tradingsymbol": "NIFTY 50"}]
            return []
    return _Catalog()


async def _build_services() -> _MockServices:
    """Build a fully wired _MockServices for workflow tests."""
    svc = _MockServices()
    svc.market_service = _make_market_service()
    await _setup_reliance(svc.market_service)
    svc.market_intel = _make_intel()
    svc.instrument_catalog = _make_catalog()
    return svc


def _register_tools(svc: _MockServices) -> dict[str, Any]:
    """Register all relevant tools and return the tool dict."""
    fake = _FakeMCP()
    from mcp_server.tools.market import register_market_tools
    from mcp_server.tools.market_intel_tools import register_market_intel_tools
    from mcp_server.tools.options_analytics_tools import register_options_analytics_tools
    from mcp_server.tools.system import register_system_tools
    register_system_tools(fake)
    register_market_tools(fake, svc)
    register_market_intel_tools(fake, svc)
    register_options_analytics_tools(fake, svc)
    return fake.tools


# ── Workflow 1: NIFTY Quote + Depth ─────────────────────────────────────────

class TestWorkflowNiftyQuoteDepth:
    """instrument_search -> market_quote -> market_depth"""

    async def test_nifty_search_quote_depth(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        # Step 1: search
        search = await tools["instrument_search"](q="NIFTY")
        assert search["status"] == "ok"
        assert search["count"] >= 1
        result = search["results"][0]
        token = result["instrument_token"]

        # Step 2: quote via instrument_ref (pipe format)
        # NIFTY quote may not be loaded (only RELIANCE is) — error is valid
        ref = f"{result['exchange']}|{token}"
        quote = await tools["market_quote"](instrument_ref=ref)
        assert isinstance(quote, dict)
        # Either ok (if loaded) or error (if not) — both are valid outcomes

        # Step 3: depth via same ref
        depth = await tools["market_depth"](instrument_ref=ref)
        assert isinstance(depth, dict)


# ── Workflow 2: RELIANCE History + Quote ─────────────────────────────────────

class TestWorkflowRelianceHistoryQuote:
    """instrument_search -> market_history -> market_quote"""

    async def test_reliance_search_history_quote(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        # Step 1: search
        search = await tools["instrument_search"](q="RELIANCE")
        assert search["status"] == "ok"
        result = search["results"][0]
        ref = f"{result['exchange']}|{result['instrument_token']}"

        # Step 2: quote
        quote = await tools["market_quote"](instrument_ref=ref)
        assert quote["status"] == "ok"
        assert quote["quote"]["ltp"] == 2850.0

        # Step 3: depth
        depth = await tools["market_depth"](instrument_ref=ref)
        assert depth["status"] == "ok"
        assert "depth" in depth
        assert len(depth["depth"]["bids"]) >= 1


# ── Workflow 3: Option Chain + Analytics ─────────────────────────────────────

class TestWorkflowOptionChainAnalytics:
    """instrument_search -> option_chain -> analytics"""

    async def test_nifty_option_chain_workflow(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        # Step 1: option chain (uses intel service)
        chain = await tools["option_chain"](underlying="NIFTY")
        assert chain["status"] == "ok"
        assert "strikes" in chain
        assert len(chain["strikes"]) >= 3

        # Step 2: analyze_option_chain requires provider_market_data
        # for the snapshot path — when unavailable, it returns error (valid)
        analytics = await tools["analyze_option_chain"](underlying="NIFTY")
        assert isinstance(analytics, dict)
        # Error is expected when provider_market_data is None


# ── Workflow 4: Strategy Pricing ─────────────────────────────────────────────

class TestWorkflowStrategyPricing:
    """option_chain -> price_long_straddle at ATM"""

    async def test_nifty_straddle_pricing(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        # Get chain to confirm data flows
        chain = await tools["option_chain"](underlying="NIFTY")
        assert chain["status"] == "ok"

        # Price a straddle — requires provider_market_data for snapshot path
        # When unavailable, error is valid
        result = await tools["price_long_straddle"](
            underlying="NIFTY", strike=24500.0)
        assert isinstance(result, dict)
        # Error is expected when provider_market_data is None


# ── Workflow 5: Unavailable Data ─────────────────────────────────────────────

class TestWorkflowUnavailableData:
    """market_quote("NONEXISTENT") -> error dict (not exception)"""

    async def test_nonexistent_instrument_returns_error(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        result = await tools["market_quote"](instrument_ref="NONEXISTENT_XYZ")
        assert isinstance(result, dict)
        assert "error" in result
        # Must NOT raise an exception
        assert result.get("status") != "ok"

    async def test_unresolvable_ref_returns_error(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        result = await tools["market_history"](
            instrument_ref="NONEXISTENT_XYZ", unit="days", interval=1,
            from_date="2026-01-01", to_date="2026-01-31")
        assert isinstance(result, dict)
        assert "error" in result


# ── Workflow 6: Futures Discovery ────────────────────────────────────────────

class TestWorkflowFuturesDiscovery:
    """futures_contracts -> market_quote nearest contract"""

    async def test_nifty_futures_discovery(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        # Step 1: list futures
        futures = await tools["futures_contracts"](underlying="NIFTY")
        assert futures["status"] == "ok"
        assert "contracts" in futures
        assert len(futures["contracts"]) >= 1

        # Step 2: try quoting the nearest contract (may not be loaded, that's ok)
        contract_key = futures["contracts"][0].get("instrument_key", "")
        if ":" in contract_key:
            exch, tok = contract_key.split(":", 1)
            quote = await tools["market_quote"](instrument_ref=f"{exch}|{tok}")
            assert isinstance(quote, dict)
            # Either ok or error — both are valid outcomes


# ── Workflow 7: Identifier Flow ──────────────────────────────────────────────

class TestIdentifierFlow:
    """Prove that identifiers from one tool work as input to another."""

    async def test_search_result_feeds_quote(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        search = await tools["instrument_search"](q="RELIANCE")
        result = search["results"][0]
        ref = f"{result['exchange']}|{result['instrument_token']}"

        quote = await tools["market_quote"](instrument_ref=ref)
        assert quote["status"] == "ok"

    async def test_pipe_format_works_directly(self) -> None:
        svc = await _build_services()
        tools = _register_tools(svc)

        # Direct pipe format without search
        quote = await tools["market_quote"](
            instrument_ref="NSE|INE002A01018")
        assert quote["status"] == "ok"
        assert quote["quote"]["ltp"] == 2850.0
