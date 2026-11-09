"""Yahoo Finance research service — deterministic unit tests (no network).

yfinance is injected as a fake, so the normal suite never depends on Yahoo
being online. Covers parameter validation, structured responses, and the
error-mapping contract.
"""
from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pandas as pd
import pytest

from yfinance_service.errors import (
    YahooError,
    map_yfinance_exception,
)
from yfinance_service.service import (
    VALID_PERIODS,
    YahooFinanceService,
)

# ── Parameter validation ─────────────────────────────────────────────────────

class TestPeriodValidation:
    def test_valid_periods_accepted(self):
        svc = YahooFinanceService(yf=FakeYF())
        assert svc.VALID_PERIODS == frozenset(VALID_PERIODS)

    async def test_invalid_period_raises(self):
        svc = YahooFinanceService(yf=FakeYF())
        with pytest.raises(YahooError) as exc:
            await svc.history(symbol="AAPL", period="7q")
        assert exc.value.code == "invalid_period"


class TestIntervalValidation:
    async def test_valid_interval_accepted(self):
        svc = YahooFinanceService(yf=FakeYF())
        # 1d is valid; must not raise
        await svc.history(symbol="AAPL", period="1mo", interval="1d")

    async def test_invalid_interval_raises(self):
        svc = YahooFinanceService(yf=FakeYF())
        with pytest.raises(YahooError) as exc:
            await svc.history(symbol="AAPL", period="1mo", interval="3s")
        assert exc.value.code == "invalid_interval"


class TestSymbolValidation:
    async def test_empty_symbol_raises(self):
        svc = YahooFinanceService(yf=FakeYF())
        with pytest.raises(YahooError) as exc:
            await svc.quote(symbol="")
        assert exc.value.code == "invalid_symbol"

    async def test_whitespace_symbol_raises(self):
        svc = YahooFinanceService(yf=FakeYF())
        with pytest.raises(YahooError) as exc:
            await svc.quote(symbol="   ")
        assert exc.value.code == "invalid_symbol"

    async def test_valid_symbol_accepted(self):
        svc = YahooFinanceService(yf=FakeYF())
        result = await svc.quote(symbol="AAPL")
        assert result["status"] == "ok"


# ── Structured response shape ────────────────────────────────────────────────

class TestQuoteResponse:
    async def test_quote_has_required_metadata_keys(self):
        svc = YahooFinanceService(yf=FakeYF())
        result = await svc.quote(symbol="AAPL")
        for key in ("source", "symbol", "data_type", "freshness",
                    "currency", "exchange", "data", "warnings"):
            assert key in result, f"missing key: {key}"

    async def test_quote_data_type_is_quote(self):
        svc = YahooFinanceService(yf=FakeYF())
        result = await svc.quote(symbol="AAPL")
        assert result["data_type"] == "quote"

    async def test_quote_includes_price(self):
        svc = YahooFinanceService(yf=FakeYF())
        result = await svc.quote(symbol="AAPL")
        assert result["data"]["price"] == 150.0

    async def test_quote_includes_quote_type(self):
        svc = YahooFinanceService(yf=FakeYF())
        result = await svc.quote(symbol="AAPL")
        assert result["data"]["quote_type"] == "EQUITY"

    async def test_no_nan_in_response(self):
        svc = YahooFinanceService(yf=FakeYF())
        result = await svc.quote(symbol="AAPL")
        import json
        json.dumps(result)  # must not raise (no NaN in payload)

    async def test_invalid_symbol_raises_typed_error(self):
        """The SERVICE raises; the MCP tool layer converts to structured data."""
        svc = YahooFinanceService(yf=FakeYF())
        with pytest.raises(YahooError) as exc:
            await svc.quote(symbol="NOTAREALSYMBOL12345")
        assert exc.value.code == "invalid_symbol"
        assert exc.value.to_dict()["source"] == "yahoo"

    async def test_error_payload_is_json_safe(self):
        svc = YahooFinanceService(yf=FakeYF())
        with pytest.raises(YahooError) as exc:
            await svc.quote(symbol="NOTAREALSYMBOL12345")
        import json
        json.dumps(exc.value.to_dict())  # no NaN / no traceback


