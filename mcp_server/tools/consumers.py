"""
Consumer tools: consumer_register, consumer_topic_add.
"""

from __future__ import annotations

from typing import Any

from core.errors import ValidationError, StorageError, ConsumerNotFoundError
from mcp_server.contract import (
    TOOL_CONSUMER_REGISTER,
    TOOL_CONSUMER_TOPIC_ADD,
)
from mcp_server.registry import get_tool_description


def register_consumer_tools(mcp, services, **kwargs) -> None:
    """Register consumer-related tools."""

    @mcp.tool(name=TOOL_CONSUMER_REGISTER, description=get_tool_description(TOOL_CONSUMER_REGISTER))
    def register_consumer(consumer_id: str) -> dict[str, Any]:
        """Register a consumer identity. Idempotent — safe to call repeatedly."""
        consumer_id = consumer_id.strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty after trimming")
        try:
            services.store.register_consumer(consumer_id)
        except Exception as exc:
            raise StorageError("consumer registration failed", exc) from exc
        return {"status": "registered", "consumer_id": consumer_id}

    @mcp.tool(name=TOOL_CONSUMER_TOPIC_ADD, description=get_tool_description(TOOL_CONSUMER_TOPIC_ADD))
    def add_consumer_topic(consumer_id: str, topic: str) -> dict[str, Any]:
        """Assign a topic to a consumer for topic-based routing."""
        consumer_id = consumer_id.strip()
        topic = topic.strip()
        if not consumer_id:
            raise ValidationError("consumer_id must not be empty after trimming")
        if not topic:
            raise ValidationError("topic must not be empty after trimming")
        try:
            services.store.add_topic(consumer_id, topic)
        except ConsumerNotFoundError:
            raise
        except Exception as exc:
            raise StorageError("topic addition failed", exc) from exc
        return {"status": "added", "consumer_id": consumer_id, "topic": topic}
