#!/usr/bin/env python3
"""SENSEX resolution: BSE contracts from discovery to demand.

The catalogue now holds only Sensex BSE_FO rows, but resolution must prove
that they are addressable end to end:

  * futures / option expiries / option chain resolve on BSE (never NSE);
  * BANKEX / SENSEX50 contracts never leak into the SENSEX surface;
  * ONE provider is selected per logical contract;
  * the option-chain request identifier is the provider-native key;
  * a rule resolves the nearest-two-expiry futures and a bounded listed
    strike window, and nothing else is demanded;
  * the NSE path is unchanged.

Isolated databases only - no network, no broker, no live catalogue.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest

_TODAY = date.today()


def _d(days: int) -> str:
    return (_TODAY + timedelta(days=days)).isoformat()


NEAR = _d(4)
NEXT = _d(32)
PAST = _d(-10)


class _Registry:
    """Provider registry double with an explicit enabled/preference order."""

    def __init__(self, order: list[str]):
        self._order = list(order)

    def preference_order(self) -> list[str]:
        return list(self._order)

    def enabled_ids(self) -> list[str]:
        return list(self._order)


@pytest.fixture(autouse=True)
def _clear_universe_cache():
    from market.derivatives_universe import clear_universe_cache
    clear_universe_cache()
    yield
    clear_universe_cache()


# -- catalogue fixtures --------------------------------------------------------


def _index_row(provider: str, symbol: str, label: str, *, token: str,
               underlying: str | None, segment: str,
               tradingsymbol: str | None = None) -> dict:
    exchange = "BSE" if label in ("SENSEX", "BANKEX", "SENSEX50") else "NSE"
    return dict(provider=provider, instrument_token=token,
                provider_symbol=symbol, exchange=exchange,
                tradingsymbol=tradingsymbol or symbol,
                name=f"{label} index",
                instrument_type="INDEX", segment=segment,
                underlying=underlying, lot_size=1)


def _future_row(provider: str, underlying: str, expiry: str, *, token: str,
                symbol: str, exchange: str = "BSE",
                segment: str = "BSE_FO") -> dict:
    return dict(provider=provider, instrument_token=token,
                provider_symbol=symbol, exchange=exchange,
                tradingsymbol=symbol, name=f"{underlying} FUT",
                instrument_type="FUTURE", segment=segment,
                underlying=underlying, expiry=expiry, lot_size=20)


def _option_row(provider: str, underlying: str, expiry: str, strike: float,
                option_type: str, *, token: str, symbol: str,
                exchange: str = "BSE", segment: str = "BSE_FO") -> dict:
    return dict(provider=provider, instrument_token=token,
                provider_symbol=symbol, exchange=exchange,
                tradingsymbol=symbol, name=f"{underlying} OPT",
                instrument_type="OPTION", segment=segment,
                underlying=underlying, expiry=expiry,
                strike=float(strike), option_type=option_type, lot_size=20)


SENSEX_STRIKES = (80000.0, 80500.0, 81000.0, 81500.0, 82000.0)


def _fyers_rows() -> list[dict]:
    rows = [
        _index_row("fyers", "BSE:SENSEX-INDEX", "SENSEX",
                   token="12100000001", underlying="SENSEX",
                   segment="BSE_INDEX"),
        _index_row("fyers", "BSE:BANKEX-INDEX", "BANKEX",
                   token="12100000002", underlying="BANKEX",
                   segment="BSE_INDEX"),
        _index_row("fyers", "NSE:NIFTY50-INDEX", "NIFTY",
                   token="10000000001", underlying="NIFTY",
                   segment="NSE_INDEX"),
    ]
    # SENSEX futures: one past (must not resolve), two live expiries.
    rows += [
        _future_row("fyers", "SENSEX", PAST, token="fy-sx-f0",
                    symbol="BSE:SENSEX25SEPFUT"),
        _future_row("fyers", "SENSEX", NEAR, token="fy-sx-f1",
                    symbol="BSE:SENSEX26OCTFUT"),
        _future_row("fyers", "SENSEX", NEXT, token="fy-sx-f2",
                    symbol="BSE:SENSEX26NOVFUT"),
        # Excluded underlyings that share the BSE_FO segment.
        _future_row("fyers", "BANKEX", NEXT, token="fy-bx-f1",
                    symbol="BSE:BANKEX26NOVFUT"),
        _future_row("fyers", "SENSEX50", NEXT, token="fy-sx50-f1",
                    symbol="BSE:SENSEX5026NOVFUT"),
        _future_row("fyers", "NIFTY", NEAR, token="fy-nf-f1",
                    symbol="NSE:NIFTY26OCTFUT", exchange="NSE",
                    segment="NSE_FO"),
    ]
    for expiry, tag in ((NEAR, "26OCT"), (NEXT, "26NOV")):
        for strike in SENSEX_STRIKES:
            for ot in ("CE", "PE"):
                rows.append(_option_row(
                    "fyers", "SENSEX", expiry, strike, ot,
                    token=f"fy-sx-{expiry}-{int(strike)}-{ot}",
                    symbol=f"BSE:SENSEX{tag}{int(strike)}{ot}"))
    rows += [
        _option_row("fyers", "BANKEX", NEXT, 81000.0, "CE",
                    token="fy-bx-opt-ce", symbol="BSE:BANKEX26NOV81000CE"),
        _option_row("fyers", "BANKEX", NEXT, 80500.0, "PE",
                    token="fy-bx-opt-pe", symbol="BSE:BANKEX26NOV80500PE"),
        _option_row("fyers", "NIFTY", NEAR, 24000.0, "CE",
                    token="fy-nf-opt-ce", symbol="NSE:NIFTY26OCT24000CE",
                    exchange="NSE", segment="NSE_FO"),
    ]
    return rows


def _upstox_rows() -> list[dict]:
    rows = [
        # Upstox publishes no ``underlying`` for an INDEX row; its
        # instrument key is the provider symbol, the symbol is the ticker.
        _index_row("upstox", "BSE_INDEX|SENSEX", "SENSEX",
                   token="BSE_INDEX|SENSEX", underlying=None,
                   segment="BSE_INDEX", tradingsymbol="SENSEX"),
    ]
    # The same logical SENSEX contracts as Fyers (one provider per contract).
    rows += [
        _future_row("upstox", "SENSEX", NEAR, token="BSE_FO|7001",
                    symbol="BSE_FO|7001"),
        _future_row("upstox", "SENSEX", NEXT, token="BSE_FO|7002",
                    symbol="BSE_FO|7002"),
    ]
    for strike in SENSEX_STRIKES:
        for ot in ("CE", "PE"):
            token = f"BSE_FO|{int(strike)}{ot}"
            rows.append(_option_row(
                "upstox", "SENSEX", NEAR, strike, ot, token=token,
                symbol=token))
    return rows


class _Env:
    def __init__(self, tmp_path):
        from app.instruments import InstrumentCatalog
        from core.persistence.store import EventStore

        self.store = EventStore(os.path.join(str(tmp_path), "catalogue.db"))
        self.store.replace_provider_instruments("fyers", _fyers_rows())
        self.store.replace_provider_instruments("upstox", _upstox_rows())
        self.catalog = InstrumentCatalog(self.store)
        self.spot_calls: list[tuple[str, str]] = []

    def make_intel(self, quotes: dict[tuple[str, str], Any] | None = None):
        from app.market_intel import MarketIntel

        def spot(exchange, token):
            self.spot_calls.append((exchange, token))
            return (quotes or {}).get((exchange, token))

        return MarketIntel(self.catalog, spot_provider=spot)

    def make_service(self, order: list[str] | None = None,
                     quotes: dict[tuple[str, str], Any] | None = None,
                     monkeypatch: Any = None):
        from app.subscriptions.service import SubscriptionService

        def spot(exchange, token):
            self.spot_calls.append((exchange, token))
            return (quotes or {}).get((exchange, token))

        reg = _Registry(order or ["upstox", "fyers"])
        if monkeypatch is not None:
            monkeypatch.setattr("providers.registry.get_registry",
                                lambda: reg)
        return SubscriptionService(store=self.store, catalog=self.catalog,
                                   spot_provider=spot, registry=reg)


def _quote(ltp: float) -> Any:
    return SimpleNamespace(ltp=ltp,
                           received_ts=datetime.now(timezone.utc))


def _tokens(contracts: list[dict]) -> set[str]:
    return {str(c.get("key")) for c in contracts}


# -- discovery ----------------------------------------------------------------


def test_futures_resolve_on_bse_and_exclude_other_underlyings(tmp_path):
    intel = _Env(tmp_path).make_intel()
    result = intel.futures_contracts("SENSEX")

    assert result["expiries"] == sorted([PAST, NEAR, NEXT]), (
        "discovery reports the catalogue's rows, including a stale one")
    assert [c["expiry"] for c in result["contracts"]].count(PAST) == 1
    assert [c["expiry"] for c in result["contracts"]][-2:] == [NEAR, NEXT]
    assert len(result["contracts"]) == 3
    assert all(c["exchange"] == "BSE" for c in result["contracts"])
    assert all(c["underlying"] == "SENSEX"
               for c in result["contracts"])
    assert not any("BANKEX" in str(c["symbol"])
                   or "SENSEX50" in str(c["symbol"])
                   for c in result["contracts"])
    assert result["underlying_instrument"]["exchange"] == "BSE"


def test_sensex_option_expiries_come_from_bse(tmp_path):
    intel = _Env(tmp_path).make_intel()
    result = intel.option_expiries("SENSEX")
    assert result["expiries"] == [NEAR, NEXT]
    assert result["underlying_instrument"]["exchange"] == "BSE"


def test_sensex_chain_resolves_on_bse_with_listed_strikes(tmp_path):
    intel = _Env(tmp_path).make_intel()
    chain = intel.option_chain("SENSEX", expiry=NEAR, window=1, spot=81040)

    assert chain["underlying"] == "SENSEX"
    assert chain["expiry"] == NEAR
    assert chain["atm_strike"] == 81000.0, (
        "ATM must be the nearest ACTUAL listed strike")
    assert [r["strike"] for r in chain["rows"]] == [80500.0, 81000.0,
                                                    81500.0]
    for row in chain["rows"]:
        assert row["call"] is not None and row["put"] is not None
        for leg in (row["call"], row["put"]):
            assert leg["exchange"] == "BSE"
            assert leg["underlying"] == "SENSEX"
            assert leg["expiry"] == NEAR
            assert "BANKEX" not in str(leg["symbol"])
            assert "SENSEX50" not in str(leg["symbol"])


def test_bankex_contracts_never_reach_the_sensex_surface(tmp_path):
    env = _Env(tmp_path)
    intel = env.make_intel()
    futures = intel.futures_contracts("SENSEX")
    chain = intel.option_chain("SENSEX", expiry=NEAR, window=25, spot=81000)

    surfaced = _tokens(futures["contracts"])
    for row in chain["rows"]:
        surfaced |= {row["call"]["native_key"], row["put"]["native_key"]}
    assert not any("BANKEX" in key or "SENSEX50" in key
                   for key in surfaced)


def test_one_provider_per_logical_contract(tmp_path, monkeypatch):
    env = _Env(tmp_path)
    monkeypatch.setattr("providers.registry.get_registry",
                        lambda: _Registry(["upstox", "fyers"]))
    intel = env.make_intel()

    chain = intel.option_chain("SENSEX", expiry=NEAR, window=1, spot=81000)
    assert len(chain["rows"]) == 3
    for row in chain["rows"]:
        assert row["call"]["provider"] == "upstox"
        assert row["put"]["provider"] == "upstox"
        assert row["call"]["native_key"].startswith("BSE_FO|")
    futures = intel.futures_contracts("SENSEX")
    by_expiry = {c["expiry"]: c for c in futures["contracts"]}
    assert by_expiry[NEAR]["provider"] == "upstox"
    assert by_expiry[NEXT]["provider"] == "upstox"

    monkeypatch.setattr("providers.registry.get_registry",
                        lambda: _Registry(["fyers", "upstox"]))
    intel = env.make_intel()
    chain = intel.option_chain("SENSEX", expiry=NEAR, window=1, spot=81000)
    for row in chain["rows"]:
        assert row["call"]["provider"] == "fyers"
        assert row["put"]["provider"] == "fyers"
        assert row["call"]["native_key"].startswith("BSE:SENSEX")


def test_spot_resolves_through_the_bse_index_quote(tmp_path):
    env = _Env(tmp_path)
    intel = env.make_intel({("BSE", "BSE:SENSEX-INDEX"): _quote(81040)})
    chain = intel.option_chain("SENSEX", expiry=NEAR, window=1)

    assert chain["spot"] == 81040.0
    assert chain["spot_basis"] == "live"
    assert chain["atm_strike"] == 81000.0
    assert ("BSE", "BSE:SENSEX-INDEX") in env.spot_calls
    assert ("NSE", "BSE:SENSEX-INDEX") not in env.spot_calls


def test_nse_chain_still_resolves_on_nse(tmp_path):
    intel = _Env(tmp_path).make_intel()
    futures = intel.futures_contracts("NIFTY")
    assert futures["expiries"] == [NEAR]
    assert all(c["exchange"] == "NSE" for c in futures["contracts"])

    chain = intel.option_chain("NIFTY", expiry=NEAR, window=25, spot=24000)
    assert chain["underlying"] == "NIFTY"
    for row in chain["rows"]:
        for leg in (row["call"], row["put"]):
            if leg is not None:
                assert leg["exchange"] == "NSE"


# -- subscriptions -------------------------------------------------------------


def test_subscription_exchange_mapping():
    from app.subscriptions.service import SubscriptionService

    assert SubscriptionService._exchange_for("SENSEX") == "BSE"
    assert SubscriptionService._exchange_for("sensex") == "BSE"
    assert SubscriptionService._exchange_for("BANKEX") == "BSE"
    assert SubscriptionService._exchange_for("NIFTY") == "NSE"
    assert SubscriptionService._exchange_for("RELIANCE") == "NSE"


def _seed_sensex_rule(store) -> None:
    store.upsert_md_subscription(category="index", key="SENSEX",
                                 label="SENSEX", enabled=True)
    store.upsert_md_derivative_rule(
        underlying="SENSEX", futures_enabled=True, futures_count=2,
        options_enabled=True, options_count=1, strikes_below=1,
        strikes_above=1, calls_enabled=True, puts_enabled=True)


def test_rule_resolves_nearest_two_futures_and_a_bounded_listed_window(
        tmp_path, monkeypatch):
    env = _Env(tmp_path)
    _seed_sensex_rule(env.store)
    svc = env.make_service(order=["fyers", "upstox"],
                           quotes={("BSE", "BSE:SENSEX-INDEX"):
                                   _quote(81040)},
                           monkeypatch=monkeypatch)

    resolved = svc.resolve()
    index_contracts = [c for c in resolved["contracts"]
                       if c["kind"] == "index"]
    futures = [c for c in resolved["contracts"] if c["kind"] == "future"]
    options = [c for c in resolved["contracts"] if c["kind"] == "option"]

    assert [c["label"] for c in index_contracts] == ["SENSEX"]
    assert index_contracts[0]["key"] == "BSE:SENSEX-INDEX"
    assert index_contracts[0]["provider"] == "fyers"

    assert [c["expiry"] for c in futures] == [NEAR, NEXT], (
        "only the nearest two UNEXPIRED expiries are demanded")
    assert all(c["provider"] == "fyers" for c in futures)
    assert not any("BANKEX" in c["key"] or "SENSEX50" in c["key"]
                   for c in resolved["contracts"])

    assert resolved["atm"] == {"SENSEX": 81000.0}
    assert sorted({(c["expiry"], c["strike"], c["option_type"])
                   for c in options}) == sorted(
        (NEAR, strike, ot)
        for strike in (80500.0, 81000.0, 81500.0)
        for ot in ("CE", "PE"))
    assert all(c["provider"] == "fyers" for c in options)
    assert not any(c["expiry"] == NEXT for c in options), (
        "only the nearest option expiry is demanded")

    lease_keys = [r["key"] for r in svc.lease_requests()]
    assert sorted(lease_keys) == sorted(
        _tokens(index_contracts + futures + options))
    assert len(lease_keys) == len(set(lease_keys)), (
        "the demand is a set: no duplicate subscriptions")


def test_without_a_rule_no_sensex_derivative_is_demanded(tmp_path,
                                                         monkeypatch):
    env = _Env(tmp_path)
    env.store.upsert_md_subscription(category="index", key="SENSEX",
                                     label="SENSEX", enabled=True)
    svc = env.make_service(order=["fyers", "upstox"],
                           quotes={("BSE", "BSE:SENSEX-INDEX"):
                                   _quote(81040)},
                           monkeypatch=monkeypatch)
    resolved = svc.resolve()
    kinds = {c["kind"] for c in resolved["contracts"]}
    assert kinds <= {"index"}
    assert not any("BSE_FO|" in c["key"] or "SENSEX26" in c["key"]
                   for c in resolved["contracts"])


def test_sensex_rule_is_not_demanded_when_the_index_is_disabled(
        tmp_path, monkeypatch):
    env = _Env(tmp_path)
    env.store.upsert_md_subscription(category="index", key="SENSEX",
                                     label="SENSEX", enabled=False)
    svc = env.make_service(order=["fyers", "upstox"],
                           quotes={("BSE", "BSE:SENSEX-INDEX"):
                                   _quote(81040)},
                           monkeypatch=monkeypatch)
    resolved = svc.resolve()
    assert not any(c["kind"] == "index" for c in resolved["contracts"])


# -- provider-native chain reference ------------------------------------------


def test_chain_underlying_ref_passes_the_native_key_through(tmp_path):
    from api.product_routes import _chain_underlying_ref

    env = _Env(tmp_path)
    intel = env.make_intel()
    chain = intel.option_chain("SENSEX", expiry=NEAR, window=1, spot=81000)

    key, exchange, tsym = _chain_underlying_ref(chain, "SENSEX", "fyers")
    assert key == "BSE:SENSEX-INDEX", (
        "splitting a Fyers symbol on ':' would address the wrong exchange")
    assert exchange == "BSE"

    upstox_block = {
        "underlying_instrument": {
            "native_key": "BSE_INDEX|SENSEX", "exchange": "BSE",
            "symbol": "SENSEX"}}
    key, exchange, _ = _chain_underlying_ref(upstox_block, "SENSEX",
                                             "upstox")
    assert key == "BSE_INDEX|SENSEX"
    assert exchange == "BSE"


def test_chain_enrichment_addresses_the_chain_owners_provider_key(
        tmp_path, monkeypatch):
    """SENSEX contracts are Upstox while the underlying row is Fyers.

    The snapshot request must use Upstox's OWN index key; the Fyers symbol
    would make the Upstox adapter query an unknown instrument.
    """
    from api.product_routes import _enrich_chain_with_provider_quotes

    env = _Env(tmp_path)
    monkeypatch.setattr("providers.registry.get_registry",
                        lambda: _Registry(["upstox", "fyers"]))
    intel = env.make_intel()
    chain = intel.option_chain("SENSEX", expiry=NEAR, window=1, spot=81000)
    assert chain["underlying_instrument"]["provider"] == "fyers"
    assert chain["rows"][0]["call"]["provider"] == "upstox"

    ref = intel.provider_underlying_ref("SENSEX", "upstox")
    assert ref == ("BSE_INDEX|SENSEX", "BSE", "SENSEX")

    class _MD:
        def __init__(self):
            self.calls: list[dict] = []

        async def option_chain(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                actual_expiry=NEAR, expiry=NEAR,
                strikes=[SimpleNamespace(
                    strike=r["strike"],
                    call=SimpleNamespace(oi=11, ltp=100.0),
                    put=SimpleNamespace(oi=22, ltp=90.0))
                    for r in chain["rows"]])

    md = _MD()
    asyncio.run(_enrich_chain_with_provider_quotes(
        chain, md, "SENSEX", NEAR,
        provider_ref=intel.provider_underlying_ref))
    assert md.calls and md.calls[0]["instrument_key"] == "BSE_INDEX|SENSEX"
    assert md.calls[0]["provider"] == "upstox"
    assert md.calls[0]["exchange"] == "BSE"
    assert chain["rows"][0]["call"]["quote"]["open_interest"] == 11
    assert chain["rows"][0]["put"]["quote"]["open_interest"] == 22
    analytics = chain["analytics"]
    assert analytics["scope"] == "window"
    assert analytics["total_call_oi"] == 11 * len(chain["rows"])
    assert analytics["total_put_oi"] == 22 * len(chain["rows"])
    assert analytics["pcr_by_oi"] == 2.0


def test_chain_enrichment_skips_when_owner_reference_is_unknown():
    """No provider's own identifier -> no request, never a foreign key."""
    from api.product_routes import _enrich_chain_with_provider_quotes

    class _MD:
        def __init__(self):
            self.calls: list[dict] = []

        async def option_chain(self, **kwargs):
            self.calls.append(kwargs)
            return None

    result = {
        "underlying_instrument": {
            "provider": "fyers", "native_key": "BSE:SENSEX-INDEX",
            "exchange": "BSE", "symbol": "BSE:SENSEX-INDEX"},
        "rows": [{"strike": 81000.0,
                  "call": {"provider": "upstox"},
                  "put": {"provider": "upstox"}}],
    }
    md = _MD()
    asyncio.run(_enrich_chain_with_provider_quotes(
        result, md, "SENSEX", NEAR))
    assert md.calls == [], (
        "an unresolved owner must skip, not send the Fyers symbol to Upstox")

    asyncio.run(_enrich_chain_with_provider_quotes(
        result, md, "SENSEX", NEAR,
        provider_ref=lambda underlying, provider: None))
    assert md.calls == []


