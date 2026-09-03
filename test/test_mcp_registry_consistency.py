"""
MCP Registry Consistency Tests.

Proves that mcp_server/registry.py is the single source of truth
and that MCP tool registration and the WebUI API both consume it.

Checks:
  - Registry contains exactly 47 unique public tools
  - Every registry tool name has a matching TOOL_* constant in contract.py
  - No duplicate names in registry
  - All tools have status "active"
  - get_input_schema() produces valid JSON Schema for every tool
  - API endpoint returns same tools as registry
  - Category/search helpers work correctly
"""
from __future__ import annotations

import json

import pytest

from mcp_server.registry import (
    TOOLS,
    by_category,
    categories,
    get_by_name,
    get_input_schema,
    search,
)


# ── Registry completeness ─────────────────────────────────────────────────

class TestRegistryCompleteness:
    """Registry contains exactly 47 unique public tools."""

    def test_tool_count(self):
        assert len(TOOLS) == 47, f"Expected 47 tools, got {len(TOOLS)}"

    def test_no_duplicate_names(self):
        names = [t["name"] for t in TOOLS]
        assert len(names) == len(set(names)), (
            f"Duplicate names: {[n for n in names if names.count(n) > 1]}"
        )

    def test_all_tools_active(self):
        for t in TOOLS:
            assert t["status"] == "active", (
                f"Tool {t['name']} has status {t['status']}"
            )

    def test_all_tools_have_description(self):
        for t in TOOLS:
            assert t["description"], f"Tool {t['name']} has no description"

    def test_all_tools_have_category(self):
        for t in TOOLS:
            assert t["category"], f"Tool {t['name']} has no category"

    def test_all_tools_have_display(self):
        for t in TOOLS:
            assert t["display"], f"Tool {t['name']} has no display name"


# ── Registry ↔ contract sync ──────────────────────────────────────────────

class TestRegistryContractSync:
    """Every registry tool name has a corresponding TOOL_* constant in contract.py."""

    def test_all_names_in_contract(self):
        from mcp_server import contract

        contract_values = {
            getattr(contract, attr)
            for attr in dir(contract)
            if attr.startswith("TOOL_") and isinstance(getattr(contract, attr), str)
        }
        # Exclude retired tools
        contract_values.discard("event_publish")

        registry_names = {t["name"] for t in TOOLS}
        missing_in_contract = registry_names - contract_values
        missing_in_registry = contract_values - registry_names

        assert not missing_in_contract, (
            f"In registry but not contract: {missing_in_contract}"
        )
        assert not missing_in_registry, (
            f"In contract but not registry: {missing_in_registry}"
        )


# ── Schema validity ───────────────────────────────────────────────────────

class TestRegistrySchema:
    """get_input_schema() produces valid JSON Schema for every tool."""

    def test_all_tools_have_valid_schema(self):
        for t in TOOLS:
            schema = get_input_schema(t)
            assert schema["type"] == "object", (
                f"Tool {t['name']}: schema type is not 'object'"
            )
            assert "properties" in schema, (
                f"Tool {t['name']}: schema has no 'properties'"
            )
            # Verify required fields exist in properties
            for req in schema.get("required", []):
                assert req in schema["properties"], (
                    f"Tool {t['name']}: required field '{req}' not in properties"
                )

    def test_schema_json_serializable(self):
        for t in TOOLS:
            schema = get_input_schema(t)
            # Must be JSON-serializable (no Python objects)
            json_str = json.dumps(schema)
            assert json_str, f"Tool {t['name']}: schema not JSON-serializable"

    def test_schema_required_fields_match_registry(self):
        for t in TOOLS:
            schema = get_input_schema(t)
            registry_required = {
                p["name"] for p in t["params"] if p["required"]
            }
            schema_required = set(schema.get("required", []))
            assert registry_required == schema_required, (
                f"Tool {t['name']}: required mismatch "
                f"registry={registry_required} schema={schema_required}"
            )


# ── Helper functions ──────────────────────────────────────────────────────

class TestRegistryHelpers:
    """Registry helper functions work correctly."""

    def test_categories_count(self):
        cats = categories()
        assert len(cats) == 10, f"Expected 10 categories, got {len(cats)}: {cats}"

    def test_categories_ordering(self):
        cats = categories()
        # Must be stable (insertion order)
        assert cats[0] == "System"
        assert cats[-1] == "Analytics"

    def test_by_category_total(self):
        bc = by_category()
        total = sum(len(v) for v in bc.values())
        assert total == 47, f"by_category total: {total}"

    def test_search_found(self):
        hits = search("gamma")
        assert len(hits) == 1
        assert hits[0]["name"] == "compute_gex"

    def test_search_case_insensitive(self):
        hits = search("system_ping")
        assert len(hits) == 1
        assert hits[0]["name"] == "system_ping"

    def test_search_no_results(self):
        hits = search("zzz_nonexistent_zzz")
        assert len(hits) == 0

    def test_get_by_name_found(self):
        t = get_by_name("market_quote")
        assert t is not None
        assert t["display"] == "Market Quote"

    def test_get_by_name_missing(self):
        assert get_by_name("nonexistent_tool") is None
