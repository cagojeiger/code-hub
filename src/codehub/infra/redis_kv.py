"""Redis ZSET for activity tracking.

Uses Sorted Set for efficient bulk operations and range queries.
Key: codehub:activity (single ZSET)
Member: workspace_id
Score: timestamp (float)

Advantages over Key-Value:
- O(1) RTT for bulk operations (vs N+1 with SCAN+GET)
- ZADD GT prevents timestamp rollback (race condition fix)
- ZRANGEBYSCORE for efficient TTL queries
"""

import logging

import redis.asyncio as redis

from codehub.infra.redis import get_redis

logger = logging.getLogger(__name__)

# ZSET key name (single key for all workspaces)
ACTIVITY_KEY = "codehub:activity"


class ActivityStore:
    """Manages workspace activity timestamps in Redis ZSET.

    Key: codehub:activity (single ZSET)
    Member: workspace_id
    Score: timestamp

    Used by TTL Manager for automatic pause/archive decisions.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def update(self, activities: dict[str, float]) -> None:
        """Bulk update activity timestamps using ZADD.

        Uses GT flag to only update if new score > existing score.
        This prevents race condition where older timestamp overwrites newer.

        Args:
            activities: Mapping of workspace_id -> timestamp.
        """
        if not activities:
            return

        await self._client.zadd(ACTIVITY_KEY, activities, gt=True)
        logger.debug("Activity ZADD GT %d workspaces", len(activities))

    async def scan_all(self) -> dict[str, float]:
        """Get all activity timestamps using ZRANGE.

        Returns:
            Mapping of workspace_id -> timestamp.

        Complexity: O(1) RTT regardless of N workspaces.
        """
        items = await self._client.zrange(ACTIVITY_KEY, 0, -1, withscores=True)
        return dict(items)

    async def delete(self, workspace_ids: list[str]) -> int:
        """Delete activity records using ZREM.

        Args:
            workspace_ids: List of workspace IDs to delete.

        Returns:
            Number of members removed.
        """
        if not workspace_ids:
            return 0

        count = await self._client.zrem(ACTIVITY_KEY, *workspace_ids)
        logger.debug("Activity ZREM %d members", count)
        return count

    async def get_expired(self, cutoff_timestamp: float) -> list[str]:
        """Get workspace IDs with timestamp <= cutoff.

        Useful for TTL Manager to find idle workspaces efficiently.

        Args:
            cutoff_timestamp: Maximum timestamp (inclusive).

        Returns:
            List of workspace IDs.
        """
        return await self._client.zrangebyscore(
            ACTIVITY_KEY, min="-inf", max=cutoff_timestamp
        )


# =============================================================================
# Global Instance Management
# =============================================================================

_activity_store: ActivityStore | None = None


def get_activity_store() -> ActivityStore:
    """Get or create ActivityStore instance."""
    global _activity_store

    client = get_redis()

    if _activity_store is None:
        _activity_store = ActivityStore(client)

    return _activity_store


def reset_activity_store() -> None:
    """Reset activity store (for testing or reconnection)."""
    global _activity_store
    _activity_store = None
