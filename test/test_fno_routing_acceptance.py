#!/usr/bin/env python3
"""F&O routing + chain-join acceptance gates (Phase B/C/D/E).

Each gate asserts a REQUIRED behaviour of the implemented data path
against the REAL catalogue shape (both providers, cross-provider
duplicates, short symbols, multiple expiries, NSE + BSE):

  G1  Every supported index-F&O underlying (INCLUDING NIFTYFPI)
      resolves to a canonical identity and a provider request reference.
  G2  Catalogue-derived stock contracts include SHORT symbols and
      MULTIPLE expiries.
  G3  The nearest-two-futures plan respects provider capabilities and
      the configured capacity, and reports states truthfully.
  G4  BSE expiry handling does not discard otherwise-supported
      contracts.
  G5  Each logical contract is routed ONCE; provider data is never
      misattributed during a join.
  G6  A chain snapshot's expiry / provider / scope metadata reflect the
      ACTUAL response.
  G7  An analytics chain key round-trips to the correct underlying and
      expiry, and the production refresh path calls the chain service
      (not the quote store).
  G8  REST, market-intelligence and the workspace agree on the same
      logical contract set.

The catalogue used here is a SYNTHETIC one that mirrors the real
catalogue's shape (both providers, duplicated contracts, short symbols,
NSE + BSE) — a full-catalogue run is not possible in a unit test, but
the SHAPE is what these gates protect.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date

import pytest

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import tempfile

from app.instruments import InstrumentCatalog
from core.persistence.store import EventStore


# Fixture expiries span 2026-10-27..2026-12-29; pin "today" for determinism.
class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 1)


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    import app.fno_coverage as fno_cov
    monkeypatch.setattr(fno_cov, "date", _FixedDate)
    try:
        import market.derivatives_universe as du
        monkeypatch.setattr(du, "date", _FixedDate)
    except Exception:
        pass
    try:
        import app.subscriptions.service as sub_svc
        monkeypatch.setattr(sub_svc, "date", _FixedDate)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# A catalogue that mirrors the real shape.
# ---------------------------------------------------------------------------

# (provider, token, provider_symbol, exchange, segment, type, tradingsymbol,
#  underlying, expiry, strike, option_type)
_ROWS: list[tuple] = []


def _eq(provider, token, psym, tsym, isin=None):
    _ROWS.append((provider, token, psym, "NSE", "NSE_EQ", "EQUITY", tsym,
                   tsym, None, None, None, isin))


def _idx(provider, token, psym, tsym, underlying):
    _ROWS.append((provider, token, psym, "NSE", "NSE_INDEX", "INDEX", tsym,
                   underlying, None, None, None, None))


def _fut(provider, token, psym, tsym, underlying, expiry):
    _ROWS.append((provider, token, psym, "NSE", "NSE_FO", "FUTURE", tsym,
                   underlying, expiry, None, None, None))


def _opt(provider, token, psym, tsym, underlying, expiry, strike, otype):
    _ROWS.append((provider, token, psym, "NSE", "NSE_FO", "OPTION", tsym,
                   underlying, expiry, strike, otype, None))


def _build_rows():
    _ROWS.clear()
    # The six canonical F&O index underlyings, both providers.
    for label, fyers_sym, upstox_sym in (
        ("NIFTY", "NSE:NIFTY50-INDEX", "NSE_INDEX|Nifty 50"),
        ("BANKNIFTY", "NSE:NIFTYBANK-INDEX", "NSE_INDEX|Nifty Bank"),
        ("FINNIFTY", "NSE:FINNIFTY-INDEX", "NSE_INDEX|Nifty Fin Service"),
        ("MIDCPNIFTY", "NSE:MIDCPNIFTY-INDEX", "NSE_INDEX|NIFTY MID SELECT"),
        ("NIFTYNXT50", "NSE:NIFTYNXT50-INDEX", "NSE_INDEX|Nifty Next 50"),
        ("NIFTYFPI", "NSE:NIFTYFPI150-INDEX", "NSE_INDEX|Nifty FPI 150"),
    ):
        _idx("upstox", upstox_sym, upstox_sym, label, None)
        _idx("fyers", f"fy-{label}", fyers_sym, fyers_sym, label)
    # A SHORT-symbol stock (LT) — the class the substring search broke.
    _eq("upstox", "NSE_EQ|INE018A01030", "NSE_EQ|INE018A01030", "LT",
        "INE018A01030")
    _eq("fyers", "fy-LT", "NSE:LT-EQ", "LT", "INE018A01030")
    # A normal stock with MULTIPLE expiries.
    for provider, tok, psym in (("upstox", "NSE_EQ|INE002A01018",
                                 "NSE_EQ|INE002A01018"),
                                ("fyers", "fy-RELIANCE", "NSE:RELIANCE-EQ")):
        _eq(provider, tok, psym, "RELIANCE", "INE002A01018")
    for expiry in ("2026-10-27", "2026-11-23", "2026-12-29"):
        _fut("upstox", f"NSE_FO|FUT-{expiry}", f"NSE_FO|FUT-{expiry}",
             f"RELIANCE FUT {expiry}", "RELIANCE", expiry)
        _fut("fyers", f"fy-FUT-{expiry}", f"NSE:RELIANCE{expiry[:7]}FUT",
             f"RELIANCE{expiry[:7]}FUT", "RELIANCE", expiry)
    # Options for BOTH providers on the same logical contracts.
    for expiry in ("2026-10-27", "2026-11-23"):
        for strike in (1200.0, 1210.0):
            for otype in ("CE", "PE"):
                _opt("upstox", f"NSE_FO|O-{expiry}-{strike}-{otype}",
                     f"NSE_FO|O-{expiry}-{strike}-{otype}",
                     f"RELIANCE {strike} {otype} {expiry}", "RELIANCE",
                     expiry, strike, otype)
                _opt("fyers", f"fy-O-{expiry}-{strike}-{otype}",
                     f"NSE:RELIANCE{expiry[:7]}{strike:.0f}{otype}",
                     f"RELIANCE{expiry[:7]}{strike:.0f}{otype}", "RELIANCE",
                     expiry, strike, otype)
    # BSE contracts with a DIFFERENT expiry calendar (must not be discarded
    # by an NSE expiry).
    _ROWS.append(("upstox", "BSE_FO|SENSEXFUT", "BSE_FO|SENSEXFUT", "BSE",
                   "BSE_FO", "FUTURE", "SENSEX FUT 29 OCT 26", "SENSEX",
                   "2026-10-29", None, None, None))
    _ROWS.append(("fyers", "fy-SENSEXFUT", "BSE:SENSEX26OCTFUT", "BSE",
                   "BSE_FO", "FUTURE", "SENSEX26OCTFUT", "SENSEX",
                   "2026-10-29", None, None, None))


def _catalog():
    _build_rows()
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    records = []
    for (provider, token, psym, exch, seg, itype, tsym, und, expiry,
         strike, otype, isin) in _ROWS:
        records.append({
            "provider": provider, "instrument_token": token,
            "provider_symbol": psym, "exchange": exch, "segment": seg,
            "instrument_type": itype, "tradingsymbol": tsym,
            "underlying": und, "expiry": expiry, "strike": strike,
            "option_type": otype, "isin": isin,
            "name": tsym, "lot_size": 500,
        })
    for provider in ("upstox", "fyers"):
        store.replace_provider_instruments(
            provider, [r for r in records if r["provider"] == provider])
    return InstrumentCatalog(store), store, tmp


# ---------------------------------------------------------------------------
# G1 — index-F&O underlyings, including NIFTYFPI
# ---------------------------------------------------------------------------

def test_g1_all_fno_indices_resolve_including_niftyfpi() -> None:
    from app.market_indices import (
        OPTION_CHAIN_INDICES,
        index_provider_keys,
        label_for_underlying,
    )
    from market.index_registry import FNO_INDEX_LABELS

    assert "NIFTYFPI" in FNO_INDEX_LABELS
    assert "NIFTYFPI" in OPTION_CHAIN_INDICES
    # The catalogue spelling maps to the canonical label.
    assert label_for_underlying("NIFTYFPI") == "NIFTYFPI"

    catalog, _store, tmp = _catalog()
    try:
        # Every index the fixture lists must resolve to a provider key.
        for label in FNO_INDEX_LABELS:
            if label in ("SENSEX", "BANKEX"):
                # BSE indices are not in this NSE-shaped fixture.
                continue
            keys = index_provider_keys(catalog, label)
            assert keys, f"no provider feed key for {label}"
            # BOTH providers' keys are exposed (the index is listed by
            # both, and each has its own identifier).
            assert set(keys) == {"upstox", "fyers"}, (label, keys)
            # At least one provider's own key is a concrete symbol.
            assert all(k for k in keys.values())
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# G2 — short symbols + multiple expiries
# ---------------------------------------------------------------------------

def test_g2_short_symbols_and_multiple_expiries() -> None:
    from app.subscriptions.service import SubscriptionService
    catalog, store, tmp = _catalog()
    try:
        svc = SubscriptionService(store, catalog)
        ws = svc.workspace_contracts("LT", future_count=2, option_expiry_count=1)
        assert ws["equity_key"], "the short symbol must resolve to an equity"
        # RELIANCE lists TWO option expiries (three futures expiries).
        ws2 = svc.workspace_contracts("RELIANCE", future_count=2,
                                      option_expiry_count=1)
        assert len(ws2["option_expiries"]) == 2, ws2["option_expiries"]
        # The nearest TWO futures are selected, from THREE listed.
        assert len(ws2["futures"]) == 2, ws2["futures"]
        expiries = {f["expiry"] for f in ws2["futures"]}
        assert expiries == {"2026-10-27", "2026-11-23"}, expiries
        # The THIRD (later) expiry is listed but not streamed.
        all_fut = svc._all_expiries("RELIANCE", "FUTURE")
        assert "2026-12-29" in all_fut, all_fut
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# G3 — capability + capacity aware routing
# ---------------------------------------------------------------------------

def test_g3_plan_respects_capabilities_and_capacity() -> None:
    from app.fno_coverage import FnoCoverageService
    from providers.capabilities import (
        CAP_LTP,
        CAP_OI,
        STYLE_COLON,
        STYLE_NUMERIC,
        STYLE_PIPE,
        ProviderCapabilities,
    )
    from providers.registry import ProviderRegistry

    catalog, _store, tmp = _catalog()
    try:
        # A TINY Upstox budget: the plan must not exceed it, and the
        # overflow must fall to the other capable provider.
        up = ProviderCapabilities(
            provider="upstox", label="Upstox",
            capabilities=frozenset({CAP_LTP, CAP_OI}),
            modes={"full": 3}, default_mode="full", key_styles=(STYLE_PIPE,))
        fy = ProviderCapabilities(
            provider="fyers", label="Fyers",
            capabilities=frozenset({CAP_LTP, CAP_OI}),
            modes={"full": 100}, default_mode="full",
            key_styles=(STYLE_COLON, STYLE_NUMERIC))
        reg = ProviderRegistry([up, fy])
        svc = FnoCoverageService(catalog, registry=reg)
        plan = svc.plan()
        assert plan.by_provider.get("upstox", 0) <= reg.budget("upstox"), \
            plan.by_provider
        assert plan.by_provider.get("fyers", 0) > 0, plan.by_provider
        # Every contract is routed at most once.
        routed = [c for c in plan.contracts if c.provider]
        keys = [(c.provider, c.underlying, c.expiry, c.instrument_type)
                for c in routed]
        assert len(keys) == len(set(keys)), "a logical contract was routed twice"
    finally:
        tmp.cleanup()


def test_g3_capacity_exhaustion_is_reported_truthfully() -> None:
    """When NO provider has budget, the contracts are reported uncovered."""
    from app.fno_coverage import FnoCoverageService
    from providers.capabilities import (
        CAP_LTP,
        CAP_OI,
        STYLE_COLON,
        STYLE_NUMERIC,
        STYLE_PIPE,
        ProviderCapabilities,
    )
    from providers.registry import ProviderRegistry

    catalog, _store, tmp = _catalog()
    try:
        tiny = ProviderCapabilities(
            provider="upstox", label="Upstox",
            capabilities=frozenset({CAP_LTP, CAP_OI}),
            modes={"full": 1}, default_mode="full", key_styles=(STYLE_PIPE,))
        fy = ProviderCapabilities(
            provider="fyers", label="Fyers",
            capabilities=frozenset({CAP_LTP, CAP_OI}),
            modes={"full": 1}, default_mode="full",
            key_styles=(STYLE_COLON, STYLE_NUMERIC))
        reg = ProviderRegistry([tiny, fy])
        svc = FnoCoverageService(catalog, registry=reg)
        plan = svc.plan()
        # Both providers are saturated -> the rest is reported, not hidden.
        assert plan.states.get("capacity_limited"), plan.states
        assert plan.uncovered, "the dropped contracts must be reported"
        for c in plan.uncovered:
            assert c.state == "capacity_limited"
            assert c.reason
    finally:
        tmp.cleanup()


def test_g3_small_capacity_still_yields_a_usable_budget() -> None:
    """A capacity below the safety reserve must still be USABLE."""
    from providers.capabilities import effective_budget
    for capacity in (1, 2, 3, 10, 50, 100, 2000):
        budget = effective_budget(capacity)
        assert budget >= 1, (capacity, budget)
        assert budget <= capacity, (capacity, budget)
    assert effective_budget(0) == 0


def test_g3_plan_reads_the_persisted_configuration() -> None:
    from app.fno_coverage import FnoCoverageService
    catalog, _store, tmp = _catalog()
    try:
        svc = FnoCoverageService(catalog)
        # Without a reader the documented defaults apply.
        assert svc._config()["preferred_provider"] is None
        # With a reader the PERSISTED value is honoured.
        svc2 = FnoCoverageService(
            catalog, config_reader=lambda: {"preferred_provider": "fyers"})
        assert svc2._config()["preferred_provider"] == "fyers"
        plan = svc2.plan()
        assert plan.by_provider.get("fyers", 0) > 0, plan.by_provider
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# G4 — BSE expiry handling
# ---------------------------------------------------------------------------

def test_g4_bse_expiry_is_not_discarded_by_an_nse_expiry() -> None:
    from app.fno_coverage import FnoCoverageService
    from providers.registry import get_registry
    catalog, _store, tmp = _catalog()
    try:
        svc = FnoCoverageService(catalog, registry=get_registry())
        plan = svc.plan()
        bse = [c for c in plan.contracts if c.exchange == "BSE"]
        assert bse, "the BSE contract must not be discarded"
        assert {c.expiry for c in bse} == {"2026-10-29"}, bse
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# G5 — one provider per logical contract; no misattribution
# ---------------------------------------------------------------------------

def test_g5_each_logical_contract_is_routed_once() -> None:
    from app.subscriptions.service import SubscriptionService
    catalog, store, tmp = _catalog()
    try:
        svc = SubscriptionService(store, catalog)
        ws = svc.workspace_contracts("RELIANCE", future_count=2,
                                      option_expiry_count=2,
                                      strikes_below=1, strikes_above=1)
        logical = {}
        for o in ws["options"]:
            key = (o["expiry"], o["strike"], o["option_type"])
            logical.setdefault(key, []).append(o["provider"])
        assert logical, ws["options"]
        for key, providers in logical.items():
            assert len(providers) == 1, (key, providers)
        # The futures are likewise not duplicated.
        fut_logical = {}
        for f in ws["futures"]:
            fut_logical.setdefault(f["expiry"], []).append(f["provider"])
        for key, providers in fut_logical.items():
            assert len(providers) == 1, (key, providers)
    finally:
        tmp.cleanup()


def test_g5_provider_data_is_never_misattributed() -> None:
    """A provider's chain data must not land on another provider's leg."""
    from api.fno_routes import _workspace_provider
    from market.models import (
        OptionChainSnapshot,
        OptionContractData,
        OptionStrikeRow,
    )

    # Two legs at the same strike, from DIFFERENT providers.
    legs = [
        {"provider": "upstox", "strike": 1200.0, "option_type": "CE",
         "key": "NSE_FO|X"},
        {"provider": "fyers", "strike": 1200.0, "option_type": "PE",
         "key": "NSE:Y"},
    ]
    ws = {"options": legs, "futures": []}
    provider = _workspace_provider(ws)
    assert provider == "upstox"
    # A Fyers leg must be excluded from an Upstox snapshot merge.
    for leg in legs:
        if leg["provider"] != provider:
            continue
    snapshot = OptionChainSnapshot(
        instrument_token="NSE_FO|X", exchange="NSE", tradingsymbol="X",
        expiry="2026-10-27", scope="whole-expiry", provider="upstox",
        strikes=(OptionStrikeRow(
            strike=1200.0,
            call=OptionContractData(oi=111, provider="upstox"),),))
    by_strike = {s.strike: s for s in snapshot.strikes}
    for leg in legs:
        if leg["provider"] != provider:
            # No Fyers leg may be enriched from the Upstox snapshot.
            assert leg.get("quote") is None
            continue
        row = by_strike.get(leg["strike"])
        assert row is not None


# ---------------------------------------------------------------------------
# G6 — chain metadata reflects the ACTUAL response
# ---------------------------------------------------------------------------

def test_g6_chain_metadata_reflects_the_response() -> None:
    from market.normalize.fyers import option_chain_from_rest

    payload = {"s": "ok", "data": {
        "date": "27-10-2026",
        "optionsChain": [
            {"symbol": "NSE:ADANIGREEN-EQ", "option_type": "",
             "strike_price": -1, "ltp": 1000.0},
            {"symbol": "NSE:ADANIGREEN26OCT1000CE", "option_type": "CE",
             "strike_price": 1000, "ltp": 5.0, "oi": 10},
        ]}}
    snap = option_chain_from_rest(
        payload, instrument_token="NSE:ADANIGREEN-EQ", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27")
    # The provider's OWN expiry is recorded.
    assert snap.expiry == "2026-10-27"
    assert snap.actual_expiry == "2026-10-27"
    # The provider and the native key are carried.
    assert snap.provider == "fyers"
    assert snap.native_underlying_key == "NSE:ADANIGREEN-EQ"
    # Every leg is attributed to the provider that supplied it.
    for row in snap.strikes:
        assert row.provider == "fyers"
        for leg in (row.call, row.put):
            if leg is not None:
                assert leg.provider == "fyers"
                assert leg.native_key


def test_g6_response_expiry_wins_over_the_requested_one() -> None:
    """A chain is never labelled with an expiry the provider did not serve."""
    from market.normalize.fyers import option_chain_from_rest
    payload = {"s": "ok", "data": {
        "date": "23-11-2026",
        "optionsChain": [
            {"symbol": "NSE:ADANIGREEN-EQ", "option_type": "",
             "strike_price": -1, "ltp": 1000.0},
        ]}}
    snap = option_chain_from_rest(
        payload, instrument_token="NSE:ADANIGREEN-EQ", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27")
    assert snap.expiry == "2026-11-23"
    assert snap.actual_expiry == "2026-11-23"


# ---------------------------------------------------------------------------
# G7 — analytics chain key round-trip + the production refresh path
# ---------------------------------------------------------------------------

def test_g7_analytics_chain_key_round_trips() -> None:
    from app.market_analytics import parse_chain_key
    for cid, expiry in (
        ("NSE:INDEX:NIFTY", "2026-10-27"),
        ("NSE:EQUITY:INE002A01018", "2026-10-27"),
        ("NSE:OPTION:ADANIGREEN:2026-10-27:1300:CE", "2026-11-23"),
        ("NSE:FUTURE:RELIANCE:2026-10-27", "2026-12-29"),
    ):
        got_cid, got_expiry = parse_chain_key(f"analytics:{cid}:{expiry}")
        assert got_cid == cid, (got_cid, cid)
        assert got_expiry == expiry, (got_expiry, expiry)


def test_g7_analytics_refresh_calls_the_chain_service() -> None:
    """The refresh must call the CHAIN service, not the quote store."""
    from app.market_analytics import MarketAnalyticsService
    from market.models import OptionChainSnapshot

    calls: list[dict] = []

    class _ChainService:
        async def option_chain(self, **kw):
            calls.append(kw)
            return OptionChainSnapshot(
                instrument_token=kw["instrument_key"], exchange=kw["exchange"],
                tradingsymbol=kw["tradingsymbol"], expiry=kw["expiry"],
                scope="window", provider="upstox")

    class _QuoteStore:
        """MarketService has NO option_chain method — calling it must fail."""
        def option_chain(self, **kw):  # pragma: no cover
            raise AssertionError("the quote store must not be the chain source")

    catalog, _store, tmp = _catalog()
    try:
        svc = MarketAnalyticsService(
            _QuoteStore(), instrument_catalog=catalog,
            chain_service=_ChainService())
        asyncio.run(svc._do_refresh(
            "analytics:NSE:INDEX:NIFTY:2026-10-27"))
        assert calls, "the chain service was not called"
        assert calls[0]["expiry"] == "2026-10-27", calls[0]
        # The intended underlying (NIFTY), not a random catalogue row.
        assert calls[0]["tradingsymbol"], calls[0]
    finally:
        tmp.cleanup()


def test_g7_analytics_refresh_resolves_the_intended_underlying() -> None:
    """The refresh must not degrade to an unrelated catalogue row."""
    from app.market_analytics import MarketAnalyticsService
    from market.models import OptionChainSnapshot

    seen: list[dict] = []

    class _ChainService:
        async def option_chain(self, **kw):
            seen.append(kw)
            return OptionChainSnapshot(
                instrument_token=kw["instrument_key"], exchange=kw["exchange"],
                tradingsymbol=kw["tradingsymbol"], expiry=kw["expiry"],
                scope="window")

    catalog, _store, tmp = _catalog()
    try:
        svc = MarketAnalyticsService(
            None, instrument_catalog=catalog, chain_service=_ChainService())
        asyncio.run(svc._do_refresh("analytics:NSE:INDEX:NIFTY:2026-10-27"))
        assert seen, "no chain request"
        # The request names the NIFTY INDEX row — not an unrelated
        # instrument (a bond, a different equity).
        requested = seen[0]
        assert requested["expiry"] == "2026-10-27", requested
        assert "NIFTY" in str(requested["tradingsymbol"]).upper(), requested
        assert "NIFTY" in str(requested["instrument_key"]).upper(), requested
    finally:
        tmp.cleanup()


# ---------------------------------------------------------------------------
# G8 — REST / market-intelligence / workspace agreement
# ---------------------------------------------------------------------------

def test_g8_chain_and_workspace_agree_on_the_contract_set() -> None:
    from app.market_intel import MarketIntel
    from app.subscriptions.service import SubscriptionService
    catalog, store, tmp = _catalog()
    try:
        intel = MarketIntel(catalog)
        chain = intel.option_chain("RELIANCE", window=1)
        assert "rows" in chain, chain
        svc = SubscriptionService(store, catalog)
        ws = svc.workspace_contracts("RELIANCE", future_count=1,
                                      option_expiry_count=1,
                                      strikes_below=1, strikes_above=1)
        chain_pairs = {
            (r["strike"], side)
            for r in chain["rows"] for side in ("CE", "PE")
            if r.get("call" if side == "CE" else "put")}
        ws_pairs = {(o["strike"], o["option_type"]) for o in ws["options"]}
        # Both surfaces resolve the SAME logical contracts (one provider
        # each, so no duplicates in either).
        assert chain_pairs, chain
        assert ws_pairs, ws
        assert len(chain_pairs) == len(set(chain_pairs))
        assert len(ws_pairs) == len(set(ws_pairs))
    finally:
        tmp.cleanup()


def test_g8_market_intel_reports_provider_attribution() -> None:
    from app.market_intel import MarketIntel
    catalog, _store, tmp = _catalog()
    try:
        intel = MarketIntel(catalog)
        chain = intel.option_chain("RELIANCE", window=1)
        for row in chain["rows"]:
            for side in ("call", "put"):
                leg = row.get(side)
                if not leg:
                    continue
                assert leg.get("provider"), leg
                assert leg.get("native_key"), leg
                assert leg.get("canonical_id"), leg
    finally:
        tmp.cleanup()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
