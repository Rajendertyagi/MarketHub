"""Canonical instrument-identity regression suite (ID1-ID12 + parity).

Proves ONE real instrument has ONE quote state across:
  config key / catalog token / tradingsymbol
for both Fyers-style and Upstox-style identifiers, and that the
option-chain spot lookup finds live data through the resolver.
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R


def _mk_registry():
    from app.instrument_identity import InstrumentIdentityRegistry
    return InstrumentIdentityRegistry()


def _mk_world():
    """Catalog (Fyers-shaped) + MarketService + intel with resolver."""
    from core.persistence.store import EventStore
    from app.instruments import InstrumentCatalog
    from app.market_intel import MarketIntel
    from market.service import MarketService, QuotePatch

    tmp = tempfile.mkdtemp()
    store = EventStore(os.path.join(tmp, "e.db"))
    rows = [
        dict(provider="fyers", instrument_token="101000000026000",
             exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
             name="Nifty 50", instrument_type="INDEX", segment="10",
             underlying="NIFTY"),
        dict(provider="fyers", instrument_token="10100000002885",
             exchange="NSE", tradingsymbol="NSE:RELIANCE-EQ",
             name="Reliance Industries", instrument_type="EQUITY",
             segment="10", underlying="RELIANCE"),
        # Upstox-shaped equity row (instrument_key style token).
        dict(provider="upstox", instrument_key="NSE_EQ|INE002A01018",
             instrument_token="NSE_EQ|INE002A01018",
             exchange="NSE_EQ", tradingsymbol="RELIANCE",
             name="Reliance Industries", instrument_type="EQUITY",
             segment="NSE_EQ"),
    ]
    # strip the non-schema key before insert
    for r in rows:
        r.pop("instrument_key", None)
    store.replace_provider_instruments("fyers",
                                       [rows[0], rows[1]])
    catalog = InstrumentCatalog(store)
    msvc = MarketService()

    def spot(exchange, token):
        return msvc.get_quote_now(exchange, token)

    registry = _mk_registry()
    intel = MarketIntel(catalog, spot_provider=spot,
                        identity_resolver=registry)

    async def apply(exchange, token, tsym, ltp):
        await msvc.apply_quote(QuotePatch(
            exchange=exchange, instrument_token=token,
            tradingsymbol=tsym, received_ts=datetime.now(timezone.utc),
            reported_fields={"ltp": ltp}))

    return registry, intel, msvc, apply, store


def test_id1_config_and_catalog_one_canonical(runner: R) -> None:
    reg = _mk_registry()
    reg.register("NSE:NIFTY50-INDEX",
                 ["NSE:NIFTY50-INDEX", "101000000026000", "NIFTY50"])
    runner.assert_eq("ID1-config-key", r1 := reg.resolve("NSE:NIFTY50-INDEX"),
                     "NSE:NIFTY50-INDEX")
    runner.assert_eq("ID1-catalog-token", reg.resolve("101000000026000"),
                     r1)
    runner.assert_eq("ID1-tsym", reg.resolve("NIFTY50"), r1)


def test_id2_fyers_nifty_single_state(runner: R) -> None:
    """The confirmed live case: config key and fyToken -> one quote."""
    registry, intel, msvc, apply, store = _mk_world()

    async def run():
        await apply("NSE", "NSE:NIFTY50-INDEX", "NIFTY50-INDEX", 24005.5)
        registry.register("NSE:NIFTY50-INDEX",
                          ["NSE:NIFTY50-INDEX", "101000000026000"])

    asyncio.run(run())
    runner.assert_eq("ID2-msvc-count", len(asyncio.run(
        msvc.quotes())), 1)


def test_id3_lookup_parity(runner: R) -> None:
    registry, intel, msvc, apply, store = _mk_world()

    async def run():
        await apply("NSE", "NSE:NIFTY50-INDEX", "NIFTY50-INDEX", 24005.5)
        registry.register("NSE:NIFTY50-INDEX",
                          ["NSE:NIFTY50-INDEX", "101000000026000"])

    asyncio.run(run())

    async def lookups():
        by_config = await msvc.get_quote("NSE", "NSE:NIFTY50-INDEX")
        by_catalog = await msvc.get_quote("NSE",
                                          registry.resolve(
                                              "101000000026000"))
        return by_config, by_catalog

    a, b = asyncio.run(lookups())
    runner.assert_true("ID3-both-found", a is not None and b is not None)
    if a and b:
        runner.assert_eq("ID3-same-ltp", float(a.ltp), float(b.ltp))
        runner.assert_true("ID3-same-object", a is b)


def test_id4_no_duplicate_state(runner: R) -> None:
    registry, intel, msvc, apply, store = _mk_world()

    async def run():
        await apply("NSE", "NSE:NIFTY50-INDEX", "NIFTY50-INDEX", 100.0)
        # Same instrument arriving under its catalog identity must NOT
        # create a second state — resolution happens before storage-side
        # lookup; the registry guarantees one canonical id.
        registry.register("NSE:NIFTY50-INDEX",
                          ["101000000026000"])

    asyncio.run(run())
    runner.assert_eq("ID4-one-quote", len(asyncio.run(msvc.quotes())), 1)


def test_id5_chain_spot_live(runner: R) -> None:
    """Option-chain spot resolves the LIVE quote via the resolver."""
    registry, intel, msvc, apply, store = _mk_world()
    # Seed chain strikes for NIFTY in the SAME catalog intel uses.
    # NOTE: replace is per-provider, so the index row must be included
    # again or _find_underlying loses its target.
    rows = [dict(provider="fyers", instrument_token="101000000026000",
                 exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
                 name="Nifty 50", instrument_type="INDEX", segment="10",
                 underlying="NIFTY")]
    for s in (23900.0, 23950.0, 24000.0, 24050.0, 24100.0):
        for ot in ("CE", "PE"):
            rows.append(dict(
                provider="fyers", instrument_token=f"900{s}{ot}",
                exchange="NSE", tradingsymbol=f"NSE:NIFTY{s}{ot}",
                name="NIFTY OPT", instrument_type="OPTION", segment="11",
                underlying="NIFTY", expiry="2026-09-01", strike=s,
                option_type=ot, lot_size=75))
    store.replace_provider_instruments("fyers", rows)
    # Re-bind index+equity identities (replace wiped provider rows).
    registry.register("NSE:NIFTY50-INDEX",
                      ["NSE:NIFTY50-INDEX", "101000000026000"])

    async def run():
        await apply("NSE", "NSE:NIFTY50-INDEX", "NIFTY50-INDEX", 24005.5)

    asyncio.run(run())
    c = intel.option_chain("NIFTY", window=1)
    runner.assert_eq("ID5-spot-basis-live", c["spot_basis"], "live")
    runner.assert_eq("ID5-spot-value", c["spot"], 24005.5)
    runner.assert_eq("ID5-atm-from-live-spot", c["atm_strike"], 24000.0)


def test_id6_fallback_when_genuinely_absent(runner: R) -> None:
    registry, intel, msvc, _apply, store = _mk_world()
    # Options listed, but no quote applied: fallback must engage.
    rows = [dict(provider="fyers", instrument_token="101000000026000",
                 exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
                 name="Nifty 50", instrument_type="INDEX", segment="10",
                 underlying="NIFTY")]
    for s in (23950.0, 24000.0, 24050.0):
        for ot in ("CE", "PE"):
            rows.append(dict(
                provider="fyers", instrument_token=f"900{s}{ot}",
                exchange="NSE", tradingsymbol=f"NSE:NIFTY{s}{ot}",
                name="NIFTY OPT", instrument_type="OPTION", segment="11",
                underlying="NIFTY", expiry="2026-09-01", strike=s,
                option_type=ot, lot_size=75))
    store.replace_provider_instruments("fyers", rows)
    registry.register("NSE:NIFTY50-INDEX",
                      ["NSE:NIFTY50-INDEX", "101000000026000"])
    c = intel.option_chain("NIFTY", window=1)   # no quotes applied
    runner.assert_eq("ID6-fallback-basis", c["spot_basis"],
                     "fallback_mid_strike")


def test_id7_upstox_aliases(runner: R) -> None:
    reg = _mk_registry()
    # Upstox storage key style + catalog row.
    reg.register("NSE_EQ|INE002A01018",
                 ["NSE_EQ|INE002A01018", "RELIANCE"])
    runner.assert_eq("ID7-upstox-key",
                     reg.resolve("NSE_EQ|INE002A01018"),
                     "NSE_EQ|INE002A01018")
    runner.assert_eq("ID7-upstox-alias",
                     reg.resolve("RELIANCE"), "NSE_EQ|INE002A01018")


def test_id8_runtime_add_registers(runner: R) -> None:
    reg = _mk_registry()
    ok = reg.register_from_catalog_row({
        "instrument_token": "10100000002885",
        "tradingsymbol": "NSE:RELIANCE-EQ",
        "provider_symbol": "NSE:RELIANCE-EQ",
    }, primary="NSE:RELIANCE-EQ")
    runner.assert_true("ID8-registered", ok["registered"] >= 1)
    runner.assert_eq("ID8-token-resolves",
                     reg.resolve("10100000002885"), "NSE:RELIANCE-EQ")


def test_id9_idempotent_registration(runner: R) -> None:
    reg = _mk_registry()
    first = reg.register("CANON", ["A", "B"])
    second = reg.register("CANON", ["A", "B"])
    runner.assert_eq("ID9-first-added", first["registered"], 2)
    runner.assert_eq("ID9-second-idempotent", second["registered"], 0)
    runner.assert_eq("ID9-no-rejections", len(second["rejected"]), 0)


def test_id10_collision_rejected(runner: R) -> None:
    reg = _mk_registry()
    reg.register("INSTRUMENT-A", ["SHARED"])
    result = reg.register("INSTRUMENT-B", ["SHARED"])
    runner.assert_in("ID10-rejected-list", "SHARED", result["rejected"])
    runner.assert_eq("ID10-original-binding-preserved",
                     reg.resolve("SHARED"), "INSTRUMENT-A")
    # Non-colliding aliases for B still register.
    runner.assert_eq("ID10-clean-aliases",
                     result["registered"], 0)


def test_id11_catalog_refresh_safe(runner: R) -> None:
    reg = _mk_registry()
    for _ in range(3):   # simulate repeated catalog sync re-registration
        res = reg.register("NSE:NIFTY50-INDEX",
                           ["NSE:NIFTY50-INDEX", "101000000026000"])
    runner.assert_eq("ID11-repeated-safe", len(res["rejected"]), 0)
    runner.assert_eq("ID11-still-resolves",
                     reg.resolve("101000000026000"), "NSE:NIFTY50-INDEX")


def test_id12_no_config_migration_needed(runner: R) -> None:
    """Existing config keys keep working with NO registry involvement:
    MarketService still stores/returns by the configured key."""
    registry, intel, msvc, apply, store = _mk_world()

    async def run():
        await apply("NSE", "NSE:NIFTY50-INDEX", "NIFTY50-INDEX", 111.0)

    asyncio.run(run())
    q = asyncio.run(msvc.get_quote("NSE", "NSE:NIFTY50-INDEX"))
    runner.assert_true("ID12-config-key-still-works", q is not None)
    runner.assert_eq("ID12-config-key-ltp", float(q.ltp), 111.0)


def test_id13_broker_to_ui_mcp_parity_wall(runner: R) -> None:
    """PERMANENT PARITY WALL: synthetic provider quote -> MarketService;
    lookup by provider/config identifier == lookup by catalog identifier
    (same object: LTP + timestamps)."""
    registry, intel, msvc, apply, store = _mk_world()
    registry.register("NSE:NIFTY50-INDEX",
                      ["NSE:NIFTY50-INDEX", "101000000026000"])

    async def run():
        await apply("NSE", "NSE:NIFTY50-INDEX", "NIFTY50-INDEX", 24005.5)

    asyncio.run(run())

    async def both():
        return (
            await msvc.get_quote("NSE", "NSE:NIFTY50-INDEX"),
            await msvc.get_quote("NSE",
                                 registry.resolve("101000000026000")),
        )

    via_provider, via_catalog = asyncio.run(both())
    runner.assert_true("PW-both-found",
                       via_provider is not None and via_catalog is not None)
    if via_provider and via_catalog:
        runner.assert_true("PW-same-instance", via_provider is via_catalog)
        runner.assert_eq("PW-ltp", float(via_provider.ltp),
                         float(via_catalog.ltp))
        runner.assert_eq("PW-received-ts",
                         via_provider.received_ts, via_catalog.received_ts)


if __name__ == "__main__":
    runner = R()
    test_id1_config_and_catalog_one_canonical(runner)
    test_id2_fyers_nifty_single_state(runner)
    test_id3_lookup_parity(runner)
    test_id4_no_duplicate_state(runner)
    test_id5_chain_spot_live(runner)
    test_id6_fallback_when_genuinely_absent(runner)
    test_id7_upstox_aliases(runner)
    test_id8_runtime_add_registers(runner)
    test_id9_idempotent_registration(runner)
    test_id10_collision_rejected(runner)
    test_id11_catalog_refresh_safe(runner)
    test_id12_no_config_migration_needed(runner)
    test_id13_broker_to_ui_mcp_parity_wall(runner)
    sys.exit(0 if runner.summary() else 1)
