"""Docker Engine API client with Pydantic models.

Provides async Docker API access for containers and volumes.
Supports both Unix socket and TCP (docker-proxy) connections.
"""

import logging
import os

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Docker host configuration
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")


# =============================================================================
# Pydantic Models
# =============================================================================


class HostConfig(BaseModel):
    """Docker HostConfig for container creation."""

    network_mode: str = "bridge"
    binds: list[str] = []

    model_config = {"frozen": True}

    def to_api(self) -> dict:
        """Convert to Docker API format."""
        return {
            "NetworkMode": self.network_mode,
            "Binds": self.binds,
        }


class ContainerConfig(BaseModel):
    """Docker container configuration for creation."""

    image: str
    name: str
    cmd: list[str] = []
    user: str | None = None
    env: list[str] = []
    exposed_ports: dict[str, dict] = {}
    host_config: HostConfig = HostConfig()

    model_config = {"frozen": True}

    def to_api(self) -> dict:
        """Convert to Docker API JSON format."""
        result: dict = {
            "Image": self.image,
            "Cmd": self.cmd,
            "ExposedPorts": self.exposed_ports,
            "HostConfig": self.host_config.to_api(),
        }
        if self.user:
            result["User"] = self.user
        if self.env:
            result["Env"] = self.env
        return result


class VolumeConfig(BaseModel):
    """Docker volume configuration for creation."""

    name: str
    driver: str = "local"
    labels: dict[str, str] = {}

    model_config = {"frozen": True}

    def to_api(self) -> dict:
        """Convert to Docker API format."""
        result: dict = {"Name": self.name, "Driver": self.driver}
        if self.labels:
            result["Labels"] = self.labels
        return result


# =============================================================================
# Docker Client (Singleton)
# =============================================================================


class DockerClient:
    """Async Docker API client.

    Supports Unix socket and TCP connections.
    Handles event loop changes (important for tests).
    """

    def __init__(self, docker_host: str | None = None) -> None:
        self._host = docker_host or DOCKER_HOST
        self._client: httpx.AsyncClient | None = None

    def _create_client(self) -> httpx.AsyncClient:
        """Create a new HTTP client."""
        if self._host.startswith("unix://"):
            # Unix socket
            socket_path = self._host.replace("unix://", "")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                timeout=30.0,
            )
        else:
            # TCP (docker-proxy)
            base_url = self._host
            if base_url.startswith("tcp://"):
                base_url = base_url.replace("tcp://", "http://")
            return httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def get(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Recreates the client if the previous one was closed
        (e.g., due to event loop change in tests).
        """
        if self._client is None or self._client.is_closed:
            self._client = self._create_client()
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Global singleton
_docker_client: DockerClient | None = None


def get_docker_client() -> DockerClient:
    """Get the global Docker client singleton."""
    global _docker_client
    if _docker_client is None:
        _docker_client = DockerClient()
    return _docker_client


async def close_docker() -> None:
    """Close the global Docker client."""
    global _docker_client
    if _docker_client:
        await _docker_client.close()
        _docker_client = None


# =============================================================================
# Container API
# =============================================================================


class ContainerAPI:
    """Docker Container API operations."""

    def __init__(self, client: DockerClient | None = None) -> None:
        self._docker = client or get_docker_client()

    async def list(self, filters: dict | None = None) -> list[dict]:
        """List containers.

        Args:
            filters: Docker API filters (e.g., {"name": ["ws-"]})

        Returns:
            List of container info dicts
        """
        client = await self._docker.get()
        params: dict = {"all": "true"}
        if filters:
            import json

            params["filters"] = json.dumps(filters)
        resp = await client.get("/containers/json", params=params)
        resp.raise_for_status()
        return resp.json()

    async def inspect(self, name: str) -> dict | None:
        """Inspect a container.

        Args:
            name: Container name or ID

        Returns:
            Container info dict or None if not found
        """
        client = await self._docker.get()
        resp = await client.get(f"/containers/{name}/json")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def create(self, config: ContainerConfig) -> None:
        """Create a container.

        Args:
            config: Container configuration

        Note:
            Idempotent - returns silently if container already exists.
        """
        client = await self._docker.get()
        resp = await client.post(
            "/containers/create",
            params={"name": config.name},
            json=config.to_api(),
        )
        if resp.status_code == 409:
            logger.debug("Container already exists: %s", config.name)
            return
        resp.raise_for_status()
        logger.info("Created container: %s", config.name)

    async def start(self, name: str) -> None:
        """Start a container.

        Args:
            name: Container name or ID
        """
        client = await self._docker.get()
        resp = await client.post(f"/containers/{name}/start")
        if resp.status_code not in (204, 304):  # 304 = already started
            resp.raise_for_status()
        logger.debug("Started container: %s", name)

    async def stop(self, name: str, timeout: int = 10) -> None:
        """Stop a container.

        Args:
            name: Container name or ID
            timeout: Seconds to wait before killing
        """
        client = await self._docker.get()
        resp = await client.post(f"/containers/{name}/stop", params={"t": str(timeout)})
        if resp.status_code not in (204, 304, 404):  # 404 = not found, ok
            resp.raise_for_status()
        logger.debug("Stopped container: %s", name)

    async def remove(self, name: str, force: bool = True) -> None:
        """Remove a container.

        Args:
            name: Container name or ID
            force: Force removal of running container
        """
        client = await self._docker.get()
        resp = await client.delete(
            f"/containers/{name}", params={"force": "true" if force else "false"}
        )
        if resp.status_code == 404:
            logger.debug("Container not found: %s", name)
            return
        resp.raise_for_status()
        logger.info("Removed container: %s", name)


# =============================================================================
# Volume API
# =============================================================================


class VolumeAPI:
    """Docker Volume API operations."""

    def __init__(self, client: DockerClient | None = None) -> None:
        self._docker = client or get_docker_client()

    async def list(self, filters: dict | None = None) -> list[dict]:
        """List volumes.

        Args:
            filters: Docker API filters (e.g., {"name": ["ws-"]})

        Returns:
            List of volume info dicts
        """
        client = await self._docker.get()
        params: dict = {}
        if filters:
            import json

            params["filters"] = json.dumps(filters)
        resp = await client.get("/volumes", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("Volumes", [])

    async def inspect(self, name: str) -> dict | None:
        """Inspect a volume.

        Args:
            name: Volume name

        Returns:
            Volume info dict or None if not found
        """
        client = await self._docker.get()
        resp = await client.get(f"/volumes/{name}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def create(self, config: VolumeConfig) -> None:
        """Create a volume.

        Args:
            config: Volume configuration
        """
        client = await self._docker.get()
        resp = await client.post("/volumes/create", json=config.to_api())
        if resp.status_code == 409:
            # Volume already exists
            logger.debug("Volume already exists: %s", config.name)
            return
        resp.raise_for_status()
        logger.info("Created volume: %s", config.name)

    async def remove(self, name: str) -> None:
        """Remove a volume.

        Args:
            name: Volume name
        """
        client = await self._docker.get()
        resp = await client.delete(f"/volumes/{name}")
        if resp.status_code == 404:
            logger.debug("Volume not found: %s", name)
            return
        if resp.status_code == 409:
            logger.warning("Volume in use, cannot delete: %s", name)
            return
        resp.raise_for_status()
        logger.info("Removed volume: %s", name)
