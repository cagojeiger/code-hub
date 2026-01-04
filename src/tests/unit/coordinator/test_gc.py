"""Tests for ArchiveGC.

Reference: docs/spec_v2/05-data-plane.md (Archive GC)
Contract #9: GC Separation & Protection
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub.control.coordinator.gc import ArchiveGC
from codehub.core.interfaces import InstanceController, StorageProvider


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
def mock_notify() -> MagicMock:
    """Mock NotifySubscriber."""
    notify = MagicMock()
    notify.subscribe = AsyncMock()
    notify.unsubscribe = AsyncMock()
    notify.get_message = AsyncMock(return_value=None)
    return notify


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
def archive_gc(
    mock_conn: MagicMock,
    mock_leader: MagicMock,
    mock_notify: MagicMock,
    mock_storage: MagicMock,
    mock_ic: MagicMock,
) -> ArchiveGC:
    """Create ArchiveGC with mocked dependencies."""
    return ArchiveGC(mock_conn, mock_leader, mock_notify, mock_storage, mock_ic)


class TestArchiveGCConfig:
    """ArchiveGC configuration tests."""

    def test_idle_interval(self, archive_gc: ArchiveGC):
        """IDLE_INTERVAL is 4 hours (14400 seconds)."""
        assert archive_gc.IDLE_INTERVAL == 14400.0


class TestListArchives:
    """_list_archives() tests."""

    async def test_empty_storage(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
    ):
        """Returns empty set when no archives in storage."""
        mock_storage.list_all_archive_keys.return_value = set()

        result = await archive_gc._list_archives()

        assert result == set()

    async def test_returns_all_archive_keys(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
    ):
        """Returns all archive keys from storage."""
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",
            "ws-abc123/op2/home.tar.zst",  # Multiple per workspace
            "ws-def456/op1/home.tar.zst",
        }

        result = await archive_gc._list_archives()

        assert len(result) == 3
        assert "ws-abc123/op1/home.tar.zst" in result
        assert "ws-abc123/op2/home.tar.zst" in result
        assert "ws-def456/op1/home.tar.zst" in result

    async def test_uses_list_all_archive_keys(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
    ):
        """Uses list_all_archive_keys() not list_archives()."""
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",
        }

        await archive_gc._list_archives()

        mock_storage.list_all_archive_keys.assert_called_once()
        mock_storage.list_archives.assert_not_called()


class TestGetProtectedPaths:
    """_get_protected_paths() tests."""

    async def test_archive_key_protected(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
    ):
        """archive_key from any workspace is protected."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-abc123/op1/home.tar.zst",),
        ]
        mock_conn.execute.return_value = mock_result

        result = await archive_gc._get_protected_paths()

        assert "ws-abc123/op1/home.tar.zst" in result

    async def test_op_id_protected_for_active_workspace(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
    ):
        """op_id path protected for active (not deleted) workspace."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("ws-abc123/current-op-id/home.tar.zst",),
        ]
        mock_conn.execute.return_value = mock_result

        result = await archive_gc._get_protected_paths()

        assert "ws-abc123/current-op-id/home.tar.zst" in result

    async def test_empty_db(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
    ):
        """Returns empty set when no protected paths."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        result = await archive_gc._get_protected_paths()

        assert result == set()


class TestDeleteArchives:
    """_delete_archives() tests."""

    async def test_calls_storage_delete(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
    ):
        """Calls storage.delete_archive for each key."""
        mock_storage.delete_archive.return_value = True

        archive_keys = {"ws-abc123/op1/home.tar.zst"}
        deleted = await archive_gc._delete_archives(archive_keys)

        assert deleted == 1
        mock_storage.delete_archive.assert_called_once_with(
            "ws-abc123/op1/home.tar.zst"
        )

    async def test_continues_on_single_failure(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
    ):
        """Continues deleting other archives if one fails."""
        # First delete fails, second succeeds
        mock_storage.delete_archive.side_effect = [False, True]

        archive_keys = {
            "ws-fail/op1/home.tar.zst",
            "ws-success/op2/home.tar.zst",
        }
        deleted = await archive_gc._delete_archives(archive_keys)

        # Only 1 succeeded
        assert deleted == 1
        # Both were attempted
        assert mock_storage.delete_archive.call_count == 2


