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
    "OIStrikeRow", "OISnapshot", "OIChangeStrikeRow", "OIChangeSnapshot",
    "MaxPainData", "PCRData", "NewsArticle", "NewsSnapshot",
    "MarketHoliday", "MarketSession",
    "FuturesSmartlistEntry", "FuturesSmartlist",
    "FIIRecord", "FIIActivity",
    "DIIRecord", "DIIActivity",
    "CompanyProfile", "KeyRatios", "CorporateAction", "Competitor",
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


def _require_non_empty(model: str, field_name: str, value: Any) -> None:
    """Required string fields must be non-empty after stripping."""
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

# ---------------------------------------------------------------------------
# OI Analytics models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OIStrikeRow:
    """Per-strike OI data for calls and puts."""
    strike_price: float
    call_oi: int | None = None
    put_oi: int | None = None


@dataclass(frozen=True, slots=True)
class OISnapshot:
    """Aggregate OI data for an underlying + expiry."""
    instrument_token: str
    exchange: str
    expiry: str
    spot_closing_price: float | None = None
    total_call_oi: int | None = None
    total_put_oi: int | None = None
    strikes: tuple[OIStrikeRow, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_token, str) or not self.instrument_token.strip():
            raise ValueError("OISnapshot.instrument_token must be a non-empty string")
        if not isinstance(self.exchange, str) or not self.exchange.strip():
            raise ValueError("OISnapshot.exchange must be a non-empty string")


@dataclass(frozen=True, slots=True)
class OIChangeStrikeRow:
    """Per-strike OI change data."""
    strike_price: float
    call_change_oi: int | None = None
    put_change_oi: int | None = None


@dataclass(frozen=True, slots=True)
class OIChangeSnapshot:
    """OI change data for an underlying + expiry over N days."""
    instrument_token: str
    exchange: str
    expiry: str
    spot_closing_price: float | None = None
    total_call_change_oi: int | None = None
    total_put_change_oi: int | None = None
    days: int | None = None
    strikes: tuple[OIChangeStrikeRow, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("OIChangeSnapshot", "instrument_token", self.instrument_token)
        _require_non_empty("OIChangeSnapshot", "exchange", self.exchange)


@dataclass(frozen=True, slots=True)
class MaxPainData:
    """Max pain analysis for an underlying + expiry."""
    instrument_token: str
    exchange: str
    expiry: str
    max_pain_strike: float | None = None
    max_pain_value: float | None = None
    spot_price: float | None = None
    total_call_pain: float | None = None
    total_put_pain: float | None = None


@dataclass(frozen=True, slots=True)
class PCRData:
    """Put-Call Ratio analysis."""
    instrument_token: str
    exchange: str
    expiry: str | None = None
    pcr: float | None = None
    total_put_oi: int | None = None
    total_call_oi: int | None = None
    spot_price: float | None = None


@dataclass(frozen=True, slots=True)
class NewsArticle:
    """News article from provider."""
    heading: str
    summary: str | None = None
    thumbnail: str | None = None
    article_link: str | None = None
    published_time: datetime | None = None
    source: str | None = None  # provider name


@dataclass(frozen=True, slots=True)
class NewsSnapshot:
    """News for one or more instruments."""
    instrument_token: str | None = None
    articles: tuple[NewsArticle, ...] = ()
    total_records: int | None = None
    page: int | None = None
    page_size: int | None = None

# ---------------------------------------------------------------------------
# Market Information models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketHoliday:
    """Holiday or special trading session for one or more exchanges."""
    date: str
    description: str
    holiday_type: str
    closed_exchanges: tuple[str, ...] = ()
    open_exchanges: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSession:
    """Trading session times for one exchange on one date."""
    exchange: str
    start_time: datetime
    end_time: datetime


# ---------------------------------------------------------------------------
# Futures Smartlist models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FuturesSmartlistEntry:
    """One ranked futures contract from a smartlist."""
    instrument_key: str
    price_current: float
    price_close: float
    price_change_abs: float
    price_change_pct: float
    metric_current: float
    metric_previous: float
    metric_change_abs: float
    metric_change_pct: float
    metric_key: str


@dataclass(frozen=True, slots=True)
class FuturesSmartlist:
    """Ranked futures contracts by a market signal."""
    asset_type: str
    category: str
    metric_key: str
    timestamp: datetime | None = None
    entries: tuple[FuturesSmartlistEntry, ...] = ()
    page_number: int | None = None
    page_size: int | None = None
    total_pages: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


# ---------------------------------------------------------------------------
# FII/DII Activity models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FIIRecord:
    """One day/month of FII activity for a segment."""
    timestamp: datetime
    buy_amount: float
    sell_amount: float
    buy_contracts: int
    sell_contracts: int
    oi_contracts: int
    oi_amount: float
    total_long_contracts: int
    total_short_contracts: int
    total_call_long_contracts: int
    total_put_long_contracts: int
    total_call_short_contracts: int
    total_put_short_contracts: int


@dataclass(frozen=True, slots=True)
class FIIActivity:
    """FII activity aggregated across segments."""
    data_type: str
    interval: str
    records: tuple[FIIRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))


