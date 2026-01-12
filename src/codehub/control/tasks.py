"""Process Tasks - 각 워커 프로세스에서 독립 실행.

리더십 불필요 (각 프로세스가 자신의 버퍼만 처리).
모든 워커에서 병렬로 실행됨.
"""

import asyncio
import logging

from codehub.app.config import get_settings
from codehub.app.proxy.activity import get_activity_buffer
from codehub.core.logging_schema import LogEvent
from codehub.infra import get_activity_store

logger = logging.getLogger(__name__)


async def flush_activity_buffer() -> None:
    """Flush activity buffer to Redis periodically.

    Runs based on ActivityConfig.flush_interval to batch memory buffer to Redis.
    TTL Manager then syncs Redis to DB every 60 seconds.

    Reference: docs/architecture_v2/ttl-manager.md
    """
    flush_interval = get_settings().activity.flush_interval
    buffer = get_activity_buffer()
    activity_store = get_activity_store()

    while True:
        await asyncio.sleep(flush_interval)
        try:
            count = await buffer.flush(activity_store)
            if count > 0:
                logger.debug("Flushed %d activities to Redis", count)
        except Exception as e:
            logger.warning(
                "Activity buffer flush error",
                extra={"event": LogEvent.REDIS_CONNECTION_ERROR, "error": str(e)},
            )
