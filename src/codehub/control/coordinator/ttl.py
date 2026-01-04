"""TTLManager - 비활성 워크스페이스 desired_state 강등.

TTL Manager는 두 가지 TTL을 관리:
1. standby_ttl: RUNNING → STANDBY (last_access_at 기준)
2. archive_ttl: STANDBY → ARCHIVED (phase_changed_at 기준)

Reference: docs/architecture_v2/ttl-manager.md
"""

import logging
from datetime import datetime

import redis.asyncio as redis
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.proxy.activity import (
    delete_redis_activities,
    scan_redis_activities,
)
from codehub.control.coordinator.base import (
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifyPublisher,
    NotifySubscriber,
)
from codehub.core.domain.workspace import DesiredState, Operation, Phase
from codehub.core.models import Workspace

logger = logging.getLogger(__name__)


class TTLManager(CoordinatorBase):
    """비활성 워크스페이스 desired_state 강등.

    tick()에서 세 가지 작업:
    1. _sync_to_db(): Redis → DB 동기화
    2. _check_standby_ttl(): RUNNING 상태 체크 → STANDBY 요청
    3. _check_archive_ttl(): STANDBY 상태 체크 → ARCHIVED 요청
    """

    COORDINATOR_TYPE = CoordinatorType.TTL

    IDLE_INTERVAL = 60.0
    ACTIVE_INTERVAL = 60.0  # TTL은 항상 60초 주기

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
        redis_client: redis.Redis,
        wake_publisher: NotifyPublisher,
    ) -> None:
        super().__init__(conn, leader, notify)
        self._redis = redis_client
        self._wake = wake_publisher

    async def tick(self) -> None:
        """TTL check loop."""
        # 1. Redis → DB 동기화
        await self._sync_to_db()

        # 2. standby_ttl 체크 (RUNNING → STANDBY)
        standby_expired = await self._check_standby_ttl()

        # 3. archive_ttl 체크 (STANDBY → ARCHIVED)
        archive_expired = await self._check_archive_ttl()

        # WC 깨우기 (expired 있으면)
        if standby_expired or archive_expired:
            await self._wake.wake_wc()
            logger.info(
                "TTL expired: standby=%d, archive=%d",
                standby_expired,
                archive_expired,
            )

        await self._conn.commit()

    async def _sync_to_db(self) -> int:
        """Sync Redis last_access:* to DB last_access_at.

        Returns:
            Number of workspaces synced.
        """
        # 1. Scan Redis for last_access:* keys
        activities = await scan_redis_activities(self._redis)
        if not activities:
            return 0

        # 2. Bulk update DB
        # Use PostgreSQL VALUES for efficient bulk update
        for ws_id, ts in activities.items():
            dt = datetime.fromtimestamp(ts)
            stmt = (
                update(Workspace)
                .where(Workspace.id == ws_id)
                .values(last_access_at=dt)
            )
            await self._conn.execute(stmt)

        # 3. Delete Redis keys
        await delete_redis_activities(self._redis, list(activities.keys()))

        logger.debug("Synced %d workspace activities to DB", len(activities))
        return len(activities)

    async def _check_standby_ttl(self) -> int:
        """Check standby_ttl for RUNNING workspaces.

        Condition: NOW() - last_access_at > standby_ttl_seconds

        Returns:
            Number of workspaces transitioned.
        """
        # Query for expired RUNNING workspaces
        result = await self._conn.execute(
            text("""
                SELECT id FROM workspaces
                WHERE phase = :phase
                  AND operation = :operation
                  AND deleted_at IS NULL
                  AND last_access_at IS NOT NULL
                  AND NOW() - last_access_at > make_interval(secs := standby_ttl_seconds)
            """),
            {
                "phase": Phase.RUNNING.value,
                "operation": Operation.NONE.value,
            },
        )
        expired_ids = [row[0] for row in result.fetchall()]

        if not expired_ids:
            return 0

        # Update desired_state to STANDBY
        for ws_id in expired_ids:
            stmt = (
                update(Workspace)
                .where(
                    Workspace.id == ws_id,
                    Workspace.phase == Phase.RUNNING.value,
                    Workspace.operation == Operation.NONE.value,
                )
                .values(desired_state=DesiredState.STANDBY.value)
            )
            await self._conn.execute(stmt)

        logger.info("standby_ttl expired for %d workspaces", len(expired_ids))
        return len(expired_ids)

    async def _check_archive_ttl(self) -> int:
        """Check archive_ttl for STANDBY workspaces.

        Condition: NOW() - phase_changed_at > archive_ttl_seconds

        Returns:
            Number of workspaces transitioned.
        """
        # Query for expired STANDBY workspaces
        result = await self._conn.execute(
            text("""
                SELECT id FROM workspaces
                WHERE phase = :phase
                  AND operation = :operation
                  AND deleted_at IS NULL
                  AND phase_changed_at IS NOT NULL
                  AND NOW() - phase_changed_at > make_interval(secs := archive_ttl_seconds)
            """),
            {
                "phase": Phase.STANDBY.value,
                "operation": Operation.NONE.value,
            },
        )
        expired_ids = [row[0] for row in result.fetchall()]

        if not expired_ids:
            return 0

        # Update desired_state to ARCHIVED
        for ws_id in expired_ids:
            stmt = (
                update(Workspace)
                .where(
                    Workspace.id == ws_id,
                    Workspace.phase == Phase.STANDBY.value,
                    Workspace.operation == Operation.NONE.value,
                )
                .values(desired_state=DesiredState.ARCHIVED.value)
            )
            await self._conn.execute(stmt)

        logger.info("archive_ttl expired for %d workspaces", len(expired_ids))
        return len(expired_ids)
