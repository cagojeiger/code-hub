"""Control Plane - Coordinator 실행.

Coordinator 분류:
- Critical (독립): Observer, WC, EventListener → 장애 격리 필요
- Background (통합): Scheduler (TTL + GC) → 장애 시 운영 불편 수준

Process Tasks:
- flush_activity_buffer → 각 워커 프로세스에서 독립 실행
"""

import asyncio
import logging
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncEngine

import redis.asyncio as redis

from codehub.adapters.instance import DockerInstanceController
from codehub.adapters.storage import S3StorageProvider
from codehub.app.config import get_settings
from codehub.control.coordinator import (
    EventListener,
    ObserverCoordinator,
    Scheduler,
    WorkspaceController,
)
from codehub.control.tasks import flush_activity_buffer
from codehub.core.logging_schema import LogEvent
from codehub.infra import get_activity_store
from codehub.infra.pg_leader import SQLAlchemyLeaderElection
from codehub.infra.redis_pubsub import ChannelPublisher, ChannelSubscriber

logger = logging.getLogger(__name__)


async def run_control_plane(engine: AsyncEngine, redis_client: redis.Redis) -> None:
    """컨트롤 플레인 전체 실행.

    Args:
        engine: SQLAlchemy AsyncEngine
        redis_client: Redis client
    """
    # Adapters (thread-safe, can be shared)
    ic = DockerInstanceController()
    sp = S3StorageProvider()

    # Redis wrappers
    publisher = ChannelPublisher(redis_client)
    activity_store = get_activity_store()

    def make_coordinator_runner(coordinator_cls: type, *args) -> Callable:
        """Factory for coordinator runner coroutines.

        Uses coordinator_cls.COORDINATOR_TYPE to create LeaderElection.
        Uses Redis PUB/SUB for wake notifications (broadcasting to all coordinators).
        """
        async def runner() -> None:
            async with engine.connect() as conn:
                leader = SQLAlchemyLeaderElection(conn, coordinator_cls.COORDINATOR_TYPE)
                subscriber = ChannelSubscriber(redis_client)
                coordinator = coordinator_cls(conn, leader, subscriber, *args)
                await coordinator.run()
        return runner

    async def event_listener_runner() -> None:
        """Run EventListener.

        Uses asyncpg connection for PG LISTEN support.
        Uses PostgreSQL Advisory Lock for leader election (only 1 instance writes).
        """
        settings = get_settings()
        db_url = settings.database.url.replace("+asyncpg", "")
        listener = EventListener(db_url, redis_client)
        await listener.run()

    try:
        await asyncio.gather(
            # Coordinators (리더십 필요)
            make_coordinator_runner(ObserverCoordinator, ic, sp)(),
            make_coordinator_runner(WorkspaceController, ic, sp)(),
            event_listener_runner(),
            make_coordinator_runner(
                Scheduler, activity_store, publisher, sp, ic
            )(),

            # Process Tasks (리더십 불필요 - 각 프로세스에서 독립 실행)
            flush_activity_buffer(),
        )
    except asyncio.CancelledError:
        logger.info("Control plane cancelled", extra={"event": LogEvent.APP_STOPPED})
        raise
    except Exception as e:
        logger.exception(
            "Control plane error",
            extra={"event": LogEvent.APP_STOPPED, "error": str(e)},
        )
    finally:
        await ic.close()
        await sp.close()
