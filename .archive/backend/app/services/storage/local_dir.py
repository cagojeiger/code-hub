"""Local directory storage provider.

Two paths needed:
- control_plane_base_dir: for file operations (container path)
- workspace_base_dir: for Docker bind mount (host path)
"""

import contextlib
import os
import shutil
from typing import Literal

from app.services.storage.interface import (
    ProvisionResult,
    StorageProvider,
    StorageStatus,
)

CODER_UID = 1000
CODER_GID = 1000


class LocalDirStorageProvider(StorageProvider):

    def __init__(
        self,
        control_plane_base_dir: str,
        workspace_base_dir: str,
    ) -> None:
        self._control_plane_base_dir = control_plane_base_dir.rstrip("/")
        self._workspace_base_dir = workspace_base_dir.rstrip("/")

    @property
    def backend_name(self) -> Literal["local-dir"]:
        return "local-dir"

    def _internal_path(self, home_store_key: str) -> str:
        """Path for file operations (container path)."""
        return f"{self._control_plane_base_dir}/{home_store_key}"

    def _external_path(self, home_store_key: str) -> str:
        """Path for Docker bind mount (host path)."""
        return f"{self._workspace_base_dir}/{home_store_key}"

    async def provision(
        self,
        home_store_key: str,
        existing_ctx: str | None = None,
    ) -> ProvisionResult:
        if existing_ctx:
            await self.deprovision(existing_ctx)

        # File operations use container path
        internal_path = self._internal_path(home_store_key)
        os.makedirs(internal_path, exist_ok=True)

        with contextlib.suppress(PermissionError):
            os.chown(internal_path, CODER_UID, CODER_GID)

        # Return host path for Docker bind mount
        home_mount = self._external_path(home_store_key)
        return ProvisionResult(home_mount=home_mount, home_ctx=home_mount)

    async def deprovision(self, home_ctx: str | None) -> None:
        """no-op for local-dir (bind mount auto-releases)"""
        pass

    async def purge(self, home_store_key: str) -> None:
        path = self._internal_path(home_store_key)
        if os.path.exists(path):
            shutil.rmtree(path)

    async def get_status(self, home_store_key: str) -> StorageStatus:
        internal_path = self._internal_path(home_store_key)
        if os.path.isdir(internal_path):
            external_path = self._external_path(home_store_key)
            return StorageStatus(
                provisioned=True, home_ctx=external_path, home_mount=external_path
            )
        return StorageStatus(provisioned=False)
