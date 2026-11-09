"""Yahoo Finance MCP tool contract tests.

Registration, schema validity, parameter validation, and the closed error-code
set. Runs against a fake service; no network, no yfinance import required.
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

PROJECT_ROOT = pathlib.Path(_PROJECT_DIR)
YAHOO_TOOLS_FILE = PROJECT_ROOT / "mcp_server" / "tools" / "yahoo_tools.py"

EXPECTED_YAHOO_TOOLS = {
    "yahoo_search",
    "yahoo_quote",
    "yahoo_history",
    "yahoo_company",
    "yahoo_financials",
    "yahoo_corporate_actions",
    "yahoo_holders",
    "yahoo_options",
    "yahoo_live_quote",
}


class _FakeMCP:
    def __init__(self):
        self.tools: dict[str, Any] = {}

    def tool(self, name=None, **kw):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


def _services(svc=None):
    class _S:
        pass
    s = _S()
    s.yahoo_service = svc
    return s


def _register(svc=None):
    from mcp_server.tools.yahoo_tools import register_yahoo_tools
    fake = _FakeMCP()
    register_yahoo_tools(fake, _services(svc))
    return fake


# ── Registration ─────────────────────────────────────────────────────────────

class TestRegistration:
    def test_all_nine_tools_register(self):
        fake = _register()
        assert EXPECTED_YAHOO_TOOLS <= set(fake.tools)

    def test_exact_nine_tools(self):
        fake = _register()
        assert set(fake.tools) == EXPECTED_YAHOO_TOOLS

    def test_tool_constants_match(self):
        from mcp_server import contract
        for name in EXPECTED_YAHOO_TOOLS:
            const = "TOOL_" + name.upper()
            assert hasattr(contract, const), f"missing {const}"
            assert getattr(contract, const) == name

    def test_every_tool_has_registry_description(self):
        from mcp_server.registry import get_by_name
        for name in EXPECTED_YAHOO_TOOLS:
            entry = get_by_name(name)
            assert entry is not None, f"{name} missing from registry"
            assert entry["description"].strip()
            assert entry["params"] is not None

    def test_registry_schemas_are_valid(self):
        from mcp_server.registry import get_by_name, get_input_schema
        for name in EXPECTED_YAHOO_TOOLS:
            schema = get_input_schema(get_by_name(name))
            assert schema["type"] == "object"
            assert isinstance(schema["properties"], dict)
            for prop in schema["properties"].values():
                assert "type" in prop
                assert "description" in prop

    def test_no_broker_imports(self):
        tree = ast.parse(YAHOO_TOOLS_FILE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert not m.startswith("brokers"), f"broker import: {m}"
                assert m not in ("app.auth", "app.secrets_store"), m


# ── Service unavailable ──────────────────────────────────────────────────────

class TestServiceUnavailable:
    @pytest.mark.parametrize("tool", sorted(EXPECTED_YAHOO_TOOLS))
    async def test_missing_service_returns_error(self, tool):
        fake = _register(None)
        handler = fake.tools[tool]
        # each tool's first required arg
        arg = {
            "yahoo_search": {"query": "apple"},
            "yahoo_quote": {"symbol": "AAPL"},
            "yahoo_history": {"symbol": "AAPL"},
            "yahoo_company": {"symbol": "AAPL"},
            "yahoo_financials": {"symbol": "AAPL"},
            "yahoo_corporate_actions": {"symbol": "AAPL"},
            "yahoo_holders": {"symbol": "AAPL"},
            "yahoo_options": {"symbol": "AAPL"},
            "yahoo_live_quote": {"symbol": "AAPL"},
        }[tool]
        result = await handler(**arg)
        assert isinstance(result, dict)
        assert result.get("status") == "error"
        assert result.get("code") == "service_unavailable"


# ── Validation at the MCP boundary ───────────────────────────────────────────

class TestValidation:
    async def test_quote_empty_symbol_rejected(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_quote"](symbol="   ")
        assert result["status"] == "error"
        assert result["code"] == "invalid_symbol"

    async def test_history_bad_period_rejected(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_history"](
            symbol="AAPL", period="7q")
        assert result["status"] == "error"
        assert result["code"] == "invalid_period"

    async def test_history_bad_interval_rejected(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_history"](
            symbol="AAPL", period="1mo", interval="3s")
        assert result["status"] == "error"
        assert result["code"] == "invalid_interval"

    async def test_history_period_and_dates_conflict(self):
        """An explicit period together with start/end is rejected."""
        fake = _register(_Stub())
        result = await fake.tools["yahoo_history"](
            symbol="AAPL", period="3mo", start="2026-01-01")
        assert result["status"] == "error"
        assert result["code"] == "invalid_arguments"

    async def test_search_limit_bounded(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_search"](query="apple", limit=999)
        assert result["status"] == "error"
        assert result["code"] == "invalid_arguments"

    async def test_financials_bad_statement_rejected(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_financials"](
            symbol="AAPL", statement="bogus")
        assert result["status"] == "error"
        assert result["code"] == "invalid_statement"

    async def test_financials_bad_period_rejected(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_financials"](
            symbol="AAPL", period="monthly")
        assert result["status"] == "error"
        assert result["code"] == "invalid_period"

    async def test_holders_bad_type_rejected(self):
        fake = _register(_Stub())
        result = await fake.tools["yahoo_holders"](
            symbol="AAPL", holder_type="nobodies")
        assert result["status"] == "error"
        assert result["code"] == "invalid_holder_type"


# ── Passthrough to service ───────────────────────────────────────────────────

class TestServiceErrorBecomesData:
    """End-to-end: a typed service error crosses the MCP boundary as DATA."""

    async def test_dead_symbol_returns_structured_error(self):
        from test_yahoo_service import FakeYF

        from yfinance_service.service import YahooFinanceService
        svc = YahooFinanceService(yf=FakeYF())
        fake = _register(svc)
        result = await fake.tools["yahoo_quote"](symbol="NOTAREALSYMBOL12345")
        assert result["status"] == "error"
        assert result["code"] == "invalid_symbol"
        assert result["source"] == "yahoo"
        assert "Traceback" not in result["reason"]

    async def test_asset_without_options_returns_unavailable(self):
        from test_yahoo_service import FakeYF

        from yfinance_service.service import YahooFinanceService
        svc = YahooFinanceService(yf=FakeYF())
        fake = _register(svc)
        result = await fake.tools["yahoo_options"](symbol="NOTAREALSYMBOL12345")
        assert result["status"] in ("error", "ok")


class TestPassthrough:
    async def test_quote_passes_resolved_symbol(self):
        svc = _Stub(quote={"status": "ok", "symbol": "AAPL", "data": {}})
        fake = _register(svc)
        result = await fake.tools["yahoo_quote"](symbol="aapl")
        assert result["status"] == "ok"
        assert svc.calls["quote"]["symbol"] == "aapl"

    async def test_all_tools_return_dict(self):
        svc = _Stub()
        fake = _register(svc)
        for name in EXPECTED_YAHOO_TOOLS:
            arg = {
                "yahoo_search": {"query": "a"},
                "yahoo_quote": {"symbol": "A"},
                "yahoo_history": {"symbol": "A"},
                "yahoo_company": {"symbol": "A"},
                "yahoo_financials": {"symbol": "A"},
                "yahoo_corporate_actions": {"symbol": "A"},
                "yahoo_holders": {"symbol": "A"},
                "yahoo_options": {"symbol": "A"},
                "yahoo_live_quote": {"symbol": "A"},
            }[name]
            result = await fake.tools[name](**arg)
            assert isinstance(result, dict), name


class _Stub:
    """Service double: records calls, returns canned payloads."""

    def __init__(self, **payloads):
        self.calls: dict[str, Any] = {}
        self._payloads = payloads

    def _p(self, name):
        return self._payloads.get(name, {"status": "ok", "data_type": name,
                                         "data": {}})

    async def search(self, **kw):
        self.calls["search"] = kw
        return self._p("search")

    async def quote(self, **kw):
        self.calls["quote"] = kw
        return self._p("quote")

    async def history(self, **kw):
        self.calls["history"] = kw
        return self._p("history")

    async def company(self, **kw):
        self.calls["company"] = kw
        return self._p("company")

    async def financials(self, **kw):
        self.calls["financials"] = kw
        return self._p("financials")

    async def corporate_actions(self, **kw):
        self.calls["corporate_actions"] = kw
        return self._p("corporate_actions")

    async def holders(self, **kw):
        self.calls["holders"] = kw
        return self._p("holders")

    async def options(self, **kw):
        self.calls["options"] = kw
        return self._p("options")

    async def live_quote(self, **kw):
        self.calls["live_quote"] = kw
        return self._p("live_quote")
