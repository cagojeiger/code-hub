"""Redis connection management."""

import redis.asyncio as redis

from codehub.app.config import get_settings
from codehub.control.coordinator.base import NotifyPublisher

_client: redis.Redis | None = None
_publisher: NotifyPublisher | None = None


async def init_redis() -> None:
    global _client

    settings = get_settings()
    url = str(settings.redis.url)

    _client = redis.from_url(url, decode_responses=True)
    await _client.ping()


async def close_redis() -> None:
    global _client

    if _client:
        await _client.aclose()
        _client = None


def get_redis() -> redis.Redis:
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
