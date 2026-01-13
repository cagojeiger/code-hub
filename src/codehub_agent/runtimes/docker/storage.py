"""Storage manager for Agent.

Provides S3 storage operations for archive management and garbage collection.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.infra import delete_object, list_objects, object_exists

if TYPE_CHECKING:
    from codehub_agent.config import AgentConfig
    from codehub_agent.runtimes.docker.naming import ResourceNaming

logger = logging.getLogger(__name__)


class ArchiveInfo(BaseModel):
    """Archive observation result."""

    workspace_id: str
    archive_key: str | None
    exists: bool
    reason: str  # ArchiveUploaded, ArchiveNotFound, etc.
    message: str


class StorageManager:
    """S3 storage manager for archive operations."""

    def __init__(
        self,
        config: AgentConfig,
        naming: ResourceNaming,
    ) -> None:
        self._config = config
        self._naming = naming

    async def run_gc(
        self,
        protected: list[tuple[str, str]],
    ) -> tuple[int, list[str]]:
        """Run garbage collection.

        Deletes archives not in the protected list.
        Only deletes archives within this cluster's prefix.

        Args:
            protected: List of (workspace_id, op_id) tuples to protect.

        Returns:
            Tuple of (deleted_count, deleted_keys).
        """
        cluster_id = self._config.cluster_id

        # Build set of protected keys
        protected_keys = {
            self._naming.archive_s3_key(ws_id, op_id) for ws_id, op_id in protected
        }

        # List all archives in this cluster
        all_keys = await list_objects(f"{cluster_id}/")

        # Find keys to delete
        keys_to_delete = [key for key in all_keys if key not in protected_keys]

        # Delete unprotected keys
        deleted_keys = []
        for key in keys_to_delete:
            if await delete_object(key):
                deleted_keys.append(key)

        logger.info("GC completed: deleted %d archives", len(deleted_keys))
        return len(deleted_keys), deleted_keys

    async def list_archives(self, prefix: str = "") -> list[ArchiveInfo]:
        """List archives in S3 for this cluster.

        Note: The prefix parameter is accepted for interface compatibility but
        is ignored. Archives are already scoped by cluster_id, not by the
        Docker resource prefix (codehub-ws-).

        Args:
            prefix: Ignored. Kept for interface compatibility.

        Returns:
            List of ArchiveInfo for each unique workspace found.
        """
        cluster_id = self._config.cluster_id

        # List all objects in cluster
        all_keys = await list_objects(f"{cluster_id}/")

        # Parse archive keys to extract workspace_id
        # Format: {cluster_id}/{workspace_id}/{op_id}/home.tar.zst
        archives: dict[str, ArchiveInfo] = {}
        pattern = re.compile(rf"^{re.escape(cluster_id)}/([^/]+)/([^/]+)/home\.tar\.zst$")

        for key in all_keys:
            match = pattern.match(key)
            if not match:
                continue

            workspace_id = match.group(1)

            # Keep the latest archive per workspace (last one in list)
            archives[workspace_id] = ArchiveInfo(
                workspace_id=workspace_id,
                archive_key=key,
                exists=True,
                reason="ArchiveUploaded",
                message="",
            )

        return list(archives.values())

    async def list_all_archive_keys(self, prefix: str = "") -> set[str]:
        """List all archive keys in S3 for this cluster.

        Note: The prefix parameter is accepted for interface compatibility but
        is ignored. Archives are already scoped by cluster_id.

        Args:
            prefix: Ignored. Kept for interface compatibility.

        Returns:
            Set of archive keys.
        """
        cluster_id = self._config.cluster_id
        all_keys = await list_objects(f"{cluster_id}/")
        return set(all_keys)

    async def delete_archive(self, archive_key: str) -> bool:
        """Delete an archive from S3.

        Args:
            archive_key: Full S3 key of the archive.

        Returns:
            True if deleted successfully.
        """
        success = await delete_object(archive_key)
        if success:
            logger.info("Deleted archive: %s", archive_key)
        else:
            logger.warning("Failed to delete archive: %s", archive_key)
        return success

    async def archive_exists(self, archive_key: str) -> bool:
        """Check if an archive exists in S3.

        Args:
            archive_key: Full S3 key of the archive.

        Returns:
            True if archive exists.
        """
        return await object_exists(archive_key)
