"""
Upstox payload normalizers (pure functions).

Inputs are decoded payloads: REST JSON entries (v2 full-market-quote) or
Protobuf-decoded WS dicts (v3 market-data-feed shapes). Outputs are either
complete canonical objects (REST snapshots) or canonical field maps (WS
partial updates, presence contract in normalize.common).

Provider aliases (last_price, net_change, cp, vtt, tbq, ...) die here —
canonical names only leave this module.

Upstox-specific semantics implemented:
  * ltpc.cp is the PREVIOUS-DAY CLOSE PRICE (locked policy #3), mapped to
    canonical ``close`` — never to change-percent.
  * REST quote timestamps: prefer ISO-8601, fall back to epoch-ms
    (locked policy #4). WS ``ltt`` is epoch-ms.
  * P-ZERO (locked): proto3 scalars have no presence — decoded
    default-zero equals unset on the wire, so WS field maps treat zero as
    NOT REPORTED (full policy: brokers/upstox/feed_protocol.py). REST JSON
    keeps literal-zero semantics (e.g. equity ``oi: 0`` is preserved).
    Verified against the official V3 schema; no live-feed adjustment
    required.
  * V3 depth rows are Quote{bidQ, bidP, askQ, askP}: order counts do not
    exist in the V3 schema, so WS-sourced DepthLevel.orders is None.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from market.models import Depth, DepthLevel, Instrument, Quote
from market.normalize.common import (
    NormalizationError,
    TimestampError,
    apply_derived_change,
    check_quote_fields,
    parse_timestamp,
    set_reported,
    to_float,
    to_int,
)

__all__ = [
    "exchange_from_segment",
    "instrument_from_master",
    "quote_from_rest",
    "depth_from_rest",
    "quote_fields_from_ws_ltpc",
    "quote_fields_from_ws_full",
    "depth_from_ws",
]

_UTC = timezone.utc


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


def exchange_from_segment(segment: str) -> str:
    """'NSE_EQ' -> 'NSE'; 'MCX_FO' -> 'MCX'; 'NSE_INDEX' -> 'NSE'."""
    return segment.split("_", 1)[0]


def _base_fields(
    instrument_key: str,
    received_ts: datetime,
    tradingsymbol: str | None,
) -> dict[str, Any]:
    """Identity fields for a WS field map. tradingsymbol only when resolved."""
    if not isinstance(instrument_key, str) or "|" not in instrument_key:
        raise NormalizationError(
            f"upstox ws: malformed instrument_key {instrument_key!r}"
        )
    fields: dict[str, Any] = {
        "instrument_token": instrument_key,
        "exchange": exchange_from_segment(instrument_key.partition("|")[0]),
        "received_ts": received_ts,
    }
    if tradingsymbol is not None:
        fields["tradingsymbol"] = tradingsymbol
    return fields


def _rest_identity(entry: dict[str, Any]) -> dict[str, str]:
    token = entry.get("instrument_token")
    if not isinstance(token, str) or not token.strip() or "|" not in token:
        raise NormalizationError(
            f"upstox entry: missing/invalid instrument_token {token!r}"
        )
    symbol = entry.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise NormalizationError(f"upstox {token}: missing symbol")
    return {
        "instrument_token": token,
        "exchange": exchange_from_segment(token.partition("|")[0]),
        "tradingsymbol": symbol.strip(),
    }


def _rest_timestamp(value: Any, field: str) -> datetime:
    """Locked policy #4: prefer ISO-8601, fall back to epoch-milliseconds."""
    try:
        return parse_timestamp(value, unit="iso", field=field)
    except TimestampError:
        return parse_timestamp(value, unit="ms", field=field)


# ---------------------------------------------------------------------------
# REST full-market-quote entry
# ---------------------------------------------------------------------------


