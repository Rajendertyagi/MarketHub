"""Sector Heatmap — canonical aggregation tests (§23, items 18–30)."""

import pytest

from market.sector_classification import sector_for_symbol, UNCLASSIFIED, known_sectors
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


# 18. canonical sector classification
def test_canonical_sector_classification():
    assert sector_for_symbol("RELIANCE") == "ENERGY"
    assert sector_for_symbol("HDFCBANK") == "BANKING"
    assert sector_for_symbol("INFY") == "IT"


# 19. unclassified security handling
def test_unclassified_security():
    assert sector_for_symbol("TOTALLY_UNKNOWN_XYZ") == UNCLASSIFIED
    assert sector_for_symbol(None) == UNCLASSIFIED
    assert sector_for_symbol("") == UNCLASSIFIED


# 20. sector constituent count
def test_sector_constituent_count():
    m = members_from_symbols(["RELIANCE", "HDFCBANK", "INFY", "UNKNOWN1"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100),
        make_quote("NSE_EQ|INFY", ltp=100, close=100),
        # UNKNOWN1 has no quote
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    assert sec["ENERGY"].constituent_count == 1
    assert sec["BANKING"].constituent_count == 1
    assert sec["IT"].constituent_count == 1
    assert sec[UNCLASSIFIED].constituent_count == 1


# 21. advances / declines per sector
def test_sector_advances_declines():
    m = members_from_symbols(["RELIANCE", "HDFCBANK"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),  # ENERGY advance
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100),   # BANKING decline
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    assert sec["ENERGY"].advances == 1 and sec["ENERGY"].declines == 0
    assert sec["BANKING"].declines == 1 and sec["BANKING"].advances == 0


# 22. equal-weight average change
def test_equal_weight_average_change():
    m = members_from_symbols(["RELIANCE", "ONGC"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100, change_percent=10.0),
        make_quote("NSE_EQ|ONGC", ltp=99, close=100, change_percent=-1.0),
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    # both ENERGY: (10.0 + -1.0)/2 = 4.5
    assert sec["ENERGY"].average_change_percent == pytest.approx(4.5, abs=0.01)


# 23. median implemented
def test_median_change():
    m = members_from_symbols(["A", "B", "C"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100, change_percent=10.0),
        make_quote("NSE_EQ|B", ltp=105, close=100, change_percent=5.0),
        make_quote("NSE_EQ|C", ltp=90, close=100, change_percent=-10.0),
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    # all unclassified (unknown symbols); median of [10,5,-10] = 5
    sec = {x.sector: x for x in s.sectors}
    assert sec[UNCLASSIFIED].median_change_percent == pytest.approx(5.0, abs=0.01)


# 24. unavailable handling
def test_unavailable_handling():
    m = members_from_symbols(["RELIANCE"])
    s = compute_sector_heatmap("U", m, _reader().get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    assert sec["ENERGY"].unavailable == 1
    assert sec["ENERGY"].quoted == 0
    assert sec["ENERGY"].average_change_percent is None


# 25. top gainer
def test_top_gainer():
    m = members_from_symbols(["A", "B"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=120, close=100, change_percent=20.0),
        make_quote("NSE_EQ|B", ltp=110, close=100, change_percent=10.0),
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    assert sec[UNCLASSIFIED].top_gainer["symbol"] == "A"
    assert sec[UNCLASSIFIED].top_gainer["change_percent"] == pytest.approx(20.0)


# 26. top loser
def test_top_loser():
    m = members_from_symbols(["A", "B"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=120, close=100, change_percent=20.0),
        make_quote("NSE_EQ|B", ltp=80, close=100, change_percent=-20.0),
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    assert sec[UNCLASSIFIED].top_loser["symbol"] == "B"


# 27. sector drill-down (members)
def test_sector_drilldown_members():
    m = members_from_symbols(["RELIANCE", "ONGC"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100),
        make_quote("NSE_EQ|ONGC", ltp=99, close=100),
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now, include_members=True)
    sec = {x.sector: x for x in s.sectors}
    assert len(sec["ENERGY"].members) == 2
    syms = {x["symbol"] for x in sec["ENERGY"].members}
    assert syms == {"RELIANCE", "ONGC"}


# 28. API contract
def test_api_contract():
    from api.routes import build_market_routes
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"},
    ])
    r = _reader(make_quote("NSE_EQ|RELIANCE", ltp=2500, close=2400))
    routes = build_market_routes(
        market_broker=None, market_service=r, index_catalog=cat)
    handler = next(rt.endpoint for rt in routes
                   if rt.path == "/api/market/sector-heatmap")
    class QP:
        def get(self, k, d=None):
            return {"universe": "FNO", "members": "1"}.get(k, d)
    class Req:
        query_params = QP()
    import asyncio, json
    resp = asyncio.run(handler(Req()))
    body = json.loads(resp.body)
    for k in ("universe", "sector_count", "classified_count", "unclassified_count",
              "weighting", "sectors", "reconciliation"):
        assert k in body, f"missing {k}"
    assert body["weighting"] == "equal"


# 29. WebUI rendering contract (data the UI consumes)
def test_webui_rendering_contract():
    m = members_from_symbols(["RELIANCE", "HDFCBANK"])
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=110, close=100, volume=1_200_000),
        make_quote("NSE_EQ|HDFCBANK", ltp=90, close=100, volume=900_000),
    )
    s = compute_sector_heatmap("U", m, r.get_quote_now, fno_symbols={"RELIANCE"})
    d = s.to_dict()
    # The WebUI reads these keys to render tiles + drill-down + treemap.
    assert "sectors" in d and len(d["sectors"]) >= 2
    for sec in d["sectors"]:
        assert "sector" in sec and "average_change_percent" in sec
        assert "top_gainer" in sec and "top_loser" in sec and "members" in sec
    # Treemap consumes per-stock volume (size) + fno (click-to-workspace).
    all_members = [mm for sec in d["sectors"] for mm in sec["members"]]
    for mm in all_members:
        assert "volume" in mm
        assert "fno" in mm
    rel = next(mm for mm in all_members if mm["symbol"] == "RELIANCE")
    assert rel["volume"] == 1_200_000
    assert rel["fno"] is True
    hdfc = next(mm for mm in all_members if mm["symbol"] == "HDFCBANK")
    assert hdfc["fno"] is False


# 30b. fno flag defaults to False when no fno_symbols supplied
def test_fno_flag_default_false():
    m = members_from_symbols(["RELIANCE"])
    r = _reader(make_quote("NSE_EQ|RELIANCE", ltp=110, close=100))
    s = compute_sector_heatmap("U", m, r.get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    rel = next(mm for mm in sec["ENERGY"].members if mm["symbol"] == "RELIANCE")
    assert rel["fno"] is False


# 30. missing quote behavior
def test_missing_quote_behavior():
    m = members_from_symbols(["RELIANCE"])
    s = compute_sector_heatmap("U", m, _reader().get_quote_now)
    sec = {x.sector: x for x in s.sectors}
    assert sec["ENERGY"].quoted == 0
    assert sec["ENERGY"].unavailable == 1
    assert s.unclassified_count == 0  # RELIANCE is classified, just no quote


def test_known_sectors_excludes_unclassified():
    ks = known_sectors()
    assert UNCLASSIFIED not in ks
    assert "ENERGY" in ks and "BANKING" in ks