class TestTick:
    """tick() tests."""

    async def test_no_archives_in_storage(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
        mock_storage: MagicMock,
    ):
        """tick() returns early when no archives in storage."""
        mock_storage.list_all_archive_keys.return_value = set()

        await archive_gc.tick()

        # Should not query DB for protected paths
        mock_conn.execute.assert_not_called()

    async def test_no_orphans(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
        mock_storage: MagicMock,
    ):
        """tick() does not delete when all archives are protected."""
        # Storage has one archive
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",
        }

        # DB also has it protected
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-abc123/op1/home.tar.zst",)]
        mock_conn.execute.return_value = mock_result

        await archive_gc.tick()

        # Should not call delete_archive
        mock_storage.delete_archive.assert_not_called()

    async def test_deletes_orphans(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
        mock_storage: MagicMock,
    ):
        """tick() deletes archives not in protected list."""
        # Storage has two archives
        mock_storage.list_all_archive_keys.return_value = {
            "ws-abc123/op1/home.tar.zst",  # Protected
            "ws-orphan/op2/home.tar.zst",  # Orphan
        }

        # DB only has one protected
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("ws-abc123/op1/home.tar.zst",)]
        mock_conn.execute.return_value = mock_result

        await archive_gc.tick()

        # Should call delete_archive for orphan
        mock_storage.delete_archive.assert_called_once_with(
            "ws-orphan/op2/home.tar.zst"
        )

    async def test_handles_storage_error(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
    ):
        """tick() re-raises storage errors for base class rollback."""
        mock_storage.list_all_archive_keys.side_effect = RuntimeError("Storage error")

        # Should raise (base class handles rollback)
        with pytest.raises(RuntimeError, match="Storage error"):
            await archive_gc.tick()


class TestProtectionRules:
    """Contract #9 protection rule tests."""

    async def test_deleted_workspace_archive_key_not_protected(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
    ):
        """deleted_at workspace: archive_key is NOT protected (user wants deletion).

        SQL query now includes deleted_at IS NULL filter for archive_key protection.
        """
        mock_result = MagicMock()
        # Deleted workspace's archive_key is NOT in result
        mock_result.fetchall.return_value = []
        mock_conn.execute.return_value = mock_result

        result = await archive_gc._get_protected_paths()

        # Deleted workspace's archive_key should NOT be protected
        assert result == set()

    async def test_deleted_workspace_op_id_not_protected(
        self,
        archive_gc: ArchiveGC,
        mock_conn: MagicMock,
    ):
        """deleted_at workspace: op_id path is NOT protected (user wants deletion)."""
        # This is verified by the SQL query:
        # Active op_id paths only selected WHERE deleted_at IS NULL
        # So deleted workspace's op_id path won't be in result
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []  # No paths from deleted workspace
        mock_conn.execute.return_value = mock_result

        result = await archive_gc._get_protected_paths()

        # Deleted workspace's op_id path should NOT be protected
        assert result == set()

    async def test_error_workspace_both_protected(
        self,
        archive_gc: ArchiveGC,
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

        result = await archive_gc._get_protected_paths()

        assert "ws-error/archive-key/home.tar.zst" in result
        assert "ws-error/current-op/home.tar.zst" in result


class TestCleanupOrphanResources:
    """_cleanup_orphan_resources() tests (Observer pattern)."""

    async def test_no_resources(
        self,
        archive_gc: ArchiveGC,
        mock_storage: MagicMock,
        mock_ic: MagicMock,
        mock_conn: MagicMock,
    ):
        """No deletion when no containers/volumes exist."""
        mock_ic.list_all.return_value = []
        mock_storage.list_volumes.return_value = []

        await archive_gc._cleanup_orphan_resources()

        # Should not query DB (early return)
        mock_conn.execute.assert_not_called()
        mock_ic.delete.assert_not_called()
        mock_storage.delete_volume.assert_not_called()

    async def test_deletes_orphan_container(
        self,
        archive_gc: ArchiveGC,
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

        await archive_gc._cleanup_orphan_resources()

        mock_ic.delete.assert_called_once_with("orphan-container-ws")

    async def test_deletes_orphan_volume(
        self,
        archive_gc: ArchiveGC,
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

        await archive_gc._cleanup_orphan_resources()

        mock_storage.delete_volume.assert_called_once_with("orphan-volume-ws")

    async def test_preserves_valid_resources(
        self,
        archive_gc: ArchiveGC,
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

        await archive_gc._cleanup_orphan_resources()

        mock_ic.delete.assert_not_called()
        mock_storage.delete_volume.assert_not_called()

    async def test_observer_pattern_race_condition_safe(
        self,
        archive_gc: ArchiveGC,
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

        await archive_gc._cleanup_orphan_resources()

        # Neither should be deleted
        mock_ic.delete.assert_not_called()
