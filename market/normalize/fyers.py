"""
FYERS payload normalizers (pure functions).

Inputs are decoded payloads: REST JSON (v3 /data/quotes entries, /data/depth
responses, sym_master JSON records) or SDK-decoded data-socket dicts
(SymbolUpdate / lite). Outputs are either complete canonical objects (REST
snapshots) or canonical field maps (WS partial updates, presence contract in
normalize.common).

Provider aliases (lp, ch, chp, prev_close_price, vol_traded_today,
tot_buy_qty, ...) die here — canonical names only leave this module.

FYERS-specific semantics implemented:
  * timestamps on the data path are epoch SECONDS (locked policy #4).
  * ``ch``/``chp`` are explicit provider change values and win over
    derivation (locked rule #6); derivation fills them from
    ltp + prev_close_price when absent.
  * bid_size / ask_size / fyToken / spread have no canonical home and are
    intentionally dropped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from market.models import Depth, DepthLevel, Instrument, OptionGreeks, Quote
from market.normalize.common import (
    NormalizationError,
    apply_derived_change,
    check_quote_fields,
    parse_timestamp,
    set_reported,
    to_float,
    to_int,
)

__all__ = [
    "split_fyers_symbol",
    "instrument_from_master",
    "quote_from_quotes_rest",
    "quote_fields_from_symbol_update",
    "depth_from_rest",
    "depth_from_ws_depth",
    "greeks_from_options_chain",
]

# Canonical fields carried as integers.
_INT_FIELDS = frozenset(
    {"volume", "last_traded_qty", "total_buy_qty", "total_sell_qty"}
)

# REST /data/quotes `v{}` aliases -> canonical names.
_QUOTES_REST_ALIASES = {
    "lp": "ltp",
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "prev_close_price": "close",
    "ch": "change",
    "chp": "change_percent",
    "bid": "best_bid",
    "ask": "best_ask",
    "atp": "avg_trade_price",
}

# Data-socket SymbolUpdate aliases -> canonical names.
# NOTE: the socket uses different key names than REST quotes — `ltp` (not
# `lp`) and `bid_price`/`ask_price` (not `bid`/`ask`). bid_size/ask_size/
# fyToken have no canonical home and are intentionally unmapped.
_WS_SYMBOL_ALIASES = {
    "ltp": "ltp",
    "open_price": "open",
    "high_price": "high",
    "low_price": "low",
    "prev_close_price": "close",
    "ch": "change",
    "chp": "change_percent",
    "bid_price": "best_bid",
    "ask_price": "best_ask",
    "atp": "avg_trade_price",
    "vol_traded_today": "volume",
    "last_traded_qty": "last_traded_qty",
    "tot_buy_qty": "total_buy_qty",
    "tot_sell_qty": "total_sell_qty",
    # extended coverage
    "upper_ckt": "upper_circuit",
    "lower_ckt": "lower_circuit",
}


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def split_fyers_symbol(symbol: str) -> tuple[str, str]:
    """'NSE:SBIN-EQ' -> ('NSE', 'SBIN-EQ'). Raises on malformed input."""
    text = str(symbol).strip()
    exchange, sep, tradingsymbol = text.partition(":")
    if not sep or not exchange or not tradingsymbol:
        raise NormalizationError(f"fyers symbol: malformed {symbol!r}")
    return exchange, tradingsymbol


def _identity(symbol: str, received_ts: datetime) -> dict[str, Any]:
    """Canonical identity for a Fyers payload keyed by its API symbol."""
    if not isinstance(symbol, str) or ":" not in symbol:
        raise NormalizationError(f"fyers payload: missing/invalid symbol {symbol!r}")
    exchange, tradingsymbol = split_fyers_symbol(symbol)
    return {
        "instrument_token": symbol.strip(),
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "received_ts": received_ts,
    }


def _fields_from_aliases(
    payload: dict[str, Any],
    aliases: dict[str, str],
) -> dict[str, Any]:
    """Map provider keys to canonical keys with presence semantics."""
    fields: dict[str, Any] = {}
    for src, dst in aliases.items():
        converter = to_int if dst in _INT_FIELDS else to_float
        set_reported(fields, payload, src, dst, converter)
    return fields


# ---------------------------------------------------------------------------
# REST /data/quotes entry (`v{}` object)
# ---------------------------------------------------------------------------


def quote_from_quotes_rest(
    v: dict[str, Any],
    *,
    symbol: str,
    received_ts: datetime,
) -> Quote:
    """Normalize one FYERS /data/quotes ``v`` object into a complete Quote."""
    if not isinstance(v, dict):
        raise NormalizationError("fyers quotes rest: expected object")
    fields = _identity(symbol, received_ts)
    fields.update(_fields_from_aliases(v, _QUOTES_REST_ALIASES))
    set_reported(fields, v, "volume", "volume", to_int)
    if v.get("tt") is not None:
        fields["exchange_ts"] = parse_timestamp(v["tt"], unit="s", field="quotes.tt")
        fields["last_trade_time"] = parse_timestamp(
            v["tt"], unit="s", field="quotes.tt")
    apply_derived_change(fields)
    check_quote_fields(fields)
    return Quote(**fields)


# ---------------------------------------------------------------------------
# Data-socket updates (SymbolUpdate / lite)
# ---------------------------------------------------------------------------


def quote_fields_from_symbol_update(
    msg: dict[str, Any],
    *,
    received_ts: datetime,
) -> dict[str, Any]:
    """Normalize a SymbolUpdate/lite tick into a canonical field map.

    Presence-exact: only fields the provider reported appear in the map.
    Every update carries ``symbol``, so identity is always resolvable here.
    """
    if not isinstance(msg, dict):
        raise NormalizationError("fyers symbol update: expected object")
    symbol = msg.get("symbol")
    fields = _identity(symbol, received_ts)
    fields.update(_fields_from_aliases(msg, _WS_SYMBOL_ALIASES))
    if msg.get("last_traded_time") is not None:
        fields["exchange_ts"] = parse_timestamp(
            msg["last_traded_time"], unit="s", field="last_traded_time"
        )
        fields["last_trade_time"] = parse_timestamp(
            msg["last_traded_time"], unit="s", field="last_traded_time"
        )
    apply_derived_change(fields)
    check_quote_fields(fields)
    return fields


# ---------------------------------------------------------------------------
# REST /data/depth response
# ---------------------------------------------------------------------------


def _levels_fyers(rows: Any, side: str) -> list[DepthLevel]:
    """Depth rows {price, volume, ord} -> DepthLevels.

    Locked policy #1: zero-price placeholder rows are dropped.
    Locked rule #7: legitimate zero quantities are KEPT.
    """
    levels: list[DepthLevel] = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            raise NormalizationError(f"fyers depth.{side}[{i}]: expected object")
        raw_price = row.get("price")
        if raw_price is None:
            continue
        price = to_float(raw_price, field=f"fyers depth.{side}.price")
        if price == 0:
            continue
        quantity = to_float(row.get("volume"), field=f"fyers depth.{side}.volume")
        orders_raw = row.get("ord")
        orders = (
            to_int(orders_raw, field=f"fyers depth.{side}.ord")
            if orders_raw is not None
            else None
        )
        levels.append(DepthLevel(price=price, quantity=quantity or 0.0, orders=orders))
    return levels


def depth_from_rest(
    payload: dict[str, Any],
    *,
    symbol: str,
    received_ts: datetime,
) -> tuple[Depth, dict[str, Any]]:
    """Normalize a FYERS /data/depth response.

    Returns ``(Depth, supplemental_quote_fields)``: the depth endpoint also
    carries ltp/v/atp/oi/totals, which belong to quote state — they are
    returned as a canonical field map for the caller to merge via the same
    presence contract.
    """
    if not isinstance(payload, dict):
        raise NormalizationError("fyers depth rest: expected object")
    identity = _identity(symbol, received_ts)
    depth = Depth(
        bids=_levels_fyers(payload.get("bids"), "bids"),
        asks=_levels_fyers(payload.get("ask"), "ask"),
        **identity,
    )
    supplemental: dict[str, Any] = {}
    for src, dst, converter in (
        ("totalbuyqty", "total_buy_qty", to_int),
        ("totalsellqty", "total_sell_qty", to_int),
        ("ltp", "ltp", to_float),
        ("v", "volume", to_int),
        ("atp", "avg_trade_price", to_float),
        ("oi", "open_interest", to_float),
        # extended coverage
        ("ltq", "last_traded_qty", to_int),
        ("upper_ckt", "upper_circuit", to_float),
        ("lower_ckt", "lower_circuit", to_float),
        ("oipercent", "oi_change_percent", to_float),
        # pdoi = previous-DAY open interest (not a change value).
        ("pdoi", "previous_oi", to_float),
    ):
        set_reported(supplemental, payload, src, dst, converter)
    if payload.get("ltt") is not None:
        supplemental["last_trade_time"] = parse_timestamp(
            payload["ltt"], unit="s", field="depth.ltt")
    # Derived-field policy: oi_change = oi - previous_oi is unambiguous
    # arithmetic; derive ONLY when both inputs are reported and the
    # provider did not supply an explicit change value.
    if ("open_interest" in supplemental
            and "previous_oi" in supplemental
            and "oi_change" not in supplemental):
        supplemental["oi_change"] = (
            supplemental["open_interest"] - supplemental["previous_oi"]
        )
    return depth, supplemental


# ---------------------------------------------------------------------------
# Data-socket DepthUpdate (`type:"dp"`, flattened 5-level message)
# ---------------------------------------------------------------------------


def depth_from_ws_depth(
    msg: dict[str, Any],
    *,
    received_ts: datetime,
) -> tuple[Depth, dict[str, Any]]:
    """Normalize a FYERS ``dp`` socket message into (Depth, identity-fields).

    The dp message carries flattened levels (bid_price1..5 / ask_price1..5
    with matching size/order counts) plus the symbol — no totals, no LTP.
    Zero-price levels are dropped (locked policy #1); zero quantities and
    zero order counts are kept (rule #7).
    """
    if not isinstance(msg, dict):
        raise NormalizationError("fyers ws depth: expected object")
    identity = _identity(msg.get("symbol"), received_ts)

    def _side(prefix: str, side: str) -> list[DepthLevel]:
        levels: list[DepthLevel] = []
        for i in range(1, 6):
            raw_price = msg.get(f"{prefix}{i}")
            if raw_price is None:
                continue
            price = to_float(raw_price, field=f"dp.{prefix}{i}")
            if price == 0:
                continue
            qty_raw = msg.get(f"{prefix.replace('price', 'size')}{i}")
            quantity = (
                to_float(qty_raw, field=f"dp.{prefix}size{i}")
                if qty_raw is not None else 0.0
            )
            ord_raw = msg.get(f"{prefix.replace('price', 'order')}{i}")
            orders = (
                to_int(ord_raw, field=f"dp.{prefix}order{i}")
                if ord_raw is not None else None
            )
            levels.append(DepthLevel(price=price, quantity=quantity,
                                     orders=orders))
        return levels

    depth = Depth(
        bids=_side("bid_price", "bids"),
        asks=_side("ask_price", "asks"),
        **identity,
    )
    return depth, {}


# ---------------------------------------------------------------------------
# Options-chain greeks (GET /data/options-chain-v3 with greeks="1")
# ---------------------------------------------------------------------------


def greeks_from_options_chain(leg: dict[str, Any]) -> OptionGreeks | None:
    """Normalize one options-chain leg's greeks into an OptionGreeks.

    Fyers exposes delta/gamma/theta/vega/iv per leg; rho is NOT provided
    by Fyers and stays None. Returns None when the leg carries no greeks
    at all. Null values are preserved as None (REST null semantics).
    """
    if not isinstance(leg, dict):
        raise NormalizationError("fyers options chain: expected object")
    g = leg.get("greeks")
    if not isinstance(g, dict) or not g:
        return None
    fields: dict[str, float | None] = {}
    for name in ("delta", "gamma", "theta", "vega"):
        raw = g.get(name)
        fields[name] = (
            to_float(raw, field=f"greeks.{name}")
            if raw is not None else None
        )
    iv_raw = g.get("iv")
    fields["iv"] = (
        to_float(iv_raw, field="greeks.iv") if iv_raw is not None else None
    )
    fields["rho"] = None  # not exposed by Fyers
    if all(v is None for v in fields.values()):
        return None
    return OptionGreeks(**fields)


# ---------------------------------------------------------------------------
# Symbol master record
# ---------------------------------------------------------------------------


def instrument_from_master(record: dict[str, Any]) -> Instrument:
    """Normalize one FYERS sym_master JSON record into an Instrument."""
    if not isinstance(record, dict):
        raise NormalizationError("fyers master record: expected object")
    ticker = record.get("symTicker")
    if not isinstance(ticker, str) or ":" not in ticker:
        raise NormalizationError(f"fyers master record: bad symTicker {ticker!r}")
    exchange, tradingsymbol = split_fyers_symbol(ticker)

    expiry_raw = record.get("expiryDate")
    expiry = (
        parse_timestamp(expiry_raw, unit="s", field="expiryDate")
        if expiry_raw not in (None, "")
        else None
    )
    strike = (
        to_float(record["strikePrice"], field="strikePrice")
        if record.get("strikePrice") is not None
        else None
    )
    if strike is not None and strike <= 0:
        strike = None  # -1 sentinel for non-options

    opt_type = record.get("optType")

    return Instrument(
        instrument_token=ticker.strip(),
        exchange=exchange,
        tradingsymbol=tradingsymbol,
        name=record.get("exSymName") or record.get("symbolDetails"),
        instrument_type=opt_type if opt_type in ("CE", "PE") else None,
        tick_size=(
            to_float(record["tickSize"], field="tickSize")
            if record.get("tickSize") is not None
            else None
        ),
        lot_size=(
            to_int(record["minLotSize"], field="minLotSize")
            if record.get("minLotSize") is not None
            else None
        ),
        expiry=expiry,
        strike=strike,
    )
