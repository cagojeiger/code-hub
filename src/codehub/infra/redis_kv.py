"""Redis Key-Value for activity tracking.

Provides ActivityStore for managing workspace last_access timestamps.
Used by TTL Manager for automatic pause/archive decisions.

Key pattern: last_access:{workspace_id}
"""

import logging

import redis.asyncio as redis

from codehub.infra.redis import get_redis

logger = logging.getLogger(__name__)


class ActivityStore:
    """Manages workspace activity timestamps in Redis.

    Key pattern: last_access:{workspace_id}
    Used by TTL Manager for automatic pause/archive.
    """

    KEY_PREFIX = "last_access:"

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _get_key(self, workspace_id: str) -> str:
        return f"{self.KEY_PREFIX}{workspace_id}"

    async def mset(self, activities: dict[str, float]) -> None:
        """Bulk set activity timestamps.

        Args:
            activities: Mapping of workspace_id -> timestamp.
        """
        if not activities:
            return
        redis_data = {self._get_key(ws_id): str(ts) for ws_id, ts in activities.items()}
        await self._client.mset(redis_data)
        logger.debug("Activity MSET %d workspaces", len(activities))

    async def scan_all(self) -> dict[str, float]:
        """Scan all activity keys.

        Returns:
            Mapping of workspace_id -> timestamp.
        """
        result: dict[str, float] = {}
        pattern = f"{self.KEY_PREFIX}*"

        async for key in self._client.scan_iter(match=pattern):
            key_str = key.decode() if isinstance(key, bytes) else key
            ws_id = key_str.removeprefix(self.KEY_PREFIX)
            value = await self._client.get(key)
            if value:
                value_str = value.decode() if isinstance(value, bytes) else value
                result[ws_id] = float(value_str)

        return result

    async def delete(self, workspace_ids: list[str]) -> int:
        """Delete activity keys.

        Args:
            workspace_ids: List of workspace IDs to delete.

        Returns:
            Number of keys deleted.
        """
        if not workspace_ids:
            return 0
        keys = [self._get_key(ws_id) for ws_id in workspace_ids]
        count = await self._client.delete(*keys)
        logger.debug("Activity DELETE %d keys", count)
        return count


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
