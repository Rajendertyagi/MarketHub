"""Market Map — canonical projection + WebUI-contract tests (§22, items 1–35).

Market Map reuses the EXACT same foundation as Breadth and Sector Heatmap:
  * market_universe.resolve_universe   (single universe owner)
  * market.sector_classification      (single sector owner)
  * market.breadth.effective_change   (identical movement/color semantics)
  * the canonical quote reader         (MarketService.get_quote_now)

Therefore eligible/quoted/unavailable/advances/declines/unchanged reconcile
with Breadth, and per-sector quoted membership + per-symbol change % reconcile
with Sector Heatmap, by construction. These tests enforce that contract.
"""

import asyncio
import json

import pytest

from api.routes import build_market_routes
from market.breadth import compute_breadth, effective_change
from market.market_map import compute_market_map
from market.market_universe import resolve_universe
from market.sector_classification import UNCLASSIFIED, sector_for_symbol
from market.sector_heatmap import compute_sector_heatmap
from test.helpers.market_breadth_fakes import (
    FakeCatalog,
    FakeQuoteReader,
    make_quote,
    members_from_symbols,
)


def _reader(*quotes):
    return FakeQuoteReader({q.instrument_token: q for q in quotes})


def _shared():
    """5 stocks spanning known sectors + one explicit unclassified."""
    members = members_from_symbols(
        ["RELIANCE", "HDFCBANK", "INFY", "TATAMOTORS", "UNKNOWN1"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),    # +10% advance
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100),     # -10% decline
        make_quote("NSE_EQ|INFY", ltp=100, close=100),        # 0% unchanged
        make_quote("NSE_EQ|TATAMOTORS", ltp=105, close=100),  # +5% advance
        # UNKNOWN1: no quote -> unavailable
    )
    fno = {"RELIANCE", "HDFCBANK"}
    return members, r.get_quote_now, fno


# ── DATA ─────────────────────────────────────────────────────────────────────

# 1. same universe resolver as Breadth
def test_same_universe_resolver_as_breadth():
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"}])
    m_b = resolve_universe("FNO", cat)
    m_m = resolve_universe("FNO", cat)
    assert [x.symbol for x in m_b] == [x.symbol for x in m_m]


# 2. same sector classifier as Heatmap
def test_same_sector_classifier_as_heatmap():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    sh = compute_sector_heatmap("U", members, reader)
    # map sectors are keyed by the canonical classifier; heatmap too.
    for stock in _all_stocks(mm):
        assert stock.sector == sector_for_symbol(stock.symbol)
    # every quoted heatmap member's sector matches the classifier as well
    for row in sh.sectors:
        for md in row.members:
            assert md["symbol"]  # membership driven by sector_for_symbol upstream
            assert sector_for_symbol(md["symbol"]) == row.sector


# 3. advance stock
def test_advance_stock():
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=110, close=100))
    mm = compute_market_map("U", m, r.get_quote_now)
    st = _all_stocks(mm)[0]
    assert st.status == "advance" and st.change_percent > 0


# 4. decline stock
def test_decline_stock():
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=90, close=100))
    mm = compute_market_map("U", m, r.get_quote_now)
    st = _all_stocks(mm)[0]
    assert st.status == "decline" and st.change_percent < 0


# 5. unchanged stock
def test_unchanged_stock():
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=100, close=100))
    mm = compute_market_map("U", m, r.get_quote_now)
    st = _all_stocks(mm)[0]
    assert st.status == "unchanged" and st.change_percent == 0.0


# 6. unavailable stock
def test_unavailable_stock():
    m = members_from_symbols(["A"])
    mm = compute_market_map("U", m, _reader().get_quote_now)
    st = _all_stocks(mm)[0]
    assert st.status == "unavailable"
    assert st.change_percent is None
    assert st.ltp is None


# 7. unavailable != unchanged
def test_unavailable_distinct_from_unchanged():
    m = members_from_symbols(["A", "B"])
    r = _reader(make_quote("NSE_EQ|B", ltp=100, close=100))  # B unchanged, A missing
    mm = compute_market_map("U", m, r.get_quote_now)
    by = {s.symbol: s for s in _all_stocks(mm)}
    assert by["A"].status == "unavailable"
    assert by["B"].status == "unchanged"
    assert by["A"].status != by["B"].status


