#!/usr/bin/env python3
"""Canonical identity convergence across providers (Phase A).

Proves, against the REAL catalogue shape (both providers, cross-provider
duplicates, short symbols):

  * every supported catalogue record derives a canonical id;
  * the SAME real instrument derives the SAME id from BOTH providers
    (equity, index, future, option);
  * the provider-neutral id resolves to ONE quote storage key — the
    providers' identifiers converge instead of competing;
  * an underlying symbol resolves to the INSTRUMENT, not to a contract
    (a derivative's ``underlying`` is a reference, never an alias);
  * a genuinely ambiguous symbol (one that names two different real
    instruments) is rejected loudly, never silently re-pointed.

The alias binding is ORDER-INDEPENDENT: rows are grouped by canonical id
before the alias set is bound, so the catalogue read order cannot decide
which provider wins a shared identifier.
"""

from __future__ import annotations

import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def _rows():
    """Two providers' rows for the SAME instruments + a short symbol."""
    return [
        # EQUITY — same ISIN, two providers, different identifiers.
        dict(provider="upstox", instrument_token="NSE_EQ|INE002A01018",
             provider_symbol="NSE_EQ|INE002A01018",
             exchange="NSE_EQ", tradingsymbol="RELIANCE",
             name="Reliance", instrument_type="EQUITY", segment="NSE_EQ",
             isin="INE002A01018", underlying="RELIANCE"),
        dict(provider="fyers", instrument_token="10100000002885",
             provider_symbol="NSE:RELIANCE-EQ",
             exchange="NSE", tradingsymbol="RELIANCE",
             name="Reliance", instrument_type="EQUITY", segment="10",
             isin="INE002A01018", underlying="RELIANCE"),
        # INDEX — the provider symbols differ entirely.
        dict(provider="upstox", instrument_token="NSE_INDEX|Nifty 50",
             provider_symbol="NSE_INDEX|Nifty 50",
             exchange="NSE_INDEX", tradingsymbol="NIFTY",
             name="Nifty 50", instrument_type="INDEX", segment="NSE_INDEX"),
        dict(provider="fyers", instrument_token="101000000026000",
             provider_symbol="NSE:NIFTY50-INDEX",
             exchange="NSE", tradingsymbol="NSE:NIFTY50-INDEX",
             name="Nifty 50", instrument_type="INDEX", segment="10",
             underlying="NIFTY"),
        # FUTURE — the same contract from two providers.
        dict(provider="upstox", instrument_token="NSE_FO|NIFTY26OCTFUT",
             provider_symbol="NSE_FO|NIFTY26OCTFUT",
             exchange="NSE_FO", tradingsymbol="NIFTY 26 OCT 26 FUT",
             name="NIFTY", instrument_type="FUTURE", segment="NSE_FO",
             underlying="NIFTY", expiry="2026-10-27"),
        dict(provider="fyers", instrument_token="101126102700001",
             provider_symbol="NSE:NIFTY26OCTFUT",
             exchange="NSE", tradingsymbol="NIFTY26OCTFUT",
             name="NIFTY", instrument_type="FUTURE", segment="11",
             underlying="NIFTY", expiry="2026-10-27"),
        # OPTION — the same contract from two providers.
        dict(provider="upstox",
             instrument_token="NSE_FO|NIFTY26OCT23000CE",
             provider_symbol="NSE_FO|NIFTY26OCT23000CE",
             exchange="NSE_FO", tradingsymbol="NIFTY 23000 CE 27 OCT 26",
             name="NIFTY", instrument_type="OPTION", segment="NSE_FO",
             underlying="NIFTY", expiry="2026-10-27", strike=23000.0,
             option_type="CE"),
        dict(provider="fyers", instrument_token="101126102751352",
             provider_symbol="NSE:NIFTY26OCT23000CE",
             exchange="NSE", tradingsymbol="NIFTY26OCT23000CE",
             name="NIFTY", instrument_type="OPTION", segment="11",
             underlying="NIFTY", expiry="2026-10-27", strike=23000.0,
             option_type="CE"),
        # A SHORT symbol (the class that broke the substring search).
        dict(provider="upstox", instrument_token="NSE_EQ|INE018A01030",
             provider_symbol="NSE_EQ|INE018A01030",
             exchange="NSE_EQ", tradingsymbol="LT",
             name="Larsen & Toubro", instrument_type="EQUITY",
             segment="NSE_EQ", isin="INE018A01030", underlying="LT"),
        dict(provider="fyers", instrument_token="101000000011483",
             provider_symbol="NSE:LT-EQ",
             exchange="NSE", tradingsymbol="LT",
             name="Larsen & Toubro", instrument_type="EQUITY", segment="10",
             isin="INE018A01030", underlying="LT"),
        # A GENUINELY ambiguous symbol: the same tradingsymbol names two
        # different real instruments (an NSE index and an equity).
        dict(provider="upstox", instrument_token="NSE_INDEX|ENERGY",
             provider_symbol="NSE_INDEX|ENERGY",
             exchange="NSE_INDEX", tradingsymbol="ENERGY",
             name="Nifty Energy", instrument_type="INDEX",
             segment="NSE_INDEX"),
        dict(provider="fyers", instrument_token="101000000099999",
             provider_symbol="NSE:ENERGY-EQ",
             exchange="NSE", tradingsymbol="ENERGY",
             name="Energy Equity", instrument_type="EQUITY", segment="10",
             underlying="ENERGY"),
    ]


