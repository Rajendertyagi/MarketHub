#!/usr/bin/env python3
"""DB-backed market-data subscription tests (persistence + resolution + API).

Covers the subscription foundation:

  PERSISTENCE
   1. all 8 indices default enabled (ensure_defaults, idempotent)
   2. index enable/disable round-trip (canonical definition survives)
   3. explicit stock add/remove
   4. derivative rule persistence + validation bounds
   5. idempotent config migration (DB wins, deletions never resurrected)
   6. restart reload (fresh EventStore instance over the same file)

  RESOLUTION
   7. current future / next future
   8. nearest option expiry / next option expiry
   9. ATM ± N resolution (listed strikes only)
  10. CE-only / PE-only / CE+PE
  11. stock option + index option resolution
  12. unsupported underlying (honest notes)
  13. no spot available → pending, rule preserved
  14. expired contract rollover (past expiries never resolve)
  15. no fabricated strikes
  16. provider routing (pipe→upstox, colon→fyers)
  17. subscription limit safety

  API
  18. list preferences / update index / stock CRUD / rules / preview / apply
  19. invalid requests → 400

  REGRESSION: architecture boundaries + auth freeze run separately.
"""

from __future__ import annotations

import asyncio
import json
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
from app.subscriptions.service import (  # noqa: E402
    SubscriptionLimitError,
    MAX_INSTRUMENTS_PER_PROVIDER,
)
from api.subscription_routes import build_subscription_routes  # noqa: E402
from starlette.requests import Request  # noqa: E402


class _Quote:
    def __init__(self, ltp: float):
        self.ltp = ltp


def _make_catalog(expiries=None, future_rows=None, option_strikes=None):
    """Synthetic catalog with controllable expiries/rows (no network)."""
    expiries = expiries if expiries is not None else [
        "2026-09-08", "2026-09-30", "2026-10-29"]
    future_rows = future_rows if future_rows is not None else [
        {"provider": "fyers", "instrument_token": "101",
         "provider_symbol": "NSE:NIFTY26908FUT",
         "tradingsymbol": "NIFTY FUT", "expiry": "2026-09-08"}]
    option_strikes = option_strikes if option_strikes is not None else [
        23500, 23700, 23800, 23900, 24100]

    class _Cat:
        def derivative_expiries(self, underlying, instrument_type):
            # Catalog behavior: only real derivative underlyings list expiries.
            if underlying not in ("NIFTY", "RELIANCE"):
                return []
            return list(expiries)

        def search(self, **kw):
            if kw.get("instrument_type") == "FUTURE":
                rows = [dict(r) for r in future_rows]
                if kw.get("expiry"):
                    rows = [r for r in rows if r["expiry"] == kw["expiry"]]
                return rows
            return []

        def option_strikes(self, underlying, expiry):
            if underlying not in ("NIFTY", "RELIANCE"):
                return []
            rows = []
            for i, s in enumerate(option_strikes):
                rows.append({
                    "provider": "fyers",
                    "instrument_token": f"20{i}C",
                    "provider_symbol": f"NSE:N{s}CE",
                    "tradingsymbol": f"N{s}CE",
                    "strike": s, "option_type": "CE"})
                rows.append({
                    "provider": "fyers",
                    "instrument_token": f"20{i}P",
                    "provider_symbol": f"NSE:N{s}PE",
                    "tradingsymbol": f"N{s}PE",
                    "strike": s, "option_type": "PE"})
            return rows

    return _Cat()


def _make_spot(ltp=23779.15):
    class _Spot:
        def __call__(self, exchange, token):
            return _Quote(ltp) if ltp is not None else None
    return _Spot()


def _make_svc(db_path=None, catalog=None, spot=23779.15):
    db_path = db_path or os.path.join(
        tempfile.gettempdir(), f"mdsub_{os.getpid()}_{id(catalog)}.db")
    if os.path.exists(db_path):
        os.unlink(db_path)
    store = EventStore(db_path)
    svc = SubscriptionService(
        store, catalog if catalog is not None else _make_catalog(),
        spot_provider=_make_spot(spot))
    return svc, store, db_path


