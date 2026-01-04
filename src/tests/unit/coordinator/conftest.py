"""Fixtures for coordinator unit tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.base import LeaderElection
from codehub.infra.redis import NotifySubscriber


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
    return leader


@pytest.fixture
def mock_notify() -> AsyncMock:
    """NotifySubscriber mock."""
    notify = AsyncMock(spec=NotifySubscriber)
    notify.subscribe = AsyncMock()
    notify.unsubscribe = AsyncMock()
    notify.get_message = AsyncMock(return_value=None)
    return notify
