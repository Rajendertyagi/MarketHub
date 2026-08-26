#!/usr/bin/env python3
"""Tests for extended broker data: holidays, timings, futures, FII/DII, fundamentals."""

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
from market.models import (  # noqa: E402
    MarketHoliday, MarketSession,
    FuturesSmartlist, FuturesSmartlistEntry,
    FIIRecord, FIIActivity,
    DIIRecord, DIIActivity,
    CompanyProfile, KeyRatios, CorporateAction, Competitor,
)
from market.normalize.upstox_market_info import (  # noqa: E402
    holidays_from_rest, holiday_status_from_rest, timings_from_rest,
)
from market.normalize.upstox_analytics_extended import (  # noqa: E402
    futures_smartlist_from_rest, fii_single_from_rest, dii_single_from_rest,
)
from market.normalize.upstox_fundamentals import (  # noqa: E402
    company_profile_from_rest, key_ratios_from_rest,
    corporate_actions_from_rest, competitors_from_rest,
)


UTC = timezone.utc


class TestMarketHolidays:
    """Market holidays model and normalizer."""

    def test_model_creation(self):
        h = MarketHoliday(
            date="2024-01-26",
            description="Republic Day",
            holiday_type="TRADING_HOLIDAY",
            closed_exchanges=("NSE", "BSE"),
        )
        assert h.date == "2024-01-26"
        assert h.holiday_type == "TRADING_HOLIDAY"

    def test_normalize_full_list(self):
        payload = {
            "status": "success",
            "data": [
                {
                    "date": "2024-01-26",
                    "description": "Republic Day",
                    "holiday_type": "TRADING_HOLIDAY",
                    "closed_exchanges": ["NSE", "BSE"],
                    "open_exchanges": [],
                },
                {
                    "date": "2024-01-01",
                    "description": "New Year",
                    "holiday_type": "TRADING_HOLIDAY",
                    "closed_exchanges": [],
                    "open_exchanges": [{"exchange": "MCX"}],
                },
            ],
        }
        result = holidays_from_rest(payload)
        assert len(result) == 2
        assert result[0].date == "2024-01-26"
        assert result[0].closed_exchanges == ("NSE", "BSE")
        assert result[1].open_exchanges == ("MCX",)

    def test_normalize_single_date(self):
        payload = {
            "status": "success",
            "data": {
                "date": "2024-01-26",
                "description": "Republic Day",
                "holiday_type": "TRADING_HOLIDAY",
                "closed_exchanges": ["NSE"],
                "open_exchanges": [],
            },
        }
        result = holiday_status_from_rest(payload)
        assert isinstance(result, MarketHoliday)
        assert result.date == "2024-01-26"


class TestMarketTimings:
    """Market session timings model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": [
                {"exchange": "NSE", "start_time": 1704080700000, "end_time": 1704103200000},
                {"exchange": "BSE", "start_time": 1704080700000, "end_time": 1704103200000},
            ],
        }
        result = timings_from_rest(payload)
        assert len(result) == 2
        assert result[0].exchange == "NSE"
        assert result[0].start_time is not None
        assert result[0].end_time is not None


class TestFuturesSmartlist:
    """Futures smartlist model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "asset_type": "INDEX",
                "category": "TOP_TRADED",
                "metric_key": "total_traded_value",
                "time_stamp": 1780045636752,
                "smartlist": [
                    {
                        "instrument_key": "NSE_FO|62329",
                        "price": {"current": 23867.0, "close_price": 23996.7, "change_abs": -129.70, "change_pct": -0.54},
                        "metric": {"current": 132094775540, "previous": 69295823909, "change_abs": 62798951631.0, "change_pct": 90.62},
                    }
                ],
                "page_number": 1,
                "page_size": 20,
                "total_pages": 9,
            },
        }
        result = futures_smartlist_from_rest(payload)
        assert isinstance(result, FuturesSmartlist)
        assert result.asset_type == "INDEX"
        assert len(result.entries) == 1
        assert result.entries[0].instrument_key == "NSE_FO|62329"
        assert result.entries[0].price_current == 23867.0


