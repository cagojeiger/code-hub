"""Logging configuration for code-hub.

Provides consistent JSON structured logging across all modules.
Supports Request ID context for request tracing.

Output format (JSON):
    {"timestamp": "2025-01-01T12:00:00.000Z", "level": "INFO", "module": "app.main", "message": "..."}
"""

import logging
import sys
from contextvars import ContextVar
from typing import Literal

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
        log_record: dict,
        record: logging.LogRecord,
        message_dict: dict,
    ) -> None:
        """Add custom fields to log record."""
        super().add_fields(log_record, record, message_dict)

        # Rename fields for consistency
        log_record["timestamp"] = self.formatTime(record, self.datefmt)
        log_record["level"] = record.levelname
        log_record["module"] = record.name

        # Add request_id if available
        request_id = request_id_ctx.get()
        if request_id:
            log_record["request_id"] = request_id

        # Remove redundant fields
        log_record.pop("levelname", None)
        log_record.pop("name", None)


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    json_format: bool = True,
) -> None:
    """Configure logging for the application.

    Sets up root logger with JSON structured format including:
    - timestamp (ISO format)
    - level (DEBUG/INFO/WARNING/ERROR)
    - module name
    - message
    - request_id (when available)

    Args:
        level: Logging level (default: INFO)
        json_format: Use JSON format (default: True). Set False for human-readable dev logs.

    Example:
        >>> setup_logging("DEBUG")
        >>> logger = logging.getLogger(__name__)
        >>> logger.info("Application started")
        {"timestamp": "2025-01-01 12:00:00", "level": "INFO", "module": "app.main", "message": "Application started"}
    """
    root_logger = logging.getLogger()

    # Clear existing handlers to avoid duplicate logs
    root_logger.handlers.clear()

    # Set log level
    root_logger.setLevel(getattr(logging, level))

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level))

    if json_format:
        # JSON format for production
        formatter = CodeHubJsonFormatter(
            fmt="%(timestamp)s %(level)s %(module)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    else:
        # Human-readable format for development
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
