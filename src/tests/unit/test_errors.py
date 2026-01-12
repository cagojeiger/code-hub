"""Tests for error handling classes."""

import pytest

from codehub.core.errors import (
    CodeHubError,
    ErrorCode,
    ForbiddenError,
    RunningLimitExceededError,
    TooManyRequestsError,
    UnauthorizedError,
    UpstreamUnavailableError,
    WorkspaceNotFoundError,
)


class TestRunningLimitExceededError:
    """Tests for RunningLimitExceededError."""

    def test_inherits_codehub_error(self) -> None:
        """RunningLimitExceededError should inherit from CodeHubError."""
        exc = RunningLimitExceededError()
        assert isinstance(exc, CodeHubError)
        assert isinstance(exc, Exception)

    def test_has_correct_error_code(self) -> None:
        """Should have RUNNING_LIMIT_EXCEEDED error code."""
        exc = RunningLimitExceededError()
        assert exc.code == ErrorCode.RUNNING_LIMIT_EXCEEDED

    def test_has_correct_status_code(self) -> None:
        """Should have 429 status code."""
        exc = RunningLimitExceededError()
        assert exc.status_code == 429

    def test_default_message(self) -> None:
        """Should have default message."""
        exc = RunningLimitExceededError()
        assert exc.message == "Running workspace limit exceeded"

    def test_custom_message(self) -> None:
        """Should accept custom message."""
        exc = RunningLimitExceededError("Custom limit message")
        assert exc.message == "Custom limit message"

    def test_to_response(self) -> None:
        """to_response() should return ErrorResponse with correct fields."""
        exc = RunningLimitExceededError()
        resp = exc.to_response()

        assert resp.error.code == "RUNNING_LIMIT_EXCEEDED"
        assert resp.error.message == "Running workspace limit exceeded"


class TestErrorCodeEnum:
    """Tests for ErrorCode enum."""

    def test_running_limit_exceeded_exists(self) -> None:
        """RUNNING_LIMIT_EXCEEDED should be in ErrorCode enum."""
        assert hasattr(ErrorCode, "RUNNING_LIMIT_EXCEEDED")
        assert ErrorCode.RUNNING_LIMIT_EXCEEDED.value == "RUNNING_LIMIT_EXCEEDED"

    def test_all_error_codes(self) -> None:
        """All expected error codes should exist."""
        expected = [
            "UNAUTHORIZED",
            "FORBIDDEN",
            "WORKSPACE_NOT_FOUND",
            "TOO_MANY_REQUESTS",
            "RUNNING_LIMIT_EXCEEDED",
            "UPSTREAM_UNAVAILABLE",
        ]
        for code in expected:
            assert hasattr(ErrorCode, code)


class TestOtherErrors:
    """Tests for other error classes to ensure consistency."""

    @pytest.mark.parametrize(
        "error_class,expected_code,expected_status",
        [
            (UnauthorizedError, ErrorCode.UNAUTHORIZED, 401),
            (ForbiddenError, ErrorCode.FORBIDDEN, 403),
            (WorkspaceNotFoundError, ErrorCode.WORKSPACE_NOT_FOUND, 404),
            (UpstreamUnavailableError, ErrorCode.UPSTREAM_UNAVAILABLE, 502),
        ],
    )
    def test_error_codes_and_status(
        self, error_class: type, expected_code: ErrorCode, expected_status: int
    ) -> None:
        """Each error class should have correct code and status."""
        exc = error_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert isinstance(exc, CodeHubError)

    def test_too_many_requests_has_retry_after(self) -> None:
        """TooManyRequestsError should have retry_after attribute."""
        exc = TooManyRequestsError(retry_after=60)
        assert exc.retry_after == 60
        assert exc.status_code == 429
        assert exc.code == ErrorCode.TOO_MANY_REQUESTS
