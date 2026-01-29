"""Tests for logging infrastructure."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from codehub.core.logging_schema import LogEvent


class TestLogEvent:
    """LogEvent enum tests."""

    def test_is_strenum(self):
        """LogEvent inherits from StrEnum."""
        assert isinstance(LogEvent.APP_STARTED, str)

    def test_event_value_format(self):
        """Event values are snake_case strings."""
        assert LogEvent.APP_STARTED == "app_started"
        assert LogEvent.REQUEST_COMPLETE == "request_complete"
        assert LogEvent.UNHANDLED_EXCEPTION == "unhandled_exception"

    def test_coordinator_events_exist(self):
        """Coordinator-related events are defined."""
        assert hasattr(LogEvent, "RECONCILE_COMPLETE")
        assert hasattr(LogEvent, "STATE_CHANGED")
        assert hasattr(LogEvent, "OPERATION_FAILED")

    def test_lifecycle_events_exist(self):
        """Lifecycle events are defined."""
        assert hasattr(LogEvent, "APP_STARTED")
        assert hasattr(LogEvent, "APP_STOPPED")

    def test_infrastructure_events_exist(self):
        """Infrastructure events are defined."""
        assert hasattr(LogEvent, "DB_CONNECTED")
        assert hasattr(LogEvent, "REDIS_SUBSCRIBED")
        assert hasattr(LogEvent, "S3_CONNECTED")


class TestTraceContext:
    """Trace context function tests."""

    @pytest.fixture(autouse=True)
    def reset_trace_context(self):
        """Reset trace context before each test."""
        from codehub.app.logging import clear_trace_context
        clear_trace_context()
        yield
        clear_trace_context()

    def test_get_trace_id_returns_none_when_not_set(self):
        """get_trace_id returns None when context is empty."""
        from codehub.app.logging import get_trace_id
        assert get_trace_id() is None

    def test_set_trace_id_with_value(self):
        """set_trace_id sets the provided value."""
        from codehub.app.logging import get_trace_id, set_trace_id
        result = set_trace_id("test-trace-123")
        assert result == "test-trace-123"
        assert get_trace_id() == "test-trace-123"

    def test_set_trace_id_generates_uuid_when_none(self):
        """set_trace_id generates UUID when no value provided."""
        from codehub.app.logging import get_trace_id, set_trace_id
        result = set_trace_id()
        assert result is not None
        assert len(result) == 36  # UUID format
        assert get_trace_id() == result

    def test_clear_trace_context_resets_to_none(self):
        """clear_trace_context resets context to None."""
        from codehub.app.logging import clear_trace_context, get_trace_id, set_trace_id
        set_trace_id("test-123")
        clear_trace_context()
        assert get_trace_id() is None


class TestRateLimitFilter:
    """RateLimitFilter tests."""

    def test_allows_first_messages(self):
        """Filter allows messages under rate limit."""
        from codehub.app.logging import RateLimitFilter
        filter = RateLimitFilter(rate_per_minute=10)
        record = MagicMock(spec=logging.LogRecord)
        record.levelno = logging.INFO
        record.name = "test"
        record.lineno = 1
        record.msg = "test message"

        # First 10 should pass
        for _ in range(10):
            assert filter.filter(record) is True

    def test_blocks_after_rate_limit(self):
        """Filter blocks messages after rate limit exceeded."""
        from codehub.app.logging import RateLimitFilter
        filter = RateLimitFilter(rate_per_minute=5)
        record = MagicMock(spec=logging.LogRecord)
        record.levelno = logging.INFO
        record.name = "test"
        record.lineno = 1
        record.msg = "test message"

        # First 5 pass
        for _ in range(5):
            filter.filter(record)
        
        # 6th should pass but modify message (rate limit warning)
        assert filter.filter(record) is True
        assert "[RATE LIMITED]" in record.msg
        
        # 7th should be blocked
        record.msg = "test message"
        assert filter.filter(record) is False

    def test_error_logs_bypass_rate_limit(self):
        """ERROR level logs bypass rate limiting."""
        from codehub.app.logging import RateLimitFilter
        filter = RateLimitFilter(rate_per_minute=1)
        record = MagicMock(spec=logging.LogRecord)
        record.levelno = logging.ERROR  # ERROR level
        record.name = "test"
        record.lineno = 1
        record.msg = "error message"

        # Should always pass regardless of rate
        for _ in range(100):
            assert filter.filter(record) is True


class TestCustomJsonFormatter:
    """CustomJsonFormatter tests."""

    @pytest.fixture
    def mock_settings(self):
        """Mock settings for formatter."""
        with patch("codehub.app.logging.get_settings") as mock:
            settings = MagicMock()
            settings.logging.schema_version = "1.0"
            settings.logging.service_name = "codehub-test"
            mock.return_value = settings
            yield settings

    def test_adds_standard_fields(self, mock_settings):
        """Formatter adds standard fields to log record."""
        from codehub.app.logging import CustomJsonFormatter
        formatter = CustomJsonFormatter()
        
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=42,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        
        log_record = {}
        formatter.add_fields(log_record, record, {})
        
        assert "timestamp" in log_record
        assert log_record["level"] == "INFO"
        assert log_record["logger"] == "test.logger"
        assert log_record["lineno"] == 42
        assert log_record["schema_version"] == "1.0"
        assert log_record["service"] == "codehub-test"

    def test_includes_trace_id_when_set(self, mock_settings):
        """Formatter includes trace_id when context is set."""
        from codehub.app.logging import CustomJsonFormatter, set_trace_id, clear_trace_context
        
        set_trace_id("trace-abc-123")
        try:
            formatter = CustomJsonFormatter()
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="test.py",
                lineno=1,
                msg="Test",
                args=(),
                exc_info=None,
            )
            
            log_record = {}
            formatter.add_fields(log_record, record, {})
            
            assert log_record.get("trace_id") == "trace-abc-123"
        finally:
            clear_trace_context()
