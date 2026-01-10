"""Integration tests for S3StorageProvider."""

import hashlib
import io
import tarfile

import pytest
import zstandard as zstd

from codehub.adapters import DockerJobRunner, DockerVolumeProvider, S3StorageProvider
from codehub.infra import get_s3_client
from codehub.infra.docker import ContainerAPI, ContainerConfig, HostConfig, VolumeAPI


@pytest.fixture
def volume_provider(volume_api: VolumeAPI) -> DockerVolumeProvider:
    """Create DockerVolumeProvider with injected VolumeAPI."""
    return DockerVolumeProvider(api=volume_api)


@pytest.fixture
def job_runner(container_api: ContainerAPI) -> DockerJobRunner:
    """Create DockerJobRunner with injected ContainerAPI."""
    return DockerJobRunner(containers=container_api)


@pytest.fixture
def storage_provider(
    volume_provider: DockerVolumeProvider, job_runner: DockerJobRunner
) -> S3StorageProvider:
    """Create S3StorageProvider with injected dependencies."""
    return S3StorageProvider(volumes=volume_provider, job_runner=job_runner)


@pytest.mark.integration
class TestS3StorageProvider:
    """S3StorageProvider integration tests."""

    @pytest.mark.asyncio
    async def test_provision_creates_volume(
        self, storage_provider: S3StorageProvider, test_prefix: str
    ):
        """Test provision creates a Docker volume."""
        workspace_id = f"{test_prefix}prov"
        try:
            await storage_provider.provision(workspace_id)
            assert await storage_provider.volume_exists(workspace_id)
        finally:
            await storage_provider.delete_volume(workspace_id)

    @pytest.mark.asyncio
    async def test_list_volumes(
        self, storage_provider: S3StorageProvider, test_prefix: str
    ):
        """Test list_volumes returns created volumes."""
        workspace_id = f"{test_prefix}list"
        try:
            await storage_provider.provision(workspace_id)
            volumes = await storage_provider.list_volumes("codehub-ws-")
            found = any(v.workspace_id == workspace_id for v in volumes)
            assert found, f"Workspace {workspace_id} not found in volumes"
        finally:
            await storage_provider.delete_volume(workspace_id)

    @pytest.mark.asyncio
    async def test_delete_volume(
        self, storage_provider: S3StorageProvider, test_prefix: str
    ):
        """Test delete_volume removes the volume."""
        workspace_id = f"{test_prefix}del"
        await storage_provider.provision(workspace_id)
        await storage_provider.delete_volume(workspace_id)
        assert not await storage_provider.volume_exists(workspace_id)

    @pytest.mark.asyncio
    async def test_create_empty_archive(
        self, storage_provider: S3StorageProvider, test_prefix: str
    ):
        """Test create_empty_archive creates valid tar.zst and .meta files."""
        from codehub.app.config import get_settings

        settings = get_settings()
        workspace_id = f"{test_prefix}empty"
        op_id = "op-empty-001"

        try:
            # Create empty archive
            archive_key = await storage_provider.create_empty_archive(workspace_id, op_id)

            # Verify archive key format
            assert archive_key.endswith("/home.tar.zst")
            assert workspace_id in archive_key
            assert op_id in archive_key

            # Verify both files exist in S3
            async with get_s3_client() as s3:
                # 1. Verify tar.zst exists
                tar_response = await s3.get_object(
                    Bucket=settings.storage.bucket_name,
                    Key=archive_key,
                )
                tar_zst_data = await tar_response["Body"].read()

                # 2. Verify .meta exists
                meta_response = await s3.get_object(
                    Bucket=settings.storage.bucket_name,
                    Key=f"{archive_key}.meta",
                )
                meta_data = await meta_response["Body"].read()

            # 3. Verify checksum matches
            expected_checksum = f"sha256:{hashlib.sha256(tar_zst_data).hexdigest()}"
            actual_checksum = meta_data.decode()
            assert actual_checksum == expected_checksum, (
                f"Checksum mismatch: expected {expected_checksum}, got {actual_checksum}"
            )

            # 4. Verify tar.zst is valid (can decompress and extract)
            dctx = zstd.ZstdDecompressor()
            tar_data = dctx.decompress(tar_zst_data)

            tar_buffer = io.BytesIO(tar_data)
            with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
                # Empty archive should have no members
                members = tar.getmembers()
                assert len(members) == 0, f"Expected empty tar, got {len(members)} members"

        finally:
            # Cleanup
            await storage_provider.delete_archive(archive_key)


