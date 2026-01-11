"""Integration tests for Scheduler GC functionality.

Tests orphan cleanup with real PostgreSQL, MinIO, and Docker.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from codehub.adapters.instance import DockerInstanceController
from codehub.adapters.storage import S3StorageProvider
from codehub.app.config import get_settings
from codehub.core.interfaces.leader import LeaderElection
from codehub.infra.redis_kv import ActivityStore
from codehub.infra.redis_pubsub import ChannelPublisher, ChannelSubscriber
from codehub.control.coordinator.scheduler import Scheduler
from codehub.infra import get_s3_client
from codehub.infra.docker import ContainerAPI, VolumeAPI, VolumeConfig

# Dummy values for test workspaces
TEST_OWNER_USER_ID = "test-user-00000000"
TEST_IMAGE_REF = "ubuntu:22.04"
TEST_INSTANCE_BACKEND = "local-docker"
TEST_STORAGE_BACKEND = "docker-volume"


async def _ensure_test_user(engine: AsyncEngine) -> None:
    """Create test user if not exists."""
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO users (id, username, password_hash, created_at, failed_login_attempts)
                VALUES (:id, :username, 'dummy-hash', :now, 0)
                ON CONFLICT (id) DO NOTHING
            """),
            {
                "id": TEST_OWNER_USER_ID,
                "username": "test-user",
                "now": datetime.now(UTC),
            },
        )


def _insert_workspace_sql() -> str:
    """SQL for inserting test workspace with all required fields."""
    return """
        INSERT INTO workspaces (
            id, owner_user_id, name, image_ref, instance_backend, storage_backend, home_store_key,
            desired_state, phase, operation, conditions, error_count, archive_key, deleted_at, created_at, updated_at
        ) VALUES (
            :id, :owner_user_id, :name, :image_ref, :instance_backend, :storage_backend, :home_store_key,
            :desired_state, :phase, 'NONE', '{}', 0, :archive_key, :deleted_at, :now, :now
        )
    """


@pytest.fixture
def mock_leader() -> MagicMock:
    """Mock LeaderElection (always leader)."""
    leader = MagicMock(spec=LeaderElection)
    leader.is_leader = True
    leader.try_acquire = MagicMock(return_value=True)
    return leader


@pytest.fixture
def mock_subscriber() -> MagicMock:
    """Mock ChannelSubscriber."""
    subscriber = MagicMock(spec=ChannelSubscriber)
    return subscriber


@pytest.fixture
def mock_activity() -> AsyncMock:
    """Mock ActivityStore."""
    activity = AsyncMock(spec=ActivityStore)
    activity.scan_all = AsyncMock(return_value={})
    activity.delete = AsyncMock(return_value=0)
    return activity


@pytest.fixture
def mock_publisher() -> AsyncMock:
    """Mock ChannelPublisher."""
    publisher = AsyncMock(spec=ChannelPublisher)
    publisher.publish = AsyncMock()
    return publisher


@pytest.fixture
def storage_provider(volume_api: VolumeAPI, container_api: ContainerAPI) -> S3StorageProvider:
    """S3StorageProvider with DI for testing."""
    from codehub.adapters.job import DockerJobRunner
    from codehub.adapters.volume import DockerVolumeProvider

    volumes = DockerVolumeProvider(volume_api)
    job_runner = DockerJobRunner(container_api)
    return S3StorageProvider(volumes=volumes, job_runner=job_runner)


@pytest.fixture
def instance_controller(container_api: ContainerAPI) -> DockerInstanceController:
    """DockerInstanceController with DI for testing."""
    return DockerInstanceController(containers=container_api)


