"""Cross-feature consistency tests (§24, items 31–36).

These prove Market Breadth and Sector Heatmap share ONE universe, ONE quote
reader, and ONE sector classifier, so their totals reconcile by construction.
"""

import pytest

from market.breadth import compute_breadth
from market.sector_heatmap import compute_sector_heatmap
from market.market_universe import resolve_universe
from test.helpers.market_breadth_fakes import (
    FakeCatalog,
    FakeQuoteReader,
    make_quote,
    members_from_symbols,
)


def _reader(*quotes):
    return FakeQuoteReader({q.instrument_token: q for q in quotes})


def _shared_state():
    """One universe, one reader, used by BOTH features (the real wiring)."""
    members = members_from_symbols(
        ["RELIANCE", "HDFCBANK", "INFY", "TATAMOTORS", "UNKNOWN1"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),   # ENERGY advance
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100),    # BANKING decline
        make_quote("NSE_EQ|INFY", ltp=105, close=100),       # IT advance
        make_quote("NSE_EQ|TATAMOTORS", ltp=100, close=100), # AUTO unchanged
        # UNKNOWN1: no quote -> unavailable (and unclassified sector)
    )
    return members, r.get_quote_now


# 31. breadth total advances = sector advances (+ unclassified advances)
def test_breadth_advances_reconcile():
    members, reader = _shared_state()
    b = compute_breadth("U", members, reader)
    h = compute_sector_heatmap("U", members, reader)
    sec_adv = sum(s.advances for s in h.sectors)
    assert sec_adv == b.advances


# 32. breadth declines reconcile similarly
def test_breadth_declines_reconcile():
    members, reader = _shared_state()
    b = compute_breadth("U", members, reader)
    h = compute_sector_heatmap("U", members, reader)
    sec_dec = sum(s.declines for s in h.sectors)
    assert sec_dec == b.declines


# 33. quoted totals reconcile
def test_quoted_totals_reconcile():
    members, reader = _shared_state()
    b = compute_breadth("U", members, reader)
    h = compute_sector_heatmap("U", members, reader)
    sec_quoted = sum(s.quoted for s in h.sectors)
    assert sec_quoted == b.quoted == 4


# 34. same symbol has same change % in both features
def test_same_change_percent_both_features():
    members, reader = _shared_state()
    b = compute_breadth("U", members, reader)
    h = compute_sector_heatmap("U", members, reader)
    brow = {x.symbol: x.change_percent for x in b.rows}
    for sec in h.sectors:
        for m in sec.members:
            assert m["change_percent"] == brow[m["symbol"]]


# 35. same as_of / freshness semantics
def test_same_as_of_freshness():
    members, reader = _shared_state()
    b = compute_breadth("U", members, reader)
    h = compute_sector_heatmap("U", members, reader)
    assert b.as_of == h.as_of
    assert b.stale == h.stale


# 36. no duplicate sector membership
def test_no_duplicate_sector_membership():
    members, reader = _shared_state()
    h = compute_sector_heatmap("U", members, reader)
    seen = set()
    total_constituents = 0
    for sec in h.sectors:
        total_constituents += sec.constituent_count
        for m in sec.members:
            assert m["symbol"] not in seen, f"duplicate {m['symbol']}"
            seen.add(m["symbol"])
    # No stock is counted in two sectors (constituent counts sum to the universe).
    assert total_constituents == len(members)
    # Within the quoted drill-down lists there are no duplicates.
    assert len(seen) == len(members) - 1  # one member is unavailable (not in lists)


def test_unclassified_count_explicit():
    members, reader = _shared_state()
    h = compute_sector_heatmap("U", members, reader)
    # UNKNOWN1 is unclassified; the count is reported, not hidden in "Other".
    assert h.unclassified_count == 1
    sec = {x.sector: x for x in h.sectors}
    assert sec["Unclassified"].constituent_count == 1


def test_reconciliation_flag_true():
    members, reader = _shared_state()
    h = compute_sector_heatmap("U", members, reader)
    rec = h.reconciliation
    assert rec["advances_match"] and rec["declines_match"] and rec["quoted_match"]


def test_index_universe_consistency_fno():
    """Same reconciliation holds for a real catalog-derived universe."""
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank", "equity_key": "NSE_EQ|HDFCBANK"},
    ])
    members = resolve_universe("FNO", cat)
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100),
    )
    b = compute_breadth("FNO", members, r.get_quote_now)
    h = compute_sector_heatmap("FNO", members, r.get_quote_now)
    assert sum(s.advances for s in h.sectors) == b.advances
    assert sum(s.declines for s in h.sectors) == b.declines
    assert h.reconciliation["advances_match"]
