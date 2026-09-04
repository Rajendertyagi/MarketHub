"""Custom logging handler that feeds the WebUI log buffer and SSE stream.

Attaches to Python's root logger (or any logger) and captures
structured records for live WebUI viewing.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from core.log_buffer import LogBuffer, LogRecord
from core.log_redaction import redact_message, redact_record, redact_value


class WebUILogHandler(logging.Handler):
    """logging.Handler that feeds LogBuffer + optional SSE broker.

    Records are:
    1. Structured into LogRecord with useful metadata
    2. Redacted for security
    3. Appended to the LogBuffer
    4. Broadcast via SSE if a broker is attached
    """

    def __init__(self, buffer: LogBuffer, broker: Any = None) -> None:
        super().__init__(level=logging.DEBUG)
        self._buffer = buffer
        self._broker = broker

    def set_broker(self, broker: Any) -> None:
        """Late-bind the SSE broker (avoids circular import at init)."""
        self._broker = broker

    def set_buffer(self, buffer: LogBuffer) -> None:
        """Rebind to a different buffer (handler reattachment support)."""
        self._buffer = buffer

    def rebind(self, buffer: LogBuffer, broker: Any = None) -> None:
        """Rebind buffer (and optionally broker) after app re-initialization."""
        self._buffer = buffer
        if broker is not None:
            self._broker = broker

    def emit(self, record: logging.LogRecord) -> None:
        """Process a single log record."""
        try:
            structured = self._structure(record)
            self._buffer.append(structured)
            if self._broker is not None:
                # Redact before SSE broadcast — defense in depth
                redacted = redact_record(structured.to_dict())
                self._broadcast_dict(redacted)
        except Exception:
            # Never let handler errors propagate to the application
            self.handleError(record)

    def _structure(self, record: logging.LogRecord) -> LogRecord:
        """Convert a stdlib LogRecord into our structured LogRecord."""
        # Format the message (handles % formatting lazily)
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)

        # Redact sensitive content
        message = redact_message(message)

        # Extract structured metadata from 'extra' dict, then redact it
        # BEFORE it enters the buffer — raw secrets must never be stored.
        extra = getattr(record, "log_extra", None) or {}
        try:
            extra = redact_value(extra) if extra else {}
        except Exception:
            extra = {}
        exception_text = None
        if record.exc_info and record.exc_info[1] is not None:
            exception_text = "".join(
                traceback.format_exception(*record.exc_info)
            )
            exception_text = redact_message(exception_text)
        elif record.exc_text:
            exception_text = redact_message(str(record.exc_text))

        return LogRecord(
            timestamp=self._fmt_ts(record.created),
            level=record.levelname,
            logger=record.name or "root",
            message=message,
            event=extra.get("event"),
            request_id=extra.get("request_id"),
            consumer_id=extra.get("consumer_id"),
            alert_id=extra.get("alert_id"),
            event_id=extra.get("event_id"),
            broker=extra.get("broker"),
            exception=exception_text,
            extra=extra if extra else None,
        )

    def _broadcast_dict(self, record_dict: dict) -> None:
        """Send redacted record dict to SSE broker (fire-and-forget).

        Thread-safe delivery: the record is handed to the loop thread via
        ``call_soon_threadsafe`` when an event loop is running; otherwise
        a direct (internally locked) broadcast is attempted.  The broker
        itself is safe to call from any thread (see EventBroker).
        Failures are swallowed — logging must never break the app.
        """
        import asyncio
        import json
        try:
            data = json.dumps(record_dict, default=str)
        except Exception:
            return
        line = f"data: {data}\n\n"
        broker = self._broker
        if broker is None:
            return
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(broker.broadcast, line)
            else:
                broker.broadcast(line)
        except Exception:
            pass

    @staticmethod
    def _fmt_ts(epoch: float) -> str:
        """Format epoch seconds to ISO-8601 UTC string."""
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
