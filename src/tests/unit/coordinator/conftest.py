"""Fixtures for coordinator unit tests."""

from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest
import redis.asyncio as redis

from codehub.control.coordinator.event_listener import EventListener
from codehub.core.interfaces.leader import LeaderElection
from codehub.infra.redis_pubsub import ChannelPublisher, ChannelSubscriber


@pytest.fixture
def mock_conn() -> AsyncMock:
    """AsyncConnection mock with advisory lock support."""
    conn = AsyncMock()
    # pg_try_advisory_lock → True (리더 획득 성공)
    result = MagicMock()
    result.fetchone.return_value = (True,)
    conn.execute = AsyncMock(return_value=result)
    return conn


@pytest.fixture
def mock_leader(mock_conn: AsyncMock) -> AsyncMock:
    """LeaderElection mock."""
    leader = AsyncMock(spec=LeaderElection)
    leader.is_leader = True
    leader.try_acquire = AsyncMock(return_value=True)
    leader.release = AsyncMock()
    leader.verify_holding = AsyncMock(return_value=True)  # P6: verify_holding mock
    return leader


@pytest.fixture
def mock_subscriber() -> AsyncMock:
    """ChannelSubscriber mock."""
    subscriber = AsyncMock(spec=ChannelSubscriber)
    subscriber.subscribe = AsyncMock()
    subscriber.unsubscribe = AsyncMock()
    subscriber.get_message = AsyncMock(return_value=None)
    return subscriber


@pytest.fixture
def mock_publisher() -> AsyncMock:
    publisher = AsyncMock(spec=ChannelPublisher)
    publisher.publish = AsyncMock(return_value=1)
    return publisher


@pytest.fixture
def mock_psycopg_conn() -> AsyncMock:
    async def _empty_notifies():
        for item in ():
            yield item

    conn = AsyncMock(spec=psycopg.AsyncConnection)
    conn.execute = AsyncMock()
    conn.close = AsyncMock()
    conn.notifies = MagicMock(return_value=_empty_notifies())
    return conn


@pytest.fixture
def mock_sa_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.connect = AsyncMock()
    engine.dispose = AsyncMock()
    return engine


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    redis_client = AsyncMock(spec=redis.Redis)
    redis_client.publish = AsyncMock(return_value=1)
    return redis_client


@pytest.fixture
def event_listener(
    mock_redis_client: AsyncMock,
    mock_publisher: AsyncMock,
    mock_psycopg_conn: AsyncMock,
    mock_conn: AsyncMock,
    mock_sa_engine: AsyncMock,
) -> EventListener:
    listener = EventListener(
        database_url="postgresql://localhost:5432/codehub",
        redis_client=mock_redis_client,
        publisher=mock_publisher,
        sse_prefix="codehub:sse",
        wake_prefix="codehub:wake",
    )
    listener._notify_conn = mock_psycopg_conn
    listener._sa_conn = mock_conn
    listener._engine = mock_sa_engine
    return listener
