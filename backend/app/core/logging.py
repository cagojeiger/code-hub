"""Logging configuration for code-hub.

Provides consistent JSON structured logging across all modules.
Supports Request ID context for request tracing.
"""

import logging
import sys
from contextvars import ContextVar
from typing import Any, Literal

from pythonjsonlogger.json import JsonFormatter as BaseJsonFormatter

# Context variable for request ID (set by middleware)
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


class CodeHubJsonFormatter(BaseJsonFormatter):
    """Custom JSON formatter with consistent field names.

    Adds:
    - timestamp (ISO format)
    - level
    - module (logger name)
    - message
    - request_id (if available in context)
    """

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        """Add custom fields to log record."""
        super().add_fields(log_record, record, message_dict)

        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["module"] = record.name

        request_id = request_id_ctx.get()
        if request_id:
            log_record["request_id"] = request_id

        log_record.pop("levelname", None)
        log_record.pop("name", None)


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    json_format: bool = True,
) -> None:
    """Configure logging for the application."""
    root_logger = logging.getLogger()

    # Clear existing handlers to avoid duplicate logs
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, level))

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level))

    formatter: logging.Formatter
    if json_format:
        formatter = CodeHubJsonFormatter(
            fmt="%(timestamp)s %(level)s %(module)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_request_id() -> str | None:
    """Get current request ID from context.

    Returns:
        Request ID string or None if not in request context.
    """
    return request_id_ctx.get()


def set_request_id(request_id: str) -> None:
    """Set request ID in context.

    Args:
        request_id: Request ID to set.
    """
    request_id_ctx.set(request_id)
