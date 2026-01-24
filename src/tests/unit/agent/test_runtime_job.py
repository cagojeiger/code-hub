"""Unit tests for JobRunner."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codehub_agent.api.errors import JobFailedError
from codehub_agent.runtimes.docker.job import (
    STUCK_THRESHOLD_SECONDS,
    JobRunner,
    JobResult,
    JobType,
)
from codehub_agent.runtimes.docker.naming import ResourceNaming
from codehub_agent.runtimes.docker.result import OperationStatus


class TestJobRunner:
    """Tests for JobRunner."""

    @pytest.fixture
    def runner(
        self,
        mock_container_api: AsyncMock,
        mock_agent_config: MagicMock,
        mock_naming: ResourceNaming,
    ) -> JobRunner:
        """Create JobRunner with mock dependencies."""
        return JobRunner(
            config=mock_agent_config,
            naming=mock_naming,
            containers=mock_container_api,
            timeout=300,
        )

    async def test_run_archive_success(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test run_archive creates and runs archive job."""
        # No existing job running
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b"Archive completed"

        result = await runner.run_archive("ws1", "op123")

        assert result.status == OperationStatus.COMPLETED
        assert result.archive_key == "codehub-ws1/op123/home.tar.zst"

        # Verify container was created with correct config
        mock_container_api.create.assert_called_once()
        call_args = mock_container_api.create.call_args[0][0]
        assert "archive" in call_args.cmd[-1]
        assert any("ro" in bind for bind in call_args.host_config.binds)

        # Verify cleanup
        mock_container_api.remove.assert_called_once()

    async def test_run_restore_success(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test run_restore creates and runs restore job with archive_key."""
        # No existing job running
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b"Restore completed"

        # Per spec: Job receives archive_key and restore_op_id
        archive_key = "codehub-ws1/op123/home.tar.zst"
        restore_op_id = "restore-456"
        result = await runner.run_restore("ws1", archive_key, restore_op_id)

        assert result.status == OperationStatus.COMPLETED
        assert result.restore_marker == restore_op_id

        # Verify container was created with correct config
        mock_container_api.create.assert_called_once()
        call_args = mock_container_api.create.call_args[0][0]
        assert "restore" in call_args.cmd[-1]
        # Restore should NOT have read-only mount
        assert not any("ro" in bind for bind in call_args.host_config.binds)

        # Verify ARCHIVE_URL is constructed from archive_key
        env_dict = {e.split("=")[0]: e.split("=", 1)[1] for e in call_args.env}
        assert "ARCHIVE_URL" in env_dict
        assert archive_key in env_dict["ARCHIVE_URL"]

    async def test_run_job_nonzero_exit(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test job raises JobFailedError on nonzero exit code."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 1
        mock_container_api.logs.return_value = b"Error occurred"

        with pytest.raises(JobFailedError):
            await runner.run_archive("ws1", "op123")

    async def test_run_job_cleanup_on_success(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test job container is cleaned up on success."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b""

        await runner.run_archive("ws1", "op123")

        mock_container_api.remove.assert_called_once()

    async def test_run_job_cleanup_on_failure(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test job container is cleaned up on failure."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.side_effect = Exception("Wait failed")

        with pytest.raises(Exception, match="Wait failed"):
            await runner.run_archive("ws1", "op123")

        mock_container_api.remove.assert_called_once()

    async def test_run_job_env_variables(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
        mock_agent_config: MagicMock,
    ) -> None:
        """Test job container has correct env variables."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b""

        await runner.run_archive("ws1", "op123")

        call_args = mock_container_api.create.call_args[0][0]
        env_dict = {e.split("=")[0]: e.split("=", 1)[1] for e in call_args.env}

        assert "ARCHIVE_URL" in env_dict
        assert "ws1" in env_dict["ARCHIVE_URL"]
        assert "op123" in env_dict["ARCHIVE_URL"]
        assert "AWS_ENDPOINT_URL" in env_dict
        assert "AWS_ACCESS_KEY_ID" in env_dict
        assert "AWS_SECRET_ACCESS_KEY" in env_dict

    async def test_archive_url_format(
        self,
        mock_naming: ResourceNaming,
    ) -> None:
        """Test archive URL is correctly formatted."""
        url = mock_naming.archive_s3_url("ws1", "op123")

        assert url == "s3://test-bucket/codehub-ws1/op123/home.tar.zst"

    async def test_volume_name_format(
        self,
        mock_naming: ResourceNaming,
    ) -> None:
        """Test volume name is correctly formatted."""
        name = mock_naming.volume_name("ws1")

        assert name == "codehub-ws1-home"

    async def test_run_job_container_name_format(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test job container name has correct format."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b""

        await runner.run_archive("ws1", "op123")

        call_args = mock_container_api.create.call_args[0][0]
        assert call_args.name.startswith("codehub-job-archive-")

    async def test_run_job_cancel_cleanup(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test job container is force cleaned up on cancellation."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.side_effect = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await runner.run_archive("ws1", "op123")

        # Force cleanup calls stop then remove with force
        mock_container_api.stop.assert_called_once()
        # At least one remove call (force cleanup or regular)
        assert mock_container_api.remove.call_count >= 1

    async def test_run_archive_in_progress(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test run_archive returns in_progress when job already running."""
        # Simulate existing running job
        mock_container_api.list.return_value = [
            {"Names": ["/codehub-job-archive-abc123"], "State": "running"}
        ]

        result = await runner.run_archive("ws1", "op123")

        assert result.status == OperationStatus.IN_PROGRESS
        assert result.archive_key is None
        # Should NOT create new container
        mock_container_api.create.assert_not_called()

    async def test_run_restore_in_progress(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test run_restore returns in_progress when job already running."""
        # Simulate existing running job
        mock_container_api.list.return_value = [
            {"Names": ["/codehub-job-restore-abc123"], "State": "running"}
        ]

        archive_key = "codehub-ws1/op123/home.tar.zst"
        restore_op_id = "restore-456"
        result = await runner.run_restore("ws1", archive_key, restore_op_id)

        assert result.status == OperationStatus.IN_PROGRESS
        assert result.restore_marker is None
        # Should NOT create new container
        mock_container_api.create.assert_not_called()

    async def test_archive_op_id_label_in_container(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test archive_op_id label is added to job container."""
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b""

        await runner.run_archive("ws1", "op123")

        call_args = mock_container_api.create.call_args[0][0]
        assert call_args.labels["codehub.workspace_id"] == "ws1"
        assert call_args.labels["codehub.job_type"] == "archive"
        assert call_args.labels["codehub.archive_op_id"] == "op123"

    async def test_find_running_job_filters_by_archive_op_id(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test find_running_job filters by archive_op_id when provided."""
        # No containers match the specific archive_op_id
        mock_container_api.list.return_value = []

        result = await runner.find_running_job("ws1", JobType.ARCHIVE, "op123")

        assert result is None
        # Verify filter includes archive_op_id
        call_args = mock_container_api.list.call_args
        filters = call_args[1]["filters"]
        assert "codehub.archive_op_id=op123" in filters["label"]

    async def test_find_running_job_cleans_stuck_container(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test find_running_job removes stuck 'created' containers."""
        # Simulate stuck container (created 10 minutes ago)
        stuck_time = int(time.time()) - (STUCK_THRESHOLD_SECONDS + 60)
        mock_container_api.list.return_value = [
            {
                "Id": "stuck123",
                "Names": ["/codehub-job-archive-stuck"],
                "State": "created",
                "Created": stuck_time,
            }
        ]

        result = await runner.find_running_job("ws1", JobType.ARCHIVE)

        # Stuck container should be removed
        mock_container_api.remove.assert_called_once_with("stuck123", force=True)
        # No valid container found after cleanup
        assert result is None

    async def test_find_running_job_keeps_recent_created_container(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test find_running_job keeps recently created containers."""
        # Simulate recently created container (1 minute ago)
        recent_time = int(time.time()) - 60
        mock_container_api.list.return_value = [
            {
                "Id": "recent123",
                "Names": ["/codehub-job-archive-recent"],
                "State": "created",
                "Created": recent_time,
            }
        ]

        result = await runner.find_running_job("ws1", JobType.ARCHIVE)

        # Recent container should NOT be removed
        mock_container_api.remove.assert_not_called()
        # Container should be returned
        assert result is not None
        assert result["Id"] == "recent123"

    async def test_run_archive_different_op_id_starts_new_job(
        self,
        runner: JobRunner,
        mock_container_api: AsyncMock,
    ) -> None:
        """Test run_archive starts new job for different archive_op_id."""
        # First call: no existing job
        mock_container_api.list.return_value = []
        mock_container_api.wait.return_value = 0
        mock_container_api.logs.return_value = b""

        result = await runner.run_archive("ws1", "op-new")

        assert result.status == OperationStatus.COMPLETED
        mock_container_api.create.assert_called_once()

    async def test_extract_archive_op_id(
        self,
        runner: JobRunner,
    ) -> None:
        """Test _extract_archive_op_id parses archive_key correctly."""
        # Standard format
        assert runner._extract_archive_op_id("codehub-ws1/op123/home.tar.zst") == "op123"
        # With prefix
        assert runner._extract_archive_op_id("prefix/codehub-ws1/op456/home.tar.zst") == "op456"
        # Invalid format
        assert runner._extract_archive_op_id("invalid-key") is None
        assert runner._extract_archive_op_id("") is None