class TestFIIActivity:
    """FII activity model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "NSE_FO|STOCK_FUTURES": [
                    {
                        "time_stamp": 1777487400000,
                        "buy_amount": 23109.75,
                        "sell_amount": 24642.52,
                        "buy_contracts": 353981,
                        "sell_contracts": 384079,
                        "oi_contracts": 7245154,
                        "oi_amount": 452650.0,
                        "total_long_contracts": 4021980,
                        "total_short_contracts": 3223174,
                        "total_call_long_contracts": 0,
                        "total_put_long_contracts": 0,
                        "total_call_short_contracts": 0,
                        "total_put_short_contracts": 0,
                    }
                ],
            },
            "interval": "1D",
        }
        result = fii_single_from_rest(payload, "NSE_FO|STOCK_FUTURES")
        assert isinstance(result, FIIActivity)
        assert len(result.records) == 1
        assert result.records[0].buy_amount == 23109.75
        assert result.records[0].sell_amount == 24642.52


class TestDIIActivity:
    """DII activity model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "NSE_EQ|CASH": [
                    {
                        "time_stamp": 1746633600000,
                        "buy_amount": 8523456789.0,
                        "sell_amount": 7234567890.5,
                        "buy_contracts": 0,
                        "sell_contracts": 0,
                        "oi_contracts": 0,
                        "oi_amount": 0.0,
                        "total_long_contracts": 0,
                        "total_short_contracts": 0,
                        "total_call_long_contracts": 0,
                        "total_put_long_contracts": 0,
                        "total_call_short_contracts": 0,
                        "total_put_short_contracts": 0,
                    }
                ],
            },
            "interval": "1D",
        }
        result = dii_single_from_rest(payload, "NSE_EQ|CASH")
        assert isinstance(result, DIIActivity)
        assert len(result.records) == 1
        assert result.records[0].buy_amount == 8523456789.0


class TestCompanyProfile:
    """Company profile model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "company_profile": "Reliance Industries Limited is engaged in...",
                "sector": "Refineries",
                "sector_market_cap_inr": {"value": 1942866.05, "unit": "crore", "formatted": "1,942,866.05 Cr"},
                "sector_market_cap_usd": {"value": 215.87, "unit": "billion", "formatted": "$215.87B"},
            },
            "isin": "INE002A01018",
        }
        result = company_profile_from_rest(payload)
        assert isinstance(result, CompanyProfile)
        assert result.sector == "Refineries"
        assert result.sector_market_cap_inr_crore == 1942866.05


class TestKeyRatios:
    """Key ratios model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "pe_ratio": 25.5,
                "pb_ratio": 3.2,
                "roe": 18.5,
                "roa": 8.2,
                "roce": 15.3,
                "ev_ebitda": 12.1,
            },
            "isin": "INE002A01018",
        }
        result = key_ratios_from_rest(payload)
        assert isinstance(result, KeyRatios)
        assert result.pe_ratio == 25.5
        assert result.roe == 18.5


class TestCorporateActions:
    """Corporate actions model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": [
                {
                    "action_type": "DIVIDEND",
                    "description": "Final dividend of Rs 9 per share",
                    "record_date": "2024-06-15",
                    "ex_date": "2024-06-14",
                    "payment_date": "2024-07-01",
                    "value": 9.0,
                }
            ],
        }
        result = corporate_actions_from_rest(payload)
        assert len(result) == 1
        assert result[0].action_type == "DIVIDEND"
        assert result[0].value == 9.0


class TestCompetitors:
    """Competitors model and normalizer."""

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": [
                {
                    "instrument_key": "NSE_EQ|INE040H01021",
                    "symbol": "AZAD",
                    "name": "Azad Engineering",
                    "sector": "Capital Goods",
                },
                {
                    "instrument_key": "NSE_EQ|INE848E01016",
                    "symbol": "DMART",
                    "name": "Avenue Supermarts",
                    "sector": "Consumer Discretionary",
                },
            ],
        }
        result = competitors_from_rest(payload)
        assert len(result) == 2
        assert result[0].symbol == "AZAD"
        assert result[1].sector == "Consumer Discretionary"
