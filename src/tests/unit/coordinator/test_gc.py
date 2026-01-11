"""Tests for Scheduler GC functionality.

Reference: docs/spec/05-data-plane.md (Archive GC)
Contract #9: GC Separation & Protection
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.scheduler import Scheduler
from codehub.core.interfaces import InstanceController, StorageProvider
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import ChannelPublisher


@pytest.fixture
def mock_conn() -> MagicMock:
    """Mock AsyncConnection."""
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.commit = AsyncMock()
    return conn


@pytest.fixture
def mock_leader() -> MagicMock:
    """Mock LeaderElection."""
    leader = MagicMock()
    leader.is_leader = True
    leader.try_acquire = AsyncMock(return_value=True)
    return leader


@pytest.fixture
def mock_subscriber() -> MagicMock:
    """Mock ChannelSubscriber."""
    subscriber = MagicMock()
    subscriber.subscribe = AsyncMock()
    subscriber.unsubscribe = AsyncMock()
    subscriber.get_message = AsyncMock(return_value=None)
    return subscriber


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
def mock_storage() -> MagicMock:
    """Mock StorageProvider."""
    storage = MagicMock(spec=StorageProvider)
    storage.list_archives = AsyncMock(return_value=[])
    storage.list_all_archive_keys = AsyncMock(return_value=set())
    storage.list_volumes = AsyncMock(return_value=[])
    storage.delete_archive = AsyncMock(return_value=True)
    storage.delete_volume = AsyncMock()
    return storage


@pytest.fixture
def mock_ic() -> MagicMock:
    """Mock InstanceController."""
    ic = MagicMock(spec=InstanceController)
    ic.list_all = AsyncMock(return_value=[])
    ic.delete = AsyncMock()
    return ic


@pytest.fixture
def scheduler(
    mock_conn: MagicMock,
    mock_leader: MagicMock,
    mock_subscriber: MagicMock,
    mock_activity: AsyncMock,
    mock_publisher: AsyncMock,
    mock_storage: MagicMock,
    mock_ic: MagicMock,
) -> Scheduler:
    """Create Scheduler with mocked dependencies."""
    return Scheduler(
        mock_conn, mock_leader, mock_subscriber,
        mock_activity, mock_publisher, mock_storage, mock_ic
    )


class TestSchedulerGCConfig:
    """Scheduler GC configuration tests."""

    def test_gc_interval(self, scheduler: Scheduler):
        """GC interval is 4 hours (14400 seconds)."""
        assert scheduler._gc_interval == 14400.0


class TestListArchives:
    """_list_archives() tests."""

    async def test_empty_storage(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
    ):
        """Returns empty set when no archives in storage."""
        mock_storage.list_all_archive_keys.return_value = set()

        result = await scheduler._list_archives()

        assert result == set()

    async def test_returns_all_archive_keys(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
    ):
        """Returns all archive keys from storage."""
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",
            "ws-abc123/op2/home.tar.zst",  # Multiple per workspace
            "ws-def456/op1/home.tar.zst",
        }

        result = await scheduler._list_archives()

        assert len(result) == 3
        assert "ws-abc123/op1/home.tar.zst" in result
        assert "ws-abc123/op2/home.tar.zst" in result
        assert "ws-def456/op1/home.tar.zst" in result

    async def test_uses_list_all_archive_keys(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
    ):
        """Uses list_all_archive_keys() not list_archives()."""
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",
        }

        await scheduler._list_archives()

        mock_storage.list_all_archive_keys.assert_called_once()
        mock_storage.list_archives.assert_not_called()


class TestGetProtectedPaths:
    """_get_protected_paths() tests."""

    async def test_archive_key_protected(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
    ):
        """archive_key from any workspace is protected."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-abc123/op1/home.tar.zst",),
        ]
        mock_conn.execute.return_value = mock_result

        result = await scheduler._get_protected_paths()

        assert "ws-abc123/op1/home.tar.zst" in result

    async def test_op_id_protected_for_active_workspace(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
    ):
        """op_id path protected for active (not deleted) workspace."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-abc123/current-op-id/home.tar.zst",),
        ]
        mock_conn.execute.return_value = mock_result

        result = await scheduler._get_protected_paths()

        assert "ws-abc123/current-op-id/home.tar.zst" in result

    async def test_empty_db(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
    ):
        """Returns empty set when no protected paths."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        result = await scheduler._get_protected_paths()

        assert result == set()