# ── Error mapping ────────────────────────────────────────────────────────────

class TestErrorMapping:
    def test_rate_limit_maps_to_rate_limited(self):
        exc = Exception("Too Many Requests. Rate limited. Please try again later.")
        result = map_yfinance_exception(exc)
        assert result.code == "rate_limited"

    def test_404_maps_to_invalid_symbol(self):
        exc = Exception("HTTP Error 404: Not Found")
        result = map_yfinance_exception(exc)
        assert result.code == "invalid_symbol"

    def test_no_data_maps_to_invalid_symbol(self):
        """'No data found, delisted' is a dead symbol, not a network fault."""
        exc = Exception("No data found, symbol may be delisted")
        result = map_yfinance_exception(exc)
        assert result.code == "invalid_symbol"

    def test_unmapped_exception_maps_to_network_error(self):
        exc = Exception("Connection reset by peer")
        result = map_yfinance_exception(exc)
        assert result.code == "network_error"

    def test_error_reason_does_not_leak_stack_trace(self):
        exc = Exception("Connection reset by peer")
        result = map_yfinance_exception(exc)
        assert "Traceback" not in result.reason
        assert "\n" not in result.reason

    def test_error_reason_is_truncated(self):
        exc = Exception("x" * 5000)
        result = map_yfinance_exception(exc)
        assert len(result.reason) <= 300

    def test_error_reason_redacts_long_blobs(self):
        exc = Exception("token=" + "A" * 80)
        result = map_yfinance_exception(exc)
        assert "A" * 80 not in result.reason

    def test_error_has_default_freshness(self):
        exc = Exception("network")
        result = map_yfinance_exception(exc)
        assert result.freshness == "UNAVAILABLE"


# ── Test doubles ─────────────────────────────────────────────────────────────

class FakeFastInfo:
    def __init__(self, data):
        self._d = data

    def __getattr__(self, k):
        return self._d.get(k)


