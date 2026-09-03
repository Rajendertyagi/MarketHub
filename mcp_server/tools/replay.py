"""
Replay tools: consumer_event_pending_list, consumer_event_acknowledge, consumer_checkpoint_get.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from core.errors import ValidationError
from pydantic import Field, StrictInt
from mcp_server.contract import (
    TOOL_CONSUMER_EVENT_PENDING_LIST,
    TOOL_CONSUMER_EVENT_ACKNOWLEDGE,
    TOOL_CONSUMER_CHECKPOINT_GET,
)
from mcp_server.registry import get_tool_description
from app.lifecycle import run_with_timeout


def register_replay_tools(mcp, services, **kwargs) -> None:
    """Register replay/checkpoint-related tools."""

    @mcp.tool(name=TOOL_CONSUMER_EVENT_PENDING_LIST, description=get_tool_description(TOOL_CONSUMER_EVENT_PENDING_LIST))
    async def get_pending_events(
        consumer_id: str,
        limit: int = 50,
        after_sequence: Annotated[StrictInt, Field(ge=0)] | None = None,
    ) -> dict[str, Any]:
        """
        Replay pending (unacknowledged) persistent events for a consumer.

        This is the canonical durable replay tool. Events are returned in
        sequence order starting from the consumer's durable checkpoint, or
        from an explicit after_sequence for pagination. Replay does NOT
        acknowledge events and does NOT advance the consumer's checkpoint —
        call consumer_event_acknowledge only after the event has been
        successfully processed (at-least-once delivery).
        """
        consumer_id = consumer_id.strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty after trimming")

        # Validate after_sequence: None or non-negative integer (reject bool, float, string, negative)
        if after_sequence is not None:
            if isinstance(after_sequence, bool) or not isinstance(after_sequence, int):
                raise ValidationError("after_sequence must be a non-negative integer or null")
            if after_sequence < 0:
                raise ValidationError("after_sequence must be a non-negative integer or null")

        # Validate limit: positive integer (reject bool, float, string, zero, negative)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValidationError("limit must be a positive integer")

        effective_limit = min(limit, services.replay_cfg["max_limit"])
        return await run_with_timeout(
            asyncio.to_thread(
                services.store.replay_events,
                consumer_id=consumer_id,
                limit=effective_limit,
                after_sequence=after_sequence,
            ),
            operation=f"get_pending_events({consumer_id})",
            timeout_seconds=services.timeouts["database_seconds"],
        )

    @mcp.tool(name=TOOL_CONSUMER_EVENT_ACKNOWLEDGE, description=get_tool_description(TOOL_CONSUMER_EVENT_ACKNOWLEDGE))
    async def acknowledge_event(
        consumer_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        """
        Acknowledge that a consumer has successfully processed a persistent event.

        Call this only after the event has been fully processed — acknowledgement
        is the commit point of at-least-once delivery. It is idempotent: repeated
        calls succeed silently and the first acknowledgement timestamp is preserved.

        After acknowledgement, the server advances the consumer's durable
        checkpoint monotonically to the highest safe sequence.
        """
        consumer_id = consumer_id.strip()
        event_id = event_id.strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty after trimming")
        if not event_id:
            raise ValidationError("event_id must not be empty after trimming")

        acknowledged = await run_with_timeout(
            asyncio.to_thread(services.store.acknowledge_event, consumer_id=consumer_id, event_id=event_id),
            operation=f"acknowledge_event({consumer_id},{event_id[:8]}...)",
            timeout_seconds=services.timeouts["database_seconds"],
        )

        # Attempt checkpoint advancement
        new_cp = await run_with_timeout(
            asyncio.to_thread(services.store.advance_checkpoint, consumer_id),
            operation=f"advance_checkpoint({consumer_id})",
            timeout_seconds=services.timeouts["database_seconds"],
        )

        return {
            "status": "acknowledged",
            "consumer_id": consumer_id,
            "event_id": event_id,
            "checkpoint": new_cp,
        }

    @mcp.tool(name=TOOL_CONSUMER_CHECKPOINT_GET, description=get_tool_description(TOOL_CONSUMER_CHECKPOINT_GET))
    async def get_consumer_checkpoint(consumer_id: str) -> dict[str, Any]:
        """Get the current durable checkpoint position for a consumer.

        Returns the consumer's checkpoint sequence and the persisted timestamp
        of the last checkpoint write (registration or advance). This reports
        the durable replay position — it does not modify anything.
        """
        consumer_id = consumer_id.strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty after trimming")

        info = await asyncio.to_thread(services.store.get_checkpoint_info, consumer_id)
        return {
            "consumer_id": consumer_id,
            "checkpoint": info["checkpoint"],
            "updated_at": info["updated_at"],
        }
