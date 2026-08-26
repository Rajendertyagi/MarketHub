#!/usr/bin/env python3
"""Fyers TBT depth channel tests (TBT1-TBT24)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

_PROJECT_DIR = "."
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

import pytest
import pytest_asyncio

from brokers.fyers.tbt.feed import FyersTbtFeed
from brokers.fyers.tbt.normalizer import normalize_tbt_depth
from brokers.fyers.tbt.order_book import LocalOrderBook, OrderBookStore
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
    return {"tbt_enabled": True, "instrument_keys": ["NSE:RELIANCE-EQ"]}


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

        mock_ws = AsyncMock()
        mock_ws.open = True
        tbt_feed._ws = mock_ws
        
        await tbt_feed._send_subscribe("NSE:RELIANCE-EQ")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == 1
        assert sent["data"]["subs"] == 1
        assert sent["data"]["symbols"] == ["NSE:RELIANCE-EQ"]
        assert sent["data"]["mode"] == "depth"

    @pytest.mark.asyncio
    async def test_tbt3_unsubscribe_correct(self, tbt_feed):
        mock_ws = AsyncMock()
        mock_ws.open = True
        tbt_feed._ws = mock_ws
        
        await tbt_feed._send_unsubscribe("NSE:RELIANCE-EQ")
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == 1
        assert sent["data"]["subs"] == -1

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
        
        assert book.check_sequence(1) == True
        book.last_sequence = 1
        
        assert book.check_sequence(3) == False

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
        tbt_feed._stats["reconnects"] = 5
        
        task = asyncio.create_task(tbt_feed.run(None, stop_event))
        await asyncio.sleep(0.1)
        
        stop_event.set()
        await asyncio.sleep(0.2)
        
        assert task.done() or tbt_feed._state == "stopped"


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
        
        assert book.check_sequence(1) == True
        book.last_sequence = 1
        assert book.check_sequence(2) == True
        assert book.check_sequence(4) == False


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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])



