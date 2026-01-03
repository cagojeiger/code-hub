"""Integration test fixtures."""

import uuid

import pytest

from codehub.infra.docker import (
    ContainerAPI,
    DockerClient,
    VolumeAPI,
)


@pytest.fixture
def test_prefix() -> str:
    """Unique prefix for test resources to avoid conflicts."""
    return f"test-{uuid.uuid4().hex[:8]}-"


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
