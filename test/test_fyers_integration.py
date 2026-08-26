import asyncio
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import os as _os
import sys as _sys
_sd = _os.path.dirname(_os.path.abspath(__file__))
_pd = _os.path.dirname(_sd)
for _p in (_pd, _sd):
    if _p not in _sys.path: _sys.path.insert(0, _p)
from helpers.runner import R


def test_fyers_auth_and_adapters(runner: R) -> None:
    from brokers.fyers.auth import FyersAuth, app_id_hash
    from market.normalize.fyers import (
        candles_from_history, option_chain_from_rest, fyers_resolution,
    )
    from app.market_data import ProviderMarketData, ProviderMarketDataError

    # Auth: URL + hash + exchange via fake transport.
    auth = FyersAuth(app_id="APP-1", secret_id="SEC",
                     redirect_uri="http://localhost:7070/auth/fyers/callback")
    url = auth.login_url(state="S1")
    runner.assert_in("FA-login-url", "/api/v3/generate-authcode", url)
    runner.assert_in("FA-login-host", "https://api-t1.fyers.in", url)
    runner.assert_in("FA-state-echo", "state=S1", url)
    import hashlib
    runner.assert_eq("FA-hash-algo",
                     app_id_hash("A", "B"),
                     hashlib.sha256(b"A:B").hexdigest())

    seen = {}

    async def fake_transport(url, body):
        seen["url"] = url
        seen["body"] = body
        assert body["appIdHash"] == app_id_hash("APP-1", "SEC")
        assert body["code"] == "ONE-TIME"
        return 200, {"access_token": "FY-TOK",
                     "refresh_token": "FY-REF", "expires_at": 99}

    auth2 = FyersAuth(app_id="APP-1", secret_id="SEC",
                      redirect_uri="http://localhost:7070/auth/fyers/callback",
                      transport=fake_transport)
    bundle = asyncio.run(auth2.validate_auth_code("ONE-TIME"))
    runner.assert_eq("FA-exchange-token", bundle["access_token"], "FY-TOK")
    runner.assert_eq("FA-refresh-present",
                     bundle["refresh_token"], "FY-REF")
    runner.assert_in("FA-validate-url",
                     "/api/v3/validate-authcode", seen["url"])

    # Normalizers.
    cs = candles_from_history({"s": "ok", "candles": [
        [1756000000, 100, 101, 99, 100.5, 12345]]})
    runner.assert_eq("FH-candle-close", cs[0].close, 100.5)
    runner.assert_true("FH-tz-aware", cs[0].timestamp.tzinfo is not None)

    oc = option_chain_from_rest(
        {"data": {"options": [
            {"strike_price": 24500, "option_type": "", "ltp": 24510.5},
            {"strike_price": 24500, "option_type": "CE", "ltp": 120.5,
             "oi": 1500000, "prev_oi": 1400000, "oich": 100000,
             "volume": 500, "greeks": {"delta": 0.52, "iv": 14.2}},
            {"strike_price": 24500, "option_type": "PE", "ltp": 95.0,
             "greeks": {"delta": -0.48}}]}},
        instrument_token="K", exchange="NSE", tradingsymbol="NIFTY",
        expiry="2026-09-24")
    runner.assert_eq("FO-atm", oc.atm_strike, 24500.0)
    runner.assert_eq("FO-call-delta", oc.strikes[0].call.delta, 0.52)

    # Resolution mapping.
    runner.assert_eq("FRES-day", fyers_resolution("days", 1), "1D")
    runner.assert_eq("FRES-15m", fyers_resolution("minutes", 15), "15")
    runner.assert_eq("FRES-unsupported", fyers_resolution("weeks", 1), None)

    # Provider resolution: unknown rejected; fyers-not-configured safe.
    md = ProviderMarketData(lambda: None)
    try:
        asyncio.run(md.history(instrument_key="K", unit="days", interval=1,
                               from_date="2026-01-01", to_date="2026-01-02",
                               provider="bogus"))
        rejected = False
    except ProviderMarketDataError as exc:
        rejected = "unknown provider" in str(exc)
    runner.assert_true("PR-unknown-rejected", rejected)

    try:
        asyncio.run(md.option_chain(instrument_key="K", exchange="NSE",
                                    expiry="2026-09-24", provider="fyers"))
        raised = False
    except ProviderMarketDataError as exc:
        raised = "not configured" in str(exc)
    runner.assert_true("PR-fyers-unconfigured-safe", raised)


if __name__ == "__main__":
    runner = R()
    test_fyers_auth_and_adapters(runner)
    success = runner.summary()
    sys.exit(0 if success else 1)

