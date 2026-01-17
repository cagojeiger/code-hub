"""Error handling module for codehub_agent.

This module defines error codes, exception classes, and response models.
Follows the same pattern as codehub/core/errors.py.

Error Response Format:
{
    "error": {
        "code": "INSTANCE_NOT_FOUND",
        "message": "Instance not found"
    }
}
"""

from enum import Enum

from pydantic import BaseModel


class ErrorCode(str, Enum):
    """Error codes for Agent API."""

    INSTANCE_NOT_FOUND = "INSTANCE_NOT_FOUND"
    VOLUME_NOT_FOUND = "VOLUME_NOT_FOUND"
    VOLUME_IN_USE = "VOLUME_IN_USE"
    CONTAINER_RUNNING = "CONTAINER_RUNNING"
    ARCHIVE_NOT_FOUND = "ARCHIVE_NOT_FOUND"
    JOB_FAILED = "JOB_FAILED"
    DOCKER_ERROR = "DOCKER_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    """Error detail containing code and message."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Error response format."""

    error: ErrorDetail


class AgentError(Exception):
    """Base exception for codehub_agent.

    All agent-specific exceptions should inherit from this class.
    This enables centralized exception handling in FastAPI.

    Attributes:
        code: The error code from ErrorCode enum.
        message: Human-readable error message.
        status_code: HTTP status code to return.
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


class InstanceNotFoundError(AgentError):
    """404 Not Found - Instance not found."""

    def __init__(self, message: str = "Instance not found") -> None:
        super().__init__(ErrorCode.INSTANCE_NOT_FOUND, message, 404)


class VolumeNotFoundError(AgentError):
    """404 Not Found - Volume not found."""

    def __init__(self, message: str = "Volume not found") -> None:
        super().__init__(ErrorCode.VOLUME_NOT_FOUND, message, 404)


class VolumeInUseError(AgentError):
    """409 Conflict - Volume is in use."""

    def __init__(self, message: str = "Volume is in use") -> None:
        super().__init__(ErrorCode.VOLUME_IN_USE, message, 409)


class ContainerRunningError(AgentError):
    """409 Conflict - Container is running (archive/restore not allowed)."""

    def __init__(self, message: str = "Container is running") -> None:
        super().__init__(ErrorCode.CONTAINER_RUNNING, message, 409)


class ArchiveNotFoundError(AgentError):
    """404 Not Found - Archive not found in S3."""

    def __init__(self, message: str = "Archive not found") -> None:
        super().__init__(ErrorCode.ARCHIVE_NOT_FOUND, message, 404)


class JobFailedError(AgentError):
    """500 Internal Server Error - Job execution failed."""

    def __init__(self, message: str = "Job execution failed") -> None:
        super().__init__(ErrorCode.JOB_FAILED, message, 500)


class DockerError(AgentError):
    """500 Internal Server Error - Docker operation failed."""

    def __init__(self, message: str = "Docker operation failed") -> None:
        super().__init__(ErrorCode.DOCKER_ERROR, message, 500)


class InternalError(AgentError):
    """500 Internal Server Error - Internal error."""

    def __init__(self, message: str = "Internal server error") -> None:
        super().__init__(ErrorCode.INTERNAL_ERROR, message, 500)
