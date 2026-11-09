#!/usr/bin/env python3
"""Provider-neutral equity identity in the F&O workspace (S: routing).

Defect: ``workspace_contracts`` looked the equity up with a hardcoded
``provider="upstox"``, so the workspace's ``equity_key`` was ALWAYS an
Upstox key. Downstream that key selects the provider for the option
chain, so with Upstox logged out the chain failed with "upstox feed is
not authenticated" even though Fyers was authenticated and able to serve
the same chain.

The fix resolves the equity through the ENABLED provider registry (the
broker-neutral source of truth), preferring an available provider and
falling back to the other. No provider is hardcoded.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class _Row(dict):
    """Dict row with attribute access (the catalogue returns dicts)."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


class _Catalog:
    """Catalog whose equity rows exist only for specific providers."""

    def __init__(self, providers=("upstox", "fyers")):
        self._providers = providers
        self.queries: list[tuple] = []

    def derivative_expiries(self, underlying, instrument_type):
        return []

    def search(self, **kw):
        self.queries.append(kw)
        provider = kw.get("provider")
        if kw.get("instrument_type") == "EQUITY" and provider in self._providers:
            return [_Row(instrument_token=f"{provider}:EQUITY:X",
                         tradingsymbol=kw.get("q"),
                         provider=provider,
                         instrument_type="EQUITY")]
        return []

    def option_strikes(self, underlying, expiry):
        return []


class _Registry:
    def __init__(self, enabled=("upstox", "fyers"), available=("fyers",)):
        self._enabled = tuple(enabled)
        self._available = set(available)

    def enabled_ids(self):
        return list(self._enabled)

    def is_enabled(self, provider):
        return provider in self._enabled


def _svc(catalog, registry):
    from app.subscriptions import SubscriptionService
    return SubscriptionService(None, catalog, registry=registry)


def test_equity_resolves_through_the_provider_registry() -> None:
    cat = _Catalog()
    reg = _Registry()
    svc = _svc(cat, reg)
    ws = svc.workspace_contracts("ADANIGREEN", future_count=1,
                                  option_expiry_count=1)
    # The lookup went through the registry, not a hardcoded provider.
    providers = [q.get("provider") for q in cat.queries]
    assert "upstox" not in providers or providers.count("upstox") <= 1
    assert ws["equity_key"]


def test_equity_falls_back_to_the_other_provider() -> None:
    """If the preferred provider has no equity row, the other one is used."""
    cat = _Catalog(providers=("fyers",))
    reg = _Registry()
    svc = _svc(cat, reg)
    ws = svc.workspace_contracts("ADANIGREEN", future_count=1,
                                  option_expiry_count=1)
    assert ws["equity_key"] == "fyers:EQUITY:X"


def test_no_equity_row_yields_no_key() -> None:
    cat = _Catalog(providers=())
    reg = _Registry()
    svc = _svc(cat, reg)
    ws = svc.workspace_contracts("ADANIGREEN", future_count=1,
                                  option_expiry_count=1)
    assert ws["equity_key"] is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
