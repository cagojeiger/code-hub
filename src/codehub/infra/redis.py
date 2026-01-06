"""Redis connection management.

Provides global Redis client lifecycle management.
All Redis operations are in separate modules:
- redis_pubsub: PUB/SUB for coordinator wake-up and SSE events
- redis_kv: Key-Value for activity tracking

Configuration via RedisConfig (REDIS_ env prefix).
"""

import logging

import redis.asyncio as redis

from codehub.app.config import get_settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


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
    """Close Redis client and reset all dependent modules."""
    global _client

    if _client:
        await _client.aclose()
        _client = None
        logger.info("Redis disconnected")

        from codehub.infra.redis_kv import reset_activity_store

        reset_activity_store()


def get_redis() -> redis.Redis:
    """Get the global Redis client."""
    if _client is None:
        raise RuntimeError("Redis not initialized")
    return _client