def quote_from_rest(entry: dict[str, Any], *, received_ts: datetime) -> Quote:
    """Normalize one Upstox v2 full-market-quote entry into a complete Quote."""
    if not isinstance(entry, dict):
        raise NormalizationError("upstox quote entry: expected object")
    fields: dict[str, Any] = _rest_identity(entry)
    fields["received_ts"] = received_ts

    ohlc = entry.get("ohlc") or {}
    set_reported(fields, entry, "last_price", "ltp", to_float)
    for name in ("open", "high", "low", "close"):
        set_reported(fields, ohlc, name, name, to_float)
    set_reported(fields, entry, "volume", "volume", to_int)
    set_reported(fields, entry, "net_change", "change", to_float)
    set_reported(fields, entry, "average_price", "avg_trade_price", to_float)
    set_reported(fields, entry, "oi", "open_interest", to_float)
    set_reported(fields, entry, "total_buy_quantity", "total_buy_qty", to_int)
    set_reported(fields, entry, "total_sell_quantity", "total_sell_qty", to_int)
    if entry.get("timestamp") is not None:
        fields["exchange_ts"] = _rest_timestamp(
            entry["timestamp"], f"upstox {fields['instrument_token']} timestamp"
        )
    _apply_best_prices(fields, entry.get("depth"))
    apply_derived_change(fields)
    check_quote_fields(fields)
    return Quote(**fields)


def _levels_upstox(rows: Any, side: str) -> list[DepthLevel]:
    """REST depth rows {quantity, price, orders} -> DepthLevels.

    Locked policy #1: zero-price placeholder rows are dropped.
    Locked rule #7: legitimate zero quantities are KEPT.
    """
    levels: list[DepthLevel] = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            raise NormalizationError(
                f"upstox depth.{side}[{i}]: expected object"
            )
        raw_price = row.get("price")
        if raw_price is None:
            continue
        price = to_float(raw_price, field=f"upstox depth.{side}.price")
        if price == 0:
            continue
        quantity = to_float(row.get("quantity"), field=f"upstox depth.{side}.quantity")
        orders_raw = row.get("orders")
        orders = (
            to_int(orders_raw, field=f"upstox depth.{side}.orders")
            if orders_raw is not None
            else None
        )
        levels.append(DepthLevel(price=price, quantity=quantity or 0.0, orders=orders))
    return levels


def _apply_best_prices(fields: dict[str, Any], depth_entry: Any) -> None:
    """best_bid/best_ask from the first non-placeholder level of each side."""
    if not isinstance(depth_entry, dict):
        return
    for side, key in (("buy", "best_bid"), ("sell", "best_ask")):
        for level in depth_entry.get(side) or []:
            raw_price = level.get("price") if isinstance(level, dict) else None
            if raw_price is None:
                continue
            price = to_float(raw_price, field=f"upstox depth.{side}.price")
            if price:
                fields[key] = price
                break


def depth_from_rest(entry: dict[str, Any], *, received_ts: datetime) -> Depth | None:
    """Normalize the depth block of a REST quote entry.

    Returns None when the entry carries no 'depth' key at all; an empty
    (all-placeholder) book yields a Depth with empty tuples.
    """
    if not isinstance(entry, dict):
        raise NormalizationError("upstox depth entry: expected object")
    if "depth" not in entry:
        return None
    identity = _rest_identity(entry)
    bids = _levels_upstox((entry.get("depth") or {}).get("buy"), "buy")
    asks = _levels_upstox((entry.get("depth") or {}).get("sell"), "sell")
    kwargs: dict[str, Any] = {
        **identity,
        "received_ts": received_ts,
        "bids": bids,
        "asks": asks,
    }
    if entry.get("timestamp") is not None:
        kwargs["exchange_ts"] = _rest_timestamp(
            entry["timestamp"], f"upstox {identity['instrument_token']} timestamp"
        )
    return Depth(**kwargs)


# ---------------------------------------------------------------------------
# WebSocket partial updates (v3 protobuf decoded dicts)
# ---------------------------------------------------------------------------


def _apply_ltpc(fields: dict[str, Any], ltpc: dict[str, Any]) -> None:
    set_reported(fields, ltpc, "ltp", "ltp", to_float)
    set_reported(fields, ltpc, "cp", "close", to_float)  # locked policy #3
    set_reported(fields, ltpc, "ltq", "last_traded_qty", to_int)
    if ltpc.get("ltt") is not None:
        fields["exchange_ts"] = parse_timestamp(
            ltpc["ltt"], unit="ms", field="ltpc.ltt"
        )


def quote_fields_from_ws_ltpc(
    ltpc: dict[str, Any],
    *,
    instrument_key: str,
    received_ts: datetime,
    tradingsymbol: str | None = None,
) -> dict[str, Any]:
    """Normalize an ltpc-mode tick into a canonical field map (presence-exact)."""
    if not isinstance(ltpc, dict):
        raise NormalizationError("upstox ws ltpc: expected object")
    fields = _base_fields(instrument_key, received_ts, tradingsymbol)
    _apply_ltpc(fields, ltpc)
    apply_derived_change(fields)
    check_quote_fields(fields)
    return fields