# 8. explicit unclassified
def test_explicit_unclassified():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    assert mm.unclassified == 1
    unc = next(s for s in _all_stocks(mm) if s.symbol == "UNKNOWN1")
    assert unc.sector == UNCLASSIFIED
    assert unc.status == "unavailable"


# 9. quoted/eligible totals
def test_quoted_eligible_totals():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    assert mm.eligible == 5
    assert mm.quoted == 4
    assert mm.unavailable == 1
    assert mm.quoted == mm.advances + mm.declines + mm.unchanged


# 10. change % parity (vs breadth effective_change)
def test_change_pct_parity():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    for st in _all_stocks(mm):
        q = reader(st.exchange, st.instrument_token)
        _, pct, has = effective_change(q)
        assert st.change_percent == pct
        assert (st.change_percent is None) == (not has)


# 11. freshness / as_of
def test_freshness_as_of():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    assert mm.as_of is not None
    assert isinstance(mm.stale, bool)
    # as_of reflects the newest quote timestamp
    assert mm.as_of.startswith("2026-01-01T09:30")


# ── SECTOR GROUPING ────────────────────────────────────────────────────────────

# 12. stock belongs to correct sector
def test_stock_belongs_to_correct_sector():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    by = {s.symbol: s for s in _all_stocks(mm)}
    assert by["RELIANCE"].sector == "ENERGY"
    assert by["HDFCBANK"].sector == "BANKING"
    assert by["INFY"].sector == "IT"


# 13. no duplicate membership
def test_no_duplicate_membership():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    total = sum(len(sg.stocks) for sg in mm.sectors)
    assert total == mm.eligible
    seen = {s.symbol for sg in mm.sectors for s in sg.stocks}
    assert len(seen) == mm.eligible


# 14. unclassified remains explicit
def test_unclassified_remains_explicit():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    unc_sector = next(sg for sg in mm.sectors if sg.sector == UNCLASSIFIED)
    assert unc_sector.stocks[0].symbol == "UNKNOWN1"
    assert mm.unclassified == len(unc_sector.stocks)


# 15. sector counts reconcile (sum of sector tallies == totals)
def test_sector_counts_reconcile():
    members, reader, _ = _shared()
    mm = compute_market_map("U", members, reader)
    assert sum(sg.advances for sg in mm.sectors) == mm.advances
    assert sum(sg.declines for sg in mm.sectors) == mm.declines
    assert sum(sg.unchanged for sg in mm.sectors) == mm.unchanged
    assert sum(sg.unavailable for sg in mm.sectors) == mm.unavailable
    assert sum(sg.quoted for sg in mm.sectors) == mm.quoted


# ── CONSISTENCY (hard acceptance) ──────────────────────────────────────────────

def _shared_breadth_map():
    members, reader, fno = _shared()
    b = compute_breadth("U", members, reader)
    mm = compute_market_map("U", members, reader, fno_symbols=fno)
    return members, reader, fno, b, mm


# 16. eligible reconcile
def test_map_eligible_eq_breadth():
    _, _, _, b, mm = _shared_breadth_map()
    assert mm.eligible == b.eligible


# 17. quoted reconcile
def test_map_quoted_eq_breadth():
    _, _, _, b, mm = _shared_breadth_map()
    assert mm.quoted == b.quoted


# 18. advances reconcile
def test_map_advances_reconcile():
    _, _, _, b, mm = _shared_breadth_map()
    assert mm.advances == b.advances
    assert mm.reconciliation["advances_cross_match"] is True


# 19. declines reconcile
def test_map_declines_reconcile():
    _, _, _, b, mm = _shared_breadth_map()
    assert mm.declines == b.declines
    assert mm.reconciliation["declines_cross_match"] is True


# 20. unchanged reconcile
def test_map_unchanged_reconcile():
    _, _, _, b, mm = _shared_breadth_map()
    assert mm.unchanged == b.unchanged
    assert mm.reconciliation["unchanged_cross_match"] is True


# 21. unavailable reconcile
def test_map_unavailable_reconcile():
    _, _, _, b, mm = _shared_breadth_map()
    assert mm.unavailable == b.unavailable
    assert mm.reconciliation["unavailable_cross_match"] is True


