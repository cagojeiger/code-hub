"""Docker job runner implementation for Storage Job."""

import logging
import uuid

from codehub.app.config import get_settings
from codehub.core.interfaces import JobResult, JobRunner
from codehub.core.logging_schema import LogEvent
from codehub.infra.docker import ContainerAPI, ContainerConfig, HostConfig

logger = logging.getLogger(__name__)


class DockerJobRunner(JobRunner):
    """Docker-based job runner for Storage Job.

    Runs archive/restore jobs as Docker containers with:
    - ARCHIVE_URL environment variable (Spec-v2 compliant)
    - Volume mounted at /data
    - Network access to S3/MinIO

    Configuration via DockerConfig (DOCKER_ env prefix).
    """

    def __init__(
        self,
        containers: ContainerAPI | None = None,
        timeout: int | None = None,
    ) -> None:
        settings = get_settings()
        self._runtime = settings.runtime
        self._docker = settings.docker
        self._containers = containers or ContainerAPI()
        self._timeout = timeout or self._docker.job_timeout

    async def run_archive(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run archive job (Volume -> S3)."""
        job_id = uuid.uuid4().hex[:8]
        helper_name = f"codehub-job-archive-{job_id}"

        try:
            config = ContainerConfig(
                image=self._runtime.storage_job_image,
                name=helper_name,
                cmd=["-c", "/usr/local/bin/archive"],
                env=[
                    f"ARCHIVE_URL={archive_url}",
                    f"AWS_ENDPOINT_URL={s3_endpoint}",
                    f"AWS_ACCESS_KEY_ID={s3_access_key}",
                    f"AWS_SECRET_ACCESS_KEY={s3_secret_key}",
                ],
                host_config=HostConfig(
                    network_mode=self._docker.network_name,
                    binds=[f"{volume_name}:/data:ro"],
                ),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            exit_code = await self._containers.wait(helper_name, timeout=self._timeout)
            logs = await self._containers.logs(helper_name)

            logger.info(
                "Archive job completed",
                extra={
                    "event": LogEvent.ARCHIVE_SUCCESS if exit_code == 0 else LogEvent.ARCHIVE_FAILED,
                    "job": helper_name,
                    "exit_code": exit_code,
                },
            )
            return JobResult(exit_code=exit_code, logs=logs.decode("utf-8", errors="replace"))

        finally:
            try:
                await self._containers.remove(helper_name)
            except Exception as e:
                # Log but don't mask the actual job result
                logger.error(
                    "Failed to cleanup archive job container",
                    extra={"event": LogEvent.OPERATION_FAILED, "job": helper_name, "error": str(e)},
                )

    async def run_restore(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run restore job (S3 -> Volume)."""
        job_id = uuid.uuid4().hex[:8]
        helper_name = f"codehub-job-restore-{job_id}"

        try:
            config = ContainerConfig(
                image=self._runtime.storage_job_image,
                name=helper_name,
                cmd=["-c", "/usr/local/bin/restore"],
                env=[
                    f"ARCHIVE_URL={archive_url}",
                    f"AWS_ENDPOINT_URL={s3_endpoint}",
                    f"AWS_ACCESS_KEY_ID={s3_access_key}",
                    f"AWS_SECRET_ACCESS_KEY={s3_secret_key}",
                ],
                host_config=HostConfig(
                    network_mode=self._docker.network_name,
                    binds=[f"{volume_name}:/data"],
                ),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            exit_code = await self._containers.wait(helper_name, timeout=self._timeout)
            logs = await self._containers.logs(helper_name)

            logger.info(
                "Restore job completed",
                extra={
                    "event": LogEvent.RESTORE_SUCCESS if exit_code == 0 else LogEvent.RESTORE_FAILED,
                    "job": helper_name,
                    "exit_code": exit_code,
                },
            )
            return JobResult(exit_code=exit_code, logs=logs.decode("utf-8", errors="replace"))

        finally:
            try:
                await self._containers.remove(helper_name)
            except Exception as e:
                # Log but don't mask the actual job result
                logger.error(
                    "Failed to cleanup restore job container",
                    extra={"event": LogEvent.OPERATION_FAILED, "job": helper_name, "error": str(e)},
                )