@dataclass(frozen=True, slots=True)
class DIIRecord:
    """One day/month of DII activity for NSE cash."""
    timestamp: datetime
    buy_amount: float
    sell_amount: float
    buy_contracts: int
    sell_contracts: int
    oi_contracts: int
    oi_amount: float
    total_long_contracts: int
    total_short_contracts: int
    total_call_long_contracts: int
    total_put_long_contracts: int
    total_call_short_contracts: int
    total_put_short_contracts: int


@dataclass(frozen=True, slots=True)
class DIIActivity:
    """DII activity for NSE equity cash segment."""
    data_type: str
    interval: str
    records: tuple[DIIRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))


# ---------------------------------------------------------------------------
# Fundamentals models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompanyProfile:
    """Company profile from fundamentals API."""
    isin: str
    company_profile: str
    sector: str
    sector_market_cap_inr_crore: float | None = None
    sector_market_cap_usd_billion: float | None = None


@dataclass(frozen=True, slots=True)
class KeyRatios:
    """Key financial ratios for a company."""
    isin: str
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    roe: float | None = None
    roa: float | None = None
    roce: float | None = None
    ev_ebitda: float | None = None


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One corporate action event."""
    action_type: str
    description: str
    record_date: str | None = None
    ex_date: str | None = None
    payment_date: str | None = None
    value: float | None = None


@dataclass(frozen=True, slots=True)
class Competitor:
    """One competitor instrument."""
    instrument_key: str
    symbol: str
    name: str | None = None
    sector: str | None = None


# ---------------------------------------------------------------------------
# News enhanced
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewsPagination:
    """Pagination metadata for news responses."""
    total_records: int | None = None
    page_number: int | None = None
    page_size: int | None = None
    total_pages: int | None = None


@dataclass(frozen=True, slots=True)
class NewsSnapshot:
    """Enhanced news response with pagination."""
    instrument_token: str | None = None
    category: str | None = None
    articles: tuple[NewsArticle, ...] = ()
    pagination: NewsPagination | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "articles", tuple(self.articles))

# ---------------------------------------------------------------------------
# Margin Calculator models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarginEntry:
    """Margin breakdown for one instrument."""
    instrument_key: str
    span_margin: float
    exposure_margin: float
    equity_margin: float
    net_buy_premium: float
    additional_margin: float
    tender_margin: float
    total_margin: float


@dataclass(frozen=True, slots=True)
class MarginBasket:
    """Margin calculation for a basket of instruments."""
    required_margin: float
    final_margin: float
    entries: tuple[MarginEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))


# ---------------------------------------------------------------------------
# Shareholdings models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShareholdingRecord:
    """One quarter of shareholding data for a category."""
    period: str
    value: float


@dataclass(frozen=True, slots=True)
class ShareholdingCategory:
    """Shareholding category with quarterly history."""
    category: str
    history: tuple[ShareholdingRecord, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "history", tuple(self.history))


@dataclass(frozen=True, slots=True)
class Shareholdings:
    """Quarterly shareholding pattern for a company."""
    isin: str
    categories: tuple[ShareholdingCategory, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "categories", tuple(self.categories))


# ---------------------------------------------------------------------------
# Option Greeks standalone models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptionGreekEntry:
    """Greeks and market data for one option instrument."""
    instrument_key: str
    last_price: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    iv: float | None = None
    oi: int | None = None
    volume: int | None = None
    last_traded_qty: int | None = None
    previous_close: float | None = None


@dataclass(frozen=True, slots=True)
class OptionGreekSnapshot:
    """Standalone option Greeks for one or more instruments."""
    entries: tuple[OptionGreekEntry, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))