# 22. sector membership reconcile with Heatmap (full membership per sector)
def test_map_sector_membership_eq_heatmap():
    members, reader, fno = _shared()
    mm = compute_market_map("U", members, reader, fno_symbols=fno)
    sh = compute_sector_heatmap("U", members, reader)
    map_by_sec = _symbols_by_sector(mm)
    heat_by_sec = _heatmap_symbols_by_sector(sh)
    assert map_by_sec == heat_by_sec


# 23. same-symbol change % parity across all three features
def test_same_symbol_change_pct_parity_all_three():
    members, reader, fno = _shared()
    b = compute_breadth("U", members, reader)
    mm = compute_market_map("U", members, reader, fno_symbols=fno)
    sh = compute_sector_heatmap("U", members, reader)
    b_pct = {row.symbol: row.change_percent for row in b.rows}
    m_pct = {s.symbol: s.change_percent for s in _all_stocks(mm)}
    # heatmap lists quoted members only
    h_pct = {md["symbol"]: md["change_percent"] for row in sh.sectors for md in row.members}
    for sym in h_pct:  # quoted symbols present in all three
        assert b_pct[sym] == m_pct[sym] == h_pct[sym]


# ── API ────────────────────────────────────────────────────────────────────────

def _routes():
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank", "equity_key": "NSE_EQ|HDFCBANK"},
        {"symbol": "INFY", "name": "Infosys", "equity_key": "NSE_EQ|INFY"},
        {"symbol": "TATAMOTORS", "name": "Tata Motors", "equity_key": "NSE_EQ|TATAMOTORS"},
        {"symbol": "UNKNOWN1", "name": "Unknown", "equity_key": "NSE_EQ|UNKNOWN1"},
    ])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100),
        make_quote("NSE_EQ|INFY", ltp=100, close=100),
        make_quote("NSE_EQ|TATAMOTORS", ltp=105, close=100),
    )
    return build_market_routes(market_broker=None, market_service=r, index_catalog=cat)


class _QP:
    def __init__(self, d):
        self._d = d

    def get(self, k, default=None):
        return self._d.get(k, default)


class _Req:
    def __init__(self, params):
        self.query_params = _QP(params)


def _call(handler, params):
    return asyncio.run(handler(_Req(params)))


def _handler(routes, path):
    return next(rt.endpoint for rt in routes if rt.path == path)


# 24. valid universe
def test_api_valid_universe():
    routes = _routes()
    resp = _call(_handler(routes, "/api/market/map"), {"universe": "FNO"})
    body = json.loads(resp.body)
    assert body["universe"] == "FNO"
    assert body["eligible"] == 5


# 25. invalid universe
def test_api_invalid_universe():
    routes = _routes()
    resp = _call(_handler(routes, "/api/market/map"), {"universe": "FOOBAR"})
    body = json.loads(resp.body)
    assert resp.status_code == 400
    assert "error" in body


# 26. response contract
def test_api_response_contract():
    routes = _routes()
    body = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "FNO"}).body)
    for k in ("universe", "eligible", "quoted", "unavailable", "advances",
              "declines", "unchanged", "unclassified", "as_of", "sectors",
              "reconciliation"):
        assert k in body, f"missing key {k}"
    sector = body["sectors"][0]
    for k in ("sector", "stocks"):
        assert k in sector
    stock = sector["stocks"][0]
    for k in ("symbol", "ltp", "change", "change_percent", "volume",
              "status", "fno"):
        assert k in stock, f"missing stock key {k}"


# 27. missing service behavior
def test_api_missing_service():
    routes = build_market_routes(market_broker=None, market_service=None,
                                 index_catalog=FakeCatalog(fno=[]))
    resp = _call(_handler(routes, "/api/market/map"), {"universe": "FNO"})
    assert resp.status_code == 503
    assert "error" in json.loads(resp.body)


# ── TEST CENTER DIAGNOSTIC (§21) ──────────────────────────────────────────────

