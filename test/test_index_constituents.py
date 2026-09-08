"""Index-constituent source tests — authoritative membership counts.

These lock market/index_constituents.py to the authoritative NSE index
constituent sets so an incomplete universe (e.g. NIFTY50 = 49/50) can never
ship silently. Constituents are sourced from NSE index factsheets (see the
module docstring), not guessed.
"""

from market.index_constituents import INDEX_MEMBERS, members_of
from market.market_universe import resolve_universe
from test.helpers.market_breadth_fakes import FakeCatalog


# Authoritative NIFTY 50 set (NSE factsheet, as of 8 Dec 2025).
EXPECTED_NIFTY50 = {
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV", "BEL", "BHARTIARTL",
    "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
    "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
    "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
    "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC",
    "POWERGRID", "RELIANCE", "SBILIFE", "SHRIRAMFIN", "SBIN",
    "SUNPHARMA", "TCS", "TATACONSUM", "TMPV", "TATASTEEL",
    "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO",
}

# Authoritative NIFTY Next 50 set (NSE factsheet, as of 1 Apr 2026).
EXPECTED_NIFTYNXT50 = {
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "BAJAJHLDNG", "BANKBARODA", "BPCL", "BRITANNIA", "BOSCHLTD",
    "CANBK", "CGPOWER", "CHOLAFIN", "CUMMINSIND", "DIVISLAB",
    "DLF", "DMART", "GAIL", "GODREJCP", "HDFCAMC",
    "HAL", "HINDZINC", "HYUNDAI", "INDHOTEL", "IOC",
    "IRFC", "JINDALSTEL", "LODHA", "LTM", "MAZDOCK",
    "MUTHOOTFIN", "PIDILITIND", "PFC", "PNB", "RECLTD",
    "MOTHERSON", "SHREECEM", "SIEMENS", "ENRIN", "SOLARINDS",
    "TATACAP", "TMCV", "TATAPOWER", "TORNTPHARM", "TVSMOTOR",
    "UNIONBANK", "UNITDSPR", "VBL", "VEDL", "ZYDUSLIFE",
}

# Bank Nifty is project-owned: the 12 standard constituents maintained here.
EXPECTED_BANKNIFTY = {
    "AUBANK", "AXISBANK", "BANKBARODA", "BANDHANBNK", "FEDERALBNK",
    "HDFCBANK", "ICICIBANK", "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK",
    "PNB", "SBIN",
}


def test_nifty50_has_exactly_50_authoritative_members():
    members = INDEX_MEMBERS["NIFTY50"]
    assert len(members) == 50, f"NIFTY50 must be 50, got {len(members)}"
    assert set(members) == EXPECTED_NIFTY50


def test_niftynxt50_has_exactly_50_authoritative_members():
    members = INDEX_MEMBERS["NIFTYNXT50"]
    assert len(members) == 50, f"NIFTYNXT50 must be 50, got {len(members)}"
    assert set(members) == EXPECTED_NIFTYNXT50


def test_banknifty_project_owned_count():
    members = INDEX_MEMBERS["BANKNIFTY"]
    assert len(members) == 12, f"BANKNIFTY (project-owned) must be 12, got {len(members)}"
    assert set(members) == EXPECTED_BANKNIFTY


def test_no_duplicate_symbols_within_universe():
    for name, members in INDEX_MEMBERS.items():
        assert len(members) == len(set(members)), f"duplicates in {name}"


def test_members_of_helper():
    assert members_of("NIFTY50") is not None
    assert members_of("DOESNOTEXIST") is None


def test_resolve_universe_nifty50_returns_50():
    # Catalog resolves every curated symbol (FakeCatalog returns a token for all).
    cat = FakeCatalog(search_map={
        s: [{"instrument_token": f"NSE_EQ|{s}", "name": s.title()}]
        for s in INDEX_MEMBERS["NIFTY50"]
    })
    members = resolve_universe("NIFTY50", cat)
    assert len(members) == 50
    # Every member carries a resolvable token (none silently dropped).
    assert all(m.instrument_token for m in members)
