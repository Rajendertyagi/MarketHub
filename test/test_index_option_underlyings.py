#!/usr/bin/env python3
"""Focused test: canonical index option-chain underlyings (Phase A3).

The supported index option-chain underlyings are owned by the backend
(app.market_indices.OPTION_CHAIN_INDICES) and exposed via
GET /api/options/index-underlyings. The React frontend must consume this
contract rather than hard-coding the list.
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_capability_owns_canonical_four() -> None:
    from app.market_indices import (
        OPTION_CHAIN_INDICES,
        index_option_underlyings,
    )
    # One authoritative backend source of truth.
    assert OPTION_CHAIN_INDICES == (
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    )
    out = index_option_underlyings()
    labels = [u["label"] for u in out]
    assert labels == ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    # Human/canonical labels + exchange only (never raw derivative tokens).
    for u in out:
        assert u["exchange"] == "NSE"
        assert " " not in u["label"]


async def test_route_exposes_canonical_underlyings() -> None:
    from api.product_routes import build_intel_routes

    routes = build_intel_routes(None, None)
    target = next(
        (r for r in routes if r.path == "/api/options/index-underlyings"),
        None,
    )
    assert target is not None, "endpoint must be registered"
    resp = await target.endpoint(None)  # handler ignores the request
    body = json.loads(resp.body)
    assert [u["label"] for u in body["underlyings"]] == [
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
    ]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
