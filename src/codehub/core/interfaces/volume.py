"""Volume provider interface for Docker/K8s volume operations."""

from abc import ABC, abstractmethod


class VolumeProvider(ABC):
    """Interface for volume operations.

    Implementations:
    - DockerVolumeProvider: Docker volumes
    - K8sVolumeProvider: Kubernetes PVCs (future)
    """

    @abstractmethod
    async def create(self, name: str) -> None:
        """Create a volume.

        Args:
            name: Volume name

        Idempotent: If volume already exists, do nothing.
        """
        ...

    @abstractmethod
    async def remove(self, name: str) -> None:
        """Remove a volume.

        Args:
            name: Volume name

        Idempotent: If volume doesn't exist, do nothing.
        """
        ...

    @abstractmethod
    async def exists(self, name: str) -> bool:
        """Check if volume exists.

        Args:
            name: Volume name

        Returns:
            True if volume exists
        """
        ...

    @abstractmethod
    async def list(self, prefix: str) -> list[dict]:
        """List volumes with given prefix.

        Args:
            prefix: Volume name prefix

        Returns:
            List of volume info dicts with at least 'Name' key
        """
        ...
