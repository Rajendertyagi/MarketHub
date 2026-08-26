#!/usr/bin/env python3
"""Option chain + market history tests (OC1-OC10).

  * OC1   Upstox candle normalization (full row incl. OI)
  * OC2   malformed candle rows skipped
  * OC3   option-chain snapshot: strikes sorted, ATM flagged
  * OC4   contract data: greeks + derived oi_change
  * OC5   missing put side -> put=None preserved
  * OC6   history service validates unit/interval/dates (no network)
  * OC7   history service unauthenticated -> safe error, no network
  * OC8   history happy path with fake transport
  * OC9   option chain unauthenticated -> safe error
  * OC10  option chain happy path with fake transport

NO LIVE BROKER. NO NETWORK. Synthetic payloads only.
"""

from __future__ import annotations

import asyncio
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402


class _FakeRest:
    """Captures requests; returns canned payloads."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def authenticated_request(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


class _AuthCtx:
    def __init__(self, rest):
        self.rest = rest
        self.creds = type("C", (), {"access_token": "SYNTHETIC-TOKEN"})()

    def __iter__(self):
        return iter((self.rest, self.creds))


def _chain_payload():
    return {"status": "ok", "data": [
        {"strike_price": 24600, "spot_price": 24510.5,
         "call_options": {"market_data": {"ltp": 80.0}},
         "put_options": {"market_data": {
             "ltp": 95.0, "volume": 400,
             "bid_price": 94.5, "ask_price": 95.5,
             "oi": 1500000.0, "prev_oi": 1400000.0,
             "close_price": 96.0},
             "option_greeks": {"delta": -0.48, "iv": 15.1}}},
        {"strike_price": 24500, "atm": True, "spot_price": 24510.5,
         "call_options": {"market_data": {
             "ltp": 120.5, "volume": 500, "bid_price": 120.0,
             "ask_price": 121.0, "oi": 1500000.0, "prev_oi": 1400000.0,
             "close_price": 118.0},
             "option_greeks": {"delta": 0.52, "theta": -6.25,
                               "gamma": 0.001, "vega": 11.4, "iv": 14.2}},
         "put_options": {"market_data": {"ltp": 95.0}}},
    ]}


# -- tests ---------------------------------------------------------------------


def test_oc1_oc2_candles(runner: R) -> None:
    from market.normalize.upstox import candles_from_rest

    payload = {"status": "ok", "data": {"candles": [
        ["2026-08-20T09:15:00+05:30", 100, 101, 99, 100.5, 12345, 555.0],
        ["bad-row"],
        ["2026-08-21T09:15:00+05:30", 100.5, 102, 100, 101.0, 22222],
    ]}}
    candles = candles_from_rest(payload)
    runner.assert_eq("OC1-count", len(candles), 2)
    first = candles[0]
    runner.assert_eq("OC1-close", first.close, 100.5)
    runner.assert_eq("OC1-oi", first.open_interest, 555.0)
    runner.assert_true("OC1-tz-aware",
                       first.timestamp.tzinfo is not None)
    runner.assert_eq("OC2-malformed-skipped",
                     [c.volume for c in candles], [12345, 22222])


def test_oc3_to_oc5_chain_normalizer(runner: R) -> None:
    from market.normalize.upstox import option_chain_from_rest

    snap = option_chain_from_rest(
        _chain_payload(), instrument_token="NSE_INDEX|NIFTY 50",
        exchange="NSE", tradingsymbol="NIFTY 50", expiry="2026-09-24")
    runner.assert_eq("OC3-sorted-strikes",
                     [s.strike for s in snap.strikes], [24500.0, 24600.0])
    atm = snap.strikes[0]
    runner.assert_true("OC3-atm-flagged", atm.atm)
    runner.assert_eq("OC3-spot", snap.spot_price, 24510.5)

    call = atm.call
    runner.assert_eq("OC4-delta", call.delta, 0.52)
    runner.assert_eq("OC4-iv", call.iv, 14.2)
    runner.assert_eq("OC4-oi-change-derived", call.oi_change, 100000.0)

    # OC5: strike with a full put side keeps all fields; missing put -> None.
    upper = snap.strikes[1]
    runner.assert_eq("OC5-put-present-ltp", upper.put.ltp, 95.0)
    runner.assert_eq("OC5-put-delta", upper.put.delta, -0.48)
    snap2 = option_chain_from_rest(
        {"data": [{"strike_price": 24700,
                   "call_options": {"market_data": {"ltp": 50.0}}}]},
        instrument_token="K", exchange="NSE", tradingsymbol="",
        expiry="2026-09-24")
    runner.assert_eq("OC5-missing-put-none", snap2.strikes[0].put, None)


async def test_oc6_history_validation(runner: R) -> None:
    from app.market_data import ProviderMarketData, ProviderMarketDataError

    md = ProviderMarketData(lambda: None)
    for kwargs in [
        dict(instrument_key="K", unit="nanoseconds", interval=1,
             from_date="2026-01-01", to_date="2026-01-02"),
        dict(instrument_key="K", unit="days", interval=9999,
             from_date="2026-01-01", to_date="2026-01-02"),
        dict(instrument_key="K", unit="days", interval=1,
             from_date="junk", to_date="2026-01-02"),
    ]:
        try:
            await md.history(**kwargs)
            ok = False
        except ProviderMarketDataError:
            ok = True
        runner.assert_true(f"OC6-validate:{kwargs['unit']}", ok)


async def test_oc7_unauthenticated_safe(runner: R) -> None:
    from app.market_data import ProviderMarketData, ProviderMarketDataError

    md = ProviderMarketData(lambda: None)   # feed absent/unauthenticated
    try:
        await md.history(instrument_key="K", unit="days", interval=1,
                         from_date="2026-01-01",
                         to_date="2026-01-02")
        raised = False
    except ProviderMarketDataError as exc:
        raised = True
        runner.assert_not_in("OC7-no-secret-in-error", "SYNTHETIC-TOKEN",
                             str(exc))
    runner.assert_true("OC7-safe-error", raised)



async def test_oc8_history_happy_path(runner: R) -> None:
    from app.market_data import ProviderMarketData

    payload = {"status": "ok", "data": {"candles": [
        ["2026-01-02T00:00:00+05:30", 100, 105, 99, 104, 800000]]}}
    fake = _FakeRest(payload)
    md = ProviderMarketData(lambda: _AuthCtx(fake))
    candles = await md.history(
        instrument_key="NSE_EQ|X1", unit="days", interval=1,
        from_date="2026-01-01", to_date="2026-01-31")
    runner.assert_eq("OC8-candle-count", len(candles), 1)
    runner.assert_eq("OC8-close", candles[0].close, 104.0)
    call = fake.calls[0]
    runner.assert_in("OC8-url-shape", "/historical-candle/", call["url"])
    runner.assert_eq("OC8-auth-header-used",
                     call["access_token"], "SYNTHETIC-TOKEN")


async def test_oc9_chain_unauthenticated(runner: R) -> None:
    from app.market_data import ProviderMarketData, ProviderMarketDataError

    md = ProviderMarketData(lambda: None)
    try:
        await md.option_chain(instrument_key="K", exchange="NSE",
                              expiry="2026-09-24")
        raised = False
    except ProviderMarketDataError:
        raised = True
    runner.assert_true("OC9-safe-error", raised)


async def test_oc10_chain_happy_path(runner: R) -> None:
    from app.market_data import ProviderMarketData

    fake = _FakeRest(_chain_payload())
    md = ProviderMarketData(lambda: _AuthCtx(fake))
    snap = await md.option_chain(
        instrument_key="NSE_INDEX|NIFTY 50", exchange="NSE",
        tradingsymbol="NIFTY 50", expiry="2026-09-24")
    runner.assert_eq("OC10-strikes", len(snap.strikes), 2)
    runner.assert_eq("OC10-atm", snap.atm_strike, 24500.0)
    call = fake.calls[0]
    runner.assert_eq("OC10-method-PUT", call["method"], "PUT")
    runner.assert_eq("OC10-body-expiry",
                     call["json_body"]["expiry_date"], "2026-09-24")


# -- main -------------------------------------------------------------------------


async def main() -> bool:
    runner = R()

    test_oc1_oc2_candles(runner)
    test_oc3_to_oc5_chain_normalizer(runner)
    await test_oc6_history_validation(runner)
    await test_oc7_unauthenticated_safe(runner)
    await test_oc8_history_happy_path(runner)
    await test_oc9_chain_unauthenticated(runner)
    await test_oc10_chain_happy_path(runner)

    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