class TestArchiveOrphanCleanup:
    """Archive orphan cleanup tests."""

    async def test_deletes_orphan_archive(
        self,
        test_db_engine: AsyncEngine,
        mock_leader: MagicMock,
        mock_subscriber: MagicMock,
        mock_activity: AsyncMock,
        mock_publisher: AsyncMock,
        storage_provider: S3StorageProvider,
        instance_controller: DockerInstanceController,
    ):
        """Orphan archive in S3 is deleted by GC."""
        settings = get_settings()
        bucket = settings.storage.bucket_name
        prefix = settings.runtime.resource_prefix

        # 1. Create orphan archive in S3 (no matching workspace in DB)
        orphan_ws_id = f"orphan-{uuid.uuid4().hex[:8]}"
        orphan_key = f"{prefix}{orphan_ws_id}/op123/home.tar.zst"

        async with get_s3_client() as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=orphan_key,
                Body=b"fake archive content",
            )

        # Verify orphan exists
        archives_before = await storage_provider.list_all_archive_keys(prefix)
        assert orphan_key in archives_before

        # 2. Run GC
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn, mock_leader, mock_subscriber,
                mock_activity, mock_publisher,
                storage_provider, instance_controller,
            )
            await scheduler._run_gc()

        # 3. Verify orphan is deleted
        archives_after = await storage_provider.list_all_archive_keys(prefix)
        assert orphan_key not in archives_after

    async def test_preserves_protected_archive(
        self,
        test_db_engine: AsyncEngine,
        mock_leader: MagicMock,
        mock_subscriber: MagicMock,
        mock_activity: AsyncMock,
        mock_publisher: AsyncMock,
        storage_provider: S3StorageProvider,
        instance_controller: DockerInstanceController,
    ):
        """Archive referenced by workspace is NOT deleted."""
        settings = get_settings()
        bucket = settings.storage.bucket_name
        prefix = settings.runtime.resource_prefix

        # 0. Ensure test user exists
        await _ensure_test_user(test_db_engine)

        # 1. Create workspace in DB with archive_key
        ws_id = uuid.uuid4().hex
        archive_key = f"{prefix}{ws_id}/op456/home.tar.zst"

        async with test_db_engine.begin() as conn:
            await conn.execute(
                text(_insert_workspace_sql()),
                {
                    "id": ws_id,
                    "owner_user_id": TEST_OWNER_USER_ID,
                    "name": f"test-{ws_id}",
                    "image_ref": TEST_IMAGE_REF,
                    "instance_backend": TEST_INSTANCE_BACKEND,
                    "storage_backend": TEST_STORAGE_BACKEND,
                    "home_store_key": f"ws-{ws_id}-home",
                    "desired_state": "ARCHIVED",
                    "phase": "ARCHIVED",
                    "archive_key": archive_key,
                    "deleted_at": None,
                    "now": datetime.now(UTC),
                },
            )

        # 2. Create matching archive in S3
        async with get_s3_client() as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=archive_key,
                Body=b"protected archive content",
            )

        # Verify archive exists
        archives_before = await storage_provider.list_all_archive_keys(prefix)
        assert archive_key in archives_before

        # 3. Run GC
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn, mock_leader, mock_subscriber,
                mock_activity, mock_publisher,
                storage_provider, instance_controller,
            )
            await scheduler._run_gc()

        # 4. Verify archive is still there (protected)
        archives_after = await storage_provider.list_all_archive_keys(prefix)
        assert archive_key in archives_after

    async def test_deletes_deleted_workspace_archive(
        self,
        test_db_engine: AsyncEngine,
        mock_leader: MagicMock,
        mock_subscriber: MagicMock,
        mock_activity: AsyncMock,
        mock_publisher: AsyncMock,
        storage_provider: S3StorageProvider,
        instance_controller: DockerInstanceController,
    ):
        """Archive of soft-deleted workspace IS deleted (user wants deletion)."""
        settings = get_settings()
        bucket = settings.storage.bucket_name
        prefix = settings.runtime.resource_prefix

        # 0. Ensure test user exists
        await _ensure_test_user(test_db_engine)

        # 1. Create soft-deleted workspace in DB with archive_key
        ws_id = uuid.uuid4().hex
        archive_key = f"{prefix}{ws_id}/op789/home.tar.zst"
        now = datetime.now(UTC)

        async with test_db_engine.begin() as conn:
            await conn.execute(
                text(_insert_workspace_sql()),
                {
                    "id": ws_id,
                    "owner_user_id": TEST_OWNER_USER_ID,
                    "name": f"test-{ws_id}",
                    "image_ref": TEST_IMAGE_REF,
                    "instance_backend": TEST_INSTANCE_BACKEND,
                    "storage_backend": TEST_STORAGE_BACKEND,
                    "home_store_key": f"ws-{ws_id}-home",
                    "desired_state": "ARCHIVED",
                    "phase": "DELETED",
                    "archive_key": archive_key,
                    "deleted_at": now,
                    "now": now,
                },
            )

        # 2. Create matching archive in S3
        async with get_s3_client() as s3:
            await s3.put_object(
                Bucket=bucket,
                Key=archive_key,
                Body=b"deleted workspace archive",
            )

        # Verify archive exists
        archives_before = await storage_provider.list_all_archive_keys(prefix)
        assert archive_key in archives_before

        # 3. Run GC
        async with test_db_engine.connect() as conn:
            scheduler = Scheduler(
                conn, mock_leader, mock_subscriber,
                mock_activity, mock_publisher,
                storage_provider, instance_controller,
            )
            await scheduler._run_gc()

        # 4. Verify archive is deleted (user wanted deletion)
        archives_after = await storage_provider.list_all_archive_keys(prefix)
        assert archive_key not in archives_after


