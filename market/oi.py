"""Canonical OI change / OI% calculation (broker-neutral, pure).

Precedence (approved):
  1. provider-supplied previous OI (``quote.previous_oi``)
  2. persisted prior-session OI (``persisted_previous_oi``)
  3. unavailable (no valid baseline)

Provider ``oi_change``/``oi_change_percent`` win when present; otherwise they
are derived from the baseline with zero/missing guards. Never fabricated.

Lives in the canonical market layer (no ``app``/``brokers`` imports) so both
the recorder (app) and the futures board (market) share ONE implementation.
"""

from __future__ import annotations

from typing import Any

BASIS_PROVIDER = "provider"
BASIS_PERSISTED = "persisted"
BASIS_UNAVAILABLE = "unavailable"


def quote_field(quote: Any, name: str) -> Any:
    if quote is None:
        return None
    if isinstance(quote, dict):
        return quote.get(name)
    return getattr(quote, name, None)


def oi_change_and_pct(
    quote: Any, *, persisted_previous_oi: float | None = None,
) -> tuple[float | None, float | None, float | None, float | None, str]:
    """Return ``(oi, previous_oi, oi_change, oi_change_pct, basis)``."""
    oi = quote_field(quote, "open_interest")
    prev = quote_field(quote, "previous_oi")
    basis = BASIS_PROVIDER
    if prev is None and persisted_previous_oi is not None:
        prev = float(persisted_previous_oi)
        basis = BASIS_PERSISTED

    change = quote_field(quote, "oi_change")
    pct = quote_field(quote, "oi_change_percent")
    if change is None and oi is not None and prev is not None:
        try:
            change = oi - prev
        except TypeError:
            change = None
    if pct is None and change is not None and prev not in (None, 0):
        try:
            pct = (change / prev) * 100.0
        except (TypeError, ZeroDivisionError):
            pct = None

    if oi is None or prev is None:
        basis = BASIS_UNAVAILABLE
    return oi, prev, change, pct, basis
