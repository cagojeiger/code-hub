"""Docker Engine API client for Agent.

Provides async Docker API access for containers and volumes.
Supports both Unix socket and TCP connections.
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import httpx
from pydantic import BaseModel

from codehub_agent.api.errors import VolumeInUseError
from codehub_agent.config import get_agent_config
from codehub_agent.infra.concurrency import get_docker_read_semaphore, get_docker_write_semaphore
from codehub_agent.logging_schema import LogEvent
from codehub_agent.metrics import AGENT_DOCKER_DURATION, AGENT_DOCKER_ERRORS

logger = logging.getLogger(__name__)

_agent_config = get_agent_config()


class HostConfig(BaseModel):
    """Docker HostConfig for container creation."""

    network_mode: str = "bridge"
    binds: list[str] = []
    dns: list[str] = []
    dns_opt: list[str] = []

    model_config = {"frozen": True}

    def to_api(self) -> dict:
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
    labels: dict[str, str] = {}

    model_config = {"frozen": True}

    def to_api(self) -> dict:
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
        if self.labels:
            result["Labels"] = self.labels
        return result


class VolumeConfig(BaseModel):
    """Docker volume configuration for creation."""

    name: str
    driver: str = "local"
    labels: dict[str, str] = {}

    model_config = {"frozen": True}

    def to_api(self) -> dict:
        result: dict = {"Name": self.name, "Driver": self.driver}
        if self.labels:
            result["Labels"] = self.labels
        return result


class ContainerState(BaseModel):
    """Container state from inspect API."""

    Running: bool = False
    Status: str = ""
    Health: dict | None = None

    model_config = {"extra": "ignore", "frozen": True}


class ContainerListItem(BaseModel):
    """Container from list API."""

    Id: str
    Names: list[str]
    State: str
    Status: str
    Created: int = 0

    model_config = {"extra": "ignore", "frozen": True}


class ContainerInspect(BaseModel):
    """Container from inspect API."""

    Id: str
    State: ContainerState

    model_config = {"extra": "ignore", "frozen": True}


class VolumeListItem(BaseModel):
    """Volume from list API."""

    Name: str

    model_config = {"extra": "ignore", "frozen": True}


class DockerClient:
    """Async Docker API client."""

    def __init__(self, docker_host: str | None = None) -> None:
        self._host = docker_host or _agent_config.docker.host
        self._client: httpx.AsyncClient | None = None

    def _create_client(self) -> httpx.AsyncClient:
        timeout = _agent_config.docker.api_timeout
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)

        if self._host.startswith("unix://"):
            socket_path = self._host.removeprefix("unix://")
            transport = httpx.AsyncHTTPTransport(uds=socket_path)
            return httpx.AsyncClient(
                transport=transport,
                base_url="http://localhost",
                timeout=timeout,
                limits=limits,
            )
        else:
            base_url = self._host.replace("tcp://", "http://")
            return httpx.AsyncClient(base_url=base_url, timeout=timeout, limits=limits)

    def client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = self._create_client()
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# Shared Docker client singleton
_shared_docker_client: DockerClient | None = None


def _get_shared_client() -> DockerClient:
    """Get or create the shared Docker client."""
    global _shared_docker_client
    if _shared_docker_client is None:
        _shared_docker_client = DockerClient()
    return _shared_docker_client


async def close_docker() -> None:
    """Close the shared Docker client."""
    global _shared_docker_client
    if _shared_docker_client is not None:
        await _shared_docker_client.close()
        _shared_docker_client = None


@asynccontextmanager
async def _docker_timer(operation: str) -> AsyncIterator[None]:
    """Time Docker operations and classify errors."""
    start = time.monotonic()
    try:
        yield
    except httpx.TimeoutException:
        AGENT_DOCKER_ERRORS.labels(operation=operation, error_type="timeout").inc()
        raise
    except httpx.HTTPStatusError as exc:
        error_type = "not_found" if exc.response.status_code == 404 else "api_error"
        AGENT_DOCKER_ERRORS.labels(operation=operation, error_type=error_type).inc()
        raise
    except Exception:
        AGENT_DOCKER_ERRORS.labels(operation=operation, error_type="api_error").inc()
        raise
    finally:
        AGENT_DOCKER_DURATION.labels(operation=operation).observe(time.monotonic() - start)


class BaseDockerAPI:
    """Base class for Docker API operations."""

    def __init__(self, client: DockerClient | None = None) -> None:
        self._docker = client or _get_shared_client()


class ContainerAPI(BaseDockerAPI):
    """Docker Container API operations."""

    async def list(self, filters: dict | None = None) -> list[ContainerListItem]:
        async with _docker_timer("list"), get_docker_read_semaphore():
            client = self._docker.client()
            params: dict = {"all": "true"}
            if filters:
                params["filters"] = json.dumps(filters)
            resp = await client.get("/containers/json", params=params)
            resp.raise_for_status()
            return [ContainerListItem.model_validate(c) for c in resp.json()]

    async def inspect(self, name: str) -> ContainerInspect | None:
        async with _docker_timer("inspect"), get_docker_read_semaphore():
            client = self._docker.client()
            resp = await client.get(f"/containers/{name}/json")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return ContainerInspect.model_validate(resp.json())

    async def create(self, config: ContainerConfig) -> None:
        async with _docker_timer("create"), get_docker_write_semaphore():
            client = self._docker.client()
            resp = await client.post(
                "/containers/create",
                params={"name": config.name},
                json=config.to_api(),
            )
            if resp.status_code == 409:
                logger.debug(
                    "Container already exists",
                    extra={"event": LogEvent.CONTAINER_CREATED, "container": config.name, "exists": True},
                )
                return
            resp.raise_for_status()
            logger.info(
                "Container created",
                extra={"event": LogEvent.CONTAINER_CREATED, "container": config.name, "image": config.image},
            )

    async def start(self, name: str) -> None:
        async with _docker_timer("start"), get_docker_write_semaphore():
            client = self._docker.client()
            resp = await client.post(f"/containers/{name}/start")
            if resp.status_code not in (204, 304):
                resp.raise_for_status()
            logger.info(
                "Container started",
                extra={"event": LogEvent.CONTAINER_STARTED, "container": name},
            )

    async def stop(self, name: str, timeout: int = 10) -> None:
        async with _docker_timer("stop"), get_docker_write_semaphore():
            client = self._docker.client()
            resp = await client.post(f"/containers/{name}/stop", params={"t": str(timeout)})
            if resp.status_code not in (204, 304, 404):
                resp.raise_for_status()
            logger.info(
                "Container stopped",
                extra={"event": LogEvent.CONTAINER_STOPPED, "container": name},
            )

    async def remove(self, name: str, force: bool = True) -> None:
        async with _docker_timer("remove"), get_docker_write_semaphore():
            client = self._docker.client()
            resp = await client.delete(
                f"/containers/{name}", params={"force": "true" if force else "false"}
            )
            if resp.status_code == 404:
                logger.debug(
                    "Container not found",
                    extra={"event": LogEvent.CONTAINER_REMOVED, "container": name, "exists": False},
                )
                return
            resp.raise_for_status()
            logger.info(
                "Container removed",
                extra={"event": LogEvent.CONTAINER_REMOVED, "container": name},
            )

    async def wait(self, name: str, timeout: int | None = None) -> int:
        """Wait for container to exit and return exit code.

        Note: This method does NOT use the semaphore because wait operations
        are long-running and should not block other Docker operations.
        """
        async with _docker_timer("wait"):
            if timeout is None:
                timeout = _agent_config.docker.container_wait_timeout
            client = self._docker.client()
            # Add buffer to HTTP timeout beyond container wait timeout
            http_timeout = timeout + _agent_config.docker.timeout_buffer
            resp = await client.post(
                f"/containers/{name}/wait",
                timeout=http_timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            exit_code = data.get("StatusCode", -1)
            logger.info(
                "Container exited",
                extra={"event": LogEvent.CONTAINER_EXITED, "container": name, "exit_code": exit_code},
            )
            return exit_code

    async def logs(self, name: str, stdout: bool = True, stderr: bool = True) -> bytes:
        async with _docker_timer("logs"), get_docker_read_semaphore():
            client = self._docker.client()
            params = {"stdout": stdout, "stderr": stderr}
            resp = await client.get(f"/containers/{name}/logs", params=params)
            resp.raise_for_status()
            return resp.content


class VolumeAPI(BaseDockerAPI):
    """Docker Volume API operations."""

    async def list(self, filters: dict | None = None) -> list[VolumeListItem]:
        async with _docker_timer("volume_list"), get_docker_read_semaphore():
            client = self._docker.client()
            params: dict = {}
            if filters:
                params["filters"] = json.dumps(filters)
            resp = await client.get("/volumes", params=params)
            resp.raise_for_status()
            data = resp.json()
            return [VolumeListItem.model_validate(v) for v in data.get("Volumes", [])]

    async def inspect(self, name: str) -> dict | None:
        async with _docker_timer("volume_inspect"), get_docker_read_semaphore():
            client = self._docker.client()
            resp = await client.get(f"/volumes/{name}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def create(self, config: VolumeConfig) -> None:
        async with _docker_timer("volume_create"), get_docker_write_semaphore():
            client = self._docker.client()
            resp = await client.post("/volumes/create", json=config.to_api())
            if resp.status_code == 409:
                logger.debug(
                    "Volume already exists",
                    extra={"event": LogEvent.VOLUME_CREATED, "volume": config.name, "exists": True},
                )
                return
            resp.raise_for_status()
            logger.info(
                "Volume created",
                extra={"event": LogEvent.VOLUME_CREATED, "volume": config.name},
            )

    async def remove(self, name: str) -> None:
        async with _docker_timer("volume_remove"), get_docker_write_semaphore():
            client = self._docker.client()
            resp = await client.delete(f"/volumes/{name}")
            if resp.status_code == 404:
                logger.debug(
                    "Volume not found",
                    extra={"event": LogEvent.VOLUME_REMOVED, "volume": name, "exists": False},
                )
                return
            if resp.status_code == 409:
                raise VolumeInUseError(f"Volume {name} is in use by a container")
            resp.raise_for_status()
            logger.info(
                "Volume removed",
                extra={"event": LogEvent.VOLUME_REMOVED, "volume": name},
            )


class ImageAPI(BaseDockerAPI):
    """Docker Image API operations."""

    async def exists(self, image_ref: str) -> bool:
        async with _docker_timer("image_exists"), get_docker_read_semaphore():
            client = self._docker.client()
            resp = await client.get(f"/images/{image_ref}/json")
            return resp.status_code == 200

    async def pull(self, image_ref: str) -> None:
        """Pull image from registry.

        Note: This method does NOT use the semaphore because image pulls
        are long-running and should not block other Docker operations.
        """
        async with _docker_timer("image_pull"):
            client = self._docker.client()

            if ":" in image_ref:
                image, tag = image_ref.rsplit(":", 1)
            else:
                image, tag = image_ref, "latest"

            logger.info(
                "Pulling image",
                extra={"event": LogEvent.IMAGE_PULLED, "image": image, "tag": tag, "started": True},
            )

            resp = await client.post(
                "/images/create",
                params={"fromImage": image, "tag": tag},
                timeout=_agent_config.docker.image_pull_timeout,
            )
            resp.raise_for_status()
            logger.info(
                "Image pulled",
                extra={"event": LogEvent.IMAGE_PULLED, "image": image, "tag": tag},
            )

    async def ensure(self, image_ref: str) -> None:
        """Ensure image exists locally, pull if not."""
        if not await self.exists(image_ref):
            await self.pull(image_ref)
