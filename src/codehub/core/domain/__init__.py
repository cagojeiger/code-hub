"""Domain models and enums."""

from codehub.core.domain.workspace import (
    ARCHIVE_TERMINAL_REASONS,
    ARCHIVE_TRANSIENT_REASONS,
    ERROR_TERMINAL_REASONS,
    ArchiveReason,
    DesiredState,
    ErrorReason,
    Operation,
    Phase,
)

__all__ = [
    "ArchiveReason",
    "DesiredState",
    "ErrorReason",
    "Operation",
    "Phase",
    "ARCHIVE_TERMINAL_REASONS",
    "ARCHIVE_TRANSIENT_REASONS",
    "ERROR_TERMINAL_REASONS",
]
