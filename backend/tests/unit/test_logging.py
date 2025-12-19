"""Tests for logging configuration."""

import json
import logging
from io import StringIO

import pytest

from app.core.logging import (
    CodeHubJsonFormatter,
    get_request_id,
    request_id_ctx,
    set_request_id,
    setup_logging,
)


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_configures_root_logger(self):
        """Test that setup_logging configures the root logger."""
        setup_logging()
        root_logger = logging.getLogger()

        assert root_logger.level == logging.INFO
        assert len(root_logger.handlers) == 1

    def test_setup_logging_with_debug_level(self):
        """Test setup_logging with DEBUG level."""
        setup_logging(level="DEBUG")
        root_logger = logging.getLogger()

        assert root_logger.level == logging.DEBUG

    def test_setup_logging_clears_existing_handlers(self):
        """Test that setup_logging clears existing handlers."""
        root_logger = logging.getLogger()
        root_logger.addHandler(logging.StreamHandler())
        root_logger.addHandler(logging.StreamHandler())

        setup_logging()

        assert len(root_logger.handlers) == 1

    def test_setup_logging_json_format_default(self):
        """Test that JSON format is used by default."""
        setup_logging()
        root_logger = logging.getLogger()

        handler = root_logger.handlers[0]
        assert isinstance(handler.formatter, CodeHubJsonFormatter)

    def test_setup_logging_text_format(self):
        """Test setup_logging with text format (non-JSON)."""
        setup_logging(json_format=False)
        root_logger = logging.getLogger()

        handler = root_logger.handlers[0]
        assert not isinstance(handler.formatter, CodeHubJsonFormatter)


class TestCodeHubJsonFormatter:
    """Tests for CodeHubJsonFormatter."""

    def test_json_output_format(self):
        """Test that log output is valid JSON."""
        setup_logging()
        logger = logging.getLogger("test.module")

        # Capture log output
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(CodeHubJsonFormatter())
        logger.addHandler(handler)

        logger.info("Test message")

        output = stream.getvalue().strip()
        log_data = json.loads(output)

        assert "timestamp" in log_data
        assert log_data["level"] == "INFO"
        assert log_data["module"] == "test.module"
        assert log_data["message"] == "Test message"

    def test_json_output_with_extra_fields(self):
        """Test that extra fields are included in JSON output."""
        logger = logging.getLogger("test.extra")

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(CodeHubJsonFormatter())
        logger.addHandler(handler)

        logger.info("Test with extra", extra={"user_id": "123", "action": "login"})

        output = stream.getvalue().strip()
        log_data = json.loads(output)

        assert log_data["user_id"] == "123"
        assert log_data["action"] == "login"

    def test_request_id_included_when_set(self):
        """Test that request_id is included when set in context."""
        logger = logging.getLogger("test.request_id")

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(CodeHubJsonFormatter())
        logger.addHandler(handler)

        # Set request ID in context
        set_request_id("test-request-123")

        logger.info("Test with request_id")

        output = stream.getvalue().strip()
        log_data = json.loads(output)

        assert log_data["request_id"] == "test-request-123"

        # Clean up
        request_id_ctx.set(None)

    def test_request_id_not_included_when_not_set(self):
        """Test that request_id is not included when not set."""
        logger = logging.getLogger("test.no_request_id")

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(CodeHubJsonFormatter())
        logger.addHandler(handler)

        # Ensure no request ID is set
        request_id_ctx.set(None)

        logger.info("Test without request_id")

        output = stream.getvalue().strip()
        log_data = json.loads(output)

        assert "request_id" not in log_data


class TestRequestIdContext:
    """Tests for request ID context functions."""

    def test_set_and_get_request_id(self):
        """Test setting and getting request ID."""
        set_request_id("my-request-id")
        assert get_request_id() == "my-request-id"

        # Clean up
        request_id_ctx.set(None)

    def test_get_request_id_returns_none_when_not_set(self):
        """Test that get_request_id returns None when not set."""
        request_id_ctx.set(None)
        assert get_request_id() is None

    def test_request_id_isolation(self):
        """Test that request IDs are isolated per context."""
        # This tests the basic behavior - in real async code,
        # each request would have its own context
        request_id_ctx.set(None)
        assert get_request_id() is None

        set_request_id("isolated-id")
        assert get_request_id() == "isolated-id"

        # Clean up
        request_id_ctx.set(None)
