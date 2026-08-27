"""
Canonical alert.triggered event payload — shared domain module.

Single source of truth for the canonical ``alert.triggered`` payload shape
used by BOTH the generic alert evaluator (core/alerts.py) and the market
alert engine (app/alerts.py). No MCP tool, broker, or SSE dependencies.

Canonical shape (frozen in MCP-2B.2):

    {
      "version": 1,
      "alert_family": "generic" | "market",
      "alert_id": <generic UUID str | market int>,
      "consumer_id": <str | null>,
      "triggered_at": <ISO-8601 UTC>,
      "source": "alert_engine",
      "condition": {"field": ..., "operator": ..., "threshold": ...},
      "observed": {"value": ..., "matched_event_id"?: ..., ...},
      "instrument": {"exchange": ..., "instrument_token": ...,
                     "tradingsymbol": ...} | null,
      "one_shot": <bool>,
      "metadata": <dict>,
    }

Required fields: version, alert_family, alert_id, triggered_at, condition,
observed. Everything else is optional-by-null.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Stable source-instance identifier owned by the alert engine. The trigger
# event is produced BY the alert engine, not by the matched source.
ALERT_ENGINE_SOURCE = "alert_engine"

# Canonical payload version (frozen in MCP-2B.2).
ALERT_TRIGGERED_VERSION = 1


def build_alert_triggered_data(
    *,
    alert_family: str,
    alert_id: Any,
    consumer_id: str | None,
    condition: dict[str, Any],
    observed: dict[str, Any],
    instrument: dict[str, Any] | None,
    one_shot: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical alert.triggered event ``data`` payload.

    Both alert engines call this single builder so the payload shape can
    never drift between the generic and market paths. ``triggered_at`` is
    always the current UTC time in ISO-8601 format (timezone-aware).
    """
    return {
        "version": ALERT_TRIGGERED_VERSION,
        "alert_family": alert_family,
        "alert_id": alert_id,
        "consumer_id": consumer_id,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "source": ALERT_ENGINE_SOURCE,
        "condition": condition,
        "observed": observed,
        "instrument": instrument,
        "one_shot": one_shot,
        "metadata": metadata or {},
    }