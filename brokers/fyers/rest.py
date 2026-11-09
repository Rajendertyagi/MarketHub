"""Fyers REST boundary — stdlib-only, stateless, injected transport.

Mirrors ``brokers.upstox.rest.UpstoxRest``: one HTTP request -> one structured
success/error, no retries, no client lifetime, no session state, no redirects.

Fyers specifics (official v3 docs):
  * Host: ``https://api-t1.fyers.in``
  * Data endpoints live under ``/data``; auth/transaction endpoints under
    ``/api/v3``.
  * Auth header: ``Authorization: <app_id>:<access_token>`` (NOT Bearer).
  * Response envelope: success ``{"s":"ok","code":200,...}``;
    error ``{"s":"error","code":<neg>,"message":"..."}``.
  * No automatic redirects (SEC-HTTP-2): 3xx surfaces as a contract violation.

Owns exactly these provider-purpose calls:
  * quotes(symbols)        -> list[Quote]
  * depth(symbol)          -> (Depth, supplemental quote fields)
  * history(symbol, ...)   -> list[Candle]
  * option_chain(symbol, strikecount, timestamp) -> OptionChainSnapshot
  * market_status()        -> dict

Does NOT own: WebSocket connect/subscribe (brokers/fyers/feed.py), credentials
storage, env resolution, SourceManager wiring.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from market.models import Candle, Depth, OptionChainSnapshot, Quote
from market.normalize.fyers import (
    candles_from_history,
    depth_from_rest,
    option_chain_from_rest,
    quote_from_quotes_rest,
)

__all__ = ["FyersMarketDataAdapter", "FyersRest"]

_DATA_HOST = "https://api-t1.fyers.in/data"
_QUOTES_URL = f"{_DATA_HOST}/quotes"
#: Documented batch size for the Quotes API (Standard plan).
_QUOTES_MAX_SYMBOLS = 50
_DEPTH_URL = f"{_DATA_HOST}/depth"
_HISTORY_URL = f"{_DATA_HOST}/history"
_OPTION_CHAIN_URL = f"{_DATA_HOST}/options-chain-v3"
_MARKET_STATUS_URL = f"{_DATA_HOST}/marketStatus"

_QUOTES_TIMEOUT_S = 10.0
_DEPTH_TIMEOUT_S = 10.0
_HISTORY_TIMEOUT_S = 15.0
_OPTION_CHAIN_TIMEOUT_S = 15.0
_MARKET_STATUS_TIMEOUT_S = 10.0
_MAX_PROVIDER_MESSAGE = 200

# ---------------------------------------------------------------------------
# Expiry instants (provider-reported)
# ---------------------------------------------------------------------------
# Fyers reports each expiry's INSTANT in the option-chain payload
# (``data.expiryData[].expiry`` as an epoch, ``date`` as DD-MM-YYYY). The
# chain endpoint ADDRESSES an expiry by that instant, so a midnight epoch
# addresses the wrong expiry. The map is populated from the provider's own
# response and consulted when a request names an expiry DATE.
_EXPIRY_EPOCHS: dict[str, str] = {}
_EXPIRY_LOCK = threading.Lock()


def _remember_expiry_epochs(payload: Any) -> None:
    """Record ``DD-MM-YYYY -> epoch`` from a chain payload's expiryData."""
    if not isinstance(payload, dict):
        return
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    entries = data.get("expiryData")
    if not isinstance(entries, list):
        return
    found: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        epoch = entry.get("expiry")
        if isinstance(date, str) and epoch is not None:
            try:
                found[date] = str(int(epoch))
            except (TypeError, ValueError):
                continue
    if found:
        with _EXPIRY_LOCK:
            _EXPIRY_EPOCHS.update(found)


def expiry_epochs() -> dict[str, str]:
    """A copy of the known expiry instants (``DD-MM-YYYY -> epoch``)."""
    with _EXPIRY_LOCK:
        return dict(_EXPIRY_EPOCHS)


def _same_expiry_date(fyers_date: str, iso_date: str) -> bool:
    """True when a Fyers ``DD-MM-YYYY`` date matches an ISO ``YYYY-MM-DD``."""
    try:
        d, m, y = fyers_date.strip().split("-")
        return (y, m, d) == tuple(iso_date.strip().split("-"))
    except (AttributeError, ValueError):
        return False


