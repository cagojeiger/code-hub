"""Docker Engine API client for Agent.

Provides async Docker API access for containers and volumes.
Supports both Unix socket and TCP connections.
"""

import json
import logging

import httpx
from pydantic import BaseModel

from codehub_agent.config import get_agent_config

logger = logging.getLogger(__name__)

_agent_config = get_agent_config()


class VolumeInUseError(Exception):
    """Raised when trying to remove a volume that is in use."""

    pass


# =============================================================================
# Pydantic Models
# =============================================================================


class HostConfig(BaseModel):
    """Docker HostConfig for container creation."""

    network_mode: str = "bridge"
    binds: list[str] = []
    dns: list[str] = []
    dns_opt: list[str] = []

    model_config = {"frozen": True}

    def to_api(self) -> dict:
        """Convert to Docker API format."""
        result = {
            "NetworkMode": self.network_mode,
            "Binds": self.binds,
        }
        if self.dns:
            result["Dns"] = self.dns
        if self.dns_opt:
            result["DnsOptions"] = self.dns_opt
        return result


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
    """Async Docker API client."""

    def __init__(self, docker_host: str | None = None) -> None:
        self._host = docker_host or _agent_config.docker.host
        self._client: httpx.AsyncClient | None = None

    def _create_client(self) -> httpx.AsyncClient:
        """Create a new HTTP client."""
        timeout = _agent_config.docker.api_timeout
        if self._host.startswith("unix://"):
            socket_path = self._host.replace("unix://", "")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                timeout=timeout,
            )
        else:
            base_url = self._host
            if base_url.startswith("tcp://"):
                base_url = base_url.replace("tcp://", "http://")
            return httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def get(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
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
        """List containers."""
        client = await self._docker.get()
        params: dict = {"all": "true"}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/containers/json", params=params)
        resp.raise_for_status()
        return resp.json()

    async def inspect(self, name: str) -> dict | None:
        """Inspect a container."""
        client = await self._docker.get()
        resp = await client.get(f"/containers/{name}/json")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def create(self, config: ContainerConfig) -> None:
        """Create a container (idempotent)."""
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
        """Start a container."""
        client = await self._docker.get()
        resp = await client.post(f"/containers/{name}/start")
        if resp.status_code not in (204, 304):
            resp.raise_for_status()
        logger.info("Started container: %s", name)

    async def stop(self, name: str, timeout: int = 10) -> None:
        """Stop a container."""
        client = await self._docker.get()
        resp = await client.post(f"/containers/{name}/stop", params={"t": str(timeout)})
        if resp.status_code not in (204, 304, 404):
            resp.raise_for_status()
        logger.info("Stopped container: %s", name)

    async def remove(self, name: str, force: bool = True) -> None:
        """Remove a container."""
        client = await self._docker.get()
        resp = await client.delete(
            f"/containers/{name}", params={"force": "true" if force else "false"}
        )
        if resp.status_code == 404:
            logger.debug("Container not found: %s", name)
            return
        resp.raise_for_status()
        logger.info("Removed container: %s", name)

    async def wait(self, name: str, timeout: int | None = None) -> int:
        """Wait for container to exit and return exit code."""
        if timeout is None:
            timeout = _agent_config.docker.container_wait_timeout
        client = await self._docker.get()
        # Add buffer to HTTP timeout beyond container wait timeout
        http_timeout = timeout + _agent_config.docker.timeout_buffer
        resp = await client.post(
            f"/containers/{name}/wait",
            timeout=http_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        exit_code = data.get("StatusCode", -1)
        logger.info("Container %s exited with code %d", name, exit_code)
        return exit_code

    async def logs(self, name: str, stdout: bool = True, stderr: bool = True) -> bytes:
        """Get container logs."""
        client = await self._docker.get()
        params = {"stdout": stdout, "stderr": stderr}
        resp = await client.get(f"/containers/{name}/logs", params=params)
        resp.raise_for_status()
        return resp.content


# =============================================================================
# Volume API
# =============================================================================


class VolumeAPI:
    """Docker Volume API operations."""

    def __init__(self, client: DockerClient | None = None) -> None:
        self._docker = client or get_docker_client()

    async def list(self, filters: dict | None = None) -> list[dict]:
        """List volumes."""
        client = await self._docker.get()
        params: dict = {}
        if filters:
            params["filters"] = json.dumps(filters)
        resp = await client.get("/volumes", params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("Volumes", [])

    async def inspect(self, name: str) -> dict | None:
        """Inspect a volume."""
        client = await self._docker.get()
        resp = await client.get(f"/volumes/{name}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def create(self, config: VolumeConfig) -> None:
        """Create a volume (idempotent)."""
        client = await self._docker.get()
        resp = await client.post("/volumes/create", json=config.to_api())
        if resp.status_code == 409:
            logger.debug("Volume already exists: %s", config.name)
            return
        resp.raise_for_status()
        logger.info("Created volume: %s", config.name)

    async def remove(self, name: str) -> None:
        """Remove a volume."""
        client = await self._docker.get()
        resp = await client.delete(f"/volumes/{name}")
        if resp.status_code == 404:
            logger.debug("Volume not found: %s", name)
            return
        if resp.status_code == 409:
            raise VolumeInUseError(f"Volume {name} is in use by a container")
        resp.raise_for_status()
        logger.info("Removed volume: %s", name)


# =============================================================================
# Image API
# =============================================================================


class ImageAPI:
    """Docker Image API operations."""

    def __init__(self, client: DockerClient | None = None) -> None:
        self._docker = client or get_docker_client()

    async def exists(self, image_ref: str) -> bool:
        """Check if image exists locally."""
        client = await self._docker.get()
        resp = await client.get(f"/images/{image_ref}/json")
        return resp.status_code == 200

    async def pull(self, image_ref: str) -> None:
        """Pull image from registry."""
        client = await self._docker.get()

        if ":" in image_ref:
            image, tag = image_ref.rsplit(":", 1)
        else:
            image, tag = image_ref, "latest"

        logger.info("Pulling image: %s:%s", image, tag)

        resp = await client.post(
            "/images/create",
            params={"fromImage": image, "tag": tag},
            timeout=_agent_config.image_pull_timeout,
        )
        resp.raise_for_status()
        logger.info("Pulled image: %s:%s", image, tag)

    async def ensure(self, image_ref: str) -> None:
        """Ensure image exists locally, pull if not."""
        if not await self.exists(image_ref):
            await self.pull(image_ref)
