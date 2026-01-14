"""Docker job runner for Agent."""

from __future__ import annotations

import asyncio
import logging
import uuid
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.api.errors import JobFailedError
from codehub_agent.infra import ContainerAPI, ContainerConfig, HostConfig
from codehub_agent.logging_schema import LogEvent

if TYPE_CHECKING:
    from codehub_agent.config import AgentConfig
    from codehub_agent.runtimes.docker.naming import ResourceNaming

logger = logging.getLogger(__name__)

# =============================================================================
# Concurrency Control
# =============================================================================

_job_semaphore: asyncio.Semaphore | None = None


def get_job_semaphore(limit: int = 3) -> asyncio.Semaphore:
    """Get or create the job semaphore.

    Limits concurrent archive/restore jobs to prevent resource exhaustion.
    Default limit of 3 balances throughput with system stability.

    Args:
        limit: Maximum concurrent jobs.
    """
    global _job_semaphore
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(limit)
    return _job_semaphore


class JobResult(BaseModel):
    exit_code: int
    logs: str


class JobType(str, Enum):
    ARCHIVE = "archive"
    RESTORE = "restore"


class JobRunner:
    """Docker job runner for archive/restore operations."""

    def __init__(
        self,
        config: AgentConfig,
        naming: ResourceNaming,
        containers: ContainerAPI | None = None,
        timeout: int | None = None,
    ) -> None:
        self._config = config
        self._naming = naming
        self._containers = containers or ContainerAPI()
        self._timeout = timeout or self._config.docker.job_timeout

    async def _force_cleanup(self, container_name: str) -> None:
        try:
            await self._containers.stop(container_name, timeout=5)
        except Exception:
            pass
        try:
            await self._containers.remove(container_name, force=True)
        except Exception as e:
            logger.warning(
                "Force cleanup failed",
                extra={"event": LogEvent.JOB_FAILED, "container": container_name, "error": str(e)},
            )

    async def _run_job(
        self,
        job_type: JobType,
        workspace_id: str,
        *,
        archive_op_id: str | None = None,
        archive_url: str | None = None,
    ) -> JobResult:
        """Run archive/restore job.

        For archive: provide archive_op_id (URL will be constructed)
        For restore: provide archive_url directly (spec compliance)

        Uses a semaphore to limit concurrent jobs (default: 3).
        """
        if archive_url is None:
            if archive_op_id is None:
                raise ValueError("Either archive_op_id or archive_url must be provided")
            archive_url = self._naming.archive_s3_url(workspace_id, archive_op_id)

        job_id = uuid.uuid4().hex[:8]
        helper_name = f"codehub-job-{job_type.value}-{job_id}"
        volume_name = self._naming.volume_name(workspace_id)

        # Archive: read-only mount, Restore: read-write mount
        volume_bind = (
            f"{volume_name}:/data:ro"
            if job_type == JobType.ARCHIVE
            else f"{volume_name}:/data"
        )

        async with get_job_semaphore():
            try:
                config = ContainerConfig(
                    image=self._config.runtime.storage_job_image,
                    name=helper_name,
                    cmd=["-c", f"/usr/local/bin/{job_type.value}"],
                    env=[
                        f"ARCHIVE_URL={archive_url}",
                        f"AWS_ENDPOINT_URL={self._config.s3.internal_endpoint}",
                        f"AWS_ACCESS_KEY_ID={self._config.s3.access_key}",
                        f"AWS_SECRET_ACCESS_KEY={self._config.s3.secret_key}",
                    ],
                    host_config=HostConfig(
                        network_mode=self._config.docker.network,
                        binds=[volume_bind],
                    ),
                )
                await self._containers.create(config)
                await self._containers.start(helper_name)

                exit_code = await self._containers.wait(helper_name, timeout=self._timeout)
                logs = await self._containers.logs(helper_name)
                logs_str = logs.decode("utf-8", errors="replace")

                logger.info(
                    "Job completed",
                    extra={
                        "event": LogEvent.JOB_COMPLETED,
                        "job_type": job_type.value,
                        "container": helper_name,
                        "workspace_id": workspace_id,
                        "exit_code": exit_code,
                    },
                )

                if exit_code != 0:
                    raise JobFailedError(
                        f"{job_type.value.capitalize()} job failed with exit code {exit_code}"
                    )

                return JobResult(exit_code=exit_code, logs=logs_str)

            except asyncio.CancelledError:
                logger.warning(
                    "Job cancelled",
                    extra={
                        "event": LogEvent.JOB_CANCELLED,
                        "job_type": job_type.value,
                        "container": helper_name,
                        "workspace_id": workspace_id,
                    },
                )
                await self._force_cleanup(helper_name)
                raise

            finally:
                try:
                    await self._containers.remove(helper_name)
                except Exception as e:
                    logger.error(
                        "Failed to cleanup job container",
                        extra={
                            "event": LogEvent.JOB_FAILED,
                            "job_type": job_type.value,
                            "container": helper_name,
                            "error": str(e),
                        },
                    )

    async def run_archive(self, workspace_id: str, archive_op_id: str) -> JobResult:
        """Volume -> S3. URL is constructed from archive_op_id."""
        return await self._run_job(JobType.ARCHIVE, workspace_id, archive_op_id=archive_op_id)

    async def run_restore(self, workspace_id: str, archive_key: str) -> JobResult:
        """S3 -> Volume. Receives archive_key directly per spec (L229)."""
        archive_url = f"s3://{self._config.s3.bucket}/{archive_key}"
        return await self._run_job(JobType.RESTORE, workspace_id, archive_url=archive_url)
