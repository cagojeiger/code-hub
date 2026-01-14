"""Docker job runner for Agent."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.api.errors import JobFailedError
from codehub_agent.infra import ContainerAPI, ContainerConfig, HostConfig
from codehub_agent.logging_schema import LogEvent
from codehub_agent.runtimes.docker.result import OperationResult, OperationStatus

if TYPE_CHECKING:
    from codehub_agent.config import AgentConfig
    from codehub_agent.runtimes.docker.naming import ResourceNaming

logger = logging.getLogger(__name__)

# =============================================================================
# Concurrency Control
# =============================================================================

_job_semaphore: asyncio.Semaphore | None = None
_workspace_locks: dict[str, asyncio.Lock] = {}

# Stuck container threshold (5 minutes)
STUCK_THRESHOLD_SECONDS = 300


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


def get_workspace_lock(workspace_id: str) -> asyncio.Lock:
    """Get or create a per-workspace lock.

    Prevents TOCTOU race condition by ensuring only one job operation
    per workspace at a time (check + create are atomic).
    """
    if workspace_id not in _workspace_locks:
        _workspace_locks[workspace_id] = asyncio.Lock()
    return _workspace_locks[workspace_id]


class JobResult(BaseModel):
    exit_code: int
    logs: str


class JobType(str, Enum):
    ARCHIVE = "archive"
    RESTORE = "restore"


class JobRunner:
    """Docker job runner for archive/restore operations."""

    # Label keys for job containers
    LABEL_WORKSPACE_ID = "codehub.workspace_id"
    LABEL_JOB_TYPE = "codehub.job_type"
    LABEL_ARCHIVE_OP_ID = "codehub.archive_op_id"

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

    async def find_running_job(
        self,
        workspace_id: str,
        job_type: JobType,
        archive_op_id: str | None = None,
    ) -> dict | None:
        """Find a running job container for the given workspace and job type.

        Args:
            workspace_id: Workspace ID to filter by
            job_type: Type of job (archive/restore)
            archive_op_id: Optional archive operation ID for precise filtering

        Returns container info dict if found, None otherwise.
        Also cleans up stuck containers (created > 5 minutes ago).
        """
        # Build filter
        filters: dict = {
            "label": [
                f"{self.LABEL_WORKSPACE_ID}={workspace_id}",
                f"{self.LABEL_JOB_TYPE}={job_type.value}",
            ],
            "status": ["created", "running"],
        }
        # Add archive_op_id filter if provided
        if archive_op_id:
            filters["label"].append(f"{self.LABEL_ARCHIVE_OP_ID}={archive_op_id}")

        containers = await self._containers.list(filters=filters)

        # Check each container, cleaning up stuck ones
        now = time.time()
        for container in containers:
            state = container.get("State", "")
            created_ts = container.get("Created", 0)

            # Handle stuck "created" containers (> 5 minutes)
            if state == "created" and created_ts:
                # Docker API returns Created as Unix timestamp (int)
                if isinstance(created_ts, int) and (now - created_ts) > STUCK_THRESHOLD_SECONDS:
                    container_id = container.get("Id", "")
                    container_name = container.get("Names", [""])[0].lstrip("/")
                    logger.warning(
                        "Removing stuck container",
                        extra={
                            "event": LogEvent.JOB_FAILED,
                            "container": container_name,
                            "workspace_id": workspace_id,
                            "state": state,
                            "age_seconds": int(now - created_ts),
                        },
                    )
                    try:
                        await self._containers.remove(container_id, force=True)
                    except Exception:
                        pass
                    continue  # Check next container

            # Found a valid running/recent container
            return container

        return None

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
                    labels={
                        self.LABEL_WORKSPACE_ID: workspace_id,
                        self.LABEL_JOB_TYPE: job_type.value,
                        self.LABEL_ARCHIVE_OP_ID: archive_op_id or "",
                    },
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

    async def run_archive(self, workspace_id: str, archive_op_id: str) -> OperationResult:
        """Volume -> S3. URL is constructed from archive_op_id.

        Uses workspace lock to prevent TOCTOU race condition.
        Checks for existing running archive job with same archive_op_id (idempotency).
        Returns OperationResult with status and archive_key.
        """
        async with get_workspace_lock(workspace_id):
            # Check for existing running job with same archive_op_id
            existing = await self.find_running_job(workspace_id, JobType.ARCHIVE, archive_op_id)
            if existing:
                logger.info(
                    "Archive job already running",
                    extra={
                        "event": LogEvent.JOB_COMPLETED,
                        "job_type": "archive",
                        "workspace_id": workspace_id,
                        "archive_op_id": archive_op_id,
                        "status": "in_progress",
                    },
                )
                return OperationResult(
                    status=OperationStatus.IN_PROGRESS,
                    message="Archive job already running",
                )

            # Run the job (lock is released after job starts, not after completion)
            await self._run_job(JobType.ARCHIVE, workspace_id, archive_op_id=archive_op_id)
            archive_key = self._naming.archive_s3_key(workspace_id, archive_op_id)
            return OperationResult(
                status=OperationStatus.COMPLETED,
                archive_key=archive_key,
            )

    async def run_restore(self, workspace_id: str, archive_key: str) -> OperationResult:
        """S3 -> Volume. Receives archive_key directly per spec (L229).

        Uses workspace lock to prevent TOCTOU race condition.
        Checks for existing running restore job first (idempotency).
        Returns OperationResult with status and restore_marker.
        """
        # Extract archive_op_id from archive_key for labeling
        # Format: {prefix}{ws_id}/{archive_op_id}/home.tar.zst
        archive_op_id = self._extract_archive_op_id(archive_key)

        async with get_workspace_lock(workspace_id):
            # Check for existing running restore job (workspace level, not op_id level)
            # Restore doesn't need op_id filtering - only one restore at a time per workspace
            existing = await self.find_running_job(workspace_id, JobType.RESTORE)
            if existing:
                logger.info(
                    "Restore job already running",
                    extra={
                        "event": LogEvent.JOB_COMPLETED,
                        "job_type": "restore",
                        "workspace_id": workspace_id,
                        "status": "in_progress",
                    },
                )
                return OperationResult(
                    status=OperationStatus.IN_PROGRESS,
                    message="Restore job already running",
                )

            # Run the job
            archive_url = f"s3://{self._config.s3.bucket}/{archive_key}"
            await self._run_job(
                JobType.RESTORE, workspace_id,
                archive_op_id=archive_op_id,
                archive_url=archive_url,
            )
            return OperationResult(
                status=OperationStatus.COMPLETED,
                restore_marker=archive_key,
            )

    def _extract_archive_op_id(self, archive_key: str) -> str | None:
        """Extract archive_op_id from archive_key.

        Format: {prefix}{ws_id}/{archive_op_id}/home.tar.zst
        Returns archive_op_id or None if not found.
        """
        parts = archive_key.split("/")
        # Expected: [..., ws_id, archive_op_id, "home.tar.zst"]
        if len(parts) >= 3 and parts[-1].endswith(".tar.zst"):
            return parts[-2]
        return None
