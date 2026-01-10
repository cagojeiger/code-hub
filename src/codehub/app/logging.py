"""JSON logging configuration with distributed tracing and rate limiting."""

import logging
import sys
import time
from collections import defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pythonjsonlogger import jsonlogger

from codehub.app.config import get_settings

# Context variables for distributed tracing
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)
span_id_ctx: ContextVar[str | None] = ContextVar("span_id", default=None)


def get_trace_id() -> str | None:
    """Get current trace_id from context."""
    return trace_id_ctx.get()


def set_trace_id(trace_id: str | None = None) -> str:
    """Set trace_id in context, generating one if not provided.

    Args:
        trace_id: Optional trace ID to set. If None, generates a new UUID.

    Returns:
        The trace ID that was set.
    """
    tid = trace_id or str(uuid4())
    trace_id_ctx.set(tid)
    return tid


def clear_trace_context() -> None:
    """Clear trace context (call at end of request)."""
    trace_id_ctx.set(None)
    span_id_ctx.set(None)


class RateLimitFilter(logging.Filter):
    """Rate limit filter to prevent log storms.

    Limits identical log messages to a configurable rate per minute.
    ERROR logs bypass rate limiting and are always logged.

    Example:
        If rate_per_minute=100, the same log message can only appear
        100 times per minute. After that, it will be suppressed until
        the minute window passes.
    """

    def __init__(self, rate_per_minute: int = 100) -> None:
        super().__init__()
        self.rate_per_minute = rate_per_minute
        self._counts: dict[str, list[float]] = defaultdict(list)
        self._warned: set[str] = set()  # Track keys that have been warned

    def filter(self, record: logging.LogRecord) -> bool:
        # ERROR and above always pass (never rate limit errors)
        if record.levelno >= logging.ERROR:
            return True

        # Build unique key for this log location + message
        key = f"{record.name}:{record.lineno}:{record.msg}"
        now = time.time()

        # Remove timestamps older than 1 minute
        self._counts[key] = [t for t in self._counts[key] if now - t < 60]

        if len(self._counts[key]) >= self.rate_per_minute:
            # Rate limited - emit warning on first suppression
            if key not in self._warned:
                self._warned.add(key)
                record.msg = f"[RATE LIMITED] {record.msg} (max {self.rate_per_minute}/min)"
                self._counts[key].append(now)
                return True
            return False

        # Reset warning flag if we're back under limit
        if key in self._warned and len(self._counts[key]) < self.rate_per_minute // 2:
            self._warned.discard(key)

        self._counts[key].append(now)
        return True


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with schema version and trace context.

    Adds the following standard fields to all logs:
    - timestamp: ISO 8601 format with timezone
    - level: Log level name
    - logger: Logger name
    - pid: Process ID
    - schema_version: Log schema version (for backwards compatibility)
    - service: Service name
    - trace_id: Distributed trace ID (if set in context)
    - span_id: Span ID (if set in context)
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        settings = get_settings()
        self._schema_version = settings.logging.schema_version
        self._service = settings.logging.service_name

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # Standard fields
        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["pid"] = record.process
        log_record["filename"] = record.filename
        log_record["lineno"] = record.lineno
        log_record["funcName"] = record.funcName

        # Schema and service (for log analysis tools)
        log_record["schema_version"] = self._schema_version
        log_record["service"] = self._service

        # Trace context (if set)
        if trace_id := get_trace_id():
            log_record["trace_id"] = trace_id
        if span_id := span_id_ctx.get():
            log_record["span_id"] = span_id

        # Exception formatting
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Remove unwanted fields
        log_record.pop("color_message", None)


def setup_logging(level: int | None = None) -> None:
    """Configure JSON logging for the application.

    Args:
        level: Log level. If None, uses LOGGING_LEVEL from settings.
    """
    settings = get_settings()

    # Determine log level
    if level is None:
        level = getattr(logging, settings.logging.level.upper(), logging.INFO)

    formatter = CustomJsonFormatter()

    # Create handler with rate limit filter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RateLimitFilter(settings.logging.rate_limit_per_minute))

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Configure uvicorn loggers (exclude uvicorn.access - LoggingMiddleware handles it)
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = False
        uv_logger.addHandler(handler)

    # Disable uvicorn.access (LoggingMiddleware provides structured request logging)
    logging.getLogger("uvicorn.access").disabled = True

    # Suppress verbose HTTP client logs (polling noise)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
