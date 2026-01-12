"""Tests for TTLRunner functionality.

Reference: docs/architecture_v2/ttl-manager.md
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.scheduler_ttl import TTLRunner
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import ChannelPublisher


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Mock AsyncConnection."""
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_activity() -> AsyncMock:
    """Mock ActivityStore."""
    activity = AsyncMock(spec=ActivityStore)
    activity.scan_all = AsyncMock(return_value={})
    activity.delete = AsyncMock(return_value=0)
    return activity


@pytest.fixture
def mock_publisher() -> AsyncMock:
    """Mock ChannelPublisher."""
    publisher = AsyncMock(spec=ChannelPublisher)
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def runner(
    mock_conn: AsyncMock,
    mock_activity: AsyncMock,
    mock_publisher: AsyncMock,
) -> TTLRunner:
    """Create TTLRunner with mocked dependencies."""
    return TTLRunner(mock_conn, mock_activity, mock_publisher)


class TestTTLRunnerConfig:
    """TTLRunner configuration tests."""

    def test_standby_ttl_default(self, runner: TTLRunner):
        """standby_ttl is loaded from settings."""
        assert runner._standby_ttl > 0

    def test_archive_ttl_default(self, runner: TTLRunner):
        """archive_ttl is loaded from settings."""
        assert runner._archive_ttl > 0


class TestSyncToDb:
    """_sync_to_db() tests."""

    async def test_empty_redis(
        self,
        runner: TTLRunner,
        mock_activity: AsyncMock,
    ):
        """Returns 0 when no activities in Redis."""
        mock_activity.scan_all.return_value = {}

        count = await runner._sync_to_db()

        assert count == 0

    async def test_syncs_activities(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
        mock_activity: AsyncMock,
    ):
        """Syncs Redis activities to DB using bulk unnest UPDATE."""
        activities = {
            "ws-1": 1704067200.0,
            "ws-2": 1704067300.0,
        }
        mock_activity.scan_all.return_value = activities

        # Mock bulk UPDATE result with RETURNING
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = mock_result

        count = await runner._sync_to_db()

        assert count == 2
        # Should execute 1 bulk UPDATE statement (not N individual updates)
        assert mock_conn.execute.call_count == 1
        # Should delete Redis keys for updated workspaces only
        mock_activity.delete.assert_called_once_with(["ws-1", "ws-2"])

    async def test_partial_update_only_deletes_updated_ids(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
        mock_activity: AsyncMock,
    ):
        """Only deletes Redis keys for successfully updated workspaces.

        Scenario: ws-3 was modified by another coordinator (WC) between
        Redis scan and DB update, so UPDATE doesn't match ws-3.
        """
        activities = {
            "ws-1": 1704067200.0,
            "ws-2": 1704067300.0,
            "ws-3": 1704067400.0,  # This one will fail to update
        }
        mock_activity.scan_all.return_value = activities

        # Mock: UPDATE only matches ws-1 and ws-2 (ws-3 was modified)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = mock_result

        count = await runner._sync_to_db()

        assert count == 2
        # Should only delete successfully updated IDs (not ws-3)
        mock_activity.delete.assert_called_once_with(["ws-1", "ws-2"])


class TestCheckStandbyTtl:
    """_check_standby_ttl() tests."""

    async def test_no_expired_workspaces(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
    ):
        """Returns 0 when no workspaces expired."""
        # Mock execute to return empty result (no RETURNING rows)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        count = await runner._check_standby_ttl()

        assert count == 0

    async def test_expired_workspaces(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
    ):
        """Updates desired_state using single UPDATE + RETURNING."""
        # Single UPDATE with RETURNING returns updated ids
        update_result = MagicMock()
        update_result.fetchall.return_value = [("ws-1",), ("ws-2",)]
        mock_conn.execute.return_value = update_result

        count = await runner._check_standby_ttl()

        assert count == 2
        # Only 1 UPDATE statement (not 1 SELECT + N UPDATEs)
        assert mock_conn.execute.call_count == 1


class TestCheckArchiveTtl:
    """_check_archive_ttl() tests."""

    async def test_no_expired_workspaces(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
    ):
        """Returns 0 when no workspaces expired."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        count = await runner._check_archive_ttl()

        assert count == 0

    async def test_expired_workspaces(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
    ):
        """Updates desired_state using single UPDATE + RETURNING."""
        # Single UPDATE with RETURNING returns updated ids
        update_result = MagicMock()
        update_result.fetchall.return_value = [("ws-1",)]
        mock_conn.execute.return_value = update_result

        count = await runner._check_archive_ttl()

        assert count == 1
        # Only 1 UPDATE statement (not 1 SELECT + 1 UPDATE)
        assert mock_conn.execute.call_count == 1


class TestRun:
    """run() tests."""

    async def test_run_no_expired(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
        mock_publisher: AsyncMock,
        mock_activity: AsyncMock,
    ):
        """run() does not wake WC when no expired workspaces."""
        # Mock empty results for all queries
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result
        mock_activity.scan_all.return_value = {}

        await runner.run()

        # Should not wake WC
        mock_publisher.publish.assert_not_called()
        # Should commit
        mock_conn.commit.assert_called_once()

    async def test_run_with_standby_expired(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
        mock_publisher: AsyncMock,
        mock_activity: AsyncMock,
    ):
        """run() wakes WC when standby_ttl expired."""
        # Mock: standby UPDATE returns expired workspaces, archive returns none
        standby_result = MagicMock()
        standby_result.fetchall.return_value = [("ws-1",)]

        archive_result = MagicMock()
        archive_result.fetchall.return_value = []

        # Order: _check_standby_ttl UPDATE, _check_archive_ttl UPDATE
        mock_conn.execute.side_effect = [standby_result, archive_result]
        mock_activity.scan_all.return_value = {}

        await runner.run()

        # Should wake WC
        mock_publisher.publish.assert_called_once()

    async def test_run_with_archive_expired(
        self,
        runner: TTLRunner,
        mock_conn: AsyncMock,
        mock_publisher: AsyncMock,
        mock_activity: AsyncMock,
    ):
        """run() wakes WC when archive_ttl expired."""
        # Mock: standby returns none, archive returns expired workspaces
        standby_result = MagicMock()
        standby_result.fetchall.return_value = []

        archive_result = MagicMock()
        archive_result.fetchall.return_value = [("ws-1",)]

        # Order: _check_standby_ttl UPDATE, _check_archive_ttl UPDATE
        mock_conn.execute.side_effect = [standby_result, archive_result]
        mock_activity.scan_all.return_value = {}

        await runner.run()

        # Should wake WC
        mock_publisher.publish.assert_called_once()
