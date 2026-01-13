"""Agent HTTP client for Control Plane.

This client communicates with the Agent service to manage workspaces.
It implements the WorkspaceRuntime interface for workspace lifecycle management.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from codehub.core.interfaces.runtime import (
    ArchiveStatus,
    ContainerStatus,
    GCResult,
    UpstreamInfo,
    VolumeStatus,
    WorkspaceRuntime,
    WorkspaceState,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """Agent connection configuration."""

    endpoint: str
    api_key: str = ""
    timeout: float = 30.0
    job_timeout: float = 600.0


class AgentClient(WorkspaceRuntime):
    """HTTP client for Agent API.

    Implements WorkspaceRuntime interface for workspace lifecycle management.
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
        """Make HTTP request with common error handling."""
        client = await self._get_client()
        if timeout:
            kwargs["timeout"] = timeout

        resp = await getattr(client, method)(path, **kwargs)

        if resp.status_code == 404:
            if on_404 == "raise":
                resp.raise_for_status()
            elif on_404 == "none":
                return None

        if resp.status_code != 404:
            resp.raise_for_status()

        return resp

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # =========================================================================
    # WorkspaceRuntime interface (NEW)
    # =========================================================================

    async def observe(self) -> list[WorkspaceState]:
        """Observe all workspaces and return their current state.

        Calls the new unified /api/v1/workspaces endpoint.
        """
        resp = await self._request("get", "/api/v1/workspaces")
        if resp is None:
            return []

        data = resp.json()
        workspaces = []
        for ws in data.get("workspaces", []):
            container = None
            if ws.get("container"):
                container = ContainerStatus(
                    running=ws["container"].get("running", False),
                    healthy=ws["container"].get("healthy", False),
                )

            volume = None
            if ws.get("volume"):
                volume = VolumeStatus(exists=ws["volume"].get("exists", False))

            archive = None
            if ws.get("archive"):
                archive = ArchiveStatus(
                    exists=ws["archive"].get("exists", False),
                    archive_key=ws["archive"].get("archive_key"),
                )

            workspaces.append(
                WorkspaceState(
                    workspace_id=ws["workspace_id"],
                    container=container,
                    volume=volume,
                    archive=archive,
                )
            )

        return workspaces

    async def provision(self, workspace_id: str) -> None:
        """Provision a new workspace (create volume)."""
        await self._request("post", f"/api/v1/workspaces/{workspace_id}/provision")
        logger.info("Provisioned workspace: %s", workspace_id)

    async def start(self, workspace_id: str, image: str) -> None:
        """Start workspace container."""
        await self._request(
            "post",
            f"/api/v1/workspaces/{workspace_id}/start",
            json={"image": image},
        )
        logger.info("Started workspace: %s", workspace_id)

    async def stop(self, workspace_id: str) -> None:
        """Stop workspace container."""
        await self._request("post", f"/api/v1/workspaces/{workspace_id}/stop")
        logger.info("Stopped workspace: %s", workspace_id)

    async def delete(self, workspace_id: str) -> None:
        """Delete workspace completely (container + volume)."""
        await self._request("delete", f"/api/v1/workspaces/{workspace_id}")
        logger.info("Deleted workspace: %s", workspace_id)

    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive workspace to S3."""
        resp = await self._request(
            "post",
            f"/api/v1/workspaces/{workspace_id}/archive",
            json={"op_id": op_id},
            timeout=self._config.job_timeout,
        )
        if resp is None:
            raise RuntimeError(f"Archive failed for {workspace_id}")
        data = resp.json()
        logger.info("Archived workspace: %s", workspace_id)
        return data.get("archive_key", f"{workspace_id}/{op_id}/home.tar.zst")

    async def restore(self, workspace_id: str, archive_key: str) -> None:
        """Restore workspace from S3 archive."""
        await self._request(
            "post",
            f"/api/v1/workspaces/{workspace_id}/restore",
            json={"archive_key": archive_key},
            timeout=self._config.job_timeout,
        )
        logger.info("Restored workspace: %s from %s", workspace_id, archive_key)

    async def delete_archive(self, archive_key: str) -> bool:
        """Delete an archive from S3."""
        resp = await self._request(
            "delete",
            "/api/v1/workspaces/archives",
            params={"archive_key": archive_key},
            on_404="false",
        )
        if resp is None:
            return False
        data = resp.json()
        return data.get("deleted", False)

    async def get_upstream(self, workspace_id: str) -> UpstreamInfo | None:
        """Get upstream address for proxy routing."""
        resp = await self._request(
            "get",
            f"/api/v1/workspaces/{workspace_id}/upstream",
            on_404="none",
        )
        if resp is None:
            return None
        data = resp.json()
        return UpstreamInfo(
            hostname=data["hostname"],
            port=data["port"],
        )

    async def run_gc(self, protected: list[tuple[str, str]]) -> GCResult:
        """Run garbage collection on archives."""
        protected_items = [
            {"workspace_id": ws_id, "op_id": op_id} for ws_id, op_id in protected
        ]
        resp = await self._request(
            "post",
            "/api/v1/workspaces/gc",
            json={"protected": protected_items},
        )
        if resp is None:
            return GCResult(deleted_count=0, deleted_keys=[])
        data = resp.json()
        return GCResult(
            deleted_count=data.get("deleted_count", 0),
            deleted_keys=data.get("deleted_keys", []),
        )

    # =========================================================================
    # Health Check
    # =========================================================================

    async def health_check(self) -> bool:
        """Check Agent health."""
        try:
            resp = await self._request("get", "/health", on_404="false")
            return resp is not None and resp.status_code == 200
        except Exception as e:
            logger.warning("Agent health check failed: %s", e)
            return False
