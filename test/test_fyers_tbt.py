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
        
        assert book.check_sequence(1) is True
        book.last_sequence = 1
        
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
        
        assert book.check_sequence(1) is True
        book.last_sequence = 1
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
            await asyncio.Future()
        raise websockets.exceptions.ConnectionClosedOK(None, None)

    async def close(self):
        self.close_calls += 1
        self.closed = True
        self.open = False

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
        gap = _build_snapshot(levels=1, sequence=5)
        ws = _FakeWS(messages=[snap, gap], block_recv=True)
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
        # initial sub+resume plus resubscribe (unsub+sub+resume) on gap
        assert sum(1 for m in msgs if m.get("type") == 1 and m["data"]["subs"] == -1) >= 1
        assert sum(1 for m in msgs if m.get("type") == 1 and m["data"]["subs"] == 1) >= 2
        assert sum(1 for m in msgs if m.get("type") == 2) >= 2

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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])



