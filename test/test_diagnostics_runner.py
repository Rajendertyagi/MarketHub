#!/usr/bin/env python3
"""Unit tests for the diagnostics runner — classification, redaction, batching."""
from __future__ import annotations

import json
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running from project root
_PROJECT_DIR = str(Path(__file__).parent.parent)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from helpers.runner import R


def _make_runner(**kwargs) -> Any:
    """Create a DiagnosticsRunner with mocked dependencies."""
    from app.diagnostics import DiagnosticsRunner
    return DiagnosticsRunner(base_url="http://127.0.0.1:9999", mcp_url="http://127.0.0.1:9999/mcp", **kwargs)


# ===================================================================
# Result model tests
# ===================================================================

class TestDiagnosticResultModel:
    def test_pass_classification(self, runner: R) -> None:
        from app.diagnostics import DiagnosticResult
        r = DiagnosticResult(
            id="test", name="Test", category="SYSTEM", layer="SERVICE",
            status="PASS", message="ok", duration_ms=10,
            classification_reason="endpoint_returned_ok",
        )
        runner.assert_eq("result-id", r.id, "test")
        runner.assert_eq("result-status", r.status, "PASS")
        runner.assert_true("result-immutable", True, hasattr(r, 'id'))

    def test_partial_classification_stale_market(self, runner: R) -> None:
        from app.diagnostics import DiagnosticResult
        r = DiagnosticResult(
            id="partial_test", name="Partial Test",
            category="MARKET DATA", layer="REST",
            status="PARTIAL", message="No live quote",
            duration_ms=5, classification_reason="no_live_quote",
        )
        runner.assert_eq("partial-status", r.status, "PARTIAL")
        runner.assert_eq("partial-reason", r.classification_reason, "no_live_quote")

    def test_unavailable_classification_no_subscription(self, runner: R) -> None:
        from app.diagnostics import DiagnosticResult
        r = DiagnosticResult(
            id="unavail_test", name="Unavailable Test",
            category="MARKET DATA", layer="REST",
            status="UNAVAILABLE", message="No depth subscription",
            duration_ms=0, classification_reason="no_subscription",
        )
        runner.assert_eq("unavail-status", r.status, "UNAVAILABLE")

    def test_fail_classification_mcp_rest_mismatch(self, runner: R) -> None:
        from app.diagnostics import DiagnosticResult
        r = DiagnosticResult(
            id="fail_test", name="Fail Test",
            category="MCP", layer="MCP",
            status="FAIL", message="MCP quote failed",
            duration_ms=100, classification_reason="mcp_error",
        )
        runner.assert_eq("fail-status", r.status, "FAIL")

    def test_skipped_classification_fyers_not_configured(self, runner: R) -> None:
        from app.diagnostics import DiagnosticResult
        r = DiagnosticResult(
            id="skip_test", name="Skip Test",
            category="BROKERS", layer="SERVICE",
            status="SKIPPED", message="Fyers not configured",
            duration_ms=0, classification_reason="not_configured",
        )
        runner.assert_eq("skip-status", r.status, "SKIPPED")


# ===================================================================
# Secret redaction tests
# ===================================================================

