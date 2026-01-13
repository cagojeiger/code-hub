"""Docker volume manager for Agent."""

import logging

from pydantic import BaseModel

from codehub_agent.config import get_agent_config
from codehub_agent.infra import VolumeAPI, VolumeConfig

logger = logging.getLogger(__name__)


class VolumeStatus(BaseModel):
    """Volume status response."""

    exists: bool
    name: str


class VolumeManager:
    """Docker volume manager."""

    def __init__(self, api: VolumeAPI | None = None) -> None:
        self._config = get_agent_config()
        self._api = api or VolumeAPI()

    def _volume_name(self, workspace_id: str) -> str:
        return f"{self._config.resource_prefix}{workspace_id}-home"

    async def list_all(self) -> list[dict]:
        """List all managed volumes."""
        prefix = self._config.resource_prefix
        volumes = await self._api.list(filters={"name": [prefix]})

        results = []
        for vol in volumes:
            name = vol.get("Name", "")
            if not name.startswith(prefix) or not name.endswith("-home"):
                continue

            # Extract workspace_id: remove prefix and -home suffix
            workspace_id = name[len(prefix) : -5]  # -5 for "-home"

            results.append(
                {
                    "workspace_id": workspace_id,
                    "exists": True,
                    "name": name,
                }
            )

        return results

    async def create(self, workspace_id: str) -> None:
        """Create volume for workspace."""
        name = self._volume_name(workspace_id)
        await self._api.create(VolumeConfig(name=name))
        logger.info("Created volume: %s", name)

    async def delete(self, workspace_id: str) -> None:
        """Delete volume for workspace."""
        name = self._volume_name(workspace_id)
        await self._api.remove(name)
        logger.info("Deleted volume: %s", name)

    async def exists(self, workspace_id: str) -> VolumeStatus:
        """Check if volume exists."""
        name = self._volume_name(workspace_id)
        data = await self._api.inspect(name)
        return VolumeStatus(exists=data is not None, name=name)
