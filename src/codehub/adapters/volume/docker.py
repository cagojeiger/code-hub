"""Docker volume provider implementation."""

from codehub.core.interfaces import VolumeProvider
from codehub.infra.docker import VolumeAPI, VolumeConfig


class DockerVolumeProvider(VolumeProvider):
    """Docker-based volume provider."""

    def __init__(self, api: VolumeAPI | None = None) -> None:
        self._api = api or VolumeAPI()

    async def create(self, name: str) -> None:
        """Create a Docker volume."""
        await self._api.create(VolumeConfig(name=name))

    async def remove(self, name: str) -> None:
        """Remove a Docker volume."""
        await self._api.remove(name)

    async def exists(self, name: str) -> bool:
        """Check if Docker volume exists."""
        data = await self._api.inspect(name)
        return data is not None

    async def list(self, prefix: str) -> list[dict]:
        """List Docker volumes with given prefix."""
        return await self._api.list(filters={"name": [prefix]})
