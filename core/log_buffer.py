"""Bounded in-memory log buffer for WebUI live viewing.

Stores structured log records in a thread-safe deque with a fixed
maximum size.  No SQLite persistence — this is purely in-memory.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class LogRecord:
    """Structured log record for WebUI consumption."""
    timestamp: str
    level: str
    logger: str
    message: str
    event: str | None = None
    request_id: str | None = None
    consumer_id: str | None = None
    alert_id: str | None = None
    event_id: str | None = None
    broker: str | None = None
    exception: str | None = None
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }
        if self.event:
            d["event"] = self.event
        if self.request_id:
            d["request_id"] = self.request_id
        if self.consumer_id:
            d["consumer_id"] = self.consumer_id
        if self.alert_id:
            d["alert_id"] = self.alert_id
        if self.event_id:
            d["event_id"] = self.event_id
        if self.broker:
            d["broker"] = self.broker
        if self.exception:
            d["exception"] = self.exception
        if self.extra:
            d["extra"] = self.extra
        return d


class LogBuffer:
    """Thread-safe bounded log buffer.

    Records are stored in a deque with a fixed maximum size.
    Older records are silently dropped when the buffer is full.
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._max_size = max_size
        self._buffer: deque[LogRecord] = deque(maxlen=max_size)
        self._lock = threading.Lock()

    def append(self, record: LogRecord) -> None:
        """Append a record. Drops oldest if at capacity."""
        with self._lock:
            self._buffer.append(record)

    def snapshot(self, limit: int = 200,
                 level: str | None = None,
                 logger_pattern: str | None = None,
                 search: str | None = None,
                 consumer_id: str | None = None,
                 alert_id: str | None = None,
                 request_id: str | None = None) -> list[dict[str, Any]]:
        """Return up to *limit* recent records matching filters.

        Filters are applied in-place on the snapshot; performance is
        acceptable because the buffer is bounded (max ~1000 records).
        """
        with self._lock:
            records = list(self._buffer)

        # Apply filters (most-selective first)
        if level:
            level_upper = level.upper()
            records = [r for r in records if r.level == level_upper]
        if logger_pattern:
            lp = logger_pattern.lower()
            records = [r for r in records if lp in r.logger.lower()]
        if search:
            s = search.lower()
            records = [r for r in records if s in r.message.lower()]
        if consumer_id:
            records = [r for r in records if r.consumer_id == consumer_id]
        if alert_id:
            records = [r for r in records if r.alert_id == alert_id]
        if request_id:
            records = [r for r in records if r.request_id == request_id]

        # Return most recent first, bounded
        records = records[-limit:]
        records.reverse()
        return [r.to_dict() for r in records]

    def clear(self) -> None:
        """Clear all records (called from WebUI 'Clear View')."""
        with self._lock:
            self._buffer.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)
