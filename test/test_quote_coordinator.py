#!/usr/bin/env python3
"""Central quote coordinator tests (S7).

The coordinator is a READ-side selection layer over the existing
native-keyed store. It must:
  * select exactly ONE fresh quote per canonical instrument
  * apply a deterministic provider-preference tie-break (preference is a
    tie-break only, never a routing gate)
  * treat stale-only instruments as unavailable (never stale-as-live)
  * be consistent across the REST snapshot and the SSE stream
  * never create a second store or change the write-path key
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

from market.models import Quote

NOW = datetime(2026, 10, 19, 10, 0, 0, tzinfo=timezone.utc)


def _q(token, exchange, age_s, canonical_id, ltp=100.0):
    base = datetime.now(timezone.utc)
    return Quote(
        instrument_token=token, exchange=exchange, tradingsymbol="X",
        received_ts=base - timedelta(seconds=age_s), ltp=ltp,
        canonical_id=canonical_id)


def test_selects_most_recent_fresh_quote() -> None:
    from market.quote_coordinator import select_quote
    sel = select_quote(
        [_q("NSE_INDEX|NIFTY", "NSE", 10, "NSE:INDEX:NIFTY"),
         _q("NSE:NIFTY50-INDEX", "NSE", 5, "NSE:INDEX:NIFTY")],
        preference=("upstox", "fyers"), now=NOW)
    assert sel is not None
    assert sel.quote.instrument_token == "NSE:NIFTY50-INDEX"
    assert sel.selected_from == "fyers"
    assert sel.stale is False


def test_provider_preference_is_tiebreak_only() -> None:
    from market.quote_coordinator import select_quote
    # Equal age -> preference order decides (upstox first).
    sel = select_quote(
        [_q("NSE:NIFTY50-INDEX", "NSE", 5, "NSE:INDEX:NIFTY"),
         _q("NSE_INDEX|NIFTY", "NSE", 5, "NSE:INDEX:NIFTY")],
        preference=("upstox", "fyers"), now=NOW)
    assert sel is not None
    assert sel.selected_from == "upstox"


def test_stale_only_is_unavailable() -> None:
    from market.quote_coordinator import select_quote
    sel = select_quote(
        [_q("NSE_INDEX|NIFTY", "NSE", 4000, "NSE:INDEX:NIFTY")],
        preference=("upstox", "fyers"), stale_after_seconds=300.0,
        now=datetime.now(timezone.utc))
    assert sel is None


def test_one_fresh_wins_over_stale() -> None:
    from market.quote_coordinator import select_quote
    sel = select_quote(
        [_q("NSE_INDEX|NIFTY", "NSE", 4000, "NSE:INDEX:NIFTY"),
         _q("NSE:NIFTY50-INDEX", "NSE", 10, "NSE:INDEX:NIFTY")],
        preference=("upstox", "fyers"), stale_after_seconds=300.0,
        now=datetime.now(timezone.utc))
    assert sel is not None
    assert sel.selected_from == "fyers"


def test_select_canonical_quotes_one_per_instrument() -> None:
    from market.quote_coordinator import select_canonical_quotes
    out = select_canonical_quotes(
        [_q("NSE_INDEX|NIFTY", "NSE", 10, "NSE:INDEX:NIFTY"),
         _q("NSE:NIFTY50-INDEX", "NSE", 5, "NSE:INDEX:NIFTY"),
         _q("NSE_INDEX|Nifty Bank", "NSE", 10, "NSE:INDEX:BANKNIFTY")],
        preference=("upstox", "fyers"), now=NOW)
    assert set(out) == {"NSE:INDEX:NIFTY", "NSE:INDEX:BANKNIFTY"}
    assert out["NSE:INDEX:NIFTY"].selected_from == "fyers"


def test_select_canonical_quotes_skips_quotes_without_canonical_id() -> None:
    from market.quote_coordinator import select_canonical_quotes
    q = Quote(instrument_token="NSE_INDEX|NIFTY", exchange="NSE",
              tradingsymbol="X", received_ts=NOW, ltp=1.0, canonical_id=None)
    out = select_canonical_quotes([q], preference=("upstox", "fyers"), now=NOW)
    assert out == {}


def test_serialization_includes_selection_metadata() -> None:
    from market.quote_coordinator import select_quote
    from market.serialization import quote_to_dict
    sel = select_quote(
        [_q("NSE_INDEX|NIFTY", "NSE", 10, "NSE:INDEX:NIFTY")],
        preference=("upstox", "fyers"), now=NOW)
    d = quote_to_dict(sel.quote)
    assert d["canonical_id"] == "NSE:INDEX:NIFTY"


class _FakeService:
    """Minimal async quote source (mirrors MarketService.quotes)."""

    def __init__(self, quotes):
        self._quotes = quotes

    async def quotes(self):
        return tuple(self._quotes)


class _FakeBroker:
    async def subscribe(self):  # pragma: no cover - not used here
        raise RuntimeError("unused")


def test_market_quotes_endpoint_returns_selected_quotes() -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from api.routes import build_market_routes
    from market.quote_coordinator import QuoteCoordinator

    service = _FakeService([
        _q("NSE_INDEX|NIFTY", "NSE", 10, "NSE:INDEX:NIFTY"),
        _q("NSE:NIFTY50-INDEX", "NSE", 5, "NSE:INDEX:NIFTY"),
    ])
    coordinator = QuoteCoordinator(service)
    routes = build_market_routes(
        _FakeBroker(), market_service=service, quote_coordinator=coordinator)
    client = TestClient(Starlette(routes=routes))
    resp = client.get("/api/market/quotes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope"] == "canonical-selected"
    assert len(body["quotes"]) == 1
    # The most recent (fyers) slot wins; selection metadata is present.
    assert body["quotes"][0]["selected_from"] == "fyers"
    assert body["quotes"][0]["canonical_id"] == "NSE:INDEX:NIFTY"


def test_market_quotes_endpoint_keeps_a_stale_quote_as_last_known() -> None:
    """A stale-only instrument stays VISIBLE - marked stale, never live.

    The REST read goes through the shared canonical quote service, whose
    contract is that a last-known value is kept with its ``stale``/
    ``as_of`` provenance instead of being dropped silently.
    """
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from api.routes import build_market_routes
    from market.quote_coordinator import QuoteCoordinator

    service = _FakeService([
        _q("NSE_INDEX|NIFTY", "NSE", 4000, "NSE:INDEX:NIFTY"),
    ])
    coordinator = QuoteCoordinator(service)
    routes = build_market_routes(
        _FakeBroker(), market_service=service, quote_coordinator=coordinator)
    client = TestClient(Starlette(routes=routes))
    resp = client.get("/api/market/quotes")
    assert resp.status_code == 200
    quotes = resp.json()["quotes"]
    assert len(quotes) == 1, "the last known value stays visible"
    assert quotes[0]["stale"] is True
    assert quotes[0]["live"] is False
    assert quotes[0]["state"] == "stale"
    assert quotes[0]["as_of"]
    assert quotes[0]["canonical_id"] == "NSE:INDEX:NIFTY"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
