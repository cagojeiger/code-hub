"""Integration test fixtures."""

import os
import uuid

import pytest

import codehub.infra.docker as docker_module
import codehub.infra.storage as storage_module
from codehub.app.config import get_settings
from codehub.infra.docker import (
    ContainerAPI,
    DockerClient,
    VolumeAPI,
)
from codehub.infra.storage import close_storage, get_s3_client, init_storage

# Test bucket name - separate from production
TEST_BUCKET = "codehub-archives-test"


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
