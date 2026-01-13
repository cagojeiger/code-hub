"""Agent HTTP client for Control Plane.

This client communicates with the Agent service to manage
Docker containers, volumes, and jobs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
        client = await self._get_client()
        resp = await client.get("/instances")
        resp.raise_for_status()

        data = resp.json()
        results = []
        for item in data.get("instances", []):
            results.append(
                ContainerInfo(
                    workspace_id=item["workspace_id"],
                    running=item["running"],
                    reason=item["reason"],
                    message=item["message"],
                )
            )
        return results

    async def start(self, workspace_id: str, image_ref: str) -> None:
        """Start container via Agent."""
        client = await self._get_client()
        resp = await client.post(
            f"/instances/{workspace_id}/start",
            json={"image_ref": image_ref},
        )
        resp.raise_for_status()
        logger.info("Started instance via Agent: %s", workspace_id)

    async def delete(self, workspace_id: str) -> None:
        """Delete container via Agent."""
        client = await self._get_client()
        resp = await client.delete(f"/instances/{workspace_id}")
        resp.raise_for_status()
        logger.info("Deleted instance via Agent: %s", workspace_id)

    async def is_running(self, workspace_id: str) -> bool:
        """Check if container is running via Agent."""
        client = await self._get_client()
        resp = await client.get(f"/instances/{workspace_id}/status")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
        return data.get("running", False) and data.get("healthy", False)

    async def resolve_upstream(self, workspace_id: str) -> UpstreamInfo | None:
        """Get upstream address from Agent."""
        client = await self._get_client()
        resp = await client.get(f"/instances/{workspace_id}/upstream")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return UpstreamInfo(
            hostname=data["hostname"],
            port=data["port"],
        )

    # =========================================================================
    # VolumeProvider interface
    # =========================================================================

    async def create(self, name: str) -> None:
        """Create volume via Agent.

        Note: Agent uses workspace_id, not volume name.
        Volume name format: {prefix}{workspace_id}-home
        """
        # Extract workspace_id from volume name
        # Assuming name format: codehub-{workspace_id}-home
        if name.endswith("-home"):
            workspace_id = name[:-5]  # Remove "-home"
            if "-" in workspace_id:
                # Remove prefix (e.g., "codehub-")
                workspace_id = workspace_id.split("-", 1)[-1]
        else:
            workspace_id = name

        client = await self._get_client()
        resp = await client.post(f"/volumes/{workspace_id}")
        resp.raise_for_status()
        logger.info("Created volume via Agent: %s", workspace_id)

    async def remove(self, name: str) -> None:
        """Remove volume via Agent."""
        # Extract workspace_id from volume name
        if name.endswith("-home"):
            workspace_id = name[:-5]
            if "-" in workspace_id:
                workspace_id = workspace_id.split("-", 1)[-1]
        else:
            workspace_id = name

        client = await self._get_client()
        resp = await client.delete(f"/volumes/{workspace_id}")
        resp.raise_for_status()
        logger.info("Removed volume via Agent: %s", workspace_id)

    async def exists(self, name: str) -> bool:
        """Check if volume exists via Agent."""
        # Extract workspace_id from volume name
        if name.endswith("-home"):
            workspace_id = name[:-5]
            if "-" in workspace_id:
                workspace_id = workspace_id.split("-", 1)[-1]
        else:
            workspace_id = name

        client = await self._get_client()
        resp = await client.get(f"/volumes/{workspace_id}/exists")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
        return data.get("exists", False)

    async def list(self, prefix: str) -> list[dict]:
        """List volumes via Agent."""
        client = await self._get_client()
        resp = await client.get("/volumes")
        resp.raise_for_status()
        data = resp.json()
        return data.get("volumes", [])

    # =========================================================================
    # JobRunner interface
    # =========================================================================

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
        We extract workspace_id and op_id from archive_url.
        """
        # Parse archive_url: s3://bucket/cluster_id/workspace_id/op_id/home.tar.zst
        parts = archive_url.replace("s3://", "").split("/")
        if len(parts) >= 4:
            workspace_id = parts[2]
            op_id = parts[3]
        else:
            raise ValueError(f"Invalid archive_url format: {archive_url}")

        client = await self._get_client()
        resp = await client.post(
            "/jobs/archive",
            json={"workspace_id": workspace_id, "op_id": op_id},
            timeout=self._config.job_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return JobResult(exit_code=data["exit_code"], logs=data["logs"])

    async def run_restore(
        self,
        archive_url: str,
        volume_name: str,
        s3_endpoint: str,
        s3_access_key: str,
        s3_secret_key: str,
    ) -> JobResult:
        """Run restore job via Agent."""
        # Parse archive_url
        parts = archive_url.replace("s3://", "").split("/")
        if len(parts) >= 4:
            workspace_id = parts[2]
            op_id = parts[3]
        else:
            raise ValueError(f"Invalid archive_url format: {archive_url}")

        client = await self._get_client()
        resp = await client.post(
            "/jobs/restore",
            json={"workspace_id": workspace_id, "op_id": op_id},
            timeout=self._config.job_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return JobResult(exit_code=data["exit_code"], logs=data["logs"])

    # =========================================================================
    # StorageProvider interface
    # =========================================================================

    async def list_volumes(self, prefix: str) -> list[VolumeInfo]:
        """List volumes from Agent."""
        client = await self._get_client()
        resp = await client.get("/volumes")
        resp.raise_for_status()

        data = resp.json()
        results = []
        for item in data.get("volumes", []):
            results.append(
                VolumeInfo(
                    workspace_id=item["workspace_id"],
                    exists=item["exists"],
                    reason="VolumeReady" if item["exists"] else "VolumeNotFound",
                    message="",
                )
            )
        return results

    async def list_archives(self, prefix: str) -> list[ArchiveInfo]:
        """List archives - not implemented via Agent yet."""
        # This would require S3 listing capability in Agent
        # For now, return empty list
        logger.warning("list_archives not fully implemented via Agent")
        return []

    async def list_all_archive_keys(self, prefix: str) -> set[str]:
        """List all archive keys - not implemented via Agent yet."""
        logger.warning("list_all_archive_keys not fully implemented via Agent")
        return set()

    async def provision(self, workspace_id: str) -> None:
        """Provision volume via Agent."""
        client = await self._get_client()
        resp = await client.post(f"/volumes/{workspace_id}")
        resp.raise_for_status()

    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore workspace from archive via Agent.

        Returns:
            restore_marker (= archive_key) for completion check
        """
        # Extract op_id from archive_key
        # Format: cluster_id/workspace_id/op_id/home.tar.zst
        parts = archive_key.split("/")
        if len(parts) >= 3:
            op_id = parts[2]
        else:
            raise ValueError(f"Invalid archive_key format: {archive_key}")

        client = await self._get_client()
        resp = await client.post(
            "/jobs/restore",
            json={"workspace_id": workspace_id, "op_id": op_id},
            timeout=self._config.job_timeout,
        )
        resp.raise_for_status()
        return archive_key  # Return restore_marker

    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive workspace via Agent.

        Returns:
            Archive key for the created archive
        """
        client = await self._get_client()
        resp = await client.post(
            "/jobs/archive",
            json={"workspace_id": workspace_id, "op_id": op_id},
            timeout=self._config.job_timeout,
        )
        resp.raise_for_status()
        # Return archive key (Agent constructs this internally)
        # Format: cluster_id/workspace_id/op_id/home.tar.zst
        return f"{workspace_id}/{op_id}/home.tar.zst"

    async def create_empty_archive(self, workspace_id: str, op_id: str) -> str:
        """Create empty archive - delegates to archive job.

        Returns:
            Archive key for the created empty archive
        """
        # For empty archive, we still run the archive job
        # The job container handles empty directories
        return await self.archive(workspace_id, op_id)

    async def volume_exists(self, workspace_id: str) -> bool:
        """Check if volume exists via Agent."""
        client = await self._get_client()
        resp = await client.get(f"/volumes/{workspace_id}/exists")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        data = resp.json()
        return data.get("exists", False)

    async def delete_volume(self, workspace_id: str) -> None:
        """Delete volume via Agent."""
        client = await self._get_client()
        resp = await client.delete(f"/volumes/{workspace_id}")
        if resp.status_code != 404:
            resp.raise_for_status()

    async def delete_archive(self, archive_key: str) -> bool:
        """Delete archive - handled by GC via Agent."""
        # Individual archive deletion is handled by GC
        logger.warning("delete_archive not implemented - use GC instead")
        return False

    async def run_gc(self, protected: list[dict]) -> dict:
        """Run garbage collection via Agent."""
        client = await self._get_client()
        resp = await client.post(
            "/storage/gc",
            json={"protected": protected},
        )
        resp.raise_for_status()
        return resp.json()

    async def health_check(self) -> bool:
        """Check Agent health."""
        try:
            client = await self._get_client()
            resp = await client.get("/health")
            return resp.status_code == 200
        except Exception as e:
            logger.warning("Agent health check failed: %s", e)
            return False
