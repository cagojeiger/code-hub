"""Activity tracking for workspace TTL management (Memory -> Redis -> DB).

Configuration via ActivityConfig (ACTIVITY_ env prefix).
"""

import asyncio
import logging
import time

import redis.asyncio as redis

from codehub.app.config import get_settings
from codehub.infra.redis_kv import ActivityStore

logger = logging.getLogger(__name__)

_activity_config = get_settings().activity


class ActivityBuffer:
    """Memory buffer that collects workspace activity and flushes to Redis."""

    def __init__(self, throttle_sec: float | None = None) -> None:
        if throttle_sec is None:
            throttle_sec = _activity_config.throttle_sec
        self._buffer: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._throttle_sec = throttle_sec

    def record(self, workspace_id: str) -> None:
        """Record activity for workspace (throttled to reduce CPU overhead)."""
        now = time.time()
        last = self._buffer.get(workspace_id, 0)
        if now - last < self._throttle_sec:
            return
        self._buffer[workspace_id] = now

    async def flush(self, store: ActivityStore) -> int:
        """Flush buffer to Redis. Returns number of workspaces flushed."""
        async with self._lock:
            if not self._buffer:
                return 0

            snapshot = self._buffer
            self._buffer = {}

        try:
            await store.mset(snapshot)
            logger.debug("Flushed %d workspace activities to Redis", len(snapshot))
            return len(snapshot)
        except redis.RedisError as e:
            logger.warning("Failed to flush activities to Redis: %s", e)
            async with self._lock:
                for ws_id, ts in snapshot.items():
                    existing = self._buffer.get(ws_id, 0)
                    self._buffer[ws_id] = max(ts, existing)
            return 0

    @property
    def pending_count(self) -> int:
        return len(self._buffer)


_activity_buffer: ActivityBuffer | None = None


def get_activity_buffer() -> ActivityBuffer:
    global _activity_buffer
    if _activity_buffer is None:
        _activity_buffer = ActivityBuffer()
    return _activity_buffer
