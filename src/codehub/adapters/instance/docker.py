"""Docker instance controller implementation."""

import logging

from codehub.core.interfaces import ContainerInfo, InstanceController
from codehub.infra.docker import (
    ContainerAPI,
    ContainerConfig,
    HostConfig,
)

logger = logging.getLogger(__name__)

# Container configuration
CONTAINER_PREFIX = "codehub-ws-"
CONTAINER_PORT = 8080
NETWORK_NAME = "codehub-net"
CODER_UID = 1000
CODER_GID = 1000


class DockerInstanceController(InstanceController):
    """Docker-based instance controller using ContainerAPI."""

    def __init__(
        self,
        image_ref: str = "cagojeiger/code-server:4.107.0",
        containers: ContainerAPI | None = None,
    ) -> None:
        self._image_ref = image_ref
        self._containers = containers or ContainerAPI()

    def _container_name(self, workspace_id: str) -> str:
        return f"{CONTAINER_PREFIX}{workspace_id}"

    async def list_all(self, prefix: str) -> list[ContainerInfo]:
        """List all containers with given prefix."""
        containers = await self._containers.list(filters={"name": [prefix]})

        results = []
        for container in containers:
            # Extract workspace_id from container name
            names = container.get("Names", [])
            name = names[0].lstrip("/") if names else ""
            if not name.startswith(prefix):
                continue

            workspace_id = name[len(prefix) :]
            state = container.get("State", "unknown")
            running = state == "running"

            results.append(
                ContainerInfo(
                    workspace_id=workspace_id,
                    running=running,
                    reason="Running" if running else state.capitalize(),
                    message=container.get("Status", ""),
                )
            )

        return results

    async def start(self, workspace_id: str, image_ref: str) -> None:
        """Start container for workspace."""
        container_name = self._container_name(workspace_id)

        # Check if container already exists
        existing = await self._containers.inspect(container_name)
        if existing:
            # Container exists, just start it
            await self._containers.start(container_name)
            logger.info("Started existing container: %s", container_name)
            return

        # Create new container with full configuration
        config = ContainerConfig(
            image=image_ref or self._image_ref,
            name=container_name,
            cmd=["--auth", "none", "--bind-addr", "0.0.0.0:8080"],
            user=f"{CODER_UID}:{CODER_GID}",
            env=["HOME=/home/coder"],
            exposed_ports={"8080/tcp": {}},
            host_config=HostConfig(
                network_mode=NETWORK_NAME,
                binds=[f"ws-{workspace_id}-home:/home/coder"],
            ),
        )

        await self._containers.create(config)
        await self._containers.start(container_name)
        logger.info("Created and started container: %s", container_name)

    async def delete(self, workspace_id: str) -> None:
        """Delete container for workspace."""
        container_name = self._container_name(workspace_id)

        # Stop first (ignore errors if not running)
        await self._containers.stop(container_name)

        # Remove container
        await self._containers.remove(container_name)

    async def is_running(self, workspace_id: str) -> bool:
        """Check if container is running."""
        container_name = self._container_name(workspace_id)

        data = await self._containers.inspect(container_name)
        if not data:
            return False

        state = data.get("State", {})
        return state.get("Running", False) and state.get("Health", {}).get(
            "Status", "healthy"
        ) in ("healthy", None)

    async def close(self) -> None:
        """Close is no-op (Docker client is singleton)."""
        pass
