"""Docker runtime for Agent."""

import logging

from codehub_agent.api.errors import (
    ArchiveNotFoundError,
    ContainerRunningError,
    VolumeNotFoundError,
)
from codehub_agent.config import AgentConfig, get_agent_config
from codehub_agent.infra import DockerClient, S3Operations
from codehub_agent.runtimes.docker.instance import InstanceManager
from codehub_agent.runtimes.docker.job import JobRunner
from codehub_agent.runtimes.docker.naming import ResourceNaming
from codehub_agent.runtimes.docker.result import (
    OperationResult,
    OperationStatus,
    PrepareArchiveResult,
    PrepareRestoreResult,
)
from codehub_agent.runtimes.docker.storage import StorageManager
from codehub_agent.runtimes.docker.volume import VolumeManager

logger = logging.getLogger(__name__)


class DockerRuntime:
    """Docker runtime combining instance, volume, job, and storage management."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        docker_client: DockerClient | None = None,
    ) -> None:
        """Call init() after construction to initialize async resources."""
        self._config = config or get_agent_config()
        self._docker = docker_client or DockerClient()
        self._naming = ResourceNaming(self._config)
        self._s3: S3Operations | None = None

        self.instances = InstanceManager(self._config, self._naming)
        self.volumes = VolumeManager(self._config, self._naming)
        self.jobs = JobRunner(self._config, self._naming)
        # storage is initialized in init() after S3 is ready
        self.storage: StorageManager | None = None

    async def init(self) -> None:
        """Initialize async resources. Must be called before using storage operations."""
        self._s3 = S3Operations(self._config)
        await self._s3.init()
        await self._s3.ensure_bucket()
        self.storage = StorageManager(self._config, self._naming, self._s3)

    async def close(self) -> None:
        try:
            if self._s3:
                await self._s3.close()
                self._s3 = None
        finally:
            await self._docker.close()

    def get_archive_key(self, workspace_id: str, archive_op_id: str) -> str:
        return self._naming.archive_s3_key(workspace_id, archive_op_id)

    # =========================================================================
    # Service Methods (Validation + Preparation)
    # =========================================================================

    async def prepare_start(self, workspace_id: str) -> OperationResult:
        """Raises VolumeNotFoundError if volume does not exist."""
        volume_status = await self.volumes.exists(workspace_id)
        if not volume_status.exists:
            raise VolumeNotFoundError(f"Volume does not exist for workspace {workspace_id}")
        return OperationResult(status=OperationStatus.IN_PROGRESS)

    async def prepare_archive(
        self, workspace_id: str, archive_op_id: str
    ) -> PrepareArchiveResult:
        """Raises ContainerRunningError or VolumeNotFoundError on precondition failure."""
        container_status = await self.instances.get_status(workspace_id)
        if container_status.running:
            raise ContainerRunningError(
                f"Cannot archive while container is running for workspace {workspace_id}"
            )

        volume_status = await self.volumes.exists(workspace_id)
        if not volume_status.exists:
            raise VolumeNotFoundError(f"Volume does not exist for workspace {workspace_id}")

        existing_job = await self.jobs.find_running_job(workspace_id)
        archive_key = self.get_archive_key(workspace_id, archive_op_id)

        return PrepareArchiveResult(
            should_run_job=existing_job is None,
            archive_key=archive_key,
        )

    async def prepare_restore(
        self, workspace_id: str, archive_key: str, restore_op_id: str
    ) -> PrepareRestoreResult:
        """Auto-creates volume. Raises ContainerRunningError or ArchiveNotFoundError."""
        container_status = await self.instances.get_status(workspace_id)
        if container_status.running:
            raise ContainerRunningError(
                f"Cannot restore while container is running for workspace {workspace_id}"
            )

        assert self.storage is not None, "Runtime not initialized. Call init() first."
        archive_exists = await self.storage.archive_exists(archive_key)
        if not archive_exists:
            raise ArchiveNotFoundError(f"Archive not found: {archive_key}")

        volume_status = await self.volumes.exists(workspace_id)
        if not volume_status.exists:
            await self.volumes.create(workspace_id)

        existing_job = await self.jobs.find_running_job(workspace_id)

        return PrepareRestoreResult(
            should_run_job=existing_job is None,
            restore_marker=restore_op_id,
        )

    async def delete_workspace(self, workspace_id: str) -> None:
        assert self.storage is not None, "Runtime not initialized. Call init() first."
        for coro, resource in [
            (self.instances.delete(workspace_id), "container"),
            (self.volumes.delete(workspace_id), "volume"),
            (self.storage.delete_workspace_markers(workspace_id), "markers"),
        ]:
            try:
                await coro
            except Exception:
                logger.debug(f"Failed to delete {resource}", extra={"workspace_id": workspace_id})


__all__ = [
    "DockerRuntime",
    "InstanceManager",
    "VolumeManager",
    "JobRunner",
    "StorageManager",
    "ResourceNaming",
    "OperationResult",
    "OperationStatus",
    "PrepareArchiveResult",
    "PrepareRestoreResult",
]
