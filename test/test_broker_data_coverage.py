#!/usr/bin/env python3
"""Regression tests for broker data coverage fixes.

Covers:
  * Fyers WS OI normalization
  * Fyers WS depth emission
  * Fyers exch_feed_time mapping
  * Upstox REST circuit limits
  * Upstox REST last_trade_time
"""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from datetime import datetime, timezone  # noqa: E402

from helpers.runner import R  # noqa: E402
from market.normalize.fyers import (  # noqa: E402
    quote_fields_from_symbol_update,
    depth_from_ws_depth,
    _WS_SYMBOL_ALIASES,
)
from market.normalize.upstox import (  # noqa: E402
    quote_from_rest,
)
from market.models import Depth, DepthLevel  # noqa: E402


UTC = timezone.utc
NOW = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


class TestFyersWSOI:
    """Fyers WS OI must reach canonical open_interest."""

    def test_oi_in_aliases(self):
        assert "OI" in _WS_SYMBOL_ALIASES
        assert _WS_SYMBOL_ALIASES["OI"] == "open_interest"

    def test_oi_reaches_canonical(self):
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "ltp": 810.5,
            "OI": 1234567,
        }
        fields = quote_fields_from_symbol_update(msg, received_ts=NOW)
        assert fields.get("open_interest") == 1234567.0

    def test_oi_zero_reported(self):
        """Fyers keeps literal zero (no P-ZERO like Upstox)."""
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "ltp": 810.5,
            "OI": 0,
        }
        fields = quote_fields_from_symbol_update(msg, received_ts=NOW)
        assert fields.get("open_interest") == 0.0


class TestFyersWSDepth:
    """Fyers WS depth must normalize to canonical Depth."""

    def test_depth_levels_preserved(self):
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "bid_price1": 810.0, "bid_size1": 100, "bid_order1": 5,
            "bid_price2": 809.5, "bid_size2": 200, "bid_order2": 10,
            "ask_price1": 811.0, "ask_size1": 150, "ask_order1": 7,
            "ask_price2": 811.5, "ask_size2": 250, "ask_order2": 12,
        }
        depth, _ = depth_from_ws_depth(msg, received_ts=NOW)
        assert isinstance(depth, Depth)
        assert len(depth.bids) == 2
        assert len(depth.asks) == 2
        assert depth.bids[0].price == 810.0
        assert depth.bids[0].quantity == 100.0
        assert depth.bids[0].orders == 5
        assert depth.asks[0].price == 811.0
        assert depth.asks[0].quantity == 150.0
        assert depth.asks[0].orders == 7

    def test_zero_price_dropped(self):
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "bid_price1": 0, "bid_size1": 100,
            "ask_price1": 811.0, "ask_size1": 150,
        }
        depth, _ = depth_from_ws_depth(msg, received_ts=NOW)
        assert len(depth.bids) == 0
        assert len(depth.asks) == 1


class TestFyersExchFeedTime:
    """Fyers exch_feed_time must map to exchange_ts."""

    def test_exch_feed_time_preferred(self):
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "ltp": 810.5,
            "exch_feed_time": 1724674800,
            "last_traded_time": 1724674801,
        }
        fields = quote_fields_from_symbol_update(msg, received_ts=NOW)
        assert fields.get("exchange_ts") is not None
        # Should use exch_feed_time (earlier)
        assert fields["exchange_ts"].timestamp() == 1724674800

    def test_fallback_to_last_traded_time(self):
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "ltp": 810.5,
            "last_traded_time": 1724674801,
        }
        fields = quote_fields_from_symbol_update(msg, received_ts=NOW)
        assert fields.get("exchange_ts") is not None
        assert fields["exchange_ts"].timestamp() == 1724674801

    def test_last_trade_time_also_set(self):
        msg = {
            "symbol": "NSE:SBIN-EQ",
            "ltp": 810.5,
            "last_traded_time": 1724674801,
        }
        fields = quote_fields_from_symbol_update(msg, received_ts=NOW)
        assert fields.get("last_trade_time") is not None
        assert fields["last_trade_time"].timestamp() == 1724674801


class TestUpstoxRESTCircuits:
    """Upstox REST must map circuit limits."""

    def test_upper_circuit_mapped(self):
        entry = {
            "instrument_token": "NSE_EQ|INE848E01016",
            "symbol": "NHPC",
            "last_price": 52.05,
            "upper_circuit_limit": 63.7,
            "lower_circuit_limit": 42.5,
        }
        q = quote_from_rest(entry, received_ts=NOW)
        assert q.upper_circuit == 63.7
        assert q.lower_circuit == 42.5

    def test_circuits_absent_when_not_in_payload(self):
        entry = {
            "instrument_token": "NSE_EQ|INE848E01016",
            "symbol": "NHPC",
            "last_price": 52.05,
        }
        q = quote_from_rest(entry, received_ts=NOW)
        assert q.upper_circuit is None
        assert q.lower_circuit is None


class TestUpstoxRESTLastTradeTime:
    """Upstox REST must map last_trade_time."""

    def test_last_trade_time_mapped(self):
        entry = {
            "instrument_token": "NSE_EQ|INE848E01016",
            "symbol": "NHPC",
            "last_price": 52.05,
            "last_trade_time": "1697624972130",
        }
        q = quote_from_rest(entry, received_ts=NOW)
        assert q.last_trade_time is not None
        # 1697624972130 ms = 2023-10-18 23:49:32 UTC
        assert q.last_trade_time.year == 2023

    def test_last_trade_time_absent_when_not_in_payload(self):
        entry = {
            "instrument_token": "NSE_EQ|INE848E01016",
            "symbol": "NHPC",
            "last_price": 52.05,
        }
        q = quote_from_rest(entry, received_ts=NOW)
        assert q.last_trade_time is None


class TestProviderParity:
    """Both providers must feed same canonical models."""

    def test_fyers_oi_canonical(self):
        msg = {"symbol": "NSE:SBIN-EQ", "ltp": 810.5, "OI": 1000000}
        fields = quote_fields_from_symbol_update(msg, received_ts=NOW)
        assert "open_interest" in fields
        assert fields["open_interest"] == 1000000.0

    def test_upstox_oi_canonical(self):
        entry = {
            "instrument_token": "NSE_FO|45450",
            "symbol": "BANKNIFTY",
            "last_price": 219.3,
            "oi": 256800,
        }
        q = quote_from_rest(entry, received_ts=NOW)
        assert q.open_interest == 256800.0
