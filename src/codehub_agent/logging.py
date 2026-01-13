"""Logging configuration for CodeHub Agent.

Supports two formats:
- text: Human-readable for local development
- json: Structured logging for production (log aggregation)
"""

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import json as jsonlogger

from codehub_agent.config import LoggingConfig


class AgentJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter with standard fields for log aggregation.

    Adds:
    - timestamp: ISO 8601 format with timezone
    - level: Log level name
    - logger: Logger name
    - service: Service identifier
    - pid: Process ID
    """

    def __init__(self, config: LoggingConfig, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._service = config.service_name

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
        log_record["service"] = self._service
        log_record["pid"] = record.process

        # Source location for debugging
        log_record["filename"] = record.filename
        log_record["lineno"] = record.lineno

        # Exception formatting
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        # Remove unwanted fields
        log_record.pop("color_message", None)


def setup_logging(config: LoggingConfig) -> None:
    """Configure logging for the application.

    Args:
        config: Logging configuration settings.
    """
    level = getattr(logging, config.level.upper(), logging.INFO)

    # Choose formatter based on config
    if config.format == "json":
        formatter = AgentJsonFormatter(config)
    else:
        # Text format for local development
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Configure uvicorn loggers
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = False
        uv_logger.addHandler(handler)

    # Disable uvicorn.access (request logging handled separately if needed)
    logging.getLogger("uvicorn.access").disabled = True

    # Suppress verbose HTTP client logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiobotocore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
