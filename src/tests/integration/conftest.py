"""Integration test fixtures."""

import os
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import codehub.infra.docker as docker_module
import codehub.infra.object_storage as storage_module
from codehub.app.config import get_settings
from codehub.infra.docker import (
    ContainerAPI,
    DockerClient,
    VolumeAPI,
)
from codehub.infra import close_storage, get_s3_client, init_storage

# Test bucket name - separate from production
TEST_BUCKET = "codehub-archives-test"

# PostgreSQL server URL (without database name)
# Use POSTGRES_HOST env var to override hostname for local testing
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_SERVER = f"postgresql+asyncpg://codehub:codehub@{POSTGRES_HOST}:5432"

# Redis host (for local testing)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")


@pytest.fixture(scope="function")
def test_db_name() -> str:
    """Unique test database name per test function."""
    return f"codehub_test_{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture(scope="function")
async def test_db_engine(test_db_name: str) -> AsyncGenerator[AsyncEngine, None]:
    """Create temporary test database and engine.

    1. Connect to 'postgres' DB to create test DB
    2. Create engine for test DB
    3. Create tables
    4. Cleanup: Drop test database after tests
    """
    # Import models to register them with SQLModel.metadata
    from codehub.core.models import User, Workspace  # noqa: F401

    # 1. Connect to 'postgres' DB to create test DB
    admin_engine = create_async_engine(f"{POSTGRES_SERVER}/postgres", isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.execute(text(f"CREATE DATABASE {test_db_name}"))
    await admin_engine.dispose()

    # 2. Create engine for test DB
    test_engine = create_async_engine(
        f"{POSTGRES_SERVER}/{test_db_name}",
        echo=False,
    )

    # 3. Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    yield test_engine

    # 4. Cleanup: Drop test database
    await test_engine.dispose()
    admin_engine = create_async_engine(f"{POSTGRES_SERVER}/postgres", isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        # Terminate existing connections
        await conn.execute(text(f"""
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = '{test_db_name}' AND pid <> pg_backend_pid()
        """))
        await conn.execute(text(f"DROP DATABASE IF EXISTS {test_db_name}"))
    await admin_engine.dispose()


@pytest.fixture(autouse=True)
async def reset_docker_client():
    """Reset global Docker client before each test.

    This prevents 'Event loop is closed' errors when tests
    use the global singleton (e.g., S3StorageProvider).
    """
    # Reset before test
    docker_module._docker_client = None
    yield
    # Cleanup after test
    if docker_module._docker_client:
        await docker_module._docker_client.close()
        docker_module._docker_client = None


@pytest.fixture(autouse=True)
async def setup_storage():
    """Initialize S3 storage for tests with separate test bucket.

    - Uses TEST_BUCKET instead of production bucket
    - Cleans up all test objects after each test
    """
    # Clear settings cache first, then set env var
    get_settings.cache_clear()
    os.environ["S3_BUCKET"] = TEST_BUCKET

    # Reset before test
    storage_module._session = None
    await init_storage()
    yield

    # Cleanup: delete all objects in test bucket
    await _cleanup_test_bucket()
    await close_storage()


async def _cleanup_test_bucket():
    """Delete all objects in the test bucket."""
    settings = get_settings()
    try:
        async with get_s3_client() as s3:
            # List and delete all objects
            paginator = s3.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=settings.storage.bucket_name):
                objects = page.get("Contents", [])
                if objects:
                    await s3.delete_objects(
                        Bucket=settings.storage.bucket_name,
                        Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
                    )
    except Exception:
        pass  # Ignore errors during cleanup


# Test resource prefix - clearly identifies test resources
TEST_PREFIX = "test-int-"


@pytest.fixture
def test_prefix() -> str:
    """Unique prefix for test resources to avoid conflicts.

    Format: test-int-{uuid8}- (e.g., test-int-a1b2c3d4-)
    """
    return f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}-"


@pytest_asyncio.fixture
async def test_redis():
    """Redis client for integration tests.

    Uses the same Redis instance as the application.
    Set REDIS_HOST env var for local testing.
    """
    import redis.asyncio as redis

    client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def docker_client() -> DockerClient:
    """Fresh DockerClient instance per test.

    Don't use the global singleton to avoid event loop issues.
    """
    return DockerClient()


@pytest.fixture
def volume_api(docker_client: DockerClient) -> VolumeAPI:
    """VolumeAPI instance with fresh client."""
    return VolumeAPI(client=docker_client)


@pytest.fixture
def container_api(docker_client: DockerClient) -> ContainerAPI:
    """ContainerAPI instance with fresh client."""
    return ContainerAPI(client=docker_client)