class TestDeleteArchives:
    """_delete_archives() tests."""

    async def test_calls_storage_delete(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
    ):
        """Calls storage.delete_archive for each key."""
        mock_storage.delete_archive.return_value = True

        archive_keys = {"ws-abc123/op1/home.tar.zst"}
        deleted = await scheduler._delete_archives(archive_keys)

        assert deleted == 1
        mock_storage.delete_archive.assert_called_once_with(
            "ws-abc123/op1/home.tar.zst"
        )

    async def test_continues_on_single_failure(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
    ):
        """Continues deleting other archives if one fails."""
        # First delete fails, second succeeds
        mock_storage.delete_archive.side_effect = [False, True]

        archive_keys = {
            "ws-fail/op1/home.tar.zst",
            "ws-success/op2/home.tar.zst",
        }
        deleted = await scheduler._delete_archives(archive_keys)

        # Only 1 succeeded
        assert deleted == 1
        # Both were attempted
        assert mock_storage.delete_archive.call_count == 2


class TestRunGC:
    """_run_gc() tests."""

    async def test_no_archives_in_storage(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
        mock_storage: MagicMock,
    ):
        """_run_gc() returns early when no archives in storage."""
        mock_storage.list_all_archive_keys.return_value = set()

        await scheduler._run_gc()

        # Should not query DB for protected paths
        mock_conn.execute.assert_not_called()

    async def test_no_orphans(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
        mock_storage: MagicMock,
    ):
        """_run_gc() does not delete when all archives are protected."""
        # Storage has one archive
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",
        }

        # DB also has it protected
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-abc123/op1/home.tar.zst",)]
        mock_conn.execute.return_value = mock_result

        await scheduler._run_gc()

        # Should not call delete_archive
        mock_storage.delete_archive.assert_not_called()

    async def test_deletes_orphans(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
        mock_storage: MagicMock,
    ):
        """_run_gc() deletes archives not in protected list."""
        # Storage has two archives
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",  # Protected
            "ws-orphan/op2/home.tar.zst",  # Orphan
        }

        # DB only has one protected
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-abc123/op1/home.tar.zst",)]
        mock_conn.execute.return_value = mock_result

        await scheduler._run_gc()

        # Should call delete_archive for orphan
        mock_storage.delete_archive.assert_called_once_with(
            "ws-orphan/op2/home.tar.zst"
        )

    async def test_handles_storage_error_gracefully(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
    ):
        """_run_gc() skips archive cleanup on S3 error (doesn't propagate)."""
        mock_storage.list_all_archive_keys.side_effect = RuntimeError("Storage error")

        # Should NOT raise - S3 error is caught and logged, cleanup skipped
        await scheduler._run_gc()

        # delete_archive should NOT be called since list failed
        mock_storage.delete_archive.assert_not_called()


class TestProtectionRules:
    """Contract #9 protection rule tests."""

    async def test_deleted_workspace_archive_key_not_protected(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
    ):
        """deleted_at workspace: archive_key is NOT protected (user wants deletion).

        SQL query now includes deleted_at IS NULL filter for archive_key protection.
        """
        mock_result = MagicMock()
        # Deleted workspace's archive_key is NOT in result
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        result = await scheduler._get_protected_paths()

        # Deleted workspace's archive_key should NOT be protected
        assert result == set()

    async def test_deleted_workspace_op_id_not_protected(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
    ):
        """deleted_at workspace: op_id path is NOT protected (user wants deletion)."""
        # This is verified by the SQL query:
        # Active op_id paths only selected WHERE deleted_at IS NULL
        # So deleted workspace's op_id path won't be in result
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []  # No paths from deleted workspace
        mock_conn.execute.return_value = mock_result

        result = await scheduler._get_protected_paths()

        # Deleted workspace's op_id path should NOT be protected
        assert result == set()

    async def test_error_workspace_both_protected(
        self,
        scheduler: Scheduler,
        mock_conn: MagicMock,
    ):
        """ERROR workspace: both archive_key and op_id paths are protected."""
        # SQL query includes:
        # WHERE (conditions->'policy.healthy'->>'status') != 'True' AND op_id IS NOT NULL
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-error/archive-key/home.tar.zst",),  # archive_key path
            ("ws-error/current-op/home.tar.zst",),  # op_id path (ERROR state)
        ]
        mock_conn.execute.return_value = mock_result

        result = await scheduler._get_protected_paths()

        assert "ws-error/archive-key/home.tar.zst" in result
        assert "ws-error/current-op/home.tar.zst" in result


