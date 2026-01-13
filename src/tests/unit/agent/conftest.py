"""Fixtures for Agent unit tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codehub_agent.infra import ContainerAPI, ImageAPI, VolumeAPI
from codehub_agent.runtimes.docker.naming import ResourceNaming


@pytest.fixture
def mock_container_api() -> AsyncMock:
    """Mock ContainerAPI for testing."""
    api = AsyncMock(spec=ContainerAPI)
    api.list = AsyncMock(return_value=[])
    api.inspect = AsyncMock(return_value=None)
    api.create = AsyncMock()
    api.start = AsyncMock()
    api.stop = AsyncMock()
    api.remove = AsyncMock()
    api.logs = AsyncMock(return_value=b"")
    api.wait = AsyncMock(return_value=0)
    return api


@pytest.fixture
def mock_volume_api() -> AsyncMock:
    """Mock VolumeAPI for testing."""
    api = AsyncMock(spec=VolumeAPI)
    api.list = AsyncMock(return_value=[])
    api.inspect = AsyncMock(return_value=None)
    api.create = AsyncMock()
    api.remove = AsyncMock()
    return api


@pytest.fixture
def mock_image_api() -> AsyncMock:
    """Mock ImageAPI for testing."""
    api = AsyncMock(spec=ImageAPI)
    api.inspect = AsyncMock(return_value=None)
    api.pull = AsyncMock()
    api.ensure = AsyncMock()
    return api


@pytest.fixture
def mock_agent_config() -> MagicMock:
    """Mock AgentConfig for testing."""
    config = MagicMock()
    config.resource_prefix = "codehub-"
    config.cluster_id = "test-cluster"
    config.docker_network = "test-network"
    config.container_port = 8080
    config.coder_uid = 1000
    config.coder_gid = 1000
    config.default_image = "codercom/code-server:latest"
    config.storage_job_image = "codehub/storage-job:latest"
    config.job_timeout = 600
    config.s3_bucket = "test-bucket"
    config.s3_endpoint = "http://minio:9000"
    config.s3_internal_endpoint = "http://minio:9000"
    config.s3_access_key = "test-access-key"
    config.s3_secret_key = "test-secret-key"
    return config


@pytest.fixture
def mock_naming(mock_agent_config: MagicMock) -> ResourceNaming:
    """ResourceNaming with mock config for testing."""
    return ResourceNaming(mock_agent_config)
