"""Unit tests for JobRunner."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codehub_agent.api.errors import JobFailedError
from codehub_agent.runtimes.docker.job import JobRunner, JobResult, JobType
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

        # Per spec L229: Job receives archive_key directly (not op_id)
        archive_key = "codehub-ws1/op123/home.tar.zst"
        result = await runner.run_restore("ws1", archive_key)

        assert result.status == OperationStatus.COMPLETED
        assert result.restore_marker == archive_key

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
        result = await runner.run_restore("ws1", archive_key)

        assert result.status == OperationStatus.IN_PROGRESS
        assert result.restore_marker is None
        # Should NOT create new container
        mock_container_api.create.assert_not_called()
