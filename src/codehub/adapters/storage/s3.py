"""S3 storage provider implementation with Docker volumes.

Implements Spec-v2 Storage Job for archive/restore operations:
- Crash-Only, Stateless, Idempotent design
- All operations (sha256, S3 upload) happen inside container
- Python only orchestrates container execution via JobRunner
"""

import hashlib
import io
import logging
import tarfile
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

from botocore.exceptions import ClientError

from codehub.app.config import get_settings
from codehub.core.interfaces import (
    ArchiveInfo,
    JobRunner,
    StorageProvider,
    VolumeInfo,
    VolumeProvider,
)
from codehub.core.logging_schema import LogEvent
from codehub.infra import get_s3_client

logger = logging.getLogger(__name__)


async def _paginate_objects(bucket: str, prefix: str) -> AsyncIterator[dict[str, Any]]:
    """Paginate S3 list_objects_v2 and yield each object.

    Args:
        bucket: S3 bucket name
        prefix: Object key prefix

    Yields:
        S3 object dicts with Key, LastModified, etc.
    """
    async with get_s3_client() as s3:
        paginator = s3.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj


class S3StorageProvider(StorageProvider):
    """S3-based storage provider using Docker volumes and S3.

    Uses VolumeProvider for volume operations and JobRunner for
    archive/restore job execution. K8s-compatible via DI.
    """

    def __init__(
        self,
        volumes: VolumeProvider | None = None,
        job_runner: JobRunner | None = None,
    ) -> None:
        settings = get_settings()
        self._resource_prefix = settings.runtime.resource_prefix

        # Lazy imports to avoid circular dependencies
        if volumes is None:
            from codehub.adapters.volume.docker import DockerVolumeProvider

            volumes = DockerVolumeProvider()
        if job_runner is None:
            from codehub.adapters.job.docker import DockerJobRunner

            job_runner = DockerJobRunner()

        self._volumes = volumes
        self._job_runner = job_runner

    def _volume_name(self, workspace_id: str) -> str:
        return f"{self._resource_prefix}{workspace_id}-home"

    async def list_volumes(self, prefix: str) -> list[VolumeInfo]:
        """List all volumes with given prefix."""
        volumes = await self._volumes.list(prefix)

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

        try:
            async for obj in _paginate_objects(settings.storage.bucket_name, prefix):
                key = obj.get("Key", "")
                # Filter for home.tar.zst files
                if not key.endswith("/home.tar.zst"):
                    continue

                # Extract workspace_id from key: ws-{workspace_id}/{op_id}/home.tar.zst
                parts = key.split("/")
                if len(parts) < 3:
                    continue

                ws_prefix_part = parts[0]
                if not ws_prefix_part.startswith(prefix):
                    continue

                workspace_id = ws_prefix_part[len(prefix) :]
                last_modified = obj.get("LastModified", "")
                workspace_archives[workspace_id].append((key, last_modified))

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                "Failed to list archives",
                extra={
                    "event": LogEvent.S3_ERROR,
                    "bucket": settings.storage.bucket_name,
                    "prefix": prefix,
                    "error_code": error_code,
                    "error": str(e),
                },
            )
            raise  # Propagate to caller (Observer uses _safe() wrapper)

        # Find latest archive per workspace
        for workspace_id, archives in workspace_archives.items():
            if not archives:
                continue
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

    async def list_all_archive_keys(self, prefix: str) -> set[str]:
        """List all archive keys with given prefix.

        Returns all archive keys for GC (not just latest per workspace).
        """
        settings = get_settings()
        archive_keys: set[str] = set()

        try:
            async for obj in _paginate_objects(settings.storage.bucket_name, prefix):
                key = obj.get("Key", "")
                # Filter for home.tar.zst files (not .meta)
                if key.endswith("/home.tar.zst"):
                    archive_keys.add(key)

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            logger.error(
                "Failed to list all archives",
                extra={
                    "event": LogEvent.S3_ERROR,
                    "bucket": settings.storage.bucket_name,
                    "prefix": prefix,
                    "error_code": error_code,
                    "error": str(e),
                },
            )
            raise  # Propagate to caller (GC handles exception)

        return archive_keys

    async def provision(self, workspace_id: str) -> None:
        """Create new volume for workspace."""
        volume_name = self._volume_name(workspace_id)
        await self._volumes.create(volume_name)

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
        archive_key = f"{self._resource_prefix}{workspace_id}/{op_id}/home.tar.zst"
        archive_url = f"s3://{settings.storage.bucket_name}/{archive_key}"
        volume_name = self._volume_name(workspace_id)

        result = await self._job_runner.run_archive(
            archive_url=archive_url,
            volume_name=volume_name,
            s3_endpoint=settings.storage.internal_endpoint_url,
            s3_access_key=settings.storage.access_key,
            s3_secret_key=settings.storage.secret_key,
        )

        if result.exit_code != 0:
            logger.error(
                "Archive job failed",
                extra={
                    "event": LogEvent.ARCHIVE_FAILED,
                    "ws_id": workspace_id,
                    "op_id": op_id,
                    "archive_url": archive_url,
                    "exit_code": result.exit_code,
                },
            )
            raise RuntimeError(f"Archive job failed (exit {result.exit_code}): {result.logs}")

        logger.info(
            "Archive complete",
            extra={
                "event": LogEvent.ARCHIVE_SUCCESS,
                "ws_id": workspace_id,
                "op_id": op_id,
                "archive_key": archive_key,
            },
        )
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
        archive_url = f"s3://{settings.storage.bucket_name}/{archive_key}"
        volume_name = self._volume_name(workspace_id)

        # Ensure volume exists
        await self.provision(workspace_id)

        result = await self._job_runner.run_restore(
            archive_url=archive_url,
            volume_name=volume_name,
            s3_endpoint=settings.storage.internal_endpoint_url,
            s3_access_key=settings.storage.access_key,
            s3_secret_key=settings.storage.secret_key,
        )

        if result.exit_code != 0:
            logger.error(
                "Restore job failed",
                extra={
                    "event": LogEvent.RESTORE_FAILED,
                    "ws_id": workspace_id,
                    "archive_key": archive_key,
                    "archive_url": archive_url,
                    "exit_code": result.exit_code,
                },
            )
            raise RuntimeError(f"Restore job failed (exit {result.exit_code}): {result.logs}")

        logger.info(
            "Restore complete",
            extra={
                "event": LogEvent.RESTORE_SUCCESS,
                "ws_id": workspace_id,
                "archive_key": archive_key,
            },
        )
        return archive_key

    async def delete_volume(self, workspace_id: str) -> None:
        """Delete volume."""
        volume_name = self._volume_name(workspace_id)
        await self._volumes.remove(volume_name)

    async def volume_exists(self, workspace_id: str) -> bool:
        """Check if volume exists."""
        volume_name = self._volume_name(workspace_id)
        return await self._volumes.exists(volume_name)

    async def create_empty_archive(self, workspace_id: str, op_id: str) -> str:
        """Create empty archive and return archive_key.

        Creates a valid empty tar.zst archive with .meta file for restore compatibility.
        Follows the same format as archive.sh (storage-job container).
        """
        import zstandard as zstd

        settings = get_settings()
        archive_key = f"{self._resource_prefix}{workspace_id}/{op_id}/home.tar.zst"
        meta_key = f"{archive_key}.meta"

        # 1. Create valid empty tar archive
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            pass  # Empty tar (end-of-archive markers only)
        tar_data = tar_buffer.getvalue()

        # 2. Compress with zstd
        cctx = zstd.ZstdCompressor()
        compressed = cctx.compress(tar_data)

        # 3. Compute sha256 checksum (restore.sh format: "sha256:<hash>")
        checksum = f"sha256:{hashlib.sha256(compressed).hexdigest()}"

        # 4. Upload both files (order: tar.zst first, .meta last as commit marker)
        async with get_s3_client() as s3:
            await s3.put_object(
                Bucket=settings.storage.bucket_name,
                Key=archive_key,
                Body=compressed,
            )
            await s3.put_object(
                Bucket=settings.storage.bucket_name,
                Key=meta_key,
                Body=checksum.encode(),
            )

        logger.info("Created empty archive: %s", archive_key)
        return archive_key

    async def delete_archive(self, archive_key: str) -> bool:
        """Delete archive and meta file from S3.

        Args:
            archive_key: Full archive path (e.g., "ws-xxx/op-id/home.tar.zst")

        Returns:
            True if deleted successfully
        """
        settings = get_settings()

        try:
            async with get_s3_client() as s3:
                await s3.delete_objects(
                    Bucket=settings.storage.bucket_name,
                    Delete={
                        "Objects": [
                            {"Key": archive_key},
                            {"Key": f"{archive_key}.meta"},
                        ],
                    },
                )
            logger.debug("Deleted archive: %s", archive_key)
            return True
        except ClientError as e:
            logger.warning("Failed to delete archive %s: %s", archive_key, e)
            return False

    async def close(self) -> None:
        """Close is no-op (Docker client is singleton)."""
        pass