# proto3 scalar absence decodes as 0 → 0 means "not reported" for these.
_ZERO_ABSENT_FLOATS = {"atp": "avg_trade_price", "oi": "open_interest"}
_ZERO_ABSENT_INTS = {
    "vtt": "volume",
    "tbq": "total_buy_qty",
    "tsq": "total_sell_qty",
}


def quote_fields_from_ws_full(
    ff: dict[str, Any],
    *,
    instrument_key: str,
    received_ts: datetime,
    tradingsymbol: str | None = None,
) -> dict[str, Any]:
    """Normalize a full-mode MarketFullFeed tick into a canonical field map.

    Extended coverage: optionGreeks, iv, and the day candle from
    marketOHLC (interval "1d") are extracted when wire-present. All other
    OHLC intervals are ignored (intraday candles are charting data, not
    quote state).
    """
    if not isinstance(ff, dict):
        raise NormalizationError("upstox ws full feed: expected object")
    fields = _base_fields(instrument_key, received_ts, tradingsymbol)

    ltpc = ff.get("ltpc")
    if ltpc is not None:
        if not isinstance(ltpc, dict):
            raise NormalizationError("upstox ws full feed.ltpc: expected object")
        _apply_ltpc(fields, ltpc)

    for src, dst in _ZERO_ABSENT_FLOATS.items():
        raw = ff.get(src)
        if raw is None or raw == 0:
            continue
        fields[dst] = to_float(raw, field=src)
    for src, dst in _ZERO_ABSENT_INTS.items():
        raw = ff.get(src)
        if raw is None or raw == 0:
            continue
        fields[dst] = to_int(raw, field=src)

    # Implied volatility (P-ZERO: wire-absent decodes as 0).
    # GREEKS WIRE-FORCED NOTE: OptionGreeks children are plain proto3
    # scalars (no `optional`), so an exact 0.0 value is NEVER serialized —
    # ListFields cannot distinguish "unset" from "truly 0.0". Dropping
    # zeros here is therefore forced by the wire format, not a heuristic.
    # A deep-OTM delta of exactly 0.0 is unrepresentable over Upstox WS;
    # near-zero values (0.05, 0.001) pass through correctly.
    iv_raw = ff.get("iv")
    if iv_raw is not None and iv_raw != 0:
        from market.models import OptionGreeks

        iv_val = to_float(iv_raw, field="iv")
        existing = fields.get("greeks")
        if existing is not None:
            fields["greeks"] = OptionGreeks(
                delta=existing.delta, gamma=existing.gamma,
                theta=existing.theta, vega=existing.vega,
                rho=existing.rho, iv=iv_val,
            )
        else:
            fields["greeks"] = OptionGreeks(iv=iv_val)

    # Option greeks message.
    og = ff.get("optionGreeks")
    if isinstance(og, dict) and og:
        from market.models import OptionGreeks

        greek_values: dict[str, float] = {}
        for name in ("delta", "gamma", "theta", "vega", "rho"):
            raw = og.get(name)
            if raw is None or raw == 0:
                continue
            greek_values[name] = to_float(raw, field=f"optionGreeks.{name}")
        if greek_values:
            existing = fields.get("greeks")
            if existing is not None:
                merged = {
                    "delta": existing.delta, "gamma": existing.gamma,
                    "theta": existing.theta, "vega": existing.vega,
                    "rho": existing.rho, "iv": existing.iv,
                }
                merged.update(greek_values)
                fields["greeks"] = OptionGreeks(**merged)
            else:
                fields["greeks"] = OptionGreeks(**greek_values)

    # Day candle from marketOHLC: fills open/high/low when wire-present.
    market_ohlc = (ff.get("marketOHLC") or {}).get("ohlc") or []
    for candle in market_ohlc:
        if not isinstance(candle, dict):
            continue
        if candle.get("interval") != "1d":
            continue
        for src, dst in (("open", "open"), ("high", "high"), ("low", "low")):
            raw = candle.get(src)
            if raw is None or raw == 0:
                continue
            fields[dst] = to_float(raw, field=f"marketOHLC.1d.{src}")
        break

    bid_ask = (ff.get("marketLevel") or {}).get("bidAskQuote") or []
    for src_key, dst_key in (("bidP", "best_bid"), ("askP", "best_ask")):
        for level in bid_ask:
            raw = level.get(src_key) if isinstance(level, dict) else None
            if raw is None:
                continue
            price = to_float(raw, field=f"bidAskQuote.{src_key}")
            if price:
                fields[dst_key] = price
                break

    apply_derived_change(fields)
    check_quote_fields(fields)
    return fields