class FakeTicker:
    def __init__(self, symbol, yf):
        self._symbol = symbol
        self._yf = yf

    @property
    def fast_info(self):
        if self._symbol == "NOTAREALSYMBOL12345":
            raise Exception("No data found, symbol may be delisted")
        return FakeFastInfo({
            "last_price": 150.0,
            "market_cap": 3_000_000_000_000.0,
            "currency": "USD",
            "exchange": "NMS",
            "quote_type": "EQUITY",
            "previous_close": 145.0,
            "open": 146.0,
            "day_high": 155.0,
            "day_low": 144.0,
            "last_volume": 50_000_000,
            "year_high": 200.0,
            "year_low": 100.0,
        })

    @property
    def info(self):
        if self._symbol == "NOTAREALSYMBOL12345":
            return {}
        return {
            "symbol": self._symbol,
            "shortName": "Apple Inc.",
            "longName": "Apple Inc.",
            "quoteType": "EQUITY",
            "currency": "USD",
            "exchange": "NMS",
            "fullExchangeName": "NasdaqGS",
            "sector": "Technology",
            "sectorKey": "technology",
            "sectorDisp": "Technology",
            "industry": "Consumer Electronics",
            "industryKey": "consumer-electronics",
            "industryDisp": "Consumer Electronics",
            "website": "https://www.apple.com",
            "country": "United States",
            "city": "Cupertino",
            "longBusinessSummary": "Apple designs and sells smartphones.",
            "fullTimeEmployees": 164000,
            "marketCap": 3_000_000_000_000.0,
            "regularMarketPrice": 150.0,
            "regularMarketChange": 5.0,
            "regularMarketChangePercent": 0.0344,
            "regularMarketTime": 1790000000,
            "regularMarketDayHigh": 155.0,
            "regularMarketDayLow": 144.0,
            "regularMarketOpen": 146.0,
            "regularMarketPreviousClose": 145.0,
            "regularMarketVolume": 50_000_000,
            "previousClose": 145.0,
            "open": 146.0,
            "dayHigh": 155.0,
            "dayLow": 144.0,
            "volume": 50_000_000,
            "bid": 149.99,
            "ask": 150.01,
            "bidSize": 100,
            "askSize": 200,
            "marketState": "REGULAR",
            "quoteSourceName": "Nasdaq Real Time",
            "exchangeDataDelayedBy": 0,
            "sourceInterval": 15,
            "fiftyTwoWeekHigh": 200.0,
            "fiftyTwoWeekLow": 100.0,
            "fiftyTwoWeekChangePercent": 0.15,
            "trailingPE": 28.5,
            "forwardPE": 26.1,
            "dividendYield": 0.005,
            "beta": 1.2,
            "sharesOutstanding": 15_000_000_000,
            "targetMeanPrice": 180.0,
            "recommendationKey": "buy",
            "numberOfAnalystOpinions": 42,
        }

    def history(self, **kwargs):
        idx = pd.to_datetime(
            ["2026-09-22", "2026-09-23", "2026-09-24"], utc=True)
        return pd.DataFrame(
            {
                "Open": [145.0, 147.0, 146.0],
                "High": [150.0, 152.0, 155.0],
                "Low": [144.0, 146.0, 144.0],
                "Close": [149.0, 151.0, 150.0],
                "Volume": [1000, 2000, 3000],
                "Dividends": [0.0, 0.0, 0.0],
                "Stock Splits": [0.0, 0.0, 0.0],
            },
            index=idx,
        )

    @property
    def dividends(self):
        idx = pd.to_datetime(["2026-08-01"], utc=True)
        return pd.Series([0.25], index=idx)

    @property
    def splits(self):
        idx = pd.to_datetime(["2026-06-01"], utc=True)
        return pd.Series([2.0], index=idx)

    @property
    def options(self):
        return ("2026-09-25", "2026-10-02")

    def option_chain(self, date=None):
        class _Chain:
            underlying = {"symbol": self._symbol, "currency": "USD"}
            calls = pd.DataFrame({
                "contractSymbol": ["AAPL260925C00150000"],
                "lastTradeDate": [1790000000],
                "strike": [150.0],
                "lastPrice": [5.0],
                "bid": [4.9],
                "ask": [5.1],
                "change": [0.1],
                "percentChange": [0.02],
                "volume": [100],
                "openInterest": [1000],
                "impliedVolatility": [0.25],
                "inTheMoney": [True],
                "contractSize": ["REGULAR"],
                "currency": ["USD"],
            })
            puts = pd.DataFrame({
                "contractSymbol": ["AAPL260925P00150000"],
                "lastTradeDate": [1790000000],
                "strike": [150.0],
                "lastPrice": [4.0],
                "bid": [3.9],
                "ask": [4.1],
                "change": [-0.1],
                "percentChange": [-0.02],
                "volume": [80],
                "openInterest": [900],
                "impliedVolatility": [0.27],
                "inTheMoney": [False],
                "contractSize": ["REGULAR"],
                "currency": ["USD"],
            })
        return _Chain()

    @property
    def income_stmt(self):
        return pd.DataFrame(
            {"2026-06-30": [400_000_000_000.0],
             "2025-06-30": [380_000_000_000.0]},
            index=["Total Revenue", "Net Income"],
        )

    @property
    def quarterly_income_stmt(self):
        return pd.DataFrame(
            {"2026-06-30": [100_000_000_000.0],
             "2026-03-31": [95_000_000_000.0]},
            index=["Total Revenue", "Net Income"],
        )

    @property
    def balance_sheet(self):
        return pd.DataFrame(
            {"2026-06-30": [500_000_000_000.0]},
            index=["Total Assets"],
        )

    @property
    def quarterly_balance_sheet(self):
        return pd.DataFrame(
            {"2026-06-30": [500_000_000_000.0]},
            index=["Total Assets"],
        )

    @property
    def cashflow(self):
        return pd.DataFrame(
            {"2026-06-30": [100_000_000_000.0]},
            index=["Free Cash Flow"],
        )

    @property
    def quarterly_cashflow(self):
        return pd.DataFrame(
            {"2026-06-30": [25_000_000_000.0]},
            index=["Free Cash Flow"],
        )

    @property
    def recommendations(self):
        return pd.DataFrame(
            {"period": ["2026-09", "2026-08"], "strongBuy": [15, 14],
             "buy": [25, 24], "hold": [8, 9], "sell": [1, 1], "strongSell": [0, 0]},
        ).set_index("period")

    @property
    def upgrades_downgrades(self):
        return pd.DataFrame(
            {
                "Firm": ["Goldman Sachs"],
                "ToGrade": ["Buy"],
                "FromGrade": ["Neutral"],
                "Action": ["up"],
            },
            index=pd.to_datetime(["2026-09-01"], utc=True),
        )

    @property
    def major_holders(self):
        return pd.DataFrame(
            {"Value": [0.017, 0.517]},
            index=["insidersPercentHeld", "institutionsPercentHeld"],
        )

    @property
    def institutional_holders(self):
        return pd.DataFrame(
            {
                "Date Reported": [pd.Timestamp("2026-06-30")],
                "Holder": ["Vanguard Group"],
                "pctHeld": [0.089],
                "Shares": [1_000_000_000],
                "Value": [150_000_000_000],
            },
        )

    @property
    def insider_transactions(self):
        return pd.DataFrame(
            {
                "Insider": ["Tim Cook"],
                "Position": ["CEO"],
                "Text": ["Sale at price 150.00"],
                "Shares": [100_000],
                "Value": [15_000_000],
                "Start Date": [pd.Timestamp("2026-08-01")],
            },
        )

    @property
    def calendar(self):
        return {
            "Earnings Date": [pd.Timestamp("2026-10-30")],
            "Dividend Date": [pd.Timestamp("2026-11-01")],
        }


