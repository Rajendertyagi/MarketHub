"""Provider-backed market-data services: history + option chain.

Consumes ONLY the canonical UpstoxRest boundary (never raw provider
modules). Credentials arrive per-call from the live feed via the injected
``auth_context_fn`` — nothing is stored here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("event_server")

_HISTORY_URL = "https://api.upstox.com/v2/historical-candle"
_INTRADAY_URL = "https://api.upstox.com/v2/historical-candle/intraday"
_CHAIN_URL = "https://api.upstox.com/v2/option/chain"

_VALID_UNITS = {"minutes", "hours", "days", "weeks", "months"}
_MAX_RANGE_DAYS = 400


class ProviderMarketDataError(RuntimeError):
    """Safe provider market-data failure (no provider bodies leaked)."""


class ProviderMarketData:
    """History + option-chain access over canonical provider adapters.

    Provider resolution is deterministic: explicit ``provider`` argument
    wins; default is "upstox". Unknown providers are rejected loudly —
    never a silent random fallback.
    """

    _PROVIDERS = ("upstox", "fyers")

    def __init__(self, upstox_auth_context_fn: Callable[[], Any],
                 fyers_adapter: Any = None) -> None:
        self._upstox_auth_context_fn = upstox_auth_context_fn
        self._fyers = fyers_adapter   # optional; None until configured

    def _resolve(self, provider: str):
        if provider == "upstox":
            return "upstox", self._upstox_auth_context_fn
        if provider == "fyers":
            if self._fyers is None:
                raise ProviderMarketDataError(
                    "fyers market data not configured")
            return "fyers", self._fyers
        raise ProviderMarketDataError(f"unknown provider: {provider}")

    def _auth(self):
        ctx = self._upstox_auth_context_fn()
        if ctx is None:
            raise ProviderMarketDataError(
                "upstox feed is not authenticated; log in first")
        return ctx  # (rest, credentials)

    # -- history ---------------------------------------------------------------

    async def history(
        self, *, instrument_key: str, unit: str, interval: int,
        from_date: str, to_date: str, provider: str = "upstox",
    ) -> list[Any]:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            from market.normalize.fyers import fyers_resolution
            resolution = fyers_resolution(unit, interval)
            if resolution is None:
                raise ProviderMarketDataError(
                    f"fyers does not support {unit}/{interval}")
            return await self._fyers.history(
                instrument_key=instrument_key, resolution=resolution,
                from_date=from_date, to_date=to_date)
        if unit not in _VALID_UNITS:
            raise ProviderMarketDataError(f"unsupported unit: {unit}")
        interval = int(interval)
        if interval < 1 or interval > 300:
            raise ProviderMarketDataError("interval out of range")
        for d in (from_date, to_date):
            if len(d) != 10 or d.count("-") != 2:
                raise ProviderMarketDataError("dates must be YYYY-MM-DD")

        rest, creds = self._auth()
        from market.normalize.upstox import candles_from_rest

        if unit == "minutes" and interval <= 75:
            url = f"{_INTRADAY_URL}/{instrument_key}/{interval}minute"
            payload = await rest.authenticated_request(
                method="GET", url=url,
                access_token=creds.access_token)
            return candles_from_rest(payload)

        url = (f"{_HISTORY_URL}/{instrument_key}/{unit}/{interval}/"
               f"{to_date}/{from_date}")
        payload = await rest.authenticated_request(
            method="GET", url=url, access_token=creds.access_token)
        return candles_from_rest(payload)

    # -- option chain ------------------------------------------------------------

    async def option_chain(
        self, *, instrument_key: str, exchange: str,
        tradingsymbol: str = "", expiry: str, provider: str = "upstox",
    ) -> Any:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            return await self._fyers.option_chain(
                instrument_key=instrument_key, exchange=exchange,
                tradingsymbol=tradingsymbol, expiry=expiry)
        if not expiry or len(expiry) != 10:
            raise ProviderMarketDataError("expiry must be YYYY-MM-DD")
        rest, creds = self._auth()
        from market.normalize.upstox import option_chain_from_rest

        payload = await rest.authenticated_request(
            method="PUT", url=_CHAIN_URL,
            access_token=creds.access_token,
            json_body={"instrument_key": instrument_key,
                       "expiry_date": expiry})
        return option_chain_from_rest(
            payload, instrument_token=instrument_key, exchange=exchange,
            tradingsymbol=tradingsymbol, expiry=expiry)

