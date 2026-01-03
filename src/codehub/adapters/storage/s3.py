"""S3 storage provider implementation with Docker volumes.

Implements Spec-v2 Storage Job for archive/restore operations:
- Crash-Only, Stateless, Idempotent design
- All operations (sha256, S3 upload) happen inside container
- Python only orchestrates container execution
"""

import logging
from collections import defaultdict

from botocore.exceptions import ClientError

from codehub.app.config import get_settings
from codehub.core.interfaces import ArchiveInfo, StorageProvider, VolumeInfo
from codehub.infra.docker import (
    ContainerAPI,
    ContainerConfig,
    HostConfig,
    VolumeAPI,
    VolumeConfig,
)
from codehub.infra import get_s3_client

logger = logging.getLogger(__name__)

VOLUME_PREFIX = "codehub-ws-"
STORAGE_JOB_IMAGE = "codehub/storage-job:latest"


class S3StorageProvider(StorageProvider):
    """S3-based storage provider using Docker volumes and S3."""

    def __init__(
        self,
        volumes: VolumeAPI | None = None,
        containers: ContainerAPI | None = None,
    ) -> None:
        self._volumes = volumes or VolumeAPI()
        self._containers = containers or ContainerAPI()

    def _volume_name(self, workspace_id: str) -> str:
        return f"{VOLUME_PREFIX}{workspace_id}-home"

    async def list_volumes(self, prefix: str) -> list[VolumeInfo]:
        """List all volumes with given prefix."""
        volumes = await self._volumes.list(filters={"name": [prefix]})

        results = []
        for volume in volumes:
            name = volume.get("Name", "")
            if not name.startswith(prefix):
                continue

            # Extract workspace_id from volume name (ws-{id}-home)
            if name.endswith("-home"):
                workspace_id = name[len(prefix) : -len("-home")]
            else:
                workspace_id = name[len(prefix) :]

            results.append(
                VolumeInfo(
                    workspace_id=workspace_id,
                    exists=True,
                    reason="VolumeExists",
                    message=f"Volume {name} exists",
                )
            )

        return results

    async def list_archives(self, prefix: str) -> list[ArchiveInfo]:
        """List all archives with given prefix.

        Performance: 1 S3 API call instead of N+1 (paginated list all objects).
        """
        settings = get_settings()
        results = []

        # workspace_id -> list of (archive_key, last_modified)
        workspace_archives: dict[str, list[tuple[str, str]]] = defaultdict(list)

        async with get_s3_client() as s3:
            try:
                # 1 query: list all objects with prefix (paginated)
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=settings.storage.bucket_name,
                    Prefix=prefix,
                ):
                    for obj in page.get("Contents", []):
                        key = obj.get("Key", "")
                        # Filter for home.tar.zst files
                        if not key.endswith("/home.tar.zst"):
                            continue

                        # Extract workspace_id from key: ws-{workspace_id}/{op_id}/home.tar.zst
                        parts = key.split("/")
                        if len(parts) < 3:
                            continue

                        ws_prefix_part = parts[0]  # ws-{workspace_id}
                        if not ws_prefix_part.startswith(prefix):
                            continue

                        workspace_id = ws_prefix_part[len(prefix) :]
                        last_modified = obj.get("LastModified", "")
                        workspace_archives[workspace_id].append((key, last_modified))

            except ClientError as e:
                logger.error("Failed to list archives: %s", e)
                return results

        # Find latest archive per workspace (in memory)
        for workspace_id, archives in workspace_archives.items():
            if not archives:
                continue
            # Sort by last_modified descending
            archives.sort(key=lambda x: x[1], reverse=True)
            latest_key = archives[0][0]

            results.append(
                ArchiveInfo(
                    workspace_id=workspace_id,
                    archive_key=latest_key,
                    exists=True,
                    reason="ArchiveUploaded",
                    message=f"Archive: {latest_key}",
                )
            )

        return results

    async def provision(self, workspace_id: str) -> None:
        """Create new volume for workspace."""
        volume_name = self._volume_name(workspace_id)
        config = VolumeConfig(name=volume_name)
        await self._volumes.create(config)

    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive volume to S3 using storage-job container (Spec-v2).

        All operations happen inside the container:
        1. HEAD check for idempotency
        2. tar + zstd compression
        3. sha256 checksum
        4. S3 upload (tar.zst first, .meta last)

        Args:
            workspace_id: Workspace ID
            op_id: Operation ID for idempotency

        Returns:
            archive_key

        Raises:
            RuntimeError: If archive job fails
        """
        settings = get_settings()
        archive_key = f"{VOLUME_PREFIX}{workspace_id}/{op_id}/home.tar.zst"
        volume_name = self._volume_name(workspace_id)
        helper_name = f"codehub-ws-helper-archive-{workspace_id}-{op_id[:8]}"

        try:
            config = ContainerConfig(
                image=STORAGE_JOB_IMAGE,
                name=helper_name,
                cmd=["-c", "/usr/local/bin/archive"],
                env=[
                    f"AWS_ACCESS_KEY_ID={settings.storage.access_key}",
                    f"AWS_SECRET_ACCESS_KEY={settings.storage.secret_key}",
                    f"AWS_ENDPOINT_URL={settings.storage.endpoint_url}",
                    f"S3_BUCKET={settings.storage.bucket_name}",
                    f"S3_KEY={archive_key}",
                ],
                host_config=HostConfig(
                    network_mode="codehub-net",
                    binds=[f"{volume_name}:/data:ro"],
                ),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            exit_code = await self._containers.wait(helper_name)
            if exit_code != 0:
                logs = await self._containers.logs(helper_name)
                raise RuntimeError(f"Archive job failed (exit {exit_code}): {logs!r}")

        finally:
            await self._containers.remove(helper_name)

        logger.info("Archive complete: %s", archive_key)
        return archive_key

    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore volume from S3 archive using storage-job container (Spec-v2).

        All operations happen inside the container:
        1. S3 download (tar.zst + .meta)
        2. sha256 checksum verification
        3. Extract to staging directory
        4. rsync --delete to /data

        Args:
            workspace_id: Workspace ID
            archive_key: S3 key for the archive

        Returns:
            restore_marker (= archive_key)

        Raises:
            RuntimeError: If restore job fails
        """
        settings = get_settings()
        volume_name = self._volume_name(workspace_id)
        helper_name = f"codehub-ws-helper-restore-{workspace_id}"

        # Ensure volume exists
        await self.provision(workspace_id)

        try:
            config = ContainerConfig(
                image=STORAGE_JOB_IMAGE,
                name=helper_name,
                cmd=["-c", "/usr/local/bin/restore"],
                env=[
                    f"AWS_ACCESS_KEY_ID={settings.storage.access_key}",
                    f"AWS_SECRET_ACCESS_KEY={settings.storage.secret_key}",
                    f"AWS_ENDPOINT_URL={settings.storage.endpoint_url}",
                    f"S3_BUCKET={settings.storage.bucket_name}",
                    f"S3_KEY={archive_key}",
                ],
                host_config=HostConfig(
                    network_mode="codehub-net",
                    binds=[f"{volume_name}:/data"],
                ),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            exit_code = await self._containers.wait(helper_name)
            if exit_code != 0:
                logs = await self._containers.logs(helper_name)
                raise RuntimeError(f"Restore job failed (exit {exit_code}): {logs!r}")

        finally:
            await self._containers.remove(helper_name)

        logger.info("Restore complete: %s", archive_key)
        return archive_key

    async def delete_volume(self, workspace_id: str) -> None:
        """Delete volume."""
        volume_name = self._volume_name(workspace_id)
        await self._volumes.remove(volume_name)

    async def volume_exists(self, workspace_id: str) -> bool:
        """Check if volume exists."""
        volume_name = self._volume_name(workspace_id)
        data = await self._volumes.inspect(volume_name)
        return data is not None

    async def create_empty_archive(self, workspace_id: str, op_id: str) -> str:
        """Create empty archive and return archive_key."""
        settings = get_settings()
        archive_key = f"{VOLUME_PREFIX}{workspace_id}/{op_id}/home.tar.zst"

        # Create an empty tar.zst file
        import zstandard as zstd

        empty_tar = b"\x00" * 1024  # Minimal tar header
        cctx = zstd.ZstdCompressor()
        compressed = cctx.compress(empty_tar)

        async with get_s3_client() as s3:
            await s3.put_object(
                Bucket=settings.storage.bucket_name,
                Key=archive_key,
                Body=compressed,
            )

        logger.info("Created empty archive: %s", archive_key)
        return archive_key

    async def close(self) -> None:
        """Close is no-op (Docker client is singleton)."""
        pass