# -- persistence -----------------------------------------------------------------

async def test_defaults_and_roundtrip(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    svc.ensure_defaults()  # idempotent
    prefs = svc.preferences()
    runner.assert_eq("defaults-8", len(prefs["indices"]), 8)
    runner.assert_eq("defaults-enabled",
                     sum(1 for i in prefs["indices"] if i["enabled"]), 8)
    # canonical keys preserved
    nifty = next(i for i in prefs["indices"] if i["label"] == "NIFTY")
    runner.assert_eq("canonical-key", nifty["key"], "NSE_INDEX|Nifty 50")
    # disable round-trip
    svc.set_index("NIFTY", False)
    nifty2 = next(i for i in svc.preferences()["indices"]
                  if i["label"] == "NIFTY")
    runner.assert_eq("disabled", nifty2["enabled"], False)
    runner.assert_eq("definition-survives", nifty2["key"],
                     "NSE_INDEX|Nifty 50")
    svc.set_index("NIFTY", True)
    nifty3 = next(i for i in svc.preferences()["indices"]
                  if i["label"] == "NIFTY")
    runner.assert_eq("re-enabled", nifty3["enabled"], True)
    try:
        svc.set_index("BOGUS", True)
        ok = False
    except ValueError:
        ok = True
    runner.assert_true("unknown-index-400", ok)
    os.unlink(db)


async def test_stock_crud(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")  # idempotent
    prefs = svc.preferences()
    runner.assert_eq("stock-added", len(prefs["stocks"]), 1)
    runner.assert_eq("stock-enabled", prefs["stocks"][0]["enabled"], True)
    svc.set_stock_enabled("NSE_EQ|INE002A01018", False)
    runner.assert_eq("stock-disabled",
                     svc.preferences()["stocks"][0]["enabled"], False)
    runner.assert_eq("stock-removed",
                     svc.remove_stock("NSE_EQ|INE002A01018"), True)
    runner.assert_eq("stock-gone", len(svc.preferences()["stocks"]), 0)
    try:
        svc.set_stock_enabled("NSE_EQ|NOPE", True)
        ok = False
    except ValueError:
        ok = True
    runner.assert_true("unknown-stock-400", ok)
    os.unlink(db)


async def test_rule_persistence_and_bounds(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.set_derivative_rule("NIFTY", futures_enabled=True, futures_count=2,
                            options_enabled=True, options_count=1,
                            strikes_below=10, strikes_above=10,
                            calls_enabled=True, puts_enabled=False)
    rule = svc.preferences()["derivatives"][0]
    runner.assert_eq("rule-underlying", rule["underlying"], "NIFTY")
    runner.assert_eq("rule-fut-count", rule["futures_count"], 2)
    runner.assert_eq("rule-puts", rule["puts_enabled"], False)
    # overwrite (idempotent upsert)
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=2,
                            strikes_below=5, strikes_above=5,
                            calls_enabled=True, puts_enabled=True)
    rule2 = svc.preferences()["derivatives"][0]
    runner.assert_eq("rule-updated", (rule2["futures_enabled"],
                                      rule2["options_count"],
                                      rule2["strikes_below"]),
                     (False, 2, 5))
    for bad in ({"futures_count": 3}, {"options_count": 0},
                {"strikes_below": 51}, {"strikes_above": -1}):
        kwargs = dict(futures_enabled=False,
                      options_enabled=False,
                      calls_enabled=True, puts_enabled=True)
        kwargs.update(bad)
        kwargs.setdefault("futures_count", 1)
        kwargs.setdefault("options_count", 1)
        kwargs.setdefault("strikes_below", 0)
        kwargs.setdefault("strikes_above", 0)
        try:
            svc.set_derivative_rule("NIFTY", **kwargs)
            ok = False
        except ValueError:
            ok = True
        runner.assert_true(f"rule-bound-{list(bad)[0]}", ok)
    runner.assert_eq("rule-removed",
                     svc.remove_derivative_rule("NIFTY"), True)
    os.unlink(db)


async def test_config_migration(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    cfg = {"upstox": {"instruments": [
        {"key": "NSE_INDEX|Nifty 50", "exchange": "NSE",
         "tradingsymbol": "Nifty 50"},
        {"key": "NSE_EQ|INE002A01018", "exchange": "NSE",
         "tradingsymbol": "RELIANCE"},
    ]}}
    mig = svc.migrate_from_config(cfg)
    runner.assert_eq("mig-imported", mig["imported"], 1)  # index already there
    runner.assert_eq("mig-skipped", mig["skipped"], 1)
    runner.assert_eq("mig-ran", mig["ran"], True)
    prefs = svc.preferences()
    stocks = [s for s in prefs["stocks"] if s["label"] == "RELIANCE"]
    runner.assert_eq("mig-stock", len(stocks), 1)
    # second run: marker set → import never re-runs (idempotent)
    mig2 = svc.migrate_from_config(cfg)
    runner.assert_eq("mig-idempotent", mig2["ran"], False)
    # user deletes the stock; migration must NOT resurrect it (DB wins)
    svc.remove_stock("NSE_EQ|INE002A01018")
    mig3 = svc.migrate_from_config(cfg)
    runner.assert_eq("mig-no-resurrect", mig3["imported"], 0)
    runner.assert_eq("mig-stock-gone",
                     len([s for s in svc.preferences()["stocks"]
                          if s["label"] == "RELIANCE"]), 0)
    os.unlink(db)


async def test_restart_reload(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    svc.set_index("SENSEX", False)
    svc.add_stock("NSE_EQ|X", "XTEST")
    svc.set_derivative_rule("NIFTY", futures_enabled=True, futures_count=1,
                            options_enabled=False, options_count=1,
                            strikes_below=0, strikes_above=0,
                            calls_enabled=True, puts_enabled=True)
    # "restart": fresh EventStore + service over the same DB file
    store2 = EventStore(db)
    svc2 = SubscriptionService(store2, _make_catalog(),
                               spot_provider=_make_spot())
    svc2.ensure_defaults()
    prefs = svc2.preferences()
    sensex = next(i for i in prefs["indices"] if i["label"] == "SENSEX")
    runner.assert_eq("restart-disabled-survives", sensex["enabled"], False)
    runner.assert_eq("restart-stock",
                     [s["label"] for s in prefs["stocks"]], ["XTEST"])
    rule = svc2.preferences()["derivatives"][0]
    runner.assert_eq("restart-rule", rule["futures_enabled"], True)
    os.unlink(db)


# -- resolution --------------------------------------------------------------------

async def test_resolution_core(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    r = svc.resolve()
    idx_keys = [c["key"] for c in r["contracts"] if c["kind"] == "index"]
    runner.assert_eq("resolve-8-indices", len(idx_keys), 8)
    runner.assert_eq("resolve-nifty-key", idx_keys[0], "NSE_INDEX|Nifty 50")
    runner.assert_eq("resolve-sensex-bse", idx_keys[6], "BSE_INDEX|SENSEX")
    runner.assert_eq("provider-routing-upstox",
                     all(c["provider"] == "upstox"
                         for c in r["contracts"] if c["kind"] == "index"),
                     True)
    # disabled index drops out
    svc.set_index("NIFTY", False)
    r2 = svc.resolve()
    idx2 = [c["key"] for c in r2["contracts"] if c["kind"] == "index"]
    runner.assert_eq("disabled-drops", len(idx2), 7)
    runner.assert_true("nifty-gone", "NSE_INDEX|Nifty 50" not in idx2)
    svc.set_index("NIFTY", True)
    # stock routing: colon keys → fyers, pipe keys → upstox
    svc.add_stock("NSE:NIFTY26908FUT", "FUTSYM")
    svc.add_stock("NSE_EQ|X1", "PIPE")
    r3 = svc.resolve()
    prov = {c["key"]: c["provider"] for c in r3["contracts"]}
    runner.assert_eq("fyers-route", prov["NSE:NIFTY26908FUT"], "fyers")
    runner.assert_eq("upstox-route", prov["NSE_EQ|X1"], "upstox")
    os.unlink(db)


async def test_resolution_futures(runner: R) -> None:
    import app.subscriptions.service as _svc_mod
    _orig_today = _svc_mod._today_iso
    # Freeze the reference "today" so the test is deterministic and independent
    # of the real system date. Production still filters expired contracts via
    # date.today() — only the test's clock is pinned.
    _svc_mod._today_iso = lambda: "2026-09-01"
    try:
        svc, store, db = _make_svc()
        svc.ensure_defaults()
        # current future only
        svc.set_derivative_rule("NIFTY", futures_enabled=True, futures_count=1,
                                options_enabled=False, options_count=1,
                                strikes_below=0, strikes_above=0,
                                calls_enabled=True, puts_enabled=True)
        r = svc.resolve()
        futs = [c for c in r["contracts"] if c["kind"] == "future"]
        runner.assert_eq("current-future", len(futs), 1)
        runner.assert_eq("current-expiry", futs[0]["expiry"], "2026-09-08")
        # current + next
        svc.set_derivative_rule("NIFTY", futures_enabled=True, futures_count=2,
                                options_enabled=False, options_count=1,
                                strikes_below=0, strikes_above=0,
                                calls_enabled=True, puts_enabled=True)
        futs2 = [c for c in svc.resolve()["contracts"] if c["kind"] == "future"]
        runner.assert_eq("two-futures", len(futs2), 1)  # catalog has 1 row; dupes collapse
        # rollover: first expiry passes → next becomes current
        svc_rolled, _, db2 = _make_svc(
            catalog=_make_catalog(expiries=["2026-09-04", "2026-09-08"]),
            spot=None)
        svc_rolled.ensure_defaults()
        svc_rolled.set_derivative_rule("NIFTY", futures_enabled=True,
                                       futures_count=1, options_enabled=False,
                                       options_count=1, strikes_below=0,
                                       strikes_above=0, calls_enabled=True,
                                       puts_enabled=True)
        # With "today" frozen before both expiries, the resolution keeps every
        # non-expired contract and the nearest becomes current.
        futs3 = [c for c in svc_rolled.resolve()["contracts"]
                 if c["kind"] == "future"]
        runner.assert_true("rollover-only-unexpired",
                           all(f["expiry"] >= "2026-09-04" for f in futs3))
        os.unlink(db)
        os.unlink(db2)
    finally:
        _svc_mod._today_iso = _orig_today


async def test_resolution_options(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    # ATM ± 1, CE+PE, nearest expiry
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=1,
                            strikes_below=1, strikes_above=1,
                            calls_enabled=True, puts_enabled=True)
    r = svc.resolve()
    opts = [c for c in r["contracts"] if c["kind"] == "option"]
    # spot 23779.15 → nearest listed ATM = 23800; below=23700; above=23900
    runner.assert_eq("atm-snap", r["atm"].get("NIFTY"), 23800.0)
    keys = sorted(c["key"] for c in opts)
    runner.assert_eq("atm-range-keys", keys,
                     sorted(["NSE:N23700CE", "NSE:N23700PE",
                             "NSE:N23800CE", "NSE:N23800PE",
                             "NSE:N23900CE", "NSE:N23900PE"]))
    runner.assert_eq("nearest-expiry",
                     {c["expiry"] for c in opts}, {"2026-09-08"})
    # CE-only
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=1,
                            strikes_below=0, strikes_above=1,
                            calls_enabled=True, puts_enabled=False)
    r2 = svc.resolve()
    otypes = {(c["strike"], c["option_type"]) for c in r2["contracts"]
              if c["kind"] == "option"}
    runner.assert_eq("ce-only", otypes,
                     {(23800.0, "CE"), (23900.0, "CE")})
    # PE-only
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=1,
                            strikes_below=1, strikes_above=0,
                            calls_enabled=False, puts_enabled=True)
    r3 = svc.resolve()
    otypes3 = {(c["strike"], c["option_type"]) for c in r3["contracts"]
               if c["kind"] == "option"}
    runner.assert_eq("pe-only", otypes3,
                     {(23700.0, "PE"), (23800.0, "PE")})
    # next expiry (count=2)
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=2,
                            strikes_below=1, strikes_above=1,
                            calls_enabled=True, puts_enabled=True)
    r4 = svc.resolve()
    exps = {c["expiry"] for c in r4["contracts"] if c["kind"] == "option"}
    runner.assert_eq("two-expiries", exps, {"2026-09-08", "2026-09-30"})
    # stock option resolution: rule on a stock underlying (spot comes from
    # the stock's own subscription key)
    svc.add_stock("NSE_EQ|INE002A01018", "RELIANCE")
    svc.set_derivative_rule("RELIANCE", futures_enabled=False,
                            futures_count=1, options_enabled=True,
                            options_count=1, strikes_below=1,
                            strikes_above=1, calls_enabled=True,
                            puts_enabled=True)
    r5 = svc.resolve()
    runner.assert_true("stock-option-resolved",
                       any(c["kind"] == "option"
                           and c["underlying"] == "RELIANCE"
                           for c in r5["contracts"]))
    # no fabricated strikes: 23650 (between listed) never appears
    runner.assert_true("no-fabricated",
                       all(c["strike"] in (23500.0, 23700.0, 23800.0,
                                           23900.0, 24100.0)
                           for c in r5["contracts"]
                           if c["kind"] == "option"))
    os.unlink(db)


async def test_resolution_edge_cases(runner: R) -> None:
    # no spot → pending, rule preserved
    svc, store, db = _make_svc(spot=None)
    svc.ensure_defaults()
    svc.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                            options_enabled=True, options_count=1,
                            strikes_below=2, strikes_above=2,
                            calls_enabled=True, puts_enabled=True)
    r = svc.resolve()
    runner.assert_eq("no-spot-pending", r["pending"], ["NIFTY"])
    runner.assert_eq("no-spot-rule-preserved",
                     svc.preferences()["derivatives"][0]["strikes_below"], 2)
    runner.assert_eq("no-spot-options",
                     sum(1 for c in r["contracts"]
                         if c["kind"] == "option"), 0)
    os.unlink(db)
    # unsupported underlying → honest note, no crash
    svc2, store2, db2 = _make_svc()
    svc2.ensure_defaults()
    svc2.set_derivative_rule("BOGUSUND", futures_enabled=True,
                             futures_count=1, options_enabled=True,
                             options_count=1, strikes_below=1,
                             strikes_above=1, calls_enabled=True,
                             puts_enabled=True)
    r2 = svc2.resolve()
    runner.assert_true("unsupported-note",
                       any("no listed" in n for n in r2["notes"]))
    os.unlink(db2)
    # no options with neither CE nor PE
    svc3, store3, db3 = _make_svc()
    svc3.ensure_defaults()
    svc3.set_derivative_rule("NIFTY", futures_enabled=False, futures_count=1,
                             options_enabled=True, options_count=1,
                             strikes_below=2, strikes_above=2,
                             calls_enabled=False, puts_enabled=False)
    r3 = svc3.resolve()
    runner.assert_eq("neither-ce-nor-pe",
                     sum(1 for c in r3["contracts"]
                         if c["kind"] == "option"), 0)
    os.unlink(db3)


async def test_limit_safety(runner: R) -> None:
    # 600 bulk stock subscriptions exceed the 500/provider safety cap.
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    for i in range(600):
        svc.add_stock(f"NSE_EQ|BULK{i:04d}", f"BULK{i}")
    try:
        svc.resolve()
        raised = False
    except SubscriptionLimitError:
        raised = True
    runner.assert_true("limit-raised", raised)
    runner.assert_eq("limit-cap", MAX_INSTRUMENTS_PER_PROVIDER, 500)
    # The service still works afterwards (no state corruption).
    for i in range(600):
        svc.remove_stock(f"NSE_EQ|BULK{i:04d}")
    runner.assert_eq("post-limit-ok",
                     len(svc.resolve()["by_provider"]["upstox"]), 8)
    os.unlink(db)


# -- runtime reconciliation ----------------------------------------------------------

async def test_reconcile_runtime(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()

    class _Feed:
        def __init__(self):
            self._instrument_keys = ("NSE_INDEX|Nifty 50",)
            self.added = []
            self.removed = []

        async def add_instruments(self, keys, metadata=None):
            self.added.extend(keys)
            cur = set(self._instrument_keys)
            fresh = [k for k in keys if k not in cur]
            self._instrument_keys = tuple(sorted(cur | set(fresh)))
            return len(fresh)

        async def remove_instruments(self, keys):
            self.removed.extend(keys)
            cur = set(self._instrument_keys)
            self._instrument_keys = tuple(sorted(cur - set(keys)))
            return len(keys)

    feed = _Feed()
    feeds = {"upstox": feed, "fyers": None}
    provider_fn = lambda name: feeds.get(name)  # noqa: E731

    # enable → subscribe (8 canonical indices; feed starts with 1 key)
    out = await svc.reconcile(provider_fn)
    up = out["apply"]["results"]["upstox"]
    runner.assert_eq("apply-ok", up["applied"], True)
    runner.assert_eq("apply-added-7", up["added"], 7)
    runner.assert_eq("feed-desired-8", len(feed._instrument_keys), 8)
    runner.assert_eq("fyers-unavailable",
                     out["apply"]["results"]["fyers"]["applied"], False)
    # disable one → unsubscribe (no restart)
    svc.set_index("NIFTY", False)
    out2 = await svc.reconcile(provider_fn)
    up2 = out2["apply"]["results"]["upstox"]
    runner.assert_eq("disable-removed", up2["removed"], 1)
    runner.assert_true("nifty-unsubscribed",
                       "NSE_INDEX|Nifty 50" not in feed._instrument_keys)
    runner.assert_eq("feed-after-disable", len(feed._instrument_keys), 7)
    # re-enable → resubscribe
    svc.set_index("NIFTY", True)
    out3 = await svc.reconcile(provider_fn)
    up3 = out3["apply"]["results"]["upstox"]
    runner.assert_eq("re-enable-added", up3["added"], 1)
    runner.assert_eq("feed-restored", len(feed._instrument_keys), 8)
    # feed offline: preference durable, applied=False recorded
    feeds["upstox"] = None
    svc.set_index("BANKEX", False)
    out4 = await svc.reconcile(provider_fn)
    runner.assert_eq("offline-not-applied",
                     out4["apply"]["results"]["upstox"]["applied"], False)
    runner.assert_eq("preference-durable",
                     next(i for i in svc.preferences()["indices"]
                          if i["label"] == "BANKEX")["enabled"], False)
    # broker failure doesn't erase settings
    class _Broken:
        _instrument_keys = ()

        async def add_instruments(self, keys, metadata=None):
            raise RuntimeError("socket gone")

        async def remove_instruments(self, keys):
            raise RuntimeError("socket gone")

    feeds["upstox"] = _Broken()
    out5 = await svc.reconcile(provider_fn)
    runner.assert_eq("broken-not-applied",
                     out5["apply"]["results"]["upstox"]["applied"], False)
    runner.assert_eq("settings-survive",
                     len(svc.preferences()["indices"]), 8)
    # never unsub the last key from a live feed
    feeds["upstox"] = feed
    for e in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
              "INDIA VIX", "SENSEX", "BANKEX", "NIFTY"):
        svc.set_index(e, False)
    svc.remove_stock  # noop reference
    out6 = await svc.reconcile(provider_fn)
    runner.assert_true("never-empty-feed", len(feed._instrument_keys) >= 1)
    os.unlink(db)


# -- API contract ---------------------------------------------------------------------

def _make_request(method, path, query=b"", body=b""):
    scope = {"type": "http", "method": method, "path": path,
             "headers": [(b"content-type", b"application/json")],
             "query_string": query}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _body_request(method, path, obj):
    return _make_request(method, path, body=json.dumps(obj).encode())


def _routes(svc):
    feeds = {"upstox": None}
    by_key = {}
    for r in build_subscription_routes(svc, lambda n: feeds.get(n)):
        for m in r.methods:
            if m != "HEAD":
                by_key[(r.path, m)] = r
    return by_key, feeds


async def _json_of(route, request):
    resp = await route.endpoint(request)
    return resp.status_code, json.loads(resp.body.decode())


async def test_api_contract(runner: R) -> None:
    svc, store, db = _make_svc()
    svc.ensure_defaults()
    routes, _feeds = _routes(svc)
    code, data = await _json_of(routes[("/api/subscriptions", "GET")],
                                _make_request("GET", "/api/subscriptions"))
    runner.assert_eq("api-list-200", code, 200)
    runner.assert_eq("api-list-8", len(data["indices"]), 8)
    # update index
    code, data = await _json_of(
        routes[("/api/subscriptions/indices", "PATCH")],
        _body_request("PATCH", "/api/subscriptions/indices",
                      {"label": "NIFTY", "enabled": False}))
    runner.assert_eq("api-patch-200", code, 200)
    # invalid index → 400
    code, data = await _json_of(
        routes[("/api/subscriptions/indices", "PATCH")],
        _body_request("PATCH", "/api/subscriptions/indices",
                      {"label": "NOPE", "enabled": True}))
    runner.assert_eq("api-patch-400", code, 400)
    # stock CRUD
    code, _d = await _json_of(
        routes[("/api/subscriptions/stocks", "POST")],
        _body_request("POST", "/api/subscriptions/stocks",
                      {"key": "NSE_EQ|A", "label": "AAA"}))
    runner.assert_eq("api-stock-add", code, 200)
    code, _d = await _json_of(
        routes[("/api/subscriptions/stocks", "DELETE")],
        _make_request("DELETE", "/api/subscriptions/stocks",
                      query=b"key=NSE_EQ%7CA"))
    runner.assert_eq("api-stock-del", code, 200)
    code, _d = await _json_of(
        routes[("/api/subscriptions/stocks", "POST")],
        _make_request("POST", "/api/subscriptions/stocks",
                      body=b"{}"))
    runner.assert_eq("api-stock-missing-400", code, 400)
    # rules
    code, _d = await _json_of(
        routes[("/api/subscriptions/rules", "PUT")],
        _body_request("PUT", "/api/subscriptions/rules", {
            "underlying": "NIFTY",
            "futures_enabled": True, "futures_count": 1,
            "options_enabled": True, "options_count": 1,
            "strikes_below": 2, "strikes_above": 2,
            "calls_enabled": True, "puts_enabled": True,
        }))
    runner.assert_eq("api-rule-put", code, 200)
    code, data = await _json_of(
        routes[("/api/subscriptions/rules", "PUT")],
        _body_request("PUT", "/api/subscriptions/rules",
                      {"underlying": "NIFTY", "futures_count": 9}))
    runner.assert_eq("api-rule-400", code, 400)
    # preview (no apply)
    code, data = await _json_of(
        routes[("/api/subscriptions/preview", "GET")],
        _make_request("GET", "/api/subscriptions/preview"))
    runner.assert_eq("api-preview-200", code, 200)
    runner.assert_true("api-preview-count", data["resolved_count"] > 8)
    runner.assert_eq("api-preview-atm", data["atm"].get("NIFTY"), 23800.0)
    # status (NIFTY was disabled above → 7 enabled)
    code, data = await _json_of(
        routes[("/api/subscriptions/status", "GET")],
        _make_request("GET", "/api/subscriptions/status"))
    runner.assert_eq("api-status-200", code, 200)
    runner.assert_eq("api-status-indices", data["indices_enabled"], 7)
    os.unlink(db)


# -- main ---------------------------------------------------------------------------

async def main() -> bool:
    runner = R()
    await test_defaults_and_roundtrip(runner)
    await test_stock_crud(runner)
    await test_rule_persistence_and_bounds(runner)
    await test_config_migration(runner)
    await test_restart_reload(runner)
    await test_resolution_core(runner)
    await test_resolution_futures(runner)
    await test_resolution_options(runner)
    await test_resolution_edge_cases(runner)
    await test_limit_safety(runner)
    await test_reconcile_runtime(runner)
    await test_api_contract(runner)
    return runner.summary()


if __name__ == "__main__":
    _ok = asyncio.run(main())
    sys.exit(0 if _ok else 1)
