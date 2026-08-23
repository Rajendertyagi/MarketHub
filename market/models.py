"""
Canonical provider-neutral market data models for MarketHub.

Pure domain-model module (Phase A):
  * standard library only — no external dependencies
  * imports nothing from the application packages (app, core, mcp_server,
    sources, brokers)
  * no provider-specific models, parsing, or normalization (that belongs to
    the future market/normalize layer and broker adapters)

Timestamp semantics (canonical across all models):

    exchange_ts
        Timestamp reported by the exchange/provider for the market update,
        when the provider supplies one. Optional everywhere.

    received_ts
        Timestamp when MarketHub accepted/normalized the update. Required on
        every model that carries it; it is owned by MarketHub, never by the
        provider.

All canonical datetime values MUST be timezone-aware when present. Naive
datetimes are rejected explicitly at construction with ValueError. No silent
conversion or timezone normalization happens here.

Validation is intentionally minimal and deterministic: required identity
strings must be non-empty, datetime fields must be real timezone-aware
datetime objects, and Depth order-book levels must be DepthLevel instances
(coerced into immutable tuples). Everything else — symbol parsing, token
translation, exchange-specific rules, session logic — is deferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["Instrument", "Quote", "DepthLevel", "Depth"]

# Identity field names shared by every instrument-bearing model.
_IDENTITY_FIELDS = ("instrument_token", "exchange", "tradingsymbol")


# ---------------------------------------------------------------------------
# Validation helpers (module-private, deterministic, side-effect free)
# ---------------------------------------------------------------------------


def _require_identity(model: str, obj: Any) -> None:
    """Required identity strings must be non-empty after stripping."""
    for field_name in _IDENTITY_FIELDS:
        value = getattr(obj, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{model}.{field_name} must be a non-empty string")


def _require_tz_aware(model: str, field_name: str, value: Any) -> None:
    """Datetime fields must be real datetime objects and timezone-aware.

    None is always acceptable (optional field). A naive datetime (tzinfo is
    None, or utcoffset() returns None) is rejected explicitly — never
    silently converted.
    """
    if value is None:
        return
    if not isinstance(value, datetime):
        raise TypeError(
            f"{model}.{field_name} must be a datetime when present; "
            f"got {type(value).__name__}"
        )
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(
            f"{model}.{field_name} must be timezone-aware; "
            "naive datetimes are rejected"
        )


def _coerce_level_tuple(
    model: str, field_name: str, value: Any
) -> tuple[DepthLevel, ...]:
    """Coerce any sequence of DepthLevel into an immutable tuple.

    Accepts any iterable sequence (list, tuple, ...) so callers are not
    forced to pre-build tuples; the frozen model then guarantees the stored
    value can never be mutated through the original container.

    Raises TypeError if the value is not iterable or contains a non-DepthLevel.
    """
    if value is None:
        raise ValueError(
            f"{model}.{field_name} must be a sequence of DepthLevel; got None"
        )
    try:
        items = tuple(value)
    except TypeError:
        raise TypeError(
            f"{model}.{field_name} must be a sequence of DepthLevel; "
            f"got {type(value).__name__}"
        ) from None
    for item in items:
        if not isinstance(item, DepthLevel):
            raise TypeError(
                f"{model}.{field_name} must contain only DepthLevel entries; "
                f"got {type(item).__name__}"
            )
    return items


# ---------------------------------------------------------------------------
# Canonical models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Instrument:
    """Provider-neutral instrument identity and reference metadata.

    Required identity:
        instrument_token   stable provider-neutral instrument identifier
        exchange           exchange code, e.g. "NSE" / "BSE" / "NFO"
        tradingsymbol      exchange trading symbol, e.g. "RELIANCE"

    Optional canonical metadata (None when unknown / not applicable):
        name  instrument_type  tick_size  lot_size  expiry  strike

    ``expiry`` is timezone-aware when present.
    """

    instrument_token: str
    exchange: str
    tradingsymbol: str
    name: str | None = None
    instrument_type: str | None = None
    tick_size: float | None = None
    lot_size: int | None = None
    expiry: datetime | None = None
    strike: float | None = None

    def __post_init__(self) -> None:
        _require_identity("Instrument", self)
        _require_tz_aware("Instrument", "expiry", self.expiry)


@dataclass(frozen=True, slots=True)
class Quote:
    """Latest normalized quote/snapshot for one instrument.

    Identity mirrors Instrument. ``received_ts`` is REQUIRED (MarketHub's own
    acceptance stamp) and therefore precedes the optional fields — Python
    requires non-default fields before defaulted ones. All price/state fields
    are optional: providers emit partial snapshots and the canonical model
    preserves exactly what was reported.

    Full order-book depth deliberately lives in Depth, not here; a quote
    carries only the best_bid / best_ask scalars.
    """

    instrument_token: str
    exchange: str
    tradingsymbol: str
    received_ts: datetime              # required: MarketHub acceptance stamp
    ltp: float | None = None           # last traded price
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None         # previous-close reference price
    volume: int | None = None
    change: float | None = None
    change_percent: float | None = None
    best_bid: float | None = None
    best_ask: float | None = None
    open_interest: float | int | None = None      # outstanding contracts (derivatives)
    avg_trade_price: float | None = None          # session average traded price
    last_traded_qty: float | int | None = None    # quantity of the last trade
    total_buy_qty: float | int | None = None      # aggregate bid quantity (all levels)
    total_sell_qty: float | int | None = None     # aggregate ask quantity (all levels)
    exchange_ts: datetime | None = None   # provider-reported, when available

    def __post_init__(self) -> None:
        _require_identity("Quote", self)
        _require_tz_aware("Quote", "received_ts", self.received_ts)
        _require_tz_aware("Quote", "exchange_ts", self.exchange_ts)


@dataclass(frozen=True, slots=True)
class DepthLevel:
    """One order-book level (price level with aggregated quantity)."""

    price: float
    quantity: float
    orders: int | None = None   # order count at this level, when reported


@dataclass(frozen=True, slots=True)
class Depth:
    """Full canonical market depth for one instrument (separate from Quote).

    ``bids`` / ``asks`` are stored as immutable tuples of DepthLevel,
    ordered best-first by convention (ordering enforcement is a
    normalization-layer concern, not a model concern). Sequences passed at
    construction are coerced to tuples so a mutable list can never alias
    into this frozen model.
    """

    instrument_token: str
    exchange: str
    tradingsymbol: str
    received_ts: datetime
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    exchange_ts: datetime | None = None

    def __post_init__(self) -> None:
        _require_identity("Depth", self)
        _require_tz_aware("Depth", "received_ts", self.received_ts)
        _require_tz_aware("Depth", "exchange_ts", self.exchange_ts)
        # frozen+slots dataclasses reject plain assignment; object.__setattr__
        # is the documented way to finalize derived values in __post_init__.
        object.__setattr__(self, "bids", _coerce_level_tuple("Depth", "bids", self.bids))
        object.__setattr__(self, "asks", _coerce_level_tuple("Depth", "asks", self.asks))