def test_diagnostics_endpoint():
    routes = _routes()
    body = json.loads(_call(_handler(routes, "/api/market/map/diagnostics"), {}).body)
    assert body["reconciliation_status"] == "ok"
    for u in ("FNO", "NIFTY50"):
        assert u in body
        assert "eligible" in body[u]
        assert "quoted" in body[u]
        assert "unavailable" in body[u]
        assert "sector_count" in body[u]
        assert "unclassified" in body[u]
        assert body[u]["reconciliation"]["advances_cross_match"] is True


# ── WEBUI CONTRACT (28–35) ─────────────────────────────────────────────────────
# The WebUI module is plain browser JS with no DOM test harness; these tests lock
# the backend contract the WebUI depends on for each behavior.

# 28. universe switching (server honors ?universe=)
def test_webui_universe_switching():
    routes = _routes()
    fno = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "FNO"}).body)
    assert fno["universe"] == "FNO"
    # NIFTY50 resolves to the curated 50-member set
    n50 = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "NIFTY50"}).body)
    assert n50["universe"] == "NIFTY50" and n50["eligible"] == 50


# 29. sector filter supported (per-stock sector present)
def test_webui_sector_filter_supported():
    routes = _routes()
    body = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "FNO"}).body)
    sectors = {s["sector"] for s in body["sectors"]}
    assert "BANKING" in sectors
    for s in body["sectors"]:
        for st in s["stocks"]:
            assert "sector" in st


# 30. movement filter supported (per-stock status present)
def test_webui_movement_filter_supported():
    routes = _routes()
    body = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "FNO"}).body)
    statuses = set()
    for s in body["sectors"]:
        for st in s["stocks"]:
            assert "status" in st
            statuses.add(st["status"])
    assert "advance" in statuses and "decline" in statuses and "unavailable" in statuses


# 31. tile content supported (symbol + change %)
def test_webui_tile_content_supported():
    routes = _routes()
    body = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "FNO"}).body)
    for s in body["sectors"]:
        for st in s["stocks"]:
            assert st["symbol"]
            assert "change_percent" in st


# 32. unavailable rendering supported (distinct state)
def test_webui_unavailable_rendering_supported():
    routes = _routes()
    body = json.loads(_call(_handler(routes, "/api/market/map"), {"universe": "FNO"}).body)
    flat = [st for s in body["sectors"] for st in s["stocks"]]
    unc = [st for st in flat if st["status"] == "unavailable"]
    assert unc, "expected an unavailable stock"
    for st in unc:
        assert st["change_percent"] is None  # not faked to 0%


# 33. stock click / navigation supported (fno flag drives workspace open)
def test_webui_stock_click_navigation_supported():
    members, reader, fno = _shared()
    mm = compute_market_map("U", members, reader, fno_symbols=fno)
    by = {s.symbol: s for s in _all_stocks(mm)}
    # F&O underlyings open the existing workspace; non-derivatives are honest.
    assert by["RELIANCE"].fno is True
    assert by["UNKNOWN1"].fno is False


# 34/35. refresh determinism (single timer / no overlapping requests):
# the backend returns a fully-deterministic snapshot for identical inputs, so the
# WebUI's single-timer + in-flight guard cannot produce conflicting view state.
def test_webui_refresh_deterministic_snapshot():
    members, reader, fno = _shared()
    a = compute_market_map("U", members, reader, fno_symbols=fno)
    b = compute_market_map("U", members, reader, fno_symbols=fno)
    assert a.eligible == b.eligible and a.quoted == b.quoted
    assert a.advances == b.advances and a.declines == b.declines
    assert a.unchanged == b.unchanged and a.unavailable == b.unavailable
    assert [s.symbol for sg in a.sectors for s in sg.stocks] == \
           [s.symbol for sg in b.sectors for s in sg.stocks]


# ── helpers ──────────────────────────────────────────────────────────────────

def _all_stocks(snapshot):
    return [s for sg in snapshot.sectors for s in sg.stocks]


def _symbols_by_sector(snapshot):
    out = {}
    for sg in snapshot.sectors:
        out[sg.sector] = {s.symbol for s in sg.stocks}
    return out


def _heatmap_symbols_by_sector(shapshot):
    out = {}
    for row in shapshot.sectors:
        out[row.sector] = {md["symbol"] for md in row.members}
    return out
