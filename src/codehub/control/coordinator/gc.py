"""ArchiveGC - Orphan archive 정리.

Contract #9 준수:
- archive_key로 참조되는 archive 보호 (deleted_at IS NULL인 경우만)
- op_id로 진행 중인 archive 보호
- deleted_at 시 archive_key, op_id 모두 보호 해제
- ERROR 상태 시 둘 다 보호
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.core.interfaces import InstanceController, StorageProvider

logger = logging.getLogger(__name__)

# Module-level settings cache
_settings = get_settings()


class ArchiveGC(CoordinatorBase):
    """Orphan archive 정리.

    Contract #9:
    - Orphan = S3에 있지만 DB에서 보호되지 않는 archive
    - op_id로 진행 중 작업 보호되므로 delay 불필요
    """

    COORDINATOR_TYPE = CoordinatorType.GC
    WAKE_TARGET = "gc"

    # GC uses longer interval from config
    IDLE_INTERVAL = _settings.coordinator.gc_interval

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
        storage: StorageProvider,
        ic: InstanceController,
    ) -> None:
        super().__init__(conn, leader, subscriber)
        self._storage = storage
        self._ic = ic
        self._prefix = _settings.runtime.resource_prefix

    async def tick(self) -> None:
        """Execute one GC cycle.

        Flow:
        1. Archive orphan cleanup (기존)
        2. Container/Volume orphan cleanup (Observer 패턴)
        """
        try:
            await self._cleanup_orphan_archives()
            await self._cleanup_orphan_resources()
        except Exception as e:
            logger.exception("[%s] GC cycle failed: %s", self.name, e)
            # Re-raise to trigger rollback in base class
            raise

    async def _cleanup_orphan_archives(self) -> None:
        """Archive orphan 정리."""
        # 1. Storage archive 목록
        s3_archives = await self._list_archives()
        if s3_archives is None:
            # S3 error occurred, skip cleanup for this tick
            return
        if not s3_archives:
            logger.debug("[%s] No archives in storage", self.name)
            return

        # 2. DB 보호 경로
        protected = await self._get_protected_paths()

        # 3. orphan 판별
        orphans = s3_archives - protected

        if not orphans:
            logger.debug(
                "[%s] No orphans found (storage=%d, protected=%d)",
                self.name,
                len(s3_archives),
                len(protected),
            )
            return

        # 4. 즉시 삭제
        deleted = await self._delete_archives(orphans)
        logger.info(
            "[%s] Deleted %d/%d orphan archives",
            self.name,
            deleted,
            len(orphans),
        )

    async def _cleanup_orphan_resources(self) -> None:
        """Container/Volume orphan 정리 (Observer 패턴).

        리소스 먼저 조회 → DB 확인 순서로 race condition 방지.
        """
        # Step 1: 리소스 먼저 조회 (Observer 패턴)
        containers = await self._ic.list_all(self._prefix)
        volumes = await self._storage.list_volumes(self._prefix)

        container_ids = {c.workspace_id for c in containers}
        volume_ids = {v.workspace_id for v in volumes}

        if not container_ids and not volume_ids:
            logger.debug("[%s] No containers/volumes in system", self.name)
            return

        # Step 2: DB에서 유효한 workspace_id 조회 (리소스 조회 후!)
        valid_ws_ids = await self._get_valid_workspace_ids()

        # Step 3: orphan 판별 및 삭제
        orphan_containers = container_ids - valid_ws_ids
        orphan_volumes = volume_ids - valid_ws_ids

        for ws_id in orphan_containers:
            logger.warning("[%s] Deleting orphan container: %s", self.name, ws_id)
            try:
                await self._ic.delete(ws_id)
            except Exception as e:
                logger.warning("[%s] Failed to delete container %s: %s", self.name, ws_id, e)

        for ws_id in orphan_volumes:
            logger.warning("[%s] Deleting orphan volume: %s", self.name, ws_id)
            try:
                await self._storage.delete_volume(ws_id)
            except Exception as e:
                logger.warning("[%s] Failed to delete volume %s: %s", self.name, ws_id, e)

        if orphan_containers or orphan_volumes:
            logger.info(
                "[%s] Deleted orphan resources: %d containers, %d volumes",
                self.name,
                len(orphan_containers),
                len(orphan_volumes),
            )

    async def _list_archives(self) -> set[str] | None:
        """List all archive keys from storage.

        Uses list_all_archive_keys() to get ALL archives (not just latest per workspace).

        Returns:
            Set of archive keys, or None if S3 error occurred (skip archive cleanup).
        """
        try:
            archive_keys = await self._storage.list_all_archive_keys(self._prefix)
            logger.debug("[%s] Found %d archives in storage", self.name, len(archive_keys))
            return archive_keys
        except Exception as e:
            logger.error("[%s] Failed to list archives from S3, skipping cleanup: %s", self.name, e)
            return None

    async def _get_protected_paths(self) -> set[str]:
        """Query DB for protected archive paths.

        Protection rules (Contract #9):
        1. Active workspaces: archive_key protected (deleted_at IS NULL)
        2. Active workspaces: op_id path protected (includes ERROR state)

        Note: deleted_at != NULL -> archive_key, op_id 모두 보호 해제
        """
        result = await self._conn.execute(
            text("""
                SELECT DISTINCT path FROM (
                    -- Active workspaces: archive_key protected
                    SELECT archive_key AS path FROM workspaces
                    WHERE archive_key IS NOT NULL
                      AND deleted_at IS NULL

                    UNION ALL

                    -- Active workspaces: op_id path protected (includes ERROR state)
                    SELECT :prefix || id || '/' || op_id || '/home.tar.zst' AS path
                    FROM workspaces
                    WHERE deleted_at IS NULL
                      AND op_id IS NOT NULL
                ) AS protected WHERE path IS NOT NULL
            """),
            {"prefix": self._prefix},
        )

        paths = {row[0] for row in result.fetchall()}
        logger.debug("[%s] Found %d protected paths in DB", self.name, len(paths))
        return paths

    async def _delete_archives(self, archive_keys: set[str]) -> int:
        """Delete orphan archives via StorageProvider.

        Individual failures are logged and skipped.

        Returns:
            Number of successfully deleted archives.
        """
        deleted = 0

        for key in archive_keys:
            if await self._storage.delete_archive(key):
                deleted += 1
                logger.debug("[%s] Deleted: %s", self.name, key)

        return deleted

    async def _get_valid_workspace_ids(self) -> set[str]:
        """Get valid workspace IDs from DB.

        Returns workspace IDs that are not deleted (deleted_at IS NULL).
        """
        result = await self._conn.execute(
            text("SELECT id::text FROM workspaces WHERE deleted_at IS NULL")
        )
        ws_ids = {row[0] for row in result.fetchall()}
        logger.debug("[%s] Found %d valid workspaces in DB", self.name, len(ws_ids))
        return ws_ids
