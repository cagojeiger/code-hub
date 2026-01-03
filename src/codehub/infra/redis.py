"""Redis connection management."""

import redis.asyncio as redis

from codehub.app.config import get_settings

_client: redis.Redis | None = None


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
