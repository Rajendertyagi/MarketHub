"""
AI Alert Observability routes.

Read-only REST endpoints exposing condition-alert lifecycle data
for the WebUI.  Uses existing SQLite tables as source of truth —
no new persistence, no secrets, no write operations.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def _condition_summary(condition_json: str) -> str:
    """Human-readable summary of a condition tree."""
    try:
        c = json.loads(condition_json) if isinstance(condition_json, str) else condition_json
    except (json.JSONDecodeError, TypeError):
        return str(condition_json)[:80]

    if not isinstance(c, dict):
        return str(c)[:80]

    version = c.get("condition_version", 1)
    if version == 1:
        metric = c.get("metric", "?")
        op = c.get("operator", "?")
        val = c.get("value", "?")
        instrument = c.get("instrument", {})
        symbol = instrument.get("canonical_id", "?")
        return f"{symbol} {metric} {op} {val}"

    # v2 group node
    logic = c.get("logic", "ALL")
    children = c.get("conditions", [])
    parts = [_condition_summary(ch) for ch in children[:3]]
    joined = f" {logic} ".join(parts)
    if len(children) > 3:
        joined += f" +{len(children) - 3} more"
    return joined


def _instrument_label(condition_json: str) -> str:
    """Extract instrument label from condition tree."""
    try:
        c = json.loads(condition_json) if isinstance(condition_json, str) else condition_json
    except (json.JSONDecodeError, TypeError):
        return "?"
    if not isinstance(c, dict):
        return "?"
    instrument = c.get("instrument", {})
    return instrument.get("canonical_id", "?")


def build_ai_alert_routes(store: Any, mcp_server: Any = None) -> list[Route]:
    """Build read-only AI alert observability routes."""

    async def _list_condition_alerts(request: Request) -> Response:  # noqa: ARG001
        """GET /api/ai-alerts — List all condition alerts with runtime state."""
        conn = store._open(store._db_path)
        try:
            rows = conn.execute("""
                SELECT
                    ca.alert_id,
                    ca.consumer_id,
                    ca.name,
                    ca.enabled,
                    ca.trigger_mode,
                    ca.condition_json,
                    ca.canonical_instrument_id,
                    ca.created_at,
                    ca.updated_at,
                    ca.last_triggered_at,
                    ca.trigger_count,
                    ca.metadata_json,
                    crs.last_result,
                    crs.crossing_side,
                    crs.updated_at AS state_updated_at
                FROM condition_alerts ca
                LEFT JOIN condition_runtime_state crs
                    ON crs.alert_id = ca.alert_id
                ORDER BY ca.created_at DESC
            """).fetchall()

            alerts = []
            for row in rows:
                alerts.append({
                    "alert_id": row[0],
                    "consumer_id": row[1],
                    "name": row[2],
                    "enabled": bool(row[3]),
                    "trigger_mode": row[4],
                    "condition_summary": _condition_summary(row[5]),
                    "condition_json": json.loads(row[5]) if row[5] else None,
                    "instrument": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                    "last_triggered_at": row[9],
                    "trigger_count": row[10],
                    "metadata": json.loads(row[11]) if row[11] else None,
                    "current_state": row[12] or "unknown",
                    "crossing_side": row[13] or "unknown",
                    "state_updated_at": row[14],
                })

            return _json({"alerts": alerts, "count": len(alerts)})
        finally:
            conn.close()

    async def _triggered_events(request: Request) -> Response:
        """GET /api/ai-alerts/events — Triggered events with delivery status."""
        limit = int(request.query_params.get("limit", "200"))
        conn = store._open(store._db_path)
        try:
            rows = conn.execute("""
                SELECT
                    pe.id,
                    pe.sequence,
                    pe.type,
                    pe.source,
                    pe.timestamp,
                    pe.data,
                    pe.created_at,
                    ces.consumer_id,
                    ces.delivered_at,
                    ces.acknowledged_at
                FROM persistent_events pe
                LEFT JOIN consumer_event_state ces
                    ON ces.event_id = pe.id
                WHERE pe.type = 'alert.triggered'
                ORDER BY pe.sequence DESC
                LIMIT ?
            """, (limit,)).fetchall()

            events = []
            for row in rows:
                data = json.loads(row[5]) if row[5] else {}
                alert_id = data.get("alert_id", data.get("alert_family", "?"))
                consumer_id = row[7] or data.get("consumer_id", "?")
                condition = data.get("condition", {})
                instrument = data.get("instrument", {})

                # Determine delivery state
                delivered = row[8] is not None
                acknowledged = row[9] is not None
                if acknowledged:
                    delivery_state = "acknowledged"
                elif delivered:
                    delivery_state = "pending"
                else:
                    delivery_state = "persisted"

                events.append({
                    "event_id": row[0],
                    "sequence": row[1],
                    "alert_id": alert_id,
                    "source": row[3],
                    "trigger_time": row[4],
                    "created_at": row[6],
                    "consumer_id": consumer_id,
                    "condition_summary": _condition_summary(json.dumps(condition)) if condition else "?",
                    "instrument": instrument.get("canonical_id", "?"),
                    "delivery_state": delivery_state,
                    "delivered_at": row[8],
                    "acknowledged_at": row[9],
                })

            return _json({"events": events, "count": len(events)})
        finally:
            conn.close()

    async def _consumer_status(request: Request) -> Response:  # noqa: ARG001
        """GET /api/ai-alerts/consumers — Per-consumer delivery status."""
        conn = store._open(store._db_path)
        try:
            consumers_rows = conn.execute(
                "SELECT consumer_id, created_at FROM consumers ORDER BY created_at"
            ).fetchall()

            consumers = []
            for crow in consumers_rows:
                consumer_id = crow[0]

                # Pending count
                pending = conn.execute("""
                    SELECT COUNT(*) FROM consumer_event_state
                    WHERE consumer_id = ? AND acknowledged_at IS NULL
                """, (consumer_id,)).fetchone()[0]

                # Last triggered event
                last_event = conn.execute("""
                    SELECT pe.id, pe.timestamp, pe.data
                    FROM persistent_events pe
                    JOIN consumer_event_state ces ON ces.event_id = pe.id
                    WHERE ces.consumer_id = ? AND pe.type = 'alert.triggered'
                    ORDER BY pe.sequence DESC LIMIT 1
                """, (consumer_id,)).fetchone()

                last_triggered = None
                if last_event:
                    data = json.loads(last_event[2]) if last_event[2] else {}
                    last_triggered = {
                        "event_id": last_event[0],
                        "trigger_time": last_event[1],
                        "alert_id": data.get("alert_id", "?"),
                    }

                # Last checkpoint
                cp_row = conn.execute("""
                    SELECT last_sequence, updated_at
                    FROM consumer_checkpoints WHERE consumer_id = ?
                """, (consumer_id,)).fetchone()
                last_checkpoint = None
                if cp_row:
                    last_checkpoint = {
                        "last_sequence": cp_row[0],
                        "updated_at": cp_row[1],
                    }

                # Unacknowledged alert events
                unacked = conn.execute("""
                    SELECT COUNT(*) FROM consumer_event_state
                    WHERE consumer_id = ? AND acknowledged_at IS NULL
                """, (consumer_id,)).fetchone()[0]

                consumers.append({
                    "consumer_id": consumer_id,
                    "created_at": crow[1],
                    "pending_count": pending,
                    "last_triggered": last_triggered,
                    "last_checkpoint": last_checkpoint,
                    "unacknowledged_count": unacked,
                })

            return _json({"consumers": consumers, "count": len(consumers)})
        finally:
            conn.close()

    async def _list_mcp_tools(request: Request) -> Response:  # noqa: ARG001
        """GET /api/mcp/tools — List all registered MCP tools."""
        if mcp_server is None:
            return _json({"tools": [], "count": 0})
        try:
            tools = await mcp_server.list_tools()
            result = []
            for t in tools:
                result.append({
                    "name": t.name,
                    "title": t.title or t.name,
                    "description": t.description or "",
                    "input_schema": t.input_schema,
                })
            return _json({"tools": result, "count": len(result)})
        except Exception as exc:
            logger.warning("Failed to list MCP tools: %s", exc)
            return _json({"tools": [], "count": 0, "error": str(exc)})

    return [
        Route("/api/ai-alerts", endpoint=_list_condition_alerts, methods=["GET"]),
        Route("/api/ai-alerts/events", endpoint=_triggered_events, methods=["GET"]),
        Route("/api/ai-alerts/consumers", endpoint=_consumer_status, methods=["GET"]),
        Route("/api/mcp/tools", endpoint=_list_mcp_tools, methods=["GET"]),
    ]
