"""Redis Streams for SSE events.

Provides SSEStreamPublisher/Reader for reliable event delivery
to connected SSE clients. Uses Streams for durability (MAXLEN limited).

Key pattern: events:{user_id}
"""

import asyncio
import json
import logging

import redis.asyncio as redis

from codehub.app.config import get_settings
from codehub.infra.redis import get_redis

logger = logging.getLogger(__name__)

_settings = get_settings()


class SSEStreamPublisher:
    """Publishes SSE events to Redis Streams.

    Stream key pattern: events:{user_id}
    Used by EventListener to publish workspace updates.
    """

    STREAM_PREFIX = "events:"
    MAXLEN = _settings.sse.stream_maxlen

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    def _get_stream_key(self, user_id: str) -> str:
        return f"{self.STREAM_PREFIX}{user_id}"

    async def publish_update(self, user_id: str, payload: str) -> str:
        """Publish workspace update event.

        Args:
            user_id: Owner user ID for stream routing.
            payload: JSON payload to publish.

        Returns:
            Message ID from XADD.
        """
        stream_key = self._get_stream_key(user_id)
        msg_id = await self._client.xadd(
            stream_key,
            {"data": payload},
            maxlen=self.MAXLEN,
        )
        logger.debug("SSE XADD %s (msg_id=%s)", stream_key, msg_id)
        return msg_id

    async def publish_deleted(self, user_id: str, workspace_id: str) -> str:
        """Publish workspace deleted event.

        Args:
            user_id: Owner user ID for stream routing.
            workspace_id: Deleted workspace ID.

        Returns:
            Message ID from XADD.
        """
        stream_key = self._get_stream_key(user_id)
        payload = json.dumps({"id": workspace_id, "deleted": True})
        msg_id = await self._client.xadd(
            stream_key,
            {"data": payload},
            maxlen=self.MAXLEN,
        )
        logger.debug("SSE XADD deleted %s (msg_id=%s)", stream_key, msg_id)
        return msg_id


class SSEStreamReader:
    """Reads SSE events from Redis Streams.

    Supports reconnection via last_id tracking.
    Used by SSE endpoint to read workspace events.
    """

    STREAM_PREFIX = "events:"
    BLOCK_MS = _settings.sse.xread_block_ms
    COUNT = _settings.sse.xread_count
    TIMEOUT_SEC = _settings.sse.xread_timeout

    def __init__(
        self, client: redis.Redis, user_id: str, last_id: str | None = None
    ) -> None:
        """Initialize reader for a specific user's stream.

        Args:
            client: Redis client.
            user_id: User ID to read events for.
            last_id: Optional last message ID for reconnection support.
        """
        self._client = client
        self._user_id = user_id
        self._stream_key = f"{self.STREAM_PREFIX}{user_id}"
        self._last_id = last_id or "$"  # Start from new messages if not specified

    async def read(self) -> list[tuple[str, dict]]:
        """Read new messages from stream.

        Returns:
            List of (msg_id, fields) tuples.
        """
        try:
            messages = await asyncio.wait_for(
                self._client.xread(
                    {self._stream_key: self._last_id},
                    block=self.BLOCK_MS,
                    count=self.COUNT,
                ),
                timeout=self.TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            return []

        result = []
        if messages:
            for _stream, entries in messages:
                for msg_id, fields in entries:
                    # Update last_id for next read
                    self._last_id = msg_id.decode() if isinstance(msg_id, bytes) else msg_id
                    result.append((self._last_id, fields))

        return result


# =============================================================================
# Global Instance Management
# =============================================================================

_sse_publisher: SSEStreamPublisher | None = None


def get_sse_publisher() -> SSEStreamPublisher:
    """Get or create SSEStreamPublisher instance."""
    global _sse_publisher

    client = get_redis()

    if _sse_publisher is None:
        _sse_publisher = SSEStreamPublisher(client)

    return _sse_publisher


def reset_sse_publisher() -> None:
    """Reset SSE publisher (for testing or reconnection)."""
    global _sse_publisher
    _sse_publisher = None
