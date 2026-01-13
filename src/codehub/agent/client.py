"""Agent HTTP client for Control Plane.

This client communicates with the Agent service to manage
Docker containers, volumes, and jobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from codehub.core.interfaces import (
    ArchiveInfo,
    ContainerInfo,
    InstanceController,
    JobResult,
    JobRunner,
    StorageProvider,
    UpstreamInfo,
    VolumeInfo,
    VolumeProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Agent connection configuration."""

    endpoint: str
    api_key: str = ""
    timeout: float = 30.0
    job_timeout: float = 600.0


class AgentClient(InstanceController, VolumeProvider, JobRunner, StorageProvider):
    """HTTP client for Agent API.

    Implements all adapter interfaces by delegating to Agent.
    """

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with API key."""
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._config.endpoint,
                headers=self._get_headers(),
                timeout=self._config.timeout,
            )
        return self._client

    async def _request(
        self,
        method: Literal["get", "post", "delete"],
        path: str,
        *,
        on_404: Literal["raise", "none", "false"] = "raise",
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response | None:
        """Make HTTP request with common error handling.

        Args:
            method: HTTP method (get, post, delete).
            path: URL path.
            on_404: How to handle 404 responses:
                - "raise": Raise HTTPStatusError (default)
                - "none": Return None
                - "false": Return the response for caller to handle
            timeout: Request timeout (uses default if not specified).
            **kwargs: Additional arguments for httpx request.

        Returns:
            Response object, or None if 404 and on_404="none".
        """
        client = await self._get_client()
        if timeout:
            kwargs["timeout"] = timeout

        resp = await getattr(client, method)(path, **kwargs)

        if resp.status_code == 404:
            if on_404 == "raise":
                resp.raise_for_status()
            elif on_404 == "none":
                return None
            # on_404 == "false": return response as-is

        if resp.status_code != 404:
            resp.raise_for_status()

        return resp

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # InstanceController interface
    # =========================================================================

    async def list_all(self, prefix: str) -> list[ContainerInfo]:
        """List all containers from Agent."""
        resp = await self._request("get", "/api/v1/instances")
        if resp is None:
            return []

        data = resp.json()
        return [
            ContainerInfo(
                workspace_id=item["workspace_id"],
                running=item["running"],
                reason=item["reason"],
                message=item["message"],
            )
            for item in data.get("instances", [])
        ]

    async def start(self, workspace_id: str, image_ref: str) -> None:
        """Start container via Agent."""
        await self._request(
            "post",
            f"/api/v1/instances/{workspace_id}/start",
            json={"image_ref": image_ref},
        )
        logger.info("Started instance via Agent: %s", workspace_id)

    async def delete(self, workspace_id: str) -> None:
        """Delete container via Agent."""
        await self._request("delete", f"/api/v1/instances/{workspace_id}")
        logger.info("Deleted instance via Agent: %s", workspace_id)

    async def is_running(self, workspace_id: str) -> bool:
        """Check if container is running via Agent."""
        resp = await self._request(
            "get", f"/api/v1/instances/{workspace_id}/status", on_404="none"
        )
        if resp is None:
            return False
        data = resp.json()
        return data.get("running", False) and data.get("healthy", False)

    async def resolve_upstream(self, workspace_id: str) -> UpstreamInfo | None:
        """Get upstream address from Agent."""
        resp = await self._request(
            "get", f"/api/v1/instances/{workspace_id}/upstream", on_404="none"
        )
        if resp is None:
            return None
        data = resp.json()
        return UpstreamInfo(
            hostname=data["hostname"],
            port=data["port"],
        )

    # =========================================================================
    # VolumeProvider interface
    # =========================================================================

    @staticmethod
    def _extract_workspace_id(name: str) -> str:
        """Extract workspace_id from volume name.

        Volume name format: {prefix}{workspace_id}-home
        Example: codehub-ws123-home -> ws123
        """
        if name.endswith("-home"):
            workspace_id = name[:-5]  # Remove "-home"
            if "-" in workspace_id:
                # Remove prefix (e.g., "codehub-")
                workspace_id = workspace_id.split("-", 1)[-1]
            return workspace_id
        return name

    async def create(self, name: str) -> None:
        """Create volume via Agent."""
        workspace_id = self._extract_workspace_id(name)
        await self._request("post", f"/api/v1/volumes/{workspace_id}")
        logger.info("Created volume via Agent: %s", workspace_id)

    async def remove(self, name: str) -> None:
        """Remove volume via Agent."""
        workspace_id = self._extract_workspace_id(name)
        await self._request("delete", f"/api/v1/volumes/{workspace_id}")
        logger.info("Removed volume via Agent: %s", workspace_id)

    async def exists(self, name: str) -> bool:
        """Check if volume exists via Agent."""
        workspace_id = self._extract_workspace_id(name)
        resp = await self._request(
            "get", f"/api/v1/volumes/{workspace_id}/exists", on_404="none"
        )
        if resp is None:
            return False
        data = resp.json()
        return data.get("exists", False)

    async def list(self, prefix: str) -> list[dict]:
        """List volumes via Agent."""
        resp = await self._request("get", "/api/v1/volumes")
        if resp is None:
            return []
        data = resp.json()
        return data.get("volumes", [])

    # =========================================================================
    # JobRunner interface
    # =========================================================================

    @staticmethod
    def _parse_archive_url(archive_url: str) -> tuple[str, str]:
        """Parse archive URL to extract workspace_id and op_id.

        Format: s3://bucket/cluster_id/workspace_id/op_id/home.tar.zst

        Returns:
            Tuple of (workspace_id, op_id).

        Raises:
            ValueError: If URL format is invalid.
        """
        parts = archive_url.replace("s3://", "").split("/")
        if len(parts) >= 4:
            return parts[2], parts[3]
        raise ValueError(f"Invalid archive_url format: {archive_url}")

    async def _run_job(
        self, job_type: str, workspace_id: str, op_id: str
    ) -> JobResult:
        """Run a job (archive or restore) via Agent."""
        resp = await self._request(
            "post",
            f"/api/v1/jobs/{job_type}",
            json={"workspace_id": workspace_id, "op_id": op_id},
            timeout=self._config.job_timeout,
        )
        if resp is None:
            raise RuntimeError(f"Job {job_type} failed: no response")
        data = resp.json()
        return JobResult(exit_code=data["exit_code"], logs=data["logs"])

    async def run_archive(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run archive job via Agent.

        Note: Agent handles S3 configuration internally.
        """
        workspace_id, op_id = self._parse_archive_url(archive_url)
        return await self._run_job("archive", workspace_id, op_id)

    async def run_restore(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run restore job via Agent."""
        workspace_id, op_id = self._parse_archive_url(archive_url)
        return await self._run_job("restore", workspace_id, op_id)

    # =========================================================================
    # StorageProvider interface
    # =========================================================================

    @staticmethod
    def _parse_archive_key(archive_key: str) -> str:
        """Extract op_id from archive key.

        Format: cluster_id/workspace_id/op_id/home.tar.zst

        Returns:
            op_id from the archive key.

        Raises:
            ValueError: If key format is invalid.
        """
        parts = archive_key.split("/")
        if len(parts) >= 3:
            return parts[2]
        raise ValueError(f"Invalid archive_key format: {archive_key}")

    async def list_volumes(self, prefix: str) -> list[VolumeInfo]:
        """List volumes from Agent."""
        resp = await self._request("get", "/api/v1/volumes")
        if resp is None:
            return []

        data = resp.json()
        return [
            VolumeInfo(
                workspace_id=item["workspace_id"],
                exists=item["exists"],
                reason="VolumeReady" if item["exists"] else "VolumeNotFound",
                message="",
            )
            for item in data.get("volumes", [])
        ]

    async def list_archives(self, prefix: str) -> list[ArchiveInfo]:
        """List archives - not implemented via Agent yet."""
        logger.warning("list_archives not fully implemented via Agent")
        return []

    async def list_all_archive_keys(self, prefix: str) -> set[str]:
        """List all archive keys - not implemented via Agent yet."""
        logger.warning("list_all_archive_keys not fully implemented via Agent")
        return set()

    async def provision(self, workspace_id: str) -> None:
        """Provision volume via Agent."""
        await self._request("post", f"/api/v1/volumes/{workspace_id}")

    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore workspace from archive via Agent.

        Returns:
            restore_marker (= archive_key) for completion check.
        """
        op_id = self._parse_archive_key(archive_key)
        await self._run_job("restore", workspace_id, op_id)
        return archive_key

    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive workspace via Agent.

        Returns:
            Archive key for the created archive.
        """
        await self._run_job("archive", workspace_id, op_id)
        return f"{workspace_id}/{op_id}/home.tar.zst"

    async def create_empty_archive(self, workspace_id: str, op_id: str) -> str:
        """Create empty archive - delegates to archive job.

        Returns:
            Archive key for the created empty archive.
        """
        return await self.archive(workspace_id, op_id)

    async def volume_exists(self, workspace_id: str) -> bool:
        """Check if volume exists via Agent."""
        resp = await self._request(
            "get", f"/api/v1/volumes/{workspace_id}/exists", on_404="none"
        )
        if resp is None:
            return False
        data = resp.json()
        return data.get("exists", False)

    async def delete_volume(self, workspace_id: str) -> None:
        """Delete volume via Agent."""
        await self._request(
            "delete", f"/api/v1/volumes/{workspace_id}", on_404="false"
        )

    async def delete_archive(self, archive_key: str) -> bool:
        """Delete archive - handled by GC via Agent."""
        logger.warning("delete_archive not implemented - use GC instead")
        return False

    async def run_gc(self, protected: list[dict]) -> dict:
        """Run garbage collection via Agent."""
        resp = await self._request(
            "post", "/api/v1/storage/gc", json={"protected": protected}
        )
        if resp is None:
            return {"deleted_count": 0, "deleted_keys": []}
        return resp.json()

    async def health_check(self) -> bool:
        """Check Agent health."""
        try:
            resp = await self._request("get", "/health", on_404="false")
            return resp is not None and resp.status_code == 200
        except Exception as e:
            logger.warning("Agent health check failed: %s", e)
            return False
