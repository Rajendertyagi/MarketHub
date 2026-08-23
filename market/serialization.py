"""
Canonical serialization: frozen market models -> JSON-safe dicts.

Single owner for the future API / SSE / MCP layers (Phase C+), so all three
consume identical shapes instead of growing divergent serializers.

Locked rules:
  * canonical model field names only — no provider aliases
  * datetimes -> ISO-8601 UTC strings (models already enforce awareness)
  * tuples -> arrays
  * None preserved verbatim
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields, is_dataclass
from datetime import datetime, timezone
from typing import Any

from market.models import Depth, DepthLevel, Instrument, Quote

__all__ = [
    "instrument_to_dict",
    "quote_to_dict",
    "depth_level_to_dict",
    "depth_to_dict",
]

_UTC = timezone.utc


def _to_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(_UTC).isoformat()
    if isinstance(value, tuple):
        return [_to_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value) and not isinstance(value, type):
        return _model_to_dict(value)
    raise TypeError(f"serialization: unsupported value type {type(value).__name__}")


def _model_to_dict(model: Any) -> dict[str, Any]:
    if not is_dataclass(model) or isinstance(model, type):
        raise TypeError(
            f"serialization: expected a model instance, got {type(model).__name__}"
        )
    return {
        f.name: _to_json_value(getattr(model, f.name))
        for f in dataclass_fields(model)
    }


def instrument_to_dict(model: Instrument) -> dict[str, Any]:
    """Canonical JSON-safe dict for an Instrument."""
    return _model_to_dict(model)


def quote_to_dict(model: Quote) -> dict[str, Any]:
    """Canonical JSON-safe dict for a Quote."""
    return _model_to_dict(model)


def depth_level_to_dict(model: DepthLevel) -> dict[str, Any]:
    """Canonical JSON-safe dict for one DepthLevel."""
    return _model_to_dict(model)


def depth_to_dict(model: Depth) -> dict[str, Any]:
    """Canonical JSON-safe dict for a Depth (bids/asks become arrays)."""
    return _model_to_dict(model)
