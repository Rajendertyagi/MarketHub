"""Yahoo Finance AI research capability (yfinance).

MarketHub -> yfinance -> Yahoo Finance. An AI research/data source that is
completely separate from the broker-authoritative market feed and the F&O
path. No broker, auth, persistence, or canonical-market dependency.
"""
from __future__ import annotations

__all__ = ["YahooFinanceService"]


def __getattr__(name: str):
    if name == "YahooFinanceService":
        from yfinance_service.service import YahooFinanceService
        return YahooFinanceService
    raise AttributeError(name)