class TestVolumeOrphanCleanup:
    """Volume orphan cleanup tests.

    Note: Uses actual resource_prefix (ws-) for volume names to match
    production behavior. GC and StorageProvider share the same prefix.
    """

    async def test_deletes_orphan_volume(
        self,
        test_db_engine: AsyncEngine,
        mock_leader: MagicMock,
        mock_subscriber: MagicMock,
        mock_activity: AsyncMock,
        mock_publisher: AsyncMock,
        storage_provider: S3StorageProvider,
        instance_controller: DockerInstanceController,
        volume_api: VolumeAPI,
    ):
        """Orphan volume is deleted by GC."""
        settings = get_settings()
        prefix = settings.runtime.resource_prefix  # Use actual prefix (ws-)

        # 1. Create orphan volume (no matching workspace in DB)
        # Use unique ID to avoid conflicts with production
        orphan_ws_id = f"test-orphan-{uuid.uuid4().hex[:8]}"
        volume_name = f"{prefix}{orphan_ws_id}-home"

        await volume_api.create(VolumeConfig(name=volume_name))

        # Verify volume exists
        volumes_before = await volume_api.list(filters={"name": [volume_name]})
        assert any(v["Name"] == volume_name for v in volumes_before)

        try:
            # 2. Run GC
            async with test_db_engine.connect() as conn:
                scheduler = Scheduler(
                    conn, mock_leader, mock_subscriber,
                    mock_activity, mock_publisher,
                    storage_provider, instance_controller,
                )
                await scheduler._run_gc()

            # 3. Verify orphan volume is deleted
            volumes_after = await volume_api.list(filters={"name": [volume_name]})
            assert not any(v["Name"] == volume_name for v in volumes_after)
        finally:
            # Cleanup in case of test failure
            try:
                await volume_api.remove(volume_name)
            except Exception:
                pass

    async def test_preserves_valid_volume(
        self,
        test_db_engine: AsyncEngine,
        mock_leader: MagicMock,
        mock_subscriber: MagicMock,
        mock_activity: AsyncMock,
        mock_publisher: AsyncMock,
        storage_provider: S3StorageProvider,
        instance_controller: DockerInstanceController,
        volume_api: VolumeAPI,
    ):
        """Volume with matching workspace is NOT deleted."""
        settings = get_settings()
        prefix = settings.runtime.resource_prefix  # Use actual prefix (ws-)

        # 0. Ensure test user exists
        await _ensure_test_user(test_db_engine)

        # 1. Create workspace in DB
        ws_id = f"test-valid-{uuid.uuid4().hex[:8]}"
        volume_name = f"{prefix}{ws_id}-home"

        async with test_db_engine.begin() as conn:
            await conn.execute(
                text(_insert_workspace_sql()),
                {
                    "id": ws_id,
                    "owner_user_id": TEST_OWNER_USER_ID,
                    "name": f"test-{ws_id}",
                    "image_ref": TEST_IMAGE_REF,
                    "instance_backend": TEST_INSTANCE_BACKEND,
                    "storage_backend": TEST_STORAGE_BACKEND,
                    "home_store_key": f"ws-{ws_id}-home",
                    "desired_state": "RUNNING",
                    "phase": "RUNNING",
                    "archive_key": None,
                    "deleted_at": None,
                    "now": datetime.now(UTC),
                },
            )

        # 2. Create matching volume
        await volume_api.create(VolumeConfig(name=volume_name))

        # Verify volume exists
        volumes_before = await volume_api.list(filters={"name": [volume_name]})
        assert any(v["Name"] == volume_name for v in volumes_before)

        try:
            # 3. Run GC
            async with test_db_engine.connect() as conn:
                scheduler = Scheduler(
                    conn, mock_leader, mock_subscriber,
                    mock_activity, mock_publisher,
                    storage_provider, instance_controller,
                )
                await scheduler._run_gc()

            # 4. Verify volume still exists (protected)
            volumes_after = await volume_api.list(filters={"name": [volume_name]})
            assert any(v["Name"] == volume_name for v in volumes_after)
        finally:
            # Cleanup
            try:
                await volume_api.remove(volume_name)
            except Exception:
                pass
