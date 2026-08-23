"""
Shared normalization primitives for MarketHub provider normalizers.

Pure stdlib. Owns:
  * normalization error types (NormalizationError hierarchy)
  * timestamp parsing -> UTC-aware datetime (locked policy #4)
  * numeric coercion  -> float/int | None (locked rules: malformed values
    never become 0, bool is rejected even though Python treats it as int,
    NaN/Infinity are rejected)
  * the canonical quote-field map contract used by WS partial-update
    normalizers — a provider-neutral patch representation that explicitly
    tracks presence, so the frozen Quote model is never abused as a
    partial-update DTO

Field-map presence contract (B1 patch representation):
  * a key PRESENT in the map means the provider reported that field;
    its value is the parsed canonical value (None only when the provider
    explicitly sent null)
  * a key ABSENT means "not reported" — consumers must preserve prior state
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

__all__ = [
    "NormalizationError",
    "TimestampError",
    "NumericError",
    "parse_timestamp",
    "to_float",
    "to_int",
    "QUOTE_FIELD_NAMES",
    "check_quote_fields",
    "set_reported",
    "apply_derived_change",
]

_UTC = timezone.utc


class NormalizationError(ValueError):
    """Base class for payload-normalization failures."""


class TimestampError(NormalizationError):
    """A timestamp could not be parsed into a UTC-aware datetime."""


class NumericError(NormalizationError):
    """A reported numeric was malformed, non-finite, or of an invalid type."""


# ---------------------------------------------------------------------------
# Timestamps (locked policy #4)
# ---------------------------------------------------------------------------


def parse_timestamp(value: Any, *, unit: str, field: str = "timestamp") -> datetime:
    """Parse a provider timestamp into a UTC-normalized aware datetime.

    unit="iso" : ISO-8601 string with offset (trailing ``Z`` accepted);
                 a datetime instance is also accepted.
    unit="ms"  : epoch milliseconds (int/float or digit string).
    unit="s"   : epoch seconds (int/float or digit string).

    Naive datetimes are rejected explicitly. Malformed values raise
    TimestampError — they are never silently coerced.
    """
    if value is None:
        raise TimestampError(f"{field}: timestamp value is missing")
    try:
        if unit == "iso":
            if isinstance(value, datetime):
                dt = value
            elif isinstance(value, str):
                text = value.strip()
                if text.endswith(("Z", "z")):
                    text = text[:-1] + "+00:00"
                dt = datetime.fromisoformat(text)
            else:
                raise TimestampError(
                    f"{field}: expected ISO-8601 string, got {type(value).__name__}"
                )
            if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                raise TimestampError(
                    f"{field}: naive ISO-8601 timestamp rejected: {value!r}"
                )
        elif unit in ("ms", "s"):
            if isinstance(value, bool):
                raise TimestampError(f"{field}: bool is not an epoch value")
            scale = 1000.0 if unit == "ms" else 1.0
            if isinstance(value, (int, float)):
                if not math.isfinite(value):
                    raise TimestampError(f"{field}: non-finite epoch value")
                dt = datetime.fromtimestamp(value / scale, tz=_UTC)
            elif isinstance(value, str):
                text = value.strip()
                if not text.isdigit():
                    raise TimestampError(
                        f"{field}: non-numeric epoch string {value!r}"
                    )
                dt = datetime.fromtimestamp(int(text) / scale, tz=_UTC)
            else:
                raise TimestampError(
                    f"{field}: unsupported epoch value type {type(value).__name__}"
                )
        else:
            raise TimestampError(f"{field}: unknown timestamp unit {unit!r}")
    except TimestampError:
        raise
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        raise TimestampError(
            f"{field}: unparseable {unit} timestamp {value!r}"
        ) from exc
    return dt.astimezone(_UTC)


# ---------------------------------------------------------------------------
# Numerics (locked rules #2/#3/#4)
# ---------------------------------------------------------------------------


def to_float(value: Any, *, field: str) -> float | None:
    """Coerce a reported provider numeric to float.

    None passes through as None (not reported). Bools are rejected even
    though Python treats them as ints; NaN/Infinity are rejected; malformed
    strings are never silently zeroed. Blank/whitespace strings count as
    not-reported (None).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise NumericError(f"{field}: bool is not a valid numeric: {value!r}")
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            num = float(text)
        except ValueError:
            raise NumericError(
                f"{field}: malformed numeric string: {value!r}"
            ) from None
    else:
        raise NumericError(f"{field}: unsupported numeric type {type(value).__name__}")
    if not math.isfinite(num):
        raise NumericError(f"{field}: non-finite numeric rejected: {value!r}")
    return num


