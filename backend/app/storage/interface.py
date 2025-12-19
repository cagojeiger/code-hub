"""Storage Provider interface for code-hub."""

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel


class ProvisionResult(BaseModel):
    """Result of a Provision operation."""

    home_mount: str
    home_ctx: str


class StorageStatus(BaseModel):
    """Current provisioning status."""

    provisioned: bool
    home_ctx: str | None = None
    home_mount: str | None = None


class StorageProvider(ABC):
    """Abstract base class for storage backends."""

    @property
    @abstractmethod
    def backend_name(self) -> Literal["local-dir", "object-store"]:
        """Return the backend identifier."""
        ...

    @abstractmethod
    async def provision(
        self,
        home_store_key: str,
        existing_ctx: str | None = None,
    ) -> ProvisionResult:
        """Prepare home_mount for container use."""
        ...

    @abstractmethod
    async def deprovision(self, home_ctx: str | None) -> None:
        """Release home_ctx resources. Idempotent."""
        ...

    @abstractmethod
    async def purge(self, home_store_key: str) -> None:
        """Completely delete all data for home_store_key."""
        ...

    @abstractmethod
    async def get_status(self, home_store_key: str) -> StorageStatus:
        """Query current provisioning state."""
        ...
