#!/usr/bin/env python3
"""Tests for margin, shareholdings, and option Greeks."""

from __future__ import annotations

import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402
from market.models import (  # noqa: E402
    MarginEntry, MarginBasket,
    ShareholdingRecord, ShareholdingCategory, Shareholdings,
    OptionGreekEntry, OptionGreekSnapshot,
)
from market.normalize.upstox_margins import margin_from_rest  # noqa: E402
from market.normalize.upstox_shareholdings import shareholdings_from_rest  # noqa: E402
from market.normalize.upstox_option_greeks import option_greeks_from_rest  # noqa: E402


class TestMarginCalculator:
    """Margin calculator model and normalizer."""

    def test_model_creation(self):
        m = MarginEntry(
            instrument_key="NSE_EQ|INE669E01016",
            span_margin=0.0,
            exposure_margin=0.0,
            equity_margin=33.6,
            net_buy_premium=0.0,
            additional_margin=0.0,
            tender_margin=0.0,
            total_margin=33.6,
        )
        assert m.instrument_key == "NSE_EQ|INE669E01016"
        assert m.total_margin == 33.6
        assert m.equity_margin == 33.6

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "margins": [
                    {
                        "instrument_key": "NSE_EQ|INE669E01016",
                        "span_margin": 0,
                        "exposure_margin": 0,
                        "equity_margin": 33.6,
                        "net_buy_premium": 0,
                        "additional_margin": 0,
                        "total_margin": 33.6,
                        "tender_margin": 0,
                    }
                ],
                "required_margin": 33.6,
                "final_margin": 33.6,
            },
        }
        result = margin_from_rest(payload)
        assert isinstance(result, MarginBasket)
        assert result.required_margin == 33.6
        assert len(result.entries) == 1
        assert result.entries[0].equity_margin == 33.6


class TestShareholdings:
    """Shareholdings model and normalizer."""

    def test_model_creation(self):
        rec = ShareholdingRecord(period="Mar 2026", value=50.0)
        assert rec.period == "Mar 2026"
        assert rec.value == 50.0

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": [
                {
                    "category": "promoters",
                    "history": [
                        {"period": "Mar 2026", "value": 50.0},
                        {"period": "Dec 2025", "value": 50.01},
                    ],
                },
                {
                    "category": "fii",
                    "history": [
                        {"period": "Mar 2026", "value": 18.67},
                        {"period": "Dec 2025", "value": 19.09},
                    ],
                },
            ],
        }
        result = shareholdings_from_rest(payload, "INE002A01018")
        assert isinstance(result, Shareholdings)
        assert result.isin == "INE002A01018"
        assert len(result.categories) == 2
        assert result.categories[0].category == "promoters"
        assert len(result.categories[0].history) == 2
        assert result.categories[0].history[0].value == 50.0


class TestOptionGreeks:
    """Option Greeks standalone model and normalizer."""

    def test_model_creation(self):
        g = OptionGreekEntry(
            instrument_key="NSE_FO|43885",
            delta=-0.8081,
            iv=0.336,
        )
        assert g.instrument_key == "NSE_FO|43885"
        assert g.delta == -0.8081

    def test_normalize(self):
        payload = {
            "status": "success",
            "data": {
                "NSE_FO|43885": {
                    "last_price": 412.2,
                    "instrument_token": "NSE_FO|43885",
                    "ltq": 75,
                    "volume": 3609600,
                    "cp": 831.2,
                    "iv": 0.33599853515625,
                    "vega": 3.3899,
                    "gamma": 0.0005,
                    "theta": -51.848,
                    "delta": -0.8081,
                    "oi": 2476650,
                }
            },
        }
        result = option_greeks_from_rest(payload)
        assert isinstance(result, OptionGreekSnapshot)
        assert len(result.entries) == 1
        assert result.entries[0].instrument_key == "NSE_FO|43885"
        assert result.entries[0].delta == -0.8081
        assert result.entries[0].iv == 0.33599853515625
