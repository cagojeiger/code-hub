"""Workspace domain enums.

Reference:
- docs/spec_v2/02-states.md - Phase, Operation, DesiredState
- docs/spec_v2/03-schema.md - ErrorReason
"""

from enum import StrEnum


class Phase(StrEnum):
    """Workspace phase (calculated from conditions)."""

    PENDING = "PENDING"
    ARCHIVED = "ARCHIVED"
    STANDBY = "STANDBY"
    RUNNING = "RUNNING"
    ERROR = "ERROR"
    DELETING = "DELETING"
    DELETED = "DELETED"


class Operation(StrEnum):
    """Current operation in progress."""

    NONE = "NONE"
    PROVISIONING = "PROVISIONING"
    RESTORING = "RESTORING"
    STARTING = "STARTING"
    STOPPING = "STOPPING"
    ARCHIVING = "ARCHIVING"
    CREATE_EMPTY_ARCHIVE = "CREATE_EMPTY_ARCHIVE"
    DELETING = "DELETING"


class DesiredState(StrEnum):
    """User-requested target state."""

    DELETED = "DELETED"
    ARCHIVED = "ARCHIVED"
    STANDBY = "STANDBY"
    RUNNING = "RUNNING"


class ErrorReason(StrEnum):
    """error_reason column values."""

    TIMEOUT = "Timeout"
    RETRY_EXCEEDED = "RetryExceeded"
    ACTION_FAILED = "ActionFailed"
    DATA_LOST = "DataLost"
    UNREACHABLE = "Unreachable"
    IMAGE_PULL_FAILED = "ImagePullFailed"
    CONTAINER_WITHOUT_VOLUME = "ContainerWithoutVolume"
    ARCHIVE_CORRUPTED = "ArchiveCorrupted"


# Terminal error reasons (no retry)
ERROR_TERMINAL_REASONS = frozenset({
    ErrorReason.TIMEOUT,
    ErrorReason.DATA_LOST,
    ErrorReason.IMAGE_PULL_FAILED,
    ErrorReason.CONTAINER_WITHOUT_VOLUME,
    ErrorReason.ARCHIVE_CORRUPTED,
})
