"""Unit tests for Volume API endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from codehub_agent.api.dependencies import get_runtime, reset_runtime
from codehub_agent.api.errors import DockerError, VolumeInUseError
from codehub_agent.main import app
from codehub_agent.runtimes.docker.volume import VolumeStatus


@pytest.fixture
def mock_runtime() -> MagicMock:
    """Create mock runtime."""
    runtime = MagicMock()
    runtime.volumes = MagicMock()
    runtime.volumes.list_all = AsyncMock(return_value=[])
    runtime.volumes.create = AsyncMock()
    runtime.volumes.delete = AsyncMock()
    runtime.volumes.exists = AsyncMock(
        return_value=VolumeStatus(exists=True, name="codehub-ws1-home")
    )
    return runtime


@pytest.fixture
def client(mock_runtime: MagicMock) -> TestClient:
    """Create test client with mocked runtime."""
    # Use FastAPI's dependency_overrides
    app.dependency_overrides[get_runtime] = lambda: mock_runtime

    with TestClient(app) as client:
        yield client

    # Cleanup
    app.dependency_overrides.clear()
    reset_runtime()


class TestVolumeAPI:
    """Tests for Volume API endpoints."""

    def test_list_volumes_empty(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test list volumes returns empty list."""
        response = client.get("/api/v1/volumes")

        assert response.status_code == 200
        data = response.json()
        assert data["volumes"] == []

    def test_list_volumes_with_data(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test list volumes returns volume data."""
        mock_runtime.volumes.list_all.return_value = [
            {"workspace_id": "ws1", "exists": True, "name": "codehub-ws1-home"}
        ]

        response = client.get("/api/v1/volumes")

        assert response.status_code == 200
        data = response.json()
        assert len(data["volumes"]) == 1
        assert data["volumes"][0]["workspace_id"] == "ws1"

    def test_create_volume(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test create volume."""
        response = client.post("/api/v1/volumes/ws1")

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "created"
        assert data["workspace_id"] == "ws1"

    def test_create_volume_error(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test create volume handles error."""
        mock_runtime.volumes.create.side_effect = DockerError("Create failed")

        response = client.post("/api/v1/volumes/ws1")

        assert response.status_code == 500
        data = response.json()
        assert data["error"]["code"] == "DOCKER_ERROR"

    def test_delete_volume(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test delete volume."""
        response = client.delete("/api/v1/volumes/ws1")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["workspace_id"] == "ws1"

    def test_delete_volume_error(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test delete volume handles error."""
        mock_runtime.volumes.delete.side_effect = VolumeInUseError("Volume in use")

        response = client.delete("/api/v1/volumes/ws1")

        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "VOLUME_IN_USE"

    def test_volume_exists_true(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test volume exists returns True."""
        response = client.get("/api/v1/volumes/ws1/exists")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is True
        assert data["name"] == "codehub-ws1-home"

    def test_volume_exists_false(
        self, client: TestClient, mock_runtime: MagicMock
    ) -> None:
        """Test volume exists returns False."""
        mock_runtime.volumes.exists.return_value = VolumeStatus(
            exists=False, name="codehub-ws1-home"
        )

        response = client.get("/api/v1/volumes/ws1/exists")

        assert response.status_code == 200
        data = response.json()
        assert data["exists"] is False
