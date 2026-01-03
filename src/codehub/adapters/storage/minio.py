"""MinIO storage provider implementation."""

import logging
import os

import httpx
from botocore.exceptions import ClientError

from codehub.app.config import get_settings
from codehub.core.interfaces import ArchiveInfo, StorageProvider, VolumeInfo
from codehub.infra.storage import get_s3_client

logger = logging.getLogger(__name__)

# Docker API for volume management
DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
VOLUME_PREFIX = "ws-"


class MinIOStorageProvider(StorageProvider):
    """MinIO-based storage provider using Docker volumes and S3."""

    def __init__(self) -> None:
        self._docker_client: httpx.AsyncClient | None = None

    async def _get_docker_client(self) -> httpx.AsyncClient:
        if self._docker_client is None:
            docker_host = DOCKER_HOST
            if docker_host.startswith("unix://"):
                # Unix socket
                socket_path = docker_host.replace("unix://", "")
                transport = httpx.AsyncHTTPTransport(uds=socket_path)
                self._docker_client = httpx.AsyncClient(
                    transport=transport, base_url="http://localhost", timeout=30.0
                )
            else:
                # TCP (docker-proxy)
                if docker_host.startswith("tcp://"):
                    docker_host = docker_host.replace("tcp://", "http://")
                self._docker_client = httpx.AsyncClient(
                    base_url=docker_host, timeout=30.0
                )
        return self._docker_client

    def _volume_name(self, workspace_id: str) -> str:
        return f"{VOLUME_PREFIX}{workspace_id}-home"

    async def list_volumes(self, prefix: str) -> list[VolumeInfo]:
        """List all volumes with given prefix."""
        client = await self._get_docker_client()
        resp = await client.get(
            "/volumes",
            params={"filters": f'{{"name": ["{prefix}"]}}'},
        )
        resp.raise_for_status()

        results = []
        data = resp.json()
        for volume in data.get("Volumes", []):
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
        """List all archives with given prefix."""
        settings = get_settings()
        results = []

        async with get_s3_client() as s3:
            try:
                paginator = s3.get_paginator("list_objects_v2")
                async for page in paginator.paginate(
                    Bucket=settings.storage.bucket_name,
                    Prefix=prefix,
                    Delimiter="/",
                ):
                    for prefix_obj in page.get("CommonPrefixes", []):
                        # prefix format: ws-{workspace_id}/
                        ws_prefix = prefix_obj.get("Prefix", "").rstrip("/")
                        if not ws_prefix.startswith(prefix):
                            continue

                        workspace_id = ws_prefix[len(prefix) :]

                        # Find latest archive in this workspace folder
                        archive_key = await self._find_latest_archive(
                            s3, settings.storage.bucket_name, ws_prefix
                        )

                        results.append(
                            ArchiveInfo(
                                workspace_id=workspace_id,
                                archive_key=archive_key,
                                exists=archive_key is not None,
                                reason="ArchiveUploaded"
                                if archive_key
                                else "NoArchive",
                                message=f"Archive: {archive_key}"
                                if archive_key
                                else "No archive found",
                            )
                        )
            except ClientError as e:
                logger.error("Failed to list archives: %s", e)

        return results

    async def _find_latest_archive(
        self, s3, bucket: str, prefix: str
    ) -> str | None:
        """Find the latest archive in a workspace folder."""
        try:
            resp = await s3.list_objects_v2(
                Bucket=bucket,
                Prefix=f"{prefix}/",
            )
            contents = resp.get("Contents", [])
            # Filter for home.tar.zst files and get the latest
            archives = [
                obj
                for obj in contents
                if obj.get("Key", "").endswith("/home.tar.zst")
            ]
            if not archives:
                return None
            # Sort by LastModified descending
            archives.sort(key=lambda x: x.get("LastModified", ""), reverse=True)
            return archives[0].get("Key")
        except ClientError:
            return None

    async def provision(self, workspace_id: str) -> None:
        """Create new volume for workspace."""
        client = await self._get_docker_client()
        volume_name = self._volume_name(workspace_id)

        resp = await client.post(
            "/volumes/create",
            json={"Name": volume_name},
        )
        if resp.status_code == 409:
            # Volume already exists
            logger.debug("Volume already exists: %s", volume_name)
            return
        resp.raise_for_status()
        logger.info("Created volume: %s", volume_name)

    async def restore(self, workspace_id: str, archive_key: str) -> str:
        """Restore volume from archive."""
        # 1. Create volume if not exists
        await self.provision(workspace_id)

        # 2. Download archive from S3 and extract to volume
        # This requires a helper container to mount the volume and extract
        # For now, return the archive_key as restore_marker
        settings = get_settings()

        # TODO: Implement actual restore using a helper container
        # For MVP, we'll use a simple approach:
        # - Download to temp file
        # - Run tar extraction in a container

        logger.info(
            "Restore requested: workspace=%s, archive=%s", workspace_id, archive_key
        )
        return archive_key

    async def archive(self, workspace_id: str, op_id: str) -> str:
        """Archive volume and return archive_key."""
        settings = get_settings()
        volume_name = self._volume_name(workspace_id)
        archive_key = f"{VOLUME_PREFIX}{workspace_id}/{op_id}/home.tar.zst"

        # TODO: Implement actual archive using a helper container
        # For MVP:
        # - Run tar in a container with volume mounted
        # - Upload result to S3

        logger.info(
            "Archive requested: workspace=%s, op_id=%s, key=%s",
            workspace_id,
            op_id,
            archive_key,
        )
        return archive_key

    async def delete_volume(self, workspace_id: str) -> None:
        """Delete volume."""
        client = await self._get_docker_client()
        volume_name = self._volume_name(workspace_id)

        resp = await client.delete(f"/volumes/{volume_name}")
        if resp.status_code == 404:
            logger.debug("Volume not found: %s", volume_name)
            return
        if resp.status_code == 409:
            logger.warning("Volume in use, cannot delete: %s", volume_name)
            return
        resp.raise_for_status()
        logger.info("Deleted volume: %s", volume_name)

    async def volume_exists(self, workspace_id: str) -> bool:
        """Check if volume exists."""
        client = await self._get_docker_client()
        volume_name = self._volume_name(workspace_id)

        resp = await client.get(f"/volumes/{volume_name}")
        return resp.status_code == 200

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
        """Close clients."""
        if self._docker_client:
            await self._docker_client.aclose()
            self._docker_client = None
