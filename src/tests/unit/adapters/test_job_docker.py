"""Unit tests for DockerJobRunner."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from codehub.adapters.job.docker import DockerJobRunner


class TestDockerJobRunner:
    """DockerJobRunner 테스트."""

    @pytest.fixture
    def mock_containers(self) -> AsyncMock:
        """Mock ContainerAPI."""
        mock = AsyncMock()
        mock.create = AsyncMock()
        mock.start = AsyncMock()
        mock.wait = AsyncMock(return_value=0)
        mock.logs = AsyncMock(return_value=b"success")
        mock.remove = AsyncMock()
        return mock

    @pytest.fixture
    def job_runner(self, mock_containers: AsyncMock) -> DockerJobRunner:
        """DockerJobRunner with mocks."""
        with patch("codehub.adapters.job.docker.get_settings") as mock_settings:
            mock_settings.return_value.runtime.storage_job_image = "storage-job:latest"
            mock_settings.return_value.docker.network_name = "codehub"
            mock_settings.return_value.docker.job_timeout = 300
            return DockerJobRunner(containers=mock_containers, timeout=30)

    async def test_archive_success(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Archive job 성공."""
        result = await job_runner.run_archive(
            archive_url="s3://bucket/key",
            volume_name="test-vol",
            s3_endpoint="http://minio:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        )

        assert result.exit_code == 0
        assert result.logs == "success"
        mock_containers.remove.assert_called_once()

    async def test_archive_cleanup_failure_does_not_mask_result(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Cleanup 실패해도 job 결과 정상 반환."""
        # Job 성공, cleanup 실패
        mock_containers.wait.return_value = 0
        mock_containers.logs.return_value = b"job completed"
        mock_containers.remove.side_effect = RuntimeError("remove failed")

        # 예외 발생 안 함
        result = await job_runner.run_archive(
            archive_url="s3://bucket/key",
            volume_name="test-vol",
            s3_endpoint="http://minio:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        )

        # Job 결과 정상 반환
        assert result.exit_code == 0
        assert result.logs == "job completed"

    async def test_restore_success(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Restore job 성공."""
        result = await job_runner.run_restore(
            archive_url="s3://bucket/key",
            volume_name="test-vol",
            s3_endpoint="http://minio:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        )

        assert result.exit_code == 0
        assert result.logs == "success"
        mock_containers.remove.assert_called_once()

    async def test_restore_cleanup_failure_does_not_mask_result(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Restore cleanup 실패해도 job 결과 정상 반환."""
        mock_containers.wait.return_value = 0
        mock_containers.logs.return_value = b"restore completed"
        mock_containers.remove.side_effect = RuntimeError("remove failed")

        result = await job_runner.run_restore(
            archive_url="s3://bucket/key",
            volume_name="test-vol",
            s3_endpoint="http://minio:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        )

        assert result.exit_code == 0
        assert result.logs == "restore completed"

    async def test_job_failure_propagates(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Job 자체 실패는 정상 전파."""
        mock_containers.wait.side_effect = RuntimeError("container wait failed")

        with pytest.raises(RuntimeError, match="container wait failed"):
            await job_runner.run_archive(
                archive_url="s3://bucket/key",
                volume_name="test-vol",
                s3_endpoint="http://minio:9000",
                s3_access_key="key",
                s3_secret_key="secret",
            )

    async def test_archive_nonzero_exit_code(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Archive job 실패 (exit code != 0) 정상 반환."""
        mock_containers.wait.return_value = 1
        mock_containers.logs.return_value = b"error: archive failed"

        result = await job_runner.run_archive(
            archive_url="s3://bucket/key",
            volume_name="test-vol",
            s3_endpoint="http://minio:9000",
            s3_access_key="key",
            s3_secret_key="secret",
        )

        assert result.exit_code == 1
        assert "error: archive failed" in result.logs

    async def test_archive_cancelled_forces_cleanup(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """CancelledError 발생 시 컨테이너 강제 정리."""
        mock_containers.wait.side_effect = asyncio.CancelledError()
        mock_containers.stop = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            await job_runner.run_archive(
                archive_url="s3://bucket/key",
                volume_name="test-vol",
                s3_endpoint="http://minio:9000",
                s3_access_key="key",
                s3_secret_key="secret",
            )

        mock_containers.stop.assert_called_once()

    async def test_restore_cancelled_forces_cleanup(
        self, job_runner: DockerJobRunner, mock_containers: AsyncMock
    ):
        """Restore CancelledError 발생 시 컨테이너 강제 정리."""
        mock_containers.wait.side_effect = asyncio.CancelledError()
        mock_containers.stop = AsyncMock()

        with pytest.raises(asyncio.CancelledError):
            await job_runner.run_restore(
                archive_url="s3://bucket/key",
                volume_name="test-vol",
                s3_endpoint="http://minio:9000",
                s3_access_key="key",
                s3_secret_key="secret",
            )

        mock_containers.stop.assert_called_once()
