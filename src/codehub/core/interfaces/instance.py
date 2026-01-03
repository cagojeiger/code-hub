"""Instance controller interface for container orchestration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ContainerInfo:
    """Container observation result."""

    workspace_id: str
    running: bool
    reason: str
    message: str


class InstanceController(ABC):
    """Interface for container orchestration.

    Implementations: DockerInstanceController, K8sInstanceController (future)
    """

    @abstractmethod
    async def list_all(self, prefix: str) -> list[ContainerInfo]:
        """Bulk observe all containers with given prefix.

        Args:
            prefix: Container name prefix (e.g., "ws-")

        Returns:
            List of ContainerInfo for all containers
        """
        ...

    @abstractmethod
    async def start(self, workspace_id: str, image_ref: str) -> None:
        """Start container for workspace.

        Args:
            workspace_id: Workspace ID
            image_ref: Container image reference
        """
        ...

    @abstractmethod
    async def delete(self, workspace_id: str) -> None:
        """Delete container for workspace.

        Args:
            workspace_id: Workspace ID
        """
        ...

    @abstractmethod
    async def is_running(self, workspace_id: str) -> bool:
        """Check if container is running and ready to receive traffic.

        Args:
            workspace_id: Workspace ID

        Returns:
            True if container is running
        """
        ...