def to_int(value: Any, *, field: str) -> int | None:
    """Coerce a reported provider numeric to int.

    Accepts ints, integral floats (250, 250.0), and numeric strings
    ("250", "250.0"). Non-integral values, bools, non-finite floats and
    malformed strings raise NumericError. Blank strings count as
    not-reported (None).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise NumericError(f"{field}: bool is not a valid integer: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NumericError(f"{field}: non-finite numeric rejected: {value!r}")
        if not value.is_integer():
            raise NumericError(
                f"{field}: non-integral value for integer field: {value!r}"
            )
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            num = float(text)
        except ValueError:
            raise NumericError(
                f"{field}: malformed numeric string: {value!r}"
            ) from None
        if not math.isfinite(num) or not num.is_integer():
            raise NumericError(
                f"{field}: non-integral value for integer field: {value!r}"
            )
        return int(num)
    raise NumericError(f"{field}: unsupported numeric type {type(value).__name__}")


# ---------------------------------------------------------------------------
# Canonical quote-field map contract (presence-explicit patch representation)
# ---------------------------------------------------------------------------

# Canonical Quote field names allowed in normalized field maps.
# Mirrors market.models.Quote exactly — provider aliases must die in the
# provider modules and never appear under these keys' names.
QUOTE_FIELD_NAMES = frozenset({
    "instrument_token", "exchange", "tradingsymbol",
    "received_ts", "exchange_ts",
    "ltp", "open", "high", "low", "close",
    "volume", "change", "change_percent",
    "best_bid", "best_ask",
    "open_interest", "avg_trade_price", "last_traded_qty",
    "total_buy_qty", "total_sell_qty",
})


def check_quote_fields(fields: dict[str, Any]) -> None:
    """Assert a normalized field map uses only canonical Quote field names."""
    unknown = set(fields) - QUOTE_FIELD_NAMES
    if unknown:
        raise NormalizationError(f"unknown canonical quote fields: {sorted(unknown)}")


def set_reported(
    fields: dict[str, Any],
    payload: dict[str, Any],
    src_key: str,
    dst_key: str,
    converter: Callable[..., Any],
) -> None:
    """Copy ``payload[src_key]`` into ``fields[dst_key]``, preserving presence.

    Locked rule #1 — absent vs present-with-null are distinguished:
      * src_key absent from payload  -> fields[dst_key] stays ABSENT
      * present with explicit null   -> fields[dst_key] = None
      * present with a value         -> fields[dst_key] = converter(value)
    """
    if src_key not in payload:
        return
    raw = payload[src_key]
    fields[dst_key] = None if raw is None else converter(raw, field=dst_key)


def apply_derived_change(fields: dict[str, Any]) -> None:
    """Locked derived-change policy (#2 / design §O), on a field map.

    Explicit provider change/change_percent always win (locked rule #6).
    Missing pieces are derived from ltp + close (previous close) only when
    both are valid and close is non-zero. Presence ("not in") decides, so an
    explicitly-null field blocks derivation for that field.
    """
    ltp = fields.get("ltp")
    close = fields.get("close")
    if ltp is None or close is None or close == 0:
        return
    if "change" not in fields:
        fields["change"] = ltp - close
    if "change_percent" not in fields:
        fields["change_percent"] = ((ltp - close) / close) * 100.0
