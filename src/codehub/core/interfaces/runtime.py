"""Workspace runtime interface for Control Plane.

This is the single interface that Control Plane uses to interact with
the underlying infrastructure (Docker, K8s, etc.) through Agent.

Design principles:
- Workspace is the domain concept (not Container/Volume/Archive)
- Agent handles infrastructure details internally
- Control Plane only knows about Workspace lifecycle
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


# =============================================================================
# Status Models (nested in WorkspaceState)
# =============================================================================


class ContainerStatus(BaseModel):
    """Container status within a workspace."""

    running: bool
    healthy: bool

    model_config = {"frozen": True}


class VolumeStatus(BaseModel):
    """Volume status within a workspace."""

    exists: bool

    model_config = {"frozen": True}


class ArchiveStatus(BaseModel):
    """Archive status within a workspace."""

    exists: bool
    archive_key: str | None = None

    model_config = {"frozen": True}


# =============================================================================
# Main Models
# =============================================================================


class WorkspaceState(BaseModel):
    """Complete state of a workspace.

    Returned by observe() to provide a unified view of all workspace resources.
    None values indicate the resource doesn't exist.
    """

    workspace_id: str
    container: ContainerStatus | None = None
    volume: VolumeStatus | None = None
    archive: ArchiveStatus | None = None

    model_config = {"frozen": True}

    @property
    def is_running(self) -> bool:
        """Check if workspace container is running and healthy."""
        return (
            self.container is not None
            and self.container.running
            and self.container.healthy
        )

    @property
    def has_volume(self) -> bool:
        """Check if workspace has a volume."""
        return self.volume is not None and self.volume.exists

    @property
    def has_archive(self) -> bool:
        """Check if workspace has an archive."""
        return self.archive is not None and self.archive.exists


class UpstreamInfo(BaseModel):
    """Upstream address for proxy routing.

    Docker: container_name:port (e.g., codehub-ws123:8080)
    K8s: service.namespace.svc.cluster.local:port
    """

    hostname: str
    port: int

    @property
    def url(self) -> str:
        """HTTP URL for upstream."""
        return f"http://{self.hostname}:{self.port}"

    @property
    def ws_url(self) -> str:
        """WebSocket URL for upstream."""
        return f"ws://{self.hostname}:{self.port}"

    model_config = {"frozen": True}


class GCResult(BaseModel):
    """Result of garbage collection operation."""

    deleted_count: int
    deleted_keys: list[str]

    model_config = {"frozen": True}


# =============================================================================
# WorkspaceRuntime Interface
# =============================================================================


class WorkspaceRuntime(ABC):
    """Single interface for all workspace operations.

    This is the only interface Control Plane uses to interact with Agent.
    It abstracts away infrastructure details (Docker, K8s) and focuses on
    workspace lifecycle management.

    Implementations:
    - AgentClient: HTTP client that delegates to Agent API
    - (Future) DirectDockerRuntime: Direct Docker access for testing
    - (Future) K8sRuntime: Kubernetes implementation
    """

    # =========================================================================
    # Observation
    # =========================================================================

    @abstractmethod
    async def observe(self) -> list[WorkspaceState]:
        """Observe all workspaces and return their current state.

        This is the primary method for Observer coordinator to get
        a complete snapshot of all workspaces.

        Returns:
            List of WorkspaceState for all known workspaces.
            Each state includes container, volume, and archive status.
        """
        ...

    # =========================================================================
    # Lifecycle
    # =========================================================================

    @abstractmethod
    async def provision(self, workspace_id: str) -> None:
        """Provision a new workspace (create volume).

        Called when a new workspace is created and needs storage.

        Args:
            workspace_id: Workspace identifier
        """
        ...

    @abstractmethod
    async def start(self, workspace_id: str, image: str) -> None:
        """Start workspace container.

        Args:
            workspace_id: Workspace identifier
            image: Container image reference (e.g., "codehub/workspace:latest")
        """
        ...

    @abstractmethod
    async def stop(self, workspace_id: str) -> None:
        """Stop workspace container.

        The container is stopped but not removed. Volume is preserved.

        Args:
            workspace_id: Workspace identifier
        """
        ...

    @abstractmethod
    async def delete(self, workspace_id: str) -> None:
        """Delete workspace completely (container + volume).

        This removes all local resources. Archives in S3 are not affected.

        Args:
            workspace_id: Workspace identifier
        """
        ...

    # =========================================================================
    # Persistence
    # =========================================================================

    @abstractmethod
    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive workspace to S3.

        Creates a compressed archive of the workspace volume.

        Args:
            workspace_id: Workspace identifier
            op_id: Operation ID for idempotency

        Returns:
            Archive key (e.g., "cluster-id/ws123/op456/home.tar.zst")
        """
        ...

    @abstractmethod
    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore workspace from S3 archive.

        Downloads and extracts the archive to the workspace volume.

        Args:
            workspace_id: Workspace identifier
            archive_key: Full archive key from S3

        Returns:
            restore_marker: Proof of which archive was restored (for crash recovery)
        """
        ...

    @abstractmethod
    async def delete_archive(self, archive_key: str) -> bool:
        """Delete an archive from S3.

        Args:
            archive_key: Full archive key

        Returns:
            True if deleted successfully
        """
        ...

    # =========================================================================
    # Routing
    # =========================================================================

    @abstractmethod
    async def get_upstream(self, workspace_id: str) -> UpstreamInfo | None:
        """Get upstream address for proxy routing.

        Args:
            workspace_id: Workspace identifier

        Returns:
            UpstreamInfo if workspace is running and ready, None otherwise
        """
        ...

    # =========================================================================
    # Garbage Collection
    # =========================================================================

    @abstractmethod
    async def run_gc(
        self, protected: list[tuple[str, str]]
    ) -> GCResult:
        """Run garbage collection on archives.

        Deletes archives that are not in the protected list.

        Args:
            protected: List of (workspace_id, op_id) tuples to protect

        Returns:
            GCResult with count and list of deleted archive keys
        """
        ...

    # =========================================================================
    # Lifecycle Management
    # =========================================================================

    @abstractmethod
    async def close(self) -> None:
        """Close runtime and release resources."""
        ...