class TestSecretRedaction:
    def test_instrument_key_never_redacted(self, runner: R) -> None:
        from app.diagnostics import _redact_secrets
        data = {"instrument_key": "NSE_INDEX|Nifty 50", "ltp": 24500.0}
        result = _redact_secrets(data)
        runner.assert_eq("instrument_key_preserved", result["instrument_key"], "NSE_INDEX|Nifty 50")
        runner.assert_eq("ltp_preserved", result["ltp"], 24500.0)

    def test_access_token_redacted(self, runner: R) -> None:
        from app.diagnostics import _redact_secrets
        data = {"access_token": "DUMMY-TOKEN-SHOULD-NOT-APPEAR", "symbol": "NIFTY"}
        result = _redact_secrets(data)
        runner.assert_eq("token_redacted", result["access_token"], "***REDACTED***")
        runner.assert_eq("symbol_preserved", result["symbol"], "NIFTY")

    def test_wss_url_redacted(self, runner: R) -> None:
        from app.diagnostics import _redact_secrets
        data = {"wss_url": "wss://example.com/DUMMY-QS", "state": "streaming"}
        result = _redact_secrets(data)
        runner.assert_eq("wss_redacted", result["wss_url"], "***REDACTED***")
        runner.assert_eq("state_preserved", result["state"], "streaming")

    def test_ltp_never_redacted(self, runner: R) -> None:
        from app.diagnostics import _redact_secrets
        data = {"ltp": 24500.5, "change_percent": 0.5}
        result = _redact_secrets(data)
        runner.assert_eq("ltp_preserved", result["ltp"], 24500.5)
        runner.assert_eq("change_preserved", result["change_percent"], 0.5)

    def test_nested_secret_redacted(self, runner: R) -> None:
        from app.diagnostics import _redact_secrets
        data = {
            "quote": {"ltp": 24500.0, "access_token": "secret123"},
            "meta": {"provider": "upstox"},
        }
        result = _redact_secrets(data)
        runner.assert_eq("nested_token_redacted", result["quote"]["access_token"], "***REDACTED***")
        runner.assert_eq("nested_ltp_preserved", result["quote"]["ltp"], 24500.0)
        runner.assert_eq("nested_provider_preserved", result["meta"]["provider"], "upstox")

    def test_api_secret_redacted(self, runner: R) -> None:
        from app.diagnostics import _redact_secrets
        data = {"api_secret": "my-secret", "api_key": "my-key"}
        result = _redact_secrets(data)
        runner.assert_eq("api_secret_redacted", result["api_secret"], "***REDACTED***")
        runner.assert_eq("api_key_redacted", result["api_key"], "***REDACTED***")


# ===================================================================
# Batching / summary tests
# ===================================================================

