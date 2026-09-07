#!/usr/bin/env python3
"""Upstox NSE_FO master → catalog → subscription resolver integration.

Proves the fixed Upstox parser feeds the EXISTING subscription foundation
with correct NSE_FO derivative identities (no resolver hacks, no
cross-provider rewriting). Real master row structure (sanitized fixtures).

  1. NIFTY current future  -> NSE_FO|<token>, provider=upstox
  2. NIFTY next future     -> distinct expiry, provider=upstox
  3. nearest option expiry -> CE+PE at ATM ± N
  4. ATM ± N               -> listed strikes only (never fabricated)
  5. CE/PE mapping         -> no swap, option_type preserved
  6. stock future/option   -> RELIANCE resolves via its catalog rows
  7. Fyers rows remain Fyers (no cross-provider routing)
  8. duplicate sync is idempotent (key uniqueness stable)
  9. all-8 index regression inside resolver
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

from core.persistence.store import EventStore  # noqa: E402
from app.subscriptions import SubscriptionService  # noqa: E402
from app.instruments import (  # noqa: E402
    InstrumentCatalog,
    upstox_master_records,
)


# Real master row shape (sanitized): epoch-ms expiry, FUT/CE/PE types.
_UPSTOX_FO_ROWS = [
    {"instrument_key": "NSE_FO|68407", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "FUT",
     "trading_symbol": "NIFTY FUT 29 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1790706599000,
     "strike_price": None, "lot_size": 65, "tick_size": 10.0,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|48704", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "FUT",
     "trading_symbol": "NIFTY FUT 27 OCT 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1793125799000,
     "strike_price": None, "lot_size": 65, "tick_size": 10.0,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|42627", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "CE",
     "trading_symbol": "NIFTY 23700 CE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1788892199000,
     "strike_price": 23700.0, "lot_size": 65, "tick_size": 0.05,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|42628", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "PE",
     "trading_symbol": "NIFTY 23700 PE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1788892199000,
     "strike_price": 23700.0, "lot_size": 65, "tick_size": 0.05,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|42631", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "CE",
     "trading_symbol": "NIFTY 23800 CE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1788892199000,
     "strike_price": 23800.0, "lot_size": 65, "tick_size": 0.05,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|42632", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "PE",
     "trading_symbol": "NIFTY 23800 PE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1788892199000,
     "strike_price": 23800.0, "lot_size": 65, "tick_size": 0.05,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|42635", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "CE",
     "trading_symbol": "NIFTY 23900 CE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1788892199000,
     "strike_price": 23900.0, "lot_size": 65, "tick_size": 0.05,
     "asset_key": "NSE_INDEX|Nifty 50"},
    {"instrument_key": "NSE_FO|42636", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "PE",
     "trading_symbol": "NIFTY 23900 PE 08 SEP 26", "name": "NIFTY",
     "underlying_symbol": "NIFTY", "expiry": 1788892199000,
     "strike_price": 23900.0, "lot_size": 65, "tick_size": 0.05,
     "asset_key": "NSE_INDEX|Nifty 50"},
    # RELIANCE stock future + option (FUTSTK family -> parser maps via FUT)
    {"instrument_key": "NSE_FO|125841", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "FUT",
     "trading_symbol": "RELIANCE FUT 29 SEP 26",
     "name": "RELIANCE INDUSTRIES LTD",
     "underlying_symbol": "RELIANCE", "expiry": 1790706599000,
     "strike_price": None, "lot_size": 500, "tick_size": 10.0,
     "asset_key": "NSE_EQ|INE002A01018"},
    {"instrument_key": "NSE_FO|125870", "exchange": "NSE",
     "segment": "NSE_FO", "instrument_type": "PE",
     "trading_symbol": "RELIANCE 1500 PE 27 OCT 26",
     "name": "RELIANCE INDUSTRIES LTD",
     "underlying_symbol": "RELIANCE", "expiry": 1793125799000,
     "strike_price": 1500.0, "lot_size": 500, "tick_size": 10.0},
    # One Fyers row — must remain Fyers through the whole pipeline.
    {"provider": "fyers", "instrument_token": "101126090842631",
     "exchange": "NSE", "tradingsymbol": "NSE:NIFTY2690823800CE",
     "name": "08 Sep 26 23800 CE", "instrument_type": "OPTION",
     "segment": "11", "expiry": "2026-09-08", "strike": 23800.0,
     "option_type": "CE", "lot_size": 65, "tick_size": 0.05,
     "underlying": "NIFTY", "provider_symbol": "NSE:NIFTY2690823800CE"},
]


class _Env:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(
            os.path.join(self._tmp.name, "t.db"))
        self.catalog = InstrumentCatalog(self.store)
        # Sync the synthetic Upstox master through the REAL parser +
        # REAL transactional replace (PF-style, no network).
        records = upstox_master_records(_UPSTOX_FO_ROWS)
        self.catalog._store.replace_provider_instruments("upstox", records)
        # Fyers row enters via the same canonical replace.
        fyers_rows = [{k: v for k, v in r.items()}
                      for r in _UPSTOX_FO_ROWS if r.get("provider") == "fyers"]
        self.catalog._store.replace_provider_instruments("fyers", fyers_rows)


def _svc(env: _Env, spot=23779.15) -> SubscriptionService:
    class _Quote:
        ltp = spot

    class _Spot:
        def __call__(self, exchange, token):
            return _Quote() if spot is not None else None

    svc = SubscriptionService(env.store, env.catalog, spot_provider=_Spot())
    svc.ensure_defaults()
    return svc


def test_upstox_futures_resolution(runner: R) -> None:
    env = _Env()
    svc = _svc(env)
    svc.set_derivative_rule("NIFTY", futures_enabled=True, futures_count=2,
                            options_enabled=False, options_count=1,
                            strikes_below=0, strikes_above=0,
                            calls_enabled=True, puts_enabled=True)
    r = svc.resolve()
    futs = [c for c in r["contracts"] if c["kind"] == "future"
            and c["underlying"] == "NIFTY"]
    up_futs = [c for c in futs if c["provider"] == "upstox"]
    runner.assert_eq("fut-upstox-count", len(up_futs), 2)
    runner.assert_eq("fut-current-key", up_futs[0]["key"], "NSE_FO|68407")
    runner.assert_eq("fut-current-expiry", up_futs[0]["expiry"], "2026-09-29")
    runner.assert_eq("fut-next-key", up_futs[1]["key"], "NSE_FO|48704")
    runner.assert_eq("fut-next-expiry", up_futs[1]["expiry"], "2026-10-27")
    runner.assert_true("fut-concrete-feed-keys",
                       all(c["key"].startswith("NSE_FO|") for c in up_futs))


def test_upstox_options_resolution(runner: R) -> None:
    env = _Env()
    svc = _svc(env)
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=1,
                            strikes_below=1, strikes_above=1,
                            calls_enabled=True, puts_enabled=True)
    r = svc.resolve()
    opts = [c for c in r["contracts"] if c["kind"] == "option"
            and c["underlying"] == "NIFTY" and c["provider"] == "upstox"]
    runner.assert_eq("opt-atm-snap", r["atm"].get("NIFTY"), 23800.0)
    keys = sorted(c["key"] for c in opts)
    runner.assert_eq("opt-keys", keys,
                     sorted(["NSE_FO|42627", "NSE_FO|42628",
                             "NSE_FO|42631", "NSE_FO|42632",
                             "NSE_FO|42635", "NSE_FO|42636"]))
    ce = [c for c in opts if c["option_type"] == "CE"]
    pe = [c for c in opts if c["option_type"] == "PE"]
    runner.assert_eq("opt-ce-count", len(ce), 3)
    runner.assert_eq("opt-pe-count", len(pe), 3)
    # no swap: 23700 CE key is 42627, PE is 42628
    ce237 = next(c for c in ce if c["strike"] == 23700.0)
    pe237 = next(c for c in pe if c["strike"] == 23700.0)
    runner.assert_eq("opt-ce-key", ce237["key"], "NSE_FO|42627")
    runner.assert_eq("opt-pe-key", pe237["key"], "NSE_FO|42628")
    # no fabricated strikes: only listed values
    runner.assert_true("opt-no-fabricated",
                       all(c["strike"] in (23700.0, 23800.0, 23900.0)
                           for c in opts))


def test_stock_derivatives_resolution(runner: R) -> None:
    env = _Env()
    svc = _svc(env, spot=1500.0)  # RELIANCE spot
    svc.ensure_defaults()
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")
    svc.set_derivative_rule("RELIANCE", futures_enabled=True,
                            futures_count=1, options_enabled=True,
                            options_count=1, strikes_below=0,
                            strikes_above=0, calls_enabled=True,
                            puts_enabled=True)
    r = svc.resolve()
    futs = [c for c in r["contracts"] if c["kind"] == "future"
            and c["underlying"] == "RELIANCE"]
    runner.assert_eq("stockfut-key", futs[0]["key"], "NSE_FO|125841")
    runner.assert_eq("stockfut-provider", futs[0]["provider"], "upstox")
    opts = [c for c in r["contracts"] if c["kind"] == "option"
            and c["underlying"] == "RELIANCE"]
    runner.assert_eq("stockopt-keys",
                     sorted(c["key"] for c in opts), ["NSE_FO|125870"])
    runner.assert_eq("stockopt-strike", opts[0]["strike"], 1500.0)
    runner.assert_eq("stockopt-pe", opts[0]["option_type"], "PE")


def test_cross_provider_isolation(runner: R) -> None:
    env = _Env()
    svc = _svc(env)
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=1,
                            strikes_below=1, strikes_above=1,
                            calls_enabled=True, puts_enabled=True)
    r = svc.resolve()
    by_prov = {}
    for c in r["contracts"]:
        if c["kind"] == "option" and c["strike"] == 23800.0:
            by_prov.setdefault(c["provider"], []).append(c["key"])
    # Same contract resolves under BOTH providers explicitly — never merged.
    runner.assert_true("fyers-stays-fyers",
                       any(k.startswith("NSE:") for k in
                           by_prov.get("fyers", [])))
    runner.assert_true("upstox-stays-upstox",
                       any(k.startswith("NSE_FO|") for k in
                           by_prov.get("upstox", [])))
    # No duplicated keys within one provider.
    runner.assert_eq("no-dup-keys",
                     len(by_prov["upstox"]), len(set(by_prov["upstox"])))


def test_sync_idempotent(runner: R) -> None:
    env = _Env()
    records = upstox_master_records(_UPSTOX_FO_ROWS)
    n1 = env.catalog._store.replace_provider_instruments("upstox", records)
    n2 = env.catalog._store.replace_provider_instruments("upstox", records)
    runner.assert_eq("sync-idempotent", n1, n2)
    runner.assert_eq("sync-no-dupes", n2, len(records))


def test_all8_indices_regression(runner: R) -> None:
    env = _Env()
    svc = _svc(env)
    r = svc.resolve()
    idx = [c["key"] for c in r["contracts"] if c["kind"] == "index"]
    runner.assert_eq("all8", len(idx), 8)
    runner.assert_eq("nifty-first", idx[0], "NSE_INDEX|Nifty 50")
    runner.assert_eq("sensex-bse", idx[6], "BSE_INDEX|SENSEX")


def main() -> bool:
    runner = R()
    test_upstox_futures_resolution(runner)
    test_upstox_options_resolution(runner)
    test_stock_derivatives_resolution(runner)
    test_cross_provider_isolation(runner)
    test_sync_idempotent(runner)
    test_all8_indices_regression(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = main()
    sys.exit(0 if _ok else 1)