def _response_expiry(payload: Any) -> str | None:
    """The expiry the provider REPORTED for the returned chain (ISO date).

    The chain response carries ``data.expiry`` (the expiry timestamp) and
    ``data.date`` (DD-MM-YYYY). When a date is present it is converted to
    the canonical ISO form, so a returned chain carries the expiry the
    provider actually served — never the requested one assumed.
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    date_str = data.get("date")
    if isinstance(date_str, str) and date_str.strip():
        try:
            d, m, y = date_str.strip().split("-")
            return f"{y}-{m}-{d}"
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Transport primitives (identical contract to UpstoxRest)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Neutral HTTP response. Body/headers excluded from repr (secret-bearing)."""

    status: int
    headers: Mapping[str, str] = field(repr=False, default_factory=dict)
    body: bytes = field(repr=False, default=b"")

    def __repr__(self) -> str:
        return (
            f"HttpResponse(status={self.status}, "
            f"headers=<redacted>, body=<redacted>)"
        )


SyncTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float], HttpResponse
]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """SEC-HTTP-2: surface 3xx responses instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_sync_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    """stdlib transport. Converts every HTTP status into an HttpResponse."""
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    if not request.has_header("User-agent"):
        request.add_header("User-Agent", "MarketHub/1.0 (trading-terminal)")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            status = exc.code
        finally:
            exc.close()
        return HttpResponse(status=status, headers=response_headers, body=payload)
    return HttpResponse(
        status=response.status,
        headers=dict(response.headers.items()),
        body=response.read(),
    )


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class FyersRestError(RuntimeError):
    """Generic Fyers REST failure (safe wording, no bodies)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        fyers_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.fyers_code = fyers_code
        self.retryable = retryable


class FyersAuthError(FyersRestError):
    """Authentication failure (401 / expired token)."""


class FyersRateLimitError(FyersRestError):
    """Rate limit exceeded (429)."""


# ---------------------------------------------------------------------------
# Safe error parsing
# ---------------------------------------------------------------------------


