"""Docker job runner for Agent."""

import asyncio
import logging
import uuid
from enum import Enum

from pydantic import BaseModel

from codehub_agent.config import get_agent_config
from codehub_agent.infra import ContainerAPI, ContainerConfig, HostConfig

logger = logging.getLogger(__name__)


class JobResult(BaseModel):
    """Job execution result."""

    exit_code: int
    logs: str


class JobType(str, Enum):
    """Job type enumeration."""

    ARCHIVE = "archive"
    RESTORE = "restore"


class JobRunner:
    """Docker job runner for archive/restore operations."""

    def __init__(
        self,
        containers: ContainerAPI | None = None,
        timeout: int | None = None,
    ) -> None:
        self._config = get_agent_config()
        self._containers = containers or ContainerAPI()
        self._timeout = timeout or self._config.job_timeout

    def _volume_name(self, workspace_id: str) -> str:
        return f"{self._config.resource_prefix}{workspace_id}-home"

    def _archive_url(self, workspace_id: str, op_id: str) -> str:
        """Build S3 archive URL."""
        return (
            f"s3://{self._config.s3_bucket}/"
            f"{self._config.cluster_id}/{workspace_id}/{op_id}/home.tar.zst"
        )

    async def _force_cleanup(self, container_name: str) -> None:
        """Force stop and remove container."""
        try:
            await self._containers.stop(container_name, timeout=5)
        except Exception:
            pass
        try:
            await self._containers.remove(container_name, force=True)
        except Exception as e:
            logger.warning("Force cleanup failed for %s: %s", container_name, e)

    async def _run_job(
        self,
        job_type: JobType,
        workspace_id: str,
        op_id: str,
    ) -> JobResult:
        """Run a job (archive or restore).

        Args:
            job_type: Type of job to run (archive or restore).
            workspace_id: Workspace identifier.
            op_id: Operation identifier for S3 path.

        Returns:
            JobResult with exit code and logs.
        """
        job_id = uuid.uuid4().hex[:8]
        helper_name = f"codehub-job-{job_type.value}-{job_id}"
        volume_name = self._volume_name(workspace_id)
        archive_url = self._archive_url(workspace_id, op_id)

        # Archive: read-only mount, Restore: read-write mount
        volume_bind = (
            f"{volume_name}:/data:ro"
            if job_type == JobType.ARCHIVE
            else f"{volume_name}:/data"
        )

        try:
            config = ContainerConfig(
                image=self._config.storage_job_image,
                name=helper_name,
                cmd=["-c", f"/usr/local/bin/{job_type.value}"],
                env=[
                    f"ARCHIVE_URL={archive_url}",
                    f"AWS_ENDPOINT_URL={self._config.s3_internal_endpoint}",
                    f"AWS_ACCESS_KEY_ID={self._config.s3_access_key}",
                    f"AWS_SECRET_ACCESS_KEY={self._config.s3_secret_key}",
                ],
                host_config=HostConfig(
                    network_mode=self._config.docker_network,
                    binds=[volume_bind],
                ),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            exit_code = await self._containers.wait(helper_name, timeout=self._timeout)
            logs = await self._containers.logs(helper_name)

            logger.info(
                "%s job completed: %s, exit_code=%d",
                job_type.value.capitalize(),
                helper_name,
                exit_code,
            )
            return JobResult(
                exit_code=exit_code, logs=logs.decode("utf-8", errors="replace")
            )

        except asyncio.CancelledError:
            logger.warning("%s job cancelled: %s", job_type.value.capitalize(), helper_name)
            await self._force_cleanup(helper_name)
            raise

        finally:
            try:
                await self._containers.remove(helper_name)
            except Exception as e:
                logger.error(
                    "Failed to cleanup %s job container %s: %s",
                    job_type.value,
                    helper_name,
                    e,
                )

    async def run_archive(self, workspace_id: str, op_id: str) -> JobResult:
        """Run archive job (Volume -> S3)."""
        return await self._run_job(JobType.ARCHIVE, workspace_id, op_id)

    async def run_restore(self, workspace_id: str, op_id: str) -> JobResult:
        """Run restore job (S3 -> Volume)."""
        return await self._run_job(JobType.RESTORE, workspace_id, op_id)
