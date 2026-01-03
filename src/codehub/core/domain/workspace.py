"""Workspace domain enums.

Reference:
- docs/spec_v2/02-states.md - Phase, Operation, DesiredState
- docs/spec_v2/03-schema.md - ArchiveReason, ErrorReason
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


class ArchiveReason(StrEnum):
    """archive_ready.reason values."""

    ARCHIVE_UPLOADED = "ArchiveUploaded"
    ARCHIVE_CORRUPTED = "ArchiveCorrupted"
    ARCHIVE_EXPIRED = "ArchiveExpired"
    ARCHIVE_NOT_FOUND = "ArchiveNotFound"
    ARCHIVE_UNREACHABLE = "ArchiveUnreachable"
    ARCHIVE_TIMEOUT = "ArchiveTimeout"
    NO_ARCHIVE = "NoArchive"


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


# Terminal archive reasons (cause ERROR phase)
ARCHIVE_TERMINAL_REASONS = frozenset({
    ArchiveReason.ARCHIVE_CORRUPTED,
    ArchiveReason.ARCHIVE_EXPIRED,
    ArchiveReason.ARCHIVE_NOT_FOUND,
})

# Transient archive reasons (allow fallback to ARCHIVED)
ARCHIVE_TRANSIENT_REASONS = frozenset({
    ArchiveReason.ARCHIVE_UNREACHABLE,
    ArchiveReason.ARCHIVE_TIMEOUT,
})

# Terminal error reasons (no retry)
ERROR_TERMINAL_REASONS = frozenset({
    ErrorReason.TIMEOUT,
    ErrorReason.DATA_LOST,
    ErrorReason.IMAGE_PULL_FAILED,
    ErrorReason.CONTAINER_WITHOUT_VOLUME,
    ErrorReason.ARCHIVE_CORRUPTED,
})
