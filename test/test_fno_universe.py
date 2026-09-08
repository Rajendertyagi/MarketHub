#!/usr/bin/env python3
"""F&O universe + stock workspace + active-view subscription tests.

Synthetic catalog only (no network). Covers:

  UNIVERSE
   1. equity with future appears
   2. equity with options appears
   3. non-F&O equity absent
   4. index underlying NOT in stock universe
   5. expired-only derivatives -> no membership
   6. duplicate contracts don't duplicate underlying
   7. search filter
   8. limit
   9. counts + nearest expiries

  WORKSPACE
  10. identity resolution (HDFCBANK-style)
  11. spot from equity feed key (never-subscribed stock works)
  12. current + next futures
  13. option expiries + nearest selection
  14. ATM ± N window (listed strikes only)
  15. CE/PE pairing, no swap

  ACTIVE VIEW
  16. opening stock -> bounded desired set (union with persistent)
  17. switching stock -> obsolete view keys removed, persistent survive
  18. all-8 indices survive every switch
  19. window bounded (no full chain subscription)
  20. clear view -> back to persistent-only desired set
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

TODAY = "2026-09-07"


def _fo(key, itype, und, expiry, strike=None, sym=None):
    return {"instrument_key": key, "exchange": "NSE", "segment": "NSE_FO",
            "instrument_type": itype, "trading_symbol": sym or key,
            "name": und, "underlying_symbol": und, "expiry": expiry,
            "strike_price": strike, "lot_size": 65, "tick_size": 0.05}


def _eq(key, sym, name):
    return {"instrument_key": key, "exchange": "NSE", "segment": "NSE_EQ",
            "instrument_type": "EQ", "trading_symbol": sym, "name": name,
            "underlying_symbol": sym, "expiry": None, "strike_price": None,
            "lot_size": 1, "tick_size": 0.05, "isin": key.split("|")[-1]}


def _rows():
    ms = 86400000  # one day in ms
    base = 1788892199000  # 2026-09-08 end-of-day
    return [
        # HDFCBANK: F&O stock
        _eq("NSE_EQ|INE040A01034", "HDFCBANK", "HDFC Bank Ltd"),
        _fo("NSE_FO|90001", "FUT", "HDFCBANK", base + 21 * ms),
        _fo("NSE_FO|90002", "FUT", "HDFCBANK", base + 49 * ms),
        _fo("NSE_FO|90010", "CE", "HDFCBANK", base + 21 * ms, 1650.0),
        _fo("NSE_FO|90011", "PE", "HDFCBANK", base + 21 * ms, 1650.0),
        _fo("NSE_FO|90012", "CE", "HDFCBANK", base + 21 * ms, 1700.0),
        _fo("NSE_FO|90013", "PE", "HDFCBANK", base + 21 * ms, 1700.0),
        _fo("NSE_FO|90014", "CE", "HDFCBANK", base + 21 * ms, 1750.0),
        _fo("NSE_FO|90015", "PE", "HDFCBANK", base + 21 * ms, 1750.0),
        # RELIANCE: F&O stock
        _eq("NSE_EQ|INE002A01018", "RELIANCE", "RELIANCE INDUSTRIES LTD"),
        _fo("NSE_FO|91001", "FUT", "RELIANCE", base + 21 * ms),
        _fo("NSE_FO|91010", "CE", "RELIANCE", base + 21 * ms, 1500.0),
        _fo("NSE_FO|91011", "PE", "RELIANCE", base + 21 * ms, 1500.0),
        _fo("NSE_FO|91012", "CE", "RELIANCE", base + 21 * ms, 1550.0),
        _fo("NSE_FO|91013", "PE", "RELIANCE", base + 21 * ms, 1550.0),
        # SBIN: options only (no future)
        _eq("NSE_EQ|INE062A01020", "SBIN", "State Bank of India"),
        _fo("NSE_FO|92010", "CE", "SBIN", base + 21 * ms, 850.0),
        _fo("NSE_FO|92011", "PE", "SBIN", base + 21 * ms, 850.0),
        # ITDC: non-F&O equity (must NOT appear)
        _eq("NSE_EQ|INE123A01011", "ITDC", "India Tourism Dev"),
        # Expired-only derivatives under a listed equity -> no membership
        _eq("NSE_EQ|INE999A01099", "EXPIRED", "Expired Ltd"),
        _fo("NSE_FO|93010", "CE", "EXPIRED", 1700000000000, 100.0),
        # NIFTY: index derivatives exist, but NO NSE_EQ row -> excluded
        _fo("NSE_FO|42631", "CE", "NIFTY", base + ms, 23800.0),
    ]


class _Quote:
    def __init__(self, ltp):
        self.ltp = ltp


class _Spot:
    def __init__(self, ltp=1675.0):
        self._ltp = ltp

    def __call__(self, exchange, token):
        return _Quote(self._ltp)


def _env(spot=1690.0):
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "t.db"))
    catalog = InstrumentCatalog(store)
    records = upstox_master_records(_rows())
    catalog._store.replace_provider_instruments("upstox", records)
    svc = SubscriptionService(store, catalog, spot_provider=_Spot(spot))
    svc.ensure_defaults()
    return tmp, store, catalog, svc


def _contract_keys(contracts, kind, und=None):
    return sorted(c["key"] for c in contracts
                  if c["kind"] == kind and (und is None or
                                            c["underlying"] == und))


# -- universe -----------------------------------------------------------------

def test_universe(runner: R) -> None:
    tmp, store, catalog, svc = _env()
    uni = catalog.fno_universe(provider="upstox", today=TODAY)
    symbols = [r["symbol"] for r in uni]
    runner.assert_in("un-hdfc", "HDFCBANK", symbols)
    runner.assert_in("un-reliance", "RELIANCE", symbols)
    runner.assert_in("un-sbin-options-only", "SBIN", symbols)
    runner.assert_not_in("un-itdc-excluded", "ITDC", symbols)
    runner.assert_not_in("un-expired-excluded", "EXPIRED", symbols)
    runner.assert_not_in("un-index-excluded", "NIFTY", symbols)
    hdfc = next(r for r in uni if r["symbol"] == "HDFCBANK")
    runner.assert_eq("un-hdfc-fut-exp", hdfc["future_expiries"], 2)
    runner.assert_eq("un-hdfc-opt-exp", hdfc["option_expiries"], 1)
    runner.assert_eq("un-hdfc-nearest-fut", hdfc["nearest_future"],
                     "2026-09-29")
    runner.assert_eq("un-hdfc-nearest-opt", hdfc["nearest_option"],
                     "2026-09-29")
    runner.assert_eq("un-hdfc-name", hdfc["name"], "HDFC Bank Ltd")
    runner.assert_eq("un-hdfc-key", hdfc["equity_key"],
                     "NSE_EQ|INE040A01034")
    # search filter
    uni2 = catalog.fno_universe(provider="upstox", today=TODAY, q="hdfc")
    runner.assert_eq("un-search", [r["symbol"] for r in uni2], ["HDFCBANK"])
    # limit
    uni3 = catalog.fno_universe(provider="upstox", today=TODAY, limit=1)
    runner.assert_eq("un-limit", len(uni3), 1)
    tmp.cleanup()


# -- workspace ------------------------------------------------------------------

def test_workspace(runner: R) -> None:
    tmp, store, catalog, svc = _env()
    ws = svc.workspace_contracts("HDFCBANK")
    runner.assert_eq("ws-identity", ws["equity_key"], "NSE_EQ|INE040A01034")
    futs = ws["futures"]
    runner.assert_eq("ws-fut-count", len(futs), 2)
    runner.assert_eq("ws-fut1", futs[0]["key"], "NSE_FO|90001")
    runner.assert_eq("ws-fut1-exp", futs[0]["expiry"], "2026-09-29")
    runner.assert_eq("ws-fut2", futs[1]["key"], "NSE_FO|90002")
    runner.assert_eq("ws-opt-expiries", len(ws["option_expiries"]), 1)
    runner.assert_eq("ws-selected", ws["selected_expiry"], "2026-09-29")
    # spot 1675 -> nearest listed ATM = 1700; window ±1 => 1650,1700,1750
    runner.assert_eq("ws-atm", ws["atm"], 1700.0)
    opt_keys = _contract_keys(ws["options"], "option")
    runner.assert_eq("ws-opt-keys", opt_keys,
                     sorted(["NSE_FO|90010", "NSE_FO|90011",
                             "NSE_FO|90012", "NSE_FO|90013",
                             "NSE_FO|90014", "NSE_FO|90015"]))
    ces = [c for c in ws["options"] if c["option_type"] == "CE"]
    pes = [c for c in ws["options"] if c["option_type"] == "PE"]
    runner.assert_eq("ws-ce-no-swap", ces[0]["key"], "NSE_FO|90010")
    runner.assert_eq("ws-pe-no-swap", pes[0]["key"], "NSE_FO|90011")
    # bounded provider key set: equity + 2 futures + 6 options
    runner.assert_eq("ws-bounded", ws["by_provider"], {"upstox": 9})
    # never-subscribed stock (no md_subscriptions row) still works
    runner.assert_eq("ws-no-sub-row",
                     store.get_md_subscription(category="stock",
                                               key="NSE_EQ|INE040A01034"),
                     None)
    tmp.cleanup()


def test_workspace_window_bounds(runner: R) -> None:
    tmp, store, catalog, svc = _env()
    ws = svc.workspace_contracts("HDFCBANK", strikes_below=0,
                                 strikes_above=0)
    keys = _contract_keys(ws["options"], "option")
    runner.assert_eq("ws-window-0-atm-only", keys,
                     ["NSE_FO|90012", "NSE_FO|90013"])
    ws2 = svc.workspace_contracts("HDFCBANK", strikes_below=50,
                                  strikes_above=50)
    runner.assert_eq("ws-window-clamped",
                     len(_contract_keys(ws2["options"], "option")), 6)
    tmp.cleanup()


# -- active view -------------------------------------------------------------------

class _Feed:
    def __init__(self, initial):
        self._instrument_keys = tuple(initial)
        self.added = []
        self.removed = []

    async def add_instruments(self, keys, metadata=None):
        cur = set(self._instrument_keys)
        fresh = [k for k in keys if k not in cur]
        self.added.extend(fresh)
        self._instrument_keys = tuple(sorted(cur | set(fresh)))
        return len(fresh)

    async def remove_instruments(self, keys):
        cur = set(self._instrument_keys)
        gone = [k for k in keys if k in cur]
        if cur - set(gone):
            self.removed.extend(gone)
            self._instrument_keys = tuple(sorted(cur - set(gone)))
            return len(gone)
        return 0


async def _reconcile(svc, feed):
    return await svc.reconcile(lambda name: feed if name == "upstox" else None)


async def test_active_view(runner: R) -> None:
    tmp, store, catalog, svc = _env()
    feed = _Feed(("NSE_INDEX|Nifty 50",))
    # persistent: disable NIFTY index so the baseline set is 7 indices
    svc.set_index("NIFTY", False)
    base = _contract_keys(svc.resolve()["contracts"], "index")
    runner.assert_eq("av-base-7", len(base), 7)

    # open HDFCBANK workspace -> bounded union
    ws = svc.workspace_contracts("HDFCBANK")
    keys = ws.pop("_keys_by_provider", {})
    svc.set_active_view(keys)
    out = await _reconcile(svc, feed)
    up = out["apply"]["results"]["upstox"]
    runner.assert_eq("av-applied", up["applied"], True)
    desired = svc.resolve()["by_provider"]["upstox"]
    runner.assert_eq("av-desired-16", len(desired), 16)  # 7 + 9 view keys
    runner.assert_true("av-persistent-intact",
                       all(k in desired for k in base))

    # switch to RELIANCE -> HDFCBANK view keys removed, persistent survive
    ws2 = svc.workspace_contracts("RELIANCE")
    keys2 = ws2.pop("_keys_by_provider", {})
    svc.set_active_view(keys2)
    out2 = await _reconcile(svc, feed)
    desired2 = svc.resolve()["by_provider"]["upstox"]
    runner.assert_eq("av-switch-desired", len(desired2), 13)  # 7 + 6
    runner.assert_true("av-hdfc-dropped",
                       "NSE_FO|90001" not in desired2)
    runner.assert_true("av-reliance-added",
                       "NSE_FO|91001" in desired2)
    runner.assert_true("av-base-survives",
                       all(k in desired2 for k in base))
    runner.assert_eq("av-feed-synced",
                     len(feed._instrument_keys), len(desired2))

    # back to HDFCBANK
    ws3 = svc.workspace_contracts("HDFCBANK")
    keys3 = ws3.pop("_keys_by_provider", {})
    svc.set_active_view(keys3)
    await _reconcile(svc, feed)
    desired3 = svc.resolve()["by_provider"]["upstox"]
    runner.assert_true("av-hdfc-back", "NSE_FO|90001" in desired3)
    runner.assert_true("av-reliance-gone", "NSE_FO|91001" not in desired3)
    runner.assert_true("av-base-still", all(k in desired3 for k in base))
    runner.assert_eq("av-no-accumulation", len(desired3), 16)

    # duplicate ownership: view key == persistent key never unsubscribes
    svc.add_stock("NSE_EQ|INE040A01034", "HDFCBANK")  # persistent stock
    svc.set_active_view({})  # clear view
    out4 = await _reconcile(svc, feed)
    desired4 = svc.resolve()["by_provider"]["upstox"]
    runner.assert_true("av-dup-owner-safe",
                       "NSE_EQ|INE040A01034" in desired4)
    # closing the view leaves persistent-only desired set
    runner.assert_eq("av-cleared", len(desired4), 8)  # 7 indices + HDFCBANK
    runner.assert_eq("av-feed-matches",
                     set(feed._instrument_keys), set(desired4))
    tmp.cleanup()


def main() -> bool:
    runner = R()
    test_universe(runner)
    test_workspace(runner)
    test_workspace_window_bounds(runner)
    asyncio.run(test_active_view(runner))
    return runner.summary()


if __name__ == "__main__":
    _ok = main()
    sys.exit(0 if _ok else 1)
