"""Runtime protocols for dependency injection.

These protocols enable swapping DockerRuntime for K8sRuntime or other implementations.
Uses structural typing (duck typing) - no inheritance required.
"""

from typing import Protocol

from codehub_agent.runtimes.docker.instance import ContainerInfo, InstanceStatus, UpstreamInfo
from codehub_agent.runtimes.docker.result import (
    OperationResult,
    PrepareArchiveResult,
    PrepareRestoreResult,
)
from codehub_agent.runtimes.docker.storage import ArchiveInfo, RestoreMarkerInfo
from codehub_agent.runtimes.docker.volume import VolumeInfo, VolumeStatus


class InstanceManagerProtocol(Protocol):
    """Protocol for instance (container) management."""

    async def list_all(self) -> list[ContainerInfo]: ...
    async def start(self, workspace_id: str, image_ref: str | None = None) -> OperationResult: ...
    async def delete(self, workspace_id: str) -> OperationResult: ...
    async def get_status(self, workspace_id: str) -> InstanceStatus: ...
    async def get_upstream(self, workspace_id: str) -> UpstreamInfo: ...


class VolumeManagerProtocol(Protocol):
    """Protocol for volume management."""

    async def list_all(self) -> list[VolumeInfo]: ...
    async def create(self, workspace_id: str) -> OperationResult: ...
    async def delete(self, workspace_id: str) -> OperationResult: ...
    async def exists(self, workspace_id: str) -> VolumeStatus: ...


class JobRunnerProtocol(Protocol):
    """Protocol for archive/restore job execution."""

    async def find_running_job(self, workspace_id: str): ...
    async def run_archive(self, workspace_id: str, archive_op_id: str) -> OperationResult: ...
    async def run_restore(
        self, workspace_id: str, archive_key: str, restore_op_id: str
    ) -> OperationResult: ...


class StorageManagerProtocol(Protocol):
    """Protocol for S3 storage operations."""

    async def list_archives_and_markers(
        self,
    ) -> tuple[list[ArchiveInfo], list[RestoreMarkerInfo], list]: ...
    async def archive_exists(self, archive_key: str) -> bool: ...
    async def delete_workspace_markers(self, workspace_id: str) -> int: ...
    async def run_gc(
        self,
        archive_keys: list[str],
        protected_workspaces: list[tuple[str, str]],
        retention_count: int = 3,
        restore_retention_count: int = 1,
    ) -> tuple[int, list[str]]: ...


class RuntimeProtocol(Protocol):
    """Protocol for workspace runtime (Docker, K8s, etc.)."""

    # Managers for observe and direct operations
    instances: InstanceManagerProtocol
    volumes: VolumeManagerProtocol
    jobs: JobRunnerProtocol
    storage: StorageManagerProtocol

    # Service methods: Validation + Preparation
    async def prepare_start(self, workspace_id: str) -> OperationResult: ...
    async def prepare_archive(
        self, workspace_id: str, archive_op_id: str
    ) -> PrepareArchiveResult: ...
    async def prepare_restore(
        self, workspace_id: str, archive_key: str, restore_op_id: str
    ) -> PrepareRestoreResult: ...
    async def delete_workspace(self, workspace_id: str) -> None: ...

    # Utility
    def get_archive_key(self, workspace_id: str, archive_op_id: str) -> str: ...

    # Lifecycle
    async def init(self) -> None: ...
    async def close(self) -> None: ...
