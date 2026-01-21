"""Control Plane - Coordinator 실행.

Coordinator 분류:
- Critical (독립): Observer, WC, EventListener → 장애 격리 필요
- Background (통합): Scheduler (TTL + GC) → 장애 시 운영 불편 수준

Process Tasks:
- flush_activity_buffer → 각 워커 프로세스에서 독립 실행

Architecture:
- WorkspaceRuntime: 유일한 런타임 인터페이스 (Domain-Driven Design)
- AgentClient: WorkspaceRuntime 구현체 (Agent 서비스와 HTTP 통신)
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncEngine

import redis.asyncio as redis

from codehub.agent.client import AgentClient, AgentConfig
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


async def _run_coordinator(
    engine: AsyncEngine,
    redis_client: redis.Redis,
    coordinator_cls: type,
    *args,
) -> None:
    """Create and run a coordinator with leader election.

    Uses coordinator_cls.COORDINATOR_TYPE to create LeaderElection.
    Uses Redis PUB/SUB for wake notifications (broadcasting to all coordinators).
    """
    async with engine.connect() as conn:
        leader = SQLAlchemyLeaderElection(conn, coordinator_cls.COORDINATOR_TYPE)
        subscriber = ChannelSubscriber(redis_client)
        coordinator = coordinator_cls(conn, leader, subscriber, *args)
        await coordinator.run()


async def _run_event_listener(redis_client: redis.Redis) -> None:
    """Run EventListener.

    Uses asyncpg connection for PG LISTEN support.
    Uses PostgreSQL Advisory Lock for leader election (only 1 instance writes).
    """
    settings = get_settings()
    db_url = settings.database.url.replace("+asyncpg", "")
    listener = EventListener(db_url, redis_client)
    await listener.run()


async def run_control_plane(engine: AsyncEngine, redis_client: redis.Redis) -> None:
    """컨트롤 플레인 전체 실행.

    Args:
        engine: SQLAlchemy AsyncEngine
        redis_client: Redis client

    Architecture:
        - WorkspaceRuntime: 유일한 런타임 인터페이스
        - AgentClient: WorkspaceRuntime 구현체
    """
    # WorkspaceRuntime (Domain-Driven Single Interface)
    settings = get_settings()
    agent_config = AgentConfig(
        endpoint=settings.agent.endpoint,
        api_key=settings.agent.api_key,
        timeout=settings.agent.timeout,
        job_timeout=settings.agent.job_timeout,
    )
    runtime = AgentClient(agent_config)  # WorkspaceRuntime 구현체

    # Redis wrappers
    publisher = ChannelPublisher(redis_client)
    activity_store = get_activity_store()

    try:
        await asyncio.gather(
            # Coordinators (리더십 필요)
            _run_coordinator(engine, redis_client, ObserverCoordinator, runtime),
            _run_coordinator(engine, redis_client, WorkspaceController, runtime),
            _run_event_listener(redis_client),
            _run_coordinator(
                engine, redis_client, Scheduler, activity_store, publisher, runtime
            ),

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
        await runtime.close()
