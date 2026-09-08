"""Curated index-constituent reference taxonomy for MarketHub.

WHY THIS EXISTS
--------------
The synced instrument catalog (Upstox/Fyers masters) carries instrument
identity, segment, underlying and expiry — but NOT index constituent
membership. The authoritative source for "which stocks make up NIFTY 50 /
NIFTY Next 50 / Bank Nifty" is therefore a maintained reference list, not
derivable from the catalog.

AUTHORITATIVE SOURCE (do not guess)
-----------------------------------
Constituent sets below are sourced from the official NSE index factsheets /
NSE Indices publications, mirrored by the public NIFTY 50 and NIFTY Next 50
constituent tables (Wikipedia, "as of" 8 Dec 2025 for NIFTY 50 and 1 Apr 2026
for NIFTY Next 50). They are kept in sync with the real indices on each
semi-annual rebalance. Do NOT add or remove a symbol without an authoritative
reference — an incomplete NIFTY 50 (e.g. 49/50) must never ship.

BANKNIFTY is project-owned: it reflects the 12 standard Bank Nifty constituents
maintained here. Adjust only with intent.

This module is the SINGLE OWNER of index-constituent membership. Both Market
Breadth and the Sector Heatmap resolve their index universes through
``market_universe.resolve_universe``, which reads these lists. No other module
may hard-code constituent symbols.

HONESTY
-------
If a curated symbol is absent from the synced catalog at runtime it is reported
as ``unavailable`` (not silently dropped), and the eligible count still reflects
the full curated list. The "NIFTY50 does not include unrelated equity" invariant
holds: the universe is exactly this list — never the whole NSE.
"""

from __future__ import annotations

# Universe key -> ordered list of NSE equity trading symbols.
# Sourced from NSE index factsheets (see module docstring). 50 each for the
# broad indices; 12 for the project-owned Bank Nifty.
INDEX_MEMBERS: dict[str, tuple[str, ...]] = {
    "NIFTY50": (
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
    ),
    "NIFTYNXT50": (
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
    ),
    "BANKNIFTY": (
        "AUBANK", "AXISBANK", "BANKBARODA", "BANDHANBNK", "FEDERALBNK",
        "HDFCBANK", "ICICIBANK", "IDFCFIRSTB", "INDUSINDBK", "KOTAKBANK",
        "PNB", "SBIN",
    ),
}


def members_of(universe: str) -> tuple[str, ...] | None:
    """Return the curated constituent symbols for an index universe, else None."""
    return INDEX_MEMBERS.get((universe or "").upper())
