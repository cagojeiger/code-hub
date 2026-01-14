"""Docker volume manager for Agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from codehub_agent.infra import VolumeAPI, VolumeConfig
from codehub_agent.logging_schema import LogEvent
from codehub_agent.runtimes.docker.result import OperationResult, OperationStatus

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

    async def create(self, workspace_id: str) -> OperationResult:
        """Create volume for workspace (provision operation).

        Checks Docker state first (idempotency):
        - If volume already exists: returns ALREADY_EXISTS
        - Otherwise: creates volume and returns COMPLETED
        """
        name = self._naming.volume_name(workspace_id)

        # Check if volume already exists
        existing = await self._api.inspect(name)
        if existing:
            logger.info(
                "Volume already exists",
                extra={
                    "event": LogEvent.VOLUME_CREATED,
                    "volume": name,
                    "workspace_id": workspace_id,
                    "status": "already_exists",
                },
            )
            return OperationResult(
                status=OperationStatus.ALREADY_EXISTS,
                message="Volume already exists",
            )

        await self._api.create(VolumeConfig(name=name))
        logger.info(
            "Volume created",
            extra={"event": LogEvent.VOLUME_CREATED, "volume": name, "workspace_id": workspace_id},
        )
        return OperationResult(status=OperationStatus.COMPLETED)

    async def delete(self, workspace_id: str) -> OperationResult:
        """Delete volume for workspace.

        Checks Docker state first (idempotency):
        - If volume doesn't exist: returns ALREADY_DELETED
        - Otherwise: deletes volume and returns COMPLETED

        Raises VolumeInUseError if volume is in use by a container.
        """
        name = self._naming.volume_name(workspace_id)

        # Check if volume exists
        existing = await self._api.inspect(name)
        if not existing:
            logger.info(
                "Volume already deleted",
                extra={
                    "event": LogEvent.VOLUME_REMOVED,
                    "volume": name,
                    "workspace_id": workspace_id,
                    "status": "already_deleted",
                },
            )
            return OperationResult(
                status=OperationStatus.ALREADY_DELETED,
                message="Volume does not exist",
            )

        await self._api.remove(name)
        logger.info(
            "Volume deleted",
            extra={"event": LogEvent.VOLUME_REMOVED, "volume": name, "workspace_id": workspace_id},
        )
        return OperationResult(status=OperationStatus.COMPLETED)

    async def exists(self, workspace_id: str) -> VolumeStatus:
        name = self._naming.volume_name(workspace_id)
        data = await self._api.inspect(name)
        return VolumeStatus(exists=data is not None, name=name)
