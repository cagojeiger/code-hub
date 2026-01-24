"""Unit tests for StorageManager and DockerRuntime prepare methods."""

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
