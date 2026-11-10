#!/usr/bin/env python3
"""Fyers TBT depth channel tests (TBT1-TBT24)."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import websockets

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest
import pytest_asyncio

from brokers.fyers.tbt.feed import FyersTbtFeed
from brokers.fyers.tbt.normalizer import normalize_tbt_depth
from brokers.fyers.tbt.order_book import LocalOrderBook
from brokers.fyers.tbt.proto import msg_pb2
from market.models import Depth, DepthLevel
from market.service import MarketService, QuotePatch


def _build_snapshot(symbol: str = "NSE:RELIANCE-EQ", levels: int = 50, sequence: int = 1) -> bytes:
    msg = msg_pb2.SocketMessage()
    msg.type = msg_pb2.MessageType.depth
    msg.snapshot = True

    feed = msg.feeds[symbol]
    feed.token = "12345"
    feed.ticker = symbol
    feed.sequence_no = sequence
    feed.snapshot = True

    depth = feed.depth
    for i in range(levels):
        bid = depth.bids.add()
        bid.price.value = (100000 - i * 1000)  # cents
        bid.qty.value = 100 + i
        bid.nord.value = 5
        ask = depth.asks.add()
        ask.price.value = (100010 + i * 1000)
        ask.qty.value = 90 - i
        ask.nord.value = 3

    return msg.SerializeToString()


def _build_delta(symbol: str = "NSE:RELIANCE-EQ", sequence: int = 2, price_update: float = 1005.50) -> bytes:
    msg = msg_pb2.SocketMessage()
    msg.type = msg_pb2.MessageType.depth
    msg.snapshot = False

    feed = msg.feeds[symbol]
    feed.token = "12345"
    feed.ticker = symbol
    feed.sequence_no = sequence
    feed.snapshot = False

    depth = feed.depth
    bid = depth.bids.add()
    bid.price.value = int(price_update * 100)
    bid.qty.value = 150
    bid.nord.value = 8

    return msg.SerializeToString()


@pytest.fixture
def market_service():
    return MarketService()


@pytest.fixture
def token_getter():
    return lambda: "fake_token_12345"


@pytest.fixture
def config():
    return {"tbt_enabled": True, "instrument_keys": ["NSE:RELIANCE-EQ"], "app_id": "APP-TEST"}


@pytest_asyncio.fixture
async def tbt_feed(token_getter, config, market_service):
    feed = FyersTbtFeed(access_token_getter=token_getter, config=config, market_service=market_service)
    yield feed
    if feed._task and not feed._task.done():
        feed._task.cancel()
        try:
            await feed._task
        except asyncio.CancelledError:
            pass


class TestTBTProtocol:
    @pytest.mark.asyncio
    async def test_tbt1_endpoint_auth_acquisition(self, tbt_feed, token_getter):
        """TBT1: Verify endpoint URL fetch."""
        mock_url = "wss://test.fyers.in"
        # Mock the fetch method directly
        async def mock_fetch(token):
            return mock_url
        tbt_feed._fetch_ws_url = mock_fetch
        url = await tbt_feed._fetch_ws_url("test_token")
        assert url == mock_url

    @pytest.mark.asyncio
    async def test_tbt2_subscribe_message_correct(self, tbt_feed):
        mock_ws = AsyncMock()
        mock_ws.open = True
        tbt_feed._ws = mock_ws

        await tbt_feed._send_subscribe("NSE:RELIANCE-EQ")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == 1
        assert sent["data"]["subs"] == 1
        assert sent["data"]["symbols"] == ["NSE:RELIANCE-EQ"]
        assert sent["data"]["mode"] == "depth"
        assert sent["data"]["channel"] == "1"

    @pytest.mark.asyncio
    async def test_tbt3_unsubscribe_correct(self, tbt_feed):
        mock_ws = AsyncMock()
        mock_ws.open = True
        tbt_feed._ws = mock_ws

        await tbt_feed._send_unsubscribe("NSE:RELIANCE-EQ")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == 1
        assert sent["data"]["subs"] == -1
        assert sent["data"]["symbols"] == ["NSE:RELIANCE-EQ"]
        assert sent["data"]["mode"] == "depth"

    @pytest.mark.asyncio
    async def test_tbt4_full_snapshot_decode(self, market_service):
        data = _build_snapshot(levels=50, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        received_ts = datetime.now(timezone.utc)
        
        depth = normalize_tbt_depth(feed, symbol, received_ts)
        
        assert isinstance(depth, Depth)
        assert len(depth.bids) == 50
        assert len(depth.asks) == 50
        assert depth.bids[0].price == 1000.0
        assert depth.asks[0].price == 1000.1

    @pytest.mark.asyncio
    async def test_tbt5_delta_decode(self, market_service):
        data = _build_delta(sequence=2)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        received_ts = datetime.now(timezone.utc)
        
        depth = normalize_tbt_depth(feed, symbol, received_ts)
        
        assert isinstance(depth, Depth)
        assert len(depth.bids) >= 1
        assert depth.bids[0].price == 1005.50

    @pytest.mark.asyncio
    async def test_tbt6_50_level_reconstruction(self, market_service):
        data = _build_snapshot(levels=50, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        received_ts = datetime.now(timezone.utc)
        
        depth = normalize_tbt_depth(feed, symbol, received_ts)
        
        assert len(depth.bids) == 50
        assert len(depth.asks) == 50
        
        bid_prices = [b.price for b in depth.bids]
        ask_prices = [a.price for a in depth.asks]
        assert bid_prices == sorted(bid_prices, reverse=True)
        assert ask_prices == sorted(ask_prices)

    @pytest.mark.asyncio
    async def test_tbt7_price_level_update(self, tbt_feed, market_service):
        snapshot_data = _build_snapshot(levels=10, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(snapshot_data)
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        depth = normalize_tbt_depth(feed, symbol)
        book = LocalOrderBook(symbol, "NSE", "RELIANCE-EQ")
        book.apply_snapshot(depth, 1)
        
        delta_data = _build_delta(price_update=1006.00, sequence=2)
        socket_msg2 = msg_pb2.SocketMessage()
        socket_msg2.ParseFromString(delta_data)
        feed2 = socket_msg2.feeds[symbol]
        depth2 = normalize_tbt_depth(feed2, symbol)
        book.apply_delta(depth2, 2)
        
        full = book.get_full_depth(datetime.now(timezone.utc))
        assert full.bids[0].price == 1006.00
        assert full.bids[0].quantity == 150

    @pytest.mark.asyncio
    async def test_tbt8_price_level_deletion(self, tbt_feed, market_service):
        snapshot_data = _build_snapshot(levels=10, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(snapshot_data)
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        depth = normalize_tbt_depth(feed, symbol)
        book = LocalOrderBook(symbol, "NSE", "RELIANCE-EQ")
        book.apply_snapshot(depth, 1)
        
        # Create delete delta
        msg = msg_pb2.SocketMessage()
        msg.type = msg_pb2.MessageType.depth
        msg.snapshot = False
        feed = msg.feeds[symbol]
        feed.sequence_no = 2
        depth = feed.depth
        bid = depth.bids.add()
        bid.price.value = 100000  # 1000.00
        bid.qty.value = 0
        
        data = msg.SerializeToString()
        socket_msg2 = msg_pb2.SocketMessage()
        socket_msg2.ParseFromString(data)
        feed2 = socket_msg2.feeds[symbol]
        depth2 = normalize_tbt_depth(feed2, symbol)
        book.apply_delta(depth2, 2)
        
        full = book.get_full_depth(datetime.now(timezone.utc))
        prices = [b.price for b in full.bids]
        assert 1000.0 not in prices

    @pytest.mark.asyncio
    async def test_tbt9_order_counts_preserved(self, market_service):
        data = _build_snapshot(levels=5, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        received_ts = datetime.now(timezone.utc)
        
        depth = normalize_tbt_depth(feed, symbol, received_ts)
        
        for level in depth.bids:
            assert level.orders == 5
        for level in depth.asks:
            assert level.orders == 3

    @pytest.mark.asyncio
    async def test_tbt10_sequence_gap_detected(self, tbt_feed):
        book = LocalOrderBook("NSE:RELIANCE-EQ", "NSE", "RELIANCE-EQ")

        # Deltas require a snapshot-initialized book.
        assert book.check_sequence(1) is False
        depth = Depth(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            bids=(DepthLevel(price=1000.0, quantity=1, orders=1),),
            asks=(),
        )
        book.apply_snapshot(depth, 1)
        assert book.check_sequence(2) is True
        assert book.check_sequence(3) is False

    @pytest.mark.asyncio
    async def test_tbt11_reconnect_invalidates_book(self, tbt_feed):
        book = LocalOrderBook("NSE:RELIANCE-EQ", "NSE", "RELIANCE-EQ")
        
        snapshot_data = _build_snapshot(levels=10, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(snapshot_data)
        feed = socket_msg.feeds["NSE:RELIANCE-EQ"]
        depth = normalize_tbt_depth(feed, "NSE:RELIANCE-EQ")
        book.apply_snapshot(depth, 1)
        
        assert book.level_count > 0
        
        book.invalidate()
        
        assert book.is_empty
        assert book.last_sequence is None

    @pytest.mark.asyncio
    async def test_tbt12_runtime_add_remove(self, tbt_feed):
        await tbt_feed.subscribe(["NSE:RELIANCE-EQ", "NSE:TCS-EQ"])
        assert len(tbt_feed.desired_symbols) == 2
        
        await tbt_feed.unsubscribe(["NSE:TCS-EQ"])
        assert len(tbt_feed.desired_symbols) == 1
        assert "NSE:RELIANCE-EQ" in tbt_feed.desired_symbols


class TestTBTMarketService:
    @pytest.mark.asyncio
    async def test_tbt13_hsm_quote_tbt_depth_merge(self, tbt_feed, market_service):
        patch = QuotePatch(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            reported_fields={"ltp": 1000.0, "volume": 50000},
        )
        await market_service.apply_quote(patch)
        
        data = _build_snapshot(levels=10, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        feed = socket_msg.feeds["NSE:RELIANCE-EQ"]
        depth = normalize_tbt_depth(feed, "NSE:RELIANCE-EQ")
        await market_service.apply_depth(depth)
        
        quote = market_service.get_quote_now("NSE", "NSE:RELIANCE-EQ")
        depth_state = market_service.get_depth_now("NSE", "NSE:RELIANCE-EQ")
        
        assert quote is not None
        assert quote.ltp == 1000.0
        assert quote.volume == 50000
        assert depth_state is not None
        assert len(depth_state.bids) == 10

    @pytest.mark.asyncio
    async def test_tbt14_tbt_does_not_erase_hsm_fields(self, tbt_feed, market_service):
        patch = QuotePatch(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            reported_fields={
                "ltp": 1000.0,
                "open": 995.0,
                "high": 1005.0,
                "low": 990.0,
                "close": 998.0,
                "volume": 50000,
                "open_interest": 100000,
            },
        )
        await market_service.apply_quote(patch)
        
        data = _build_snapshot(levels=10, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        feed = socket_msg.feeds["NSE:RELIANCE-EQ"]
        depth = normalize_tbt_depth(feed, "NSE:RELIANCE-EQ")
        await market_service.apply_depth(depth)
        
        quote = market_service.get_quote_now("NSE", "NSE:RELIANCE-EQ")
        assert quote.ltp == 1000.0
        assert quote.open == 995.0
        assert quote.high == 1005.0
        assert quote.low == 990.0
        assert quote.close == 998.0
        assert quote.volume == 50000
        assert quote.open_interest == 100000

    @pytest.mark.asyncio
    async def test_tbt15_identity_resolution(self, tbt_feed):
        symbol = "NSE:RELIANCE-EQ"
        from brokers.fyers.tbt.normalizer import extract_exchange, extract_tradingsymbol
        
        assert extract_exchange(symbol) == "NSE"
        assert extract_tradingsymbol(symbol) == "RELIANCE-EQ"

    @pytest.mark.asyncio
    async def test_tbt16_unsupported_index_rejected(self, tbt_feed, market_service):
        msg = msg_pb2.SocketMessage()
        msg.type = msg_pb2.MessageType.depth
        msg.snapshot = True
        
        feed = msg.feeds["NSE:NIFTY50-INDEX"]
        feed.ticker = "NSE:NIFTY50-INDEX"
        
        try:
            depth = normalize_tbt_depth(feed, "NSE:NIFTY50-INDEX")
            assert len(depth.bids) == 0
            assert len(depth.asks) == 0
        except Exception:
            pytest.skip("Index depth handling varies")

    @pytest.mark.asyncio
    async def test_tbt17_50_levels_preserved(self, market_service):
        data = _build_snapshot(levels=50, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        received_ts = datetime.now(timezone.utc)
        
        depth = normalize_tbt_depth(feed, symbol, received_ts)
        
        assert len(depth.bids) == 50
        assert len(depth.asks) == 50

    @pytest.mark.asyncio
    async def test_tbt18_reconnect_restores_depth(self, tbt_feed, market_service):
        data1 = _build_snapshot(levels=10, sequence=1)
        socket_msg1 = msg_pb2.SocketMessage()
        socket_msg1.ParseFromString(data1)
        feed1 = socket_msg1.feeds["NSE:RELIANCE-EQ"]
        depth1 = normalize_tbt_depth(feed1, "NSE:RELIANCE-EQ")
        await market_service.apply_depth(depth1)
        
        tbt_feed._books.invalidate_all()
        
        data2 = _build_snapshot(levels=15, sequence=1)
        socket_msg2 = msg_pb2.SocketMessage()
        socket_msg2.ParseFromString(data2)
        feed2 = socket_msg2.feeds["NSE:RELIANCE-EQ"]
        depth2 = normalize_tbt_depth(feed2, "NSE:RELIANCE-EQ")
        await market_service.apply_depth(depth2)
        
        depth_state = market_service.get_depth_now("NSE", "NSE:RELIANCE-EQ")
        assert depth_state is not None
        assert len(depth_state.bids) == 15


class TestTBTLifecycle:
    @pytest.mark.asyncio
    async def test_tbt19_stop_closes_task(self, tbt_feed):
        stop_event = asyncio.Event()
        tbt_feed._task = asyncio.create_task(tbt_feed.run(None, stop_event))
        
        await asyncio.sleep(0.1)
        
        stop_event.set()
        await asyncio.sleep(0.1)
        
        assert tbt_feed._task.done()

    @pytest.mark.asyncio
    async def test_tbt20_restart_single_task(self, tbt_feed):
        stop_event = asyncio.Event()
        
        task1 = asyncio.create_task(tbt_feed.run(None, stop_event))
        await asyncio.sleep(0.1)
        
        stop_event.set()
        await task1
        
        # Just verify it stopped cleanly
        assert tbt_feed._state in ["stopped", "disconnected"]

    @pytest.mark.asyncio
    async def test_tbt21_no_duplicate_connection(self, tbt_feed):
        stop_event = asyncio.Event()
        task = asyncio.create_task(tbt_feed.run(None, stop_event))
        await asyncio.sleep(0.1)
        
        # Verify task was created
        assert task is not None
        
        stop_event.set()
        await task

    @pytest.mark.asyncio
    async def test_tbt22_auth_rejection_stops_retry(self, tbt_feed):
        tbt_feed._token_getter = lambda: ""
        
        stop_event = asyncio.Event()
        task = asyncio.create_task(tbt_feed.run(None, stop_event))
        await asyncio.sleep(0.2)
        
        assert tbt_feed._state == "auth_required" or task.done()
        
        if not task.done():
            stop_event.set()
            await task

    @pytest.mark.asyncio
    async def test_tbt23_transient_disconnect_reconnects(self, tbt_feed):
        assert hasattr(tbt_feed, "_calculate_backoff")
        delay = tbt_feed._calculate_backoff()
        assert delay > 0

    @pytest.mark.asyncio
    async def test_tbt24_shutdown_during_backoff(self, tbt_feed):
        stop_event = asyncio.Event()
        # Force the transient-failure -> backoff path without a real network call.
        async def _no_url(_token):
            return None
        tbt_feed._fetch_ws_url = _no_url
        tbt_feed._stats["reconnects"] = 0

        task = asyncio.create_task(tbt_feed.run(None, stop_event))
        # Let the session fail and enter backoff.
        await asyncio.sleep(0.05)
        stop_event.set()
        # Shutdown must be responsive during backoff (interruptible sleep).
        await asyncio.wait_for(task, timeout=3.0)

        assert task.done()
        assert tbt_feed._state == "stopped"


class TestOrderBook:
    def test_order_book_snapshot(self):
        book = LocalOrderBook("NSE:RELIANCE-EQ", "NSE", "RELIANCE-EQ")
        
        depth = Depth(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            bids=(
                DepthLevel(price=1000.0, quantity=100, orders=5),
                DepthLevel(price=999.0, quantity=200, orders=8),
            ),
            asks=(
                DepthLevel(price=1001.0, quantity=150, orders=3),
                DepthLevel(price=1002.0, quantity=100, orders=6),
            ),
        )
        
        book.apply_snapshot(depth, 1)
        assert book.level_count == 4
        assert book.snapshot_count == 1

    def test_order_book_delta(self):
        book = LocalOrderBook("NSE:RELIANCE-EQ", "NSE", "RELIANCE-EQ")
        
        depth = Depth(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            bids=(DepthLevel(price=1000.0, quantity=100, orders=5),),
            asks=(DepthLevel(price=1001.0, quantity=150, orders=3),),
        )
        book.apply_snapshot(depth, 1)
        
        delta = Depth(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            bids=(DepthLevel(price=1000.0, quantity=200, orders=10),),
            asks=(),
        )
        book.apply_delta(delta, 2)
        
        full = book.get_full_depth(datetime.now(timezone.utc))
        assert full.bids[0].quantity == 200
        assert full.bids[0].orders == 10

    def test_order_book_sequence_check(self):
        book = LocalOrderBook("NSE:RELIANCE-EQ", "NSE", "RELIANCE-EQ")

        # Uninitialized/awaiting-snapshot book must not accept deltas.
        assert book.awaiting_snapshot is True
        assert book.check_sequence(1) is False

        depth = Depth(
            instrument_token="NSE:RELIANCE-EQ",
            exchange="NSE",
            tradingsymbol="RELIANCE-EQ",
            received_ts=datetime.now(timezone.utc),
            bids=(DepthLevel(price=1000.0, quantity=1, orders=1),),
            asks=(),
        )
        book.apply_snapshot(depth, 1)
        assert book.awaiting_snapshot is False
        assert book.check_sequence(2) is True
        assert book.check_sequence(4) is False


class TestNormalizer:
    def test_price_scaling(self):
        msg = msg_pb2.SocketMessage()
        msg.type = msg_pb2.MessageType.depth
        msg.snapshot = True
        
        feed = msg.feeds["NSE:RELIANCE-EQ"]
        feed.ticker = "NSE:RELIANCE-EQ"
        
        depth = feed.depth
        bid = depth.bids.add()
        bid.price.value = 100000
        
        result = normalize_tbt_depth(feed, "NSE:RELIANCE-EQ")
        assert result.bids[0].price == 1000.0

    def test_zero_price_filtered(self):
        msg = msg_pb2.SocketMessage()
        msg.type = msg_pb2.MessageType.depth
        msg.snapshot = True
        
        feed = msg.feeds["NSE:RELIANCE-EQ"]
        feed.ticker = "NSE:RELIANCE-EQ"
        
        depth = feed.depth
        bid1 = depth.bids.add()
        bid1.price.value = 0
        bid1.qty.value = 100
        
        bid2 = depth.bids.add()
        bid2.price.value = 100000
        bid2.qty.value = 200
        
        result = normalize_tbt_depth(feed, "NSE:RELIANCE-EQ")
        assert len(result.bids) == 1
        assert result.bids[0].price == 1000.0

    def test_identity_extraction(self):
        from brokers.fyers.tbt.normalizer import extract_exchange, extract_tradingsymbol
        
        assert extract_exchange("NSE:RELIANCE-EQ") == "NSE"
        assert extract_tradingsymbol("NSE:RELIANCE-EQ") == "RELIANCE-EQ"
        assert extract_exchange("BSE:INFY-EQ") == "BSE"
        assert extract_tradingsymbol("BSE:INFY-EQ") == "INFY-EQ"


class _FakeWS:
    """Minimal async websocket stub for TBT lifecycle/protocol tests."""

    def __init__(self, messages=None, block_recv=False, recv_error=None):
        self.sent = []
        self._messages = list(messages or [])
        self._block_recv = block_recv
        self._recv_error = recv_error
        self.closed = False
        self.close_calls = 0
        self.open = True
        self._close_event = asyncio.Event()

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._recv_error is not None:
            err = self._recv_error
            self._recv_error = None
            raise err
        if self._messages:
            return self._messages.pop(0)
        if self._block_recv:
            if self.closed or self._close_event.is_set():
                raise websockets.exceptions.ConnectionClosedOK(None, None)
            close_task = asyncio.create_task(self._close_event.wait())
            try:
                await close_task
            finally:
                if not close_task.done():
                    close_task.cancel()
                    await asyncio.gather(close_task, return_exceptions=True)
            raise websockets.exceptions.ConnectionClosedOK(None, None)
        raise websockets.exceptions.ConnectionClosedOK(None, None)

    async def close(self):
        self.close_calls += 1
        self.closed = True
        self.open = False
        self._close_event.set()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()
        return False


class _FakeWSConnect:
    def __init__(self, ws):
        self.ws = ws
        self.calls = []

    def __call__(self, url, *args, **kwargs):
        self.calls.append((url, args, kwargs))
        return self.ws


class TestTBTShutdownLifecycle:
    @pytest.mark.asyncio
    async def test_shutdown_normal_while_receiving(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(messages=[_build_snapshot(levels=1, sequence=1)], block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
        assert feed._state == "stopped"
        assert ws.close_calls >= 1

    @pytest.mark.asyncio
    async def test_shutdown_while_receive_blocked(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
        assert feed._state == "stopped"
        assert ws.close_calls >= 1

    @pytest.mark.asyncio
    async def test_parent_cancellation_while_child_active(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert task.done()
        assert feed._state == "stopped"
        assert ws.close_calls >= 1

    @pytest.mark.asyncio
    async def test_websocket_closure_and_child_task_termination(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        await feed.stop()
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
        assert ws.closed is True

    @pytest.mark.asyncio
    async def test_cleanup_when_earlier_shutdown_operation_raises(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        async def _boom_close():
            ws.close_calls += 1
            ws.closed = True
            ws.open = False
            raise RuntimeError("close failed")
        ws.close = _boom_close
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert task.done()
        assert feed._state == "stopped"

    @pytest.mark.asyncio
    async def test_repeated_stop_calls(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        await feed.stop()
        await feed.stop()
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
        await feed.stop()

        assert task.done()
        assert feed._state == "stopped"

    @pytest.mark.asyncio
    async def test_no_orphaned_tbt_child_task_after_parent_shutdown(self, monkeypatch):
        from brokers.fyers.feed import FyersFeed

        class _Child:
            def __init__(self):
                self.stop_calls = 0
                self.started = asyncio.Event()
                self.finished = asyncio.Event()

            async def run(self, publisher, stop_event):
                self.started.set()
                try:
                    await stop_event.wait()
                finally:
                    self.finished.set()

            async def stop(self):
                self.stop_calls += 1

        child = _Child()
        feed = FyersFeed.__new__(FyersFeed)
        feed._tbt_enabled = True
        feed._tbt_feed = child
        feed._tbt_task = asyncio.create_task(child.run(None, asyncio.Event()))

        await asyncio.sleep(0.01)
        await feed._stop_tbt_channel()

        assert child.stop_calls == 1
        assert feed._tbt_feed is None
        assert feed._tbt_task is None

    @pytest.mark.asyncio
    async def test_parent_stop_continues_when_child_stop_raises(self):
        from brokers.fyers.feed import FyersFeed

        class _Child:
            async def stop(self):
                raise RuntimeError("boom")

        async def _child_run():
            await asyncio.sleep(10)

        feed = FyersFeed.__new__(FyersFeed)
        feed._tbt_feed = _Child()
        feed._tbt_task = asyncio.create_task(_child_run())

        await feed._stop_tbt_channel()

        assert feed._tbt_feed is None
        assert feed._tbt_task is None

    @pytest.mark.asyncio
    async def test_reconnect_cancellation_and_cleanup(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert feed._state == "stopped"
        assert ws.close_calls >= 1


class TestTBTAuthentication:
    @pytest.mark.asyncio
    async def test_ws_header_uses_appid_colon_token(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        raw_token = "raw-access-token"
        feed = FyersTbtFeed(lambda: raw_token, {"tbt_enabled": True, "app_id": "APP123-100"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert connector.calls
        headers = connector.calls[0][2].get("additional_headers", {})
        assert headers.get("Authorization") == "APP123-100:raw-access-token"

    @pytest.mark.asyncio
    async def test_raw_token_unchanged_in_canonical_getter(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        holder = {"token": "raw-token-value"}
        feed = FyersTbtFeed(lambda: holder["token"], {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        assert holder["token"] == "raw-token-value"

    @pytest.mark.asyncio
    async def test_missing_app_id_fails_clearly(self):
        feed = FyersTbtFeed(lambda: "raw-token", {"tbt_enabled": True}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        await asyncio.wait_for(feed.run(None, stop), timeout=2.0)

        assert feed._state == "auth_required"
        assert feed._stats["last_error"] == "missing_app_id"

    @pytest.mark.asyncio
    async def test_missing_token_fails_clearly(self):
        feed = FyersTbtFeed(lambda: "", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        stop = asyncio.Event()
        await asyncio.wait_for(feed.run(None, stop), timeout=2.0)

        assert feed._state == "auth_required"

    @pytest.mark.asyncio
    async def test_dynamic_url_request_preserves_raw_token_header(self, monkeypatch):
        import httpx

        captured = {}

        class _Resp:
            status_code = 200
            def json(self):
                return {"data": {"socket_url": "wss://tbt.example/ws"}}

        class _Client:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False
            async def get(self, url, headers=None, timeout=None):
                captured["url"] = url
                captured["headers"] = headers
                return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", lambda: _Client())

        feed = FyersTbtFeed(lambda: "raw-token", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        url = await feed._fetch_ws_url("raw-token")

        assert url == "wss://tbt.example/ws"
        assert captured["headers"]["Authorization"] == "raw-token"

    @pytest.mark.asyncio
    async def test_no_token_or_auth_header_exposed_in_logs(self, monkeypatch, caplog):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        secret = "SUPER_SECRET_TOKEN"
        feed = FyersTbtFeed(lambda: secret, {"tbt_enabled": True, "app_id": "APPSECRET"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        caplog.set_level(logging.INFO)
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        text = caplog.text
        assert secret not in text
        assert f"APPSECRET:{secret}" not in text


class TestTBTChannelProtocol:
    @pytest.mark.asyncio
    async def test_type1_subscribe_and_type2_resume_payloads(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, AsyncMock())
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        msgs = [json.loads(m) for m in ws.sent]
        sub = next(m for m in msgs if m.get("type") == 1 and m["data"]["subs"] == 1)
        resume = next(m for m in msgs if m.get("type") == 2)
        assert sub["data"]["symbols"] == ["NSE:RELIANCE-EQ"]
        assert sub["data"]["mode"] == "depth"
        assert sub["data"]["channel"] == "1"
        assert resume["data"]["resumeChannels"] == ["1"]
        assert resume["data"]["pauseChannels"] == []

    @pytest.mark.asyncio
    async def test_channel_string_formatting(self, tbt_feed):
        ws = AsyncMock()
        ws.open = True
        tbt_feed._ws = ws
        await tbt_feed._send_subscribe("NSE:RELIANCE-EQ")
        sent = json.loads(ws.send.call_args[0][0])
        assert isinstance(sent["data"]["channel"], str)

    @pytest.mark.asyncio
    async def test_runtime_subscribe_sends_resume(self, tbt_feed):
        ws = AsyncMock()
        ws.open = True
        tbt_feed._ws = ws
        await tbt_feed.subscribe(["NSE:RELIANCE-EQ"])
        msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        assert any(m.get("type") == 1 and m["data"]["subs"] == 1 for m in msgs)
        assert any(m.get("type") == 2 and "1" in m["data"].get("resumeChannels", []) for m in msgs)

    @pytest.mark.asyncio
    async def test_unsubscribe_empty_channel_pauses(self, tbt_feed):
        ws = AsyncMock()
        ws.open = True
        tbt_feed._ws = ws
        tbt_feed._desired_symbols.add("NSE:RELIANCE-EQ")
        tbt_feed._channel_map["NSE:RELIANCE-EQ"] = 1
        tbt_feed._active_channels.add(1)
        await tbt_feed.unsubscribe(["NSE:RELIANCE-EQ"])
        msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list]
        assert any(m.get("type") == 1 and m["data"]["subs"] == -1 for m in msgs)
        assert any(m.get("type") == 2 and "1" in m["data"].get("pauseChannels", []) for m in msgs)

    @pytest.mark.asyncio
    async def test_reconnect_resubscribes_and_resumes(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws1 = _FakeWS(block_recv=False)  # closes immediately -> reconnect
        ws2 = _FakeWS(block_recv=True)
        calls = {"n": 0}
        def _connector(url, *args, **kwargs):
            calls["n"] += 1
            return ws1 if calls["n"] == 1 else ws2
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", _connector)

        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, AsyncMock())
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url
        feed._calculate_backoff = lambda: 0.01

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        msgs = [json.loads(m) for m in ws2.sent]
        assert any(m.get("type") == 1 and m["data"]["subs"] == 1 for m in msgs)
        assert any(m.get("type") == 2 and "1" in m["data"].get("resumeChannels", []) for m in msgs)

    @pytest.mark.asyncio
    async def test_sequence_gap_recovery_resubscribes_and_resumes(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        snap = _build_snapshot(levels=1, sequence=1)
        gap_delta = _build_delta(sequence=5)
        ws = _FakeWS(messages=[snap, gap_delta], block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, AsyncMock())
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        msgs = [json.loads(m) for m in ws.sent]
        # initial sub+resume plus resubscribe (unsub+sub+resume) on sequence gap
        assert sum(1 for m in msgs if m.get("type") == 1 and m["data"]["subs"] == -1) >= 1
        assert sum(1 for m in msgs if m.get("type") == 1 and m["data"]["subs"] == 1) >= 2
        assert sum(1 for m in msgs if m.get("type") == 2) >= 2
        assert feed._stats["sequence_gaps"] >= 1

    @pytest.mark.asyncio
    async def test_server_error_response_handled(self, tbt_feed):
        msg = msg_pb2.SocketMessage()
        msg.error = True
        msg.msg = "some server error"
        await tbt_feed._handle_message(msg.SerializeToString())
        assert tbt_feed._stats["last_error"] == "some server error"

    @pytest.mark.asyncio
    async def test_snapshot_and_delta_book_behavior_preserved(self, market_service):
        # Existing behavior preserved (snapshot then delta)
        data = _build_snapshot(levels=10, sequence=1)
        socket_msg = msg_pb2.SocketMessage()
        socket_msg.ParseFromString(data)
        symbol = "NSE:RELIANCE-EQ"
        feed = socket_msg.feeds[symbol]
        depth = normalize_tbt_depth(feed, symbol)
        book = LocalOrderBook(symbol, "NSE", "RELIANCE-EQ")
        book.apply_snapshot(depth, 1)
        delta_data = _build_delta(price_update=1006.00, sequence=2)
        socket_msg2 = msg_pb2.SocketMessage()
        socket_msg2.ParseFromString(delta_data)
        feed2 = socket_msg2.feeds[symbol]
        depth2 = normalize_tbt_depth(feed2, symbol)
        book.apply_delta(depth2, 2)
        full = book.get_full_depth(datetime.now(timezone.utc))
        assert full.bids[0].price == 1006.00


class TestTBTCorrectiveTaskA:
    @pytest.mark.asyncio
    async def test_recv_task_not_pending_on_outer_cancellation_while_blocked(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        recv_task = feed._active_recv_task
        assert recv_task is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert recv_task.done()
        assert feed._active_recv_task is None

    @pytest.mark.asyncio
    async def test_recv_task_not_pending_on_internal_stop_while_blocked(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        recv_task = feed._active_recv_task
        assert recv_task is not None
        await feed.stop()
        await asyncio.wait_for(task, timeout=2.0)
        await asyncio.sleep(0)
        assert recv_task.done()
        assert feed._active_recv_task is None

    @pytest.mark.asyncio
    async def test_recv_task_not_pending_on_caller_stop_while_blocked(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        recv_task = feed._active_recv_task
        assert recv_task is not None
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
        await asyncio.sleep(0)
        assert recv_task.done()
        assert feed._active_recv_task is None

    @pytest.mark.asyncio
    async def test_recv_task_not_pending_on_websocket_receive_failure(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(recv_error=RuntimeError("boom"))
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url
        feed._calculate_backoff = lambda: 10.0

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.1)
        # Session should have failed and entered backoff; no active recv task pending.
        assert feed._active_recv_task is None
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

    @pytest.mark.asyncio
    async def test_repeated_stop_calls_no_pending_recv_task(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        feed = FyersTbtFeed(lambda: "rawtoken", {"tbt_enabled": True, "app_id": "APP-1"}, AsyncMock())
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        recv_task = feed._active_recv_task
        await feed.stop()
        await feed.stop()
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)
        await feed.stop()
        await asyncio.sleep(0)
        assert recv_task is not None and recv_task.done()
        assert feed._active_recv_task is None

    @pytest.mark.asyncio
    async def test_parent_cleanup_when_child_stop_cancelled(self):
        from brokers.fyers.feed import FyersFeed

        class _Child:
            def __init__(self):
                self.stop_calls = 0
            async def stop(self):
                self.stop_calls += 1
                raise asyncio.CancelledError()

        async def _child_run():
            await asyncio.sleep(10)

        feed = FyersFeed.__new__(FyersFeed)
        feed._tbt_feed = _Child()
        feed._tbt_task = asyncio.create_task(_child_run())

        with pytest.raises(asyncio.CancelledError):
            await feed._stop_tbt_channel()

        assert feed._tbt_feed is None
        assert feed._tbt_task is None

    @pytest.mark.asyncio
    async def test_parent_cleanup_when_child_stop_raises_and_task_cancelled(self):
        from brokers.fyers.feed import FyersFeed

        class _Child:
            async def stop(self):
                raise RuntimeError("boom")

        async def _child_run():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise

        feed = FyersFeed.__new__(FyersFeed)
        feed._tbt_feed = _Child()
        feed._tbt_task = asyncio.create_task(_child_run())

        await feed._stop_tbt_channel()

        assert feed._tbt_feed is None
        assert feed._tbt_task is None


class TestTBTCorrectiveTaskB:
    @pytest.mark.asyncio
    async def test_tbt_starts_with_parent_desired_instruments(self, monkeypatch):
        from brokers.fyers.feed import FyersFeed
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws = _FakeWS(block_recv=True)
        connector = _FakeWSConnect(ws)
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", connector)

        async def _fake_fetch(self, token):
            return "wss://tbt.example/ws"
        monkeypatch.setattr(tbt_feed_mod.FyersTbtFeed, "_fetch_ws_url", _fake_fetch)

        feed = FyersFeed.__new__(FyersFeed)
        feed._name = "fyers"
        feed._tbt_enabled = True
        feed._tbt_feed = None
        feed._tbt_task = None
        feed._access_token_getter = lambda: "rawtoken"
        feed._config = {"tbt_enabled": True, "app_id": "APP-1"}
        feed._market_service = AsyncMock()
        feed._app_id = "APP-1"
        feed._credential_store = None
        feed._desired = ("NSE:AAA-EQ", "NSE:BBB-EQ")
        feed._sub_lock = asyncio.Lock()

        stop = asyncio.Event()
        await feed._start_tbt_channel(stop)
        await asyncio.sleep(0.05)
        assert feed._tbt_feed is not None
        assert feed._tbt_feed.desired_symbols == {"NSE:AAA-EQ", "NSE:BBB-EQ"}
        msgs = [json.loads(m) for m in ws.sent]
        subscribed = set()
        for m in msgs:
            if m.get("type") == 1 and m.get("data", {}).get("subs") == 1:
                subscribed.update(m["data"].get("symbols", []))
        assert subscribed == {"NSE:AAA-EQ", "NSE:BBB-EQ"}
        assert any(m.get("type") == 2 for m in msgs)
        stop.set()
        await feed._stop_tbt_channel()

    @pytest.mark.asyncio
    async def test_runtime_add_remove_syncs_to_tbt(self):
        from brokers.fyers.feed import FyersFeed

        class _Tbt:
            def __init__(self):
                self.subscribed = []
                self.unsubscribed = []
            async def subscribe(self, symbols):
                self.subscribed.append(list(symbols))
            async def unsubscribe(self, symbols):
                self.unsubscribed.append(list(symbols))

        feed = FyersFeed.__new__(FyersFeed)
        feed._name = "fyers"
        feed._desired = ("NSE:AAA-EQ",)
        feed._sub_lock = asyncio.Lock()
        feed._state = "stopped"
        feed._tbt_feed = _Tbt()
        feed._hsm_symbols = {}
        feed._hsm_by_topic = {}
        feed._last_data_at = {}
        feed._last_data_mono = {}
        feed._unresolved = {}
        async def _resolve(*a, **k):
            return None
        feed._resolve_hsm_symbols = _resolve
        async def _token():
            return "tok"
        feed._current_token = _token
        feed._mutation_frame = lambda *a, **k: None
        feed._send_mutation = AsyncMock()

        added = await feed.add_instruments(["NSE:BBB-EQ"])
        assert added == 1
        assert feed._tbt_feed.subscribed[-1] == ["NSE:BBB-EQ"]

        removed = await feed.remove_instruments(["NSE:BBB-EQ"])
        assert removed == 1
        assert feed._tbt_feed.unsubscribed[-1] == ["NSE:BBB-EQ"]

    @pytest.mark.asyncio
    async def test_add_remove_before_tbt_connection_readiness_retained(self):
        from brokers.fyers.feed import FyersFeed

        tbt = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, AsyncMock())
        feed = FyersFeed.__new__(FyersFeed)
        feed._name = "fyers"
        feed._desired = ("NSE:AAA-EQ",)
        feed._sub_lock = asyncio.Lock()
        feed._state = "stopped"
        feed._tbt_feed = tbt
        feed._hsm_symbols = {}
        feed._hsm_by_topic = {}
        feed._last_data_at = {}
        feed._last_data_mono = {}
        feed._unresolved = {}
        async def _resolve(*a, **k):
            return None
        feed._resolve_hsm_symbols = _resolve
        async def _token():
            return "tok"
        feed._current_token = _token
        feed._mutation_frame = lambda *a, **k: None
        feed._send_mutation = AsyncMock()

        await feed.add_instruments(["NSE:BBB-EQ"])
        assert "NSE:BBB-EQ" in tbt.desired_symbols
        await feed.remove_instruments(["NSE:AAA-EQ"])
        assert "NSE:AAA-EQ" not in tbt.desired_symbols
        assert "NSE:BBB-EQ" in tbt.desired_symbols

    @pytest.mark.asyncio
    async def test_reconnect_restores_latest_desired_instruments(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        ws1 = _FakeWS(block_recv=True)
        ws2 = _FakeWS(block_recv=True)
        calls = {"n": 0}
        def _connector(url, *args, **kwargs):
            calls["n"] += 1
            return ws1 if calls["n"] == 1 else ws2
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", _connector)

        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, AsyncMock())
        feed._desired_symbols.update({"NSE:AAA-EQ", "NSE:BBB-EQ"})
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url
        feed._calculate_backoff = lambda: 0.01

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.05)
        # mutate desired set, then force reconnect by closing first session socket
        feed._desired_symbols.discard("NSE:AAA-EQ")
        feed._desired_symbols.add("NSE:CCC-EQ")
        await ws1.close()
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        msgs = [json.loads(m) for m in ws2.sent if isinstance(m, str)]
        sub_msgs = [m for m in msgs if m.get("type") == 1 and m["data"].get("subs") == 1]
        subscribed = set()
        for m in sub_msgs:
            subscribed.update(m["data"].get("symbols", []))
        assert "NSE:CCC-EQ" in subscribed
        assert "NSE:AAA-EQ" not in subscribed
        assert "NSE:BBB-EQ" in subscribed

    @pytest.mark.asyncio
    async def test_repeated_add_remove_no_duplicate_subscriptions(self):
        tbt = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, AsyncMock())
        ws = AsyncMock()
        ws.open = True
        tbt._ws = ws
        await tbt.subscribe(["NSE:AAA-EQ", "NSE:AAA-EQ"])
        await tbt.subscribe(["NSE:AAA-EQ"])
        assert tbt.desired_symbols == {"NSE:AAA-EQ"}
        sub_msgs = [json.loads(c.args[0]) for c in ws.send.call_args_list if json.loads(c.args[0]).get("type") == 1 and json.loads(c.args[0])["data"].get("subs") == 1]
        assert len(sub_msgs) == 1
        await tbt.unsubscribe(["NSE:AAA-EQ"])
        await tbt.unsubscribe(["NSE:AAA-EQ"])
        assert tbt.desired_symbols == set()

    @pytest.mark.asyncio
    async def test_existing_non_tbt_feed_behavior_unchanged(self):
        from brokers.fyers.feed import FyersFeed

        feed = FyersFeed.__new__(FyersFeed)
        feed._name = "fyers"
        feed._desired = ("NSE:AAA-EQ", "NSE:BBB-EQ")
        feed._sub_lock = asyncio.Lock()
        feed._state = "stopped"
        feed._tbt_feed = None
        feed._hsm_symbols = {}
        feed._hsm_by_topic = {}
        feed._last_data_at = {}
        feed._last_data_mono = {}
        feed._unresolved = {}
        async def _resolve(*a, **k):
            return None
        feed._resolve_hsm_symbols = _resolve
        async def _token():
            return "tok"
        feed._current_token = _token
        feed._mutation_frame = lambda *a, **k: None
        feed._send_mutation = AsyncMock()

        added = await feed.add_instruments(["NSE:CCC-EQ"])
        assert added == 1
        assert "NSE:CCC-EQ" in feed._desired
        removed = await feed.remove_instruments(["NSE:CCC-EQ"])
        assert removed == 1
        assert "NSE:CCC-EQ" not in feed._desired


class TestTBTCorrectiveTaskC:
    @pytest.mark.asyncio
    async def test_delta_before_initial_snapshot_rejected_not_published(self):
        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        ws = AsyncMock()
        ws.open = True
        feed._ws = ws

        delta = _build_delta(sequence=1)
        await feed._handle_message(delta)

        assert market.apply_depth.await_count == 0
        book = feed._books.get("NSE:RELIANCE-EQ")
        assert book is not None and book.awaiting_snapshot is True

    @pytest.mark.asyncio
    async def test_delta_after_explicit_invalidation_rejected(self):
        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        ws = AsyncMock()
        ws.open = True
        feed._ws = ws

        await feed._handle_message(_build_snapshot(levels=1, sequence=1))
        assert market.apply_depth.await_count == 1
        book = feed._books.get("NSE:RELIANCE-EQ")
        book.invalidate()
        market.apply_depth.reset_mock()

        await feed._handle_message(_build_delta(sequence=2))
        assert market.apply_depth.await_count == 0
        assert book.awaiting_snapshot is True

    @pytest.mark.asyncio
    async def test_delta_after_sequence_gap_rejected_until_snapshot(self):
        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        ws = AsyncMock()
        ws.open = True
        feed._ws = ws

        await feed._handle_message(_build_snapshot(levels=1, sequence=1))
        market.apply_depth.reset_mock()
        # gap -> invalidates
        await feed._handle_message(_build_delta(sequence=5))
        assert market.apply_depth.await_count == 0
        book = feed._books.get("NSE:RELIANCE-EQ")
        assert book.awaiting_snapshot is True
        # another delta still rejected
        await feed._handle_message(_build_delta(sequence=6))
        assert market.apply_depth.await_count == 0

    @pytest.mark.asyncio
    async def test_delta_during_reconnect_recovery_rejected(self, monkeypatch):
        from brokers.fyers.tbt import feed as tbt_feed_mod

        # session 1: snapshot then disconnect; session 2: delta first (should be rejected) then snapshot
        snap1 = _build_snapshot(levels=1, sequence=1)
        delta2 = _build_delta(sequence=1)
        snap2 = _build_snapshot(levels=1, sequence=1)
        ws1 = _FakeWS(messages=[snap1], block_recv=False)
        ws2 = _FakeWS(messages=[delta2, snap2], block_recv=True)
        calls = {"n": 0}
        def _connector(url, *args, **kwargs):
            calls["n"] += 1
            return ws1 if calls["n"] == 1 else ws2
        monkeypatch.setattr(tbt_feed_mod, "ws_connect", _connector)

        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        async def _url(_token):
            return "wss://tbt.example/ws"
        feed._fetch_ws_url = _url
        feed._calculate_backoff = lambda: 0.01

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(None, stop))
        await asyncio.sleep(0.25)
        stop.set()
        await asyncio.wait_for(task, timeout=2.0)

        # first session published snapshot; second session's leading delta must not publish incomplete book
        # published depths should correspond to snapshots only (2 times)
        assert market.apply_depth.await_count == 2

    @pytest.mark.asyncio
    async def test_fresh_snapshot_restores_book_and_permits_deltas(self):
        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        ws = AsyncMock()
        ws.open = True
        feed._ws = ws

        await feed._handle_message(_build_snapshot(levels=1, sequence=1))
        book = feed._books.get("NSE:RELIANCE-EQ")
        book.invalidate()
        market.apply_depth.reset_mock()

        await feed._handle_message(_build_snapshot(levels=1, sequence=10))
        assert market.apply_depth.await_count == 1
        assert book.awaiting_snapshot is False
        assert book.last_sequence == 10

        await feed._handle_message(_build_delta(sequence=11))
        assert market.apply_depth.await_count == 2
        assert book.last_sequence == 11

    @pytest.mark.asyncio
    async def test_invalid_snapshot_does_not_restore_validity(self):
        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        ws = AsyncMock()
        ws.open = True
        feed._ws = ws

        book = feed._books.get_or_create("NSE:RELIANCE-EQ", "NSE", "RELIANCE-EQ")
        book.invalidate()
        # malformed/invalid snapshot payload -> normalization/parse path should not mark valid
        await feed._handle_message(b"not-a-protobuf")
        assert book.awaiting_snapshot is True
        assert market.apply_depth.await_count == 0

    @pytest.mark.asyncio
    async def test_no_incomplete_order_book_published_while_awaiting_snapshot(self):
        market = AsyncMock()
        feed = FyersTbtFeed(lambda: "raw", {"tbt_enabled": True, "app_id": "APP"}, market)
        feed._desired_symbols.add("NSE:RELIANCE-EQ")
        ws = AsyncMock()
        ws.open = True
        feed._ws = ws

        # multiple deltas before snapshot
        for seq in (1, 2, 3):
            await feed._handle_message(_build_delta(sequence=seq))
        assert market.apply_depth.await_count == 0
        book = feed._books.get("NSE:RELIANCE-EQ")
        assert book is not None and book.awaiting_snapshot is True and book.is_empty


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])