class FakeYF:
    """Fake yfinance module — same public shape, no network."""
    __version__ = "0.0.0-fake"

    def Ticker(self, symbol, **kwargs):
        return FakeTicker(symbol, self)

    def Search(self, query, **kwargs):
        return FakeSearch(query)

    def download(self, symbols, **kwargs):
        frames = {}
        for s in symbols:
            frames[s] = FakeTicker(s, self).history()
        return pd.concat(frames, axis=1)


class FakeSearch:
    def __init__(self, query):
        self._q = query

    @property
    def quotes(self):
        return [
            {"symbol": "AAPL", "shortname": "Apple Inc.",
             "exchange": "NMS", "quoteType": "EQUITY",
             "index": "quotes", "score": 100.0},
            {"symbol": "AAPL.MX", "shortname": "Apple Inc. Mexico",
             "exchange": "MEX", "quoteType": "EQUITY",
             "index": "quotes", "score": 50.0},
        ]

    @property
    def news(self):
        return []


# -- WebSocket / live-tick contract ----------------------------------------

class FakeAsyncWebSocket:
    """Fake AsyncWebSocket matching yfinance 1.7.0's real shape.

    listen() is a COROUTINE (long-running task), not an async iterator.
    Getting this wrong is the classic yfinance streaming bug.
    """

    instances: list[FakeAsyncWebSocket] = []

    def __init__(self, url="wss://example", verbose=True):
        self.url = url
        self.verbose = verbose
        self.subscribed: list[str] = []
        self.closed = False
        self.listen_calls = 0
        self._handler = None
        FakeAsyncWebSocket.instances.append(self)

    async def subscribe(self, symbols):
        self.subscribed.extend(
            [symbols] if isinstance(symbols, str) else list(symbols))

    async def unsubscribe(self, symbols):
        pass

    async def listen(self, message_handler=None):
        self.listen_calls += 1
        self._handler = message_handler
        if message_handler is not None:
            message_handler({"id": "AAPL", "price": 151.25,
                             "timestamp": 1790000000, "exchange": "NMS"})
            # Real listen() runs forever; emulate with a short idle.
            await asyncio.sleep(0.01)

    async def close(self):
        self.closed = True


