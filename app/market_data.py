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
_OI_URL = "https://api.upstox.com/v2/market/oi"
_OI_CHANGE_URL = "https://api.upstox.com/v2/market/change-oi"
_MAX_PAIN_URL = "https://api.upstox.com/v2/market/max-pain"
_PCR_URL = "https://api.upstox.com/v2/market/pcr"
_NEWS_URL = "https://api.upstox.com/v2/news"
_MARGIN_URL = "https://api.upstox.com/v2/charges/margin"
_SHAREHOLDINGS_URL = "https://api.upstox.com/v2/fundamentals"
_OPTION_GREEKS_URL = "https://api.upstox.com/v3/market-quote/option-greek"
_HOLIDAYS_URL = "https://api.upstox.com/v2/market/holidays"
_TIMINGS_URL = "https://api.upstox.com/v2/market/timings"
_FUTURES_SMARTLIST_URL = "https://api.upstox.com/v2/market/smartlist/futures"
_FII_URL = "https://api.upstox.com/v2/market/fii"
_DII_URL = "https://api.upstox.com/v2/market/dii"
_COMPANY_PROFILE_URL = "https://api.upstox.com/v2/fundamentals"
_KEY_RATIOS_URL = "https://api.upstox.com/v2/fundamentals"
_CORPORATE_ACTIONS_URL = "https://api.upstox.com/v2/fundamentals"
_COMPETITORS_URL = "https://api.upstox.com/v2/fundamentals"

# Canonical (unit, interval) -> Upstox historical-candle interval token,
# verified live against the provider (read-only probes, Sept 2026):
#   * daily/weekly/monthly endpoint takes day|week|month (interval is
#     always 1; multi-day/week/month aggregates are NOT supported upstream)
#   * intraday endpoint takes 1minute|30minute ONLY (anything else is
#     rejected with UDAPI1076); there is no hourly candle endpoint, so
#     hours/* is unsupported.
_UPSTOX_INTERVALS = {
    ("minutes", 1): ("intraday", "1minute"),
    ("minutes", 30): ("intraday", "30minute"),
    ("days", 1): ("history", "day"),
    ("weeks", 1): ("history", "week"),
    ("months", 1): ("history", "month"),
}


def upstox_interval_token(unit: str, interval: int) -> tuple[str, str] | None:
    """Map canonical (unit, interval) to (endpoint, token), or None."""
    try:
        key = (str(unit), int(interval))
    except (TypeError, ValueError):
        return None
    return _UPSTOX_INTERVALS.get(key)


class ProviderMarketDataError(RuntimeError):
    """Safe provider market-data failure (no provider bodies leaked)."""


def _infer_provider(instrument_key: str) -> str:
    """Infer the broker provider from an instrument key's format.

    Upstox keys embed a segment prefix with a pipe (``NSE_EQ|RELIANCE``,
    ``NSE_INDEX|Nifty 50``); Fyers keys are ``EXCH:SYMBOL`` or a numeric
    instrument token. Anything else defaults to upstox for backward
    compatibility.
    """
    if not instrument_key:
        return "upstox"
    if "|" in instrument_key:
        return "upstox"
    if ":" in instrument_key:
        return "fyers"
    if instrument_key.isdigit():
        return "fyers"
    return "upstox"


