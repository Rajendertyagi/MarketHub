"""
Event tools: event_list.

Note: event_publish was removed from the public MCP registry in MCP-2B.3D.
The internal generate_event() helper remains available for tests/internal use.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core import events
from mcp_server.contract import TOOL_EVENT_PUBLISH, TOOL_EVENT_LIST


def register_event_tools(mcp, services, **kwargs) -> None:
    """Register event-related tools.

    Note: event_publish is intentionally NOT registered. The internal
    generate_event() helper below remains available for tests and
    internal development that need to seed events directly.
    """

    # --- Internal helper (unregistered — used by tests via core.events) ------
    # Kept as module-level function per MCP-2B.3D spec:
    # "leave internal helper/module code if tests/internal development still need it"

    async def generate_event(
        event_type: str,
        source: str = "manual-test",
        data: dict[str, Any] | None = None,
        persistent: bool = False,
        routing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Publish an event into the server's canonical pipeline.

        This is an INTERNAL helper — NOT registered as an MCP tool since
        MCP-2B.3D. It calls the same publish_event() that sources and the
        alert engine use. Tests import and call this directly against the
        shared EventStore instead of going through the MCP boundary.
        """
        event = await events.publish_event(
            event_type=event_type,
            source=source,
            data=data or {},
            persistent=persistent,
            routing=routing,
            store=services.store,
            bus=services.subscription_bus,
        )
        return {
            "status": "published",
            "event": event,
        }

    @mcp.tool(name=TOOL_EVENT_LIST)
    def list_events(limit: int = 10) -> dict[str, Any]:
        """
        List recent events from the in-memory history buffer.

        This is a diagnostics/observational journal of events that passed
        through the server — it is NOT a durable replay source. It does not
        reflect per-consumer delivery state, acknowledgements, or checkpoints.
        For durable, per-consumer replay use consumer_event_pending_list.

        limit: Maximum number of events to return (default 10, max 50).
        """
        event_list = events.get_event_history(limit=limit)
        return {
            "total_events": events.get_event_count(),
            "returned": len(event_list),
            "events": event_list,
        }
