"""JSON logging configuration."""

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from pythonjsonlogger import jsonlogger


class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """Custom JSON formatter with additional fields."""

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat()
        log_record["level"] = record.levelname
        log_record["logger"] = record.name
        log_record["pid"] = record.process
        log_record["filename"] = record.filename
        log_record["lineno"] = record.lineno
        log_record["funcName"] = record.funcName

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        log_record.pop("color_message", None)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure JSON logging for the application."""
    formatter = CustomJsonFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = False
        uv_logger.addHandler(handler)

    # Suppress verbose HTTP client logs (polling noise)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
