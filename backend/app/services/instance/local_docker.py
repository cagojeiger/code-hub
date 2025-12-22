"""Local Docker instance controller.

Manages workspace container lifecycle using Docker Engine API.
"""

import logging
from typing import Literal

import docker
from docker.errors import NotFound
from docker.models.containers import Container

from app.services.instance.interface import (
    InstanceController,
    InstanceStatus,
    UpstreamInfo,
)

logger = logging.getLogger(__name__)

# Default values (can be overridden via config)
DEFAULT_CONTAINER_PREFIX = "codehub-ws-"
DEFAULT_NETWORK_NAME = "codehub-net"
HOME_MOUNT_PATH = "/home/coder"
CODE_SERVER_PORT = 8080
CODER_UID = 1000
CODER_GID = 1000


class LocalDockerInstanceController(InstanceController):
    """Instance controller using local Docker engine."""

    def __init__(
        self,
        docker_host: str | None = None,
        container_prefix: str | None = None,
        network_name: str | None = None,
        expose_ports: bool = False,
    ) -> None:
        """Initialize with optional Docker host and naming configuration.

        Args:
            docker_host: Docker host URL (e.g., 'tcp://docker-proxy:2375').
                        If None, uses DOCKER_HOST env var or default socket.
            container_prefix: Prefix for container names (default: 'codehub-ws-').
            network_name: Docker network name (default: 'codehub-net').
            expose_ports: If True, bind container ports to random host ports.
                         Used for E2E testing where the test client runs on host.
        """
        if docker_host:
            self._client = docker.DockerClient(base_url=docker_host)
        else:
            self._client = docker.from_env()

        self._container_prefix = container_prefix or DEFAULT_CONTAINER_PREFIX
        self._network_name = network_name or DEFAULT_NETWORK_NAME
        self._expose_ports = expose_ports

    @property
    def backend_name(self) -> Literal["local-docker"]:
        return "local-docker"

    @property
    def container_prefix(self) -> str:
        """Get the container name prefix."""
        return self._container_prefix

    @property
    def network_name(self) -> str:
        """Get the Docker network name."""
        return self._network_name

    def _container_name(self, workspace_id: str) -> str:
        """Generate container name from workspace ID."""
        return f"{self._container_prefix}{workspace_id}"

    def _get_container(self, workspace_id: str) -> Container | None:
        """Get container by workspace ID, or None if not found."""
        try:
            return self._client.containers.get(self._container_name(workspace_id))
        except NotFound:
            return None

    def _ensure_network_sync(self) -> None:
        """Ensure Docker network exists (sync)."""
        try:
            self._client.networks.get(self._network_name)
        except NotFound:
            logger.info("Creating network: %s", self._network_name)
            self._client.networks.create(self._network_name, driver="bridge")

    def _start_workspace_sync(
        self,
        workspace_id: str,
        image_ref: str,
        home_mount: str,
    ) -> None:
        """Start workspace container (sync)."""
        container_name = self._container_name(workspace_id)
        container = self._get_container(workspace_id)

        if container:
            # Container exists - just start if not running
            if container.status != "running":
                logger.info("Starting existing container: %s", container_name)
                container.start()
            else:
                logger.info("Container already running: %s", container_name)
        else:
            # Container doesn't exist - create and start
            self._ensure_network_sync()

            logger.info(
                "Creating container: %s (image=%s, home=%s)",
                container_name,
                image_ref,
                home_mount,
            )

            # Build container kwargs
            container_kwargs: dict = {
                "command": ["--auth", "none"],  # Disable password authentication
                "name": container_name,
                "detach": True,
                "network": self._network_name,
                "volumes": {home_mount: {"bind": HOME_MOUNT_PATH, "mode": "rw"}},
                "user": f"{CODER_UID}:{CODER_GID}",
                "environment": {"HOME": HOME_MOUNT_PATH},
            }

            # Port binding for E2E tests (expose_ports=True)
            # In production, proxy connects via internal Docker network
            if self._expose_ports:
                # Bind container port to random available host port
                container_kwargs["ports"] = {
                    f"{CODE_SERVER_PORT}/tcp": ("127.0.0.1", None)
                }

            self._client.containers.run(image_ref, **container_kwargs)
            logger.info("Container created and started: %s", container_name)

    async def start_workspace(
        self,
        workspace_id: str,
        image_ref: str,
        home_mount: str,
    ) -> None:
        """Start workspace container. Idempotent.

        Execute synchronous docker-py calls in a thread pool.
        """
        import asyncio

        await asyncio.to_thread(
            self._start_workspace_sync, workspace_id, image_ref, home_mount
        )

    def _stop_workspace_sync(self, workspace_id: str) -> None:
        """Stop workspace container (sync)."""
        container = self._get_container(workspace_id)

        if not container:
            logger.info(
                "Container not found (no-op): %s", self._container_name(workspace_id)
            )
            return

        if container.status == "running":
            logger.info("Stopping container: %s", self._container_name(workspace_id))
            container.stop()
        else:
            logger.info(
                "Container already stopped: %s", self._container_name(workspace_id)
            )

    async def stop_workspace(self, workspace_id: str) -> None:
        """Stop workspace container. Idempotent.

        Execute synchronous docker-py calls in a thread pool.
        """
        import asyncio

        await asyncio.to_thread(self._stop_workspace_sync, workspace_id)

    def _delete_workspace_sync(self, workspace_id: str) -> None:
        """Delete workspace container (sync)."""
        container = self._get_container(workspace_id)

        if not container:
            logger.info(
                "Container not found (no-op): %s", self._container_name(workspace_id)
            )
            return

        logger.info("Deleting container: %s", self._container_name(workspace_id))
        container.remove(force=True)

    async def delete_workspace(self, workspace_id: str) -> None:
        """Delete workspace container. Idempotent.

        Execute synchronous docker-py calls in a thread pool.
        """
        import asyncio

        await asyncio.to_thread(self._delete_workspace_sync, workspace_id)

    def _resolve_upstream_sync(self, workspace_id: str) -> UpstreamInfo:
        """Resolve upstream connection info (sync)."""
        container = self._get_container(workspace_id)

        container_name = self._container_name(workspace_id)
        if not container:
            raise ValueError(f"Container not found: {container_name}")

        if container.status != "running":
            raise ValueError(f"Container not running: {container_name}")

        # When expose_ports is enabled (E2E tests), return localhost:host_port
        # Otherwise return container name for internal network communication
        if self._expose_ports:
            # Get the host port binding
            container.reload()  # Refresh to get port info
            port_bindings = container.attrs.get("NetworkSettings", {}).get("Ports", {})
            port_key = f"{CODE_SERVER_PORT}/tcp"
            if port_bindings.get(port_key):
                host_port = int(port_bindings[port_key][0]["HostPort"])
                return UpstreamInfo(host="127.0.0.1", port=host_port)
            raise ValueError(f"No port binding found for {container_name}")

        return UpstreamInfo(host=container_name, port=CODE_SERVER_PORT)

    async def resolve_upstream(self, workspace_id: str) -> UpstreamInfo:
        """Resolve upstream connection info via docker inspect.

        Execute synchronous docker-py calls in a thread pool.
        """
        import asyncio

        return await asyncio.to_thread(self._resolve_upstream_sync, workspace_id)

    def _get_status_sync(self, workspace_id: str) -> InstanceStatus:
        """Query current container status (sync)."""
        container = self._get_container(workspace_id)

        if not container:
            return InstanceStatus(exists=False, running=False, healthy=False)

        running = container.status == "running"

        # Health check: for MVP, consider running as healthy
        # Real health check will be done by Control Plane via HTTP probe
        healthy = running

        return InstanceStatus(
            exists=True,
            running=running,
            healthy=healthy,
        )

    async def get_status(self, workspace_id: str) -> InstanceStatus:
        """Query current container status.

        Execute synchronous docker-py calls in a thread pool.
        """
        import asyncio

        return await asyncio.to_thread(self._get_status_sync, workspace_id)
