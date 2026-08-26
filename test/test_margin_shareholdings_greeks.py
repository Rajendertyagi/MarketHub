#!/usr/bin/env python3
"""Broker gap-closure tests (BG1-BG8): Margin / Shareholdings / Option Greeks.

Wires the three new ProviderMarketData methods + REST routes against synthetic
Upstox REST payloads (NO LIVE BROKER). Validates:
  * normalizer output shapes (canonical models),
  * service-method -> REST call correctness (method/url/params/body),
  * route registration, and one handler round-trip per endpoint.

Mirrors the function/runner style of test_options_analytics.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

from market.models import (  # noqa: E402
    MarginBasket,
    Shareholdings,
    OptionGreekSnapshot,
)
from app.market_data import ProviderMarketData  # noqa: E402
from api.product_routes import build_market_data_routes  # noqa: E402
from starlette.requests import Request  # noqa: E402


# --------------------------------------------------------------------------
# Fakes: stand in for the live Upstox REST client + credentials.
# --------------------------------------------------------------------------

class _FakeCreds:
    access_token = "test-token"


class _FakeRest:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def authenticated_request(self, *, method, url, access_token,
                                   json_body=None, params=None):
        self.calls.append({
            "method": method,
            "url": url,
            "access_token": access_token,
            "json_body": json_body,
            "params": params,
        })
        return self._payload


def _make_md(payload):
    rest = _FakeRest(payload)
    md = ProviderMarketData(upstox_auth_context_fn=lambda: (rest, _FakeCreds()))
    return md, rest


class _StubProvider:
    """Stand-in passed only at route-build time (handlers not invoked)."""


# --------------------------------------------------------------------------
# Synthetic request / response helpers for handler round-trips.
# --------------------------------------------------------------------------

def _make_request(method, path, *, query_string=b"", body=None):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": query_string,
    }

    def _receive(raw):
        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}
        return receive

    raw = body.encode() if isinstance(body, str) else (body or b"")
    return Request(scope, receive=_receive(raw))


def _response_json(response):
    return json.loads(response.body.decode())


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

async def test_bg1_margin_service(runner: R) -> None:
    payload = {
        "status": "success",
        "data": {
            "required_margin": 100.0,
            "final_margin": 95.0,
            "margins": [
                {
                    "instrument_key": "NSE_FO|X",
                    "span_margin": 50.0,
                    "exposure_margin": 20.0,
                    "equity_margin": 10.0,
                    "net_buy_premium": 5.0,
                    "additional_margin": 0.0,
                    "tender_margin": 0.0,
                    "total_margin": 75.0,
                },
            ],
        },
    }
    md, rest = _make_md(payload)
    instruments = [{
        "exchange": "NSE", "symbol": "RELIANCE",
        "transaction_type": "BUY", "quantity": 1, "product": "I",
        "lot_size": 1, "price": 2500, "trigger_price": 0,
    }]
    basket = await md.margin(instruments=instruments)

    runner.assert_true("BG1-is-basket", isinstance(basket, MarginBasket))
    runner.assert_eq("BG1-required", basket.required_margin, 100.0)
    runner.assert_eq("BG1-final", basket.final_margin, 95.0)
    runner.assert_eq("BG1-n-entries", len(basket.entries), 1)
    runner.assert_eq("BG1-total", basket.entries[0].total_margin, 75.0)
    runner.assert_eq("BG1-equity", basket.entries[0].equity_margin, 10.0)

    runner.assert_eq("BG1-call-count", len(rest.calls), 1)
    call = rest.calls[0]
    runner.assert_eq("BG1-method", call["method"], "POST")
    runner.assert_eq("BG1-url", call["url"],
                     "https://api.upstox.com/v2/charges/margin")
    runner.assert_eq("BG1-token", call["access_token"], "test-token")
    runner.assert_in("BG1-body-has-instruments", "instruments", call["json_body"])
    runner.assert_eq("BG1-body-instruments", call["json_body"]["instruments"],
                     instruments)
    runner.assert_eq("BG1-item-type", call["json_body"]["item_type"], "SECURITY")
    runner.assert_eq("BG1-margin-cat", call["json_body"]["margin_category"],
                     "intraday")


async def test_bg2_margin_validation(runner: R) -> None:
    md, _ = _make_md({})
    raised = False
    try:
        await md.margin(instruments=[])
    except Exception as exc:  # noqa: BLE001 - intentionally broad
        raised = True
        runner.assert_in("BG2-err-msg", "instruments", str(exc))
    runner.assert_true("BG2-raised", raised)


async def test_bg3_shareholdings_service(runner: R) -> None:
    payload = {
        "status": "success",
        "data": [
            {
                "category": "Promoters",
                "history": [
                    {"period": "Jun 2024", "value": 52.31},
                    {"period": "Mar 2024", "value": 52.10},
                ],
            },
            {
                "category": "Public",
                "history": [{"period": "Jun 2024", "value": 30.0}],
            },
        ],
    }
    md, rest = _make_md(payload)
    sh = await md.shareholdings(isin="INE123456789")

    runner.assert_true("BG3-is-sh", isinstance(sh, Shareholdings))
    runner.assert_eq("BG3-isin", sh.isin, "INE123456789")
    runner.assert_eq("BG3-n-cats", len(sh.categories), 2)
    runner.assert_eq("BG3-cat0", sh.categories[0].category, "Promoters")
    runner.assert_eq("BG3-cat0-n", len(sh.categories[0].history), 2)
    runner.assert_eq("BG3-cat0-val", sh.categories[0].history[0].value, 52.31)

    runner.assert_eq("BG3-call-count", len(rest.calls), 1)
    call = rest.calls[0]
    runner.assert_eq("BG3-method", call["method"], "GET")
    runner.assert_eq(
        "BG3-url", call["url"],
        "https://api.upstox.com/v2/fundamentals/INE123456789/share-holdings")


async def test_bg4_option_greeks_service(runner: R) -> None:
    payload = {
        "status": "success",
        "data": {
            "NSE_FO|43885": {
                "last_price": 100.0, "ltq": 5, "volume": 1000, "cp": 98.0,
                "iv": 18.5, "vega": 0.5, "gamma": 0.02, "theta": -1.2,
                "delta": 0.55, "oi": 5000,
                # intentionally no rho -> must normalize to None
            },
        },
    }
    md, rest = _make_md(payload)
    snap = await md.option_greeks(instrument_keys="NSE_FO|43885")

    runner.assert_true("BG4-is-snap", isinstance(snap, OptionGreekSnapshot))
    runner.assert_eq("BG4-n-entries", len(snap.entries), 1)
    e = snap.entries[0]
    runner.assert_eq("BG4-key", e.instrument_key, "NSE_FO|43885")
    runner.assert_eq("BG4-delta", e.delta, 0.55)
    runner.assert_eq("BG4-gamma", e.gamma, 0.02)
    runner.assert_eq("BG4-theta", e.theta, -1.2)
    runner.assert_eq("BG4-vega", e.vega, 0.5)
    runner.assert_eq("BG4-iv", e.iv, 18.5)
    runner.assert_eq("BG4-oi", e.oi, 5000)
    runner.assert_eq("BG4-vol", e.volume, 1000)
    runner.assert_eq("BG4-cp", e.previous_close, 98.0)
    runner.assert_true("BG4-rho-none", e.rho is None)

    call = rest.calls[0]
    runner.assert_eq("BG4-method", call["method"], "GET")
    runner.assert_eq("BG4-url", call["url"],
                     "https://api.upstox.com/v3/market-quote/option-greek")
    runner.assert_eq("BG4-params", call["params"],
                     {"instrument_key": "NSE_FO|43885"})

    # list form -> comma-joined
    await md.option_greeks(instrument_keys=["NSE_FO|43885", "NSE_FO|49210"])
    runner.assert_eq("BG4-list-params", rest.calls[1]["params"],
                     {"instrument_key": "NSE_FO|43885,NSE_FO|49210"})


async def test_bg5_route_registration(runner: R) -> None:
    routes = build_market_data_routes(_StubProvider())
    by_path = {r.path: set(r.methods) for r in routes}
    runner.assert_in("BG5-margin", "/api/margin", by_path)
    runner.assert_true("BG5-margin-post", "POST" in by_path["/api/margin"])
    runner.assert_in("BG5-shareholdings", "/api/shareholdings", by_path)
    runner.assert_true("BG5-sh-get", "GET" in by_path["/api/shareholdings"])
    runner.assert_in("BG5-greeks", "/api/options/greeks", by_path)
    runner.assert_true("BG5-greeks-get", "GET" in by_path["/api/options/greeks"])
    # pre-existing routes still wired
    runner.assert_in("BG5-history", "/api/market/history", by_path)
    runner.assert_true("BG5-history-get", "GET" in by_path["/api/market/history"])
    runner.assert_in("BG5-chain", "/api/options/chain", by_path)
    runner.assert_true("BG5-chain-get", "GET" in by_path["/api/options/chain"])


async def test_bg6_margin_handler(runner: R) -> None:
    payload = {
        "status": "success",
        "data": {"required_margin": 100.0, "final_margin": 95.0, "margins": []},
    }
    md, _ = _make_md(payload)
    routes = build_market_data_routes(md)
    route = next(r for r in routes if r.path == "/api/margin")
    body = json.dumps({
        "instruments": [{
            "exchange": "NSE", "symbol": "RELIANCE",
            "transaction_type": "BUY", "quantity": 1, "product": "I",
            "lot_size": 1, "price": 2500, "trigger_price": 0,
        }],
    })
    request = _make_request("POST", "/api/margin", body=body)
    data = _response_json(await route.endpoint(request))
    runner.assert_eq("BG6-status", data.get("status"), "ok")
    runner.assert_eq("BG6-required", data["data"]["required_margin"], 100.0)
    runner.assert_eq("BG6-final", data["data"]["final_margin"], 95.0)


async def test_bg7_shareholdings_handler(runner: R) -> None:
    payload = {
        "status": "success",
        "data": [
            {"category": "Promoters",
             "history": [{"period": "Jun 2024", "value": 52.0}]},
        ],
    }
    md, _ = _make_md(payload)
    routes = build_market_data_routes(md)
    route = next(r for r in routes if r.path == "/api/shareholdings")
    request = _make_request("GET", "/api/shareholdings",
                            query_string=b"isin=INE123456789")
    data = _response_json(await route.endpoint(request))
    runner.assert_eq("BG7-status", data.get("status"), "ok")
    runner.assert_eq("BG7-isin", data["data"]["isin"], "INE123456789")
    runner.assert_eq("BG7-cat", data["data"]["categories"][0]["category"],
                     "Promoters")


async def test_bg8_greeks_handler(runner: R) -> None:
    payload = {
        "status": "success",
        "data": {"NSE_FO|43885": {"delta": 0.5, "iv": 20.0}},
    }
    md, _ = _make_md(payload)
    routes = build_market_data_routes(md)
    route = next(r for r in routes if r.path == "/api/options/greeks")
    request = _make_request("GET", "/api/options/greeks",
                            query_string=b"instrument_key=NSE_FO|43885")
    data = _response_json(await route.endpoint(request))
    runner.assert_eq("BG8-status", data.get("status"), "ok")
    runner.assert_eq("BG8-n", len(data["data"]["entries"]), 1)
    runner.assert_eq("BG8-delta", data["data"]["entries"][0]["delta"], 0.5)


async def main() -> bool:
    runner = R()
    await test_bg1_margin_service(runner)
    await test_bg2_margin_validation(runner)
    await test_bg3_shareholdings_service(runner)
    await test_bg4_option_greeks_service(runner)
    await test_bg5_route_registration(runner)
    await test_bg6_margin_handler(runner)
    await test_bg7_shareholdings_handler(runner)
    await test_bg8_greeks_handler(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
