"""Docker volume manager for Agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.infra import VolumeAPI, VolumeConfig

if TYPE_CHECKING:
    from codehub_agent.config import AgentConfig
    from codehub_agent.runtimes.docker.naming import ResourceNaming

logger = logging.getLogger(__name__)


class VolumeStatus(BaseModel):
    exists: bool
    name: str


class VolumeManager:
    """Docker volume manager."""

    def __init__(
        self,
        config: AgentConfig,
        naming: ResourceNaming,
        api: VolumeAPI | None = None,
    ) -> None:
        self._config = config
        self._naming = naming
        self._api = api or VolumeAPI()

    async def list_all(self) -> list[dict]:
        prefix = self._naming.prefix
        volumes = await self._api.list(filters={"name": [prefix]})

        results = []
        for vol in volumes:
            name = vol.get("Name", "")
            if not name.startswith(prefix) or not name.endswith("-home"):
                continue

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
        name = self._naming.volume_name(workspace_id)
        await self._api.create(VolumeConfig(name=name))
        logger.info("Created volume: %s", name)

    async def delete(self, workspace_id: str) -> None:
        """Raises VolumeInUseError if volume is in use."""
        name = self._naming.volume_name(workspace_id)
        await self._api.remove(name)
        logger.info("Deleted volume: %s", name)

    async def exists(self, workspace_id: str) -> VolumeStatus:
        name = self._naming.volume_name(workspace_id)
        data = await self._api.inspect(name)
        return VolumeStatus(exists=data is not None, name=name)
