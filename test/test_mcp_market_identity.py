#!/usr/bin/env python3
"""Phase 4C — MCP identity resolution for Upstox + Fyers feed keys.

Validates _split_identity and _parse_instrument_ref against the real
MarketService storage-key conventions:
  * Upstox: ("NSE", "NSE_INDEX|Nifty 50")
  * Fyers:  ("NSE", "NSE:NIFTY50-INDEX")
"""

from __future__ import annotations

import asyncio
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from helpers.runner import R  # noqa: E402

from mcp_server.tools.market import (  # noqa: E402
    _parse_instrument_ref,
    _split_identity,
)


class _Services:
    def __init__(self, resolver=None, catalog=None):
        self.identity_resolver = resolver
        self.instrument_catalog = catalog


class _Resolver:
    def __init__(self, mapping):
        self._m = mapping

    def resolve(self, ident):
        return self._m.get(ident)


async def test_split_identity_upstox_prefixed(runner: R) -> None:
    runner.assert_eq("upstox-prefixed",
                     _split_identity("NSE:NSE_INDEX|Nifty 50"),
                     ("NSE", "NSE_INDEX|Nifty 50"))


async def test_split_identity_upstox_bare(runner: R) -> None:
    runner.assert_eq("upstox-bare",
                     _split_identity("NSE_INDEX|Nifty 50"),
                     ("NSE", "NSE_INDEX|Nifty 50"))


async def test_split_identity_upstox_eq(runner: R) -> None:
    runner.assert_eq("upstox-eq",
                     _split_identity("NSE_EQ|RELIANCE"),
                     ("NSE", "NSE_EQ|RELIANCE"))


async def test_split_identity_bse(runner: R) -> None:
    runner.assert_eq("bse-index",
                     _split_identity("BSE_INDEX|Sensex"),
                     ("BSE", "BSE_INDEX|Sensex"))


async def test_split_identity_fyers(runner: R) -> None:
    runner.assert_eq("fyers-index",
                     _split_identity("NSE:NIFTY50-INDEX"),
                     ("NSE", "NSE:NIFTY50-INDEX"))
    runner.assert_eq("fyers-eq",
                     _split_identity("BSE:RELIANCE"),
                     ("BSE", "BSE:RELIANCE"))


async def test_split_identity_invalid(runner: R) -> None:
    runner.assert_true("empty", _split_identity("") is None)
    runner.assert_true("symbol", _split_identity("RELIANCE") is None)


async def test_parse_direct_upstox(runner: R) -> None:
    svc = _Services()
    runner.assert_eq("direct-upstox",
                     _parse_instrument_ref("NSE:NSE_INDEX|Nifty 50", svc),
                     ("NSE", "NSE_INDEX|Nifty 50"))
    runner.assert_eq("direct-upstox-bare",
                     _parse_instrument_ref("NSE_INDEX|Nifty 50", svc),
                     ("NSE", "NSE_INDEX|Nifty 50"))


async def test_parse_direct_fyers(runner: R) -> None:
    svc = _Services()
    runner.assert_eq("direct-fyers",
                     _parse_instrument_ref("NSE:NIFTY50-INDEX", svc),
                     ("NSE", "NSE:NIFTY50-INDEX"))


async def test_parse_via_resolver(runner: R) -> None:
    # Resolver returns a canonical feed key; direct parse must still map to the
    # correct storage key (no cross-provider corruption of the token).
    svc = _Services(resolver=_Resolver({
        "NIFTY": "NSE:NSE_INDEX|Nifty 50",
        "NIFTY50": "NSE:NIFTY50-INDEX",
    }))
    runner.assert_eq("via-resolver-upstox",
                     _parse_instrument_ref("NIFTY", svc),
                     ("NSE", "NSE_INDEX|Nifty 50"))
    runner.assert_eq("via-resolver-fyers",
                     _parse_instrument_ref("NIFTY50", svc),
                     ("NSE", "NSE:NIFTY50-INDEX"))


async def main() -> bool:
    runner = R()
    await test_split_identity_upstox_prefixed(runner)
    await test_split_identity_upstox_bare(runner)
    await test_split_identity_upstox_eq(runner)
    await test_split_identity_bse(runner)
    await test_split_identity_fyers(runner)
    await test_split_identity_invalid(runner)
    await test_parse_direct_upstox(runner)
    await test_parse_direct_fyers(runner)
    await test_parse_via_resolver(runner)
    return runner.summary()


if __name__ == "__main__":
    success = asyncio.run(main())
    raise SystemExit(0 if success else 1)
