"""TTL Runner - TTL 만료 체크 및 상태 전환.

RUNNING → STANDBY: standby_ttl 초과 시
STANDBY → ARCHIVED: archive_ttl 초과 시
"""

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from codehub.app.config import get_settings
from codehub.app.metrics.collector import (
    TTL_EXPIRATIONS_TOTAL,
    TTL_SYNC_DB_DURATION,
    TTL_SYNC_REDIS_DURATION,
)
from codehub.core.domain.workspace import DesiredState, Operation, Phase
from codehub.core.logging_schema import LogEvent
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import ChannelPublisher

logger = logging.getLogger(__name__)

# Module-level settings cache
_settings = get_settings()
_channel_config = _settings.redis_channel


class TTLRunner:
    """TTL 만료 체크 및 상태 전환."""

    def __init__(
        self,
        conn: AsyncConnection,
        activity: ActivityStore,
        publisher: ChannelPublisher,
    ) -> None:
        self._conn = conn
        self._activity = activity
        self._publisher = publisher
        self._standby_ttl = _settings.ttl.standby_seconds
        self._archive_ttl = _settings.ttl.archive_seconds

    async def run(self) -> None:
        """TTL 체크 실행."""
        try:
            # 1. Redis → DB 동기화
            await self._sync_to_db()

            # 2. standby_ttl 체크 (RUNNING → STANDBY)
            standby_expired = await self._check_standby_ttl()
            if standby_expired > 0:
                TTL_EXPIRATIONS_TOTAL.labels(transition="running_to_standby").inc(
                    standby_expired
                )

            # 3. archive_ttl 체크 (STANDBY → ARCHIVED)
            archive_expired = await self._check_archive_ttl()
            if archive_expired > 0:
                TTL_EXPIRATIONS_TOTAL.labels(transition="standby_to_archived").inc(
                    archive_expired
                )

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

    async def _sync_to_db(self) -> None:
        """Sync Redis last_access:* to DB last_access_at."""
        # 1. Redis scan
        redis_start = time.monotonic()
        activities = await self._activity.scan_all()
        TTL_SYNC_REDIS_DURATION.observe(time.monotonic() - redis_start)

        if not activities:
            TTL_SYNC_DB_DURATION.observe(0)  # No activities to sync
            return

        ws_ids = list(activities.keys())
        timestamps = [
            datetime.fromtimestamp(ts, tz=timezone.utc) for ts in activities.values()
        ]

        # 2. DB bulk update
        db_start = time.monotonic()
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
        TTL_SYNC_DB_DURATION.observe(time.monotonic() - db_start)

        updated_ids = [row[0] for row in result.fetchall()]
        if updated_ids:
            await self._activity.delete(updated_ids)

        logger.debug("Synced %d workspace activities to DB", len(updated_ids))

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