class TestCleanupOrphanResources:
    """_cleanup_orphan_resources() tests (Observer pattern)."""

    async def test_no_resources(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
        mock_ic: MagicMock,
        mock_conn: MagicMock,
    ):
        """No deletion when no containers/volumes exist."""
        mock_ic.list_all.return_value = []
        mock_storage.list_volumes.return_value = []

        await scheduler._cleanup_orphan_resources()

        # Should not query DB (early return)
        mock_conn.execute.assert_not_called()
        mock_ic.delete.assert_not_called()
        mock_storage.delete_volume.assert_not_called()

    async def test_deletes_orphan_container(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
        mock_ic: MagicMock,
        mock_conn: MagicMock,
    ):
        """Deletes container not in DB."""
        # Container exists
        container = MagicMock()
        container.workspace_id = "orphan-container-ws"
        mock_ic.list_all.return_value = [container]
        mock_storage.list_volumes.return_value = []

        # DB has no matching workspace
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        await scheduler._cleanup_orphan_resources()

        mock_ic.delete.assert_called_once_with("orphan-container-ws")

    async def test_deletes_orphan_volume(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
        mock_ic: MagicMock,
        mock_conn: MagicMock,
    ):
        """Deletes volume not in DB."""
        mock_ic.list_all.return_value = []

        # Volume exists
        volume = MagicMock()
        volume.workspace_id = "orphan-volume-ws"
        mock_storage.list_volumes.return_value = [volume]

        # DB has no matching workspace
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        await scheduler._cleanup_orphan_resources()

        mock_storage.delete_volume.assert_called_once_with("orphan-volume-ws")

    async def test_preserves_valid_resources(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
        mock_ic: MagicMock,
        mock_conn: MagicMock,
    ):
        """Does not delete resources that exist in DB."""
        # Container and volume exist
        container = MagicMock()
        container.workspace_id = "valid-ws"
        volume = MagicMock()
        volume.workspace_id = "valid-ws"
        mock_ic.list_all.return_value = [container]
        mock_storage.list_volumes.return_value = [volume]

        # DB has matching workspace
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("valid-ws",)]
        mock_conn.execute.return_value = mock_result

        await scheduler._cleanup_orphan_resources()

        mock_ic.delete.assert_not_called()
        mock_storage.delete_volume.assert_not_called()

    async def test_observer_pattern_race_condition_safe(
        self,
        scheduler: Scheduler,
        mock_storage: MagicMock,
        mock_ic: MagicMock,
        mock_conn: MagicMock,
    ):
        """Resources queried first, then DB (Observer pattern prevents race condition).

        Scenario: New workspace created between resource list and DB query.
        - T1: containers = [A] (no B yet)
        - T2: workspace B created (DB + container)
        - T3: valid_ws_ids = {A, B} from DB

        Result: orphan = {A} - {A, B} = {} -> B is safe!
        """
        # Only container A at time of list
        container_a = MagicMock()
        container_a.workspace_id = "ws-a"
        mock_ic.list_all.return_value = [container_a]
        mock_storage.list_volumes.return_value = []

        # DB has both A and B (B was created after resource list)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-a",), ("ws-b",)]
        mock_conn.execute.return_value = mock_result

        await scheduler._cleanup_orphan_resources()

        # Neither should be deleted
        mock_ic.delete.assert_not_called()
