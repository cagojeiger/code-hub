"""S3 storage provider implementation with Docker volumes.

Implements Spec-v2 Storage Job for archive/restore operations:
- Crash-Only, Stateless, Idempotent design
- sha256 checksum with .meta file
- Helper container pattern for volume operations
"""

import io
import logging
import tarfile
from collections import defaultdict

from botocore.exceptions import ClientError

from codehub.adapters.storage.job import (
    HELPER_IMAGE,
    compute_sha256,
    create_meta,
    parse_meta,
)
from codehub.app.config import get_settings
from codehub.core.interfaces import ArchiveInfo, StorageProvider, VolumeInfo
from codehub.infra.docker import (
    ContainerAPI,
    ContainerConfig,
    HostConfig,
    VolumeAPI,
    VolumeConfig,
)
from codehub.infra.storage import get_s3_client

logger = logging.getLogger(__name__)

VOLUME_PREFIX = "ws-"


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

    async def _head_object(self, s3, key: str) -> bool:
        """Check if S3 object exists using HEAD request."""
        settings = get_settings()
        try:
            await s3.head_object(Bucket=settings.storage.bucket_name, Key=key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise

    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore volume from S3 archive (Spec-v2).

        Flow:
        1. Download tar.zst + .meta from S3
        2. Verify sha256 checksum
        3. Create volume if not exists
        4. Extract to staging directory via helper container
        5. rsync --delete to /data

        Args:
            workspace_id: Workspace ID
            archive_key: S3 key for the archive

        Returns:
            restore_marker (= archive_key)

        Raises:
            ValueError: If checksum verification fails
            RuntimeError: If restore job fails
        """
        settings = get_settings()
        meta_key = f"{archive_key}.meta"
        volume_name = self._volume_name(workspace_id)
        helper_name = f"ws-helper-restore-{workspace_id}"

        # 1. Download archive + meta from S3
        async with get_s3_client() as s3:
            tar_resp = await s3.get_object(
                Bucket=settings.storage.bucket_name,
                Key=archive_key,
            )
            compressed_data = await tar_resp["Body"].read()

            meta_resp = await s3.get_object(
                Bucket=settings.storage.bucket_name,
                Key=meta_key,
            )
            meta_content = await meta_resp["Body"].read()

        # 2. Verify checksum
        expected_hash = parse_meta(meta_content)
        actual_hash = compute_sha256(compressed_data)
        if expected_hash != actual_hash:
            raise ValueError(f"Checksum mismatch: {expected_hash} != {actual_hash}")

        logger.debug("Checksum verified for %s", archive_key)

        # 3. Ensure volume exists
        await self.provision(workspace_id)

        # Use a temp volume for input data (avoids docker-proxy PUT restrictions)
        input_volume = f"ws-restore-input-{workspace_id}"

        try:
            # 4. Create temp input volume
            await self._volumes.create(VolumeConfig(name=input_volume))

            # 5. Write archive data to input volume using a writer container
            writer_name = f"ws-helper-writer-{workspace_id}"
            writer_config = ContainerConfig(
                image=HELPER_IMAGE,
                name=writer_name,
                cmd=["sh", "-c", "sleep 30"],  # Keep alive for put_archive
                host_config=HostConfig(binds=[f"{input_volume}:/input"]),
            )
            await self._containers.create(writer_config)
            await self._containers.start(writer_name)

            # Put archive data into running writer container
            tar_buffer = io.BytesIO()
            with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
                tarinfo = tarfile.TarInfo(name="home.tar.zst")
                tarinfo.size = len(compressed_data)
                tar.addfile(tarinfo, io.BytesIO(compressed_data))
            tar_buffer.seek(0)

            await self._containers.put_archive(writer_name, "/input", tar_buffer.read())
            await self._containers.stop(writer_name, timeout=1)
            await self._containers.remove(writer_name)

            # 6. Create restore container with both volumes
            config = ContainerConfig(
                image=HELPER_IMAGE,
                name=helper_name,
                cmd=[
                    "sh",
                    "-c",
                    "apk add --no-cache zstd rsync && "
                    "mkdir -p /tmp/staging && "
                    "zstd -d < /input/home.tar.zst | tar -xf - -C /tmp/staging && "
                    "rsync -a --delete /tmp/staging/ /data/",
                ],
                host_config=HostConfig(
                    binds=[f"{volume_name}:/data", f"{input_volume}:/input:ro"]
                ),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            # 7. Wait for completion
            exit_code = await self._containers.wait(helper_name)

            if exit_code != 0:
                logs = await self._containers.logs(helper_name)
                raise RuntimeError(f"Restore job failed (exit {exit_code}): {logs!r}")

        finally:
            await self._containers.remove(helper_name)
            await self._volumes.remove(input_volume)

        logger.info("Restore complete: %s", archive_key)
        return archive_key  # restore_marker

    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive volume to S3 using helper container (Spec-v2).

        Flow:
        1. HEAD check: skip if tar.zst + meta both exist (idempotent)
        2. Create helper container with volume mounted
        3. Compress volume to tar.zst
        4. Compute sha256 checksum
        5. Upload tar.zst → upload .meta (order fixed)

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
        meta_key = f"{archive_key}.meta"
        volume_name = self._volume_name(workspace_id)
        helper_name = f"ws-helper-archive-{workspace_id}-{op_id[:8]}"

        # 1. HEAD check - idempotent
        async with get_s3_client() as s3:
            tar_exists = await self._head_object(s3, archive_key)
            meta_exists = await self._head_object(s3, meta_key)
            if tar_exists and meta_exists:
                logger.info("Archive already complete: %s", archive_key)
                return archive_key

        try:
            # 2. Create helper container with volume mounted
            config = ContainerConfig(
                image=HELPER_IMAGE,
                name=helper_name,
                cmd=[
                    "sh",
                    "-c",
                    "apk add --no-cache zstd && "
                    "mkdir -p /output && "
                    "tar --exclude='*.sock' --exclude='*.socket' "
                    "-cf - -C /data . | zstd -o /output/home.tar.zst",
                ],
                host_config=HostConfig(binds=[f"{volume_name}:/data:ro"]),
            )
            await self._containers.create(config)
            await self._containers.start(helper_name)

            # 3. Wait for completion
            exit_code = await self._containers.wait(helper_name)
            if exit_code != 0:
                logs = await self._containers.logs(helper_name)
                raise RuntimeError(f"Archive job failed (exit {exit_code}): {logs!r}")

            # 4. Get output file from container
            tar_data = await self._containers.get_archive(helper_name, "/output/home.tar.zst")

            # Docker returns a tar containing the file, extract it
            tar_buffer = io.BytesIO(tar_data)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                member = tar.getmembers()[0]
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise RuntimeError("Failed to extract archive from container")
                compressed_data = extracted.read()

        finally:
            await self._containers.remove(helper_name)

        # 5. Compute sha256 and upload
        sha256_hex = compute_sha256(compressed_data)

        async with get_s3_client() as s3:
            # Upload tar.zst first
            await s3.put_object(
                Bucket=settings.storage.bucket_name,
                Key=archive_key,
                Body=compressed_data,
            )
            # Upload .meta last (commit marker)
            await s3.put_object(
                Bucket=settings.storage.bucket_name,
                Key=meta_key,
                Body=create_meta(sha256_hex),
            )

        logger.info("Archive complete: %s (sha256=%s)", archive_key, sha256_hex[:16])
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
        # zstd compressed empty tar: header only
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
