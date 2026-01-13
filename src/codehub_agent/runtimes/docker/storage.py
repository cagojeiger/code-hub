"""Storage manager for Agent."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.infra import S3Operations

if TYPE_CHECKING:
    from codehub_agent.config import AgentConfig
    from codehub_agent.runtimes.docker.naming import ResourceNaming

logger = logging.getLogger(__name__)


class ArchiveInfo(BaseModel):
    """Archive observation result."""

    workspace_id: str
    archive_key: str | None
    exists: bool
    reason: str
    message: str


class StorageManager:
    """S3 storage manager for archive operations."""

    def __init__(
        self,
        config: AgentConfig,
        naming: ResourceNaming,
        s3: S3Operations | None = None,
    ) -> None:
        self._config = config
        self._naming = naming
        self._s3 = s3 or S3Operations(config)

    async def run_gc(
        self,
        protected: list[tuple[str, str]],
    ) -> tuple[int, list[str]]:
        """Delete archives not in the protected list."""
        protected_keys = {
            self._naming.archive_s3_key(ws_id, op_id) for ws_id, op_id in protected
        }

        all_keys = await self._s3.list_objects(self._naming.prefix)
        keys_to_delete = [key for key in all_keys if key not in protected_keys]

        # Use batch delete for better performance
        deleted_keys = await self._s3.delete_objects(keys_to_delete)

        logger.info("GC completed: deleted %d archives", len(deleted_keys))
        return len(deleted_keys), deleted_keys

    async def list_archives(self, prefix: str = "") -> list[ArchiveInfo]:
        """List archives in S3 matching resource prefix.

        Uses resource_prefix to match archive_s3_key() output format.
        """
        resource_prefix = self._naming.prefix
        archive_suffix = re.escape(self._naming.archive_suffix)
        all_keys = await self._s3.list_objects(resource_prefix)

        archives: dict[str, ArchiveInfo] = {}
        pattern = re.compile(rf"^{re.escape(resource_prefix)}([^/]+)/([^/]+)/{archive_suffix}$")

        for key in all_keys:
            match = pattern.match(key)
            if not match:
                continue

            workspace_id = match.group(1)
            archives[workspace_id] = ArchiveInfo(
                workspace_id=workspace_id,
                archive_key=key,
                exists=True,
                reason="ArchiveUploaded",
                message="",
            )

        return list(archives.values())

    async def list_all_archive_keys(self, prefix: str = "") -> set[str]:
        """List all archive keys matching resource prefix."""
        all_keys = await self._s3.list_objects(self._naming.prefix)
        return set(all_keys)

    async def delete_archive(self, archive_key: str) -> bool:
        success = await self._s3.delete_object(archive_key)
        if success:
            logger.info("Deleted archive: %s", archive_key)
        else:
            logger.warning("Failed to delete archive: %s", archive_key)
        return success

    async def archive_exists(self, archive_key: str) -> bool:
        return await self._s3.object_exists(archive_key)
