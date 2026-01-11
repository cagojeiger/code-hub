"""GC Runner - 고아 리소스 정리.

Archive: S3에 있지만 DB에 없는 파일 삭제
Container/Volume: 존재하지만 DB에 없는 리소스 삭제
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.core.interfaces import InstanceController, StorageProvider
from codehub.core.logging_schema import LogEvent

logger = logging.getLogger(__name__)

# Module-level settings cache
_settings = get_settings()


class GCRunner:
    """고아 리소스 정리."""

    def __init__(
        self,
        conn: AsyncConnection,
        storage: StorageProvider,
        ic: InstanceController,
    ) -> None:
        self._conn = conn
        self._storage = storage
        self._ic = ic
        self._prefix = _settings.runtime.resource_prefix

    async def run(self) -> None:
        """GC 사이클 실행."""
        try:
            await self._cleanup_orphan_archives()
            await self._cleanup_orphan_resources()
        except Exception as e:
            logger.exception("GC cycle failed: %s", e)
            raise

    async def _cleanup_orphan_archives(self) -> None:
        """Archive orphan 정리."""
        s3_archives = await self._list_archives()
        if s3_archives is None:
            return
        if not s3_archives:
            logger.debug("No archives in storage")
            return

        protected = await self._get_protected_paths()
        orphans = s3_archives - protected

        if not orphans:
            logger.debug(
                "No orphans found (storage=%d, protected=%d)",
                len(s3_archives),
                len(protected),
            )
            return

        deleted = await self._delete_archives(orphans)
        logger.info(
            "Deleted orphan archives",
            extra={
                "event": LogEvent.OPERATION_SUCCESS,
                "deleted": deleted,
                "total": len(orphans),
            },
        )

    async def _cleanup_orphan_resources(self) -> None:
        """Container/Volume orphan 정리 (Observer 패턴)."""
        containers = await self._ic.list_all(self._prefix)
        volumes = await self._storage.list_volumes(self._prefix)

        container_ids = {c.workspace_id for c in containers}
        volume_ids = {v.workspace_id for v in volumes}

        if not container_ids and not volume_ids:
            logger.debug("No containers/volumes in system")
            return

        valid_ws_ids = await self._get_valid_workspace_ids()

        orphan_containers = container_ids - valid_ws_ids
        orphan_volumes = volume_ids - valid_ws_ids

        for ws_id in orphan_containers:
            logger.warning(
                "Deleting orphan container",
                extra={"event": LogEvent.OPERATION_SUCCESS, "ws_id": ws_id},
            )
            try:
                await self._ic.delete(ws_id)
            except Exception as e:
                logger.warning(
                    "Failed to delete container",
                    extra={"event": LogEvent.OPERATION_FAILED, "ws_id": ws_id, "error": str(e)},
                )

        for ws_id in orphan_volumes:
            logger.warning(
                "Deleting orphan volume",
                extra={"event": LogEvent.OPERATION_SUCCESS, "ws_id": ws_id},
            )
            try:
                await self._storage.delete_volume(ws_id)
            except Exception as e:
                logger.warning(
                    "Failed to delete volume",
                    extra={"event": LogEvent.OPERATION_FAILED, "ws_id": ws_id, "error": str(e)},
                )

        if orphan_containers or orphan_volumes:
            logger.info(
                "Deleted orphan resources",
                extra={
                    "event": LogEvent.OPERATION_SUCCESS,
                    "containers": len(orphan_containers),
                    "volumes": len(orphan_volumes),
                },
            )

    async def _list_archives(self) -> set[str] | None:
        """List all archive keys from storage."""
        try:
            archive_keys = await self._storage.list_all_archive_keys(self._prefix)
            logger.debug("Found %d archives in storage", len(archive_keys))
            return archive_keys
        except Exception as e:
            logger.error(
                "Failed to list archives from S3, skipping cleanup",
                extra={"event": LogEvent.S3_ERROR, "error": str(e)},
            )
            return None

    async def _get_protected_paths(self) -> set[str]:
        """Query DB for protected archive paths."""
        result = await self._conn.execute(
            text("""
                SELECT DISTINCT path FROM (
                    SELECT archive_key AS path FROM workspaces
                    WHERE archive_key IS NOT NULL
                      AND deleted_at IS NULL

                    UNION ALL

                    SELECT :prefix || id || '/' || op_id || '/home.tar.zst' AS path
                    FROM workspaces
                    WHERE deleted_at IS NULL
                      AND op_id IS NOT NULL
                ) AS protected WHERE path IS NOT NULL
            """),
            {"prefix": self._prefix},
        )

        paths = {row[0] for row in result.fetchall()}
        logger.debug("Found %d protected paths in DB", len(paths))
        return paths

    async def _delete_archives(self, archive_keys: set[str]) -> int:
        """Delete orphan archives via StorageProvider."""
        deleted = 0

        for key in archive_keys:
            if await self._storage.delete_archive(key):
                deleted += 1
                logger.debug("Deleted: %s", key)

        return deleted

    async def _get_valid_workspace_ids(self) -> set[str]:
        """Get valid workspace IDs from DB."""
        result = await self._conn.execute(
            text("SELECT id::text FROM workspaces WHERE deleted_at IS NULL")
        )
        ws_ids = {row[0] for row in result.fetchall()}
        logger.debug("Found %d valid workspaces in DB", len(ws_ids))
        return ws_ids