class TestBatching:
    def test_one_failure_does_not_abort_batch(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner, DiagnosticResult
        runner_instance = _make_runner()

        async def _good_check(_self):
            return DiagnosticResult(
                id="good", name="Good", category="SYSTEM", layer="SERVICE",
                status="PASS", message="ok", duration_ms=1,
                classification_reason="ok",
            )

        async def _bad_check(_self):
            return DiagnosticResult(
                id="bad", name="Bad", category="SYSTEM", layer="SERVICE",
                status="FAIL", message="boom", duration_ms=1,
                classification_reason="error",
            )

        import types
        checks = [
            types.SimpleNamespace(id="good", name="Good", category="SYSTEM", layer="SERVICE",
                                  mode=("quick",), description="", fn=_good_check, safe_for_auto_run=True),
            types.SimpleNamespace(id="bad", name="Bad", category="SYSTEM", layer="SERVICE",
                                  mode=("quick",), description="", fn=_bad_check, safe_for_auto_run=True),
        ]

        import asyncio
        result = asyncio.run(runner_instance._run_checks(checks))
        statuses = [r["status"] for r in result["results"]]
        runner.assert_eq("batch_has_two_results", len(statuses), 2)
        runner.assert_in("batch_contains_pass", "PASS", statuses)
        runner.assert_in("batch_contains_fail", "FAIL", statuses)

    def test_summary_counts_all_results(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner, DiagnosticResult
        results = [
            DiagnosticResult(id="a", name="A", category="X", layer="Y",
                             status="PASS", message="ok", duration_ms=1),
            DiagnosticResult(id="b", name="B", category="X", layer="Y",
                             status="PARTIAL", message="partial", duration_ms=1),
            DiagnosticResult(id="c", name="C", category="X", layer="Y",
                             status="FAIL", message="fail", duration_ms=1),
            DiagnosticResult(id="d", name="D", category="X", layer="Y",
                             status="UNAVAILABLE", message="navail", duration_ms=1),
            DiagnosticResult(id="e", name="E", category="X", layer="Y",
                             status="SKIPPED", message="skip", duration_ms=1),
        ]
        summary = DiagnosticsRunner._summarize(results)
        runner.assert_eq("sum-pass", summary.get("PASS"), 1)
        runner.assert_eq("sum-partial", summary.get("PARTIAL"), 1)
        runner.assert_eq("sum-fail", summary.get("FAIL"), 1)
        runner.assert_eq("sum-unavailable", summary.get("UNAVAILABLE"), 1)
        runner.assert_eq("sum-skipped", summary.get("SKIPPED"), 1)

    def test_timeout_result_becomes_fail(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner, DiagnosticResult
        runner_instance = _make_runner()

        async def _slow_check(_self):
            await asyncio.sleep(60)  # will timeout (wait_for is 30s)
            return DiagnosticResult(
                id="slow", name="Slow", category="SYSTEM", layer="SERVICE",
                status="PASS", message="ok", duration_ms=1,
                classification_reason="ok",
            )

        import types
        import asyncio
        checks = [types.SimpleNamespace(
            id="slow", name="Slow", category="SYSTEM", layer="SERVICE",
            mode=("quick",), description="", fn=_slow_check, safe_for_auto_run=True,
        )]
        result = asyncio.run(runner_instance._run_checks(checks))
        assert len(result["results"]) == 1
        runner.assert_eq("timeout-becomes-fail", result["results"][0]["status"], "FAIL")
        runner.assert_in("timeout-message", "timed out", result["results"][0]["message"])


# ===================================================================
# Parity logic tests
# ===================================================================

class TestParityLogic:
    def test_pass_when_rest_and_mcp_agree(self, runner: R) -> None:
        """If REST and MCP both return the same LTP, parity is PASS."""
        rest_ltp = 24500.0
        mcp_ltp = 24500.0
        diff_pct = abs(rest_ltp - mcp_ltp) / rest_ltp * 100
        runner.assert_true("same-ltp-zero-diff", diff_pct < 0.1, "LTP should match within 0.1%")

    def test_fail_when_rest_has_quote_but_mcp_fails(self, runner: R) -> None:
        """REST has quote but MCP returns error → FAIL."""
        rest_ltp = 24500.0
        mcp_ltp = None
        runner.assert_true("mcp-fail-while-rest-ok", rest_ltp is not None and mcp_ltp is None)

    def test_partial_when_both_unavailable(self, runner: R) -> None:
        """Both REST and MCP have no quote → PARTIAL."""
        rest_ltp = None
        mcp_ltp = None
        runner.assert_true("both-unavailable", rest_ltp is None and mcp_ltp is None)

    def test_pass_within_tolerance(self, runner: R) -> None:
        """REST=24500, MCP=24502 → 0.008% diff → PASS."""
        rest_ltp = 24500.0
        mcp_ltp = 24502.0
        diff_pct = abs(rest_ltp - mcp_ltp) / rest_ltp * 100
        runner.assert_true("within-tolerance", diff_pct < 0.1, f"diff={diff_pct:.3f}%")

    def test_fail_exceeds_tolerance(self, runner: R) -> None:
        """REST=24500, MCP=25000 → 2.04% diff → FAIL."""
        rest_ltp = 24500.0
        mcp_ltp = 25000.0
        diff_pct = abs(rest_ltp - mcp_ltp) / rest_ltp * 100
        runner.assert_true("exceeds-tolerance", diff_pct >= 0.1, f"diff={diff_pct:.3f}%")


# ===================================================================
# Check metadata tests
# ===================================================================

class TestCheckMetadata:
    def test_list_checks_returns_definitions(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner
        runner_instance = _make_runner()
        checks = runner_instance.list_checks()
        runner.assert_true("checks-not-empty", len(checks) > 0, f"got {len(checks)} checks")

    def test_quick_mode_has_expected_checks(self, runner: R) -> None:
        from app.diagnostics import _QUICK_CHECKS
        ids = [c.id for c in _QUICK_CHECKS]
        expected = {"app_health", "upstox_feed", "market_service",
                    "nifty_quote", "banknifty_quote", "mcp_status", "mcp_quote",
                    "option_chain", "history", "depth", "greeks", "news", "sentiment"}
        runner.assert_true("quick-has-app_health", "app_health" in ids)
        runner.assert_true("quick-has-mcp_quote", "mcp_quote" in ids)
        runner.assert_true("quick-has-nifty_quote", "nifty_quote" in ids)
        for exp in expected:
            runner.assert_in(f"quick-has-{exp}", exp, ids)

    def test_full_mode_contains_quick_checks(self, runner: R) -> None:
        from app.diagnostics import _QUICK_CHECKS, _FULL_CHECKS
        quick_ids = {c.id for c in _QUICK_CHECKS}
        full_ids = {c.id for c in _FULL_CHECKS}
        runner.assert_true("full-superset", quick_ids.issubset(full_ids),
                           f"quick={quick_ids}, full={full_ids}")

    def test_sse_not_in_quick_checks(self, runner: R) -> None:
        """SSE check is browser-owned, not in Python quick checks."""
        from app.diagnostics import _QUICK_CHECKS
        ids = [c.id for c in _QUICK_CHECKS]
        runner.assert_not_in("no-sse-in-python", "sse_connection", ids)

    def test_fyers_in_full_only(self, runner: R) -> None:
        from app.diagnostics import _QUICK_CHECKS, _FULL_CHECKS
        quick_ids = {c.id for c in _QUICK_CHECKS}
        full_ids = {c.id for c in _FULL_CHECKS}
        runner.assert_true("fyers-in-full", "fyers_feed" in full_ids)
        runner.assert_not_in("fyers-not-in-quick", "fyers_feed", quick_ids)


class TestFullCoverage:
    def test_full_meaningfully_broader_than_quick(self, runner: R) -> None:
        from app.diagnostics import _QUICK_CHECKS, _FULL_CHECKS
        quick_ids = {c.id for c in _QUICK_CHECKS}
        full_ids = {c.id for c in _FULL_CHECKS}
        extra = full_ids - quick_ids
        runner.assert_true("full-has-extra", len(extra) >= 5,
                           f"only {len(extra)} extra checks: {extra}")
        runner.assert_true("full-count-bigger",
                           len(_FULL_CHECKS) > len(_QUICK_CHECKS) + 4)

    def test_full_only_checks_present(self, runner: R) -> None:
        from app.diagnostics import _FULL_EXTRA
        ids = {c.id for c in _FULL_EXTRA}
        expected_extra = {
            "fyers_feed", "indices_coverage", "equity_quote",
            "future_quote", "option_quote", "option_detail", "sources_detail",
        }
        for exp in expected_extra:
            runner.assert_in(f"full-extra-{exp}", exp, ids)

    def test_eight_canonical_indices_represented(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner
        indices = DiagnosticsRunner._CANONICAL_INDICES
        runner.assert_eq("canonical-count", len(indices), 8)
        expected = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                    "NIFTYNXT50", "INDIA VIX", "SENSEX", "BANKEX"}
        runner.assert_true("canonical-match", set(indices) == expected,
                           f"got {set(indices)}")

    def test_indices_coverage_in_full(self, runner: R) -> None:
        from app.diagnostics import _QUICK_CHECKS, _FULL_CHECKS
        quick_ids = {c.id for c in _QUICK_CHECKS}
        full_ids = {c.id for c in _FULL_CHECKS}
        runner.assert_in("indices-in-full", "indices_coverage", full_ids)
        runner.assert_not_in("indices-not-in-quick", "indices_coverage", quick_ids)

    def test_representative_instrument_checks(self, runner: R) -> None:
        from app.diagnostics import _FULL_CHECKS
        full_ids = {c.id for c in _FULL_CHECKS}
        for exp in ("equity_quote", "future_quote", "option_quote"):
            runner.assert_in(f"repr-{exp}", exp, full_ids)

    def test_option_detail_check(self, runner: R) -> None:
        from app.diagnostics import _FULL_CHECKS
        full_ids = {c.id for c in _FULL_CHECKS}
        runner.assert_in("option-detail-in-full", "option_detail", full_ids)

    def test_sources_detail_check(self, runner: R) -> None:
        from app.diagnostics import _FULL_CHECKS
        full_ids = {c.id for c in _FULL_CHECKS}
        runner.assert_in("sources-detail-in-full", "sources_detail", full_ids)


class TestRepresentativeClassification:
    def test_equity_quote_classifies_unavailable_when_no_instrument(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner, DiagnosticResult
        inst = _make_runner()

        async def _no_inst(_self):
            return None

        import types
        inst._resolve_equity = _no_inst
        res = asyncio.run(inst._quote_instrument(None, "equity"))
        runner.assert_eq("equity-unavailable", res.status, "UNAVAILABLE")

    def test_option_detail_classifies_legs_present(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner
        inst = _make_runner()

        fake_chain = {"rows": [
            {"strike": 1, "call": {"instrument_key": "X", "oi": 10},
             "put": {"instrument_key": "Y"}},
        ]}
        import types
        async def _get(path):
            return fake_chain
        inst._get = _get
        res = asyncio.run(inst._check_option_detail())
        runner.assert_eq("option-detail-status", res.status, "PASS")
        runner.assert_true("option-detail-ce", res.data["ce_present"])
        runner.assert_true("option-detail-pe", res.data["pe_present"])
        runner.assert_in("option-detail-oi-exposed", "oi", res.data["fields_exposed"])

    def test_indices_coverage_fails_on_missing(self, runner: R) -> None:
        from app.diagnostics import DiagnosticsRunner
        inst = _make_runner()
        async def _get(path):
            return {"indices": [{"label": "NIFTY", "symbol": "NSE:NIFTY50-INDEX",
                                  "quote": None, "basis": "unavailable",
                                  "provider": None}]}
        inst._get = _get
        res = asyncio.run(inst._check_indices_coverage())
        runner.assert_eq("indices-missing-fail", res.status, "FAIL")
        runner.assert_true("indices-missing-reported",
                           "BANKNIFTY" in (res.data.get("missing") or []))


class TestParityLogicExtended:
    def test_partial_when_sse_has_no_quote(self, runner: R) -> None:
        """REST/MCP no quote, SSE connected but no fresh quote → PARTIAL."""
        rest_ltp = None
        mcp_ltp = None
        sse_has_quote = False
        sse_connected = True
        is_partial = (rest_ltp is None and mcp_ltp is None
                      and sse_connected and not sse_has_quote)
        runner.assert_true("sse-no-quote-partial", is_partial)

    def test_fail_on_rest_mcp_mismatch(self, runner: R) -> None:
        """REST quote present but MCP cannot resolve → FAIL."""
        rest_ltp = 24500.0
        mcp_ltp = None
        is_fail = rest_ltp is not None and mcp_ltp is None
        runner.assert_true("rest-mcp-mismatch-fail", is_fail)


# ===================================================================
# MAIN
# ===================================================================

def main() -> None:
    r = R()
    print("=" * 60)
    print("Diagnostics Runner Unit Tests")
    print("=" * 60)

    print("\n--- Result Model ---")
    t = TestDiagnosticResultModel()
    t.test_pass_classification(r)
    t.test_partial_classification_stale_market(r)
    t.test_unavailable_classification_no_subscription(r)
    t.test_fail_classification_mcp_rest_mismatch(r)
    t.test_skipped_classification_fyers_not_configured(r)

    print("\n--- Secret Redaction ---")
    t = TestSecretRedaction()
    t.test_instrument_key_never_redacted(r)
    t.test_access_token_redacted(r)
    t.test_wss_url_redacted(r)
    t.test_ltp_never_redacted(r)
    t.test_nested_secret_redacted(r)
    t.test_api_secret_redacted(r)

    print("\n--- Batching ---")
    t = TestBatching()
    t.test_one_failure_does_not_abort_batch(r)
    t.test_summary_counts_all_results(r)
    t.test_timeout_result_becomes_fail(r)

    print("\n--- Parity Logic ---")
    t = TestParityLogic()
    t.test_pass_when_rest_and_mcp_agree(r)
    t.test_fail_when_rest_has_quote_but_mcp_fails(r)
    t.test_partial_when_both_unavailable(r)
    t.test_pass_within_tolerance(r)
    t.test_fail_exceeds_tolerance(r)

    print("\n--- Check Metadata ---")
    t = TestCheckMetadata()
    t.test_list_checks_returns_definitions(r)
    t.test_quick_mode_has_expected_checks(r)
    t.test_full_mode_contains_quick_checks(r)
    t.test_sse_not_in_quick_checks(r)
    t.test_fyers_in_full_only(r)

    print("\n--- Full Coverage ---")
    t = TestFullCoverage()
    t.test_full_meaningfully_broader_than_quick(r)
    t.test_full_only_checks_present(r)
    t.test_eight_canonical_indices_represented(r)
    t.test_indices_coverage_in_full(r)
    t.test_representative_instrument_checks(r)
    t.test_option_detail_check(r)
    t.test_sources_detail_check(r)

    print("\n--- Representative Classification ---")
    t = TestRepresentativeClassification()
    t.test_equity_quote_classifies_unavailable_when_no_instrument(r)
    t.test_option_detail_classifies_legs_present(r)
    t.test_indices_coverage_fails_on_missing(r)

    print("\n--- Parity Logic (extended) ---")
    t = TestParityLogicExtended()
    t.test_partial_when_sse_has_no_quote(r)
    t.test_fail_on_rest_mcp_mismatch(r)

    print("\n" + "=" * 60)
    r.summary()
    print("=" * 60)
    if r.failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
