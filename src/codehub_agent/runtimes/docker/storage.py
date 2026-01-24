"""Storage manager for Agent."""

from __future__ import annotations

import asyncio
import heapq
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

# Regex pattern cache for GC operations
_ARCHIVE_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _get_archive_pattern(resource_prefix: str, archive_suffix: str) -> re.Pattern:
    """Get or create cached compiled regex pattern for archive matching."""
    cache_key = f"{resource_prefix}||{archive_suffix}"
    if cache_key not in _ARCHIVE_PATTERN_CACHE:
        _ARCHIVE_PATTERN_CACHE[cache_key] = re.compile(
            rf"^{re.escape(resource_prefix)}([^/]+)/([^/]+)/{re.escape(archive_suffix)}$"
        )
    return _ARCHIVE_PATTERN_CACHE[cache_key]


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


class ErrorMarkerInfo(BaseModel):
    """Error marker observation result (archive or restore failure)."""

    workspace_id: str
    operation: str  # "archive" or "restore"
    error_code: int
    error_at: str
    archive_op_id: str | None = None  # For archive errors
    restore_op_id: str | None = None  # For restore errors
    archive_key: str | None = None  # For restore errors


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
        pattern = _get_archive_pattern(resource_prefix, archive_suffix)

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
            if len(archives) <= retention_count:
                continue  # All archives retained

            # Use heapq to find top N (more efficient than full sort for large lists)
            latest_n = heapq.nlargest(retention_count, archives, key=lambda x: x[1])
            latest_prefixes = {prefix for prefix, _ in latest_n}

            # Mark rest for deletion
            for archive_prefix, _ in archives:
                if archive_prefix not in latest_prefixes and archive_prefix not in protected_prefixes:
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

    async def list_archives_and_markers(
        self,
    ) -> tuple[list[ArchiveInfo], list[RestoreMarkerInfo], list[ErrorMarkerInfo]]:
        """List archives, restore markers, and error markers in a single S3 scan.

        This method combines list_archives(), list_restore_markers(), and error marker
        collection to avoid duplicate S3 list operations. It parallelizes content fetching.

        Returns:
            tuple of (archives, restore_markers, error_markers)
        """
        import json

        resource_prefix = self._naming.prefix
        archive_suffix = self._naming.archive_suffix
        restore_marker_suffix = ".restore_marker"
        archive_error_suffix = ".error"
        restore_error_suffix = ".restore_error"
        meta_suffix = ".meta"

        # Single S3 list call
        all_objects = await self._s3.list_objects_with_metadata(resource_prefix)

        # Build key set for .meta lookup
        all_keys = {obj["Key"] for obj in all_objects}

        # Pattern for archive matching: {prefix}{ws_id}/{archive_op_id}/home.tar.zst
        archive_pattern = re.compile(
            rf"^{re.escape(resource_prefix)}([^/]+)/([^/]+)/{re.escape(archive_suffix)}$"
        )
        # Pattern for archive error: {prefix}{ws_id}/{archive_op_id}/.error
        archive_error_pattern = re.compile(
            rf"^{re.escape(resource_prefix)}([^/]+)/([^/]+)/{re.escape(archive_error_suffix)}$"
        )

        # Single-pass processing
        workspace_archives: dict[str, list[tuple[str, datetime]]] = defaultdict(list)
        restore_marker_keys: list[tuple[str, str]] = []  # (key, workspace_id)
        archive_error_keys: list[tuple[str, str, str]] = []  # (key, workspace_id, archive_op_id)
        restore_error_keys: list[tuple[str, str]] = []  # (key, workspace_id)

        for obj in all_objects:
            key = obj["Key"]

            # Handle tar.zst files
            match = archive_pattern.match(key)
            if match:
                meta_key = f"{key}{meta_suffix}"
                if meta_key in all_keys:  # Only include complete archives
                    workspace_id = match.group(1)
                    workspace_archives[workspace_id].append((key, obj["LastModified"]))
                continue

            # Handle archive error markers (.error)
            error_match = archive_error_pattern.match(key)
            if error_match:
                workspace_id = error_match.group(1)
                archive_op_id = error_match.group(2)
                archive_error_keys.append((key, workspace_id, archive_op_id))
                continue

            # Handle restore markers
            if key.endswith(restore_marker_suffix):
                path_after_prefix = key[len(resource_prefix):]
                if "/" in path_after_prefix:
                    workspace_id = path_after_prefix.split("/")[0]
                    restore_marker_keys.append((key, workspace_id))
                continue

            # Handle restore error markers (.restore_error)
            if key.endswith(restore_error_suffix):
                path_after_prefix = key[len(resource_prefix):]
                if "/" in path_after_prefix:
                    workspace_id = path_after_prefix.split("/")[0]
                    restore_error_keys.append((key, workspace_id))

        # Build archive list (latest per workspace)
        archives: list[ArchiveInfo] = []
        for workspace_id, archive_list in workspace_archives.items():
            if archive_list:
                # Use max() instead of sort for efficiency when finding single latest
                latest_key = max(archive_list, key=lambda x: x[1])[0]
                archives.append(
                    ArchiveInfo(
                        workspace_id=workspace_id,
                        archive_key=latest_key,
                        exists=True,
                        reason="ArchiveComplete",
                        message="",
                    )
                )

        # Fetch all markers in parallel
        all_fetch_tasks = [
            *[self._s3.get_object(key) for key, _ in restore_marker_keys],
            *[self._s3.get_object(key) for key, _, _ in archive_error_keys],
            *[self._s3.get_object(key) for key, _ in restore_error_keys],
        ]
        all_contents = await asyncio.gather(*all_fetch_tasks, return_exceptions=True)

        # Split results back
        restore_marker_count = len(restore_marker_keys)
        archive_error_count = len(archive_error_keys)
        restore_marker_contents = all_contents[:restore_marker_count]
        archive_error_contents = all_contents[restore_marker_count:restore_marker_count + archive_error_count]
        restore_error_contents = all_contents[restore_marker_count + archive_error_count:]

        # Parse restore markers
        restore_markers: list[RestoreMarkerInfo] = []
        for (key, workspace_id), content in zip(restore_marker_keys, restore_marker_contents):
            if content and not isinstance(content, Exception):
                try:
                    data = json.loads(content.decode("utf-8"))
                    restore_markers.append(
                        RestoreMarkerInfo(
                            workspace_id=workspace_id,
                            restore_op_id=data.get("restore_op_id", ""),
                            archive_key=data.get("archive_key", ""),
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to parse restore marker",
                        extra={"event": LogEvent.S3_GET_FAILED, "key": key, "error": str(e)},
                    )

        # Parse error markers
        error_markers: list[ErrorMarkerInfo] = []

        # Archive errors
        for (key, workspace_id, archive_op_id), content in zip(archive_error_keys, archive_error_contents):
            if content and not isinstance(content, Exception):
                try:
                    data = json.loads(content.decode("utf-8"))
                    error_markers.append(
                        ErrorMarkerInfo(
                            workspace_id=workspace_id,
                            operation=data.get("operation", "archive"),
                            error_code=data.get("error_code", -1),
                            error_at=data.get("error_at", ""),
                            archive_op_id=data.get("archive_op_id", archive_op_id),
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to parse archive error marker",
                        extra={"event": LogEvent.S3_GET_FAILED, "key": key, "error": str(e)},
                    )

        # Restore errors
        for (key, workspace_id), content in zip(restore_error_keys, restore_error_contents):
            if content and not isinstance(content, Exception):
                try:
                    data = json.loads(content.decode("utf-8"))
                    error_markers.append(
                        ErrorMarkerInfo(
                            workspace_id=workspace_id,
                            operation=data.get("operation", "restore"),
                            error_code=data.get("error_code", -1),
                            error_at=data.get("error_at", ""),
                            restore_op_id=data.get("restore_op_id"),
                            archive_key=data.get("archive_key"),
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to parse restore error marker",
                        extra={"event": LogEvent.S3_GET_FAILED, "key": key, "error": str(e)},
                    )

        return archives, restore_markers, error_markers
