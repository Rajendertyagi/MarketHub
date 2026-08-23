"""
Provider payload normalizers for MarketHub.

Public API (provider-prefixed re-exports):
    upstox_quote_from_rest(entry, received_ts=...)          -> Quote
    upstox_depth_from_rest(entry, received_ts=...)          -> Depth | None
    upstox_quote_fields_from_ws_ltpc(ltpc, ...)             -> dict  (field map)
    upstox_quote_fields_from_ws_full(ff, ...)               -> dict  (field map)
    upstox_depth_from_ws(market_level, ...)                 -> Depth
    upstox_instrument_from_master(record)                   -> Instrument
    fyers_quote_from_quotes_rest(v, symbol=..., ...)        -> Quote
    fyers_quote_fields_from_symbol_update(msg, ...)         -> dict  (field map)
    fyers_depth_from_rest(payload, symbol=..., ...)         -> (Depth, dict)
    fyers_instrument_from_master(record)                    -> Instrument

Field maps follow the presence contract in normalize.common: a key present
means the provider reported that field; an absent key means "not reported".
Pure stdlib + market.models only.
"""

from market.normalize.common import (
    NormalizationError,
    NumericError,
    QUOTE_FIELD_NAMES,
    TimestampError,
    apply_derived_change,
    check_quote_fields,
    parse_timestamp,
    set_reported,
    to_float,
    to_int,
)
from market.normalize.fyers import (
    depth_from_rest as fyers_depth_from_rest,
    instrument_from_master as fyers_instrument_from_master,
    quote_fields_from_symbol_update as fyers_quote_fields_from_symbol_update,
    quote_from_quotes_rest as fyers_quote_from_quotes_rest,
    split_fyers_symbol,
)
from market.normalize.upstox import (
    depth_from_rest as upstox_depth_from_rest,
    depth_from_ws as upstox_depth_from_ws,
    exchange_from_segment,
    instrument_from_master as upstox_instrument_from_master,
    quote_fields_from_ws_full as upstox_quote_fields_from_ws_full,
    quote_fields_from_ws_ltpc as upstox_quote_fields_from_ws_ltpc,
    quote_from_rest as upstox_quote_from_rest,
)

__all__ = [
    # shared contract
    "NormalizationError",
    "NumericError",
    "TimestampError",
    "QUOTE_FIELD_NAMES",
    "apply_derived_change",
    "check_quote_fields",
    "parse_timestamp",
    "set_reported",
    "to_float",
    "to_int",
    # upstox
    "upstox_quote_from_rest",
    "upstox_depth_from_rest",
    "upstox_quote_fields_from_ws_ltpc",
    "upstox_quote_fields_from_ws_full",
    "upstox_depth_from_ws",
    "upstox_instrument_from_master",
    "exchange_from_segment",
    # fyers
    "fyers_quote_from_quotes_rest",
    "fyers_quote_fields_from_symbol_update",
    "fyers_depth_from_rest",
    "fyers_instrument_from_master",
    "split_fyers_symbol",
]
