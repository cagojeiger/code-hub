"""Redis PUB/SUB for Coordinator wake-up notifications.

Provides NotifyPublisher/Subscriber for broadcasting wake messages
to all Coordinator instances. Uses PUB/SUB pattern (no durability).

Key pattern: {target}:wake (e.g., ob:wake, wc:wake, gc:wake)
"""

import asyncio
import logging
from enum import StrEnum

import redis.asyncio as redis

from codehub.infra.redis import get_redis

logger = logging.getLogger(__name__)


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
# Global Instance Management
# =============================================================================

_publisher: NotifyPublisher | None = None


def init_publisher() -> NotifyPublisher:
    """Initialize NotifyPublisher with the current Redis client."""
    global _publisher

    client = get_redis()
    _publisher = NotifyPublisher(client)
    return _publisher


def get_publisher() -> NotifyPublisher:
    """Get the global NotifyPublisher instance."""
    if _publisher is None:
        raise RuntimeError("NotifyPublisher not initialized. Call init_publisher() first.")
    return _publisher


def reset_publisher() -> None:
    """Reset publisher (for testing or reconnection)."""
    global _publisher
    _publisher = None
