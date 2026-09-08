"""Market Breadth — canonical aggregation tests (§22, items 1–17)."""

import pytest

from market.breadth import compute_breadth, effective_change
from market.market_universe import Member, resolve_universe
from test.helpers.market_breadth_fakes import (
    FakeCatalog,
    FakeQuoteReader,
    make_quote,
    members_from_symbols,
)


def _reader(*quotes):
    return FakeQuoteReader({q.instrument_token: q for q in quotes})


# 1. advance
def test_advance():
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=110, close=100))
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.advances == 1 and s.declines == 0 and s.unchanged == 0


# 2. decline
def test_decline():
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=90, close=100))
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.declines == 1 and s.advances == 0


# 3. unchanged
def test_unchanged():
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=100, close=100))
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.unchanged == 1 and s.advances == 0 and s.declines == 0


# 4. unavailable (no quote)
def test_unavailable():
    m = members_from_symbols(["A"])
    s = compute_breadth("U", m, _reader().get_quote_now)
    assert s.unavailable == 1 and s.quoted == 0


# 5. unavailable != unchanged
def test_unavailable_distinct_from_unchanged():
    m = members_from_symbols(["A", "B"])
    r = _reader(make_quote("NSE_EQ|A", ltp=100, close=100))  # B has no quote
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.unchanged == 1 and s.unavailable == 1
    # A stock with no quote is classified "unavailable", never "unchanged".
    row_a = next(x for x in s.rows if x.symbol == "A")
    row_b = next(x for x in s.rows if x.symbol == "B")
    assert row_a.status == "unchanged"
    assert row_b.status == "unavailable"
    assert row_b.status != row_a.status


# 6. counts reconcile to eligible
def test_counts_reconcile_to_eligible():
    m = members_from_symbols(["A", "B", "C", "D"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),
        make_quote("NSE_EQ|B", ltp=90, close=100),
        make_quote("NSE_EQ|C", ltp=100, close=100),
        # D unavailable
    )
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.advances + s.declines + s.unchanged + s.unavailable == s.eligible == 4
    assert s.quoted == 3


# 7. percentage math
def test_percentage_math():
    m = members_from_symbols(["A", "B", "C"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),   # advance
        make_quote("NSE_EQ|B", ltp=90, close=100),    # decline
        make_quote("NSE_EQ|C", ltp=100, close=100),   # unchanged
    )
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.advance_percent == pytest.approx(33.33, abs=0.1)
    assert s.decline_percent == pytest.approx(33.33, abs=0.1)
    assert s.advance_percent + s.decline_percent + (s.unchanged / s.quoted * 100) == pytest.approx(100.0, abs=0.1)


# 8. A/D ratio
def test_ad_ratio():
    m = members_from_symbols(["A", "B", "C"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),
        make_quote("NSE_EQ|B", ltp=120, close=100),
        make_quote("NSE_EQ|C", ltp=90, close=100),
    )
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.ad_ratio == pytest.approx(2 / 1)


# 9. zero-decline ratio semantics
def test_zero_decline_ratio():
    m = members_from_symbols(["A", "B"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),
        make_quote("NSE_EQ|B", ltp=105, close=100),
    )
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.declines == 0
    assert s.ad_ratio is None  # not 0, not inf — explicitly undefined


# 10. net advances
def test_net_advances():
    m = members_from_symbols(["A", "B", "C"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),
        make_quote("NSE_EQ|B", ltp=120, close=100),
        make_quote("NSE_EQ|C", ltp=90, close=100),
    )
    s = compute_breadth("U", m, r.get_quote_now)
    assert s.net_advances == 1  # 2 advances - 1 decline


# 11. universe filtering
def test_universe_filtering_fno_via_catalog():
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank", "equity_key": "NSE_EQ|HDFCBANK"},
    ])
    members = resolve_universe("FNO", cat)
    assert {m.symbol for m in members} == {"RELIANCE", "HDFCBANK"}


