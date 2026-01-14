"""Docker instance manager for Agent."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from codehub_agent.infra import ContainerAPI, ContainerConfig, HostConfig, ImageAPI
from codehub_agent.logging_schema import LogEvent
from codehub_agent.runtimes.docker.lock import get_workspace_lock
from codehub_agent.runtimes.docker.result import OperationResult, OperationStatus

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

    async def start(self, workspace_id: str, image_ref: str | None = None) -> OperationResult:
        """Start container for workspace.

        Uses workspace lock to prevent TOCTOU race condition.
        Checks Docker state first (idempotency):
        - If container already running: returns ALREADY_RUNNING
        - Otherwise: creates/starts container and returns COMPLETED
        """
        async with get_workspace_lock(workspace_id):
            container_name = self._naming.container_name(workspace_id)
            volume_name = self._naming.volume_name(workspace_id)

            existing = await self._containers.inspect(container_name)
            if existing:
                state = existing.get("State", {})
                if state.get("Running", False):
                    logger.info(
                        "Container already running",
                        extra={
                            "event": LogEvent.CONTAINER_STARTED,
                            "container": container_name,
                            "workspace_id": workspace_id,
                            "status": "already_running",
                        },
                    )
                    return OperationResult(
                        status=OperationStatus.ALREADY_RUNNING,
                        message="Container already running",
                    )

                try:
                    await self._containers.start(container_name)
                    logger.info(
                        "Started existing container",
                        extra={
                            "event": LogEvent.CONTAINER_STARTED,
                            "container": container_name,
                            "workspace_id": workspace_id,
                            "existing": True,
                        },
                    )
                    return OperationResult(status=OperationStatus.COMPLETED)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        logger.warning(
                            "Container disappeared, recreating",
                            extra={
                                "event": LogEvent.CONTAINER_REMOVED,
                                "container": container_name,
                                "workspace_id": workspace_id,
                            },
                        )
                        await self._containers.remove(container_name)
                    else:
                        raise

            image = image_ref or self._config.runtime.default_image
            await self._images.ensure(image)

            port = self._config.docker.container_port
            config = ContainerConfig(
                image=image,
                name=container_name,
                cmd=["--auth", "none", "--bind-addr", f"0.0.0.0:{port}"],
                user=f"{self._config.docker.coder_uid}:{self._config.docker.coder_gid}",
                env=["HOME=/home/coder"],
                exposed_ports={f"{port}/tcp": {}},
                host_config=HostConfig(
                    network_mode=self._config.docker.network,
                    binds=[f"{volume_name}:/home/coder"],
                ),
            )

            await self._containers.create(config)
            await self._containers.start(container_name)
            logger.info(
                "Created and started container",
                extra={
                    "event": LogEvent.CONTAINER_STARTED,
                    "container": container_name,
                    "workspace_id": workspace_id,
                    "image": image,
                },
            )
            return OperationResult(status=OperationStatus.COMPLETED)

    async def delete(self, workspace_id: str) -> OperationResult:
        """Delete container for workspace (used for stop operation).

        Uses workspace lock to prevent TOCTOU race condition.
        Checks Docker state first (idempotency):
        - If container doesn't exist or already stopped: returns ALREADY_STOPPED
        - Otherwise: stops/removes container and returns COMPLETED
        """
        async with get_workspace_lock(workspace_id):
            container_name = self._naming.container_name(workspace_id)

            # Check current state
            existing = await self._containers.inspect(container_name)
            if not existing:
                logger.info(
                    "Container already deleted",
                    extra={
                        "event": LogEvent.CONTAINER_STOPPED,
                        "container": container_name,
                        "workspace_id": workspace_id,
                        "status": "already_stopped",
                    },
                )
                return OperationResult(
                    status=OperationStatus.ALREADY_STOPPED,
                    message="Container does not exist",
                )

            state = existing.get("State", {})
            if not state.get("Running", False):
                # Container exists but not running - just remove it
                await self._containers.remove(container_name)
                logger.info(
                    "Removed stopped container",
                    extra={
                        "event": LogEvent.CONTAINER_STOPPED,
                        "container": container_name,
                        "workspace_id": workspace_id,
                        "status": "already_stopped",
                    },
                )
                return OperationResult(
                    status=OperationStatus.ALREADY_STOPPED,
                    message="Container was already stopped",
                )

            # Container is running - stop and remove
            await self._containers.stop(container_name)
            await self._containers.remove(container_name)
            return OperationResult(status=OperationStatus.COMPLETED)

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
            port=self._config.docker.container_port,
        )
