"""Redis connection management and wrapper classes.

All Redis operations are encapsulated in wrapper classes:
- NotifyPublisher/Subscriber: PUB/SUB for coordinator wake-up
- SSEStreamPublisher/Reader: Streams for SSE events
- ActivityStore: Key-Value for activity tracking

Configuration via RedisConfig and SSEConfig.
"""

import asyncio
import json
import logging
from enum import StrEnum

import redis.asyncio as redis

from codehub.app.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# =============================================================================
# Wake-up (PUB/SUB)
# =============================================================================


class WakeTarget(StrEnum):
    """Wake target identifiers for PUB/SUB channels."""

    OB = "ob"
    WC = "wc"
    GC = "gc"


def _get_wake_channel(target: WakeTarget) -> str:
    """Get PUB/SUB channel name for wake target."""
    return f"{target}:wake"


class NotifyPublisher:
    """Publishes notifications to wake up Coordinators via Redis PUB/SUB.

    Uses PUBLISH to broadcast wake messages to all subscribers.
    """

    def __init__(self, client: redis.Redis) -> None:
        self._client = client

    async def publish(self, target: WakeTarget) -> int:
        """Publish wake message to PUB/SUB channel.

        Returns the number of subscribers that received the message.
        """
        channel = _get_wake_channel(target)
        count = await self._client.publish(channel, "wake")
        logger.debug("Published wake to %s (subscribers=%d)", channel, count)
        return count

    async def wake_ob(self) -> int:
        return await self.publish(WakeTarget.OB)

    async def wake_wc(self) -> int:
        return await self.publish(WakeTarget.WC)

    async def wake_gc(self) -> int:
        return await self.publish(WakeTarget.GC)

    async def wake_ob_wc(self) -> tuple[int, int]:
        """Wake both OB and WC in parallel.

        Reduces 2 RTT to 1 RTT by using asyncio.gather.

        Returns:
            Tuple of (ob_subscribers, wc_subscribers).
        """
        ob_count, wc_count = await asyncio.gather(
            self.wake_ob(),
            self.wake_wc(),
        )
        return ob_count, wc_count


class NotifySubscriber:
    """Subscribes to wake channel using Redis PUB/SUB.

    Uses PUB/SUB for broadcasting - all subscribers receive all messages.
    This ensures every coordinator instance gets wake-up notifications.
    """

    def __init__(self, client: redis.Redis) -> None:
        """Initialize subscriber.

        Args:
            client: Redis client.
        """
        self._client = client
        self._pubsub: redis.client.PubSub | None = None
        self._target: WakeTarget | None = None

    async def subscribe(self, target: WakeTarget) -> None:
        """Subscribe to wake channel for a specific target.

        Creates PubSub connection and subscribes to the target's channel.
        """
        self._target = target
        channel = _get_wake_channel(target)
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(channel)
        logger.info("Subscribed to %s (target=%s)", channel, target)

    async def unsubscribe(self) -> None:
        """Unsubscribe and close PubSub connection."""
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.close()
            except Exception as e:
                logger.warning("Error closing pubsub: %s", e)
            self._pubsub = None
        self._target = None

    async def get_message(self, timeout: float = 0.0) -> str | None:
        """Read wake message from PUB/SUB channel.

        Returns the target if a message was received, None otherwise.
        """
        if not self._pubsub or not self._target:
            return None

        try:
            msg = await self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=timeout,
            )
            if msg and msg["type"] == "message":
                logger.debug("Received wake (target=%s)", self._target)
                return str(self._target)
            return None

        except redis.ConnectionError as e:
            logger.warning(
                "Redis connection error in pubsub: %s",
                e,
                extra={"error_type": "connection", "target": str(self._target)},
            )
            return None
        except Exception as e:
            logger.warning(
                "Error reading from pubsub: %s",
                e,
                extra={"error_type": type(e).__name__, "target": str(self._target)},
            )
            return None


# =============================================================================
# SSE Streams
# =============================================================================


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

    def __init__(self, client: redis.Redis, user_id: str) -> None:
        """Initialize reader for a specific user's stream.

        Args:
            client: Redis client.
            user_id: User ID to read events for.
        """
        self._client = client
        self._user_id = user_id
        self._stream_key = f"{self.STREAM_PREFIX}{user_id}"
        self._last_id = "$"  # Start from new messages

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
# Activity (Key-Value)
# =============================================================================


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
# Client Management
# =============================================================================

_client: redis.Redis | None = None
_publisher: NotifyPublisher | None = None
_sse_publisher: SSEStreamPublisher | None = None
_activity_store: ActivityStore | None = None


async def init_redis() -> None:
    """Initialize Redis client."""
    global _client

    settings = get_settings()
    url = str(settings.redis.url)
    max_connections = settings.redis.max_connections

    _client = redis.from_url(
        url,
        decode_responses=True,
        max_connections=max_connections,
    )
    await _client.ping()
    logger.info("Redis connected: %s (max_connections=%d)", url, max_connections)


async def close_redis() -> None:
    """Close Redis client."""
    global _client, _publisher, _sse_publisher, _activity_store

    if _client:
        await _client.aclose()
        _client = None
        _publisher = None
        _sse_publisher = None
        _activity_store = None
        logger.info("Redis disconnected")


def get_redis() -> redis.Redis:
    """Get the global Redis client."""
    if _client is None:
        raise RuntimeError("Redis not initialized")
    return _client


def init_publisher() -> NotifyPublisher:
    """Initialize NotifyPublisher with the current Redis client."""
    global _publisher

    if _client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")

    _publisher = NotifyPublisher(_client)
    return _publisher


def get_publisher() -> NotifyPublisher:
    """Get the global NotifyPublisher instance."""
    if _publisher is None:
        raise RuntimeError("NotifyPublisher not initialized. Call init_publisher() first.")
    return _publisher


def get_sse_publisher() -> SSEStreamPublisher:
    """Get or create SSEStreamPublisher instance."""
    global _sse_publisher

    if _client is None:
        raise RuntimeError("Redis not initialized")

    if _sse_publisher is None:
        _sse_publisher = SSEStreamPublisher(_client)

    return _sse_publisher


def get_activity_store() -> ActivityStore:
    """Get or create ActivityStore instance."""
    global _activity_store

    if _client is None:
        raise RuntimeError("Redis not initialized")

    if _activity_store is None:
        _activity_store = ActivityStore(_client)

    return _activity_store
