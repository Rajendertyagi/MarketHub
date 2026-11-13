#!/usr/bin/env python3
"""Focused test: canonical index option-chain underlyings.

The supported index option-chain underlyings are owned by the backend
(``market.index_registry`` -> ``app.market_indices.OPTION_CHAIN_INDICES``)
and exposed via GET /api/options/index-underlyings. The React frontend must
consume this contract rather than hard-coding the list.

The picker is DERIVED from the canonical F&O index registry — never a
separate hand-maintained list. That is what keeps NIFTYNXT50 and NIFTYFPI
present: the previous hardcoded six-label list silently omitted both
established F&O underlyings.
"""

from __future__ import annotations

import json
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)


def test_capability_owns_canonical_fno_indices() -> None:
    from app.market_indices import (
        OPTION_CHAIN_INDICES,
        index_option_underlyings,
    )
    from market.index_registry import FNO_INDEX_LABELS

    # ONE authoritative backend source of truth: the canonical F&O index
    # registry. The picker may not be a separate, smaller list.
    assert OPTION_CHAIN_INDICES == tuple(FNO_INDEX_LABELS)

    # Supported F&O index scope (NSE index F&O + BSE SENSEX F&O only).
    assert list(FNO_INDEX_LABELS) == [
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX",
    ]
    for label in ("NIFTYFPI", "BANKEX"):
        assert label not in OPTION_CHAIN_INDICES, label

    out = index_option_underlyings()
    labels = [u["label"] for u in out]
    assert labels == list(FNO_INDEX_LABELS)
    # Human/canonical labels + exchange only (never raw derivative tokens).
    for u in out:
        assert u["exchange"] in ("NSE", "BSE")
        assert " " not in u["label"]


async def test_route_exposes_canonical_underlyings() -> None:
    from api.product_routes import build_intel_routes
    from market.index_registry import FNO_INDEX_LABELS

    routes = build_intel_routes(None, None)
    target = next(
        (r for r in routes if r.path == "/api/options/index-underlyings"),
        None,
    )
    assert target is not None, "endpoint must be registered"
    resp = await target.endpoint(None)  # handler ignores the request
    body = json.loads(resp.body)
    assert [u["label"] for u in body["underlyings"]] == list(FNO_INDEX_LABELS)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
