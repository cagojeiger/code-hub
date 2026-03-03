"""Unit tests for StorageManager and DockerRuntime prepare methods."""

# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub_agent.api.errors import (
    ArchiveNotFoundError,
    ContainerRunningError,
    VolumeNotFoundError,
)
from codehub_agent.runtimes.docker import DockerRuntime
from codehub_agent.runtimes.docker.instance import InstanceStatus
from codehub_agent.runtimes.docker.job import JobType
from codehub_agent.runtimes.docker.naming import ResourceNaming
from codehub_agent.runtimes.docker.storage import StorageManager
from codehub_agent.runtimes.docker.volume import VolumeStatus
from codehub_agent.logging_schema import LogEvent


class TestPrepareArchive:
    """Tests for DockerRuntime.prepare_archive()."""

    @pytest.fixture
    def runtime(
        self,
        mock_agent_config: MagicMock,
    ) -> DockerRuntime:
        """Create DockerRuntime with mock dependencies."""
        runtime = DockerRuntime(config=mock_agent_config)
        runtime.instances = AsyncMock()
        runtime.volumes = AsyncMock()
        runtime.jobs = AsyncMock()
        runtime.storage = AsyncMock()
        return runtime

    async def test_prepare_archive_success(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_archive returns should_run_job=True when all preconditions pass."""
        # Container is stopped
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        # Volume exists
        runtime.volumes.exists.return_value = VolumeStatus(
            exists=True, name="codehub-ws1-home"
        )
        # No existing job
        runtime.jobs.find_running_job.return_value = None

        result = await runtime.prepare_archive("ws1", "archive-op-1")

        assert result.should_run_job is True
        assert "ws1" in result.archive_key
        assert "archive-op-1" in result.archive_key
        runtime.instances.get_status.assert_called_once_with("ws1")
        runtime.volumes.exists.assert_called_once_with("ws1")
        runtime.jobs.find_running_job.assert_called_once_with(
            "ws1", JobType.ARCHIVE, "archive-op-1"
        )

    async def test_prepare_archive_container_running(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_archive raises ContainerRunningError when container is running."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=True, healthy=True, reason="running", message=""
        )

        with pytest.raises(ContainerRunningError) as exc_info:
            await runtime.prepare_archive("ws1", "archive-op-1")

        assert "running" in str(exc_info.value).lower()
        runtime.volumes.exists.assert_not_called()
        runtime.jobs.find_running_job.assert_not_called()

    async def test_prepare_archive_volume_not_found(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_archive raises VolumeNotFoundError when volume doesn't exist."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        runtime.volumes.exists.return_value = VolumeStatus(
            exists=False, name="codehub-ws1-home"
        )

        with pytest.raises(VolumeNotFoundError) as exc_info:
            await runtime.prepare_archive("ws1", "archive-op-1")

        assert "ws1" in str(exc_info.value)
        runtime.jobs.find_running_job.assert_not_called()

    async def test_prepare_archive_job_already_running(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_archive returns should_run_job=False when job exists."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        runtime.volumes.exists.return_value = VolumeStatus(
            exists=True, name="codehub-ws1-home"
        )
        runtime.jobs.find_running_job.return_value = {"Id": "existing-job-container"}

        result = await runtime.prepare_archive("ws1", "archive-op-1")

        assert result.should_run_job is False
        assert result.archive_key is not None


class TestPrepareRestore:
    """Tests for DockerRuntime.prepare_restore()."""

    @pytest.fixture
    def runtime(
        self,
        mock_agent_config: MagicMock,
    ) -> DockerRuntime:
        """Create DockerRuntime with mock dependencies."""
        runtime = DockerRuntime(config=mock_agent_config)
        runtime.instances = AsyncMock()
        runtime.volumes = AsyncMock()
        runtime.jobs = AsyncMock()
        runtime.storage = AsyncMock()
        return runtime

    async def test_prepare_restore_success(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_restore returns should_run_job=True when all preconditions pass."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        runtime.storage.archive_exists.return_value = True
        runtime.volumes.exists.return_value = VolumeStatus(
            exists=True, name="codehub-ws1-home"
        )
        runtime.jobs.find_running_job.return_value = None

        result = await runtime.prepare_restore(
            "ws1", "codehub-ws1/archive-op-1/home.tar.zst", "restore-op-1"
        )

        assert result.should_run_job is True
        assert result.restore_marker == "restore-op-1"
        runtime.instances.get_status.assert_called_once_with("ws1")
        runtime.storage.archive_exists.assert_called_once_with(
            "codehub-ws1/archive-op-1/home.tar.zst"
        )
        runtime.volumes.exists.assert_called_once_with("ws1")
        runtime.volumes.create.assert_not_called()
        runtime.jobs.find_running_job.assert_called_once_with("ws1", JobType.RESTORE)

    async def test_prepare_restore_container_running(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_restore raises ContainerRunningError when container is running."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=True, healthy=True, reason="running", message=""
        )

        with pytest.raises(ContainerRunningError) as exc_info:
            await runtime.prepare_restore(
                "ws1", "codehub-ws1/archive-op-1/home.tar.zst", "restore-op-1"
            )

        assert "running" in str(exc_info.value).lower()
        runtime.storage.archive_exists.assert_not_called()

    async def test_prepare_restore_archive_not_found(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_restore raises ArchiveNotFoundError when archive doesn't exist."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        runtime.storage.archive_exists.return_value = False

        with pytest.raises(ArchiveNotFoundError) as exc_info:
            await runtime.prepare_restore(
                "ws1", "codehub-ws1/archive-op-1/home.tar.zst", "restore-op-1"
            )

        assert "archive" in str(exc_info.value).lower()
        runtime.volumes.exists.assert_not_called()

    async def test_prepare_restore_auto_creates_volume(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_restore creates volume if it doesn't exist."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        runtime.storage.archive_exists.return_value = True
        runtime.volumes.exists.return_value = VolumeStatus(
            exists=False, name="codehub-ws1-home"
        )
        runtime.jobs.find_running_job.return_value = None

        result = await runtime.prepare_restore(
            "ws1", "codehub-ws1/archive-op-1/home.tar.zst", "restore-op-1"
        )

        assert result.should_run_job is True
        runtime.volumes.create.assert_called_once_with("ws1")

    async def test_prepare_restore_job_already_running(
        self,
        runtime: DockerRuntime,
    ) -> None:
        """Test prepare_restore returns should_run_job=False when job exists."""
        runtime.instances.get_status.return_value = InstanceStatus(
            exists=True, running=False, healthy=False, reason="stopped", message=""
        )
        runtime.storage.archive_exists.return_value = True
        runtime.volumes.exists.return_value = VolumeStatus(
            exists=True, name="codehub-ws1-home"
        )
        runtime.jobs.find_running_job.return_value = {"Id": "existing-job-container"}

        result = await runtime.prepare_restore(
            "ws1", "codehub-ws1/archive-op-1/home.tar.zst", "restore-op-1"
        )

        assert result.should_run_job is False


class TestDeleteWorkspaceMarkers:
    """Tests for StorageManager.delete_workspace_markers()."""

    @pytest.fixture
    def mock_s3(self) -> AsyncMock:
        """Create mock S3Operations."""
        s3 = AsyncMock()
        s3.list_objects = AsyncMock(return_value=[])
        s3.delete_objects = AsyncMock(return_value=[])
        return s3

    @pytest.fixture
    def storage_manager(
        self,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
        mock_s3: AsyncMock,
    ) -> StorageManager:
        """Create StorageManager with mock dependencies."""
        return StorageManager(mock_agent_config, mock_naming, mock_s3)

    async def test_delete_markers_success(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        """Test delete_workspace_markers deletes marker files."""
        mock_s3.list_objects.return_value = [
            "codehub-ws1/.restore_marker",
            "codehub-ws1/.restore_error",
            "codehub-ws1/archive-1/.error",
        ]
        mock_s3.delete_objects.return_value = [
            "codehub-ws1/.restore_marker",
            "codehub-ws1/.restore_error",
            "codehub-ws1/archive-1/.error",
        ]

        result = await storage_manager.delete_workspace_markers("ws1")

        assert result == 3
        mock_s3.list_objects.assert_called_once_with("codehub-ws1/")
        mock_s3.delete_objects.assert_called_once_with([
            "codehub-ws1/.restore_marker",
            "codehub-ws1/.restore_error",
            "codehub-ws1/archive-1/.error",
        ])

    async def test_delete_markers_no_markers(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        """Test delete_workspace_markers returns 0 when no markers exist."""
        mock_s3.list_objects.return_value = []

        result = await storage_manager.delete_workspace_markers("ws1")

        assert result == 0
        mock_s3.list_objects.assert_called_once_with("codehub-ws1/")
        mock_s3.delete_objects.assert_not_called()

    async def test_delete_markers_filters_non_markers(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        """Test delete_workspace_markers only deletes marker files."""
        mock_s3.list_objects.return_value = [
            "codehub-ws1/.restore_marker",
            "codehub-ws1/archive-1/home.tar.zst",  # Not a marker
            "codehub-ws1/config.json",  # Not a marker
            "codehub-ws1/archive-2/.error",
        ]
        mock_s3.delete_objects.return_value = [
            "codehub-ws1/.restore_marker",
            "codehub-ws1/archive-2/.error",
        ]

        result = await storage_manager.delete_workspace_markers("ws1")

        assert result == 2
        mock_s3.delete_objects.assert_called_once_with([
            "codehub-ws1/.restore_marker",
            "codehub-ws1/archive-2/.error",
        ])

    async def test_delete_markers_empty_workspace(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        """Test delete_workspace_markers handles empty workspace."""
        mock_s3.list_objects.return_value = []

        result = await storage_manager.delete_workspace_markers("ws1")

        assert result == 0
        mock_s3.delete_objects.assert_not_called()


class TestGetArchivePattern:
    def test_pattern_matches_valid_key(
        self,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
        mock_s3: AsyncMock,
    ) -> None:
        manager = StorageManager(mock_agent_config, mock_naming, mock_s3)
        pattern = manager._get_archive_pattern("codehub-", "home.tar.zst")
        match = pattern.match("codehub-ws-1/op-1/home.tar.zst")

        assert match is not None
        assert match.group(1) == "ws-1"
        assert match.group(2) == "op-1"

    def test_pattern_caching(
        self,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
        mock_s3: AsyncMock,
    ) -> None:
        manager = StorageManager(mock_agent_config, mock_naming, mock_s3)
        pattern1 = manager._get_archive_pattern("codehub-", "home.tar.zst")
        pattern2 = manager._get_archive_pattern("codehub-", "home.tar.zst")

        assert pattern1 is pattern2
        assert len(manager._archive_pattern_cache) == 1


class TestStorageManagerInit:
    def test_init_with_provided_s3(
        self,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
        mock_s3: AsyncMock,
    ) -> None:
        manager = StorageManager(mock_agent_config, mock_naming, mock_s3)

        assert manager._s3 is mock_s3


class TestRunGc:
    async def test_gc_empty_bucket(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.list_objects_with_metadata.return_value = []
        mock_s3.delete_objects.return_value = []

        deleted_count, deleted_keys = await storage_manager.run_gc([], [])

        assert deleted_count == 0
        assert deleted_keys == []
        mock_s3.delete_objects.assert_called_once_with([])

    async def test_gc_under_retention_no_delete(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(hours=1)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now),
        ]
        mock_s3.delete_objects.return_value = []

        deleted_count, deleted_keys = await storage_manager.run_gc([], [], retention_count=3)

        assert deleted_count == 0
        assert deleted_keys == []
        mock_s3.delete_objects.assert_called_once_with([])

    async def test_gc_over_retention_deletes_oldest(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=4)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws1/op-4/home.tar.zst", now - timedelta(days=1)),
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = ["codehub-ws1/op-1/home.tar.zst"]

        deleted_count, deleted_keys = await storage_manager.run_gc([], [], retention_count=3)

        assert deleted_count == 1
        assert deleted_keys == ["codehub-ws1/op-1/home.tar.zst"]
        mock_s3.delete_objects.assert_called_once_with(["codehub-ws1/op-1/home.tar.zst"])

    async def test_gc_protects_restoring_archives(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=1)),
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = []

        deleted_count, deleted_keys = await storage_manager.run_gc(
            ["codehub-ws1/op-1/home.tar.zst"],
            [],
            retention_count=2,
        )

        assert deleted_count == 0
        assert deleted_keys == []
        mock_s3.delete_objects.assert_called_once_with([])

    async def test_gc_protects_archiving_workspaces(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=1)),
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = []

        deleted_count, deleted_keys = await storage_manager.run_gc(
            [],
            [("ws1", "op-1")],
            retention_count=2,
        )

        assert deleted_count == 0
        assert deleted_keys == []
        mock_s3.delete_objects.assert_called_once_with([])

    async def test_gc_skips_restore_markers(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-1/.restore_marker", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=1)),
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = ["codehub-ws1/op-1/home.tar.zst"]

        deleted_count, deleted_keys = await storage_manager.run_gc([], [], retention_count=2)

        assert deleted_count == 1
        assert deleted_keys == ["codehub-ws1/op-1/home.tar.zst"]
        mock_s3.delete_objects.assert_called_once_with(["codehub-ws1/op-1/home.tar.zst"])

    async def test_gc_multiple_workspaces(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=4)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws2/op-a/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws2/op-b/home.tar.zst", now - timedelta(days=1)),
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = ["codehub-ws1/op-1/home.tar.zst"]

        deleted_count, deleted_keys = await storage_manager.run_gc([], [], retention_count=2)

        assert deleted_count == 1
        assert deleted_keys == ["codehub-ws1/op-1/home.tar.zst"]

    async def test_gc_custom_retention_count(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=1)),
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = [
            "codehub-ws1/op-1/home.tar.zst",
            "codehub-ws1/op-2/home.tar.zst",
        ]

        deleted_count, deleted_keys = await storage_manager.run_gc([], [], retention_count=1)

        assert deleted_count == 2
        assert set(deleted_keys) == {
            "codehub-ws1/op-1/home.tar.zst",
            "codehub-ws1/op-2/home.tar.zst",
        }

    async def test_gc_returns_deleted_keys(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-2/home.tar.zst", now - timedelta(days=2)),
            make_s3_object_info("codehub-ws1/op-3/home.tar.zst", now - timedelta(days=1)),
        ]
        returned_deleted = ["codehub-ws1/op-1/home.tar.zst"]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = returned_deleted

        result = await storage_manager.run_gc([], [], retention_count=2)

        assert result == (1, returned_deleted)

    async def test_gc_deletes_all_keys_under_prefix(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        objects = [
            make_s3_object_info("codehub-ws1/op-old/home.tar.zst", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-old/home.tar.zst.meta", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-old/chunk-1", now - timedelta(days=3)),
            make_s3_object_info("codehub-ws1/op-new/home.tar.zst", now - timedelta(days=1)),
        ]
        expected_deleted = [
            "codehub-ws1/op-old/home.tar.zst",
            "codehub-ws1/op-old/home.tar.zst.meta",
            "codehub-ws1/op-old/chunk-1",
        ]
        mock_s3.list_objects_with_metadata.return_value = objects
        mock_s3.delete_objects.return_value = expected_deleted

        deleted_count, deleted_keys = await storage_manager.run_gc([], [], retention_count=1)

        assert deleted_count == 3
        assert deleted_keys == expected_deleted
        mock_s3.delete_objects.assert_called_once_with(expected_deleted)


class TestListArchives:
    async def test_list_archives_empty(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.list_objects_with_metadata.return_value = []

        archives = await storage_manager.list_archives()

        assert archives == []

    async def test_list_archives_single_complete(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        key = "codehub-ws1/op-1/home.tar.zst"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(key, now),
            make_s3_object_info(f"{key}.meta", now),
        ]

        archives = await storage_manager.list_archives()

        assert len(archives) == 1
        assert archives[0].workspace_id == "ws1"
        assert archives[0].archive_key == key
        assert archives[0].archive_op_id == "op-1"

    async def test_list_archives_incomplete_skipped(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info("codehub-ws1/op-1/home.tar.zst", datetime.now(timezone.utc)),
        ]

        archives = await storage_manager.list_archives()

        assert archives == []

    async def test_list_archives_latest_per_workspace(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        older = "codehub-ws1/op-1/home.tar.zst"
        newer = "codehub-ws1/op-2/home.tar.zst"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(older, now - timedelta(hours=2)),
            make_s3_object_info(f"{older}.meta", now - timedelta(hours=2)),
            make_s3_object_info(newer, now - timedelta(hours=1)),
            make_s3_object_info(f"{newer}.meta", now - timedelta(hours=1)),
        ]

        archives = await storage_manager.list_archives()

        assert len(archives) == 1
        assert archives[0].archive_key == newer

    async def test_list_archives_multiple_workspaces(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        ws1 = "codehub-ws1/op-1/home.tar.zst"
        ws2 = "codehub-ws2/op-2/home.tar.zst"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(ws1, now),
            make_s3_object_info(f"{ws1}.meta", now),
            make_s3_object_info(ws2, now),
            make_s3_object_info(f"{ws2}.meta", now),
        ]

        archives = await storage_manager.list_archives()

        assert len(archives) == 2
        assert {a.workspace_id for a in archives} == {"ws1", "ws2"}

    async def test_list_archives_extracts_op_id(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        key = "codehub-ws-id/archive-op-id/home.tar.zst"
        now = datetime.now(timezone.utc)
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(key, now),
            make_s3_object_info(f"{key}.meta", now),
        ]

        archives = await storage_manager.list_archives()

        assert len(archives) == 1
        assert archives[0].archive_op_id == "archive-op-id"

    async def test_list_archives_with_prefix_filter(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        key = "codehub-ws1/op-1/home.tar.zst"
        now = datetime.now(timezone.utc)
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(key, now),
            make_s3_object_info(f"{key}.meta", now),
        ]

        archives = await storage_manager.list_archives(prefix="ws1")

        assert len(archives) == 1
        assert archives[0].workspace_id == "ws1"


class TestListArchivesAndMarkers:
    async def test_combined_empty(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.list_objects_with_metadata.return_value = []

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert restore_markers == []
        assert error_markers == []

    async def test_combined_archives_only(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        key = "codehub-ws1/op-1/home.tar.zst"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(key, now),
            make_s3_object_info(f"{key}.meta", now),
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert len(archives) == 1
        assert restore_markers == []
        assert error_markers == []

    async def test_combined_with_restore_markers(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        marker_key = "codehub-ws1/.restore_marker"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(marker_key, datetime.now(timezone.utc)),
        ]
        mock_s3.get_object.side_effect = [
            json.dumps({
                "restore_op_id": "restore-1",
                "archive_key": "codehub-ws1/op-1/home.tar.zst",
                "restored_at": "2026-01-01T00:00:00Z",
            }).encode()
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert len(restore_markers) == 1
        assert restore_markers[0].workspace_id == "ws1"
        assert restore_markers[0].restore_op_id == "restore-1"
        assert error_markers == []

    async def test_combined_with_archive_errors(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        error_key = "codehub-ws1/op-1/.error"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(error_key, datetime.now(timezone.utc)),
        ]
        mock_s3.get_object.side_effect = [
            json.dumps({
                "operation": "archive",
                "error_code": 500,
                "error_at": "2026-01-01T00:00:00Z",
                "archive_op_id": "op-1",
            }).encode()
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert restore_markers == []
        assert len(error_markers) == 1
        assert error_markers[0].operation == "archive"
        assert error_markers[0].archive_op_id == "op-1"

    async def test_combined_with_restore_errors(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        error_key = "codehub-ws1/.restore_error"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(error_key, datetime.now(timezone.utc)),
        ]
        mock_s3.get_object.side_effect = [
            json.dumps({
                "operation": "restore",
                "error_code": 404,
                "error_at": "2026-01-01T00:00:00Z",
                "restore_op_id": "restore-1",
                "archive_key": "codehub-ws1/op-1/home.tar.zst",
            }).encode()
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert restore_markers == []
        assert len(error_markers) == 1
        assert error_markers[0].operation == "restore"
        assert error_markers[0].restore_op_id == "restore-1"

    async def test_combined_all_types(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        archive_key = "codehub-ws1/op-1/home.tar.zst"
        restore_marker_key = "codehub-ws2/.restore_marker"
        archive_error_key = "codehub-ws3/op-3/.error"
        restore_error_key = "codehub-ws4/.restore_error"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(archive_key, now),
            make_s3_object_info(f"{archive_key}.meta", now),
            make_s3_object_info(restore_marker_key, now),
            make_s3_object_info(archive_error_key, now),
            make_s3_object_info(restore_error_key, now),
        ]
        mock_s3.get_object.side_effect = [
            json.dumps({
                "restore_op_id": "restore-2",
                "archive_key": "codehub-ws2/op-2/home.tar.zst",
            }).encode(),
            json.dumps({
                "operation": "archive",
                "error_code": 501,
                "error_at": "2026-01-01T00:00:00Z",
                "archive_op_id": "op-3",
            }).encode(),
            json.dumps({
                "operation": "restore",
                "error_code": 400,
                "error_at": "2026-01-01T00:00:00Z",
                "restore_op_id": "restore-4",
                "archive_key": "codehub-ws4/op-4/home.tar.zst",
            }).encode(),
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert len(archives) == 1
        assert len(restore_markers) == 1
        assert len(error_markers) == 2

    async def test_combined_marker_parse_failure(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(LogEvent, "S3_GET_FAILED", "s3_get_failed", raising=False)
        marker_key = "codehub-ws1/.restore_marker"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(marker_key, datetime.now(timezone.utc)),
        ]
        mock_s3.get_object.side_effect = [b"not-json"]

        with caplog.at_level("WARNING"):
            archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert restore_markers == []
        assert error_markers == []
        assert "Failed to parse restore marker" in caplog.text

    async def test_combined_s3_get_failure(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        marker_key = "codehub-ws1/.restore_marker"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(marker_key, datetime.now(timezone.utc)),
        ]
        mock_s3.get_object.side_effect = [RuntimeError("boom")]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert restore_markers == []
        assert error_markers == []

    async def test_combined_incomplete_archive_skipped(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        key = "codehub-ws1/op-1/home.tar.zst"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(key, datetime.now(timezone.utc)),
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert archives == []
        assert restore_markers == []
        assert error_markers == []

    async def test_combined_multiple_archives_latest(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        make_s3_object_info,
    ) -> None:
        now = datetime.now(timezone.utc)
        old_key = "codehub-ws1/op-1/home.tar.zst"
        new_key = "codehub-ws1/op-2/home.tar.zst"
        mock_s3.list_objects_with_metadata.return_value = [
            make_s3_object_info(old_key, now - timedelta(hours=2)),
            make_s3_object_info(f"{old_key}.meta", now - timedelta(hours=2)),
            make_s3_object_info(new_key, now - timedelta(hours=1)),
            make_s3_object_info(f"{new_key}.meta", now - timedelta(hours=1)),
        ]

        archives, restore_markers, error_markers = await storage_manager.list_archives_and_markers()

        assert len(archives) == 1
        assert archives[0].archive_key == new_key
        assert restore_markers == []
        assert error_markers == []


class TestListAllArchiveKeys:
    async def test_list_all_keys_returns_set(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.list_objects.return_value = [
            "codehub-ws1/op-1/home.tar.zst",
            "codehub-ws1/op-1/home.tar.zst",
            "codehub-ws2/op-2/home.tar.zst",
        ]

        keys = await storage_manager.list_all_archive_keys()

        assert keys == {
            "codehub-ws1/op-1/home.tar.zst",
            "codehub-ws2/op-2/home.tar.zst",
        }

    async def test_list_all_keys_empty(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.list_objects.return_value = []

        keys = await storage_manager.list_all_archive_keys()

        assert keys == set()


class TestDeleteArchive:
    async def test_delete_archive_success(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.delete_object.return_value = True

        result = await storage_manager.delete_archive("codehub-ws1/op-1/home.tar.zst")

        assert result is True

    async def test_delete_archive_failure(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.delete_object.return_value = False

        result = await storage_manager.delete_archive("codehub-ws1/op-1/home.tar.zst")

        assert result is False

    async def test_delete_archive_deletes_with_meta(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        key = "codehub-ws1/op-1/home.tar.zst"
        mock_s3.delete_object.return_value = True

        result = await storage_manager.delete_archive(key)

        assert result is True
        mock_s3.delete_object.assert_awaited_once_with(key)


class TestArchiveExists:
    async def test_archive_exists_true(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.object_exists.return_value = True

        result = await storage_manager.archive_exists("codehub-ws1/op-1/home.tar.zst")

        assert result is True

    async def test_archive_exists_false(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.object_exists.return_value = False

        result = await storage_manager.archive_exists("codehub-ws1/op-1/home.tar.zst")

        assert result is False


class TestListRestoreMarkers:
    async def test_list_restore_markers_empty(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        mock_s3.list_objects.return_value = []

        markers = await storage_manager.list_restore_markers()

        assert markers == []

    async def test_list_restore_markers_single(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        key = "codehub-ws1/.restore_marker"
        mock_s3.list_objects.return_value = [key]
        mock_s3.get_object.return_value = json.dumps({
            "restore_op_id": "restore-1",
            "archive_key": "codehub-ws1/op-1/home.tar.zst",
            "restored_at": "2026-01-01T00:00:00Z",
        }).encode()

        markers = await storage_manager.list_restore_markers()

        assert len(markers) == 1
        assert markers[0].workspace_id == "ws1"
        assert markers[0].restore_op_id == "restore-1"
        assert markers[0].archive_key == "codehub-ws1/op-1/home.tar.zst"

    async def test_list_restore_markers_multiple(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        keys = ["codehub-ws1/.restore_marker", "codehub-ws2/.restore_marker"]
        mock_s3.list_objects.return_value = keys
        mock_s3.get_object.side_effect = [
            json.dumps({"restore_op_id": "r1", "archive_key": "a1"}).encode(),
            json.dumps({"restore_op_id": "r2", "archive_key": "a2"}).encode(),
        ]

        markers = await storage_manager.list_restore_markers()

        assert len(markers) == 2
        assert {m.workspace_id for m in markers} == {"ws1", "ws2"}

    async def test_list_restore_markers_parse_failure(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(LogEvent, "S3_GET_FAILED", "s3_get_failed", raising=False)
        key = "codehub-ws1/.restore_marker"
        mock_s3.list_objects.return_value = [key]
        mock_s3.get_object.return_value = b"not-json"

        with caplog.at_level("WARNING"):
            markers = await storage_manager.list_restore_markers()

        assert markers == []
        assert "Failed to read restore marker" in caplog.text

    async def test_list_restore_markers_fetch_failure(
        self,
        storage_manager: StorageManager,
        mock_s3: AsyncMock,
    ) -> None:
        key = "codehub-ws1/.restore_marker"
        mock_s3.list_objects.return_value = [key]
        mock_s3.get_object.return_value = None

        markers = await storage_manager.list_restore_markers()

        assert markers == []