class ProviderMarketData:
    """History + option-chain access over canonical provider adapters.

    Provider resolution is deterministic: an explicit ``provider`` argument
    wins; when omitted it is inferred from the instrument key's format (see
    :func:`_infer_provider`). Unknown providers are rejected loudly — never a
    silent random fallback.
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
        from_date: str, to_date: str, provider: str | None = None,
    ) -> list[Any]:
        if provider is None:
            provider = _infer_provider(instrument_key)
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            from market.normalize.fyers import fyers_resolution
            try:
                resolution = fyers_resolution(unit, int(interval))
            except (TypeError, ValueError):
                resolution = None
            if resolution is None:
                raise ProviderMarketDataError(
                    f"fyers does not support {unit}/{interval}")
            return await self._fyers.history(
                instrument_key=instrument_key, resolution=resolution,
                from_date=from_date, to_date=to_date)
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            raise ProviderMarketDataError(
                "interval must be an integer") from None
        mapped = upstox_interval_token(unit, interval)
        if mapped is None:
            raise ProviderMarketDataError(
                f"upstox does not support {unit}/{interval}")
        for d in (from_date, to_date):
            if not isinstance(d, str) or len(d) != 10 or d.count("-") != 2:
                raise ProviderMarketDataError("dates must be YYYY-MM-DD")

        rest, creds = self._auth()
        from urllib.parse import quote
        from market.normalize.upstox import candles_from_rest

        # Path segments are encoded: raw `|`/spaces are rejected by the
        # HTTP client and misparsed upstream (InvalidURL / invalid key).
        key = quote(str(instrument_key), safe="")
        endpoint, token = mapped
        if endpoint == "intraday":
            url = f"{_INTRADAY_URL}/{key}/{token}"
        else:
            url = f"{_HISTORY_URL}/{key}/{token}/{to_date}/{from_date}"
        try:
            payload = await rest.authenticated_request(
                method="GET", url=url, access_token=creds.access_token)
        except Exception as exc:
            # Classify WITHOUT importing broker internals (duck-typed):
            # invalid key / unknown resource -> client error (400);
            # retryable upstream trouble propagates as-is (502 upstream).
            status = getattr(exc, "status_code", None)
            codes = getattr(exc, "upstox_codes", None) or []
            if status in (400, 404) or "UDAPI100011" in codes \
                    or "UDAPI100060" in codes:
                raise ProviderMarketDataError(
                    "upstox history unavailable for this instrument "
                    "(invalid key or no data for the range)") from None
            raise
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
        from urllib.parse import urlencode

        # Upstox serves the option chain as a REST snapshot (GET + query
        # params). It returns last-session OI/LTP/IV/greeks even when the
        # market is closed, so the chain is viewable on weekends too.
        qs = urlencode({"instrument_key": instrument_key,
                        "expiry_date": expiry})
        url = f"{_CHAIN_URL}?{qs}"
        payload = await rest.authenticated_request(
            method="GET", url=url, access_token=creds.access_token)
        return option_chain_from_rest(
            payload, instrument_token=instrument_key, exchange=exchange,
            tradingsymbol=tradingsymbol, expiry=expiry)
    # -- OI analytics --------------------------------------------------------

    async def oi(
        self, *, instrument_key: str, expiry: str, provider: str = "upstox",
    ) -> Any:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            raise ProviderMarketDataError("fyers OI API not yet implemented")
        rest, creds = self._auth()
        from market.normalize.upstox_analytics import oi_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=_OI_URL,
            access_token=creds.access_token,
            params={"instrument_key": instrument_key, "expiry": expiry})
        return oi_from_rest(payload)

    async def oi_change(
        self, *, instrument_key: str, expiry: str, interval: int = 1,
        provider: str = "upstox",
    ) -> Any:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            raise ProviderMarketDataError("fyers OI change API not yet implemented")
        rest, creds = self._auth()
        from market.normalize.upstox_analytics import oi_change_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=_OI_CHANGE_URL,
            access_token=creds.access_token,
            params={"instrument_key": instrument_key, "expiry": expiry, "interval": interval})
        return oi_change_from_rest(payload)

    async def max_pain(
        self, *, instrument_key: str, expiry: str, provider: str = "upstox",
    ) -> Any:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            raise ProviderMarketDataError("fyers max pain API not yet implemented")
        rest, creds = self._auth()
        from market.normalize.upstox_analytics import max_pain_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=_MAX_PAIN_URL,
            access_token=creds.access_token,
            params={"instrument_key": instrument_key, "expiry": expiry})
        return max_pain_from_rest(payload)

    async def pcr(
        self, *, instrument_key: str, expiry: str | None = None,
        provider: str = "upstox",
    ) -> Any:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            raise ProviderMarketDataError("fyers PCR API not yet implemented")
        rest, creds = self._auth()
        from market.normalize.upstox_analytics import pcr_from_rest
        params: dict[str, Any] = {"instrument_key": instrument_key}
        if expiry:
            params["expiry"] = expiry
        payload = await rest.authenticated_request(
            method="GET", url=_PCR_URL,
            access_token=creds.access_token, params=params)
        return pcr_from_rest(payload)

    # -- News ---------------------------------------------------------------

    async def news(
        self, *, instrument_keys: list[str] | None = None,
        category: str = "instrument_keys", provider: str = "upstox",
    ) -> Any:
        provider, _auth_src = self._resolve(provider)
        if provider == "fyers":
            raise ProviderMarketDataError("fyers news API not available")
        rest, creds = self._auth()
        from market.normalize.upstox_news import news_from_rest
        params: dict[str, Any] = {"category": category}
        if category == "instrument_keys" and instrument_keys:
            params["instrument_keys"] = ",".join(instrument_keys[:30])  # max 30
        payload = await rest.authenticated_request(
            method="GET", url=_NEWS_URL,
            access_token=creds.access_token, params=params)
        key = instrument_keys[0] if instrument_keys else ""
        return news_from_rest(payload, key)
    # -- Market Information --------------------------------------------------

    async def holidays(self, date: str | None = None, provider: str = "upstox") -> Any:
        """GET /market/holidays - Trading holidays."""
        if provider != "upstox":
            raise ProviderMarketDataError("holidays not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_market_info import holidays_from_rest, holiday_status_from_rest
        url = f"{_HOLIDAYS_URL}/{date}" if date else _HOLIDAYS_URL
        payload = await rest.authenticated_request(
            method="GET", url=url, access_token=creds.access_token)
        if date:
            return holiday_status_from_rest(payload)
        return holidays_from_rest(payload)

    async def timings(self, date: str, provider: str = "upstox") -> Any:
        """GET /market/timings/:date - Session times."""
        if provider != "upstox":
            raise ProviderMarketDataError("timings not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_market_info import timings_from_rest
        url = f"{_TIMINGS_URL}/{date}"
        payload = await rest.authenticated_request(
            method="GET", url=url, access_token=creds.access_token)
        return timings_from_rest(payload)

    # -- Futures Smartlist ---------------------------------------------------

    async def futures_smartlist(
        self, *, asset_type: str, category: str,
        page_number: int = 1, page_size: int = 20,
        provider: str = "upstox",
    ) -> Any:
        """GET /market/smartlist/futures - Ranked futures contracts."""
        if provider != "upstox":
            raise ProviderMarketDataError("futures smartlist not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_analytics_extended import futures_smartlist_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=_FUTURES_SMARTLIST_URL,
            access_token=creds.access_token,
            params={
                "asset_type": asset_type,
                "category": category,
                "page_number": page_number,
                "page_size": min(page_size, 50),
            })
        return futures_smartlist_from_rest(payload)

    # -- FII Activity --------------------------------------------------------

    async def fii(
        self, *, data_types: list[str] | None = None, interval: str = "1D",
        from_date: str | None = None, provider: str = "upstox",
    ) -> Any:
        """GET /market/fii - FII activity data.

        The Upstox endpoint returns only the single ``data_type`` requested,
        so each requested type is fetched with its own call and normalized
        independently (keyed by data_type in the result).
        """
        if provider != "upstox":
            raise ProviderMarketDataError("fii data not available from this provider")
        if not data_types:
            data_types = []
        rest, creds = self._auth()
        from market.normalize.upstox_analytics_extended import fii_single_from_rest
        result: dict[str, Any] = {}
        for dt in data_types[:5]:  # max 5 at once
            params: dict[str, Any] = {"interval": interval, "data_type": dt}
            if from_date:
                params["from"] = from_date
            payload = await rest.authenticated_request(
                method="GET", url=_FII_URL,
                access_token=creds.access_token, params=params)
            result[dt] = fii_single_from_rest(payload, dt)
        return result

    # -- DII Activity --------------------------------------------------------

    async def dii(
        self, *, data_types: list[str] | None = None,
        interval: str = "1D", from_date: str | None = None,
        provider: str = "upstox",
    ) -> Any:
        """GET /market/dii - DII activity data."""
        if provider != "upstox":
            raise ProviderMarketDataError("dii data not available from this provider")
        if not data_types:
            data_types = ["NSE_EQ|CASH"]
        rest, creds = self._auth()
        from market.normalize.upstox_analytics_extended import dii_single_from_rest
        result: dict[str, Any] = {}
        for dt in data_types:
            params: dict[str, Any] = {"interval": interval, "data_type": dt}
            if from_date:
                params["from"] = from_date
            payload = await rest.authenticated_request(
                method="GET", url=_DII_URL,
                access_token=creds.access_token, params=params)
            result[dt] = dii_single_from_rest(payload, dt)
        return result

    # -- Fundamentals --------------------------------------------------------

    async def company_profile(self, isin: str, provider: str = "upstox") -> Any:
        """GET /fundamentals/:isin/profile - Company profile."""
        if provider != "upstox":
            raise ProviderMarketDataError("company profile not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_fundamentals import company_profile_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=f"{_COMPANY_PROFILE_URL}/{isin}/profile",
            access_token=creds.access_token)
        return company_profile_from_rest(payload)

    async def key_ratios(self, isin: str, provider: str = "upstox") -> Any:
        """GET /fundamentals/:isin/ratios - Key financial ratios."""
        if provider != "upstox":
            raise ProviderMarketDataError("key ratios not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_fundamentals import key_ratios_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=f"{_KEY_RATIOS_URL}/{isin}/ratios",
            access_token=creds.access_token)
        return key_ratios_from_rest(payload)

    async def corporate_actions(self, isin: str, provider: str = "upstox") -> Any:
        """GET /fundamentals/:isin/corporate-actions - Corporate actions."""
        if provider != "upstox":
            raise ProviderMarketDataError("corporate actions not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_fundamentals import corporate_actions_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=f"{_CORPORATE_ACTIONS_URL}/{isin}/corporate-actions",
            access_token=creds.access_token)
        return corporate_actions_from_rest(payload)

    async def competitors(self, isin: str, provider: str = "upstox") -> Any:
        """GET /fundamentals/:isin/competitors - Competitor instruments."""
        if provider != "upstox":
            raise ProviderMarketDataError("competitors not available from this provider")
        rest, creds = self._auth()
        from market.normalize.upstox_fundamentals import competitors_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=f"{_COMPETITORS_URL}/{isin}/competitors",
            access_token=creds.access_token)
        return competitors_from_rest(payload)

    # -- Broker data: margin / shareholdings / greeks -----------------------

    async def margin(
        self, *, instruments: list[dict[str, Any]],
        item_type: str = "SECURITY", margin_category: str = "intraday",
        provider: str = "upstox",
    ) -> Any:
        """POST /v2/charges/margin - Required margin for a basket of instruments.

        ``instruments`` is a list of Upstox margin specs, e.g.::

            {"exchange": "NSE", "symbol": "RELIANCE",
             "transaction_type": "BUY", "quantity": 1, "product": "I",
             "lot_size": 1, "price": 2500, "trigger_price": 0}
        """
        if provider != "upstox":
            raise ProviderMarketDataError("margin not available from this provider")
        if not instruments or not isinstance(instruments, list):
            raise ProviderMarketDataError("instruments (non-empty list) is required")
        rest, creds = self._auth()
        from market.normalize.upstox_margins import margin_from_rest
        payload = await rest.authenticated_request(
            method="POST", url=_MARGIN_URL,
            access_token=creds.access_token,
            json_body={
                "instruments": instruments,
                "item_type": item_type,
                "margin_category": margin_category,
            })
        return margin_from_rest(payload)

    async def shareholdings(
        self, *, isin: str, provider: str = "upstox",
    ) -> Any:
        """GET /fundamentals/:isin/share-holdings - Quarterly shareholding pattern."""
        if provider != "upstox":
            raise ProviderMarketDataError("shareholdings not available from this provider")
        if not isin:
            raise ProviderMarketDataError("isin is required")
        rest, creds = self._auth()
        from market.normalize.upstox_shareholdings import shareholdings_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=f"{_SHAREHOLDINGS_URL}/{isin}/share-holdings",
            access_token=creds.access_token)
        return shareholdings_from_rest(payload, isin=isin)

    async def option_greeks(
        self, *, instrument_keys: list[str] | str, provider: str | None = None,
    ) -> Any:
        """GET /v3/market-quote/option-greek - Standalone option Greeks.

        Accepts a single instrument key or a list; comma-joined for the API.
        Note: the Upstox endpoint does NOT return ``rho`` — it is always None.
        """
        if provider is None:
            if isinstance(instrument_keys, str):
                first = instrument_keys.strip()
            else:
                first = next(
                    (str(k).strip() for k in instrument_keys if str(k).strip()),
                    "")
            provider = _infer_provider(first)
        if provider != "upstox":
            raise ProviderMarketDataError("option greeks not available from this provider")
        if isinstance(instrument_keys, str):
            keys = [k.strip() for k in instrument_keys.split(",") if k.strip()] \
                if instrument_keys else []
        else:
            keys = [str(k).strip() for k in instrument_keys if str(k).strip()]
        if not keys:
            raise ProviderMarketDataError("at least one instrument_key is required")
        rest, creds = self._auth()
        from market.normalize.upstox_option_greeks import option_greeks_from_rest
        payload = await rest.authenticated_request(
            method="GET", url=_OPTION_GREEKS_URL,
            access_token=creds.access_token,
            params={"instrument_key": ",".join(keys)})
        return option_greeks_from_rest(payload)
