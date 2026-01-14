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
    """Mock AgentConfig for testing.

    Uses the new nested config structure:
    - config.docker.* for Docker settings
    - config.s3.* for S3 settings
    - config.runtime.* for runtime settings
    """
    config = MagicMock()

    # Docker sub-config
    config.docker = MagicMock()
    config.docker.network = "test-network"
    config.docker.container_port = 8080
    config.docker.coder_uid = 1000
    config.docker.coder_gid = 1000
    config.docker.job_timeout = 600

    # S3 sub-config
    config.s3 = MagicMock()
    config.s3.bucket = "test-bucket"
    config.s3.endpoint = "http://minio:9000"
    config.s3.internal_endpoint = "http://minio:9000"
    config.s3.access_key = "test-access-key"
    config.s3.secret_key = "test-secret-key"

    # Runtime sub-config
    config.runtime = MagicMock()
    config.runtime.resource_prefix = "codehub-"
    config.runtime.archive_suffix = "home.tar.zst"
    config.runtime.default_image = "codercom/code-server:latest"
    config.runtime.storage_job_image = "codehub/storage-job:latest"

    return config


@pytest.fixture
def mock_naming(mock_agent_config: MagicMock) -> ResourceNaming:
    """ResourceNaming with mock config for testing."""
    return ResourceNaming(mock_agent_config)