# 12. NIFTY50 does not include unrelated equity
def test_nifty50_no_unrelated_equity():
    cat = FakeCatalog(search_map={
        "RELIANCE": [{"instrument_token": "NSE_EQ|RELIANCE", "name": "Reliance"}],
    })
    members = resolve_universe("NIFTY50", cat)
    syms = {m.symbol for m in members}
    # NIFTY50 curated list only — must NOT contain an arbitrary non-member.
    assert "SOME_RANDOM_STOCK" not in syms
    assert "RELIANCE" in syms  # it IS a curated member and resolves
    # every member is from the curated list exactly
    from market import index_constituents as idx
    assert syms <= set(idx.INDEX_MEMBERS["NIFTY50"])


# 13. F&O universe breadth
def test_fno_universe_breadth():
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"},
        {"symbol": "HDFCBANK", "name": "HDFC Bank", "equity_key": "NSE_EQ|HDFCBANK"},
    ])
    members = resolve_universe("FNO", cat)
    r = _reader(
        make_quote("NSE_EQ|RELIANCE", ltp=2500, close=2400),
        make_quote("NSE_EQ|HDFCBANK", ltp=1500, close=1550),
    )
    s = compute_breadth("FNO", members, r.get_quote_now)
    assert s.eligible == 2 and s.advances == 1 and s.declines == 1


# 14. stale / quote freshness projection
def test_stale_freshness_projection():
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(minutes=30)
    m = members_from_symbols(["A"])
    r = _reader(make_quote("NSE_EQ|A", ltp=110, close=100, received_ts=old))
    s = compute_breadth("U", m, r.get_quote_now, stale_minutes=5)
    assert s.stale is True
    recent = datetime.now(timezone.utc)
    r2 = _reader(make_quote("NSE_EQ|A", ltp=110, close=100, received_ts=recent))
    s2 = compute_breadth("U", m, r2.get_quote_now, stale_minutes=5)
    assert s2.stale is False


# 15. API contract (route returns expected keys)
def test_api_contract():
    from api.routes import build_market_routes
    cat = FakeCatalog(fno=[
        {"symbol": "RELIANCE", "name": "Reliance", "equity_key": "NSE_EQ|RELIANCE"},
    ])
    r = _reader(make_quote("NSE_EQ|RELIANCE", ltp=2500, close=2400))
    routes = build_market_routes(
        market_broker=None, market_service=r, index_catalog=cat)
    handler = next(rt.endpoint for rt in routes if rt.path == "/api/market/breadth")
    # Build a minimal fake Request
    class QP:
        def get(self, k, d=None):
            return {"universe": "FNO"}.get(k, d)
    class Req:
        query_params = QP()
    import asyncio
    resp = asyncio.run(handler(Req()))
    import json
    body = json.loads(resp.body)
    for k in ("universe", "eligible", "quoted", "advances", "declines",
              "unchanged", "unavailable", "advance_percent", "decline_percent",
              "ad_ratio", "net_advances", "as_of", "rows"):
        assert k in body, f"missing key {k}"
    assert body["universe"] == "FNO"


# 16. detail rows
def test_detail_rows():
    m = members_from_symbols(["A", "B"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),
        make_quote("NSE_EQ|B", ltp=90, close=100),
    )
    s = compute_breadth("U", m, r.get_quote_now)
    assert len(s.rows) == 2
    row_a = next(x for x in s.rows if x.symbol == "A")
    assert row_a.status == "advance" and row_a.change == 10


# 17. sorting / filtering (logic mirrored by WebUI)
def test_sorting_filtering():
    m = members_from_symbols(["A", "B", "C", "D"])
    r = _reader(
        make_quote("NSE_EQ|A", ltp=110, close=100),
        make_quote("NSE_EQ|B", ltp=90, close=100),
        make_quote("NSE_EQ|C", ltp=100, close=100),
    )
    s = compute_breadth("U", m, r.get_quote_now)
    adv = [x for x in s.rows if x.status == "advance"]
    assert len(adv) == 1 and adv[0].symbol == "A"
    by_pct = sorted(s.rows, key=lambda x: (x.change_percent or -1e9), reverse=True)
    assert by_pct[0].symbol == "A"


def test_effective_change_derives_from_ltp_close():
    q = make_quote("X", ltp=105, close=100)  # no explicit change
    ch, pct, has = effective_change(q)
    assert has and ch == 5 and pct == pytest.approx(5.0)
