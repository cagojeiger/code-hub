"""Error handling module for code-hub.

This module defines error codes, exception classes, and response models.

Error Response Format:
{
    "error": {
        "code": "WORKSPACE_NOT_FOUND",
        "message": "Workspace not found"
    }
}

Usage:
    from codehub.core.errors import WorkspaceNotFoundError, ForbiddenError

    # Raise with default message
    raise WorkspaceNotFoundError()

    # Raise with custom message
    raise ForbiddenError("Cannot access this workspace")
"""

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Error codes."""

    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    WORKSPACE_NOT_FOUND = "WORKSPACE_NOT_FOUND"
    TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"
    RUNNING_LIMIT_EXCEEDED = "RUNNING_LIMIT_EXCEEDED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


class ErrorDetail(BaseModel):
    """Error detail containing code and message."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Error response format."""

    error: ErrorDetail


class CodeHubError(Exception):
    """Base exception for code-hub.

    All code-hub specific exceptions should inherit from this class.
    This enables centralized exception handling in FastAPI.

    Attributes:
        code: The error code from ErrorCode enum
        message: Human-readable error message
        status_code: HTTP status code to return
    """

    def __init__(self, code: ErrorCode, message: str, status_code: int) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def to_response(self) -> ErrorResponse:
        """Convert exception to ErrorResponse model."""
        return ErrorResponse(
            error=ErrorDetail(code=self.code.value, message=self.message)
        )


class UnauthorizedError(CodeHubError):
    """401 Unauthorized - Authentication required."""

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(ErrorCode.UNAUTHORIZED, message, 401)


class ForbiddenError(CodeHubError):
    """403 Forbidden - Permission denied."""

    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(ErrorCode.FORBIDDEN, message, 403)


class WorkspaceNotFoundError(CodeHubError):
    """404 Not Found - Workspace not found."""

    def __init__(self, message: str = "Workspace not found") -> None:
        super().__init__(ErrorCode.WORKSPACE_NOT_FOUND, message, 404)


class TooManyRequestsError(CodeHubError):
    """429 Too Many Requests - Rate limit exceeded."""

    def __init__(
        self, retry_after: int, message: str = "Too many failed attempts"
    ) -> None:
        self.retry_after = retry_after
        super().__init__(ErrorCode.TOO_MANY_REQUESTS, message, 429)


class RunningLimitExceededError(CodeHubError):
    """429 Too Many Requests - Running workspace limit exceeded."""

    def __init__(self, message: str = "Running workspace limit exceeded") -> None:
        super().__init__(ErrorCode.RUNNING_LIMIT_EXCEEDED, message, 429)


class UpstreamUnavailableError(CodeHubError):
    """502 Bad Gateway - Upstream service unavailable."""

    def __init__(self, message: str = "Upstream service unavailable") -> None:
        super().__init__(ErrorCode.UPSTREAM_UNAVAILABLE, message, 502)
