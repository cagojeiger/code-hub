"""Redis PUB/SUB abstraction.

Provides generic ChannelPublisher/Subscriber for PUB/SUB operations.
Channel naming is the responsibility of the caller (service layer).

This is a pure infrastructure abstraction - no application-specific logic.
"""

import logging

import redis.asyncio as redis

from codehub.core.logging_schema import LogEvent

logger = logging.getLogger(__name__)


class ChannelPublisher:
    """Generic Redis PUB/SUB publisher.

    Publishes messages to a specified channel.
    Channel naming convention is determined by the caller.
    """

    def __init__(self, client: redis.Redis) -> None:
        """Initialize publisher.

        Args:
            client: Redis client instance.
        """
        self._client = client

    async def publish(self, channel: str, payload: str = "") -> int:
        """Publish message to channel.

        Args:
            channel: Full channel name (e.g., "codehub:sse:user123").
            payload: Message payload (default: empty string for signals).

        Returns:
            Number of subscribers that received the message.
        """
        count = await self._client.publish(channel, payload)
        logger.debug("PUBLISH %s (subscribers=%d)", channel, count)
        return count


class ChannelSubscriber:
    """Generic Redis PUB/SUB subscriber.

    Subscribes to a specified channel and receives messages.
    Channel naming convention is determined by the caller.
    """

    def __init__(self, client: redis.Redis) -> None:
        """Initialize subscriber.

        Args:
            client: Redis client instance.
        """
        self._client = client
        self._pubsub: redis.client.PubSub | None = None
        self._channel: str | None = None

    @property
    def channel(self) -> str | None:
        """Get the subscribed channel name."""
        return self._channel

    async def subscribe(self, channel: str) -> None:
        """Subscribe to channel.

        Creates PubSub connection and subscribes to the channel.

        Args:
            channel: Full channel name to subscribe to.
        """
        self._channel = channel
        self._pubsub = self._client.pubsub()
        await self._pubsub.subscribe(channel)
        logger.info(
            "Redis subscribed",
            extra={"event": LogEvent.REDIS_SUBSCRIBED, "channel": channel},
        )

    async def unsubscribe(self) -> None:
        """Unsubscribe and close PubSub connection."""
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe()
                await self._pubsub.close()
            except Exception as e:
                logger.warning("Error closing pubsub: %s", e)
            self._pubsub = None
        self._channel = None

    async def get_message(self, timeout: float = 0.0) -> str | None:
        """Read message from channel.

        Args:
            timeout: Maximum time to wait for message (seconds).
                     0.0 means non-blocking.

        Returns:
            Message payload if received, None otherwise.
        """
        if not self._pubsub:
            return None

        try:
            msg = await self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=timeout,
            )
            if msg and msg["type"] == "message":
                data = msg["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                logger.debug("RECEIVED from %s", self._channel)
                return data
            return None

        except redis.ConnectionError as e:
            logger.warning(
                "Redis connection error",
                extra={
                    "event": LogEvent.REDIS_CONNECTION_ERROR,
                    "channel": self._channel,
                    "error_type": "connection_lost",
                    "error": str(e),
                },
            )
            return None
        except Exception as e:
            logger.warning(
                "Error reading from pubsub: %s",
                e,
                extra={"error_type": type(e).__name__, "channel": self._channel},
            )
            return None
