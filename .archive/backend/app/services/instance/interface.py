"""Instance Controller interface for code-hub."""

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel


class UpstreamInfo(BaseModel):
    """Upstream connection info for proxying."""

    host: str
    port: int


class InstanceStatus(BaseModel):
    """Current container instance status."""

    exists: bool
    running: bool
    healthy: bool
    port: int | None = None


class InstanceController(ABC):
    """Abstract base class for instance controller backends."""

    @property
    @abstractmethod
    def backend_name(self) -> Literal["local-docker", "k8s"]:
        """Return the backend identifier."""
        ...

    @abstractmethod
    async def start_workspace(
        self,
        workspace_id: str,
        image_ref: str,
        home_mount: str,
    ) -> None:
        """Start a workspace container. Idempotent."""
        ...

    @abstractmethod
    async def stop_workspace(self, workspace_id: str) -> None:
        """Stop a workspace container. Idempotent."""
        ...

    @abstractmethod
    async def delete_workspace(self, workspace_id: str) -> None:
        """Delete a workspace container. Idempotent."""
        ...

    @abstractmethod
    async def resolve_upstream(self, workspace_id: str) -> UpstreamInfo:
        """Resolve upstream connection info for proxying."""
        ...

    @abstractmethod
    async def get_status(self, workspace_id: str) -> InstanceStatus:
        """Query current container status."""
        ...