def depth_from_ws(
    market_level: dict[str, Any],
    *,
    instrument_key: str,
    tradingsymbol: str,
    received_ts: datetime,
) -> Depth:
    """Normalize a WS marketLevel.bidAskQuote array into a canonical Depth.

    ``tradingsymbol`` is REQUIRED: Depth carries full identity and the Upstox
    wire format does not, so adapters must resolve it from their instrument
    directory before calling this.
    """
    if not isinstance(market_level, dict):
        raise NormalizationError("upstox ws marketLevel: expected object")
    if not isinstance(tradingsymbol, str) or not tradingsymbol.strip():
        raise NormalizationError(
            f"upstox ws depth {instrument_key}: tradingsymbol is required"
        )
    bid_ask = market_level.get("bidAskQuote") or []
    bids: list[DepthLevel] = []
    asks: list[DepthLevel] = []
    for i, level in enumerate(bid_ask):
        if not isinstance(level, dict):
            raise NormalizationError(f"upstox ws bidAskQuote[{i}]: expected object")
        # V3 wire shape: Quote{bidQ, bidP, askQ, askP} — one combined row
        # per level. Order counts do not exist in the V3 schema, so
        # WS-sourced DepthLevel.orders is always None.
        bid_q = level.get("bidQ")
        ask_q = level.get("askQ")
        if bid_q is not None:
            price = to_float(level.get("bidP"), field="bidAskQuote.bidP")
            if price:
                bids.append(
                    DepthLevel(
                        price=price,
                        quantity=to_float(bid_q, field="bidAskQuote.bidQ") or 0.0,
                        orders=None,
                    )
                )
        if ask_q is not None:
            price = to_float(level.get("askP"), field="bidAskQuote.askP")
            if price:
                asks.append(
                    DepthLevel(
                        price=price,
                        quantity=to_float(ask_q, field="bidAskQuote.askQ") or 0.0,
                        orders=None,
                    )
                )
    return Depth(
        instrument_token=instrument_key,
        exchange=exchange_from_segment(instrument_key.partition("|")[0]),
        tradingsymbol=tradingsymbol.strip(),
        received_ts=received_ts,
        bids=bids,
        asks=asks,
    )


# ---------------------------------------------------------------------------
# Instrument master record
# ---------------------------------------------------------------------------


def instrument_from_master(record: dict[str, Any]) -> Instrument:
    """Normalize one Upstox instruments-file JSON record into an Instrument."""
    if not isinstance(record, dict):
        raise NormalizationError("upstox master record: expected object")
    key = record.get("instrument_key")
    if not isinstance(key, str) or "|" not in key:
        raise NormalizationError(f"upstox master record: bad instrument_key {key!r}")
    symbol = record.get("tradingsymbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise NormalizationError(f"upstox master {key}: missing tradingsymbol")

    expiry: datetime | None = None
    expiry_raw = record.get("expiry")
    if isinstance(expiry_raw, str) and expiry_raw.strip():
        try:
            d = date.fromisoformat(expiry_raw.strip())
        except ValueError as exc:
            raise NormalizationError(
                f"upstox master {key}: malformed expiry {expiry_raw!r}"
            ) from exc
        expiry = datetime(d.year, d.month, d.day, tzinfo=_UTC)

    strike = (
        to_float(record["strike"], field="strike")
        if record.get("strike") is not None
        else None
    )
    if strike is not None and strike <= 0:
        strike = None  # non-derivative sentinel

    return Instrument(
        instrument_token=key,
        # Master records may carry either the plain exchange ("NSE", JSON
        # files) or the segment form ("NSE_FO", CSV columns) — normalize
        # both to the bare exchange via prefix split.
        exchange=exchange_from_segment(
            str(record.get("exchange") or key.partition("|")[0])
        ),
        tradingsymbol=symbol.strip(),
        name=record.get("name"),
        instrument_type=record.get("instrument_type"),
        tick_size=(
            to_float(record["tick_size"], field="tick_size")
            if record.get("tick_size") is not None
            else None
        ),
        lot_size=(
            to_int(record["lot_size"], field="lot_size")
            if record.get("lot_size") is not None
            else None
        ),
        expiry=expiry,
        strike=strike,
    )