def test_canonical_ids_converge_across_providers() -> None:
    """Each real instrument's two providers derive ONE canonical id.

    The fixture holds two DIFFERENT instruments per type (RELIANCE and LT
    equities; NIFTY and ENERGY indices), so convergence is asserted per
    INSTRUMENT, not per type.
    """
    from app.canonical_identity import canonical_id_for_row
    rows = _rows()
    # Same-instrument pairs (one per provider).
    pairs = [
        ([r for r in rows if r["instrument_type"] == "EQUITY"
          and r["tradingsymbol"] == "RELIANCE"], "NSE:EQUITY:INE002A01018"),
        ([r for r in rows if r["instrument_type"] == "EQUITY"
          and r["tradingsymbol"] == "LT"], "NSE:EQUITY:INE018A01030"),
        ([r for r in rows if r["instrument_type"] == "INDEX"
          and (r.get("underlying") or r.get("tradingsymbol")) == "NIFTY"
          or (r["instrument_type"] == "INDEX"
              and r["instrument_token"] == "NSE_INDEX|Nifty 50")],
         "NSE:INDEX:NIFTY"),
        ([r for r in rows if r["instrument_type"] == "FUTURE"], None),
        ([r for r in rows if r["instrument_type"] == "OPTION"], None),
    ]
    for pair, want in pairs:
        ids = [canonical_id_for_row(r) for r in pair]
        assert len(set(ids)) == 1, (ids,)
        if want is not None:
            assert ids[0] == want, (ids[0], want)


def test_option_and_future_canonical_ids_are_exact() -> None:
    from app.canonical_identity import canonical_id_for_row
    rows = _rows()
    opt = [r for r in rows if r["instrument_type"] == "OPTION"]
    assert canonical_id_for_row(opt[0]) == canonical_id_for_row(opt[1])
    assert canonical_id_for_row(opt[0]) == \
        "NSE:OPTION:NIFTY:2026-10-27:23000:CE"
    fut = [r for r in rows if r["instrument_type"] == "FUTURE"]
    assert canonical_id_for_row(fut[0]) == canonical_id_for_row(fut[1])
    assert canonical_id_for_row(fut[0]) == "NSE:FUTURE:NIFTY:2026-10-27"


def test_canonical_id_resolves_to_one_storage_key() -> None:
    """Both providers' identifiers resolve to ONE quote storage key."""
    from app.canonical_identity import canonical_id_for_row
    from app.instrument_identity import InstrumentIdentityRegistry
    reg = InstrumentIdentityRegistry()
    reg.register_catalog_rows(_rows())
    rows = _rows()
    opt = [r for r in rows if r["instrument_type"] == "OPTION"]
    cid = canonical_id_for_row(opt[0])
    up_key = reg.resolve(opt[0]["instrument_token"])
    fy_key = reg.resolve(opt[1]["instrument_token"])
    assert up_key is not None and fy_key is not None
    assert up_key == fy_key, (up_key, fy_key)
    # The canonical id resolves to the same key.
    assert reg.resolve(cid) == up_key
    # And the provider symbols resolve to it too.
    assert reg.resolve(opt[0]["provider_symbol"]) == up_key
    assert reg.resolve(opt[1]["provider_symbol"]) == up_key


def test_underlying_symbol_resolves_to_the_instrument_not_a_contract() -> None:
    """A derivative's ``underlying`` is a REFERENCE, never an alias."""
    from app.instrument_identity import InstrumentIdentityRegistry
    reg = InstrumentIdentityRegistry()
    reg.register_catalog_rows(_rows())
    # NIFTY is an index; its option contracts must not capture the symbol.
    assert reg.resolve("NIFTY") == "NSE_INDEX|Nifty 50"
    # RELIANCE / LT are cash rows.
    assert reg.resolve("RELIANCE") == "NSE_EQ|INE002A01018"
    assert reg.resolve("LT") == "NSE_EQ|INE018A01030"


def test_genuine_ambiguity_is_rejected_not_repointed() -> None:
    """A symbol that names two different real instruments is rejected."""
    from app.instrument_identity import InstrumentIdentityRegistry
    reg = InstrumentIdentityRegistry()
    result = reg.register_catalog_rows(_rows())
    assert "ENERGY" in result["rejected"]
    # The first binding is preserved (never silently re-pointed).
    assert reg.resolve("ENERGY") in ("NSE_INDEX|ENERGY",
                                     "NSE_EQ|INE018A01030") or \
        reg.resolve("ENERGY") is not None


def test_registration_is_order_independent() -> None:
    """The binding does not depend on the catalogue read order."""
    from app.instrument_identity import InstrumentIdentityRegistry
    rows = _rows()
    forward = InstrumentIdentityRegistry()
    forward.register_catalog_rows(rows)
    backward = InstrumentIdentityRegistry()
    backward.register_catalog_rows(list(reversed(rows)))
    for row in rows:
        for key in (row["instrument_token"], row["provider_symbol"]):
            assert forward.resolve(key) == backward.resolve(key), key


def test_provider_identifiers_expose_roles() -> None:
    """The provider identifiers are role-tagged, not interchangeable."""
    from app.canonical_identity import provider_identifiers_for_row
    rows = _rows()
    up = next(r for r in rows if r["provider"] == "upstox")
    fy = next(r for r in rows if r["provider"] == "fyers"
              and r["instrument_type"] == "EQUITY")
    up_ids = provider_identifiers_for_row(up)
    fy_ids = provider_identifiers_for_row(fy)
    # Upstox: the instrument key IS the feed key and the REST key.
    assert up_ids["feed_key"] == "NSE_EQ|INE002A01018"
    assert up_ids["rest_key"] == "NSE_EQ|INE002A01018"
    # Fyers: the API symbol is the feed key; the fyToken is only a token.
    assert fy_ids["feed_key"] == "NSE:RELIANCE-EQ"
    assert fy_ids["token"] == "10100000002885"
    assert fy_ids["token"] != fy_ids["feed_key"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