@pytest.mark.integration
class TestArchiveRestore:
    """Archive and Restore integration tests (Spec-v2)."""

    @pytest.mark.asyncio
    async def test_archive_creates_s3_objects(
        self,
        storage_provider: S3StorageProvider,
        container_api: ContainerAPI,
        test_prefix: str,
    ):
        """Test archive creates tar.zst and .meta in S3."""
        workspace_id = f"{test_prefix}archive"
        op_id = "op-test-001"

        try:
            # Create volume with test data
            await storage_provider.provision(workspace_id)

            # Write test data to volume using helper container
            await _write_test_data(workspace_id, b"archive test data", container_api)

            # Archive
            archive_key = await storage_provider.archive(workspace_id, op_id)

            # Verify archive key format
            assert archive_key.endswith("/home.tar.zst")
            assert workspace_id in archive_key
            assert op_id in archive_key

        finally:
            await storage_provider.delete_volume(workspace_id)

    @pytest.mark.asyncio
    async def test_archive_idempotent(
        self,
        storage_provider: S3StorageProvider,
        container_api: ContainerAPI,
        test_prefix: str,
    ):
        """Test archive is idempotent with same op_id."""
        workspace_id = f"{test_prefix}idem"
        op_id = "op-idem-001"

        try:
            await storage_provider.provision(workspace_id)
            await _write_test_data(workspace_id, b"idempotent test", container_api)

            # Archive twice with same op_id
            key1 = await storage_provider.archive(workspace_id, op_id)
            key2 = await storage_provider.archive(workspace_id, op_id)

            assert key1 == key2

        finally:
            await storage_provider.delete_volume(workspace_id)

    @pytest.mark.asyncio
    async def test_archive_restore_roundtrip(
        self,
        storage_provider: S3StorageProvider,
        container_api: ContainerAPI,
        test_prefix: str,
    ):
        """Test archive and restore preserves data."""
        workspace_id = f"{test_prefix}roundtrip"
        op_id = "op-roundtrip-001"
        test_data = b"roundtrip test content 12345"

        try:
            # 1. Create volume with test data
            await storage_provider.provision(workspace_id)
            await _write_test_data(workspace_id, test_data, container_api)

            # 2. Archive
            archive_key = await storage_provider.archive(workspace_id, op_id)

            # 3. Delete volume
            await storage_provider.delete_volume(workspace_id)
            assert not await storage_provider.volume_exists(workspace_id)

            # 4. Restore
            restore_marker = await storage_provider.restore(workspace_id, archive_key)
            assert restore_marker == archive_key

            # 5. Verify volume exists
            assert await storage_provider.volume_exists(workspace_id)

            # 6. Verify data
            restored_data = await _read_test_data(workspace_id, container_api)
            assert restored_data == test_data

        finally:
            await storage_provider.delete_volume(workspace_id)


async def _write_test_data(
    workspace_id: str, data: bytes, containers: ContainerAPI
) -> None:
    """Write test data to volume using helper container."""
    volume_name = f"codehub-ws-{workspace_id}-home"
    helper_name = f"codehub-ws-helper-write-{workspace_id}"

    try:
        # Create container
        config = ContainerConfig(
            image="alpine:latest",
            name=helper_name,
            cmd=["sh", "-c", "cat > /data/test.txt"],
            host_config=HostConfig(binds=[f"{volume_name}:/data"]),
        )
        await containers.create(config)

        # Put data into container
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            tarinfo = tarfile.TarInfo(name="test.txt")
            tarinfo.size = len(data)
            tar.addfile(tarinfo, io.BytesIO(data))
        tar_buffer.seek(0)

        await containers.put_archive(helper_name, "/data", tar_buffer.read())

    finally:
        await containers.remove(helper_name)


async def _read_test_data(workspace_id: str, containers: ContainerAPI) -> bytes:
    """Read test data from volume using helper container."""
    volume_name = f"codehub-ws-{workspace_id}-home"
    helper_name = f"codehub-ws-helper-read-{workspace_id}"

    try:
        # Create container
        config = ContainerConfig(
            image="alpine:latest",
            name=helper_name,
            cmd=["sh", "-c", "sleep 30"],
            host_config=HostConfig(binds=[f"{volume_name}:/data:ro"]),
        )
        await containers.create(config)
        await containers.start(helper_name)

        # Get data from container
        tar_data = await containers.get_archive(helper_name, "/data/test.txt")

        # Extract from tar
        tar_buffer = io.BytesIO(tar_data)
        with tarfile.open(fileobj=tar_buffer, mode="r") as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted is None:
                raise RuntimeError("Failed to extract test data")
            return extracted.read()

    finally:
        await containers.stop(helper_name, timeout=1)
        await containers.remove(helper_name)
