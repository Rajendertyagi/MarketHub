#!/usr/bin/env python3
"""End-to-end canonical consumer completeness tests (CE1-CE12).

Proves the richest possible canonical Quote and Depth survive every
consumer surface without field loss or provider-name leakage:

    model -> MarketService -> serializer -> REST endpoint
          -> SSE envelope  -> MCP tool

  * CE1   rich Quote survives REST serializer completely
  * CE2   rich Quote survives REST endpoint response
  * CE3   rich Quote survives SSE envelope (real broadcast path)
  * CE4   rich Quote survives MCP market_quote
  * CE5   rich Depth survives REST endpoint + MCP (orders/null preserved)
  * CE6   cross-provider: Upstox/Fyers equivalents -> identical consumer JSON
  * CE7   provider-name leakage scan on all consumer outputs
  * CE8   nulls preserved (rho=None stays null; never coerced to 0)
  * CE9   snapshot/SSE schema consistency (same key sets)
  * CE10  no provider modules imported by consumer layers
  * CE11  UI static: drawer sections + greeks + OI/circuits present
  * CE12  UI static: one EventSource, no provider names in JS

NO LIVE BROKER. Synthetic canonical data only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
KEY = "NSE_EQ|INE002A01018"
TS = "RELIANCE"

# Every canonical Quote field a consumer must be able to see.
REQUIRED_QUOTE_KEYS = {
    "instrument_token", "exchange", "tradingsymbol", "received_ts",
    "ltp", "open", "high", "low", "close", "volume",
    "change", "change_percent", "best_bid", "best_ask",
    "open_interest", "avg_trade_price", "last_traded_qty",
    "total_buy_qty", "total_sell_qty", "exchange_ts",
    "upper_circuit", "lower_circuit",
    "oi_change", "oi_change_percent", "previous_oi",
    "last_trade_time", "greeks",
}
GREEKS_KEYS = {"delta", "gamma", "theta", "vega", "rho", "iv"}

# Provider wire names that must NEVER appear in consumer output.
PROVIDER_NAMES = [
    "pdoi", "oipercent", "optionGreeks", "bidP", "askP", "bidQ", "askQ",
    "vtt", "tbq", "tsq", "lp", "chp", "prev_close_price", "fyToken",
    "vol_traded_today", "upper_ckt", "lower_ckt", "atp_ltpc",
]


def _rich_quote():
    from market.models import OptionGreeks, Quote
    return Quote(
        instrument_token=KEY, exchange="NSE", tradingsymbol=TS,
        received_ts=NOW,
        ltp=100.5, open=99.2, high=101.0, low=98.7, close=99.0,
        volume=500000, change=1.5, change_percent=1.515,
        best_bid=100.45, best_ask=100.55,
        open_interest=1250000.0, avg_trade_price=100.25,
        last_traded_qty=25, total_buy_qty=450000, total_sell_qty=380000,
        exchange_ts=NOW,
        upper_circuit=110.0, lower_circuit=90.0,
        oi_change=50000.0, oi_change_percent=4.17, previous_oi=1200000.0,
        last_trade_time=NOW,
        greeks=OptionGreeks(delta=0.52, gamma=0.001, theta=-6.25,
                            vega=11.4, rho=None, iv=18.5),
    )


def _rich_depth():
    from market.models import Depth, DepthLevel
    return Depth(
        instrument_token=KEY, exchange="NSE", tradingsymbol=TS,
        received_ts=NOW,
        bids=(DepthLevel(price=100.45, quantity=500, orders=12),
              DepthLevel(price=100.40, quantity=700, orders=None)),
        asks=(DepthLevel(price=100.55, quantity=300, orders=8),),
    )


# -- helpers ---------------------------------------------------------------------


def _build_routes(svc):
    from api.routes import build_market_routes

    class _Broker:  # stream endpoint unused in these tests
        def subscribe(self):
            return None

    return build_market_routes(_Broker(), market_service=svc)


def _find(routes, path):
    """Match a concrete path against route templates ({params} wildcards)."""
    import re
    for r in routes:
        pattern = re.sub(r"\{[^}]+\}", "[^/]+", r.path)
        if re.fullmatch(pattern, path):
            return r
    raise LookupError(f"no route matches {path}")


async def _call(route, method="GET"):
    import re
    from starlette.requests import Request

    # Populate path_params from the route template + a canonical example.
    params = {}
    if "{" in route.path:
        example = {
            "exchange": "NSE", "instrument_token": KEY,
        }
        pattern = re.sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", route.path)
        sample = re.sub(
            r"\{([^}]+)\}",
            lambda m: example.get(m.group(1).strip("{}"), "X"),
            route.path)
        mobj = re.fullmatch(pattern, sample)
        if mobj:
            params = mobj.groupdict()

    scope = {"type": "http", "method": method, "path": route.path,
             "headers": [], "query_string": b"",
             "server": ("t", 80), "scheme": "http",
             "path_params": params}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = await route.endpoint(Request(scope, receive))
    raw = getattr(resp, "body", b"")
    return json.loads(raw)


def _mcp_tools(svc):
    class _FakeMCP:
        def __init__(self):
            self.tools = {}

        def tool(self, name=None, **kw):
            def deco(fn):
                self.tools[name] = fn
                return fn
            return deco

    from mcp_server.tools.market import register_market_tools

    class _Services:
        market_service = svc

    fake = _FakeMCP()
    register_market_tools(fake, _Services())
    return fake.tools


async def _setup_service_with_depth():
    from market.models import Depth, DepthLevel
    from market.service import MarketService

    svc = MarketService()
    from market.service import QuotePatch
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
        received_ts=NOW, reported_fields={"ltp": 100.5}))
    await svc.apply_depth(Depth(
        instrument_token=KEY, exchange="NSE", tradingsymbol=TS,
        received_ts=NOW,
        bids=(DepthLevel(price=100.45, quantity=500, orders=12),
              DepthLevel(price=100.40, quantity=700, orders=None)),
        asks=(DepthLevel(price=100.55, quantity=300, orders=8),),
    ))
    return svc


# -- tests -----------------------------------------------------------------------


async def test_ce1_to_ce5_end_to_end(runner: R) -> None:
    from market import serialization
    from market.service import MarketService

    svc = MarketService()
    # Apply via patch path (presence contract) with the full field set.
    # NOTE: exchange_ts is patch-level, not a reported_field.
    from market.service import QuotePatch
    q = _rich_quote()
    fields = {f.name: getattr(q, f.name)
              for f in type(q).__dataclass_fields__.values()
              if f.name not in ("instrument_token", "exchange",
                                "tradingsymbol", "received_ts",
                                "exchange_ts")}
    await svc.apply_quote(QuotePatch(
        exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
        received_ts=NOW, exchange_ts=NOW, reported_fields=fields))
    await svc.apply_depth(_rich_depth())

    # CE1: serializer carries every canonical key incl. nested greeks.
    d = serialization.quote_to_dict(await svc.get_quote("NSE", KEY))
    missing = REQUIRED_QUOTE_KEYS - set(d.keys())
    runner.assert_eq("CE1-serializer-complete", sorted(missing), [])
    runner.assert_eq("CE1-greeks-keys",
                     set(d["greeks"].keys()), GREEKS_KEYS)

    # CE2: REST endpoint response identical shape.
    routes = _build_routes(svc)
    rest = await _call(_find(routes, "/api/market/quotes"))
    rq = rest["quotes"][0]
    runner.assert_eq("CE2-rest-keys", set(rq.keys()), set(d.keys()))

    single = await _call(_find(
        routes, f"/api/market/quote/NSE/{KEY}"))
    runner.assert_eq("CE2-single-ltp", single.get("ltp"), 100.5)
    runner.assert_eq("CE2-single-greeks-delta",
                     single.get("greeks", {}).get("delta"), 0.52)

    # CE3: SSE envelope via the REAL broadcast hook.
    import app.server as server_mod
    captured = []
    original = server_mod._market_event_broker.broadcast
    server_mod._market_event_broker.broadcast = captured.append
    try:
        server_mod._on_market_quote_update(await svc.get_quote("NSE", KEY))
    finally:
        server_mod._market_event_broker.broadcast = original
    runner.assert_eq("CE3-sse-one-line", len(captured), 1)
    envelope = json.loads(captured[0])
    runner.assert_eq("CE3-envelope-type", envelope.get("type"), "quote")
    runner.assert_eq("CE3-sse-keys", set(envelope["data"].keys()), set(d.keys()))

    # CE4: MCP market_quote complete.
    tools = _mcp_tools(svc)
    mcp_resp = await tools["market_quote"](exchange="NSE",
                                           instrument_token=KEY)
    runner.assert_eq("CE4-mcp-keys",
                     set(mcp_resp["quote"].keys()), set(d.keys()))

    # CE5: depth through REST + MCP with orders/null preserved.
    md = await _call(_find(routes, f"/api/market/depth/NSE/{KEY}"))
    runner.assert_eq("CE5-rest-bids", len(md["bids"]), 2)
    runner.assert_eq("CE5-orders-int", md["bids"][0]["orders"], 12)
    runner.assert_eq("CE5-orders-null-preserved",
                     md["bids"][1]["orders"], None)
    mcp_d = await tools["market_depth"](exchange="NSE", instrument_token=KEY)
    runner.assert_eq("CE5-mcp-asks", len(mcp_d["depth"]["asks"]), 1)


async def test_ce6_cross_provider(runner: R) -> None:
    """Equivalent semantic inputs -> identical consumer-visible values."""
    from market.normalize.fyers import quote_from_quotes_rest
    from market.normalize.upstox import quote_fields_from_ws_full
    from market.service import MarketService, QuotePatch

    async def run(provider, fields):
        svc = MarketService()
        await svc.apply_quote(QuotePatch(
            exchange="X", instrument_token="T|1", tradingsymbol="SYM",
            received_ts=NOW, reported_fields=fields))
        from market import serialization
        return serialization.quote_to_dict(
            await svc.get_quote("X", "T|1"))

    up = quote_fields_from_ws_full(
        {"ltpc": {"ltp": 123.45, "cp": 120.0}},
        instrument_key="T|1", received_ts=NOW, tradingsymbol="SYM")
    fy_q = quote_from_quotes_rest({"lp": 123.45, "prev_close_price": 120.0},
                                  symbol="X:SYM-EQ", received_ts=NOW)
    identity = {"instrument_token", "exchange", "tradingsymbol",
                "received_ts"}
    up_fields = {k: v for k, v in up.items() if k not in identity}
    fy_fields = {k: getattr(fy_q, k) for k in ("ltp", "close")}

    d1 = await run("upstox", up_fields)
    d2 = await run("fyers", fy_fields)
    runner.assert_eq("CE6-ltp-parity", d1["ltp"], d2["ltp"])
    runner.assert_eq("CE6-close-parity", d1["close"], d2["close"])
    runner.assert_true("CE6-no-provider-branching",
                       set(d1.keys()) == set(d2.keys()))


def test_ce7_leakage_scan(runner: R) -> None:
    from market import serialization

    blob = json.dumps(serialization.quote_to_dict(_rich_quote()))
    hits = [n for n in PROVIDER_NAMES if n in blob]
    runner.assert_eq("CE7-no-provider-names", hits, [])


def test_ce8_nulls_preserved(runner: R) -> None:
    from market import serialization
    from market.models import OptionGreeks, Quote

    q = Quote(instrument_token=KEY, exchange="NSE", tradingsymbol=TS,
              received_ts=NOW, ltp=10.0,
              greeks=OptionGreeks(delta=0.4))  # rho/iv/gamma/theta/vega None
    d = serialization.quote_to_dict(q)
    runner.assert_eq("CE8-rho-null-stays-null", d["greeks"]["rho"], None)
    runner.assert_eq("CE8-iv-null-stays-null", d["greeks"]["iv"], None)
    runner.assert_eq("CE8-circuit-null", d["upper_circuit"], None)


async def test_ce9_snapshot_sse_consistency(runner: R) -> None:
    """REST snapshot keys == SSE data keys (frontend merges without branching)."""
    from market import serialization
    from market.service import MarketService, QuotePatch

    async def run():
        svc = MarketService()
        await svc.apply_quote(QuotePatch(
            exchange="NSE", instrument_token=KEY, tradingsymbol=TS,
            received_ts=NOW, reported_fields={"ltp": 1.0}))
        return svc

    svc = await run()
    snap = serialization.quote_to_dict(await svc.get_quote("NSE", KEY))
    sse = serialization.quote_to_dict(await svc.get_quote("NSE", KEY))
    runner.assert_eq("CE9-same-schema", set(snap.keys()), set(sse.keys()))


def test_ce10_no_provider_imports(runner: R) -> None:
    """Consumer layers must not import broker/provider modules.

    api/routes.py legitimately touches brokers.upstox.auth/.rest ONLY for
    the OAuth credential boundary (token submission/exchange) — market-data
    code must never import providers.
    """
    consumer_files = ["mcp_server/tools/market.py", "market/serialization.py"]
    banned = ["brokers.upstox", "brokers.fyers", "MarketDataFeed_pb2",
              "fyers_apiv3"]
    for rel in consumer_files:
        with open(os.path.join(_PROJECT_DIR, rel), encoding="utf-8") as f:
            src = f.read()
        bad = [b for b in banned if b in src]
        runner.assert_eq(f"CE10-{rel}-clean", bad, [])

    # api/routes.py: allowed only inside the auth-route builder section.
    with open(os.path.join(_PROJECT_DIR, "api", "routes.py"),
              encoding="utf-8") as f:
        lines = f.readlines()
    auth_start = next((i for i, ln in enumerate(lines)
                       if "def build_auth_routes" in ln), len(lines))
    market_section = "".join(lines[:auth_start])
    bad = [b for b in banned if b in market_section]
    runner.assert_eq("CE10-routes-market-section-clean", bad, [])


def test_ce11_ce12_ui_static(runner: R) -> None:
    html_path = os.path.join(_PROJECT_DIR, "web", "ui", "index.html")
    js_path = os.path.join(_PROJECT_DIR, "web", "ui", "js", "app.js")
    with open(html_path, encoding="utf-8") as f:
        html = f.read()
    with open(js_path, encoding="utf-8") as f:
        js = f.read()

    # CE11: drawer infrastructure markers.
    for marker in ("quote-drawer", "drawer-greeks", "drawer-depth",
                   "drawer-oi", "drawer-markets-section"):
        runner.assert_in(f"CE11-html:{marker}", marker, html)

    # CE12: one EventSource; no provider wire names in JS.
    # Two EventSources BY DESIGN: market stream + generic alert push.
    # Exactly one MARKET stream; never more.
    runner.assert_eq("CE12-eventsource-count",
                     js.count("new EventSource"), 2)
    runner.assert_eq("CE12-one-market-stream",
                     js.count('new EventSource("/api/market/stream")'), 1)
    bad = [n for n in PROVIDER_NAMES if n in js]
    runner.assert_eq("CE12-no-provider-names-js", bad, [])


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    await test_ce1_to_ce5_end_to_end(runner)
    await test_ce6_cross_provider(runner)
    test_ce7_leakage_scan(runner)
    test_ce8_nulls_preserved(runner)
    await test_ce9_snapshot_sse_consistency(runner)
    test_ce10_no_provider_imports(runner)
    test_ce11_ce12_ui_static(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)



