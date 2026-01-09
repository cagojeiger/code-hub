"""Docker instance controller implementation."""

import logging

from codehub.app.config import get_settings
from codehub.core.interfaces import ContainerInfo, InstanceController, UpstreamInfo
from codehub.infra.docker import (
    ContainerAPI,
    ContainerConfig,
    HostConfig,
    ImageAPI,
)

logger = logging.getLogger(__name__)


class DockerInstanceController(InstanceController):
    """Docker-based instance controller using ContainerAPI."""

    def __init__(
        self,
        containers: ContainerAPI | None = None,
        images: ImageAPI | None = None,
    ) -> None:
        settings = get_settings()
        self._runtime = settings.runtime
        self._docker = settings.docker
        self._containers = containers or ContainerAPI()
        self._images = images or ImageAPI()

    def _container_name(self, workspace_id: str) -> str:
        return f"{self._runtime.resource_prefix}{workspace_id}"

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

        existing = await self._containers.inspect(container_name)
        if existing:
            await self._containers.start(container_name)
            logger.info("Started existing container: %s", container_name)
            return

        # Ensure image exists (auto-pull if not)
        image = image_ref or self._runtime.default_image
        await self._images.ensure(image)

        port = self._runtime.container_port
        config = ContainerConfig(
            image=image,
            name=container_name,
            cmd=["--auth", "none", "--bind-addr", f"0.0.0.0:{port}"],
            user=f"{self._docker.coder_uid}:{self._docker.coder_gid}",
            env=["HOME=/home/coder"],
            exposed_ports={f"{port}/tcp": {}},
            host_config=HostConfig(
                network_mode=self._docker.network_name,
                binds=[f"{self._runtime.resource_prefix}{workspace_id}-home:/home/coder"],
                dns=self._docker.dns_servers,
                dns_opt=self._docker.dns_options,
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

    async def resolve_upstream(self, workspace_id: str) -> UpstreamInfo | None:
        """Resolve upstream address for proxy.

        Returns container_name:port for Docker environment.
        """
        return UpstreamInfo(
            hostname=self._container_name(workspace_id),
            port=self._runtime.container_port,
        )
