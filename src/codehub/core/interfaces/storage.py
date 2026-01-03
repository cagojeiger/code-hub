"""Storage provider interface for volume and archive operations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VolumeInfo:
    """Volume observation result."""

    workspace_id: str
    exists: bool
    reason: str
    message: str


@dataclass
class ArchiveInfo:
    """Archive observation result."""

    workspace_id: str
    archive_key: str | None
    exists: bool
    reason: str  # ArchiveUploaded, ArchiveCorrupted, ArchiveUnreachable, etc.
    message: str


class StorageProvider(ABC):
    """Interface for storage operations.

    Implementations: MinIOStorageProvider, LocalStorageProvider (future)
    """

    @abstractmethod
    async def list_volumes(self, prefix: str) -> list[VolumeInfo]:
        """Bulk observe all volumes with given prefix.

        Args:
            prefix: Volume name prefix (e.g., "ws-")

        Returns:
            List of VolumeInfo for all volumes
        """
        ...

    @abstractmethod
    async def list_archives(self, prefix: str) -> list[ArchiveInfo]:
        """Bulk observe all archives with given prefix.

        Args:
            prefix: Archive key prefix (e.g., "ws-")

        Returns:
            List of ArchiveInfo for all archives
        """
        ...

    @abstractmethod
    async def provision(self, workspace_id: str) -> None:
        """Create new volume for workspace.

        Args:
            workspace_id: Workspace ID
        """
        ...

    @abstractmethod
    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore volume from archive.

        Args:
            workspace_id: Workspace ID
            archive_key: Archive key to restore from

        Returns:
            restore_marker (= archive_key) for completion check
        """
        ...

    @abstractmethod
    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive volume and return archive_key.

        Args:
            workspace_id: Workspace ID
            op_id: Operation ID for idempotency (archive_key = {workspace_id}/{op_id}/home.tar.zst)

        Returns:
            Archive key for the created archive
        """
        ...

    @abstractmethod
    async def delete_volume(self, workspace_id: str) -> None:
        """Delete volume.

        Args:
            workspace_id: Workspace ID
        """
        ...

    @abstractmethod
    async def volume_exists(self, workspace_id: str) -> bool:
        """Check if volume exists.

        Args:
            workspace_id: Workspace ID

        Returns:
            True if volume exists
        """
        ...

    @abstractmethod
    async def create_empty_archive(self, workspace_id: str, op_id: str) -> str:
        """Create empty archive and return archive_key.

        Args:
            workspace_id: Workspace ID
            op_id: Operation ID for idempotency

        Returns:
            Archive key for the created empty archive
        """
        ...