def _parse_error_body(body: bytes) -> tuple[int | None, str]:
    """Extract (fyers_code, message) from a Fyers error envelope.

    Fyers shape: ``{"s":"error","code":<neg>,"message":"..."}``.
    Malformed bodies yield (None, "").
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None, ""
    if not isinstance(payload, dict):
        return None, ""
    code = payload.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        code = None
    message = payload.get("message")
    if not isinstance(message, str):
        message = ""
    return code, message.strip()[:_MAX_PROVIDER_MESSAGE]


def _raise_for_status(response: HttpResponse, operation: str) -> None:
    """Classify a non-200 response into the typed error hierarchy."""
    fyers_code, provider_message = _parse_error_body(response.body)
    message = f"fyers {operation} failed: HTTP {response.status}"
    if fyers_code is not None:
        message += f" [code={fyers_code}]"
    if provider_message:
        message += f": {provider_message}"

    status = response.status
    if 300 <= status < 400:
        raise FyersRestError(
            f"{message} (unexpected redirect)", status_code=status,
            fyers_code=fyers_code, retryable=False,
        )
    if status == 401:
        raise FyersAuthError(message, status_code=status, fyers_code=fyers_code)
    if status == 429:
        # The provider signalled a rate limit. The BLOCK is reported by
        # the caller (which knows the quota bucket).
        pass
        raise FyersRateLimitError(
            message, status_code=status, fyers_code=fyers_code,
        )
    if status >= 500:
        raise FyersRestError(
            message, status_code=status, fyers_code=fyers_code, retryable=True,
        )
    raise FyersRestError(
        message, status_code=status, fyers_code=fyers_code, retryable=False,
    )


def _require_success_json(response: HttpResponse, operation: str) -> dict[str, Any]:
    """HTTP 200 + strict JSON-dict decoding for a success envelope."""
    if response.status != 200:
        _raise_for_status(response, operation)
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise FyersRestError(
            f"fyers {operation} failed: HTTP {response.status} (non-JSON body)",
            status_code=response.status, retryable=False,
        ) from None
    if not isinstance(payload, dict):
        raise FyersRestError(
            f"fyers {operation} failed: HTTP {response.status} "
            "(unexpected response shape)",
            status_code=response.status, retryable=False,
        )
    # Fyers envelope check.
    if payload.get("s") == "error":
        fyers_code, provider_message = _parse_error_body(response.body)
        message = f"fyers {operation} failed"
        if fyers_code is not None:
            message += f" [code={fyers_code}]"
        if provider_message:
            message += f": {provider_message}"
        raise FyersRestError(
            message, status_code=response.status, fyers_code=fyers_code,
            retryable=False,
        )
    return payload


def _wrap_network_error(operation: str, exc: Exception) -> FyersRestError:
    return FyersRestError(
        f"fyers {operation} failed: network error",
        status_code=None, retryable=True,
    )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class FyersRest:
    """Stateless Fyers REST boundary (see module docstring).

    Stores only the injected transport/clock — never credentials, tokens,
    or session state.
    """

    def __init__(
        self,
        *,
        sync_transport: SyncTransport | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        # Every FYERS REST request (quotes, depth, history, option chain,
        # market status) goes through the SHARED per-provider request
        # budget, so all callers share one accounting state and an
        # exhausted window never reaches the network.
        self._transport: SyncTransport = sync_transport or _default_sync_transport
        self._utc_now: Callable[[], datetime] = utc_now or (
            lambda: datetime.now(timezone.utc)
        )

    async def _call(self, method: str, url: str, headers: Any,
                     body: bytes | None, timeout: float) -> HttpResponse:
        """Perform one request through the SHARED FYERS request budget.

        Every FYERS REST call (quotes, depth, history, option chain,
        market status) goes through here, so all callers share one
        accounting state. An exhausted window is refused BEFORE the
        request - the provider is never pushed into a block by our own
        retries - and the refusal surfaces as the provider's rate-limit
        error so the caller's contract is unchanged.
        """
        from providers.rate import BudgetExhausted, get_budget_manager
        # FYERS documents ONE shared Data-API pool per user.
        try:
            get_budget_manager().acquire("fyers", "data")
        except BudgetExhausted as exc:
            raise FyersRateLimitError(
                "fyers request budget exhausted", status_code=429) from exc
        response = await asyncio.to_thread(
            self._transport, method, url, headers, body, timeout)
        if getattr(response, "status", None) == 429:
            # The provider signalled a rate limit: stop THIS quota bucket
            # for the window instead of retrying into a restriction.
            try:
                from providers.rate import get_budget_manager
                get_budget_manager().report_block("fyers", "data")
            except Exception:
                pass
        return response

    def _auth_header(self, access_token: str) -> dict[str, str]:
        # Fyers format: "Authorization: <app_id>:<access_token>".
        # app_id is embedded in the token by the caller; here we just pass
        # through the full credential string.
        return {
            "Authorization": access_token.strip(),
            "Accept": "application/json",
        }

    # -- quotes ---------------------------------------------------------------

    async def quotes(
        self, access_token: str, symbols: list[str],
    ) -> list[Quote]:
        """Fetch quotes for up to 50 symbols.

        Returns a list of canonical ``Quote`` objects.
        """
        if not isinstance(access_token, str) or not access_token.strip():
            raise FyersAuthError("access_token must be a non-empty string")
        if not symbols:
            return []
        received_ts = self._utc_now()
        # The documented batch size is 50 symbols per request on the
        # Standard plan (1,000 on Prime) - larger requests are split.
        wanted = [s.strip() for s in symbols if s and s.strip()]
        if len(wanted) > _QUOTES_MAX_SYMBOLS:
            out: list[Quote] = []
            for start in range(0, len(wanted), _QUOTES_MAX_SYMBOLS):
                out.extend(await self.quotes(
                    access_token, wanted[start:start + _QUOTES_MAX_SYMBOLS]))
            return out
        symbols_str = ",".join(wanted)
        url = f"{_QUOTES_URL}?symbols={urllib.parse.quote(symbols_str, safe=',')}"
        operation = "quotes"
        try:
            response = await self._call(
                "GET", url,
                self._auth_header(access_token), None, _QUOTES_TIMEOUT_S,
            )
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc

        payload = _require_success_json(response, operation)
        data = payload.get("d") or payload.get("data") or {}
        out: list[Quote] = []
        # Fyers quotes response: {"s":"ok","d":[{"n":"NSE:SBIN-EQ","v":{...}}]}
        items = data if isinstance(data, list) else []
        if isinstance(data, dict):
            items = data.get("quotes") or data.get("v") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            symbol = item.get("n") or item.get("symbol") or item.get("sym")
            v = item.get("v") or item
            if not isinstance(v, dict):
                continue
            if not symbol:
                continue
            try:
                q = quote_from_quotes_rest(v, symbol=symbol, received_ts=received_ts)
                out.append(q)
            except Exception:
                continue
        return out

    # -- depth ----------------------------------------------------------------

    async def depth(
        self, access_token: str, symbol: str,
    ) -> tuple[Depth, dict[str, Any]]:
        """Fetch market depth for one symbol.

        Returns ``(Depth, supplemental_quote_fields)``.
        """
        if not isinstance(access_token, str) or not access_token.strip():
            raise FyersAuthError("access_token must be a non-empty string")
        if not isinstance(symbol, str) or not symbol.strip():
            raise FyersRestError("symbol must be a non-empty string",
                                 retryable=False)
        received_ts = self._utc_now()
        url = f"{_DEPTH_URL}?symbol={urllib.parse.quote(symbol.strip())}&ohlcv_flag=1"
        operation = "depth"
        try:
            response = await self._call(
                "GET", url,
                self._auth_header(access_token), None, _DEPTH_TIMEOUT_S,
            )
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc

        payload = _require_success_json(response, operation)
        data = payload.get("d") or payload.get("data") or payload
        if not isinstance(data, dict):
            raise FyersRestError(
                f"fyers {operation} failed: unexpected data shape",
                status_code=response.status, retryable=False,
            )
        return depth_from_rest(data, symbol=symbol.strip(),
                               received_ts=received_ts)

    # -- history --------------------------------------------------------------

    async def history(
        self,
        access_token: str,
        symbol: str,
        resolution: str,
        from_date: str,
        to_date: str,
    ) -> list[Candle]:
        """Fetch historical candles.

        ``resolution`` is a Fyers resolution string (e.g. "1", "5", "1D").
        ``from_date``/``to_date`` are YYYY-MM-DD strings.
        """
        if not isinstance(access_token, str) or not access_token.strip():
            raise FyersAuthError("access_token must be a non-empty string")
        if not isinstance(symbol, str) or not symbol.strip():
            raise FyersRestError("symbol must be a non-empty string",
                                 retryable=False)
        params = {
            "symbol": symbol.strip(),
            "resolution": resolution,
            "date_format": "1",
            "range_from": from_date,
            "range_to": to_date,
            "cont_flag": "1",
        }
        query = urllib.parse.urlencode(params)
        url = f"{_HISTORY_URL}?{query}"
        operation = "history"
        try:
            response = await self._call(
                "GET", url,
                self._auth_header(access_token), None, _HISTORY_TIMEOUT_S,
            )
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc

        payload = _require_success_json(response, operation)
        return candles_from_history(payload)

    # -- option chain ---------------------------------------------------------

    async def option_chain(
        self,
        access_token: str,
        symbol: str,
        strikecount: int = 50,
        timestamp: str | None = None,
        expiry: str = "",
    ) -> OptionChainSnapshot:
        """Fetch option chain for one underlying.

        ``timestamp`` is the expiry epoch (seconds) or None for nearest.
        ``expiry`` is the expiry date string (YYYY-MM-DD) for the normalizer.
        """
        if not isinstance(access_token, str) or not access_token.strip():
            raise FyersAuthError("access_token must be a non-empty string")
        if not isinstance(symbol, str) or not symbol.strip():
            raise FyersRestError("symbol must be a non-empty string",
                                 retryable=False)
        params: dict[str, Any] = {
            "symbol": symbol.strip(),
            "strikecount": str(int(strikecount)),
            "greeks": "1",
        }
        if timestamp is not None:
            params["timestamp"] = str(timestamp)
        query = urllib.parse.urlencode(params)
        url = f"{_OPTION_CHAIN_URL}?{query}"
        operation = "option_chain"
        try:
            response = await self._call(
                "GET", url,
                self._auth_header(access_token), None, _OPTION_CHAIN_TIMEOUT_S,
            )
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc

        payload = _require_success_json(response, operation)
        # Remember the provider's own expiry instants (expiryData) so the
        # NEXT request can address the expiry precisely. Fyers' expiry epoch
        # is the expiry INSTANT, not midnight of the expiry date.
        _remember_expiry_epochs(payload)
        # The chain is addressed by the UNDERLYING's provider symbol
        # (live-verified: an equity/index underlying symbol returns the
        # chain; a numeric fyToken is rejected with -300). The response's
        # own expiry is used when the provider reports one, so a returned
        # chain is never mislabelled as the requested expiry.
        actual_expiry = _response_expiry(payload) or expiry
        # Fyers option chain returns data.optionsChain (list of legs).
        # The normalizer expects payload with data key.
        return option_chain_from_rest(
            payload,
            instrument_token=symbol.strip(),
            exchange=symbol.split(":")[0] if ":" in symbol else "NSE",
            tradingsymbol=symbol.split(":")[1] if ":" in symbol else symbol,
            expiry=actual_expiry,
        )

    # -- market status --------------------------------------------------------

    async def market_status(self, access_token: str) -> dict[str, Any]:
        """Fetch market status for all segments."""
        if not isinstance(access_token, str) or not access_token.strip():
            raise FyersAuthError("access_token must be a non-empty string")
        operation = "market_status"
        try:
            response = await self._call(
                "GET", _MARKET_STATUS_URL,
                self._auth_header(access_token), None, _MARKET_STATUS_TIMEOUT_S,
            )
        except OSError as exc:
            raise _wrap_network_error(operation, exc) from exc

        payload = _require_success_json(response, operation)
        return payload


# ---------------------------------------------------------------------------
# Adapter: duck-typed interface for ProviderMarketData
# ---------------------------------------------------------------------------


class FyersMarketDataAdapter:
    """Wraps FyersRest + auth context into the interface ProviderMarketData expects.

    ProviderMarketData calls ``.history(instrument_key=..., resolution=...,
    from_date=..., to_date=...)`` and ``.option_chain(instrument_key=...,
    exchange=..., tradingsymbol=..., expiry=...)``. This adapter translates
    those calls into FyersRest calls with the live access token.
    """

    def __init__(self, rest: FyersRest, auth_context_fn: Callable[[], str | None],
                 catalog: Any = None) -> None:
        self._rest = rest
        self._auth_context_fn = auth_context_fn
        # Optional instrument catalogue, used to resolve an EQUITY instrument
        # key to a CONTRACT symbol for the option-chain endpoint (Fyers
        # answers -300 for an equity key on that endpoint).
        self._catalog = catalog

    def _token(self) -> str:
        token = self._auth_context_fn()
        if not token:
            from app.market_data import ProviderMarketDataError
            raise ProviderMarketDataError(
                "fyers market data not authenticated; log in first")
        return token

    def _expiry_timestamp(self, expiry: str) -> str | None:
        """The expiry INSTANT Fyers expects, as an epoch string.

        Fyers' ``expiryData[].expiry`` is the expiry instant (e.g. NSE index
        expiries land at 15:45 IST == 10:15 UTC), NOT midnight of the expiry
        date. Sending midnight falls outside the expiry and Fyers answers
        "Please provide valid expiry" (code=1), which silently empties the
        option chain.

        The instant is resolved from the provider's own ``expiryData`` when
        it is known, and otherwise falls back to the IST session close
        (15:30 IST) of the expiry date.
        """
        if not expiry or len(expiry) != 10:
            return None
        # 1) The provider's own expiry instant (authoritative).
        try:
            from brokers.fyers.rest import expiry_epochs
            known = expiry_epochs()
        except Exception:
            known = {}
        for date_key, epoch in known.items():
            if _same_expiry_date(date_key, expiry):
                return str(epoch)
        # 2) Fallback: the expiry instant Fyers uses for NSE expiries -
        #    the expiry date at 15:40 IST (10:10 UTC). Verified against the
        #    provider's own expiryData (27-10-2026, 23-11-2026, 29-12-2026 all
        #    land on 15:40 IST).
        try:
            from datetime import datetime, timedelta, timezone
            ist = timezone(timedelta(hours=5, minutes=30))
            dt = datetime.strptime(expiry, "%Y-%m-%d").replace(
                hour=15, minute=40, tzinfo=ist)
            return str(int(dt.timestamp()))
        except (ValueError, TypeError):
            return None

    def _chain_symbol(self, instrument_key: str, *, exchange: str,
                      tradingsymbol: str, expiry: str) -> str:
        """The symbol the Fyers option-chain endpoint expects.

        LIVE-VERIFIED (2026-10-20, read-only authenticated probe against
        ``GET /data/options-chain-v3``):

        * the UNDERLYING symbol works — ``NSE:ADANIGREEN-EQ`` (equity) and
          ``NSE:NIFTY50-INDEX`` (index) both return ``s=ok, code=200``;
        * a CONTRACT symbol also works, but only because Fyers resolves it
          back to the same underlying chain;
        * the numeric fyToken is REJECTED (``code=-300``, "Please provide a
          valid symbol").

        The endpoint is therefore addressed by the UNDERLYING's provider
        symbol — the documented request attribute. A fyToken or a contract
        symbol is never sent.

        The symbol comes from the instrument catalogue (the authority on
        the provider's own identifier for the underlying), so short symbols
        such as ``IOC``/``LT`` resolve correctly and no string-shape guess
        is involved.
        """
        key = (instrument_key or "").strip()
        if not key:
            return key
        # A symbol-style key (EXCH:SYMBOL) is already the underlying's
        # provider symbol.
        if ":" in key and not key.isdigit():
            return key
        if self._catalog is None:
            return key
        resolved = self._resolve_underlying_symbol(
            exchange=exchange, tradingsymbol=tradingsymbol)
        if resolved:
            return resolved
        return key

    def _resolve_underlying_symbol(self, *, exchange: str,
                                   tradingsymbol: str) -> str | None:
        """The provider's own symbol for the UNDERLYING (catalogue-backed).

        Resolved from the catalogue's cash/index row for the underlying —
        never from a substring search over derivative contracts (which
        fails for short symbols) and never from the identifier's shape.
        """
        symbol = (tradingsymbol or "").strip()
        if not symbol or ":" in symbol:
            # Already a provider symbol (e.g. an index row's tradingsymbol).
            if ":" in symbol:
                return symbol
            return None
        for instrument_type in ("EQUITY", "INDEX"):
            row = None
            try:
                if instrument_type == "EQUITY":
                    row = self._catalog.find_equity(
                        symbol, provider="fyers", exchange=exchange)
                else:
                    row = self._catalog.find_index(
                        symbol, provider="fyers")
            except Exception:
                row = None
            if not row:
                # Fallback for catalogues without the exact-match helpers.
                try:
                    rows = self._catalog.search(
                        q=symbol, provider="fyers",
                        instrument_type=instrument_type, limit=50)
                except Exception:
                    rows = []
                for candidate in rows or []:
                    tsym = str(candidate.get("tradingsymbol") or "").upper()
                    und = str(candidate.get("underlying") or "").upper()
                    if tsym == symbol or und == symbol:
                        row = candidate
                        break
            if not row:
                continue
            provider_symbol = row.get("provider_symbol") or ""
            if provider_symbol and ":" in provider_symbol:
                return str(provider_symbol)
        return None

    async def history(
        self, *, instrument_key: str, resolution: str,
        from_date: str, to_date: str,
    ) -> list[Candle]:
        token = self._token()
        return await self._rest.history(
            token, instrument_key, resolution, from_date, to_date)

    async def option_chain(
        self, *, instrument_key: str, exchange: str,
        tradingsymbol: str = "", expiry: str = "",
    ) -> OptionChainSnapshot:
        token = self._token()
        # The expiry INSTANT (not midnight) is what Fyers addresses.
        timestamp = self._expiry_timestamp(expiry)
        symbol = self._chain_symbol(
            instrument_key, exchange=exchange, tradingsymbol=tradingsymbol,
            expiry=expiry)
        return await self._rest.option_chain(
            token, symbol, strikecount=50, timestamp=timestamp,
            expiry=expiry)
