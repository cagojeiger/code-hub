"""
Error handling module for code-hub.

This module defines error codes, exception classes, and response models
following the spec.md error format.

Error Response Format:
{
    "error": {
        "code": "WORKSPACE_NOT_FOUND",
        "message": "Workspace not found"
    }
}

Usage:
    from app.core.errors import WorkspaceNotFoundError, InvalidStateError

    # Raise with default message
    raise WorkspaceNotFoundError()

    # Raise with custom message
    raise InvalidStateError("Cannot start workspace in RUNNING state")
"""

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Error codes as defined in spec.md."""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    WORKSPACE_NOT_FOUND = "WORKSPACE_NOT_FOUND"
    INVALID_STATE = "INVALID_STATE"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """Error detail containing code and message."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Error response format as defined in spec.md."""

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


class InvalidRequestError(CodeHubError):
    """400 Bad Request - Invalid request parameters."""

    def __init__(self, message: str = "Invalid request") -> None:
        super().__init__(ErrorCode.INVALID_REQUEST, message, 400)


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


class InvalidStateError(CodeHubError):
    """409 Conflict - Invalid state for the requested operation."""

    def __init__(self, message: str = "Invalid state for this operation") -> None:
        super().__init__(ErrorCode.INVALID_STATE, message, 409)


class UpstreamUnavailableError(CodeHubError):
    """502 Bad Gateway - Upstream service unavailable."""

    def __init__(self, message: str = "Upstream service unavailable") -> None:
        super().__init__(ErrorCode.UPSTREAM_UNAVAILABLE, message, 502)


class InternalError(CodeHubError):
    """500 Internal Server Error - Unexpected error."""

    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, message, 500)
