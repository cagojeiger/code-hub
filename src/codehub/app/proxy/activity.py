"""Activity tracking for workspace TTL management.

3-stage buffering: Memory -> Redis -> DB
- Memory: Instant write (proxy activity)
- Redis: Bulk flush (30s interval) via ActivityStore
- DB: TTL Manager sync (60s interval)

Reference: docs/architecture_v2/ttl-manager.md
"""

import asyncio
import logging
import time

import redis.asyncio as redis

from codehub.infra.redis import ActivityStore

logger = logging.getLogger(__name__)


class ActivityBuffer:
    """Memory buffer for workspace activity tracking.

    Thread-safe (asyncio) buffer that collects workspace activity and
    periodically flushes to Redis via ActivityStore.

    Usage:
        buffer = ActivityBuffer()

        # Record activity (instant, non-blocking)
        buffer.record(workspace_id)

        # Flush to Redis (periodic background task)
        count = await buffer.flush(activity_store)
    """

    def __init__(self) -> None:
        self._buffer: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def record(self, workspace_id: str) -> None:
        """Record activity for workspace (non-blocking).

        Stores the current timestamp. Multiple calls update to latest time.
        """
        self._buffer[workspace_id] = time.time()

    async def flush(self, store: ActivityStore) -> int:
        """Flush buffer to Redis via ActivityStore.

        Args:
            store: ActivityStore instance for Redis operations.

        Returns:
            Number of workspaces flushed.
        """
        async with self._lock:
            if not self._buffer:
                return 0

            # Snapshot and clear
            snapshot = dict(self._buffer)
            self._buffer.clear()

        try:
            await store.mset(snapshot)
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


# Global buffer instance (singleton per process)
_activity_buffer: ActivityBuffer | None = None


def get_activity_buffer() -> ActivityBuffer:
    """Get or create the global activity buffer."""
    global _activity_buffer
    if _activity_buffer is None:
        _activity_buffer = ActivityBuffer()
    return _activity_buffer
