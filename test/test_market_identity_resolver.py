#!/usr/bin/env python3
"""B2 provider-neutral identity resolver tests.

Proves the same real instrument registered from different providers
(Upstox + Fyers) converges to ONE canonical id, so a condition alert bound
to a canonical id fires regardless of which provider's quote arrives:

  * IR1  RELIANCE equity — Upstox + Fyers rows -> NSE:EQUITY:INE002A01018
  * IR2  NIFTY index — Upstox + Fyers rows -> NSE:INDEX:NIFTY
  * IR3  NIFTY future — both providers -> NSE:FUTURE:NIFTY:<expiry>
  * IR4  NIFTY CE/PE options — both providers -> NSE:OPTION:...:strike:CE/PE
  * IR5  expiry normalization — epoch string and ISO date converge
  * IR6  strike normalization — 25000 == 25000.0 -> '25000'
  * IR7  symbol aliases — "Nifty 50"/"NIFTY50-INDEX"/"NIFTY 50" -> NIFTY
  * IR8  resolve_quote — token and tradingsymbol both resolve
  * IR9  collision rejected loudly, never silently re-pointed
  * IR10 re-registration idempotent
  * IR11 provider-switch — a quote from the OTHER provider resolves to the
        same canonical id (no condition migration needed)
  * IR12 context_for carries display/derivative context

NO LIVE BROKER. Synthetic only.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (_PROJECT_DIR, _SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers.runner import R

from app.market_identity import MarketInstrumentIdentityResolver
from market.models import Quote

RELIANCE_ISIN = "INE002A01018"
EXPIRY_ISO = "2026-09-29"
EXPIRY_EPOCH = "1790640000"  # 2026-09-29T00:00:00Z


def _mk_quote(exchange, token, tsym) -> Quote:
    return Quote(instrument_token=token, exchange=exchange,
                 tradingsymbol=tsym,
                 received_ts=datetime.now(timezone.utc), ltp=100.0)


def _upstox_equity() -> dict:
    return dict(provider="upstox", instrument_token=f"NSE_EQ|{RELIANCE_ISIN}",
                exchange="NSE_EQ", tradingsymbol="RELIANCE",
                name="Reliance Industries", instrument_type="EQUITY",
                segment="NSE_EQ", isin=RELIANCE_ISIN)


def _fyers_equity() -> dict:
    return dict(provider="fyers", instrument_token="10100000002885",
                exchange="NSE", tradingsymbol="NSE:RELIANCE-EQ",
                name="Reliance Industries", instrument_type="EQUITY",
                segment="10", isin=RELIANCE_ISIN)


def _upstox_index() -> dict:
    return dict(provider="upstox", instrument_token="NSE_INDEX|Nifty 50",
                exchange="NSE_INDEX", tradingsymbol="Nifty 50",
                name="Nifty 50", instrument_type="INDEX",
                segment="NSE_INDEX")


def _fyers_index() -> dict:
    return dict(provider="fyers", instrument_token="101000000026000",
                exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
                name="Nifty 50", instrument_type="INDEX", segment="10")


def _upstox_future() -> dict:
    return dict(provider="upstox", instrument_token="NSE_FO|NIFTY26SEP",
                exchange="NSE_FO", tradingsymbol="NIFTY26SEPFUT",
                name="NIFTY FUT", instrument_type="FUT",
                segment="NSE_FO", underlying="NIFTY", expiry=EXPIRY_EPOCH)


def _fyers_future() -> dict:
    return dict(provider="fyers", instrument_token="10100000000000",
                exchange="NSE", tradingsymbol="NSE:NIFTY26SEPFUT",
                name="NIFTY FUT", instrument_type="FUT",
                segment="11", underlying="NIFTY", expiry=EXPIRY_ISO)


def _upstox_option(option_type: str) -> dict:
    return dict(provider="upstox", instrument_token=f"NSE_FO|NIFTY26SEP25000{option_type}",
                exchange="NSE_FO", tradingsymbol=f"NIFTY26SEP25000{option_type}",
                name="NIFTY OPT", instrument_type="OPTION",
                segment="NSE_FO", underlying="NIFTY", expiry=EXPIRY_EPOCH,
                strike=25000, option_type=option_type)


def _fyers_option(option_type: str) -> dict:
    return dict(provider="fyers", instrument_token=f"90025000{option_type}",
                exchange="NSE", tradingsymbol=f"NSE:NIFTY26SEP25000{option_type}",
                name="NIFTY OPT", instrument_type="OPTION",
                segment="11", underlying="NIFTY", expiry=EXPIRY_ISO,
                strike=25000.0, option_type=option_type)


def test_ir1_equity_convergence(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    r.register_catalog_row(_upstox_equity())
    r.register_catalog_row(_fyers_equity())
    want = f"NSE:EQUITY:{RELIANCE_ISIN}"
    runner.assert_eq("IR1-upstox-cid", r.canonical_id_for_row(_upstox_equity()), want)
    runner.assert_eq("IR1-fyers-cid", r.canonical_id_for_row(_fyers_equity()), want)
    # Both providers' identifiers resolve to the same canonical id.
    runner.assert_eq("IR1-upstox-token", r.resolve(f"NSE_EQ|{RELIANCE_ISIN}"), want)
    runner.assert_eq("IR1-fyers-token", r.resolve("10100000002885"), want)
    runner.assert_eq("IR1-fyers-tsym", r.resolve("NSE:RELIANCE-EQ"), want)


def test_ir2_index_convergence(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    r.register_catalog_row(_upstox_index())
    r.register_catalog_row(_fyers_index())
    want = "NSE:INDEX:NIFTY"
    runner.assert_eq("IR2-upstox-cid", r.canonical_id_for_row(_upstox_index()), want)
    runner.assert_eq("IR2-fyers-cid", r.canonical_id_for_row(_fyers_index()), want)
    runner.assert_eq("IR2-upstox-token", r.resolve("NSE_INDEX|Nifty 50"), want)
    runner.assert_eq("IR2-fyers-token", r.resolve("101000000026000"), want)
    runner.assert_eq("IR2-fyers-tsym", r.resolve("NSE:NIFTY50-INDEX"), want)


def test_ir3_future_convergence(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    r.register_catalog_row(_upstox_future())
    r.register_catalog_row(_fyers_future())
    want = f"NSE:FUTURE:NIFTY:{EXPIRY_ISO}"
    runner.assert_eq("IR3-upstox-cid", r.canonical_id_for_row(_upstox_future()), want)
    runner.assert_eq("IR3-fyers-cid", r.canonical_id_for_row(_fyers_future()), want)
    runner.assert_eq("IR3-upstox-token", r.resolve("NSE_FO|NIFTY26SEP"), want)
    runner.assert_eq("IR3-fyers-token", r.resolve("10100000000000"), want)


def test_ir4_option_convergence(runner: R) -> None:
    for ot in ("CE", "PE"):
        r = MarketInstrumentIdentityResolver()
        r.register_catalog_row(_upstox_option(ot))
        r.register_catalog_row(_fyers_option(ot))
        want = f"NSE:OPTION:NIFTY:{EXPIRY_ISO}:25000:{ot}"
        runner.assert_eq(f"IR4-{ot}-upstox-cid",
                         r.canonical_id_for_row(_upstox_option(ot)), want)
        runner.assert_eq(f"IR4-{ot}-fyers-cid",
                         r.canonical_id_for_row(_fyers_option(ot)), want)
        runner.assert_eq(f"IR4-{ot}-upstox-token",
                         r.resolve(f"NSE_FO|NIFTY26SEP25000{ot}"), want)
        runner.assert_eq(f"IR4-{ot}-fyers-token",
                         r.resolve(f"90025000{ot}"), want)


def test_ir5_expiry_normalization(runner: R) -> None:
    from app.market_identity import _normalize_expiry
    runner.assert_eq("IR5-epoch", _normalize_expiry(EXPIRY_EPOCH), EXPIRY_ISO)
    runner.assert_eq("IR5-iso", _normalize_expiry(EXPIRY_ISO), EXPIRY_ISO)
    runner.assert_eq("IR5-none", _normalize_expiry(None), None)
    runner.assert_eq("IR5-empty", _normalize_expiry(""), None)
    runner.assert_eq("IR5-zero", _normalize_expiry(0), None)


def test_ir6_strike_normalization(runner: R) -> None:
    from app.market_identity import _normalize_strike
    runner.assert_eq("IR6-int", _normalize_strike(25000), "25000")
    runner.assert_eq("IR6-float", _normalize_strike(25000.0), "25000")
    runner.assert_eq("IR6-decimal-str", _normalize_strike("25000.0"), "25000")
    runner.assert_eq("IR6-fractional", _normalize_strike(25000.5), "25000.5")
    runner.assert_eq("IR6-none", _normalize_strike(None), None)


def test_ir7_symbol_aliases(runner: R) -> None:
    from app.market_identity import _canonical_symbol
    for raw in ("Nifty 50", "NIFTY50-INDEX", "NIFTY 50", "nifty"):
        runner.assert_eq(f"IR7-{raw!r}", _canonical_symbol(raw), "NIFTY")
    runner.assert_eq("IR7-banknifty", _canonical_symbol("BANKNIFTY"), "BANKNIFTY")
    runner.assert_eq("IR7-sensex", _canonical_symbol("SENSEX"), "SENSEX")
    # EXCH: prefix stripped (Fyers provider_symbol style).
    runner.assert_eq("IR7-exch-prefix", _canonical_symbol("NSE:NIFTY50-INDEX"), "NIFTY")


def test_ir8_resolve_quote(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    r.register_catalog_row(_upstox_equity())
    r.register_catalog_row(_fyers_equity())
    want = f"NSE:EQUITY:{RELIANCE_ISIN}"
    # Quote from the Upstox provider (token style).
    q_up = _mk_quote("NSE", f"NSE_EQ|{RELIANCE_ISIN}", "RELIANCE")
    runner.assert_eq("IR8-upstox-quote", r.resolve_quote(q_up), want)
    # Quote from the Fyers provider (token + tsym style).
    q_fy = _mk_quote("NSE", "10100000002885", "NSE:RELIANCE-EQ")
    runner.assert_eq("IR8-fyers-quote", r.resolve_quote(q_fy), want)
    # Unknown quote -> None.
    q_unknown = _mk_quote("NSE", "999999", "UNKNOWN")
    runner.assert_eq("IR8-unknown", r.resolve_quote(q_unknown), None)


def test_ir9_collision_rejected(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    r.register("NSE:EQUITY:AAA", ["SHARED-TOKEN"])
    result = r.register("NSE:EQUITY:BBB", ["SHARED-TOKEN"])
    runner.assert_in("IR9-rejected", "SHARED-TOKEN", result["rejected"])
    runner.assert_eq("IR9-original-preserved",
                     r.resolve("SHARED-TOKEN"), "NSE:EQUITY:AAA")
    # The colliding alias was NOT re-pointed to B.
    runner.assert_not_eq("IR9-not-repointed",
                         r.resolve("SHARED-TOKEN"), "NSE:EQUITY:BBB")
    # A completely unknown identifier resolves to None.
    runner.assert_eq("IR9-unknown", r.resolve("NO-SUCH-ID"), None)


def test_ir10_idempotent(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    first = r.register("NSE:EQUITY:AAA", ["T1", "SYM"])
    second = r.register("NSE:EQUITY:AAA", ["T1", "SYM"])
    runner.assert_eq("IR10-first", first["registered"], 2)
    runner.assert_eq("IR10-second", second["registered"], 0)
    runner.assert_eq("IR10-no-reject", len(second["rejected"]), 0)


def test_ir11_provider_switch(runner: R) -> None:
    """A condition bound to the canonical id fires from EITHER provider."""
    r = MarketInstrumentIdentityResolver()
    r.register_catalog_row(_upstox_equity())
    r.register_catalog_row(_fyers_equity())
    want = f"NSE:EQUITY:{RELIANCE_ISIN}"
    # Same canonical id regardless of which provider's quote arrives.
    runner.assert_eq("IR11-upstox", r.resolve_quote(
        _mk_quote("NSE", f"NSE_EQ|{RELIANCE_ISIN}", "RELIANCE")), want)
    runner.assert_eq("IR11-fyers", r.resolve_quote(
        _mk_quote("NSE", "10100000002885", "NSE:RELIANCE-EQ")), want)


def test_ir12_context(runner: R) -> None:
    r = MarketInstrumentIdentityResolver()
    r.register_catalog_row(_fyers_option("CE"))
    want = f"NSE:OPTION:NIFTY:{EXPIRY_ISO}:25000:CE"
    ctx = r.context_for(want)
    runner.assert_eq("IR12-type", ctx.get("instrument_type"), "OPTION")
    runner.assert_eq("IR12-underlying", ctx.get("underlying"), "NIFTY")
    runner.assert_eq("IR12-expiry", ctx.get("expiry"), EXPIRY_ISO)
    runner.assert_eq("IR12-strike", ctx.get("strike"), 25000.0)
    runner.assert_eq("IR12-option_type", ctx.get("option_type"), "CE")
    runner.assert_eq("IR12-exchange", ctx.get("exchange"), "NSE")
    runner.assert_eq("IR12-unknown-ctx", r.context_for("NOPE"), {})


async def main() -> bool:
    runner = R()
    test_ir1_equity_convergence(runner)
    test_ir2_index_convergence(runner)
    test_ir3_future_convergence(runner)
    test_ir4_option_convergence(runner)
    test_ir5_expiry_normalization(runner)
    test_ir6_strike_normalization(runner)
    test_ir7_symbol_aliases(runner)
    test_ir8_resolve_quote(runner)
    test_ir9_collision_rejected(runner)
    test_ir10_idempotent(runner)
    test_ir11_provider_switch(runner)
    test_ir12_context(runner)
    return runner.summary()


if __name__ == "__main__":
    import asyncio
    success = asyncio.run(main())
    sys.exit(0 if success else 1)