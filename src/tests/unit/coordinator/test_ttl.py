"""Tests for TTLManager.

Reference: docs/architecture_v2/ttl-manager.md
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as redis

from codehub.control.coordinator.base import NotifyPublisher
from codehub.control.coordinator.ttl import TTLManager
from codehub.core.domain.workspace import DesiredState, Operation, Phase


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Mock AsyncConnection."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_leader() -> AsyncMock:
    """Mock LeaderElection."""
    leader = AsyncMock()
    leader.is_leader = True
    leader.try_acquire = AsyncMock(return_value=True)
    return leader


@pytest.fixture
def mock_notify() -> AsyncMock:
    """Mock NotifySubscriber."""
    notify = AsyncMock()
    notify.subscribe = AsyncMock()
    notify.unsubscribe = AsyncMock()
    notify.get_message = AsyncMock(return_value=None)
    return notify


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Mock Redis client."""
    return AsyncMock(spec=redis.Redis)


@pytest.fixture
def mock_wake() -> AsyncMock:
    """Mock NotifyPublisher."""
    wake = AsyncMock(spec=NotifyPublisher)
    wake.wake_wc = AsyncMock()
    return wake


@pytest.fixture
def ttl_manager(
    mock_conn: AsyncMock,
    mock_leader: AsyncMock,
    mock_notify: AsyncMock,
    mock_redis: AsyncMock,
    mock_wake: AsyncMock,
) -> TTLManager:
    """Create TTLManager with mocked dependencies."""
    return TTLManager(mock_conn, mock_leader, mock_notify, mock_redis, mock_wake)


class TestTTLManagerConfig:
    """TTLManager configuration tests."""

    def test_idle_interval(self, ttl_manager: TTLManager):
        """IDLE_INTERVAL is 60 seconds."""
        assert ttl_manager.IDLE_INTERVAL == 60.0

    def test_active_interval(self, ttl_manager: TTLManager):
        """ACTIVE_INTERVAL is 60 seconds (always same for TTL)."""
        assert ttl_manager.ACTIVE_INTERVAL == 60.0


class TestSyncToDb:
    """_sync_to_db() tests."""

    async def test_empty_redis(self, ttl_manager: TTLManager):
        """Returns 0 when no activities in Redis."""
        with patch(
            "codehub.control.coordinator.ttl.scan_redis_activities",
            new_callable=AsyncMock,
            return_value={},
        ):
            count = await ttl_manager._sync_to_db()

        assert count == 0

    async def test_syncs_activities(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
    ):
        """Syncs Redis activities to DB."""
        activities = {
            "ws-1": 1704067200.0,
            "ws-2": 1704067300.0,
        }

        with patch(
            "codehub.control.coordinator.ttl.scan_redis_activities",
            new_callable=AsyncMock,
            return_value=activities,
        ), patch(
            "codehub.control.coordinator.ttl.delete_redis_activities",
            new_callable=AsyncMock,
        ) as mock_delete:
            count = await ttl_manager._sync_to_db()

        assert count == 2
        # Should execute 2 UPDATE statements
        assert mock_conn.execute.call_count == 2
        # Should delete Redis keys
        mock_delete.assert_called_once()


class TestCheckStandbyTtl:
    """_check_standby_ttl() tests."""

    async def test_no_expired_workspaces(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
    ):
        """Returns 0 when no workspaces expired."""
        # Mock execute to return empty result
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        count = await ttl_manager._check_standby_ttl()

        assert count == 0

    async def test_expired_workspaces(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
    ):
        """Updates desired_state for expired workspaces."""
        # First call (SELECT): return expired workspaces
        select_result = MagicMock()
        select_result.fetchall.return_value = [("ws-1",), ("ws-2",)]

        # Subsequent calls (UPDATE): return success
        update_result = MagicMock()

        mock_conn.execute.side_effect = [select_result, update_result, update_result]

        count = await ttl_manager._check_standby_ttl()

        assert count == 2
        # 1 SELECT + 2 UPDATEs
        assert mock_conn.execute.call_count == 3


class TestCheckArchiveTtl:
    """_check_archive_ttl() tests."""

    async def test_no_expired_workspaces(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
    ):
        """Returns 0 when no workspaces expired."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        count = await ttl_manager._check_archive_ttl()

        assert count == 0

    async def test_expired_workspaces(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
    ):
        """Updates desired_state for expired workspaces."""
        # First call (SELECT): return expired workspaces
        select_result = MagicMock()
        select_result.fetchall.return_value = [("ws-1",)]

        # Subsequent calls (UPDATE): return success
        update_result = MagicMock()

        mock_conn.execute.side_effect = [select_result, update_result]

        count = await ttl_manager._check_archive_ttl()

        assert count == 1
        # 1 SELECT + 1 UPDATE
        assert mock_conn.execute.call_count == 2


class TestTick:
    """tick() tests."""

    async def test_tick_no_expired(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
        mock_wake: AsyncMock,
    ):
        """tick() does not wake WC when no expired workspaces."""
        # Mock empty results for all queries
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        with patch(
            "codehub.control.coordinator.ttl.scan_redis_activities",
            new_callable=AsyncMock,
            return_value={},
        ):
            await ttl_manager.tick()

        # Should not wake WC
        mock_wake.wake_wc.assert_not_called()
        # Should commit
        mock_conn.commit.assert_called_once()

    async def test_tick_with_standby_expired(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
        mock_wake: AsyncMock,
    ):
        """tick() wakes WC when standby_ttl expired."""
        # Mock: standby_ttl returns expired workspaces
        select_result = MagicMock()
        select_result.fetchall.return_value = [("ws-1",)]

        empty_result = MagicMock()
        empty_result.fetchall.return_value = []

        update_result = MagicMock()

        # _sync_to_db: no activities
        # _check_standby_ttl: SELECT returns 1, UPDATE
        # _check_archive_ttl: SELECT returns 0
        mock_conn.execute.side_effect = [
            select_result,  # standby SELECT
            update_result,  # standby UPDATE
            empty_result,  # archive SELECT
        ]

        with patch(
            "codehub.control.coordinator.ttl.scan_redis_activities",
            new_callable=AsyncMock,
            return_value={},
        ):
            await ttl_manager.tick()

        # Should wake WC
        mock_wake.wake_wc.assert_called_once()

    async def test_tick_with_archive_expired(
        self,
        ttl_manager: TTLManager,
        mock_conn: AsyncMock,
        mock_wake: AsyncMock,
    ):
        """tick() wakes WC when archive_ttl expired."""
        # Mock: archive_ttl returns expired workspaces
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []

        select_result = MagicMock()
        select_result.fetchall.return_value = [("ws-1",)]

        update_result = MagicMock()

        # _sync_to_db: no activities
        # _check_standby_ttl: SELECT returns 0
        # _check_archive_ttl: SELECT returns 1, UPDATE
        mock_conn.execute.side_effect = [
            empty_result,  # standby SELECT
            select_result,  # archive SELECT
            update_result,  # archive UPDATE
        ]

        with patch(
            "codehub.control.coordinator.ttl.scan_redis_activities",
            new_callable=AsyncMock,
            return_value={},
        ):
            await ttl_manager.tick()

        # Should wake WC
        mock_wake.wake_wc.assert_called_once()
