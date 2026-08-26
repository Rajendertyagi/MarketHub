#!/usr/bin/env python3
"""Tests for OI analytics, Max Pain, PCR, and News data layers."""

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
    OIStrikeRow, OISnapshot, OIChangeStrikeRow, OIChangeSnapshot,
    MaxPainData, PCRData, NewsArticle, NewsSnapshot,
)
from market.normalize.upstox_analytics import (  # noqa: E402
    oi_from_rest, oi_change_from_rest, max_pain_from_rest, pcr_from_rest,
)
from market.normalize.upstox_news import news_from_rest  # noqa: E402


UTC = timezone.utc


class TestOISnapshot:
    """OI snapshot model and normalizer."""

    def test_model_creation(self):
        oi = OISnapshot(
            instrument_token="NSE_INDEX|Nifty 50",
            exchange="NSE",
            expiry="2026-05-29",
            total_call_oi=9800000,
            total_put_oi=12500000,
        )
        assert oi.instrument_token == "NSE_INDEX|Nifty 50"
        assert oi.total_call_oi == 9800000
        assert oi.total_put_oi == 12500000

    def test_normalize_from_rest(self):
        payload = {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "data": {
                "total_puts": 12500000,
                "total_calls": 9800000,
                "spot_closing_price": 24450.75,
                "expiry": "2026-05-29",
                "call_put_oi_data_list": [
                    {"call_oi": 450000, "put_oi": 680000, "strike_price": 24000.0},
                    {"call_oi": 500000, "put_oi": 700000, "strike_price": 24500.0},
                ],
            },
        }
        result = oi_from_rest(payload)
        assert isinstance(result, OISnapshot)
        assert result.total_call_oi == 9800000
        assert result.total_put_oi == 12500000
        assert len(result.strikes) == 2
        assert result.strikes[0].strike_price == 24000.0
        assert result.strikes[0].call_oi == 450000
        assert result.strikes[0].put_oi == 680000

    def test_empty_strikes(self):
        payload = {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "data": {"total_puts": 0, "total_calls": 0, "expiry": "2026-05-29"},
        }
        result = oi_from_rest(payload)
        assert result.strikes == ()


class TestOIChangeSnapshot:
    """OI change snapshot model and normalizer."""

    def test_normalize_from_rest(self):
        payload = {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "data": {
                "total_put_change_oi": 2500000,
                "total_call_change_oi": -1800000,
                "spot_closing_price": 24450.75,
                "expiry": "2026-05-29",
                "interval": 1,
                "call_put_oi_data_list": [
                    {"strike_price": 24000.0, "call_change_oi": -120000, "put_change_oi": 350000},
                ],
            },
        }
        result = oi_change_from_rest(payload)
        assert isinstance(result, OIChangeSnapshot)
        assert result.total_call_change_oi == -1800000
        assert result.total_put_change_oi == 2500000
        assert result.days == 1
        assert len(result.strikes) == 1
        assert result.strikes[0].call_change_oi == -120000
        assert result.strikes[0].put_change_oi == 350000


class TestMaxPainData:
    """Max pain data model and normalizer."""

    def test_normalize_from_rest(self):
        payload = {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "data": {
                "expiry": "2026-05-29",
                "max_pain_strike": 24000.0,
                "max_pain_value": 50000000,
                "spot_price": 24450.75,
            },
        }
        result = max_pain_from_rest(payload)
        assert isinstance(result, MaxPainData)
        assert result.max_pain_strike == 24000.0
        assert result.max_pain_value == 50000000
        assert result.spot_price == 24450.75


class TestPCRData:
    """PCR data model and normalizer."""

    def test_normalize_from_rest(self):
        payload = {
            "instrument_key": "NSE_INDEX|Nifty 50",
            "data": {
                "expiry": "2026-05-29",
                "pcr": 1.25,
                "total_put_oi": 12500000,
                "total_call_oi": 10000000,
                "spot_price": 24450.75,
            },
        }
        result = pcr_from_rest(payload)
        assert isinstance(result, PCRData)
        assert result.pcr == 1.25
        assert result.total_put_oi == 12500000
        assert result.total_call_oi == 10000000


class TestNewsArticle:
    """News article model and normalizer."""

    def test_model_creation(self):
        article = NewsArticle(
            heading="Test News",
            summary="Test summary",
        )
        assert article.heading == "Test News"
        assert article.summary == "Test summary"

    def test_normalize_from_rest(self):
        payload = {
            "data": {
                "NSE_EQ|INE040H01021": [
                    {
                        "heading": "Reliance reports quarterly results",
                        "summary": "Company posts 10% growth",
                        "thumbnail": "https://example.com/img.jpg",
                        "article_link": "https://example.com/article",
                        "published_time": 1776251261821,
                    }
                ]
            },
            "metadata": {"page": {"total_records": 1, "page_number": 1, "page_size": 100}},
        }
        result = news_from_rest(payload, "NSE_EQ|INE040H01021")
        assert isinstance(result, NewsSnapshot)
        assert len(result.articles) == 1
        assert result.articles[0].heading == "Reliance reports quarterly results"
        assert result.articles[0].summary == "Company posts 10% growth"
        assert result.total_records == 1
        assert result.articles[0].published_time is not None
        assert result.articles[0].source == "upstox"


class TestProviderParity:
    """Both providers must feed same canonical models."""

    def test_oi_canonical(self):
        oi = OISnapshot(
            instrument_token="NSE_INDEX|Nifty 50",
            exchange="NSE",
            expiry="2026-05-29",
            total_call_oi=9800000,
            total_put_oi=12500000,
        )
        assert oi.instrument_token == "NSE_INDEX|Nifty 50"
        assert oi.total_call_oi == 9800000

    def test_max_pain_canonical(self):
        mp = MaxPainData(
            instrument_token="NSE_INDEX|Nifty 50",
            exchange="NSE",
            expiry="2026-05-29",
            max_pain_strike=24000.0,
        )
        assert mp.max_pain_strike == 24000.0

    def test_pcr_canonical(self):
        pcr = PCRData(
            instrument_token="NSE_INDEX|Nifty 50",
            exchange="NSE",
            pcr=1.25,
        )
        assert pcr.pcr == 1.25

    def test_news_canonical(self):
        article = NewsArticle(
            heading="Test",
            source="upstox",
        )
        assert article.source == "upstox"
