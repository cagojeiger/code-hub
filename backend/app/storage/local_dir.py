"""Local directory storage provider.

Deterministic path: workspace_base_dir + home_store_key
"""

import os
import shutil
from typing import Literal

from app.storage.interface import ProvisionResult, StorageProvider, StorageStatus

CODER_UID = 1000
CODER_GID = 1000


class LocalDirStorageProvider(StorageProvider):

    def __init__(self, workspace_base_dir: str) -> None:
        self._workspace_base_dir = workspace_base_dir.rstrip("/")

    @property
    def backend_name(self) -> Literal["local-dir"]:
        return "local-dir"

    def _compute_path(self, home_store_key: str) -> str:
        return f"{self._workspace_base_dir}/{home_store_key}"

    async def provision(
        self,
        home_store_key: str,
        existing_ctx: str | None = None,
    ) -> ProvisionResult:
        if existing_ctx:
            await self.deprovision(existing_ctx)

        home_mount = self._compute_path(home_store_key)
        os.makedirs(home_mount, exist_ok=True)

        try:
            os.chown(home_mount, CODER_UID, CODER_GID)
        except PermissionError:
            pass

        return ProvisionResult(home_mount=home_mount, home_ctx=home_mount)

    async def deprovision(self, home_ctx: str | None) -> None:
        """no-op for local-dir (bind mount auto-releases)"""
        pass

    async def purge(self, home_store_key: str) -> None:
        path = self._compute_path(home_store_key)
        if os.path.exists(path):
            shutil.rmtree(path)

    async def get_status(self, home_store_key: str) -> StorageStatus:
        path = self._compute_path(home_store_key)
        if os.path.isdir(path):
            return StorageStatus(provisioned=True, home_ctx=path, home_mount=path)
        return StorageStatus(provisioned=False)
