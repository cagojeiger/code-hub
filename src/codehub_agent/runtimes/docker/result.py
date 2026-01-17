"""Operation result types for Docker runtime."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel


class OperationStatus(str, Enum):
    """Operation status values."""

    # Common
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"

    # Container-specific
    ALREADY_RUNNING = "already_running"
    ALREADY_STOPPED = "already_stopped"

    # Volume-specific
    ALREADY_EXISTS = "already_exists"

    # Delete-specific
    ALREADY_DELETED = "already_deleted"


class OperationResult(BaseModel):
    """Unified operation result for all runtime operations.

    Provides idempotent responses for:
    - Job operations (archive, restore): Check if job already running
    - Container operations (start, stop): Check current state
    - Volume operations (provision, delete): Check existence
    """

    status: OperationStatus
    message: str = ""

    # Operation-specific fields
    archive_key: str | None = None
    restore_marker: str | None = None

    @property
    def is_success(self) -> bool:
        """Check if operation completed or was already in desired state."""
        return self.status in (
            OperationStatus.COMPLETED,
            OperationStatus.ALREADY_RUNNING,
            OperationStatus.ALREADY_STOPPED,
            OperationStatus.ALREADY_EXISTS,
            OperationStatus.ALREADY_DELETED,
        )

    @property
    def needs_retry(self) -> bool:
        """Check if operation is in progress and needs retry."""
        return self.status == OperationStatus.IN_PROGRESS
