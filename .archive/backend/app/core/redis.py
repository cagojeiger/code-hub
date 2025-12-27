"""Redis connection management for Pub/Sub."""

import logging

import redis.asyncio as redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_redis_client: redis.Redis | None = None


async def init_redis() -> None:
    """Initialize Redis connection."""
    global _redis_client
    settings = get_settings()
    _redis_client = redis.from_url(  # type: ignore[no-untyped-call]
        settings.redis.url,
        decode_responses=True,
    )
    # Verify connection
    await _redis_client.ping()  # type: ignore[misc]
    logger.info("Redis connection established")


async def close_redis() -> None:
    """Close Redis connection."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")


def get_redis() -> redis.Redis:
    """Get Redis client.

    Raises:
        RuntimeError: If Redis is not initialized.
    """
    if _redis_client is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis_client
