#!/usr/bin/env python3
"""Fyers option-chain expiry timestamp (S: provider-correct request).

Defect: the adapter converted an expiry DATE to an epoch at 00:00 UTC.
Fyers' expiry timestamp is the expiry INSTANT (15:45 IST == 10:15 UTC for
NSE index expiries), so a midnight epoch falls outside the expiry and Fyers
answers "Please provide valid expiry" (code=1) - which the workspace
swallowed, leaving the option chain empty.

The adapter now sends the expiry instant that Fyers itself reports in
``expiryData[].expiry`` for the requested expiry date.
"""

from __future__ import annotations

import asyncio
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


class _FakeRest:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def option_chain(self, token, symbol, strikecount=50, timestamp=None,
                           expiry=""):
        self.calls.append({"symbol": symbol, "expiry": expiry,
                           "timestamp": timestamp})
        return {"symbol": symbol}


def _adapter():
    from brokers.fyers.rest import FyersMarketDataAdapter
    rest = _FakeRest()
    return FyersMarketDataAdapter(rest, lambda: "TOKEN"), rest


def test_expiry_timestamp_is_the_fyers_expiry_instant() -> None:
    """The timestamp must be the provider's expiry INSTANT, not midnight."""
    from brokers.fyers import rest as frest
    # Seed the cache with the provider's own expiryData (as a live response
    # would) and assert the adapter addresses THAT instant.
    frest._remember_expiry_epochs({"data": {"expiryData": [
        {"date": "27-10-2026", "expiry": "1793095800", "expiry_flag": "M"},
    ]}})
    ad, rest = _adapter()
    asyncio.run(ad.option_chain(
        instrument_key="NSE:ADANIGREEN26OCT1000CE", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    ts = rest.calls[0]["timestamp"]
    assert ts == "1793095800", f"expected the Fyers expiry instant, got {ts}"


def test_expiry_timestamp_is_never_midnight_utc() -> None:
    """Midnight UTC addresses the wrong expiry - Fyers rejects it."""
    ad, rest = _adapter()
    asyncio.run(ad.option_chain(
        instrument_key="NSE:ADANIGREEN26OCT1000CE", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    ts = int(rest.calls[0]["timestamp"])
    midnight = 1793059200     # 2026-10-27T00:00:00Z
    assert ts != midnight, "must not use midnight UTC for the expiry"


def test_expiry_date_still_sent_alongside() -> None:
    ad, rest = _adapter()
    asyncio.run(ad.option_chain(
        instrument_key="NSE:ADANIGREEN26OCT1000CE", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls[0]["expiry"] == "2026-10-27"


def test_unknown_expiry_still_sends_a_timestamp() -> None:
    """An expiry Fyers did not report falls back to the IST session close."""
    ad, rest = _adapter()
    asyncio.run(ad.option_chain(
        instrument_key="NSE:ADANIGREEN26OCT1000CE", exchange="NSE",
        tradingsymbol="ADANIGREEN", expiry="2026-10-27"))
    assert rest.calls[0]["timestamp"] is not None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
