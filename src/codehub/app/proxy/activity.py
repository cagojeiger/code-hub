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

    Optimizations:
    - Throttling: Skip record() if last record was < throttle_sec ago
    - Dict swap: O(1) flush instead of O(n) copy
    - Auto cleanup: No separate tracking dict needed

    Usage:
        buffer = ActivityBuffer()

        # Record activity (instant, non-blocking, throttled)
        buffer.record(workspace_id)

        # Flush to Redis (periodic background task)
        count = await buffer.flush(activity_store)
    """

    def __init__(self, throttle_sec: float = 1.0) -> None:
        self._buffer: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._throttle_sec = throttle_sec

    def record(self, workspace_id: str) -> None:
        """Record activity for workspace (non-blocking, throttled).

        Stores the current timestamp. Skips if last record was < throttle_sec ago.
        This reduces CPU overhead for high-frequency WebSocket messages.
        """
        now = time.time()
        last = self._buffer.get(workspace_id, 0)
        if now - last < self._throttle_sec:
            return
        self._buffer[workspace_id] = now

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

            # Dict swap: O(1) instead of O(n) copy
            snapshot = self._buffer
            self._buffer = {}

        try:
            await store.mset(snapshot)
            logger.debug("Flushed %d workspace activities to Redis", len(snapshot))
            return len(snapshot)
        except redis.RedisError as e:
            logger.warning("Failed to flush activities to Redis: %s", e)
            # Restore with max(ts) to keep latest timestamp
            async with self._lock:
                for ws_id, ts in snapshot.items():
                    existing = self._buffer.get(ws_id, 0)
                    self._buffer[ws_id] = max(ts, existing)
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
