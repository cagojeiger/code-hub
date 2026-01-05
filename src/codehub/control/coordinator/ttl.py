"""TTLManager - 비활성 워크스페이스 desired_state 강등.

TTL Manager는 두 가지 TTL을 관리:
1. standby_ttl: RUNNING → STANDBY (last_access_at 기준)
2. archive_ttl: STANDBY → ARCHIVED (phase_changed_at 기준)

Optimized with bulk operations:
- Single UPDATE + RETURNING instead of SELECT + N UPDATEs
- PostgreSQL unnest for bulk activity sync

Reference: docs/architecture_v2/ttl-manager.md
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.control.coordinator.base import (
    CoordinatorBase,
    CoordinatorType,
    LeaderElection,
    NotifySubscriber,
)
from codehub.core.domain.workspace import DesiredState, Operation, Phase
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import NotifyPublisher

logger = logging.getLogger(__name__)

# Module-level settings cache (consistent with Observer pattern)
_settings = get_settings()


class TTLManager(CoordinatorBase):
    """비활성 워크스페이스 desired_state 강등.

    tick()에서 세 가지 작업 (모두 O(1) DB 왕복):
    1. _sync_to_db(): Redis → DB 동기화 (unnest bulk UPDATE)
    2. _check_standby_ttl(): RUNNING → STANDBY (single UPDATE + RETURNING)
    3. _check_archive_ttl(): STANDBY → ARCHIVED (single UPDATE + RETURNING)
    """

    COORDINATOR_TYPE = CoordinatorType.TTL

    # TTL Manager uses fixed interval from config (always same idle/active)
    IDLE_INTERVAL = _settings.coordinator.ttl_interval
    ACTIVE_INTERVAL = _settings.coordinator.ttl_interval

    def __init__(
        self,
        conn: AsyncConnection,
        leader: LeaderElection,
        notify: NotifySubscriber,
        activity_store: ActivityStore,
        wake_publisher: NotifyPublisher,
    ) -> None:
        super().__init__(conn, leader, notify)
        self._activity = activity_store
        self._wake = wake_publisher

        # Use module-level cached settings
        self._standby_ttl = _settings.ttl.standby_seconds
        self._archive_ttl = _settings.ttl.archive_seconds

    async def tick(self) -> None:
        """TTL check loop - all bulk operations."""
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
                "[%s] TTL expired: standby=%d, archive=%d",
                self.name,
                standby_expired,
                archive_expired,
            )

        await self._conn.commit()

    async def _sync_to_db(self) -> int:
        """Sync Redis last_access:* to DB last_access_at.

        Uses PostgreSQL unnest for O(1) bulk update regardless of N workspaces.

        Returns:
            Number of workspaces synced.
        """
        activities = await self._activity.scan_all()
        if not activities:
            return 0

        # Prepare arrays for PostgreSQL unnest
        ws_ids = list(activities.keys())
        timestamps = [
            datetime.fromtimestamp(ts, tz=timezone.utc) for ts in activities.values()
        ]

        # Single bulk UPDATE using PostgreSQL unnest
        # Note: Use CAST() instead of :: to avoid conflict with SQLAlchemy named params
        # RETURNING w.id: Only delete Redis keys for successfully updated workspaces
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

        # Only delete Redis keys for successfully updated workspaces
        updated_ids = [row[0] for row in result.fetchall()]
        if updated_ids:
            await self._activity.delete(updated_ids)

        logger.debug("[%s] Synced %d workspace activities to DB", self.name, len(updated_ids))
        return len(updated_ids)

    async def _check_standby_ttl(self) -> int:
        """Check standby_ttl for RUNNING workspaces.

        Uses single UPDATE + RETURNING instead of SELECT + N UPDATEs.
        Complexity: O(1) DB round-trips.

        Condition: NOW() - last_access_at > standby_ttl (from config)

        Returns:
            Number of workspaces transitioned.
        """
        # Single UPDATE with RETURNING - no separate SELECT needed
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
            logger.info("[%s] standby_ttl expired for %d workspaces", self.name, len(updated_ids))
        return len(updated_ids)

    async def _check_archive_ttl(self) -> int:
        """Check archive_ttl for STANDBY workspaces.

        Uses single UPDATE + RETURNING instead of SELECT + N UPDATEs.
        Complexity: O(1) DB round-trips.

        Condition: NOW() - phase_changed_at > archive_ttl (from config)

        Returns:
            Number of workspaces transitioned.
        """
        # Single UPDATE with RETURNING - no separate SELECT needed
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
            logger.info("[%s] archive_ttl expired for %d workspaces", self.name, len(updated_ids))
        return len(updated_ids)
