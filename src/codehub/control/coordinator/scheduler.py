"""Scheduler - TTL + GC 통합 스케줄러.

Background tasks:
- TTL: RUNNING → STANDBY → ARCHIVED 전환 (매 60초)
- GC: 고아 archive/container/volume 정리 (매 4시간)

장애 시 사용자 영향: 낮음 (운영 불편)
→ 같은 coordinator에서 실행해도 무방
"""

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.app.metrics.collector import (
    TTL_ACTIVITY_SYNCED_TOTAL,
    TTL_EXPIRATIONS_TOTAL,
)
from codehub.control.coordinator.base import (
    ChannelSubscriber,
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
)
from codehub.core.domain.workspace import DesiredState, Operation, Phase
from codehub.core.interfaces import InstanceController, StorageProvider
from codehub.core.logging_schema import LogEvent
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import ChannelPublisher

logger = logging.getLogger(__name__)

# Module-level settings cache
_settings = get_settings()
_channel_config = _settings.redis_channel


class Scheduler(CoordinatorBase):
    """TTL + GC 통합 스케줄러.

    tick()에서 시간 기반으로 각 작업 실행:
    - TTL: 매 ttl_interval (60초)
    - GC: 매 gc_interval (4시간)
    """

    COORDINATOR_TYPE = CoordinatorType.SCHEDULER
    WAKE_TARGET = None  # No wake signal needed

    # Use shortest interval as base (TTL 60s)
    IDLE_INTERVAL = _settings.coordinator.ttl_interval
    ACTIVE_INTERVAL = _settings.coordinator.ttl_interval

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        subscriber: ChannelSubscriber,
        activity_store: ActivityStore,
        publisher: ChannelPublisher,
        storage: StorageProvider,
        ic: InstanceController,
    ) -> None:
        super().__init__(conn, leader, subscriber)

        # TTL dependencies
        self._activity = activity_store
        self._publisher = publisher
        self._standby_ttl = _settings.ttl.standby_seconds
        self._archive_ttl = _settings.ttl.archive_seconds

        # GC dependencies
        self._storage = storage
        self._ic = ic
        self._prefix = _settings.runtime.resource_prefix

        # Interval tracking
        self._ttl_interval = _settings.coordinator.ttl_interval
        self._gc_interval = _settings.coordinator.gc_interval
        self._last_ttl: float = 0.0
        self._last_gc: float = 0.0

    async def tick(self) -> None:
        """Execute scheduled tasks based on elapsed time."""
        now = time.monotonic()

        # TTL check (every ttl_interval)
        if now - self._last_ttl >= self._ttl_interval:
            await self._run_ttl()
            self._last_ttl = now

        # GC cleanup (every gc_interval)
        if now - self._last_gc >= self._gc_interval:
            await self._run_gc()
            self._last_gc = now

    # =================================================================
    # TTL Logic (from ttl.py)
    # =================================================================

    async def _run_ttl(self) -> None:
        """TTL check loop - all bulk operations."""
        try:
            # 1. Redis → DB 동기화
            synced = await self._sync_to_db()
            if synced > 0:
                TTL_ACTIVITY_SYNCED_TOTAL.inc(synced)

            # 2. standby_ttl 체크 (RUNNING → STANDBY)
            standby_expired = await self._check_standby_ttl()
            if standby_expired > 0:
                TTL_EXPIRATIONS_TOTAL.labels(transition="standby").inc(standby_expired)

            # 3. archive_ttl 체크 (STANDBY → ARCHIVED)
            archive_expired = await self._check_archive_ttl()
            if archive_expired > 0:
                TTL_EXPIRATIONS_TOTAL.labels(transition="archive").inc(archive_expired)

            # WC 깨우기 (expired 있으면)
            if standby_expired or archive_expired:
                wc_channel = f"{_channel_config.wake_prefix}:wc"
                await self._publisher.publish(wc_channel)
                logger.info(
                    "TTL expired",
                    extra={
                        "event": LogEvent.STATE_CHANGED,
                        "standby_expired": standby_expired,
                        "archive_expired": archive_expired,
                    },
                )

            await self._conn.commit()
        except Exception as e:
            logger.exception("TTL check failed: %s", e)
            raise

    async def _sync_to_db(self) -> int:
        """Sync Redis last_access:* to DB last_access_at."""
        activities = await self._activity.scan_all()
        if not activities:
            return 0

        ws_ids = list(activities.keys())
        timestamps = [
            datetime.fromtimestamp(ts, tz=timezone.utc) for ts in activities.values()
        ]

        result = await self._conn.execute(
            text("""
                UPDATE workspaces AS w
                SET last_access_at = v.ts
                FROM unnest(CAST(:ids AS text[]), CAST(:timestamps AS timestamptz[])) AS v(id, ts)
                WHERE w.id = v.id
                RETURNING w.id
            """),
            {"ids": ws_ids, "timestamps": timestamps},
        )

        updated_ids = [row[0] for row in result.fetchall()]
        if updated_ids:
            await self._activity.delete(updated_ids)

        logger.debug("Synced %d workspace activities to DB", len(updated_ids))
        return len(updated_ids)

    async def _check_standby_ttl(self) -> int:
        """Check standby_ttl for RUNNING workspaces."""
        result = await self._conn.execute(
            text("""
                UPDATE workspaces
                SET desired_state = :desired_state
                WHERE phase = :phase
                  AND operation = :operation
                  AND deleted_at IS NULL
                  AND last_access_at IS NOT NULL
                  AND NOW() - last_access_at > make_interval(secs := :standby_ttl)
                RETURNING id
            """),
            {
                "phase": Phase.RUNNING.value,
                "operation": Operation.NONE.value,
                "standby_ttl": self._standby_ttl,
                "desired_state": DesiredState.STANDBY.value,
            },
        )
        updated_ids = [row[0] for row in result.fetchall()]

        if updated_ids:
            logger.info(
                "standby_ttl expired",
                extra={
                    "event": LogEvent.STATE_CHANGED,
                    "ttl_type": "standby",
                    "count": len(updated_ids),
                },
            )
        return len(updated_ids)

    async def _check_archive_ttl(self) -> int:
        """Check archive_ttl for STANDBY workspaces."""
        result = await self._conn.execute(
            text("""
                UPDATE workspaces
                SET desired_state = :desired_state
                WHERE phase = :phase
                  AND operation = :operation
                  AND deleted_at IS NULL
                  AND phase_changed_at IS NOT NULL
                  AND NOW() - phase_changed_at > make_interval(secs := :archive_ttl)
                RETURNING id
            """),
            {
                "phase": Phase.STANDBY.value,
                "operation": Operation.NONE.value,
                "archive_ttl": self._archive_ttl,
                "desired_state": DesiredState.ARCHIVED.value,
            },
        )
        updated_ids = [row[0] for row in result.fetchall()]

        if updated_ids:
            logger.info(
                "archive_ttl expired",
                extra={
                    "event": LogEvent.STATE_CHANGED,
                    "ttl_type": "archive",
                    "count": len(updated_ids),
                },
            )
        return len(updated_ids)

    # =================================================================
    # GC Logic (from gc.py)
    # =================================================================

    async def _run_gc(self) -> None:
        """Execute GC cycle."""
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