class FakeYFWithStream(FakeYF):
    def __init__(self):
        FakeAsyncWebSocket.instances = []

    def __getattr__(self, name):
        raise AttributeError(name)


class _StreamYF(FakeYF):
    AsyncWebSocket = FakeAsyncWebSocket


class TestLiveQuote:
    async def test_collects_one_tick_and_closes(self):
        svc = YahooFinanceService(yf=_StreamYF())
        result = await svc.live_quote(symbol="AAPL", timeout=5)
        assert result["status"] == "ok", result
        assert result["data"]["price"] == 151.25
        # The socket MUST be closed - no leaked connection.
        assert FakeAsyncWebSocket.instances, "no socket created"
        assert all(ws.closed for ws in FakeAsyncWebSocket.instances)

    async def test_subscribes_to_the_symbol(self):
        svc = YahooFinanceService(yf=_StreamYF())
        await svc.live_quote(symbol="AAPL", timeout=5)
        assert any("AAPL" in ws.subscribed
                   for ws in FakeAsyncWebSocket.instances)

    async def test_listen_is_awaited_not_iterated(self):
        """Regression: listen() is a coroutine; async-for never awaits it."""
        svc = YahooFinanceService(yf=_StreamYF())
        await svc.live_quote(symbol="AAPL", timeout=5)
        assert any(ws.listen_calls >= 1
                   for ws in FakeAsyncWebSocket.instances), (
            "listen() was never called as a coroutine")

    async def test_never_claims_live_without_a_tick(self):
        class SilentWS(FakeAsyncWebSocket):
            async def listen(self, message_handler=None):
                self.listen_calls += 1
                await asyncio.sleep(0.01)

        class SilentYF(_StreamYF):
            AsyncWebSocket = SilentWS

        svc = YahooFinanceService(yf=SilentYF())
        result = await svc.live_quote(symbol="AAPL", timeout=1)
        assert result["status"] == "error"
        assert result["code"] in ("stream_unavailable", "network_error")
        assert result["freshness"] == "UNAVAILABLE"

    async def test_no_websocket_support_is_graceful(self):
        class NoWS(FakeYF):
            pass

        svc = YahooFinanceService(yf=NoWS())
        result = await svc.live_quote(symbol="AAPL", timeout=1)
        assert result["status"] == "error"
        assert result["code"] == "stream_unavailable"

    async def test_timeout_does_not_leak_socket(self):
        class HangWS(FakeAsyncWebSocket):
            async def listen(self, message_handler=None):
                self.listen_calls += 1
                await asyncio.sleep(30)

        class HangYF(_StreamYF):
            AsyncWebSocket = HangWS

        FakeAsyncWebSocket.instances = []
        svc = YahooFinanceService(yf=HangYF())
        result = await svc.live_quote(symbol="AAPL", timeout=1)
        assert result["status"] == "error"
        assert all(ws.closed for ws in FakeAsyncWebSocket.instances), (
            "socket leaked after timeout")


# -- history must not use the deprecated raise_errors kwarg -----------------

class TestHistoryNoDeprecatedKwarg:
    async def test_history_does_not_pass_raise_errors(self):
        seen: dict = {}

        class SpyTicker(FakeTicker):
            def history(self, **kwargs):
                seen.update(kwargs)
                return super().history(**kwargs)

        class SpyYF(FakeYF):
            def Ticker(self, symbol, **kw):
                return SpyTicker(symbol, self)

        svc = YahooFinanceService(yf=SpyYF())
        await svc.history(symbol="AAPL", period="1mo", interval="1d")
        assert "raise_errors" not in seen, (
            "raise_errors is deprecated in yfinance 1.7.0 and must not be passed")

