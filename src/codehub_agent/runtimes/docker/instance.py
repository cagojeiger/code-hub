"""Docker instance manager for Agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from codehub_agent.infra import ContainerAPI, ContainerConfig, HostConfig, ImageAPI

if TYPE_CHECKING:
    from codehub_agent.config import AgentConfig
    from codehub_agent.runtimes.docker.naming import ResourceNaming

logger = logging.getLogger(__name__)


class InstanceStatus(BaseModel):
    """Instance status response."""

    exists: bool
    running: bool
    healthy: bool
    reason: str
    message: str


class UpstreamInfo(BaseModel):
    """Upstream information for proxy."""

    hostname: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.hostname}:{self.port}"


class InstanceManager:
    """Docker instance manager."""

    def __init__(
        self,
        config: AgentConfig,
        naming: ResourceNaming,
        containers: ContainerAPI | None = None,
        images: ImageAPI | None = None,
    ) -> None:
        self._config = config
        self._naming = naming
        self._containers = containers or ContainerAPI()
        self._images = images or ImageAPI()

    async def list_all(self) -> list[dict]:
        """List all managed containers."""
        prefix = self._naming.prefix
        containers = await self._containers.list(filters={"name": [prefix]})

        results = []
        for container in containers:
            names = container.get("Names", [])
            name = names[0].lstrip("/") if names else ""
            if not name.startswith(prefix):
                continue

            # Skip job containers
            if "-job-" in name:
                continue

            workspace_id = name[len(prefix) :]
            state = container.get("State", "unknown")
            running = state == "running"

            results.append(
                {
                    "workspace_id": workspace_id,
                    "running": running,
                    "reason": "Running" if running else state.capitalize(),
                    "message": container.get("Status", ""),
                }
            )

        return results

    async def start(self, workspace_id: str, image_ref: str | None = None) -> None:
        """Start container for workspace."""
        container_name = self._naming.container_name(workspace_id)
        volume_name = self._naming.volume_name(workspace_id)

        existing = await self._containers.inspect(container_name)
        if existing:
            try:
                await self._containers.start(container_name)
                logger.info("Started existing container: %s", container_name)
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning("Container disappeared, recreating: %s", container_name)
                    await self._containers.remove(container_name)
                else:
                    raise

        image = image_ref or self._config.default_image
        await self._images.ensure(image)

        port = self._config.container_port
        config = ContainerConfig(
            image=image,
            name=container_name,
            cmd=["--auth", "none", "--bind-addr", f"0.0.0.0:{port}"],
            user=f"{self._config.coder_uid}:{self._config.coder_gid}",
            env=["HOME=/home/coder"],
            exposed_ports={f"{port}/tcp": {}},
            host_config=HostConfig(
                network_mode=self._config.docker_network,
                binds=[f"{volume_name}:/home/coder"],
            ),
        )

        await self._containers.create(config)
        await self._containers.start(container_name)
        logger.info("Created and started container: %s", container_name)

    async def delete(self, workspace_id: str) -> None:
        """Delete container for workspace."""
        container_name = self._naming.container_name(workspace_id)
        await self._containers.stop(container_name)
        await self._containers.remove(container_name)

    async def get_status(self, workspace_id: str) -> InstanceStatus:
        """Get instance status."""
        container_name = self._naming.container_name(workspace_id)

        data = await self._containers.inspect(container_name)
        if not data:
            return InstanceStatus(
                exists=False,
                running=False,
                healthy=False,
                reason="NotFound",
                message="Container not found",
            )

        state = data.get("State", {})
        running = state.get("Running", False)
        health_status = state.get("Health", {}).get("Status", "healthy")
        healthy = running and health_status in ("healthy", None)

        return InstanceStatus(
            exists=True,
            running=running,
            healthy=healthy,
            reason="Running" if running else "Stopped",
            message=state.get("Status", ""),
        )

    async def get_upstream(self, workspace_id: str) -> UpstreamInfo:
        """Get upstream address for proxy."""
        return UpstreamInfo(
            hostname=self._naming.container_name(workspace_id),
            port=self._config.container_port,
        )
