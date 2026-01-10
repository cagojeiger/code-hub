"""Docker Engine API client with Pydantic models.

Provides async Docker API access for containers and volumes.
Supports both Unix socket and TCP (docker-proxy) connections.

Configuration via DockerConfig (DOCKER_ env prefix).
"""

import logging
import os

import httpx
from pydantic import BaseModel

from codehub.app.config import get_settings
from codehub.core.logging_schema import LogEvent
from codehub.core.retryable import VolumeInUseError  # noqa: F401 - re-exported

logger = logging.getLogger(__name__)

_docker_config = get_settings().docker

# Docker host configuration
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")


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
            socket_path = self._host.replace("unix://", "")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                timeout=_docker_config.api_timeout,
            )
        else:
            base_url = self._host
            if base_url.startswith("tcp://"):
                base_url = base_url.replace("tcp://", "http://")
            return httpx.AsyncClient(base_url=base_url, timeout=_docker_config.api_timeout)

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
        logger.info(
            "Started container: %s",
            name,
            extra={"event": LogEvent.CONTAINER_STARTED, "container": name},
        )

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
        logger.info(
            "Stopped container: %s",
            name,
            extra={"event": LogEvent.CONTAINER_STOPPED, "container": name},
        )

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

    async def wait(self, name: str, timeout: int | None = None) -> int:
        """Wait for container to exit and return exit code.

        Args:
            name: Container name or ID
            timeout: Seconds to wait for container to exit (default from config)

        Returns:
            Container exit code (0 = success)
        """
        if timeout is None:
            timeout = _docker_config.container_wait_timeout
        client = await self._docker.get()
        # Use longer HTTP timeout than container timeout
        resp = await client.post(
            f"/containers/{name}/wait",
            timeout=timeout + 10,
        )
        resp.raise_for_status()
        data = resp.json()
        exit_code = data.get("StatusCode", -1)
        log_extra = {
            "event": LogEvent.CONTAINER_EXITED,
            "container": name,
            "exit_code": exit_code,
        }
        if exit_code == 0:
            logger.info("Container %s exited successfully", name, extra=log_extra)
        else:
            logger.warning(
                "Container %s exited with code %d", name, exit_code, extra=log_extra
            )
        return exit_code

    async def logs(
        self, name: str, stdout: bool = True, stderr: bool = True
    ) -> bytes:
        """Get container logs.

        Args:
            name: Container name or ID
            stdout: Include stdout
            stderr: Include stderr

        Returns:
            Raw log bytes (with Docker stream headers)
        """
        client = await self._docker.get()
        params = {"stdout": stdout, "stderr": stderr}
        resp = await client.get(f"/containers/{name}/logs", params=params)
        resp.raise_for_status()
        return resp.content

    async def get_archive(self, name: str, path: str) -> bytes:
        """Get file from container as tar archive.

        Args:
            name: Container name or ID
            path: Path inside container

        Returns:
            Tar archive containing the file/directory
        """
        client = await self._docker.get()
        resp = await client.get(
            f"/containers/{name}/archive",
            params={"path": path},
        )
        resp.raise_for_status()
        return resp.content

    async def put_archive(self, name: str, path: str, data: bytes) -> None:
        """Put tar archive into container.

        Args:
            name: Container name or ID
            path: Destination path inside container
            data: Tar archive data
        """
        client = await self._docker.get()
        resp = await client.put(
            f"/containers/{name}/archive",
            params={"path": path},
            content=data,
            headers={"Content-Type": "application/x-tar"},
        )
        resp.raise_for_status()


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
        """Check if image exists locally.

        Args:
            image_ref: Image reference (e.g., "python:3.13-slim")

        Returns:
            True if image exists locally
        """
        client = await self._docker.get()
        resp = await client.get(f"/images/{image_ref}/json")
        return resp.status_code == 200

    async def pull(self, image_ref: str) -> None:
        """Pull image from registry.

        Args:
            image_ref: Image reference (e.g., "python:3.13-slim")

        Note:
            Uses streaming endpoint. Docker API returns chunked JSON progress.
        """
        client = await self._docker.get()

        # Parse image:tag
        if ":" in image_ref:
            image, tag = image_ref.rsplit(":", 1)
        else:
            image, tag = image_ref, "latest"

        logger.info("Pulling image: %s:%s", image, tag)

        # POST /images/create?fromImage=xxx&tag=yyy
        # This is a streaming endpoint, read until complete
        resp = await client.post(
            "/images/create",
            params={"fromImage": image, "tag": tag},
            timeout=_docker_config.image_pull_timeout,
        )
        resp.raise_for_status()
        logger.info("Pulled image: %s:%s", image, tag)

    async def ensure(self, image_ref: str) -> None:
        """Ensure image exists locally, pull if not.

        Args:
            image_ref: Image reference (e.g., "python:3.13-slim")
        """
        if not await self.exists(image_ref):
            await self.pull(image_ref)