def test_chain_enrichment_skips_a_mismatched_served_expiry():
    from api.product_routes import _enrich_chain_with_provider_quotes

    class _MD:
        def __init__(self, snapshot):
            self.snapshot = snapshot
            self.calls: list[dict] = []

        async def option_chain(self, **kwargs):
            self.calls.append(kwargs)
            return self.snapshot

    result = {
        "underlying_instrument": {
            "native_key": "BSE:SENSEX-INDEX", "exchange": "BSE",
            "symbol": "BSE:SENSEX-INDEX"},
        "rows": [{"strike": 81000.0,
                  "call": {"provider": "fyers"},
                  "put": {"provider": "fyers"}}],
    }
    mismatched = SimpleNamespace(actual_expiry=NEXT, expiry=NEXT, strikes=[
        SimpleNamespace(strike=81000.0, call=SimpleNamespace(oi=10),
                        put=SimpleNamespace(oi=20))])
    md = _MD(mismatched)
    asyncio.run(_enrich_chain_with_provider_quotes(
        result, md, "SENSEX", NEAR))
    assert md.calls and md.calls[0]["expiry"] == NEAR
    assert "quote" not in result["rows"][0]["call"], (
        "a chain served for another expiry must never be merged")

    matched = SimpleNamespace(actual_expiry=NEAR, expiry=NEAR, strikes=[
        SimpleNamespace(strike=81000.0,
                        call=SimpleNamespace(oi=11, ltp=100.0),
                        put=SimpleNamespace(oi=22, ltp=90.0))])
    result["rows"].append({"strike": 81000.0,
                           "call": {"provider": "upstox"},
                           "put": {"provider": "upstox"}})
    asyncio.run(_enrich_chain_with_provider_quotes(
        result, _MD(matched), "SENSEX", NEAR))
    assert result["rows"][0]["call"]["quote"]["open_interest"] == 11
    assert result["rows"][0]["put"]["quote"]["open_interest"] == 22
    assert "quote" not in result["rows"][1]["call"], (
        "quotes are never merged into another provider's contract")
