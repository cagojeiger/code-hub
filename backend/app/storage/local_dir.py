"""Local directory storage provider for code-hub.

Implements StorageProvider interface using host directory bind mounts.
MVP implementation with deterministic path calculation for idempotency.
"""

import os
import shutil
from typing import Literal

from app.storage.interface import ProvisionResult, StorageProvider, StorageStatus

# code-server default uid/gid
CODER_UID = 1000
CODER_GID = 1000


class LocalDirStorageProvider(StorageProvider):
    """Storage provider using local directories with bind mounts."""

    def __init__(self, workspace_base_dir: str) -> None:
        """Initialize with workspace base directory (host path).

        Args:
            workspace_base_dir: Host path for Docker bind mount
        """
        self._workspace_base_dir = workspace_base_dir.rstrip("/")

    @property
    def backend_name(self) -> Literal["local-dir"]:
        """Return the backend identifier."""
        return "local-dir"

    def _compute_path(self, home_store_key: str) -> str:
        """Compute deterministic path for home_store_key."""
        return f"{self._workspace_base_dir}/{home_store_key}"

    async def provision(
        self,
        home_store_key: str,
        existing_ctx: str | None = None,
    ) -> ProvisionResult:
        """Prepare home_mount for container use.

        For local-dir backend:
        - Computes deterministic path: workspace_base_dir + home_store_key
        - Creates directory if not exists
        - Sets ownership to 1000:1000 (coder user)

        Args:
            home_store_key: Logical key (e.g., users/{user_id}/workspaces/{ws_id}/home)
            existing_ctx: Previous context (ignored for local-dir, path is deterministic)

        Returns:
            ProvisionResult with home_mount and home_ctx
        """
        if existing_ctx:
            await self.deprovision(existing_ctx)

        home_mount = self._compute_path(home_store_key)

        os.makedirs(home_mount, exist_ok=True)

        try:
            os.chown(home_mount, CODER_UID, CODER_GID)
        except PermissionError:
            pass

        return ProvisionResult(
            home_mount=home_mount,
            home_ctx=home_mount,
        )

    async def deprovision(self, home_ctx: str | None) -> None:
        """Release home_ctx resources.

        For local-dir backend: no-op.
        Bind mount is automatically released when container stops.

        Args:
            home_ctx: Context to release (ignored)
        """
        pass

    async def purge(self, home_store_key: str) -> None:
        """Completely delete all data for home_store_key.

        Args:
            home_store_key: Logical key to purge
        """
        path = self._compute_path(home_store_key)
        if os.path.exists(path):
            shutil.rmtree(path)

    async def get_status(self, home_store_key: str) -> StorageStatus:
        """Query current provisioning state.

        Args:
            home_store_key: Logical key to check

        Returns:
            StorageStatus with provisioned state
        """
        path = self._compute_path(home_store_key)
        exists = os.path.isdir(path)

        if exists:
            return StorageStatus(
                provisioned=True,
                home_ctx=path,
                home_mount=path,
            )
        return StorageStatus(provisioned=False)
