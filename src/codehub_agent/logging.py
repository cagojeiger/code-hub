"""Logging configuration for CodeHub Agent.

Supports two formats:
- text: Human-readable for local development
- json: Structured logging for production (log aggregation)
"""

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import json as jsonlogger

from codehub_agent.config import LoggingConfig


class RateLimitFilter(logging.Filter):
    """Filter to prevent log storms from repeated messages.

    Suppresses duplicate log messages within a time window.
    Useful for high-frequency errors that would otherwise flood logs.

    Args:
        rate_limit_seconds: Minimum seconds between identical messages (default: 5)
        max_cache_size: Maximum number of messages to track (default: 1000)
    """

    def __init__(
        self,
        rate_limit_seconds: float = 5.0,
        max_cache_size: int = 1000,
        name: str = "",
    ) -> None:
        super().__init__(name)
        self._rate_limit = rate_limit_seconds
        self._max_cache = max_cache_size
        self._last_log: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter duplicate messages within the rate limit window."""
        # ERROR and above always pass through (critical for debugging)
        if record.levelno >= logging.ERROR:
            return True

        # Create a key from the log message and location
        key = f"{record.name}:{record.lineno}:{record.getMessage()}"

        now = time.monotonic()
        last_time = self._last_log.get(key, 0.0)

        if now - last_time < self._rate_limit:
            return False

        # Update last log time
        self._last_log[key] = now

        # Prevent unbounded growth of cache
        if len(self._last_log) > self._max_cache:
            # Remove oldest entries (simple cleanup)
            oldest_keys = sorted(self._last_log, key=self._last_log.get)[:100]  # type: ignore[arg-type]
            for old_key in oldest_keys:
                del self._last_log[old_key]

        return True


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

    # Create handler with rate limiting
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(RateLimitFilter(rate_limit_seconds=5.0))

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
