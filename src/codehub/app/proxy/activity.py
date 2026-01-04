"""Activity tracking for workspace TTL management.

3-stage buffering: Memory -> Redis -> DB
- Memory: Instant write (proxy activity)
- Redis: Bulk flush (30s interval)
- DB: TTL Manager sync (60s interval)

Reference: docs/architecture_v2/ttl-manager.md
"""

import asyncio
import logging
import time
from collections.abc import Mapping

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis key prefix for last access timestamps
REDIS_KEY_PREFIX = "last_access:"


class ActivityBuffer:
    """Memory buffer for workspace activity tracking.

    Thread-safe (asyncio) buffer that collects workspace activity and
    periodically flushes to Redis.

    Usage:
        buffer = ActivityBuffer()

        # Record activity (instant, non-blocking)
        buffer.record(workspace_id)

        # Flush to Redis (periodic background task)
        count = await buffer.flush(redis_client)
    """

    def __init__(self) -> None:
        self._buffer: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def record(self, workspace_id: str) -> None:
        """Record activity for workspace (non-blocking).

        Stores the current timestamp. Multiple calls update to latest time.
        """
        self._buffer[workspace_id] = time.time()

    async def flush(self, client: redis.Redis) -> int:
        """Flush buffer to Redis using MSET.

        Returns:
            Number of workspaces flushed.
        """
        async with self._lock:
            if not self._buffer:
                return 0

            # Snapshot and clear
            snapshot = dict(self._buffer)
            self._buffer.clear()

        # MSET all at once: last_access:ws1 ts1 last_access:ws2 ts2 ...
        redis_data = {f"{REDIS_KEY_PREFIX}{ws_id}": str(ts) for ws_id, ts in snapshot.items()}

        try:
            await client.mset(redis_data)  # type: ignore[arg-type]
            logger.debug("Flushed %d workspace activities to Redis", len(snapshot))
            return len(snapshot)
        except redis.RedisError as e:
            logger.warning("Failed to flush activities to Redis: %s", e)
            # Re-add to buffer for next flush attempt
            async with self._lock:
                for ws_id, ts in snapshot.items():
                    # Only restore if not already updated
                    if ws_id not in self._buffer:
                        self._buffer[ws_id] = ts
            return 0

    @property
    def pending_count(self) -> int:
        """Number of workspaces pending flush."""
        return len(self._buffer)


async def scan_redis_activities(client: redis.Redis) -> Mapping[str, float]:
    """Scan Redis for all last_access:* keys.

    Returns:
        Mapping of workspace_id -> timestamp.
    """
    result: dict[str, float] = {}
    pattern = f"{REDIS_KEY_PREFIX}*"

    async for key in client.scan_iter(match=pattern):
        key_str = key.decode() if isinstance(key, bytes) else key
        ws_id = key_str.removeprefix(REDIS_KEY_PREFIX)
        value = await client.get(key)
        if value:
            value_str = value.decode() if isinstance(value, bytes) else value
            result[ws_id] = float(value_str)

    return result


async def delete_redis_activities(client: redis.Redis, workspace_ids: list[str]) -> int:
    """Delete last_access:* keys from Redis.

    Returns:
        Number of keys deleted.
    """
    if not workspace_ids:
        return 0

    keys = [f"{REDIS_KEY_PREFIX}{ws_id}" for ws_id in workspace_ids]
    return await client.delete(*keys)


# Global buffer instance (singleton per process)
_activity_buffer: ActivityBuffer | None = None


def get_activity_buffer() -> ActivityBuffer:
    """Get or create the global activity buffer."""
    global _activity_buffer
    if _activity_buffer is None:
        _activity_buffer = ActivityBuffer()
    return _activity_buffer
