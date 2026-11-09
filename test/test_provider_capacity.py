#!/usr/bin/env python3
"""Verified provider capacity / entitlement model.

Capacity is a CEILING, not a target. The model is derived from the
providers' DOCUMENTED limits (official API documentation, cited in the
adapter declarations):

  Upstox (Market Data Feed V3)
    * LTPC 5,000 / Option Greeks 3,000 / Full 2,000 / Full D30 50
      (individual limits);
    * combined limits when several categories are used:
      LTPC 2,000 / Option Greeks 2,000 / Full 1,500;
    * two connections per user (five with Upstox Plus) - connections do
      NOT multiply the subscription limits;
    * REST: 50 rps / 500 rmin / 2,000 per 30 min per API; up to 500
      instrument keys per quote request.

  FYERS (Plans & Limits)
    * Data socket: 100 symbols on Standard, 5,000 on Prime - counted PER
      USER across all connections to that socket; one Data Socket
      instance at a time;
    * TBT socket: 1 (Standard) / 15 (Prime);
    * REST: 5 rps / 50 rmin / 5,000 per day (Standard); 50 symbols per
      quotes request; option chain at most 50 strikes per side.

There is NO application-level cap and NO blanket reserve any more: the
budget is the verified tier limit. A tier that is not verified is
reported as the documented default with its uncertainty.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_no_application_level_cap_exists() -> None:
    """The global caps are GONE (capacity is the provider's ceiling)."""
    import app.subscriptions.service as svc
    import providers.capacity as cap

    assert not hasattr(cap, "APP_CAP"), (
        "there must be no global application cap on provider capacity")
    assert not hasattr(svc, "MAX_INSTRUMENTS_PER_PROVIDER"), (
        "the subscription service must not enforce an arbitrary cap")


def test_effective_budget_is_the_full_verified_limit() -> None:
    from providers.capabilities import effective_budget
    assert effective_budget(100) == 100
    assert effective_budget(2000) == 2000
    assert effective_budget(5000) == 5000
    assert effective_budget(0) == 0


def test_upstox_declares_the_documented_limits() -> None:
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    assert UPSTOX.mode_limit("ltpc") == 5000
    assert UPSTOX.mode_limit("option_greeks") == 3000
    assert UPSTOX.mode_limit("full") == 2000
    assert UPSTOX.mode_limit("full_d30") == 50
    # Combined limits (several categories at once).
    assert UPSTOX.combined_limit("full") == 1500
    assert UPSTOX.combined_limit("ltpc") == 2000
    # REST limits, per documented quota scope ("per API, per user").
    assert set(UPSTOX.rest_limits) == {"market_quote", "historical",
                                       "option_chain", "market_info", "auth",
                                       "auth_code", "feed_authorize"}
    quote = UPSTOX.rest_limits["market_quote"]
    assert quote["requests_per_second"] == 50
    assert quote["requests_per_minute"] == 500
    assert quote["requests_per_30_minutes"] == 2000
    assert quote["max_instrument_keys_per_request"] == 500
    # The interactive-login row is stricter and has its own bucket.
    assert UPSTOX.rest_limits["auth"]["requests_per_second"] == 1
    assert UPSTOX.rest_limits["auth"]["requests_per_30_minutes"] == 60
    # The OAuth code exchange is a different documented API family: the
    # standard row, in its own budget.
    assert UPSTOX.rest_limits["auth_code"]["requests_per_second"] == 50
    assert UPSTOX.rest_limits["auth_code"]["requests_per_30_minutes"] == 2000
    # The feed authorization is its own API: it is NOT in the strict
    # login quota and not merged into an unrelated market-data quota.
    feed = UPSTOX.rest_limits["feed_authorize"]
    assert feed["requests_per_second"] == 50
    assert feed["requests_per_30_minutes"] == 2000


def test_fyers_declares_the_documented_limits() -> None:
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    assert FYERS.mode_limit("full") == 100
    assert FYERS.tier_limit("standard") == 100
    assert FYERS.tier_limit("prime") == 5000
    # FYERS documents ONE shared Data-API pool (plus a separate
    # transaction / user-info pool).
    assert set(FYERS.rest_limits) == {"data", "transaction"}
    data = FYERS.rest_limits["data"]
    assert data["requests_per_second"] == 5
    assert data["requests_per_minute"] == 50
    assert data["requests_per_day"] == 5000
    assert data["max_symbols_per_request"] == 50
    assert data["max_strikes_per_side"] == 50
    # No Prime assumption in the default declaration.
    assert FYERS.capacity == 100
    assert FYERS.default_tier == "standard"


def test_default_entitlement_is_the_documented_default_tier() -> None:
    from providers.capacity import resolve_entitlement
    ent = resolve_entitlement("fyers")
    assert ent.tier == "standard"
    assert ent.symbols == 100
    assert ent.source == "declared_default"
    assert ent.is_verified is False
    assert "not verified" in ent.note

    ent = resolve_entitlement("upstox")
    assert ent.symbols == 2000
    assert ent.is_verified is False


def test_operator_declared_tier_is_used_and_reported() -> None:
    from providers.capacity import resolve_entitlement
    ent = resolve_entitlement("fyers", tier="prime")
    assert ent.symbols == 5000
    assert ent.tier == "prime"
    assert ent.source == "operator_declared"
    assert ent.is_verified is True


def test_registry_budget_is_the_verified_ceiling() -> None:
    from brokers.fyers.capabilities import CAPABILITIES as FYERS
    from brokers.upstox.capabilities import CAPABILITIES as UPSTOX
    from providers.registry import ProviderRegistry
    r = ProviderRegistry([UPSTOX, FYERS])
    assert r.budget("fyers") == 100
    assert r.budget("fyers", tier="prime") == 5000
    assert r.budget("upstox") == 2000
    assert r.budget("unknown") is None
    assert r.mode_budget("upstox", "full") == 2000
    assert r.mode_budget("upstox", "ltpc") == 5000


def test_projection_reports_the_entitlement_source() -> None:
    from providers.registry import get_registry
    projection = {row["provider"]: row for row in get_registry().projection()}
    ent = projection["fyers"]["entitlement"]
    assert ent["tier"] == "standard"
    assert ent["symbols"] == 100
    assert ent["verified"] is False
    assert ent["source"] == "declared_default"


def test_rest_batch_limits_are_exposed() -> None:
    from providers.capacity import max_keys_per_request
    assert max_keys_per_request("upstox", "market_quote") == 500
    assert max_keys_per_request("fyers", "data") == 50


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
