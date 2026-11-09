"""YahooFinanceService — the only owner of the yfinance client.

This is an AI RESEARCH capability, not a market-data feed:
    User/AI -> MCP tools -> YahooFinanceService -> yfinance -> Yahoo

It is deliberately isolated from MarketService, the broker adapters, the
F&O universe, and the heatmap. Nothing here writes a quote, a feed, or a
persisted record. Every response is a fresh, structured, JSON-safe snapshot
carrying honest freshness metadata.

yfinance is injected (``yf=``) so tests never touch the network.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from yfinance_service.config import (
    MAX_HISTORY_ROWS,
    MAX_HOLDER_ROWS,
    MAX_OPTION_ROWS,
    MAX_SEARCH_LIMIT,
    MAX_STATEMENT_ROWS,
    MAX_STREAM_TIMEOUT_SECONDS,
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_LIST,
    VALID_INTERVALS,
    VALID_PERIODS,
    defaults,
)
from yfinance_service.errors import (
    DATA_UNAVAILABLE,
    INVALID_ARGUMENTS,
    INVALID_HOLDER_TYPE,
    INVALID_INTERVAL,
    INVALID_PERIOD,
    INVALID_REQUEST,
    INVALID_STATEMENT,
    INVALID_SYMBOL,
    SERVICE_UNAVAILABLE,
    SYMBOL_NOT_FOUND,
    UNAVAILABLE_FOR_ASSET,
    UNSUPPORTED_OPERATION,
    YahooError,
    map_yfinance_exception,
)
from yfinance_service.normalize import (
    classify_freshness,
    json_safe,
    records_from_frame,
    series_to_records,
)

logger = logging.getLogger(__name__)

SOURCE = "yahoo"
VALID_STATEMENTS = ("income", "balance", "cashflow", "all")
VALID_PERIOD_MODES = ("annual", "quarterly")
VALID_HOLDER_TYPES = ("all", "major", "institutional", "insider")

# Quote fields projected from Yahoo's ``info`` dict, in a stable order.
_QUOTE_FIELDS = (
    "regularMarketPrice", "previousClose", "open", "dayHigh", "dayLow",
    "regularMarketVolume", "bid", "ask", "bidSize", "askSize",
    "marketCap", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "fiftyTwoWeekChangePercent", "trailingPE", "forwardPE",
    "dividendYield", "beta", "sharesOutstanding", "targetMeanPrice",
    "recommendationKey", "numberOfAnalystOpinions",
)

# Company fields projected from Yahoo's ``info`` dict.
_COMPANY_FIELDS = (
    "longName", "shortName", "quoteType", "typeDisp", "sector", "sectorKey",
    "industry", "industryKey", "website", "country", "city", "state",
    "address1", "zip", "phone", "fullTimeEmployees", "longBusinessSummary",
)

# Tickers that are structural, not equity — statements/holders don't apply.
_NON_EQUITY_QUOTE_TYPES = frozenset({
    "INDEX", "CRYPTOCURRENCY", "CURRENCY", "FUTURE", "OPTION", "BOND",
})


class YahooFinanceService:
    """Structured, honest, on-demand Yahoo Finance research reads."""

    VALID_PERIODS = frozenset(VALID_PERIODS)
    VALID_INTERVALS = frozenset(VALID_INTERVALS)

    def __init__(self, yf: Any = None, settings: dict[str, Any] | None = None,
                 sleep: Any = None) -> None:
        self._yf = yf if yf is not None else _load_yfinance()
        self._settings = {**defaults(), **(settings or {})}
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._last_call_at = 0.0
        self._lock = asyncio.Lock()

    # ── plumbing ────────────────────────────────────────────────────────

    @property
    def yf_version(self) -> str:
        return str(getattr(self._yf, "__version__", "unknown"))

    def available(self) -> bool:
        return self._yf is not None

    async def _throttle(self) -> None:
        """Minimum gap between outbound calls — Yahoo courtesy + rate limit."""
        gap = float(self._settings.get("min_request_interval_seconds", 0.5))
        if gap <= 0:
            return
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < gap:
            await self._sleep(gap - elapsed)
        self._last_call_at = time.monotonic()

    def _ticker(self, symbol: str) -> Any:
        return self._yf.Ticker(symbol)

    def _info(self, symbol: str) -> dict[str, Any]:
        try:
            raw = self._ticker(symbol).info
        except Exception as exc:
            raise map_yfinance_exception(exc) from None
        return dict(raw or {})

    def _base(self, *, symbol: str, data_type: str,
              info: dict[str, Any] | None = None,
              data: Any = None, warnings: list[str] | None = None,
              resolved_symbol: str | None = None,
              freshness: str | None = None) -> dict[str, Any]:
        info = info or {}
        return json_safe({
            "status": "ok",
            "source": SOURCE,
            "symbol": symbol,
            "requested_symbol": symbol,
            "resolved_symbol": resolved_symbol or info.get("symbol") or symbol,
            "data_type": data_type,
            "quote_type": info.get("quoteType"),
            "currency": info.get("currency") or info.get("financialCurrency"),
            "exchange": (info.get("fullExchangeName") or info.get("exchange")),
            "market_state": info.get("marketState"),
            "quote_source_name": info.get("quoteSourceName"),
            "exchange_data_delayed_by": info.get("exchangeDataDelayedBy"),
            "source_interval": info.get("sourceInterval"),
            "market_time": json_safe(info.get("regularMarketTime")),
            "timestamp": _now_iso(),
            "freshness": freshness or classify_freshness(info),
            "data": data if data is not None else {},
            "warnings": warnings or [],
        })

    @staticmethod
    def _err(exc: BaseException, symbol: str | None = None) -> dict[str, Any]:
        err = (exc if isinstance(exc, YahooError)
               else map_yfinance_exception(exc))
        payload = err.to_dict()
        payload["symbol"] = symbol
        return payload

    async def _call(self, name: str, symbol: str | None, **kwargs: Any) -> dict:
        """Invoke a public method, converting typed Yahoo failures to data.

        Validation errors (bad period/interval/symbol/statement) propagate —
        the MCP layer owns input validation and needs them to raise. Provider
        and data failures come back as a structured error payload, which is
        what the spec requires for unavailable Yahoo data.
        """
        try:
            return await getattr(self, name)(**kwargs)
        except YahooError as exc:
            if exc.code in (INVALID_PERIOD, INVALID_INTERVAL,
                            INVALID_STATEMENT, INVALID_HOLDER_TYPE,
                            INVALID_ARGUMENTS, INVALID_SYMBOL):
                raise
            return self._err(exc, symbol)

    # ── validation helpers ───────────────────────────────────────────────

    @staticmethod
    def _clean_symbol(raw: Any) -> str:
        s = str(raw or "").strip()
        if not s:
            raise YahooError(INVALID_SYMBOL, "symbol must not be empty")
        if len(s) > 64:
            raise YahooError(INVALID_SYMBOL, "symbol is too long")
        return s

    def _check_period(self, period: str | None) -> str | None:
        if period is None:
            return None
        p = str(period).strip().lower()
        if p not in self.VALID_PERIODS:
            raise YahooError(
                INVALID_PERIOD,
                f"period must be one of: {', '.join(VALID_PERIODS)}")
        return p

    def _check_interval(self, interval: str | None) -> str:
        iv = str(interval or "1d").strip().lower()
        if iv not in self.VALID_INTERVALS:
            raise YahooError(
                INVALID_INTERVAL,
                f"interval must be one of: {', '.join(VALID_INTERVALS)}")
        return iv

    @staticmethod
    def _require_equity(info: dict[str, Any], symbol: str,
                        operation: str) -> None:
        qtype = str(info.get("quoteType") or "").upper()
        if qtype in _NON_EQUITY_QUOTE_TYPES:
            raise YahooError(
                UNAVAILABLE_FOR_ASSET,
                f"{operation} is not available for {qtype} instruments "
                f"({symbol}); Yahoo exposes these for equities/ETFs only")

    # ── public API ──────────────────────────────────────────────────────

    async def search(self, *, query: str, limit: int = 10,
                     include_news: bool = False) -> dict[str, Any]:
        """Symbol/company discovery via yfinance's Yahoo search."""
        q = str(query or "").strip()
        if not q:
            raise YahooError(INVALID_ARGUMENTS, "query must not be empty")
        lim = max(1, min(int(limit or 10), MAX_SEARCH_LIMIT))
        async with self._lock:
            await self._throttle()
            try:
                result = self._yf.Search(q)
                quotes = list(getattr(result, "quotes", None) or [])
                news = list(getattr(result, "news", None) or [])
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        if not quotes:
            raise YahooError(
                SYMBOL_NOT_FOUND,
                f"no Yahoo symbol matched '{q}'")
        matches = [{
            "symbol": x.get("symbol"),
            "short_name": x.get("shortname") or x.get("longname"),
            "exchange": x.get("exchDisp") or x.get("exchange"),
            "quote_type": x.get("quoteType"),
            "type_display": x.get("typeDisp"),
            "sector": x.get("sectorDisp"),
            "industry": x.get("industryDisp"),
        } for x in quotes[:lim]]
        data: dict[str, Any] = {"count": len(matches), "matches": matches}
        if include_news and news:
            data["news"] = [{
                "title": n.get("title"),
                "publisher": n.get("publisher"),
                "link": n.get("link"),
                "published": json_safe(n.get("providerPublishTime")),
                "type": n.get("type"),
            } for n in news[:MAX_SUMMARY_LIST]]
        return self._base(symbol=q, data_type="search", data=data,
                          resolved_symbol=matches[0].get("symbol"),
                          freshness="SNAPSHOT")

    async def quote(self, *, symbol: str,
                    fields: list[str] | None = None) -> dict[str, Any]:
        """Current quote snapshot across every Yahoo asset class."""
        sym = self._clean_symbol(symbol)
        async with self._lock:
            await self._throttle()
            try:
                ticker = self._ticker(sym)
                info = dict(getattr(ticker, "info", None) or {})
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        if not info:
            raise YahooError(
                INVALID_SYMBOL, f"Yahoo has no data for symbol '{sym}'")
        price = info.get("regularMarketPrice")
        if price is None:
            price = info.get("currentPrice")
        if price is None:
            raise YahooError(
                DATA_UNAVAILABLE,
                f"Yahoo returned no current price for '{sym}'")
        data: dict[str, Any] = {
            "price": price,
            "quote_type": info.get("quoteType"),
            "name": info.get("longName") or info.get("shortName"),
        }
        for f in _QUOTE_FIELDS:
            if info.get(f) is not None:
                data[f] = info.get(f)
        if fields:
            wanted = {str(f).strip() for f in fields if str(f).strip()}
            if wanted:
                data = {k: v for k, v in data.items()
                        if k in wanted or k in ("price", "quote_type", "name")}
        warnings: list[str] = []
        delayed = info.get("exchangeDataDelayedBy")
        if delayed:
            warnings.append(
                f"Yahoo reports this quote delayed by {delayed} minute(s)")
        if str(info.get("quoteType") or "").upper() == "INDEX":
            warnings.append("Index data has no order book or bid/ask")
        return self._base(symbol=sym, data_type="quote", info=info,
                          data=data, warnings=warnings)

    async def history(self, *, symbol: str, period: str | None = "1mo",
                      interval: str = "1d", start: str | None = None,
                      end: str | None = None,
                      events: str = "div,splits",
                      limit: int | None = None) -> dict[str, Any]:
        """OHLCV candles for any yfinance-supported interval."""
        sym = self._clean_symbol(symbol)
        iv = self._check_interval(interval)
        if period and (start or end):
            raise YahooError(
                INVALID_ARGUMENTS,
                "use either period or start/end, not both")
        per = self._check_period(period) if not (start or end) else None
        cap = min(int(limit or MAX_HISTORY_ROWS), MAX_HISTORY_ROWS)
        async with self._lock:
            await self._throttle()
            try:
                # NOTE: do NOT pass raise_errors — deprecated in yfinance
                # 1.7.0. An unknown/dead symbol yields an empty frame, which
                # we surface honestly as UNAVAILABLE with a warning.
                kwargs: dict[str, Any] = {
                    "interval": iv, "auto_adjust": False, "actions": True,
                }
                if start or end:
                    kwargs["start"] = start
                    kwargs["end"] = end
                else:
                    kwargs["period"] = per or "1mo"
                frame = self._ticker(sym).history(**kwargs)
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        rows = records_from_frame(frame, limit=cap)
        data = {
            "rows": rows,
            "count": len(rows),
            "interval": iv,
            "period": per,
            "start": start,
            "end": end,
            "columns": list(rows[0].keys()) if rows else [],
        }
        warnings: list[str] = []
        if not rows:
            warnings.append(
                "Yahoo returned no candles for this range/interval")
        return self._base(symbol=sym, data_type="history",
                          data=data, warnings=warnings,
                          freshness="HISTORICAL" if rows else "UNAVAILABLE")

    async def company(self, *, symbol: str) -> dict[str, Any]:
        """Company / instrument profile (sector, industry, business summary)."""
        sym = self._clean_symbol(symbol)
        async with self._lock:
            await self._throttle()
            try:
                info = dict(self._ticker(sym).info or {})
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        if not info:
            raise YahooError(
                INVALID_SYMBOL, f"Yahoo has no company data for '{sym}'")
        data = {f: info.get(f) for f in _COMPANY_FIELDS
                if info.get(f) is not None}
        summary = data.get("longBusinessSummary")
        if isinstance(summary, str) and len(summary) > MAX_SUMMARY_CHARS:
            data["longBusinessSummary"] = summary[:MAX_SUMMARY_CHARS] + " ..."
        warnings: list[str] = []
        if not data.get("longBusinessSummary"):
            warnings.append(
                "Yahoo exposes no business summary for this instrument")
        return self._base(symbol=sym, data_type="company", info=info,
                          data=data, warnings=warnings)

    async def financials(self, *, symbol: str, period: str = "annual",
                         statement: str = "income",
                         limit: int | None = None) -> dict[str, Any]:
        """Income / balance sheet / cash flow, annual or quarterly."""
        sym = self._clean_symbol(symbol)
        mode = str(period or "annual").strip().lower()
        if mode not in VALID_PERIOD_MODES:
            raise YahooError(
                INVALID_PERIOD,
                f"period must be one of: {', '.join(VALID_PERIOD_MODES)}")
        what = str(statement or "income").strip().lower()
        if what not in VALID_STATEMENTS:
            raise YahooError(
                INVALID_STATEMENT,
                f"statement must be one of: {', '.join(VALID_STATEMENTS)}")
        cap = min(int(limit or MAX_STATEMENT_ROWS), MAX_STATEMENT_ROWS)
        async with self._lock:
            await self._throttle()
            try:
                info = dict(self._ticker(sym).info or {})
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        self._require_equity(info, sym, "financial statements")
        wanted = VALID_STATEMENTS if what == "all" else (what,)
        attr = {
            "income": "income_stmt",
            "balance": "balance_sheet",
            "cashflow": "cashflow",
        }
        quarterly = mode == "quarterly"
        data: dict[str, Any] = {"period": mode, "statements": {}}
        warnings: list[str] = []
        for name in wanted:
            prop = attr[name]
            if quarterly:
                prop = f"quarterly_{prop}"
            try:
                frame = getattr(self._ticker(sym), prop)
            except Exception:
                frame = None
            if frame is None:
                warnings.append(
                    f"Yahoo exposes no {name} statement for '{sym}'")
                data["statements"][name] = {"rows": [], "count": 0}
                continue
            rows = records_from_frame(frame, limit=cap)
            data["statements"][name] = {
                "rows": rows, "count": len(rows),
                "line_items": [str(i) for i in list(frame.index)[:cap]],
                "periods": [str(c) for c in list(frame.columns)],
            }
            if not rows:
                warnings.append(f"{name} statement is empty for '{sym}'")
        return self._base(symbol=sym, data_type="financials", info=info,
                          data=data, warnings=warnings,
                          freshness="HISTORICAL")

    async def corporate_actions(self, *, symbol: str,
                                limit: int | None = None) -> dict[str, Any]:
        """Dividend and split history."""
        sym = self._clean_symbol(symbol)
        cap = min(int(limit or MAX_STATEMENT_ROWS), MAX_STATEMENT_ROWS)
        async with self._lock:
            await self._throttle()
            try:
                ticker = self._ticker(sym)
                divs = getattr(ticker, "dividends", None)
                splits = getattr(ticker, "splits", None)
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        dividends = series_to_records(
            divs, value_name="amount", date_name="ex_date", limit=cap)
        split_rows = series_to_records(
            splits, value_name="ratio", date_name="ex_date", limit=cap)
        data = {
            "dividends": dividends, "dividend_count": len(dividends),
            "splits": split_rows, "split_count": len(split_rows),
        }
        warnings: list[str] = []
        if not dividends and not split_rows:
            warnings.append(
                f"Yahoo exposes no dividend/split history for '{sym}'")
        return self._base(symbol=sym, data_type="corporate_actions",
                          data=data, warnings=warnings,
                          freshness="HISTORICAL")

    async def holders(self, *, symbol: str,
                      holder_type: str = "all",
                      limit: int | None = None) -> dict[str, Any]:
        """Major / institutional / insider ownership where Yahoo exposes it."""
        sym = self._clean_symbol(symbol)
        kind = str(holder_type or "all").strip().lower()
        if kind not in VALID_HOLDER_TYPES:
            raise YahooError(
                INVALID_HOLDER_TYPE,
                f"holder_type must be one of: {', '.join(VALID_HOLDER_TYPES)}")
        cap = min(int(limit or MAX_HOLDER_ROWS), MAX_HOLDER_ROWS)
        async with self._lock:
            await self._throttle()
            try:
                info = dict(self._ticker(sym).info or {})
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        self._require_equity(info, sym, "ownership data")
        wanted = (("major", "institutional", "insider")
                  if kind == "all" else (kind,))
        prop = {"major": "major_holders", "institutional":
                "institutional_holders", "insider": "insider_transactions"}
        data: dict[str, Any] = {"holder_type": kind, "groups": {}}
        warnings: list[str] = []
        for name in wanted:
            try:
                frame = getattr(self._ticker(sym), prop[name])
            except Exception:
                frame = None
            rows = records_from_frame(frame, limit=cap)
            data["groups"][name] = {"rows": rows, "count": len(rows)}
            if not rows:
                warnings.append(
                    f"Yahoo exposes no {name} holder data for '{sym}'")
        return self._base(symbol=sym, data_type="holders", info=info,
                          data=data, warnings=warnings,
                          freshness="HISTORICAL")

    async def options(self, *, symbol: str, date: str | None = None,
                      limit: int | None = None) -> dict[str, Any]:
        """Yahoo option chain — clearly identified as Yahoo research data.

        This NEVER feeds the broker-authoritative F&O path; the canonical
        MarketHub option chain is unchanged.
        """
        sym = self._clean_symbol(symbol)
        cap = min(int(limit or MAX_OPTION_ROWS), MAX_OPTION_ROWS)
        async with self._lock:
            await self._throttle()
            try:
                ticker = self._ticker(sym)
                expirations = list(getattr(ticker, "options", None) or ())
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        if not expirations:
            raise YahooError(
                UNAVAILABLE_FOR_ASSET,
                f"Yahoo lists no options for '{sym}'")
        chosen = str(date).strip() if date else expirations[0]
        if chosen not in expirations:
            raise YahooError(
                INVALID_REQUEST,
                f"expiry '{chosen}' not listed by Yahoo; available: "
                f"{', '.join(expirations[:12])}")
        async with self._lock:
            await self._throttle()
            try:
                chain = self._ticker(sym).option_chain(chosen)
            except Exception as exc:
                raise map_yfinance_exception(exc) from None
        data = {
            "expirations": expirations,
            "expiration_count": len(expirations),
            "expiry": chosen,
            "underlying": json_safe(getattr(chain, "underlying", None) or {}),
            "calls": records_from_frame(
                getattr(chain, "calls", None), limit=cap),
            "puts": records_from_frame(
                getattr(chain, "puts", None), limit=cap),
        }
        data["call_count"] = len(data["calls"])
        data["put_count"] = len(data["puts"])
        warnings = ["Yahoo option data is research-only; it is not the "
                    "broker-authoritative MarketHub F&O chain"]
        return self._base(symbol=sym, data_type="options", data=data,
                          warnings=warnings, freshness="SNAPSHOT")

    async def live_quote(self, *, symbol: str,
                         timeout: float | None = None) -> dict[str, Any]:
        """One bounded Yahoo WebSocket tick.

        Bounded by design: a fresh socket, at most one tick, a hard timeout,
        always closed. No permanent per-request connection, no shared
        subscription storm, no permanent polling loop.
        """
        sym = self._clean_symbol(symbol)
        tmo = float(timeout if timeout is not None
                    else self._settings.get("stream_timeout_seconds", 10.0))
        tmo = max(1.0, min(tmo, MAX_STREAM_TIMEOUT_SECONDS))
        if not hasattr(self._yf, "AsyncWebSocket"):
            return self._stream_unavailable(
                sym, "installed yfinance exposes no AsyncWebSocket")
        tick, err = await self._collect_tick(sym, tmo)
        if err is not None:
            return err
        return self._base(symbol=sym, data_type="live_quote", data=tick,
                          freshness="LIVE_STREAM",
                          warnings=["Single WebSocket tick; Yahoo stream "
                                    "entitlement is not guaranteed per symbol"])

    def _stream_unavailable(self, symbol: str,
                            reason: str) -> dict[str, Any]:
        from yfinance_service.errors import (
            YahooStreamUnavailableError,
        )
        return self._err(YahooStreamUnavailableError(reason), symbol)

    async def _collect_tick(self, symbol: str,
                            timeout: float) -> tuple[dict, dict | None]:
        """Subscribe once, take one tick, close. Never raises.

        yfinance's ``AsyncWebSocket.listen()`` is a COROUTINE that runs
        forever and pushes messages into a callback — it is NOT an async
        iterator. We therefore run it as a task, capture the first tick via
        the callback, then cancel it and close the socket. Getting this wrong
        (iterating it) silently yields no ticks and leaks the coroutine.
        """
        from yfinance_service.errors import YahooStreamUnavailableError
        ws = None
        listener: asyncio.Task | None = None
        got: list[dict] = []
        done = asyncio.Event()

        def _on_message(message: Any) -> None:
            if not got:
                got.append(dict(message or {}))
                done.set()

        try:
            await self._throttle()
            ws = self._yf.AsyncWebSocket(verbose=False)
            await asyncio.wait_for(ws.subscribe([symbol]), timeout=timeout)
            listener = asyncio.ensure_future(ws.listen(_on_message))
            try:
                await asyncio.wait_for(done.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        except asyncio.TimeoutError:
            return {}, self._err(YahooStreamUnavailableError(
                f"no Yahoo stream tick within {timeout:g}s"), symbol)
        except Exception as exc:
            return {}, self._err(map_yfinance_exception(exc), symbol)
        finally:
            if listener is not None and not listener.done():
                listener.cancel()
                try:
                    await listener
                except (asyncio.CancelledError, Exception):
                    pass
            if ws is not None:
                await _safe_close(ws)
        if not got:
            return {}, self._err(YahooStreamUnavailableError(
                "Yahoo stream returned no tick for this symbol"), symbol)
        first = got[0]
        data = {
            "raw_message_keys": sorted(first.keys()),
            "price": first.get("price"),
            "exchange_timestamp": json_safe(first.get("timestamp")),
            "received_at": _now_iso(),
        }
        for key in ("id", "exchange", "quote_type"):
            if first.get(key) is not None:
                data[key] = json_safe(first.get(key))
        return data, None


async def _safe_close(ws: Any) -> None:
    try:
        result = ws.close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        logger.debug("yahoo websocket close failed", exc_info=True)


def _load_yfinance() -> Any:
    """Import yfinance lazily so a missing/broken install degrades cleanly."""
    try:
        import yfinance
        return yfinance
    except Exception:
        logger.warning("yfinance is not importable; Yahoo tools disabled")
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# Re-exported for the MCP layer's fail-closed validation.
__all__ = [
    "YahooFinanceService", "SOURCE", "VALID_STATEMENTS",
    "VALID_PERIOD_MODES", "VALID_HOLDER_TYPES", "VALID_PERIODS",
    "VALID_INTERVALS", "SERVICE_UNAVAILABLE", "UNSUPPORTED_OPERATION",
]
