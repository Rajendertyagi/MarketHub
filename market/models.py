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

__all__ = [
    "Instrument", "Quote", "DepthLevel", "Depth", "OptionGreeks",
    "merge_greeks", "Candle",
    "OptionContractData", "OptionStrikeRow", "OptionChainSnapshot",
]

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
class OptionGreeks:
    """Provider-neutral option risk metrics for one instrument.

    All fields optional (None = not reported by the provider). Values are
    stored exactly as normalized — no unit conversion happens here:
        delta/gamma/theta/vega/rho   provider-reported sensitivities
        iv                           implied volatility in provider units
                                     (Upstox: percent, e.g. 18.5; Fyers
                                     options-chain: percent)
    """

    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    iv: float | None = None


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
    # ── Extended coverage (provider-reported where available) ──────────────
    upper_circuit: float | None = None    # upper price band (None = n/a)
    lower_circuit: float | None = None    # lower price band (None = n/a)
    oi_change: float | None = None        # OI change vs previous session
    oi_change_percent: float | None = None
    previous_oi: float | None = None      # previous-session OI, when the
    #                                       provider reports it explicitly
    last_trade_time: datetime | None = None  # time of the last trade, when
    #                                           reported separately from the
    #                                           provider feed timestamp
    greeks: OptionGreeks | None = None    # option risk metrics (options only)

    def __post_init__(self) -> None:
        _require_identity("Quote", self)
        _require_tz_aware("Quote", "received_ts", self.received_ts)
        _require_tz_aware("Quote", "exchange_ts", self.exchange_ts)
        _require_tz_aware("Quote", "last_trade_time", self.last_trade_time)
        if self.greeks is not None and not isinstance(self.greeks, OptionGreeks):
            if isinstance(self.greeks, dict):
                # Ergonomic coercion: patch maps may carry a plain dict of
                # greek values; normalize into the frozen model.
                object.__setattr__(
                    self, "greeks",
                    OptionGreeks(**{
                        k: v for k, v in self.greeks.items()
                        if k in OptionGreeks.__dataclass_fields__
                    }),
                )
            else:
                raise TypeError(
                    "Quote.greeks must be an OptionGreeks instance when present; "
                    f"got {type(self.greeks).__name__}"
                )


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


def merge_greeks(
    old: "OptionGreeks | None", new: "OptionGreeks | None"
) -> "OptionGreeks | None":
    """Field-wise merge of an incoming Greeks snapshot over prior state.

    Providers report greeks as snapshots; a snapshot may legitimately
    carry only SOME fields (e.g. Upstox wire-absent scalars are dropped
    by the P-ZERO rule). Whole-object replacement would silently discard
    previously reported values, so each field merges independently:
      * new field not-None  -> take the new value
      * new field None      -> preserve the old value
      * both models None    -> result None
    An explicit full clear is expressed at the patch layer by reporting
    ``greeks: None`` (whole-object clear), which bypasses this helper.
    """
    if new is None:
        return old
    if old is None:
        return new
    return OptionGreeks(
        delta=new.delta if new.delta is not None else old.delta,
        gamma=new.gamma if new.gamma is not None else old.gamma,
        theta=new.theta if new.theta is not None else old.theta,
        vega=new.vega if new.vega is not None else old.vega,
        rho=new.rho if new.rho is not None else old.rho,
        iv=new.iv if new.iv is not None else old.iv,
    )


# ---------------------------------------------------------------------------
# Market history + option chain (canonical, provider-neutral)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candle:
    """One canonical OHLCV candle (timezone-aware timestamp required)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None
    open_interest: float | None = None

    def __post_init__(self) -> None:
        _require_tz_aware("Candle", "timestamp", self.timestamp)


@dataclass(frozen=True, slots=True)
class OptionContractData:
    """Market data + greeks for one option contract (CE or PE)."""

    ltp: float | None = None
    volume: int | None = None
    bid: float | None = None
    ask: float | None = None
    oi: float | None = None
    previous_oi: float | None = None
    oi_change: float | None = None
    close: float | None = None
    iv: float | None = None
    delta: float | None = None
    theta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    pop: float | None = None   # probability of profit, when provider supplies


@dataclass(frozen=True, slots=True)
class OptionStrikeRow:
    strike: float
    call: OptionContractData | None = None
    put: OptionContractData | None = None
    atm: bool = False


@dataclass(frozen=True, slots=True)
class OptionChainSnapshot:
    instrument_token: str      # underlying instrument key
    exchange: str
    tradingsymbol: str         # underlying trading symbol ("" when unknown)
    expiry: str
    spot_price: float | None = None
    atm_strike: float | None = None
    strikes: tuple[OptionStrikeRow, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tradingsymbol, str):
            raise TypeError(
                "OptionChainSnapshot.tradingsymbol must be a str")
        object.__setattr__(
            self, "strikes",
            _coerce_strike_rows(self.strikes))


def _coerce_strike_rows(value: Any) -> tuple["OptionStrikeRow", ...]:
    if value is None:
        return ()
    try:
        items = tuple(value)
    except TypeError:
        raise TypeError(
            "OptionChainSnapshot.strikes must be a sequence of "
            "OptionStrikeRow") from None
    for item in items:
        if not isinstance(item, OptionStrikeRow):
            raise TypeError(
                "OptionChainSnapshot.strikes must contain only "
                "OptionStrikeRow entries")
    return items
