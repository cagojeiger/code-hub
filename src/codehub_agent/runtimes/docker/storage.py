"""Storage manager for Agent."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.infra import S3Operations
from codehub_agent.logging_schema import LogEvent

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


class RestoreMarkerInfo(BaseModel):
    """Restore marker observation result."""

    workspace_id: str
    restore_op_id: str
    archive_key: str


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
        archive_keys: list[str],
        protected_workspaces: list[tuple[str, str]],
        retention_count: int = 3,
    ) -> tuple[int, list[str]]:
        """Delete old archives while keeping latest N per workspace.

        Two-layer protection:
        1. Retention: Keep latest N archives per workspace
        2. Protection: Never delete archives in protected list (RESTORING/ARCHIVING)

        Args:
            archive_keys: Direct archive_key values (RESTORING target protection)
            protected_workspaces: (ws_id, archive_op_id) tuples (ARCHIVING protection)
            retention_count: Number of archives to keep per workspace (default: 3)
        """
        resource_prefix = self._naming.prefix
        archive_suffix = self._naming.archive_suffix

        # 1. Build protected prefixes (directory-based)
        protected_prefixes: set[str] = set()

        for key in archive_keys:
            # "workspaces/ws-1/op-1/home.tar.zst" → "ws-1/op-1/"
            relative = key[len(resource_prefix):] if key.startswith(resource_prefix) else key
            if "/" in relative:
                parts = relative.rsplit("/", 1)[0]  # "ws-1/op-1"
                protected_prefixes.add(parts + "/")

        for ws_id, archive_op_id in protected_workspaces:
            protected_prefixes.add(f"{ws_id}/{archive_op_id}/")

        # 2. Get all objects with metadata for sorting by date
        all_objects = await self._s3.list_objects_with_metadata(resource_prefix)

        # 3. Group archives by workspace
        #    Pattern: {prefix}{ws_id}/{archive_op_id}/home.tar.zst
        pattern = re.compile(
            rf"^{re.escape(resource_prefix)}([^/]+)/([^/]+)/{re.escape(archive_suffix)}$"
        )

        # Group: {ws_id: [(archive_prefix, last_modified), ...]}
        workspace_archives: dict[str, list[tuple[str, datetime]]] = defaultdict(list)

        for obj in all_objects:
            key = obj["Key"]
            match = pattern.match(key)
            if match:
                ws_id = match.group(1)
                archive_op_id = match.group(2)
                archive_prefix = f"{ws_id}/{archive_op_id}/"
                workspace_archives[ws_id].append((archive_prefix, obj["LastModified"]))

        # 4. Determine archives to delete (retention + protection)
        prefixes_to_delete: set[str] = set()

        for ws_id, archives in workspace_archives.items():
            # Sort by date descending (newest first)
            archives.sort(key=lambda x: x[1], reverse=True)

            # Keep latest N, mark rest for deletion
            for archive_prefix, _ in archives[retention_count:]:
                # Check if protected
                if archive_prefix not in protected_prefixes:
                    prefixes_to_delete.add(archive_prefix)

        # 5. Collect all keys under prefixes to delete
        keys_to_delete: list[str] = []
        for obj in all_objects:
            key = obj["Key"]
            relative = key[len(resource_prefix):]

            # Never delete .restore_marker
            if relative.endswith(".restore_marker"):
                continue

            # Check if under a prefix to delete
            for prefix in prefixes_to_delete:
                if relative.startswith(prefix):
                    keys_to_delete.append(key)
                    break

        # 6. Execute deletion
        deleted_keys = await self._s3.delete_objects(keys_to_delete)

        logger.info(
            "GC completed",
            extra={
                "event": LogEvent.GC_COMPLETED,
                "deleted_count": len(deleted_keys),
                "deleted_prefixes": len(prefixes_to_delete),
                "protected_prefixes": len(protected_prefixes),
                "retention_count": retention_count,
            },
        )
        return len(deleted_keys), deleted_keys

    async def list_archives(self, prefix: str = "") -> list[ArchiveInfo]:
        """List complete archives (tar.zst + .meta, latest per workspace).

        Returns one archive per workspace:
        - Only archives with both tar.zst and .meta files (complete)
        - Selects the most recent by LastModified when multiple exist
        """
        resource_prefix = self._naming.prefix
        archive_suffix = self._naming.archive_suffix

        # 1. Get all objects with metadata for sorting
        all_objects = await self._s3.list_objects_with_metadata(resource_prefix)

        # 2. Build key set for O(1) .meta lookup
        all_keys = {obj["Key"] for obj in all_objects}

        # 3. Group archives by workspace_id
        pattern = re.compile(
            rf"^{re.escape(resource_prefix)}([^/]+)/([^/]+)/{re.escape(archive_suffix)}$"
        )
        workspace_archives: dict[str, list[tuple[str, datetime]]] = defaultdict(list)

        for obj in all_objects:
            key = obj["Key"]
            match = pattern.match(key)
            if not match:
                continue

            # Require .meta file for completeness
            meta_key = f"{key}.meta"
            if meta_key not in all_keys:
                continue

            workspace_id = match.group(1)
            workspace_archives[workspace_id].append((key, obj["LastModified"]))

        # 4. Select latest archive per workspace
        archives: list[ArchiveInfo] = []
        for workspace_id, archive_list in workspace_archives.items():
            if not archive_list:
                continue

            # Sort by LastModified descending, pick latest
            archive_list.sort(key=lambda x: x[1], reverse=True)
            latest_key = archive_list[0][0]

            archives.append(ArchiveInfo(
                workspace_id=workspace_id,
                archive_key=latest_key,
                exists=True,
                reason="ArchiveComplete",
                message="",
            ))

        return archives

    async def list_all_archive_keys(self, prefix: str = "") -> set[str]:
        """List all archive keys matching resource prefix."""
        all_keys = await self._s3.list_objects(self._naming.prefix)
        return set(all_keys)

    async def delete_archive(self, archive_key: str) -> bool:
        success = await self._s3.delete_object(archive_key)
        if success:
            logger.info(
                "Archive deleted",
                extra={"event": LogEvent.ARCHIVE_DELETED, "archive_key": archive_key},
            )
        else:
            logger.warning(
                "Failed to delete archive",
                extra={"event": LogEvent.S3_DELETE_FAILED, "archive_key": archive_key},
            )
        return success

    async def archive_exists(self, archive_key: str) -> bool:
        return await self._s3.object_exists(archive_key)

    async def list_restore_markers(self) -> list[RestoreMarkerInfo]:
        """List all restore markers (.restore_marker files).

        Returns one marker per workspace containing:
        - restore_op_id: The restore operation ID
        - archive_key: The archive that was restored
        """
        import json

        resource_prefix = self._naming.prefix
        marker_suffix = ".restore_marker"

        # List all objects and find .restore_marker files
        all_objects = await self._s3.list_objects(resource_prefix)

        markers: list[RestoreMarkerInfo] = []
        for key in all_objects:
            if not key.endswith(marker_suffix):
                continue

            # Extract workspace_id from path: {prefix}{workspace_id}/.restore_marker
            # Remove prefix and marker suffix to get workspace_id
            path_after_prefix = key[len(resource_prefix):]
            if "/" not in path_after_prefix:
                continue

            workspace_id = path_after_prefix.split("/")[0]

            # Read marker content
            try:
                content = await self._s3.get_object(key)
                if content:
                    data = json.loads(content.decode("utf-8"))
                    markers.append(RestoreMarkerInfo(
                        workspace_id=workspace_id,
                        restore_op_id=data.get("restore_op_id", ""),
                        archive_key=data.get("archive_key", ""),
                    ))
            except Exception as e:
                logger.warning(
                    "Failed to read restore marker",
                    extra={
                        "event": LogEvent.S3_GET_FAILED,
                        "key": key,
                        "error": str(e),
                    },
                )

        return markers
